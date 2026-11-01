"""Upstream reachability for the fixed deployment materials.

A 60.8 GB download is the longest and most failure-prone step a new machine
runs. Checking the four hosts it depends on takes seconds and turns a mid-flight
failure into a named blocker the operator can act on (proxy, VPN, firewall).

Design rules:
* read-only: one one-byte GET per host, never a full artifact;
* any HTTP answer proves DNS, TCP and TLS worked; 5xx is reported as DEGRADED
  instead of pretending the upstream is healthy;
* only a transport failure (DNS, TLS, proxy, timeout) is BLOCKED;
* the client is injectable so tests never touch the network;
* results carry the host only - never a signed URL or a query string.
"""

from __future__ import annotations

from datetime import datetime

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


def probe_upstreams(*, client=None, timeout: float = 8.0) -> dict:
    """Probe every upstream the installation plan depends on, sequentially."""
    owned = client is None
    if owned:
        import httpx

        client = httpx.Client(follow_redirects=False)
    try:
        results = [_probe_one(client, spec, timeout) for spec in PROBES]
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
            "只有全部 OK 或 PARTIAL 时，才适合开始 60.8 GB 的下载。",
        ],
    }


def connectivity_blockers(report: dict) -> list[dict]:
    return [{"code": "UPSTREAM_UNREACHABLE", "host": row["host"], "error": row["error"]}
            for row in report.get("upstreams", []) if row["status"] == "BLOCKED"]
