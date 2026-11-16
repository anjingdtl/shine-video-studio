"""用户-facing 两步归一（文件齐不齐 / 跑不跑得通）与出图全能力验证。

锁定：能力报告把十几步工程事实归一成两行用户状态；缺文件给出数量与体积，
跑不通给出原因；I2I/INPAINT 验证用隔离测试底图+遮罩，走与素材中心同一台账。
"""

from __future__ import annotations

from shine.comfyops.image_edit_validation import _mask_png, _solid_png
from shine.comfyops.setup_summary import (
    _capability_verdict,
    _two_step_projection,
    capability_report,
)
from shine.security import probe_upload


def test_two_step_missing_files_names_count_and_bytes():
    facts = {"FILES_FOUND": False, "FILES_VERIFIED": False}
    missing = [{"component_id": "c1", "relative_path": "a", "size": 1024 ** 3}]
    proj = _two_step_projection("h3", facts, missing_bytes=1024 ** 3,
                                missing_files=missing, advisories=[])
    assert proj["files"]["state"] == "MISSING"
    assert proj["files"]["missing_count"] == 1
    assert "1 个文件" in proj["files"]["label"]
    assert proj["run"]["state"] == "BLOCKED"
    assert "文件未就绪" in proj["run"]["reason"]


def test_two_step_ready_when_verified_and_rendered():
    facts = {"FILES_FOUND": True, "FILES_VERIFIED": True,
             "INSTANCE_BOUND": True, "WORKFLOW_VALIDATED": True, "RENDER_VERIFIED": True}
    proj = _two_step_projection("image", facts, missing_bytes=0, missing_files=[], advisories=[])
    assert proj["files"]["state"] == "READY"
    assert proj["run"]["state"] == "READY"
    assert proj["run"]["label"] == "已验证可运行"


def test_two_step_run_blocked_names_the_reason():
    facts = {"FILES_FOUND": True, "FILES_VERIFIED": True, "INSTANCE_BOUND": False}
    advisories = [{"code": "VRAM_INSUFFICIENT", "message": "显存不足，需要 15360 MiB"}]
    proj = _two_step_projection("h3", facts, missing_bytes=0, missing_files=[], advisories=advisories)
    assert proj["files"]["state"] == "READY"
    assert proj["run"]["state"] == "BLOCKED"
    assert "显存不足" in proj["run"]["reason"]


def test_two_step_found_but_unverified():
    facts = {"FILES_FOUND": True, "FILES_VERIFIED": False}
    proj = _two_step_projection("h3", facts, missing_bytes=0, missing_files=[], advisories=[])
    assert proj["files"]["state"] == "UNVERIFIED"


def test_two_step_run_mounts_the_authoritative_runtime_verification():
    # 唯一检验链路：机器级运行合同已核对（顶部裁决同一个 runtime_report）时，
    # 「② 运行」必须报已验证可运行，即使本变体的项目方案逐节点核对尚未落库。
    # 修复前这里会报「跑不通」，与顶部「真实制作已就绪」自相矛盾。
    facts = {"FILES_FOUND": True, "FILES_VERIFIED": True, "INSTANCE_BOUND": True,
             "WORKFLOW_VALIDATED": False, "RUNTIME_SERVICES_VERIFIED": True}
    proj = _two_step_projection("h3", facts, missing_bytes=0, missing_files=[], advisories=[])
    assert proj["run"]["state"] == "READY"
    assert proj["run"]["label"] == "已验证可运行"


def test_two_step_candidate_does_not_borrow_baseline_verification():
    # 候选变体不是当前激活的运行配置：RUNTIME_SERVICES_VERIFIED 只在已验收基线
    # 上为真。候选自身方案未核对时仍须如实报跑不通，不能借基线核验冒充就绪。
    facts = {"FILES_FOUND": True, "FILES_VERIFIED": True, "INSTANCE_BOUND": True,
             "WORKFLOW_VALIDATED": False, "RUNTIME_SERVICES_VERIFIED": False}
    proj = _two_step_projection("h3", facts, missing_bytes=0, missing_files=[], advisories=[])
    assert proj["run"]["state"] == "BLOCKED"


def test_capability_verdict_advances_past_node_check_when_runtime_verified():
    # 同一权威信号也驱动「一句话结论 + 唯一下一步」：运行合同已核对时不再停在
    # 「核对运行合同」，而是前进到「真实试镜一次」，与两步投影保持一致。
    facts = {"FILES_FOUND": True, "FILES_VERIFIED": True, "INSTANCE_BOUND": True,
             "WORKFLOW_VALIDATED": False, "RUNTIME_SERVICES_VERIFIED": True,
             "RENDER_VERIFIED": False}
    state, _summary, step = _capability_verdict(
        "h3", facts, missing_bytes=0, has_installation=True, missing_files=[])
    assert state == "READY"
    assert step is not None and step["code"] == "TRIAL_NOT_RUN"


def test_capability_report_carries_two_step(studio):
    client, factory, _tmp, settings = studio
    with factory() as db:
        report = capability_report(db, settings, "relayvsr", installation=None, project_id=None)
    assert "two_step" in report
    assert report["two_step"]["files"]["state"] == "MISSING"
    assert report["two_step"]["run"]["state"] == "BLOCKED"


def test_validation_pngs_are_valid_and_aligned():
    base = _solid_png(64, 64, (10, 20, 30))
    mask = _mask_png(64, 64)
    base_info = probe_upload("image", base, "image/png")
    mask_info = probe_upload("image", mask, "image/png")
    assert (base_info.width, base_info.height) == (64, 64)
    assert (mask_info.width, mask_info.height) == (64, 64)
    # mask 中心白、边缘黑（white=edit, black=preserve）
    import struct
    import zlib

    def _rows(data):
        # 解析 IDAT
        pos = 8
        idat = b""
        while pos < len(data):
            length = struct.unpack(">I", data[pos:pos + 4])[0]
            kind = data[pos + 4:pos + 8]
            if kind == b"IDAT":
                idat += data[pos + 8:pos + 8 + length]
            pos += 12 + length
        raw = zlib.decompress(idat)
        stride = 64 * 4 + 1
        return [raw[y * stride + 1:(y + 1) * stride] for y in range(64)]

    rows = _rows(mask)
    center = rows[32][32 * 4:32 * 4 + 4]
    corner = rows[0][0:4]
    assert center == b"\xff\xff\xff\xff"
    assert corner == b"\x00\x00\x00\xff"