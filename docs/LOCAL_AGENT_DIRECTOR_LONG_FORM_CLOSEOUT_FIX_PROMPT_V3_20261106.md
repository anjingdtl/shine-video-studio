# 全片持续生产收尾修复与续建提示词 V3

- 日期：2026-11-06。
- 当前核实基线：`main / 3f2705396c32c266b912a729f0c7a06ffd6b86c3`，执行时必须重新核对。
- 本版作为后续执行入口；保留 V1/V2、历次报告与原始证据，不覆盖历史结论。
- 独立验收事实：[3f27053 验收报告](ACCEPTANCE_DIRECTOR_LONG_FORM_3F27053_20261106.md)。
- 完整产品范围：[建设方案](DIRECTOR_WORKFLOW_AND_LONG_FORM_PRODUCTION_PLAN_20261105.md)。

把下面代码块全文交给本地 Agent，或让它完整读取本文件并立即执行。

```text
你现在接手 Shine Video Studio 的收尾修复和后续建设。请直接修改代码、完成必要集成、运行回归并留下可独立复验的证据，不要只提交分析、任务清单或设计稿。已有实现应保留并修复，避免无关重写。

工作目录：F:\ClaudeWorkSpace\projects\shine-video-studio
当前已核实提交：main / 3f2705396c32c266b912a729f0c7a06ffd6b86c3。
核心目标：用户事先确认全片剧本、素材、风格、分集和执行政策后，从 Web 启动一次，由同一常驻 Worker 持续完成全部选定镜头、逐集剪辑与交付；关闭浏览器不影响生产。不能停留在三段视频、手工推进批次或测试脚本代替后台执行的状态。

一、先核实现场，保护已有工作

1. 读取当前 AGENTS.md，并按其中顺序读取 V1.0、首轮开发约束、V1.1 及当前补充规则。完整读取：
   - docs/DIRECTOR_WORKFLOW_AND_LONG_FORM_PRODUCTION_PLAN_20261105.md
   - docs/ACCEPTANCE_DIRECTOR_LONG_FORM_3F27053_20261106.md
   - docs/LOCAL_AGENT_DIRECTOR_LONG_FORM_CLOSEOUT_FIX_PROMPT_V2_20261106.md
   - docs/ACCEPTANCE_DIRECTOR_LONG_FORM_CLOSEOUT_20261106.md（这是建设方自验记录，涉及分支、未提交、运行端口的文字已过时，不能当现场事实）
2. 重新核对 HEAD、status、diff、worktree。已知有未跟踪验收报告 docs/ACCEPTANCE_DIRECTOR_LONG_FORM_3F27053_20261106.md，必须保留。使用 codex/ 功能分支或安全 worktree，不 reset、clean、强制 stash、覆盖其他人的修改或使用 git add -A。
3. 只读核对 API、Web、Worker 的 PID/出生时间/命令/数据目录/实际数据库/schema、活动 Run/series/job、UNKNOWN 和 GPU 租约。上轮 8133/5193 原服务未动，8151/5211 临时服务已关闭；这些只是历史快照，不能据此杀进程或重启。不能只相信 processes.json。
4. 测试使用新隔离数据目录和空闲端口。先确认配置指向隔离库再执行迁移或测试，保留正式库、个人素材、通过版本、冻结合同、候选资产和历史审计。新 worktree 不包含被忽略证据时，到上述原工作目录读取，不伪造“文件不存在”的结论。

二、当前结论与必须先复现的证据

当前为 NOT_ACCEPTED：原 18 条反例已全部通过，但新增 3 条独立集成反例失败，归为两类 P1。正式后端全绿没有覆盖这些隔离目录中的失败探针。

最近独立结果：后端 1767 passed / 1 skipped / EXIT=0；前端 21 文件、148 passed；构建、Ruff、新库迁移和 Alembic check 通过。真实 ComfyUI 启动探测被跳过；真实文本、生图、H3、多集无人值守、8h/24h 与 Windows 包验收未完成。

先读取并原样运行：
1. .local-acceptance/longform-recheck-20261106/test_recheck.py
   SHA-256 ca245176c94e9daa7dca986636399522f6c5da4080917dc81c35736f77b29b7f
   当前期望：18 passed，必须保留。
2. .local-acceptance/acceptance-3f27053-20261106/test_new_paths.py
   SHA-256 9bb7fc329d33d3b1d9aab0add6a6c4a1347c21e2fcfd9c4f2cfea6823053d0a1
   当前实际：3 failed，修复后必须全部通过。
3. 同目录 new-path-probes.log、verification-status.json、backend-full.xml 和界面截图。

这些探针使用隔离夹具和合成素材；管理门控替身、执行器 spy、手工预置完成状态只用于定位软件行为，不是真实 H3 证据。不得把合成产物迁入真实项目或提升其验收等级。不要编辑原始探针来消除失败；将等价且更完整的断言迁入正式 backend/tests，并保留原脚本可复跑。

三、P1-A：修复 Run 调度饿死拍摄和装配执行器

参考位置（实际行号可能变化）：
- backend/shine/worker.py:4049–4051：Run advance 非零即 return，后续交付和 series 执行器没有机会执行。
- backend/shine/production/runner.py:1008、1028：等待且 changes=[] 仍算 advanced。
- runner.py:649、965：相同 PENDING 装配单仍返回 ID，被反复记为 episode_queued。因此只过滤空 changes 不足以修复。

实测：连续四次真实 Worker tick 都返回 run:1，series 留在 QUEUED，下游调用为零；预先生成 PENDING 分集装配单后，同样四次都不进入交付执行器。

要求：
1. 设计明确的、工作量有界的调度顺序和公平性，使 Run 编排与其 series、普通任务及分集装配能够持续前进。可以调整返回语义或调度结构，但不能添加第二个 Worker 平台、另起旁路脚本或靠多开 Worker 掩盖缺陷。
2. 区分“检查过”“真实状态变化”“新工作入队”“复用已有待执行单”。无进展心跳与重复 PENDING/ASSEMBLING 单不得制造虚假进度。
3. 保持 H3、图像模型和本地评审的 GPU 串行与租约保护；修复公平性不能让两个模型阶段重叠。不要在 ComfyUI 外部提交期间持有 SQLite writer transaction。
4. 独立镜头可继续，依赖镜头必须等待真实通过且当前版本有效的源作业；暂停、取消、预算、存储、UNKNOWN 和不确定租约仍须遵守既有合同。
5. 不用无限轮询、硬编码 sleep、无条件刷新 last_progress_at 或假标 COMPLETED 让测试通过。

四、P1-B：补齐创意剧本任务的领取、续租、恢复和迟到写回保护

backend/shine/director/creative.py:329–429 目前只领取 QUEUED；RUNNING 写 owner 却不设到期时间，现有恢复入口不处理 creative_script_run；finish 和成功写文档缺状态/owner/version fencing。

实测：领取后注入 Worker 中断，任务永久留在 RUNNING/dead-worker；明确设成过期后，重启 Worker 调度仍不处理。

要求：
1. 纳入现有 AgentTaskRecord、Worker、审计和请求幂等链，设置并续租；过期表示需要核对，不表示请求没发出。
2. 在外部调用前持久化稳定请求身份、发送意图及足以恢复的阶段；响应、解析/修复调用及文档落库都可追溯。复用 Gateway/TrackedProvider 的既有台账，不另造不兼容调用路径。
3. 明确区分：已证明未发送、已发送结果未知、响应已持久化、文档已成功落库。能证明未发送才可按政策重新领取；UNKNOWN 不自动重放，进入可见的核对/NEEDS_REVIEW 路径。已持久化成功结果优先复用，不能再消耗一次模型调用。
4. 成功和失败写回都校验任务当前状态、owner、state_version 与简报/请求身份。租约失效的旧 owner 不得迟到覆盖、创建重复 ProjectDocument 或重新复活已终结任务。
5. 角色、provider 指纹、同意证据与确认后的简报 hash 仍须匹配，不静默换 provider；密钥不进数据库、日志或前端。
6. 前端能显示中断、核对、失败与下一步，不能无限显示“生成中”。同一请求重复点击返回原任务/原结果，不能制造重放。

五、补齐真实 Worker 集成与完整导演旅程

1. 必须新增从真实 Worker 调度入口运行的整链测试：Web/API 启动已确认导演包→Run 派发→series 执行→各批次通过→分集交付执行→Run 完成。不能在测试中手工调用 advance_run 驱动下一步、直接把 job/series 标为 PASSED 或手工补交付后声称后台已闭环。
   离线测试可替换模型/网络边界，使用合成可解码媒体；真实调度、领取、状态变化、交付入队和 FFmpeg 装配应尽量保留。所有替换点和证据等级必须声明。
2. 至少覆盖 3 集、每集多批次、全片超过三段；另做数千任务的有界规模/查询/恢复测试。验证无重复提交、无漏镜、无跨 Run 借片、无静默缩小范围、无固定三段上限和全部必需集完成前提前 COMPLETED。
3. 调度回归覆盖：无变化等待、已有 PENDING 单、多个 Run 的公平性、普通任务共存、局部 NEEDS_REVIEW、暂停/恢复、取消收尾、预算暂停、装配长租与失联、旧 owner 迟到写回。
4. 创意任务覆盖：领取后未发送中断、发送后响应丢失、响应已保存但文档未写入、文档已写入后重启、两个 Worker 竞争、旧 owner 迟到、重复点击。恢复实际走重启后的 Worker 入口，并断言外部调用计数与文档数量。
5. 核查完整建设方案中的已有路径，发现接口存在但页面/任务链断开时继续修复：
   - 想法→确认简报→生成剧本，与现成剧本导入汇合为同一统筹范式；保留锁定事实和来源。
   - 确认大纲→按叙事边界先分集→各场次分镜实际应用→物化成镜头→冻结包，不只测试手工伪造的 apply 回执。
   - 剧本统筹产生素材需求；已有、上传、生成候选可混用，逐项人工确认后晋升，最终风格由用户冻结。AI 不代替用户批准。
   - 内嵌导演包直接启动生产；进度区准确区分等待、阻塞、拍摄、装配、交付与艺术待审，页面切换或关闭不取消后台工作。
   - 机器检测→明确视频/生图组合及复用/下载量→用户确认安装计划→实际可用性验证。估算、文件校验和真实就绪分开，不能凭检测显卡或目录存在显示 READY。
   - 剪辑入出点和字幕修订使用当前合同，新交付版本保留旧版且不重跑 H3；高级音频/调色没有实现时具名声明范围，不做假按钮。
6. 跨 series 任意媒体续接目前未实现，不能把旧限制隐去。首版保留连续链不跨批的策略时，要校验单链资源/时长边界；超出则具名阻塞，不能偷偷切断依赖。确需扩建续接时先落实来源 job、产物 hash、映射、失效传播和真实兼容证据，禁止用未实现功能宣传任意长连续镜头。

六、回归、真机验证与交付顺序

1. 先复现三个失败，再做最小修复、专项回归与实际 Worker 整链；确认通过后运行一次最终完整套件，避免同一未改代码反复全跑。
2. 从仓库根运行项目 Python 3.12 虚拟环境：
   backend/.venv/Scripts/python.exe -m pytest .local-acceptance/longform-recheck-20261106/test_recheck.py -q
   backend/.venv/Scripts/python.exe -m pytest .local-acceptance/acceptance-3f27053-20261106/test_new_paths.py -q
   backend/.venv/Scripts/python.exe -m pytest backend/tests -q -o addopts='' --junitxml=<本轮隔离证据目录>/backend-full.xml
   backend/.venv/Scripts/python.exe -m ruff check backend/shine backend/tests
   前两条在新 worktree 执行时须使用原证据绝对路径并确认导入的是待测工作树，或完整复制原探针并核对 hash；不得误测原工作区代码。
3. Worker/质量/crew 改动先按 AGENTS.md 跑 offline_e2e、film_crew、production_series、comfy_completion_window、advisory_queue、comfy_handoff_journal、v13_preparation/migrations/e2e、h3_compiler 等专项，最终全量必须包括新正式回归。frontend 下运行 npm.cmd test -- --no-file-parallelism 与 npm.cmd run build。
4. 新空库 upgrade head/check、旧 schema 与正式库只读快照的副本升级；比较行数、外键、冻结身份和产物关联。不要在正式库试迁移，不把副本迁移成功说成正式环境已升级。
5. 通过浏览器验证上述真实用户旅程，保存关键截图和 API/DB 关联证据；不能仅以 mock/intercept 页面截图证明真实生产。修复代码受 client_review_identity 钉住时，按既有规则重新进行真实 VLM 正负校准、发布新 revision、弃用旧 revision、分析并人工重新冻结计划，不沿用旧校准。
6. 工程阻塞消除后，在现有已授权模型、素材、冻结合同、服务和预算范围内递进验收：真实部署/三类生图与 H3 小样→至少 3 集、每集约 60 秒的 Web 启动多批次自动交付→实际活跃生产至少 8h→独立 24h 稳定性与 Worker/机器重启恢复。不能由脚本手工触发下一集，不能用睡眠、等待和暂停补时长；分别报告活跃运行时长和最终影片时长。
   现有授权/资源不足或需要用户确认素材、下载、云传输、冻结风格时，先完成可独立进行的修复、离线集成、数据准备和可审阅方案，再列出准确阻塞与最小下一步。不得自动确认，也不得把提示词理解成新增付费预算、大模型下载、云传输、正式库迁移或重启其他服务的许可。长时实验不能以无任务空等代替，也不能凭部署代码就宣称通过。
7. 复用唯一 SQLite/Worker/任务尝试/GPU 租约/审批审计/H3PromptCompiler；未知 ComfyUI 提交只核对不重发，引用缺映射具名阻塞，产物与通过版本不覆盖，技术通过不等于最终人工艺术通过。评审模型保持 advisory，不因缺失或慢响应阻断主生产；确定性技术错误不能自动放行。
8. 未获明确提交/推送授权时不自行提交、推送或合并。准备好可审阅 diff、拟提交文件清单与准确构建步骤；Windows 包仅从已授权的最终提交且干净的树构建，用 scripts/build_test_package.ps1 -Verify。不得为了打包吞掉预先存在的未跟踪文件；保留 cmd ASCII、非 ASCII ps1 UTF-8 BOM，不打包权重、数据库、秘密、个人素材和验收证据。

七、最终必须交付

- 修复代码、正式回归与真实 Worker 集成测试；说明两个 P1 的根因及为何原套件遗漏。
- 本轮报告 docs/ACCEPTANCE_DIRECTOR_LONG_FORM_CLOSEOUT_V3_20261106.md；若跨日可用实际日期，但清楚链接本次 V3 任务。保留既有独立报告，不把它改写成通过。
- 独立证据目录中的命令、退出码、JUnit、请求身份与状态变化、任务/series/交付关联、关键截图、hash、verification-status.json；记录最终 HEAD、分支、diff、schema 和准确进程归属，不沿用旧端口/旧 SHA/旧测试计数。
- 建设方案需求逐项映射到真实入口、实现、回归、模型证据和遗留项。明确分开 IMPLEMENTED、OFFLINE_VERIFIED、REAL_H3_VERIFIED、LONG_RUN_VERIFIED、HUMAN_REVIEW、BLOCKED/NOT_RUN；任何一层未完成就不能宣称整体验收通过。
- 清理本轮自己启动的临时服务，先证明身份再停止；保留证据，不动原有服务。若留下供查看的进程，写清端口、PID、数据目录与停止方式。
- 给出下一次独立验收的最短启动步骤与剩余阻塞。不能只给“全绿”截图或一句“已完成”。

请先完成现场核对与三个独立反例复现，然后连续推进修复、集成、回归和可执行的续建工作。仅在确需用户授权或外部条件改变时停下对应依赖路径，其余已授权工作继续推进；过程中简短报告真实进展。
```
