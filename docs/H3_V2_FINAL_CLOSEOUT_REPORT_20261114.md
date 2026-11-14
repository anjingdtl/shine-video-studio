# Shine H3 动态资源自整备 V2.0 最终收尾报告（2026-11-14）

> 本报告是工程收尾记录，**不是真机生产验收声明**。基线 `origin/main@28e1e553365f`（与上一轮远端验收基准一致，本轮 `git fetch` 后远端 main 未前进）。
> 工作分支：`feature/h3-v2-final-closeout-20261114`，独立 worktree
> `F:/ClaudeWorkSpace/projects/shine-h3-v2-closeout-20261114`，不覆盖其他 Agent 的 worktree。
> 原方案：`docs/SHINE_H3_DYNAMIC_RESOURCE_SELF_PREPARATION_V2_MAIN_23467957_20261113.md`；
> 上一轮阶段报告：`docs/H3_RESOURCE_V2_PROGRESS_20261114.md`。

## 0. 三级结论（严格区分，不混用）

| 等级 | 本轮结论 |
| --- | --- |
| **ENGINEERING_PASS** | 本轮新增/修复的代码、接口与离线测试通过；全量后端 8 分片 + 前端全量 + Ruff + Alembic 见 §4。W6A8 资源「选择→扫描→授权下载→校验→绑定」的工程链路打通，候选生产闸门保持关闭。 |
| **REAL_HOST_PASS** | **未达成（NOT_RUN）**。本机为 RTX 5080 16GB，非 RTX 4070 12GB；W6A8 官方权重未下载（候选 `auto_download_allowed=false`，本轮未授权下载 32GB 候选权重），没有 W6A8 真实 attempt/artifact/显存峰值/解码证据。 |
| **PRODUCTION_READY** | **未达成（BLOCKED）**。W6A8 仍为 CANDIDATE，无真机试镜与质量验收；RelayVSR X2 权重未钉 SHA。INT8 基线的既有生产能力不受本轮改动影响（回归保护）。 |

## 1. 本轮实际修复（已提交，含回归）

### 1.1 P0 导演模式 W6A8 无法选择/下载（首要阻断）

根因（三处叠加）：
1. `DirectorShootPanel` 模型按钮 `disabled={!ready}`，未就绪的 W6A8 永远点不中；
2. `StrategyResourceCard` 仅在 `variantId === 'ref2va-lowend-candidate'` 时渲染，而该 id 又选不中 → 死锁；
3. `variant-availability` 用 `bool(required_component_ids) and required <= present` 判 `installed`，W6A8 的 `required_component_ids=[]` 使 `installed` 恒假。

修复：
- 后端 `GET /setup/variant-availability` 解耦七个事实：`selectable_for_preparation` / `installed` / `verified` / `bound` / `profiles_registered` / `runtime_validated` / `trial_ready` / `production_ready`，`available` 保留为开拍闸门。`installed` 改以**图推导的权重组件**为准（manifest `required_component_ids` 为空时不再误判），并保留 adopted 目录扫描作为旁证。
- 前端模型按钮不再因未就绪禁用；选中未就绪/候选模型即显示资源整备卡；开拍仍由 `chosenVariantAvailable` + 服务端 `strategy_blockers`（`MODEL_VARIANT_NOT_ACCEPTED`）双重把守。
- 回归：`backend/tests/test_resource_resolver_v2.py::test_variant_availability_decouples_preparation_from_production`、`frontend/src/test/directorRun.test.tsx`「未安装的 W6A8 仍可选中并进入资源整备，但开拍闸门保持关闭」。

### 1.2 P0 新装机资源安装循环依赖

根因：`/setup/resources/install-plan` 强制 `managed_installation_pin(instance_id)`，新装机没有受管 ComfyUI 实例 → 无法下载权重；而装 ComfyUI 本身又需要先下载。

修复（阶段 A/B 拆分，复用既有安装链，不新建第二套安装器）：
- `ResourcePlanRequest.instance_id` 改为可选。阶段 A（离线整备：硬件/磁盘识别、依赖解析、扫描、哈希、授权下载、断点、缓存登记）不要求实例；阶段 B（绑定受管实例、节点 ABI、运行配置）仍钉住实例身份。
- `validate_resource_plan` 接受无实例计划，并拒绝伪造 `instance_pin`。
- 前端资源卡无实例时跳过受管目录扫描、直接生成离线计划，并明示「装好 ComfyUI 后再绑定（阶段 B），不会凭空启动渲染器」。
- 回归：`test_resource_resolver_v2.py::test_phase_a_plan_needs_no_managed_instance`。

### 1.3 P0 动态工作流资源装配闭环（W6A8 + native20 样例）

核验事实：`graph_compiler._candidate_stack` 已读取 manifest 钉住的候选配方哈希，采用候选图的完整模型链与 VAE，**不是只替换 DiT**。本轮编译实测 W6A8 native20 图：
- DiT `minimax_h3_ref2va_pruned_w6a8`、文本编码器 NVFP4/AWQ、视频 VAE **INT8 ConvRot**（非基线 FP16）、音频 VAE FP32；
- 无 realism 人物 LoRA、无 SolAttn；模型链 UNETLoader→SigmaShift→ChunkFeedForward→SageAttentionPatch→LowVRAMAttention；
- `execution_identity().compiler == shine.graph_compiler.v2`；PDD/二采对候选具名拒绝（`VARIANT_RECIPE_UNVERIFIED`）。
- 资源解析精确导出四组件、合计 35,087,209,179 字节（`test_candidate_preview_is_exact_and_does_not_claim_trial`）。

本轮强化回归 `test_graph_compiler.py::test_candidate_stack_uses_pinned_recipe_and_refuses_unverified_combinations`，锁定四权重完整闭包，防止回退到基线 FP16 VAE/LoRA。

**仍 BLOCKED 的子项（item 10 受管启动配置）**：`deployment.generate_configuration` 仍以默认 INT8 catalog 生成节点白名单与显存/内存门槛（`default_variant_requirements()`）。W6A8 manifest 的 `requirements.*` 全为 `unknown`，无真机实测门槛；按任务纪律**不得猜测门槛**，故 W6A8 受管激活与试镜保持 BLOCKED，待真机验收。生产闸门已确保 W6A8 不会进入正式生产。

### 1.4 P0 模型下载与文件校验

复用既有 `ControlledDownloader` + `InstallationPlan/Attempt` + `installation_worker`，未新建下载器。十项场景在既有合成网络测试中已覆盖（见 T03–T09、T22 映射）：同 SHA 复用、异 SHA 拒绝（`FILE_CHECKSUM_FAILED`）、Range 断点续传、镜像/官方互切、取消/崩溃恢复同一 Attempt、重复点击幂等、未审镜像拒绝、不在受管模型目录不得称就绪（`bound`/`consumable` 与 `verified` 分离）。`/variant-availability` 的校验态与 available 态不一致问题随 §1.1 解耦修复。

### 1.5 P0 ComfyUI 连接诊断与安全恢复

核验事实：`runtime_orchestrator/status.py` 已实现七层状态投影（transport/ownership/capability）、`_RECOVERY` 安全动作表、外部端口冲突检测、PID/birth/命名 Job Object 归属三证、已知 COMFY 错误码（`COMFY_HANDOFF_OWNERSHIP_UNVERIFIED`/`COMFY_STARTUP_ALREADY_RECORDED`/`COMFY_REQUIRED_NODE_MISSING`/`EXTERNAL_INSTANCE_ON_PORT`）；`EngineStatusEntry` 已分层展示并支持复制脱敏诊断包。近期 handoff 恢复提交（`b663fa2`/`ab947a2`/`ca7d1b5`/`ec9f26b`）已处理过期启动记录的安全收束。

本轮新增修复：**旧 Edge WebView 兼容性**。`AbortSignal.timeout` 在旧版 Edge WebView2 / Safari 16 之前不存在，直接调用会抛 `TypeError`，使每个带超时的请求失败、界面误报「断线」。新增 `api.ts::timeoutSignal(ms)`：原生可用时用原生，缺失时回退 `AbortController + setTimeout`，行为一致；全站 8 个文件（含 `/system/status` 轮询的 `useStudio.ts`）替换。回归 `frontend/src/test/timeoutSignal.test.ts`（含删除 `AbortSignal.timeout` 模拟旧环境）。

### 1.6 P1 最新 GitHub CI 失败（run 43491015993）

| 失败 | 根因 | 修复 |
| --- | --- | --- |
| Ruff UP037 `api_setup.py:662` | 注解多余引号 | 去引号（`from __future__ import annotations` 已在） |
| Windows `test_gpu_release_confirmation` 两项 | 阳性阶段 50ms 释放窗口短于 CI Windows 冷 httpx 客户端 + TCP 往返延迟，确认释放被误判未确认 | 阳性阶段放宽到抗抖动窗口（`RELEASE_WINDOW_S=5`），产品默认 120s 不变；拒绝阶段保持短窗口 |
| Windows `test_production_gate_weight_evidence::test_the_package_entry_refuses_what_the_gate_refuses` | 缺 `fake_deployment_host`；CI Windows pytest 临时目录在系统盘 → `require_storage` 抛 `CHOOSE_DEPLOYMENT_DIRECTORY_OUTSIDE_SYSTEM_DRIVE` → 缓存读不到 → `NOT_AVAILABLE` 而非 `NOT_VERIFIED` | 补 fixture 隔离存储盘策略，还原字节核验判定（已在系统盘 basetemp 复现并验证修复） |
| 浏览器 E2E 大纲文案 | 收敛提交把页面唯一主操作收进页面级操作栏，按钮不再在 `#outline-confirm` 内 | 断言改读 `.action-rail`（真实产品 surface） |
| L1 quick `test_v13_preparation` 两项 `CANDIDATE_NOT_APPROVED` | **预存 CI 配置缺口**：L1 quick job 未装 FFmpeg（L2/L3 才装），候选 QC 的 `_full_decode_check` 返回 `unavailable` → `QC_FAILED`。main 上被 Ruff UP037 失败掩盖（Ruff 先于测试），修复 Ruff 后暴露 | 给 L1 quick job 补 FFmpeg/FFprobe 安装步骤（与 L2/L3 一致）。非代码回归：同一 quick 命令在本机 Windows（有 FFmpeg）通过 |

均未删除失败测试、未放宽安全拒绝；仅隔离环境干扰与同步真实 UI 行为。

## 2. 残余问题与外部阻断清单

| 项 | 状态 | 原因 / 下一步 |
| --- | --- | --- |
| W6A8 受管启动配置按变体生成 | BLOCKED | `generate_configuration` 仍用 INT8 默认门槛；W6A8 requirements 全 unknown，需真机实测后才能钉门槛，不得猜测 |
| W6A8 隔离试镜入口 `/setup/resources/trial` | NOT_RUN | 未实现；依赖上一项与真实 W6A8 权重 |
| RTX 4070 12GB SM8.9 SageAttention ABI | BLOCKED | 本机为 RTX 5080，无 4070；候选图仍含 Sage 节点，SM8.9 兼容 wheel 未核实，不得删节点或用未验证 wheel 冒充 |
| W6A8 真机出片 / 显存峰值 / 解码 | NOT_RUN | 无 4070、未下载候选权重 |
| RelayVSR X2 权重 pin 与真实二倍超分 | BLOCKED | `RELAYVSR_WEIGHTS_NOT_PINNED`，无可信 SHA/固定版本 |
| AUTO 真实网络跨源测速与断线切换 | NOT_RUN | 仅有界 HEAD 择源 + 合成网络测试；真实网络未验收 |
| VLM 正负校准 | NOT_RUN | 本轮未改 `worker.py`/`preproduction.py`，不触发重校准义务；但真实校准本来就没做，不得声称已校准 |

## 3. 受保护的既有能力（回归确认）

INT8 基线动态逐镜工作流、8 步/20 步、真二采、素材中心、历史项目与成片：本轮未改 `worker.py`/`preproduction.py`/H3 提交语义/VLM 身份/历史工件；相关回归（`test_graph_compiler`、`test_dynamic_strategy_pipeline`、`test_strategy_worker_submission`、`test_production_series`、`test_offline_e2e` 等）在全量分片中执行。旧风格/旧请求哈希形状不变。

## 4. 本机测试记录

- 后端全量：8 分片按文件均分并行（`CODEBUDDY_SAFE_DELETE_ENABLED=0`），日志 `.local-data/v2-closeout/chunk-*.log`，退出码 `.local-data/v2-closeout/shards-exit.txt`。**结果见 §4.1**。
- 前端全量：`npx vitest run --no-file-parallelism` → **49 文件 / 287 项通过**（`.local-data/v2-closeout/frontend-full.log`）。
- 前端构建：`npm run build` → exit 0（`.local-data/v2-closeout/frontend-build.log`）。
- 前端类型：`npx tsc --noEmit` → 通过。
- Ruff：`ruff check backend/shine backend/alembic backend/tests scripts/run_tests.py` → All checks passed。
- Alembic：隔离临时 SQLite `upgrade head` + `check` → 无 drift（head `f180d0010001`）。
- 定向：`test_gpu_release_confirmation` + `test_production_gate_weight_evidence` 在系统盘 basetemp（复现 CI 条件）下 21 项通过；`test_resource_resolver_v2` 19 项通过；连接/恢复批 51 项通过。

### 4.1 后端 8 分片结果

按文件均分 8 分片（219 个测试文件），`CODEBUDDY_SAFE_DELETE_ENABLED=0`：

| 分片 | 首轮退出码 | 干净环境复跑 | 结论 |
| --- | --- | --- | --- |
| chunk-ab / ac / ad / af / ag / ah | 0 | — | 绿，无 FAILED/ERROR |
| chunk-aa | 1 | 0 | 首轮唯一失败 `test_renderer_supervisor::test_listener_mismatch_refuses_drain_before_any_side_effect` 为 `NO_PROXY` 含 `[::1]` 的 httpx 解析假失败；`env -u NO_PROXY -u no_proxy` 复跑 exit 0、零失败 |
| chunk-ae | 1 | 0 | 首轮唯一失败 `test_v13_e2e::test_v13_full_preproduction_to_fake_h3_advisory_review_and_archive` 同为 `[::1]` 假失败；干净复跑 exit 0、零失败（20 模块） |

**定稿口径**：采用 AGENTS.md 记载的 `NO_PROXY [::1]` 处置（`env -u NO_PROXY -u no_proxy`）后，**8 分片全部 exit 0，日志无 `FAILED`/`ERROR` 行**；合计 160 个模块摘要、2106 个用例。两处首轮失败均已单独在干净环境复跑通过，确认为环境假失败而非代码回归（本会话 `NO_PROXY=localhost,...,[::1],::1`）。

证据：`.local-data/v2-closeout/chunk-*.log`（首轮）、`chunk-aa.clean.log`/`chunk-ae.clean.log`（干净复跑）、`final-modules.txt`（汇总）。

## 5. 主分支合并风险评估

- 改动集中在 `api_setup.py`（variant-availability/resources）、`resource_resolver.py`、导演拍摄前端与超时信号；未触及 `worker.py`/`preproduction.py`/handoff 客户端源码，**不触发 VLM 重校准义务**。
- `variant-availability` 新增字段为向后兼容增量；`available` 语义不变，开拍闸门不变。
- `instance_id` 改可选是放宽输入约束，但 `validate_resource_plan` 仍强制身份一致，且拒绝伪造 pin；既有带实例计划路径回归通过。
- `timeoutSignal` 在现代环境行为与原生一致，仅补旧环境回退；风险低。
- CI 修复均为测试隔离/文案同步，无安全门禁放宽。
- 合并前须按当时 main 再审共享文件（`api_setup.py`、`deployment.py`、导演前端），避免与其他 worktree 冲突。

## 6. 交付物

- 代码分支 `feature/h3-v2-final-closeout-20261114` 与独立 PR（见交付说明）。
- 本报告 + `docs/H3_V2_T01_T24_ACCEPTANCE_20261114.md`（T01–T24 逐项 + 场景 A–F）。
- 本机测试证据 `.local-data/v2-closeout/`（Git 忽略，不进安装包）。