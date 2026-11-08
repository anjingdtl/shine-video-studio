# Shine 视频工作台四阶段流水线对抗性审查

**2026-11-08 · 工程验收结论 PARTIAL / NOT ACCEPTED**

本地和远端 `main` 均为 `7081104a79e6758228eb98e96d74e0bb36da52f8`。对话「优化分镜流程」中的远端验收针对合并前分支。本文以合并后的代码、回归和隔离故障注入重新定级。真实长篇逐卷交付仍有阻断项。 [PR #8](https://github.com/anjingdtl/shine-video-studio/pull/8) · [审查 commit](https://github.com/anjingdtl/shine-video-studio/commit/7081104a79e6758228eb98e96d74e0bb36da52f8)

## 1. 从生产结果倒推合同

从不可变的创意或母本出发，先确定全书约束，再逐卷创作。某卷交付时，需要能逐集交付与已确认剧本一致的可播放视频。这个结果要求下列事实同时成立。

| 必须成立的事实 | 可证伪的验收条件 |
| --- | --- |
| 母本与全局事实有唯一版本 | 分卷区间按 Unicode code point 连续覆盖母本，卷衍生文档按母本和区间哈希定位。正式蓝图固定当前计划各卷大纲及重大冲突裁决。 |
| 编剧与分镜引用可核查 | 具有 `source span → event → scene revision → beat → ShotSpec revision → episode → package` 的有序映射。用户确认绑定实际展示内容，确认对白可逐句核查。 |
| 每次外部调用有明确状态 | 持久授权、请求身份、预算和发送意图可追溯。已知未发送可以重排，已持久化的有效结果可以采纳，结果未知保持待对账。 |
| 草稿可以并行推进 | 蓝图草稿可以驱动剧本草稿，并留下依据和差异。生产端拦住确定性错误并标出受影响卷、集、场。 |
| 已交付卷可以独立复查 | 导演包固定该卷的来源、确认链和镜头版本。相关新改动阻止陈旧包进入新生产，历史 PASS、媒体和交付可以按旧快照回读，无依赖卷可以继续。 |

四步骤属于导演模式创作层。拍摄沿用现有导演包、ProductionRun、ProductionSeries、Worker、H3PromptCompiler 和本机 ComfyUI Ref2VA。文字成功、分镜应用、包可启动、真 H3 产物、技术 QC、逐集交付和人工艺术确认各自需要证据。

## 2. 合并后已经成立的部分

- 分卷建议、卷文档物化、边界调整 API、总览和进度、卷大纲确认、EpisodeScript artifact、卷包端点和生成方案选择均已存在。上一份审查中的「完全缺失」结论需要更新。
- `queue_bible` 已加项目过滤，执行期也拒绝异项目的大纲 ID。Worker 对 `ACTIVE_RUNTIME_CONFIGURATION_CHANGED` 引起的旧饥饿场景有定向修复。大纲、蓝图、场次剧本调用有各自的持久同意检查。
- `SENDING` 崩溃路径可阻止重发，预置了有效 artifact 指针时可以采纳。实际双提交窗口和已分类为 `UNKNOWN` 的普通恢复路径分别见 F03、F04。
- 预算已有 60% 计算、预检分类和 usage 摘要。`128000 → 76800` 及 `131072 → 78643` 有离线回归，能力证据仍需细化。
- 上轮真实 MiniMax M3.1 Flash 浏览器记录覆盖新建作品、分卷、授权、三段提取加归并及确认卷大纲。母本经 API 入库，修复后的蓝图、场次剧本、分镜、交付为 `NOT_RUN`。 [实测进度](PROGRESS_20261108_FOUR_STAGE_LIVE_ACCEPTANCE.md) · [测试报告](TEST_REPORT_20261108_FOUR_STAGE_LIVE_ACCEPTANCE.md)

## 3. 对抗性发现

**复现**表示在隔离 SQLite、TestClient、mock 或故障替身下观测到。**静态**表示已沿源码查到，现场执行待补。P0 阻断四阶段正式交付。P1 影响范围、可用性或可核查性。

### F01 · P0 · 同项目旧大纲混入，蓝图范围可缺卷 · 复现

两卷计划仅确认卷一大纲，再加入同项目另一文档的已确认大纲。入队成功，trace 收进卷一与无关大纲，卷二缺失。项目过滤和逐项 hash 校验无法证明当前计划的全卷有序覆盖。卷大纲 fallback 也会在文档级指针缺失时取项目级指针，未比对大纲 payload 的卷文档 ID/hash。

**关闭条件** 由当前计划逐卷解析确切文档和大纲，锁定范围、顺序及 revision。可保留部分蓝图草稿，正式确认、交付和再启动均核验完整范围和重大开放事实。 [队列](../backend/shine/director/pipeline.py#L389) · [卷大纲读取](../backend/shine/director/pipeline.py#L712)

### F02 · P1 · 蓝图草稿入队与 Worker 条件矛盾 · 复现

实际任务流生成未确认蓝图后，场次任务成功入队并标记 `draft_unconfirmed`，Worker 因 `not bible.approved_at` 报 `PIPELINE_BIBLE_CHANGED`，剧本 responder 调用数为零。现有软门禁测试只覆盖入队和摘要。

**关闭条件** 队列和 Worker 共用草稿资格判定，固定依据。正式发布时与已确认蓝图核对，等价可沿用，相关差异须复审。 [入队](../backend/shine/director/pipeline.py#L730) · [Worker](../backend/shine/director/pipeline.py#L818) · [既有测试](../backend/tests/test_pipeline.py#L729)

### F03 · P0 · artifact 与完成指针分开提交，恢复漏采纳 · 复现

在蓝图 artifact 事务提交后、`_finish_pipeline_task` 写 trace 前注入崩溃，再模拟租约过期。DB 已有对应 `source_task_id` 的结果，任务仍为 `SENDING` 且无指针。新 Worker 将它标为 `NEEDS_REVIEW / PIPELINE_SEND_OUTCOME_UNKNOWN`，总调用数仍为 1。场次脚本采用相同写法。既有采纳测试直接设置了实际窗口中缺失的指针。

**关闭条件** 结果、来源、usage 与带 fence 的任务状态原子提交。历史孤儿结果须按项目、任务、角色、kind、契约版本、输入链及重算 digest 匹配，唯一且相符才采纳。丢失 fence 的 Worker 无法发布新结果。 [蓝图写入](../backend/shine/director/pipeline.py#L657) · [场次写入](../backend/shine/director/pipeline.py#L916) · [采纳测试](../backend/tests/test_pipeline.py#L401)

### F04 · P0 · 已分类的 UNKNOWN 可被常规恢复再次发送 · 复现

调用替身返回 `LLM_HTTP / external_outcome=UNKNOWN`。常规 `POST /pipeline/tasks/{id}/resume` 返回 200，Worker 第二次进入调用替身。恢复只挡指定的 `blocked_reason=PIPELINE_SEND_OUTCOME_UNKNOWN`，常规供应商超时/断连错误用原始错误码。Worker 又允许原 `NEEDS_REVIEW` trace 重新推进。

**关闭条件** 根据持久外部状态及证据判定恢复许可。`UNKNOWN` 进入对账，显式新尝试使用独立 attempt 身份与授权。队列、API、领取和 UI 一致，普通重排以可证实 `NOT_SENT` 为前提。 [恢复服务](../backend/shine/director/pipeline.py#L1540) · [结果分类](../backend/shine/materialprep/agent_call.py#L160) · [面板](../frontend/src/components/FourStagePanel.tsx#L118)

### F05 · P0 · 卷分镜调用未走持久远程文本授权 · 静态

卷分镜内部设置 `allow_remote_text=True`，填入母本 consent 字段但未提供 `production_context`。通用分镜链仅在有 `production_context` 时将 consent 字段传给 Dispatcher。缺两者时 Dispatcher 不调 `text_consent.guard`，而前面的远程检查被内部 `True` 满足。静态链路表明五工种可能收到派生剧本及蓝图事实，持久授权守卫未被执行。本轮未发送远程剧本。

**关闭条件** 每次远程发送前按母本和派生范围、实际角色、Provider fingerprint 校验持久同意。缺同意、换路由、撤回或过期父 revision 的 fake transport 调用数为零。 [卷分镜](../backend/shine/director/pipeline.py#L1088) · [转发](../backend/shine/api_film.py#L941) · [Dispatcher 守卫](../backend/shine/agents/film.py#L837)

### F06 · P0 · 卷包用数量代替覆盖，按 75 秒重新切集 · 静态

`build_volume_handoff` 从 apply receipt 取 shot IDs，比较镜头数与场次数，数量不足还可由布尔值放行。所有镜头都来自第一场也可能达到计数。EpisodeScript 存在时仅比较 section ID 并集，查不出重复归属、顺序或过期场次版本。接着按无 `ORDER BY` 的镜头查询结果按 75 秒切集，确认过的分集边界没有用于交付。包范围与自身 receipt 一致不足以证明覆盖原剧本。

**关闭条件** 在生成、审核、应用、交付间传递有序 scene/beat/shot 关系，校验项目、唯一性、顺序、revision 与覆盖。按已确认 EpisodeScript 推导 EpisodePlan。改编删减需要上游确定的范围与理由。 [交付](../backend/shine/director/pipeline.py#L1176) · [分集读取](../backend/shine/director/pipeline.py#L1470) · [正式应用](../backend/shine/api_film.py#L348)

### F07 · P0 · 改剧本后旧包仍可启动，编辑尾部静默丢失 · 复现

复用 mock 草案 → apply → 卷包路径。编辑已确认 SceneScript 后，旧包 `package_readiness.startable` 仍为 true。Run 创建和批次派发读取该 readiness。重新交付前存在文本 hash 校验，已发布包的路径缺同一保护。另以 24,003 字符编辑，末尾 3 字符丢失且仍写出 artifact。

**关闭条件** 包根清单固定创作 lineage，编辑/换版精准使待启动和待派发范围失效；包确认、Run 创建、派发、恢复共用判定。现有外部提交走对账，历史 PASS 和媒体可回读。编辑超限具名拒绝或按已确认边界拆分。 [编辑](../backend/shine/director/pipeline.py#L975) · [包准入](../backend/shine/director/package.py#L337) · [Run 准入](../backend/shine/production/runner.py#L209) · [批次准入](../backend/shine/production/runner.py#L553)

### F08 · P1 · Profile 配置值被标为能力已验证 · 复现

未知 OpenAI-compatible Profile 仅配置 `max_completion_tokens=128000`，未声明输出能力，却返回 `output_max_verified=True` 和 76,800。任一 min-chain 来源存在就标 verified，Anthropic adapter 默认也会触发。其它未知模型无来源兜底为 100,000。估算预检有价值，无法据此证明真实 tokenizer、有效上限或推理计数方式。

**关闭条件** 区分 declared/configured/adapter/probed，未知采用有依据的安全 cap。按实际序列化消息、schema、工具、余量做窗口校验，并记录 adapter 计数语义与 usage。明确区分超窗未发送、外部未知和截断未完成。 [预算实现](../backend/shine/llm/output_budget.py#L42) · [Gateway](../backend/shine/llm/gateway.py#L87)

### F09 · P1 · 整卷同步分镜仍受 60,000 字上限 · 静态

按原文分卷无法保证输出剧本字数。`serialize_volume_script` 拼卷套旧接口 60,000 字符限制，超长单章只有警告；整卷通过同步 HTTP 跑五工种，四阶段面板未承接续跑和 apply 信息。错误更容易诊断，长卷仍可走不通。

**关闭条件** 按场或预算内小批次调用既有五工种，超长单场按节拍建立可核查边界。复用持久回执和角色续跑，只重做受影响单元。 [序列化](../backend/shine/director/pipeline.py#L1055) · [同步路由](../backend/shine/api_pipeline.py#L232) · [面板](../frontend/src/components/FourStagePanel.tsx#L289)

### F10 · P1 · 手工卷身份无法区分同母本的两次边界调整 · 静态

手工卷 UUID 由项目、序号与母本总字数产生。边界改变时物化会按范围创建新文档，但读取只按 volume ID 返回首个匹配。新确认计划可能读旧边界。确认链也需拒绝过期页面以旧 revision 推进。

**关闭条件** 区分稳定卷键与内容 revision。用当前计划锁定 `master_sha + span + volume_revision + document_sha`，无歧义才采用历史派生文档。 [手工 ID](../backend/shine/director/pipeline.py#L1297) · [物化](../backend/shine/director/pipeline.py#L306) · [读取](../backend/shine/director/pipeline.py#L699)

### F11 · P1 · 面板交付固定卷一，重复交付无快照幂等 · 静态

交付按钮传 `volumes[0]`。分镜按钮有任一场确认即可点。面板缺分集脚本、边界调整和 apply 入口，交付只提示缺项数量。重复提交会再建计划、包和交付 artifact。

**关闭条件** 四步内维护选中卷，贯通审阅、局部重做、应用、具名阻塞与既有生产入口。写操作携带所见 hash/version，交付按快照幂等，导航到确切 package。 [面板](../frontend/src/components/FourStagePanel.tsx#L289) · [交付路由](../backend/shine/api_pipeline.py#L256) · [计划和包](../backend/shine/director/pipeline.py#L1247)

## 4. 高星 GitHub 模块的可移植做法

Star 为 2026-11-08 GitHub REST 快照。对照按工作流或单模块契合度选取，结论基于固定修订的模块与许可证。采用各自的领域约束，在 Shine 的既有合同内独立实现。

| 项目、快照及许可 | 已核对模块 | 落地逻辑 |
| --- | --- | --- |
| **Toonflow-app** · 21,030 ★ · `6086cdffceb704a632eca143dd16cd461a78cdac` · MIT | 分章事件来源索引、分集剧本、按场/片段/镜组织的分镜表，写回后回读对白、事件覆盖、空间和动作连续性。 [事件](https://github.com/HBAI-Ltd/Toonflow-app/blob/6086cdffceb704a632eca143dd16cd461a78cdac/packages/skills/v118workflow/references/sourceEvents.md) · [分集](https://github.com/HBAI-Ltd/Toonflow-app/blob/6086cdffceb704a632eca143dd16cd461a78cdac/packages/skills/v118workflow/references/episodeScript.md) · [分镜](https://github.com/HBAI-Ltd/Toonflow-app/blob/6086cdffceb704a632eca143dd16cd461a78cdac/packages/skills/v118workflow/references/storyboardTable.md) · [许可](https://github.com/HBAI-Ltd/Toonflow-app/blob/6086cdffceb704a632eca143dd16cd461a78cdac/LICENSE) | 稳定事件 ID、可回溯来源、逐场镜头清单、回读校验和审阅回执。画布、估时、资产和生成合同各有自身前提。对应 F01/F06/F09。 |
| **Huobao Drama** · 17,109 ★ · `b48e6b2312166e25b1ff141832d2e82e5d63c142` · 根许可 CC BY-NC-SA 4.0 | `validateStoryboardBindings` 写入前查本集关联场景、人物和道具的成员资格。 [模块](https://github.com/chatfire-AI/huobao-drama/blob/b48e6b2312166e25b1ff141832d2e82e5d63c142/backend/src/routes/storyboards.ts#L53) · [许可](https://github.com/chatfire-AI/huobao-drama/blob/b48e6b2312166e25b1ff141832d2e82e5d63c142/LICENSE) | 对卷、集、场和 Shot revision 做集合校验，越界在 apply/交接副作用前拒绝。借鉴领域约束，代码、prompt、UI、素材排除在移植范围。对应 F06/F07。 |
| **NarratoAI** · 11,742 ★ · `29a11b6cdf61b69ba90321fc5babc160e0ca2657` · MIT | 脚本入 UI session 前先过解析层，测试覆盖 list、对象包装、常见 JSON 格式偏差和错误形状。 [解析](https://github.com/linyqh/NarratoAI/blob/29a11b6cdf61b69ba90321fc5babc160e0ca2657/webui/tools/generate_script_short.py#L21) · [测试](https://github.com/linyqh/NarratoAI/blob/29a11b6cdf61b69ba90321fc5babc160e0ca2657/webui/tools/test_generate_script_short_unittest.py) · [许可](https://github.com/linyqh/NarratoAI/blob/29a11b6cdf61b69ba90321fc5babc160e0ca2657/LICENSE) | 把解析与内容验收分开，格式修整保持语义，随后验 schema、顺序、覆盖和 hash。截断明确进入未完成。对应 F08/F09。 |
| **Temporal** · 24,253 ★ · `74c71f963b99f366f2adf8c503ecd785b0d8b4dc` · MIT，工作流可靠性参照 | Activity 完成后、报告前崩溃可能引发重跑，外部副作用的幂等性由业务保障。 [Activity idempotency](https://docs.temporal.io/activity-definition#idempotency) · [许可](https://github.com/temporalio/temporal/blob/74c71f963b99f366f2adf8c503ecd785b0d8b4dc/LICENSE) | 区分逻辑单元与物理 attempt，可重放本地事务与外部调用分离。采用 SQLite fence、证据采纳与 UNKNOWN 对账。对应 F03/F04/F05。 |

按 OpenAI Docs 的任务级评测方法，把结构、隔离、顺序、授权和幂等作为确定性断言，把创作质量、人审单列。fixture 覆盖正常、边界和对抗输入。 [OpenAI Evaluation Best Practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices)

## 5. 验证账本

| 证据 | 本轮结论 |
| --- | --- |
| `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_pipeline.py backend/tests/test_creative_output_budget.py -q -p no:warnings` | 31 项通过。 |
| `cd frontend; npm.cmd test -- --run src/test/fourStagePanel.test.tsx` | 2 项通过。 |
| `backend/.venv/Scripts/python.exe -` 调用临时 pytest harness，独立 temp root、隔离 SQLite | 6 个反例通过现状复现断言，对应 F01、F02、F03、F04、F07、F08。修复后应转为不变量回归。初次 harness 选根命中 Windows junction，调整参数及 fixture 后最终全过。 |
| 当前基线 Actions | 采样时 `frontend` 与 `web-e2e` 成功，`backend` 与 `backend-windows` 尚在运行。合并前全量结果见历史报告。 [Actions](https://github.com/anjingdtl/shine-video-studio/actions/runs/42441218281) |
| 本轮真实 LLM、浏览器、H3、交付、人审 | `NOT_RUN`。审查使用源码、历史证据、公开上游与隔离验证。 |

复跑故障探针时可用 `tests.conftest.api` 建临时 DB，以 `test_pipeline.py` 的 `_make_doc`、`_outline_artifact`、`_route_mock` 构造计划与角色。F03 拦截 `_finish_pipeline_task(..., "SUCCEEDED")` 后令 lease 过期，再领取同一 task 并核对结果行和总调用数。F04 让 `call_material_agent` 返回 `UNKNOWN`，用所见 `state_version` 调真实 resume 路由，再统计替身调用数。F07 先跑现有 mock 草案→apply→handoff 测试，编辑其中一场后重新读旧包 readiness。探针随临时环境清理，后续 Agent 应提交反向断言的持久回归。

本报告将 F04/F05 置于首批修复，随后补 F01/F03/F06/F07 的范围、回执和准入，再收拢其余交互、预算与长篇缺口。保留既有未跟踪测试文件。执行细节与验收矩阵见 [本机 Agent 收尾 prompt](LOCAL_AGENT_FOUR_STAGE_CLOSEOUT_PROMPT_20261108.md)。
