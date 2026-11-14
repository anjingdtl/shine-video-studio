"""Upstream reachability for the fixed deployment materials.

The fixed materials are the longest and most failure-prone step a new machine
runs. Checking the four hosts it depends on takes seconds and turns a mid-flight
failure into a named blocker the operator can act on (proxy, VPN, firewall).

Design rules:
* read-only: one one-byte GET per host, never a full artifact;
* any HTTP answer proves DNS, TCP and TLS worked; 5xx is reported as DEGRADED
  instead of pretending the upstream is healthy;
* only a transport failure (DNS, TLS, proxy, timeout) is BLOCKED;
* the client is injectable so tests never touch the network;
* results carry the host only - never a signed URL or a query string.

The four-site report stays a *diagnostic* view.  Download admission is
plan-scoped (``required_sources``): a capability whose components only
download from one host is never blocked because an unrelated host is down.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlsplit

from shine.http_env import build_client

PROBES = (
    {
        "id": "huggingface",
        "label": "Hugging Face 模型库",
        "host": "huggingface.co",
        "url": "https://huggingface.co/api/models/Comfy-Org/MiniMax-H3",
        "needed_by": "56.5 GB 的 7 个模型组件",
    },
    {
        "id": "codeload",
        "label": "GitHub 源码归档",
        "host": "codeload.github.com",
        "url": "https://codeload.github.com/Comfy-Org/ComfyUI/zip/b0b743566f65daafc423b4fea8a2fbda94b3384a",
        "needed_by": "ComfyUI 与 5 个自定义节点包",
    },
    {
        "id": "pypi",
        "label": "PyPI 发行包",
        "host": "files.pythonhosted.org",
        "url": "https://files.pythonhosted.org/",
        "needed_by": "154 个固定版本 Windows 发行包",
    },
    {
        "id": "torch",
        "label": "PyTorch 官方 CUDA 索引",
        "host": "download.pytorch.org",
        "url": "https://download.pytorch.org/whl/cu128/",
        "needed_by": "torch 2.7.1+cu128 三件套",
    },
)

#: Pinned download host -> the probe that speaks for its transport family.
#: Several trusted hosts share one probe endpoint (mirrors and signed asset
#: hosts sit behind the same reachability as their canonical host); a host
#: that is trusted by the installer but has no probe vocabulary entry is
#: reported as ``NOT_PROBED`` instead of being silently skipped or invented.
HOST_PROBES: dict[str, str] = {
    "huggingface.co": "huggingface",
    "hf-mirror.com": "huggingface",
    "us.aws.cdn.hf.co": "huggingface",
    "cas-bridge.xethub.hf.co": "huggingface",
    "codeload.github.com": "codeload",
    "github.com": "codeload",
    "release-assets.githubusercontent.com": "codeload",
    "raw.githubusercontent.com": "codeload",
    "files.pythonhosted.org": "pypi",
    "download.pytorch.org": "torch",
    "download-r2.pytorch.org": "torch",
}


def probe_id_for_host(host: str | None) -> str | None:
    """The probe id that owns a download host, or None when unmapped."""
    return HOST_PROBES.get((host or "").lower())


def required_sources(catalog: dict, components: list[str]) -> list[dict]:
    """The download sources a component closure still needs, by host.

    The answer is derived from the *server owned* catalog file URLs, never
    from a request-declared host list.  Components that the request did not
    name contribute nothing: an already-verified capability's hosts must not
    block the capability that is still being prepared.

    Each row carries the probe id, every host of that family the closure
    downloads from, and the component ids that need it - the facts an
    operator needs to tell "this capability is blocked" from "unrelated
    host is down".
    """
    by_probe: dict[str, dict] = {}
    unmapped: dict[str, set[str]] = {}
    for component in catalog.get("components", []):
        component_id = component.get("component_id")
        if component_id not in components:
            continue
        for file_entry in component.get("files", []):
            host = urlsplit(str(file_entry.get("url") or "")).hostname
            if not host:
                continue
            probe_id = probe_id_for_host(host)
            if probe_id is None:
                unmapped.setdefault(host, set()).add(component_id)
                continue
            row = by_probe.setdefault(probe_id, {"probe_id": probe_id, "hosts": set(), "component_ids": set()})
            row["hosts"].add(host.lower())
            row["component_ids"].add(component_id)
    rows = []
    for probe in PROBES:
        row = by_probe.pop(probe["id"], None)
        if row is not None:
            rows.append({
                "probe_id": probe["id"],
                "label": probe["label"],
                "host": probe["host"],
                "download_hosts": sorted(row["hosts"]),
                "component_ids": sorted(row["component_ids"]),
                "needed_by": "、".join(sorted(row["component_ids"])),
            })
    # A trusted-but-unprobed host is reported honestly, never silently
    # skipped and never invented as reachable.
    for host in sorted(unmapped):
        rows.append({
            "probe_id": None,
            "label": host,
            "host": host,
            "download_hosts": [host],
            "component_ids": sorted(unmapped[host]),
            "needed_by": "、".join(sorted(unmapped[host])),
        })
    return rows


def _probe_one(client, spec, timeout):
    # Stream the request and close it immediately: a plain GET would buffer the
    # whole body, and the codeload archive alone is 13 MB - that timed out and
    # made a reachable host look blocked.
    try:
        with client.stream("GET", spec["url"], headers={"Range": "bytes=0-0"}, timeout=timeout) as response:
            status_code = response.status_code
    except Exception as exc:  # transport failures are the point of this probe
        return {
            "id": spec["id"], "label": spec["label"], "host": spec["host"],
            "status": "BLOCKED", "http_status": None, "error": type(exc).__name__,
            "needed_by": spec["needed_by"],
        }
    return {
        "id": spec["id"], "label": spec["label"], "host": spec["host"],
        "status": "OK" if status_code < 500 else "DEGRADED",
        "http_status": status_code, "error": None,
        "needed_by": spec["needed_by"],
    }


def probe_upstreams(*, client=None, timeout: float = 8.0, probe_ids: list[str] | None = None) -> dict:
    """Probe every upstream the installation plan depends on, sequentially.

    ``probe_ids`` narrows the run to a subset (plan-scoped preflight); the
    default still probes all four sites so the diagnostic view and every
    existing caller keep their exact answer.
    """
    owned = client is None
    if owned:

        client = build_client("https://huggingface.co", 8.0, follow_redirects=False)
    specs = PROBES if probe_ids is None else [spec for spec in PROBES if spec["id"] in set(probe_ids)]
    try:
        results = [_probe_one(client, spec, timeout) for spec in specs]
    finally:
        if owned:
            client.close()
    blocked = [row["id"] for row in results if row["status"] == "BLOCKED"]
    degraded = [row["id"] for row in results if row["status"] == "DEGRADED"]
    status = "OK" if not blocked and not degraded else ("BLOCKED" if len(blocked) == len(results) else "PARTIAL")
    return {
        "status": status,
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "blocked": blocked,
        "degraded": degraded,
        "upstreams": results,
        "guidance": [
            "BLOCKED 表示这台机器建立不了到该主机的 HTTPS 连接（DNS/代理/防火墙/证书）。",
            "先在本机浏览器打开对应站点；企业代理或 VPN 要在启动服务前设成系统代理。",
            "只有全部 OK 或 PARTIAL 时，才适合开始固定物料的下载。",
        ],
    }


def connectivity_blockers(report: dict) -> list[dict]:
    return [{"code": "UPSTREAM_UNREACHABLE", "host": row["host"], "error": row["error"]}
            for row in report.get("upstreams", []) if row["status"] == "BLOCKED"]


def preflight_for_components(catalog: dict, components: list[str], *, client=None, timeout: float = 8.0) -> dict:
    """Plan-scoped download admission for one component closure.

    The closure's file URLs decide which probes run: a capability that only
    downloads from one host is never blocked because an unrelated host is
    down.  Only a transport failure (BLOCKED) refuses admission - DEGRADED
    (a 5xx answer proves the transport works) and NOT_PROBED (a trusted host
    without a probe vocabulary entry) stay named facts, never silent ones.

    The caller passes the components it still needs; an already-verified
    component contributes no source and can never block this request.
    """
    sources = required_sources(catalog, components)
    probed = [row["probe_id"] for row in sources if row["probe_id"]]
    report = probe_upstreams(client=client, timeout=timeout, probe_ids=probed)
    by_id = {row["id"]: row for row in report["upstreams"]}
    required = []
    blocked = []
    degraded = []
    for source in sources:
        probe_id = source["probe_id"]
        if probe_id is None:
            required.append({**source, "status": "NOT_PROBED", "http_status": None, "error": None,
                             "retryable": False, "reason_code": "SOURCE_NOT_PROBED"})
            continue
        probed_row = by_id.get(probe_id, {})
        status = probed_row.get("status", "OK")
        if status == "BLOCKED":
            blocked.append(probe_id)
        elif status == "DEGRADED":
            degraded.append(probe_id)
        required.append({
            **source,
            "status": status,
            "http_status": probed_row.get("http_status"),
            "error": probed_row.get("error"),
            "retryable": status != "OK",
            "reason_code": "UPSTREAM_UNREACHABLE" if status == "BLOCKED"
            else ("UPSTREAM_DEGRADED" if status == "DEGRADED" else None),
        })
    return {
        "status": "BLOCKED" if blocked else ("PARTIAL" if degraded or any(
            row["status"] == "NOT_PROBED" for row in required) else "OK"),
        "checked_at": report["checked_at"],
        "blocked": blocked,
        "degraded": degraded,
        "required": required,
        "guidance": [
            "BLOCKED 只表示这台机器建立不了到该来源的 HTTPS 连接（DNS/代理/防火墙/证书）。",
            "一次探测失败不是永久不可下载：先在本机浏览器打开对应站点；企业代理或 VPN 要在启动服务前设成系统代理。",
            "这里只检查本次要下载的来源；与这次准备无关的站点失联不影响本次计划。",
        ],
    }
