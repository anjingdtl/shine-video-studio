# Shine 四阶段镜像来源清单（MANUAL4_MIRROR_SOURCE_MANIFEST）

> 说明：本清单登记 `backend/shine/manual4/` 与 `frontend/src/manual4/` 从七阶既有实现镜像复制/移植的每个单元、来源 commit、源文件 SHA-1（`git hash-object`）、复制日期、差异理由、依赖清单与四阶独立测试。  
> **规则**：四阶不通过运行时 `import` 七阶业务模块达到“复用”；镜像代码不随七阶同步升级，任何调整需在四阶侧独立评审和回归。

| 镜像单元 | 源文件（来源 commit `9be718b`） | 源文件 Git Hash | 四阶镜像文件 | 移植日期 | 差异理由与依赖说明 | 对应四阶测试 |
|---|---|---|---|---|---|---|
| 独立 SQLite 引擎与 WAL/外键连接池 | `backend/shine/db.py` | `ee7176be424952611677248732c0ca3ddb579202` | `backend/shine/manual4/storage/db.py` | 2026-11-20 | 使用独立 `M4Base` 与 `manual4.sqlite` URL，彻底隔离七阶 `Base`/`shine.db` | `backend/tests/manual4/test_p1_platform_routing_and_isolation.py` |
| 安全文件名、路径穿越防护与媒体头校验 | `backend/shine/security.py` | `cbb6797acff596b8d0b0b7a444c7135e5a6298ba` | `backend/shine/manual4/storage/files.py` | 2026-11-20 | 文件落地严格限定于 `<data_dir>/manual4/projects/<id>/`，不读写七阶目录 | `backend/tests/manual4/test_isolation_static.py` |
| 文档解析（TXT/MD/JSON/DOCX/PDF）与长文本偏移切分 | `backend/shine/agents/script.py`、`backend/shine/materialprep/source_spans.py` | `603f5c0a8b63645f6c9378a79f4be5d8a547c78a`、`d66c755c4602f86b096c416c458fbf0657a562c8` | `backend/shine/manual4/storyboard/parser.py` | 2026-11-20 | 仅保留确定性分集/分镜/时长/实体抽取；扫描图 PDF 明确报 `M4_SCANNED_PDF_OCR_UNSUPPORTED`；不写七阶 `ProjectDocument` | `backend/tests/manual4/test_p2_storyboard.py` |
| ComfyUI 本地回环客户端、API 图哈希/校验与断连不明语义 | `backend/shine/comfy/client.py`、`backend/shine/comfy/workflow.py` | `122d9f4acea62a9ab4e6a0de6bd6f5f5c88289e1`、`7dbf753b7d25b5f0693c392653ab6baa9597593b` | `backend/shine/manual4/comfy/client.py`、`backend/shine/manual4/comfy/workflow.py` | 2026-11-20 | 仅保留四阶所需 `/system_stats`、`/object_info`、`/upload/image`、`/prompt`、`/history`、`/view` 与 `/shine/handoff/*`；断开不盲重投 | `backend/tests/manual4/test_isolation_static.py` |
| Qwen-Image-2.1 T2I 工作流图模板 | `backend/shine/materialprep/qwen_workflow.py` | `8d0ec48a0cfb18f17e1f7c53d8bba361f314b0af` | `backend/shine/manual4/comfy/image_workflow.py` | 2026-11-20 | 独立图模板构造，不写七阶 `CandidateAsset` | `backend/tests/manual4/test_p3_materials.py` |
| 受管 Job Object 启动与进程身份核对 | `backend/shine/comfyops/owned_process.py` | `379c5f102cc3940d481a8bad576912f7f44ed070` | `backend/shine/manual4/comfy/owned_process.py` | 2026-11-20 | 使用 `Local\ShineM4Handoff-*` 独立 Job 命名空间与 8198+ 独立端口，只读共享模型目录 | `backend/tests/manual4/test_isolation_static.py` |
| 本地媒体文件与字节头探测（FFprobe/FFmpeg/Python 回退） | `backend/shine/media/probe.py` | `6ca9563e5abdf6e65f47a1a47d19212c391df7f0` | `backend/shine/manual4/materials/probe.py` | 2026-11-20 | 四阶独立媒体元数据解析与安全探测 | `backend/tests/manual4/test_p3_materials.py` |
