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
- 因无法取得 provider 侧结果，本轮通过产品路由 `POST /api/v1/projects/{P}/film-runs/{task_id}/abandon-remote-response`，每次先签发一次性 `model_reconcile` grant；由受托 `local-user` 写明证据后，三个子任务均返回 `ABANDONED / external_outcome=UNKNOWN / automatic_replay=false`。这是放弃丢失响应的审计动作，不代表证明未发送或成功，也没有重发请求。
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
- 当前作品仍未完成：后续有 46 个以上 shots 待生成/验证，delivery 0/4；订单 18–20 的旧 MiniMax `external_outcome=UNKNOWN` 不因套餐额度而重发。VLM 正负校准仍未做；`pending_review` 不得写成 accepted。最终每镜 artifact/SHA/ffprobe/decode、EDL 顺序、交付单必须在 Run 结束后再次全量核验。
