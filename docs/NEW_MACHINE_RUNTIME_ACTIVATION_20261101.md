# 运行配置持久激活

目标仍为完整新机 PASS。本轮补齐向导生成配方与常驻 API/Worker 采用配方之间的交接。激活不代表服务、模型或影片验收通过。

## 当前行为

- 「生成受管配置」在现有 SQLite 的 AppSetting 中保存候选身份，并写 `SHINE_SETUP_RECIPE` 草稿路径；不再写入可自动启用的渲染模式或 managed recipe 环境变量。
- 「确认并激活运行配置」要求本机操作员认证、当前项目和生成时返回的配方哈希。实例必须有匹配的非 Mock 完成安装凭证、固定节点和完整模型家族绑定，并保持停止。已有未完成任务、生产系列、未对账安装或 ACTIVE/RECONCILE_REQUIRED 租约会阻止切换，过期租约也不被当作已释放。
- 候选及激活记录绑定配方、实例负载、目录清单，以及 Python 入口、ComfyUI 主入口、适配器、依赖锁、审片 manifest 和服务入口等小文件身份。不在轮询中重复读取模型权重。
- API 状态、Comfy 配置、Job/系列创建和 Worker 每轮从同一数据库解析生效配置。启动脚本留下的 Mock 参数不会盖过已确认的持久激活。历史任务和其 provider 不被改写。
- 已激活身份变化时，状态报告 `unavailable` / `INVALID_REQUIRES_REVIEW`，Worker 在领取或恢复工作前阻断，不退回 Mock 或旧端口。操作员接口仍使用启动时认证配置，可以重新生成并确认修复后的配方。
- `_managed_recipe` 在实际读取时再次校验确认过的图哈希。原有手动环境配置路径仍兼容；新机向导使用持久激活路径。
- 同库已经激活真实配置时，禁止用 offline simulation 模式读取该配置，避免将真实数据带入模拟执行。

复用现有 AppSetting、审计记录和任务/资源表，无新增数据库、队列或迁移。生成和激活都在现有 SQLite writer 锁下检查空闲条件；生成配置也不能绕过未完成任务保护。

## 本轮验证

55 项定向合同通过，日志 `.local-acceptance/new-machine-goal-20261101/runtime-activation-final-targeted.log`，包括旧新机/首启合同、Worker 状态和离线 E2E。

其中新增 14 项覆盖认证与哈希确认、跨 Settings 对象/下一轮 Worker 读取、配方/审片清单/入口/依赖锁/实例改变时阻断、过期资源、队列未清空、Fake/缺失安装凭证及 offline simulation 边界。测试中的安装凭证和模型目录是明确标注的合成合同数据，不能当作真实安装验收。

一项真实 OS 常驻 Worker 测试验证：先以 Mock 启动，在同一数据库通过 API 激活，随后改变已绑定入口文件；原进程进入 `configuration_blocked`，恢复原字节后同一进程回到 idle，全程没有 Job、模型加载或渲染。测试同时确认只登记一条 Worker 记录。

首次真实进程测试暴露旧启动入口会先登记 `starting` Worker，再在 `serve` 登记第二条记录的问题；已将常驻入口直接交给 `serve`，`--once` 保留自己的单次身份。原始失败日志 `runtime-activation-real-worker.log` 和修复后定向日志分别保留。更早的 `queued_job` 用例漏传 style_id 为测试构造错误，补齐后保留队列阻断断言并通过。

全目录 Ruff、TypeScript/Vite 已通过。完整后端结果和浏览器确认按钮合同在当前测试记录中单独登记，不沿用上一轮 1245 项作为本轮结果。

最终完整回归：`uv run --project backend --locked pytest backend/tests -q`，**1259 passed / 1 skipped / 2 warnings，exit=0**，日志 `full-suite-runtime-activation-marker-final.log`，数字从静默日志状态符统计。唯一 skip 为门控的真实 ComfyUI 探测；Starlette/AnyIO 与 SQLAlchemy 两项既有警告保留。监督器文件另有 47 项定向通过，日志 `renderer-ready-marker-fixed.log`。本轮包含旧离线 E2E、film crew、V1.3、Worker 和生产系列完整合同。

浏览器确认按钮、项目隔离和提交的项目/配方哈希通过 `scripts/verify_setup_web.py` 验证，日志 `setup-runtime-activation-browser-final.log`，截图 `runtime-activation-ui-contract.png`。浏览器中的 runtime 响应和最终激活请求显式拦截，因此仅为 UI 合同，不能冒充真实激活；真实 API/常驻 Worker 行为由上述独立合同覆盖。初次因后端环境未安装 Playwright 而退出；临时下载 1.64.1 又遇网络超时，随后复用本机既有 Playwright 1.62.0 与 Edge 验证通过，失败日志保留，没有为测试修改项目锁文件。

第一次完整后端回归为 1258 passed / 1 skipped / 1 error，exit=1：监督器测试的 `port.txt` 已创建但尚未写入内容，`int('')` 抛出 ValueError，原日志 `full-suite-runtime-activation-final.log` 保留。将测试服务的就绪文件改为同目录临时文件写完后原子替换，并在 setup/teardown 的 finally 中关闭自有进程；原有断言与 10 秒超时不变。监督器定向与后续完整重验结果单独记录。

## 未完成

审片服务首启、实时身份/能力探测与路由自动配置仍需闭环，L4 保持 PENDING。完整物料下载、真实模型加载、真实 H3 生产系列及最终影片验收尚未完成，整体保持 PARTIAL。激活没有自动晋升资产、冻结音色/风格、批准工作流或放行质量门。

继续核对代码已确认下一断点：生成配方的 renderer.instance_id 是持久安装实例的 ID，Worker 将它直接传给 start_managed_renderer；该函数登记运行时 payload 时拒绝同 ID、不同 payload 的已有实例（COMFY_STARTUP_INSTANCE_CHANGED），且 startup intent 对同一 ID 只允许记录一次。因此必须在同一数据库内绑定安装身份与每次运行身份，覆盖首次和后续生产的验证；不能删除安装证据或旧 intent 规避冲突。本轮激活合同不证明这一步已经可用。
