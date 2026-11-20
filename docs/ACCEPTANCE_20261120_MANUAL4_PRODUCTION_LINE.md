# Shine 四阶 H3 自主制片生产线（`manual4`）建设与验收报告

- **日期**：2026-11-20
- **开发分支**：`main`（基线 `9be718b`，无新建开发分支或 worktree）
- **开发与真机验收硬件**：Windows 11 · NVIDIA GeForce RTX 5080（16303 MiB VRAM）

---

## 一、建设阶段与 Git 提交记录

| 阶段 | 提交 SHA | 核心交付内容 |
|---|---|---|
| **P0** | `4eabfb4` | 检出建设方案文档 `docs/SHINE_MANUAL4_ISOLATED_FOUR_STAGE_PRODUCTION_LINE_V1_20261120.md`，冻结基线，建立 `docs/MANUAL4_BASELINE_AND_ISOLATION_GUARD.md` 与 `docs/MANUAL4_MIRROR_SOURCE_MANIFEST.md` |
| **P1** | `64c41c4` | 平台 `project_workflows`（Alembic `f180f0010008`）不可变选线、跨线 409 防护（`PROJECT_IS_MANUAL4_NOT_DIRECTOR7`）、平台 GPU Windows 互斥锁 + `resource_leases` 协调（`platform_gpu.py`）、独立四阶数据库 `manual4.sqlite` 与唯一裁决 `evaluate_m4_readiness`、四阶独立 React 入口 |
| **P2** | `bccb5ef` | 镜像确定性文档解析（TXT/MD/JSON/DOCX/PDF，拒绝扫描图 PDF `M4_SCANNED_PDF_OCR_UNSUPPORTED`）、长文本保序分块不编造时长、草稿乐观锁、原子冻结与集时长偏差保护、需求自动归并、① 人工分镜书交互页 |
| **P3** | `8a45026` | 独立四阶 ComfyUI 受管启停（`Local\ShineM4Handoff-*` Job Object、8198+ 独立端口、`<data_dir>/manual4/comfy-runtime/`）、Qwen-Image-2.1 镜像、上传/生成候选→人工晋升授权→Ref2VA 绑定槽位限制与 `user_declined` 持久保留、② 素材生成交互页 |
| **P4** | `9ecd993` | 人工 H3 提示词冻结与非阻塞格式检查（`M4ManualPromptRevision`；变更失效报 `409 M4_MANUAL_PROMPT_INVALIDATED` 严禁回退模板）、H3 Ref2VA 动态执行图编译与 `last_frame` FFmpeg 尾帧抽取、独立四阶 Worker（`python -m shine.manual4.worker`，单 Batch 序列持锁连续跑完、提交意图前置持久化与断连 `NEEDS_REVIEW` 对账）、`start_shine.ps1` 同步拉起 `worker-m4`、③ 排片生产交互页 |
| **P5** | `9e6e4f6` | 镜像 FFmpeg 标准化拼接与外挂 SRT 字幕生成、SHA-256 绑定人工观看验收（严禁接受 Mock 演示件）、④ 产出成片交互页 |
| **真机修复与合同同步** | `1b4ed9b`, `6dc1d6f` | RTX 5080 真机联调修复（`/shine-handoff/*` Bearer 鉴权、`QwenImage21Cache` 与 `TextEncodeQwenImage21` 真实节点契约、`object_info` 严格校验）与构建期平台合同清单（`copilot_contracts.json`、`knowledge_manifest.json`）同步 |

---

## 二、双线隔离核验报告

1. **七阶业务代码零侵入**
   - 核验 `git diff 9be718b..HEAD --stat`：七阶业务目录（`director/`, `director_styles/`, `literary/`, `materialprep/`, `production/`, `media/`, `asset_center/`, `agents/`, `voiceops/`, `quality/`, `h3/`, `comfy/`, `comfyops/`, `copilot/`, `services.py`, `worker.py`, `api_*.py` 及七阶前端面板）修改行数为 **0**。
   - 仅修改 P0 白名单允许的平台外壳：`models.py` + Alembic `f180f0010008`、`main.py`、`platform_projects.py`、`platform_gpu.py`、`scripts/start_shine.ps1`、`support/generated/copilot_contracts.json`、`support/knowledge_manifest.json` 与前端顶层选线壳。
2. **静态 AST 与前端隔离测试**
   - `backend/tests/manual4/test_isolation_static.py` 通过：禁止 `backend/shine/manual4/` 导入任何七阶业务模块；禁止 `frontend/src/manual4/` 调用任何 `/api/v1/projects/`、`/director/` 或导入七阶组件。
3. **运行时数据库与文件隔离**
   - `D:\Shine\data\shine.db` 原有 **15** 个七阶用户项目经核对完全未受影响（无新增或篡改记录）。
   - 所有四阶测试与端到端穿测中，七阶 `assets` / `candidate_assets` / `shots` / `jobs` / `job_attempts` / `media_artifacts` 表写入计数均为 **0**。
4. **跨线 GPU 与 ComfyUI 隔离**
   - `platform_gpu.py` 通过 OS 级命名互斥锁 `Global\ShinePlatformGpuArbitrator-*` 与 `resource_leases` 协调；`test_p4_worker_crash_reconciliation_and_cross_line_gpu_lease_exclusion` 验证当 `d7:` 持有活跃租约时，`m4:` 立即返回 `409 GPU_BUSY_OTHER_LINE` 且不强制中断七阶进程。
   - 四阶 ComfyUI 固定绑定独立回环端口（8198+）、独立运行时目录 `<m4_root>/comfy-runtime/` 与独立 Job Object `Local\ShineM4Handoff-*`，仅以只读方式共享机台 `models/` 权重目录。

---

## 三、全量回归测试结果

| 测试范围 | 执行命令 / 脚本 | 结果 | 日志 / 备注 |
|---|---|---|---|
| **后端全量（七阶 + 四阶）8 分片并行** | `$env:CODEBUDDY_SAFE_DELETE_ENABLED="0"; .\run_backend_shards.ps1 -WorkRoot F:\ShineTest\manual4-shards` | **8 / 8 分片全部 Clean（exit 0，286 文件，0 失败）** | `F:\ShineTest\manual4-shards\shards-20261120-153139-911` |
| **四阶专项套件** | `pytest backend/tests/manual4/ -q` | **14 / 14 passed** | 覆盖 P1 路由/选线、P2 分镜、P3 素材、P4 排片/Worker/GPU、P5 装配/验收、静态 AST 隔离 |
| **Copilot 路由合同与知识清单** | `pytest backend/tests/test_support_copilot_v21.py backend/tests/test_support_copilot_v3.py -q` | **68 / 68 passed** | `test_support_copilot_v21` (41) + `test_support_copilot_v3` (27) |
| **后端静态 Lint** | `ruff check backend/shine` | **All checks passed!** | 零警告零报错 |
| **前端类型与生产构建** | `cd frontend && npm.cmd run build` | **通过（1658 模块）** | `tsc -b && vite build` |
| **前端全量 Vitest** | `cd frontend && npx.cmd vitest run --no-file-parallelism` | **88 / 88 文件、452 / 452 用例 passed** | 含 `src/test/manual4Routing.test.tsx` |
| **打包入口与 PS1 编码检查** | `pytest backend/tests/test_package_entrypoints.py -q` | **16 / 16 passed** | 校验 `scripts/start_shine.ps1` 改动未破坏编码/入口规范 |

---

## 四、本机 RTX 5080 真实 MiniMax-H3 端到端穿测实录

- **隔离测试目录**：`F:\ShineTest\manual4-real-e2e`（未动 `D:\Shine\data\`）
- **项目 ID**：`0fb2cc79-7b43-4b11-93ab-1e7ac5f886b9`
- **Stage 1（人工分镜书）**：导入 1 集 2 镜剧本（每镜 4s，总 8s）并原子确认冻结。
- **Stage 2（Qwen-Image-2.1 真实生成 + 人工晋升）**：
  - 人物参考「林晚」（768×768 PNG，`is_mock=false`）：SHA-256 `e52bd50abbeaa1a2ceb44421f6bb291dbb4ae66c707999945438f937eaeaffae`
  - 场景参考「雨夜青石巷」（768×512 PNG，`is_mock=false`）：SHA-256 `f28f06bc47b9719c7d1f4dd854bbada658d7f83a734d6254507a45365582f2a7`
- **Stage 3（MiniMax-H3 Ref2VA 真机双镜连续出片）**：
  - 批次 Run `e053f8e0-799f-4ff4-91ff-9b703dc17b42`（`provider=comfyui`, `variant=h3-ref2va-int8-convrot`, `accelerator=pdd8`, `resolution_scale=0.5`），状态 **`COMPLETED`**。
  - 第 1 镜（`cut`）：`PASSED`，实测 `4.458s`，`672×384`，`24.01 fps`，`612,611` 字节，SHA-256 `07fa448542e514b810ebe6823636dc324b9de938822b32216648a09e65886b00`。
  - 第 2 镜（`last_frame`，从第 1 镜真实 MP4 尾帧由 FFmpeg 抽取并作为 `<Picture>` 参考图继续生成）：`PASSED`，实测 `4.458s`，`672×384`，`24.01 fps`，`449,843` 字节，SHA-256 `6f49915dbdd7d731ee7378be502c36c7d5c6683ac506ede10796534706fdcf10`。
- **Stage 4（FFmpeg 真实拼接成片与 SHA 绑定验收）**：
  - 成片输出：`F:\ShineTest\manual4-real-e2e\manual4\projects\0fb2cc79-7b43-4b11-93ab-1e7ac5f886b9\deliveries\d8\d80c44f4772a31d046ed833322e207dfbedae64ad710ef0eaec7990b90bbb2a0.mp4`
  - FFprobe 实测：`duration_s=8.937667s`，`672×384`，`24.0 fps`，`has_video=True`，`has_audio=True`，`decoded=True`，`1,062,654` 字节，`is_mock=false`。
  - 人工验收记录：`decision=accepted`，`watched=true`，`delivery_sha256=d80c44f4772a31d046ed833322e207dfbedae64ad710ef0eaec7990b90bbb2a0`。
- **资源清理核对**：`F:\ShineTest\manual4-real-e2e\shine.db` 中 2 条 `m4:*` GPU 租约均已正常 `RELEASED`（`active_leases=0`），七阶 `assets`/`shots`/`jobs`/`media_artifacts` 全为 `0`。

---

## 五、已明确标注的硬件边界与遗留限制

1. **硬件与分辨率范围**：本机实测硬件为单卡 **RTX 5080（16 GiB VRAM）**，`pdd8` + `0.5` 分辨率档位（672×384）双镜连续运行通过；4070 / 12GB 显卡低显存量化（`ref2va-nvfp4-lowend`）及 `turbo4` 加速配方因缺少对应硬件/真机验收证据，当前在 `h3_adapter.py` 中明确拒绝编译。
2. **原生视频参考节点（`LoadVideo`）**：当前仅开放 `last_frame`（经 FFmpeg 提取前镜尾帧 PNG 进入 H3 `ref_images`）；未经过 live `object_info` 合约校验的原始视频直传槽位保持显式阻滞（`M4_VIDEO_REFERENCE_UNVERIFIED`），未做静默放行。
3. **扫描件 PDF OCR**：Stage 1 导入明确拒绝无提取文本的扫描图 PDF（`422 M4_SCANNED_PDF_OCR_UNSUPPORTED`），需由本地先转可提取文本/DOCX/TXT。
4. **单机本地信任边界**：沿用平台个人桌面本地回环（Loopback + `x-shine-local-action`）模式，未实现多用户远程身份鉴权。
