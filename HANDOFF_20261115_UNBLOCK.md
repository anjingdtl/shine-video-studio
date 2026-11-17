# 交接提示词 · Shine 视频工作台「解阻滞 + 全流程穿测」续作

> 用法：把下面「== 提示词正文 ==」整段复制给下一个 agent 即可。它自带上下文，不需要额外解释。

== 提示词正文 ==

你在接手 `F:\ClaudeWorkSpace\projects\shine-video-studio`（Shine 视频工作台，本机优先的
FastAPI + React + SQLite 产品）。上一个 agent 做了一轮「解除阻滞点」的修复后被用户叫停，
工作栈已关闭。下面是完整交接。

## 一、用户的原始目标（不要偏离）

用 `agent-browser` skill 打开浏览器，**拟人化**操作本机工作台，走完一条真实链路：
从「文本导入 / 写想法」一直到**出素材、出成片**（1 集、约 6 镜）。沿途**穿越测试**，
每遇到一个 BUG 或阻滞点就**当场修复**，然后**从阻滞点继续往下跑**，直到整条链路走通。
用户已明确授权调用项目内的 LLM（MiniMax）。

用户近三轮的原话（这决定了优先级）：
- 「这三个板块就不能合并到一起，减少用户的阻滞吗？现在根本没办法正常跑通基础配置」
- 「我都不知道哪个地方还有镜头，一直卡在这里没办法迁移目录。能不能解除这些乱七八糟的阻滞点」
- 「关闭工作栈，你不用搞了，换下一个 agent」

**判断标准**：任何时候页面上的结论必须来自后端同一份事实；任何「待处理 / 待核对 / 需要重新配置」
都必须有**当场可点的下一步**，不能只给一句让人自己去找开关的说明。

## 二、环境（Windows，本机）

- 仓库：`F:\ClaudeWorkSpace\projects\shine-video-studio`（PowerShell 7 / `pwsh`）
- 数据目录（当前穿测用）：`F:\ClaudeWorkSpace\projects\shine-video-studio\.local-acceptance\witch-main-20261111`
- 数据库：`sqlite:///.../witch-main-20261111/shine.db`
- 端口：API 8000 / Web 5173 / 监督进程 8790 / 受管 ComfyUI 8188
- 启动：
  ```powershell
  $env:SHINE_DATA_DIR="F:\ClaudeWorkSpace\projects\shine-video-studio\.local-acceptance\witch-main-20261111"
  pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\start_shine.ps1 -ApiPort 8000 -WebPort 5173
  ```
  停止：
  ```powershell
  & backend\.venv\Scripts\python.exe scripts\stop_shine.py --data-dir "F:\ClaudeWorkSpace\projects\shine-video-studio\.local-acceptance\witch-main-20261111"
  ```
  （监督进程不在 `processes.json` 里，要单独停：`Stop-Process -Id <8790 的监听 pid> -Force`）
- 前端热更新可用；**后端改动必须重启整栈**才生效（`start_shine.ps1` 幂等）。
- 浏览器：`agent-browser --session shine open http://127.0.0.1:5173/ ...`
  - PowerShell 里 **`@e12` 必须加引号**写成 `'@e12'`，否则被当成 splat 语法；
  - 同一 session 的页面会掉回 `about:blank`，命令用 `&&` 串在一次调用里；
  - `find text "xxx" click` 常命中同名文本节点而非按钮，优先 `snapshot -i` 拿 ref 再 `click '@eN'`。

## 三、上一轮已改完并**真机验证**的（不要重做）

全部改动见 `AGENTS.md` 末尾「2026-11-15 解阻滞轮」（该节已写清根因与证据）。要点：

1. **三处配置面板同源**：新增 `GET /api/v1/setup/verdict`（后端 `backend/shine/api_setup.py`，
   由 `summarize_production_mode` 同一函数算出）。右上角徽标、导演模式「基础配置」、
   专业模式「设置」现在都读它。**实测三处都是同一句「真实制作已就绪」。**
2. **停泊镜头不再堵死目录迁移**：`REPAIR_PENDING` 从 `output_layout._ACTIVE_JOB_STATUSES` 移除；
   阻断面板现在报 `project_title` / `shot_name`。
3. **失败的「可选审片模型」下载不再冻结整台机器**：
   `runtime_configuration.require_idle_configuration` 新增 `_live_installation_attempts()`
   （失败且不占租约、或计划里全是可选组件 → 不算活工作）；
   `setup_preparation._live_installation_work` 同步收紧。
   *实障*：一次 ASR 下载 `UNTRUSTED_DOWNLOAD_URL` 让服务核验/激活/徽标永久拒绝。
4. **Windows 上 npm 探针**：`comfyops/host_env.py` 改为把整条命令行交给 cmd.exe
   （`_windows_script_command` / `executable_command`）。实测 npm 11.14.1 达标。
5. **文本通道不再空转**：`literary/governor.py` 的 `_gateway_budget()`——非创作类调用一律
   `completion_cap + streaming + reasoning_effort=low`；创作类保留模型思考预算但强制流式。
   `llm/output_budget.py` 同步带 `reasoning_effort`。
6. **重试不覆盖失败证据**：`literary/ledger.py` 查表键加 `task_id`，`error_code` 会清空。
7. **监督进程重启不再永久卡住**：`scripts/shine_supervisor.py`
   - `_run_restart` 的启动器/停止器输出改写**文件**而不是管道（被启动的整栈会继承管道句柄，
     监督进程等不到 EOF，重启状态卡在 starting，之后每次都判「进行中」）；
   - `RESTART_STALE_AFTER_SECONDS=900`：停滞 15 分钟的重启作废重开；
   - `_mark_worker_stopped()`：停 Worker 时立刻把它记成 `stopped`。
   *实测*：重启 18 秒完成并回报「后台已重启」。
8. **前端裸错误码要带出 code**：`frontend/src/api.ts` 的 `api()` 现在会把裸字符串 detail
   按码形状取回 `code` → 页面上「停止 Worker 并继续验证」的恢复入口**终于会出现**。
9. **停 Worker 后要等后端自己的判断**：`stopWorkerAndWaitOffline` 改为等 `/system/status`
   的 `worker_online` 变 false（原来等进程消失，后端还要 30 秒心跳过期）。
10. **一键准备成功后自动恢复 Worker**：`SetupPanel.prepareAll` 调 `startWorker()`。
11. **提示语按来源标记**：`SetupPanel` 的 `say(text, kind, tag)`，只有同来源的提示自己读成功
    才清除（否则别处的失败原因会被别人的成功顺手抹掉）。

### 已跑过的验证

- 后端定向回归：45 例通过
  （`test_output_directory.py` `test_literary_governor.py` `test_literary_transport_and_ledger.py`
  `test_host_tool_resolution.py`）、`test_idle_configuration.py` 11 例、
  `test_supervisor_lifecycle.py` + `test_supervisor_restart.py` 14 例、
  `frontend/src/test/apiError.test.ts` 8 例。
- `npm.cmd run build` 通过（1655 modules）。
- 浏览器实跑：徽标/基础配置/专业设置一致 → 「一键准备 → 停止 Worker 并继续验证 → 核验 READY
  （1281 节点 / 4 套工作流）→ 激活 → 恢复 Worker」整条恢复链跑通。

## 四、当前流程进度（穿测项目 `穿测 · 一集六镜 1115`，project_id=`cef9e171-495d-415b-97bc-b99bc33bbd79`）

七个阶段：**2 / 7 已完成**。

| 阶段 | 状态 |
|---|---|
| 1 创意与剧本 | ✅ 大纲已确认（`b00c7bce`） |
| 2 剧本统筹 | ✅ 1 集 · 风格第 1 版（押井守动画电影参考风格 / 动画 / 1 集 / 每集 30 秒 / 对白平衡 / 少旁白） |
| 3 镜头拆解 | ⛔ **本集卡住**：第 1 场 APPLIED（产出 4 镜），第 2 场 `scriptwriter` 两次 `LLM_FORMAT` → `NEEDS_REVIEW` → 整集 `DIRECTOR_STORYBOARD_EPISODE_BLOCKED`，第 3–6 场 BLOCKED |
| 4 拍摄素材准备 | 未开始 |
| 5 导演包 | 未开始 |
| 6 视频生产 | 未开始 |
| 7 后期成片 | 未开始 |

证据：`GET /api/v1/projects/{id}/director/storyboard/state`
（`episodes[0].blocker = {unit_index:1, status:REJECTED, failed_role:scriptwriter,
failed_task_id:b3a47526-d7cf-462c-b771-07cb5fd40a5f}`）；
`AgentTaskRecord` 里 08:10:49 那条 `scriptwriter NEEDS_REVIEW err=LLM_FORMAT`。

## 五、下一步（按顺序做，别跳）

1. **先回答一个产品问题**：第 2 场被拒时，页面有没有说清「哪一场、为什么、接下来能点什么」？
   这与用户「我都不知道哪个地方还有镜头」是同一类毛病。查 `frontend/src/components/` 里
   镜头拆解页（`#d/produce`）对 `NEEDS_REVIEW` / `DIRECTOR_STORYBOARD_EPISODE_BLOCKED` 的呈现，
   以及后端是否给得出**可执行**的恢复动作（重试该场 / 人工放行 / 改写后重来）。
   缺什么就补什么，并补回归。
2. **把第 3 阶段走完**：解决上面的拒绝后，让 6 场全部落镜（用户要的是 1 集 6 镜）。
   每一场是 5 个分镜工种的链，单场 1–6 分钟；`LLM_FORMAT` 会被自动纠正一次
   （`SUPERSEDED` 后重跑），连续两次才是真拒绝。
3. **继续跑完第 4–7 阶段**：素材准备 → 导演包 → 视频生产（真实 ComfyUI H3 Ref2VA）→ 后期成片。
   每遇阻滞点照旧「定位 → 修 → 继续」，别停在报告上。
4. **补齐回归并重跑**：
   - 后端全量必须 **8 分片并行**（AGENTS.md 固定规则，命令前加 `CODEBUDDY_SAFE_DELETE_ENABLED=0`）。
     上一轮 8 片里 **chunk-3、chunk-6 被中途杀掉，不算数**，必须重跑那两片或整轮。
     仓库根目录有上一轮留下的辅助脚本 `run_backend_shards.ps1`（PowerShell 版分片器，
     直接 `pwsh -File run_backend_shards.ps1` 即可），**用完可以删掉**。
     日志落在 `.local-acceptance/shards/chunk-*.log`。
   - 前端全量 `npx.cmd vitest run --no-file-parallelism`（上一轮 78 个文件里 1 例失败，
     原因是本轮改动引入的提示语互相覆盖，**已修**，但**全量还没重跑确认**）。
   - `npm.cmd run build` 必须过。
5. **每轮改动写进 `AGENTS.md`**（本轮已写在文件末尾「2026-11-15 解阻滞轮」，续作再追加一节）。

## 六、硬约束（踩过就别再踩）

- **Ref2VA only**：本机真实渲染只走 H3 Ref2VA，没有 T2VA。
- **审查/审片/语音核对模型只是辅助**：不得作为生产硬阻滞（AGENTS.md「Product invariants」）。
  本轮已经因此改了两处门禁，别改回去。
- **只读来源必须保持只读**：导入的原文、已确认的大纲不得被静默改写。
- **失败必须具名**：每个失败都要有代码 + 人能看懂的原因 + 下一步，不允许「状态未知」。
- **不许用「假成功」**：mock/Fake 不能把状态标成 READY。
- Windows 下 `python -c` 内联脚本用 PowerShell 引号很容易炸，**探针脚本写到
  `C:\Users\Administrator\AppData\Local\Temp\shine_probe\*.py` 再用 venv python 跑**。

## 七、别动的地方

- `scripts/start_shine.ps1` 的启动/记录逻辑、`stop_shine.py` 的身份核验语义。
- H3 渲染器生命周期、变体契约、显存门槛（`min_free_vram_mb=8000`）、四阶段提示词。
- 已在页面上的未提交改动（`git status` 里 M 的那 20 个文件）是上一轮成果，**别 reset**。
  工作区还有未跟踪目录 `.local-lifecycle-test/`、`.local-lifecycle-test2/`、`.trae/`、
  `frontend/node_modules.broken-20261115/`，不要提交它们。

== 提示词正文结束 ==

（交接文件：`F:\ClaudeWorkSpace\projects\shine-video-studio\HANDOFF_20261115_UNBLOCK.md`；
本轮修复的完整根因与证据：`AGENTS.md` 末尾「2026-11-15 解阻滞轮」。）
