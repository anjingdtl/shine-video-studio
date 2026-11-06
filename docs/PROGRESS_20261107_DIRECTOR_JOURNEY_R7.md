# 进度：导演模式全旅程第 7 轮（2026-11-07 凌晨，接手轮）

一句话：**26 张场景底图 + 6 个人物主素材全部真实出图并晋升；
21 个生产段落中 10 个已分镜并应用（含第 1 场共 30 个镜头）；
剩余 11 段的已知阻塞都在本文档里，含两个已修复的真实提示词缺陷。**

数据目录不变：`.local-acceptance\live-browser`（项目 `3707a9ed…`《夜航船 真实长片 20261106》）。

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
| 9 | S8 最后一个名字 | （camera `529c502d` 链 SUCCEEDED） | ❌ apply-camera 422，见 §5.1 |
| 10 | S9 十年前的名单 | S10-* ×4 | ✅ 应用 |
| 11 | S10 暴雨中的约定 | S11-* ×1 | ✅ 应用 |
| 12–21 | S11–S20 | 未跑 | 顺序执行即可（§5.2） |

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

### 5.1 订单 9（S8）：apply-camera 422 待解

- 链已 SUCCEEDED（任务 `31c64d81`），camera artifact `529c502d-010d-4692-8ca2-f31862b4946c`。
- `api.err.log`：`apply_camera_rejected artifact=529c502d reason=1 validation error for ShotSpec`（pydantic 细节被日志截断）。
- 复现：直接重调 `POST /film-artifacts/529c502d…/apply-camera`（幂等），或按 apply_camera 的构造逻辑在本地重建 ShotSpec 打全 ValidationError。优先怀疑 cut 台词时间重排（`start_s/end_s` 装配分支）。
- 若是模型产出非法 cut 时间：重跑该段（换一条 continuity fact 文本即新任务身份）通常即可。

### 5.2 订单 12–21：直接顺序执行

- 用 `.local-acceptance/r7/storyboard.py <order>`（驱动：queue→等链→REJECTED 自动 resume≤3→apply-camera；facts 在 `facts2.py`，已含 12–21）。
- S12（订单 14）4 人物 + 场景 = 5 参考图，宽方案 6 槽足够；说话方绑定规则已修，`广播` 不在 S12 出现。
- 已知风险：`老周（周远山）` 别名未再复现（本轮 S4/S5 等段 fresh scriptwriter 都写的是 `老周`），若再遇 `BLOCKED_MISSING_MASTERS` 且 missing 是别名，**不要生成第二张脸**，按 §5.3 处理。

### 5.3 已知产品缺口（未修，接手者不要假装不存在）

1. 同一人物跨场别名（老周/老周（周远山）/周远山（老周））没有合并动作；本轮通过 fresh run 自然规避。
2. `broadcast 类"仅声音实体"`没有正式建模；本轮用 character 家族的号角喇叭图满足绑定门（§3.3）。
3. 订单 9 的 ShotSpec 校验错误细节被日志截断（日志只打 str(exc) 首行）。

## 6. 之后的主链（不变）

materialize v2 → 导演包（宽方案、1024×576）→ confirm → production-runs 整部 Run（无人值守）→ 拼接交付核对。
每镜验收口径不变：artifact sha + ffprobe + `ffmpeg -f null -` 解码 0 错 + EDL 顺序 + 交付单；`pending_review` 不许写成 accepted。
