# 2026-11-06 导演模式解阻与真实 H3 出片（接手轮）

本文件只记录**本轮真实发生**的事实：修了什么、跑了什么、哪些是本机 H3 真实产物、
哪些仍然没做完。任何未跑通的项都在第 5 节具名列出，不算通过。

---

## 1. 本轮起点（交接状态复核）

| 项 | 交接描述 | 本轮实测 |
| --- | --- | --- |
| 分支 / HEAD | `codex/cross-series-continuity-20261106` / `c6d9582`，工作树干净 | 一致（未 reset、未 checkout、未 clean） |
| 工作台栈 | 8000/5173/8116/8188 均无监听 | 一致，起栈前 `nvidia-smi` 无计算进程 |
| 过期租约 | `92d9cf3e`（vlm，ACTIVE）、`66575f45`（agent，WAITING） | 一致，均阻塞 GPU/AGENT 准入 |
| 大纲任务 | `08242db9` NEEDS_REVIEW，子任务 `7108acb2` `external_outcome=UNKNOWN` | 一致；子任务 `phase=WAITING_RESOURCE`（从未发出请求） |

**重要运行事实**：工具侧会在每次调用返回时回收该次调用拉起的进程树，因此
`scripts/start_shine.ps1` 直接调用会在十几秒后被回收。本轮用一个"持有"包装脚本
（`.local-acceptance/_hold_stack.ps1`，非产品入口）把官方启动器拉起的进程树保持在一个
存活的调用里，栈才跨调用存活。产品入口与启动参数未改。

---

## 2. 两条过期租约：按产品路径对账（无盲释放）

| 租约 | 事实 | 处理路径 | 结果 |
| --- | --- | --- | --- |
| `66575f45`（`agent:`，WAITING） | 从未被准入（WAITING = 队列位，从未持有模型） | `POST /comfyops/resource-reservations/{id}/release`（产品自带、未附加条件） | `RELEASED` |
| `92d9cf3e`（`vlm:`，ACTIVE 过期） | 挂着真实 `model_call:VLM:review` `de02c6c0`（RUNNING，owner pid 已随重启消失） | 先用产品自己的启动构造器（`build_review_launch` + `OwnedProcessGroup`）拉起审片服务，`/health` 证明 `loaded=false, allocated_bytes=0`；再 `POST /preparation/model-calls/{id}/abandon`（一次性授权） | 调用 `ABANDONED`（`LOST_RESULT_NOT_APPROVED`），租约 `RELEASED` |

对账后资源台账：**无 ACTIVE / WAITING / RECONCILE_REQUIRED 租约**，8188/8116 无监听，
GPU 回到基线（约 0.9–1.0 GiB）。

### 2.1 顺带修掉的真实缺陷：本机代理劫持了本地释放证明

`LocalReviewRelease.release_models()` 用 `httpx.Client(...)` 时继承环境代理
（本机 `HTTP_PROXY=http://127.0.0.1:19546`，`NO_PROXY` 为空）。对 `127.0.0.1:8116`
的 `POST /free` 通过了，但随后的 `GET /health` 被代理回 **404**，于是
`MODEL_RELEASE_UNCONFIRMED`——一条"本机模型已释放"的证明永远无法成立，租约也就永远
卡住。

- 修复：`release_models()` 对已证明为环回的端点使用 `trust_env=False`；
  同一处推理/转写客户端在**环回**地址上也不再把请求交给宿主代理（远端仍走代理）。
- 证据：修复前 `_probe_release_url.py` 稳定复现 404；修复后 `abandon` 返回 200 并释放租约。
- 测试：`backend/tests/test_runtime_reviewers.py`、`test_gpu_release_confirmation.py` 通过。

---

## 3. 修掉的三处"走不下去"的缺陷（含回归）

### 3.1 中断的场次子调用让大纲任务永久不可恢复

`POST /story-preparation/tasks/{id}/reconcile` 原本只返回一句说明、**不改任何状态**；
而 `resume` 在阻塞子任务为 RUNNING/NEEDS_REVIEW 时 409，于是"核对已有结果 → 恢复未完成
工作"这条产品路径实际走不通。

现在 `reconcile` 按证据给出三种结论，且绝不重发：

| 结论 | 判据 | 对父任务 |
| --- | --- | --- |
| `RESULT_ADOPTED` | 子任务 SUCCEEDED 且有不失效产物 | 直接采用，解除阻塞 |
| `NOT_SENT_PROVEN` | 子任务停在 `PREPARING / WAITING_RESOURCE / PREPARING_PROVIDER`，无产物，`external_outcome∈{None,UNKNOWN}`（网关在真正调用**前**才写 `WAITING_PROVIDER`） | 注销为 `SUPERSEDED`，释放 request_identity，允许重新请求 |
| `UNCONFIRMABLE` | 可能已发出 | 保持阻塞；只有用户显式替换才能新建调用 |

`FAILED`（模型给了回答但被合同拒绝）另有一条**有上限**的重试路径
（`FAILED_RETRY_ALLOWED`，每场最多 2 次，超过即 `RETRIES_EXHAUSTED` 交回人工）。

本轮实测：`7108acb2` 的 `phase=WAITING_RESOURCE` → 结论 `NOT_SENT_PROVEN`，
注销后大纲任务恢复并从第 2 场继续，最终 SUCCEEDED（20 场，21 个生产段落）。

### 3.2 注销后仍占着唯一的请求身份（`IntegrityError`）

第一次恢复时任务以 `IntegrityError` 失败：被注销的行仍持有项目内唯一的
`request_identity`，新请求无法取得同一身份。修复：注销时把身份移到
`trace.previous_request_identity` 并清空列；`reconcile` 另做一次幂等修补，
把历史 `SUPERSEDED + NOT_SENT` 行上残留的身份释放掉（本轮实测释放 1 条）。

### 3.3 "有待确认问题的大纲"永远无法确认（悬疑剧本死锁）

`confirm` 在 `payload.questions` 非空时 409，UI 也禁用按钮，唯一出路是"补事实再重生成"。
本轮事实：三次真实生成分别得到 12 / 7 / （第三次在 S2 因 `LLM_FORMAT` 失败）条问题，
补充 9 条锁定事实后仍会产生新的"哪一版顺序为准"问题——对悬疑剧本这是一个**不可满足**的门禁。

修复保持门禁强度、只增加一个**显式用户决定**：`confirm` 接受
`acknowledge_questions_sha256`（服务端在 `GET story-preparation` 里给出，
绑定该 artifact 的当前问题列表）；未提供或与当前问题不符 → 仍然 409。
确认审计里写入 `questions_acknowledged`，artifact 本身不被改写（其 sha256 仍与
payload 自洽）。

本轮据此确认了大纲 `22dd8d5e`（21 个生产段落，7 条悬念按作者决定保留）。

> 说明：这一条改的是产品行为，不是测试；如果产品方不接受"作者可确认保留悬念"，
> 需要在下一次评审里否决并改回 409——但那样本项目无法进入分集/分镜/生产。

---

## 4. 真实出片证据（本机 H3，非 mock）

### 4.1 阶段推进（导演模式，项目 `3707a9ed…`）

| 阶段 | 结果 | 证据 |
| --- | --- | --- |
| 读剧本 / 大纲 | SUCCEEDED，21 个生产段落，确认 `22dd8d5e` | `agent_artifacts` + `STORY_OUTLINE_CONFIRMED` 审计 |
| 分集方案 | 4 集（100s±40s）已确认 `ef996d7a` v1 | `director_episode_plans` |
| 素材 | 两位主角真实出图并晋升主素材：沈砚 `86d4c050`、老周 `cb93027d` | 候选 `renderer_owned=true`，`Qwen-Image-2.1`，`materials_ready=true` |
| 工作流方案 | 本项目 4 个 Ref2VA 方案在真实渲染器上校验通过（`missing_nodes=[]`、`missing_inputs=0`） | `POST /setup/profiles/validate` |
| 分镜 / 导演包 / 整部作品 Run | **未做** | 见第 5 节 |

出图两条被真实拒绝的路（都按产品要求补齐，没有绕过）：
`MATERIAL_BRIEF_NEEDS_CLARIFICATION`（出品人要澄清年龄/服装/表在谁手里）→ 把答案写进
英文外观描述后 `regenerate`；`MATERIAL_IDENTITY_AMBIGUOUS`（同一人物在不同场有 4 种描述）
→ 本轮只对**单场**（S16）建立素材计划，跨场同一人物合并仍是一个未解决的产品缺口。

### 4.2 唯一的真实 H3 镜头

手工镜头 `96c6d395`（rev 2）「S16 沈砚从货舱走出」，6 秒，1024×576，无对白；
绑定：`<Subject 1>`=沈砚主素材、`<Picture 1>`=老周主素材
（方案 `ee3649d6` 只有 1 个 subject 槽 + 1 个 picture 槽，第二个 subject 会被
`PENDING_WORKFLOW_MAPPING` 拒绝，因此按产品语义把第二人绑成画面参考）。

| 证据 | 值 |
| --- | --- |
| job / attempt | `aa8baa09-0c1f-4627-a874-193a1a3647e1` / `0f67d5d8-97f9-49ec-9719-df10f8ac4e17` |
| ComfyUI prompt id | `72738f4e-3f5d-4fa4-bea3-1d720f1d8bfc` |
| prompt graph sha256 | `620b22eb1fba…` |
| artifact | `dce63426-d85e-44ca-86f7-aa901c16235d` |
| 文件 | `artifacts/3707a9ed…/aa8baa09…/92b862e64eb7b4e31b2961c455503ff1f0b8fc4bf7163f184ea2bef8a718a190.mp4` |
| SHA-256 | `92b862e64eb7b4e31b2961c455503ff1f0b8fc4bf7163f184ea2bef8a718a190`（与台账一致） |
| 字节数 | 2,049,538 |
| ffprobe | h264 1024×576，24 fps，158 帧，6.583 s；AAC 32 kHz 双声道 6.583 s |
| 解码 | `ffmpeg -f null -` 退出码 0，无错误（**是解码，不只是探测**） |
| 门禁 | technical `passed`(ffprobe)；semantic `not_run`(`SEMANTIC_REVIEW_NOT_CONFIGURED`)；audio `not_applicable`；style `not_run`；final `needs_review`；VLM 探测 `unavailable`(`VLM_UNAVAILABLE`) |
| 验收状态 | `acceptance_status=pending_review`（**不是 accepted**，语义/风格只能由人看） |
| GPU | 峰值 15.4 GiB / 98%，渲染结束后回到 0.95 GiB；租约 `RELEASED`，8188 无监听 |

抽帧目检（第 3 秒）：两个中国人站在昏暗的木舱内，左侧年轻男性着深灰长衫外罩旧黑呢外套，
右侧年长男性着深靛蓝粗布短褂、右手把一只黄铜怀表按在胸前，左侧暖色马灯、背景冷暗、
有胶片颗粒，无文字水印——与镜头规格和两张主素材一致。

### 4.5 收尾时的链路进展（第 1 场正式分镜已打通）

在真实出片之后，本轮又把"分镜链 → 镜头"这一段用真实调用打通了一次：

1. 第 1 场分镜任务 `343a8834` **SUCCEEDED**（scriptwriter → director → cinematographer →
   continuity_supervisor → editor_edit_intent，含仲裁），产物 camera `8ac8ecbb`。
2. `apply-camera` 生成镜头 `38d4a1d7`（S1-S1_S01，15 秒），绑定
   `老周`=`subject(0)`、`夜航船甲板与船尾`=`picture(1)`——正好填满方案的两个槽位。
3. 第一次跑该场时被 `continuity_issues: reference_set_status policy_compliant=false` 拒绝
   （缺"场景|画面"参考）；补出并晋升场景主素材（货轮 / 甲板 / 码头 / 缆绳）后重建任务即通过。
4. 该场渲染任务 `51be58ff` 因出图渲染器残留导致 `COMFY_INSTALLATION_BINDING_CHANGED` 在提交前失败，
   已 `abandon`（无外部提交），**未产生伪造成片**。
5. 衔接探针：`section_storyboard_shot_ids` 对第 1 场返回 `['38d4a1d7']`，
   对第 2/3 场返回 `SECTION_RUN_MISSING`——与 `materialize` 的判定口径一致。

剩余步骤与逐条阻塞处理见 `docs/HANDOFF_20261106_DIRECTOR_JOURNEY_REMAINING.md`。

### 4.3 本轮踩到并绕过的真实阻塞（都按产品路径处理，无伪造）1. `COMFY_INSTALLATION_BINDING_CHANGED`：上一次受管实例记录停在 `RUNNING_PENDING_HEALTH`
   且真的还有一个渲染器活着。用 `POST /setup/managed-processes/release` 关掉它并把记录写回
   `STOPPED`（`outcome=stopped`，`active_processes=0`）。
2. 启动阶段失败的 H3 租约会被 `reconcile_dead_renderer_leases` 保守拒绝（该任务已有 attempt，
   不属于"孤儿"），而 `operator/jobs/{id}/abandon` 又要求 `H3_LEASE_STILL_ACTIVE` 先解除——
   **这是一个双向死锁**。本轮的实际解法是重启工作台，由启动时的对账 + 过期判定把上一轮
   ACTIVE 租约清掉；该死锁本身仍未修（见第 5.3 节）。
3. 中文 Style Bible 直接把 CJK 写进编译提示词，`H3_ENGLISH` 拒绝提交；手工镜头必须自带
   `prompt_style` 英文版（本轮给出七维英文转写并写进 rev 2）。分镜链会产出英文
   `prompt_style`，所以这是"手工入口"的专属要求，不是编译器缺陷。

### 4.4 出片后 Worker 被一条审计写入打死（本轮修复）

19:25:11（出片完成后一秒）Worker 进程退出，页面表现为"显卡空闲、后台像卡住"。
真实原因：审片模型释放失败时（本机审片服务未运行 → `VLM:ConnectError`）写入
`ADVISORY_REVIEW_RELEASE_FALLBACK` 审计，`entity_type='resource_lease'` 却把
`entity_id` 传成 `None`，触发 `NOT NULL constraint failed: audit_events.entity_id`，
异常发生在 `try/except` 之外，直接终止 Worker 主循环。

- 修复：审计改为挂在真实存在的实体上（`agent_task` / `task_id`），并在 payload 里记
  `lease_id`；该写入再失败也不会终止这一拍。
- 回归：`backend/tests/test_advisory_queue.py::test_an_unreleasable_reviewer_is_recorded_and_never_stops_the_worker`。
- 事后：栈重启后 `worker_online=true`，本项目的三个任务状态为
  `aa8baa09 PASSED`、`518d3ddb NEEDS_REVIEW(PENDING_WORKFLOW_MAPPING，历史的双 subject 绑定)`、
  `d3b03fe1 FAILED(USER_ABANDONED_UNRECONCILED)`。

---

## 5. 明确的未完成（不计入通过）

### 5.1 真实多集无人值守 20 段成片

未做。原因链（都是真实阻塞，不跳过）：
- 分镜链要求"已确认素材计划 + 冻结风格"，而跨场同一人物在素材计划里被判为
  `MATERIAL_IDENTITY_AMBIGUOUS`（14 种"老周"描述、4 种"老周（周远山）"），产品没有
  "同一人物的多种描述合并"的用户动作；
- 因此只能建立**单场**素材计划（S16），分镜只能覆盖该场；导演包与整部作品 Run 都要求
  覆盖全部分集/镜头，所以没有生成（生成也会在 readiness 因未覆盖被拒）。

### 5.2 真实 VLM 正负校准 / 发布新 revision / 重冻结计划

未做。本轮查清了上一位 agent 的"80 分钟不返回"：本机 `HTTP_PROXY` 被本地审片客户端继承，
长推理请求被交给宿主代理（同一缺陷也让 `/health` 释放证明 404）。已修客户端，
但本轮把 GPU 让给了真实 H3 出片，**没有**再跑校准；旧 revision 不存在，故也没有可弃用的对象。

### 5.3 遗留缺陷（本轮未修，只记录）

- 死锁：启动阶段失败（有 attempt、无外部提交）的 H3 租约，`release_dead_renderer_lease`
  与 `operator abandon` 互相要求对方先完成；目前只能靠重启工作台触发过期对账。
- 素材身份的跨场合并缺失（见 5.1）。
- `LocalVisionReviewer` 的 `VLM_UNAVAILABLE` 仍是 advisory，不阻断 H3（符合设计）。

---

## 6. 回归

- `backend/tests/test_story_preparation.py`：9 项（新增 4 项：未发送证明与恢复、
  可能已发出的拒绝、已决失败的有上限重试、问题确认门禁）。
- `backend/tests/test_material_scenes.py`、`test_story_section_run.py`、
  `test_runtime_reviewers.py`、`test_gpu_release_confirmation.py`：通过。
- `ruff check backend/shine backend/tests`：通过。
- 前端 `tsc --noEmit`：通过。
- 全量 `pytest backend/tests`：见本轮结束时的记录（未跑完不得声称通过）。

## 7. 证据文件

- `.local-acceptance/vlm…`：本轮未生成（校准未做）。
- `.local-acceptance/lease-reconcile-20261106.json`：两条租约的对账结果。
- `.local-acceptance/_shot16_frame.png`：真实成片的第 3 秒抽帧。
- 产物本体在 `.local-acceptance/live-browser/artifacts/…`（Git 忽略）。
