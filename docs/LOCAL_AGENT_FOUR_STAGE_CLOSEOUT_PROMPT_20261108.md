# 本机 Agent 执行 prompt · AI 编导四阶段收尾

你负责在 `F:\ClaudeWorkSpace\projects\shine-video-studio` 完成这轮工程收尾、回归和验收。基线是 `anjingdtl/shine-video-studio` 的 `main@7081104a79e6758228eb98e96d74e0bb36da52f8`。先核对当前 HEAD 与远端，保留用户已有工作。若已有后续修复，先用反例验证，再缩小变更。以可运行的代码和证据交付。

## 1. 开工与边界

1. 按 `AGENTS.md` 顺序读项目基线，再读 [四阶段建设方案](AI_DIRECTOR_FOUR_STAGE_LONGFORM_PLAN_20261107.md)、[本轮对抗审查](ADVERSARIAL_REVIEW_FOUR_STAGE_PIPELINE_20261108.md)、[上轮实测断点](PROGRESS_20261108_FOUR_STAGE_LIVE_ACCEPTANCE.md)、[Token 政策](LLM_CONTEXT_BUDGET_POLICY.md)。F01–F11 是收尾清单。以 HEAD 行为复核并记录分支、commit、dirty 文件、迁移头、当前生产栈和验证环境。
2. 使用隔离 feature branch 或 worktree、临时 SQLite、合成素材和本机独立端口。原有 `gui-test-screenshots/test_novel.txt`、用户库、配置、模型和所有历史产物留存。真实验证前重新检查进程归属、队列、GPU lease、权威 DB、工作流及启动记录。
3. 复用既有 FastAPI、React、SQLite、Gateway、Worker、crew、DirectorPackage、ProductionRun 和 ProductionSeries。视频仅由本机 MiniMax-H3/ComfyUI 执行，导演模式默认多参考 Ref2VA。正式 prompt 统一由 H3PromptCompiler 输出。素材晋升、Style Bible 冻结、远程文本同意、真实开拍均沿用用户确认。
4. 真实文本验证使用已配置且获授权的 Profile 与可发送的合成文本。真实 H3 使用隔离作品、明确的本机授权及经检查的资源。遇外部进程、归属不清的租约、待对账任务或缺授权，保留状态并继续所有可执行离线项。分列剩余阻断。涉及被 `client_review_identity` 钉住的源码时，按 `AGENTS.md` 完成适用的真实正负 VLM 校准和 revision 迁移，或准确报告此项未验。保留 advisory 判定与确定性 QC 的边界。

## 2. 先封重复调用、授权与恢复窗口（F03–F05）

- 把创作操作分成稳定的逻辑输入身份、独立物理 attempt 和可校验结果回执。身份至少包括 project、工作单元、父 artifact ID/hash、实际输入/角色、Profile fingerprint、契约版本。入队与幂等命中使用同一规则。排队后发现输入变更，停止这次 attempt，返回受影响范围。
- 发送前以当前 claim fence 持久化请求、预算、授权 scope 和 `SENDING`；网络调用在 SQLite writer 事务之外。使用已有 heartbeat 或等价所有权保护，响应落库由 status、owner、state_version、当前输入版本共同约束。artifact、任务终态、回执和 usage 同事务提交。
- 在现有代码实际提交边界做故障注入。历史孤儿 artifact 只有在 task/project/kind/role、输入链、契约版本和重算 digest 唯一相符时才采纳。过期 Worker 迟到结果不能推进当前版本。外部结果未知保留 `UNKNOWN`，普通 resume 和再次排队都要拒绝。若供应商支持权威对账，优先采纳其证据；需要用户发起明确的新尝试时，用独立授权和 attempt。
- 卷分镜五工种逐步绑定派生文本来源和持久同意。修复 `volume_storyboard_draft → generate_storyboard → FilmRunRequest → Dispatcher` 的 consent 透传，校验每次实际发送用的角色与 fingerprint。已有 loopback/mock 路径保留；拒绝/撤回/换 Provider/篡改 hash 时，fake transport 计数为零。
- 把 UNKNOWN 处理拆成「核对结果」和确证未发送后的「继续」，由服务端返回可用操作。UI 恢复动作携带最新 state_version，不按错误文案推测是否可重发。

## 3. 锁定来源、确认链与连续性（F01、F02、F10）

- 用稳定卷键定位业务卷，用独立 revision/hash 定位具体范围。母本的原文件、规范化文本、哈希和解析告警有可追溯证据。自动与手工分卷均校验无空洞、无重复、保序，Unicode offset 清楚。旧文档指针只有在项目、母本、范围、文档 hash 完整匹配时复用。没有足够证据的历史记录保留可读并提示复核。
- 将一句创意、粘贴/上传、已有剧本纳入同一来源合同。已有剧本允许忠实整理。沿用文件大小、格式和资源配额，按方案补齐仍缺的多文件 manifest/跨文件覆盖，明确扫描件或解析失败提示。按来源处理超长单章与过长场次，保留映射。
- `queue_bible` 从当前 `VolumePlan` 解析唯一有序的卷大纲集合。全局确认必须覆盖全卷当前 revision，持久记录来源和重大跨卷未决事项。允许部分蓝图进入草稿写作，队列、Worker 和 UI 使用同一资格判定。草稿转正式时检查来源及关键事实差异。把原文事实、用户锁定、改编提议、未决问题分开，重大未决只阻塞受影响的发布范围。
- 给 SceneScript 的前场状态和连续性输入固定实际 revision/hash。补齐稳定的分集/场次 ID、顺序、可读文本、对白及衔接状态。用已确认场次构成可单独编辑和确认的 EpisodeScript 与卷摘要。保存新版而保留历史，父 revision 变化可影响检查。用具名错误处理文本超限。局部重生成只消费当前选中的范围，前后场受影响时标出具体原因。

## 4. 长文本预算与场次分镜（F08、F09）

- 长篇创作使用能力感知预算。已证实可用总输出上限取模型能力、配置硬顶、adapter 上限的有效最小值，单次总 completion ≤ `floor(0.60 × effective_output_max)`，再受共享窗口可用余量限制。已证实 128000/131072 分别得到 76800/78643。Profile 配置值仅能约束，不能单独认证能力。能力未确认时给保守 cap 与来源。Adapter 只有确证支持相应参数时才发送。
- 预算覆盖实际消息、原文、schema、工具、安全余量和 completion reserve。用 tokenizer 或标记保守估算，对独立输入窗口按该 Provider 语义分别验证。保存 usage 数值与 finish/stop reason。reasoning 缺失标记 unavailable。私有思维链文本、凭据与用户原文不得进入诊断日志。
- 先按自然场次拆分，超长单场按可确认节拍进一步切片。复用五工种，每批引用当前场次、前后状态、锁定事实和按顺序的来源事件。旧手工/专家调用边界独立保留。给每个单元持久状态、失败位置、可续跑步骤和结果回执。输出截断标记 `INCOMPLETE`，只保留独立校验通过的单元，接续使用明确边界。
- 分镜审阅按「集 → 场 → 片段 → 镜」展示。验原文事件、对白顺序、明确删改授权、时长与动作/空间连续性。草稿可以缺已批准素材和冻结风格。正式应用用已有素材/H3 规则，遵从 Style Bible、WorkflowProfile、parameter_map、参考槽位和确定性 preflight。

## 5. 从批准场次到可交付卷（F06、F07、F11）

- 建立 `SourceMaster/VolumePlan → Outline/Bible → EpisodeScript/SceneScript → CameraPlan/ContinuityReport → 已应用 ShotSpec → Package` 的有序 lineage。使用稳定 ID 和 revision/hash 验证集合与顺序，核对归属、缺漏、重复、过期、被授权删改。每个硬连续性依赖绑定可验证的前镜 job 与版本，旧媒体或不支持的跨 series 边界具名阻塞，改成 CUT 走明确确认。
- 由已确认 EpisodeScript 推导交付 EpisodePlan，保留集标题、顺序和场/镜映射，时长不满足时定位到集或镜头。移除以计数和布尔覆盖许可代替映射的路径。交付按选定卷和确认快照幂等，重复提交返回同一有效回执；冲突回报用户重新核对。`package_readiness`、Run 创建、派发、恢复共用准入依据，运行时仍执行已有 H3 和 preproduction gate。
- 编辑任意上游场次、蓝图或边界时，按依赖让关联未生产版本 stale，冻结历史可读。针对已在途的任务保留现有对账及租约处理。卷三无依赖编辑后，卷一已 PASS job 与交付 SHA 应一致，H3 submit 计数不增加。
- 导演模式保持四步和当前步的主要操作。承接已有导入、边界调整、分集编辑确认、逐场草稿审阅、局部重做、正式 apply、当前卷交付和精确 package 的生产页。服务端提供已确认/待补/需复审/已交付进度及下一可用操作。后续卷可以独立选择，刷新和切项目后从权威台账恢复，项目 A 的异步回包不能覆盖项目 B 页面。默认向用户显示可操作的中文阻塞原因，技术诊断折叠。

## 6. 参考模块的转化

以对抗审查第 4 节的固定 commit、模块、许可证为依据，独立实现以下合同。把实际采用范围和来源写入 `docs/upstream-audit.md`。

- **Toonflow-app** 的分章事件、分集剧本、分镜回读审阅转化为持久的事件来源、场/镜映射与确认回执。对应 F01、F06、F09。
- **Huobao Drama** 的单集成员资格验证转化为正式应用和交付前的作用域判定。根许可为 CC BY-NC-SA 4.0，使用领域约束，代码、prompt、UI、素材不进入 Shine。对应 F06、F07。
- **NarratoAI** 的格式反例转化为隔离 Gateway fixtures。解析、内容完整性和生产验收分层。对应 F08、F09。
- **Temporal** 的活动幂等与崩溃窗口转化为 SQLite 本地 fence、持久回执和安全对账，产品依赖保留现有编排器。对应 F03–F05。
- **OpenAI Docs** 的逐级评测用于组织合成 fixture、实际模型样本和人工评分。评测可在本机跑。 [Evaluation Best Practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices)

## 7. 必须执行的验收矩阵

| 用例 | 可检查的通过条件 |
| --- | --- |
| 作用域污染 | A/B 项目、同项目无关旧文档、同卷已替代大纲、第二卷缺失、错文档 fallback 均覆盖。任何越界输入的远程发送数为 0。部分蓝图有明确草稿状态。 |
| 分卷与来源 | 单卷、至少三卷、跨文件、重复标题、CJK/emoji、空行、尾部、超长单章。同一母本两次改卷界只能读取当前区间。每个源 span 归属唯一，旧确认命中冲突。 |
| 草稿与变更 | 草稿蓝图真实通过 Worker 进入场次草稿。相同蓝图确认可复用，有相关差异则具名复审。修改场次后关联包不得创建或派发新 Run，独立卷不失效，历史产物可读。 |
| 外部副作用 | 发送前本地拒绝、SENDING 后崩溃、结果事务前后中断、真实 timeout/5xx 的故障替身、双 Worker、过期 owner、重复点击。记录传输层计数。UNKNOWN 自动及常规人工恢复新增发送均为 0；唯一有效已落库结果被采纳。 |
| 授权 | 不同角色与 Provider、授权撤回、哈希变更、越界派生文本。缺权限的直接 API 和 UI 都无远程副作用。 |
| Token 与输出 | 未知模型、仅配置大 cap、不同 adapter、带/不带 reasoning usage、共享窗口、硬超限、截断、合法 JSON 漏场。证明实际 wire 参数和状态，超限在发送前拒绝或无损拆分。 |
| 分镜覆盖 | 总剧本超过 60,000 字可分批，单场超限能拆或具名阻断。所有镜头堆在一场、顺序打乱、错卷、重复镜、过期 Shot/Scene/分集、缺对白均不能正式发布。时长和分集边界来自确认合同。 |
| 跨卷交付 | 选卷二交付并进入确切包；刷新重试无重复包；卷三修改后卷一历史保持；素材/风格/工作流或参考依赖缺失时阻塞准确，仍可继续独立草稿。 |
| 浏览器旅程 | 真实上传短篇 1 卷，再用多卷合成样本走导入、提炼、蓝图、剧本、分镜、审阅、交接并恢复一处中断。保留实际按钮、状态与服务端 receipt；Mock 与真实 LLM 分开记录。 |
| 真实 H3 | 在具备本机条件和授权时，选择已确认卷，走浏览器到现有 Run，由常驻 Worker 串行交给 Ref2VA，至少有多集和多批次证据。核对真实 prompt ID、attempt/lease、产物 hash、技术 QC、逐集 FFmpeg 装配、实际播放和最后租约状态。艺术接受另记。 |

先跑本轮定向回归，再跑全量。后端包括 `test_pipeline.py`、`test_creative_output_budget.py`、`test_story_preparation.py`、`test_creative_script_task_lifecycle.py`、`test_film_crew.py`、`test_h3_compiler.py`、`test_offline_e2e.py`、`test_production_series.py`、`test_comfy_completion_window.py`、`test_comfy_handoff_journal.py`、`test_advisory_queue.py`、`test_director_worker_scheduling.py`、`test_director_long_form_regressions.py` 和 V1.3 指定回归。路径以当前仓库为准。

记录实际 PowerShell 命令、退出码、通过数和失败原因。包括 `backend/.venv/Scripts/python.exe -m pytest backend/tests -q -p no:warnings`，Ruff 检查与格式，Alembic 隔离空库升级、旧库副本升级、`heads` 和 `check`。前端跑 `npm.cmd test`、`npx.cmd tsc --noEmit`、`npm.cmd run build` 与浏览器回归。遵守项目 CI。若有明确打包任务，用最终干净已提交树及 `pwsh -File scripts/build_test_package.ps1 -OutDir <dir> -Verify`。

## 8. 交付与停止条件

提交可审阅的小步变更，附迁移/兼容策略、UI 到 API 的路径、F01–F11 对应复现与测试、证据文件路径和提交号。只有全部软件门禁和回归通过才报软件验收通过。分别给出 `OFFLINE/MOCK`、`REAL_LLM`、`REAL_H3`、`VOLUME_DELIVERED`、`HUMAN_ACCEPTED` 的范围和证据。被授权、运行时或硬件阻断的层标记 `BLOCKED` 或 `NOT_RUN`，精确记录可安全恢复的下一步。用户未要求外发、合并或发版时，交付本机分支及可审阅结果。
