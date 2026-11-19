# Shine AI 管家 V3.1 本地验收（任务级一次授权）

> 本地 `main` 直接开发，不建分支、不建 worktree、不合并文档分支（仅复制方案文件）。
> 本报告只写有证据的结论；未实测项在 §9「未完成事项与验证边界」明确标注。

## 1. 基线

| 项 | SHA / 值 |
|---|---|
| 本地 `main`（开发前） | `ae3cd990f64b1b22d24657a79b41417d23831c2c` |
| 远端 `main`（`git fetch origin main` 后） | 同上（无差异） |
| 远端方案分支 | `origin/docs/shine-copilot-v31-task-authorization-20261119` = `c9bd2f7831ad1cf5b276549c5e7156c1649c6ed1` |
| 方案文件来源 | 该分支唯一文件 `docs/SHINE_AI_COPILOT_V31_TASK_AUTHORIZATION_CONTRACT.md`（`git restore --source`，未 merge） |
| 开发前工作区 | 干净；仅 `.trae/` 未跟踪；`stash@{0}` 为已合并的历史 WIP，未触碰 |
| 本地 `main`（开发后） | 见 §10 |

未执行任何 `git reset --hard` / `git clean -fd` / 强制覆盖。

## 2. 架构落点（一套事实、一个裁决、一个执行者）

| 关注点 | 唯一落点 | 说明 |
|---|---|---|
| 任务授权事实 | `SupportDelegation.payload["grant"]`（`shine.copilot.task-grant.v1`） | 原表加列：`grant_id / grant_digest / expires_at / budget_version` |
| 授权写入口 | `shine/support/grants.py` | 创建、预算 CAS、工具调用记账、暂停、撤销；不含任何业务执行 |
| 自主执行入口 | `shine/support/actions.py::execute_under_grant` | 只做「授权检查 + 调用 `preview_action` + 调用 `approve_action` + 回读」 |
| 子动作台账 | `SupportAction`（加 `grant_id / tool_call_id / grant_tool_key`） | `grant_tool_key` 唯一索引保证一个工具调用只落一个动作 |
| 能力目录 | `copilot/declarations.py::WRITE_ACTIONS` | 新增 `resource_install_plan`、`workflow_graph_register` |
| 参数合同 | `copilot/params.py`（从原 Pydantic 派生） | 修 `StrEnum` 无法接受 JSON 字符串的既有缺陷 |
| 模型协议 | `llm/tool_calls.py` + provider `complete_with_tools` + gateway `complete_tools_for_role` | 原生 tool calling 与结构化降级两条通道 |
| 自主循环 | `copilot/tool_loop.py` | 步数 / 墙钟 / 工具次数 / 无进展四道上限 |
| 成功裁决 | 原执行者：`verification.outcome`、`InstallationPlan.status`、`WorkflowProfile.validated_against`、`/setup/verdict` | 模型文本不参与判定 |
| 前端 | `components/TaskGrantCard.tsx` + `ShineCopilot` 入口 | 一张授权卡 + 六段状态 + 技术细节折叠 |

## 3. 新增与修改文件

**新增（后端）**

- `backend/shine/copilot/task_grant.py` — 合同纯函数层（权限、范围、预算、意图校验）
- `backend/shine/copilot/tool_loop.py` — 有界自主循环
- `backend/shine/copilot/tool_registry.py` — 由声明派生模型工具菜单
- `backend/shine/support/grants.py` — grant 唯一写入口
- `backend/shine/llm/tool_calls.py` — 工具协议与降级契约
- `backend/alembic/versions/f180f0010007_task_grant.py` — 原表加列 + 唯一索引

**新增（前端 / 文档 / 测试）**

- `frontend/src/components/TaskGrantCard.tsx`
- `frontend/src/test/taskGrantCard.test.tsx`
- `docs/SHINE_COPILOT_V31_BASELINE_AUDIT.md`
- `docs/SHINE_COPILOT_V31_ACCEPTANCE.md`（本文件）
- `backend/tests/test_copilot_task_grant_v31.py`
- `backend/tests/test_copilot_grant_execution_v31.py`
- `backend/tests/test_copilot_tool_loop_v31.py`
- `backend/tests/test_copilot_llm_tools_v31.py`
- `backend/tests/test_copilot_source_zero_write_v31.py`

**修改**

| 文件 | 改动 |
|---|---|
| `backend/shine/support/models.py` | `SupportDelegation` 加 4 列 + 索引；`SupportAction` 加 3 列 + 唯一索引 |
| `backend/shine/support/actions.py` | 新增 `execute_under_grant` / `read_under_grant` / `_grant_receipt` / `_bind_grant`；`_preflight` 与 `approve_action` 增加两种新能力的预检与分派；`refresh_action` 增加两条回读分支；`_target_fingerprint` 增加两条事实分支 |
| `backend/shine/support/agent.py` | 模型继承（`_copilot_profile`）；系统提示补充「授权内可调用已注册能力」 |
| `backend/shine/copilot/declarations.py` | 新增 2 个能力声明 |
| `backend/shine/copilot/params.py` | `_coerce_enum`：JSON 字符串 → `StrEnum`（形状校验仍严格） |
| `backend/shine/llm/gateway.py` | `ROLE_INHERITANCE`、`inherited_profile`、`profile_for_role_or_inherit`、`complete_tools_for_role` |
| `backend/shine/llm/openai_compatible.py` / `anthropic.py` | `complete_with_tools`（复用各自传输与错误分类） |
| `backend/shine/llm/role_catalog.py` | 管家角色描述更新 |
| `backend/shine/api_support.py` | 6 个 task-grant 端点；`/capabilities` 暴露授权契约 |
| `backend/shine/support/generated/copilot_contracts.json`、`knowledge_manifest.json` | 重新生成（16 个写能力 / 22 条知识 / 426 条已发现路由） |
| `frontend/src/components/ShineCopilot.tsx` | 一次授权入口 + 卡片渲染 |
| `frontend/src/styles.css` | 授权卡片样式 |
| `AGENTS.md` | 追加 V3.1 十二条硬约束 |
| `backend/tests/test_alembic.py` | `HEAD_REVISION` → `f180f0010007` |
| `backend/tests/test_support_agent.py` | 能力集合断言加入 2 个新能力（保留「无 shell / 无源码写入」） |

## 4. 删除 / 收束的重复链路

1. **移除 6 个「有名字、无合同」的假能力**：`resource_install_execute`、`resource_reconcile`、
   `node_package_plan`、`node_package_execute`、`runtime_configure`、`test_inference`、
   `workflow_graph_validate` 已从权限映射中删除（节点安装由 `setup_repair` 覆盖）。
   授权预览不再可能出现「列了却执行不了」的条目。
2. **未新建**：AI 下载器、AI 安装器、AI 节点编辑器、第二套工作流表、第二套授权表、
   第二套能力目录、第二套环境裁决、第二套成功判定。
3. **删除死代码**：本轮初稿的 `expires_at_iso` / `canonical_json` / `dumps` /
   `transcript_digest` / `read_only_capabilities` / `open_steps` 全部移除。
4. **业务幂等复用**：同名同图的工作流注册复用既有 `WorkflowProfile`（`reuse_existing_profile_id`），
   不产生无限副本。

## 5. 三原则审查

| 原则 | 证据 |
|---|---|
| **一套事实** | 授权只有 `SupportDelegation.payload["grant"]`；预算只有 `payload.grant.usage`；子动作只有 `support_actions` 行；环境裁决只有 `/setup/verdict`；工作流裁决只有 `WorkflowProfile.validated_against`；安装裁决只有 `InstallationPlan` + 安装回执。`test_copilot_source_zero_write_v31.py::test_the_grant_catalog_and_the_declarations_are_one_set` 机器校验「权限映射 ⊆ 声明」。 |
| **一个裁决** | 子动作是否允许：`execute_under_grant` 内单一判定链（actor → 状态 → 合同 → 权限 → 范围 → 预算 → 原 preview/approve）。是否成功：`_satisfied()` 只读原执行者 `verification`。模型无判定权（`test_finishing_without_a_receipt_is_not_success`）。 |
| **一个执行者** | 唯一自主写路径 `execute_under_grant` → `preview_action` → `approve_action` → 原执行器（`setup_preparation.enqueue` / `create_workflow_profile` / `create_resource_install_plan` / `release_managed_processes_guarded`）。`grants.py` 源码级断言不含 `preview_action` / `approve_action` / `LocalInstaller` / `create_workflow_profile`（`test_the_grant_store_holds_no_business_execution`）。 |

## 6. 源码零修改运行时保护

`backend/tests/test_copilot_source_zero_write_v31.py`（9 条）机器化断言：

1. 权限映射与能力声明是同一集合；
2. 每个 executor 都是既有业务入口（白名单模块），且名称不含 `write_source` / `patch` / `run_command` / `install_script`；
3. 每个 executor 真实存在且可调用（或为模块）；
4. `ACTION_SPECS` 里不存在 `shell` / `git` / `pip_install` / `write_source` / `write_node_source` / `bypass_gate`；
5. 任何工具 schema 都不暴露 `command` / `script` / `patch` / `source_path` / `target_path` / `code`，且 `additionalProperties=false`；
6. 即使勾选全部能力组，`modify_source_code` / `arbitrary_shell` / `arbitrary_http` / `delete_user_assets` 仍为 `false`；
7. `task_grant.py` 源码不含 `subprocess` / `os.system` / `shutil` / `open(` / `eval(` / `exec(`；
8. `tool_loop.py` 同上（无 `eval/exec/subprocess`）；
9. `grants.py` 不调用任何业务执行函数。

## 7. 测试结果

（见 §7.1–§7.4，全部为本地实跑）

### 7.1 L1 合同与协议（新增）

- `test_copilot_task_grant_v31.py` — 24 条
- `test_copilot_llm_tools_v31.py` — 9 条
- `test_copilot_source_zero_write_v31.py` — 9 条

### 7.2 L2 执行链与循环（新增）

- `test_copilot_grant_execution_v31.py` — 11 条
- `test_copilot_tool_loop_v31.py` — 15 条
- `frontend/src/test/taskGrantCard.test.tsx` — 6 条

### 7.3 既有回归

- `test_support_agent.py` / `test_support_copilot_v2.py` / `v21` / `v3` / `test_local_actions.py` / `test_support_memory_v22.py`
- `test_alembic.py`
- `frontend` 全量：`npx vitest run --no-file-parallelism` → **85 个文件 / 441 条全部通过**
  （含 `shineCopilot.test.tsx` 19 条、`copilotSettings.test.tsx` 8 条、`taskGrantCard.test.tsx` 6 条）

### 7.4 后端全量（固定 8 分片并行）

命令（仓库既有脚本 `run_backend_shards.ps1`，`WorkRoot` 必须非系统盘）：

```
run_backend_shards.ps1 -WorkRoot F:\ClaudeWorkSpace\.pytest-v31
```

结果：

```
shards=8 files=280 workroot=F:\ClaudeWorkSpace\.pytest-v31
chunk-0: clean (exit 0)
chunk-1: clean (exit 0)
chunk-2: clean (exit 0)
chunk-3: clean (exit 0)
chunk-4: clean (exit 0)
chunk-5: clean (exit 0)
chunk-6: clean (exit 0)
chunk-7: clean (exit 0)
shards_with_failure_markers=0
```

- 280 个测试文件，约 2,400 条用例，**8/8 分片 exit 0 且无 `FAILED`/`ERROR`**（26 分钟）。
- `basetemp` 由脚本自动清理（`workroot now: 1 dirs`）。

**并行执行的三个前提（本轮实证，缺一即整片假失败）**：

1. `NO_PROXY` / `no_proxy` 置空——宿主值含 `[::1]` 会让 httpx 拒绝建客户端，环回真实 HTTP 用例整片失败；
2. `PYTEST_DEBUG_TEMPROOT` 置空 + 每片独立 `--basetemp`——共享编号目录会被并发进程互相清理，表现为 `SystemExit: 1`；
3. `WorkRoot` 必须在非系统盘——否则 `require_storage` 按产品规则返回 `CHOOSE_DEPLOYMENT_DIRECTORY_OUTSIDE_SYSTEM_DRIVE`。

（手工手写 8 进程循环时曾因第 1、2 点得到 126 次 `SystemExit: 1` 的假失败，单跑相同文件全过；改用仓库脚本后 8/8 clean。）

## 8. 方案验收矩阵对照

| ID | 场景 | 结果 | 证据 |
|---|---|---|---|
| GRANT-01 | 授权按钮连点 / HTTP 重放 | PASS | `test_authorize_twice_over_http_returns_one_grant`（同一 grant_id，第二条 `created=false`，库中 1 行） |
| GRANT-02 | 授权后新增缺失权重，自动推进 | PARTIAL | 预算/范围/幂等已覆盖（`test_budget_reservation_is_monotonic_and_bounded`）；真实 ComfyUI 上「发现新缺件→下载」需真机，见 §9 |
| GRANT-03 | 缺少受审节点 → 自动安装并 live 证明 | PARTIAL | `setup_repair` 走原链路（`test_support_copilot_v3` 覆盖），本轮未在真机装新节点包 |
| GRANT-04 | 不受审随机节点包 | PASS（结构性） | `PackageSpec` 强制 `NODE_COMMIT_REQUIRED`；AI 无包声明能力，`test_no_capability_can_be_turned_into_a_shell_or_a_patch` |
| GRANT-05 | 臆造 class_type / 字段 | PASS | `test_graph_with_an_invented_node_is_refused_before_registration`、`test_registration_without_positive_prompt_mapping_is_refused`（均未落库） |
| GRANT-06 | 已登记但生产仍用另一张图 | PARTIAL | 注册链路 pin `graph_sha256` 与 `validated_against`；「实际被生产选中」需真机拍摄验证 |
| GRANT-07 | 下载成功但节点加载失败 | PASS（语义） | `REGISTERED_NOT_EXECUTABLE` 归入停止原因，不报成功（`_must_stop`） |
| GRANT-08 | 渲染占用 GPU | PASS | 全部子动作经 `_action_occupancy`；`BUSY` → `BLOCKED` 且不执行 |
| GRANT-09 | 外部 adopted 只读实例 | PASS | 安装身份纳入 grant scope；越界 `SCOPE_EXCEEDED` |
| GRANT-10 | 中途撤销 / 过期 | PASS | `test_revoke_stops_new_writes...`、`test_a_revoked_grant_cannot_start_a_new_step`、`test_a_revoked_grant_stops_the_loop_before_any_call` |
| GRANT-11 | Worker / 浏览器重启 | PASS（授权侧） | grant 全量落库；`grant_tool_key` 唯一索引使重放收敛（`test_the_same_tool_call_is_one_action`） |
| GRANT-12 | UNKNOWN / SUPERSEDED / 记录丢失 | PASS | `_must_stop` 覆盖 `UNKNOWN`/`NEEDS_REVIEW`/`SUPERSEDED`；既有 `refresh_action` 保留 |
| GRANT-13 | 预算耗尽 / 范围漂移 / 合同升级 | PASS | `GRANT_BUDGET_EXCEEDED`、`SCOPE_EXCEEDED`、`GRANT_CONTRACT_CHANGED` 各有测试 |
| GRANT-14 | 试图改源码 / Shell / pip | PASS | `test_copilot_source_zero_write_v31.py` 9 条 |
| GRANT-15 | 未单独设置 support_agent | PASS | `test_preview_inherits_the_configured_text_model`、`test_an_unrouted_copilot_inherits_the_configured_text_model` |
| GRANT-16 | 只登记成功、渲染失败、verdict 仍阻滞 | PASS | 回执区分 `REGISTERED_NOT_EXECUTABLE` 与 `VERIFIED`；`production_ready` 恒为 false |
| GRANT-17 | 用户机 2026-11-18 配置变化 | 未验证 | 需用户机台账，见 §9 |
| GRANT-18 | 用户已有完整环境 | PASS（工作流侧）/ PARTIAL（环境侧） | `test_two_tool_calls_for_one_workflow_name_reuse_the_business_identity`；环境侧由 `setup_reconcile` 只读核验，真机 0 下载未实测 |

## 9. 未完成事项与验证边界

1. **真机 GPU 出片未验证**：本轮全部为开发机上的模拟渲染器（`live_object_info` 为测试替身，
   仅用于证明「注册 → live 节点合同校验」这条原链路可跑通，**不是**真实 H3 出片证据）。
2. **GRANT-02 / 03 / 06 的真实下载与生产选中**需要实际 ComfyUI + 权重 + 拍摄任务；
   本轮只验证了「授权内可发起、预算内可冻结、范围外即拒绝」的结构。
3. **GRANT-17**（用户机 `ACTIVE_RUNTIME_CONFIGURATION_CHANGED`）未验证：没有该机器的现场台账，
   且不得用开发机结论代替。
4. **不得**将本报告解释为对 RTX 4070 / 5090 等其他机器的验证。
5. GitHub CI 因额度限制未作为本轮门槛。

## 10. 最终提交

见本报告随附的本地提交（`git log -1 --format='%H %s'`）。
