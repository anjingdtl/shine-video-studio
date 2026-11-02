# 真人模拟验收进度与交接（2026-11-02）

> **最终续验（2026-11-03 凌晨）：核心用户旅程已真实跑通。** 详见 [测试报告最终续验](REAL_USER_JOURNEY_TEST_REPORT_20261102.md)。本轮由接手 Agent 用本机内置浏览器真实点击界面完成：3 镜系列 `51f22d82-fcc1-4baf-937d-51ea1e1cbdef` 以 `COMPLETED 3/3` 结束，三镜均为本地 Ref2VA INT8 真实出片并通过技术质检；自动装配成片 21.896 s、768×1344、24 fps、AAC，浏览器内已实际播放；导出链接与端点返回同一产物字节。后端全量 1432 passed / 1 skipped，前端 90 passed，构建与 Ruff、Alembic 均通过。
>
> 总验收仍**不签发 `REAL_USER_JOURNEY_ALL_PASS`**：ASR/VLM 建议式审片未执行（`final=needs_review`、`acceptance_status=pending_review`），审美/版权/发布为 `PENDING_HUMAN`，导出仅验证端点与页面链接（自动化下载捕获被无头浏览器取消）。当前工作区未提交、未推送。
>
> **2026-11-03 更新（已提交并推送 main `ef22534`）：** 审片改为纯辅助——「基础建议」不再因缺少冻结原生能力在建任务时 409，改为记录 `needs_review` 并继续出片；已在界面用「基础建议」跑通单镜系列 `db223693…`（`COMPLETED 1/1`、真实出片、`technical=passed`）。ASR/VLM 每个角色是 **1 个模型包**，界面 13/10 是包内文件数，文案已澄清。导演/专业两套界面各板块逐页打开无加载错误。

> 续验更新：最新结果见 [测试报告续验更新](REAL_USER_JOURNEY_TEST_REPORT_20261102.md)。实际隔离项目 ID 为 `b6f22954-258e-4544-ad89-c4e8862387e8`；下文原交接 ID 有误。当前 HEAD 为 `f076d71a0cb2a50a898083cef88afba6de181f90`。摄影任务经只读对账仍为 UNKNOWN，禁止原样重放。真人总验收仍为 PARTIAL。

## 当前结论

**`PARTIAL`。本次工作已按世恒哥要求暂停；不能签发 `REAL_USER_JOURNEY_ALL_PASS`。** 当前隔离作品尚未创建分镜，未启动 H3 推理，也没有本轮镜头、成片、ASR/VLM 实际视频检查、EDL、浏览器播放或导出证据。

## 源码与工作区

- 仓库：`F:\ClaudeWorkSpace\projects\shine-video-studio`
- 分支：`codex/confirm-existing-comfyui-install`
- 当前 HEAD / 暂停基线：`b943780eb9bdedfad927ca80f10af93d947efc24`
- 暂停时 `origin/main` 与 HEAD 相同；没有提交或推送。
- 工作区仍有大量本轮未提交修改（暂停时 `git diff --stat` 为 62 个已修改文件，约 1,978 行新增、476 行删除，另有未跟踪文件）。**后续 Agent 必须先检查并保护这些差异；禁止 reset、checkout 覆盖或清理未跟踪文件。**
- 完整矩阵见 [REAL_USER_JOURNEY_TEST_REPORT_20261102.md](REAL_USER_JOURNEY_TEST_REPORT_20261102.md)。原 [REAL_USER_JOURNEY_FINAL_ACCEPTANCE.md](REAL_USER_JOURNEY_FINAL_ACCEPTANCE.md) 顶部已标注为历史快照，旧矩阵不是本轮证据。

## 已完成的真人用户操作与证据

1. 通过 Codex 内置浏览器在隔离工作台创建作品 **《Shine真人模拟验收短剧（建议式审片回归）》**，项目 ID `b6f22954-258e-4544-ad89-4c2e8862387e`；剧本由页面输入/导入，保存后仍能在项目中查看。
2. 经世恒哥授权，在隔离作品里通过素材页面导入并确认 4 项本机素材（人物、场景、道具、声音）；正式项目库与既有批准状态没有改动。通过页面选择并冻结风格“冷调悬疑”，冻结版本 ID `82c0362d-fb62-4935-a5a6-6a14eea6e4f8`，哈希前缀 `1c3b045`。
3. MiniMax M3.1 Flash Profile 在页面显示 **1,000,000-token 输入上下文**，文本 Agent 路由指向该 Profile，输出 token 上限留空。策略实现按各角色当前路由的模型元数据识别窗口，不再对剧本/审片输入施加固定的小字符截断。真实 MiniMax 的编剧与导演调用成功（`is_mock=false`；对应输入载荷分别为 5,035 和 8,254 UTF-8 字节）。摄影 Agent 请求以 `UNKNOWN/timeout` 结束，任务 ID `b789edde-c08b-4bba-9abd-ffdff9d2e877`，没有收到结果哈希；未盲目重放，未创建镜头。
4. 已按用户授权通过页面扫描 `F:\AIGC2026` 中指定的 ComfyUI 安装，并在原目录执行只读哈希核验：10 个候选文件、约 41 GB 全部匹配；H3 固定组件 6 项匹配。**0 字节复制、0 字节下载。** 新机检查也列出 ASR/VLM 资源状态，但当前只核验了部分清单文件，VLM/ASR 核心权重仍缺。
5. 用户明确要求小模型审片、ASR/VLM 为建议式检查，不得阻断出片，也不得默认下载大量审片模型。后端建议式审片专项回归 49 项通过：建议性发现保留为待人工复核，确定性的技术失败仍阻断。官方上下文规格也已查证并记录：MiniMax M3.1 Flash 1M；DeepSeek `deepseek-flash`（V4.1 Flash）1,048,576 context、393,216 max output。见 [LLM_CONTEXT_BUDGET_POLICY.md](LLM_CONTEXT_BUDGET_POLICY.md)。

## 本轮发现、已编辑但尚未回归的缺陷

- **SETUP-REVIEW-STATUS：** 新机检查完成哈希后，模型分类卡仅依据“检查已完成”就将 ASR/VLM 标成整类已安装，即使固定清单仍缺 19 个文件、核心审片权重不在清单内。已修改 `SetupPanel` 按每类期望文件数与哈希结果显示完整度，并注明缺失审片模型不阻断 H3 出片。新浏览器界面尚未复验。
- **SETUP-OPTIONAL-REVIEW-DOWNLOAD：** 安装页的默认 H3 计划把 `qwen3-vl-8b-fp8` 和 `qwen3-asr-0.6b` 也包含在下载清单。已修改自动计划和页面“生成安装计划”从默认清单排除 VLM/ASR，并在确认说明中明确只准备 H3 生产所需组件。新增前端回归尚未完成。
- 上述前端回归第一次失败是测试 fixture 缺少 `revision` 等目录字段；后续补齐 fixture 后仍发现两处失败，已调整导入响应 fixture 和断言。最后一轮定向 Vitest 在用户要求暂停时尚无结果，状态为 **`UNKNOWN/INTERRUPTED`**，不能算通过。
- `ruff check backend\shine backend\tests` 当前有 1 项 `I001` 导入排序错误：`backend/tests/test_workflow.py`。尚未修复。

## 阻断与未知结果

- 隔离作品当前没有分镜；摄影 Agent 的远端结果未知，不允许重发相同请求。后续须先通过任务记录/可用对账机制确认，不可把 timeout 当成确定失败。
- 生产序列还没有可执行镜头；隔离数据库没有经服务验证的 H3 API Workflow Profile。ComfyUI 本机 UI 工作流文件不等于可提交的 API 图，不能凭文件存在编造参数映射。
- 本轮没有真实 H3 推理，没有视频证据。ASR/VLM 的实际运行和浏览器播放均未运行；建议式审片不构成阻断，但实际审片结果也不能假报通过。
- 当前 UI 曾显示“真实制作已就绪”，但无镜头、无已验证 Workflow Profile 的真实 H3 任务没有开始；后续须以生产任务与模型证据为准，不以该状态文案判定 H3 已通过。

## 暂停时测试状态

- 后端建议式审片聚焦回归：**49 passed**。
- 后端全量 pytest：本轮从隔离环境启动，手动停止时进度条约 **86%**；没有最终摘要，按 `INTERRUPTED/UNKNOWN` 处理。日志：`.local-acceptance/real-user-journey-final/logs/full-backend-pytest-latest.log`。
- 前端全量 Vitest：在本轮最近的 SetupPanel 改动之前，旧工作区曾有 **87 passed**；不能代表当前差异。
- 最新前端 `npm run build`：**通过**（TypeScript + Vite）。
- 最新 SetupPanel 定向 Vitest：修改前置 fixture 后用户要求停止，最终轮次没有结果；勿按通过处理。
- Ruff：**失败 1 项**，见上方 `test_workflow.py` import sort。
- 隔离 Alembic 数据库：`upgrade head` 和 `check` 均通过；使用 `.local-acceptance/real-user-journey-final/data/alembic-check/`，未触碰正式数据库。
- GitHub Actions：`WAIVED_EXTERNAL_BILLING`。真实作品版权/审美/发布审批：`PENDING_HUMAN`。

## 下一位 Agent 的执行顺序

1. 先核对当前 HEAD、`origin/main`、`git status` 和 `git diff`；保留所有未提交文件。读取此文档、测试报告、`.local-acceptance/real-user-journey-final/evidence/bug-tracker.md` 与最新测试日志。
2. 完成 `SetupPanel` 新增定向测试：确认局部 ASR/VLM 文件不会显示整类就绪；默认 H3 安装计划排除 VLM/ASR；页面明确可选建议项不阻断 H3。修复失败后重跑前端全量测试及构建，再回到 Codex 内置浏览器验证页面状态。
3. 修复 Ruff 的单条导入顺序问题；重跑相关测试和完整后端套件。暂停前的全量运行没有最终结果，不可复用其进度条作 PASS。
4. 通过 Web 页面查看未知摄影 Agent 任务及真实状态；没有确定的对账结果之前，不要原样重放。若用户旅程需要新调用，只能使用有实质变化的新输入/版本并保留旧记录。
5. 在有效分镜、已有主资产绑定、冻结风格以及经过服务验证的 Ref2VA API Workflow Profile 全部就绪之前，不启动 H3。禁止下载或替代 H3 模型；ASR/VLM 只做可选建议，不从默认安装计划下载。
6. 只有在真实 Web → API → 常驻 Worker → 本地 Ref2VA INT8 → 技术媒体结果 → 装配 → 浏览器播放/导出都被当前源码的 UI 真实操作证明后，才可提升验收状态。最后更新测试矩阵与报告，审查密钥/媒体/数据库是否入 Git，再依既有授权决定提交和推送；不得强推。

## 可直接交给后续 Agent 的提示词

```text
继续 F:\ClaudeWorkSpace\projects\shine-video-studio 的《Shine视频工作台》真人模拟验收。先读 docs/REAL_USER_JOURNEY_PROGRESS_20261102.md 和 docs/REAL_USER_JOURNEY_TEST_REPORT_20261102.md，再核对 git status、HEAD、origin/main 和全部现存 diff。当前 HEAD 是 b943780eb9bdedfad927ca80f10af93d947efc24，工作区有大量本轮未提交修改；严禁 reset、清理或覆盖它们。

当前验收只能判 PARTIAL。隔离作品 ID 为 b6f22954-258e-4544-ad89-4c2e8862387e，已有 UI 导入的测试素材和冻结风格；MiniMax M3.1 Flash 的 1,000,000 上下文已在界面显示，编剧和导演真实请求成功；摄影任务 b789edde-c08b-4bba-9abd-ffdff9d2e877 是 UNKNOWN/timeout，未创建镜头，绝不原样重放。先完成只读对账。

世恒哥已授权使用 C:\Users\Administrator\Desktop\AIstudio\Test-key\MINIMAX-TEST.txt 配置真实 LLM、读取 F:\AIGC2026 下素材、复用 F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base 与已安装模型，并在隔离项目模拟确认。密钥不得打印或入 Git；已有 Ref2VA INT8 模型已确认哈希匹配，禁止重复下载/替换。ASR/VLM/小模型审片是建议项：不得阻断 H3 生产，也不得把缺失审片模型放入默认安装下载计划；技术损坏/分辨率/音轨等确定性硬错误仍保留。

立即处理未完成的 SetupPanel 回归：新机模型卡应按文件完整度准确显示 ASR/VLM 的部分就绪，清楚说明它们不阻断 H3；默认安装计划排除 VLM/ASR。新增测试的最后一轮被暂停，尚无结果。先修好该测试并验证，再重跑前端全量测试/build。修复 backend/tests/test_workflow.py 的 Ruff I001，然后重跑相关和全量后端测试；暂停时全量 pytest 只到 86% 且无最终摘要，不算通过。

继续真人验收时只能通过 Codex 内置浏览器操作。若 Agent 结果仍未知，不可重放同指纹请求。当前没有分镜、没有可执行 API Workflow Profile，也没有 H3 MP4；先解决 Agent/工作流契约及主资产绑定。不要把本机 ComfyUI UI JSON 冒充 API Workflow。仅当 UI 实际走通 Web→API→常驻 Worker→本地 MiniMax-H3 Ref2VA INT8→真实镜头→自动装配→浏览器播放/导出后，才能签发 REAL_USER_JOURNEY_ALL_PASS。始终将 PASS_REAL_USER、PASS_REAL_MODEL、PASS_SOFTWARE、PASS_MOCK_ONLY、FAIL、BLOCKED、NOT_RUN、PENDING_HUMAN 分开。完成修复、回归、当前源码真人关键路径和脱敏报告后，再按既有授权提交/推送，禁止强推。
```
