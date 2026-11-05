# 导演模式验收收尾与新增能力：本机 Agent 总提示词

> 日期：2026-11-05。把下列代码块全文交给负责本机建设的 Agent。
> 配套事实清单：[本次验收报告](ACCEPTANCE_20261105_DIRECTOR_REVIEW_AND_CLOSEOUT.md)。

```text
你在 F:\ClaudeWorkSpace\projects\shine-video-studio 继续完成导演模式建设。请直接做代码、测试、真实 UI 验收与交付，不只写方案。不能因为前轮写过“真实三类小样通过”就把完整导演旅程当成已完成。

一、必读及基线

按当前 AGENTS.md 的顺序阅读三份权威文档，然后读：
docs/DIRECTOR_AI_MATERIAL_PREPARATION_PLAN_20261104.md
docs/PROGRESS_DIRECTOR_AI_MATERIAL_PREPARATION.md
docs/ACCEPTANCE_20261105_REAL_IMAGE_VALIDATION.md
docs/ACCEPTANCE_20261105_DIRECTOR_REVIEW_AND_CLOSEOUT.md
以及相关 ADR、当前源码和测试。

本次审查基线 main / 693751fe77c4d613dcd6aa160bb60e9dd078b1f7。开工必须重查 HEAD、status、分支、diff、API/Worker 进程、数据目录、series、未知任务、GPU 租约。存在 8000/5173 与 8133/5193 两套监听，不能混用数据与证据。保留 .zcode/、个人文件和预先修改。
实质开发用 codex/ 功能分支或隔离 worktree。沿用唯一 SQLite、Gateway、Worker、任务/attempt、候选/主资产、GPU 租约、审批与审计，禁止另造平行调度系统。

二、先修复已确认缺陷

1. [P0] 前期编剧 UNKNOWN 被重复调用。
   scenes.py 的复用只看 SUCCEEDED；同输入第一次模拟响应丢失后第二次 scenes/prepare 仍调用 provider，本次隔离复现 provider_calls=2、unknown_tasks=2。
   为项目/原文修订/场或片段/工具及模型指纹建立持久输入身份和原子认领。RUNNING 返回原任务，UNKNOWN 只查询/对账，不自动重发。显式替代请求保留旧 UNKNOWN 与来源，不用重试按钮偷换语义。
   加并发、刷新、Worker 重启、超时二次请求回归。本机 docs/evidence/director-acceptance-20261105/test_unknown_scene_replay_repro.py 断言的是旧缺陷存在，修复测试必须反转为单次调用。

2. [P1] 原文分段有损且长场不再拆分。
   source_spans.py 会丢首个标题前的前言；200 spans 后 break；有标题的9000字场仍原样成单场。
   保留前言/人物表/锁定事实，所有文本含长单行均按真实上下文预算继续拆分，保留稳定来源偏移与编号，不静默截断。来源引用必须限于当前输入范围，拒绝跨场或伪造 scene_key，而不只检查全剧 catalogue 是否存在。

3. [P1] 修复“所选素材”虚假选择语义。
   当前候选无缩略图和选择控件，promoteSelected 自动取某个 run 中首个未晋升候选。新增真实逐项选图、版本/批次定位与放大预览。只有用户选中的候选才能申请/消费 grant 晋升。validation 小样不能无提示混入正式需求。

4. [P1] 已有素材绑定与策略回填。
   当前 strategy[id] ?? 'generate' 未读取后端策略，reuse_master 没有 asset_id 选择器。恢复权威快照，提供本项目素材/版本选择与待确认上传状态，未确认上传不默认重复生成。
   service.evaluate_materials 先按身份找第一条，可能忽略 bound_asset_id。显式资产ID/版本/hash必须优先并校验，不能擅自选同身份其他版本。加双版本回归。

5. [P1] 远程文字模型授权链未闭环。
   scenes/prepare 支持 allow_remote_text，但素材页面没授权入口；Worker producer brief 对所有远程 provider 直接拒绝。
   增加明确文字发送授权，绑定项目/文档修订/角色/provider范围，持久化并由 Worker 验证；失效/撤回有行为。仅发送必要文字，不传素材字节，不删除同意门禁、不静默换 provider。不用“LLM READY”代替可执行性。

6. [P1] 补导演页 resume/reconcile/regenerate。
   后端有接口，页面只有 cancel。按 allowed_actions 实现单项恢复、UNKNOWN核对、修改描述再生成与历史对照，重复操作幂等，不自动覆盖旧图。

三、用户新增需求 R1：素材网格

上传图片与AI候选全部使用紧凑响应式网格，不再每项独占整行。沿用现有设计语言，卡片包含缩略图、身份/用途、版本、状态与主要操作。图片 contain，不能裁掉头部和完整道具；点击放大、明确选中态，选择状态按项目/需求/版本隔离。
支持角色/场景/道具分组与合理按需加载；不一次加载所有原图。宽屏多列，591px侧面板和窄屏不溢出，键盘可选择、按钮有名称。暗色/亮色都验证。网格不是拖拽重新排序，不擅改H3引用顺序。
人工确认仍是独立动作，自动生成不等于批准。批量确认逐项快照/授权，部分失败逐项报告。

四、用户新增需求 R2：最后第四步视频网格与单段重生成

在当前 #d/deliver 成片页先展示实际H3工作流单次输出的N个生成分段，最后展示完整拼接成片。区分H3生成分段与editorial cut；一个生成段里的多个剪辑切点不能假装可各自重生。
各卡片显示序号、剧情/剪辑范围、时长、当前job/attempt版本、状态、预览/下载及“重新生成此段”；未完成段有占位与原因。播放器lazy/metadata按需加载。成片与delivery-order按artifact去重，历史交付折叠保留。

“重新生成此段”复用现有生产服务/series/shot revision/attempt与H3PromptCompiler，建立新可审计请求，保留旧通过版本；保持冻结Style/Rule/资产和工作流合同。用户调整镜头意图时创建新修订，不直接向H3提交自由文本。
普通已完成的独立段可以单独重新排队，不能自动重新跑其他独立段；同时禁止把同一shot的重复提交当成合法重生成。
有last_frame等真实依赖时展示影响范围，按现有规则失效/复核后继。依赖不是借口无限重跑，独立已通过版本不受影响。
运行中/UNKNOWN/租约待核对时禁止再次提交并提供可理解原因。GPU所有权、非重叠模型阶段、幂等和崩溃对账继续有效。
新段通过既有门禁、明确成为当前装配版本后，创建新的delivery revision。旧视频保留，不静默覆盖、不把未通过版本拼成已接受成片。
测试至少3个独立段只重做第2段，确认第1/3段job/媒体hash不变；另测有连续性依赖的后段正确失效。刷新和重启不会重复提交。

五、用户新增需求 R3：长剧本总体大纲与分步分镜

这是一项实际编排扩展。不要把当前素材按场循环当成已经满足要求。
明确两个概念：生产段落=章节/场群、承载分步工作；生成分段=H3单次4–15秒视频。UI和数据库用清晰不同的键。

实现：
1. 保存完整原文、hash和offset，分块局部提取再层级归并全剧大纲；不能一次发送数小时全文，也不能靠截断输入解决超限。
2. 版本化大纲包含故事主线、人物/关系、时间线、锁定事实、章节/场群索引与来源。用户确认后才用于分步正式分镜，摘要不能替代原文。
3. 服务端提出可生产段落列表，展示顺序/剧情范围/估计时长/素材缺口/状态；用户选择本次一个或若干段落，不默认生成全剧所有分镜。
4. 每次只把选定段相关原文、确认大纲、人物/资产/冻结Style/Rule合同与必要承接摘要送给现有crew链。正式ScriptBreakdown及H3确定性时长/对白/参考门禁不变。
5. 大纲/段落/分镜运行由常驻Worker持久调度，API返回任务ID，GET轮询；可暂停/取消/失败段恢复。页面关闭、Worker重启、重复点击不会丢进度或重放UNKNOWN。
6. 全局人物entity_id稳定；剧情/空间/服装/道具/上一段末状态有版本化连续性，缺前序事实如实阻塞/待确认，不能凭空声称前镜已通过。
7. 原文/大纲/用户事实变更精确失效关联段与依赖；旧通过媒体保留，用户选择新当前版本后更新装配。
8. 旧短剧本项目继续可用；长单行、超200行、有标题超长场、无标题长文、多章节和输入上限都做有意义的边界测试。

六、当前环境恢复和真实验收

本次主栈只读采样：/setup/readiness 显示 SERVICE_VALIDATION_STALE_CONFIGURATION、ACTIVE_RUNTIME_CONFIGURATION_CHANGED、render_provider=unavailable；/system/status 显示 worker_online=false、running_jobs=0。
这是当前运行阻塞，先核对身份/配置/数据/心跳，按受管路径修复和重新验证，不手改READY、不盲停外部ComfyUI、不按时间过期释放不明租约。检查活跃任务后再做启动链验收。

前轮三类真实Qwen小样成功只作为已有证据。重新走正式素材批次：真实编剧→用户确认→真实出品人brief→受管Qwen→用户逐项选用→风格冻结→选定生产段落正式分镜→H3引用新素材→分段预览/重生成→新合成版。
用户必须明确做素材晋升/风格冻结/艺术评审；你不能替用户点击来伪造人工验收。准备好具体图片/差异与操作入口再交用户处理，同时继续其他独立工作。
修改client_review_identity绑定的Worker/预制作/质检等文件时，在真实H3验收前完成正负VLM校准、新revision/弃用旧revision、重分析/冻结。不得用旧计划绕开。

七、回归与交付

本次审查已实际跑过：素材相关7个现有测试文件79 passed；前端18文件130 passed；tsc/Vite build成功；Ruff成功。这不覆盖以上缺陷，不能拿旧计数作为修复证据。
增加UNKNOWN去重/并发、原文覆盖、候选选择、显式绑定、多版、远程授权、网格、单段重生、连续性、长剧本分步生产专项。
按当前AGENTS.md运行offline_e2e、film_crew、v13系列、completion_window、production_series、advisory_queue、handoff、H3 compiler等要求与完整后端/Ruff/Alembic，前端test/TypeScript/Vite；保留Windows CI。
验证开发机启动入口与安装包两链，至少干净启动/端口占用/停止；改端口或进程记录补半启动残栈。更新实际下载量/占用与已知限制，最终提交树构建 -Verify，保留旧包，不夹带模型/素材/生成媒体/数据库/密钥/evidence/日志。
Git只add明确文件，保留预先修改，不强推。提交/集成遵循当前授权和仓库规则，报告实际SHA与包路径，不伪造发布。

先把任务拆成可接续小阶段并写实际进度，然后持续实施。不要重复询问用户已经明确的网格、单段重生、总体大纲和分步分镜产品决定。
最终单独写收尾验收报告，逐条关闭A01-A08及R1-R3；区分IMPLEMENTED、OFFLINE_VERIFIED、REAL_QWEN_VERIFIED、REAL_H3_VERIFIED、HUMAN_REVIEW、WINDOWS_AND_PACKAGE和BLOCKED/NOT_RUN。任何必要门槛未完成，整体仍为PARTIAL / NOT ACCEPTED。
```
