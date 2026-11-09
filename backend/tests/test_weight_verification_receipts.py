"""J04: weight evidence is a receipt about bytes, never a registry summary.

The candidate catalog's real files are tens of gigabytes, so these tests use
synthetic components of a few bytes.  Everything else is the real code path:
the same receipt store, the same stat reuse, the same content hashing and the
same gate-facing ``weight_evidence``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

from shine.comfyops import verification_receipts as receipts
from shine.comfyops.models import ModelPackage
from shine.comfyops.variant_readiness import weight_evidence
from shine.models import AppSetting


def _component(payload: bytes, *, component_id: str = "synthetic-weights",
               revision: str = "rev-1", relative_path: str = "weights.bin") -> dict:
    return {
        "component_id": component_id,
        "revision": revision,
        "files": [{
            "relative_path": relative_path,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        }],
    }


def _binding(component_id: str = "synthetic-weights", variant_id: str = "candidate"):
    return SimpleNamespace(
        variant_id=variant_id,
        catalog_id="synthetic-catalog",
        catalog={"catalog_version": "1.0.0"},
        profiles={},
    )


def _write(directory: Path, relative: str, payload: bytes) -> Path:
    target = directory / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target


def _save(factory, component: dict, directory: Path, *, variant_id: str = "candidate"):
    with factory.begin() as db:
        receipts.save_receipt(
            db, variant_id=variant_id, catalog_id="synthetic-catalog",
            catalog_version="1.0.0", component=component,
            root=directory.parent, directory=directory,
        )


def test_a_matching_receipt_verifies_without_reading_the_bytes_again(api, tmp_path, monkeypatch):
    """The user's second visit reuses a real verification; no copy, no re-read."""
    _client, factory, _settings = api
    directory = tmp_path / "isolated-packages" / "synthetic-weights"
    payload = b"pinned-weights"
    _write(directory, "weights.bin", payload)
    component = _component(payload)
    _save(factory, component, directory)

    calls = []
    original = receipts.file_digest

    def spy(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(receipts, "file_digest", spy)
    with factory() as db:
        result = receipts.verify_receipt(
            db, component=component, variant_id="candidate",
            catalog_id="synthetic-catalog", catalog_version="1.0.0",
        )
    assert result["verified"] is True
    assert calls == [], "an unchanged file must not be read a second time"


def test_same_length_wrong_bytes_never_count_as_verified(api, tmp_path):
    """A byte change with the same size invalidates the receipt and the cache."""
    _client, factory, _settings = api
    directory = tmp_path / "isolated-packages" / "synthetic-weights"
    payload = b"0123456789"
    target = _write(directory, "weights.bin", payload)
    component = _component(payload)
    _save(factory, component, directory)

    target.write_bytes(b"9876543210")  # same length, different bytes
    with factory() as db:
        result = receipts.verify_receipt(
            db, component=component, variant_id="candidate",
            catalog_id="synthetic-catalog", catalog_version="1.0.0",
        )
    assert result["verified"] is False
    assert result["present"] is True
    assert result["reason"] == "BYTES_CHANGED"

    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / component["files"][0]["sha256"]).write_bytes(b"9876543210")
    cached = receipts.cache_candidates(cache, component)
    assert cached["verified"] is False
    assert cached["reason"] == "BYTES_CHANGED"


def test_a_receipt_for_another_revision_is_not_evidence(api, tmp_path):
    _client, factory, _settings = api
    directory = tmp_path / "isolated-packages" / "synthetic-weights"
    payload = b"same-bytes"
    _write(directory, "weights.bin", payload)
    _save(factory, _component(payload, revision="rev-1"), directory)

    with factory() as db:
        result = receipts.verify_receipt(
            db, component=_component(payload, revision="rev-2"),
            variant_id="candidate", catalog_id="synthetic-catalog",
            catalog_version="1.0.0",
        )
    assert result["verified"] is False
    assert result["reason"] == "RECEIPT_IDENTITY_CHANGED"


def test_a_registry_status_alone_is_not_weight_evidence(api, tmp_path, monkeypatch):
    """The old false green: a row says verified, the bytes belong to nothing."""
    _client, factory, settings = api
    component = _component(b"expected-bytes")
    with factory.begin() as db:
        db.add(ModelPackage(id="synthetic-weights", payload={"files": []},
                            sha256="0" * 64, status="FILES_VERIFIED_PENDING_RUNTIME"))

    monkeypatch.setattr(
        "shine.comfyops.variant_readiness._required_components",
        lambda catalog, profiles: [component],
    )
    with factory() as db:
        evidence = weight_evidence(db, settings, _binding("synthetic-weights"))
    row = next(item for item in evidence if item["component_id"] == "synthetic-weights")
    assert row["registry_status"] == "FILES_VERIFIED_PENDING_RUNTIME"
    assert row["registry_verified"] is False
    assert row["prepared"] is False
    assert row["verified"] is False
    assert row["receipt"]["reason"] == "NO_RECEIPT"


def test_an_existing_install_receipt_is_reusable_by_another_variant(api, tmp_path, monkeypatch):
    """Content identity, not the variant label, decides a valid reuse."""
    _client, factory, settings = api
    directory = tmp_path / "ComfyUI"
    payload = b"runtime-bytes"
    _write(directory, "custom_nodes/runtime.bin", payload)
    component = _component(payload, component_id="runtime-node",
                           relative_path="custom_nodes/runtime.bin")
    _save(factory, component, directory, variant_id="baseline")

    monkeypatch.setattr(
        "shine.comfyops.variant_readiness._required_components",
        lambda catalog, profiles: [component],
    )
    with factory() as db:
        evidence = weight_evidence(db, settings, _binding("runtime-node", variant_id="candidate"))
    row = next(item for item in evidence if item["component_id"] == "runtime-node")
    assert row["verified"] is True
    assert row["prepared"] is True


def test_no_receipt_row_is_left_for_a_partial_file_set(api, tmp_path):
    """Only a complete, verified file set produces a receipt."""
    _client, factory, _settings = api
    with factory() as db:
        assert [row for row in db.query(AppSetting).all()
                if str(row.key).startswith("verify-receipt:")] == []
    directory = tmp_path / "isolated-packages" / "synthetic-weights"
    directory.mkdir(parents=True)
    component = _component(b"missing")
    with factory() as db:
        result = receipts.verify_receipt(
            db, component=component, variant_id="candidate",
            catalog_id="synthetic-catalog", catalog_version="1.0.0",
        )
    assert result["verified"] is False
    assert result["reason"] == "NO_RECEIPT"
