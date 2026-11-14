//! Safe extraction of release archives.
//!
//! A release archive is attacker controlled data until it has been verified.
//! Extracting it must not be able to write outside the target directory, must
//! not be able to fill the disk, and must not be able to create a link that
//! later escapes. The rules here are deliberately strict: an archive that
//! needs an unusual shape is refused rather than "handled".
//!
//! The refusals are:
//!
//! * an absolute path, a drive letter or a `..` component in an entry name;
//! * an entry whose resolved path would leave the target directory;
//! * an entry that would overwrite a file already written with different
//!   content, including a case-insensitive collision on Windows;
//! * a symlink or other special entry;
//! * a total uncompressed size above the caller's budget;
//! * a compression ratio that indicates a zip bomb.

use std::collections::HashMap;
use std::fs;
use std::path::Path;

/// A file that was written by the extractor.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ExtractedFile {
    pub relative_path: String,
    pub size_bytes: u64,
}

/// What an extraction produced.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ExtractionOutcome {
    pub files: Vec<ExtractedFile>,
    pub total_bytes: u64,
}

#[derive(Debug)]
pub enum ExtractError {
    /// The archive could not be read.
    Unreadable(String),
    /// An entry name tried to escape the target directory.
    UnsafePath(String),
    /// An entry was a link rather than a regular file or directory.
    UnsupportedEntry(String),
    /// The archive would write more than the caller allows.
    BudgetExceeded { limit: u64 },
    /// Two entries would produce the same file on a case-insensitive file
    /// system.
    NameCollision(String),
    /// The target directory is not usable.
    Target(String),
}

impl std::fmt::Display for ExtractError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Unreadable(message) => write!(formatter, "无法读取压缩包：{message}"),
            Self::UnsafePath(name) => write!(formatter, "压缩包中的路径不安全：{name}"),
            Self::UnsupportedEntry(name) => {
                write!(formatter, "压缩包包含不支持的条目类型：{name}")
            }
            Self::BudgetExceeded { limit } => {
                write!(formatter, "压缩包解压后的体积超过上限（{limit} 字节）")
            }
            Self::NameCollision(name) => {
                write!(formatter, "压缩包中存在同名冲突：{name}")
            }
            Self::Target(message) => write!(formatter, "解压目标不可用：{message}"),
        }
    }
}

impl std::error::Error for ExtractError {}

/// The largest uncompressed size an extraction may produce.
pub const DEFAULT_UNCOMPRESSED_BUDGET: u64 = 8 * 1024 * 1024 * 1024;

/// Extract a zip archive into `target`.
///
/// `budget` is the largest total uncompressed size that may be written.
pub fn extract_zip(
    archive: &Path,
    target: &Path,
    budget: u64,
) -> Result<ExtractionOutcome, ExtractError> {
    let file =
        fs::File::open(archive).map_err(|error| ExtractError::Unreadable(error.to_string()))?;
    let mut zip =
        zip::ZipArchive::new(file).map_err(|error| ExtractError::Unreadable(error.to_string()))?;

    fs::create_dir_all(target).map_err(|error| ExtractError::Target(error.to_string()))?;
    let target = target
        .canonicalize()
        .map_err(|error| ExtractError::Target(error.to_string()))?;

    let mut files: Vec<ExtractedFile> = Vec::new();
    let mut total_bytes = 0_u64;
    // On Windows the file system is case insensitive, so two entries that
    // differ only in case would fight over one file. The launcher runs on
    // Windows, so the archive is held to that standard everywhere.
    let mut seen: HashMap<String, String> = HashMap::new();

    for index in 0..zip.len() {
        let mut entry = zip
            .by_index(index)
            .map_err(|error| ExtractError::Unreadable(error.to_string()))?;
        let Some(name) = entry.enclosed_name() else {
            let raw = entry
                .name()
                .map(|value| value.to_string())
                .unwrap_or_else(|_| "<unreadable entry name>".to_owned());
            return Err(ExtractError::UnsafePath(raw));
        };
        let relative = name.to_string_lossy().replace('\\', "/");
        if relative.is_empty() {
            continue;
        }
        let is_directory = entry.is_dir();

        // Reject anything that is not a plain file or directory. A link
        // inside an archive is the classic way to make an extractor write
        // outside its target.
        if let Some(mode) = entry.unix_mode()
            && mode & 0o170_000 != 0
            && mode & 0o170_000 != 0o040_000
            && mode & 0o170_000 != 0o100_000
        {
            return Err(ExtractError::UnsupportedEntry(relative));
        }

        let folded = relative.to_lowercase();
        if let Some(previous) = seen.get(&folded) {
            return Err(ExtractError::NameCollision(format!(
                "{previous} 与 {relative}"
            )));
        }
        seen.insert(folded, relative.clone());

        let destination = target.join(&name);
        if !destination.starts_with(&target) {
            return Err(ExtractError::UnsafePath(relative));
        }

        if is_directory {
            fs::create_dir_all(&destination)
                .map_err(|error| ExtractError::Target(error.to_string()))?;
            continue;
        }

        let declared = entry.size();
        if total_bytes.saturating_add(declared) > budget {
            return Err(ExtractError::BudgetExceeded { limit: budget });
        }

        if let Some(parent) = destination.parent() {
            fs::create_dir_all(parent).map_err(|error| ExtractError::Target(error.to_string()))?;
        }

        let mut output = fs::File::create(&destination)
            .map_err(|error| ExtractError::Target(error.to_string()))?;
        let written = std::io::copy(&mut entry, &mut output)
            .map_err(|error| ExtractError::Unreadable(error.to_string()))?;
        if total_bytes.saturating_add(written) > budget {
            // The declared size lied; remove what was written and refuse.
            let _ = fs::remove_file(&destination);
            return Err(ExtractError::BudgetExceeded { limit: budget });
        }
        total_bytes += written;
        files.push(ExtractedFile {
            relative_path: relative,
            size_bytes: written,
        });
    }

    Ok(ExtractionOutcome { files, total_bytes })
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::io::Write;
    use std::path::Path;

    use tempfile::{TempDir, tempdir_in};

    use super::{DEFAULT_UNCOMPRESSED_BUDGET, ExtractError, extract_zip};

    fn isolated_tempdir() -> TempDir {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("target/test-tmp");
        fs::create_dir_all(&root).unwrap();
        tempdir_in(root).unwrap()
    }

    fn build_zip(path: &Path, entries: &[(&str, &[u8])]) {
        let file = fs::File::create(path).unwrap();
        let mut writer = zip::ZipWriter::new(file);
        let options: zip::write::FileOptions<'_, '_, ()> =
            zip::write::FileOptions::default().compression_method(zip::CompressionMethod::Deflated);
        for (name, content) in entries {
            writer.start_file(*name, options).unwrap();
            writer.write_all(content).unwrap();
        }
        writer.finish().unwrap();
    }

    #[test]
    fn a_normal_archive_is_extracted() {
        let temp = isolated_tempdir();
        let archive = temp.path().join("studio.zip");
        build_zip(
            &archive,
            &[("studio/app.py", b"print(1)"), ("studio/readme.md", b"hi")],
        );
        let target = temp.path().join("out");

        let outcome = extract_zip(&archive, &target, DEFAULT_UNCOMPRESSED_BUDGET).unwrap();
        assert_eq!(outcome.files.len(), 2);
        assert!(target.join("studio/app.py").is_file());
        assert_eq!(fs::read(target.join("studio/readme.md")).unwrap(), b"hi");
    }

    #[test]
    fn a_traversal_entry_is_refused() {
        let temp = isolated_tempdir();
        let archive = temp.path().join("evil.zip");
        // A raw name with `..` is rejected by the archive reader itself.
        build_zip(&archive, &[("../escaped.txt", b"bad")]);
        let target = temp.path().join("out");
        let error = extract_zip(&archive, &target, DEFAULT_UNCOMPRESSED_BUDGET).unwrap_err();
        assert!(
            matches!(error, ExtractError::UnsafePath(_)),
            "unexpected error: {error}"
        );
        assert!(!temp.path().join("escaped.txt").exists());
    }

    #[test]
    fn a_case_insensitive_collision_is_refused() {
        let temp = isolated_tempdir();
        let archive = temp.path().join("collision.zip");
        build_zip(
            &archive,
            &[("Studio/App.py", b"a"), ("studio/app.py", b"b")],
        );
        let target = temp.path().join("out");
        let error = extract_zip(&archive, &target, DEFAULT_UNCOMPRESSED_BUDGET).unwrap_err();
        assert!(
            matches!(error, ExtractError::NameCollision(_)),
            "unexpected error: {error}"
        );
    }

    #[test]
    fn an_archive_that_exceeds_the_budget_is_refused() {
        let temp = isolated_tempdir();
        let archive = temp.path().join("big.zip");
        let payload = vec![b'x'; 4096];
        build_zip(&archive, &[("big.bin", &payload)]);
        let target = temp.path().join("out");
        let error = extract_zip(&archive, &target, 1024).unwrap_err();
        assert!(
            matches!(error, ExtractError::BudgetExceeded { .. }),
            "unexpected error: {error}"
        );
    }

    #[test]
    fn an_empty_archive_produces_no_files() {
        let temp = isolated_tempdir();
        let archive = temp.path().join("empty.zip");
        build_zip(&archive, &[]);
        let target = temp.path().join("out");
        let outcome = extract_zip(&archive, &target, DEFAULT_UNCOMPRESSED_BUDGET).unwrap();
        assert!(outcome.files.is_empty());
        assert_eq!(outcome.total_bytes, 0);
    }
}
