# 全片持续生产：独立验收后的收尾修复提示词

- 日期：2026-11-06（Asia/Shanghai）。
- 目标：交给本机另一个 Agent，继续补齐已确认需求、复现并修复验收发现的风险，完成回归和分级交付。
- 建设依据：[完整建设方案](DIRECTOR_WORKFLOW_AND_LONG_FORM_PRODUCTION_PLAN_20261105.md)。
- 原实施提示词：[本机建设执行提示词](LOCAL_AGENT_DIRECTOR_WORKFLOW_AND_LONG_FORM_PRODUCTION_PROMPT_20261105.md)。
- 本提示词区分已实跑结果、代码静态观察和待复现风险；后两者不得写成已经取得失败运行证据。
- 本轮仅编写交接文档，没有修改产品代码或启动真实 H3。

把下面代码块全文交给接手 Agent，或让它完整读取本文件后执行：

```text
你在本机 F:\ClaudeWorkSpace\projects\shine-video-studio 接手“导演流程重构与全片持续生产”的收尾修复。用户要求的是实际代码修复、需求补齐、回归和可验证交付，请直接开展工作，不再重复讨论已经确认的产品方向。

一、权威文件与本轮真实基线

先读完整的当前 AGENTS.md，按其中顺序读 V1.0 基线、首轮工程约束、V1.1 专业 Agent 方案，然后完整读取：
- docs/DIRECTOR_WORKFLOW_AND_LONG_FORM_PRODUCTION_PLAN_20261105.md
- docs/LOCAL_AGENT_DIRECTOR_WORKFLOW_AND_LONG_FORM_PRODUCTION_PROMPT_20261105.md
- docs/PROGRESS_DIRECTOR_LONG_FORM_PRODUCTION.md
- docs/ACCEPTANCE_DIRECTOR_LONG_FORM_PRODUCTION.md
- docs/adr/0007-director-package-and-long-form-run.md
- 对应实际源码、测试、迁移和 Git 历史。

上轮实现已存在，禁止从头重搭一套系统。main 上新增的 director、production、推荐模块和六阶段界面都应在现有基础上修复。

独立验收开始时 HEAD 为 d463bb4；验收中 HEAD 变为 8a4e72b5b9656a0e9c7f3ecd49c0e296fc5e9a17，后一个提交只改 .gitignore。开工必须重查，不把这里的提交当成永久事实。不要覆盖并发或后续改动。

独立验收留下的证据目录：
.local-acceptance/independent-longform-20261106/
其中 verification-status.json、frontend-tests.log、frontend-build.log、backend-full.log 是本轮结果来源。目录可能被 Git 忽略，若不在你的 worktree 中，应到原工作区读取；不要编造丢失的证据。

已实跑：
1. 前端 npm.cmd test -- --no-file-parallelism：20 文件 / 140 测试通过。
2. 前端 npm.cmd run build：通过，存在 bundle 超过 500 kB 的非阻塞警告。
3. backend/.venv/Scripts/python.exe -m ruff check backend/shine backend/tests：通过。
4. 后端全量曾运行到约三分之一，因用户转交收尾任务而停止，只能记 INTERRUPTED/UNKNOWN，不能计为通过或产品失败。
5. 原有前端 http://127.0.0.1:5193 连接 8133 旧 API；真实浏览器点击导演包后显示 Not Found。前端首页在 journey 不可用时仍显示“全部选定范围已交付”。
6. 本轮没有实际跑真实 H3、多集、8 小时或 24 小时验收，也没有执行下文源码风险的隔离失败复现。

本轮自己的 pytest 启动进程已停止；用户原有 API PID 12168 和 Worker PID 21404 未动。这些 PID 只用于识别历史，不是你开工时终止进程的依据。

独立验收当时 F 盘约有 323 GiB 可用，多个其他盘也有空间；旧报告写的“目标盘只剩 83 GB”不能继续直接作为真实 H3 的阻塞理由。请重新检查真正的部署目录、数据盘、临时盘和组合要求。也不能反过来仅凭 F 盘有空间就宣布 H3 已可执行。

二、执行方式和权限边界

先做精简修复清单，然后马上开始能隔离验证的代码修复。普通实现、功能分支、只读核查和隔离本地测试继续自主推进，不要求用户每个步骤批准。

必须遵守：
- 一个数据库、一个 Worker 编排体系、一个 H3PromptCompiler、一套 GPU 租约/审计。
- 正式视频继续走本机 H3/ComfyUI；不静默改模型、模式、规则或参考槽位。
- 用户明确晋升主素材、冻结风格和确认创作内容；候选不能自己变主资产。
- 保留 preproduction.enforce、渲染身份和快照校验，不为测试过关关闭门禁。
- 外部调用不持有 SQLite 写事务；UNKNOWN H3/LLM 不盲重放；过期租约不等于进程死亡。
- 技术检查、advisory 模型意见、交付和艺术验收分开；pending_review 不得改 accepted。
- 不读取或输出明文密钥，不把文字发送授权扩展为媒体上传授权。
- 正式库、既有素材、冻结版本、已通过镜头及未知结果全部保留。

先识别当前进程、端口、数据库 schema、数据目录、活动 series、租约和未知任务，采用隔离目录/数据库/端口开发和验收。不要直接在现有监听栈上热切 schema，不接管归属未证明的 ComfyUI。

下面涉及源码的风险项，先建立有针对性的失败测试或最小复现，核实实际调用路径，记录修复前后差异。若复现不成立，应说明保护来自哪里并补相应回归，不为迎合清单而修改正确逻辑。

三、最高优先级：全片生产正确性

R1 [P1，源码风险待复现] 历史 job 可能被错误选为本次分集产物，分集可能提前进入交付。

检查：
- backend/shine/production/runner.py：_current_passed_jobs、_ensure_episode_delivery、advance_run。
- backend/shine/production/episode_delivery.py：_passed_jobs、episode_input_digest、build_episode_edl。

当前观察：通过镜头按 shot_id + PASSED + revision + 非 stale 全局选择最新 job，没有限定到本 Run 的 batch/series item。交付创建只检查已经存在的 batch 是否通过，没有显式验证整集计划中的批次均已派发并完成。

最小复现：
1. 隔离项目包含两个镜头，设为一集、每批一个镜头。
2. 给两个镜头准备上一轮通过的合成视频 job；这只是测试夹具，绝非真实 H3 证据。
3. 创建新 Run，只完成新 Run 的第一批，第二批尚未完成。
4. 推进 Run，检查是否已创建分集交付订单，是否可能用旧的第二镜完成交付，或在仍有活动 series 时将 Run 标 COMPLETED。
5. 再加入新的无关同镜头通过 job，检查待装配订单的选片是否被全局最新 job 改写。

修复要求：
- 本次分集只能使用本 Run 的明确 batch/series item 所指向的具体 job/attempt/artifact。
- 历史复用必须是用户已确认的显式选择，验证完整 render binding，不得隐式替代。
- 交付前验证完整计划覆盖、全部必需批次完成、没有缺失/重复片段；完成 Run 时不能遗留未决的必需生产。
- 交付 digest 必须绑定有序的确切产物与后期合同，执行时重新核对，不能重新挑“当前最新”镜头。

R2 [P1，源码风险待复现] Run 暂停和预算暂停没有传播到已排队/运行中的 series。

检查 runner.py 的 pause_run、advance_run 预算/磁盘分支，以及现有 pause_series / resume_series。
当前 pause_run 直接写 Run=PAUSED，未见将子 series 改为暂停；Run 不再推进，并不阻止现有 Worker 继续领取已排队 series 或跑完批内后续镜头。

复现 QUEUED 和 RUNNING 两种子 series：用户暂停后不能继续新增外部提交；当前已提交片段应安全落盘。另测时间预算耗尽、磁盘不足、重启与暂停/恢复重复请求。

修复要求：
- 先 PAUSE_REQUESTED，阻止新提交，安全边界完成后才 PAUSED；明确当前片段仍在收尾。
- 复用既有 series 暂停/恢复语义和所有权检查，不直接释放仍运行 renderer 的租约。
- 预算和磁盘闸门使用同一安全控制路径；活动装配任务的行为也需明确。
- resume 必须处理因 Run 暂停的子 series；不能把用户另行暂停的任务擅自恢复。
- 暂停时间、墙钟预算和活跃推理预算按合同记录，恢复后预算变化需有明确授权记录。

R3 [P1，源码风险待复现] 包启动后、下一批派发前缺少冻结身份重校验。

package_readiness 在 create_run 中调用；advance_run/_create_series_for_batch 当前未见同等包级复核。
隔离复现：确认包并创建 Run，尚未派发或已完成第一批后，改变 workflow graph/hash、shot revision、素材绑定或将包明确标 stale。先证明 readiness 拒绝，再检查下一批是否仍创建 series。

修复要求：每次派发验证包/清单完整性、冻结 render binding、shot revision 和准入计划；Worker 提交继续保留最终门禁。变化导致明确阻塞，不能用同一个 profile ID 下的新内容替代包中的旧内容。不能原地重写包以让校验重新通过。

R4 [P1，源码风险待复现] 冻结政策可能只保存，没有作用到实际生产。

package.DEFAULT_POLICY 含 auto_repair / max_repairs_per_shot / stop_on_blocker；runner 中 max_repairs 进入 budget，但本轮未发现它继续传到实际 Worker 修复决策。
检查完整调用链，复现包设置 auto_repair=false、max_repairs_per_shot=0，而项目默认允许自动修复的情况；确认 Job/Attempt 执行是否遵守包合同。

修复要求：以类型化政策约束创建任务、实际修复及局部继续行为，项目默认值后续变化不能改写已启动 Run。无意义字段不应出现在确认界面假装已生效；非法政策、负值和类型错误在 API 边界拒绝。

R5 [P1，源码风险待复现] 取消立即标终态，恢复与进展状态可能不真实。

检查 cancel_run 对进行中 series/装配的处理，Run 是否在资源尚未收尾时直接 CANCELLED 并允许另一个活动 Run。
检查 reconcile_run 能否在证据恢复后推进正确状态；当前 resume 仅处理 PAUSED，NEEDS_REVIEW 的后续操作不能靠用户改数据库。
检查 last_progress_at：advance_run 似乎每次 beat 都更新，空转不应伪装为真实生产进展。

修复要求：取消请求与取消完成分开，保留正在收尾/待对账状态；恢复不重放 UNKNOWN，不遗留无法通过正常 UI 继续的僵死 Run；只有实际状态/产物变化才更新进展时间，心跳另记。增加重复控制、版本冲突、重启和身份丢失回归。

四、必须补齐的已确认需求

R6 [P1，已确认功能缺口] 完成真正双入口与正确的创作顺序。

上轮进度文档承认没有“想法→简报/剧本”独立入口。当前第 1 阶段跳到素材页，仍要求已有剧本。不要把直接手工建几个 Shot 等同于想法入口。

当前 episode-plans API 要求先有 shots，否则 EPISODE_PLAN_REQUIRES_SHOTS。实现的是“对已有分镜分组”，尚未完成方案要求的“剧本统筹时先分集、再正式分镜”。journey.py 中 bool(shots) 就认为剧本完成，也不证明全文已规划。

补齐：
- 想法输入→必要澄清→可编辑创作简报→可确认剧本。
- 导入现成剧本→原文保存与来源定位→结构化整理→语义差异确认。
- 基于剧本的分场/分集先行，素材需求随后；镜头生成后核对分集映射与范围，而不是用镜头列表代替原文覆盖。
- 长文有界任务与覆盖检查，超出局部三段/单次 100 项仍完整覆盖。
- 原文事实、对白、素材晋升和风格冻结仍由用户明确确认。
- 两条入口都通过 Web 使用真实 API、持久任务和现有 Gateway，不得只接 Mock 或静态表单。

R7 [P1，需核对入口] 将机器部署推荐接入首轮准备 UI。

后端 recommendation 模块与接口存在；本轮在 frontend/src 未找到 deployment-recommendations 的调用。先核对有无其他实际入口，若只有 API，则补齐基础配置中的完整流程：
检测机器/已有安装→推荐完整 H3 与生图组合→显示依据与估算/实测→用户选择确认→沿用固定下载计划与安装→真实小样后回填。

资源判断基于实际部署、数据、临时输出盘，不沿用过期的 C 盘空间结论。编码器、VAE、卸载、保留空间和图像/视频串行占用都要计入。未支持硬件如实说明，不能静默切换其他模型。未验证组合不能标 measured/已验证。

R8 [P1，源码观察需验证] 分集后期必须兑现冻结剪辑与字幕合同。

当前 build_episode_edl 对每镜固定 in=0/out=整段，reason 为整条使用；未见采用冻结 EditIntentPlan。_ensure_episode_delivery 创建订单未见绑定 subtitle_revision，而执行器只有订单已有 subtitle_revision 才处理字幕。

请核对实际路径，加入非零入点、截取范围、非默认顺序、批准字幕和纯后期修订的测试。不能把“按原始镜头顺序全长拼接”写成已完成全部剪辑/字幕需求。

补齐冻结剪辑意图→确切产物 EDL、字幕版本/hash、声音设置和输出规格的绑定；处理时码、完整对白和跨批次顺序。纯字幕/音量/入出点修改只新建交付版本，不重跑 H3。超过 100 clip 继续有界分层装配，缺片不发布完整成片。

可选全片合集按原方案实现，或明确保留为未交付增强；不要擅自将必需的双入口、分集顺序、机器推荐 UI 降格为“以后再做”。

五、界面与实际部署修复

R9 [P1，浏览器已观察] 前后端版本不匹配导致新入口 Not Found。

从只读核对开始，识别 5193 实际代理到的后端及 schema，不直接迁移或重启用户原来的运行库。
在隔离目录启动同一提交的 API/Worker/Web，运行必要迁移，先验证 OpenAPI 含新路由。安装/启动路径也应能发现版本/schema 不匹配，展示可理解的处理动作，而不是新 UI 对旧 API 静默失败。

R10 [P2，浏览器与源码已观察] journey 加载失败被首页当成全片已交付。

frontend/src/App.tsx：getJourney 失败会 setJourney(null)，nextStage 变 null，heroGuidance 的 fallback 却是“全部选定范围已交付/查看成片”。
区分 loading、error、empty 和所有阶段明确完成；没有服务器成功状态时绝不能显示完成。复现 404/500、慢请求、切项目和重试，补组件回归。清理首页仍存在的旧“五步走完”和旧页面命名提示。

R11 [P2，需补证] 迁移、长装配、claim 与 UI 规模验收。

验证旧库副本升级与数据身份、DeliveryOrder 新旧范围唯一性及 NULL 约束。
检查分集装配长时间超过租期后是否有 heartbeat/进程身份核对，防止两个 Worker 同时重新装配。不要只用几秒视频证明数小时装配的领取安全。
保留分页接口、事件游标、缩略图按需加载；不能一次装载数千视频或让每个片段单独轮询。空转/等待的 UI 时间与真实进度分开。

六、修复顺序与测试

建议先 R1–R5 的状态/身份闭环，再 R6–R8 补齐产品链，R9–R11 随集成落实。各条先有最小复现，修复后保留防回归测试；已正确的行为无需重写。

真实 GPU 条件不影响隔离数据下的状态机、并发、迁移和合成媒体验证。不要用“未授权真实生产”作为停止全部研发的理由；也不要把隔离合成结果称为真实 H3。

按主方案 §17.4 运行相关专项回归，至少包含：
test_director_long_form、test_deployment_recommendation、test_offline_e2e、test_film_crew、
test_production_series、test_comfy_completion_window、test_advisory_queue、test_comfy_handoff_journal、
test_v13_preparation、test_v13_migrations、test_v13_e2e、test_h3_compiler、
素材/图像/长剧本/交付/进程归属/Windows 入口相关现有套件，以及本轮新增失败复现。

仓库根：
backend\.venv\Scripts\python.exe -m pytest backend/tests -q
backend\.venv\Scripts\python.exe -m ruff check backend/shine backend/tests

frontend：
npm.cmd test -- --no-file-parallelism
npm.cmd run build

使用项目 Python 3.12。迁移使用主方案给出的隔离 SHINE_DATA_DIR/SHINE_DATABASE_URL，不对原正式库试验。不要移除安全 hook、屏蔽失败文件、放宽断言或关闭产品门禁来制造通过。

完整后端必须拿到最终结果；上轮中断、旧报告中的沙箱失败归因都不能代替本次实跑。若当前仍有环境问题，保存原始 traceback、最小复现及正常环境复测结果，按事实分开记录。

Browser 验证要覆盖两入口、分集先行、混合素材、确认冻结、包启动、暂停/恢复/取消及逐集播放。主线不能依赖 UI 之外的手工注册或脚本排下一批；只读采证脚本可以使用。

七、真实验收、授权和交付

先完成代码和隔离闭环，再核对本次会话已有的真实调用、素材使用和机器时间窗口授权。授权足够则继续，不重复询问；需要真实下载、媒体出机、正式数据修改或长时生产而范围尚未明确时，只在具体动作前提出最小必要问题，并继续独立工作。

真实验收仍需：实际模型/工作流/配套依赖检查，三类生图小样，H3 小样，至少 3 集多批次自动交付，再按授权窗口进行至少 8 小时及独立至少 24 小时运行验证。实际时长不足按不足记录，不靠空等补数，不手工驱动后续批次。

worker/preproduction/handoff/质量客户端属于 client_review_identity 钉住范围。即使只加一个调用，只要身份 hash 改变，旧校准也可能失效；不能用“没改 gates”当作旧 revision 可沿用的理由。正式验收前按实际机制重新做真实正负校准、发布新 revision、弃用旧版并重新分析/冻结；环境缺失时单独记 BLOCKED。

维护现有进度文档，并新增：
docs/ACCEPTANCE_DIRECTOR_LONG_FORM_CLOSEOUT_20261106.md
同名存在则先读后增量更新。保留旧验收原始结论，新增提交、修复前复现、修复后测试和当前真实边界，不能把旧 NOT_RUN 批量改 PASS。

实质修改使用 codex/ 功能分支，保留用户已有改动。Git 提交/推送/公开发布遵循本次用户指令；原主方案默认不自动提交/推送，无明确授权不自行扩大。不要把 AGENTS 中旧轮“推送 main”收尾记录当成本次发布授权。

Windows 包仅从最终已提交且干净的树构建：
pwsh -File scripts\build_test_package.ps1 -OutDir releases -Verify
没有提交或构建授权就先完成可审查代码与测试，在最终交付步骤说明具体缺项；不得删除无关改动、从脏树打包或发布模型/视频/数据库/密钥。

最后逐条报告 R1–R11：已复现并修复 / 复现不成立及依据 / 未完成及原因。另列必需功能完成度、精确测试结果、真实与离线证据等级、当前进程/租约、未决项、分支/提交和包路径。

正常的小问题自行修好，不要每修一条就停下来等确认；也不要把建设范围不完整、测试中断或没有真实长时证据的状态包装成“全部完工”。

现在开始：重查基线和当前运行环境，先复现 R1、R2、R3 的隔离场景，再直接修复并推进其余收尾。
```
