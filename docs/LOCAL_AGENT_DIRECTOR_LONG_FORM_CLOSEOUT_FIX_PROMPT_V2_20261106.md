# 全片持续生产收尾修复提示词 V2：以第二次独立验收为依据

- 日期：2026-11-06（Asia/Shanghai）。
- 配套事实：[第二次独立验收报告](ACCEPTANCE_DIRECTOR_LONG_FORM_RECHECK_20261106.md)。
- 权威范围：[完整建设方案](DIRECTOR_WORKFLOW_AND_LONG_FORM_PRODUCTION_PLAN_20261105.md)。
- 本版取代[上一版提示词](LOCAL_AGENT_DIRECTOR_LONG_FORM_CLOSEOUT_FIX_PROMPT_20261106.md)作为本轮修复入口；旧文档和历史证据保留。
- 本版包含已经运行失败的 18 条反例，不能再把这些全部列成“仅待复现的猜测”。未跑过的真实模型、长时装配和艺术验收仍单独注明。

将下面代码块全文交给本机 Agent，或让它完整读取本文件并执行：

```text
你现在接手 Shine Video Studio 导演流程重构与全片持续生产的收尾修复。任务是修改真实代码、补齐主需求、保留回归、完成集成和分级验收；不要再只写分析或方案。

项目根目录：F:\ClaudeWorkSpace\projects\shine-video-studio
事实报告：docs/ACCEPTANCE_DIRECTOR_LONG_FORM_RECHECK_20261106.md
建设方案：docs/DIRECTOR_WORKFLOW_AND_LONG_FORM_PRODUCTION_PLAN_20261105.md

当前结论是 NOT ACCEPTED。不是所有东西都要重写：导演包、运行层、series、素材链和交付基础已存在；但关键状态/身份路径有实测缺陷，且双入口等用户需求没有真正闭环。

一、先读规则和证据，重查当前事实

1. 读取当前 AGENTS.md，按其顺序读取 V1.0 项目基线、首轮工程约束、V1.1 专业 Agent 方案；再完整读取上述事实报告和建设方案。
2. 读取现有进度、原验收、ADR 0006/0007、素材方案以及相关源码和测试，辨别后续补充对旧规则的覆盖。
3. 检查 git status、HEAD、分支、diff、worktree。独立复验基线是 main / 8a4e72b5b9656a0e9c7f3ecd49c0e296fc5e9a17；它不是你执行时的事实保证。
4. 不覆盖用户或其他 Agent 的改动，不 reset/clean/强制 stash，不一次性 git add -A。实质开发使用 codex/ 功能分支或安全隔离 worktree；确保未提交的方案和证据仍可读取。
5. 只读核对 API/Worker、端口、数据目录、schema、活动 Run/series/job、UNKNOWN 结果、GPU 租约和后台所有权；不输出密钥或敏感命令行。

优先读取原工作区的证据：
.local-acceptance/longform-recheck-20261106/test_recheck.py
.local-acceptance/longform-recheck-20261106/independent-probes.log
.local-acceptance/longform-recheck-20261106/existing-targeted.log
.local-acceptance/longform-recheck-20261106/backend-full.log
.local-acceptance/longform-recheck-20261106/frontend-tests.log
.local-acceptance/longform-recheck-20261106/frontend-build.log
.local-acceptance/longform-recheck-20261106/deployment-check.json
以及三张 ui-*.png 和 ui-setup.txt。

这些目录可能被 Git 忽略。使用新 worktree 时不要因为文件不在新目录就假装没有证据；先到原工作区读取。确实缺失时，依据报告的具体步骤重建，不伪造历史日志。

独立反例没有访问真实模型：它们复用了项目测试夹具、隔离数据库和 FFmpeg 合成视频。测试使用 is_mock=false 的合成媒体仅用于软件合同场景，不是实际 H3 证据，严禁导入真实项目或升级验收等级。

本轮已拿到最终回归结果：原有后端 1731 passed / 1 skipped / 2 warnings，EXIT=0；原有导演与推荐专项 34 项通过；前端 20 文件/140 项、构建、Ruff、新隔离库迁移与 check 均通过。但独立 18 个反例全部失败，故仍 NOT ACCEPTED。反例不在 backend/tests 收集范围内，不要把两组结果混为一组，也不要声称全量通过就无需修复。

证据脚本 SHA-256：ca245176c94e9daa7dca986636399522f6c5da4080917dc81c35736f77b29b7f。完整状态见 verification-status.json。本轮临时 8137/5197 服务、浏览器标签和测试进程已收尾，数据与日志保留；不要尝试使用已停止的临时端口而不重新启动隔离栈。

本轮观察到原有 8133 API 的 OpenAPI 没有新 director/Run/journey 路由，旧库停在 f170a0030001；新代码隔离栈 8137/5197、新库 f180a0010001 有 15 条相关路由且可打开导演包。不要把“旧服务没重启/旧 schema”错判成源码没有 API。原用户 API/Worker 未被独立验收重启或迁移。

二、先锁定修复顺序和执行边界

先列精简的“已证实缺陷 F01–F08 / 必需缺口 G01–G04 / 尚待验证 T01–T04”台账，并立即开始代码闭环。

顺序：F01 → F02/F03/F06/F07 → F04/F05 → G01/G02/G03/G04 → F08/界面/部署与综合回归。可穿插独立任务，但不能用页面装修代替生产正确性修复。

正常的代码组织、隔离测试、可逆修复不必逐项征求用户批准；用户已经确认产品方向。发现某项阻塞时继续其他不依赖它的工作，不停在“需要真实模型所以先不做”。

继续遵守以下不可破坏的约束：
- 一个 SQLite 项目/资产账本，一个任务编排体系，一个常驻 Worker 主循环，一个 H3PromptCompiler，一套资源租约和审计。
- 正式视频只使用本机 ComfyUI/MiniMax-H3，默认多参考 Ref2VA；不静默换模型、生成模式或 Provider。
- AI 可生成 CandidateAsset，只有用户动作能晋升正式主素材和冻结风格；不得给研发 Agent 运行时后门。
- 冻结规则、风格、主资产、工作流和每镜绑定都带版本/hash；保留 Job 创建及 Worker 提交的 preproduction.enforce。
- 外部调用不持有 SQLite writer 事务；提交意图先落库、外部 ID 返回后立即保存；UNKNOWN H3/LLM 不盲重放。
- 过期不等于 renderer 已死；GPU 释放仍须证明 PID/birth 与命名 Job Object 的所有权和实际终止。
- 确定性技术错误不自动通过；advisory VLM/ASR 不阻塞正常生产或交付；pending_review 不改 accepted。
- 文字授权不等于媒体字节出机授权；不泄漏、重写或提交已有密钥。

三、先修 18 条已失败的业务反例

先在当前树重跑 test_recheck.py，确认代码是否已经被别人修过。然后把有价值的反例迁入 backend/tests 的正式 regression 套件，按实际合同改善夹具，但保留其业务断言。原日志保留，修复后另写新日志；不能删除、xfail、skip、降低断言或关闭准入门禁来转绿。

F01 [P1] 分集串用历史/无关 job，未拍完也宣告全片完成（3 条失败断言）。

拥有模块：
- production/runner.py：_current_passed_jobs、_ensure_episode_delivery、advance_run。
- production/episode_delivery.py：_passed_jobs、episode_input_digest、build_episode_edl。

已观察到：
一集两镜、每批一镜，旧镜头已有通过产物。新 Run 仅完成第一批，第二批仍 QUEUED，却创建 episode 交付订单；装配后 Run 变 COMPLETED，第二批仍 QUEUED。再创建一个不在本 Run series item 中的同镜头新通过 job，EDL 还会改选它。

必须修复：
1. 从本 Run 的 episode → batch → series item 解析确切 job/attempt/artifact，不能全局取 shot_id 最新通过项。
2. 同时验证计划中全部必需批次均已创建、覆盖完整并通过；不能仅检查数据库里已经存在的批次。
3. 历史产物复用必须是明确、已确认的选择，且 render binding/产物完整性完全匹配；不允许隐式借旧片补齐未拍内容。
4. 交付订单固定有序输入身份、hash 与后期合同 digest，执行前重新核对；不能执行时再挑“最新”。
5. Run 只有全部必需交付有效、且没有本范围未决必需生产时才能 COMPLETED。已误标完成的运行须通过明确对账纠正，保留原事件，不偷偷重写历史产物。
6. 为本次归属、旧数据复用拒绝、并发新 job、未派发批次、重复片段和失效传播分别加测试。

F02 [P1] 用户暂停、预算暂停没有到达子 series（3 条失败断言）。

现象分别是 Run=PAUSED 而 series=QUEUED、Run=PAUSED 而 series=RUNNING、预算耗尽后 series 仍 QUEUED。

要求：
1. 调用既有 series 控制机制，在同一一致性协议中禁止后续外部提交。已提交片段允许安全落盘。
2. 正在收尾时是 PAUSE_REQUESTED，满足安全边界才 PAUSED；资源未释放时不显示已释放。
3. 时间预算、低磁盘与用户暂停采用统一控制路径；明确装配任务是收尾还是暂停。
4. 恢复只恢复由本 Run 暂停的子任务，不能抢走另一个用户/控制原因的暂停；重复动作幂等。
5. 增加真实 series 路径的暂停边界测试，不能只断言 run.status 或“未派发下一批”。

F03 [P1] 旧预算 beat 能覆盖已提交的取消状态（1 条失败断言）。

会话 A 先读预算耗尽的 Run，会话 B 取消并提交，会话 A 再推进，结果 CANCELLED 被改回 PAUSED。

要求所有写状态分支，包括预算、存储、错误处理和对账，都有 status/state_version/owner 等条件更新；不是只给正常派发加 claim。先取得有效推进身份再修改，或使用等价的 CAS/fencing。终态不可被陈旧对象复活。补暂停/取消/恢复与多个 Worker 的竞争测试。

F04 [P1] 冻结包身份在运行中变化未阻止派发，根内容 hash 未完整验证（2 条失败断言）。

反例一：包确认并创建 Run 后，修改 workflow 合法 seed 输入并更新 graph hash。readiness 已明确 WORKFLOW_PROFILE_CHANGED，但推进仍创建 series。
反例二：隔离库中改变根 manifest 政策、不改根 hash/policy hash，readiness 仍 startable=true。

要求：
1. 根 manifest 与各集子 manifest 均重算并验证固定摘要；policy hash、scope digest 和引用身份也需要一致。
2. 每次批次派发复核包版本、shot revision、素材绑定、工作流/模型、风格/规则、准入计划；Worker 外部提交前继续执行最终校验。
3. 包确认不是一次永久运行资格，不能只在 create_run 校验一次。
4. 校验失败进入有名原因的阻塞/核对状态，不能原地改包内容或绕过验证。
5. 动态末帧与合同内合法修复采用显式来源和 lineage；不要把正常的受授权技术派生误当成用户创作内容变更，也不能伪造尚不存在的文件/hash。

F05 [P1/P2] 政策未落实到实际 Job，非法政策进入冻结包（5 条失败断言）。

auto_repair=false、max_repairs_per_shot=0 的包生成了 max_semantic_retries=2 的 Job；非数字批次上限以及负数时间/返工/磁盘阈值都返回 HTTP 201。

要求：
1. 使用版本化、类型化政策合同，API 边界拒绝非法值和无法执行的组合，返回可读原因。未知字段按明确兼容策略处理，不能 silently accept 后忽略。
2. 包政策落实到 Job/Attempt、Worker 实际修复、预算与独立继续的决定，不能只存 JSON 或 UI 显示。
3. 不要通过改变项目全局设置来临时实现某个 Run 的政策，以免影响其他运行或历史合同。
4. 除传播字段测试外，增加对实际 repair 路径的受控测试，证明 false/0 时没有超范围重拍。基础设施重试、语义返工和装配重试分别计数。
5. 自动修复必须产生不同的请求修订，受类型和预算约束；身份、锁定对白、主素材、冻结风格、模型身份不可自改。

F06 [P1] NEEDS_REVIEW 子 series 未汇总、证据恢复后正常操作不能续跑（2 条失败断言）。

要求覆盖 series 的完整状态集合。已安全释放的 NEEDS_REVIEW 不能让 batch 永久 QUEUED、Run 永久 RUNNING。
仅阻塞真实依赖；逻辑独立且物理资源已确认可用的批次继续。资源归属仍不确定时，不为继续生产而强行释放或抢占 GPU。
核对动作本身不能提交新外部任务，但应正确同步已解决的 batch/episode；提供有状态检查和明确授权的恢复入口。不能要求手改数据库，也不能一遇失败就另建 Run 重拍全部。
为 UNKNOWN 未解决、已持久产物可采用、批次已恢复通过、无独立工作和有独立工作等场景分别回归。

F07 [P1] 尚有运行 Job 时，Run 已进入取消终态（1 条失败断言）。

要求：CANCEL_REQUESTED 与 CANCELLED 分开；完成外部结果核对及受管资源收尾后才写取消完成。必要时保持 RECONCILING/待处理。
不要只改 Run 表面状态：一起核对 cancel_series_in_session、当前 item/job、装配订单、资源租约以及允许新 Run 的唯一约束。取消保留全部产物，不触碰归属不明进程，不盲重放旧请求。
复现包含排队、执行中、UNKNOWN、已存视频、装配中、重启和重复取消；异步收尾可观察。

F08 [P2] 心跳空转被当作真实进展（1 条失败断言）。

没有批次/产物变化时 advance_run 也更新 last_progress_at。只在真实状态推进、实际产物或可解释的执行进度变化时更新它，心跳另记；不要通过虚假进度缓解用户焦虑。
记录等待资源、GPU 推理、下载/保存、检查、修复、装配、暂停等阶段的真实耗时；服务端 UTC 时间与前端解析一致。

四、补齐已确认的用户主需求

G01 [P1] 真正双入口与统一剧本。

同版本浏览器已确认：点击“创意与剧本”跳到素材页，只有已有剧本选择器，没有想法输入/创作简报。

实现：
- 入口一：想法 → 必要澄清 → 可编辑 CreativeBrief → 结合 H3 可拍摄能力的剧本 → 用户确认。
- 入口二：导入 → 原文/hash/来源范围 → 规范化 PreparedScript → 歧义与语义差异确认。
- 两入口进入同一持久前期任务、Gateway 和剧本范式。格式整理可自动；改写锁定事实/对白须显示差异。
- 长文按真实上下文能力有界处理、保留全文覆盖，不用静默截断或摘要替代原文。
- 不把手工建几个 Shot 当成想法入口，也不把任何 shots 存在当成整部原文已准备完成。

G02 [P1] 剧本统筹先分集，再正式拆镜。

现有 episode-plans 要先有 shots，属于镜头分组；UI 也明确要求先建镜头再回来分集。这与用户确认的顺序不同。

实现基于剧本/场景/节拍的分集目标、容差、承接和结尾，再为每集建立正式分镜；已有镜头分组作为明确的兼容入口保留。
建立原文范围 → 分集 → 分场 → 分镜工作单元 → H3 生成片段 → 实际产物的映射，校验不重不漏。
每集目标秒数是叙事与交付目标；H3 单次时长和电脑运行时长另算。优先完整对白和动作，不凑片、不截断台词。
阶段完成按所选包/范围/版本的覆盖事实判断，禁止“有一个镜头就算全片分镜完成”。

G03 [P1] 把机器完整组合推荐接入真正的新机流程。

当前推荐 API/模块存在，前端没有对应调用；基础配置已有检测和复用能力，但没有完整推荐组合选择。

实现：检测机器与已有安装 → 推荐 H3/生图完整组合 → 显示估算/实测证据、下载/复用量、目录/资源/许可证 → 用户选择确认 → 既有安装计划 → 实际小样回填。
组合包括主模型、编码器、VAE、插件、工作流和卸载策略。权重精度与采样/放大质量档分开；不按下载体积承诺显存，不把多卡显存相加。
全部自行上传素材时允许不装生图。不要静默换其他视频/图片模型。未支持配置和未实测输出范围如实显示。
安装、推荐、后端准入和前端读取同一资源要求；依据真正部署/数据/临时盘，不能继续引用过期 83 GB 数据，也不能只凭某盘有空间就宣布已验证。

G04 [P1，源码观察，先补反例] 剪辑/字幕/声音合同要真正进入分集交付。

目前 build_episode_edl 固定整条从 0 到全长；订单创建未见绑定 subtitle_revision。先验证非默认 EDL、已批准字幕与后期修订的实际路径。

实现已确认 EditIntentPlan/后期设置到实际 EDL 的映射，固定确切产物、入出点、顺序、字幕版本/hash、声音参数和输出规格。
分集订单从多个 series 取完整输入，不将每个 batch 都发布为一集。缺必要片段不能正式交付完整集；需要预览时显著区分。
字幕、音量或剪辑修订只建立新交付版本，不重跑 H3、不覆盖已交付历史。逐集技术完成与人工艺术验收分别记录。
保留单次 100 clip 合同，用分页/分层 manifest 与流式 FFmpeg 装配，避免命令行过长、数小时媒体整体进内存或每层重复有损编码。
可选全片合集明确实现或列为未交付增强，不影响对必需功能缺口的如实判断。

五、尚需补证的综合风险与界面交付

T01 长装配领取安全：当前装配租期 2400 秒，源码路径未见 heartbeat/子进程身份保存。用可控时钟/受控长任务复现第一 Worker 仍活跃时跨租期、第二 Worker 竞争、失联后恢复、过期输出回写和重复文件发布。若真实有保护先证明，不简单把租期调大。

T02 跨批连续性：区分 planned 剧情事实与 observed/verified 媒体证据。末帧引用必须指向真实通过且当前有效的 job/attempt/artifact；在包里先声明动态依赖，产物出现后才最终编译。若首版不切断连续链，明确其边界并测长链，而不是宣称实现了任意跨 series 续接。

T03 版本/schema 与运行入口：原 5193/8133 服务落后已被确认；同版本隔离栈有效。修复启动/升级和可理解的版本不匹配提示，保留旧数据。未经当前授权不迁移/重启原正式栈；先在新库与旧库副本验证，不删除数据库“解决”问题。

T04 journey 不可用不能显示全片完成：App.tsx 将请求失败变 null，首页 fallback 却写“全部选定范围已交付”。区分 loading/error/empty/completed；404/500、慢响应、切作品和重试都要测试。清理旧“五步走完”、旧页面名和新建成功后直接先传素材的过期引导。

真实 Web 验收必须覆盖两入口、分集先行、混合素材、确认冻结、导演包、全片启动、暂停/预算/取消/恢复和逐集播放。不得靠 UI 外的手工注册、手工建全片镜头或脚本逐批排产替代用户主线。

六、迁移与兼容要求

保留既有项目、Asset/Candidate、Shot、Job/Attempt、series、审批、产物和审计身份。包是只读引用快照，不新增可变素材库。
新增政策/恢复/产物选用等数据走 Alembic，验证新库和旧库副本；DeliveryOrder 的旧 series/version 唯一性与 scope 部分唯一索引都要有效，不能依赖 NULL 的错误防重假设。
旧项目显示明确的兼容入口或迁移预览，用户确认后形成新包；不能自动批准、改风格或开拍。
既有运行与产物若已被错误汇总，要提供可审计的修复/对账策略，不以后台 SQL 批量改成成功代替。

七、测试与证据要求

优先执行并修复独立反例，然后执行专项、全量、迁移、前端与 Web。不能只跑新增测试，也不能把原有 34 项通过当作反例已经解决。

仓库根，使用项目 Python 3.12：
backend\.venv\Scripts\python.exe -m pytest .local-acceptance/longform-recheck-20261106/test_recheck.py -q -s --tb=short
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_director_long_form.py backend/tests/test_deployment_recommendation.py -q
backend\.venv\Scripts\python.exe -m pytest backend/tests -q
backend\.venv\Scripts\python.exe -m ruff check backend/shine backend/tests

按建设方案 §17.4 与当前 AGENTS.md 加跑 worker/gates/repair/crew/series、offline_e2e、film_crew、completion_window、advisory_queue、handoff_journal、v13、h3_compiler、素材、长剧本、交付、进程归属和 Windows 入口专项。

frontend 目录：
npm.cmd test -- --no-file-parallelism
npm.cmd run build
当前 build 包含 TypeScript 检查，不声称运行不存在的独立 lint 脚本。

迁移使用显式隔离 SHINE_DATA_DIR/SHINE_DATABASE_URL，执行 upgrade head、check 及旧库副本升级；不要误用 .env 指向的正式库。保留 Windows CI、cmd ASCII 和含中文 ps1 UTF-8 BOM 约束。

读取本次事实报告第 6 节的最终全量结果，不沿用旧报告中的沙箱归因。中断/超时没有最终结果就记 INTERRUPTED/UNKNOWN；失败须保存 traceback 和最小复现，不禁用安全 hook、删测试或绕门禁来过关。

八、真实验收与现有授权

正常研发和隔离测试自主完成。真实模型下载、收费调用、媒体出机、正式库修改、候选晋升/风格冻结和长时运行，按当前会话及产品内具体确认执行；已有具体授权足够则继续，不重复请求。缺什么就只询问具体动作的最小范围，同时继续独立工作。

先修复 P1 并通过隔离闭环，再核对本机模型/节点/工作流/哈希/资源，开展实际小样、至少 3 集多批次自动交付，再按授权窗口完成至少 8 小时和独立至少 24 小时运行。运行时长与成片时长分别报告，不用睡眠/空等补数，不手工驱动下一批。

修改 client_review_identity 钉住的 worker/preproduction/handoff/质量客户端会使旧 revision 证据失效，即使只是增加一个调用也要按实际 hash 判断。正式验收前做真实正负校准、发布新 VLM revision、弃用旧版、重新分析并冻结；缺环境时单独记 BLOCKED，不能靠“没动 gates”沿用旧校准。

真实生产路径不依赖编程 Agent，也不增加 Mock/Fake 生产兜底。审片不可用时 advisory 如实记录、可播放交付 pending_review，只有真正 final gate 通过才 accepted。

九、交付、进度与停止条件

维护 docs/PROGRESS_DIRECTOR_LONG_FORM_PRODUCTION.md，新增或更新：
docs/ACCEPTANCE_DIRECTOR_LONG_FORM_CLOSEOUT_20261106.md
不要覆盖旧报告证据；记录当前提交、每项修复前/后结果、迁移影响、命令/日志、实际运行数据和未决项。

Git 提交/推送/发布依据本次用户指令；没有明确授权不自动扩大。仅提交本任务的明确文件，绝不打包 .local-acceptance、.zcode、模型、视频、数据库、用户素材或秘密。
Windows 测试包只能来自最终已提交且干净的树：
pwsh -File scripts\build_test_package.ps1 -OutDir releases -Verify
若缺最终提交/构建授权，先完成代码与验证，在最终步骤说明具体缺项；不提前反复申请，不丢弃已有修改或从脏树伪造包。

最终逐项列出 F01–F08、G01–G04、T01–T04：修复并通过 / 当前不成立及依据 / 尚未完成及具体条件。特别给出：
- 独立 18 条反例在修复后是否全部满足业务断言，以及新增场景结果。
- 新旧后端全量、前端、迁移、Windows、浏览器的确切结果。
- IMPLEMENTED、OFFLINE_VERIFIED、REAL_DEPLOYMENT/IMAGE/H3_VERIFIED、LONG_RUN_VERIFIED、HUMAN_REVIEW、BLOCKED/NOT_RUN 分级。
- 分支/提交/包路径、真实项目/Run/产物身份与 hash、实际运行和成片时长。
- 当前仍在运行的任务、资源租约、进程归属和下一项动作，不留下未知后台。

“正常部分都跑过”“页面能打开”“原套件全绿”都不等于全片无人值守验收通过。当前阶段完成可单独报告，不能把主需求缺失或真实长时未跑包装成完整完工。

现在开始：核对规则、基线与证据，重跑独立反例，先修 F01 的确切产物归属和完整范围，再持续完成其余代码与验收工作。
```
