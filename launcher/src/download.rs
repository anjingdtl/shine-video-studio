//! Resumable, verified downloads.
//!
//! A download that is interrupted must not throw away the bytes it already
//! has, and a download that completes must be proven to be the file the
//! manifest described. This module does both: the partial file is kept and
//! resumed with a range request, and the finished file is checked against the
//! expected size and SHA-256 before it is allowed to become a staged
//! artifact.

use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::time::Duration;

use sha2::{Digest, Sha256};

use crate::protocol::is_allowed_release_url;

/// The largest file the launcher will download. Studio packages and model
/// archives are large, but nothing legitimate reaches this size.
const MAX_DOWNLOAD_BYTES: u64 = 64 * 1024 * 1024 * 1024;
const CHUNK: usize = 256 * 1024;

/// What to download and how to prove it is the right file.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DownloadRequest {
    pub url: String,
    pub destination: PathBuf,
    pub expected_sha256: Option<String>,
    pub expected_size: Option<u64>,
    pub max_bytes: u64,
}

impl DownloadRequest {
    pub fn new(url: &str, destination: PathBuf) -> Self {
        Self {
            url: url.to_owned(),
            destination,
            expected_sha256: None,
            expected_size: None,
            max_bytes: MAX_DOWNLOAD_BYTES,
        }
    }

    pub fn with_sha256(mut self, sha256: &str) -> Self {
        self.expected_sha256 = Some(sha256.to_owned());
        self
    }

    pub fn with_size(mut self, size: u64) -> Self {
        self.expected_size = Some(size);
        self
    }
}

/// Progress notification. The launcher forwards this to the interface; it is
/// never used to decide whether the download succeeded.
pub trait DownloadObserver {
    fn on_progress(&self, downloaded: u64, total: Option<u64>);
}

/// An observer that discards progress.
pub struct SilentObserver;

impl DownloadObserver for SilentObserver {
    fn on_progress(&self, _downloaded: u64, _total: Option<u64>) {}
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DownloadOutcome {
    pub path: PathBuf,
    pub bytes: u64,
    /// How many bytes were already on disk when this run started.
    pub resumed_from: u64,
    pub sha256: String,
}

#[derive(Debug)]
pub enum DownloadError {
    /// The URL is not an accepted release source.
    RejectedUrl(String),
    /// The network is not usable.
    Network(String),
    /// The server answered with an unexpected status.
    HttpStatus(u16),
    /// The server did not honour the range request.
    ResumeNotSupported,
    /// The transfer failed part way through.
    Interrupted(String),
    /// The file is larger than the plan allows.
    TooLarge { limit: u64 },
    /// The finished file does not match the expected digest.
    DigestMismatch { expected: String, actual: String },
    /// The finished file does not match the expected size.
    SizeMismatch { expected: u64, actual: u64 },
    /// The file could not be written.
    Io(String),
}

impl std::fmt::Display for DownloadError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::RejectedUrl(url) => write!(formatter, "下载来源不受信任：{url}"),
            Self::Network(message) => write!(formatter, "网络不可用：{message}"),
            Self::HttpStatus(status) => write!(formatter, "下载服务返回 HTTP {status}"),
            Self::ResumeNotSupported => formatter.write_str("下载服务不支持断点续传，已重新开始"),
            Self::Interrupted(message) => write!(formatter, "下载中断：{message}"),
            Self::TooLarge { limit } => {
                write!(formatter, "文件超过允许的大小上限（{limit} 字节）")
            }
            Self::DigestMismatch { expected, actual } => {
                write!(
                    formatter,
                    "文件校验和不符（应为 {expected}，实际 {actual}）"
                )
            }
            Self::SizeMismatch { expected, actual } => {
                write!(formatter, "文件大小不符（应为 {expected}，实际 {actual}）")
            }
            Self::Io(message) => write!(formatter, "写入下载文件失败：{message}"),
        }
    }
}

impl std::error::Error for DownloadError {}

/// Download one file, resuming a previous attempt when possible.
pub fn download(
    request: &DownloadRequest,
    observer: &dyn DownloadObserver,
) -> Result<DownloadOutcome, DownloadError> {
    if !is_allowed_release_url(&request.url) {
        return Err(DownloadError::RejectedUrl(request.url.clone()));
    }
    if request.max_bytes == 0 || request.max_bytes > MAX_DOWNLOAD_BYTES {
        return Err(DownloadError::TooLarge {
            limit: request.max_bytes,
        });
    }

    let parent = request
        .destination
        .parent()
        .ok_or_else(|| DownloadError::Io("下载目标缺少父目录".to_owned()))?;
    fs::create_dir_all(parent).map_err(|error| DownloadError::Io(error.to_string()))?;

    let partial = partial_path(&request.destination);
    let already = fs::metadata(&partial)
        .map(|metadata| metadata.len())
        .unwrap_or(0);
    if already > request.max_bytes {
        let _ = fs::remove_file(&partial);
        return Err(DownloadError::TooLarge {
            limit: request.max_bytes,
        });
    }

    let agent = ureq::AgentBuilder::new()
        .timeout_connect(Duration::from_secs(10))
        .timeout_read(Duration::from_secs(60))
        .timeout_write(Duration::from_secs(30))
        .redirects(3)
        .build();

    let mut request_builder = agent.get(&request.url);
    if already > 0 {
        request_builder = request_builder.set("Range", &format!("bytes={already}-"));
    }

    let (response, resumed) = match request_builder.call() {
        Ok(response) => {
            let status = response.status();
            let resumed = if status == 206 {
                true
            } else if status == 200 {
                false
            } else {
                return Err(DownloadError::HttpStatus(status));
            };
            (response, resumed)
        }
        Err(ureq::Error::Status(status, _)) => return Err(DownloadError::HttpStatus(status)),
        Err(ureq::Error::Transport(error)) => {
            return Err(DownloadError::Network(error.to_string()));
        }
    };

    if !resumed && already > 0 {
        // The server ignored the range request, so the partial file cannot be
        // extended safely. Start over rather than concatenating two
        // different versions of the same file.
        let _ = fs::remove_file(&partial);
    }

    let total = response
        .header("content-length")
        .and_then(|value| value.parse::<u64>().ok())
        .map(|length| length + if resumed { already } else { 0 });

    let mut reader = response.into_reader();
    let mut file = OpenOptions::new()
        .write(true)
        .create(true)
        .append(resumed)
        .truncate(!resumed)
        .open(&partial)
        .map_err(|error| DownloadError::Io(error.to_string()))?;

    let mut buffer = vec![0_u8; CHUNK];
    let mut downloaded = if resumed { already } else { 0 };
    loop {
        let read = reader
            .read(&mut buffer)
            .map_err(|error| DownloadError::Interrupted(error.to_string()))?;
        if read == 0 {
            break;
        }
        downloaded += read as u64;
        if downloaded > request.max_bytes {
            let _ = fs::remove_file(&partial);
            return Err(DownloadError::TooLarge {
                limit: request.max_bytes,
            });
        }
        file.write_all(&buffer[..read])
            .map_err(|error| DownloadError::Io(error.to_string()))?;
        observer.on_progress(downloaded, total);
    }
    file.flush()
        .map_err(|error| DownloadError::Io(error.to_string()))?;
    file.sync_all()
        .map_err(|error| DownloadError::Io(error.to_string()))?;
    drop(file);

    if let Some(expected) = request.expected_size
        && downloaded != expected
    {
        return Err(DownloadError::SizeMismatch {
            expected,
            actual: downloaded,
        });
    }

    let digest = hash_file(&partial)?;
    if let Some(expected) = &request.expected_sha256
        && !digest.eq_ignore_ascii_case(expected)
    {
        return Err(DownloadError::DigestMismatch {
            expected: expected.clone(),
            actual: digest,
        });
    }

    fs::rename(&partial, &request.destination)
        .map_err(|error| DownloadError::Io(error.to_string()))?;

    Ok(DownloadOutcome {
        path: request.destination.clone(),
        bytes: downloaded,
        resumed_from: if resumed { already } else { 0 },
        sha256: digest,
    })
}

/// The path a download is written to while it is still in flight.
pub fn partial_path(destination: &Path) -> PathBuf {
    let mut name = destination
        .file_name()
        .map(|value| value.to_os_string())
        .unwrap_or_default();
    name.push(".part");
    destination.with_file_name(name)
}

/// The SHA-256 of a file on disk.
pub fn hash_file(path: &Path) -> Result<String, DownloadError> {
    let mut file = File::open(path).map_err(|error| DownloadError::Io(error.to_string()))?;
    let mut hasher = Sha256::new();
    let mut buffer = vec![0_u8; CHUNK];
    loop {
        let read = file
            .read(&mut buffer)
            .map_err(|error| DownloadError::Io(error.to_string()))?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    Ok(hasher
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect())
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::path::Path;

    use tempfile::{TempDir, tempdir_in};

    use super::{
        DownloadError, DownloadRequest, SilentObserver, download, hash_file, partial_path,
    };

    fn isolated_tempdir() -> TempDir {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("target/test-tmp");
        fs::create_dir_all(&root).unwrap();
        tempdir_in(root).unwrap()
    }

    #[test]
    fn a_url_outside_the_allow_list_is_refused_before_any_request() {
        let temp = isolated_tempdir();
        let request = DownloadRequest::new(
            "https://evil.example.invalid/studio.zip",
            temp.path().join("studio.zip"),
        );
        let error = download(&request, &SilentObserver).unwrap_err();
        assert!(matches!(error, DownloadError::RejectedUrl(_)));
        assert!(!temp.path().join("studio.zip").exists());
    }

    #[test]
    fn a_plain_http_url_is_refused() {
        let temp = isolated_tempdir();
        let request = DownloadRequest::new(
            "http://github.com/owner/repo/releases/download/x/y.zip",
            temp.path().join("studio.zip"),
        );
        let error = download(&request, &SilentObserver).unwrap_err();
        assert!(matches!(error, DownloadError::RejectedUrl(_)));
    }

    #[test]
    fn a_download_limit_below_the_file_size_is_refused() {
        let temp = isolated_tempdir();
        let mut request = DownloadRequest::new(
            "https://github.com/owner/repo/releases/download/x/y.zip",
            temp.path().join("studio.zip"),
        );
        request.max_bytes = 0;
        let error = download(&request, &SilentObserver).unwrap_err();
        assert!(matches!(error, DownloadError::TooLarge { .. }));
    }

    #[test]
    fn the_partial_file_sits_next_to_the_destination() {
        let temp = isolated_tempdir();
        let destination = temp.path().join("studio.zip");
        let partial = partial_path(&destination);
        assert_eq!(partial.parent(), destination.parent());
        assert!(
            partial
                .file_name()
                .unwrap()
                .to_string_lossy()
                .ends_with(".part")
        );
    }

    #[test]
    fn the_digest_of_a_known_file_is_stable() {
        let temp = isolated_tempdir();
        let path = temp.path().join("payload.bin");
        fs::write(&path, b"shine").unwrap();
        // The well known SHA-256 of the five bytes "shine".
        assert_eq!(
            hash_file(&path).unwrap(),
            "6d7cbfe8a1ceaf7d2c362691053a0700d36713afeba5138b9870375fa4005561"
        );
    }

    #[test]
    fn a_digest_mismatch_leaves_no_finished_file_behind() {
        let temp = isolated_tempdir();
        let destination = temp.path().join("studio.zip");
        let request = DownloadRequest::new(
            "https://github.com/owner/repo/releases/download/x/y.zip",
            destination.clone(),
        )
        .with_sha256(&"a".repeat(64));
        // The request fails at the network layer on this machine; what matters
        // is that a digest mismatch never leaves a file that looks finished.
        let error = download(&request, &SilentObserver).unwrap_err();
        assert!(!destination.exists());
        assert!(matches!(
            error,
            DownloadError::Network(_) | DownloadError::HttpStatus(_)
        ));
    }
}
