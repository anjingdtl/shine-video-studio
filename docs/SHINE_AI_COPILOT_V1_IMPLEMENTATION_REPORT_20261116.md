# Shine AI 管家（Shine Copilot）V1.0 工程实施报告

- 日期：2026-11-16
- 建设依据：`docs/SHINE_AI_COPILOT_SUPPORT_AGENT_V1_BUILD_PLAN_20261116.md`（方案提交 `d3f6ae56c26a57a65658293ae43f99285bc1c5e9`，本分支 cherry-pick 为 `b8aac6c`）
- 开发分支：`feat/shine-ai-copilot-support-agent-v1`
- 基线：`origin/main` @ `a2ea6af`（Merge PR #53）
- 工作方式：独立 Worktree（当前 `/workspace/.worktrees/shine-copilot`），未触碰 main 工作区与其他分支/Worktree
- 迁移版本：`f180f0010001` → `f180f0010003`（support ledger + 可重试授权 attempt）

## 1. 架构审查结论

本次实施先核对真实代码，再落功能，结论如下：

| 事实/裁决/执行者 | 复用点 | 管家如何接入 |
| --- | --- | --- |
| 唯一环境裁决 | `GET /api/v1/setup/verdict`（`shine/setup_*` / `setup_verdict`） | 证据快照直接调用该函数与接口，不复制判定逻辑；执行后只回读该裁决 |
| 运行态投影 | `/api/v1/setup/runtime`、`/api/v1/setup/summary` | 以只读调用汇总，`summary(discovered=[])` 明确不触发全盘扫描 |
| 统一安装器 | `shine.comfyops.setup_preparation`（TASK_KEY、enqueue） | 受控动作 `setup_repair` 直接 `enqueue` 原准备任务 |
| 对账 | `reconcile_installation` 及 setup reconcile 流程 | `setup_reconcile` 委托原对账入口，不另建调度 |
| 受管进程 | `describe_managed_processes` / `release_managed_processes_guarded` | `managed_release` 仅走原受管释放，外部实例不强停 |
| 任务/Worker | `Job`、`JobAttempt`、`WorkerInstance` 只读投影 | 证据内展示，禁止新建并行任务或重复提交 |
| 工作流注册 | `create_workflow_profile` 用户副本链路 | `workflow_user_copy` 只建用户级副本，内置模板只读 |
| LLM 路由 | `AgentRole` + `llm/role_catalog.py` + 统一 Gateway | 新增可选角色 `support_agent`，不新增 Key/Provider 体系 |
| 前端工作台 | 现有 React 19 单页、hash 路由、主题变量 | 全局挂一个浮动管家，不新增工程设置页，不重置七阶段 |

四项最高原则的落地：

- **一套事实**：`support/context.py` 只采集现有裁决/运行态/资源索引/任务记录，统一脱敏后作为唯一证据快照。
- **一个裁决**：诊断与动作复核均引用 `setup_verdict`；LLM 与前端都无权宣布 READY。
- **一个执行者**：所有写操作都委托既有安装器/对账/受管释放/工作流副本接口，管家不自带执行器。
- **代码零修改**：工具注册表中不存在 shell、文件写入源码、Git/Patch/Issue 工具；运行时只能写受控报告目录。

## 2. 交付物清单

### 2.1 后端新增

| 文件 | 作用 |
| --- | --- |
| `backend/shine/support/models.py` | `SupportSession` / `SupportMessage` / `SupportAction` / `SupportReport` 四张表；动作含 `request_key` + `attempt_no` 与唯一 `request_identity`，消息级联删除 |
| `backend/shine/support/privacy.py` | 密钥、Token、用户目录、私有数据根的统一脱敏 |
| `backend/shine/support/tools.py` | READ / CONFIRM 工具注册表、FORBIDDEN 能力清单、注入意图识别、`MAX_TOOL_STEPS=8` |
| `backend/shine/support/context.py` | 单一证据快照（裁决/运行态/资源/进程/任务）、`evidence_id`、来源标注、TTL、快照 revision |
| `backend/shine/support/occupancy.py` | 组合既有生产/租约/受管进程/素材批次/未决结果，输出唯一占用结论，失败即 fail-closed |
| `backend/shine/support/investigation.py` | 有界多步取证规划器（≤8 步 / 2.5s），仅调用注册表内只读工具，标注证据等级 |
| `backend/shine/support/diagnostics.py` | 确定性规则诊断：UNKNOWN/RECONCILE、BLOCKED、SETUP、VALIDATION、生产忙、失败保持、无法判定等 |
| `backend/shine/support/knowledge.py` | 版本化本地知识库（`shine-support-kb-2026.11.16`），官方联网检索默认关闭、域名白名单、仅引用 |
| `backend/shine/support/reports.py` | BLOCKER/BUG/REQUIREMENT MD 报告；受控目录、文件名白名单、原子写、24h 指纹去重 |
| `backend/shine/support/actions.py` | 预览→授权→委托执行→回读裁决→可回滚；参数白名单；生产/GPU 忙时拒绝；幂等 |
| `backend/shine/support/agent.py` | 会话（30 天）、`support_agent` 路由、最多 8 步工具轨迹、LLM 不可用回退、联网开关 |
| `backend/shine/api_support.py` | `/api/v1/support` REST API，写操作需 Operator 本地动作，Schema `extra="forbid"` |
| `backend/alembic/versions/f180f0010002_support_agent.py` | 幂等创建四表与索引，支持空库与旧库升级 |
| `backend/alembic/versions/f180f0010003_support_action_attempts.py` | 为既有授权行增加 `request_key` / `attempt_no`，回填并保留历史记录 |

### 2.2 后端修改

- `backend/shine/schemas.py`：`AgentRole.SUPPORT_AGENT`。
- `backend/shine/llm/role_catalog.py`：新增可选文本角色「AI 管家」，复用统一 LLM Gateway。
- `backend/shine/main.py`：挂载 `support_router`。
- `backend/shine/models.py`：注册 support 模型。
- 受影响测试：`test_alembic.py`、`test_global_workflow_name_migration.py`、`test_migrations_windows.py`（HEAD 迁移号）、`test_llm_multi_model_routing.py`（角色数 14→15）。

### 2.3 前端新增/修改

- `frontend/src/components/ShineCopilot.tsx`：全局浮动管家（FAB + 面板），页面上下文、提问、诊断卡、证据、受控动作预览/授权/刷新、报告生成与下载、联网开关、会话恢复。
- `frontend/src/App.tsx`：导演模式与素材中心两条渲染路径均挂载管家。
- `frontend/src/styles.css`：沿用既有主题变量的管家样式，移动端全宽、`prefers-reduced-motion` 支持。
- `frontend/src/auditEventCodes.json`：补齐 7 个 `SUPPORT_*` 事件码（与后端 AST 扫描一致）。
- `frontend/src/test/shineCopilot.test.tsx`：7 个组件测试。

### 2.4 API 一览

```
GET  /api/v1/support/capabilities
GET  /api/v1/support/context
GET  /api/v1/support/knowledge
GET  /api/v1/support/preferences
POST /api/v1/support/preferences
POST /api/v1/support/sessions
GET  /api/v1/support/sessions/{id}
POST /api/v1/support/sessions/{id}/messages
GET  /api/v1/support/sessions/{id}/events
POST /api/v1/support/actions/preview
POST /api/v1/support/actions/{id}/approve
GET  /api/v1/support/actions/{id}
POST /api/v1/support/actions/{id}/rollback
POST /api/v1/support/reports
GET  /api/v1/support/reports
GET  /api/v1/support/reports/{id}
GET  /api/v1/support/reports/{id}/download
```

所有响应体不返回数据库绝对路径；报告只返回受控相对展示路径。

## 3. 权限与强制隔离

工具分三层，由服务端注册表裁决，不依赖提示词：

- **READ**：裁决、运行态、资源、进程、任务、知识库、报告生成等只读工具。
- **CONFIRM**：`setup_repair`、`setup_reconcile`、`managed_release`、`workflow_user_copy`。必须“预览 → 用户显式授权（匹配 preview hash）→ 原执行者执行 → 回读权威裁决”。
- **FORBIDDEN**：shell/命令执行、源码与节点源码写入、Git/Patch/Issue、内置模板修改、任意路径写文件、复制凭据。

强制边界：

1. 无任何可写源码或执行命令的工具条目（`test_tool_registry_has_no_shell_or_source_write_capability`）。
2. 动作参数按 kind 允许清单校验类型、长度和枚举；未知参数直接 400。
3. 预览 15 分钟过期，`request_identity` 唯一保证幂等；授权必须携带匹配 hash。
4. 生产任务（ComfyUI SUBMITTING/RUNNING/QC_PENDING、系列 QUEUED/RUNNING）存在时拒绝修复类动作（423）。
5. 执行后只读取原裁决；执行器失败保持 FAILED，不由管家改写。
6. 报告目录固定为 `<data_dir>/support-reports`，文件名白名单 + 父目录校验，拒绝目录穿越。
7. 指令型 LLM 输出被隐藏并记录为拒绝/无动作。

## 4. 诊断与恢复链路

```
页面失败/用户提问
  → 采集受控上下文（page_id / project / job / error_code）
  → build_evidence_snapshot（直接调用 setup verdict / runtime / summary / processes / jobs）
  → 确定性规则诊断（离线可用；UNKNOWN 只建议 reconcile）
  → 本地知识库检索（可选官方联网，仅作证据）
  → 可选 LLM 解释（失败/未配置自动回退，不改变裁决）
  → 诊断卡：类别、严重度、现象、依据、下一步、报告类型、越权警告
  → 受控动作：预览 → 授权 → 原执行者 → 回读裁决
  → 需要改代码：仅生成 BLOCKER/BUG/REQUIREMENT MD
```

## 5. 知识库、联网与隐私

- 本地知识库为纯文本版本化条目，不加载 embedding，不占 GPU。
- 联网检索默认关闭，用户显式开启后才访问官方域名白名单；结果标记 `REFERENCE_ONLY`，不得作为可执行修复指令。
- 外部网页内容、日志、模型输出一律视为证据；注入内容不会提升工具权限。
- 报告与证据统一脱敏 API Key、Bearer Token、用户目录与私有数据根。

## 6. 验证结果摘要

详见《Shine AI 管家 V1.0 验收报告》（`docs/SHINE_AI_COPILOT_V1_ACCEPTANCE_REPORT_20261116.md`）。

- 后端定向：`tests/test_support_agent.py` 39 项全部通过。
- 后端八分片全量（第二轮重新完整执行）：共 2958 项参与，8 个分片全部退出码 0。
- 前端：79 个测试文件 / 405 项 Vitest 全部通过；`tsc -b` + `vite build` 通过。
- Ruff：`shine` 与 `tests` 全部通过。
- 迁移：空库 `alembic upgrade head` 成功（`f180f0010003`）；旧库升级保留授权行并回填 `request_key/attempt_no`；Windows 迁移用例通过。
- 浏览器 E2E：真实 Vite dev + 真实后端（临时数据目录、无 LLM、无 ComfyUI），浮动管家可打开、读取真实裁决、离线回复诊断与证据，证据标签与新工具链正常，控制台无错误。
- 未执行：Windows 真机、真实 ComfyUI/GPU、真实 H3 生成、安装包实机安装、启动器离线诊断（LLM/ComfyUI/GPU 相关项在验收报告中逐条标注）。

## 7. 用户使用说明（简版）

1. 任意页面右下角点击「AI 管家」。
2. 直接提问，或在失败卡片点击「交给 AI 管家诊断」，自动带上页面与任务编号。
3. 查看「结论 / 依据 / 下一步」；证据来自本机真实裁决与任务记录。
4. 若出现配置修复建议，先看预览（是否下载、是否占用 GPU、是否改 ComfyUI），确认后才执行。
5. 执行后以本机裁决为准；管家不会自行宣布恢复成功。
6. 需要改代码的问题，生成 BLOCKER/BUG/REQUIREMENT Markdown 报告，交给开发者。

## 8. 明确边界

- 运行时管家无源码、节点源码、内置模板、Git/命令执行权限。
- 本轮未实现启动器级离线诊断（S29 标 BLOCKED），不伪造该项通过。
- Windows/ComfyUI/GPU 真机未在本环境执行，相关验收不宣称真机 PASS。

## 9. 第二轮收束建设（PR #55 验收修复）

第二轮针对远端验收发现的问题做了以下真实修改，全部提交在原功能分支 `feat/shine-ai-copilot-support-agent-v1`。

### 9.1 P0-01 原执行任务身份与恢复结果强绑定

- `support/actions.py` 以 `original_task_id` 记录执行器返回的唯一任务 ID，并在审计台账写入 `SUPPORT_ACTION_TASK_BOUND`（含任务 ID 与 `TASK_KEY`）。
- `refresh_action()` 不再读取“最新整备任务”的全局状态冒充本次结果：
  - 当前 `TASK_KEY.task_id` 与绑定 ID 不一致且能由审计证明该 ID 曾入队时，结论为 `SUPERSEDED`；
  - 原任务记录缺失或无法对应时返回 `NEEDS_REVIEW` / `UNKNOWN`；
  - `FAILED`、`CANCELLED`、`SUPERSEDED` 保留原始终态语义（`verification.executor_status`），动作状态维持失败或待核对，绝不升级为成功。
- 执行器 `COMPLETED` 后仍需核对**本次恢复目标能力**的权威裁决（`/api/v1/setup/summary#capabilities`）：`setup_repair` 要求目标能力达到 `READY`/`VERIFIED_RENDER`；`setup_reconcile` 要求文件已核验并登记。整机 READY 不能替代目标能力就绪。
- 新增迁移 `f180f0010003`：`request_key` + `attempt_no`，保留既有授权行并回填。

### 9.2 P0-02 统一 GPU / Worker / 生产占用判定

- 新增 `support/occupancy.py`，只组合既有权威来源，不新建调度器：
  - `media.output_layout.active_work_blockers`（任务/序列/整片运行/素材中心渲染任务）；
  - `ResourceLease` 的 `ACTIVE` / `RECONCILE_REQUIRED`（所有模型阶段共用同一 GPU 租约台账）；
  - `describe_managed_processes` 的 `in_flight`；
  - `MaterialRun` 的 `QUEUED` / `RUNNING`；
  - `JobAttempt.reconciliation_state` 的未决外部结果。
- 任一来源读取失败即 `OCCUPANCY_UNKNOWN`，按占用中处理（fail-closed）。
- 预览与授权各自读取一次占用；授权时的第二次读取关闭预览→授权之间的竞态；外部 ComfyUI 永不被管家强停。

### 9.3 P1 授权、预览、幂等与参数合同

- 授权前重新计算证据快照 revision；事实变化则旧预览立即 `EXPIRED`，返回 409 `PREVIEW_STALE`。
- `preview_action` 只复用仍在生命周期内的 `PREVIEWED` / `APPROVED` / `DELEGATED` 请求；失败、取消、过期、拒绝、回滚、执行的旧操作允许发起新 attempt（`attempt_no` 自增），旧授权不再永久阻塞。
- 授权用原子 `UPDATE ... WHERE status='PREVIEWED'` 抢占一次，重复/并发点击只会返回已复用，不会二次执行。
- 执行参数原文存入 `SupportAction.params`，预览与日志只保存脱敏投影；Windows 路径与安装路径不再以 `<本机路径>` 占位符进入执行器。
- 授权身份包含项目、安装路径、源工作流与参数；跨项目预览/授权不复用；私有项目工作流禁止复制到其他项目。

### 9.4 P1 全局诊断交互与故障绑定

- 新增 `frontend/src/copilotContext.ts`：只传项目、任务、尝试、错误码与具名原因，不抓取整页 DOM。
- 会话键改为「项目 + 故障指纹」：切换导演/素材中心与七阶段继承同一诊断；换项目或换故障则分开。
- 失败卡片（生产任务、生产批次、素材逐场、四阶段任务、交付、通用必做块）均可「交给 AI 管家诊断」，自动绑定真实任务/尝试/错误码并只诊断一次；诊断进行中到达的新故障会排队等待，不丢失。

### 9.5 P1 Agent 有界多步取证

- 新增 `support/investigation.py`：确定性规划器按证据决定追加读取任务失败、质检门、工作流合同、安装回执、GPU 占用与本地知识；最多 8 步、2.5 秒预算，工具名与参数完全由服务端白名单决定。
- LLM 只做解释：提示词预算 12,000 字符、输出 `completion_cap=700`；证据充分且用户未要求解释时不强制调用；LLM 失败/401/429/超时自动回到本地规则诊断。
- 每个调查步骤带 `evidence_level`（LOCAL_AUTHORITY / LOCAL_LEDGER / LOCAL_KB / EXTERNAL_REFERENCE）。

### 9.6 P1 真正的联网诊断

- `search_official_web` 现在会检索 GitHub Issues（仅 `comfyanonymous/ComfyUI`、`Comfy-Org/ComfyUI-Manager`、`pytorch/pytorch`）。
- 出站查询只含错误码、固定主题词与清洗后的本机版本，不含用户问题、项目名、路径或密钥。
- 结果带来源、更新时间、适用性判断（`LIKELY_APPLICABLE` / `POSSIBLY_NOT_APPLICABLE` / `UNKNOWN`）与 `REFERENCE_ONLY` 标记；指令型正文被隐藏；域名白名单之外的 URL 被丢弃；10 分钟缓存；断网返回离线参考并降级。

### 9.7 工程报告复核

- 报告头记录页面、项目、任务、尝试、失败阶段、外部核对状态。
- 「已确认事实」与「疑似原因（未经证实）」严格分开；新增「证据来源与等级」表与外部参考。
- 已尝试动作记录执行器状态、复核结论与权威来源。
- `report_root` 与 `read_report` 拒绝目录符号链接、文件符号链接与任何越界路径。

### 9.8 S29 启动器离线诊断

- 远端 `launcher/` 是独立 Rust + Slint 的工程预览，尚未提供稳定的“无后端离线诊断 + Markdown 导出”接口，且与本功能分支无合入关系。
- 本轮不重构启动器、不把其他分支未验收代码合入。S29 继续标 **BLOCKED**，依赖接口与后续要求写入验收报告。

