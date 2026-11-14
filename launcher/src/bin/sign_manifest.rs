//! Sign a Deployment Protocol V1 manifest.
//!
//! This is the release-side half of the trust chain. It is a separate binary
//! so that the private key is only ever touched by the release job, never by
//! the launcher.
//!
//! The canonical identity is computed with the same code the launcher uses
//! to verify it, so a manifest signed here verifies byte for byte.
//!
//! Usage:
//!
//! ```text
//! shine-sign-manifest --key <seed-file> --key-id <id> \
//!     --input <manifest.json> --output <signed-manifest.json>
//! ```
//!
//! The seed file holds the base64 encoded 32 byte Ed25519 seed. In GitHub
//! Actions it is provided through the `SHINE_RELEASE_SIGNING_KEY` secret and
//! written to a temporary file that is removed afterwards. The private key is
//! never committed to the repository.

use std::fs;
use std::path::PathBuf;
use std::process::ExitCode;

use base64::Engine as _;
use ed25519_dalek::{Signer, SigningKey};
use serde_json::{Value, json};

use shine_launcher::protocol::compute_source_identity;

fn usage() -> &'static str {
    "用法：shine-sign-manifest --key <私钥种子文件> --key-id <密钥标识> \
     --input <清单文件> --output <签名后的清单文件>"
}

fn main() -> ExitCode {
    match run() {
        Ok(identity) => {
            println!("已签名清单，source_identity = {identity}");
            ExitCode::SUCCESS
        }
        Err(message) => {
            eprintln!("签名失败：{message}");
            ExitCode::FAILURE
        }
    }
}

fn run() -> Result<String, String> {
    let mut key_path: Option<PathBuf> = None;
    let mut key_id: Option<String> = None;
    let mut input: Option<PathBuf> = None;
    let mut output: Option<PathBuf> = None;

    let mut arguments = std::env::args().skip(1);
    while let Some(argument) = arguments.next() {
        let mut value = || {
            arguments
                .next()
                .ok_or_else(|| format!("参数 {argument} 缺少取值"))
        };
        match argument.as_str() {
            "--key" => key_path = Some(PathBuf::from(value()?)),
            "--key-id" => key_id = Some(value()?),
            "--input" => input = Some(PathBuf::from(value()?)),
            "--output" => output = Some(PathBuf::from(value()?)),
            "--help" | "-h" => return Err(usage().to_owned()),
            other => return Err(format!("未知参数 {other}\n{}", usage())),
        }
    }

    let key_path = key_path.ok_or_else(|| format!("缺少 --key\n{}", usage()))?;
    let key_id = key_id.ok_or_else(|| format!("缺少 --key-id\n{}", usage()))?;
    let input = input.ok_or_else(|| format!("缺少 --input\n{}", usage()))?;
    let output = output.ok_or_else(|| format!("缺少 --output\n{}", usage()))?;

    let seed_text =
        fs::read_to_string(&key_path).map_err(|error| format!("无法读取私钥文件：{error}"))?;
    let seed_text = seed_text.trim();
    let seed_bytes = base64::engine::general_purpose::STANDARD
        .decode(seed_text)
        .map_err(|_| "私钥文件不是有效的 Base64".to_owned())?;
    let seed: [u8; 32] = seed_bytes
        .try_into()
        .map_err(|_| "私钥种子长度必须是 32 字节".to_owned())?;
    let signing_key = SigningKey::from_bytes(&seed);

    let raw = fs::read(&input).map_err(|error| format!("无法读取清单：{error}"))?;
    let mut document: Value =
        serde_json::from_slice(&raw).map_err(|error| format!("清单不是有效 JSON：{error}"))?;

    // The identity is computed over the manifest without the identity and
    // the signature, exactly as the launcher computes it when verifying.
    {
        let object = document
            .as_object_mut()
            .ok_or_else(|| "清单根节点必须是对象".to_owned())?;
        object.remove("source_identity");
        object.remove("signature");
    }
    let identity_bytes =
        compute_source_identity(&serde_json::to_vec(&document).map_err(|error| error.to_string())?)
            .map_err(|error| error.to_string())?;
    let identity: String = identity_bytes
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect();
    let signature = signing_key.sign(&identity_bytes);

    {
        let object = document
            .as_object_mut()
            .ok_or_else(|| "清单根节点必须是对象".to_owned())?;
        object.insert("source_identity".to_owned(), json!(identity.clone()));
        object.insert(
            "signature".to_owned(),
            json!({
                "algorithm": "ed25519",
                "key_id": key_id,
                "value": base64::engine::general_purpose::STANDARD.encode(signature.to_bytes())
            }),
        );
    }

    let bytes = serde_json::to_vec_pretty(&document).map_err(|error| error.to_string())?;
    fs::write(&output, bytes).map_err(|error| format!("无法写入签名清单：{error}"))?;
    Ok(identity)
}
