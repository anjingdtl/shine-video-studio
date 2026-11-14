use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use fs2::FileExt;
use serde::{Deserialize, Serialize};

use crate::identity::{
    self, IdentityResolution, is_legacy_path_derived_id, is_stable_installation_id,
    legacy_path_derived_id,
};
use crate::path_safety::{reject_reparse_components, validate_local_absolute_path};

const REGISTRY_SCHEMA_VERSION: u32 = 2;
const LEGACY_REGISTRY_SCHEMA_VERSION: u32 = 1;
const MAX_REGISTRY_BYTES: u64 = 2 * 1024 * 1024;
static TEMP_SEQUENCE: AtomicU64 = AtomicU64::new(0);

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum UpdateChannel {
    #[default]
    Stable,
    Beta,
    Development,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DiscoverySource {
    /// The user pointed the launcher at the directory.
    #[default]
    #[serde(alias = "legacy_directory_scan", alias = "LegacyDirectoryScan")]
    UserSelected,
    /// Carried over from a launcher 0.1.x registry record.
    #[serde(alias = "source_checkout", alias = "release_package")]
    MigratedLegacy,
    /// Found automatically on this machine.
    AutoDetected,
}

/// One registered Shine installation on this machine.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RegisteredInstallation {
    /// Stable, path independent installation identity.
    pub installation_id: String,
    pub install_root: PathBuf,
    pub data_root: Option<PathBuf>,
    pub bootstrap_root: Option<PathBuf>,
    pub channel: UpdateChannel,
    pub discovery_source: DiscoverySource,
    pub created_at_unix_seconds: u64,
    pub last_scanned_unix_seconds: u64,
    pub runtime_ready: bool,
    /// Retired launcher 0.1.x ids that must keep resolving to this record.
    #[serde(default)]
    pub legacy_installation_ids: Vec<String>,
    /// Studio version currently placed in `install_root`.
    #[serde(default)]
    pub studio_version: Option<String>,
    /// Studio commit currently placed in `install_root`.
    #[serde(default)]
    pub studio_commit: Option<String>,
    /// Last Studio version proven healthy; the rollback anchor.
    #[serde(default)]
    pub last_known_good_version: Option<String>,
    /// Identifier of the recovery receipt that last touched this installation.
    #[serde(default)]
    pub recovery_receipt: Option<String>,
    /// True when the install root is no longer present on disk.
    #[serde(default)]
    pub missing_install_root: bool,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InstallationRegistry {
    pub schema_version: u32,
    pub installations: Vec<RegisteredInstallation>,
}

#[derive(Debug)]
pub enum RegistryError {
    Io(std::io::Error),
    Json(serde_json::Error),
    InvalidPath(String),
    UnsupportedSchema(u32),
    InvalidRecord(String),
}

impl std::fmt::Display for RegistryError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Io(error) => write!(formatter, "注册记录读写失败：{error}"),
            Self::Json(error) => write!(formatter, "注册记录格式无效：{error}"),
            Self::InvalidPath(message) => formatter.write_str(message),
            Self::UnsupportedSchema(version) => write!(formatter, "不支持安装注册表版本 {version}"),
            Self::InvalidRecord(message) => formatter.write_str(message),
        }
    }
}

impl std::error::Error for RegistryError {}

impl From<std::io::Error> for RegistryError {
    fn from(error: std::io::Error) -> Self {
        Self::Io(error)
    }
}

impl From<serde_json::Error> for RegistryError {
    fn from(error: serde_json::Error) -> Self {
        Self::Json(error)
    }
}

/// The launcher 0.1.x registry record.
///
/// It is kept as its own type because the 0.1.x wire format has a different
/// field set. Deserializing an old registry into the current record type
/// would simply fail on the first file a returning user opens.
#[derive(Clone, Debug, Deserialize)]
struct LegacyRegisteredInstallation {
    installation_id: String,
    install_root: PathBuf,
    #[serde(default)]
    data_root: Option<PathBuf>,
    #[serde(default)]
    bootstrap_root: Option<PathBuf>,
    #[serde(default)]
    channel: UpdateChannel,
    #[serde(default)]
    last_scanned_unix_seconds: u64,
    #[serde(default)]
    runtime_ready: bool,
}

impl InstallationRegistry {
    pub fn empty() -> Self {
        Self {
            schema_version: REGISTRY_SCHEMA_VERSION,
            installations: Vec::new(),
        }
    }

    /// Load the registry, migrating a launcher 0.1.x registry in memory.
    ///
    /// A 0.1.x record used a path derived id. The migration mints a stable id
    /// for it and keeps the retired id as an alias, so a reference written by
    /// the previous launcher still resolves to the same installation instead
    /// of silently pointing at nothing.
    pub fn load(path: &Path) -> Result<Self, RegistryError> {
        validate_registry_path(path)?;
        let mut file = match File::open(path) {
            Ok(file) => file,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(Self::empty()),
            Err(error) => return Err(error.into()),
        };
        let metadata = file.metadata()?;
        if metadata.len() > MAX_REGISTRY_BYTES {
            return Err(RegistryError::InvalidRecord(
                "注册记录超过大小限制".to_owned(),
            ));
        }
        let mut content = Vec::with_capacity(metadata.len() as usize);
        file.read_to_end(&mut content)?;
        let document: serde_json::Value = serde_json::from_slice(&content)?;
        let schema_version = document
            .get("schema_version")
            .and_then(serde_json::Value::as_u64)
            .unwrap_or(0);
        if schema_version == u64::from(LEGACY_REGISTRY_SCHEMA_VERSION) {
            return Ok(Self::migrate_legacy_document(&document));
        }
        let registry: Self = serde_json::from_value(document)?;
        if registry.schema_version != REGISTRY_SCHEMA_VERSION {
            return Err(RegistryError::UnsupportedSchema(registry.schema_version));
        }
        registry.validate()?;
        Ok(registry)
    }

    /// Migrate a launcher 0.1.x registry document into the current format.
    fn migrate_legacy_document(document: &serde_json::Value) -> Self {
        let legacy_records: Vec<LegacyRegisteredInstallation> = document
            .get("installations")
            .and_then(serde_json::Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .filter_map(|item| {
                        serde_json::from_value::<LegacyRegisteredInstallation>(item.clone()).ok()
                    })
                    .collect()
            })
            .unwrap_or_default();

        let mut migrated = Vec::with_capacity(legacy_records.len());
        for record in legacy_records {
            let retired_id = record.installation_id.clone();
            // A 0.1.x record may claim the runtime was ready. That claim
            // came from a file scan, which cannot prove a running stack, so
            // it is deliberately not carried over.
            let _legacy_ready_claim = record.runtime_ready;
            let stable_id = identity::generate_installation_id()
                .unwrap_or_else(|_| legacy_path_derived_id(&record.install_root));
            let mut aliases = Vec::new();
            if is_legacy_path_derived_id(&retired_id) {
                aliases.push(retired_id);
            }
            migrated.push(RegisteredInstallation {
                installation_id: stable_id,
                install_root: record.install_root,
                data_root: record.data_root,
                bootstrap_root: record.bootstrap_root,
                channel: record.channel,
                // Every migrated record says so. How the old launcher found
                // the directory is not worth preserving; the user can rescan
                // at any time, and the retired id is kept as an alias.
                discovery_source: DiscoverySource::MigratedLegacy,
                created_at_unix_seconds: record.last_scanned_unix_seconds,
                last_scanned_unix_seconds: record.last_scanned_unix_seconds,
                runtime_ready: false,
                legacy_installation_ids: aliases,
                studio_version: None,
                studio_commit: None,
                last_known_good_version: None,
                recovery_receipt: None,
                missing_install_root: false,
            });
        }
        Self {
            schema_version: REGISTRY_SCHEMA_VERSION,
            installations: migrated,
        }
    }

    /// Register (or refresh) the installation located at `install_root`.
    ///
    /// The stable identity is resolved from the identity file inside the
    /// install root, so re-scanning the same directory never creates a second
    /// installation, and scanning a moved directory keeps the original id.
    pub fn register_install_root(
        &mut self,
        install_root: &Path,
        data_root: Option<PathBuf>,
        bootstrap_root: Option<PathBuf>,
        channel: UpdateChannel,
        discovery_source: DiscoverySource,
    ) -> Result<RegisteredInstallation, RegistryError> {
        let known_ids: Vec<String> = self
            .installations
            .iter()
            .map(|record| record.installation_id.clone())
            .collect();
        let resolution = identity::resolve_install_identity(install_root, &known_ids)
            .map_err(|error| RegistryError::InvalidRecord(error.to_string()))?;
        let resolved_identity = match resolution {
            IdentityResolution::Existing { identity, .. } => identity,
            IdentityResolution::Created { identity, .. } => identity,
        };
        let now = unix_now();

        if let Some(existing) = self
            .installations
            .iter_mut()
            .find(|record| record.installation_id == resolved_identity.installation_id)
        {
            // The same installation, possibly at a new path: the identity file
            // travelled with the directory.
            existing.install_root = install_root.to_path_buf();
            existing.data_root = data_root;
            existing.bootstrap_root = bootstrap_root;
            existing.channel = channel;
            existing.last_scanned_unix_seconds = now;
            existing.missing_install_root = false;
            return Ok(existing.clone());
        }

        let mut aliases = Vec::new();
        let retired = legacy_path_derived_id(install_root);
        if is_legacy_path_derived_id(&retired) {
            aliases.push(retired);
        }

        let record = RegisteredInstallation {
            installation_id: resolved_identity.installation_id,
            install_root: install_root.to_path_buf(),
            data_root,
            bootstrap_root,
            channel,
            discovery_source,
            created_at_unix_seconds: resolved_identity.created_at_unix_seconds.max(now),
            last_scanned_unix_seconds: now,
            runtime_ready: false,
            legacy_installation_ids: aliases,
            studio_version: None,
            studio_commit: None,
            last_known_good_version: None,
            recovery_receipt: None,
            missing_install_root: false,
        };
        self.installations.push(record.clone());
        self.installations
            .sort_by(|left, right| left.installation_id.cmp(&right.installation_id));
        Ok(record)
    }

    /// Resolve any known id (current or retired) to its installation.
    pub fn resolve(&self, installation_id: &str) -> Option<&RegisteredInstallation> {
        self.installations
            .iter()
            .find(|record| record.installation_id == installation_id)
            .or_else(|| {
                self.installations.iter().find(|record| {
                    record
                        .legacy_installation_ids
                        .iter()
                        .any(|alias| alias == installation_id)
                })
            })
    }

    /// The installation the launcher should offer by default.
    pub fn preferred(&self) -> Option<&RegisteredInstallation> {
        self.installations
            .iter()
            .find(|record| !record.missing_install_root)
            .or_else(|| self.installations.first())
    }

    /// Flag installations whose directory disappeared. Records are never
    /// dropped: a user who re-attaches the drive gets the same identity back.
    pub fn refresh_presence(&mut self) {
        for record in &mut self.installations {
            record.missing_install_root = !record.install_root.is_dir();
        }
    }

    pub fn upsert_atomic(
        path: &Path,
        record: RegisteredInstallation,
    ) -> Result<Self, RegistryError> {
        validate_registry_path(path)?;
        validate_record(&record)?;
        let parent = path
            .parent()
            .ok_or_else(|| RegistryError::InvalidPath("注册表文件必须有父目录".to_owned()))?;
        reject_reparse_components(parent).map_err(|_| {
            RegistryError::InvalidPath("注册表目录经过符号链接或重解析点".to_owned())
        })?;
        fs::create_dir_all(parent)?;
        reject_reparse_components(parent).map_err(|_| {
            RegistryError::InvalidPath("注册表目录经过符号链接或重解析点".to_owned())
        })?;

        let lock_path = path.with_extension("lock");
        reject_reparse_components(&lock_path).map_err(|_| {
            RegistryError::InvalidPath("注册表锁文件经过符号链接或重解析点".to_owned())
        })?;
        let lock = OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(lock_path)?;
        lock.lock_exclusive()?;

        let update_result = (|| {
            let mut registry = Self::load(path)?;
            if let Some(existing) = registry
                .installations
                .iter_mut()
                .find(|item| item.installation_id == record.installation_id)
            {
                *existing = record;
            } else {
                registry.installations.push(record);
            }
            registry
                .installations
                .sort_by(|left, right| left.installation_id.cmp(&right.installation_id));
            registry.validate()?;
            write_registry_atomic(path, &registry)?;
            Ok(registry)
        })();

        let unlock_result = FileExt::unlock(&lock);
        match (update_result, unlock_result) {
            (Ok(registry), Ok(())) => Ok(registry),
            (Err(error), _) => Err(error),
            (Ok(_), Err(error)) => Err(error.into()),
        }
    }

    /// Register an installation and persist the registry atomically.
    ///
    /// The read, the registration and the write all happen under one cross
    /// process lock. Doing the read outside the lock would let two launchers
    /// that start at the same moment each write a registry that contains only
    /// their own installation, and one of the two would be silently lost.
    #[allow(clippy::too_many_arguments)]
    pub fn register_install_root_atomic(
        path: &Path,
        install_root: &Path,
        data_root: Option<PathBuf>,
        bootstrap_root: Option<PathBuf>,
        channel: UpdateChannel,
        discovery_source: DiscoverySource,
    ) -> Result<Self, RegistryError> {
        let parent = path
            .parent()
            .ok_or_else(|| RegistryError::InvalidPath("注册表文件必须有父目录".to_owned()))?;
        validate_registry_path(path)?;
        fs::create_dir_all(parent)?;
        reject_reparse_components(parent).map_err(|_| {
            RegistryError::InvalidPath("注册表目录经过符号链接或重解析点".to_owned())
        })?;

        let lock_path = path.with_extension("lock");
        reject_reparse_components(&lock_path).map_err(|_| {
            RegistryError::InvalidPath("注册表锁文件经过符号链接或重解析点".to_owned())
        })?;
        let lock = OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(lock_path)?;
        lock.lock_exclusive()?;

        let update_result = (|| {
            let mut registry = Self::load(path)?;
            registry.register_install_root(
                install_root,
                data_root,
                bootstrap_root,
                channel,
                discovery_source,
            )?;
            write_registry_atomic(path, &registry)?;
            Ok(registry)
        })();

        let unlock_result = FileExt::unlock(&lock);
        match (update_result, unlock_result) {
            (Ok(registry), Ok(())) => Ok(registry),
            (Err(error), _) => Err(error),
            (Ok(_), Err(error)) => Err(error.into()),
        }
    }

    /// Persist the whole registry atomically, under the same cross process
    /// lock the launcher uses for single record updates.
    pub fn save_atomic(path: &Path, registry: &InstallationRegistry) -> Result<(), RegistryError> {
        validate_registry_path(path)?;
        registry.validate()?;
        let parent = path
            .parent()
            .ok_or_else(|| RegistryError::InvalidPath("注册表文件必须有父目录".to_owned()))?;
        reject_reparse_components(parent).map_err(|_| {
            RegistryError::InvalidPath("注册表目录经过符号链接或重解析点".to_owned())
        })?;
        fs::create_dir_all(parent)?;
        reject_reparse_components(parent).map_err(|_| {
            RegistryError::InvalidPath("注册表目录经过符号链接或重解析点".to_owned())
        })?;

        let lock_path = path.with_extension("lock");
        reject_reparse_components(&lock_path).map_err(|_| {
            RegistryError::InvalidPath("注册表锁文件经过符号链接或重解析点".to_owned())
        })?;
        let lock = OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(lock_path)?;
        lock.lock_exclusive()?;

        let write_result = write_registry_atomic(path, registry);
        let unlock_result = FileExt::unlock(&lock);
        match (write_result, unlock_result) {
            (Ok(()), Ok(())) => Ok(()),
            (Err(error), _) => Err(error),
            (Ok(()), Err(error)) => Err(error.into()),
        }
    }

    pub fn validate(&self) -> Result<(), RegistryError> {
        if self.schema_version != REGISTRY_SCHEMA_VERSION {
            return Err(RegistryError::UnsupportedSchema(self.schema_version));
        }
        let mut seen = std::collections::HashSet::new();
        for record in &self.installations {
            validate_record(record)?;
            if !seen.insert(record.installation_id.as_str()) {
                return Err(RegistryError::InvalidRecord(
                    "安装注册表含重复安装 ID".to_owned(),
                ));
            }
            for alias in &record.legacy_installation_ids {
                if !is_legacy_path_derived_id(alias) {
                    return Err(RegistryError::InvalidRecord(
                        "安装注册表含无效的历史身份别名".to_owned(),
                    ));
                }
            }
        }
        Ok(())
    }
}

impl RegisteredInstallation {
    /// The data directory this installation uses.
    pub fn effective_data_root(&self) -> PathBuf {
        self.data_root
            .clone()
            .unwrap_or_else(|| self.install_root.join(".shine-data"))
    }

    /// True when the identity file inside the install root still agrees with
    /// the recorded identity.
    pub fn identity_matches_install_root(&self) -> bool {
        identity::read_identity_file(&self.install_root)
            .ok()
            .flatten()
            .map(|identity| identity.installation_id == self.installation_id)
            .unwrap_or(false)
    }
}

pub fn default_registry_path() -> Result<PathBuf, RegistryError> {
    launcher_state_dir().map(|directory| directory.join("installations-v2.json"))
}

/// The launcher's own private state directory, separate from Studio data.
pub fn launcher_state_dir() -> Result<PathBuf, RegistryError> {
    #[cfg(windows)]
    let base = std::env::var_os("LOCALAPPDATA")
        .or_else(|| std::env::var_os("APPDATA"))
        .map(PathBuf::from)
        .ok_or_else(|| RegistryError::InvalidPath("Windows 用户配置目录不可用".to_owned()))?;

    #[cfg(not(windows))]
    let base = std::env::var_os("XDG_STATE_HOME")
        .map(PathBuf::from)
        .or_else(|| {
            std::env::var_os("HOME").map(|home| PathBuf::from(home).join(".local").join("state"))
        })
        .ok_or_else(|| RegistryError::InvalidPath("XDG_STATE_HOME 未配置".to_owned()))?;

    Ok(base.join("ShineLauncher"))
}

fn validate_registry_path(path: &Path) -> Result<(), RegistryError> {
    validate_local_absolute_path(path).map_err(RegistryError::InvalidPath)?;
    if path
        .components()
        .any(|component| component == std::path::Component::ParentDir)
    {
        return Err(RegistryError::InvalidPath(
            "注册表路径不能包含 ..".to_owned(),
        ));
    }
    reject_reparse_components(path)
        .map_err(|_| RegistryError::InvalidPath("注册表路径经过符号链接或重解析点".to_owned()))
}

fn validate_record(record: &RegisteredInstallation) -> Result<(), RegistryError> {
    validate_path_field(&record.install_root, "安装目录")?;
    if let Some(path) = record.data_root.as_deref() {
        validate_path_field(path, "数据目录")?;
    }
    if let Some(path) = record.bootstrap_root.as_deref() {
        validate_path_field(path, "依赖目录")?;
    }
    if !is_stable_installation_id(&record.installation_id) {
        return Err(RegistryError::InvalidRecord(
            "安装 ID 不是受支持的稳定身份格式".to_owned(),
        ));
    }
    if record.runtime_ready {
        return Err(RegistryError::InvalidRecord(
            "文件目录扫描不能证明运行时 READY".to_owned(),
        ));
    }
    Ok(())
}

fn validate_path_field(path: &Path, name: &str) -> Result<(), RegistryError> {
    validate_local_absolute_path(path)
        .map_err(|_| RegistryError::InvalidPath(format!("{name} 必须是本机绝对路径且不包含 ..")))
}

fn unix_now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

fn write_registry_atomic(
    path: &Path,
    registry: &InstallationRegistry,
) -> Result<(), RegistryError> {
    let parent = path
        .parent()
        .ok_or_else(|| RegistryError::InvalidPath("注册表文件必须有父目录".to_owned()))?;
    let bytes = serde_json::to_vec_pretty(registry)?;
    if bytes.len() as u64 > MAX_REGISTRY_BYTES {
        return Err(RegistryError::InvalidRecord(
            "注册记录超过大小限制".to_owned(),
        ));
    }

    let sequence = TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed);
    let temporary = parent.join(format!(
        ".installations-{}-{sequence}.tmp",
        std::process::id()
    ));
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temporary)?;
    let write_result = (|| {
        file.write_all(&bytes)?;
        file.sync_all()?;
        drop(file);
        fs::rename(&temporary, path)?;
        #[cfg(unix)]
        File::open(parent)?.sync_all()?;
        Ok::<(), std::io::Error>(())
    })();
    if write_result.is_err() {
        let _ = fs::remove_file(temporary);
    }
    write_result.map_err(RegistryError::Io)
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::path::Path;
    use std::sync::{Arc, Barrier};
    use std::thread;

    use tempfile::{TempDir, tempdir_in};

    use super::{
        DiscoverySource, InstallationRegistry, RegisteredInstallation, UpdateChannel,
        default_registry_path,
    };
    use crate::identity::{identity_file_path, read_identity_file};

    fn isolated_tempdir() -> TempDir {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("target/test-tmp");
        fs::create_dir_all(&root).unwrap();
        tempdir_in(root).unwrap()
    }

    fn make_fixture(root: &Path) {
        fs::create_dir_all(root.join("backend")).unwrap();
        fs::create_dir_all(root.join("frontend")).unwrap();
        fs::write(root.join("backend/pyproject.toml"), "[project]\n").unwrap();
        fs::write(root.join("frontend/package.json"), "{}\n").unwrap();
    }

    #[test]
    fn registration_round_trip_is_idempotent_and_never_sets_ready() {
        let temp = isolated_tempdir();
        let root = temp.path().join("studio");
        make_fixture(&root);
        let registry_path = temp.path().join("state/installations-v2.json");

        let mut registry = InstallationRegistry::empty();
        let record = registry
            .register_install_root(
                &root,
                None,
                None,
                UpdateChannel::Stable,
                DiscoverySource::UserSelected,
            )
            .unwrap();
        assert!(!record.runtime_ready);

        InstallationRegistry::save_atomic(&registry_path, &registry).unwrap();
        let loaded = InstallationRegistry::load(&registry_path).unwrap();
        assert_eq!(loaded.installations.len(), 1);
        assert_eq!(
            loaded.installations[0].installation_id,
            record.installation_id
        );
        assert!(!loaded.installations[0].runtime_ready);
        assert_eq!(
            loaded.installations[0].discovery_source,
            DiscoverySource::UserSelected
        );
    }

    #[test]
    fn rescanning_the_same_directory_never_creates_a_second_installation() {
        let temp = isolated_tempdir();
        let root = temp.path().join("studio");
        make_fixture(&root);
        let mut registry = InstallationRegistry::empty();
        let first = registry
            .register_install_root(
                &root,
                None,
                None,
                UpdateChannel::Stable,
                DiscoverySource::UserSelected,
            )
            .unwrap()
            .installation_id
            .clone();
        let second = registry
            .register_install_root(
                &root,
                None,
                None,
                UpdateChannel::Stable,
                DiscoverySource::UserSelected,
            )
            .unwrap()
            .installation_id
            .clone();
        assert_eq!(first, second);
        assert_eq!(registry.installations.len(), 1);
    }

    #[test]
    fn moving_the_install_root_keeps_the_installation_identity() {
        let temp = isolated_tempdir();
        let original = temp.path().join("disk-a/studio");
        let moved = temp.path().join("disk-b/studio");
        make_fixture(&original);

        let mut registry = InstallationRegistry::empty();
        let original_id = registry
            .register_install_root(
                &original,
                None,
                None,
                UpdateChannel::Stable,
                DiscoverySource::UserSelected,
            )
            .unwrap()
            .installation_id
            .clone();

        fs::create_dir_all(moved.parent().unwrap()).unwrap();
        fs::rename(&original, &moved).unwrap();

        let moved_id = registry
            .register_install_root(
                &moved,
                None,
                None,
                UpdateChannel::Stable,
                DiscoverySource::UserSelected,
            )
            .unwrap()
            .installation_id
            .clone();

        assert_eq!(original_id, moved_id);
        assert_eq!(registry.installations.len(), 1);
        assert_eq!(registry.installations[0].install_root, moved);
    }

    #[test]
    fn a_retired_path_derived_id_still_resolves_after_migration() {
        let temp = isolated_tempdir();
        let root = temp.path().join("studio");
        make_fixture(&root);
        let retired = crate::identity::legacy_path_derived_id(&root);

        let mut registry = InstallationRegistry::empty();
        let stable = registry
            .register_install_root(
                &root,
                None,
                None,
                UpdateChannel::Stable,
                DiscoverySource::UserSelected,
            )
            .unwrap()
            .installation_id
            .clone();

        assert!(registry.resolve(&stable).is_some());
        assert_eq!(
            registry
                .resolve(&retired)
                .map(|record| record.installation_id.clone()),
            Some(stable)
        );
    }

    #[test]
    fn a_version_one_registry_is_migrated_without_losing_the_installation() {
        let temp = isolated_tempdir();
        let root = temp.path().join("studio");
        make_fixture(&root);
        let retired = crate::identity::legacy_path_derived_id(&root);
        let path = temp.path().join("installations-v1.json");
        fs::write(
            &path,
            serde_json::json!({
                "schema_version": 1,
                "installations": [{
                    "installation_id": retired,
                    "install_root": root.to_string_lossy(),
                    "data_root": null,
                    "bootstrap_root": null,
                    "channel": "stable",
                    "discovery_source": "LegacyDirectoryScan",
                    "last_scanned_unix_seconds": 10,
                    "runtime_ready": false
                }]
            })
            .to_string(),
        )
        .unwrap();

        let registry = InstallationRegistry::load(&path).unwrap();
        assert_eq!(registry.schema_version, 2);
        assert_eq!(registry.installations.len(), 1);
        let record = &registry.installations[0];
        assert!(crate::identity::is_stable_installation_id(
            &record.installation_id
        ));
        assert!(record.legacy_installation_ids.contains(&retired));
        assert_eq!(
            registry.resolve(&retired).unwrap().installation_id,
            record.installation_id
        );
        assert_eq!(record.discovery_source, DiscoverySource::MigratedLegacy);
    }

    #[test]
    fn a_missing_install_root_is_flagged_but_never_dropped() {
        let temp = isolated_tempdir();
        let root = temp.path().join("studio");
        make_fixture(&root);
        let mut registry = InstallationRegistry::empty();
        registry
            .register_install_root(
                &root,
                None,
                None,
                UpdateChannel::Stable,
                DiscoverySource::UserSelected,
            )
            .unwrap();
        fs::remove_dir_all(&root).unwrap();
        registry.refresh_presence();
        assert_eq!(registry.installations.len(), 1);
        assert!(registry.installations[0].missing_install_root);
    }

    #[test]
    fn the_identity_file_lives_inside_the_install_root() {
        let temp = isolated_tempdir();
        let root = temp.path().join("studio");
        make_fixture(&root);
        let mut registry = InstallationRegistry::empty();
        let id = registry
            .register_install_root(
                &root,
                None,
                None,
                UpdateChannel::Stable,
                DiscoverySource::UserSelected,
            )
            .unwrap()
            .installation_id
            .clone();
        assert!(identity_file_path(&root).is_file());
        assert_eq!(
            read_identity_file(&root).unwrap().unwrap().installation_id,
            id
        );
        assert!(registry.installations[0].identity_matches_install_root());
    }

    #[test]
    fn a_registry_record_never_claims_runtime_readiness() {
        let temp = isolated_tempdir();
        let root = temp.path().join("studio");
        make_fixture(&root);
        let mut registry = InstallationRegistry::empty();
        let record = registry
            .register_install_root(
                &root,
                None,
                None,
                UpdateChannel::Stable,
                DiscoverySource::UserSelected,
            )
            .unwrap();
        let mut tampered: RegisteredInstallation = record.clone();
        tampered.runtime_ready = true;
        assert!(
            InstallationRegistry::upsert_atomic(
                &temp.path().join("state/installations-v2.json"),
                record
            )
            .is_ok()
        );
        let mut invalid = registry.clone();
        invalid.installations[0].runtime_ready = true;
        assert!(invalid.validate().is_err());
    }

    #[test]
    fn concurrent_registrations_do_not_drop_entries() {
        let temp = isolated_tempdir();
        let registry_path = Arc::new(temp.path().join("state/installations-v2.json"));
        let barrier = Arc::new(Barrier::new(4));
        let mut workers = Vec::new();
        for index in 0..4 {
            let root = temp.path().join(format!("studio-{index}"));
            make_fixture(&root);
            let path = Arc::clone(&registry_path);
            let start = Arc::clone(&barrier);
            workers.push(thread::spawn(move || {
                start.wait();
                InstallationRegistry::register_install_root_atomic(
                    &path,
                    &root,
                    None,
                    None,
                    UpdateChannel::Stable,
                    DiscoverySource::UserSelected,
                )
                .unwrap();
            }));
        }
        for worker in workers {
            worker.join().unwrap();
        }
        let loaded = InstallationRegistry::load(&registry_path).unwrap();
        assert_eq!(loaded.installations.len(), 4);
    }

    #[test]
    fn the_default_registry_path_is_user_scoped() {
        let path = default_registry_path().unwrap();
        assert!(path.ends_with("installations-v2.json"));
        assert!(path.is_absolute());
    }
}
