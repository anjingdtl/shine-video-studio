# AI 素材准备建设进度 / 验收记录

- 日期与时区：2026-11-05（北京时间，UTC+8）
- 分支 / HEAD / 基线差异：`codex/director-ai-material-preparation`，自 `main / bff25ad`（0.2.0）开出；
  本轮提交 **`46b2f40`**（27 个文件：8 个既有文件修改、1 个迁移、9 个新模块、2 个新测试文件、
  3 处 head 断言更新、审计 + ADR + 本进度文件）。工作树除 4 个开工前既有未跟踪文件外干净。
  **未推送**（推送属于共享状态变更，需用户确认）。
- 当前阶段与完成判定：**阶段 A 完成；阶段 B 代码与离线验证完成，实机出图待权重下载；
  阶段 C 完成契约接入（工具选择 + 两个运行时提示词），尚未接入真实 LLM 调用链**；
  阶段 D/E 未开始
- 用户既有修改和保留文件：`.zcode/`、`docs/DIRECTOR_AI_MATERIAL_PREPARATION_PLAN_20261104.md`、
  `docs/LOCAL_AGENT_MATERIAL_PREPARATION_PROMPT_20261104.md`、`docs/H3_MV_MANSIONS_15S_T2VA_SCRIPT.md`
  均为开工前既有的未跟踪文件，**未删除、未覆盖、未纳入提交**
- 数据目录 / 测试项目（不记录秘密）：
  - 开发机既有活动栈：API `127.0.0.1:8000`（PID 26204，2026-11-04 21:56 启动，
    数据目录 `.local-acceptance/real-user-journey-final/data`，alembic `f160a0010001`），
    Web `127.0.0.1:5173`（PID 26472，vite）。**未停止、未接管、未写入。**
  - 迁移验证使用两个独立目录：`.local-acceptance/materialprep-mig-fresh`（空库）与
    `.local-acceptance/materialprep-mig-oldcopy`（活动库的一致性副本，经 SQLite backup API 生成）。
    活动库本体仍停在 `f160a0010001`，未被升级。
  - 开工前资源状态：`resource_leases` 498 行**全部 RELEASED**；`production_series` 无
    RUNNING/CLAIMED（12 COMPLETED / 10 CANCELLED / 1 NEEDS_REVIEW）；无 worker 进程、无 ComfyUI 进程；
    GPU RTX 5080 16303 MiB 中仅 877 MiB 占用。**因此不存在活跃生产被本轮中断。**

## 实际实现

### 阶段 A：契约、迁移与上游审计

| 文件 | 内容 |
| --- | --- |
| `backend/shine/comfyops/models.py` | `PLAN_PURPOSE_PRODUCTION/ASSET_PREPARATION` 常量；`PreProductionPlan.purpose`（String(24)，`server_default='production'`，非空）+ 复合索引 `ix_preproduction_plans_project_purpose` |
| `backend/shine/comfyops/preproduction.py` | `current_plan` 改为按 `purpose='production'` 过滤（新增 `current_plan_for_purpose`/`current_material_plan`）；`_plan_for_manifest` 同样过滤；`analyze` 显式写入 purpose；新增 `require_plan_for_purpose` 供按 ID 操作校验用途 |
| `backend/shine/api_preparation.py` | `run_preparation` 的 CAS 认领 `UPDATE` 增加 `purpose='production'` 条件，使写侧也不可能认领素材计划 |
| `backend/alembic/versions/f170a0010001_material_preparation.py` | 新迁移（down_revision `f160a0010001`）：加 purpose 列 + 索引；建 `material_runs` / `material_items` / `material_image_attempts` |
| `backend/shine/materialprep/states.py` | 计划/单项/批次/attempt/外部结果状态与终态、可认领态、在途态集合 |
| `backend/shine/materialprep/contracts.py` | `ScriptPreparation`、`ScriptPreparationRequirement`、`MaterialImageBrief`、`MaterialVisualDirection`、`RequirementDecision`、`MaterialConfirmation`；全部 `extra='forbid'`，逐项长度上限 |
| `backend/shine/materialprep/models.py` | 三张台账表，复用同一 SQLite / Worker / `ResourceLease` |
| `docs/adr/0006-director-ai-material-preparation.md` | 复用映射、purpose 隔离后果、持久执行模型、反重复约束、提示词权威边界 |
| `docs/upstream-audit.md` | 新增 2026-11-05 Qwen-Image-2.1 章节 |

**隔离点枚举（不是只改一个 `current_plan`）**：生产侧对 `PreProductionPlan` 的读取全部经过
`current_plan`（readiness / enforce / enforce_references / enforce_job / freeze / analyze 去重 /
`main.py` 风格冻结保护 / `native.py` 两处 / `api_operator.py` revalidate）或 `_plan_for_manifest`
（revalidate），两者均已过滤；仓库内**没有任何路由从请求体接受 PreProductionPlan id**
（已 grep 确认，所有 `plan_id` 用法属于 `InstallationPlan` 或 `AssetRequirement.plan_id`），
因此新的按 ID 素材路由使用 `require_plan_for_purpose`。

### 阶段 B：运行时与持久化图片任务

| 文件 | 内容 |
| --- | --- |
| `backend/shine/materialprep/qwen_workflow.py` | 上游钉点常量；**展开后的 API 图**（保留模板节点号 451/480/453/454/452/456/458/457/461）；`PARAMETER_MAP`（含 width/height 显式映射）；`REQUIRED_NODES`；`runtime_fingerprint`；`declared_output_slot`（只取声明输出节点）；四个模型文件的 role/字节/SHA-256/URL 与 required 标记 |
| `backend/shine/materialprep/image_prompts.py` | `PROMPT_TEMPLATE_VERSION='material-image-v1'`；三类固定构图规则；确定性 `compose()`（拒绝 CJK、拒绝超长、不截断）；`avoid` → 独立 negative prompt；`template_evidence()` |
| `backend/shine/materialprep/runtime.py` | `ImageRuntimeConfig`（原子写 + digest）；四级就绪 `NOT_CONFIGURED/FILES_VERIFIED/GRAPH_VERIFIED/RENDER_VERIFIED`；逐文件 SHA-256 校验；`verify_graph_against_object_info`；`render_verified_families`（以台账中真实成功 attempt + 指纹为证据）；许可与上游信息随报告返回 |
| `backend/shine/materialprep/execution.py` | `claim_next_item`（条件 UPDATE 认领）；`resolve_runtime`（真实模式门禁，验证批允许低一层）；`execute_item` + `_render_inside_lease`（提交意图先落库 → 事务外 `/prompt` → prompt_id 立即落库 → 轮询 → 只取声明输出节点 → 字节校验入库 → 技术 QC）；`_submit_outcome`（只有 `ComfyUnavailable` 算未送达可恢复，其余不确定一律 UNKNOWN）；`run_pending_image_items`（逐项取放租约的 Worker 驱动） |
| `backend/shine/materialprep/service.py` | `create_draft_plan` / `aggregated_requirements`（服务端归并身份、报告歧义）/ `confirm`（写**新**计划修订，快照 hash 不符即 409）/ `create_batch`（幂等键 + 请求 hash + 业务唯一约束）/ `create_validation_batch`（三类固定小样 brief）/ `request_cancel` / `resume_items`（拒绝 UNKNOWN）/ `reconcile_run`（只核对，`resubmitted=False`）/ `refresh_run_status` / `plan_progress` |
| `backend/shine/api_material.py` | 12 条路由；变更类路由全部 `status_code=202`；`generate` 的 `mode` 是 `Literal["real"]`，无 fake/auto 取值 |
| `backend/shine/worker.py` | `_run_configured_once` 在 H3 series 之后、advisory 审片之前插入 `run_pending_image_items(limit=1)` |
| `backend/shine/main.py` | 注册 `material_router` |

**关键上游发现（已写入审计，非猜测）**：

1. 官方模板是 **UI 格式 v0.4 且含 subgraph 实例**，不能直接提交 `/prompt`；已展开为 API 图。
2. 模板所需节点**全部已在本机 ComfyUI v0.39.0 核心 `comfy_extras`** 中（commit
   `b0b743566f65daafc423b4fea8a2fbda94b3384a`），无需安装任何自定义节点包，因此
   **不需要为兼容性隔离或升级 H3 环境**。
3. 模板出厂即 `switch=false`，而 `ComfySwitchNode` 两个分支都是 `lazy=True` 且
   `check_lazy_status` 只在 switch 为真时请求 `on_true`，因此**提示词扩写分支永不被求值**。
   据此 API 图省略该分支，`qwen3.5_9b_...pe_t2i`（9,471,072,252 字节）**不需要下载**，
   必需权重从 24.92 GiB 降到 **16.10 GiB**（17,283,091,112 字节）。
4. 输出节点由 `SaveImageAdvanced` 改为核心 `SaveImage`：前者的 `format` 是 `DynamicCombo`，
   其 API 序列化不在已审计证据内，而模板自身的 widget 值（png / 8-bit / sRGB）正是
   `SaveImage` 的默认输出。字节等价，输出节点唯一且无歧义。
5. 权威权重来源是 `Comfy-Org/Qwen-Image-2.1`（revision `aacf31a6…`），与本机既有 H3
   `int8_convrot` 权重同一发布方；**Qwen 官方仓库发布的是 diffusers 分片格式，不含这些单文件**。
6. 许可为 **"Qwen RESEARCH LICENSE AGREEMENT"（2026-09-20）**，第 1.i 条非商业=仅研究或评估，
   第 2.b 条商业使用需另行取得许可。本轮用途限定本地研究评估；`create_validation_batch` 与
   候选 provenance 均写入 `LOCAL_RESEARCH_AND_EVALUATION_ONLY`。
7. **本机无法访问 `huggingface.co`**（`http=000`），`hf-mirror.com` 可访问且提供同一 LFS 对象。
   既有 `config/deployment/h3-ref2va-windows-x64.json` 的下载 URL 全部指向 huggingface.co，
   图片安装计划若沿用该主机会直接失败——已记入审计，尚未改安装器。

### 阶段 C（部分）：Agent 契约接入

| 文件 | 内容 |
| --- | --- |
| `backend/shine/agents/film_contracts.py` | `FilmRunRequest.tool` 增加 `script_prepare` / `material_image_brief`；`FilmAgentRegistry.get()` 增加两个分支并扩展有效工具集合：`script_prepare` 仅 `scriptwriter` → `ScriptPreparation`，`material_image_brief` 仅 `producer` → `MaterialImageBrief`。**`producer` 的默认契约仍是 `ProductionPlan`，未改动** |
| `backend/shine/agents/crew_prompts.py` | 新增 `script_prepare_prompt()` 与 `material_image_brief_prompt()`，`crew_prompt()` 对这两个 tool 直接返回专用提示词 |
| `backend/shine/materialprep/service.py` | `create_batch` 新增 `briefs` 透传并纳入 `request_hash`；`api_material.regenerate` 校验 `MaterialImageBrief` 后写入 item |

两个专用提示词**刻意不复用分镜链的公共块**：`script_prepare` 若带上
"4-to-15-second beat timeline" 或 `editorial_timing_plan` 段落，就等于邀请模型产出
服务端从未要求的镜头——这正是 AGENTS.md 记录的 2026-11-04 `EDITORIAL_TIMING_CONFLICT`
一类的成因（提示词与服务端校验互相矛盾）。已用测试锁定这一点。

**尚未实现**：从 Worker 内以 `material_image_brief` 真实调用出品人并把产物写入
`item.payload["brief"]`（状态机的 `BRIEF_RUNNING → BRIEF_READY` 段），以及
`script_prepare` 的分场调度、预算预检与源片段校验。因此正式素材批次目前会以
`MATERIAL_BRIEF_MISSING` 具名拒绝——这是刻意选择：宁可用具名错误阻塞，
也不让服务端替出品人编造外观描述。三类**验证小样**自带固定 brief，可独立于该缺口运行。

### 兼容性与生产门禁

- 旧冻结计划的 id / payload / sha256 / rowid 顺序全部不变（见下表验证）。
- `enforce` / `readiness` / freeze / revalidate / Worker 预制作门禁不可能读到素材计划。
- 只有素材计划的项目**没有**生产合同：`readiness` 返回 `PREPRODUCTION_NOT_ANALYZED`，
  `enforce` 走 legacy 无计划分支，风格冻结不被其"保护"。
- 旧 `candidate-assets/generate` 入口的兼容行为**未改动**（含其在缺配置时选择
  `FakeImageProvider` 的分支与 `is_mock` 标注）；新入口不经过该分支。

## 验证

| 类别 | 命令或 UI 路径 | 实际结果 | 证据 | 未验证边界 |
| --- | --- | --- | --- | --- |
| 离线/自动化（A） | `pytest backend/tests/test_material_preparation.py` | **18 passed** | 本文件 | 无 |
| 离线/自动化（B） | `pytest backend/tests/test_material_image_jobs.py` | **passed（exit 0）** | 本文件 | 未覆盖真实渲染 |
| 迁移（空库） | `SHINE_DATA_DIR=.local-acceptance/materialprep-mig-fresh alembic upgrade head` + `check` | 升到 `f170a0010001`；`No new upgrade operations detected` | 三张 material 表 + purpose 索引存在 | 无 |
| 迁移（旧库副本） | 同上，目标为活动库的一致性副本（13 条 FROZEN 计划） | 13 行 `purpose='production'`；`rowid/id/project/status/sha256/created_at/payload` **逐行完全一致**；`current_plan` 仍解析为 `7832906a-d383-4eaa-a94d-0e0b510769d2`（与 AGENTS.md 记录的冻结计划一致）；`alembic check` 无漂移 | 与未升级的原始活动库逐行对比 | 未在原始活动库执行（刻意） |
| 降级/升级往返 | `pytest backend/tests/test_migrations_windows.py` | passed（含 downgrade 到 `9f2c4d7a10b3` 再 upgrade head） | 4 处 head 断言已更新为 `f170a0010001` | 无 |
| 既有回归（定向） | `pytest test_v13_preparation.py test_v13_migrations.py test_timestamp_ordering.py test_native_capabilities.py test_review_input_evidence.py test_style_input_assessment.py` | **240 passed**（exit 0） | 后台任务 `bb4mvdlae` | 无 |
| 全量后端（A 后） | `pytest backend/tests` | 首轮 4 个迁移测试因 head 断言失败 → 已修正 | `/tmp/fullsuite.log` | 见下 |
| 全量后端（B/C 后） | `pytest backend/tests` | **PYTEST_EXIT=0**，无 FAILED/ERROR | `/tmp/full2.log` | 无 |
| 素材 + 分镜链回归 | `pytest test_material_preparation.py test_material_image_jobs.py test_crew_prompt_contract.py test_film_crew.py` | **99 passed**（exit 0） | `/tmp/mat2.log` | 无 |
| 前端 | — | **NOT_RUN（本轮未改动 `frontend/` 任何文件）** | `git show --stat 46b2f40` | 阶段 D 必须跑 `npm.cmd test` / `tsc -b` / `npm.cmd run build` |
| Ruff | `ruff check backend/shine backend/alembic backend/tests` | **All checks passed** | — | 无 |
| 路由注册 | `create_app()` 后枚举 | 12 条 material/image 路由全部注册 | 冒烟脚本输出 | 未做真实 HTTP 端到端 |
| 真实 LLM | — | **NOT_RUN** | — | 阶段 C 未实现 |
| 真实 Qwen：角色/场景/道具 | — | **NOT_RUN（进行中）** | 见"运行状态" | 权重下载未完成；未启动受管实例 |
| 用户晋升与风格冻结 | — | **NOT_RUN** | — | 需阶段 C/D 与用户本人操作 |
| 真实 H3 引用 | — | **NOT_RUN** | — | 依赖上一步 |
| 人工作品评审 | — | **NOT_RUN** | — | 需用户本人 |
| Windows 入口 / 安装包 | — | **NOT_RUN** | — | 本轮未改依赖/启动/前端产物，阶段 E 再判定 |

### 本轮已修复的真实回归

全量后端首轮暴露 4 个失败，**根因是测试硬编码了 head 修订号**，不是功能缺陷：
`test_alembic.py:101`、`test_global_workflow_name_migration.py:44`、
`test_migrations_windows.py:91,114` 断言 `alembic_version == "f160a0010001"`。
已按仓库既有约定更新为 `f170a0010001`，未删除测试、未降低门禁。

## 运行状态与恢复

- **权重下载已完成并逐文件 SHA-256 校验通过**（脚本 `.local-acceptance/qwen-image21/fetch_qwen_image21.sh`，
  日志 `fetch.log`）。落点在既有 ComfyUI 安装
  `F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI\models\`，全部为**新增文件**，
  H3 权重（`diffusion_models/Minimax-H3/*`）未被修改：

  | 文件 | 字节 | SHA-256 | 结论 |
  | --- | --- | --- | --- |
  | `vae/qwen_image_2.1_vae_bf16.safetensors` | 675,509,688 | `bb21f747…e4876b7c9` | VERIFIED |
  | `diffusion_models/qwen_image_2.1_int8_convrot.safetensors` | 7,256,783,064 | `cb74113c…75eaa57d` | VERIFIED（独立复核） |
  | `text_encoders/qwen3vl_8b_int8_convrot.safetensors` | 9,350,798,360 | `8bfd0f6e…60452e8f` | VERIFIED |

  合计 17,283,091,112 字节（16.10 GiB），与 `qwen_workflow.REQUIRED_MODEL_BYTES` 一致。
  磁盘剩余 331 GB。
- `text_encoders/qwen3.5_9b_qwen_image_2.1_pe_t2i.int8_convrot.safetensors` **已删除**：
  该文件下载不完整（9,370,058,752 / 9,471,072,252），且本产品 API 图不含提示词扩写分支，
  不需要它。留在 models 目录会让 ComfyUI 把它列为可选模型而被误选到损坏权重，故清除。
- **两次已处理的下载事故（必须记录）**：停止后台任务后残留的孤儿 `curl` 与重启后的 curl
  并发追加同一文件，使扩散模型一度膨胀到 10,358,233,304 字节；脚本的尺寸+SHA-256 双重校验
  判为 `[FAIL-SIZE]` 而**没有**写成 VERIFIED。随后杀掉孤儿进程、删除损坏文件、单进程重下并校验通过。
  第二次清理时又发现脚本的重试循环会在 curl 被杀后重新拉起 curl，必须杀父 bash；
  期间一条 PowerShell 过滤条件匹配到了工具自身的 shell 而误杀了自己的会话（已恢复）。
  结论：下载后必须逐文件校验字节，且停止下载要杀父进程而不是只杀 curl。
- Worker：**未启动**。API `127.0.0.1:8000` / Web `127.0.0.1:5173` 是开工前既有的活动栈，未接管、未重启。
- 持有资源：无。开工前 498 条租约全部 RELEASED，本轮未获取任何 GPU 租约。
- UNKNOWN 请求：**无**。本轮未向任何外部渲染器提交过 `/prompt`。
- 不可自动重试项：无在途外部提交。
- 受管进程状态与所有权证据：本轮未启动、未停止任何受管 ComfyUI 或审片进程。

## 阻塞及下一步

### 阻塞

1. ~~权重下载未完成~~ **已解除**：三个必需权重全部下载并逐文件 SHA-256 校验通过（见"运行状态"）。
   剩余依赖项变为下面第 3 条（需启动受管实例读取活体 `object_info`）。
2. **阶段 C 只完成契约接入，正式素材批次还无法端到端出图**。
   `execute_item` 通过 `_compose_prompt` 读取 `item.payload["brief"]`；
   `create_validation_batch` 会写入固定小样 brief（**三类验证任务可独立运行**），
   `regenerate` 路由接受并校验用户/出品人给出的 brief，
   但普通 `create_batch` 不产出 brief，正式批次会以 `MATERIAL_BRIEF_MISSING` 明确拒绝。
   缺少的是"Worker 内真实调用出品人 → 写入 item.payload['brief']"这一段。
   这是刻意选择：宁可用具名错误阻塞，也不让服务端替出品人编造外观描述。
3. **API 图尚未对活体 `object_info` 校验**。测试用的是从钉点 ComfyUI v0.39.0 源码读出的
   输入名 fixture；`runtime.verify_graph_against_object_info` 是真实门禁，但需要启动受管实例。
   在此之前不得声称"节点与图已校验"。
   **本轮刻意没有手工启动 ComfyUI**：AGENTS.md 规定受管渲染器属于工作台，一个由脚本手工拉起、
   工作台不认识的渲染器会在栈之外常驻显存并阻塞下一次运行，而停止它又需要 PID/birth/命名
   Job Object 三证。正确路径是走产品自身的受管实例机制（`comfyops/instances.py` +
   `handoff_startup.py`），本轮预算不足以把它接完，因此留下明确的下一步而不是制造一个
   工作台无法释放的孤儿渲染器。权重已就位，该步骤现在只差"启动受管实例"这一个动作。
4. **安装器主机可用性未处理**。审计已记录本机 `huggingface.co` 不可达；
   产品内的图片安装计划若沿用既有钉点主机会失败。本轮只做了本机验收用的下载脚本，
   **没有**改动 `config/deployment/*` 或安装器。

### 需要用户做的具体选择及对应页面

- 无（本轮尚未到需要人工确认的界面步骤）。
- 提前告知：本轮为完成实机验证，已在你既有 ComfyUI 安装
  `F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI\models\` 下下载 Qwen-Image-2.1 权重
  （必需 3 个文件，合计 16.10 GiB；VAE 与 CLIP 已校验通过）。这些是**新增文件**，
  不修改、不升级、不隔离既有 H3 环境。若不希望保留，删除这 3 个文件即可，
  H3 生产不受影响。

### 下一阶段首个可执行动作

阶段 C 剩余部分第一步：在 Worker 的 item 生命周期里补上 `BRIEF_RUNNING → BRIEF_READY` 段——
以 `role='producer'`、`tool='material_image_brief'` 通过既有 `FilmAgentDispatcher` /
LLM Gateway 真实调用一次，把返回的 `MaterialImageBrief` 写入 `item.payload["brief"]`
并落 `AgentArtifact`，随后才进入 `resolve_runtime`。调用必须在获取 GPU 租约**之前**
（Gateway 在 `CURRENT_RESOURCE` 已设置时会拒绝），并复用既有的
`SHINE_AGENT_STEP_DELAY_MS` 间隔、显式角色路由与"已知 Schema 错误最多一次定向修正、
UNKNOWN 不重放"规则。
其后是 `script_prepare` 的分场调度、预算预检与服务端源片段校验，再接阶段 D 的界面。

### 最终结论

**PARTIAL / NOT ACCEPTED**

阶段 A 已按退出条件完成并有证据；阶段 B 的代码与离线验证完成，但
`REAL_QWEN_VERIFIED`、`REAL_H3_VERIFIED`、`HUMAN_REVIEW`、`WINDOWS_AND_PACKAGE`
全部 `NOT_RUN`，阶段 C/D 未开始。不得把"路由返回 202""离线测试通过""模板与权重钉点已核验"
当成导演 UI 实机验收。
