# 模拟测评团队（MiniMax Bot）操作手册

建立日期：2026-11-15
平台：MiniMax Bot 插件（`@minimax-bot`）。工具不在默认工具表内，需先 `tool_search` 取回定义，再在 `run_code` 内以 `await tools["mcp__minimax-bot__<name>"](args)` 调用。

## 1. 名册与所有权

| Bot | bot_id | 角色 | 职责 | 可写范围 |
| --- | --- | --- | --- | --- |
| Chief | `bot_a48efeacd5bc`（main） | Shine 模拟测评总控 | 拆任务、派发、汇总、决定缺陷是否进入修复轮、向用户汇报 | 汇总结论；不直接改代码 |
| 小导演 | `bot_9f34d4c2a5de` | 视频创作全旅程测评员 | 导演模式七阶段主线走查 | `docs/TEST_REPORT_*`、`.local-acceptance/` 证据 |
| 美术师 | `bot_b6f37e74112d` | 图片与素材中心测评员 | 素材中心 / 角色绑定 / 图片生成链路走查 | 同上 |
| 修复师 | `bot_ffa031e389fd` | 缺陷定位与修复工程师 | 定位根因、最小改动、带定向回归 | `backend/**`、`frontend/src/**`（**唯一**） |
| 质检员 | `bot_d5c8166508ea` | 回归与验收质检员 | 只读复核 + 全量回归，不合格退回 | `docs/ACCEPTANCE_*` |

群：**Shine 实景测评组**，group_id `grp_e251e930ccd2`，admin=Chief，reply_mode=admin（默认只有管理员回话，用 `@名字` 交接）。

## 2. 派发与协作

- `dispatch_tasks`：批量派发。`request_id` 必填且重试必须复用（相同内容幂等，不同内容报冲突）。返回 `batch_id`/`task_id`，结果自动回到发起聊天。
- `message_bot`：单聊交给某一个成员。`post_group`：单聊里往群里发要求开工，群内用 `@名字` 接力。
- `list_team` / `list_helpers` / `get_tasks` / `cancel_tasks`：名册与任务状态。`create_bot` / `update_bot` / `create_group` / `update_group`：编制与治理。
- 团队记忆：`recall(scope=team)` / `remember(scope=team, key=...)`；更新已有条目必须带 `expected_revision`。现有键：`shine.env`、`shine.team`、`shine.journey`、`shine.rules`。

## 2.1 沟通语言

一律使用**全中文**：群内发言、给用户或总控的回执、报告正文、缺陷描述、总结与结论全部用中文书写，不输出英文句子或英文段落。仅以下保持原样不翻译：代码标识符与文件名、文件路径、命令行与 API 路径、HTTP 方法与状态码、错误码原文（如 `PENDING_WORKFLOW_MAPPING`、`WORKFLOW_SIZE_NOT_MAPPED`、`IMAGE_WORKER_LOST`、`EPISODE_PLAN_NOT_MATERIALIZED`）、产品与模型专名（ComfyUI、H3、Ref2VA、FastAPI、React、SQLite、Playwright、Vitest、Ruff、npm.cmd、uv 等）。

## 3. 轮次编排

| 轮次 | 谁 | 内容 |
| --- | --- | --- |
| R1 | 小导演 + 美术师（并行） | 各自主线走查，只出报告与证据，不改代码 |
| R2 | 修复师（串行，一次一个） | 修已复现且影响面明确的缺陷，必带定向回归 |
| R3 | 质检员 | 只读复核 + 全量回归，不合格原样退回修复师 |
| R4 | Chief | 汇总为四类结论：已实现 / 离线已验证 / 待真机 H3 验证 / 阻塞 |

## 3.1 用户如何直接指挥团队

用户是团队的甲方，不需要通过我（中控会话）转达：

1. **对总控说**：在「Shine 实景测评组」群里或与 Chief 的单聊里直接提要求，例如「让小导演把导演模式七阶段走一遍，缺陷写进 `docs/TEST_REPORT_20261116_DIRECTOR_JOURNEY.md`」。Chief 会拆解、派发、汇总，再把结论回给用户。
2. **点名成员**：群里 `@小导演`、`@美术师`、`@修复师`、`@质检员` 直接派活，或与某个成员单聊。
3. **验收**：成员与总控的产出都是中文报告 + 群内回执；用户可要求任何成员补证据或重跑。

## 3.2 共享环境红线（2026-11-15 事故后定）

- **绝不执行 `npm ci` / `npm install`**。它会先删掉 `frontend/node_modules`；本机在跑的 vite 占用 `esbuild.exe`，Windows 上直接 EPERM 失败，即使成功也会洗掉共享依赖、打断用户与其他 agent 正在运行的工作台。2026-11-15 曾因此把共享 `node_modules` 洗掉一半（vite 整个消失）、8000/5173 工作台掉线，后用 `npm --prefix frontend ci`（194 包）按锁文件还原修复。要装依赖先停下来问人。
- **绝不抢占他人端口**。8010/5180 可能被别的 agent 建设占用；起实例前先 `Get-NetTCPConnection` 查端口，被占用就换 8020/5190，或改用已在运行的实例。
- **起停任何工作栈前先问用户或总控**。

## 4. 隔离运行（成员默认）

- 数据目录：`.local-acceptance/<run-id>-<who>/`；端口：视频线 8010/5180，素材线 8011/5181。**不碰 `.local-data/`**（用户真实项目）。
- 起（**已验证**，复用现有依赖、不重装）：`pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/start_eval_instance.ps1 -DataDir <dir> -ApiPort <api> -WebPort <web>`
  它按 `start_shine.ps1` 的同一口径写 `run/receipts.json`（`backend/uv.lock` 与 `frontend/package-lock.json` 的 SHA256），复用现有 `.venv` 与 `node_modules`；实测 API 8010 / Web 5180 能正常起来（worker 在线、mock 可用、`h3_connected=false`）。
- 停：`backend/.venv/Scripts/python.exe scripts/stop_shine.py --data-dir <dir>`
- **起之前先查端口**（`Get-NetTCPConnection`）：8010/5180 可能被别的 agent 建设占用，被占用就换 8020/5190，或改用已在运行的实例。

## 5. 浏览器实操

- 首选：会话自带的 Browser 工具打开 `http://127.0.0.1:<web>`，可见浏览器里像人一样操作。
- 备选：`uv run --with playwright==1.64.1 python scripts/journey_longform_browser.py <base-url>`（Chromium 装在 `%LOCALAPPDATA%\ms-playwright`；`backend/.venv` 内**没有** playwright，别直接 `python -m playwright`）。
- 纪律：命令行只用于起停服务、只读查日志台账、改代码、跑回归；**禁止用 API 直接改业务状态绕过界面推进流程**。

## 6. 证据与报告

- 截图：`.local-acceptance/<run-id>-<who>/shots/`。
- 报告：`docs/TEST_REPORT_<YYYYMMDD>_<SCOPE>_R<n>.md`；复核：`docs/ACCEPTANCE_<YYYYMMDD>_<topic>.md`。
- 缺陷字段：编号与级别（P0/P1/P2）、症状、编号复现步骤、期望行为、实际行为、页面原始错误码、截图路径、影响面。
- 分级口径：P0 = 正常动作停摆 / 串错执行内容 / 未知提交失去保护；P1 = 可恢复但主要入口恢复失败；P2 = 体验与文案。

## 7. 回归口径（继承 AGENTS.md）

- 后端全量按 8 分片并行：`split -n r/8` 按**文件**均分，命令前必须加 `CODEBUDDY_SAFE_DELETE_ENABLED=0`；判定口径是 8 个分片退出码全 0 且日志无 `FAILED`/`ERROR` 行，不看 pytest 汇总行。
- 前端：`cd frontend && npm.cmd run build`、`npm.cmd test`（vitest 必须 `--no-file-parallelism`）。
- 改 worker / 质量门 / 修复引擎后加跑：`test_offline_e2e.py`、`test_comfy_completion_window.py`、`test_production_series.py`、`test_advisory_queue.py`、`test_comfy_handoff_journal.py`、`test_v13_preparation.py`、`test_h3_compiler.py`。

## 8. 诚实边界

本机 mock provider 可用，但 `h3_connected=false`、`production_mode=BLOCKED`。离线/mock 结果必须标注"离线模拟"，**不得声称真实 H3 渲染或成片验收**；没跑到的阶段写"未运行"，不脑补、不夸大。

## 9. 首轮记录

2026-11-15 R1：batch `batch_b759406ae054`；task `task_3e00068e5cac`（小导演·视频主线）、`task_a697c8ef836a`（美术师·素材与图片主线）。
