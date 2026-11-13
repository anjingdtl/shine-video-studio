# Shine Launcher Deployment Protocol V1

The machine-readable contract is [deployment-protocol-v1.schema.json](../config/launcher/deployment-protocol-v1.schema.json). The Launcher parser and compatibility tests live in `launcher/src/protocol.rs`.

## Trust and identity

`source_identity` is SHA-256 over RFC 8785 canonical JSON after removing the top-level `source_identity` and `signature` fields. The Ed25519 detached signature signs the 32 raw digest bytes. Whitespace and object key order do not change the identity; changing any signed field does.

Publisher keys have an explicit product scope (`studio` or `launcher`). A Studio key cannot authorize a Launcher self-update. Manifest verification returns a `VerifiedDeploymentManifest`; update planning must require that type, not an unverified parse result.

The checked-in trust store is intentionally empty. Until maintainers add and verify the official public key, the Launcher rejects every remote Studio manifest and all automatic updates. The test suite uses an ephemeral test-only key that is not included in the trust store.

## Compatibility and permitted data

- Protocol major version 1 is the only supported major. A newer minimum Launcher version blocks the operation before an update plan can be built.
- `required_features` are fail-closed. Unknown `optional_features` and unknown top-level optional fields are ignored and never executed.
- `operations` is a closed enum: `probe`, `install`, `repair`, `start`, `stop`, and `upgrade`. There is no shell, script, executable, or arbitrary command field.
- Artifact URLs must use HTTPS on the GitHub host allowlist. Each artifact has a positive size ceiling and a 64-character SHA-256 digest.
- Catalog and workflow references are relative paths inside the selected immutable Studio version root. Absolute paths, drive prefixes, backslashes, empty components, `.` and `..` are rejected.
- Runtime tools and model catalogs remain governed by the Studio's pinned catalogs. This protocol does not promote upstream versions or unverified model files to READY.
- `data_policy` is fixed to `preserve-and-backup`.

## Current implementation boundary

The parser performs schema-level structural validation, compatibility handshakes, URL/path validation, source-identity hashing, and Ed25519 verification. A user-triggered GitHub release metadata scanner classifies `studio-v*` stable/Beta tags separately from `launcher-v*` tags and caches bounded API responses. Development refs are syntax-checked and resolve through the configured repository to a 40-character commit SHA.

The scanner does not download or execute assets. The checked-in release tag prefixes are a proposed convention and the remote currently has no matching releases. The repository has no production publisher key, resumable artifact downloader, install transaction, or Launcher self-update transaction. The trust store remains empty and all remote install/update paths stay disabled; L2 is not accepted until the release convention and real publisher keys are approved and Windows recovery evidence exists.
