# AGENTS.md — Shine Video Studio（唯一权威规范）

## 0. 怎么用这份文件

- **本文件是开发、测试、验收、发布的唯一口径**，按三原则组织：§2 一套事实（每样东西只有一个落点）、
  §3 一个裁决（每件事只有一条铁律）、§4 一个执行者（每类动作只有一个入口）。
- **本文件是开源仓库的一部分，不写任何本机目录。** 落点一律写成角色加解析规则（`<仓库根>`、`<日常数据目录>`、
  `<测试根>`…）。每台机器的实际取值记在该机器自己的 `AGENTS.local.md`（已被 Git 忽略，不入库、不进安装包）；
  两者冲突时，规则以本文件为准，取值以本机文件为准。
- **这些是工作纪律，不是产品门禁。** 不得为了落实本文件去给安装、整备或生产链路新增检查、状态或入口
  （§1 三条禁令）。本文件只约束在这个仓库里工作的人和 agent：东西放在哪、什么时候清。
- **2026-11-27 精简**：此前按日期追加的上百轮「事故 + 裁决」记录已原样移到 `docs/AGENTS_HISTORY.md`
  （只读归档，不再追加）。本文件的条目都能在那里找到出处，索引见 §7。
- **动工前**：先查 §3.3 登记表与 §7 索引，把对应主题的历史小节读完再动手。只读本文件不够——
  那里才有函数签名、错误码、回归入口和事故原文。
- **冲突时的优先级**：§1 三原则 > §3 铁律 > §4 入口 > §5 工作流 > §6 工作区规范 > 历史记录。
  历史小节里没被收进本文件的约束**仍然有效**——发现时把它补收进本文件对应条目，
  不要在历史里追加，也不要再新开一段带日期的小节。
- **改规则的方式**：改对应主题的那一条，保持一事一条，条目末尾用「（YYYY-MM-DD 修订）」标注；
  事故经过、任务 ID、哈希等证据写进 `docs/` 的验收文档，不写在这里。

## 1. 三原则（最高依据）

后续所有开发、测试、修改、验收都以这一条为最高依据，优先于下文任何具体规则。
任何功能只允许有**一份权威事实**（同一台账/同一读投影）、**一个裁决口**（同一判定函数）、
**一个执行者**（同一写入口/同一 Worker）。所有界面与下游都挂载这同一套，不得另起平行链路。

**三条禁令（修复缺陷时不得违反）：**
1. **不要再用增加新的检查来修复检查过多的问题。** 检查过多/互相矛盾时，先找到既有的唯一裁决口，
   让各处复用它、删掉冗余判定，而不是再叠一层新检查。
2. **不要再用增加新的状态来解决状态不一致的问题。** 状态不一致时，先确定唯一权威事实，
   让所有显示与判定都读它、由同一执行者写入，而不是新增一个状态字段去“同步”旧状态。
3. **不要再用增加新的操作入口来解决原有入口反复阻滞的问题。** 入口反复阻滞时，先修好原入口
   背后的事实与执行链路，让它一次走通，而不是另开一个新入口绕过它。

**做法**：遇到缺陷先问「这件事的唯一事实/裁决/执行者在哪？」，把分裂的链路收敛到那一处，
让所有表面挂载同一信号；修复以「收敛为一」为准，不以「新增一个」为准。
先例：2026-11-16 素材准备→导演包唯一链路轮（`docs/ACCEPTANCE_20261116_MATERIAL_TO_PACKAGE_UNIQUENESS.md`）——
晋升/确认/上传/合并共用一个采纳执行者，journey/overview/导演包/素材清单读同一份 `shot.bindings` 事实。

## 2. 一套事实：唯一落点表

任何东西只有一个合法落点。下表只写角色和解析规则，实际路径由当前机器的配置解析（见 §0）。
写错了位置，按 §6.3 的清理口径收敛回去，**不要再加一个位置**。

| 事实 | 唯一落点（角色） | 怎么解析 | 不得出现的地方 |
|---|---|---|---|
| 正式开发库（源码） | 当前这个仓库检出，分支 `main` | 仓库根由入口脚本自身推导；远端 `anjingdtl/shine-video-studio` | 第二个检出、worktree、源码快照、安装包解压副本（测试目录里也不行） |
| 依赖 | `<仓库根>\backend\.venv`、`<仓库根>\frontend\node_modules` | 只由锁文件驱动安装 | 测试目录里的 `site-packages`、`node_modules`、venv 副本 |
| 日常数据（真实作品） | `<日常数据目录>` | 命令行 `-DataDir` → 进程环境 → 仓库根 `.env` 的 `SHINE_DATA_DIR` → 默认 `.local-data` | 测试、验收、清理一律不得读写 |
| 渲染器权重 | 工作台登记的 ComfyUI 安装目录下的 `models\` | 由基础配置的受管实例与运行配方解析 | 任何测试目录、默认数据目录、隔离数据目录里的副本 |
| 工作台受管包（LoRA、RelayVSR、审片模型） | `<日常数据目录>\comfyops\isolated-packages\`，内容寻址缓存在其 `.cache\` | 只由工作台安装器写入 | 同上 |
| 节点（custom nodes） | 同一安装目录的 `custom_nodes\`；源码在仓库 `integrations\` | 部署副本由配方 `adapter_sha256` 钉住 | 测试目录里的副本；手改部署副本（要改就改仓库源再走安装流程） |
| 提示词 | `H3PromptCompiler`（唯一输出路径） | — | 自由文本直提 H3 |
| 项目台账 | `<日常数据目录>` 里的 SQLite | — | 平行台账、为同步而加的状态字段 |
| 一切可丢弃的测试产物 | `<测试根>`（布局见 §6.1） | 由调用方显式给出：`-WorkRoot` / `--work-root`，无参数解析的脚本读 `SHINE_TEST_ROOT`；唯一实现 `scripts/shine_test_root.py` | `%TEMP%`、仓库根、`.local-data\`、任何写死在仓库里的路径 |
| 验收证据 | 入库：`docs/evidence/<日期-名称>/`；本机：`<测试根>\acceptance\<轮次>\evidence\` | — | 聊天记录、`.local-data\*.log`、`/tmp` |
| 分片日志归档 | `<测试根>\shards-<时间戳>\chunk-*.log` | 由 `-KeepRuns` 管保留数 | `/tmp`、`.local-data\` |
| 交付物 | `<仓库根>\releases\` | 只由构建脚本从已提交的树产出 | 手工拷贝 |

## 3. 一个裁决：铁律

### 3.1 产品基线不变量（原文保留）

#### Project authority
Before editing code, read in order:
1. [V1.0 产品与工程基线](docs/Shine视频工作台_V1.0_项目建设方案.md)
2. [Codex Cloud 首轮开发任务](docs/Codex云端Agent_建仓及首轮开发总提示词.md)
3. [V1.1 影视专业 Agent 与 H3 官方规范中心](docs/V1.1_影视专业Agent与H3官方规范中心_修订方案.md)
4. Existing source, tests, docs, and current Git history.

**Repository already exists:** `anjingdtl/shine-video-studio`, **public**, default branch `main`. Do not attempt to create another repository or overwrite existing work. Codex is used for development only; it must **not** become a runtime dependency.

#### Product invariants
- Local MiniMax-H3 through ComfyUI is the only official video-generation execution path. Mock adapters are for development/testing, never presented as real video.
- **Ref2VA only.** Every real render is reference-driven; T2VA and the first/last-frame modes are not allowed. `shine/h3/mode_policy.py` is the single decision point, `make_render_request` is the single enforcement point, and package readiness asks the same question. A shot without a usable reference is refused by name (`PROJECT_REQUIRES_REF2VA`), never silently rendered from text alone.
- Windows local-first Web UI: FastAPI + React/TypeScript + SQLite, with a separately restartable worker.
- Independent Agent Runtime; configurable LLM/VLM API providers and capability detection.
- User-uploaded and user-approved character, location, prop and voice assets only. Agents must not silently create, replace, approve or mutate master assets. Technical derivatives are permitted with provenance, but cannot be promoted automatically.
- A user-approved immutable Style Bible must be frozen before production. All shots, renders and repairs must reference its version/hash.
- One authoritative project/asset database, one task orchestrator, one H3 prompt compiler, one audit trail.
- Job attempts are separate from logical shots; persist idempotency and ComfyUI task IDs, retry budgets, dependency invalidation, and crash reconciliation. Uncertain submissions require reconciliation, never blind re-submission.
- QC distinguishes deterministic errors and probabilistic VLM/ASR judgments. Hard errors never auto-pass; low-confidence issues require review, and independent shots should continue.
- Never copy third-party GPL sources into the main project without legal review. Myang remains an optional separately installed ComfyUI plugin; verify all upstream licenses and exact revisions.
- Never commit model weights, generated videos, personal source material, secrets or API keys. Default backend binding is localhost; cloud transmission of asset bytes requires explicit user permission.

#### First development milestone
Deliver actual **M0 + M1**, not design-only outputs. Prioritize working FastAPI APIs, React screens wired to APIs, migrations, asset approval/lock, frozen style versions, persistent job state machine, mock renderer, worker recovery and adversarial tests. Create upstream audit and local-H3 acceptance instructions. Never claim local GPU/H3 validation from a cloud-only test.

#### M2/M3 offline additions (current branch)

- `H3PromptCompiler` is the only prompt output path. Do not submit free-form agent text directly to H3. Keep the pinned `h3-prompt-writing` rule version/hashes and official section order/label families.
- ComfyUI jobs require a registered API workflow profile with an explicit `parameter_map`. Never fabricate a "validated" H3 workflow or silently change generation mode/checkpoint. Unmapped or missing workflow JSON is a documented blocker, not a reason to invent one.
- Every reference in a request owns a deterministic logical slot. A slot without a mapping, or a video/audio family without a declared `media_transfers` adapter, blocks the render with `PENDING_WORKFLOW_MAPPING`. **Never drop a reference silently.**
- The LLM gateway must keep provider keys outside SQLite/Git/logs/frontend responses. Role routing is explicit; do not silently fall back to another provider. Remote VLM/ASR requires project consent and derived evidence only.
- Per-shot bindings are authoritative. A job may not substitute a different asset set. Request-local H3 labels are remapped deterministically; global asset IDs are never label numbers.
- Worker writes require status, `state_version`, lease owner, and shot-revision checks. Expired/ambiguous external submissions are reconciled; never blind-resubmit.
- The submit intent (including the prompt-graph hash) is persisted **before** the external call, and the external prompt id immediately after it. An uncertain submission is `NEEDS_REVIEW`, never an automatic retry.
- Deterministic technical QC and probabilistic semantic QC are separate. Missing FFprobe/FFmpeg yields `unavailable`, not `pass`. Probing metadata is not a decode.
- Five named quality gates (`technical`, `semantic`, `audio`, `style`, `final`) are recorded per attempt. `PASSED` means the production attempt completed under the project policy; it is not a claim that every advisory model review passed. `acceptance_status` is `accepted` only when the latest final gate really passed. A pending final gate is recorded as `pending_review`, never relabelled as accepted.
- Advisory model reviews (`advisory_model_reviews`, always true) never sit in the managed render critical path: a reviewer that is missing, incompatible, slow or failing is recorded as `not_run`/`needs_review` advisory evidence and must not block a shot, series or delivery. Reviewer probes must not load weights or claim READY without an import check; a setup-owned reviewer route whose executor cannot import is retired, never used by the worker.
- Repairs are typed, bounded, and must protect passed versions. An automatic repair must produce a **different** request revision (recompiled prompt, rebuilt manifest, or changed parameters). Do not claim unattended full-drama completion or real H3 acceptance from offline tests.
- Upstream re-renders invalidate their dependants so downstream validity is recomputed; passed shot versions and their artifacts are never overwritten.
- The ComfyUI renderer URL must be loopback unless `SHINE_ALLOW_REMOTE_COMFYUI=1` is set explicitly, because reference bytes are uploaded to it.
- Keep `/api/v1/system/status` distinctions (API, Worker, Mock, real H3) honest. Keep the API loopback-only until authentication exists.
- The offline production loop is covered by `backend/tests/test_offline_e2e.py`; run it after any change to the worker, the gates or the repair engine.

#### Production series (real multi-shot H3 production)

- A **production series** is the durable unit of a real H3 run. It owns an ordered list of shots, its own claim, its own heartbeat and its own state. The resident Worker claims exactly one series at a time.
- The first shot of a series owns a fresh managed startup; every following shot continues the **same** fenced renderer through the idle keeper's lease transfer. The exclusive GPU lease is therefore held across the whole run and released **exactly once**, at the end. Two serial H3 model stages must never overlap.
- A dependent shot is only created after its source job really **passed**, is not stale and still matches the current shot revision. A continuity relation always points at a real job; it is never inferred.
- The lease release and the series state transition commit in **one** transaction. A release that already committed is idempotent, not a second release. A renderer that cannot be stopped keeps its lease for reconciliation.
- A worker that stops renewing leaves a **claimable** series, not a locked one, and the run resumes at its first unfinished shot. An uncertain external submission is never resubmitted by the recovery path.
- The ordinary job queue keeps its own semantics and is driven behind the same managed renderer, so a single queued shot no longer holds the lease forever.
- The series must be reachable from the Web workbench, not only from a test harness. A run that a script drives by hand is not an unattended production acceptance.

### 3.2 红线与硬约束（按主题收束）

每条是一句话的裁决；细则、函数签名与事故原文在 `docs/AGENTS_HISTORY.md` 对应小节（§7）。

**产品与证据**

- 真实视频只走本地 MiniMax-H3 / ComfyUI；mock 只用于开发测试，永不冒充真实视频。
- **Ref2VA only**（2026-11-27 用户裁定为唯一规则）：`shine/h3/mode_policy.py` 是唯一裁决，`make_render_request` 是唯一执行点；没有可用参考的镜头具名拒绝（`PROJECT_REQUIRES_REF2VA`），绝不文字直出。历史小节里「素材是可选类需求、无参考时按 T2VA 编译」的说法以本条为准。
- 只有用户动作能晋升主素材、冻结音色、应用风格或镜头建议、冻结 Style Bible、放弃未知结果。AI 可以生成 `CandidateAsset`，不得静默创建、替换、批准或改写主素材；候选的接受只能由用户评审。
- 证据如实：技术交付不等于艺术验收，`pending_review` 不得改成 `accepted`；离线或替身渲染器的产物一律 `offline_only`，永不写成真机验证；用户反馈只能记 `reported_by_users`；拿不准就写「未验证」「未校准」「未运行」。
- 已产出的成片、历史版本、已落库请求的 provenance 永不重写；修订等于产生新版本，旧版保留。
- 云端传输素材字节需要用户明确许可；已证明为环回的端点一律 `trust_env=False`（本地调用不得继承宿主代理）。
- 永不盲重提、不盲杀身份不明的进程、不静默降级：结果不确定就具名阻塞并交给人。

**H3 与提示词**

- `H3PromptCompiler` 是提示词的唯一输出路径，crew 永不写提示词；官方标准是规则包，不是风格。提示词新行为只进下一个编译器版本，已发布版本逐字节不变（`recompile_for_provenance` 按钉住版本重放）。
- 口语语言的唯一事实是项目 `language`（规范英文名，`h3/spoken_language.py` 归一化）；音频合同一律正向描述；无对白镜头恒带禁声合同。
- crew 提示词只在 `shine/agents/crew_prompts.py`，只写角色权限、官方约束、服务端核对用的数字；分段由 `editorial_timing_plan` 算好下发；无解的请求在调用模型前拒绝（`EDITORIAL_TIMING_UNGROUPABLE` / `EDITORIAL_BEAT_COUNT_MISMATCH`）。每次 Provider 调用前只在 `call_material_agent` 调一次 `pace_provider_call()`，逐场串行。
- 一张镜头图以冻结请求为权威：`make_render_request` 建任务时裁决一次并冻结哈希，Worker 只认冻结哈希；两种合法形态的等价判断只在 `low_vram_attention_form_of_graph()`。新生产图只由 `comfy/graph_compiler.py` 编译；缺可选能力 `409 STRATEGY_BLOCKED`，不静默降级。
- 人物与主素材的唯一连接键是 `entity_id`（`resolve_character_asset`），永不查 `tags`；顺序是先剧本后素材；同一镜的素材一次采纳、一次写入（`bind_adopted_masters_to_shots`），取消采用是显式释放。
- 门禁拒绝时不替换方案：不改 INT8/FL2VA、不换参考资产或 checkpoint；用户显式选定的方案，一键开拍与导演包在任何情况下都不替换。候选变体不继承基线的验收，必须走完整门禁；服务端是唯一的门（`comfyops/profile_gate.py`），浏览器只显示、读不到结论时不发明拒绝。
- 影片类型是服务端固定的 H3 合同（`StyleBible.film_type` 只接受 `live_action` / `animation`，固定英文句在 `h3/film_type.py`），不交给模型自由改写。

**渲染器、租约与对账**

- 所有本机模型阶段共用一张全局资源租约表：`ResourceScheduler.admit` 只允许一个 `ACTIVE`（显存够也不并排），跨线互斥同一套；等待显卡不是失败。
- 独占租约是对活进程的占有：释放只认 PID/birth + 命名 Job Object + 端口空闲三证；读不到身份是不确定，不是死亡；租约释放与 series `COMPLETED/RELEASED` 在同一事务提交。
- 渲染器与审片器属于工作台：只停经双重证明归本安装所有的进程，关闭动作只有 `POST /api/v1/setup/managed-processes/release`；不得用脚本手工拉起 ComfyUI。`/free` 是异步请求，HTTP 200 不是释放证明（`free_models()` 轮询取证）。不跨外部提交持有 SQLite 写事务。
- 提交意图先落盘、外部 ID 随后落盘。**`UNKNOWN` 不重发、不替代、不释放身份**，核对结论 `UNCONFIRMABLE`；只有 `NOT_SENT_PROVEN` 才可再请求；唯一例外是用户显式确认后的一次重发（原因记 `USER_AUTHORIZED_NEW_REQUEST`，不写 `NOT_SENT_PROVEN`）。
- 外部结果必须经 `agent_call.classify_external_outcome()` 分为 `NOT_SENT` / `RESPONSE_RECEIVED` / `UNKNOWN`，依据 Provider 层已记录的 HTTP 状态，不猜测；流式响应「headers 未到不等于请求未送达」。被服务端确认过的流绝不静默重发。
- 已落盘产物在整条身份链吻合时是权威：从已存字节续跑质检，不联系渲染器、不重提；记录采纳前先核验磁盘字节。
- 一次授权只买一次新尝试（`start_pipeline_new_attempt` 在同一事务里消费授权）；429 按 `Retry-After` / 指数退避重试，它是「拒绝且没有答案」，5xx 与超时仍是未知结论、不重放。「这一镜归谁」只由 `comfyops/ownership.py` 回答。

**质量门与校准**

- 确定性技术 QC 与概率性语义 QC 分开；缺 FFprobe/FFmpeg 是 `unavailable`，不是 pass；探测元数据不等于解码。审片（VLM/ASR）是 advisory：缺席、不兼容、失败都只记证据，永不阻塞生产，也不得伪报 READY。
- 改了 `client_review_identity` 钉住的文件（`worker.py`、`preproduction.py`、`comfy/client.py`、`quality/client_identity.py`、handoff 模块）就必须重做真实 VLM 正负校准、发布新 revision 并重新冻结制片计划；没做就如实写「未校准」，旧证据不得复用。
- 重验已产出的媒体，只在渲染绑定（风格、规则包、主素材、模型、H3 工作流）未变时允许。

**工作台与界面**

- 导演模式的页面保持挂载、只切 `hidden`；面板必须承受数据未到或字段缺失（不得出现让整棵树崩溃的取值），缺失字段给可读值。用户必须做的动作统一用 `.required-action` / `.must-button` 并带简短说明，不得只显示裸错误码。
- 完成态与进度只来自服务端（journey、台账）：前端不得另算就绪、不得累计自己的进度。三处配置入口同读 `GET /api/v1/setup/verdict`。
- ⑦后期成片是单一选集链路；字幕的生成、批准、应用只在⑦；字幕事实读 `shot.spec.dialogue`，时码与装配同源。每集秒数的唯一裁决是 `episode_video_seconds`；画幅只在⑤一个控制节点。
- 成片库是既有台账的只读视图；成片删除是墓碑，不改写历史；「当前采用版本」只在用户显式选中时改变（`media/video_layout.py`）。全机只有一个输出根目录（`media/output_layout.py::output_root`），变更只走 `PUT /api/v1/settings/output-directory`：保存即迁移，逐字节核验通过才在一个事务里切换。

**Copilot（AI 管家）**

- 能力只在 `backend/shine/copilot/declarations.py` 声明一次，知识索引与契约是生成器产物（`--check` 在本地与 CI 必须通过）：不手改产物，不加第二套参数 schema、执行者、调度器或就绪裁决。
- 唯一授权事实是 `SupportDelegation.payload["grant"]`；唯一写入口 `shine.support.grants`；唯一自主执行入口 `execute_under_grant`（先 `preview_action`，再 `approve_action`，最后 `refresh_action`）。
- 权限 = actor ∩ grant ∩ capability_contract ∩ original_executor_policy；改源码、任意 shell、任意 HTTP、删用户资产四项永久 false。模型只提议，永不携带权限（Discovery is not permission）。
- `SUCCEEDED` 只在存在原任务回执时给出；循环必须有步数、墙钟、调用次数、无进展四道上限；`PAUSED_USER_DECISION` 只由用户显式恢复，Worker 不自动恢复。

**测试纪律**

- 测试必须隔离：`make_studio` 的默认 ComfyUI 端点是刚释放的环回端口，不得默认 8188；后端测试在仓库根运行；不得用改测试预期的方式制造 PASS。
- 假权重只能用 `tests.conftest.make_sparse_file` 写成真稀疏文件。
- 共享环境红线：不执行 `npm ci` / `npm install`（会洗掉共享依赖）；不抢他人端口；不得用静态服务暴露仓库根（`.env`、`.git` 会变得可读）。

**分支与发布**

- 修复与验证一律在主仓原地、当前分支（`main`）做：不新建 worktree、不克隆副本、不把运行配置指向验收或测试目录。
- `main` 只有仓库所有者可推（Ruleset `main-owner-only-push`）；被拒的推送是正确结果，不删 ruleset、不 `--force`。未经用户明确指示不推远端、不自动合并 PR。
- 安装包只从已提交的树构建；没跑通的步骤在说明里写「未验证」，不得口头声称已发布。

### 3.3 唯一事实 / 裁决 / 执行者登记表

动到左列的任何一件事，先看右列：那是唯一允许改、允许读、允许调用的位置。要改行为就改那里，
不要在旁边再写一个。表里没列的事，到 `docs/AGENTS_HISTORY.md` 对应小节查「唯一」字样。

| 事项 | 唯一位置 |
|---|---|
| 渲染模式裁决 / 执行点 | `h3/mode_policy.py` / `make_render_request` |
| 提示词输出 / crew 提示词 / 分段答案 | `H3PromptCompiler` / `agents/crew_prompts.py` / `editorial_timing_plan` |
| 图编译入口 / 图形态等价判断 | `comfy/graph_compiler.py` / `low_vram_attention_form_of_graph()` |
| 可选能力裁决 / 单镜渲染策略门禁 | `sealed_optional_capabilities` / `services.make_render_request` |
| H3 提交与 ACK 对账 | `submit_prepared_prompt` / `ComfySubmission`（两条业务线共用） |
| 全局资源租约 / 模型释放证明 | `ResourceScheduler.admit` / `ComfyUIClient.free_models()` |
| 渲染器归属与停止 / 关闭动作 | `comfyops/renderer_release.py` / `POST /api/v1/setup/managed-processes/release` |
| 死渲染器租约释放 | `reconcile_dead_renderer_leases` |
| 出图渲染器启动 / 活体复用裁决 | `comfyops/image_renderer.py` / `adopted_live_renderer` |
| 续接派生媒体 | `shine/media/continuity.py`（Worker 拥有，按 lineage 校验） |
| 导演包可否启动 / Run 推进 | `package_readiness`（每次派发重跑） / Worker tick 内的 `advance_runs` |
| 分集产物来源 / 装配 / 后期合同 | `episode_current_jobs`（含 `run_owned_passed_jobs`） / `DeliveryAssembler.assemble` / `EpisodePostContract` |
| 字幕事实与时码 / 字幕字号 | `shot.spec.dialogue` 与 `DeliveryOrder.edl_payload` / `subtitle-style:{project_id}`（裁决 `render_ass`） |
| 每集秒数 / 画幅 | `director/contracts.py::episode_video_seconds` / ⑤的 `outputFrame.ts` 与 `h3/output_frame.py` |
| 人工验收 / 镜头修订 | `delivery_acceptances` + `POST /deliveries/{id}/acceptance` / `POST /shots/{id}/revise` |
| 大纲确认 / 确认依据 | `confirm_outline_artifact` / `director/basis.py::outline_for_document` |
| 风格应用 / 镜头绑定采纳 | `api_freezes.apply_frozen_style_to_shots` / `materialprep.service.bind_adopted_masters_to_shots` |
| 人物与主素材连接 / 镜头素材缺口 | `entity_id`（`resolve_character_asset`） / journey `_shot_material_problems` |
| LoRA 选择 / 挂载 / 注词 | `evaluate_style_lora_selection()` / `attach_qwen_style_loras()` / `apply_image_lora_prompts()` |
| 显存适配建议 / 实测显存准入 | `comfyops/capability.py::image_vram_advice()`（永不拦截） / `comfyops/image_renderer.py` 启动处 |
| 外部结果分类 / UNKNOWN 重发 | `agent_call.classify_external_outcome()` / 三处 `…/resend-unknown` 入口（story-preparation、film-runs、material-preparation/scenes），各一次 |
| 一次授权的新尝试 / 四阶段任务对账 | `start_pipeline_new_attempt` / `POST /projects/{id}/pipeline/tasks/{id}/reconcile` |
| 变体解析 / 候选准入门 / 拍摄归属 | `comfyops/variant_binding.py` / `comfyops/profile_gate.py` / `comfyops/ownership.py` |
| 制作依据（这版剧本或大纲）/ 导演包复用键 | `director/basis.py` / `delivery_snapshot` |
| 人物、场景、道具目录 / 素材清单派生 | `materialprep/catalog.py` / `POST …/material-preparation/derive` |
| 输出根目录 / 当前采用版本 / 成片库 | `media/output_layout.py::output_root` / `media/video_layout.py` / `media_artifacts` 的只读视图 |
| 影片类型 / 代理环境规则 / 长文本切分与预算 | `h3/film_type.py` / `http_env.py` / `segment_document` 与 `budget_report` |
| 提交是否已结清 / 审片身份 | `production/runner._job_submission_settled` / `client_review_identity`（输入经 `TrackedProvider`） |
| 审片就绪 / 缺件分类 / 基础配置执行 | `advisory_review_readiness` / `capability_report` / 「一键准备」`/setup/prepare/execute` |
| 配置是否同一 / 配方读取 / 配置结论 | `runtime_configuration.same_configuration` / `load_runtime_recipe` / `GET /api/v1/setup/verdict` |
| 环境就绪与能力标签 / 运行身份 | `foundation/environment.py` / `source_identity.py` |
| 页面步骤完成态 / 排产镜头范围 | `GET /projects/{id}/journey` / 其 storyboard `planned_shot_ids` |
| Copilot 能力声明 / 授权事实 / 执行入口 | `copilot/declarations.py` / `SupportDelegation.payload["grant"]` / `execute_under_grant` |
| 目标是否完成 / 最小修复范围 | `copilot/objective_verify.py` / `support/diagnostics.minimal_repair_scope` |
| 整栈启动 / 停止 / 代码更新 / 栈存活裁决 | `scripts/start_shine.ps1` / `scripts/stop_shine.py` / `scripts/update_checkout.ps1` / `shine_supervisor.py::_stack_live` |

### 3.4 V1.3 与个人桌面补充（原文保留）

#### V1.3 amendment (supersedes V1.0's source-only restriction)

- AI may generate `CandidateAsset` rows. Only explicit local-user grants may promote new
  `Asset` masters or freeze Voice Profiles. Do not use legacy approve/lock to promote candidates.
- Retain `offline_only` on Fake masters. Never turn Fake workflow/model/voice/QC evidence into
  real verification. `SHINE_OFFLINE_SIMULATION` is for isolated test data only.
- An analyzed project must pass `comfyops.preproduction.enforce` at both Job creation and Worker
  submission. Never disable it to make a test pass. Existing H3/Style/Rule/continuity gates remain.
- Preparation and GPU leases use the same SQLite database. Expired resources remain blocked
  until reconciled; a clock timeout does not prove models were unloaded.
- Run `test_v13_preparation.py`, `test_v13_migrations.py`, `test_v13_e2e.py`, the old offline E2E,
  film crew, complete backend suite, Ruff, Alembic and TypeScript/Vite. Preserve Windows CI.
- See `docs/V1.3_CLOUD_IMPLEMENTATION_REPORT.md` for evidence and explicit local boundaries.

#### Personal desktop simplification (2026-11-01, user-authorized)

- No password or manually typed rights evidence is required for uploaded-master confirmation.
  Same-origin loopback UI actions use a CSRF marker, with the actor recorded as `local-user`.
  This is a personal-machine trust boundary, not authenticated proof of human identity.
- Keep action/snapshot-bound one-use grants, file integrity checks, candidate promotion gates,
  style freezing, cloud consent, and uncertain-submission reconciliation.
- One explicit download confirmation may cover the displayed pinned deployment and its ordered
  instance/configuration steps. Stop on failure or changed page/project; never blind-retry.
- Normal installers do not generate or request an operator password. Preserve existing secrets
  and explicit legacy provisioning callers without rewriting their files.

## 4. 一个执行者：每类动作的唯一入口

同一类动作只有一个入口。要改行为就改这个入口，不在旁边再加一个；入口失灵时修它背后的链路（§1 三条禁令）。

| 动作 | 唯一入口 | 说明 |
|---|---|---|
| 日常启动（含更新代码） | `启动 Shine 工作台.cmd` 或同名快捷方式 → `scripts/start_dev.ps1` → `scripts/update_checkout.ps1` → `scripts/start_shine.ps1` | 入口只有这两个，指向同一套脚本（§5.6） |
| 整栈启动 / 恢复 | `scripts/start_shine.ps1` | 选端口、收尾旧栈、迁移数据库、换代判定、写 `processes.json` |
| 整栈停止（含隔离栈） | `scripts/stop_shine.py --data-dir <目录>` | 唯一的进程身份核验与收尾，含 supervisor |
| 关闭本机后台并释放显存 | `POST /api/v1/setup/managed-processes/release` | 工作台唯一的关闭动作 |
| 后端全量回归 | `.\run_backend_shards.ps1 -WorkRoot <测试根>`（8 分片） | 唯一的全量执行者（§5.4） |
| 定向测试 | 仓库根 `pytest … --basetemp=<测试根>\manual\<轮次>\bt` | 测试根见 §2；不用 `%TEMP%` 的默认目录 |
| 前端全量 | `frontend\` 下 `npx vitest run --no-file-parallelism`、`tsc --noEmit`、`npm run build` | tinypool worker 在部分机器上会崩，与代码无关 |
| 隔离验收栈 | `scripts/start_shine.ps1 -DataDir <测试根>\acceptance\<轮次>\data`，非日常端口，并设 `SHINE_RUNTIME_ENV_PATH` | §5.3、§6.1 |
| 测试根的解析 | `scripts/shine_test_root.py` | 参数优先，环境变量 `SHINE_TEST_ROOT` 兜底；脚本不得写死本机目录 |
| 竣工清理与占盘测量 | `scripts/clean_test_workspace.py --work-root <测试根>` | §6.3 口径的唯一执行者：默认演练，`--apply` 才删，只拆链接 |
| 能力声明再生成 | `backend/scripts/generate_shine_copilot_contracts.py`、`generate_copilot_knowledge.py` | 生成器无参数才写入；开发检出不带 `--commit` |
| 打包 | `scripts\build_test_package.ps1 -OutDir releases -Verify` | 只从已提交的树（§5.7） |
| 提交 / 推送 | `git add <明确路径>` → commit → 用户授权后普通 push `main` | 不 `git add -A`、不 `--force`（§5.6 C） |

## 5. 标准工作流（一轮 = 开发或测试或验收）

每一轮按同一顺序走完；任何一步没做完，这一轮就不算「竣工」，不得开下一轮。

| 步骤 | 做什么 | 依据 |
|---|---|---|
| ① 开工 | 核对 `git status` 与 `HEAD`；留存日常配置基线；建本轮目录 | §5.3、§6.1 |
| ② 开发 | 主仓原地、当前分支；小步可回退；动工前先问「这件事的唯一事实、裁决、执行者在哪」，并读完对应主题的历史小节 | §1、§3、§5.1、§7 |
| ③ 测试 | 定向 → 全量 8 分片 → 前端全量与构建 → Ruff、契约 `--check`、Alembic | §5.4 |
| ④ 验收 | 需要真机或隔离栈时，起隔离栈、走真实链路、留证据 | §5.3、§6.1 |
| ⑤ 竣工清理 | 停隔离栈、删本轮数据、留存证据、量化核对占盘 | §6.4 |
| ⑥ 交还复验 | 恢复日常配置，用原入口启动并核对原作品 | §5.3 |
| ⑦ 提交发布 | 明确路径 `git add`；授权后普通 push；从干净树打包 | §5.6、§5.7 |

定向测试的标准写法（在仓库根执行；`<测试根>` 取本机约定值，见 §0）：

```powershell
New-Item -ItemType Directory -Force <测试根>\manual\<轮次> | Out-Null    # --basetemp 的上级目录必须先存在
backend\.venv\Scripts\python.exe -m pytest <测试文件…> -q -p no:cacheprovider `
  --basetemp=<测试根>\manual\<轮次>\bt *> <测试根>\manual\<轮次>\pytest.log
```

定向运行会做环回 HTTP 调用的模块时，带上分片脚本同款的免代理设置（`NO_PROXY` / `no_proxy` 含 `localhost,127.0.0.1,::1`）：
宿主系统代理会劫持环回请求，造成与代码无关的假失败；分片脚本自己会处理，单独跑不会。

### 5.1 开发方法（原文保留）

#### Working method

V1.1 keeps professional Agent outputs as typed, versioned proposals on the existing Gateway
and database. Only user actions may apply style/camera suggestions. Rule-pack locks are
immutable per project; unsupported source/rule changes require code review and tests.
Never bundle official full-text guides without redistribution permission. Run
`backend/tests/test_film_crew.py` alongside the offline E2E after crew/rulebook changes.

1. Inspect current repo and upstream implementations/commit IDs before reuse; write `docs/upstream-audit.md`.
2. Keep feature changes small, testable and recoverable. Do not alter locked product decisions without user confirmation.
3. Run backend tests and frontend build/lint where possible. Report exact commands and failures, including unavailable external services.
4. Follow the user's specified checkout and branch. For direct work on `main`, keep changes in the current checkout, preserve unrelated local files, and use an ordinary push when authorized.
5. Report implemented, mock-verified, pending Windows/H3 hardware validation, and blocked items separately.

### 5.2 安装、路径与配置（原文保留）

##### 新机安装、仓库搬迁与主分支交付

- **源码以当前仓库为准。** 从入口脚本自身推导仓库根。外置虚拟环境只提供依赖，安装修复和运行使用同一个解释器，API、Worker 和迁移都从当前仓库加载 `shine`。复用依赖时用现有的安装回执和解释器核对这一点。
- **配置读取和启停各有唯一实现。** Windows 路径由 `scripts/shine_local_config.ps1` 解析。数据目录按命令行 `-DataDir`、进程环境、当前仓库 `.env`、默认目录的顺序解析。显式 `-DataDir` 同步限定 SQLite 位置。开发入口、安装入口与监督进程都交给 `start_shine.ps1` 做整栈启动和存活裁决，停止沿用 `stop_shine.py` 的进程身份核对。整栈与单 Worker 启停共用同一把锁和 `processes.json`。
- **路径按用途决定来源。** 仓库文件相对于仓库根，安装和数据文件相对于当前配置的根，机器专属输入由维护命令显式传入。测试与验收的可丢弃产物只进 `<测试根>`（§6）；`.local-acceptance/` 自 2026-11-27 起冻结为只读历史，新轮次不再写入。把绝对路径、旧 worktree、旧分支和个人 DB、模型、密钥引用限定在历史证据中，正式执行路径从当前配置或发布输入解析。
- **锁文件和 CI 以公共仓库可用为准。** `backend/pyproject.toml` 与 `backend/uv.lock` 共同锁定公开 PyPI。检查锁文件的 registry、下载 URL 和版本变化，使用默认配置及 `uv --no-config sync --project backend --locked --dry-run` 验证。Windows Launcher 的有效构建定义在 `.github/workflows/launcher-windows.yml`，按触发 commit 上传构建产物。当前主分支由操作者授权的普通 push 更新。
- **用实际启动验证路径。** 改安装、启动、依赖或发布后，从当前工作目录以外调用入口，使用含空格和中文的隔离路径，覆盖外置虚拟环境、端口被占用、半栈恢复和停止。核对实际源码、进程、SQLite 与 Web。全新缓存安装、复用依赖启动、打包校验与真实 H3 出片分别记录验收结果。安装包的临时解包只用于验证交付物。

### 5.3 开工留存、验收隔离与交还（原文保留）

##### 日常工作台启动配置交还（2026-11-27 用户裁定）

用户日常双击仓库根目录的 `启动 Shine 工作台.cmd`。每轮建设或验收交付前都要确认该入口能打开
原日常作品。涉及启动、Setup、端口或数据目录变更时，恢复本轮改动后再复验。失败、中断和交接也执行收尾。

1. **开工留存。** 用入口现有的配置解析器定位日常数据目录和 SQLite，记录生效的启动配置。
   工作栈在线时，核对实际监听 PID、匹配的 `processes.json` 和 `/api/v1/system/status`，记录
   API/Web 端口及可见的原作品 ID。动配置前，在本机保存仓库根 `.env` 的原始字节和 SHA-256，
   原先没有则记录文件不存在。快照留在本机，日志只记用于核对的路径和校验值。
2. **验收隔离。** 隔离验收通过 `start_shine.ps1 -DataDir` 启动，使用日常入口以外的端口。
   Setup 激活和生成运行配方可能回写仓库根 `.env` 的 `SHINE_DATA_DIR`、`SHINE_SETUP_RECIPE`
   等设置。需要经过这类操作时，把配置恢复纳入 `finally` 或等效收尾流程。
   `DataDir` 一律用 `<测试根>\acceptance\<轮次>\data`（§6.1），并设置 `SHINE_RUNTIME_ENV_PATH` 指向该隔离目录自己的 `.env`；
   否则 `POST /setup/configuration` 会把数据目录写回仓库根 `.env`，日常入口被换成隔离库。
3. **收尾恢复。** 在任务到达安全边界后，沿用 `stop_shine.py --data-dir` 按身份收尾本轮隔离栈。
   恢复本轮改动的日常启动配置，核对 `.env` 恢复到开工快照的字节或原先不存在的状态。原库、媒体、
   密钥和任务台账原地保留。已有错库迹象、基线缺失或同期有用户改动时，必须用历史配置、原作品记录
   或用户确认确定恢复目标。日常库的更换以用户授权为准。
4. **用原入口复验。** 用 `启动 Shine 工作台.cmd` 按用户日常方式启动或接回，核对实际
   API/Worker/Web 归属、SQLite 路径，以及「项目管理」中开工前的作品 ID。把入口、端口、数据库
   路径和作品核对结果写入收尾报告。若用户明确要求停机，按要求收尾并记录实启状态。无法安全恢复
   或复验时，交还状态写 `BLOCKED` 并列出具体原因。

### 5.4 测试与回归（原文保留）

##### 全量回归执行方式（固定规则：后端 8 分片并行）

后端全量 `pytest backend/tests -q` 单进程串行要 1.5 小时量级，**不允许**这样跑；本项目
的全量回归一律按测试文件均分成 **8 个分片并行**执行，单次约 10 分钟。

Windows 从当前仓库调用 `.\run_backend_shards.ps1 -WorkRoot <测试根>`（见 §6：每台机器固定一个测试根，由调用方显式传入）。
脚本沿用当前仓库配置的 Python，每轮使用独立的临时目录，并记录 8 个分片的退出码与日志。

**跑完必须落地清理。** 脚本判定完成后删除各分片的 `bt-*` basetemp（有分片失败时保留，
便于定位失败用例的 `tmp_path`；`-KeepBasetemp` 强制保留），并删掉本脚本产出的旧
`shards-*` 日志目录（`-KeepRuns N` 保留最近 N 次，默认 1）。`WorkRoot` 要**固定复用同一个
测试根**：换一个中文名的手工目录跑一次，就多一份没人认领的残留（历史事故：测试根
累计 327 GB、`%TEMP%` 下的 pytest 临时目录累计 132 GB，两者合计吃掉测试盘 459 GB）。

**假权重必须写成真稀疏文件。** 按字节数匹配目录清单的用例只能用
`tests.conftest.make_sparse_file`。NTFS 上先 `FSCTL_SET_SPARSE` 再 `truncate` **仍然真实分配**
（实测 20 GiB 全部落盘），必须标记稀疏后 seek 到末尾写最后一个字节（实测 0.1 MiB）。
改回 `truncate` 会让单次全量回归写约 70 GB，这是上一轮把测试盘吃满的直接原因。

**分片数是时间/并发参数，不是空间参数。** 峰值占用由"哪几个用例造大文件"决定，与分片数无关；
收缩分片只会让单次从约 10 分钟拉长到 20 分钟量级，不减少任何字节。要治空间就治造假文件和清理。

以下两段是分片脚本内部的等价做法，用来说明判定口径；实际执行一律走 `run_backend_shards.ps1`，不要手工另起一套。

```bash
cd <repo>
ls backend/tests/test_*.py > <测试根>/manual/all-tests.txt
split -n r/8 <测试根>/manual/all-tests.txt <测试根>/manual/chunk-          # 按文件均分，不按用例切
for f in <测试根>/manual/chunk-*; do
  backend/.venv/Scripts/python.exe -m pytest $(tr '\n' ' ' < "$f") -q -p no:cacheprovider \
    > "$f.log" 2>&1 &
done
wait
grep -H "^FAILED\|^ERROR" <测试根>/manual/chunk-*.log          # 无输出才算全绿
```

判定口径：

1. **8 个分片全部退出码 0，且日志里没有 `FAILED` / `ERROR` 行**才叫通过。
2. 本仓库 `conftest.py` 把 `[shine-module]` 逐模块摘要打在终端摘要位置，所以看不到
   "N passed" 汇总行；不要用汇总行判定，也不要因为它缺失就认为没跑。
3. 分片必须**按文件均分**（`split -n r/8`）。按用例切会让同一模块的 fixture 在多个进程
   里重复初始化，出现假失败。
4. 分片日志留存在产生它的 `<测试根>\shards-<时间戳>\` 归档目录（需要跨轮引用时显式传 `-KeepRuns`），报告里写清目录名、分片数与各分片结果；`/tmp` 与 `.local-data\` 不算留存位置。
5. 前端全量仍是 `npx vitest run --no-file-parallelism`（tinypool worker 在本机会崩，
   与代码无关），这一条不受后端 8 分片影响。
6. **必须在命令前加 `CODEBUDDY_SAFE_DELETE_ENABLED=0`**。执行环境自带的 safe-delete 垫片按
   "轮"统计删除次数，累计 50 次后开始拦截**每一次** `unlink`；pytest 清理 tmp 目录正好触发，
   表现为 `test_v13_instances` / `test_v13_preparation` / `test_security_windows` 等模块
   大片与代码无关的 `FAILED`/`ERROR`（实测守卫触发次数与失败数强相关，单独跑同一模块则全通过）。
   加这个变量是让 pytest 自己删临时文件，不是放宽被测代码的门禁；**不要**因此改测试或跳过模块。

```bash
CODEBUDDY_SAFE_DELETE_ENABLED=0 bash -c 'for f in <测试根>/manual/chunk-*; do
  (backend/.venv/Scripts/python.exe -m pytest $(tr "\n" " " < "$f") -q -p no:cacheprovider \
    > "$f.log" 2>&1; echo "$f exit=$?" >> <测试根>/manual/shards-exit.txt) & done; wait'
```

### 5.5 竣工清理与交还复验

按 §6.4 的闸门执行，再按 §5.3 第 3、4 条恢复日常配置并用原入口复验。

### 5.6 启动器与发版规范（原文保留，每次建设或发布必须执行）

#### 2026-11-04 启动器与发版规范（每次建设/发布必须执行）

本节是**强制流程**，不是建议。任何会改变启动方式、依赖、端口、数据目录或前端产物的
改动，都必须同时完成「开发机根目录一键启动」和「项目安装包」两条验收链。只改代码、
不验证入口的提交视为未完成。

##### A. 开发机根目录一键启动（每轮必验）

交还用户入口时执行上文的[日常工作台启动配置交还](#日常工作台启动配置交还2026-11-27-用户裁定)，核对日常 SQLite 和原作品。

入口只有两个，二者必须指向同一套脚本：

| 入口 | 作用 | 何时更新 |
| --- | --- | --- |
| `一键启动 Shine 视频工作台.lnk` | 日常双击，参数含 `-Update` | 仓库路径、PowerShell 7 路径、脚本名/参数、图标变化时 |
| `启动 Shine 工作台.cmd` | 快捷方式失效时的便携入口，参数含 `-Update` | 脚本名/参数变化时 |
| `scripts/start_dev.ps1` | 按 `-Update` 先快进代码、需要时停旧栈，再调用 start_shine | 入口参数变化时 |
| `scripts/update_checkout.ps1` | 代码更新的唯一裁决与执行者：只做 ff，脏/分叉/离线一律拒绝但不阻断启动 | 更新策略变化时 |
| `scripts/start_shine.ps1` | 选择端口、收尾旧栈、迁移数据库、启动 API/Worker/Web、写进程记录 | 服务启动逻辑变化时 |

**更新快捷方式**：运行 `scripts\update_dev_shortcut.ps1`（重建 lnk 的
Target/Arguments/WorkingDirectory，保留原图标）。不要手写一个指向绝对路径的 lnk 后
就认为完成——脚本参数变了，lnk 也必须重建。

**每轮必须通过的四条验收入口**（至少跑 1、2、4；改端口/记录逻辑时四条全跑）：

1. **干净重启**：先停止旧栈，再双击。API `/health` 返回 200，Web
   `http://127.0.0.1:<web>/` 返回 200，浏览器落在正确页面：已有作品 → `/`
   （导演模式首页）；空库 → `/#d/setup`（导演模式基础配置）。
2. **默认端口被别的进程占用**：保留一个非本栈的监听进程在 8000，双击启动。
   启动器必须识别「记录里的 API 身份与 8000 上的进程不一致」，不能报「工作台已在运行」；
   必须自动换到 8001/5174 一类空闲端口，并让浏览器打开实际端口。
3. **半启动残栈**：只保留 API、杀掉 Web，双击。必须收尾并重启，不能把浏览器送到
   一个已死的 5173。
4. **停止**：`stop_shine.py --data-dir <数据目录>` 退出码 0，`processes.json` 消失，
   端口不再监听；无法核对身份的进程只归档、不盲杀，归档最多保留 5 份。
5. **代码更新（改更新链路时必验）**：在临时克隆里回退一个提交后双击，必须先快进、
   再停旧栈、再启动，启动后 HEAD 为新提交；已跟踪文件有改动时双击，必须保留改动并照常
   启动本地代码。离线/分叉不得阻断启动。

**启动器硬规则**：

- 更新只允许 `merge --ff-only`：绝不 merge 分叉、绝不 reset/stash。已跟踪文件有改动、
  分叉、离线（15 秒 fetch 预算）时跳过更新，但必须继续启动本地代码；只有真的快进了
  （退出码 10）才允许先停旧栈，避免“没有更新却把正在跑的工作台停掉”。
- PowerShell 变量名大小写不敏感：不要写 `$apiPort` 和 `$ApiPort` 两个“不同”变量；
  参数默认值必须先存到 `$defaultApiPort` 再解析。
- 判断“端口空闲”必须先查 `Get-NetTCPConnection -State Listen`，再尝试
  `TcpListener`；只靠绑定会在地址复用时误判。
- “已在运行”必须同时满足：API `/health` 通、Web 页面 200、且 API 监听 PID 与
  `processes.json` 记录的 `listener_pid` 一致。只查 API 会把半死栈当成活着。
- 浏览器只能在 Web 真正返回 200 后打开；只等监听 socket 不够。
- 永不盲杀身份不明的进程；无法核对就归档记录并继续（唯一入口不能被一条坏记录卡死）。

##### B. 项目安装包（每次发布必更）

安装包从**已提交的树**构建；已跟踪文件有未提交修改时脚本会拒绝（`BUILD_INFO` 必须
指向真实 commit）。未跟踪的个人文件不会进入包，也不会阻塞构建。

```powershell
scripts\build_test_package.ps1 -OutDir releases -Verify
```

**每次发布前必须同步的文件/字段**：

- `packaging/windows/START_HERE.md`、`TESTER_CHECKLIST.md`：入口、端口、浏览器落点、
  已知限制有变化时更新。
- `packaging/windows/START_APP.cmd`：必须调用 `start_shine.ps1 -OpenBrowser -AutoPorts`，
  保证 8000 被占用时安装包也能启动。
- `scripts/build_test_package.ps1` 里的静态字段：`fixed_materials`（组件数/下载体积/
  安装体积）、`known_blockers`、`closed_in_this_build` 必须与当前事实一致；
  不复制上一版文案。
- `scripts/install_once.ps1`：端口探测必须用 `Get-NetTCPConnection` 先行；
  安装包首次启动与二次双击都必须可用。

**验收**：

- 产出 `releases/ShineVideoStudio-<日期>-<短commit>-win64.zip` 与同名 `.sha256`；
- `-Verify` 必须通过：解压后入口文件齐全、文件数 = 清单 + 1、逐文件 SHA-256 一致；
- 保留上一版 zip，不覆盖历史发布；发布页同时上传 zip 与 sha256；
- 安装包绝不包含：模型权重、生成的视频、SQLite 数据库、`.env`/密钥、
  `node_modules`/虚拟环境、`docs/evidence`、日志。

##### C. 提交与发布顺序

1. 后端 `pytest` 相关批次、`ruff check shine tests`；前端 `tsc --noEmit`、
   `vitest run`、`npm run build`。
2. 启动器四条验收（见 A）。
3. 更新 `AGENTS.md` 与 `docs/` 验收记录。
4. `git add` 明确路径（不要 `git add -A`），提交，推送 `main`。
5. 干净树上构建安装包并 `-Verify`，把 zip 与 sha256 放入 `releases/`；
   需要公开时再创建 GitHub Release 并上传两个资产。
6. 任何一条没跑通，就在提交说明/交付说明里写明「未验证」，不得口头声称已发布。

### 5.7 安装包（原文保留）

#### Distribution package (tester hand-off)

- Build releases only from a committed tree: `pwsh -File scripts\build_test_package.ps1 -OutDir <dir> -Verify`.
  The script refuses a dirty working tree, takes files from `git ls-files`, excludes models/databases/secrets/
  `docs/evidence`/virtualenvs, and writes `BUILD_INFO.json` plus a per-file `PACKAGE_MANIFEST.json`.
- One tester entry only: `一键安装并启动.cmd` (ASCII fallback `SETUP.cmd`) → `scripts/install_once.ps1`.
  It must stay idempotent (installed → start, running → delegate to the launcher's three-proof liveness
  verdict), prefer a non-system drive with ≥180 GiB free, and land on `#setup`. Personal desktop setup
  requires no operator password. **2026-11-15 user ruling**: a single-disk machine may install to the
  system drive only after explicit confirmation (interactive `YES` or `-AllowSystemDrive`); the 180 GiB
  capacity floor is NOT lowered. The approval persists as `SHINE_ALLOW_SYSTEM_DRIVE=1` in `.env` plus
  `allow_system_drive` in `deployment-storage.json`; bootstrap and start_shine honor that one fact.
- Encoding is a correctness requirement, not style: `.cmd` entry files stay pure ASCII (cmd.exe uses the
  active code page), and every `scripts/*.ps1` containing non-ASCII text must be UTF-8 **with BOM**
  (Windows PowerShell 5.1 reads BOM-less UTF-8 as ANSI and fails to parse). `test_package_entrypoints.py`
  enforces both.
- Never package weights, generated media, SQLite files, `.env`, `operator-credentials.txt` or acceptance
  evidence. Testers get the installer, not the data.

## 6. 测试根与工作区规范

### 6.1 测试根布局：`<测试根>` 是一切可丢弃测试产物的唯一落点

```text
<测试根>\
├─ manual\<YYYYMMDD>-<主题>\        定向测试：pytest --basetemp、临时脚本、日志 —— 竣工即删
├─ acceptance\<YYYYMMDD>-<主题>\    隔离验收（需要真机或隔离栈时才建）
│   ├─ data\                         隔离 DataDir —— 竣工即删
│   └─ evidence\                     必要证据 —— 保留；要长期引用的复制进 docs\evidence\
├─ shards-<时间戳>\                  只由 run_backend_shards.ps1 产生与清理（-KeepRuns，默认留 1 轮）
└─ .media-cache\                     只由分片脚本使用
```

- 测试根由调用方显式给出，每台机器固定一个非系统盘目录并复用：有参数解析的脚本用 `-WorkRoot` / `--work-root`，
  没有参数解析的（pytest 模块、裸跑的浏览器脚本）读环境变量 `SHINE_TEST_ROOT`。唯一实现在 `scripts/shine_test_root.py`；
  仓库里的脚本、测试与配置不得写死任何本机目录（`backend/tests/test_workspace_hygiene.py` 守护）。
- **导入即建栈的陷阱**：`shine/main.py` 在模块顶层执行 `app = create_app()`，默认配置会经仓库根 `.env` 解析到
  **日常数据目录**——任何进程只要导入 `shine.main`，就会打开（不存在则创建）日常库。所以 `backend/tests/conftest.py`
  在导入任何 `shine` 模块之前把 `SHINE_DATA_DIR` 钉到一次性目录，并在退出时关引擎、删目录（顺序由
  `test_workspace_hygiene.py` 守护）。新增 pytest 入口、插件或要导入应用模块的独立脚本时照此办理：
  **先隔离数据目录，再导入应用模块**。
- **顶层不放任何自起名的目录或文件。** 手工 basetemp 一律进 `manual\<轮次>\`，不落在根下，不每跑一次换一个新名字；
  重跑复用同一目录，跑前先清。
- `%TEMP%`、`.local-data\`、仓库根不得再写入测试产物或隔离数据。`<仓库根>\.local-acceptance\` 是旧的验收目录，
  自 2026-11-27 起冻结为只读历史，新轮次不再写入。
- 模型与节点不得在测试目录留副本：隔离栈竣工时连同它的 `data\` 整个删掉，其中就包含它自行缓存的权重。

### 6.2 命名

- 轮次目录 `YYYYMMDD-<主题>`：只用 ASCII 字母、数字和 `-`，不含空格、中文、括号。
- 路径里不得出现中文或乱码：控制台代码页会把中文路径写成乱码目录（曾留下一个名字全是乱码的测试目录）。
- 一轮一个目录、一个名字；本轮产生的一切都写在这里。

### 6.3 清理口径（测试根通用；竣工清理与大规模清理都用这一套，不得另定标准）

唯一执行者是 `scripts/clean_test_workspace.py`：默认演练并按真实分配字节数报告，`--apply` 才删除，
`--prune-links` 才动保留条目里的链接。以条目为单位（测试根的一级条目，以及 `manual\`、`acceptance\` 下的轮次目录）：

1. 条目顶层的散文件是证据（日志、JSON、截图、交接文档），一律保留。
2. pytest basetemp（子目录以 `test_*` 为主）整棵删除，不论大小；不含任何文件的空壳目录移除。
3. 分片归档目录（子目录全是 `bt-*`）只删 `bt-*`，保留 `chunk-*.log`。
4. 真实占盘 < 100 MiB 的目录视为轻量证据，整棵保留。
5. ≥ 100 MiB 的目录视为数据目录，内容全删；但下列目录整棵保留：`comfyops\{isolated-packages,models,setup}`、
   名为 `evidence` / `browser-evidence` / `logs` / `handoff-history` 的目录、直接含真实 safetensors 权重的目录。
6. junction 与符号链接只拆链接本身，绝不进入其目标。
7. 真实权重副本不在这里删：先核对「规范位置存在同大小、头尾逐字节一致的原件」并迁回模型库，核对不到的保留并上报。

**占盘看真实字节数，不看 `du`**：Git Bash 的 `du` 对稀疏文件报逻辑大小（2026-11-27 实测同一目录 571 GiB 逻辑、
346 GiB 真实）。脚本按真实分配字节数统计。

### 6.4 竣工清理闸门（每轮必做，缺一不算竣工）

1. 停隔离栈：`scripts/stop_shine.py --data-dir <本轮 data>`，确认端口无监听、GPU 租约为 0。
2. 清理：`scripts/clean_test_workspace.py --work-root <测试根>` 先演练并留下清单，确认后加 `--apply --prune-links` 执行；
   分片失败保留的 `bt-*` 在定位后立即删除。
3. 需要长期引用的证据复制进 `docs/evidence/<日期-名称>/`，报告里写清路径。
4. 量化核对：把演练输出里的删除量与测试根占用写进收尾报告。预算：`<测试根>` 在轮次关闭后 ≤ 2 GiB
   （分片归档与证据）。超出必须列明原因和保留清单。
5. 恢复日常配置并用原入口复验（§5.3）。

### 6.5 删除安全

- 永久删除没有回收站：先演练（逐条目列出大小与动作），复核后执行，再把执行结果与演练逐条比对。
- 动手前查三件事：日常栈是否引用（配方、清单、库的全表全列）；里面有没有真实权重或从未入库的内容；有没有 junction。
- 下列内容永不当作测试垃圾删除：日常数据目录及其备份、从未入库的源码或文档、`backup-*` 目录、
  别人正在运行的进程所使用的目录。

## 7. 专题细则索引

### 7.1 主题 → 历史小节（`docs/AGENTS_HISTORY.md`，括号内是精简前 AGENTS.md 的行号）

| 要做的事或碰到的问题 | 先读哪里 |
|---|---|
| 写新功能、改既有链路 | 本文件 §3.3 登记表 → 对应「唯一位置」的源码 → 下表对应小节 |
| 持久化产物恢复、死渲染器租约、口语语言合同 | 2026-11-03 late supplement（L219）、closing round（L258） |
| 渲染器的启动、归属、释放 | 2026-11-04 renderer lifecycle round（L307）、2026-11-05 受管出图渲染器（L581）、2026-11-15 出图渲染器崩溃取证（L1746） |
| 剧组提示词、分段、分镜链失败 | 2026-11-04 crew prompt round（L334）、2026-11-07 第 7 轮（L864）、2026-11-15 拍摄提示词调优轮（L1781）、分镜链真跑轮（L1862） |
| 人物绑定、音色、方案槽位、画幅 | 2026-11-03 workbench usability round（L370）、2026-11-04 round（L399）、director-mode round（L430）、受托素材轮（L628） |
| 导演模式旅程、一键开拍、七阶段 | 2026-11-04 导演模式全旅程（L461）、2026-11-08 素材后置与第二轮收尾（L1125、L1141）、2026-11-09 复验收尾（L1218）、七阶段归并（L1461） |
| 导演包、整部作品生产、调度、跨批次续接 | 2026-11-06 导演包与持续生产（L691）、收尾修复 V3（L748）、跨批次续接轮（L789）、2026-11-21 中断拍摄恢复（L2443） |
| UNKNOWN、对账、授权重发、死亡 worker | 2026-11-07 UNKNOWN 请求身份安全补充（L960）、2026-11-08 导演链路阻滞（L1030）、授权重发（L1055）、四阶段二轮复验（L1093）、2026-11-22 LLM 流式 UNKNOWN（L2567） |
| 变体、候选权重、整备与安装链 | 2026-11-09 W6A8 候选轮（L1166）、2026-11-14 H3 资源自整备 V2.0（L1493）、2026-11-15 工作栈生命周期（L1682）、新装机对抗性审查（L1713）、2026-11-17 环境整备与钉死路径（L1946）、能力卡缺件（L1987）、2026-11-19「文件找到却用不了」（L2122） |
| 启动器、supervisor、端口、入口归属 | 2026-11-15 解阻滞轮（L1801）、2026-11-17 supervisor 归入同一停止链（L2014）、端口归属与身份收敛（L2037）、接手轮（L2059）、2026-11-19 收尾建设（L2179） |
| 素材：需求、采纳、逐镜注入、LoRA | 2026-11-17 素材缺口与一次采纳（L1916）、2026-11-19 逐镜素材注入（L2220）、④页单一选定面（L2244）、2026-11-25 统一素材生图 LoRA（L2617） |
| 大纲、分集、每集秒数 | 2026-11-14 大纲版本管理（L1557）、大纲节奏三档（L1581）、2026-11-10 长文本分块（L1290）、2026-11-21 每集秒数（L2487） |
| 成片、字幕、后期、验收 | 2026-11-10 成片库（L1257）、2026-11-17 字幕唯一生成入口（L2303）、2026-11-19 后期成片重构（L2372）、2026-11-19 重拍、选用与字幕烧录（L2349）、2026-11-10 输出目录与可读文件名（L1346） |
| Copilot（AI 管家） | Copilot V2.1 合同规则（L2098）、V3.1 任务级一次授权（L2146）、2026-11-19 收尾建设（L2179） |
| 两条业务线（七阶与四阶）、V3 工作流 | 2026-11-20 V2 双生产线（L2403）、2026-11-22 V3 P1–P4（L2509–L2551）、V3 最终收尾（L2558） |
| LowVRAM、真实出片耗时 | 2026-11-23 LowVRAM 高水位门控（L2577）、2026-11-26 中世纪 8 镜穿测（L2665） |
| 模拟测评团队（MiniMax Bot） | 2026-11-15 模拟测评团队轮（L1652） |
| 该跑哪些测试文件 | 上表对应小节末尾的「回归入口」，以及本文件 §5.4 |

### 7.2 仓库脚本里的位置引用

开发与验收脚本一律通过 `scripts/shine_test_root.py` 取测试根。仓库里的脚本、测试与配置不再指向旧的验收目录或
任何本机目录，`backend/tests/test_workspace_hygiene.py` 静态守护这一点；新增脚本照此办理。唯一的例外是
`config/deployment/_upstream-pins/review-runtime-metadata.lock` 头部由 uv 自动生成的命令记录：它记的是生成当时的
路径，属于事实记录；源输入清单已在同目录（`review-runtime-metadata.in`），不要为了改这两行注释去重新生成这份锁。

## 8. 现行待收束

收掉一项就删一行。产品功能层面的遗留项以 `docs/AGENTS_HISTORY.md` 各小节里的「未修 / 待定 / 待用户决定」为准。

- **隔离栈重复缓存权重**：隔离数据目录的安装器会把规范安装里已有的权重再复制一份进自己的缓存（2026-11-27
  一轮复制了 16 GiB）。应在产品层改为就地核验或同卷硬链接；改之前靠 §6.4 的竣工清理兜底。
- **历史记录里的本机路径**：`docs/AGENTS_HISTORY.md` 与早期验收文档保留着原文里的本机路径，它们只是当时的事实
  记录，不是现行落点；现行落点只看 §2 与本机的 `AGENTS.local.md`。
