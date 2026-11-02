# 真人模拟验收测试报告（2026-11-02，本轮暂停版）

## 最终续验（2026-11-03 凌晨）：真实 Web 用户旅程跑通

本轮由接手 Agent 用本机内置浏览器（agent-browser 驱动 Chrome）**真实点击工作台界面**完成，不再用直接 API 调用代替用户操作。API `127.0.0.1:8110`、Web `127.0.0.1:5191`、常驻 Worker 与本地 Ref2VA INT8 渲染器均取自当前源码。

### 真实三镜连续制作（界面发起）

- `PASS_REAL_USER`：在「分镜与任务」勾选 3 个镜头，点击「开始制作影片（3 镜）」。系列 `51f22d82-fcc1-4baf-937d-51ea1e1cbdef` 进入 `RUNNING`，页面逐镜推进到 `COMPLETED 3/3`，`finalization_state=RELEASED`。
- `PASS_REAL_MODEL`：三镜均由本地 MiniMax-H3 Ref2VA INT8 真实生成（`is_mock=0`），任务 `5d34125f`（3,268,307 B）、`5c6af42b`（3,347,674 B）、`dc346aff`（4,092,989 B），每镜技术质检 `technical=passed`。
- 审计链完整：`PRODUCTION_SERIES_CREATED → COMFY_STARTUP_INTENT/RUNNING → COMFY_SUBMIT_INTENT/ACKNOWLEDGED → COMFY_OUTPUT_PERSISTED → COMFY_HANDOFF_STAGE(DRAIN→STOP→REVIEW→RESTART) → COMFY_HANDOFF_JOB_RESERVED → 下一镜`，最后一镜结束后停止渲染器并一次性释放独占租约。
- `PASS_SOFTWARE`：建议式审片闸门保持 `semantic/audio/style=not_run`、`final=needs_review`、`required=0`；硬性 `technical` 仍为 `passed`。这是项目策略下的正常结果，不等于审美/对白已通过人工审核。

### 自动装配与播放/导出（界面验证）

- 自动交付订单 `74b12db5-82f0-46e7-8078-26cf634d15be` 状态 `DELIVERED`，成片产物 `e64ba9fd-8676-445e-8a45-8407bffa8b99`，SHA-256 `9039fc307f3dee89d09039a7f50dd603a36d460efd1bfde38f05f914a370e99c`，9,927,251 B，`21.895996 s`，768×1344，24 fps，AAC 48 kHz 双声道（ffprobe 实测）。
- `PASS_REAL_USER`：在「成片与导出」页面加载该成片，浏览器媒体栈 `readyState=4`、`videoWidth=768`、`videoHeight=1344`、`duration=21.896`、无播放错误；触发播放后 `paused=false`、`currentTime` 持续前进。
- 导出链接指向 `/api/v1/deliveries/{id}/media`，在页面上下文实测返回 `200 video/mp4`、`Content-Disposition: attachment`、9,927,251 B，与产物字节一致。自动化 CLI 的下载捕获被无头浏览器取消，因此**不声称**已通过自动化保存文件；导出端点与链接本身已验证。

### 本轮发现并修复的真实缺陷

| 缺陷 | 触发场景 | 修复 | 回归 |
|---|---|---|---|
| `COMFY_RESTART_REQUEST_INVALID` | 项目为建议式审片且本机无审片模型时，交接仍要求“审片释放凭据”才允许重启 | `restore_after_review` 支持“无审片阶段”的物理边界（仍验证进程已停、端口已释放、显存已回收） | `test_advisory_only_handoff_restarts_without_a_review_receipt` |
| `HANDOFF_IDLE_CLAIM_LOST` → 租约永久卡死 | 结束受管运行时先停渲染器、后停租约心跳，心跳在停机窗口判定渲染器丢失 | `IdleHandoffKeeper.stop_renewal()`：先停续期 → 再停渲染器 → 证明释放 → 再释放租约；失败时租约自然过期而非被复活 | `test_release_stops_renewal_before_stopping_the_renderer` |
| `COMFY_RESTART_OUTCOME_UNCONFIRMED` | 真实渲染器重启偶发超过 30 秒才监听 | 重启等待上限由 30 s 放宽到 120 s（与首次受管启动的等待策略一致） | 现有 supervisor 回归 |
| 系列镜头被普通队列单独抢跑，系列错位并 `SERIES_INCOMPLETE` | 系列任务同时留在普通队列，`run_one` 未排除系列所属任务 | `run_one` 排除 `ProductionSeriesItem` 所属任务；取消系列时把其 `QUEUED` 任务置 `stale` | 全量后端回归 |
| 已停机交接失败后租约无人可解 | 渲染后交接失败，任务 `NEEDS_REVIEW`、租约 `RECONCILE_REQUIRED`，界面无释放入口 | `reconcile_stopped_handoffs`：仅当记录的渲染器进程已死且端口空闲时释放租约，任务保持 `NEEDS_REVIEW` | `test_stopped_handoff_releases_the_gpu_without_turning_a_failure_into_a_pass`、`test_live_renderer_keeps_a_failed_handoff_blocked` |
| 看守失败原因不可诊断 | 只记录固定原因码 | 记录具体未通过的校验项与租约状态/到期时间；记录循环与释放决策 | 手工复现确认 |

### 审片定位调整（2026-11-03）

- **审片不再硬阻断**：`preproduction.enforce` 与 `native.production_review_binding` 在缺少冻结原生审片能力时改为继续制作；人物/对白门记录为 `needs_review` 且 `required=0`。确定性媒体硬错误仍阻断。
- **模型包 ≠ 文件数**：ASR/VLM 每个角色只需 **1 个模型包**。页面显示的 VLM 13 / ASR 10 是包内文件数（权重分片、分词器、配置、README），不是 13 个模型；界面文案已改为「N 个模型包 · 已核验 X/Y 个文件」。
- **真实复验**：在界面把「审片建议」切到「基础建议：人物与对白」，发起单镜系列 `db223693-75d8-44d3-9903-ea50efc452be`，结果 `COMPLETED 1/1`、`finalization_state=RELEASED`：真实出片 3,268,307 B，`technical=passed`（required），`semantic/audio=needs_review`（required=0），`final=needs_review`；自动交付订单 `0dde76bf…`，7.31 s、768×1344、24 fps。此前同样的选择会在建任务时返回 409。

### 界面全板块穿测（2026-11-03）

- 导演模式 1→4 步与专业模式侧栏逐页打开：素材与风格、制片准备、人名与字幕、分镜与任务、多镜头连续生产、成片与导出、制作环境、模型与系统、显示与偏好、影视制片团队、H3 官方规范均正常渲染，未出现「加载失败 / 请求失败 / 无法连接 / undefined」。
- 「模型与系统」显示核心 LLM 为 MiniMax 真人验收（1,000,000 上下文），状态「LLM 准备：配置及连接检查通过（尚非文本生成验收）」；工作流 Profile 注册区可展开。
- 专业板块显示各 Agent 产物为「待用户审查」，没有被自动批准或替换主资产。
- 修复一处界面矛盾：运行配置原先按“创建它的作品”判断，导致顶部显示「真实制作已就绪」而环境检查却报「还没有本机可用的真实制作配置」。现在把运行配置视为整机共享（新作品复用同一套本机环境），服务验证针对当前作品的工作流执行。
- 新增用户可用的了结入口：镜头详情出现「放弃这次生成（保留产物，不重投）」。它只结束这次无法核对的任务（`USER_ABANDONED_UNRECONCILED`），解除其租约并保留产物，绝不重新提交；界面已实际点通。
- 「验证本机节点」已从界面真实跑通：审计新增 `WORKFLOW_PROFILE_VALIDATED`（`missing_nodes: []`、`services_stopped: true`，2026-11-03 02:15:58）。同时把“空闲”判定收窄为**真正在途**的任务（进行中状态、非 stale、在途租约、未结束系列），不再让历史待核对记录永久挡住运行配置。

### 本轮测试结果（当前源码）

- 后端全量：`backend\.venv\Scripts\python.exe -m pytest -c backend/pyproject.toml backend/tests -ra --tb=short` → **1433 passed、1 skipped、2 warnings，exit 0，730.02 s**。跳过项 `test_real_managed_startup.py:85` 需 `SHINE_REAL_COMFY_PROBE=1` 与本机 ComfyUI。
- 前端全量：**90 passed（15 个测试文件）**；`npm run build`（tsc + Vite）**通过**。
- `ruff check backend/shine backend/tests`：**通过**。
- 隔离库 Alembic：`upgrade head` 与 `check` 均**通过**（`No new upgrade operations detected`）。
- 未提交、未推送；`.local-acceptance/` 仍在 `.gitignore` 内，无媒体/数据库/密钥进入 Git 索引。

### 仍未完成 / 不能声称的部分

- ASR/VLM 建议式审片资源未安装，`semantic/audio/style` 为 `not_run`，`final=needs_review`；`acceptance_status` 为 `pending_review`，**不是** `accepted`。
- 该验收项目没有预生产计划（走 legacy 契约），因此 `comfyops.preproduction.enforce` 为既有的“无计划即放行”路径；本轮**不声称**已完成“分析过的项目”全链路验收。
- 审美、版权与发布审批仍为 `PENDING_HUMAN`。真实 H3 只在本机 RTX 5080 上验证。
- 不签发 `REAL_USER_JOURNEY_ALL_PASS`：核心用户旅程已真实跑通，但建议审片未执行、人工审片/版权未决，且导出仅验证端点与页面链接，未由自动化保存文件。

## 续验更新（2026-11-02 晚间）

### 后续真实 Agent 与分镜进展

- `PASS_REAL_MODEL`：在更新后的隔离 API / Codex 内置浏览器中，编剧、导演、摄影、场记、剪辑五个真实 MiniMax Agent 完成；页面逐项显示 HTTP 200 / 服务商代码 0，进度按完成角色而非虚构模型内部百分比显示。单次输出预算为 100,000 tokens，低于配置或模型已知更低上限时取更低值。
- `PASS_REAL_USER`：经页面复核英文事实和七项英文风格草案后，点击应用创建 3 个分镜：`428332e1-c1c0-4f8a-958e-5c2a05242178`、`9d445e05-ab7d-40ce-b932-5a97ea93d7cf`、`d6e265a3-4f0b-4797-8f32-5b5838affc14`。每镜 7.2 秒，均保存人物、场景、怀表和声音 4 项绑定；第二镜为 `last_frame`，原中文事实与冻结风格仍保留。实际生成、连续性画面和视觉质量尚未验证。
- 发现并修复中文冻结风格、锁定事实及素材显示名直接进入英文 H3 描述导致应用分镜失败的问题；译文作为需核对的草案进入镜头修订，原始风格快照和事实不被改写。临时数据库副本的三镜头应用仅作故障复现，不计入真实验收。
- `BLOCKED`：三图一音候选 Profile `79bdd263-68fd-4e18-96a1-48065066be24` 由页面登记，API 图结构检查通过；它扩展自现有 API 图并参考本机节点定义，**未通过实时节点或真实生成验证**。登记后图哈希 `8df95767beda99ee7896888d5bc1c6e608053ef2d0391478c91a7df73d355273`；导入前候选序列化哈希不同，解析后的图对象相等。未启动 H3 推理、未下载或替换权重。
- 保留两项未知远端任务：摄影 `b789edde-c08b-4bba-9abd-ffdff9d2e877` 和场记 `637fce4e-c3d2-4198-ae33-1191311deed1`；均不得原样重放。不同剧情修订的新请求另行记录。
- 代码继续变化后的最终完整后端、前端全套与构建以本次最新运行结束摘要为准；中途启动且跨源码变更的 pytest 不作为最终源码全套通过证据。

- 总判定仍为 **`PARTIAL`**。当前 HEAD 为 `f076d71a0cb2a50a898083cef88afba6de181f90`，本地 `origin/main` 为 `b943780eb9bdedfad927ca80f10af93d947efc24`；HEAD 多出的提交仅记录暂停交接，原有大量未提交修改均保留。
- 隔离数据库和内置浏览器的 Agent 运行记录只读对账：摄影任务 `b789edde-c08b-4bba-9abd-ffdff9d2e877` 为 `NEEDS_REVIEW / LLM_TIMEOUT`，`external_outcome=UNKNOWN`，`automatic_replay=false`，无任务产物。未重放。该任务及页面所示验收作品实际关联 ID 为 `b6f22954-258e-4544-ad89-c4e8862387e8`；交接文字中的 `b6f22954-258e-4544-ad89-4c2e8862387e` 与数据库不一致。
- `PASS_SOFTWARE`：SetupPanel 定向 15 项通过；修正扫描阶段 `PENDING_HASH` 被误报为哈希不匹配后，前端全套 88 项通过，`npm run build` 通过。`ruff check backend/shine backend/alembic backend/tests` 通过，修复仅为 `test_workflow.py` 导入排序。
- `PASS_REAL_USER`（页面状态范围）：Codex 内置浏览器的隔离工作台显示 VLM `1/13`、ASR `3/10` 未就绪且不阻断 H3；默认安装说明不自动下载 ASR/VLM。页面恢复既有 41 GB 哈希核验结果，未重新读取权重、复制或下载；再次扫描时逐文件显示“待哈希校验”。这不证明 H3 运行时或视频生成通过。
- `BLOCKED`：实际验收项目仍无已登记 Workflow Profile；数据库中另外两项属于另一个项目且 `validated_against` 为空。无有效分镜；没有启动 H3。真实三镜头、装配、播放和导出均 `NOT_RUN`。
- `PASS_SOFTWARE`：从仓库根目录以独立 `SHINE_DATA_DIR` 运行 `backend\.venv\Scripts\python.exe -m pytest -c backend/pyproject.toml backend/tests -ra --tb=short`，最终 **1417 passed、1 skipped、2 warnings，exit 0，677.62 秒**。跳过项 `test_real_managed_startup.py:85` 需要 `SHINE_REAL_COMFY_PROBE=1` 和本机 ComfyUI；因此真实启动探测仍为 `NOT_RUN`。日志 `.local-acceptance/real-user-journey-final/logs/full-backend-pytest-continuation.log`。警告分别为 AnyIO 别名弃用与 SQLAlchemy 空主键告警。

下方矩阵是暂停时的历史快照；前端、Ruff 和全量 pytest 状态以上述续验更新为准，真实生产链状态仍未改变。

## 判定

**`PARTIAL`。本轮应用户要求中止，不能签发 `REAL_USER_JOURNEY_ALL_PASS`。** 下表只记录当前源码/本轮操作的证据；暂停前没有结论的测试统一标为 `UNKNOWN/INTERRUPTED` 或 `NOT_RUN`，不以进度百分比、旧报告、页面就绪徽标或 Mock 结果推断通过。

- 项目：`F:\ClaudeWorkSpace\projects\shine-video-studio`
- 分支：`codex/confirm-existing-comfyui-install`
- 暂停时 HEAD：`b943780eb9bdedfad927ca80f10af93d947efc24`
- 暂停时 `origin/main`：`b943780eb9bdedfad927ca80f10af93d947efc24`
- 工作区：大量未提交的本轮代码、测试和文档修改；无提交、无推送。后续必须先审查并保留全部现存差异。
- 隔离验收数据目录：`.local-acceptance/real-user-journey-final/`
- 隔离项目：`Shine真人模拟验收短剧（建议式审片回归）`，ID `b6f22954-258e-4544-ad89-4c2e8862387e`
- GitHub Actions：`WAIVED_EXTERNAL_BILLING`
- 真实作品版权、艺术质量与发布审批：`PENDING_HUMAN`

## 验收矩阵

| 验收项 | 状态 | 本轮证据与边界 |
|---|---|---|
| Windows 一键安装/启动入口 | `NOT_RUN` | 本轮没有从 `.cmd` 正式入口启动的证据。已有浏览器工作台可用不等于 Windows 安装器验收通过。 |
| Codex 内置浏览器打开工作台 | `PASS_REAL_USER` | 使用 Codex 内置浏览器访问本机工作台，完成隔离项目的页面操作；暂停时停在真实制作设置页。 |
| 创建隔离作品、输入/保存剧本、刷新后查看 | `PASS_REAL_USER` | 通过页面创建作品、导入剧本并回到项目查看；不涉及直接写数据库来代替用户操作。 |
| 导入和确认授权测试素材 | `PASS_REAL_USER` | 按用户授权从 `F:\AIGC2026` 素材在隔离项目中经 UI 导入/确认 4 项（人物、场景、道具、声音）。正式作品和既有审批状态未改。 |
| 风格选择与冻结 | `PASS_REAL_USER` | 页面选择“冷调悬疑”并在隔离作品冻结；版本 `82c0362d-fb62-4935-a5a6-6a14eea6e4f8`，哈希前缀 `1c3b045`。 |
| 现有 ComfyUI/H3 安装发现和确认 | `PASS_REAL_USER` | UI 扫描用户指定安装并明确确认复用；10 个候选文件约 41 GB 哈希匹配，H3 固定组件 6 项确认，0 字节复制、0 字节下载。此证据只证明发现和文件校验，不证明运行时或 H3 推理可用。 |
| ASR/VLM 新机状态识别 | `PARTIAL` | 页面枚举了审片资源，但只核验部分清单；仍缺 19 个固定清单文件，ASR/VLM 核心权重未完整。最新 UI 状态修复尚未通过定向 Vitest/浏览器复验，旧“整类已安装”展示可能误导用户。 |
| 默认安装计划不下载 ASR/VLM | `UNKNOWN/INTERRUPTED` | 已修改 SetupPanel 排除可选审片模型并更新中文说明；最后一次定向 Vitest 在用户要求暂停时无最终结果，页面行为没有复验。 |
| 真实 MiniMax LLM 配置与上下文预算 | `PASS_REAL_MODEL` / `PASS_REAL_USER` | 已通过 UI 配置真实 MiniMax M3.1 Flash Profile，页面显示 1,000,000 输入上下文。剧本 Agent、导演 Agent 真实调用成功，`is_mock=false`，载荷分别为 5,035、8,254 UTF-8 字节。 |
| Agent 按真实角色完成分镜 | `BLOCKED` | 最新流程中的摄影 Agent 任务 `b789edde-c08b-4bba-9abd-ffdff9d2e877` 返回 `UNKNOWN/timeout`，无结果哈希、未创建镜头；没有盲目重放。项目当前没有可生产分镜。后续先对账，不能原样重发。 |
| H3 API Workflow Profile | `BLOCKED` | 隔离数据库没有已通过服务验证的 H3 API Workflow Profile。不能把 ComfyUI UI 工作流当作 API workflow，也不能凭空编造节点映射。 |
| Web → API → 常驻 Worker → 本地 Ref2VA INT8 H3 | `NOT_RUN` | 本轮未启动 H3 推理，未生成任何本轮 H3 原始 MP4。不得把页面“真实制作已就绪”徽标解释为真实任务可运行。 |
| 三镜头 768×1344、24fps、约 7 秒生产 | `NOT_RUN` | 无镜头任务、无生成视频、无 ffprobe/全帧解码数据。 |
| ASR/VLM 实际视频分析 | `NOT_RUN` | 没有本轮 H3 视频供分析。ASR/VLM 与小模型审片按用户要求是建议功能，不应成为出片阻断项；实际 findings 也不得假报通过。确定性媒体技术错误仍应阻断。 |
| EDL、FFmpeg 自动装配、成片中心播放/导出 | `NOT_RUN` | 未生成 EDL 或最终 MP4；无本轮浏览器视频播放及导出证据。 |
| 浏览器关闭、Worker/ComfyUI 故障及真实生产恢复 | `NOT_RUN` | 没有运行中的 H3 生产序列，无法验证生产期间恢复和租约释放。 |
| 后端建议式审片聚焦回归 | `PASS_SOFTWARE` | 暂停前命令运行结果为 49 passed。它仅覆盖该组测试，不代表全后端或真实 H3 验收通过。 |
| 后端全量 pytest | `UNKNOWN/INTERRUPTED` | 由用户要求停止时约进行到 86%，pytest 无最终摘要；日志 `.local-acceptance/real-user-journey-final/logs/full-backend-pytest-latest.log`。 |
| 前端全量 Vitest（当前差异） | `UNKNOWN/INTERRUPTED` | 87 passed 是最新 SetupPanel 改动之前的旧结果，不能代表当前源码。新增/调整后的定向测试没有最终结果。 |
| 前端定向 SetupPanel Vitest | `UNKNOWN/INTERRUPTED` | 第一轮 fixture 缺字段；第二轮仍有失败，随后修改了 fixture 与断言，但最后一轮结果未得出。 |
| TypeScript/Vite 构建 | `PASS_SOFTWARE` | 本轮最近一次 `npm run build` 成功；不等于前端测试通过。 |
| Ruff | `FAIL` | `ruff check backend\shine backend\tests` 有 1 项 `I001`，位于 `backend/tests/test_workflow.py`，暂停时未修复。 |
| 隔离 Alembic 升级与一致性检查 | `PASS_SOFTWARE` | 在验收隔离目录执行 `upgrade head` 与 `alembic check` 成功；正式数据库未触碰。 |
| Git 提交/推送与远端同步 | `NOT_RUN` | 没有提交、推送；保留本地差异由后续 Agent 接手。 |
| 总体验收 | `PARTIAL` | 用户旅程未到真实 H3、多镜头、自动装配及播放器验证。 |

## 真实 LLM 上下文来源

- MiniMax M3.1 Flash 官方页面标示 1M 上下文：[MiniMax M3.1 Flash](https://agent.minimax.io/tools/m3.1-flash)。
- DeepSeek 官方模型列表显示 `deepseek-flash` context 1,048,576、最大输出 393,216：[DeepSeek API 模型列表](https://api-docs.deepseek.com/api/list-models/)。
- DeepSeek 官方公告将 `deepseek-flash` 描述为 V4.1 Flash：[DeepSeek V4.1 Flash 公告](https://api-docs.deepseek.com/news/news260910/)。
- 本轮模型路由和 Agent 输入去固定小字符截断的改动已存在于未提交工作区；代码改动及新 SetupPanel 还需要完整回归。

## 已发现的问题及当前修复状态

| 缺陷 | 当前处置 | 当前回归状态 |
|---|---|---|
| 部分 ASR/VLM 文件核验后被 UI 显示为整类已安装 | 已改 SetupPanel 依完整文件数显示状态 | `UNKNOWN/INTERRUPTED`：需定向测试及内置浏览器复验 |
| 默认 H3 安装计划包含 Qwen3-VL/ASR 下载 | 已改默认计划排除可选审片模型，并补充说明 | `UNKNOWN/INTERRUPTED`：需测试、构建及浏览器复验 |
| 真实摄影 Agent 调用超时，外部结果未知 | 保留未知任务；不自动重放相同请求 | 尚待只读对账；不得将超时改成确定失败 |
| Ruff 导入排序失败 | 已定位 `test_workflow.py` 的 `I001` | 尚未修复/回归 |

更早的资产名归一化、时间码、未知 Agent 结果保护等缺陷记录见本地证据目录的 `evidence/bug-tracker.md`；只有当前源码和当前 UI 复验完成后才能标记关闭。

## 后续工作优先级

1. 读取 `docs/REAL_USER_JOURNEY_PROGRESS_20261102.md`、本报告、`evidence/bug-tracker.md`，再核对 Git 差异；保护所有未提交文件。
2. 对摄影任务做只读状态对账；不对未知外部结果原样重放。确认无在途结果且确需继续时，使用显式修订后的请求并留下新版本记录。
3. 完成 SetupPanel 定向前端测试，确认部分 ASR/VLM 不冒充完整安装、默认 H3 计划不含审片模型；再运行前端全套 Vitest、`npm run build`，最后用 Codex 内置浏览器检查实际页面。
4. 修复 Ruff `I001` 并重跑；之后从仓库根目录运行完整后端套件。记录完整摘要和退出码。
5. 确认隔离项目分镜与素材绑定正确，并解决已验证可用的 H3 API Workflow Profile。未具备 Workflow Profile、主资产/冻结风格和安全运行检查时不启动 H3。
6. 仅在可安全执行且不重复模型下载的情况下，从 UI 提交真实系列，经常驻 Worker 使用已有 Ref2VA INT8 完成至少三镜；保留任务、模型、视频及哈希证据。
7. 技术媒体检查仍须真实执行；ASR/VLM/小模型审片仅作为建议和 `pending_review` 证据，不得阻断合规生产或伪造人工通过。随后验证自动 EDL/FFmpeg、浏览器播放与导出。
8. 最后进行所需故障恢复和视口 UX 检查，更新最终矩阵，审核密钥/数据库/个人素材是否入 Git。只有当前源码 UI 旅程全通过才能签发全 PASS；否则保持 `PARTIAL`/`BLOCKED`，不得淡化未完成项。

## 证据边界

本报告是中止时的状态快照。未引用旧版 `REAL_USER_JOURNEY_FINAL_ACCEPTANCE.md` 的历史结论作为本轮通过证据；历史报告已在顶部标记为 superseded。验收证据目录可能包含多个历史轮次，后续 Agent 应按时间和任务 ID 区分，不能混用旧视频、旧测试结果或旧截图。
