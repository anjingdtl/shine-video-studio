# 交接：导演模式剩余链路（2026-11-06 晚，视频段待 GPU 空闲）

本文件给"接着往下跑"的人（或下一次会话）用。**只写已经用真实调用验证过的衔接**，
以及每一步的预期阻塞与处理办法。不要跳步，不要用测试图样或 mock 冒充。

---

## 1. 现在真实到哪一步（都以库为准，不以文档为准）

项目 `3707a9ed-eced-435b-b69a-0b1ecdd5f146`《夜航船 真实长片 20261106》

| 物 | id / 值 |
| --- | --- |
| 剧本 | document `2670e301-39ac-494a-a61a-916ec3722dff`（MiniMax 真实生成，20 场） |
| 已确认大纲 | `22dd8d5e-4f44-4c57-b422-2a4f909f56d7`，sha `c7ff9ffd…`，21 个生产段落，7 条悬念按作者决定保留 |
| 分集方案 | v1 `ef996d7a-94a5-4867-a4b4-566c05f287b9`，4 集，**DRAFT→CONFIRMED，但尚未 materialize** |
| 冻结风格 | `46c5041a-e6a0-4a10-96e2-559dbf27f0a6`（V1 冷调悬疑） |
| 主素材 | 老周 `cb93027d`、沈砚 `86d4c050`、货轮 `85ab1ed7`、夜航船甲板 `e670eb38`、码头 `b88e5ea9`、缆绳 `4c6f1004`（另有误晋升的 `validation-scene` `2e8b478a`，无绑定、未被使用） |
| 方案 | 4 个 Ref2VA 已在真实渲染器校验通过；本片用 `ee3649d6`（pdd8-2img-1aud：1 subject + 1 picture 槽） |
| 第 1 场分镜 | 任务 `343a8834` SUCCEEDED；camera `8ac8ecbb`、continuity `0964c72f`、edit-intent `d2069b57` |
| 第 1 场镜头 | `38d4a1d7`（S1-S1_S01，15s，绑定 `老周`=subject(0)、`夜航船甲板与船尾`=picture(1)） |
| 真实成片 | 手工镜头 `96c6d395` 的 job `aa8baa09` PASSED，artifact `92b862e6…`（6.583s / 1024×576 / 24fps / h264+AAC，解码 0 错） |
| 第 1 场渲染 | job `51be58ff` 因 `COMFY_INSTALLATION_BINDING_CHANGED`（出图渲染器残留）在提交前失败，已 `abandon`，**没有外部提交**，可重试 |

**衔接验证（本轮实跑，非推断）**：`section_storyboard_shot_ids(db, P, section)` 对
第 1 场返回 `['38d4a1d7']`、`missing=[]`；对第 2/3 场返回 `missing=[{'reason':'SECTION_RUN_MISSING'}]`。
这说明"场次 → 已应用分镜 → 镜头"的解析是通的，`materialize` 会按这个口径逐场要求。

---

## 2. 剩余链路（顺序固定）

### 2.1 逐场正式分镜（做完全部 21 场）

```
POST /api/v1/projects/{P}/story-preparation/sections
{"outline_artifact_id":"22dd8d5e-…","expected_outline_hash":"c7ff9ffd…",
 "production_section_ids":["<该场 id>"],"continuity_facts":{}}
```

- 每场会产生一个 `story_section_run` 任务，五角色链 + 仲裁，**约 1.5–3 分钟/场**。
- 已完成：第 1 场。其余 20 场用上面同一条调用逐场提交（**一次一场**，便于定位失败）。
- 成功后按 `progress.results[0].storyboard.next.apply_camera` 调用：

```
POST /api/v1/projects/{P}/film-artifacts/{camera_artifact_id}/apply-camera
{"continuity_artifact_id":"…","arbitration_artifact_id":null,"name_prefix":"S<n>"}
```
  返回 `shot_ids`，即该场镜头。

**预期阻塞与处理**

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `REJECTED / LOCKED_FACTS_CHANGED` | 模型没有逐字回显 9 条中文锁定事实（事实是长句） | 直接 `POST …/tasks/{id}/resume` 重跑该角色，实测第 2 次即通过；连续 3 次不过就把事实改短（需重跑大纲） |
| `NEEDS_REVIEW / continuity_issues: reference_set_status policy_compliant=false` | 该场需要"场景|画面"参考，而项目里还没有对应的场景主素材 | 先给该场建**单场**素材计划并出场景图（见 2.2），再重建该场分镜任务（`asset_manifest_hash` 变了会新建任务） |
| `MATERIAL_BRIEF_NEEDS_CLARIFICATION` | 出品人简报有待澄清问题 | 把答案写进英文 `appearance` 后 `POST /material-preparation/regenerate`（本轮实测一次通过） |

### 2.2 场景/道具主素材（**需要 GPU，出图**）

每场的环境与道具是独立 requirement（货舱、驾驶台、水道、渔村灯火……）。做法（本轮已对第 1 场跑通）：

1. `POST /material-preparation/analyze`，`scene_preparations` 只放**这一场**的准备结果
   （跨场会 `MATERIAL_IDENTITY_AMBIGUOUS`，见 §4.1）。
2. `POST /material-preparation/confirm`：非人物 requirement 用 `generate`；人物**不要**下决策
   （`老周（周远山）` 与主素材 `老周` 的 entity_id 不同，`reuse_master` 会 409 `ASSET_IDENTITY_MISMATCH`）。
3. `POST /material-preparation/generate`（`rights_id=81343d81-…`，`mode=real`）。
4. Worker 出图（本机 Qwen-Image-2.1，约 20–30 秒/张）→ `POST /candidate-assets/{id}/promote`
   （先 `POST /preparation/approval-grants` 取 `promote` 授权）。

### 2.3 分集落地（materialize）→ 确认

```
POST /api/v1/projects/{P}/episode-plans/1/materialize      # 21 场都有镜头后
POST /api/v1/projects/{P}/episode-plans/2/confirm          # 新版本 v2
```
- 未拆完的场次会 409 `EPISODE_SECTION_NOT_STORYBOARDED` 并**逐场列出** pending，不会静默跳过。
- 第 1 场的镜头已经能解析到，可作为回归探针。

### 2.4 导演包（build → confirm）

```
POST /api/v1/projects/{P}/director-packages
{"workflow_profile_id":"ee3649d6-…","generation_mode":"Ref2VA","output":{"width":1024,"height":576}}
GET  /api/v1/director-packages/{id}/readiness
POST /api/v1/director-packages/{id}/confirm
```
- `readiness` 会逐条列出阻塞：`PACKAGE_NOT_CONFIRMED` 是预期的（先 build 后 confirm），
  其它如 `SHOT_STYLE_MISMATCH`、`SHOT_REVISION_CHANGED` 必须真修，不要改包。
- 本项目**没有** comfyops 制片准备计划（legacy 口径），所以 `PREPRODUCTION_NOT_FROZEN` 不会出现；
  若要更严，可先 `POST /preparation/preproduction/analyze` → `readiness` 到 `SHOOT_READY` →
  `freeze`（需 `manifest_freeze` 授权）。

### 2.5 整部作品生产（**需要 GPU，视频**）

```
POST /api/v1/projects/{P}/production-runs {"package_id":"…"}
GET  /api/v1/production-runs/{run_id}          # 无人值守进度
GET  /api/v1/production-runs/{run_id}/blockers
```
- Worker 逐集建 **production series**，一个 series 独占 GPU 租约跑到底，跨镜走 idle keeper 的租约转移。
- 单镜实测：模型加载约 40 秒，6.5 秒成片约 2 分钟，15 秒镜头更久；20+ 镜按 1.5–3 分钟/镜估算。

### 2.6 后期与交付

- Run 的 `post` 阶段按 EDL 顺序拼接，交付单记录每段来源 `job/attempt/artifact` + SHA-256。
- 核对口径：每段 artifact 的 sha256 与台账一致、`ffprobe` 规格、`ffmpeg -f null -` 解码 0 错、
  EDL 顺序与分集一致、交付单状态。

---

## 3. 本轮实测过的"环境类"阻塞（不是产品缺陷，但要会处理）

| 现象 | 处理 |
| --- | --- |
| `COMFY_INSTALLATION_BINDING_CHANGED` | 出图渲染器跑完后受管实例记录不是 `STOPPED`：`POST /api/v1/setup/managed-processes/release`，确认实例 `STOPPED` 后再提交视频任务 |
| H3 任务 `NEEDS_REVIEW` 且租约 `RECONCILE_REQUIRED` | `POST /api/v1/operator/jobs/{id}/abandon`（无外部提交时可结案并释放租约）；若报 `H3_LEASE_STILL_ACTIVE`，重启工作台触发启动对账（本轮实测有效） |
| 工具环境的删除守卫 | 全量 pytest 在沙箱内跑到后半段会被 `safe-delete` 守卫打断（50 次删除阈值），失败集中在需要删文件的用例；**在沙箱外跑**才是有效结果 |

---

## 4. 已知产品缺口（本轮没有修，不要在验收里假装不存在）

1. **同一人物跨场身份歧义**：`老周` / `老周（周远山）` / `周远山（老周）` 在库里是三个身份，
   每场描述都不同；全片级素材计划会 409 `MATERIAL_IDENTITY_AMBIGUOUS`，而产品没有"合并同一人物"
   的用户动作。目前只能用**单场计划**绕过；镜头靠 entity_id 精确匹配绑定，所以名字变体不会被自动复用。
2. **启动阶段失败的 H3 租约死锁**：`release_dead_renderer_lease` 对已有 attempt 的任务保守拒绝，
   `operator abandon` 又要求 `H3_LEASE_STILL_ACTIVE` 先解除。
3. **审片 advisory**：VLM/ASR 未配置时 `semantic/style` 门是 `not_run`、`final` 是 `needs_review`，
   `acceptance_status=pending_review` 是正确状态，不要改成 accepted。

---

## 5. 纪律

- 不删除、不覆盖已通过的 artifact 与主素材；产物目录只增不改。
- 任何 `external_outcome=UNKNOWN` 的子调用只核对不重发；`NOT_SENT_PROVEN` 才能重新请求。
- 不为了让流程走下去而放宽门禁（大纲疑问、媒体族、参考槽位、风格哈希）。
- 报告只写真实证据：artifact sha + ffprobe/解码 + 交付单；服务 READY、mock、历史校准都不算。
