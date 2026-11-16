# Shine AI 管家（Shine Copilot）V1.0 工程实施报告

- 日期：2026-11-16
- 建设依据：`docs/SHINE_AI_COPILOT_SUPPORT_AGENT_V1_BUILD_PLAN_20261116.md`（方案提交 `d3f6ae56c26a57a65658293ae43f99285bc1c5e9`，本分支 cherry-pick 为 `b8aac6c`）
- 开发分支：`feat/shine-ai-copilot-support-agent-v1`
- 基线：`origin/main` @ `a2ea6af`（Merge PR #53）
- 工作方式：独立 Worktree（`/shine-copilot-wt`），未触碰 main 工作区与其他分支/Worktree
- 迁移版本：`f180f0010001` → `f180f0010002`（AI copilot support ledger）

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
| `backend/shine/support/models.py` | `SupportSession` / `SupportMessage` / `SupportAction` / `SupportReport` 四张表；动作含唯一 `request_identity`，消息级联删除 |
| `backend/shine/support/privacy.py` | 密钥、Token、用户目录、私有数据根的统一脱敏 |
| `backend/shine/support/tools.py` | READ / CONFIRM 工具注册表、FORBIDDEN 能力清单、注入意图识别、`MAX_TOOL_STEPS=8` |
| `backend/shine/support/context.py` | 单一证据快照（裁决/运行态/资源/进程/任务）、`evidence_id`、来源标注、TTL、快照 revision |
| `backend/shine/support/diagnostics.py` | 确定性规则诊断：UNKNOWN/RECONCILE、BLOCKED、SETUP、VALIDATION、生产忙、失败保持、无法判定等 |
| `backend/shine/support/knowledge.py` | 版本化本地知识库（`shine-support-kb-2026.11.16`），官方联网检索默认关闭、域名白名单、仅引用 |
| `backend/shine/support/reports.py` | BLOCKER/BUG/REQUIREMENT MD 报告；受控目录、文件名白名单、原子写、24h 指纹去重 |
| `backend/shine/support/actions.py` | 预览→授权→委托执行→回读裁决→可回滚；参数白名单；生产/GPU 忙时拒绝；幂等 |
| `backend/shine/support/agent.py` | 会话（30 天）、`support_agent` 路由、最多 8 步工具轨迹、LLM 不可用回退、联网开关 |
| `backend/shine/api_support.py` | `/api/v1/support` REST API，写操作需 Operator 本地动作，Schema `extra="forbid"` |
| `backend/alembic/versions/f180f0010002_support_agent.py` | 幂等创建四表与索引，支持空库与旧库升级 |

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

- 后端定向：`tests/test_support_agent.py` 25 项全部通过。
- 后端八分片全量：共 2937 项参与；7 个分片退出码 0，1 个分片因前端事件码表漏 `SUPPORT_*` 失败；补齐后该用例通过（详见验收报告）。
- 前端：79 个测试文件 / 403 项 Vitest 全部通过；`tsc -b` + `vite build` 通过。
- Ruff：`shine` 与 `tests` 全部通过。
- 迁移：空库 `alembic upgrade head` 成功；旧库与 Windows 迁移用例通过。
- 浏览器 E2E：真实 Vite dev + 真实后端（临时数据目录、无 LLM、无 ComfyUI），浮动管家可打开、读取真实裁决、离线回复诊断与证据，页面不被遮挡。
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
