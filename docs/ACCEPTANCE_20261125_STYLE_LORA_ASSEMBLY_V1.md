# Shine Video Studio — 风格化 LoRA 装配能力 V1 建设与验收报告

- **日期**：2026-11-25
- **工作分支**：`main`（本地直接开发，未创建独立分支，未合并方案分支 `origin/docs/style-lora-assembly-v1-20261125`）
- **参考方案**：`origin/docs/style-lora-assembly-v1-20261125:docs/plan_20261125_style_lora_assembly_v1.md`
- **最高工程纪律**：**一套事实、一个裁决、一个执行者**

---

## 一、核心交付概览

本次建设在本地 `main` 完成 Shine Video Studio 风格化 LoRA（Style LoRA）装配能力 V1，涵盖从数据库台账、自动发现与导入、唯一装配裁决、动态图编译、INT8/W6A8 变体门禁、七阶段/四阶段全链路贯通到共享前端装配面板与真机双流水线出片验证：

1. **去硬编码与自动发现**：移除 `backend/shine/comfy/graph_compiler.py` 零 LoRA 基线模型栈中硬编码的 `REALISM_LORA` 节点；启动/列表时自动发现本机 `MiniMax-H3/h3-realism-people-t2v-i2v-r2v.safetensors`，以 `id="h3-realism-people"`、`origin="discovered"` 纳管进入统一年鉴。
2. **统一资产台账与实时可见性投影**：新增数据库模型 `StyleLoraAsset`（表 `style_lora_assets`）与加法 Alembic 迁移 `f180f0010009`（`down_revision = "f180f0010008"`）；支持 SHA-256 哈希去重、安全扩展名/字节数校验、本地路径/文件上传导入至托管 `shine_user` 目录、同步生成 `comfyops/setup/shine-style-loras.yaml`，以及基于实时 ComfyUI `/object_info/LoraLoaderModelOnly` 的状态投影（`READY`、`COMFY_UNREACHABLE`、`NEEDS_COMFY_REFRESH`、`MISSING_FILE`、`HASH_MISMATCH`）。
3. **唯一裁决口与唯一触发词注入点**：
   - `evaluate_style_lora_selection()`：七阶段作业创建/执行、四阶段快照/重拍创建/执行与装配接口统一调用同一裁决入口，强制检验 0～3 槽位上限、重复 LoRA 拦截（`STYLE_LORA_DUPLICATE`）、权重范围 `[0.0, 1.5]`、ASCII 触发词长度 `<= 160`（`STYLE_LORA_INVALID_TRIGGER`）、INT8 唯一支持（W6A8 返回 `409 VARIANT_LORA_UNSUPPORTED`）以及资产可用性与实时 ComfyUI 可见性。
   - `apply_style_lora_triggers()`：统一点将已选 LoRA 非空触发词注入编译后提示词 `Style LoRA triggers:` 段，并与提示词/快照 SHA-256 一致性校验绑定。
4. **动态 0～3 风格 LoRA 图编译**：`compile_ref2va_workflow()` 支持 0～3 个 `LoraLoaderModelOnly` 动态串联（节点 ID 依次为 `"11"` → `"111"` → `"112"` → `"12" MiniMaxH3SigmaShift`）；0 LoRA 时 `"12"` 直接接 `["10", 0]`；历史归档兼容保留 `_legacy_realism_lora=True` 解析能力，INT8 配方版本提升为 `shine.graph_compiler.v3`，保持与 PDD8/Native20、二采、高清放大、`MiniMaxLowVRAMAttention` 及多模态 Ref2VA 的无损协同。
5. **共享前端组件双流水线接入**：新建 `frontend/src/shared/StyleLoraAssemblyPanel.tsx`，同时接入七阶段导演包面板（`DirectorShootPanel.tsx`）与四阶段排产页（`ScheduleProduceStagePage.tsx`），提供导入、删除（含 `409 LORA_IN_USE` 保护）、0～3 槽位排序、权重微调、触发词编辑、实时状态标识与中文错误提示/跳转映射。

---

## 二、方案适配与最小化收束说明（对照 `plan_20261125_style_lora_assembly_v1.md`）

遵循“以本地 `main` 实际代码为准，遇架构差异按三原则最小化收束”的原则，记录以下工程适配项：

| 适配点 | 处理方式 | 依据与影响 |
|---|---|---|
| **跨平台配方哈希去 OS 路径分隔符** | `graph_compiler.py` 的 `recipe_payload["style_loras"]` 仅哈希与顺序/权重/资产指纹有关的便携字段（`lora_id`、`sha256`、`strength_model`、`trigger_word`），不将含 `\` / `/` 的 `lora_name` 写入配方哈希；并在 `worker.py` 冻结图校验时对图谱节点做斜杠归一化 | 避免 Windows 反斜杠 `MiniMax-H3\...` 与 ComfyUI/Unix 正斜杠导致误报 `WORKFLOW_IDENTITY_CHANGED` |
| **托管运行时 `--extra-model-paths-config` 注入** | `managed_recipe.py` 动态追加 `--extra-model-paths-config <comfyops_dir>/setup/shine-style-loras.yaml`，不改写现有 `pins` 哈希资产 | 保护已有受管安装锁与签名基线，不破坏历史部署配置 |
| **提示词重新编译入参兼容性** | `backend/shine/h3/rulebook.py` 的 `recompile_for_provenance()` 通过 `getattr(request, "strategy", None)` 读取策略触发词 | 兼容既有单元测试中仅构造精简 `SimpleNamespace` 的 `request` 桩对象 |
| **客服/帮助台契约账本同步** | 重新生成 `copilot_contracts.json` 与 `knowledge_manifest.json`（`app=2.2.0 actions=16 knowledge=22 discovered=482`） | 保持帮助台运行时与路由/知识索引一致，使 `test_support_copilot_v21.py` 维持全绿 |
| **共享请求/选项与零 LoRA 基线守护** | 仅向请求模型与系统能力选项添加 `style_loras` 与 `lora_supported`，未选择 LoRA 时不触发任何额外权重下载、节点门禁或前置整备拦截 | 更新 `test_mode_capabilities.py` 与 `test_new_machine_setup.py`，明确零 LoRA 基线不再要求预置 LoRA 权重 |

---

## 三、三原则审查结果（一套事实、一个裁决、一个执行者）

| 维度 | 唯一事实来源 / 裁决口 / 执行者 | 审查结论 |
|---|---|---|
| **一套事实** | SQLite `style_lora_assets` 表（唯一台账）+ `project_style_lora_asset()`（实时 ComfyUI `/object_info/LoraLoaderModelOnly` 投影像）+ 作业/快照冻结后的 `style_loras` 快照字段 | 通过：七阶段、四阶段、REST `/api/v1/loras` 及共享 UI 均读取同一投影，无第二张 LoRA 状态表或前端静态配置副本 |
| **一个裁决** | `evaluate_style_lora_selection()`（装配与变体裁决）+ `apply_style_lora_triggers()`（唯一触发词注入函数） | 通过：API 创建、快照冻结、Worker 预检查、图编译器复用统一裁决与注入逻辑，无平行 if/elif 检查叠加 |
| **一个执行者** | `import_style_lora()`（导入写入）+ `compile_ref2va_workflow()`（工作流图编译）+ `StyleLoraAssemblyPanel.tsx`（前端共享装配视图） | 通过：本地路径导入与上传导入共用同一写入口与 YAMLs 同步；七阶段与四阶段共用同一图编译器及前端装配组件 |

---

## 四、变更文件清单

### 修改文件（36 个）

- **后端核心与模型/迁移相关测试**：
  - `backend/shine/api_segments.py`
  - `backend/shine/comfy/client.py`
  - `backend/shine/comfy/graph_compiler.py`
  - `backend/shine/comfyops/managed_recipe.py`
  - `backend/shine/comfyops/mode_capabilities.py`
  - `backend/shine/comfyops/models.py`
  - `backend/shine/director/package.py`
  - `backend/shine/foundation/environment.py`
  - `backend/shine/foundation/h3.py`
  - `backend/shine/h3/rulebook.py`
  - `backend/shine/main.py`
  - `backend/shine/manual4/api/production_routes.py`
  - `backend/shine/manual4/comfy/h3_adapter.py`
  - `backend/shine/manual4/production/service.py`
  - `backend/shine/manual4/worker.py`
  - `backend/shine/services.py`
  - `backend/shine/strategy.py`
  - `backend/shine/support/generated/copilot_contracts.json`
  - `backend/shine/support/knowledge_manifest.json`
  - `backend/shine/worker.py`
  - `backend/tests/fake_http.py`
  - `backend/tests/test_alembic.py`
  - `backend/tests/test_global_workflow_name_migration.py`
  - `backend/tests/test_graph_compiler.py`
  - `backend/tests/test_migrations_windows.py`
  - `backend/tests/test_mode_capabilities.py`
  - `backend/tests/test_new_machine_setup.py`
- **前端**：
  - `frontend/src/api.ts`
  - `frontend/src/auditEventCodes.json`
  - `frontend/src/components/DirectorShootPanel.tsx`
  - `frontend/src/constants.ts`
  - `frontend/src/errorActions.ts`
  - `frontend/src/manual4/api/client.ts`
  - `frontend/src/manual4/pages/ScheduleProduceStagePage.tsx`
  - `frontend/src/styles.css`
  - `frontend/src/test/studioStub.ts`

### 新增文件（7 个）

- `backend/alembic/versions/f180f0010009_style_lora_assets.py`
- `backend/shine/api_loras.py`
- `backend/shine/comfyops/loras.py`
- `backend/tests/test_style_lora_assembly.py`
- `frontend/src/shared/StyleLoraAssemblyPanel.tsx`
- `frontend/src/test/styleLoraAssembly.test.tsx`
- `docs/ACCEPTANCE_20261125_STYLE_LORA_ASSEMBLY_V1.md`（本报告）

---

## 五、自动化测试与静态检查结果

以下检查均在最终代码编辑完成后实际运行并通过（退出码 0）：

| 检查项 | 执行命令 | 实测结果 |
|---|---|---|
| **Ruff 静态检查** | `backend\.venv\Scripts\python.exe -m ruff check backend` | `All checks passed!`（exit 0） |
| **Alembic 往返迁移** | `alembic upgrade head` -> `downgrade -1` -> `upgrade head`（`f180f0010009`） | 建表/回退/重升正常，`style_lora_assets` 11 列齐备 |
| **前端类型、全量单测与生产构建** | `npx tsc --noEmit && npx vitest run --no-file-parallelism && npm.cmd run build` | **95 个测试文件 / 494 条用例全部通过**，`tsc` 与 `vite build` 清洁完成（`1.45s`） |
| **后端专项测试集** | `test_h3_compiler.py`、`test_audit_event_codes.py`、`test_new_machine_setup.py`、`test_style_lora_assembly.py` | 80/80 通过（exit 0）；另单独验证 `test_graph_compiler.py`（58/58）与 `test_support_copilot_v21.py`（41/41） |
| **后端全量 8 分片并行回归** | `$env:CODEBUDDY_SAFE_DELETE_ENABLED='0'; pwsh -File run_backend_shards.ps1 -WorkRoot F:\ShineTestWorkRoot -Shards 8` | **`shards=8 files=312`、`wall=708.9s`、`chunk-0`~`chunk-7` 全部 `exit 0`、`shards_with_failure_markers=0`**（日志目录：`F:\ShineTestWorkRoot\shards-20261125-114931-728`） |

---

## 六、RTX 5080 + ComfyUI + MiniMax-H3 双流水线真机出片验收

在完全隔离目录 `F:\ClaudeWorkSpace\projects\shine-video-studio\.local-acceptance\style-lora-v1-20261125` 与独立 ComfyUI 端口 `127.0.0.1:8195`（加载 `--extra-model-paths-config ...\comfyops\setup\shine-style-loras.yaml`）下运行真实 GPU 出片，未改动仓库根 `.env` 或宿主运行中实例。

- **真机环境与发现/导入实证**：
  - GPU：`NVIDIA GeForce RTX 5080`（`16303 MiB` 总显存）
  - 自动发现主 LoRA：`id="h3-realism-people"`，路径 `F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI\models\loras\MiniMax-H3\h3-realism-people-t2v-i2v-r2v.safetensors`，SHA-256：`acc529601d2da117fb81179e76c56e488a3beab1171659d305f04fa3655b787e`，ComfyUI 启动前 `COMFY_UNREACHABLE` -> 启动后实时投影 `READY`（`MiniMax-H3\h3-realism-people-t2v-i2v-r2v.safetensors`）。
  - 导入第二风格 LoRA：`POST /api/v1/loras/import` 导入 `minimax_h3_turbo_v4_step600_ema_pruned_comfyui.safetensors` -> `id="lora_01dcc4c73a2e"`，SHA-256：`7098acf3ee75028fd9fcd948f50fcc8d995057fabb76f86bd3ca2c0ffc58e409`，托管路径 `comfyops/models/loras/shine_user/...` 并写入 `shine-style-loras.yaml`，实时投影 `READY`（`shine_user\minimax_h3_turbo_v4_step600_ema_pruned_comfyui.safetensors`）。
  - 变体拦截实证：`POST /api/v1/projects/{pid}/jobs` 提交 `model_variant_id="ref2va-lowend-candidate"` + LoRA -> 返回 **`409 VARIANT_LORA_UNSUPPORTED`**。

- **双流水线 6 镜真机出片实测证据（全部 0.5x 分辨率 `672x384`、24fps、`pdd8`、关闭二采与高清放大、每镜 8s）**：

| 流水线 | 用例标签 | LoRA 配置与权重 | 触发词注入 | LowVRAM Attention | 实测节点12上游 | `ffmpeg -f null` / `ffprobe` | 输出大小 (B) | 输出 SHA-256（前16位） | 耗时 (s) |
|---|---|---|---|---|---|---|---|---|---|
| 7 阶段 | `7s-shot1-zero-lora` | 0 个（基线） | 否 (`false`) | `false` | `["10", 0]` | 通过 (`h264 672x384 7.998s audio=true`) | 1,155,851 | `394a2bcdd58ff4d7` | 68.48 |
| 7 阶段 | `7s-shot2-realism-040` | 1 个：`node 11` (`0.40`) | 否 (`false`) | `false` | `["11", 0]` | 通过 (`h264 672x384 7.998s audio=true`) | 1,181,257 | `4821e98ac10b3533` | 70.00 |
| 7 阶段 | `7s-shot3-realism-085-trigger` | 1 个：`node 11` (`0.85`) | 是 (`true`) | `false` | `["11", 0]` | 通过 (`h264 672x384 7.998s audio=true`) | 1,256,880 | `20f65b45e75bd393` | 89.25 |
| 4 阶段 | `4s-shot1-zero-lora` | 0 个（基线） | 否 (`false`) | `false` | `["10", 0]` | 通过 (`h264 672x384 7.998s audio=true`) | 1,005,558 | `579ea13b7dacc248` | 133.28 |
| 4 阶段 | `4s-shot2-realism-065-trigger` | 1 个：`node 11` (`0.65`) | 是 (`true`) | `false` | `["11", 0]` | 通过 (`h264 672x384 7.998s audio=true`) | 1,077,985 | `21e59ff0b99da63b` | 113.45 |
| 4 阶段 | `4s-shot3-dual-lora-lowvram` | 2 个：`node 11` (`0.55`) -> `node 111` (`0.25`) | 是 (`true`) | `true`（阈值设 `1` 实测生效） | `["111", 0]` | 通过 (`h264 672x384 7.998s audio=true`) | 1,298,943 | `997329ff35dcb9ab` | 89.20 |

- **收尾与资源释放核查**：
  - 6 镜均写入 `PASSED`，`ffmpeg -v error -i <mp4> -f null -` 零报错，`duration_s` 均为 `7.998s`，6 个输出文件 SHA-256 互不相同且已记录在 `acceptance_results.json`。
  - 零 LoRA 基线（`7s-shot1-zero-lora`、`4s-shot1-zero-lora`）执行图含 0 个 `LoraLoaderModelOnly` 节点、无触发词污染、未出现额外整备阻滞。
  - SQLite `resource_leases` 中 `status IN ('ACTIVE', 'RECONCILE_REQUIRED')` 为 `0`；独立 ComfyUI 端口 `8195` 无监听进程；`nvidia-smi` 显示 RTX 5080 显存已释放（`1471 MiB / 16303 MiB` used，`14507 MiB` free）。

---

## 七、未验证项与运行注意事项（含后续午后轮更新）

> 补充见 [ACCEPTANCE_20261125_BROWSER_AND_SHORT_DRAMA_E2E.md](ACCEPTANCE_20261125_BROWSER_AND_SHORT_DRAMA_E2E.md)：2026-11-25 午后已完成 `start_shine.ps1` 全托管栈 + Chromium 真浏览器双流水线穿测，关闭了下方第 2、4 项。

1. **VLM 评审标识失效影响（仍受本机权重缺失限制）**：本次修改了 `backend/shine/worker.py` 与 `backend/shine/comfy/client.py`，按 `client_review_identity` 约束会使历史 VLM 校准版本自动失效；经 `#d/setup` 核实本机 `qwen3-vl-8b-fp8`（0/13，缺 9.9 GiB）与 `qwen3-asr-0.6b`（缺 10 文件，1.8 GiB）均未下载，需后续安装后执行重校准。
2. **三 LoRA 真机实拍（已由午后轮关闭 ✅）**：初轮误判仅存 2 枚；午后轮核验 `minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy_resized_avg_rank_21_bf16.safetensors`（`3a069f26...`）同为有效 H3 ComfyUI LoRA 后，已在四阶段 `ep01-s03`（`job 02c91433`）完成三 LoRA（`0.45 + 0.20 + 0.15`）RTX 5080 实拍串联并通过（`7.998s`，`ffmpeg -v error` 零报错）。
3. **W6A8 真机推理组合（仍受本机权重缺失限制）**：W6A8 + LoRA 拒绝已在 API 创单、装配裁决器与图编译器层面实测拦截（`409 VARIANT_LORA_UNSUPPORTED`）；经 `#d/setup` 核实本机缺少 `ref2va-lowend-candidate` 权重文件，因此 W6A8 零 LoRA 真实 GPU 出片仍为未验证项。
4. **隔离环境预检与租约说明（已由午后轮全托管栈验证关闭 ✅）**：初轮在独立 `8195` 端口手动验证；午后轮已通过 `scripts/start_shine.ps1 -Provider comfyui` 拉起完整托管 Recipe（动态注入 `--extra-model-paths-config shine-style-loras.yaml`）、API、Web、Supervisor 与双 Worker，在浏览器无值守跑通七阶段 7 镜与四阶段 3 镜 + UI 触发词重拍，并确认停机后 `resource_leases` 零残留。
