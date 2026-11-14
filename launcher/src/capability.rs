//! Capability packs: what an installation can actually do.
//!
//! The workbench's capability bundles belong to the Studio's deployment
//! manifest, not to the launcher. The launcher only has to understand a small
//! number of generic operations and report honestly what is present.
//!
//! The honesty rules are the hard part:
//!
//! * Only a variant that the project itself marks as supported and allowed to
//!   download may be fetched automatically. A candidate that is merely
//!   "compatible with low VRAM" stays untouched.
//! * A capability that has no closed loop — no pinned upstream, no node
//!   contract, no machine evidence — is reported as deferred. It is never
//!   counted as installed.
//! * The state is layered. Files being on disk is not the same as the
//!   runtime being compatible, and neither is the same as a real generation
//!   having been verified.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::path_safety::{regular_file_without_reparse, validate_local_absolute_path};

/// A way to obtain the digest of a file, used to avoid re-hashing very large
/// model files when the caller already knows the answer.
pub type DigestLookup = dyn Fn(&Path) -> Option<String>;

/// Where a capability's files live inside a managed ComfyUI instance.
const MODEL_FAMILIES: &[&str] = &[
    "diffusion_models",
    "text_encoders",
    "vae",
    "loras",
    "pdd_acc",
    "unet",
    "clip",
    "checkpoints",
];

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct CatalogFile {
    pub relative_path: String,
    #[serde(default)]
    pub url: String,
    #[serde(default)]
    pub sha256: String,
    #[serde(default)]
    pub size: u64,
    #[serde(default)]
    pub dependency: Option<String>,
    #[serde(default)]
    pub version: Option<String>,
    #[serde(default)]
    pub redirect_hosts: Vec<String>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct CatalogComponent {
    pub component_id: String,
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub role: Option<String>,
    #[serde(default)]
    pub destination_family: Option<String>,
    #[serde(default)]
    pub dependencies: Vec<String>,
    #[serde(default)]
    pub files: Vec<CatalogFile>,
    #[serde(default)]
    pub license_id: Option<String>,
    #[serde(default)]
    pub license_url: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct DeploymentCatalog {
    pub catalog_id: String,
    #[serde(default)]
    pub catalog_version: String,
    #[serde(default)]
    pub platform: HashMap<String, serde_json::Value>,
    #[serde(default)]
    pub components: Vec<CatalogComponent>,
    #[serde(default)]
    pub total_bytes: u64,
}

/// A capability variant as declared by the project.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct CapabilityVariant {
    pub variant_id: String,
    #[serde(default)]
    pub model_family: String,
    #[serde(default)]
    pub generation_mode: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub label: String,
    #[serde(default)]
    pub summary: String,
    /// `SUPPORTED`, `CANDIDATE`, `DEFERRED`…
    #[serde(default)]
    pub acceptance_status: String,
    #[serde(default)]
    pub default_recommended: bool,
    /// Whether the launcher may download this variant on its own.
    #[serde(default)]
    pub auto_download_allowed: bool,
    #[serde(default)]
    pub catalog_id: String,
    #[serde(default)]
    pub required_component_ids: Vec<String>,
    #[serde(default)]
    pub install_component_ids: Vec<String>,
    #[serde(default)]
    pub workflow_profile_ids: Vec<String>,
    #[serde(default)]
    pub checkpoint_id: Option<String>,
}

/// The layered truth about one capability.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CapabilityState {
    /// Nothing of this capability is present.
    NotInstalled,
    /// The files are on disk and their digests match.
    FilesVerified,
    /// The files match and the runtime that would use them is compatible.
    EnvCompatible,
    /// A managed runtime is running and serving the workflow contract.
    ManagedRuntimeReady,
    /// A real generation was verified on this machine.
    RealGenerationVerified,
    /// The capability has no closed loop and is not claimed.
    Deferred,
    /// The launcher may not install this capability on its own.
    Blocked,
}

impl CapabilityState {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::NotInstalled => "not_installed",
            Self::FilesVerified => "files_verified",
            Self::EnvCompatible => "env_compatible",
            Self::ManagedRuntimeReady => "managed_runtime_ready",
            Self::RealGenerationVerified => "real_generation_verified",
            Self::Deferred => "deferred",
            Self::Blocked => "blocked",
        }
    }

    /// The user facing wording. It never promises more than was proven.
    pub fn user_message(self) -> &'static str {
        match self {
            Self::NotInstalled => "尚未安装。",
            Self::FilesVerified => "文件已就位并通过校验，运行验证尚未进行。",
            Self::EnvCompatible => "依赖兼容，尚未验证真实出片。",
            Self::ManagedRuntimeReady => "受管运行时就绪，尚未验证真实出片。",
            Self::RealGenerationVerified => "已通过真实生成验证。",
            Self::Deferred => "该能力尚未形成完整闭环，暂不提供。",
            Self::Blocked => "该项目不允许自动安装该能力。",
        }
    }

    /// True only for the state that a real generation proved.
    pub fn is_ready(self) -> bool {
        matches!(self, Self::RealGenerationVerified)
    }
}

/// The status of one capability on one machine.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct CapabilityStatus {
    pub variant_id: String,
    pub title: String,
    pub state: CapabilityState,
    pub verified_components: Vec<String>,
    pub missing_components: Vec<String>,
    pub missing_files: Vec<String>,
    pub total_bytes: u64,
    pub verified_bytes: u64,
    pub download_blocked_reason: Option<String>,
}

impl CapabilityStatus {
    /// A short explanation for the interface.
    pub fn summary(&self) -> String {
        let mut text = format!("{}：{}", self.title, self.state.user_message());
        if let Some(reason) = &self.download_blocked_reason {
            text.push_str(&format!("（{reason}）"));
        } else if !self.missing_components.is_empty() {
            text.push_str(&format!("缺少 {} 个组件。", self.missing_components.len()));
        }
        text
    }
}

/// A capability pack: a variant plus the catalog it draws from.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CapabilityPack {
    pub variant: CapabilityVariant,
    pub components: Vec<CatalogComponent>,
}

impl CapabilityPack {
    /// Build a pack from a variant and the catalog it names.
    pub fn build(
        variant: CapabilityVariant,
        catalog: &DeploymentCatalog,
    ) -> Result<Self, CapabilityError> {
        let wanted: HashSet<&str> = if variant.install_component_ids.is_empty() {
            catalog
                .components
                .iter()
                .map(|component| component.component_id.as_str())
                .collect()
        } else {
            variant
                .install_component_ids
                .iter()
                .map(String::as_str)
                .collect()
        };
        let components: Vec<CatalogComponent> = catalog
            .components
            .iter()
            .filter(|component| wanted.contains(component.component_id.as_str()))
            .cloned()
            .collect();
        if components.is_empty() {
            return Err(CapabilityError::UnknownCatalog(catalog.catalog_id.clone()));
        }
        Ok(Self {
            variant,
            components,
        })
    }

    /// The total download size of the pack.
    pub fn total_bytes(&self) -> u64 {
        self.components
            .iter()
            .flat_map(|component| component.files.iter())
            .map(|file| file.size)
            .sum()
    }

    /// The number of files the pack contains.
    pub fn file_count(&self) -> usize {
        self.components
            .iter()
            .map(|component| component.files.len())
            .sum()
    }

    /// Whether the launcher may install this pack without an explicit,
    /// informed user choice.
    ///
    /// The project decides this in the variant manifest. The launcher never
    /// decides to unlock a candidate model on its own.
    pub fn auto_install_allowed(&self) -> bool {
        self.variant
            .acceptance_status
            .eq_ignore_ascii_case("SUPPORTED")
            && self.variant.auto_download_allowed
    }

    /// Why automatic installation is refused, if it is.
    pub fn auto_install_block_reason(&self) -> Option<String> {
        if self.auto_install_allowed() {
            return None;
        }
        Some(
            if self
                .variant
                .acceptance_status
                .eq_ignore_ascii_case("SUPPORTED")
            {
                "项目未允许自动下载该能力".to_owned()
            } else {
                format!(
                    "该能力的项目验收状态为 {}，不满足正式支持的下载条件",
                    self.variant.acceptance_status
                )
            },
        )
    }

    /// Where one component's files belong inside a managed instance.
    pub fn destination_for(&self, component: &CatalogComponent, instance_root: &Path) -> PathBuf {
        let family = component
            .destination_family
            .as_deref()
            .filter(|value| MODEL_FAMILIES.contains(value))
            .unwrap_or(component.kind.as_str());
        match component.kind.as_str() {
            "source" => instance_root.join("ComfyUI"),
            "node" => instance_root.join("ComfyUI").join("custom_nodes"),
            "wheel" => instance_root.join("python").join("wheels"),
            _ => instance_root.join("models").join(family),
        }
    }

    /// Check what of the pack is present on this machine.
    ///
    /// Only files whose digest matches count as present. A file that exists
    /// with the wrong digest is a missing file, because a truncated download
    /// is worse than no download at all.
    pub fn inspect(
        &self,
        instance_root: &Path,
        hash_override: Option<&DigestLookup>,
    ) -> CapabilityStatus {
        let mut verified_components = Vec::new();
        let mut missing_components = Vec::new();
        let mut missing_files = Vec::new();
        let mut total_bytes = 0_u64;
        let mut verified_bytes = 0_u64;

        for component in &self.components {
            let destination = self.destination_for(component, instance_root);
            let mut component_ok = true;
            for file in &component.files {
                total_bytes += file.size;
                let path = destination.join(&file.relative_path);
                let present = regular_file_without_reparse(&path).unwrap_or(false)
                    && file_matches(&path, &file.sha256, hash_override);
                if present {
                    verified_bytes += file.size;
                } else {
                    component_ok = false;
                    missing_files
                        .push(format!("{}:{}", component.component_id, file.relative_path));
                }
            }
            if component_ok {
                verified_components.push(component.component_id.clone());
            } else {
                missing_components.push(component.component_id.clone());
            }
        }

        let state = if self
            .variant
            .acceptance_status
            .eq_ignore_ascii_case("DEFERRED")
        {
            CapabilityState::Deferred
        } else if !self.auto_install_allowed() {
            CapabilityState::Blocked
        } else if missing_components.is_empty() {
            CapabilityState::FilesVerified
        } else {
            CapabilityState::NotInstalled
        };

        CapabilityStatus {
            variant_id: self.variant.variant_id.clone(),
            title: if self.variant.title.is_empty() {
                self.variant.label.clone()
            } else {
                self.variant.title.clone()
            },
            state,
            verified_components,
            missing_components,
            missing_files,
            total_bytes,
            verified_bytes,
            download_blocked_reason: self.auto_install_block_reason(),
        }
    }
}

fn file_matches(path: &Path, expected_sha256: &str, hash_override: Option<&DigestLookup>) -> bool {
    if expected_sha256.is_empty() {
        // A file without a declared digest can only be counted as present by
        // size, which is not proof of identity. It is reported as present so
        // the operator can see it, but it never upgrades the pack state on
        // its own.
        return path.is_file();
    }
    let actual = match hash_override {
        Some(hash) => hash(path).unwrap_or_default(),
        None => crate::download::hash_file(path).unwrap_or_default(),
    };
    actual.eq_ignore_ascii_case(expected_sha256)
}

/// Parse a deployment catalog document.
pub fn parse_catalog(bytes: &[u8]) -> Result<DeploymentCatalog, CapabilityError> {
    serde_json::from_slice(bytes)
        .map_err(|error| CapabilityError::InvalidCatalog(error.to_string()))
}

/// Parse a capability variant document.
pub fn parse_variant(bytes: &[u8]) -> Result<CapabilityVariant, CapabilityError> {
    serde_json::from_slice(bytes)
        .map_err(|error| CapabilityError::InvalidVariant(error.to_string()))
}

/// Read a catalog and a variant from an installation's `config/deployment`.
///
/// The paths are resolved inside the installation, so a manifest cannot point
/// the launcher at a file outside it.
pub fn load_pack(
    install_root: &Path,
    catalog_relative: &str,
    variant_relative: &str,
) -> Result<CapabilityPack, CapabilityError> {
    validate_local_absolute_path(install_root).map_err(CapabilityError::InvalidPath)?;
    let catalog_path = install_root.join(catalog_relative);
    let variant_path = install_root.join(variant_relative);
    for path in [&catalog_path, &variant_path] {
        if !path.starts_with(install_root) {
            return Err(CapabilityError::InvalidPath(
                "能力清单必须位于安装目录内".to_owned(),
            ));
        }
    }
    let catalog = parse_catalog(
        &std::fs::read(&catalog_path)
            .map_err(|error| CapabilityError::Unreadable(error.to_string()))?,
    )?;
    let variant = parse_variant(
        &std::fs::read(&variant_path)
            .map_err(|error| CapabilityError::Unreadable(error.to_string()))?,
    )?;
    CapabilityPack::build(variant, &catalog)
}

#[derive(Debug)]
pub enum CapabilityError {
    Unreadable(String),
    InvalidPath(String),
    InvalidCatalog(String),
    InvalidVariant(String),
    UnknownCatalog(String),
}

impl std::fmt::Display for CapabilityError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Unreadable(message) => write!(formatter, "无法读取能力清单：{message}"),
            Self::InvalidPath(message) => formatter.write_str(message),
            Self::InvalidCatalog(message) => write!(formatter, "部署清单格式无效：{message}"),
            Self::InvalidVariant(message) => write!(formatter, "能力变体格式无效：{message}"),
            Self::UnknownCatalog(catalog) => write!(formatter, "清单 {catalog} 没有可用组件"),
        }
    }
}

impl std::error::Error for CapabilityError {}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::path::Path;

    use serde_json::json;
    use tempfile::{TempDir, tempdir_in};

    use super::{
        CapabilityPack, CapabilityState, CapabilityVariant, DeploymentCatalog, load_pack,
        parse_catalog, parse_variant,
    };

    fn isolated_tempdir() -> TempDir {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("target/test-tmp");
        fs::create_dir_all(&root).unwrap();
        tempdir_in(root).unwrap()
    }

    fn variant(acceptance: &str, auto: bool) -> CapabilityVariant {
        CapabilityVariant {
            variant_id: "h3-ref2va-int8-convrot".to_owned(),
            model_family: "MiniMax-H3".to_owned(),
            generation_mode: "Ref2VA".to_owned(),
            title: "H3 Ref2VA INT8 ConvRot".to_owned(),
            label: "Ref2VA INT8 ConvRot".to_owned(),
            summary: String::new(),
            acceptance_status: acceptance.to_owned(),
            default_recommended: true,
            auto_download_allowed: auto,
            catalog_id: "h3-ref2va-windows-x64".to_owned(),
            required_component_ids: vec!["minimax-h3-diffusion".to_owned()],
            install_component_ids: vec![
                "comfyui".to_owned(),
                "minimax-h3-diffusion".to_owned(),
                "minimax-h3-vae".to_owned(),
            ],
            workflow_profile_ids: Vec::new(),
            checkpoint_id: None,
        }
    }

    fn catalog() -> DeploymentCatalog {
        DeploymentCatalog {
            catalog_id: "h3-ref2va-windows-x64".to_owned(),
            catalog_version: "1.3.0".to_owned(),
            platform: Default::default(),
            components: vec![
                super::CatalogComponent {
                    component_id: "comfyui".to_owned(),
                    kind: "source".to_owned(),
                    status: "READY".to_owned(),
                    role: None,
                    destination_family: None,
                    dependencies: Vec::new(),
                    files: vec![super::CatalogFile {
                        relative_path: "ComfyUI.zip".to_owned(),
                        url: "https://codeload.github.com/Comfy-Org/ComfyUI/zip/abc".to_owned(),
                        sha256: "a".repeat(64),
                        size: 10,
                        dependency: None,
                        version: None,
                        redirect_hosts: Vec::new(),
                    }],
                    license_id: None,
                    license_url: None,
                },
                super::CatalogComponent {
                    component_id: "minimax-h3-diffusion".to_owned(),
                    kind: "model".to_owned(),
                    status: "READY".to_owned(),
                    role: None,
                    destination_family: Some("diffusion_models".to_owned()),
                    dependencies: Vec::new(),
                    files: vec![super::CatalogFile {
                        relative_path: "h3.safetensors".to_owned(),
                        url: "https://huggingface.co/x/y/resolve/abc/h3.safetensors".to_owned(),
                        sha256: "b".repeat(64),
                        size: 20,
                        dependency: None,
                        version: None,
                        redirect_hosts: Vec::new(),
                    }],
                    license_id: None,
                    license_url: None,
                },
                super::CatalogComponent {
                    component_id: "minimax-h3-vae".to_owned(),
                    kind: "model".to_owned(),
                    status: "READY".to_owned(),
                    role: None,
                    destination_family: Some("vae".to_owned()),
                    dependencies: Vec::new(),
                    files: vec![super::CatalogFile {
                        relative_path: "vae.safetensors".to_owned(),
                        url: "https://huggingface.co/x/y/resolve/abc/vae.safetensors".to_owned(),
                        sha256: "c".repeat(64),
                        size: 5,
                        dependency: None,
                        version: None,
                        redirect_hosts: Vec::new(),
                    }],
                    license_id: None,
                    license_url: None,
                },
            ],
            total_bytes: 35,
        }
    }

    #[test]
    fn a_supported_variant_may_be_installed_automatically() {
        let pack = CapabilityPack::build(variant("SUPPORTED", true), &catalog()).unwrap();
        assert!(pack.auto_install_allowed());
        assert!(pack.auto_install_block_reason().is_none());
    }

    #[test]
    fn a_candidate_variant_is_never_installed_automatically() {
        let pack = CapabilityPack::build(variant("CANDIDATE", true), &catalog()).unwrap();
        assert!(!pack.auto_install_allowed());
        assert!(pack.auto_install_block_reason().is_some());
        let status = pack.inspect(Path::new("/nonexistent"), None);
        assert_eq!(status.state, CapabilityState::Blocked);
        assert!(status.download_blocked_reason.is_some());
    }

    #[test]
    fn a_deferred_capability_is_reported_as_deferred() {
        let pack = CapabilityPack::build(variant("DEFERRED", false), &catalog()).unwrap();
        let status = pack.inspect(Path::new("/nonexistent"), None);
        assert_eq!(status.state, CapabilityState::Deferred);
        assert!(!status.state.is_ready());
    }

    #[test]
    fn a_pack_only_contains_the_components_the_variant_names() {
        let pack = CapabilityPack::build(variant("SUPPORTED", true), &catalog()).unwrap();
        assert_eq!(pack.components.len(), 3);
        assert_eq!(pack.total_bytes(), 35);
    }

    #[test]
    fn missing_files_keep_the_pack_out_of_the_verified_state() {
        let temp = isolated_tempdir();
        let instance = temp.path().join("instance");
        let pack = CapabilityPack::build(variant("SUPPORTED", true), &catalog()).unwrap();
        let status = pack.inspect(&instance, None);
        assert_eq!(status.state, CapabilityState::NotInstalled);
        assert_eq!(status.missing_components.len(), 3);
        assert!(status.verified_components.is_empty());
        assert_eq!(status.total_bytes, 35);
    }

    /// A catalog whose declared digests match the placeholder payload, so a
    /// present file really does verify.
    fn catalog_with_matching_digests() -> DeploymentCatalog {
        use sha2::{Digest, Sha256};
        let digest = Sha256::digest(b"payload")
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>();
        let mut catalog = catalog();
        for component in &mut catalog.components {
            for file in &mut component.files {
                file.sha256 = digest.clone();
            }
        }
        catalog
    }

    /// Write every file of a pack into an instance directory.
    fn place_files(pack: &CapabilityPack, instance: &Path) {
        for component in &pack.components {
            let destination = pack.destination_for(component, instance);
            fs::create_dir_all(&destination).unwrap();
            for file in &component.files {
                fs::write(destination.join(&file.relative_path), b"payload").unwrap();
            }
        }
    }

    #[test]
    fn a_file_with_the_wrong_digest_does_not_count_as_present() {
        let temp = isolated_tempdir();
        let instance = temp.path().join("instance");
        let pack = CapabilityPack::build(variant("SUPPORTED", true), &catalog()).unwrap();

        // Every file is on disk, but the catalog declares digests that the
        // bytes on disk do not have.
        place_files(&pack, &instance);
        let status = pack.inspect(&instance, None);
        assert_eq!(status.state, CapabilityState::NotInstalled);
        assert!(status.verified_components.is_empty());
        assert_eq!(status.missing_components.len(), 3);
    }

    #[test]
    fn every_declared_file_present_with_the_right_digest_reaches_files_verified() {
        let temp = isolated_tempdir();
        let instance = temp.path().join("instance");
        let pack =
            CapabilityPack::build(variant("SUPPORTED", true), &catalog_with_matching_digests())
                .unwrap();

        place_files(&pack, &instance);
        let status = pack.inspect(&instance, None);
        assert_eq!(status.state, CapabilityState::FilesVerified);
        assert!(status.missing_components.is_empty());
        assert_eq!(status.verified_bytes, 35);
        // Files being present is not the same as a real generation.
        assert!(!status.state.is_ready());
    }

    #[test]
    fn a_pack_is_loaded_from_the_installation_configuration() {
        let temp = isolated_tempdir();
        let root = temp.path().join("studio");
        let deployment = root.join("config/deployment");
        fs::create_dir_all(&deployment).unwrap();
        fs::write(
            deployment.join("catalog.json"),
            serde_json::to_vec(&catalog()).unwrap(),
        )
        .unwrap();
        fs::write(
            deployment.join("variant.json"),
            serde_json::to_vec(&variant("SUPPORTED", true)).unwrap(),
        )
        .unwrap();

        let pack = load_pack(
            &root,
            "config/deployment/catalog.json",
            "config/deployment/variant.json",
        )
        .unwrap();
        assert_eq!(pack.variant.variant_id, "h3-ref2va-int8-convrot");
        assert_eq!(pack.components.len(), 3);
    }

    #[test]
    fn a_pack_cannot_be_loaded_from_outside_the_installation() {
        let temp = isolated_tempdir();
        let root = temp.path().join("studio");
        fs::create_dir_all(&root).unwrap();
        assert!(load_pack(&root, "../outside.json", "../outside.json").is_err());
    }

    #[test]
    fn a_malformed_catalog_is_refused() {
        assert!(parse_catalog(b"{not json").is_err());
        assert!(parse_variant(b"[]").is_err());
        let value = json!({"catalog_id": "x", "components": []});
        let empty: DeploymentCatalog = serde_json::from_value(value).unwrap();
        assert!(CapabilityPack::build(variant("SUPPORTED", true), &empty).is_err());
    }
}
