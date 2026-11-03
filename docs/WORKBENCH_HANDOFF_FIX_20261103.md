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
