use std::collections::{BTreeMap, HashMap, HashSet};

use base64::Engine as _;
use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use semver::Version;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use url::Url;

const SUPPORTED_PROTOCOL_MAJOR: u32 = 1;
const MAX_ARTIFACT_BYTES: u64 = 20 * 1024 * 1024 * 1024;
const ACCEPTED_RELEASE_HOSTS: &[&str] = &[
    "github.com",
    "api.github.com",
    "codeload.github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "github-releases.githubusercontent.com",
];
const SUPPORTED_REQUIRED_FEATURES: &[&str] = &[
    "typed-operations-v1",
    "sha256-artifacts",
    "ed25519-manifest-signature",
    "relative-catalog-paths",
    "data-preservation-v1",
];

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReleaseChannel {
    Stable,
    Beta,
    Development,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProtocolOperation {
    Probe,
    Install,
    Repair,
    Start,
    Stop,
    Upgrade,
}

#[derive(Clone, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum ReleaseProduct {
    Studio,
    Launcher,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StudioIdentity {
    pub name: String,
    pub version: String,
    pub channel: ReleaseChannel,
    pub commit: String,
    pub build_id: String,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Compatibility {
    pub min_launcher_version: String,
    pub supported_protocol_major: u32,
    pub platform: Vec<String>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Artifact {
    pub id: String,
    pub url: String,
    pub sha256: String,
    pub size_bytes: u64,
    #[serde(default)]
    pub media_type: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DetachedSignature {
    pub algorithm: String,
    pub key_id: String,
    pub value: String,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DeploymentManifestV1 {
    pub protocol_version: String,
    pub studio: StudioIdentity,
    pub compatibility: Compatibility,
    pub artifacts: Vec<Artifact>,
    pub toolchain_catalog: String,
    pub capability_catalogs: Vec<String>,
    pub workflow_root: String,
    pub operations: Vec<ProtocolOperation>,
    #[serde(default)]
    pub required_features: Vec<String>,
    #[serde(default)]
    pub optional_features: Vec<String>,
    pub data_policy: String,
    pub source_identity: String,
    pub signature: DetachedSignature,
    #[serde(flatten)]
    pub optional_fields: BTreeMap<String, Value>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ParsedManifest {
    manifest: DeploymentManifestV1,
    source_identity_bytes: [u8; 32],
    ignored_optional_features: Vec<String>,
}

impl ParsedManifest {
    pub fn manifest(&self) -> &DeploymentManifestV1 {
        &self.manifest
    }

    pub fn ignored_optional_features(&self) -> &[String] {
        &self.ignored_optional_features
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct VerifiedDeploymentManifest {
    manifest: DeploymentManifestV1,
    verified_key_id: String,
    source_identity: String,
}

impl VerifiedDeploymentManifest {
    pub fn manifest(&self) -> &DeploymentManifestV1 {
        &self.manifest
    }

    pub fn verified_key_id(&self) -> &str {
        &self.verified_key_id
    }

    pub fn source_identity(&self) -> &str {
        &self.source_identity
    }

    pub fn authorized_operations(&self) -> &[ProtocolOperation] {
        &self.manifest.operations
    }
}

#[derive(Debug)]
pub enum ProtocolError {
    Json(serde_json::Error),
    InvalidField(String),
    UnsupportedProtocol(u32),
    LauncherUpgradeRequired { minimum: String, current: String },
    UnsupportedPlatform(String),
    UnsupportedRequiredFeature(String),
    InvalidSourceIdentity,
    InvalidSignature(String),
    UnknownPublisher(String),
    PublisherScopeNotAllowed(String),
}

impl std::fmt::Display for ProtocolError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Json(error) => write!(formatter, "Deployment Protocol JSON 无效：{error}"),
            Self::InvalidField(field) => write!(formatter, "Deployment Protocol 字段无效：{field}"),
            Self::UnsupportedProtocol(major) => {
                write!(formatter, "不支持 Deployment Protocol 主版本 {major}")
            }
            Self::LauncherUpgradeRequired { minimum, current } => write!(
                formatter,
                "该 Studio 版本需要 Shine Launcher {minimum} 或更高版本（当前 {current}）"
            ),
            Self::UnsupportedPlatform(platform) => {
                write!(formatter, "Deployment Protocol 不支持当前平台 {platform}")
            }
            Self::UnsupportedRequiredFeature(feature) => {
                write!(formatter, "当前 Launcher 不支持必需能力 {feature}")
            }
            Self::InvalidSourceIdentity => {
                formatter.write_str("Deployment Protocol source_identity 与规范化清单不匹配")
            }
            Self::InvalidSignature(message) => {
                write!(formatter, "Deployment Protocol 签名无效：{message}")
            }
            Self::UnknownPublisher(key) => {
                write!(formatter, "发布者密钥未受信任：{key}")
            }
            Self::PublisherScopeNotAllowed(key) => {
                write!(formatter, "发布者密钥无权签署此产品：{key}")
            }
        }
    }
}

impl std::error::Error for ProtocolError {}

impl From<serde_json::Error> for ProtocolError {
    fn from(error: serde_json::Error) -> Self {
        Self::Json(error)
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TrustedPublisher {
    pub key_id: String,
    pub public_key_base64: String,
    pub products: Vec<ReleaseProduct>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TrustedPublisherStore {
    keys: HashMap<String, TrustedPublisher>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct TrustedPublisherDocument {
    schema_version: u32,
    signature_algorithm: String,
    policy: String,
    keys: Vec<TrustedPublisher>,
}

impl<'de> Deserialize<'de> for TrustedPublisher {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Entry {
            key_id: String,
            public_key_base64: String,
            products: Vec<ReleaseProduct>,
        }
        let entry = Entry::deserialize(deserializer)?;
        Ok(Self {
            key_id: entry.key_id,
            public_key_base64: entry.public_key_base64,
            products: entry.products,
        })
    }
}

impl TrustedPublisherStore {
    pub fn embedded() -> Result<Self, ProtocolError> {
        Self::from_json(include_bytes!(
            "../../config/launcher/trusted-publishers.json"
        ))
    }

    pub fn from_json(bytes: &[u8]) -> Result<Self, ProtocolError> {
        let document: TrustedPublisherDocument = serde_json::from_slice(bytes)?;
        if document.schema_version != 1 || document.signature_algorithm != "ed25519" {
            return Err(ProtocolError::InvalidField(
                "trusted publisher schema or algorithm".to_owned(),
            ));
        }
        if document.policy != "fail-closed-if-key-is-not-listed" {
            return Err(ProtocolError::InvalidField("publisher policy".to_owned()));
        }
        let mut keys = HashMap::new();
        for key in document.keys {
            let public_key = decode_fixed::<32>(&key.public_key_base64, "trusted public key")?;
            VerifyingKey::from_bytes(&public_key).map_err(|_| {
                ProtocolError::InvalidSignature("受信发布者公钥编码无效".to_owned())
            })?;
            let mut products = HashSet::new();
            if !is_safe_publisher_id(&key.key_id)
                || key.products.is_empty()
                || key.products.iter().any(|product| !products.insert(product))
                || keys.insert(key.key_id.clone(), key).is_some()
            {
                return Err(ProtocolError::InvalidField(
                    "duplicate or empty publisher key id".to_owned(),
                ));
            }
        }
        Ok(Self { keys })
    }

    pub fn verify_studio_manifest(
        &self,
        parsed: ParsedManifest,
    ) -> Result<VerifiedDeploymentManifest, ProtocolError> {
        self.verify_manifest(parsed, ReleaseProduct::Studio)
    }

    /// Verify a launcher release manifest.
    ///
    /// The launcher trusts a *separate* publisher key from the studio, so a
    /// key that may sign a studio package can never sign the launcher
    /// executable, and the other way around.
    pub fn verify_launcher_manifest(
        &self,
        parsed: ParsedManifest,
    ) -> Result<VerifiedDeploymentManifest, ProtocolError> {
        self.verify_manifest(parsed, ReleaseProduct::Launcher)
    }

    /// The publisher keys the launcher trusts, for display.
    pub fn trusted_publishers(&self) -> Vec<&TrustedPublisher> {
        let mut publishers: Vec<&TrustedPublisher> = self.keys.values().collect();
        publishers.sort_by(|left, right| left.key_id.cmp(&right.key_id));
        publishers
    }

    fn verify_manifest(
        &self,
        parsed: ParsedManifest,
        product: ReleaseProduct,
    ) -> Result<VerifiedDeploymentManifest, ProtocolError> {
        let signature = &parsed.manifest.signature;
        if signature.algorithm != "ed25519" {
            return Err(ProtocolError::InvalidSignature(
                "只接受 Ed25519 签名".to_owned(),
            ));
        }
        let publisher = self
            .keys
            .get(&signature.key_id)
            .ok_or_else(|| ProtocolError::UnknownPublisher(signature.key_id.clone()))?;
        if !publisher.products.contains(&product) {
            return Err(ProtocolError::PublisherScopeNotAllowed(
                signature.key_id.clone(),
            ));
        }
        let public_key = decode_fixed::<32>(&publisher.public_key_base64, "trusted public key")?;
        let verifying_key = VerifyingKey::from_bytes(&public_key)
            .map_err(|_| ProtocolError::InvalidSignature("受信发布者公钥编码无效".to_owned()))?;
        let signature_bytes = base64::engine::general_purpose::STANDARD
            .decode(&signature.value)
            .map_err(|_| ProtocolError::InvalidSignature("签名不是有效 Base64".to_owned()))?;
        let signature_value = Signature::from_slice(&signature_bytes)
            .map_err(|_| ProtocolError::InvalidSignature("Ed25519 签名长度无效".to_owned()))?;
        verifying_key
            .verify(&parsed.source_identity_bytes, &signature_value)
            .map_err(|_| ProtocolError::InvalidSignature("签名验证失败".to_owned()))?;

        let source_identity = parsed.manifest.source_identity.clone();
        Ok(VerifiedDeploymentManifest {
            manifest: parsed.manifest,
            verified_key_id: publisher.key_id.clone(),
            source_identity,
        })
    }

    pub fn trusted_key_count(&self) -> usize {
        self.keys.len()
    }
}

pub fn parse_manifest(
    bytes: &[u8],
    launcher_version: &str,
    platform: &str,
) -> Result<ParsedManifest, ProtocolError> {
    let manifest: DeploymentManifestV1 = serde_json::from_slice(bytes)?;
    validate_manifest(&manifest, launcher_version, platform)?;

    let source_identity_bytes = compute_source_identity(bytes)?;
    let actual = bytes_to_hex(&source_identity_bytes);
    if !manifest.source_identity.eq_ignore_ascii_case(&actual) {
        return Err(ProtocolError::InvalidSourceIdentity);
    }
    let ignored_optional_features = manifest
        .optional_features
        .iter()
        .filter(|feature| !SUPPORTED_REQUIRED_FEATURES.contains(&feature.as_str()))
        .cloned()
        .collect();
    Ok(ParsedManifest {
        manifest,
        source_identity_bytes,
        ignored_optional_features,
    })
}

pub fn embedded_trust_readiness() -> Result<String, ProtocolError> {
    let store = TrustedPublisherStore::embedded()?;
    if store.trusted_key_count() == 0 {
        Ok("未配置受信发布者密钥；自动更新保持关闭".to_owned())
    } else {
        Ok(format!(
            "已加载 {} 个受信发布者密钥",
            store.trusted_key_count()
        ))
    }
}

fn validate_manifest(
    manifest: &DeploymentManifestV1,
    launcher_version: &str,
    platform: &str,
) -> Result<(), ProtocolError> {
    let protocol_major = manifest
        .protocol_version
        .split_once('.')
        .and_then(|(major, minor)| {
            if minor.is_empty() || minor.chars().any(|ch| !ch.is_ascii_digit()) {
                return None;
            }
            major.parse::<u32>().ok()
        })
        .ok_or_else(|| ProtocolError::InvalidField("protocol_version".to_owned()))?;
    if protocol_major != SUPPORTED_PROTOCOL_MAJOR
        || manifest.compatibility.supported_protocol_major != SUPPORTED_PROTOCOL_MAJOR
    {
        return Err(ProtocolError::UnsupportedProtocol(protocol_major));
    }

    let current_version = Version::parse(launcher_version)
        .map_err(|_| ProtocolError::InvalidField("launcher_version".to_owned()))?;
    let minimum_version = Version::parse(&manifest.compatibility.min_launcher_version)
        .map_err(|_| ProtocolError::InvalidField("min_launcher_version".to_owned()))?;
    if current_version < minimum_version {
        return Err(ProtocolError::LauncherUpgradeRequired {
            minimum: minimum_version.to_string(),
            current: current_version.to_string(),
        });
    }
    if !manifest
        .compatibility
        .platform
        .iter()
        .any(|item| item == platform)
    {
        return Err(ProtocolError::UnsupportedPlatform(platform.to_owned()));
    }

    if manifest.studio.name != "Shine Video Studio"
        || manifest.studio.version.is_empty()
        || manifest.studio.version.len() > 64
        || manifest.studio.build_id.is_empty()
        || !(manifest.studio.commit.len() == 40 || manifest.studio.commit.len() == 64)
        || !manifest
            .studio
            .commit
            .chars()
            .all(|ch| ch.is_ascii_hexdigit())
    {
        return Err(ProtocolError::InvalidField("studio identity".to_owned()));
    }
    if Version::parse(&manifest.studio.version).is_err() {
        return Err(ProtocolError::InvalidField("studio.version".to_owned()));
    }
    if manifest.artifacts.is_empty() {
        return Err(ProtocolError::InvalidField("artifacts".to_owned()));
    }
    let mut artifact_ids = HashSet::new();
    for artifact in &manifest.artifacts {
        if !is_safe_identifier(&artifact.id, 64) || !artifact_ids.insert(&artifact.id) {
            return Err(ProtocolError::InvalidField("artifact id".to_owned()));
        }
        validate_release_url(&artifact.url)?;
        if artifact.size_bytes == 0 || artifact.size_bytes > MAX_ARTIFACT_BYTES {
            return Err(ProtocolError::InvalidField(
                "artifact size_bytes".to_owned(),
            ));
        }
        if artifact.sha256.len() != 64 || !artifact.sha256.chars().all(|ch| ch.is_ascii_hexdigit())
        {
            return Err(ProtocolError::InvalidField("artifact sha256".to_owned()));
        }
    }
    validate_relative_resource_path(&manifest.toolchain_catalog)?;
    validate_relative_resource_path(&manifest.workflow_root)?;
    for path in &manifest.capability_catalogs {
        validate_relative_resource_path(path)?;
    }
    if manifest.operations.is_empty() || !manifest.operations.contains(&ProtocolOperation::Probe) {
        return Err(ProtocolError::InvalidField(
            "operations must include probe".to_owned(),
        ));
    }
    if manifest.data_policy != "preserve-and-backup" {
        return Err(ProtocolError::InvalidField("data_policy".to_owned()));
    }
    for feature in &manifest.required_features {
        if !SUPPORTED_REQUIRED_FEATURES.contains(&feature.as_str()) {
            return Err(ProtocolError::UnsupportedRequiredFeature(feature.clone()));
        }
    }
    if manifest.signature.algorithm != "ed25519"
        || !is_safe_publisher_id(&manifest.signature.key_id)
    {
        return Err(ProtocolError::InvalidField("signature".to_owned()));
    }
    Ok(())
}

/// True when a URL points at an accepted release source.
///
/// The same policy governs manifest artifacts and launcher downloads, so a
/// file can never reach the disk from a host that the protocol would have
/// rejected.
pub fn is_allowed_release_url(raw: &str) -> bool {
    validate_release_url(raw).is_ok()
}

fn validate_release_url(raw: &str) -> Result<(), ProtocolError> {
    let url =
        Url::parse(raw).map_err(|_| ProtocolError::InvalidField("artifact url".to_owned()))?;
    if url.scheme() != "https"
        || url.username() != ""
        || url.password().is_some()
        || url.port().is_some()
        || url.fragment().is_some()
        || !url
            .host_str()
            .is_some_and(|host| ACCEPTED_RELEASE_HOSTS.contains(&host))
    {
        return Err(ProtocolError::InvalidField(
            "artifact url must be HTTPS on an allow-listed GitHub host".to_owned(),
        ));
    }
    Ok(())
}

fn validate_relative_resource_path(path: &str) -> Result<(), ProtocolError> {
    if path.is_empty()
        || path.starts_with('/')
        || path.contains('\\')
        || path.contains(':')
        || !path
            .split('/')
            .all(|part| !part.is_empty() && part != "." && part != "..")
        || !path
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-' | b'/'))
    {
        return Err(ProtocolError::InvalidField(
            "catalog paths must be relative and cannot escape the version root".to_owned(),
        ));
    }
    Ok(())
}

fn is_safe_identifier(value: &str, max_length: usize) -> bool {
    !value.is_empty()
        && value.len() <= max_length
        && value.as_bytes()[0].is_ascii_lowercase()
        && value
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-')
}

fn is_safe_publisher_id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'))
}

/// The canonical identity of a manifest document.
///
/// The identity covers the whole declarative manifest with the identity and
/// the signature removed, so a signature can never be moved to a different
/// manifest.
pub fn compute_source_identity(bytes: &[u8]) -> Result<[u8; 32], ProtocolError> {
    let mut document: Value = serde_json::from_slice(bytes)?;
    let object = document
        .as_object_mut()
        .ok_or_else(|| ProtocolError::InvalidField("root must be an object".to_owned()))?;
    object.remove("source_identity");
    object.remove("signature");
    let canonical = serde_jcs::to_vec(&document).map_err(|error| {
        ProtocolError::InvalidField(format!("RFC 8785 canonical JSON: {error}"))
    })?;
    Ok(Sha256::digest(canonical).into())
}

fn decode_fixed<const N: usize>(value: &str, label: &str) -> Result<[u8; N], ProtocolError> {
    let decoded = base64::engine::general_purpose::STANDARD
        .decode(value)
        .map_err(|_| ProtocolError::InvalidSignature(format!("{label} 不是有效 Base64")))?;
    decoded
        .try_into()
        .map_err(|_| ProtocolError::InvalidSignature(format!("{label} 长度无效")))
}

#[cfg(test)]
fn decode_hex_identity(value: &str) -> Result<[u8; 32], ProtocolError> {
    if value.len() != 64 {
        return Err(ProtocolError::InvalidSourceIdentity);
    }
    let mut bytes = [0_u8; 32];
    for (index, slot) in bytes.iter_mut().enumerate() {
        let start = index * 2;
        *slot = u8::from_str_radix(&value[start..start + 2], 16)
            .map_err(|_| ProtocolError::InvalidSourceIdentity)?;
    }
    Ok(bytes)
}

fn bytes_to_hex(bytes: &[u8; 32]) -> String {
    let mut hex = String::with_capacity(64);
    for byte in bytes {
        use std::fmt::Write as _;
        let _ = write!(hex, "{byte:02x}");
    }
    hex
}

#[cfg(test)]
mod tests {
    use base64::Engine as _;
    use ed25519_dalek::{Signer, SigningKey};
    use serde_json::{Value, json};

    use super::{
        ProtocolError, ReleaseProduct, TrustedPublisherStore, compute_source_identity,
        decode_hex_identity, parse_manifest,
    };

    const FIXTURE: &str =
        include_str!("../../config/launcher/test-fixtures/deployment-protocol-v1-minimal.json");
    /// A manifest signed by the production studio publisher key.
    ///
    /// Only the public signature is stored. It is a permanent regression test
    /// that the trust anchor committed to the repository really does verify
    /// a manifest, and that a tampered manifest no longer does.
    const SIGNED_PRODUCTION_FIXTURE: &str = include_str!(
        "../../config/launcher/test-fixtures/deployment-protocol-v1-signed-production.json"
    );
    const SCHEMA: &str = include_str!("../../config/launcher/deployment-protocol-v1.schema.json");

    fn signed_fixture() -> (Vec<u8>, String, String) {
        let signing_key = SigningKey::from_bytes(&[42_u8; 32]);
        let verifying_key = signing_key.verifying_key();
        let public_key = base64::engine::general_purpose::STANDARD.encode(verifying_key.to_bytes());
        let mut value: Value = serde_json::from_str(FIXTURE).unwrap();
        let root = value.as_object_mut().unwrap();
        root.remove("source_identity");
        root.remove("signature");
        let identity_bytes = compute_source_identity(&serde_json::to_vec(&value).unwrap()).unwrap();
        let identity = identity_bytes
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>();
        let identity_raw = decode_hex_identity(&identity).unwrap();
        let signature = signing_key.sign(&identity_raw);
        value["source_identity"] = json!(identity);
        value["signature"] = json!({
            "algorithm": "ed25519",
            "key_id": "unit-test",
            "value": base64::engine::general_purpose::STANDARD.encode(signature.to_bytes())
        });
        (serde_json::to_vec(&value).unwrap(), public_key, identity)
    }

    fn test_trust_store(public_key: &str, products: &[&str]) -> TrustedPublisherStore {
        let document = json!({
            "schema_version": 1,
            "signature_algorithm": "ed25519",
            "policy": "fail-closed-if-key-is-not-listed",
            "keys": [{
                "key_id": "unit-test",
                "public_key_base64": public_key,
                "products": products,
            }]
        });
        TrustedPublisherStore::from_json(&serde_json::to_vec(&document).unwrap()).unwrap()
    }

    #[test]
    fn schema_and_fixture_are_valid_json_and_the_trust_anchors_are_separated() {
        let schema: Value = serde_json::from_str(SCHEMA).unwrap();
        let fixture: Value = serde_json::from_str(FIXTURE).unwrap();
        assert_eq!(schema["title"], "Shine Deployment Protocol V1");
        assert_eq!(fixture["protocol_version"], "1.0");

        let store = TrustedPublisherStore::embedded().unwrap();
        // Production trust anchors are configured, one per product, so a
        // studio key can never sign a launcher update or the other way round.
        assert_eq!(store.trusted_key_count(), 2);
        let publishers = store.trusted_publishers();
        assert_eq!(publishers.len(), 2);
        assert!(
            publishers
                .iter()
                .any(|publisher| publisher.products == vec![ReleaseProduct::Studio])
        );
        assert!(
            publishers
                .iter()
                .any(|publisher| publisher.products == vec![ReleaseProduct::Launcher])
        );
    }

    #[test]
    fn a_manifest_signed_by_the_production_publisher_verifies_against_the_committed_anchor() {
        let bytes = SIGNED_PRODUCTION_FIXTURE.as_bytes();
        let parsed = parse_manifest(bytes, "1.0.0", "windows-x64").unwrap();
        let store = TrustedPublisherStore::embedded().unwrap();
        let verified = store.verify_studio_manifest(parsed).unwrap();
        assert_eq!(verified.verified_key_id(), "shine-studio-release-2026");
        assert_eq!(
            verified.source_identity(),
            "4c32c9713eabd2d3ed6d0daf69608ce0f9c7cbf5b495bdbbf6a693106c774354"
        );
    }

    #[test]
    fn a_tampered_production_manifest_stops_verifying() {
        let mut value: Value = serde_json::from_str(SIGNED_PRODUCTION_FIXTURE).unwrap();
        value["artifacts"][0]["size_bytes"] = json!(999);
        let tampered = serde_json::to_vec(&value).unwrap();
        // The canonical identity no longer matches the manifest.
        assert!(matches!(
            parse_manifest(&tampered, "1.0.0", "windows-x64"),
            Err(ProtocolError::InvalidSourceIdentity)
        ));
    }

    #[test]
    fn a_launcher_manifest_needs_a_launcher_scoped_publisher() {
        let (bytes, public_key, _) = signed_fixture();
        let parsed = parse_manifest(&bytes, "1.0.0", "windows-x64").unwrap();
        // The fixture is signed by the unit test key, which is scoped to the
        // studio in the helper store.
        let store = test_trust_store(&public_key, &["launcher"]);
        assert!(store.verify_launcher_manifest(parsed).is_ok());

        let (bytes, public_key, _) = signed_fixture();
        let parsed = parse_manifest(&bytes, "1.0.0", "windows-x64").unwrap();
        let store = test_trust_store(&public_key, &["studio"]);
        assert!(matches!(
            store.verify_launcher_manifest(parsed),
            Err(ProtocolError::PublisherScopeNotAllowed(_))
        ));
    }

    #[test]
    fn valid_signed_manifest_requires_an_explicit_studio_trust_anchor() {
        let (bytes, public_key, identity) = signed_fixture();
        let parsed = parse_manifest(&bytes, "1.0.0", "windows-x64").unwrap();
        assert_eq!(parsed.manifest().source_identity, identity);
        let verified = test_trust_store(&public_key, &["studio"])
            .verify_studio_manifest(parsed)
            .unwrap();
        assert_eq!(verified.verified_key_id(), "unit-test");
        assert_eq!(verified.authorized_operations().len(), 6);
    }

    #[test]
    fn current_embedded_store_fails_closed_when_no_publisher_key_is_configured() {
        let (bytes, _, _) = signed_fixture();
        let parsed = parse_manifest(&bytes, "1.0.0", "windows-x64").unwrap();
        let error = TrustedPublisherStore::embedded()
            .unwrap()
            .verify_studio_manifest(parsed)
            .unwrap_err();
        assert!(matches!(error, ProtocolError::UnknownPublisher(_)));
    }

    #[test]
    fn version_platform_features_paths_and_hosts_are_checked_before_verification() {
        let (bytes, public_key, _) = signed_fixture();
        assert!(matches!(
            parse_manifest(&bytes, "0.9.9", "windows-x64"),
            Err(ProtocolError::LauncherUpgradeRequired { .. })
        ));
        assert!(matches!(
            parse_manifest(&bytes, "1.0.0", "linux-x64"),
            Err(ProtocolError::UnsupportedPlatform(_))
        ));

        let mut value: Value = serde_json::from_slice(&bytes).unwrap();
        value["required_features"] = json!(["unknown-required-feature"]);
        let error = parse_manifest(&resign_value(value), "1.0.0", "windows-x64").unwrap_err();
        assert!(matches!(
            error,
            ProtocolError::UnsupportedRequiredFeature(_)
        ));

        let mut value: Value = serde_json::from_slice(&bytes).unwrap();
        value["toolchain_catalog"] = json!("../../outside.json");
        let error = parse_manifest(&resign_value(value), "1.0.0", "windows-x64").unwrap_err();
        assert!(matches!(error, ProtocolError::InvalidField(_)));

        let mut value: Value = serde_json::from_slice(&bytes).unwrap();
        value["artifacts"][0]["url"] = json!("https://evil.example/Studio.zip");
        let error = parse_manifest(&resign_value(value), "1.0.0", "windows-x64").unwrap_err();
        assert!(matches!(error, ProtocolError::InvalidField(_)));

        let mut value: Value = serde_json::from_slice(&bytes).unwrap();
        value["optional_features"] = json!(["unknown-optional-feature"]);
        value["custom_optional_payload"] = json!({"launcher_is_not_a_shell": "cmd /c whoami"});
        let reparsed = parse_manifest(&resign_value(value), "1.0.0", "windows-x64").unwrap();
        assert_eq!(
            reparsed.ignored_optional_features(),
            &["unknown-optional-feature"]
        );
        assert_eq!(
            reparsed.manifest().optional_fields["custom_optional_payload"]["launcher_is_not_a_shell"],
            "cmd /c whoami"
        );
        let error = test_trust_store(&public_key, &["launcher"])
            .verify_studio_manifest(reparsed)
            .unwrap_err();
        assert!(matches!(error, ProtocolError::PublisherScopeNotAllowed(_)));
    }

    #[test]
    fn arbitrary_operation_strings_are_rejected_by_the_typed_contract() {
        let (bytes, _, _) = signed_fixture();
        let mut value: Value = serde_json::from_slice(&bytes).unwrap();
        value["operations"][0] = json!("run-powershell");
        let error = parse_manifest(&resign_value(value), "1.0.0", "windows-x64").unwrap_err();
        assert!(matches!(error, ProtocolError::Json(_)));
    }

    #[test]
    fn source_hash_and_signature_are_bound_to_the_entire_declarative_manifest() {
        let (bytes, public_key, _) = signed_fixture();
        let mut value: Value = serde_json::from_slice(&bytes).unwrap();
        value["artifacts"][0]["size_bytes"] = json!(999);
        let tampered = serde_json::to_vec(&value).unwrap();
        assert!(matches!(
            parse_manifest(&tampered, "1.0.0", "windows-x64"),
            Err(ProtocolError::InvalidSourceIdentity)
        ));

        let (bytes, _, _) = signed_fixture();
        let mut value: Value = serde_json::from_slice(&bytes).unwrap();
        value["signature"]["value"] =
            json!(base64::engine::general_purpose::STANDARD.encode([0_u8; 64]));
        let parsed =
            parse_manifest(&serde_json::to_vec(&value).unwrap(), "1.0.0", "windows-x64").unwrap();
        assert!(matches!(
            test_trust_store(&public_key, &["studio"]).verify_studio_manifest(parsed),
            Err(ProtocolError::InvalidSignature(_))
        ));
    }

    #[test]
    fn source_identity_uses_whitespace_and_key_order_independent_jcs() {
        let left = br#"{"b":2,"a":1,"source_identity":"ignored","signature":{"value":"ignored"}}"#;
        let right = br#"{
          "signature": {"value": "different"},
          "a": 1,
          "source_identity": "also ignored",
          "b": 2
        }"#;
        assert_eq!(
            compute_source_identity(left).unwrap(),
            compute_source_identity(right).unwrap()
        );
    }

    fn resign_value(mut value: Value) -> Vec<u8> {
        let signing_key = SigningKey::from_bytes(&[42_u8; 32]);
        let root = value.as_object_mut().unwrap();
        root.remove("source_identity");
        root.remove("signature");
        let identity_bytes = compute_source_identity(&serde_json::to_vec(&value).unwrap()).unwrap();
        let identity = identity_bytes
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>();
        let signature = signing_key.sign(&decode_hex_identity(&identity).unwrap());
        value["source_identity"] = json!(identity);
        value["signature"] = json!({
            "algorithm": "ed25519",
            "key_id": "unit-test",
            "value": base64::engine::general_purpose::STANDARD.encode(signature.to_bytes())
        });
        serde_json::to_vec(&value).unwrap()
    }
}
