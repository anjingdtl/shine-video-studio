import hashlib
import zipfile
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from shine.comfyops.review_runtime import WHEEL_NAMES, prepare_review_runtime


def _catalog(tmp_path, *, unsafe=False):
    root = tmp_path / "isolated-packages" / "comfy-runtime-wheels"
    root.mkdir(parents=True)
    files = []
    for index, name in enumerate(WHEEL_NAMES):
        wheel = root / f"{name}-1-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("../escape.py" if unsafe and index == 0 else f"{name}/module.py", name)
        payload = wheel.read_bytes()
        files.append({"relative_path": wheel.name, "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
    return {"components": [{"component_id": "comfy-runtime-wheels", "files": files}]}


def test_overlay_is_extracted_from_verified_wheels_and_never_overwrites(tmp_path):
    catalog = _catalog(tmp_path)
    settings = SimpleNamespace(comfyops_dir=tmp_path)
    assert prepare_review_runtime(settings, catalog) == "PREPARED_FROM_VERIFIED_WHEELS"
    target = tmp_path / "setup" / "review-runtime"
    assert (target / "interegular" / "module.py").read_text() == "interegular"
    (target / "user-file").write_text("keep")
    assert prepare_review_runtime(settings, catalog) == "EXISTING_UNCHANGED"
    assert (target / "user-file").read_text() == "keep"


def test_hash_mismatch_and_path_traversal_leave_no_overlay(tmp_path):
    settings = SimpleNamespace(comfyops_dir=tmp_path)
    catalog = _catalog(tmp_path, unsafe=True)
    with pytest.raises(HTTPException, match="REVIEW_WHEEL_CONTENT_INVALID"):
        prepare_review_runtime(settings, catalog)
    assert not (tmp_path / "escape.py").exists()
    assert not (tmp_path / "setup" / "review-runtime").exists()
    catalog["components"][0]["files"][0]["sha256"] = "0" * 64
    with pytest.raises(HTTPException, match="REVIEW_WHEEL_HASH_MISMATCH"):
        prepare_review_runtime(settings, catalog)


def test_definite_review_import_failure_does_not_wait_for_the_probe_timeout(monkeypatch):
    from shine.comfyops import service_validation

    calls = []

    def failure(*args, **kwargs):
        calls.append(1)
        raise HTTPException(409, "REVIEW_EXECUTOR_IMPORT_FAILED:ImportError")

    monkeypatch.setattr(service_validation, "_json_get", failure)
    with pytest.raises(HTTPException, match="REVIEW_EXECUTOR_IMPORT_FAILED"):
        service_validation._wait_json("http://127.0.0.1", timeout_s=300, predicate=lambda _: True)
    assert len(calls) == 1
