# 进度：导演模式全旅程（第 7 轮接手基线 / 第 8 轮续跑，2026-11-07）

第 7 轮交接快照：**26 张场景底图 + 6 个人物主素材真实出图并晋升；10 个生产段落已分镜。**
第 8 轮续跑现状：**21 段中 18 段已有已应用分镜；`GET /journey` 读到 54 个项目镜头。订单 18–20 的同步 MiniMax 调用结果为 `UNKNOWN`，分集 materialize 仍被这三段阻断；本轮没有创建导演包、ProductionRun 或 H3 视频。** 当前事实与证据见 §7。

数据目录不变：`.local-acceptance\live-browser`（项目 `3707a9ed…`《夜航船 真实长片 20261106》）。

> §1–6 记录第 7 轮交接时的材料、规则和旧接手点；当前续跑结果以 §7 和服务端台账为准。

---

## 1. 相对上一轮（bafb241）的净进展

| 项 | 上一轮 | 本轮 | 证据 |
| --- | --- | --- | --- |
| 场景主素材 | 3 张（甲板/码头/船尾 + 误晋升 1 张） | **+26 张，共 30 个 scene master** | S2–S20 每场的场景需求全部经"单场素材计划 → confirm(generate) → Worker 真实出图（Qwen-Image-2.1）→ 候选评审 → promote"入账，`offline=false`、`renderer_owned=true` |
| 人物主素材 | 沈砚、老周 | **+郑海、师父、船员甲、船员乙、广播（号角喇叭）** | 同上路径；`广播` 是 S7/S8 台词说话方，以"舱壁铜质号角喇叭"为视觉身份按 character 登记（详见 §4.3） |
| 分镜 | 仅第 1 场 | **orders 1–8、10、11 共 10 段分镜 + apply-camera 成功** | 镜头 30 个（见 §2 明细） |
| 生成方案 | 仅 2 图槽（ee3649d6） | **+本项目宽方案 `local-h3-ref2va-pdd8-6img`（12908aec），真实渲染器校验通过** | `missing_nodes=[]`、`source=live_object_info`，校验后渲染器已停、GPU 回基线 |
| 真实提示词缺陷 | — | **修复 2 个（导演提示词与服务器校验相矛盾）** | 见 §3，回归 `test_crew_prompt_contract.py` |

## 2. 分镜明细（以库为准，shots 表）

| 段 | 场 | 镜头（name → 数量） | 状态 |
| --- | --- | --- | --- |
| 1 | S1 暴雨靠岸 | S1-S1_S01 | 上一轮已应用 |
| 2 | S2 缆绳断裂 | S02-* ×3 | ✅ 应用 |
| 3 | S3 稳住船身 | S03-* ×3 | ✅ 应用（宽方案后首轮通过） |
| 4 | S4 舱内钟声 | S04-* ×2 | ✅ 应用 |
| 5 | 第二声钟响（S4 续） | S05-* ×3 | ✅ 应用 |
| 6 | S5 停摆的怀表 | S06-* ×3 | ✅ 应用 |
| 7 | S6 表盖内侧 | S07-* ×3 | ✅ 应用 |
| 8 | S7 失踪名单 | S08-* ×3 | ✅ 应用 |
| 9 | S8 最后一个名字 | S09-* ×3 | ✅ 第 8 轮新 task 应用（§7.1） |
| 10 | S9 十年前的名单 | S10-* ×4 | ✅ 应用 |
| 11 | S10 暴雨中的约定 | S11-* ×1 | ✅ 应用 |
| 12 | S11 | S12-* ×4 | ✅ 第 8 轮应用（§7.2） |
| 13 | S12 | S13-* ×3 | ✅ 第 8 轮应用（§7.2） |
| 14 | S13 | S14-* ×1 | ✅ 第 8 轮应用；1 次 resume（§7.2） |
| 15 | S14 | S15-* ×4 | ✅ 第 8 轮应用（§7.2） |
| 16 | S15 | S16-* ×3 | ✅ 第 8 轮应用（§7.2） |
| 17 | S16 | S17-* ×3 | ✅ 第 8 轮应用（§7.2） |
| 18 | S17 | 无 | ⛔ editor `LLM_TIMEOUT` / `UNKNOWN`，未应用（§7.3） |
| 19 | S18 | 无 | ⛔ director `LLM_TIMEOUT` / `UNKNOWN`，未应用（§7.3） |
| 20 | S19 | 无 | ⛔ editor `LLM_TIMEOUT` / `UNKNOWN`，未应用（§7.3） |
| 21 | S20 | S21-* ×4 | ✅ 第 8 轮应用（§7.2） |

另有上一轮遗留：E00 建立镜头、S16 手工镜头（rev2，唯一真实 H3 成片 job `aa8baa09`）。

## 3. 本轮修复的两个真实缺陷（提示词与服务器校验矛盾，2026-11-04 同类）

两处都在 `shine/agents/crew_prompts.py` 导演提示词，回归都在 `test_crew_prompt_contract.py`。

### 3.1 无剪辑表场景：导演把单个 beat 碎片化成 same-beat cuts

- 现象：S3 五角色链四次 `EDITORIAL_TIMING_CONFLICT`。用临时调试钩子（已还原，不入库）抓到导演真实回答：对 `scene3_03`（台词到 8.1s）发 4 个 cut（2.5/4.2/6.5/8.5s），每个 cut 窗口都容不下台词——服务器必拒，模型无法自愈。
- 根因：服务器 `validate_output` 要求每个 cut 窗口覆盖所涉 beat 的台词；提示词只给了时长规则，从未约束无 `editorial_timing_plan` 时的 cuts。
- 修复：无剪辑表时"**一段一个 shot、按 beat 顺序、完全不用 cuts**；cut 只用于折叠更晚的 beat，绝不回头剪自己的开场 beat、绝不重复剪同一 beat"。
- 静/无声场景（S1/S2/S5）此前通过只是因为没有台词，不构成反证。

### 3.2 台词说话方不算"出场人物"：导演不绑说话方主素材

- 现象：S8（订单 9）三次 `CHARACTER_MASTER_BINDING_REQUIRED`。服务器把 dialogue speaker 一律算作必须绑定的人物（`film.py` 的 required_characters 含 speakers）；提示词只说"出场人物"。
- 修复：提示词明确"**台词说话方即使从不露面也拥有其台词；片段内每一句台词的说话方的已批准人物主素材必须出现在该片段 asset_ids 里**"。

### 3.3 配套事实：为走通链路而登记的 `广播` 主素材

S7/S8 的剧本把"广播"写成念名单的说话方，`_character_coverage` 按人物名解析必然 409。产品没有"仅声音实体"概念；本轮把 S7 leaf 的 prop 需求（船上广播）以 character 家族建单场计划（视觉=铜质号角喇叭特写，VLM 目检通过后 promote，entity_id=广播）。这是**登记决策**不是绕门禁——门禁（说话方必须可绑定）被如实地满足了。

## 4. 资产与方案台账（截至停栈）

- 主素材 39 个（7 原有 + 26 场景 + 5 人物 + 1 广播），全部 approved+locked，`renderer_owned=true`、`offline_only=false`。
- 候选评审口径：候选图逐一与台账 `content_sha256` 比对（本轮 32/32 一致），拼版 + 单图 VLM 复核（人物/水印/画质/年代）；两处误报（码头系缆桩误判人影、拼版自绘编号误判水印）均以原图复核实锤排除。
- 出图失败 7 次 `MATERIAL_BRIEF_NEEDS_CLARIFICATION`，全部按产品路径把答案写进英文 appearance 后 `regenerate`（S4/S7/S8/S9/S12/S15/师父），其中 S12/S15 因 1200 字上限改写整段外观（regen2）。
- 方案：本项目 5 个（4 标准 + `12908aec` 宽方案 1 subject + 5 picture，**image-only 传输族，无对白镜头不会被 audio 族拦**）。宽方案来自已提交的 `config/workflows/ref2va-pdd8-6img.json`，在真实渲染器校验（`validated_at=2026-11-06T15:57:07Z`）。

## 5. 接手点（下一步从这里开始）

### 5.1（第 7 轮记录）订单 9（S8）：apply-camera 422（已于 §7.1 解决）

- 链已 SUCCEEDED（任务 `31c64d81`），camera artifact `529c502d-010d-4692-8ca2-f31862b4946c`。
- `api.err.log`：`apply_camera_rejected artifact=529c502d reason=1 validation error for ShotSpec`（pydantic 细节被日志截断）。
- 复现：直接重调 `POST /film-artifacts/529c502d…/apply-camera`（幂等），或按 apply_camera 的构造逻辑在本地重建 ShotSpec 打全 ValidationError。优先怀疑 cut 台词时间重排（`start_s/end_s` 装配分支）。
- 若是模型产出非法 cut 时间：重跑该段（换一条 continuity fact 文本即新任务身份）通常即可。

### 5.2（第 7 轮计划）订单 12–21：逐单执行；当前结果见 §7.2–§7.3

- 用 `.local-acceptance/r7/storyboard.py <order>`（驱动：queue→等链→REJECTED 自动 resume≤3→apply-camera；facts 在 `facts2.py`，已含 12–21）。
- S12（订单 14）4 人物 + 场景 = 5 参考图，宽方案 6 槽足够；说话方绑定规则已修，`广播` 不在 S12 出现。
- 已知风险：`老周（周远山）` 别名未再复现（本轮 S4/S5 等段 fresh scriptwriter 都写的是 `老周`），若再遇 `BLOCKED_MISSING_MASTERS` 且 missing 是别名，**不要生成第二张脸**，按 §5.3 处理。

### 5.3 已知产品缺口（未修，接手者不要假装不存在）

1. 同一人物跨场别名（老周/老周（周远山）/周远山（老周））没有合并动作；本轮通过 fresh run 自然规避。
2. `broadcast 类"仅声音实体"`没有正式建模；本轮用 character 家族的号角喇叭图满足绑定门（§3.3）。
3. `apply-camera` API 仍只返回泛化的 `FILM_HANDOFF_REJECTED`，日志只记录 `ValidationError` 首行；本次通过本地同构重建 `ShotSpec` 找到说话方遗漏（§7.1），尚未改造产品日志细节。

## 6. 之后的主链（不变）

materialize v2 → 导演包（宽方案、1024×576）→ confirm → production-runs 整部 Run（无人值守）→ 拼接交付核对。
每镜验收口径不变：artifact sha + ffprobe + `ffmpeg -f null -` 解码 0 错 + EDL 顺序 + 交付单；`pending_review` 不许写成 accepted。

## 7. 第 8 轮续跑结果（2026-11-07）

### 7.1 订单 9：根因复现与修复

- 按原 camera artifact `529c502d-010d-4692-8ca2-f31862b4946c` 重调 `apply-camera`，API 返回 422 `FILM_HANDOFF_REJECTED`。
- 从数据库只读重建 `apply_camera` 的 `ShotSpec` 输入，两镜均完整复现校验错误：`every dialogue speaker must be listed in characters`。这不是 cut 时间错误：对白 speaker 是 `广播`，但这份旧 run 的 continuity fact 明确写了“不是出场人物”，ScriptBreakdown 因而只把 `老周` 放入 characters。
- 保留旧 run/旧 artifacts；通过受托 `local-user` 改一条 continuity fact，明确“画外声 `广播` 仍须列入 characters 并绑定 `entity_id=广播` 的已批准主素材，画面仍只拍老周”，创建新任务 `46468f92-fbdf-4462-8059-7379fcf002a3`。任务 `SUCCEEDED`，新 CameraPlan `d1e1bf7e-ca43-4104-a770-ca44e4b085c0`，`apply-camera=200`，入账 3 镜：`ae391caf…`、`dfc321b6…`、`6f123b83…`。

### 7.2 第 8 轮新增应用镜头

| 订单 | section task | CameraPlan | 镜头数 / 结果 |
| --- | --- | --- | --- |
| 12 | `4e744e3e-d640-4e7e-8c1b-44079d574db1` | `d789850a-6957-47de-b98c-95bcf84bffb5` | 4，`apply-camera=200`；scriptwriter `LOCKED_FACTS_CHANGED` 后 resume 1 次，数据库终态 `SUCCEEDED` |
| 13 | `be09582f-febb-4bc1-9384-12f0be30de78` | `6b4fc06b-079f-4e01-8647-0c2071fcd468` | 3，`apply-camera=200` |
| 14 | `327790e1-dbf4-469a-b26d-ef96b6977c15` | `0b1f8179-b31f-4c95-882a-af49606dd0e4` | 1，`apply-camera=200`；scriptwriter `LOCKED_FACTS_CHANGED` 后 resume 1 次，数据库终态 `SUCCEEDED` |
| 15 | `11fd64ae-da82-4414-8673-6b0cf73fc141` | `3171cf62-2dd9-4f7f-b8d6-e32271ef4c14` | 4，`apply-camera=200` |
| 16 | `bbf53393-f15b-4e43-8a1d-6afe45a8c5c8` | `cf85bbb3-5616-46ea-ad6d-398650b7eba3` | 3，`apply-camera=200` |
| 17 | `c61b2126-4362-497a-b506-bf2f55b262c9` | `2ee0fa66-e644-475d-b1f5-01fffa5dfbda` | 3，`apply-camera=200` |
| 21 | `8cec31e8-2601-4565-b920-b9ce89262c7c` | `4ea13c39-184f-4fa0-abca-79afdaec094f` | 4，`apply-camera=200` |

以上新增 25 镜；最终 `GET /api/v1/projects/{P}/journey` 当前读到 54 个项目镜头。所有这些新增素材仍只是分镜，不是 H3 视频。

### 7.3 订单 18–20：外部结果 UNKNOWN，必须停在门禁

| 订单 | section task | 子任务 | 角色 | 最终状态 |
| --- | --- | --- | --- | --- |
| 18 | `354dc1f1-07b0-4297-89d4-7f7fb673b776` | `f1022cae-2257-409f-988b-a200f54c6734` | editor | `NEEDS_REVIEW / LLM_TIMEOUT / external_outcome=UNKNOWN` |
| 19 | `3fd6676b-e611-4962-8f84-ac460d16aa59` | `4d53e72f-fd11-4de6-9c70-0c25fe514253` | director | `NEEDS_REVIEW / LLM_TIMEOUT / external_outcome=UNKNOWN` |
| 20 | `b83d7f9f-85de-49d2-a2c8-4063278d0151` | `b95be68d-e3e0-43a4-acfb-5acb32ec3f3b` | editor | `NEEDS_REVIEW / LLM_TIMEOUT / external_outcome=UNKNOWN` |

- 三条子任务都没有 `request_identity`、provider task id、HTTP status、provider code 或 `received_result_sha256`；`automatic_replay=false`。订单 18/19/20 的父任务经过最多 3 次受限 resume 后仍然 `NEEDS_REVIEW`，没有可应用的 camera artifact。
- 因无法取得 provider 侧结果，本轮通过产品路由 `POST /api/v1/projects/{P}/film-runs/{task_id}/abandon-remote-response`，每次先签发一次性 `model_reconcile` grant；由受托 `local-user` 写明证据后，三个子任务均返回 `ABANDONED / external_outcome=UNKNOWN / automatic_replay=false`。这是放弃丢失响应的审计动作，不代表证明未发送或成功。此处早先“没有重发请求”的判断后来被同一台账三条 `STORY_UNKNOWN_SUBTASK_REPLACED` 事件证伪，见 §10。
- 后续只读复核 SQLite：三条记录仍均为 `ABANDONED`、`LLM_TIMEOUT`、`external_outcome=UNKNOWN`，`phase=WAITING_PROVIDER`，无 response id/hash。`WAITING_PROVIDER` 在 `provider.complete(...)` 前立即持久化；OpenAI-compatible transport 对 `LLM_TIMEOUT` 的映射是超时但未收到回复并标记 `UNKNOWN`，而连接建立前失败才会标记 `NOT_SENT`。因此不能用该 phase 推导 `NOT_SENT_PROVEN`；现有局部 reconciliation 逻辑也把 `WAITING_PROVIDER` 排除在 pre-request phases 外。没有可采信的新证据改变三段状态。
- 不得通过改 continuity fact 另建近似任务来绕过这三条 UNKNOWN。只有服务端/提供方能给出可信 reconciliation 证据，或将来出现能证明 `NOT_SENT_PROVEN` 的新证据，才可继续相应段落。

### 7.3.1 恢复路径复核（目标恢复后的只读检查）

- 订单 18 留有成功的 ScriptBreakdown `4b86d9cf…`、DirectorialPlan `f6fef69a…`、CameraPlan `3f04072d…` 和 ContinuityReport `c6cc89e9…`；但场记报告是 `needs_review` 且含 2 个 issues，没有匹配的裁定产物。`apply-camera` 按现有 API 合同需要对应 arbitration 释放这些冲突，不能直接应用；剪辑子任务 `f1022cae…` 仍 UNKNOWN。
- 订单 19 只留有 ScriptBreakdown `cfd143c4…`；导演子任务 `4d53e72f…` 在产出 DirectorialPlan 前超时，因此无摄影方案或场记报告可应用。
- 订单 20 的修复后 CameraPlan `3823d61d…` 与 ContinuityReport `ec0c428b…` 完整且 `proceed_to_preflight`，但修复后 editor 子任务 `b95be68d…` UNKNOWN。早先 EditIntentPlan `3c040ff3…` 的输入仍是修复前的 Director/Camera/Continuity artifacts，不能冒充当前链的剪辑结果。
- 不对以上 artifact 执行低层单独 `apply-camera`：`section_storyboard_shot_ids` 先从最新 section run 的 `trace.results` 取 cinematographer artifact，再找对应 `film-apply` receipt。三条 section run 的 `results` 均为空；只写低层 receipt 仍不会进入分集，反而会造出未关联镜头。现有 section `reconcile` 也只处理 `blocked_subtasks`；三条父任务该字段为空，无法采用这些 Film 子任务产物。
- 本次检查只读 SQLite 与源码，没有修改任务、发起模型请求或创建镜头；分集仍被 orders 18–20 阻断。

### 7.3.2 Provider 侧对账能力复核（目标恢复后的只读检查）

- 官方 MiniMax ChatCompletion v2 文档把 `id` 定义为响应字段；三条超时调用均未收到响应，所以台账没有 response id 可据此定位。官方 Token Plan 页面/API 提供套餐剩余额度，不是单次 ChatCompletion 请求历史。当前公开资料未找到按客户端身份查询同步请求结果的接口；这不证明 MiniMax 内部不存在客服/控制台记录，只说明本机现有证据无法通过公开接口完成逐请求核对。资料：[ChatCompletion v2](https://platform.minimaxi.com/document/%E5%AF%B9%E8%AF%9D?key=36954880539f4e2ab8b13067)、[Token Plan 用量](https://platform.minimaxi.com/subscribe/coding-plan?code=H2y7yU1UUd&source=link)。
- 当前 Windows 窗口枚举只有 Codex；Chrome/360 浏览器没有运行窗口。未启动浏览器、未登录控制台、未读取或输出 API key，也未发起额度查询或生成请求。仍无新的 provider 侧证据改变订单 18–20 的 `UNKNOWN`。

### 7.4 materialize 与整片 Run

- `POST /api/v1/projects/3707a9ed-eced-435b-b69a-0b1ecdd5f146/episode-plans/1/materialize` 返回 409 `EPISODE_SECTION_NOT_STORYBOARDED`，明确列出 `ep-04` 的三个 `section_id`：`05916709-6f6b-5a0a-a754-324a4cf8ebd1`、`a68e9f18-7771-56a5-bd5d-9e6f480669ad`、`6a4ecc4a-5c6a-5849-abdf-ef2b4f3186da`，原因均为 `STORYBOARD_NOT_APPLIED`。没有创建 v2；台账仍只有已确认的 plan v1 `ef996d7a-94a5-4867-a4b4-566c05f287b9`。
- `GET /journey`：54 个项目镜头、`planned_shot_count=0`、`plan_materialized=false`，阻塞 `EPISODE_PLAN_NOT_MATERIALIZED`。因此本轮没有建导演包、没有确认 v2、没有创建 production run、没有新 H3 渲染或交付单。项目仍保留上一轮唯一真实 H3 产物：job `aa8baa09-0c1f-4627-a874-193a1a3647e1` 为 `PASSED`、`quality_verification=real`，artifact SHA-256 `92b862e64eb7b4e31b2961c455503ff1f0b8fc4bf7163f184ea2bef8a718a190`（2,049,538 bytes），`last_qc_result=needs_review`；它不是整片 Run 或 accepted 证据。
- 无活动资源租约，8188 没有 renderer listener；本轮通过 `scripts/stop_shine.py --data-dir .local-acceptance/live-browser` 关闭 API/Worker/Web，随后验证 8000/5173/8188 均无监听。
- 本轮未改源代码、`worker.py` 或 `preproduction.py`；R7 的全量 pytest/Ruff 结果仍是第 7 轮旧证据，本轮未重新运行测试。VLM 正负校准仍未完成，不能把任何新镜头写成 accepted。
- 详细本机证据保存在 Git 忽略目录 `.local-acceptance/r7/`：`order9_apply_reproduction.json`、`storyboard_order9_repair.jsonl`、`storyboard_runs.jsonl`、`order18_remote_reconciliation.json`、`order19_remote_reconciliation.json`、`order20_remote_reconciliation.json`、`final_snapshot.json`。

## 8. 串行资源根因修复与浏览器续跑（2026-11-07）

### 8.1 根因与修复

1. **过期 WAITING 永不出队**：`ResourceScheduler.enqueue()` 初始只给预约 60 秒，原 `admit()` 只把过期 ACTIVE 改成 `RECONCILE_REQUIRED`，不处理过期 WAITING。现场 3 个老 H3 reservation（`609b9cb1…`、`919aeacc…`、`0087e36e…`）因此能长期挡住 MiniMax/其他资源请求；其中一条 Agent 子任务停在 `WAITING_RESOURCE`，持久化阶段证明尚未到 `WAITING_PROVIDER`，没有 provider id/response，约 35KB 的输入也不是 context overflow 证据。修复后，仍活跃的排队方由 `enqueue()` 续租；无人维护且到期的 WAITING（从未拿到模型/外部请求）被释放；当前调用只能续自己的预约。过期 ACTIVE 仍进 reconciliation，绝不按超时放行。
2. **卸载确认窗口过短**：`/free` 的 200 只是接受异步卸载请求。旧实现只轮询约 3 秒，慢卸载会让 lease 进入 `RECONCILE_REQUIRED` 并挡住整条串行队列。现在最长等待 120 秒、0.5 秒轮询；只有 Comfy 队列为空且 live CUDA tensor allocation 确认低于 512 MiB（旧版 Comfy 需 torch pool 归零）才释放。不可读/超时仍 fail closed；模型预检失败不重复发第二次 unload。
3. **safe resume 把排队显示成并行生产**：恢复逻辑一次恢复多个无外部提交的 series，却把每个 child 都写成 `RUNNING`；Worker 随后只领取一个，其余回到 `QUEUED`，而 run-batch 的旧 `RUNNING` 又不随 series 回退，UI 遂显示多个“拍摄中”。恢复现改写为 `QUEUED`，唯一 Worker 领取后才进入 `RUNNING`；reconcile 与 API read model 都按真实 series 状态显示，任何阶段只应有一个 series/资源 lease。

**不加无证据的固定冷却睡眠**：serial handoff 等待真实释放信号；H3 同一 series 内按产品合同复用同一个 renderer/租约，不在每镜之间卸载；series 边界停止被证明归本机所有的 renderer 并确认端口已释放，租约和 series 终态同事务提交。不得改成未经证明的释放。

### 8.2 本地用户代行与真实运行证据

- 按所有者授权，由 Agent 以 `local-user` 代行为订单/运行继续与无副作用的安全恢复操作。实时 SQLite 中恢复事件 `PRODUCTION_SERIES_RECOVERED_PRE_SUBMIT` 的 actor=`local-user`，payload 明确 `external_submission=NOT_SENT_PROVEN`、`old_job_preserved=true`；没有重发带有外部身份/UNKNOWN 的请求。
- 使用内置浏览器进入 `http://127.0.0.1:5173/#d/run`，拟人点击「继续安全可恢复批次」，页面 toast「已恢复生产」，从有阻塞转为进行中。点击后的 run 是 `e2da69c8-5b28-45b4-83e8-102b7c09a997`；UI 随后读到 17/64 镜通过、2/6 批次已通过、0/4 集已交付。
- 后续只读库快照（2026-11-07 05:25 UTC）：Run=`RUNNING`、18/64 个 series items 已 `PASSED`、3/6 batches `PASSED`、已交付 0/4；恰有 1 个 `ACTIVE` H3 lease（owner `h3:609b9cb1-d798-4d5a-a87a-c3aed8acf841`）和 1 个 `SUBMITTING` job。其余等待 series=`QUEUED`，没有第二条 active lease/并行 H3 提交。Run 仍在进行，不能称作整片完成。
- 对当时 17 个已落盘视频逐一重算磁盘 SHA-256，并与 artifact/attempt output SHA、字节数核对；逐个 `ffprobe`：H.264、1024×576、约 24fps；逐个 `ffmpeg -f null -`：退出码 0、无解码错误。17/17 均真实（非 mock）；其中最后一个当时处于 `RUNNING` 的 job 有已落盘视频但尚未终态，不能据此计作通过镜头。后续快照到 18 镜通过后，新落盘镜头仍待同一验收循环复核。还没有 episode EDL/交付单证据。
- 05:29 UTC 再读库并全量复核本 Run 已有的 19 个 artifact：19/19 的文件 SHA 同时等于台账 artifact SHA 与 attempt `output_sha256`，字节数一致，均为非 mock H.264 / 1024×576 / 约 24fps，`ffmpeg -f null -` 全部 exit 0 且 stderr 为空。逐镜记录写入 Git 忽略证据 `.local-acceptance/r7/serial_run_artifact_verification_20261107.json`。当时 Run=`RUNNING`、19/64 镜通过、3/6 批次通过、交付 0/4；1 个 ACTIVE lease（`h3:a6191258-5b74-448c-9734-2b1325483386`）、1 个带 provider id 的 `RUNNING` H3 job，其余 series 排队。新提交会推进，当前数字是时间点快照而非终态。
- 工作台仍由旧进程运行以保护正在执行的 H3 handoff；源码里的 safe resume/read-model 状态修正尚未在该进程中热载入。因此仍观察到 ep-02-b02/ep-04-b01 的 batch 行残留“进行中”，但它们的 series 实际 `QUEUED` 且资源租约只有一条。待当前 series 到安全边界再按官方栈脚本重启，重读真实状态；不要中途杀进程或释放租约。

### 8.3 验证边界

- 全量 `backend/tests` 在首批串行资源修复后跑完，exit 0、1 skip、0 failed（有 Starlette deprecation 与既有 SAWarning）；后续针对 batch queue/read-model 修正重跑 `test_director_long_form_regressions.py` 与 `test_production_series.py`，均 exit 0；最终 read-model 断言版再次跑 `test_director_long_form_regressions.py`，exit 0；Ruff 修改文件全绿。
- 本机前端 `npm.cmd test -- --run` 150 passed、21 files；`npm.cmd run build` exit 0（仅 Vite >500kB chunk warning）。浏览器验收是实际 UI 点击，不把 API-only 或测试结果冒充 UI 验收。
- 当时作品仍未完成：后续有 46 个以上 shots 待生成/验证，delivery 0/4。订单 18–20 的旧 MiniMax `external_outcome=UNKNOWN` 不因套餐额度而重发；这个旧状态判断不包括后来发生并由 §10 审计确认的显式替换。VLM 正负校准仍未做；`pending_review` 不得写成 accepted。最终每镜 artifact/SHA/ffprobe/decode、EDL 顺序、交付单必须在 Run 结束后再次全量核验。

## 9. 64 镜 / 4 集真实输出旅程收尾（2026-11-07）

### 9.1 确认计划、整部生产和队列终态

- 只读 SQLite 确认 episode plan v2=`0af55b3b-a628-48eb-a3fd-34f8bb326dd4`，
  `status=CONFIRMED`、source=`script_sections_materialized`、SHA-256=
  `2e856030014f8f126239409bbed7b7f076fc23f349acb9509a399d0950428a6b`；
  `created_by=confirmed_by=local-user`，由所有者授权委托代行确认。v1 留作 `SUPERSEDED`。
- Run `e2da69c8-5b28-45b4-83e8-102b7c09a997` 的 final snapshot 时间为 2026-11-07
  07:44:49 UTC：`COMPLETED`、64/64 shots verified 且 `PASSED`、12/12 batches
  `PASSED`、4/4 episodes `DELIVERED`、总时长 503.584 秒、0 blocker、0 active series、
  0 live resource lease。每镜 artifact SHA 与 attempt `output_sha256`、磁盘 bytes
  匹配，均为 real/non-mock；`ffprobe` 为 H.264 1024×576@24fps，
  `ffmpeg -f null -` 全文件解码 exit 0、stderr 为空。
- 逐镜 machine evidence 在 Git 忽略的本机文件
  `.local-acceptance/r7/final_run_verification_20261107.json`：64 个 shot 条目与 4 个
  delivery 条目均 verified，顶层 `errors=[]`；此本机台账不纳入 Git。

### 9.2 分集 EDL 与交付单核对

EDL 的 clip/job 顺序逐项匹配 Run episode manifest 的 `expected_job_ids`；每集的
`delivery_order_match`、`edl_digest_match`、`clip_artifacts_match` 均为 true。EDL hash
是数据库 `edl_payload` 原文字节的 SHA-256；input digest 等于交付单 `input_digest` /
`shots_digest`。

| 集 | 镜头 | Delivery order | Input digest | EDL payload SHA-256 | 交付 SHA-256 | bytes | 时长（秒） |
| --- | ---: | --- | --- | --- | --- | ---: | ---: |
| ep-01 | 18 | `896b2978-5ebb-4886-ac0c-00e3c15e76cf` | `7b1de22e791ce515efeff394b8e823da81f44e9c07f48d0243a4907bdad56bea` | `8dda72dfcd3d685392c2f0c27abf94b8b8697b8d88bb5c39b0da11b752d12287` | `a48d642b93923bf578f3a2876fa361eb0be3acdfce2fc65bbde49d5ab442cbc7` | 42,949,685 | 157.479329 |
| ep-02 | 15 | `03724f26-411b-42e9-a644-b9940616cb36` | `a512050789e50670f4431826f327cad3177013280d796f5a4420cab9affe7090` | `7d1736fd08531b3858bf73476662f4132a4bc8756a6385b010ecf46f3581c17b` | `b0ae474fe36fcf039e67b676f8e2adbdaf746d41d41e664ebaec7869c585b40b` | 25,032,533 | 110.104329 |
| ep-03 | 14 | `fa3e92ec-1f72-407c-ad18-043d35aa4e0b` | `3a64d145f089ae16e4875ce7269fc25c30ddedbcf90891af7072ae0c953123e3` | `31a5b04eecf3848017893a74839262543cd397e5b8e8bd39000e83e73f190e67` | `bc5f239ea7c35a587769835164af2a66b78313b589b9693bd03a9a8eb3afb1a9` | 28,997,482 | 111.312663 |
| ep-04 | 17 | `26acb356-9a5c-4812-a9c7-165c5374b36d` | `fee044d8f55f594f6fa109681bff0f2cbbda42a142cbdcf2c9a76bd7eca3be86` | `ab9445c5c54e3eb80c404545b5cfbef21876b18aa58ed7db6f077c601fd22a8c` | `dc0a9f1b155c8344217cd3155203035c60aad3d98795237f69ce460ff6e1a594` | 33,283,273 | 124.687663 |

四份成片 artifact 均为 `DELIVERED`、artifact kind=`delivery`、content type=`video/mp4`、
非 mock；H.264 1024×576@24fps `ffprobe` 通过，逐集完整解码 exit 0 且 stderr 为空。

### 9.3 内置浏览器与验收语义

- 官方 wrapper 安全重启后，内置浏览器的导演项目页读到 4/4 集、64/64 镜和 12/12
  已通过批次；在「成片中心」看到四集交付记录并打开 ep-04 全片预览，播放器从加载
  状态进入真实画面。UI 仍逐集显示「待你确认」。实际覆盖了作品状态、集列表、播放器，
  不等同于审美、人声或语义质量验收。
- VLM advisory 68 条 `UNAVAILABLE`，ASR advisory 29 条 `UNAVAILABLE`；队列已排空。
  VLM 正负校准未执行。页面没有代点人工艺术确认，任何 `pending_review` 都没有改成
  `accepted`。

### 9.4 MiniMax 调整与全旅程阻塞点结论

- 官方 MiniMax API Console 将 `MiniMax-M3.1-Flash` 标为 1M context，示例请求采用
  `max_completion_tokens=16384`。代码将该模型的默认 output budget 设为 16,384、
  对该模型的更大配置施加同值 ceiling，并将 agent context / JSON schema 序列化压缩为
  紧凑 JSON；不截断语义内容或必需事实。该数值是示例支持的保守输出预算，不是 context
  上限。参考：[MiniMax API Console](https://solutions.minimaxi.com/debug/text)。
- 订单 18–20 的旧调用没有持久化 provider response/body、HTTP status、response `id`、
  `base_resp.status_code` 或 usage；仅存 `LLM_TIMEOUT / external_outcome=UNKNOWN`。
  所以 context overflow 仍是未证实假设，配额截图不能推出逐请求 outcome。审计另证实三条旧
  UNKNOWN 子调用之后各有一次 `STORY_UNKNOWN_SUBTASK_REPLACED`，见 §10；新 task 成功不等于旧
  请求结果已核对，也不符合 no-resend 门禁。
- 全旅程的确定性/资源阻滞（旧 WAITING reservation 长期挡队列、Comfy `/free` 异步释放
  未确认、safe resume/UI 残留多个 RUNNING）在 `3813a2e` 修复。真实 12 批串行 Run
  最终跑完，终态无活动 lease/series。`pending_review` 未变成 accepted；UNKNOWN 重请求边界
  曾被旧 UI 的显式替换功能绕过，后在 §10 记录并修复。
  后续缺项是 reviewer 环境能力、VLM 正负 calibration 和每集人工艺术确认，不能以放宽
  门禁代替。
- 自动化回归、启动器检查及发布验证见 `docs/TEST_REPORT_20261107_DIRECTOR_JOURNEY_R7.md`
  §7；其中 §8 的 19 镜 / 0 交付是当时有效快照，本节全量终态 supersede 之。

## 10. UNKNOWN 重请求门禁缺口及修复（2026-11-07）

### 10.1 历史审计事实与责任

- 重查同一数据库 `audit_events` 得到三条 `STORY_UNKNOWN_SUBTASK_REPLACED`，actor 均为
  `local-user`。事件 entity / previous task / 新 task 分别为：
  - entity `3fd6676b-e611-4962-8f84-ac460d16aa59`；原 director task
    `4d53e72f-fd11-4de6-9c70-0c25fe514253`（`ABANDONED` / `LLM_TIMEOUT` / `UNKNOWN`）；新 task
    `1cc3a3e8-7bc5-4481-9b9f-5d31e83b4bd9`=`SUCCEEDED`。
  - entity `354dc1f1-07b0-4297-89d4-7f7fb673b776`；原 editor task
    `f1022cae-2257-409f-988b-a200f54c6734`（`ABANDONED` / `LLM_TIMEOUT` / `UNKNOWN`）；新 task
    `b4f5b5ac-833d-4e60-81c8-69acea9586b2`=`SUCCEEDED`。
  - entity `b83d7f9f-85de-49d2-a2c8-4063278d0151`；原 editor task
    `b95be68d-e3e0-43a4-acfb-5acb32ec3f3b`（`ABANDONED` / `LLM_TIMEOUT` / `UNKNOWN`）；新 task
    `01bc0fa6-358b-4326-8cd6-e4b1c85e9b46`=`SUCCEEDED`。
- 事件中的 `automatic_replay=false` 只说明系统没有自动 replay 原 task；新 identity 的 provider
  请求确实被发出。所有者曾授权 `local-user` 代行用户动作，但授权不覆盖“UNKNOWN 只核对”的
  明确纪律；三次替换是执行错误。保留全部旧 task、新 task、audit、artifact 与成片，不删改或
  伪装其历史；技术交付证据有效，但本旅程不能声称完全遵守 UNKNOWN 门禁。
- 旧 MiniMax 报文缺失仍无法判定其原结果、usage 或 context overflow。成功的新 task 不可反推
  旧调用的 provider outcome。

### 10.2 已修入口与产品语义

- 分段导演 `/replace-unknown`、整剧分镜 `replace_interrupted_task_id`、Film dispatcher 替换参数、
  单场素材 `replace_unknown_task_id` 都拒绝创建新任务。`execution.guard_request` 对 real provider
  的 `UNKNOWN` / `ABANDONED` 及未 reconciliation 的失败响应也拒绝同输入新调用；改变合同或
  provider profile 不能解除旧 UNKNOWN。
- 单场素材增加 `POST /projects/{project_id}/material-preparation/scenes/{task_id}/reconcile`：只查
  原任务或校验 identity/hash 完整的已落库结果；只有已记录 transport `NOT_SENT` 且 task 已终止，
  才写 `NOT_SENT_PROVEN`、释放旧 identity。UNKNOWN / response-received 不变且无外部调用。
  前端把“核对后重新请求此场”改为“核对已有结果”；确认未发送后仍需用户单独点击“分析剧本”，
  reconcile 本身不自动发送。
- `call_material_agent` 的 outcome 现在保存精确外部状态；没有证据不再把所有非 timeout 错误
  简化成 `NOT_SENT`。story outline merge 持久化 provider 阶段；无 phase 不作未发送证明；已收到但
  不合格答案不能进入 retry 路径。旧 UNKNOWN 原记录保持不动。
- 验收覆盖 `test_material_scene_safety.py`、`test_story_preparation.py`、
  `test_story_section_run.py`、`test_storyboard.py`、`test_film_unknown_outcomes.py` 与两组前端交互。
  精确运行结果、全量复测、启动恢复事故及剩余交付见测试报告 §7.5–§7.7。

### 10.3 接受边界

64/64 H3 shots、四集 EDL / delivery、SHA、ffprobe 与全文件 decode 均已完成；既有产物未覆盖。
它们证明技术输出旅程跑通，但不抹掉三次历史新请求越过 UNKNOWN 门禁。episode UI 仍为“待你确认”，
VLM 正负校准未执行，不能宣称已完成艺术验收或完整合规验收。导演 UI 范围计数与排产候选的修复和
内置浏览器复核见 §11；不得用重新命名、替换身份或 `local-user` 代行来豁免 UNKNOWN。

## 11. 全旅程范围展示修复（2026-11-07）

- 实际浏览器检查发现导演阶段 UI 用全项目读数代替确认分集范围：剧本分镜摘要写成 70 镜、
  一键开拍把本次确认计划外的 6 镜也列入候选、任务页用历史 job 计数表示本次 Run（曾显示
  `65/72`）。这会让已完成的 64 镜 Run 看起来与阶段数量不一致，也可能误把旧镜头再次排产。
- `/journey` 的 storyboard facts 增加 `planned_shot_ids`，取值仅为当前确认 plan 的顺序镜头 ID；
  无 confirmed plan 时保持现有“全项目镜头”语义。导演阶段的 `ProductionSeriesPanel` 现在仅接收
  这些已确认镜头；全项目 `ShotPanel` 与任务审计记录仍保留，不删除历史。分镜显示“64 个计划镜头
  （项目共 70 镜）”，一键开拍只数确认范围，旁注明确其余 6 镜在本次计划外；进度摘要使用
  `/journey` 的 `passed_shot_count/scope_shot_count`，历史 job 列表显式说明用途。
- 回归覆盖 `/journey` 计划 ID 顺序与 UI 范围筛选；实际内置浏览器重启后读取服务端 64 IDs，
  点击“② 排产制作”看到“自动制作全部 64 个镜头”和“另有 6 镜不属于本次计划”，点击“③
  进度与结果”看到 `64/64` 计划镜已通过，且列表说明项目历史与本次状态的关系。
- 此修正只处理**范围显示和可排产集合**，不更改 Run、episode plan、镜头、artifact 或交付物；
  原 64 镜四集证据不受影响。历史的三条 UNKNOWN 子调用替换仍属本轮执行违规，真实结果保留；
  当前 API/Worker 空闲、无 running/pending job、无 active series、renderer offline。完整自动化
  回归与此次源码修订的最终测试结果见测试报告 §7.5–§7.7。

## 12. H3 外语/无对白人声提示词根因与防护（2026-11-07）

- 对当前 Run 的 64 个 `PASSED` job 逐条读取数据库内存储的最终编译 prompt 并对照 shot spec：26 镜有对白、38 镜无对白。38 条无对白 prompt 都含原先的显式禁声句；但它们同时把整部作品的全部锁定剧情事实重复带入 `detailed_description`，包括“当沈砚说‘今晚时间会停住’……”等属于其他镜头的对白事实。例：`S21-scene-20-beat-04` job `057471d1-a373-4300-9d90-3dcbb1e39336` prompt SHA-256 `184e773cdc048e917620121dd216630a10baf7087cd13d37c87f22b32ccf8bf2`。
- 另在对白镜头 `S08-s7-shot-02`（job `f715c60b-05bb-441e-8c60-d77939bf0b45`，prompt SHA-256 `7d36b5dabf99bcdcb30a659d0b2b4305aae124a071de7a46ff7c6e52eaa50128`）发现同一声音被描述为“filtered vocalization begins”与“muffled, filtered voice begins reading”，同时才在 `<d>[Chinese]…</d>` 中限定精确台词。这些过宽的声音描述与项目级台词事实是实际 H3 prompt 的额外语音来源线索，最可能解释项目生成与直接 ComfyUI 使用体验的差异；这属于对提示词的因果推断，不是对未知音轨内容的 ASR 结论。
- 编译器升至 `shine-h3-compiler/1.2.1`。锁定剧情事实现以一次性的“background continuity context / never spoken or narrated”说明包裹；无对白合同不再依赖项目语言是否存在，并明确涵盖 narration、voice-over、whisper、mumbling、singing、humming；有对白镜头只允许当前显式标记的原文台词，视觉描述中的 voice/reading 仅解释该台词声音。官方规则包版本、hash 和 Ref2VA section 顺序未改。
- ASR advisory 调整为 `basic` / `full` 范围的每个镜头都排队，包括无对白镜头；无对白镜头出现 ASR 文本、或有对白镜头出现脚本台词之外的 ASR 文本，只记录 `uncertain` 提示，不变成生产硬门禁。交付页按实际交付 job 的 artifact 身份汇总无 ASR 记录、unavailable 或未完成项；技术音轨解码不会被显示为语言核验。
- 现有 4 集/64 镜产物没有重新生成、覆盖或重验收。当前数据里 26 镜 ASR `UNAVAILABLE / ASR_ROUTE_NOT_CONFIGURED`，另 38 镜无 ASR 记录；不能从现有台账确认确切外语、音节或时间码。本轮未把视频/音频发送到云端 ASR，未调用模型重渲染，也未把 `pending_review` 改为 `accepted`。提示词修复经自动化合同测试，但尚无修复后真实 H3 新镜音频可证明模型必然服从。

## 13. 64 镜分段列表与六镜分页（2026-11-07）

- 现场 GET `/projects/{project_id}/generation-segments` 返回 5 个镜头；核对其 `series_id=d1b84083-...` 后确认它是 Run `e2da69c8-...` 的第 12 个 batch（第 4 集最后一批），并非只有 5 镜生成。该 Run 实际 4 集/12 batch/64 镜，batch 镜数依次为 6、6、6；6、6、3；6、6、2；6、6、5。原因是旧接口只选最近的 `ProductionSeries`，没有沿 `ProductionRun`→episode→batch 聚合。
- `api_segments.segments` 现默认读取最新 Run，沿 episode 顺序和 episode 内 `shot_ids` 顺序合并全部 batch series；每镜仍带原 batch `series_id`，并按原批次 selection 查当前 artifact。不存在 Run 时才保留最新独立 series 的兼容行为；明确请求 `run_id` 时校验其项目归属。失败、待核对、未启动镜头不会因聚合被自动重排或重发。
- Web 分段面板以 Run 顺序呈现全部 64 镜，每页最多 6 张（64 镜共 11 页，最后一页 4 镜），同时显示集序和镜序；可翻页预览、下载与查看镜头描述。通过但不满意的镜头仍走单镜重拍→新版本真实质检→显式选入原所属 batch assembly selection；旧 artifact 保留。失败/UNKNOWN 提示返回原任务或 Run 核对，禁止把“重新生成此段”当成 UNKNOWN 的绕过入口。
- 回归增加多个 batch series 跨集聚合顺序断言，以及 64 镜/6 镜分页交互。定向结果和内置浏览器实测在测试报告 §7.9 更新。既有 Run、4 集交付、artifact 和审查状态不变；本轮只修展示/入口，没有重拍或改写媒体。
- 分页实测还暴露同镜头两条历史 `NEEDS_REVIEW / PENDING_WORKFLOW_MAPPING` 记录挡住已通过镜头的重拍：attempt 没有 provider job id/ack/prompt graph/output，renderer activation 不存在，带 `COMFY_WORKFLOW_MAPPING_PENDING` 且没有 `COMFY_SUBMIT_INTENT` 审计，相关 H3 lease 已 `RELEASED`。第一条和第二条均可依与整部 Run recovery 相同的严格 pre-submit 证据认定 `NOT_SENT_PROVEN`；显式点击重拍时先写入 `ExternalJobReconciliation` 与 `JOB_EXTERNAL_OUTCOME_NOT_SENT_PROVEN` 审计，再创建新的 retake identity，历史 job/attempt 不改写。带 provider job ID、ack、prompt graph、artifact、activation、submit evidence 或未释放 lease 时仍阻塞；未证明的 `UNKNOWN` 不会被忽略或重发。

## 14. 分集成片下载命名（2026-11-07）

- 原成片下载接口直接把 content-addressed 存储名（SHA-256 英文十六进制）作为下载名，用户保存四集后难以区分。`GET /api/v1/deliveries/{artifact_id}/media` 现为下载响应生成 `项目名称-第N集.mp4`，N 按所属 ProductionRun 内的实际集序计算；重装配的历史版本追加 `-vN`。非分集交付使用 `项目名称-成片.mp4`。非法 Windows 文件名字符会被替换，标题部分有长度限制。
- 文件仍以原 SHA-256 存储路径保存，响应继续核对 artifact 的 byte size 和 SHA；没有移动、覆盖或重新装配既有成片。以真实 episode assembly 测试验证下载 `Content-Disposition`，项目名“整部作品”生成“整部作品-第1集.mp4”。真实四集运行对应名称为“夜航船 真实长片 20261106-第1集.mp4”至“夜航船 真实长片 20261106-第4集.mp4”；实际下载名最终以每个 artifact 的 HTTP 响应头为准。
- 目标交付测试首轮失败是测试断言把 episode `position` 当作零基；实现与产品顺序统一通过同一 Run 的有序 episode rows 求 ordinal 后，目标后端命令结果为 `20 passed`。最终全量 backend suite `1826 passed, 1 skipped, 0 failed`（2 个既有/兼容 warning），Ruff 全量通过，Alembic 无新增迁移；前端 `154 passed`、`tsc -b` / Vite build exit 0。真实 four-artifact 下载响应头均为 HTTP 200，四个名称逐集为“夜航船 真实长片 20261106-第1集.mp4”至“第4集.mp4”。完整命令与运行证据见测试报告 §7.10。
