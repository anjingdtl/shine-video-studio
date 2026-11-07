# 本地 Agent 建设指令：AI 编导四阶段流水线与超长作品分卷交付

你是 Shine Video Studio 的本地总工程师、架构师、测试负责人和真实用户体验验收负责人。本次任务不是再写一份建议书，而是**根据已批准的方案完成真实软件建设、对抗测试、问题修复和交付**。授权你自主读取、设计、编码、编写迁移、测试、调试、修复、整理文档和提交分支；不必在每个普通代码步骤中停下来询问。不得绕开安全边界、改写历史产物或虚构验收结论。

**本机项目位置**：`F:\ClaudeWorkSpace\projects\shine-video-studio`  
**远端仓库**：`anjingdtl/shine-video-studio`，默认分支 `main`。  
**首要依据**：`docs/AI_DIRECTOR_FOUR_STAGE_LONGFORM_PLAN_20261107.md`（编制提交 `874495d922bde9d3a764f3d0979da7d2dc046510`）；其次 `AGENTS.md`、`docs/DIRECTOR_WORKFLOW_AND_LONG_FORM_PRODUCTION_PLAN_20261105.md`、`docs/LLM_CONTEXT_BUDGET_POLICY.md` 及最新代码/测试/迁移。

## 一、启动与保全

1. 进入上述本机项目，读取 `git status`、当前分支、最近提交和进程/任务状态。**保留所有未提交工作和当前数据**；若存在脏工作树，先安全隔离/备份并报告，不用 `reset --hard`、`clean -fd`、force push，也不覆盖其他 Agent 的修改。
2. `git fetch` 并核对远端最新 `main`（不能假定 874495d 仍是 HEAD）。在工作树干净且无其他写入时建立专用实施分支；若有并发修改，按可回滚的方式处理冲突。先阅读项目的全部约束文件，检查当前代码是否已有同类模块，优先复用，不能造第二套权威数据/Worker。
3. 检查本机是否有 live H3、ComfyUI、ProductionRun、未知结果 LLM 任务或独占 GPU 租约。开发与测试默认使用隔离数据库、隔离端口、Fake/Mock 服务；**绝不为测试终止、重启、占用或篡改正在运行的真实制作和 UNKNOWN 台账**。
4. 输出一份「当前代码审计与分阶段改造清单」，但**不要停在规划阶段**；继续实际建设。

## 二、必须落地的用户目标

在导演模式里把文字创作统一为一个简单的「AI 编导流水线」页面，四步始终是：**①文本导入 → ②故事大纲提炼 → ③剧本编写 → ④分镜编写**。无论一句创意还是百万字小说，用户都使用同一套四步 UI；后台可自动分卷、分集、分场、多轮 LLM 处理。

必须实现：
- 双入口统一（想法/小说/现成剧本），不可变母本、解析完整性证明、可追溯来源、用户可编辑分卷。
- 全局 Story Bible 与跨卷连续性，先确认全局主线/人物/伏笔/结局方向，再逐卷提纲、逐集剧本、逐场对白与动作、逐镜结构化分镜。
- 「剧本编写」是大纲与分镜之间**真正独立**的可编辑、版本化、可确认中间成果，不得以大纲或 ScriptBreakdown 冒充已确认的 EpisodeScript/SceneScript。
- 60% 动态输出硬预算：以真实有效 output max 为基数，若已验证 128000 则上限 76800 Token/轮；包括按 Provider 协议计入 completion 的 reasoning/thinking Token 与可见内容。未知/较低上限必须安全降级。共享/独立窗口正确预算，usage/finish_reason/截断检验必需。不得全局粗暴去掉 MiniMax 16384/Anthropic 4096 保护后假设模型必支持 128K；创建任务分类并逐 Profile 验证。旧小型结构化调用不要无意义增大。
- 分卷按叙事边界+输入/输出预算，而非固定字数；各卷能按集/场多轮续写、按来源范围完全覆盖、保留 checkpoint，用户可编辑/确认，支持局部重生成和精准失效。
- 分镜草案**不要求**先完成资产晋升和风格冻结；正式应用及真实 H3 交付保留人物、场景、风格、H3 编译器、workflow/parameter_map、continuity、包版本、人工确认等原有门禁。
- 全局总纲/跨卷锁定事实确认后，当前卷完成所有必需剧本和分镜确认、素材与风格准备及 scope 校验，即可**独立发布卷范围的导演包并进入已有 ProductionRun/ProductionSeries/H3**；后续卷仍可继续编剧/分镜。卷级部分完成不能显示全片完成。
- 原 H3/ComfyUI/素材中心接口/导演风格接口/剪辑与交付能力保持复用；H3 仍是唯一正式视频模型，Agent 不能自动晋升正式素材或自动冻结风格。
- 所有新功能必须通过真正接通的 API、数据库、Worker 和 Web UI 实现，不接受只有假按钮、静态 Mock UI、只写类名/空接口或只新增文档。

## 三、执行顺序（连续推进，不在阶段报告处停止）

**P0-A 基线与预算**：审计 `llm/gateway.py`、`context_window.py`、Provider adapters、`materialprep/agent_call.py`；实现创作任务感知的 60% 输出预算、thinking 计量、模型上限核对、续写安全检验与单元测试。

**P0-B 原文/分卷/全局总纲**：扩展 ProjectDocument/AgentArtifact 或必要的新表，新增 Alembic 迁移、可重复导入、SourceSpan 全覆盖、分卷版本/确认、GlobalStoryBible/ContinuityLedger；迁移后旧项目必须可读。

**P0-C 剧本编写**：加入 VolumeScript/EpisodeScript/SceneScript 的真实持久化产物、编辑/人工确认、逐集逐场生成、源文引用、全局审查、局部修改影响图、任务 checkpoint/续租/UNKNOWN 对账。处理现有 `MAX_SCRIPT_CHARS=60000` 与 API 字符上限，但不得仅删除防护。

**P1-A 分镜/卷级交付**：现有五工种 Agent 读取已确认剧本，分镜草案与正式 ShotSpec 应用分离。实现卷级 readiness、scope digest、VolumeHandoff、与旧 EpisodePlan/DirectorPackage/ProductionRun 的真实映射，不绕过任何旧生产准入。

**P1-B 简化 UI**：将原 `ScriptEntryPanel`、`StoryPreparationPanel`、`StoryboardPanel` 的能力整合进一个适合普通用户的四步页面；每步只突出「生成/编辑/确认」，卷/集/场左侧导航、进度和待解决问题明确，工程细节放高级面板。不要删除专家页面或破坏旧入口。

**P2 工程强化**：全局/逐卷一致性审校、长篇分页与性能、审计和 Token 成本透明化。若 P2 因真实 Provider 能力或本机资源受限，记录明确阻塞，但 P0/P1 必须交付可用端到端闭环。

每个阶段写代码、做数据迁移、测试并提交小规模 commits；遇到 Bug 自主复现根因、修复再验证，不允许为了让测试变绿而削弱生产门禁或改写测试预期掩盖问题。

## 四、对抗验收（全部尽可能用隔离环境跑）

1. 短篇单卷：创意/文本 → 总纲确认 → 分集分场剧本确认 → 分镜草案 → 正式准入检查。
2. 超长多卷：全母本完整范围 → 叙事分卷 → 当前卷继续创作并交付、下一卷同时待创作 → 全局人物/伏笔一致性。
3. Token 反例：128000*0.6=76800；131072*0.6=78643；真实 Provider 输出限制更小；配置更小；reasoning usage 有/无；completion 长度截断；schema 无效；共享上下文超限；所有情况安全、可诊断。
4. 版本与恢复：修改第 3 卷第 2 场不触发第 1 卷已通过 H3 重做；浏览器刷新；Worker 重启；调用前宕机；回答持久化后宕机；外部 UNKNOWN 不盲目重发；迟到 Worker 写回被拒；无重复集/场/镜头。
5. 准入与 UI：仅分镜草稿完成时不能误显示 H3 可拍，缺素材、未冻结风格、reference slot 未映射均明确阻断；恢复后刷新显示正确，普通用户可以独立完成四步操作。
6. 回归：旧项目、旧 APIs、手动镜头、Mock、现有生产调度、GPU 串行租约和 FFmpeg 交付保持可用。不能让创作任务饿死 H3/交付任务。

至少执行后端全量 pytest、Ruff、Alembic 全新库/升级/检查、前端 Vitest 与 build，以及直接相关的长篇、crew、Worker、production/continuity、安全与既有 E2E 测试。真实 LLM/H3/GPU 调用必须区分明确授权、实际成功与纯模拟结果；**不要主动下载模型权重或启动收费大量推理**。失败记录准确命令、退出码与证据；修复后重跑。没有真实证据的项只能写 `NOT_RUN/BLOCKED`，绝不写 PASS。

## 五、最终交付与自主权限

允许你自主完成本次建设范围内的代码编写、文件创建、隔离测试、非破坏性迁移、回归修复、技术文档与 feature branch 提交，并在测试通过后将实施分支推送远端供验收。禁止 force push、修改 live 作品/媒体资产、越过外部授权与主素材/风格人工确认、捏造真实出片或外部调用结果。不可逆操作、收费/大规模模型下载或会中断活跃真实任务的动作必须先取得明确许可。

结束时给出：
- 本轮新增/改造的架构和四阶段用户流程；
- 代码文件、迁移版本、API 路由、UI 页与真实入口；
- 对照方案逐条 `IMPLEMENTED / OFFLINE_VERIFIED / REAL_LLM_VERIFIED / REAL_H3_VERIFIED / BLOCKED` 的状态；
- 精确测试命令、通过/失败数量、剩余风险、下一步真实机验收事项；
- 分支、commit SHA 与远端链接，提交 `docs/AI_DIRECTOR_FOUR_STAGE_IMPLEMENTATION_REPORT.md`。

**不要只回复「设计完成」或「建议后续实现」。在安全边界内持续自主建设、测通、修复，直至 P0/P1 有可证明的完整软件闭环。**