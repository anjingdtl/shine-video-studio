from __future__ import annotations

import binascii
import hashlib
import json
import math
import struct
import zlib
from typing import Any

from fastapi import HTTPException

from shine.materialprep import qwen_workflow

QWEN_EDIT_REPOSITORY = "Comfy-Org/workflow_templates"
QWEN_EDIT_PATH = "templates/image_qwen_image_2_1_image_edit.json"
QWEN_EDIT_COMMIT = "21d7cc4fb9afd983957f2d2d6b1d583860ccb40b"
QWEN_EDIT_TEMPLATE_SHA256 = "c1e6551759de131f083a040da072ac6f032c3c8401ceca202412cd912897d0fc"
QWEN_MASK_STATUS = "PENDING_LOCAL_VALIDATION"
MAX_QWEN_EDIT_IMAGES = 10
IMAGE_MODES = {"T2I", "I2I", "INPAINT", "MULTIREF", "WARDROBE", "RGBA"}
REFERENCE_ROLES = {
    "subject",
    "face",
    "garment",
    "hair",
    "shoe",
    "bag",
    "hat",
    "prop",
    "scene",
    "style",
    "accessory",
    "canvas",
}


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def qwen_edit_adapter_hash(mode: str) -> str:
    if mode not in {"I2I", "INPAINT", "MULTIREF", "WARDROBE", "RGBA"}:
        raise ValueError("unsupported Qwen edit adapter mode")
    return canonical_hash(
        {
            "repository": QWEN_EDIT_REPOSITORY,
            "path": QWEN_EDIT_PATH,
            "commit": QWEN_EDIT_COMMIT,
            "template_sha256": QWEN_EDIT_TEMPLATE_SHA256,
            "model_revision": qwen_workflow.MODEL_REVISION,
            "adapter": "shine-qwen-edit-api-v2",
            "mode": mode,
            "input_slots": [f"images.image_{index}" for index in range(1, MAX_QWEN_EDIT_IMAGES + 1)],
            "mask_contract": (
                "image_slot_annotation_plus_post_composite_white_edit_black_preserve"
                if mode == "INPAINT"
                else None
            ),
            "output_node_id": "461",
            "api_graph_sha256": canonical_hash(
                build_qwen_edit_graph(
                    prompt="adapter fingerprint",
                    uploaded_names=["shine-adapter-fingerprint.png"],
                    width=1024,
                    height=1024,
                    seed=0,
                )
            ),
        }
    )


def validate_image_request(payload: dict[str, Any]) -> list[dict[str, Any]]:
    mode = payload.get("mode")
    if mode not in IMAGE_MODES:
        raise HTTPException(422, {"code": "IMAGE_MODE_UNSUPPORTED", "message": "不支持的图片创作方式"})
    if mode != "RGBA" and not str(payload.get("prompt") or "").strip():
        raise HTTPException(422, {"code": "IMAGE_PROMPT_REQUIRED", "message": "请说明希望生成或修改什么内容"})
    references = payload.get("references") or []
    if mode == "T2I" and references:
        raise HTTPException(422, {"code": "T2I_REFERENCES_NOT_ALLOWED", "message": "文生图不能夹带未使用的参考图"})
    if mode == "I2I" and not references:
        raise HTTPException(422, {"code": "I2I_SOURCE_REQUIRED", "message": "图生图需要一张真实原图"})
    if mode in {"MULTIREF", "WARDROBE"} and not 2 <= len(references) <= MAX_QWEN_EDIT_IMAGES:
        raise HTTPException(
            422,
            {
                "code": "REFERENCE_COUNT_OUT_OF_RANGE",
                "message": f"此工作流的输入上限为 {MAX_QWEN_EDIT_IMAGES} 张（含底图）",
            },
        )
    if mode == "INPAINT" and (not references or not payload.get("mask_media_id")):
        raise HTTPException(
            422, {"code": "INPAINT_INPUT_AND_MASK_REQUIRED", "message": "局部修订需要原图和实际遮罩文件"}
        )
    if mode == "INPAINT" and len(references) >= MAX_QWEN_EDIT_IMAGES:
        raise HTTPException(
            422,
            {
                "code": "INPAINT_TOTAL_INPUT_LIMIT",
                "message": (
                    f"局部修订会额外传入一张实际遮罩图，底图、参考图和遮罩总数不能超过 "
                    f"{MAX_QWEN_EDIT_IMAGES} 张"
                ),
            },
        )
    if mode == "RGBA" and not references and not payload.get("prompt", "").strip():
        raise HTTPException(422, {"code": "RGBA_INPUT_REQUIRED", "message": "透明背景处理需要提示词或原图"})

    normalized = []
    seen_orders = set()
    for item in references:
        if not isinstance(item, dict):
            raise HTTPException(422, {"code": "REFERENCE_MANIFEST_INVALID", "message": "参考图清单格式不正确"})
        order = item.get("order")
        media_id = item.get("media_id")
        version_id = item.get("version_id")
        role = item.get("role")
        if (
            type(order) is not int
            or order < 0
            or order in seen_orders
            or bool(media_id) == bool(version_id)
            or role not in REFERENCE_ROLES
        ):
            raise HTTPException(
                422, {"code": "REFERENCE_MANIFEST_INVALID", "message": "每张参考图都需要唯一顺序和用途"}
            )
        seen_orders.add(order)
        reference = {"order": order, "role": role}
        reference["media_id" if media_id else "version_id"] = str(media_id or version_id)
        normalized.append(reference)
    normalized.sort(key=lambda item: item["order"])
    if normalized and [item["order"] for item in normalized] != list(range(len(normalized))):
        raise HTTPException(422, {"code": "REFERENCE_ORDER_GAP", "message": "参考图序号需从 0 开始连续排列"})
    if mode in {"I2I", "INPAINT"} and normalized and normalized[0]["role"] not in {"canvas", "subject"}:
        normalized[0]["role"] = "canvas"
    if mode == "MULTIREF" and normalized and normalized[0]["role"] not in {"canvas", "subject"}:
        raise HTTPException(
            422, {"code": "MULTIREF_EDIT_TARGET_REQUIRED", "message": "多图合成的第 1 张必须是编辑底图或人物主体"}
        )
    if mode == "WARDROBE" and normalized and normalized[0]["role"] != "subject":
        raise HTTPException(
            422, {"code": "WARDROBE_SUBJECT_MUST_BE_FIRST", "message": "人物穿戴的第 1 张必须是人物底图"}
        )
    if mode == "WARDROBE" and not {"subject", "garment"}.issubset({item["role"] for item in normalized}):
        raise HTTPException(422, {"code": "WARDROBE_SLOTS_REQUIRED", "message": "穿戴合成至少需要人物底图和服装参考"})

    output = payload.get("output") or {}
    width, height = output.get("width", 1024), output.get("height", 1024)
    if type(width) is not int or type(height) is not int or not (256 <= width <= 2048 and 256 <= height <= 2048):
        raise HTTPException(422, {"code": "IMAGE_SIZE_OUT_OF_RANGE", "message": "输出尺寸需在 256 至 2048 像素之间"})
    if width % 32 or height % 32:
        raise HTTPException(422, {"code": "IMAGE_SIZE_ALIGNMENT_INVALID", "message": "尺寸需为 32 的倍数"})
    count = payload.get("candidate_count", 1)
    if type(count) is not int or not 1 <= count <= 4:
        raise HTTPException(422, {"code": "CANDIDATE_COUNT_OUT_OF_RANGE", "message": "一次最多排队 4 张候选图"})
    seed = payload.get("seed")
    if seed is not None and (type(seed) is not int or not 0 <= seed <= qwen_workflow.MAX_SEED):
        raise HTTPException(422, {"code": "IMAGE_SEED_OUT_OF_RANGE", "message": "随机种子超出支持范围"})
    return normalized


def map_qwen_reference_slots(references: list[dict[str, Any]], *, max_slots: int = MAX_QWEN_EDIT_IMAGES):
    """Compile the ordered manifest to the actual numbered Qwen edit inputs.

    The inspected official UI graph exposes images.image_1 through
    images.image_10. Its first image is the edit target/canvas. We do not
    silently drop an eleventh input or infer a different slot from role names.
    """
    if len(references) > max_slots:
        raise HTTPException(
            422, {"code": "WORKFLOW_REFERENCE_LIMIT", "message": f"当前工作流最多接收 {max_slots} 张图"}
        )
    ordered = sorted(references, key=lambda item: item["order"])
    if [item["order"] for item in ordered] != list(range(len(ordered))):
        raise HTTPException(422, {"code": "REFERENCE_ORDER_GAP", "message": "参考图顺序不连续"})
    return {f"images.image_{index + 1}": {**item, "slot": index + 1} for index, item in enumerate(ordered)}


def build_qwen_edit_graph(
    *,
    prompt: str,
    uploaded_names: list[str],
    width: int,
    height: int,
    seed: int,
    steps: int = 25,
    cfg: float = 1.0,
) -> dict[str, Any]:
    """Compile the published native Qwen edit subgraph to an API prompt graph.

    This is a registered adapter, not evidence that the target ComfyUI has the
    exact node schema or model files. Callers must validate against live
    ``object_info`` and keep the capability below READY until a real sample is
    recorded. The official node exposes autogrow image inputs; this adapter
    deliberately maps only the ten slots exposed by the audited UI template.
    """
    if not uploaded_names or len(uploaded_names) > MAX_QWEN_EDIT_IMAGES:
        raise HTTPException(422, {"code": "WORKFLOW_REFERENCE_LIMIT", "message": "Qwen 编辑图需要 1 至 10 个真实输入"})
    if not 256 <= width <= 2048 or not 256 <= height <= 2048 or width % 32 or height % 32:
        raise HTTPException(
            422, {"code": "IMAGE_SIZE_ALIGNMENT_INVALID", "message": "输出宽高需为 32 的倍数且不超过 2048"}
        )

    graph: dict[str, Any] = {
        "451": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": qwen_workflow.UNET_FILE, "weight_dtype": "default"},
        },
        "453": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": qwen_workflow.CLIP_FILE, "type": "qwen_image", "device": "default"},
        },
        "454": {"class_type": "VAELoader", "inputs": {"vae_name": qwen_workflow.VAE_FILE}},
        "469": {
            "class_type": "QwenImage21Cache",
            "inputs": {"model": ["451", 0], "device": "auto", "dtype": "default"},
        },
        "474": {
            "class_type": "TextEncodeQwenImage21",
            "inputs": {
                "clip": ["453", 0],
                "vae": ["454", 0],
                "prompt": prompt,
                "negative_prompt": "",
                # The native node budgets reference pixels by area.
                "resolution": round(math.sqrt(width * height) / 32) * 32,
                **{f"images.image_{index + 1}": [str(700 + index), 0] for index in range(len(uploaded_names))},
            },
        },
        "458": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["469", 0],
                "positive": ["474", 0],
                "negative": ["474", 1],
                "latent_image": ["474", 2],
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1.0,
            },
        },
        "457": {"class_type": "VAEDecode", "inputs": {"samples": ["458", 0], "vae": ["454", 0]}},
        "7010": {
            "class_type": "ImageScale",
            "inputs": {
                "image": ["457", 0],
                "upscale_method": "lanczos",
                "width": width,
                "height": height,
                "crop": "center",
            },
        },
        "461": {
            "class_type": "SaveImage",
            "inputs": {"images": ["7010", 0], "filename_prefix": "shine-assets-qwen-edit"},
        },
    }
    for index, uploaded in enumerate(uploaded_names):
        node_id = str(700 + index)
        graph[node_id] = {"class_type": "LoadImage", "inputs": {"image": uploaded}}
    return graph


def png_dimensions(data: bytes) -> tuple[int, int, int, int]:
    if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise HTTPException(415, {"code": "PNG_REQUIRED", "message": "遮罩必須是 PNG 圖片"})
    if data[12:16] != b"IHDR":
        raise HTTPException(415, {"code": "PNG_HEADER_INVALID", "message": "PNG 文件头无效"})
    width, height, depth, color_type, compression, filtering, interlace = struct.unpack(">IIBBBBB", data[16:29])
    if width <= 0 or height <= 0 or width > 8192 or height > 8192 or depth != 8 or color_type not in {0, 2, 4, 6}:
        raise HTTPException(
            415, {"code": "PNG_LAYOUT_UNSUPPORTED", "message": "当前画布只接受 8 位灰度、RGB 或 RGBA PNG"}
        )
    if compression != 0 or filtering != 0 or interlace != 0:
        raise HTTPException(415, {"code": "PNG_LAYOUT_UNSUPPORTED", "message": "交错或非标准 PNG 遮罩不受支持"})
    return width, height, depth, color_type


def _decode_png_rows(data: bytes) -> tuple[int, int, int, list[bytes]]:
    width, height, _, color_type = png_dimensions(data)
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    cursor, compressed = 8, bytearray()
    found_end = False
    while cursor + 12 <= len(data):
        size = struct.unpack(">I", data[cursor : cursor + 4])[0]
        kind = data[cursor + 4 : cursor + 8]
        end = cursor + 12 + size
        if end > len(data):
            raise HTTPException(415, {"code": "PNG_CHUNK_TRUNCATED", "message": "PNG 数据不完整"})
        body = data[cursor + 8 : cursor + 8 + size]
        expected_crc = struct.unpack(">I", data[cursor + 8 + size : end])[0]
        if binascii.crc32(kind + body) & 0xFFFFFFFF != expected_crc:
            raise HTTPException(415, {"code": "PNG_CRC_INVALID", "message": "PNG 校验失败"})
        if kind == b"IDAT":
            compressed.extend(body)
        if kind == b"IEND":
            found_end = True
            break
        cursor = end
    if not found_end:
        raise HTTPException(415, {"code": "PNG_IEND_MISSING", "message": "PNG 数据不完整"})
    stride = width * channels
    expected_size = height * (stride + 1)
    if expected_size > 64 * 1024 * 1024:
        raise HTTPException(413, {"code": "PNG_PIXEL_BUDGET_EXCEEDED", "message": "遮罩像素超过安全解码上限"})
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(bytes(compressed), expected_size + 1)
    except zlib.error as exc:
        raise HTTPException(415, {"code": "PNG_DECODE_FAILED", "message": "PNG 像素无法解码"}) from exc
    if len(raw) != expected_size or not decoder.eof or decoder.unconsumed_tail or decoder.unused_data:
        raise HTTPException(415, {"code": "PNG_PIXEL_LENGTH_INVALID", "message": "PNG 像素长度与尺寸不匹配"})

    rows: list[bytes] = []
    previous = bytearray(stride)
    offset = 0
    for _ in range(height):
        filter_type = raw[offset]
        encoded = raw[offset + 1 : offset + 1 + stride]
        offset += stride + 1
        current = bytearray(stride)
        for i, value in enumerate(encoded):
            left = current[i - channels] if i >= channels else 0
            up = previous[i]
            upper_left = previous[i - channels] if i >= channels else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = up
            elif filter_type == 3:
                predictor = (left + up) // 2
            elif filter_type == 4:
                p = left + up - upper_left
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - upper_left)
                predictor = left if pa <= pb and pa <= pc else up if pb <= pc else upper_left
            else:
                raise HTTPException(415, {"code": "PNG_FILTER_UNSUPPORTED", "message": "PNG 行过滤类型无效"})
            current[i] = (value + predictor) & 0xFF
        rows.append(bytes(current))
        previous = current
    return width, height, channels, rows


def validate_aligned_mask(source_width: int, source_height: int, mask: bytes) -> dict[str, Any]:
    mask_width, mask_height, channels, rows = _decode_png_rows(mask)
    if (mask_width, mask_height) != (source_width, source_height):
        raise HTTPException(409, {"code": "MASK_DIMENSIONS_MISMATCH", "message": "遮罩像素尺寸必须与原图完全相同"})
    if channels != 4:
        raise HTTPException(415, {"code": "MASK_RGBA_REQUIRED", "message": "遮罩必须是白色编辑、黑色保留的 RGBA PNG"})
    changed = retained = 0
    for row in rows:
        for index in range(0, len(row), 4):
            red, green, blue, alpha = row[index : index + 4]
            if alpha != 255 or not (red == green == blue and red in {0, 255}):
                raise HTTPException(
                    422, {"code": "MASK_SEMANTICS_INVALID", "message": "遮罩只能包含不透明黑色和白色像素"}
                )
            if red == 255:
                changed += 1
            else:
                retained += 1
    if not changed:
        raise HTTPException(422, {"code": "MASK_EMPTY", "message": "请先涂出需要修改的白色区域"})
    if not retained:
        raise HTTPException(422, {"code": "MASK_FULL_FRAME", "message": "整个画面都被选中；请改用整图编辑模式"})
    return {
        "width": mask_width,
        "height": mask_height,
        "changed_pixels": changed,
        "retained_pixels": retained,
        "semantics": "white=edit,black=preserve",
        "sha256": hashlib.sha256(mask).hexdigest(),
    }


def validate_rgba_output(data: bytes) -> dict[str, Any]:
    width, height, channels, rows = _decode_png_rows(data)
    if channels != 4:
        raise HTTPException(409, {"code": "RGBA_ALPHA_CHANNEL_MISSING", "message": "结果没有可验证的 RGBA 透明通道"})
    partial = transparent = 0
    for row in rows:
        for index in range(0, len(row), 4):
            alpha = row[index + 3]
            transparent += alpha == 0
            partial += 0 < alpha < 255
    if transparent + partial == 0:
        raise HTTPException(
            409, {"code": "RGBA_ALPHA_NOT_TRANSPARENT", "message": "PNG 含 Alpha 通道，但所有像素仍完全不透明"}
        )
    return {"width": width, "height": height, "transparent_pixels": transparent, "partial_alpha_pixels": partial}


def composite_masked_png(source: bytes, generated: bytes, mask: bytes) -> bytes:
    """Apply the actual uploaded edit mask to an RGB/RGBA source and Qwen output.

    The audited Qwen edit node has no native mask input. This adapter preserves
    original source pixels outside the white selection and adopts generated
    pixels inside it. The result remains unready until a real local sample passes.
    """
    source_width, source_height, source_channels, source_rows = _decode_png_rows(source)
    output_width, output_height, output_channels, output_rows = _decode_png_rows(generated)
    mask_width, mask_height, mask_channels, mask_rows = _decode_png_rows(mask)
    if mask_channels != 4:
        raise HTTPException(415, {"code": "MASK_RGBA_REQUIRED", "message": "遮罩必须是 RGBA PNG"})
    if (source_width, source_height) != (output_width, output_height) or (source_width, source_height) != (
        mask_width,
        mask_height,
    ):
        raise HTTPException(
            409, {"code": "INPAINT_OUTPUT_DIMENSIONS_MISMATCH", "message": "原图、遮罩和生成结果尺寸必须一致"}
        )

    def rgba_pixels(rows: list[bytes], channels: int):
        for row in rows:
            values = []
            for index in range(0, len(row), channels):
                pixel = row[index : index + channels]
                if channels == 1:
                    values.extend((pixel[0], pixel[0], pixel[0], 255))
                elif channels == 2:
                    values.extend((pixel[0], pixel[0], pixel[0], pixel[1]))
                elif channels == 3:
                    values.extend((*pixel, 255))
                else:
                    values.extend(pixel)
            yield values

    src = rgba_pixels(source_rows, source_channels)
    out = rgba_pixels(output_rows, output_channels)
    scanlines = bytearray()
    for source_row, output_row, mask_row in zip(src, out, mask_rows, strict=True):
        scanlines.append(0)
        for offset in range(0, len(mask_row), 4):
            use_generated = mask_row[offset] >= 128
            row = output_row if use_generated else source_row
            pixel_offset = offset
            scanlines.extend(row[pixel_offset : pixel_offset + 4])

    header = struct.pack(">IIBBBBB", source_width, source_height, 8, 6, 0, 0, 0)

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", binascii.crc32(kind + body) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(scanlines)))
        + chunk(b"IEND", b"")
    )


def cutout_png_by_mask(source: bytes, mask: bytes) -> bytes:
    """Create a real RGBA cutout using a user-drawn subject-preservation mask.

    White pixels retain the source alpha; black pixels become fully transparent.
    This is deterministic manual background removal, not an AI segmentation claim.
    """
    width, height, source_channels, source_rows = _decode_png_rows(source)
    mask_width, mask_height, mask_channels, mask_rows = _decode_png_rows(mask)
    if mask_channels != 4:
        raise HTTPException(415, {"code": "MASK_RGBA_REQUIRED", "message": "遮罩必须是 RGBA PNG"})
    if (width, height) != (mask_width, mask_height):
        raise HTTPException(409, {"code": "MASK_DIMENSIONS_MISMATCH", "message": "遮罩与原图像素尺寸必须完全一致"})

    def rgba_pixels(rows: list[bytes], channels: int):
        for row in rows:
            values = []
            for index in range(0, len(row), channels):
                pixel = row[index : index + channels]
                if channels == 1:
                    values.extend((pixel[0], pixel[0], pixel[0], 255))
                elif channels == 2:
                    values.extend((pixel[0], pixel[0], pixel[0], pixel[1]))
                elif channels == 3:
                    values.extend((*pixel, 255))
                else:
                    values.extend(pixel)
            yield values

    scanlines = bytearray()
    for source_row, mask_row in zip(rgba_pixels(source_rows, source_channels), mask_rows, strict=True):
        scanlines.append(0)
        for offset in range(0, len(mask_row), 4):
            red, green, blue, alpha = mask_row[offset : offset + 4]
            if alpha != 255 or not (red == green == blue and red in {0, 255}):
                raise HTTPException(
                    422, {"code": "MASK_SEMANTICS_INVALID", "message": "遮罩只能包含不透明黑色和白色像素"}
                )
            pixel = source_row[offset : offset + 4]
            scanlines.extend((*pixel[:3], pixel[3] if red == 255 else 0))

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", binascii.crc32(kind + body) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(scanlines)))
        + chunk(b"IEND", b"")
    )
