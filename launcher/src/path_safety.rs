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
            Some(Component::Prefix(prefix))
                if matches!(prefix.kind(), Prefix::Disk(_) | Prefix::VerbatimDisk(_)) => {}
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
    for current in path
        .ancestors()
        .filter(|ancestor| !ancestor.as_os_str().is_empty())
    {
        match fs::symlink_metadata(current) {
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
            Err(error) if error.kind() == io::ErrorKind::NotFound => continue,
            Err(error) => return Err(error),
        }
    }
    Ok(())
}

/// Reject links inside a directory the launcher owns, without touching the
/// directories above it.
///
/// A user's own profile directory may legitimately be a link: a relocated
/// Windows profile, a junction, a symlinked home directory. Refusing to work
/// there would lock a real user out of the launcher for no security gain.
///
/// `state_directory` is the launcher's own directory (for example
/// `%LOCALAPPDATA%\ShineLauncher`). That directory and everything below it
/// must be real: if an attacker replaced it, or the registry file inside it,
/// with a link, the launcher's writes would land somewhere else. The user's
/// own directories above it are the user's choice and are not inspected.
pub(crate) fn reject_reparse_components_in_state(
    state_directory: &Path,
    path: &Path,
) -> io::Result<()> {
    check_not_a_link(state_directory)?;
    let relative = path.strip_prefix(state_directory).map_err(|_| {
        io::Error::new(
            io::ErrorKind::PermissionDenied,
            "path is not inside the launcher state directory",
        )
    })?;
    let mut current = state_directory.to_path_buf();
    for component in relative.components() {
        current.push(component);
        check_not_a_link(&current)?;
    }
    Ok(())
}

fn check_not_a_link(path: &Path) -> io::Result<()> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if is_reparse_or_symlink(&metadata) => Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            format!(
                "reparse point or symbolic link is not allowed: {}",
                path.display()
            ),
        )),
        Ok(_) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
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

#[cfg(test)]
mod tests {
    use std::path::Path;

    use super::is_local_absolute_path;

    #[cfg(windows)]
    #[test]
    fn accepts_local_verbatim_disk_paths_but_rejects_network_and_device_namespaces() {
        assert!(is_local_absolute_path(Path::new(r"\\?\C:\Shine\Studio")));
        assert!(!is_local_absolute_path(Path::new(r"\\server\share\Shine")));
        assert!(!is_local_absolute_path(Path::new(
            r"\\?\UNC\server\share\Shine"
        )));
        assert!(!is_local_absolute_path(Path::new(r"\\.\pipe\shine")));
    }

    #[cfg(not(windows))]
    #[test]
    fn rejects_network_style_absolute_paths() {
        assert!(!is_local_absolute_path(Path::new("//server/share/shine")));
    }

    #[cfg(unix)]
    #[test]
    fn rejects_symlinked_ancestor_even_when_child_does_not_exist() {
        use std::os::unix::fs::symlink;

        let temp = tempfile::tempdir().unwrap();
        let real = temp.path().join("real");
        let alias = temp.path().join("alias");
        std::fs::create_dir_all(&real).unwrap();
        symlink(&real, &alias).unwrap();

        let missing_child = alias.join("not-created/child");
        assert!(super::reject_reparse_components(&missing_child).is_err());
    }
}
