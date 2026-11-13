use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use fs2::FileExt;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::legacy_scan::LegacyScanResult;
use crate::path_safety::{reject_reparse_components, validate_local_absolute_path};

const REGISTRY_SCHEMA_VERSION: u32 = 1;
const MAX_REGISTRY_BYTES: u64 = 2 * 1024 * 1024;
static TEMP_SEQUENCE: AtomicU64 = AtomicU64::new(0);

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum UpdateChannel {
    Stable,
    Beta,
    Development,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DiscoverySource {
    LegacyDirectoryScan,
    SourceCheckout,
    ReleasePackage,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RegisteredInstallation {
    pub installation_id: String,
    pub install_root: PathBuf,
    pub data_root: Option<PathBuf>,
    pub bootstrap_root: Option<PathBuf>,
    pub channel: UpdateChannel,
    pub discovery_source: DiscoverySource,
    pub last_scanned_unix_seconds: u64,
    pub runtime_ready: bool,
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

impl InstallationRegistry {
    pub fn empty() -> Self {
        Self {
            schema_version: REGISTRY_SCHEMA_VERSION,
            installations: Vec::new(),
        }
    }

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
        let registry: Self = serde_json::from_slice(&content)?;
        registry.validate()?;
        Ok(registry)
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

    fn validate(&self) -> Result<(), RegistryError> {
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
        }
        Ok(())
    }
}

impl RegisteredInstallation {
    pub fn from_legacy_scan(
        scan: &LegacyScanResult,
        channel: UpdateChannel,
    ) -> Result<Self, RegistryError> {
        Ok(Self {
            installation_id: installation_id(&scan.install_root)?,
            install_root: scan.install_root.clone(),
            data_root: scan.data_root.clone(),
            bootstrap_root: scan.bootstrap_root.clone(),
            channel,
            discovery_source: DiscoverySource::LegacyDirectoryScan,
            last_scanned_unix_seconds: SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs(),
            runtime_ready: false,
        })
    }
}

pub fn default_registry_path() -> Result<PathBuf, RegistryError> {
    #[cfg(windows)]
    let base = std::env::var_os("LOCALAPPDATA")
        .or_else(|| std::env::var_os("APPDATA"))
        .map(PathBuf::from)
        .ok_or_else(|| RegistryError::InvalidPath("Windows 用户配置目录不可用".to_owned()))?;

    #[cfg(not(windows))]
    let base = std::env::var_os("XDG_STATE_HOME")
        .map(PathBuf::from)
        .ok_or_else(|| RegistryError::InvalidPath("XDG_STATE_HOME 未配置".to_owned()))?;

    Ok(base.join("ShineLauncher").join("installations-v1.json"))
}

pub fn installation_id(path: &Path) -> Result<String, RegistryError> {
    validate_local_absolute_path(path).map_err(RegistryError::InvalidPath)?;
    let normalized = normalize_identity_path(path);
    let digest = Sha256::digest(normalized.as_bytes());
    let mut hex = String::with_capacity(digest.len() * 2);
    for byte in digest {
        use std::fmt::Write as _;
        let _ = write!(hex, "{byte:02x}");
    }
    Ok(format!("sha256:{hex}"))
}

fn validate_registry_path(path: &Path) -> Result<(), RegistryError> {
    validate_local_absolute_path(path).map_err(RegistryError::InvalidPath)?;
    if path
        .components()
        .any(|component| component == Component::ParentDir)
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
    let expected_id = installation_id(&record.install_root)?;
    if record.installation_id != expected_id {
        return Err(RegistryError::InvalidRecord(
            "安装 ID 与安装目录不匹配".to_owned(),
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

fn normalize_identity_path(path: &Path) -> String {
    let text = path.to_string_lossy();
    #[cfg(windows)]
    {
        return text.replace('/', "\\").to_ascii_lowercase();
    }
    #[cfg(not(windows))]
    text.into_owned()
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

    use crate::legacy_scan::scan_existing_installation;

    use super::{
        DiscoverySource, InstallationRegistry, RegisteredInstallation, UpdateChannel,
        installation_id,
    };

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
        let scan = scan_existing_installation(&root).unwrap();
        let registry_path = temp.path().join("state/installations-v1.json");
        let record =
            RegisteredInstallation::from_legacy_scan(&scan, UpdateChannel::Stable).unwrap();

        let first = InstallationRegistry::upsert_atomic(&registry_path, record.clone()).unwrap();
        let second = InstallationRegistry::upsert_atomic(&registry_path, record.clone()).unwrap();
        let loaded = InstallationRegistry::load(&registry_path).unwrap();

        assert_eq!(first.installations.len(), 1);
        assert_eq!(second.installations.len(), 1);
        assert_eq!(loaded.installations, vec![record]);
        assert!(!loaded.installations[0].runtime_ready);
        assert_eq!(
            loaded.installations[0].discovery_source,
            DiscoverySource::LegacyDirectoryScan
        );
    }

    #[test]
    fn rejects_unknown_schema_without_overwriting_existing_file() {
        let temp = isolated_tempdir();
        let registry_path = temp.path().join("installations-v1.json");
        fs::write(
            &registry_path,
            r#"{"schema_version":99,"installations":[]}"#,
        )
        .unwrap();
        let before = fs::read(&registry_path).unwrap();
        assert!(InstallationRegistry::load(&registry_path).is_err());
        assert_eq!(fs::read(&registry_path).unwrap(), before);
    }

    #[test]
    fn installation_id_is_a_stable_one_way_directory_identity() {
        let temp = isolated_tempdir();
        let root = temp.path().join("studio");
        fs::create_dir_all(&root).unwrap();
        let id = installation_id(&root).unwrap();
        assert!(id.starts_with("sha256:"));
        assert_eq!(id, installation_id(&root).unwrap());
        assert!(!id.contains(&root.to_string_lossy().to_string()));
    }

    #[test]
    fn concurrent_registrations_do_not_drop_entries() {
        let temp = isolated_tempdir();
        let registry_path = Arc::new(temp.path().join("state/installations-v1.json"));
        let barrier = Arc::new(Barrier::new(4));
        let mut workers = Vec::new();
        for index in 0..4 {
            let root = temp.path().join(format!("studio-{index}"));
            make_fixture(&root);
            let scan = scan_existing_installation(&root).unwrap();
            let record =
                RegisteredInstallation::from_legacy_scan(&scan, UpdateChannel::Stable).unwrap();
            let path = Arc::clone(&registry_path);
            let start = Arc::clone(&barrier);
            workers.push(thread::spawn(move || {
                start.wait();
                InstallationRegistry::upsert_atomic(&path, record).unwrap();
            }));
        }
        for worker in workers {
            worker.join().unwrap();
        }
        assert_eq!(
            InstallationRegistry::load(&registry_path)
                .unwrap()
                .installations
                .len(),
            4
        );
    }
}
