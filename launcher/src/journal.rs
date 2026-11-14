//! Durable transaction journal.
//!
//! An install or upgrade transaction must survive a launcher restart, a
//! machine reboot or a crashed script. The journal is the only place that
//! records what a transaction was doing, so the launcher can pick it up again
//! and either finish it, roll it back, or tell the user that a human has to
//! look at it.
//!
//! The journal is append-only JSON Lines. Every record is flushed and synced
//! before the call returns, so a record that was written is a record that a
//! later run will see.

use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use fs2::FileExt;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::path_safety::{reject_reparse_components, validate_local_absolute_path};

const MAX_JOURNAL_BYTES: u64 = 32 * 1024 * 1024;
const JOURNAL_FILE_NAME: &str = "launcher-journal.jsonl";
const TRANSACTION_LOCK_NAME: &str = "launcher-transaction.lock";

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum JournalStatus {
    /// The phase was entered.
    Started,
    /// The phase finished successfully.
    Completed,
    /// The phase failed and the transaction may be retried.
    Failed,
    /// The phase could not proceed and needs a decision.
    Blocked,
    /// The phase needs a human to reconcile two sides.
    NeedsReview,
}

impl JournalStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Started => "started",
            Self::Completed => "completed",
            Self::Failed => "failed",
            Self::Blocked => "blocked",
            Self::NeedsReview => "needs_review",
        }
    }
}

/// One journal entry.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct JournalRecord {
    pub sequence: u64,
    pub at_unix_seconds: u64,
    pub transaction_id: String,
    pub phase: String,
    pub status: JournalStatus,
    #[serde(default)]
    pub detail: Value,
}

/// Append-only, crash tolerant journal.
#[derive(Clone, Debug)]
pub struct Journal {
    path: PathBuf,
}

impl Journal {
    pub fn new(state_directory: &Path) -> Result<Self, JournalError> {
        validate_local_absolute_path(state_directory).map_err(JournalError::InvalidPath)?;
        Ok(Self {
            path: state_directory.join(JOURNAL_FILE_NAME),
        })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    /// Append one record and make sure it reached the disk.
    pub fn append(
        &self,
        transaction_id: &str,
        phase: &str,
        status: JournalStatus,
        detail: Value,
    ) -> Result<JournalRecord, JournalError> {
        if transaction_id.is_empty() || transaction_id.len() > 128 {
            return Err(JournalError::InvalidRecord("事务 ID 无效".to_owned()));
        }
        if phase.is_empty() || phase.len() > 64 {
            return Err(JournalError::InvalidRecord("阶段名称无效".to_owned()));
        }
        let parent = self
            .path
            .parent()
            .ok_or_else(|| JournalError::InvalidPath("日志目录无效".to_owned()))?;
        reject_reparse_components(parent)
            .map_err(|_| JournalError::InvalidPath("日志目录经过符号链接或重解析点".to_owned()))?;
        fs::create_dir_all(parent)?;
        reject_reparse_components(&self.path)
            .map_err(|_| JournalError::InvalidPath("日志文件经过符号链接或重解析点".to_owned()))?;

        let record = JournalRecord {
            sequence: next_sequence(&self.path)?,
            at_unix_seconds: unix_now(),
            transaction_id: transaction_id.to_owned(),
            phase: phase.to_owned(),
            status,
            detail,
        };
        let mut line = serde_json::to_vec(&record)
            .map_err(|error| JournalError::InvalidRecord(error.to_string()))?;
        line.push(b'\n');

        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)?;
        file.write_all(&line)?;
        file.sync_all()?;
        Ok(record)
    }

    /// Read every record, skipping a trailing partial line.
    pub fn read_all(&self) -> Result<Vec<JournalRecord>, JournalError> {
        if !self.path.is_file() {
            return Ok(Vec::new());
        }
        reject_reparse_components(&self.path)
            .map_err(|_| JournalError::InvalidPath("日志文件经过符号链接或重解析点".to_owned()))?;
        let metadata = fs::metadata(&self.path)?;
        if metadata.len() > MAX_JOURNAL_BYTES {
            return Err(JournalError::InvalidPath("日志文件超过大小上限".to_owned()));
        }
        let mut content = String::new();
        File::open(&self.path)?.read_to_string(&mut content)?;
        let mut records = Vec::new();
        for line in content.lines() {
            let trimmed = line.trim();
            if trimmed.is_empty() {
                continue;
            }
            match serde_json::from_str::<JournalRecord>(trimmed) {
                Ok(record) => records.push(record),
                // A torn final line is expected after a crash; it is skipped
                // instead of making the whole journal unreadable.
                Err(_) => continue,
            }
        }
        Ok(records)
    }

    /// The identifiers of every transaction the journal knows about, oldest
    /// first.
    pub fn transaction_ids(&self) -> Result<Vec<String>, JournalError> {
        let mut seen = Vec::new();
        for record in self.read_all()? {
            if !seen
                .iter()
                .any(|existing| existing == &record.transaction_id)
            {
                seen.push(record.transaction_id);
            }
        }
        Ok(seen)
    }

    /// The records of one transaction, in order.
    pub fn records_of(&self, transaction_id: &str) -> Result<Vec<JournalRecord>, JournalError> {
        Ok(self
            .read_all()?
            .into_iter()
            .filter(|record| record.transaction_id == transaction_id)
            .collect())
    }

    /// The last phase that was entered for a transaction.
    pub fn last_phase(&self, transaction_id: &str) -> Result<Option<String>, JournalError> {
        Ok(self
            .records_of(transaction_id)?
            .last()
            .map(|record| record.phase.clone()))
    }

    /// The last status recorded for a transaction.
    pub fn last_status(&self, transaction_id: &str) -> Result<Option<JournalStatus>, JournalError> {
        Ok(self
            .records_of(transaction_id)?
            .last()
            .map(|record| record.status))
    }
}

/// A cross process lock that guarantees only one install transaction touches
/// one installation at a time.
#[derive(Debug)]
pub struct TransactionLock {
    file: File,
    path: PathBuf,
}

impl TransactionLock {
    /// Take the lock, waiting up to `timeout` for a competing launcher.
    pub fn acquire(
        state_directory: &Path,
        installation_id: &str,
        timeout: std::time::Duration,
    ) -> Result<Self, JournalError> {
        validate_local_absolute_path(state_directory).map_err(JournalError::InvalidPath)?;
        fs::create_dir_all(state_directory)?;
        let path = state_directory.join(format!("{TRANSACTION_LOCK_NAME}-{installation_id}"));
        reject_reparse_components(&path).map_err(|_| {
            JournalError::InvalidPath("事务锁文件经过符号链接或重解析点".to_owned())
        })?;
        let file = OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(&path)?;
        let deadline = std::time::Instant::now() + timeout;
        loop {
            match FileExt::try_lock_exclusive(&file) {
                Ok(()) => break,
                Err(_) => {
                    if std::time::Instant::now() >= deadline {
                        return Err(JournalError::LockBusy(
                            "另一个启动器正在处理这个安装，请稍后再试。".to_owned(),
                        ));
                    }
                    std::thread::sleep(std::time::Duration::from_millis(100));
                }
            }
        }
        Ok(Self { file, path })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }
}

impl Drop for TransactionLock {
    fn drop(&mut self) {
        let _ = FileExt::unlock(&self.file);
    }
}

#[derive(Debug)]
pub enum JournalError {
    Io(std::io::Error),
    InvalidPath(String),
    InvalidRecord(String),
    LockBusy(String),
}

impl std::fmt::Display for JournalError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Io(error) => write!(formatter, "日志读写失败：{error}"),
            Self::InvalidPath(message) => formatter.write_str(message),
            Self::InvalidRecord(message) => formatter.write_str(message),
            Self::LockBusy(message) => formatter.write_str(message),
        }
    }
}

impl std::error::Error for JournalError {}

impl From<std::io::Error> for JournalError {
    fn from(error: std::io::Error) -> Self {
        Self::Io(error)
    }
}

fn next_sequence(path: &Path) -> Result<u64, JournalError> {
    if !path.is_file() {
        return Ok(1);
    }
    let content = fs::read_to_string(path)?;
    let mut highest = 0_u64;
    for line in content.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        if let Ok(record) = serde_json::from_str::<JournalRecord>(trimmed) {
            highest = highest.max(record.sequence);
        }
    }
    Ok(highest + 1)
}

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
    use std::sync::Arc;
    use std::thread;

    use serde_json::json;
    use tempfile::{TempDir, tempdir_in};

    use super::{Journal, JournalStatus, TransactionLock};

    fn isolated_tempdir() -> TempDir {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("target/test-tmp");
        fs::create_dir_all(&root).unwrap();
        tempdir_in(root).unwrap()
    }

    #[test]
    fn records_are_appended_in_order_and_survive_a_reopen() {
        let temp = isolated_tempdir();
        let journal = Journal::new(temp.path()).unwrap();
        journal
            .append("tx-1", "discover", JournalStatus::Started, json!({"a": 1}))
            .unwrap();
        journal
            .append("tx-1", "plan", JournalStatus::Completed, json!({"b": 2}))
            .unwrap();

        let reopened = Journal::new(temp.path()).unwrap();
        let records = reopened.read_all().unwrap();
        assert_eq!(records.len(), 2);
        assert_eq!(records[0].phase, "discover");
        assert_eq!(records[1].status, JournalStatus::Completed);
        assert_eq!(
            reopened.last_phase("tx-1").unwrap().as_deref(),
            Some("plan")
        );
        assert_eq!(
            reopened.last_status("tx-1").unwrap(),
            Some(JournalStatus::Completed)
        );
    }

    #[test]
    fn a_torn_final_line_does_not_destroy_the_journal() {
        let temp = isolated_tempdir();
        let journal = Journal::new(temp.path()).unwrap();
        journal
            .append("tx-1", "discover", JournalStatus::Started, json!({}))
            .unwrap();
        let mut file = fs::OpenOptions::new()
            .append(true)
            .open(journal.path())
            .unwrap();
        use std::io::Write as _;
        file.write_all(b"{\"sequence\": 2, \"at_unix_seco").unwrap();
        drop(file);

        let records = journal.read_all().unwrap();
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].transaction_id, "tx-1");
    }

    #[test]
    fn transaction_ids_are_reported_oldest_first_without_duplicates() {
        let temp = isolated_tempdir();
        let journal = Journal::new(temp.path()).unwrap();
        journal
            .append("tx-1", "discover", JournalStatus::Started, json!({}))
            .unwrap();
        journal
            .append("tx-2", "discover", JournalStatus::Started, json!({}))
            .unwrap();
        journal
            .append("tx-1", "plan", JournalStatus::Completed, json!({}))
            .unwrap();
        assert_eq!(
            journal.transaction_ids().unwrap(),
            vec!["tx-1".to_owned(), "tx-2".to_owned()]
        );
        assert_eq!(journal.records_of("tx-1").unwrap().len(), 2);
    }

    #[test]
    fn an_empty_journal_is_not_an_error() {
        let temp = isolated_tempdir();
        let journal = Journal::new(temp.path()).unwrap();
        assert!(journal.read_all().unwrap().is_empty());
        assert!(journal.last_phase("tx-missing").unwrap().is_none());
    }

    #[test]
    fn only_one_transaction_lock_can_be_held_at_a_time() {
        let temp = isolated_tempdir();
        let directory = Arc::new(temp.path().to_path_buf());
        let first =
            TransactionLock::acquire(&directory, "inst-1", std::time::Duration::from_secs(1))
                .unwrap();

        let other = Arc::clone(&directory);
        let worker = thread::spawn(move || {
            TransactionLock::acquire(&other, "inst-1", std::time::Duration::from_millis(200))
                .is_err()
        });
        assert!(worker.join().unwrap());
        drop(first);

        // Once the lock is released the next caller gets it.
        let second =
            TransactionLock::acquire(&directory, "inst-1", std::time::Duration::from_secs(1))
                .unwrap();
        assert!(second.path().is_file());
    }

    #[test]
    fn different_installations_do_not_share_a_lock() {
        let temp = isolated_tempdir();
        let _first =
            TransactionLock::acquire(temp.path(), "inst-1", std::time::Duration::from_secs(1))
                .unwrap();
        let second =
            TransactionLock::acquire(temp.path(), "inst-2", std::time::Duration::from_secs(1))
                .unwrap();
        assert!(second.path().is_file());
    }
}
