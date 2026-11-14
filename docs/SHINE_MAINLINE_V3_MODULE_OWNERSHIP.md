# Shine 主线收束 V3.0｜模块责任与调用图（MODULE_OWNERSHIP）

> 基线：`origin/main@e4a3f198`；实施分支 `fix/shine-mainline-v3-convergence-20261114`。
> 本文档是 V3 收束后的权威责任表：**每个业务事实只有一个权威来源**，入口可以有多个，但都消费同一事实。

## 1. 六条核心责任链

| 责任链 | 唯一权威 | 入口（都消费同一权威） | 明确不做 |
|---|---|---|---|
| 安装计划 admission | `POST /setup/install-plan`（`api_setup.py`，含能力判定/变体资格/候选确认）+ `deployment.create_install_plan`（target_key 去重） | SetupCenter / SetupPanel / RealProductionWizard 三入口全部走它；专业路由 `POST /comfyops/install-plans` 的 **local H3 闭包已改为具名拒绝**（`H3_INSTALL_PLAN_USES_SETUP_ADMISSION`），只保留 fake 演练、审片权重与自定义包 | 不再有第二套 H3 准入 |
| 下载传输与校验 | `comfyops/installer.py`（白名单/固定 SHA/续传）+ `installation_worker.py`（唯一执行器，`worker.py:4263` 唯一调用点）+ attempt 台账 | 前端 `setupExecution.ts` 按 attempt_id 重附着；暂停/续传/取消走 `/comfyops/installation-attempts/{id}/*` | 不开第二个下载器/浏览器直下 |
| 下载预检 | `POST /setup/connectivity/for-plan`（`connectivity.preflight_for_components`，按计划组件闭包推导来源） | SetupCenter 每能力各自预检；`GET /setup/connectivity` 保留为四站诊断视图，**不作准入** | 不把无关站点失联写成全局拒绝 |
| 运行时生命周期 | `runtime_orchestrator/*`（API 提交意图；Worker `advance_control_operations` 唯一执行）+ `comfyops/scheduler.py ResourceScheduler`（GPU 互斥租约） | UI 只发 operation、读回执；`/runtime-orchestrator/status` 唯一快照 | UI 不抢端口、不自启 ComfyUI、不另设 GPU 锁 |
| 导演旅程投影 | `director/journey.py compute_journey`（7 阶段，`GET /projects/{id}/journey` 唯一出口，支持 `package_id/run_id` scope） | `App.tsx` 的 journey 与 `/director/overview` **带同一 package scope、同一批刷新触发**；步骤条完成态只来自 journey | 不建第二套导演队列/阶段机 |
| 生产执行 | `production/runner.py advance_runs`（Worker 唯一驱动）→ Series → Job/Attempt → QC → `media/delivery_engine.run_pending_deliveries` | 导演页/专业页只读进度与下一步 | 浏览器/脚本不直接驱动执行 |

## 2. 前端三入口的合同（WP2 收束后）

| 入口 | 角色 | 状态来源 |
|---|---|---|
| `SetupCenter.tsx` | 导演模式主入口：检测→选能力→确认→整备→状态 | `/setup/*` + `/setup/connectivity/for-plan`（分项预检）+ attempt 台账 |
| `SetupPanel.tsx` | 专业视图：审计、对账、镜像/代理、人工导入 | 同一批 `/setup/*`；计划列表 `/comfyops/install-plans?project_id=`（只读）；reconcile 走 grant 机器 |
| `RealProductionWizard.tsx` | 步骤导航/恢复 | 同一批 `/setup/*`；不自带第二套硬件准入/重试/成功判定 |

兼容保留（都有真实调用方，登记不删）：

- `GET /comfyops/install-attempts/{id}`（SetupPanel reconcile 用）与 `GET /comfyops/installation-attempts/{id}`（setupExecution 轮询用）：同一张表的两个读名，语义相同。
- `POST /comfyops/install-plans`：专家诊断/自定义包入口；**local+H3 闭包被拒绝**，fake/审片/自定义包照旧。

## 3. 能力状态词汇（WP1-B 收束后）

| 事实 | 词 | UI 文案 | 不是什么 |
|---|---|---|---|
| 安装 attempt 终态、文件逐字节核验 | `FILES_VERIFIED_PENDING_RUNTIME` → 前端 `FILES_READY` | 「文件已核验 · 运行待验证」 | 不是能出片 |
| 运行合同核验（实例/节点/工作流） | `RUNTIME_CONTRACT_READY` / 服务核验+激活 | 「资源已就绪」/「制作环境已就绪」 | 不是真实生成成功 |
| 真实生成证据 | image `RENDER_VERIFIED` / H3 真实 job `quality_verification=real` | 各自的能力页 | 不是人工艺术验收 |
| 人工艺术验收 | `acceptance_status=accepted`（用户动作） | 成片中心 | 永不自动写 |

## 4. Worker 单拍次序（`worker.py run_configured_once`，无第二执行器）

安装队列 → 文本准备（burst 限流）→ runtime 解析 → 控制操作（runtime_orchestrator）→ 审片租约对账 → 过期 job 恢复 → Run 推进（`advance_runs`）→ 交付 → H3 series → 创意/故事任务 → 图片项 → RelayVSR → 审片 → 普通 job。

审计结论（WP4）：`run_pending_installation`、`advance_control_operations`、`advance_runs`、`run_pending_deliveries` 全仓唯一非测试调用点都在 Worker 主循环；`serve()` 是唯一常驻入口，`main() --once` 是同循环单次变体。**没有第二套调度器/安装执行器/交付驱动，因此本轮不做结构性拆分**（行数是风险信号，不是缺陷）。

## 5. Launcher 发行物身份（如实登记，WP2）

- 根目录 `ShineLauncher.exe`（git 追踪）= **0.1.0 原型**：`ShineLauncher-build-info.json` 记 `source_commit f41e609…`、`windows_gui_acceptance: PENDING_LOCAL_ACCEPTANCE`，与当前 main 源码**不一致**，不得当作 V1 正式发行物。
- Launcher V1 源码已合入（`launcher/`），CI `launcher-windows.yml` 仅在 `feat/shine-launcher-impl-20261113` 分支构建并回写 exe；正式 release 工作流模板在 `launcher/packaging/ci/launcher-release.yml`，**未安装**到 `.github/workflows`。
- 本轮**不构建、不签名、不发布**新 exe；签名信任链与 Windows 真人验证未完成前，任何「V1 正式发行」都是 `PENDING`。建议（需仓库管理员授权后执行）：安装 release workflow、启用 tag 触发、配置签名 Secret。

## 6. 治理建议（本轮只登记，不操作仓库设置）

- main 保护：Ruleset 已存在但 required status check enforcement 为 off。建议启用 PR 必经 `L1 quick`、`Frontend build`、`Browser E2E`（按 CI 现有 job 名），禁止直接推 main（已是事实，所有者除外）。
- CI 配额不足时按 AGENTS.md 如实标「未验证」，不伪造全绿。
