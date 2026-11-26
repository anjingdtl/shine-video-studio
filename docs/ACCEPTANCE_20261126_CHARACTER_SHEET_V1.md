# 角色定妆照生成（16:9 五视图与视频流水线整合）工程与真机验收报告

- **日期**：2026-11-26
- **代码基线**：`main`（`ddb6b43` + 本轮改动）
- **真机环境**：NVIDIA GeForce RTX 5080（16303 MiB VRAM，Driver `621.40`）、ComfyUI `0.39.0`（`Qwen-Image-2.1` int8_convrot + `qwen-image-2.1-lora-multiple-angles`）
- **最终结论**：**通过**。本轮将整体定妆照主成品升级为无降采样的 **精确 `16:9`（`3584×2016`）五视图版式**（左列上下叠加「肩上正面照 + 3/4 侧面肩上照」，右列等高拼合「全身正面 / 侧面 / 背面三视图」），契合 H3 `MiniMaxH3ReferenceToVideo`（`ref_image_size="match"`）的等比例匹配缩放特性；同时将视频流水线 `materialprep` 的**人物素材产出**（`family="character", kind="production"`）接入「T2I 基准肖像 → 五视图 16:9 定妆照」单 GPU 租约流水线（道具、场景与小样验证保持单步 T2I 不变）。离线全量门禁与 RTX 5080 双链路端到端实测全部一次通过。

---

## 一、16:9 版式设计与 H3 参考输入契约

1. **H3 节点输入核验**（`comfy_extras/nodes_minimax_h3.py:301-308`）
   - `MiniMaxH3ReferenceToVideo` 固定以 `ref_image_size="match"` 接入参考图：仅对大于目标视频像素面积的参考图**按原长宽比等比缩小**并对齐 32 像素网格（原生横版目标为 `1344×768`，`1.75:1 ≈ 16:9`）。
2. **零降采样 `3584×2016`（16:9）定妆照版式**（[character_sheet.py](../../backend/shine/asset_center/character_sheet.py) `character-sheet-16x9-dual-closeup-triview-v2`）
   - **总画布**：`3584 × 2016`（严格 `16:9` 且宽高均为 32 倍数，`#E8EAED` 中性浅灰底色，`outer_margin=64`，`section_gap=64`，`closeup_gap=32`，`view_gap=32`，画布内零文字水印）。
   - **左列特写（`x=64..800`，共 `736w × 1888h`）**：
     - 上：`closeup_front`（肩上正面照，`736×928`，无 LoRA，`28` 步，`cfg=1.0`，原分辨率 `1:1` 落位 `x=64, y=64`）；
     - 下：`closeup_3_4`（3/4 侧面肩上照，`736×928`，`multiple-angles` `0.8`，`36` 步，`cfg=1.0`，`<mva> front-right quarter view, eye-level shot close-up` + 约 45° 侧转语义与正脸负向排斥，原分辨率 `1:1` 落位 `x=64, y=1024`）。
   - **右列全身三视图（`x=864..3520`，三槽均分 `864 × 1888`）**：
     - 依次为 `fullbody_front`（无 LoRA，`28` 步，`576×1280`）、`fullbody_side`（`multiple-angles` `0.8`，`36` 步，`<mva> right side view`）、`fullbody_back`（`multiple-angles` `0.8`，`36` 步，`<mva> back view`），每张全身照等高按原长宽比放大至 `850 × 1888`（居中贴入 `864×1888` 槽位，不裁切不拉伸、不降低原生分辨率）。
   - **独立全身三视图归档（`fullbody_triview`）**：以原生 `576×1280` 无缩放拼合为 `1872 × 1376`（`outer_margin=48, view_gap=24`）。

---

## 二、视频流水线整合与三原则收敛核对

| 原则 | 实现位置 | 验证事实 |
|---|---|---|
| **一套事实** | `style_lora_assets`、`LibraryVersion`、`MaterialItem`、`MaterialImageAttempt`、`CandidateAsset` | 素材中心与视频流水线共用统一 LoRA 台账与 `STEP_SPECS` / `compose_character_sheet()`；人物 `MaterialItem` 顺序记录 6 条 `MaterialImageAttempt`（1 次 T2I + 5 步编辑），唯一主候选 `CandidateAsset` 完整写入 `pipeline="t2i_to_character_sheet_v2"`、`t2i_seed_portrait` 及 5 步 `prompt_id`/`graph_sha256`/摘要。 |
| **一个裁决** | `evaluate_style_lora_selection()`、`resolve_live_image_loras()`、`_ensure_lora_not_in_use()` | `materialprep` 在人物出图进入 GPU 阶段 0（T2I）之前统一先校验 `qwen-image-2.1-lora-multiple-angles` 就绪状态；缺失或不可见时立即以 `CHARACTER_SHEET_LORA_REQUIRED` 阻断，不浪费 T2I 算力且不静默降级。`_strategy_references_lora()` 同步识别在途 `character_sheet_lora` 挂载，保持 `409 LORA_IN_USE` 删除保护。 |
| **一个执行者** | [asset_center/execution.py](../../backend/shine/asset_center/execution.py)、[materialprep/execution.py](../../backend/shine/materialprep/execution.py) | 两条链路均复用 `build_qwen_edit_graph()` 与 `attach_qwen_style_loras()`（`451 -> 4511 -> 469`）；人物 `MaterialItem` 在**单次 `resource_slot` 租约**内串行完成 T2I + 5 步编辑 + `3584×2016` 拼版 + 技术 QC，`UNKNOWN` 提交/超时立即停步进 `NEEDS_REVIEW`，绝不盲目重放。道具、场景及验证小样保持原单步 T2I 不变。 |
| **保护红线** | `backend/shine/worker.py`、`backend/shine/comfy/client.py`、`backend/shine/comfyops/preproduction.py` | 全程零改动（`git diff --stat` 为空），不影响 VLM 审片校准指纹。 |

---

## 三、离线全量验证结果（均于最终代码树通过）

| 检查项 | 命令与实测结果 |
|---|---|
| Python 锁文件一致性 | `uv --no-config sync --project backend --locked --dry-run` → `Would make no changes`（`pillow==13.0.0`） |
| Ruff 静态检查 | `backend\.venv\Scripts\python.exe -m ruff check backend/shine backend/tests backend/alembic/versions` → exit `0` |
| Copilot 契约校验 | `python -m scripts.generate_shine_copilot_contracts --check` → `contracts ok: app=2.2.0 actions=16 knowledge=22 discovered=482`（以及 Copilot v2/v21/v3 共 93/93 全部通过） |
| 定妆照前后端专项单测 | `pytest backend/tests/test_character_sheet.py -q` → **7/7 passed**（含 `3584×2016` 拼版、`5` 步素材中心 E2E、项目流水线 6 步 E2E、缺 LoRA 阻断、`UNKNOWN` 不重放、道具保持单步 T2I）；`vitest run src/test/characterSheet.test.tsx src/test/materialPreparation.test.tsx` → **27/27 passed** |
| 前端全量检查 | `tsc --noEmit`（exit 0）、`npx vitest run --no-file-parallelism`（**97 个文件 / 509 项测试全绿**）、`npm run build`（exit 0） |
| 后端全量 8 分片回归 | `CODEBUDDY_SAFE_DELETE_ENABLED=0 pwsh -NoProfile -File .\run_backend_shards.ps1 -WorkRoot F:\ShineTest -Shards 8 -KeepRuns 5` → **8/8 分片全部 exit 0**（覆盖 `317` 个测试文件，`shards_with_failure_markers=0`，耗时 `708.6s`，归档 `F:\ShineTest\shards-20261126-113238-418`） |

---

## 四、RTX 5080 双链路端到端真机验收实测

- **隔离环境**：`.local-acceptance/character-sheet-20261126/隔离 数据`，端口 `8318`（API）、`5378`（Web）、`8898`（Supervisor）、`8250`（ComfyUI）。

### 1. Run A：素材中心 16:9 五视图定妆照（胡金铨式武侠女主角）

- **任务 ID**：`63aec97a-5646-48eb-8686-75e30e0ac543`，耗时 **`82.47s`**，终态 `CANDIDATE_READY`。
- **落库四版本**（全部通过 `FFmpeg` 全解码与 SHA-256 核验）：
  - **主候选 `composite_sheet`（`#5`）**：`f316e776-1eaf-4135-9ce5-c62720451249`，**`3584×2016`**（严格 `16:9`，`5,166,085` B，SHA-256 `4d6ea5127cf9f013...`）
  - `closeup_front`（`#2`）：`1be1a83b-4ea3-4349-80f5-980a39b45b66`，`736×928`（SHA-256 `fef2288a43ff...`）
  - `closeup_3_4`（`#3`）：`7a2d2fe1-c7cb-4c26-9c70-a877c342b3cd`，`736×928`（SHA-256 `6309d4de6d25...`）
  - `fullbody_triview`（`#4`）：`31c5b8af-4b0a-4889-9ba0-d07ada4924df`，`1872×1376`（SHA-256 `d2c6685092f3...`）
- **5 步 ComfyUI 历史记录**：
  - `1. closeup_front`：`5f399d86...`（`736×928`）
  - `2. closeup_3_4`：`96ec9f62...`（`736×928`）
  - `3. fullbody_front`：`e2ba8941...`（`576×1280`）
  - `4. fullbody_side`：`ebeaf8c1...`（`576×1280`）
  - `5. fullbody_back`：`54a65485...`（`576×1280`）

### 2. Run B：视频流水线 `materialprep` 真机端到端（人物 T2I→定妆照 + 道具单步 T2I）

- **项目**：`43c6023c-a394-4a29-93a5-6b83705c0bf8`，批运行 `5c9c481e-3537-422c-af0f-710419a10ba4`，耗时 **`75.30s`**，终态 `COMPLETED`。
- **人物「燕三娘」**：单租约 `3e7439a3-1373-4372-8915-d3d291c3a265` 内串行执行 **6 步**（`t2i_seed_portrait` `1024×1024` + 5 步定妆照），产出主候选 `80cad990-3de7-41d7-9a15-71b583ed2d0a`（**`3584×2016`**，`16:9`，`4,877,258` B，`provenance.pipeline="t2i_to_character_sheet_v2"`），并成功通过 `grant` + `promote` 晋升为项目主资产 `0e155c03-a30c-44b6-8827-427c1a705f62`（`role="character"`）。
- **道具「竹节铜簪」**：严格保持 **1 步** T2I（`083216c7-aa52-4bc3-850d-da909c2a0297`，`1024×1024`），未受定妆照流程影响。

### 3. 证据图片与视觉目检结论

- [01-wuxia-heroine-16x9-sheet.jpg](evidence/20261126-character-sheet/01-wuxia-heroine-16x9-sheet.jpg)：胡金铨式武侠女主角 `3584×2016`（16:9）无水印主成品。
- [02-wuxia-heroine-5step-pipeline.jpg](evidence/20261126-character-sheet/02-wuxia-heroine-5step-pipeline.jpg)：原图 + 5 步输出 + 独立 `1872×1376` 三视图对比。
- [03-video-pipeline-matprep-sheet.jpg](evidence/20261126-character-sheet/03-video-pipeline-matprep-sheet.jpg)：视频流水线 `materialprep` 人物 `燕三娘`（6 步 T2I→定妆照）与道具 `竹节铜簪`（单步 T2I）。

**目检评估**：Run A 与 Run B 的左列均清晰呈现「上正面肩上 + 下 3/4 侧面肩上（约 45° 侧转）」；右列全身正/侧/背三视图高度齐整、头脚及袖摆完整无裁切、服饰腰带武裤布靴前后侧一致，画布比例精确为 `16:9` 且无文字水印。（注：Run B 因第 0 步 T2I 输出为半身至腰，其 `closeup_front` 相应保持胸腰构图，面部与发簪细节仍清晰可辨。）

---

## 五、显存释放、停机核验与未验证边界

- **显存与租约清零**：初始 `1117 MiB` → 双链路峰值 `15548 MiB`（`165` 次采样）→ 调用 `POST /setup/image-runtime/stop` 返回 `STOPPED`（`active_processes: 0`）、`11` 条 `resource_leases` 全为 `RELEASED`、`8318/5378/8898/8250` 零端口监听，停机后显存回落至 `826–1098 MiB`。
- **未验证边界**：① 本轮真机主要通过 API 端到端链路完成，未另开桌面浏览器手工点击截图；② 真机仅在 RTX 5080 `16 GB` 实测，`12 GB` 分页档与 `24 GB` 档未在本轮实机跑样；③ 视频 H3 Ref2VA 真实渲染消费该 `3584×2016` 角色主资产属于下一阶段真实出片验收范围，本轮已确认 `MiniMaxH3ReferenceToVideo` 按 `ref_image_size="match"` 可无裁剪接入 `16:9` 参考图。

---

## 六、收尾改造：独立可选能力 `character_sheet` 与视频生产一步勾选开关（2026-11-26）

- **代码基线**：`main`（`95ae2e0` + 本轮收尾改造）
- **三原则收敛核对**：
  1. **一套事实**：将 `qwen-image-2.1-lora-multiple-angles`（SHA-256 `548c57977c3f1c09c2c9e5f4e83e930cbc6f719c878a576ab8666ce29fef39b6`，`318,819,792` B）从 `config/deployment/qwen-image-loras-windows-x64.json` 拆出至独立清单 [qwen-character-sheet-windows-x64.json](../../config/deployment/qwen-character-sheet-windows-x64.json)（`catalog_id: "shine-qwen-character-sheet-windows-x64"`）；原 `qwen-image-loras-windows-x64.json` 仅保留 3 枚纯画风/画质 LoRA（`fix` + `natural-exposure` + `anything2real`，`457,564,276` B，约 `436 MiB`），与 2 套出厂画风配方严格一一对应。在 `capability.py`、`deployment.py`、`api_setup.py`、`resource_discovery.py`、`setup_summary.py`、`setup_preparation.py`、`setup_reconcile.py`、`installation_worker.py` 与 `loras.py` 中统一注册独立可选能力 `character_sheet`（纳入 `OPTIONAL_INSTALL_COMPONENTS` 与 `optional_only_plan()`，不阻塞零 LoRA 基础出图或 H3 生产门禁）。`GET /api/v1/loras?base_model=qwen_image_21` 复用 `project_character_sheet_lora_from_listed()` 输出统一实时就绪投影 `character_sheet`，前端共享开关组件 [CharacterSheetToggle.tsx](../../frontend/src/shared/CharacterSheetToggle.tsx) 仅消费该服务端投影。
  2. **一个裁决**：`api_material.py`（`/generate`、`/regenerate`、`/regenerate-all`）、`api_streamline.py`（`/director/materials/commit`）及 `materialprep/service.py::create_batch()` 新增显式开关参数 `character_sheet_mode: bool = False`。未勾选（`False`）时人物条目严格走既有单步 T2I（零 LoRA 新机器开箱可用）；勾选（`True`）时建单阶段即调用唯一裁决口 `freeze_character_sheet_lora()` 同步校验，缺失时直接返回 `409 CHARACTER_SHEET_LORA_REQUIRED`（不写 `MaterialRun`/`MaterialItem` 也不排入后台队列才失败），就绪时冻结 `character_sheet_mode=True` 与 `character_sheet_lora` 到 `MaterialItem.payload`，Worker 执行期继续通过 `resolve_live_image_loras()` 做提交前最后防线复检；`_ensure_lora_not_in_use()` 保持在途任务 `409 LORA_IN_USE` 删除保护。
  3. **一个执行者**：下载整备复用 `setup_preparation.py` / `installation_worker.py`，生成复用 `materialprep/execution.py` 与 `asset_center/execution.py`。前端 `SetupCenter.tsx` / `SetupEnvironmentStatus.tsx` 新增与「RelayVSR 2X 精修」「生图风格 LoRA 包（约 436 MiB）」并列的独立勾选项「角色五视图定妆照（约 304 MiB）」；`MaterialPreparationPanel.tsx` / `StreamlinePanels.tsx` 新增一步勾选开关「人物生成 16:9 五视图定妆照」，未整备时默认关闭且禁用并支持直达「设置 → 制作环境」下载，所有引导文案统一指向「设置 → 制作环境 → 角色五视图定妆照」。
  4. **保护红线**：`backend/shine/worker.py`、`backend/shine/comfy/client.py`、`backend/shine/comfyops/preproduction.py` 全程零改动（`git diff --name-only 95ae2e0` 核验为空）。

### 老机器权重复用核验记录

1. **回执跨清单同源兼容**：`verification_receipts.py` 为拆出的 `(shine-qwen-character-sheet-windows-x64, qwen-image-2.1-lora-multiple-angles)` 声明前序合法目录 `shine-qwen-image-loras-windows-x64`（仍严格校对 `component_identity` + 磁盘字节/头尾采样见证），`setup_reconcile.py` 与 `setup_summary.py` 同步纳入 `isolated-packages/<cid>` 与 `models/loras/shine_user/`。
2. **真实老数据目录纯只读复核**（`.local-acceptance/character-sheet-20261126/隔离 数据`）：
   - `setup_summary.capability_report(..., "character_sheet")` → `state="READY"`, `FILES_FOUND=True`, `FILES_VERIFIED=True`, `RESOURCE_REGISTERED=True`, `missing_bytes=0`；
   - `setup_preparation.plan_prepare_downloads(..., capabilities=["character_sheet"])` → `download_bytes=0`, `needs_download=False`（零重复下载）；
   - `setup_summary.capability_report(..., "image_loras")` → `state="MISSING"`, `missing_bytes=457564276`（准确反映拆分后剩余 3 枚画风 LoRA）；
   - `project_character_sheet_lora()` → `lora_ready=True`, `lora_id="qwen-image-2.1-lora-multiple-angles"`, `lora_sha256="548c5797..."`。
3. **无旧回执裸文件复核**：单测 `test_character_sheet_capability_discovery_plan_and_legacy_sha256_reuse` 覆盖裸文件经 `resource_discovery` + `setup_reconcile.reconcile_installation(..., capability="character_sheet")` 按 SHA-256 核验登记并复用（`download_bytes=0`）。

### 本轮全量门禁结果（最终代码树）

| 检查项 | 命令与实测结果 |
|---|---|
| Python 锁文件一致性 | `uv --no-config sync --project backend --locked --dry-run` → `Would make no changes` |
| Ruff 静态检查 | `backend/.venv/Scripts/python.exe -m ruff check backend/shine backend/tests backend/alembic/versions` → `All checks passed!`（exit `0`） |
| Copilot 契约与知识校验 | `python -m scripts.generate_shine_copilot_contracts --check` & `python -m scripts.generate_copilot_knowledge --check` → 均返回 `contracts ok: app=2.2.0 actions=16 knowledge=22 discovered=482` |
| 审计事件码同步检查 | `pytest backend/tests/test_audit_event_codes.py -q` → **3/3 passed** |
| 后端专项单测 | `pytest backend/tests/test_character_sheet.py backend/tests/test_image_lora_assembly.py backend/tests/test_image_vram_advice.py -q` → **30/30 passed**（`test_character_sheet.py` 10 例、`test_image_lora_assembly.py` 7 例、`test_image_vram_advice.py` 13 例） |
| 前端专项单测 | `npx vitest run src/test/setupCenter.test.tsx src/test/characterSheet.test.tsx src/test/characterSheetToggle.test.tsx src/test/materialPreparation.test.tsx src/test/setupOptionalCapabilities.test.tsx` → **5 文件 / 47 项全绿** |
| 前端全量门禁 | `npx tsc --noEmit`（exit `0`）、`npx vitest run --no-file-parallelism`（**98 个文件 / 514 项测试全绿**）、`npm run build`（exit `0`） |
| 后端全量 8 分片回归 | `CODEBUDDY_SAFE_DELETE_ENABLED=0 pwsh -NoProfile -File ./run_backend_shards.ps1 -WorkRoot 'F:\ShineTest' -Shards 8 -KeepRuns 5` → **8/8 分片全部 clean（exit `0`）**，覆盖 `317` 个测试文件，`shards_with_failure_markers=0`，总耗时 `886.5s`，日志归档 `F:\ShineTest\shards-20261126-131655-619` |

