//! Stable installation identity.
//!
//! The previous launcher derived the installation id from a SHA-256 of the
//! install path. That made the identity a function of *where* the directory
//! happens to live, so moving the folder (a new disk, a fresh clone, a
//! reinstall) silently produced a different installation and orphaned the
//! history that belonged to it.
//!
//! The identity is now a random, persisted token:
//!
//! * it is generated once and stored in `<install_root>/.shine/installation.json`;
//! * the launcher registry keeps the same token as the authoritative copy;
//! * when the directory moves, the identity file travels with it and the
//!   registry updates the path in place instead of minting a new installation;
//! * installations created before this change are adopted, and their old
//!   path-derived id is retained as an alias so any stored reference to it
//!   still resolves to the same installation.

use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};

use crate::path_safety::{regular_file_without_reparse, reject_reparse_components};

/// Location of the per-installation identity file, relative to the install root.
pub const IDENTITY_RELATIVE_PATH: &str = ".shine/installation.json";

const IDENTITY_SCHEMA_VERSION: u32 = 1;
const MAX_IDENTITY_BYTES: u64 = 64 * 1024;
const INSTALLATION_ID_PREFIX: &str = "inst_";
const LEGACY_ID_PREFIX: &str = "sha256:";

/// A persisted, path-independent installation identity.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InstallationIdentity {
    pub schema_version: u32,
    pub installation_id: String,
    pub created_at_unix_seconds: u64,
    pub origin: String,
}

#[derive(Debug)]
pub enum IdentityError {
    Io(std::io::Error),
    Json(serde_json::Error),
    Invalid(String),
}

impl std::fmt::Display for IdentityError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Io(error) => write!(formatter, "安装身份文件读写失败：{error}"),
            Self::Json(error) => write!(formatter, "安装身份文件格式无效：{error}"),
            Self::Invalid(message) => formatter.write_str(message),
        }
    }
}

impl std::error::Error for IdentityError {}

impl From<std::io::Error> for IdentityError {
    fn from(error: std::io::Error) -> Self {
        Self::Io(error)
    }
}

impl From<serde_json::Error> for IdentityError {
    fn from(error: serde_json::Error) -> Self {
        Self::Json(error)
    }
}

/// Generate a fresh random installation id.
///
/// The id is an identifier, not a secret, but it must never collide and must
/// not be guessable from the install path, so it is drawn from the operating
/// system entropy source.
pub fn generate_installation_id() -> Result<String, IdentityError> {
    let mut bytes = [0_u8; 16];
    getrandom::fill(&mut bytes).map_err(|error| {
        IdentityError::Invalid(format!("无法读取系统随机数用于生成安装身份：{error}"))
    })?;
    // RFC 4122 version 4 / variant 1 layout.
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    let hex: String = bytes
        .iter()
        .flat_map(|byte| {
            let mut pair = String::with_capacity(2);
            use std::fmt::Write as _;
            let _ = write!(pair, "{byte:02x}");
            pair.chars().collect::<Vec<_>>()
        })
        .collect();
    let characters: Vec<char> = hex.chars().collect();
    Ok(format!(
        "{INSTALLATION_ID_PREFIX}{}-{}-{}-{}-{}",
        characters[0..8].iter().collect::<String>(),
        characters[8..12].iter().collect::<String>(),
        characters[12..16].iter().collect::<String>(),
        characters[16..20].iter().collect::<String>(),
        characters[20..32].iter().collect::<String>(),
    ))
}

/// True when the id uses the current stable format.
pub fn is_stable_installation_id(value: &str) -> bool {
    let Some(body) = value.strip_prefix(INSTALLATION_ID_PREFIX) else {
        return false;
    };
    let parts: Vec<&str> = body.split('-').collect();
    if parts.len() != 5 {
        return false;
    }
    let expected_lengths = [8_usize, 4, 4, 4, 12];
    parts.iter().zip(expected_lengths).all(|(part, length)| {
        part.len() == length && part.bytes().all(|byte| byte.is_ascii_hexdigit())
    })
}

/// True when the id is one of the retired path-derived ids.
pub fn is_legacy_path_derived_id(value: &str) -> bool {
    let Some(body) = value.strip_prefix(LEGACY_ID_PREFIX) else {
        return false;
    };
    body.len() == 64 && body.bytes().all(|byte| byte.is_ascii_hexdigit())
}

/// Recompute the retired path-derived id of a directory.
///
/// Only used to recognise and alias the ids written by launcher 0.1.x; new
/// installations never use it.
pub fn legacy_path_derived_id(path: &Path) -> String {
    use sha2::{Digest, Sha256};
    let text = path.to_string_lossy();
    #[cfg(windows)]
    let normalized = text.replace('/', "\\").to_ascii_lowercase();
    #[cfg(not(windows))]
    let normalized = text.into_owned();
    let digest = Sha256::digest(normalized.as_bytes());
    let mut hex = String::with_capacity(digest.len() * 2);
    for byte in digest {
        use std::fmt::Write as _;
        let _ = write!(hex, "{byte:02x}");
    }
    format!("{LEGACY_ID_PREFIX}{hex}")
}

/// Absolute path of the identity file for an install root.
pub fn identity_file_path(install_root: &Path) -> PathBuf {
    install_root.join(IDENTITY_RELATIVE_PATH)
}

/// Read the identity file of an install root, if it exists.
///
/// The read is strictly read-only: a missing file is not an error, and an
/// unreadable or malformed file is reported instead of being silently
/// replaced.
pub fn read_identity_file(
    install_root: &Path,
) -> Result<Option<InstallationIdentity>, IdentityError> {
    let path = identity_file_path(install_root);
    reject_reparse_components(&path)
        .map_err(|_| IdentityError::Invalid("安装身份文件经过符号链接或重解析点".to_owned()))?;
    if !regular_file_without_reparse(&path)
        .map_err(|error| IdentityError::Invalid(format!("无法安全读取安装身份文件：{error}")))?
    {
        return Ok(None);
    }
    let metadata = fs::metadata(&path)?;
    if metadata.len() > MAX_IDENTITY_BYTES {
        return Err(IdentityError::Invalid(
            "安装身份文件超过大小限制".to_owned(),
        ));
    }
    let mut content = Vec::with_capacity(metadata.len() as usize);
    File::open(&path)?.read_to_end(&mut content)?;
    let identity: InstallationIdentity = serde_json::from_slice(&content)?;
    validate_identity(&identity)?;
    Ok(Some(identity))
}

/// Persist the identity file of an install root.
///
/// The write is atomic so an interrupted first-run never leaves a half-written
/// identity behind.
pub fn write_identity_file(
    install_root: &Path,
    identity: &InstallationIdentity,
) -> Result<(), IdentityError> {
    validate_identity(identity)?;
    let path = identity_file_path(install_root);
    let parent = path
        .parent()
        .ok_or_else(|| IdentityError::Invalid("安装身份文件缺少父目录".to_owned()))?;
    reject_reparse_components(parent)
        .map_err(|_| IdentityError::Invalid("安装身份目录经过符号链接或重解析点".to_owned()))?;
    fs::create_dir_all(parent)?;
    reject_reparse_components(&path)
        .map_err(|_| IdentityError::Invalid("安装身份文件经过符号链接或重解析点".to_owned()))?;

    let bytes = serde_json::to_vec_pretty(identity)?;
    if bytes.len() as u64 > MAX_IDENTITY_BYTES {
        return Err(IdentityError::Invalid(
            "安装身份文件超过大小限制".to_owned(),
        ));
    }
    let temporary = parent.join(".installation-identity.tmp");
    let mut file = OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .open(&temporary)?;
    let write_result = (|| {
        file.write_all(&bytes)?;
        file.sync_all()?;
        drop(file);
        fs::rename(&temporary, &path)?;
        Ok::<(), std::io::Error>(())
    })();
    if write_result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    write_result.map_err(IdentityError::Io)
}

/// How the launcher resolved the identity of a scanned directory.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum IdentityResolution {
    /// The directory already carried an identity file; it is reused.
    Existing {
        identity: InstallationIdentity,
        source: IdentitySource,
    },
    /// The directory carried no identity file; a new one is minted and stored.
    Created {
        identity: InstallationIdentity,
        source: IdentitySource,
    },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum IdentitySource {
    /// Discovered inside the install root by scanning the directory.
    IdentityFile,
    /// Recovered from the launcher registry after the directory was moved.
    RegistryCarryOver,
}

/// Resolve (and if needed create) the stable identity of an install root.
///
/// `known_ids` are the installation ids the launcher registry already knows
/// about. When the directory carries an identity file whose id the registry
/// already knows, the installation is the same one and only the path changed.
pub fn resolve_install_identity(
    install_root: &Path,
    known_ids: &[String],
) -> Result<IdentityResolution, IdentityError> {
    if let Some(identity) = read_identity_file(install_root)? {
        let source = if known_ids
            .iter()
            .any(|known| known == &identity.installation_id)
        {
            IdentitySource::RegistryCarryOver
        } else {
            IdentitySource::IdentityFile
        };
        return Ok(IdentityResolution::Existing { identity, source });
    }

    let identity = InstallationIdentity {
        schema_version: IDENTITY_SCHEMA_VERSION,
        installation_id: generate_installation_id()?,
        created_at_unix_seconds: unix_now(),
        origin: "launcher-generated".to_owned(),
    };
    write_identity_file(install_root, &identity)?;
    Ok(IdentityResolution::Created {
        identity,
        source: IdentitySource::IdentityFile,
    })
}

fn validate_identity(identity: &InstallationIdentity) -> Result<(), IdentityError> {
    if identity.schema_version != IDENTITY_SCHEMA_VERSION {
        return Err(IdentityError::Invalid(format!(
            "不支持的安装身份版本 {}",
            identity.schema_version
        )));
    }
    if !is_stable_installation_id(&identity.installation_id) {
        return Err(IdentityError::Invalid(
            "安装身份不是受支持的稳定格式".to_owned(),
        ));
    }
    if identity.origin.is_empty() || identity.origin.len() > 64 {
        return Err(IdentityError::Invalid("安装身份来源标记无效".to_owned()));
    }
    Ok(())
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

    use tempfile::{TempDir, tempdir_in};

    use super::{
        IdentityResolution, IdentitySource, generate_installation_id, identity_file_path,
        is_legacy_path_derived_id, is_stable_installation_id, legacy_path_derived_id,
        read_identity_file, resolve_install_identity, write_identity_file,
    };

    fn isolated_tempdir() -> TempDir {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("target/test-tmp");
        fs::create_dir_all(&root).unwrap();
        tempdir_in(root).unwrap()
    }

    #[test]
    fn generated_ids_are_unique_and_use_the_stable_format() {
        let first = generate_installation_id().unwrap();
        let second = generate_installation_id().unwrap();
        assert_ne!(first, second);
        assert!(is_stable_installation_id(&first));
        assert!(is_stable_installation_id(&second));
        assert!(first.starts_with("inst_"));
        assert!(!is_legacy_path_derived_id(&first));
    }

    #[test]
    fn stable_id_validation_rejects_malformed_values() {
        assert!(!is_stable_installation_id(""));
        assert!(!is_stable_installation_id("inst_"));
        assert!(!is_stable_installation_id(&format!(
            "sha256:{}",
            "a".repeat(64)
        )));
        assert!(!is_stable_installation_id(
            "inst_zzzzzzzz-zzzz-4zzz-yzzz-zzzzzzzzzzzz"
        ));
        assert!(!is_stable_installation_id(
            "inst_12345678-1234-4234-8234-1234567890"
        ));
        assert!(is_stable_installation_id(
            "inst_9f3c1d20-7a44-4c1e-9b2f-6d5e8a0c1234"
        ));
    }

    #[test]
    fn legacy_path_derived_ids_are_recognised_but_never_reissued() {
        let id = legacy_path_derived_id(Path::new("/tmp/example"));
        assert!(is_legacy_path_derived_id(&id));
        assert!(!is_stable_installation_id(&id));
    }

    #[test]
    fn identity_survives_the_install_root_moving_to_another_path() {
        let temp = isolated_tempdir();
        let original = temp.path().join("disk-a/shine");
        let moved = temp.path().join("disk-b/shine");
        fs::create_dir_all(&original).unwrap();

        let resolution = resolve_install_identity(&original, &[]).unwrap();
        let IdentityResolution::Created { identity, .. } = resolution else {
            panic!("expected a freshly created identity");
        };

        // The directory is moved, exactly like a user moving an installation to
        // another disk. The identity file travels with it.
        fs::create_dir_all(moved.parent().unwrap()).unwrap();
        fs::rename(&original, &moved).unwrap();

        let stored = read_identity_file(&moved).unwrap().unwrap();
        assert_eq!(stored.installation_id, identity.installation_id);

        // Re-resolving at the new path with the registry knowledge of the old
        // installation recognises it as the same installation.
        let known = [identity.installation_id.clone()];
        let resolution = resolve_install_identity(&moved, &known).unwrap();
        let IdentityResolution::Existing {
            identity: resolved,
            source,
        } = resolution
        else {
            panic!("expected the existing identity to be reused");
        };
        assert_eq!(resolved.installation_id, identity.installation_id);
        assert_eq!(source, IdentitySource::RegistryCarryOver);
    }

    #[test]
    fn a_second_scan_never_mints_a_second_identity_for_the_same_directory() {
        let temp = isolated_tempdir();
        let root = temp.path().join("studio");
        fs::create_dir_all(&root).unwrap();

        let first = resolve_install_identity(&root, &[]).unwrap();
        let second = resolve_install_identity(&root, &[]).unwrap();
        let IdentityResolution::Created {
            identity: created, ..
        } = first
        else {
            panic!("expected a created identity");
        };
        let IdentityResolution::Existing {
            identity: existing, ..
        } = second
        else {
            panic!("expected the existing identity to be reused");
        };
        assert_eq!(created.installation_id, existing.installation_id);
    }

    #[test]
    fn identity_file_is_written_atomically_inside_the_install_root() {
        let temp = isolated_tempdir();
        let root = temp.path().join("studio");
        fs::create_dir_all(&root).unwrap();
        let resolution = resolve_install_identity(&root, &[]).unwrap();
        let IdentityResolution::Created { identity, .. } = resolution else {
            panic!("expected a created identity");
        };
        let path = identity_file_path(&root);
        assert!(path.is_file());
        assert!(path.starts_with(&root));
        let stored = read_identity_file(&root).unwrap().unwrap();
        assert_eq!(stored, identity);
        // No temporary file is left behind.
        let leftovers: Vec<_> = fs::read_dir(path.parent().unwrap())
            .unwrap()
            .filter_map(Result::ok)
            .filter(|entry| entry.file_name().to_string_lossy().contains("tmp"))
            .collect();
        assert!(leftovers.is_empty());
    }

    #[test]
    fn a_corrupted_identity_file_is_reported_instead_of_silently_replaced() {
        let temp = isolated_tempdir();
        let root = temp.path().join("studio");
        fs::create_dir_all(identity_file_path(&root).parent().unwrap()).unwrap();
        fs::write(identity_file_path(&root), b"{not json").unwrap();
        assert!(read_identity_file(&root).is_err());
        assert!(resolve_install_identity(&root, &[]).is_err());
    }

    #[test]
    fn an_identity_file_with_a_foreign_id_is_rejected() {
        let temp = isolated_tempdir();
        let root = temp.path().join("studio");
        fs::create_dir_all(identity_file_path(&root).parent().unwrap()).unwrap();
        let foreign = serde_json::json!({
            "schema_version": 1,
            "installation_id": "not-a-stable-id",
            "created_at_unix_seconds": 1,
            "origin": "test"
        });
        fs::write(
            identity_file_path(&root),
            serde_json::to_vec(&foreign).unwrap(),
        )
        .unwrap();
        assert!(read_identity_file(&root).is_err());
    }

    #[test]
    fn writing_an_identity_outside_the_install_root_is_impossible() {
        let temp = isolated_tempdir();
        let root = temp.path().join("studio");
        fs::create_dir_all(&root).unwrap();
        let identity = super::InstallationIdentity {
            schema_version: 1,
            installation_id: generate_installation_id().unwrap(),
            created_at_unix_seconds: 1,
            origin: "test".to_owned(),
        };
        write_identity_file(&root, &identity).unwrap();
        assert!(identity_file_path(&root).is_file());
    }
}
