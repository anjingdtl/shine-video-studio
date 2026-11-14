//! The install / upgrade transaction.
//!
//! An upgrade of a user's workbench is the one operation in this project that
//! can destroy real work, so it is modelled as an explicit state machine with
//! a frozen plan, a durable journal and a rollback rule that refuses to
//! overwrite data the new version has already accepted.
//!
//! The phases are:
//!
//! ```text
//! Discover -> Plan -> Consent -> PreDownload -> Verify -> QuiesceOwnedProcesses
//!   -> Backup -> Stage -> ApplyDependencies -> MigrateDatabase -> ValidateCandidate
//!   -> SwitchStudio -> HealthCheck -> Complete
//! ```
//!
//! and the failure exits are `Paused`, `NeedsReview`, `RollbackPending`,
//! `RolledBack` and `Blocked`.

use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use crate::journal::{Journal, JournalStatus};

/// One artifact the plan needs before it can switch versions.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct PlannedArtifact {
    pub id: String,
    pub url: String,
    pub sha256: String,
    pub size_bytes: u64,
    /// Where the verified file is staged.
    pub staged_path: PathBuf,
}

/// The immutable description of one upgrade.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct UpgradePlan {
    pub plan_id: String,
    pub installation_id: String,
    pub target_studio_version: String,
    /// The identity of the signed deployment manifest this plan was built from.
    pub target_manifest_identity: String,
    pub artifacts: Vec<PlannedArtifact>,
    /// Capability packs this upgrade will (re)install.
    pub capabilities: Vec<String>,
    /// Space the whole transaction may use, including the backup.
    pub disk_budget_bytes: u64,
    /// True when owned studio processes have to be stopped first.
    pub requires_quiesce: bool,
    /// True when the database has to be migrated as part of the switch.
    pub requires_database_migration: bool,
    /// Set once the user approved the plan. After that the plan is frozen.
    pub consented_at_unix_seconds: Option<u64>,
    /// The hash of the plan content at consent time.
    pub plan_hash: Option<String>,
}

/// Why a plan was refused.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum PlanError {
    Empty,
    InvalidArtifact(String),
    InvalidBudget,
    AlreadyConsented,
    /// The plan changed after the user approved it.
    PlanChanged {
        expected: String,
        actual: String,
    },
    /// The plan was never approved.
    NotConsented,
}

impl std::fmt::Display for PlanError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Empty => formatter.write_str("升级计划为空"),
            Self::InvalidArtifact(detail) => {
                write!(formatter, "升级计划中的文件无效：{detail}")
            }
            Self::InvalidBudget => formatter.write_str("升级计划的磁盘预算无效"),
            Self::AlreadyConsented => formatter.write_str("该升级计划已经获得确认"),
            Self::PlanChanged { expected, actual } => write!(
                formatter,
                "升级计划在确认后发生了变化（确认时 {expected}，当前 {actual}），已停止执行"
            ),
            Self::NotConsented => formatter.write_str("该升级计划尚未获得用户确认"),
        }
    }
}

impl std::error::Error for PlanError {}

impl UpgradePlan {
    pub fn new(
        installation_id: &str,
        target_studio_version: &str,
        target_manifest_identity: &str,
    ) -> Self {
        Self {
            plan_id: format!(
                "plan-{}-{}",
                target_studio_version.replace(['.', '+'], "-"),
                unix_now()
            ),
            installation_id: installation_id.to_owned(),
            target_studio_version: target_studio_version.to_owned(),
            target_manifest_identity: target_manifest_identity.to_owned(),
            artifacts: Vec::new(),
            capabilities: Vec::new(),
            disk_budget_bytes: 0,
            requires_quiesce: true,
            requires_database_migration: false,
            consented_at_unix_seconds: None,
            plan_hash: None,
        }
    }

    pub fn with_artifact(mut self, artifact: PlannedArtifact) -> Self {
        self.artifacts.push(artifact);
        self
    }

    pub fn with_capability(mut self, capability: &str) -> Self {
        self.capabilities.push(capability.to_owned());
        self
    }

    pub fn with_disk_budget(mut self, bytes: u64) -> Self {
        self.disk_budget_bytes = bytes;
        self
    }

    /// The hash of the plan content that the user approves.
    ///
    /// The consent timestamp and the stored hash are excluded, so hashing a
    /// consented plan again yields the same value it had when it was approved.
    pub fn content_hash(&self) -> String {
        let mut canonical = serde_json::to_vec(&json!({
            "plan_id": self.plan_id,
            "installation_id": self.installation_id,
            "target_studio_version": self.target_studio_version,
            "target_manifest_identity": self.target_manifest_identity,
            "artifacts": self.artifacts,
            "capabilities": self.capabilities,
            "disk_budget_bytes": self.disk_budget_bytes,
            "requires_quiesce": self.requires_quiesce,
            "requires_database_migration": self.requires_database_migration,
        }))
        .unwrap_or_default();
        canonical.sort_unstable();
        let digest = Sha256::digest(&canonical);
        digest.iter().map(|byte| format!("{byte:02x}")).collect()
    }

    /// Check that the plan is well formed before anything is downloaded.
    pub fn validate(&self) -> Result<(), PlanError> {
        if self.installation_id.is_empty() || self.target_studio_version.is_empty() {
            return Err(PlanError::Empty);
        }
        if self.disk_budget_bytes == 0 {
            return Err(PlanError::InvalidBudget);
        }
        for artifact in &self.artifacts {
            if artifact.id.is_empty() {
                return Err(PlanError::InvalidArtifact("缺少文件标识".to_owned()));
            }
            if artifact.sha256.len() != 64
                || !artifact.sha256.bytes().all(|byte| byte.is_ascii_hexdigit())
            {
                return Err(PlanError::InvalidArtifact(format!(
                    "{} 的校验和无效",
                    artifact.id
                )));
            }
            if artifact.size_bytes == 0 {
                return Err(PlanError::InvalidArtifact(format!(
                    "{} 的大小无效",
                    artifact.id
                )));
            }
            if !artifact.url.starts_with("https://") {
                return Err(PlanError::InvalidArtifact(format!(
                    "{} 必须使用 https 下载",
                    artifact.id
                )));
            }
        }
        Ok(())
    }

    /// Record the user's approval and freeze the plan.
    pub fn freeze(&mut self) -> Result<String, PlanError> {
        if self.consented_at_unix_seconds.is_some() {
            return Err(PlanError::AlreadyConsented);
        }
        self.validate()?;
        let hash = self.content_hash();
        self.consented_at_unix_seconds = Some(unix_now());
        self.plan_hash = Some(hash.clone());
        Ok(hash)
    }

    /// Verify that the plan is still the one the user approved.
    pub fn verify_frozen(&self) -> Result<(), PlanError> {
        let Some(expected) = &self.plan_hash else {
            return Err(PlanError::NotConsented);
        };
        let actual = self.content_hash();
        if &actual != expected {
            return Err(PlanError::PlanChanged {
                expected: expected.clone(),
                actual,
            });
        }
        Ok(())
    }
}

/// The phases of an upgrade transaction.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum UpgradePhase {
    Discover,
    Plan,
    Consent,
    PreDownload,
    Verify,
    QuiesceOwnedProcesses,
    Backup,
    Stage,
    ApplyDependencies,
    MigrateDatabase,
    ValidateCandidate,
    SwitchStudio,
    HealthCheck,
    Complete,
    /// The transaction stopped and can be resumed.
    Paused,
    /// The two sides have to be reconciled by a human.
    NeedsReview,
    /// The transaction is about to be undone.
    RollbackPending,
    /// The transaction was undone.
    RolledBack,
    /// The transaction cannot proceed.
    Blocked,
}

impl UpgradePhase {
    /// The happy path, in order.
    pub const PATH: [UpgradePhase; 14] = [
        Self::Discover,
        Self::Plan,
        Self::Consent,
        Self::PreDownload,
        Self::Verify,
        Self::QuiesceOwnedProcesses,
        Self::Backup,
        Self::Stage,
        Self::ApplyDependencies,
        Self::MigrateDatabase,
        Self::ValidateCandidate,
        Self::SwitchStudio,
        Self::HealthCheck,
        Self::Complete,
    ];

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Discover => "discover",
            Self::Plan => "plan",
            Self::Consent => "consent",
            Self::PreDownload => "pre_download",
            Self::Verify => "verify",
            Self::QuiesceOwnedProcesses => "quiesce_owned_processes",
            Self::Backup => "backup",
            Self::Stage => "stage",
            Self::ApplyDependencies => "apply_dependencies",
            Self::MigrateDatabase => "migrate_database",
            Self::ValidateCandidate => "validate_candidate",
            Self::SwitchStudio => "switch_studio",
            Self::HealthCheck => "health_check",
            Self::Complete => "complete",
            Self::Paused => "paused",
            Self::NeedsReview => "needs_review",
            Self::RollbackPending => "rollback_pending",
            Self::RolledBack => "rolled_back",
            Self::Blocked => "blocked",
        }
    }

    pub fn parse(value: &str) -> Option<Self> {
        Some(match value {
            "discover" => Self::Discover,
            "plan" => Self::Plan,
            "consent" => Self::Consent,
            "pre_download" => Self::PreDownload,
            "verify" => Self::Verify,
            "quiesce_owned_processes" => Self::QuiesceOwnedProcesses,
            "backup" => Self::Backup,
            "stage" => Self::Stage,
            "apply_dependencies" => Self::ApplyDependencies,
            "migrate_database" => Self::MigrateDatabase,
            "validate_candidate" => Self::ValidateCandidate,
            "switch_studio" => Self::SwitchStudio,
            "health_check" => Self::HealthCheck,
            "complete" => Self::Complete,
            "paused" => Self::Paused,
            "needs_review" => Self::NeedsReview,
            "rollback_pending" => Self::RollbackPending,
            "rolled_back" => Self::RolledBack,
            "blocked" => Self::Blocked,
            _ => return None,
        })
    }

    /// A user facing label.
    pub fn user_label(self) -> &'static str {
        match self {
            Self::Discover => "检查当前安装",
            Self::Plan => "生成升级计划",
            Self::Consent => "等待用户确认",
            Self::PreDownload => "下载升级文件",
            Self::Verify => "校验下载文件",
            Self::QuiesceOwnedProcesses => "停止本安装的运行进程",
            Self::Backup => "备份数据",
            Self::Stage => "准备新版本",
            Self::ApplyDependencies => "安装依赖",
            Self::MigrateDatabase => "升级数据库",
            Self::ValidateCandidate => "验证新版本",
            Self::SwitchStudio => "切换到新版本",
            Self::HealthCheck => "确认新版本可用",
            Self::Complete => "升级完成",
            Self::Paused => "已暂停，可稍后继续",
            Self::NeedsReview => "需要人工核对",
            Self::RollbackPending => "正在回退",
            Self::RolledBack => "已回退到原版本",
            Self::Blocked => "无法继续，已停止",
        }
    }

    pub fn is_failure_exit(self) -> bool {
        matches!(
            self,
            Self::Paused
                | Self::NeedsReview
                | Self::RollbackPending
                | Self::RolledBack
                | Self::Blocked
        )
    }

    pub fn is_terminal(self) -> bool {
        matches!(self, Self::Complete | Self::RolledBack | Self::Blocked)
    }
}

/// What may be done about a failed upgrade.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RollbackDecision {
    /// Restoring the backup is safe.
    Restore,
    /// The new version already accepted writes; a human has to decide.
    Reconcile,
}

impl RollbackDecision {
    pub fn user_message(self) -> &'static str {
        match self {
            Self::Restore => "可以安全回退到原版本。",
            Self::Reconcile => "新版本已经写入新数据，不能直接覆盖；需要人工核对后再处理。",
        }
    }
}

/// Decide whether a rollback may overwrite the database.
///
/// The rule is deliberately one-sided: a rollback is only automatic while the
/// new version has not accepted any write. Once it has, the two sides have to
/// be reconciled by a person, because a blind restore silently discards work.
pub fn decide_rollback(database_written_since_backup: bool) -> RollbackDecision {
    if database_written_since_backup {
        RollbackDecision::Reconcile
    } else {
        RollbackDecision::Restore
    }
}

/// Drives one upgrade transaction and records every step in the journal.
pub struct UpgradeTransaction {
    transaction_id: String,
    plan: UpgradePlan,
    phase: UpgradePhase,
}

impl UpgradeTransaction {
    pub fn new(transaction_id: &str, plan: UpgradePlan) -> Self {
        Self {
            transaction_id: transaction_id.to_owned(),
            plan,
            phase: UpgradePhase::Discover,
        }
    }

    pub fn phase(&self) -> UpgradePhase {
        self.phase
    }

    pub fn plan(&self) -> &UpgradePlan {
        &self.plan
    }

    pub fn transaction_id(&self) -> &str {
        &self.transaction_id
    }

    /// Move to the next phase of the happy path.
    ///
    /// The phase is written to the journal before it is entered, so a crash
    /// between the two leaves a record that says which phase was in flight.
    pub fn advance(
        &mut self,
        journal: &Journal,
        detail: Value,
    ) -> Result<UpgradePhase, crate::journal::JournalError> {
        let next = match self.phase {
            UpgradePhase::Complete => return Ok(self.phase),
            other => {
                let index = UpgradePhase::PATH
                    .iter()
                    .position(|phase| *phase == other)
                    .unwrap_or(0);
                UpgradePhase::PATH
                    .get(index + 1)
                    .copied()
                    .unwrap_or(UpgradePhase::Complete)
            }
        };
        journal.append(
            &self.transaction_id,
            next.as_str(),
            JournalStatus::Started,
            detail,
        )?;
        self.phase = next;
        Ok(next)
    }

    /// Record a successful phase.
    pub fn complete_phase(
        &self,
        journal: &Journal,
        detail: Value,
    ) -> Result<(), crate::journal::JournalError> {
        journal
            .append(
                &self.transaction_id,
                self.phase.as_str(),
                JournalStatus::Completed,
                detail,
            )
            .map(|_| ())
    }

    /// Leave the happy path with a failure.
    pub fn fail(
        &mut self,
        journal: &Journal,
        phase: UpgradePhase,
        detail: Value,
    ) -> Result<(), crate::journal::JournalError> {
        let status = match phase {
            UpgradePhase::NeedsReview => JournalStatus::NeedsReview,
            UpgradePhase::Blocked => JournalStatus::Blocked,
            _ => JournalStatus::Failed,
        };
        journal.append(&self.transaction_id, phase.as_str(), status, detail)?;
        self.phase = phase;
        Ok(())
    }

    /// Decide what may happen to the data after a failure.
    pub fn rollback_decision(&self, database_written_since_backup: bool) -> RollbackDecision {
        decide_rollback(database_written_since_backup)
    }
}

/// Where a staged artifact is verified before it is allowed to be used.
pub fn verify_staged_artifact(
    path: &Path,
    expected_sha256: &str,
    expected_size: u64,
) -> Result<(), VerifyError> {
    use sha2::Digest as _;
    let metadata =
        std::fs::metadata(path).map_err(|error| VerifyError::Unreadable(error.to_string()))?;
    if metadata.len() != expected_size {
        return Err(VerifyError::SizeMismatch {
            expected: expected_size,
            actual: metadata.len(),
        });
    }
    let mut file =
        std::fs::File::open(path).map_err(|error| VerifyError::Unreadable(error.to_string()))?;
    let mut hasher = Sha256::new();
    std::io::copy(&mut file, &mut hasher)
        .map_err(|error| VerifyError::Unreadable(error.to_string()))?;
    let actual = hasher
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    if !actual.eq_ignore_ascii_case(expected_sha256) {
        return Err(VerifyError::ChecksumMismatch {
            expected: expected_sha256.to_owned(),
            actual,
        });
    }
    Ok(())
}

#[derive(Debug)]
pub enum VerifyError {
    Unreadable(String),
    SizeMismatch { expected: u64, actual: u64 },
    ChecksumMismatch { expected: String, actual: String },
}

impl std::fmt::Display for VerifyError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Unreadable(message) => write!(formatter, "无法读取已下载文件：{message}"),
            Self::SizeMismatch { expected, actual } => {
                write!(formatter, "文件大小不符（应为 {expected}，实际 {actual}）")
            }
            Self::ChecksumMismatch { expected, actual } => write!(
                formatter,
                "文件校验和不符（应为 {expected}，实际 {actual}）"
            ),
        }
    }
}

impl std::error::Error for VerifyError {}

fn unix_now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::path::Path;

    use serde_json::json;
    use sha2::{Digest, Sha256};
    use tempfile::{TempDir, tempdir_in};

    use super::{
        JournalStatus, PlanError, RollbackDecision, UpgradePhase, UpgradePlan, UpgradeTransaction,
        decide_rollback, verify_staged_artifact,
    };
    use crate::journal::Journal;

    fn isolated_tempdir() -> TempDir {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("target/test-tmp");
        fs::create_dir_all(&root).unwrap();
        tempdir_in(root).unwrap()
    }

    fn sample_plan() -> UpgradePlan {
        UpgradePlan::new("inst-test", "1.2.3", "manifest-abc")
            .with_artifact(super::PlannedArtifact {
                id: "studio".to_owned(),
                url: "https://example.invalid/studio.zip".to_owned(),
                sha256: "a".repeat(64),
                size_bytes: 1024,
                staged_path: Path::new("/tmp/studio.zip").to_path_buf(),
            })
            .with_disk_budget(10 * 1024 * 1024)
    }

    #[test]
    fn a_plan_hash_does_not_change_when_it_is_frozen() {
        let mut plan = sample_plan();
        let before = plan.content_hash();
        plan.consented_at_unix_seconds = Some(1234);
        plan.plan_hash = Some(before.clone());
        assert_eq!(plan.content_hash(), before);
    }

    #[test]
    fn changing_the_plan_after_consent_changes_its_hash() {
        let mut plan = sample_plan();
        let before = plan.content_hash();
        plan.consented_at_unix_seconds = Some(1234);
        plan.plan_hash = Some(before.clone());
        plan.target_studio_version = "9.9.9".to_owned();
        assert_ne!(plan.content_hash(), before);
    }

    #[test]
    fn a_plan_with_a_bad_artifact_is_refused() {
        let mut plan = sample_plan();
        plan.artifacts[0].sha256 = "not-a-digest".to_owned();
        assert!(matches!(
            plan.validate(),
            Err(PlanError::InvalidArtifact(_))
        ));

        let mut plan = sample_plan();
        plan.artifacts[0].url = "http://example.invalid/studio.zip".to_owned();
        assert!(matches!(
            plan.validate(),
            Err(PlanError::InvalidArtifact(_))
        ));

        let mut plan = sample_plan();
        plan.artifacts.clear();
        plan.disk_budget_bytes = 0;
        assert!(matches!(plan.validate(), Err(PlanError::InvalidBudget)));
    }

    #[test]
    fn a_frozen_plan_cannot_be_frozen_twice() {
        let mut plan = sample_plan();
        plan.consented_at_unix_seconds = Some(1);
        assert!(matches!(plan.freeze(), Err(PlanError::AlreadyConsented)));
    }

    #[test]
    fn the_phase_path_reaches_completion() {
        let mut phase = UpgradePhase::Discover;
        let mut seen = vec![phase];
        loop {
            let index = super::UpgradePhase::PATH
                .iter()
                .position(|candidate| *candidate == phase)
                .unwrap();
            let Some(next) = super::UpgradePhase::PATH.get(index + 1) else {
                break;
            };
            if *next == phase {
                break;
            }
            phase = *next;
            seen.push(phase);
            if phase == UpgradePhase::Complete {
                break;
            }
        }
        assert_eq!(seen.last(), Some(&UpgradePhase::Complete));
        assert!(UpgradePhase::Complete.is_terminal());
        assert!(UpgradePhase::Blocked.is_failure_exit());
    }

    #[test]
    fn a_rollback_is_refused_once_the_new_version_wrote_data() {
        assert_eq!(decide_rollback(false), RollbackDecision::Restore);
        assert_eq!(decide_rollback(true), RollbackDecision::Reconcile);
        assert!(RollbackDecision::Reconcile.user_message().contains("人工"));
    }

    #[test]
    fn every_transaction_step_is_written_to_the_journal() {
        let temp = isolated_tempdir();
        let journal = Journal::new(temp.path()).unwrap();
        let mut transaction = UpgradeTransaction::new("tx-1", sample_plan());
        transaction.advance(&journal, json!({"step": 1})).unwrap();
        transaction.advance(&journal, json!({"step": 2})).unwrap();
        transaction
            .complete_phase(&journal, json!({"done": true}))
            .unwrap();

        let records = journal.records_of("tx-1").unwrap();
        // Two phase entries plus one completion entry.
        assert_eq!(records.len(), 3);
        assert_eq!(records[0].status, JournalStatus::Started);
        assert_eq!(records.last().unwrap().status, JournalStatus::Completed);
    }

    #[test]
    fn a_blocked_transaction_is_visible_in_the_journal() {
        let temp = isolated_tempdir();
        let journal = Journal::new(temp.path()).unwrap();
        let mut transaction = UpgradeTransaction::new("tx-2", sample_plan());
        transaction
            .fail(
                &journal,
                UpgradePhase::Blocked,
                json!({"reason": "生产任务进行中"}),
            )
            .unwrap();
        let records = journal.records_of("tx-2").unwrap();
        assert_eq!(records.last().unwrap().status, JournalStatus::Blocked);
        assert_eq!(transaction.phase(), UpgradePhase::Blocked);
    }

    #[test]
    fn a_staged_artifact_is_verified_by_size_and_digest() {
        let temp = isolated_tempdir();
        let path = temp.path().join("artifact.bin");
        let payload = b"shine launcher artifact";
        fs::write(&path, payload).unwrap();
        let digest = Sha256::digest(payload)
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>();

        verify_staged_artifact(&path, &digest, payload.len() as u64).unwrap();
        assert!(verify_staged_artifact(&path, &"b".repeat(64), payload.len() as u64).is_err());
        assert!(verify_staged_artifact(&path, &digest, 999).is_err());
        assert!(verify_staged_artifact(&path, &digest, payload.len() as u64 - 1).is_err());
    }
}
