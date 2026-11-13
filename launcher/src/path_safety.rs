use std::fs;
use std::io;
use std::path::{Component, Path, PathBuf};

pub(crate) fn validate_local_absolute_path(path: &Path) -> Result<(), String> {
    if !is_local_absolute_path(path) {
        return Err("只接受本机本地绝对路径".to_owned());
    }
    if path
        .components()
        .any(|component| component == Component::ParentDir)
    {
        return Err("路径不能包含 ..".to_owned());
    }
    Ok(())
}

pub(crate) fn is_local_absolute_path(path: &Path) -> bool {
    if !path.is_absolute() {
        return false;
    }

    #[cfg(windows)]
    {
        use std::path::Prefix;
        match path.components().next() {
            Some(Component::Prefix(prefix)) if matches!(prefix.kind(), Prefix::Disk(_)) => {}
            _ => return false,
        }
    }

    #[cfg(not(windows))]
    if path.to_string_lossy().starts_with("//") {
        return false;
    }

    true
}

pub(crate) fn validate_existing_path_components(path: &Path) -> Result<(), String> {
    if !is_local_absolute_path(path) {
        return Err("只接受本机本地绝对路径".to_owned());
    }
    reject_reparse_components(path).map_err(|_| "路径经过符号链接或无法安全读取".to_owned())
}

pub(crate) fn reject_reparse_components(path: &Path) -> io::Result<()> {
    let mut current = PathBuf::new();
    for component in path.components() {
        current.push(component.as_os_str());
        match fs::symlink_metadata(&current) {
            Ok(metadata) if is_reparse_or_symlink(&metadata) => {
                return Err(io::Error::new(
                    io::ErrorKind::PermissionDenied,
                    format!(
                        "reparse point or symbolic link is not allowed: {}",
                        current.display()
                    ),
                ));
            }
            Ok(_) => {}
            Err(error) if error.kind() == io::ErrorKind::NotFound => break,
            Err(error) => return Err(error),
        }
    }
    Ok(())
}

pub(crate) fn canonical_existing_directory(path: &Path) -> Result<PathBuf, String> {
    validate_local_absolute_path(path)?;
    reject_reparse_components(path).map_err(|_| "目录路径经过符号链接或无法安全读取".to_owned())?;
    let metadata = fs::metadata(path).map_err(|_| "目录不存在或无法读取".to_owned())?;
    if !metadata.is_dir() {
        return Err("路径不是目录".to_owned());
    }
    let canonical = fs::canonicalize(path).map_err(|_| "无法规范化目录路径".to_owned())?;
    validate_local_absolute_path(&canonical)?;
    Ok(canonical)
}

pub(crate) fn regular_file_without_reparse(path: &Path) -> io::Result<bool> {
    reject_reparse_components(path)?;
    let metadata = match fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(false),
        Err(error) => return Err(error),
    };
    if is_reparse_or_symlink(&metadata) {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "symbolic links and reparse points are not accepted",
        ));
    }
    Ok(metadata.is_file())
}

pub(crate) fn directory_without_reparse(path: &Path) -> io::Result<bool> {
    reject_reparse_components(path)?;
    let metadata = match fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(false),
        Err(error) => return Err(error),
    };
    if is_reparse_or_symlink(&metadata) {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "symbolic links and reparse points are not accepted",
        ));
    }
    Ok(metadata.is_dir())
}

fn is_reparse_or_symlink(metadata: &fs::Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0400;
        metadata.file_type().is_symlink()
            || metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
    }

    #[cfg(not(windows))]
    metadata.file_type().is_symlink()
}
