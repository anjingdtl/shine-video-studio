# 2026-11-03 分镜交接与制作入口修复

基线：`0aa6246`；工作分支：`codex/workbench-crew-handoff-fix`。

## 现场发现

- 用户项目 `b28fcd5c…` 没有正式镜头，只有编剧结果、人物对照提案与风格提案；导演分镜和生产尚未完成。
- 切换导演模式步骤会卸载分镜面板，丢失前端调用状态。
- 素材实体名称为编号，编剧使用 S1–S6；仅上传顺序不能证明图片身份，必须人工确认。
- Vite 热更新后，旧 API 进程拒绝新增 `confirm_characters` 字段。该次 422 是前后端版本不一致，并非模型拒绝。

## 改动

- WEB 新分镜请求在编剧后停到人物确认。编剧可提出 `character_names`，人物对照使用当前编剧产物 ID，展示名图建议，用户确认后才写入并续跑导演。
- 确认不能漏掉人物，也不能将同一人物主素材用于不同人物。提案不自动批准或替换素材。
- 增加读取已有编剧人物对照的入口；界面提示核对它是否属于当前剧本。历史产物不被重写，也不自动触发模型调用。
- 导演模式内切步骤时保留制作面板；不承诺整页刷新、切作品或关闭浏览器后保留内存状态。
- 等待时间独立更新；进度读取有超时和故障说明；未知外部结果锁住重试入口。
- 制作按钮旁列明缺分镜、未勾选、缺方案、模式未就绪和已有序列等原因。0 镜时提供返回分镜的按钮，并写明需点击「应用为正式分镜（人工确认）」。

## 在线修复

仅重启 API 8110。先只读确认无 RUNNING/RECONCILING Agent，再核对实际监听进程的命令、项目数据目录、PID/birth，沿用其环境与工作目录启动新 API。保留 Worker、审片与 Web 进程。

旧 processes.json 的 API PID 已失效，未按旧 PID 盲杀；实际监听进程经过单独核验。原清单另存于本机 `processes.before-crew-api-restart.json`，API 记录更新为新进程身份。

在线 OpenAPI 已包含 `confirm_characters`。向不存在项目发送携带该字段的请求返回 `404 Project not found`，确认通过字段校验且未触发模型调用。

## 验证及边界

- `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_character_binding.py backend/tests/test_storyboard.py backend/tests/test_film_crew.py backend/tests/test_offline_e2e.py -q`：通过。
- 新增人物确认专项：通过。
- `backend/.venv/Scripts/python.exe -m ruff check backend`：通过。
- `npm.cmd test -- --reporter=dot`（frontend）：15 文件、100 项通过。覆盖实际 App 切页后的单次请求与结果保留、0 镜阻塞入口、确认后续跑及等待时间更新；这是 stub UI 回归，不是真实模型验收。
- `npm.cmd run build`（frontend）：通过。
- `backend/.venv/Scripts/python.exe -m pytest backend/tests -q`：退出码 0，全套通过，1 项跳过；保留 Starlette 弃用和 SQLAlchemy 空主键查询警告。
- 浏览器核验未完成：Computer Use 无法可靠确认当前 Windows 浏览器 URL，自动停止。未继续操作浏览器。
- 没有调用付费模型、提交 H3、改变用户主素材、重新校准 VLM、修改冻结生产计划或宣称真实成片验收。

本机日志：`.local-acceptance/crew-handoff-backend.log`、`.local-acceptance/crew-handoff-frontend.log`；不进入分发包。

## 后续：进度必须解释交接，而不只是等待秒数

现场编剧请求 `372cd493…` 返回 HTTP 200/服务商代码 0，模型耗时 216500 ms，已生成六个人物的名字与匹配提案。分镜导演尚未被调用：等待用户确认人物；风格提案的导演调用不等于分镜导演已运行。

此前人物对照加载期间的进度 GET 可能覆盖权威结果，显示「准备下一位 Agent」。现已在链请求返回时立即停止旧轮询，防止这类提示覆盖。

进度区现在独立显示「等待你确认人物，导演尚未启动」，解释继续等待不会自动进入下一步，并提供跳到人物对照的按钮。续跑时复用编剧步骤，完成数不会归零；角色尚未开始与已完成复用也分别标明。

Gateway 以短事务记录 `WAITING_RESOURCE`、`PREPARING_PROVIDER`、`WAITING_PROVIDER`、`RELEASING_PROVIDER`、`VALIDATING_RESULT`；成功记录 `SUCCEEDED`。没有把资源等待伪装为已发送网络请求，没有添加重试或改变审批。模型等待没有虚构内部百分比，界面显示配置的网络等待值、模型名、最近成功查询后台时间。网络等待值不是整个流程的总时限。

专项验证确认独立数据库会话能在 provider 返回之前读到 `WAITING_PROVIDER`，结束后为 `SUCCEEDED`。前端最终 103 项通过，构建与 Ruff 通过；阶段反馈初版的完整后端回归退出码 0（1 项跳过）。全套启动后追加的资源释放阶段细化另通过 Gateway 与阶段持久化专项；随后追加的已确认人物恢复通过全部人物绑定专项。全套离线回归使用 `SHINE_AGENT_STEP_DELAY_MS=0` 去除一般用例的工种间人为等待，专门的间隔用例仍显式设置 250ms 并验证真实间隔；生产默认值未改。

本轮没有新发付费调用，没有改写现场任务或替用户确认人物。API 在确认没有活动 Agent 后单独重启；Windows 浏览器可视核验仍未执行。


### 确认名字后仍不能续跑

现场确认生成了新的 ScriptBreakdown，人物名字已写入，但没有新的 storyboard/generate 请求或导演任务。前端原来先保存名字，再检查文本发送同意与剧本选择；读取旧结果或刷新后这些状态可能缺失，导致写入成功却没有继续。

现在先检查发送同意与剧本选择，在确认按钮旁显示缺项；后端提案通过当前已批准、锁定主素材重新核对既有 character_bindings，返回 already_confirmed。前端可复用该产物直接从导演开始，不重复改名或调用编剧；改动任何名图选择则取消复用标记。发送同意仍必须由用户明确给出，没有替用户勾选。

在线 API 已重启并核验：读取现场已确认产物 0094faf6…，返回 already_confirmed=true、6 位人物。该探针只新增读取提案，没有修改人物、调用 LLM 或提交 H3。前端新增回归覆盖读取已确认人物、缺少同意时禁用、明确同意后只发一次 director 续跑请求且不再调用 character-binding/apply。

最终补充日志：.local-acceptance/agent-progress-full-backend.log、agent-progress-backend.log、agent-progress-gateway-final.log、agent-progress-binding-final.log、agent-progress-frontend.log。
