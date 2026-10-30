"""Seal review admission and check a specific isolated-review process receipt."""

import re
import time
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from shine.comfyops.handoff_memory import evaluate_readiness, probe_memory
from shine.comfyops.installer import process_identity
from shine.comfyops.owned_process import OwnedProcessGroup
from shine.comfyops.renderer_supervisor import windows_loopback_listener_pid
from shine.quality.isolation_receipt import validate_receipt_fields


def reserve_review_service(*, base_url, runtime_sha256, supervisor, gpu_uuid, handoff_token, review_id,
                           min_ram_mb, min_vram_mb, owner_group=None, api_key=None, previous_receipt=None,
                           cold_fence_id=None):
    """Reserve one server request; uncertain results must never be retried."""
    parsed = urlsplit(base_url)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port
            or parsed.path.rstrip("/") not in {"", "/v1"} or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError("REVIEW_SUPERVISOR_URL_INVALID")
    try:
        if not isinstance(review_id, str) or str(UUID(review_id)) != review_id:
            raise ValueError
    except (ValueError, AttributeError):
        raise ValueError("REVIEW_ADMISSION_ID_INVALID") from None
    if previous_receipt is not None and cold_fence_id is not None:
        raise ValueError("REVIEW_ADMISSION_ID_INVALID")
    if cold_fence_id is not None:
        try:
            if not isinstance(cold_fence_id, str) or str(UUID(cold_fence_id)) != cold_fence_id:
                raise ValueError
        except (ValueError, AttributeError):
            raise ValueError("REVIEW_ADMISSION_ID_INVALID") from None
    if (not isinstance(handoff_token, str) or len(handoff_token) < 32 or not isinstance(supervisor, dict)
            or type(supervisor.get("pid")) is not int or not isinstance(supervisor.get("birth"), str)
            or not isinstance(runtime_sha256, str) or not re.fullmatch("[a-f0-9]{64}", runtime_sha256)
            or not isinstance(gpu_uuid, str) or not gpu_uuid):
        raise ValueError("REVIEW_SUPERVISOR_IDENTITY_INVALID")

    def check_owner():
        if (not isinstance(owner_group, OwnedProcessGroup)
                or not owner_group.owns(supervisor["pid"], supervisor["birth"])
                or windows_loopback_listener_pid(parsed.port) != supervisor["pid"]
                or process_identity(supervisor["pid"]) != supervisor["birth"]):
            raise ValueError("REVIEW_SUPERVISOR_OWNERSHIP_UNVERIFIED")

    headers = {"X-Shine-Handoff-Token": handoff_token}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    check_owner()
    with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
        endpoint = ("rearm-reserve" if previous_receipt is not None
                    else "cold-reserve" if cold_fence_id is not None else "reserve")
        body = {"review_id": review_id}
        if previous_receipt is not None:
            body["previous_receipt"] = validate_receipt_fields(previous_receipt)
        if cold_fence_id is not None:
            body["fence_id"] = cold_fence_id
        response = client.post(f"http://127.0.0.1:{parsed.port}/handoff/{endpoint}", headers=headers, json=body)
        if response.status_code != 200 or response.json() != {
            "reserved": True, "review_id": review_id, "runtime_sha256": runtime_sha256,
        }:
            raise ValueError("REVIEW_ADMISSION_UNCONFIRMED")
        health_response = client.get(f"http://127.0.0.1:{parsed.port}/health", headers=headers)
        if health_response.status_code != 200:
            raise ValueError("REVIEW_SUPERVISOR_NOT_READY")
        health = health_response.json()
        if (not isinstance(health, dict) or health.get("status") != "ok" or health.get("is_mock") is not False
                or health.get("loaded") is not False or health.get("asr_loaded") is not False
                or type(health.get("allocated_bytes")) is not int or health["allocated_bytes"] != 0
                or type(health.get("reserved_bytes")) is not int or health["reserved_bytes"] != 0
                or health.get("runtime_sha256") != runtime_sha256 or health.get("handoff_sealed") is not False):
            raise ValueError("REVIEW_SUPERVISOR_NOT_READY")
        measured = probe_memory(gpu_uuid, min_ram_mb=min_ram_mb, min_vram_mb=min_vram_mb)
        verified = evaluate_readiness(available_ram_bytes=measured.get("available_ram_bytes"),
                                      gpu=measured.get("gpu"), min_ram_mb=min_ram_mb,
                                      min_vram_mb=min_vram_mb, expected_gpu=gpu_uuid)
        if measured.get("ready") is not True or not verified["ready"]:
            raise ValueError("REVIEW_READY_MEMORY_INSUFFICIENT")
    check_owner()


def verify_review_ready(*, base_url, runtime_sha256, supervisor, gpu_uuid, min_ram_mb, min_vram_mb,
                        handoff_token, owner_group=None, api_key=None):
    """Verify the owned supervisor and physical resources before model admission."""
    parsed = urlsplit(base_url)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port
            or parsed.path.rstrip("/") not in {"", "/v1"} or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError("REVIEW_SUPERVISOR_URL_INVALID")
    if (not isinstance(runtime_sha256, str) or not re.fullmatch("[a-f0-9]{64}", runtime_sha256)
            or not isinstance(handoff_token, str) or len(handoff_token) < 32
            or not isinstance(supervisor, dict) or type(supervisor.get("pid")) is not int
            or supervisor["pid"] <= 0 or not isinstance(supervisor.get("birth"), str)
            or not supervisor["birth"].isdigit()):
        raise ValueError("REVIEW_SUPERVISOR_IDENTITY_INVALID")

    def check_owner():
        if not isinstance(owner_group, OwnedProcessGroup) or not owner_group.owns(
                supervisor["pid"], supervisor["birth"]):
            raise ValueError("REVIEW_SUPERVISOR_OWNERSHIP_UNVERIFIED")
        if (windows_loopback_listener_pid(parsed.port) != supervisor["pid"]
                or process_identity(supervisor["pid"]) != supervisor["birth"]):
            raise ValueError("REVIEW_SUPERVISOR_IDENTITY_CHANGED")

    def check_health(client):
        authorization = client.get(f"http://127.0.0.1:{parsed.port}/handoff/ready",
                                   headers={**headers, "X-Shine-Handoff-Token": handoff_token})
        if authorization.status_code != 200 or authorization.json() != {
            "ready": True, "runtime_sha256": runtime_sha256,
        }:
            raise ValueError("REVIEW_HANDOFF_AUTH_UNCONFIRMED")
        response = client.get(f"http://127.0.0.1:{parsed.port}/health", headers=headers)
        if response.status_code != 200:
            raise ValueError("REVIEW_SUPERVISOR_NOT_READY")
        health = response.json()
        if (not isinstance(health, dict) or health.get("status") != "ok" or health.get("is_mock") is not False
                or health.get("loaded") is not False or health.get("asr_loaded") is not False
                or type(health.get("allocated_bytes")) is not int or health["allocated_bytes"] != 0
                or type(health.get("reserved_bytes")) is not int or health["reserved_bytes"] != 0
                or health.get("runtime_sha256") != runtime_sha256 or health.get("handoff_sealed") is not False):
            raise ValueError("REVIEW_SUPERVISOR_NOT_READY")

    headers = {"Authorization": "Bearer " + api_key} if api_key else {}
    check_owner()
    with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
        check_health(client)
        measured = probe_memory(gpu_uuid, min_ram_mb=min_ram_mb, min_vram_mb=min_vram_mb)
        verified = evaluate_readiness(available_ram_bytes=measured.get("available_ram_bytes"),
                                      gpu=measured.get("gpu"), min_ram_mb=min_ram_mb,
                                      min_vram_mb=min_vram_mb, expected_gpu=gpu_uuid)
        if measured.get("ready") is not True or not verified["ready"]:
            raise ValueError("REVIEW_READY_MEMORY_INSUFFICIENT")
        check_health(client)
    check_owner()
    return verified


def create_cold_review_fence(*, base_url, runtime_sha256, supervisor, gpu_uuid,
                             min_ram_mb, min_vram_mb, handoff_token, api_key=None, owner_group=None):
    """Close an untouched review supervisor and bind its first reservation.

    This is the first-close primitive. It is only valid while the supervisor has
    never executed a review, so the sealed fence proves that the next review
    starts from a pristine runtime with no model resident.
    """
    parsed = urlsplit(base_url)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port
            or parsed.path.rstrip("/") not in {"", "/v1"} or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError("REVIEW_SUPERVISOR_URL_INVALID")
    if (not isinstance(runtime_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", runtime_sha256)
            or not isinstance(handoff_token, str) or len(handoff_token) < 32
            or not isinstance(supervisor, dict) or type(supervisor.get("pid")) is not int
            or supervisor["pid"] <= 0 or not isinstance(supervisor.get("birth"), str)
            or not supervisor["birth"].isdigit()):
        raise ValueError("REVIEW_SUPERVISOR_IDENTITY_INVALID")

    def check_owner():
        if not isinstance(owner_group, OwnedProcessGroup) or not owner_group.owns(
                supervisor["pid"], supervisor["birth"]):
            raise ValueError("REVIEW_SUPERVISOR_OWNERSHIP_UNVERIFIED")
        if (windows_loopback_listener_pid(parsed.port) != supervisor["pid"]
                or process_identity(supervisor["pid"]) != supervisor["birth"]):
            raise ValueError("REVIEW_SUPERVISOR_IDENTITY_CHANGED")

    headers = {"X-Shine-Handoff-Token": handoff_token}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    endpoint = f"http://127.0.0.1:{parsed.port}"
    check_owner()
    with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
        response = client.post(endpoint + "/handoff/cold-fence", headers=headers, json={})
        if response.status_code != 200:
            raise ValueError("REVIEW_COLD_FENCE_UNCONFIRMED")
        body = response.json()
        if (not isinstance(body, dict) or body.get("fenced") is not True
                or not isinstance(body.get("fence_id"), str)
                or body.get("runtime_sha256") != runtime_sha256
                or not isinstance(body.get("initial_stage"), dict)):
            raise ValueError("REVIEW_COLD_FENCE_UNCONFIRMED")
        fence_id = body["fence_id"]
    check_owner()
    proof = verify_cold_review_fence(**{
        "base_url": base_url, "fence_id": fence_id, "runtime_sha256": runtime_sha256,
        "supervisor": supervisor, "gpu_uuid": gpu_uuid, "min_ram_mb": min_ram_mb,
        "min_vram_mb": min_vram_mb, "handoff_token": handoff_token, "api_key": api_key,
        "owner_group": owner_group,
    })
    return {"fence_id": fence_id, "initial_stage": body["initial_stage"], "memory": proof["memory"]}


def verify_cold_review_fence(*, base_url, fence_id, runtime_sha256, supervisor, gpu_uuid,
                             min_ram_mb, min_vram_mb, handoff_token, api_key=None, owner_group=None,
                             timeout=60):
    """Verify a live pristine fence and the startup child's physical exit.

    This is evidence only: it neither creates a fence nor authorizes an H3
    submission. The caller must hold the durable exclusive resource intent.
    """
    parsed = urlsplit(base_url)
    try:
        valid_fence = isinstance(fence_id, str) and str(UUID(fence_id)) == fence_id
    except (ValueError, AttributeError):
        valid_fence = False
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port
            or parsed.path.rstrip("/") not in {"", "/v1"} or parsed.username or parsed.password
            or parsed.query or parsed.fragment or not valid_fence):
        raise ValueError("REVIEW_COLD_FENCE_INVALID")
    if (not isinstance(runtime_sha256, str) or not re.fullmatch("[a-f0-9]{64}", runtime_sha256)
            or not isinstance(handoff_token, str) or len(handoff_token) < 32
            or not isinstance(supervisor, dict) or type(supervisor.get("pid")) is not int
            or supervisor["pid"] <= 0 or not isinstance(supervisor.get("birth"), str)
            or not supervisor["birth"].isdigit()):
        raise ValueError("REVIEW_SUPERVISOR_IDENTITY_INVALID")

    def check_owner():
        if (not isinstance(owner_group, OwnedProcessGroup)
                or not owner_group.owns(supervisor["pid"], supervisor["birth"])
                or windows_loopback_listener_pid(parsed.port) != supervisor["pid"]
                or process_identity(supervisor["pid"]) != supervisor["birth"]):
            raise ValueError("REVIEW_SUPERVISOR_OWNERSHIP_UNVERIFIED")

    headers = {"X-Shine-Handoff-Token": handoff_token}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    endpoint = f"http://127.0.0.1:{parsed.port}"

    def check_fence(client):
        response = client.get(endpoint + "/handoff/cold-status", headers=headers)
        if response.status_code != 200:
            raise ValueError("REVIEW_COLD_FENCE_UNCONFIRMED")
        status = response.json()
        child = status.get("initial_stage") if isinstance(status, dict) else None
        if (not isinstance(status, dict) or status.get("fenced") is not True or status.get("fence_id") != fence_id
                or status.get("runtime_sha256") != runtime_sha256 or not isinstance(child, dict)
                or type(child.get("pid")) is not int or child["pid"] <= 0
                or not isinstance(child.get("birth"), str) or not child["birth"].isdigit()
                or child.get("joined") is not True or type(child.get("exitcode")) is not int
                or child["exitcode"] != 0 or type(child.get("group_active_processes")) is not int
                or child["group_active_processes"] != 0 or type(child.get("group_members_waited")) is not int
                or child["group_members_waited"] < 0 or "stage_id" in child):
            raise ValueError("REVIEW_COLD_FENCE_UNCONFIRMED")
        if process_identity(child["pid"]) == child["birth"]:
            raise ValueError("REVIEW_PROCESS_STILL_ACTIVE")
        response = client.get(endpoint + "/health", headers=headers)
        if response.status_code != 200:
            raise ValueError("REVIEW_SUPERVISOR_NOT_READY")
        health = response.json()
        if (not isinstance(health, dict) or health.get("status") != "ok" or health.get("is_mock") is not False
                or health.get("loaded") is not False or health.get("asr_loaded") is not False
                or type(health.get("allocated_bytes")) is not int or health["allocated_bytes"] != 0
                or type(health.get("reserved_bytes")) is not int or health["reserved_bytes"] != 0
                or health.get("runtime_sha256") != runtime_sha256 or health.get("last_stage") != child
                or health.get("handoff_sealed") is not True):
            raise ValueError("REVIEW_COLD_FENCE_UNCONFIRMED")
        return child

    check_owner()
    with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
        first = check_fence(client)
        # The renderer that served the review releases its GPU memory
        # asynchronously, so the baseline is measured once the release has
        # actually happened instead of on the first sample.
        deadline = time.monotonic() + timeout
        while True:
            measured = probe_memory(gpu_uuid, min_ram_mb=min_ram_mb, min_vram_mb=min_vram_mb)
            verified = evaluate_readiness(available_ram_bytes=measured.get("available_ram_bytes"),
                                          gpu=measured.get("gpu"), min_ram_mb=min_ram_mb,
                                          min_vram_mb=min_vram_mb, expected_gpu=gpu_uuid)
            if measured.get("ready") is True and verified["ready"]:
                break
            if time.monotonic() >= deadline:
                raise ValueError("REVIEW_READY_MEMORY_INSUFFICIENT")
            time.sleep(0.5)
        if check_fence(client) != first:
            raise ValueError("REVIEW_COLD_FENCE_UNCONFIRMED")
    check_owner()
    return {"fence_id": fence_id, "initial_stage": first, "memory": verified}


def verify_review_release(*, base_url, receipt, runtime_sha256, supervisor, gpu_uuid,
                          min_ram_mb, min_vram_mb, handoff_token, api_key=None, owner_group=None):
    receipt = validate_receipt_fields(receipt)
    parsed = urlsplit(base_url)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port
            or parsed.path.rstrip("/") not in {"", "/v1"} or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError("REVIEW_SUPERVISOR_URL_INVALID")
    if (not isinstance(handoff_token, str) or len(handoff_token) < 32
            or not isinstance(runtime_sha256, str) or not re.fullmatch("[a-f0-9]{64}", runtime_sha256)
            or receipt["runtime_sha256"] != runtime_sha256 or not isinstance(supervisor, dict)
            or type(supervisor.get("pid")) is not int or supervisor["pid"] <= 0
            or not isinstance(supervisor.get("birth"), str) or not supervisor["birth"].isdigit()
            or supervisor["pid"] == receipt["pid"]):
        raise ValueError("REVIEW_SUPERVISOR_IDENTITY_INVALID")
    endpoint = f"http://127.0.0.1:{parsed.port}"
    headers = {"Authorization": "Bearer " + api_key} if api_key else {}

    def check_owner():
        if not isinstance(owner_group, OwnedProcessGroup) or not owner_group.owns(
                supervisor["pid"], supervisor["birth"]):
            raise ValueError("REVIEW_SUPERVISOR_OWNERSHIP_UNVERIFIED")
        if (windows_loopback_listener_pid(parsed.port) != supervisor["pid"]
                or process_identity(supervisor["pid"]) != supervisor["birth"]):
            raise ValueError("REVIEW_SUPERVISOR_IDENTITY_CHANGED")

    def check_child():
        if process_identity(receipt["pid"]) == receipt["birth"]:
            raise ValueError("REVIEW_PROCESS_STILL_ACTIVE")

    def check_health(client):
        response = client.get(endpoint + "/health", headers=headers)
        if response.status_code != 200:
            raise ValueError("REVIEW_RELEASE_UNCONFIRMED")
        health = response.json()
        if (not isinstance(health, dict) or health.get("status") != "ok" or health.get("is_mock") is not False
                or health.get("loaded") is not False or health.get("asr_loaded") is not False
                or type(health.get("allocated_bytes")) is not int or health["allocated_bytes"] != 0
                or type(health.get("reserved_bytes")) is not int or health["reserved_bytes"] != 0
                or health.get("runtime_sha256") != runtime_sha256 or health.get("last_stage") != receipt
                or health.get("handoff_sealed") is not True):
            raise ValueError("REVIEW_RELEASE_RECEIPT_MISMATCH")

    check_owner()
    with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
        seal = client.post(endpoint + "/handoff/seal", headers={**headers, "X-Shine-Handoff-Token": handoff_token},
                           json=receipt)
        if seal.status_code != 200 or seal.json() != {
            "sealed": True, "stage_id": receipt["stage_id"], "runtime_sha256": runtime_sha256,
        }:
            raise ValueError("REVIEW_SEAL_UNCONFIRMED")
        check_owner()
        check_child()
        check_health(client)
        measured = probe_memory(gpu_uuid, min_ram_mb=min_ram_mb, min_vram_mb=min_vram_mb)
        verified = evaluate_readiness(available_ram_bytes=measured.get("available_ram_bytes"),
                                      gpu=measured.get("gpu"), min_ram_mb=min_ram_mb,
                                      min_vram_mb=min_vram_mb, expected_gpu=gpu_uuid)
        if measured.get("ready") is not True or not verified["ready"]:
            raise ValueError("REVIEW_RELEASE_MEMORY_INSUFFICIENT")
        check_health(client)
    check_owner()
    check_child()
    return {"review_stage_id": receipt["stage_id"], "supervisor": dict(supervisor),
            "review_process": {"pid": receipt["pid"], "birth": receipt["birth"]}, "memory": verified}
