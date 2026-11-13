use std::fs::{self, File};
use std::io::{self, Read};
use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};

use crate::path_safety::{
    canonical_existing_directory, directory_without_reparse, regular_file_without_reparse,
    reject_reparse_components, validate_existing_path_components, validate_local_absolute_path,
};

const MAX_ENV_BYTES: u64 = 128 * 1024;
const SQLITE_HEADER: &[u8; 16] = b"SQLite format 3\0";

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum DatabaseEvidence {
    NoDatabaseFound,
    SqliteHeaderFound,
    ExistingDatabaseUnrecognized,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LegacyScanResult {
    pub install_root: PathBuf,
    pub data_root: Option<PathBuf>,
    pub bootstrap_root: Option<PathBuf>,
    pub database_evidence: DatabaseEvidence,
    pub custom_database_url_configured: bool,
    pub tool_roots_unverified: Vec<PathBuf>,
    pub toolchain_receipt_matches_manifest: Option<bool>,
    pub runtime_receipt_found: bool,
    pub model_directories_unverified: Vec<PathBuf>,
    pub comfyui_directories_unverified: Vec<PathBuf>,
    pub evidence: Vec<String>,
    pub warnings: Vec<String>,
    pub runtime_ready: bool,
}

#[derive(Debug)]
pub enum LegacyScanError {
    InvalidPath(String),
    NotShineInstallation,
    UnsafeMarker(String),
    ConfigTooLarge,
    Io(io::Error),
}

impl std::fmt::Display for LegacyScanError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidPath(message) => formatter.write_str(message),
            Self::NotShineInstallation => {
                formatter.write_str("目录不包含可识别的 Shine Studio 文件签名")
            }
            Self::UnsafeMarker(name) => write!(formatter, "检测到不安全的链接或文件：{name}"),
            Self::ConfigTooLarge => formatter.write_str(".env 超过只读扫描大小上限"),
            Self::Io(error) => write!(formatter, "读取目录失败：{error}"),
        }
    }
}

impl std::error::Error for LegacyScanError {}

impl From<io::Error> for LegacyScanError {
    fn from(error: io::Error) -> Self {
        Self::Io(error)
    }
}

#[derive(Default)]
struct EnvPointers {
    bootstrap_root: Option<String>,
    data_root: Option<String>,
    custom_database_url_configured: bool,
}

/// Read-only scan of one explicitly selected local installation directory.
/// It does not execute files, create directories, open a database for writing,
/// inspect secrets, or claim that models/runtime are ready.
pub fn scan_existing_installation(path: &Path) -> Result<LegacyScanResult, LegacyScanError> {
    let install_root = canonical_existing_directory(path).map_err(LegacyScanError::InvalidPath)?;
    let backend_marker = is_regular_marker(
        &install_root.join("backend/pyproject.toml"),
        "backend/pyproject.toml",
    )?;
    let frontend_marker = is_regular_marker(
        &install_root.join("frontend/package.json"),
        "frontend/package.json",
    )?;
    let startup_marker = is_regular_marker(
        &install_root.join("scripts/start_shine.ps1"),
        "scripts/start_shine.ps1",
    )?;
    if !backend_marker || !(frontend_marker || startup_marker) {
        return Err(LegacyScanError::NotShineInstallation);
    }

    let mut evidence = vec!["Shine Studio 后端项目签名".to_owned()];
    if frontend_marker {
        evidence.push("Shine Studio 前端项目签名".to_owned());
    }
    if startup_marker {
        evidence.push("Shine Studio 受控启动脚本签名".to_owned());
    }

    let (pointers, has_env) = read_env_pointers(&install_root)?;
    if has_env {
        evidence.push("发现 .env 配置文件（只读取安装/数据路径字段）".to_owned());
    }
    let mut warnings = Vec::new();

    let bootstrap_root = resolve_existing_pointer(
        &install_root,
        pointers.bootstrap_root.as_deref(),
        "SHINE_BOOTSTRAP_ROOT",
        &mut warnings,
    );
    let data_root = match pointers.data_root.as_deref() {
        Some(value) => {
            resolve_existing_pointer(&install_root, Some(value), "SHINE_DATA_DIR", &mut warnings)
        }
        None => {
            let mut default_candidates = vec![install_root.join(".local-data")];
            if let Some(root) = bootstrap_root.as_ref() {
                default_candidates.push(root.join("data"));
            }
            let selected = default_candidates
                .into_iter()
                .find(|candidate| directory_without_reparse(candidate).unwrap_or(false));
            if let Some(selected) = selected {
                Some(fs::canonicalize(selected).map_err(LegacyScanError::Io)?)
            } else {
                warnings.push("未发现 .local-data 或安装数据目录；扫描不会创建数据目录".to_owned());
                None
            }
        }
    };

    if pointers.custom_database_url_configured {
        warnings
            .push("检测到自定义 SHINE_DATABASE_URL；为避免读取连接凭据，不解析其路径".to_owned());
    }

    let database_evidence = match data_root.as_ref() {
        Some(root) => inspect_sqlite_header(&root.join("shine.db"), &mut warnings)?,
        None => DatabaseEvidence::NoDatabaseFound,
    };
    if database_evidence == DatabaseEvidence::SqliteHeaderFound {
        evidence.push("在数据目录发现 SQLite 数据库文件头".to_owned());
    }

    let toolchain_receipt_matches_manifest =
        inspect_toolchain_receipt(&install_root, bootstrap_root.as_deref(), &mut warnings);
    if toolchain_receipt_matches_manifest == Some(true) {
        evidence.push("固定工具链回执与仓库清单哈希匹配（工具状态仍未验证）".to_owned());
    } else if toolchain_receipt_matches_manifest == Some(false) {
        warnings.push("工具链回执与仓库固定清单不匹配；不会据此复用工具".to_owned());
    }
    let runtime_receipt_found = data_root.as_ref().is_some_and(|root| {
        regular_file_without_reparse(&root.join("run/receipts.json")).unwrap_or(false)
    });
    if runtime_receipt_found {
        evidence.push("发现 Studio 运行回执文件（未读取凭据或执行状态）".to_owned());
    }

    let mut tool_roots_unverified = Vec::new();
    if let Some(root) = bootstrap_root.as_ref() {
        for candidate in [
            root.join("tools"),
            root.join("bin"),
            root.join("python"),
            root.join("app/backend-env"),
            root.join("app/frontend"),
        ] {
            add_existing_directory(candidate, &mut tool_roots_unverified, &mut warnings);
        }
    }

    let mut model_directories_unverified = Vec::new();
    for candidate in [
        install_root.join("models"),
        install_root.join("ComfyUI/models"),
    ] {
        add_existing_directory(candidate, &mut model_directories_unverified, &mut warnings);
    }
    if let Some(data_root) = data_root.as_ref() {
        for candidate in [data_root.join("models"), data_root.join("comfyops/models")] {
            add_existing_directory(candidate, &mut model_directories_unverified, &mut warnings);
        }
    }
    if let Some(root) = bootstrap_root.as_ref() {
        for candidate in [
            root.join("runtime/models"),
            root.join("runtime/ComfyUI/models"),
        ] {
            add_existing_directory(candidate, &mut model_directories_unverified, &mut warnings);
        }
    }

    let mut comfyui_directories_unverified = Vec::new();
    let mut comfy_candidates = vec![
        install_root.join("ComfyUI"),
        install_root.join("ComfyUI_windows_portable/ComfyUI"),
    ];
    if let Some(root) = data_root.as_ref() {
        comfy_candidates.push(root.join("ComfyUI"));
        comfy_candidates.push(root.join("comfyops/ComfyUI"));
    }
    if let Some(root) = bootstrap_root.as_ref() {
        comfy_candidates.push(root.join("ComfyUI"));
        comfy_candidates.push(root.join("runtime/ComfyUI"));
        comfy_candidates.push(root.join("runtime/comfyui"));
    }
    for candidate in comfy_candidates {
        if !directory_without_reparse(&candidate).unwrap_or(false) {
            continue;
        }
        let main_marker = is_regular_marker(&candidate.join("main.py"), "ComfyUI/main.py")?;
        let management_marker = is_regular_marker(
            &candidate.join("comfy/model_management.py"),
            "ComfyUI/comfy/model_management.py",
        )?;
        if main_marker && management_marker {
            comfyui_directories_unverified
                .push(fs::canonicalize(candidate).map_err(LegacyScanError::Io)?);
        }
    }
    if !model_directories_unverified.is_empty() {
        warnings.push("发现模型目录；未计算文件哈希，能力状态保持未验证".to_owned());
    }
    if !comfyui_directories_unverified.is_empty() {
        warnings.push("发现 ComfyUI 目录；未核验版本、节点、依赖或运行状态".to_owned());
    }

    Ok(LegacyScanResult {
        install_root,
        data_root,
        bootstrap_root,
        database_evidence,
        custom_database_url_configured: pointers.custom_database_url_configured,
        tool_roots_unverified,
        toolchain_receipt_matches_manifest,
        runtime_receipt_found,
        model_directories_unverified,
        comfyui_directories_unverified,
        evidence,
        warnings,
        runtime_ready: false,
    })
}

fn is_regular_marker(path: &Path, label: &str) -> Result<bool, LegacyScanError> {
    regular_file_without_reparse(path).map_err(|_| LegacyScanError::UnsafeMarker(label.to_owned()))
}

fn read_env_pointers(root: &Path) -> Result<(EnvPointers, bool), LegacyScanError> {
    let path = root.join(".env");
    if !path.exists() {
        return Ok((EnvPointers::default(), false));
    }
    if !is_regular_marker(&path, ".env")? {
        return Err(LegacyScanError::UnsafeMarker(".env".to_owned()));
    }
    let metadata = fs::metadata(&path)?;
    if metadata.len() > MAX_ENV_BYTES {
        return Err(LegacyScanError::ConfigTooLarge);
    }

    let contents = fs::read_to_string(path)?;
    let mut pointers = EnvPointers::default();
    for line in contents.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let Some((key, raw_value)) = line.split_once('=') else {
            continue;
        };
        let value = clean_env_value(raw_value);
        match key.trim() {
            "SHINE_BOOTSTRAP_ROOT" => pointers.bootstrap_root = value,
            "SHINE_DATA_DIR" => pointers.data_root = value,
            "SHINE_DATABASE_URL" if value.is_some() => {
                pointers.custom_database_url_configured = true;
            }
            _ => {}
        }
    }
    Ok((pointers, true))
}

fn clean_env_value(raw: &str) -> Option<String> {
    let value = raw.trim();
    if value.is_empty() {
        return None;
    }
    let unquoted = if (value.starts_with('"') && value.ends_with('"'))
        || (value.starts_with('\'') && value.ends_with('\''))
    {
        &value[1..value.len().saturating_sub(1)]
    } else {
        value
    };
    if unquoted.is_empty() || unquoted.contains('\0') {
        None
    } else {
        Some(unquoted.to_owned())
    }
}

fn resolve_existing_pointer(
    install_root: &Path,
    raw: Option<&str>,
    name: &str,
    warnings: &mut Vec<String>,
) -> Option<PathBuf> {
    let raw = raw?;
    let configured = PathBuf::from(raw);
    let candidate = if configured.is_absolute() {
        configured
    } else {
        install_root.join(configured)
    };
    if validate_existing_path_components(&candidate).is_err() {
        warnings.push(format!("{name} 不是受支持的本机绝对路径；未访问该目录"));
        return None;
    }
    if !directory_without_reparse(&candidate).unwrap_or(false) {
        warnings.push(format!("{name} 指向的目录不存在或包含链接；未访问该目录"));
        return None;
    }
    if reject_reparse_components(&candidate).is_err() {
        warnings.push(format!("{name} 路径经过符号链接或重解析点；未访问该目录"));
        return None;
    }
    match fs::canonicalize(candidate) {
        Ok(path) if validate_local_absolute_path(&path).is_ok() => Some(path),
        Ok(_) => {
            warnings.push(format!("{name} 解析为不受支持的路径；未访问该目录"));
            None
        }
        Err(_) => {
            warnings.push(format!("无法规范化 {name}；未访问该目录"));
            None
        }
    }
}

fn inspect_sqlite_header(
    path: &Path,
    warnings: &mut Vec<String>,
) -> Result<DatabaseEvidence, LegacyScanError> {
    if !path.exists() {
        return Ok(DatabaseEvidence::NoDatabaseFound);
    }
    if !is_regular_marker(path, "shine.db")? {
        warnings.push("数据目录中的 shine.db 不是普通文件；未读取".to_owned());
        return Ok(DatabaseEvidence::ExistingDatabaseUnrecognized);
    }
    let mut file = File::open(path)?;
    let mut header = [0_u8; SQLITE_HEADER.len()];
    match file.read_exact(&mut header) {
        Ok(()) if &header == SQLITE_HEADER => Ok(DatabaseEvidence::SqliteHeaderFound),
        Ok(()) => Ok(DatabaseEvidence::ExistingDatabaseUnrecognized),
        Err(error) if error.kind() == io::ErrorKind::UnexpectedEof => {
            Ok(DatabaseEvidence::ExistingDatabaseUnrecognized)
        }
        Err(error) => Err(LegacyScanError::Io(error)),
    }
}

fn inspect_toolchain_receipt(
    install_root: &Path,
    bootstrap_root: Option<&Path>,
    warnings: &mut Vec<String>,
) -> Option<bool> {
    let bootstrap_root = bootstrap_root?;
    let receipt = bootstrap_root.join("run/toolchain.json");
    match regular_file_without_reparse(&receipt) {
        Ok(true) => {}
        Ok(false) => return None,
        Err(_) => {
            warnings.push("工具链回执包含链接或无法安全读取".to_owned());
            return None;
        }
    }
    match fs::metadata(&receipt) {
        Ok(metadata) if metadata.len() <= MAX_ENV_BYTES => {}
        Ok(_) => {
            warnings.push("工具链回执超过大小上限；未读取".to_owned());
            return None;
        }
        Err(_) => return None,
    }
    let receipt_bytes = match fs::read(&receipt) {
        Ok(bytes) => bytes,
        Err(_) => {
            warnings.push("无法读取工具链回执；不会据此复用工具".to_owned());
            return None;
        }
    };
    let parsed: serde_json::Value = match serde_json::from_slice(&receipt_bytes) {
        Ok(parsed) => parsed,
        Err(_) => {
            warnings.push("工具链回执格式无效；不会据此复用工具".to_owned());
            return None;
        }
    };
    let Some(expected_hash) = parsed
        .get("manifest_sha256")
        .and_then(serde_json::Value::as_str)
    else {
        warnings.push("工具链回执缺少固定清单哈希；不会据此复用工具".to_owned());
        return None;
    };

    let manifest = install_root.join("config/deployment/tools-windows-x64.json");
    if !regular_file_without_reparse(&manifest).unwrap_or(false) {
        warnings.push("未找到仓库固定工具清单；工具回执无法核验".to_owned());
        return None;
    }
    let manifest_bytes = match fs::read(&manifest) {
        Ok(bytes) if bytes.len() as u64 <= MAX_ENV_BYTES => bytes,
        Ok(_) => {
            warnings.push("固定工具清单超过只读扫描大小上限".to_owned());
            return None;
        }
        Err(_) => {
            warnings.push("无法读取固定工具清单；工具回执无法核验".to_owned());
            return None;
        }
    };
    let digest = Sha256::digest(manifest_bytes);
    let mut actual_hash = String::with_capacity(digest.len() * 2);
    for byte in digest {
        use std::fmt::Write as _;
        let _ = write!(actual_hash, "{byte:02x}");
    }
    Some(expected_hash.eq_ignore_ascii_case(&actual_hash))
}

fn add_existing_directory(path: PathBuf, results: &mut Vec<PathBuf>, warnings: &mut Vec<String>) {
    match directory_without_reparse(&path) {
        Ok(true) => match fs::canonicalize(path) {
            Ok(path) if !results.contains(&path) => results.push(path),
            Ok(_) => {}
            Err(_) => warnings.push("无法规范化候选模型目录；未访问该目录".to_owned()),
        },
        Ok(false) => {}
        Err(_) => warnings.push("候选模型目录包含链接；未访问该目录".to_owned()),
    }
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::io::Write;
    use std::path::Path;

    use sha2::{Digest, Sha256};
    use tempfile::{TempDir, tempdir_in};

    use super::{DatabaseEvidence, LegacyScanError, scan_existing_installation};

    fn isolated_tempdir() -> TempDir {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("target/test-tmp");
        fs::create_dir_all(&root).unwrap();
        tempdir_in(root).unwrap()
    }

    fn make_fixture(root: &Path) {
        fs::create_dir_all(root.join("backend")).unwrap();
        fs::create_dir_all(root.join("frontend")).unwrap();
        fs::create_dir_all(root.join("scripts")).unwrap();
        fs::write(
            root.join("backend/pyproject.toml"),
            "[project]\nname='shine'\n",
        )
        .unwrap();
        fs::write(root.join("frontend/package.json"), "{}\n").unwrap();
        fs::write(root.join("scripts/start_shine.ps1"), "# not executed\n").unwrap();
    }

    fn file_hash(path: &Path) -> Vec<u8> {
        Sha256::digest(fs::read(path).unwrap()).to_vec()
    }

    #[test]
    fn scans_only_known_paths_and_never_modifies_user_database() {
        let temp = isolated_tempdir();
        let root = temp.path().join("old-shine");
        make_fixture(&root);
        fs::create_dir_all(root.join(".local-data/models")).unwrap();
        let database = root.join(".local-data/shine.db");
        let mut file = fs::File::create(&database).unwrap();
        file.write_all(b"SQLite format 3\0test database bytes")
            .unwrap();
        drop(file);
        let before = file_hash(&database);

        let result = scan_existing_installation(&root).unwrap();
        let after = file_hash(&database);

        assert_eq!(
            result.database_evidence,
            DatabaseEvidence::SqliteHeaderFound
        );
        let expected_data_root = fs::canonicalize(root.join(".local-data")).unwrap();
        assert_eq!(
            result.data_root.as_deref(),
            Some(expected_data_root.as_path())
        );
        assert_eq!(result.model_directories_unverified.len(), 1);
        assert!(!result.runtime_ready);
        assert!(
            result
                .warnings
                .iter()
                .any(|warning| warning.contains("未计算文件哈希"))
        );
        assert_eq!(before, after);
    }

    #[test]
    fn reads_only_path_keys_and_does_not_expose_secret_values() {
        let temp = isolated_tempdir();
        let root = temp.path().join("old-shine");
        make_fixture(&root);
        fs::create_dir_all(root.join(".local-data")).unwrap();
        fs::write(
            root.join(".env"),
            "SHINE_DATA_DIR=.local-data\nSHINE_DATABASE_URL=postgresql://user:super-secret-token@localhost/db\nLLM_API_KEY=another-secret\n",
        )
        .unwrap();

        let result = scan_existing_installation(&root).unwrap();
        let debug_text = format!("{result:?}");

        assert!(result.custom_database_url_configured);
        assert!(
            result
                .warnings
                .iter()
                .any(|warning| warning.contains("不解析其路径"))
        );
        assert!(!debug_text.contains("super-secret-token"));
        assert!(!debug_text.contains("another-secret"));
    }

    #[test]
    fn rejects_relative_paths_and_does_not_create_default_data_directory() {
        let temp = isolated_tempdir();
        let root = temp.path().join("old-shine");
        make_fixture(&root);
        assert!(matches!(
            scan_existing_installation(Path::new("relative")),
            Err(LegacyScanError::InvalidPath(_))
        ));

        let result = scan_existing_installation(&root).unwrap();
        assert_eq!(result.data_root, None);
        assert!(!root.join(".local-data").exists());
        assert_eq!(result.database_evidence, DatabaseEvidence::NoDatabaseFound);
    }

    #[test]
    fn uses_existing_bootstrap_data_directory_without_creating_it() {
        let temp = isolated_tempdir();
        let root = temp.path().join("old-shine");
        let bootstrap = temp.path().join("bootstrap");
        make_fixture(&root);
        fs::create_dir_all(bootstrap.join("data")).unwrap();
        fs::write(
            root.join(".env"),
            format!("SHINE_BOOTSTRAP_ROOT={}\n", bootstrap.display()),
        )
        .unwrap();
        fs::write(
            bootstrap.join("data/shine.db"),
            b"SQLite format 3\0legacy data",
        )
        .unwrap();

        let result = scan_existing_installation(&root).unwrap();

        let expected_data_root = fs::canonicalize(bootstrap.join("data")).unwrap();
        assert_eq!(result.data_root, Some(expected_data_root));
        assert_eq!(
            result.database_evidence,
            DatabaseEvidence::SqliteHeaderFound
        );
        assert!(bootstrap.join("data").is_dir());
        assert!(!result.runtime_ready);
    }

    #[test]
    fn verifies_receipt_hash_against_the_pinned_tool_manifest_without_running_tools() {
        let temp = isolated_tempdir();
        let root = temp.path().join("old-shine");
        let bootstrap = temp.path().join("bootstrap");
        make_fixture(&root);
        fs::create_dir_all(root.join("config/deployment")).unwrap();
        fs::create_dir_all(bootstrap.join("run")).unwrap();
        let manifest = b"{\"version\":1,\"tools\":[]}\n";
        fs::write(
            root.join("config/deployment/tools-windows-x64.json"),
            manifest,
        )
        .unwrap();
        let digest = Sha256::digest(manifest);
        let expected_hash = digest
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>();
        fs::write(
            bootstrap.join("run/toolchain.json"),
            format!("{{\"manifest_sha256\":\"{expected_hash}\",\"installed\":[\"node\"]}}"),
        )
        .unwrap();
        fs::write(
            root.join(".env"),
            format!("SHINE_BOOTSTRAP_ROOT={}\n", bootstrap.display()),
        )
        .unwrap();

        let result = scan_existing_installation(&root).unwrap();

        assert_eq!(result.toolchain_receipt_matches_manifest, Some(true));
        assert!(!result.runtime_ready);
        assert!(
            result
                .evidence
                .iter()
                .any(|item| item.contains("工具状态仍未验证"))
        );
    }

    #[cfg(unix)]
    #[test]
    fn rejects_a_symlinked_installation_root() {
        use std::os::unix::fs::symlink;

        let temp = isolated_tempdir();
        let real = temp.path().join("real");
        let alias = temp.path().join("alias");
        make_fixture(&real);
        symlink(&real, &alias).unwrap();
        assert!(matches!(
            scan_existing_installation(&alias),
            Err(LegacyScanError::InvalidPath(_))
        ));
    }
}
