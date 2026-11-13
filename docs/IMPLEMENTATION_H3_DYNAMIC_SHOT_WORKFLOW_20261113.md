# H3 动态逐镜工作流与统一调度融合 V1.0 —— 实施报告

- 日期：2026-11-13
- 分支：`feature/h3-dynamic-shot-workflow-20261113`（独立 worktree `F:\ClaudeWorkSpace\projects\shine-h3-dynamic-20261113`）
- 基线：origin/main `1a55c41509924b898a587901b0aa5aa5ac71cc70`（方案分支 `97e6a145…` 基于同一提交）
- 方案：`docs/SHINE_H3_DYNAMIC_SHOT_WORKFLOW_UNIFIED_ORCHESTRATOR_V1_20261113.md`

## 1. 总览

本轮按方案 WP0–WP8 完整实施"用户选模型与生产策略，系统按每一镜自动编译执行图"的架构改造，并保持
**单一统一 ComfyUI 调度层**（Run/Series/Job/Attempt/Worker/ResourceLease 全部复用，无第二调度器、
第二队列或第二状态机）：

| 工作包 | 内容 | 状态 |
|---|---|---|
| WP0 | 基线审计（六路并行审计）、独立 worktree、真机事实核对 | 完成 |
| WP1 | 模式级能力登记，消除四条固定 Profile 字节级全局阻断 | 完成（ENGINEERING_PASS + 既有测试） |
| WP2 | 生产策略合同贯穿（包/Run/Series/Job/Worker） | 完成（ENGINEERING_PASS） |
| WP3 | 逐镜动态图编译器（N 图 N 音、确定性哈希） | 完成（ENGINEERING_PASS + 真实节点合同验证） |
| WP4 | 真二次采样（第二调度器 + 3D refine handoff） | 完成（ENGINEERING_PASS + 真机渲染验证） |
| WP5 | RelayVSR X2 接入（能力登记/后处理执行链/2× 合同） | 代码完成；真机 BLOCKED（如实记录，见 §6） |
| WP6 | 统一调度层融合（resource_slot 接入、集交付等待、EDL 优先产物） | 完成（ENGINEERING_PASS） |
| WP7 | 前端策略选择器、逐镜阶段进度、移除 Profile 下拉 | 完成 |
| WP8 | 八分片回归 / 前端全量 / Ruff / Alembic / 真机验证 / 报告 | 完成（见 §7–§9） |

## 2. 核心设计决策

### 2.1 模式级能力取代字节级启动契约（WP1）

- 新模块 `backend/shine/comfyops/mode_capabilities.py`：`core_mode_node_types()` 从默认目录的已审计
  模板派生"基线 Ref2VA 模式必需节点集"；`classify_baseline_profiles()` 把模板分为
  contracts/drifted/missing，**drifted/missing 一律 advisory**。
- `service_validation._workflow_contracts`：不再 409 `WORKFLOW_PROFILE_CONTENT_CHANGED`；
  新的硬门是 `_validate_core_mode_nodes`（`CORE_MODE_NODES_MISSING`）。缺失的基线模板由
  `auto_register_missing_baseline_profiles` 从仓库模板幂等补登记（不覆盖漂移行）。
- `runtime_configuration`：服务证据升级 **v2 mode-capability seal**（`mode_capabilities.required_nodes`
  + 模板 advisory）；v1 历史证据可读、漂移降级 advisory、不伪造 v2 READY；核心集变化 →
  `MODE_CAPABILITY_CONTRACT_CHANGED`。
- `handoff_startup.required_workflow_node_types`：启动门槛收窄为**基线模式 Profile 的节点集**
  （候选变体/项目宽图不再成为每次渲染器启动的硬门槛；它们在使用时由逐镜 live 校验把关）。
- 顺带修复 `variant_binding.resolve_registration_target` catalog-only 路径 stem vs `shine-` 前缀
  恒不等的缺陷（`VARIANT_CATALOG_MISMATCH` 假阳性）。

### 2.2 生产策略合同（WP2）

- `backend/shine/strategy.py`：`GenerationStrategy`（extra=forbid）：`model_variant_id` /
  `accelerator_id` / `second_pass{enabled,recipe_id,denoise,latent_scale}` /
  `upscale{enabled,engine,variant}`。
- 可用性全部证据派生：变体来自 `variants/*.json` 的 `acceptance_status`（CANDIDATE →
  `MODEL_VARIANT_NOT_ACCEPTED`，绝不静默换 INT8）；加速器来自编译器配方表（turbo4 声明在列但
  `ACCELERATOR_UNVERIFIED` 拒编译）；RelayVSR 来自能力记录（未就绪 → `UPSCALE_BACKEND_UNAVAILABLE`）。
- 贯穿链：`ShootBody.strategy` → `streamline.start_director_production`（策略哈希进入包复用判定：
  **同 scope 不同策略绝不复用旧包**）→ `build_package(strategy=…)`（binding 冻结
  `strategy`+`strategy_sha256`，参与 manifest_sha256）→ `package_readiness`（策略重验 + 逐镜动态编译
  预检）→ `runner._create_series_for_batch`（派发时二次门禁）→ `ProductionSeries.generation_strategy`
  （新列）→ 每镜 `CreateJob.strategy` → `RenderRequest.strategy` → Worker 动态编译。
- 批次级拒绝码进 `BATCH_LEVEL_CODES`（STRATEGY_BLOCKED/INVALID/PROFILE_EXCLUSIVE）。

### 2.3 逐镜动态图编译器（WP3/WP4）

- `backend/shine/comfy/graph_compiler.py`：`compile_ref2va_workflow(variant, accelerator,
  second_pass, image_count, audio_count, video_count, object_info=None)`。
  蓝图直接派生自机器验证过的 PDD8 图与 variants 清单：模型链按变体 checkpoint（W6A8 用自己声明的
  权重文件），采样链按加速配方（pdd8 挂 PDDAccApply；native20 直连 BasicScheduler 20 步），
  参考 loader 按 manifest 顺序编号（`reference_image_N`/`reference_audio_N` 位置序列，与既有
  slot resolver 兼容），31 号节点的 autogrow 成员键逐一连线。
- **真二次采样**：第二段独立调度（61 `MiniMaxH3PDDAccScheduler(denoise)`）+ 第二模型补丁
  （62 `MiniMaxH3PDDAccApply`）+ `MinimaxH3LatentUpscaler3DRefineHandoff(latent=[53 的透传],
  noise/sampler/positive 复用一段条件)`；打包音轨用**一段**的 `VAEDecodeAudio`（二采绝不重合成声音）；
  `latent_scale=1.0` 为纯尾部精修（尺寸不变），1.5 为 latent 放大精修。denoise∈(0,1]、
  scale∈[1,2] 越界具名拒绝。
- 官方上限硬门：图≤9、音≤3、视≤3、总≤12；**视频参考当前具名拒绝**
  （`VIDEO_REFERENCE_LOADER_UNVERIFIED`）——本安装没有已核验的 ref_videos 装载配方，按方案
  "绝不自造节点名"的纪律，待真机 object_info 证据后开放。
- `CompiledWorkflow` 与 `WorkflowProfile` 鸭子类型兼容（graph/parameter_map/media_transfers/
  required_nodes/validated_against），`ComfyUIRenderService.prepare()` 零改动；`execution_identity()`
  提供逐 attempt 冻结快照（compiler 版本、variant、accelerator、二采配方哈希、参考计数、图哈希）。

### 2.4 Worker 动态编译接线（WP2）

- `_run_comfy_job`：`request.strategy` 存在且无 profile → 在**写事务之外**拉一次 live
  `object_info` 并编译（同一确定性输入 → 与请求时记录的图哈希一致）；live 校验缺节点 →
  `STRATEGY_GRAPH_NODE_MISSING`；编译拒绝 → `STRATEGY_COMPILE_<code>`；审计
  `STRATEGY_GRAPH_COMPILED` 携带执行身份。

### 2.5 RelayVSR X2（WP5/WP6）

- 上游事实（已核实，非猜测）：core 合并于 `2f894332`（PR #17511，2026-11-05）；官方节点
  `RelayVSRConditioning(model, images)→(positive, latent)` 与 `VSRDecode(vae, samples, images,
  detail)→IMAGE`，1 步 CFG 1、每 15 帧过模型；官方权重 `relayvsr_x2_lightx2v_int8_convrot.safetensors`
  + `relayvsr_flash_vae_x2_fp32.safetensors`（Comfy-Org/RelayVSR，Apache-2.0）。**本机钉定 revision
  `b0b74356…`（2026-10-05）不含该合并**，权重未下载未钉 sha256。
- `backend/shine/comfyops/relayvsr.py`：权重 pin 占位（sha256=None →
  `RELAYVSR_WEIGHTS_NOT_PINNED`）、官方节点合同图（1 步 Euler/CFG1 + Flash VAE 解码）、
  `verify_upscale_contract`（**严格 2×/同 fps/同时长**）、`mux_source_audio`（`-c:v copy -c:a copy`
  无损回灌源音轨）、`stage_intent_digest`（提交前意图身份）。
- `backend/shine/comfyops/relay_stage.py`：`RelayUpscaleItem` 执行链——CAS 领取 + 租约心跳；
  `resource_slot("RELAYVSR", 8192)` 进同一租约表（GPU 忙 → 回 PENDING 不是失败）；readiness 在
  提交前再核（本机 BLOCKED 时条目具名失败、**零提交**）；intent 先落库、prompt_id 随后、UNKNOWN
  永久事实；产物 FFmpeg 音轨合并 + 合同 QC + `store_artifact` 新产物（源产物不动）。
- 调度融合：Worker tick 在素材出图之后加 `run_pending_relay_items(limit=1)`；run 的集交付单在
  "全部 H3 镜头 PASSED 后" 入队 relay 条目并**等待全部终态**（`episode_relay_items_unfinished`）；
  EDL 逐镜优先采用已验证的 X2 产物（`relay_artifact_for_job`，身份校验不过不采用）。

### 2.6 前端（WP7）

- `DirectorShootPanel`：`shoot-profile` 下拉与首条 Ref2VA 自动选中**移除**；新策略选择器
  （模型版本/画面加速 radiogroup + 二次精修/高清视频开关；未验收项带「未验收」徽标禁用，
  RelayVSR 显示"本机还没就绪"）；开拍提交 `strategy`，成功文案
  "系统已为 N 镜自动安排参考素材和生成策略"。选项读
  `GET /projects/{id}/director/strategy-options`；读不到时保守回退（INT8+pdd8），不发明拒绝。
- 分段卡（成片中心）显示 `X2 高清` 阶段状态；PASSED 后封面/打开文件夹指向 X2 产物。
- 专业模式面板（ProductionSeriesPanel 等）的选择器保留——它们是专家层，不在导演路径。

## 3. 数据库迁移

`f180d0010001`（down_revision `f180c0030001`）：

- `production_series.generation_strategy`（JSON，nullable）——系列冻结策略；
- 新表 `relay_upscale_items`（唯一键 `(project_id, job_id, stage)`；状态机/租约三件套/
  intent_sha256/prompt_id/external_outcome/输出身份）。

`alembic upgrade head` + `alembic check` 在全新库验证干净（无 drift）。

## 4. 文件变更清单（生产代码）

新增：`comfyops/mode_capabilities.py`、`comfy/graph_compiler.py`、`comfyops/relayvsr.py`、
`comfyops/relay_stage.py`、`strategy.py`、迁移 `f180d0010001`。
修改：`comfyops/service_validation.py`、`runtime_configuration.py`、`handoff_startup.py`、
`variant_binding.py`、`production_series.py`、`services.py`、`worker.py`、`main.py`、
`schemas.py`、`models.py`、`director/package.py`、`director/streamline.py`、
`production/runner.py`、`production/episode_delivery.py`、`api_streamline.py`、`api_segments.py`；
前端 `api.ts`、`App.tsx`、`DirectorShootPanel.tsx`、`GenerationSegmentsPanel.tsx`、`styles.css`。

## 5. 与既有纪律的关系

- **未修改** `worker.py`/`preproduction.py` 的审片身份钉扎所覆盖语义之外的门禁；`worker.py`
  本身有改动（动态编译 + relay tick），按 AGENTS.md 规则 VLM 正负校准义务在本轮**仍然未执行**
  （沿袭上一轮未完成状态），不得声称已校准。
- 幂等/UNKNOWN 对账/断点恢复/历史交付语义未放松：series/attempt 的既有机制原样运行，relay 条目
  复用同一套纪律（intent-before-submit、UNKNOWN 永久、CAS fencing）。
- 旧 `workflow_profile_id` 路径逐字节兼容（所有旧测试不动断言通过）。

## 6. 真机验收分级（诚实边界）

| 项 | 等级 | 证据 |
|---|---|---|
| 动态图节点合同（7 种组合） | **REAL_PASS**（真实 object_info 验证） | `.local-acceptance/h3dyn-20261113/node_contract_validation.json`：ComfyUI 0.39.0，全部 0 missing |
| RelayVSR 节点缺失 | **如实 BLOCKED** | 同上：`RelayVSRConditioning`/`VSRDecode` 不在 object_info；权重未钉定 |
| 单采真实渲染（INT8+PDD8, 1 图 5s） | 见 `render_evidence.json`（§8 引用） | |
| 二采真实渲染（同配方 + 二段） | 同上 | |
| RelayVSR X2 真实超分 | **NOT_VERIFIED / BLOCKED** | 需要：ComfyUI 升级到含 `2f89433` 或经审计的兼容节点包 + 下载并钉定两个官方权重的 sha256；复验方式见 §6.1 |
| 语义/艺术验收 | **NOT_REVIEWED** | 人工审片保留给用户 |

### 6.1 RelayVSR 复验方式（解锁步骤）

1. 升级 ComfyUI 到 ≥ `2f8943324e1eaa89a5ee6c7db93602299246b5f1`（含 comfy-kitchen window_attention
   依赖），按 `docs/upstream-audit.md` 纪律钉 revision 并回归 H3 基线图；或审计等价节点包。
2. 下载 `relayvsr_x2_lightx2v_int8_convrot.safetensors`、`relayvsr_flash_vae_x2_fp32.safetensors`
   （Comfy-Org/RelayVSR，Apache-2.0），计算 sha256 后写入 `relayvsr.py` 的 pin。
3. `relayvsr_readiness()` 自动转 AVAILABLE；策略 UI 的「高清视频」开关解锁；跑一轮真实集即得
   REAL_RELAY_X2_PASS 证据。

## 7. 回归证据

- 后端八分片（按文件均分、`split -n r/8`、`CODEBUDDY_SAFE_DELETE_ENABLED=0`）：
  `.local-acceptance/shards-20261113/`（各分片退出码与日志留存；首跑 6/8 绿，修复 5 个
  HEAD_REVISION/启动门槛测试期望后复跑 ac/af 分片全绿——见 §7.1）。
- 前端：`npx vitest run --no-file-parallelism` **47 文件 278 用例全过**；`npm run build` 成功；
  `npx tsc --noEmit` 干净。
- Ruff：`ruff check backend/shine backend/tests` 全过。
- Alembic：`upgrade head` + `check` 干净。

### 7.1 首跑失败与修复

| 失败 | 根因 | 修复 |
|---|---|---|
| `test_alembic` / `test_migrations_windows` ×2 / `test_global_workflow_name_migration` | 测试硬编码旧 head `f180c0030001` | 期望更新为 `f180d0010001`（迁移本身干净） |
| `test_managed_startup::test_startup_refuses_a_renderer…` | 启动门槛收窄为基线名后，随机名测试 Profile 不再触发 | fixture 改用基线名注册，断言不变（语义仍成立） |

## 8. 新增测试清单

| 文件 | 数量 | 覆盖 |
|---|---|---|
| `test_mode_capabilities.py` | 11 | T06 漂移 advisory、v2 seal、v1 兼容、自动补齐、启动门槛收窄、catalog-only 修复 |
| `test_graph_compiler.py` | 30 | T01/T03/T08：合法组合/上限具名拒绝/二采结构/确定性/变体/解析器兼容 |
| `test_dynamic_strategy_pipeline.py` | 7 | 策略贯穿链、T05/T18、互斥、幂等、策略选项 API |
| `test_strategy_worker_submission.py` | 3 | Worker 动态编译提交、live 缺节点拒绝、二采图提交 |
| `test_relay_upscale_stage.py` | 5 | BLOCKED 如实阻断、全链成功、1.5× 反例、幂等/交付等待、EDL 优先 |
| 前端 `directorRun.test.tsx` | 更新 | 开拍提交策略而非 profile id |

## 9. 残余风险与限制

1. **RelayVSR X2 真机 BLOCKED**（§6.1）；UI 开关禁用、策略级启用会被
   `UPSCALE_BACKEND_UNAVAILABLE` 拒绝——不会静默降级。
2. **视频参考（ref_videos）编译器暂拒绝**：无已核验装载配方，待真机证据；H3 的
   `video_reference` 连续性镜头在动态路径同样不可用（静态路径行为不变）。
3. `turbo4` 加速已声明未验收（拒绝编译）；W6A8 候选变体门禁不变。
4. worker.py 有改动 → `client_review_identity` 变化，VLM 正负校准义务**未执行**（沿袭待办）。
5. 二采 `latent_scale=1.0`（纯尾部精修）的节点参数组合经真机 object_info 合同验证与一次真实
   渲染验证；画质收益属 HUMAN_ART_REVIEW，未签字。
