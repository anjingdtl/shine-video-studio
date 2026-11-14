#[cfg(unix)]
use std::fs::File;
use std::fs::{self, OpenOptions};
use std::io::{self, Read, Write};
use std::path::Path;
use std::sync::OnceLock;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use semver::Version;
use serde::{Deserialize, Serialize};
use url::Url;

use crate::installation_registry::default_registry_path;
use crate::path_safety::{
    regular_file_without_reparse, reject_reparse_components_in_state, validate_local_absolute_path,
};

const MAX_RESPONSE_BYTES: u64 = 4 * 1024 * 1024;
const MAX_CACHE_BYTES: u64 = 8 * 1024 * 1024;
static TEMP_SEQUENCE: AtomicU64 = AtomicU64::new(0);

#[derive(Deserialize)]
struct ReleasePolicy {
    repository: String,
    launcher_tag_prefix: String,
    studio_tag_prefix: String,
    max_release_list_age_seconds: u64,
}

fn release_policy() -> Option<&'static ReleasePolicy> {
    static POLICY: OnceLock<Option<ReleasePolicy>> = OnceLock::new();
    POLICY
        .get_or_init(|| {
            let policy: ReleasePolicy = serde_json::from_str(include_str!(
                "../../config/launcher/launcher-release-policy.json"
            ))
            .ok()?;
            let mut segments = policy.repository.split('/');
            let (Some(owner), Some(repository), None) =
                (segments.next(), segments.next(), segments.next())
            else {
                return None;
            };
            if !is_safe_github_component(owner)
                || !is_safe_github_component(repository)
                || !is_safe_tag_prefix(&policy.launcher_tag_prefix)
                || !is_safe_tag_prefix(&policy.studio_tag_prefix)
                || policy.max_release_list_age_seconds == 0
                || policy.max_release_list_age_seconds > 7 * 24 * 60 * 60
            {
                return None;
            }
            Some(policy)
        })
        .as_ref()
}

fn is_safe_github_component(value: &str) -> bool {
    !value.is_empty()
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
}

fn is_safe_tag_prefix(value: &str) -> bool {
    !value.is_empty()
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
}

fn cache_ttl_seconds() -> Option<u64> {
    Some(release_policy()?.max_release_list_age_seconds)
}

fn releases_api_url() -> Option<String> {
    let policy = release_policy()?;
    Some(format!(
        "https://api.github.com/repos/{}/releases?per_page=100",
        policy.repository
    ))
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum ReleaseTarget {
    Studio,
    Launcher,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum ManagedChannel {
    Stable,
    Beta,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct GitHubAsset {
    pub name: String,
    pub browser_download_url: String,
    pub size: u64,
    #[serde(default)]
    pub digest: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct GitHubRelease {
    pub tag_name: String,
    pub draft: bool,
    pub prerelease: bool,
    pub target_commitish: Option<String>,
    pub published_at: Option<String>,
    pub html_url: String,
    #[serde(default)]
    pub assets: Vec<GitHubAsset>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ReleaseCandidate {
    pub target: ReleaseTarget,
    pub channel: ManagedChannel,
    pub version: Version,
    pub tag_name: String,
    pub published_at: Option<String>,
    pub release_url: String,
    pub assets: Vec<GitHubAsset>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ReleaseScanResult {
    pub candidates: Vec<ReleaseCandidate>,
    pub fetched_at_unix_seconds: Option<u64>,
    pub checked_at_unix_seconds: u64,
    pub from_cache: bool,
    pub stale: bool,
    pub status_message: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DevelopmentCommit {
    pub requested_ref: String,
    pub resolved_sha: String,
    pub commit_url: String,
}

#[derive(Debug)]
pub enum ReleaseScanError {
    NetworkUnavailable,
    RateLimited { reset_at_unix_seconds: Option<u64> },
    HttpStatus(u16),
    InvalidResponse,
    ResponseTooLarge,
    InvalidReference,
    CacheUnavailable,
}

impl std::fmt::Display for ReleaseScanError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::NetworkUnavailable => formatter.write_str("GitHub 版本服务暂时不可用"),
            Self::RateLimited {
                reset_at_unix_seconds: Some(reset),
            } => {
                write!(
                    formatter,
                    "GitHub 匿名 API 已限流，预计可在 Unix 时间 {reset} 后重试"
                )
            }
            Self::RateLimited {
                reset_at_unix_seconds: None,
            } => formatter.write_str("GitHub 匿名 API 已限流，稍后重试"),
            Self::HttpStatus(status) => write!(formatter, "GitHub 版本 API 返回 HTTP {status}"),
            Self::InvalidResponse => formatter.write_str("GitHub 版本 API 返回的数据格式无效"),
            Self::ResponseTooLarge => formatter.write_str("GitHub 版本 API 响应超过大小上限"),
            Self::InvalidReference => formatter.write_str("开发通道 ref 格式无效"),
            Self::CacheUnavailable => formatter.write_str("版本缓存路径不可用"),
        }
    }
}

impl std::error::Error for ReleaseScanError {}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct ReleaseCache {
    schema_version: u32,
    fetched_at_unix_seconds: u64,
    etag: Option<String>,
    releases: Vec<GitHubRelease>,
}

#[derive(Deserialize)]
struct GitHubCommitResponse {
    sha: String,
    html_url: String,
}

pub struct GitHubReleaseScanner {
    agent: ureq::Agent,
}

impl Default for GitHubReleaseScanner {
    fn default() -> Self {
        Self {
            agent: ureq::AgentBuilder::new()
                .timeout_connect(Duration::from_secs(8))
                .timeout_read(Duration::from_secs(15))
                .timeout_write(Duration::from_secs(8))
                .redirects(0)
                .build(),
        }
    }
}

impl GitHubReleaseScanner {
    pub fn scan(&self, force_refresh: bool) -> Result<ReleaseScanResult, ReleaseScanError> {
        let registry = default_registry_path().map_err(|_| ReleaseScanError::CacheUnavailable)?;
        let cache_path = registry.with_file_name("github-releases-v1.json");
        self.scan_with_cache_path(&cache_path, force_refresh)
    }

    pub fn scan_with_cache_path(
        &self,
        cache_path: &Path,
        force_refresh: bool,
    ) -> Result<ReleaseScanResult, ReleaseScanError> {
        let policy = release_policy().ok_or(ReleaseScanError::InvalidResponse)?;
        let api_url = releases_api_url().ok_or(ReleaseScanError::InvalidResponse)?;
        let cached = load_cache(cache_path).ok();
        let now = unix_now();
        if !force_refresh
            && let Some(cache) = cached.as_ref()
            && now.saturating_sub(cache.fetched_at_unix_seconds)
                < policy.max_release_list_age_seconds
        {
            return Ok(result_from_cache(
                cache,
                now,
                true,
                "使用有效期内的 GitHub 版本缓存",
                policy.max_release_list_age_seconds,
            ));
        }

        let mut request = self
            .agent
            .get(&api_url)
            .set("User-Agent", "ShineLauncher/0.1.0")
            .set("Accept", "application/vnd.github+json")
            .set("X-GitHub-Api-Version", "2022-11-28");
        if let Some(etag) = cached
            .as_ref()
            .and_then(|cache| cache.etag.as_deref())
            .filter(|etag| is_safe_etag(etag))
        {
            request = request.set("If-None-Match", etag);
        }

        match request.call() {
            Ok(response) => {
                if response.status() != 200
                    || !response.get_url().starts_with("https://api.github.com/")
                {
                    return self.cache_or_error(cached, now, ReleaseScanError::InvalidResponse);
                }
                let etag = response.header("etag").map(str::to_owned);
                let bytes = match read_limited(response) {
                    Ok(bytes) => bytes,
                    Err(error) => return self.cache_or_error(cached, now, error),
                };
                let releases: Vec<GitHubRelease> = match serde_json::from_slice(&bytes) {
                    Ok(releases) => releases,
                    Err(_) => {
                        return self.cache_or_error(cached, now, ReleaseScanError::InvalidResponse);
                    }
                };
                let cache = ReleaseCache {
                    schema_version: 1,
                    fetched_at_unix_seconds: now,
                    etag,
                    releases,
                };
                let _ = save_cache_atomic(cache_path, &cache);
                Ok(result_from_cache(
                    &cache,
                    now,
                    false,
                    "GitHub 版本列表已刷新",
                    policy.max_release_list_age_seconds,
                ))
            }
            Err(ureq::Error::Status(304, response)) => {
                if let Some(cache) = cached.as_ref() {
                    let checked_at = unix_now();
                    let result = result_from_cache(
                        cache,
                        checked_at,
                        true,
                        "GitHub 版本缓存未变化",
                        policy.max_release_list_age_seconds,
                    );
                    drop(response);
                    Ok(result)
                } else {
                    Err(ReleaseScanError::InvalidResponse)
                }
            }
            Err(ureq::Error::Status(403, response)) => {
                let reset = response
                    .header("x-ratelimit-reset")
                    .and_then(|value| value.parse::<u64>().ok());
                self.cache_or_error(
                    cached,
                    now,
                    ReleaseScanError::RateLimited {
                        reset_at_unix_seconds: reset,
                    },
                )
            }
            Err(ureq::Error::Status(status, _)) => {
                self.cache_or_error(cached, now, ReleaseScanError::HttpStatus(status))
            }
            Err(ureq::Error::Transport(_)) => {
                self.cache_or_error(cached, now, ReleaseScanError::NetworkUnavailable)
            }
        }
    }

    pub fn resolve_development_ref(
        &self,
        reference: &str,
    ) -> Result<DevelopmentCommit, ReleaseScanError> {
        if !is_safe_development_ref(reference) {
            return Err(ReleaseScanError::InvalidReference);
        }
        let repository = &release_policy()
            .ok_or(ReleaseScanError::InvalidResponse)?
            .repository;
        let mut url = Url::parse(&format!(
            "https://api.github.com/repos/{repository}/commits/"
        ))
        .map_err(|_| ReleaseScanError::InvalidResponse)?;
        url.path_segments_mut()
            .map_err(|_| ReleaseScanError::InvalidResponse)?
            .push(reference);
        let response = self
            .agent
            .get(url.as_str())
            .set("User-Agent", "ShineLauncher/0.1.0")
            .set("Accept", "application/vnd.github+json")
            .set("X-GitHub-Api-Version", "2022-11-28")
            .call()
            .map_err(map_ureq_error)?;
        if response.status() != 200 || !response.get_url().starts_with("https://api.github.com/") {
            return Err(ReleaseScanError::InvalidResponse);
        }
        let bytes = read_limited(response)?;
        let commit: GitHubCommitResponse =
            serde_json::from_slice(&bytes).map_err(|_| ReleaseScanError::InvalidResponse)?;
        if commit.sha.len() != 40 || !commit.sha.chars().all(|ch| ch.is_ascii_hexdigit()) {
            return Err(ReleaseScanError::InvalidResponse);
        }
        Ok(DevelopmentCommit {
            requested_ref: reference.to_owned(),
            resolved_sha: commit.sha.to_ascii_lowercase(),
            commit_url: commit.html_url,
        })
    }

    fn cache_or_error(
        &self,
        cached: Option<ReleaseCache>,
        now: u64,
        error: ReleaseScanError,
    ) -> Result<ReleaseScanResult, ReleaseScanError> {
        if let (Some(cache), Some(ttl)) = (cached.as_ref(), cache_ttl_seconds()) {
            let status = format!("{error}；显示本地缓存，未下载任何更新");
            return Ok(result_from_cache(cache, now, true, &status, ttl));
        }
        Err(error)
    }
}

fn map_ureq_error(error: ureq::Error) -> ReleaseScanError {
    match error {
        ureq::Error::Status(403, response) => ReleaseScanError::RateLimited {
            reset_at_unix_seconds: response
                .header("x-ratelimit-reset")
                .and_then(|value| value.parse::<u64>().ok()),
        },
        ureq::Error::Status(status, _) => ReleaseScanError::HttpStatus(status),
        ureq::Error::Transport(_) => ReleaseScanError::NetworkUnavailable,
    }
}

fn result_from_cache(
    cache: &ReleaseCache,
    checked_at: u64,
    from_cache: bool,
    status_message: &str,
    ttl_seconds: u64,
) -> ReleaseScanResult {
    ReleaseScanResult {
        candidates: classify_releases(&cache.releases),
        fetched_at_unix_seconds: Some(cache.fetched_at_unix_seconds),
        checked_at_unix_seconds: checked_at,
        from_cache,
        stale: checked_at.saturating_sub(cache.fetched_at_unix_seconds) >= ttl_seconds,
        status_message: status_message.to_owned(),
    }
}

pub fn classify_releases(releases: &[GitHubRelease]) -> Vec<ReleaseCandidate> {
    let Some(policy) = release_policy() else {
        return Vec::new();
    };
    let mut candidates = releases
        .iter()
        .filter_map(|release| classify_release(release, policy))
        .collect::<Vec<_>>();
    candidates.sort_by(|left, right| {
        left.target
            .cmp(&right.target)
            .then_with(|| left.channel.cmp(&right.channel))
            .then_with(|| right.version.cmp(&left.version))
    });
    candidates
}

fn classify_release(release: &GitHubRelease, policy: &ReleasePolicy) -> Option<ReleaseCandidate> {
    if release.draft || !is_github_release_page(&release.html_url, &policy.repository) {
        return None;
    }
    let (target, version_text) =
        if let Some(version) = release.tag_name.strip_prefix(&policy.launcher_tag_prefix) {
            (ReleaseTarget::Launcher, version)
        } else {
            (
                ReleaseTarget::Studio,
                release.tag_name.strip_prefix(&policy.studio_tag_prefix)?,
            )
        };
    let version = Version::parse(version_text).ok()?;
    let is_prerelease = !version.pre.is_empty();
    if is_prerelease != release.prerelease {
        return None;
    }
    let channel = if is_prerelease {
        ManagedChannel::Beta
    } else {
        ManagedChannel::Stable
    };
    Some(ReleaseCandidate {
        target,
        channel,
        version,
        tag_name: release.tag_name.clone(),
        published_at: release.published_at.clone(),
        release_url: release.html_url.clone(),
        assets: release.assets.clone(),
    })
}

pub fn latest_candidate(
    candidates: &[ReleaseCandidate],
    target: ReleaseTarget,
    channel: ManagedChannel,
) -> Option<&ReleaseCandidate> {
    candidates
        .iter()
        .filter(|item| item.target == target && item.channel == channel)
        .max_by(|left, right| left.version.cmp(&right.version))
}

pub fn is_safe_development_ref(reference: &str) -> bool {
    !reference.is_empty()
        && reference.len() <= 128
        && !reference.starts_with('-')
        && !reference.starts_with('/')
        && !reference.ends_with('/')
        && !reference.contains("..")
        && !reference.contains("//")
        && reference
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'/' | b'.' | b'_' | b'-'))
}

fn is_github_release_page(value: &str, repository: &str) -> bool {
    Url::parse(value).is_ok_and(|url| {
        url.scheme() == "https"
            && url.host_str() == Some("github.com")
            && url
                .path()
                .starts_with(&format!("/{repository}/releases/tag/"))
            && url.username().is_empty()
            && url.password().is_none()
    })
}

fn read_limited(response: ureq::Response) -> Result<Vec<u8>, ReleaseScanError> {
    let mut reader = response.into_reader().take(MAX_RESPONSE_BYTES + 1);
    let mut bytes = Vec::new();
    reader
        .read_to_end(&mut bytes)
        .map_err(|_| ReleaseScanError::InvalidResponse)?;
    if bytes.len() as u64 > MAX_RESPONSE_BYTES {
        return Err(ReleaseScanError::ResponseTooLarge);
    }
    Ok(bytes)
}

fn is_safe_etag(value: &str) -> bool {
    !value.is_empty() && value.len() <= 512 && !value.bytes().any(|byte| byte.is_ascii_control())
}

fn load_cache(path: &Path) -> Result<ReleaseCache, ReleaseScanError> {
    validate_local_absolute_path(path).map_err(|_| ReleaseScanError::CacheUnavailable)?;
    let state_directory = path.parent().unwrap_or(path);
    reject_reparse_components_in_state(state_directory, path)
        .map_err(|_| ReleaseScanError::CacheUnavailable)?;
    if !regular_file_without_reparse(path).map_err(|_| ReleaseScanError::CacheUnavailable)? {
        return Err(ReleaseScanError::CacheUnavailable);
    }
    let metadata = fs::metadata(path).map_err(|_| ReleaseScanError::CacheUnavailable)?;
    if metadata.len() > MAX_CACHE_BYTES {
        return Err(ReleaseScanError::CacheUnavailable);
    }
    let cache: ReleaseCache =
        serde_json::from_slice(&fs::read(path).map_err(|_| ReleaseScanError::CacheUnavailable)?)
            .map_err(|_| ReleaseScanError::CacheUnavailable)?;
    if cache.schema_version != 1 {
        return Err(ReleaseScanError::CacheUnavailable);
    }
    Ok(cache)
}

fn save_cache_atomic(path: &Path, cache: &ReleaseCache) -> io::Result<()> {
    validate_local_absolute_path(path).map_err(io::Error::other)?;
    let parent = path
        .parent()
        .ok_or_else(|| io::Error::other("cache path has no parent"))?;
    fs::create_dir_all(parent)?;
    reject_reparse_components_in_state(parent, path)?;
    let bytes = serde_json::to_vec(cache).map_err(io::Error::other)?;
    if bytes.len() as u64 > MAX_CACHE_BYTES {
        return Err(io::Error::other("cache size limit exceeded"));
    }
    let sequence = TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed);
    let temporary = parent.join(format!(
        ".github-releases-{}-{sequence}.tmp",
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
        Ok::<(), io::Error>(())
    })();
    if write_result.is_err() {
        let _ = fs::remove_file(temporary);
    }
    write_result
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
        GitHubRelease, GitHubReleaseScanner, ManagedChannel, ReleaseCache, ReleaseTarget,
        classify_releases, is_safe_development_ref, latest_candidate, unix_now,
    };

    fn isolated_tempdir() -> TempDir {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("target/test-tmp");
        fs::create_dir_all(&root).unwrap();
        tempdir_in(root).unwrap()
    }

    fn release(tag: &str, prerelease: bool) -> GitHubRelease {
        GitHubRelease {
            tag_name: tag.to_owned(),
            draft: false,
            prerelease,
            target_commitish: None,
            published_at: Some("2026-11-13T00:00:00Z".to_owned()),
            html_url: format!("https://github.com/anjingdtl/shine-video-studio/releases/tag/{tag}"),
            assets: Vec::new(),
        }
    }

    #[test]
    fn release_channels_keep_launcher_and_studio_lifecycles_separate() {
        let releases = vec![
            release("studio-v1.2.0", false),
            release("studio-v1.3.0-beta.1", true),
            release("launcher-v1.0.0", false),
            release("v99.0.0", false),
            release("studio-v2.0.0", true),
            release("launcher-v2.0.0-alpha.1", false),
        ];
        let candidates = classify_releases(&releases);
        assert_eq!(candidates.len(), 3);
        assert_eq!(
            latest_candidate(&candidates, ReleaseTarget::Studio, ManagedChannel::Stable)
                .unwrap()
                .tag_name,
            "studio-v1.2.0"
        );
        assert_eq!(
            latest_candidate(&candidates, ReleaseTarget::Studio, ManagedChannel::Beta)
                .unwrap()
                .tag_name,
            "studio-v1.3.0-beta.1"
        );
        assert_eq!(
            latest_candidate(&candidates, ReleaseTarget::Launcher, ManagedChannel::Stable)
                .unwrap()
                .tag_name,
            "launcher-v1.0.0"
        );
    }

    #[test]
    fn development_refs_must_be_simple_and_resolve_to_immutable_commit_before_use() {
        assert!(is_safe_development_ref("main"));
        assert!(is_safe_development_ref("feat/shine-launcher"));
        assert!(!is_safe_development_ref("../main"));
        assert!(!is_safe_development_ref("main;rm -rf"));
        assert!(!is_safe_development_ref("//server/branch"));
    }

    #[test]
    fn fresh_release_cache_supports_offline_scan_without_network_access() {
        let directory = isolated_tempdir();
        let cache_path = directory.path().join("github-releases-v1.json");
        let cache = ReleaseCache {
            schema_version: 1,
            fetched_at_unix_seconds: unix_now(),
            etag: Some("\"test-etag\"".to_owned()),
            releases: vec![release("studio-v1.2.0", false)],
        };
        fs::write(&cache_path, serde_json::to_vec(&cache).unwrap()).unwrap();

        let result = GitHubReleaseScanner::default()
            .scan_with_cache_path(&cache_path, false)
            .unwrap();
        assert!(result.from_cache);
        assert!(!result.stale);
        assert_eq!(result.candidates.len(), 1);
        assert_eq!(result.candidates[0].tag_name, "studio-v1.2.0");
    }
}
