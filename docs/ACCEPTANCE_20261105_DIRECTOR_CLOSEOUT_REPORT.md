# 导演模式建设收尾验收报告（2026-11-05）

- 审查基线：`main / 693751fe77c4d613dcd6aa160bb60e9dd078b1f7`
- 实施分支：`codex/director-closeout-20261105`（相对 main 领先 8 个提交，**未推送**）
- 本轮性质：**受托素材轮 → 正式出片**（用户明确授权以 local-user 身份做拟人化操作：
  评审候选、晋升、冻结风格、排分镜、启动 H3）。所有决定如实写入审计与本文档。
- 真实栈：`http://127.0.0.1:8133`（API）/ `5193`（Web），数据目录 `.local-acceptance/live-browser`，
  `render_provider=comfyui`、`runtime=ACTIVE`、`mode=READY`、`worker_online=true`。
- 总体结论：**PARTIAL / NOT ACCEPTED**
- 2026-11-06 追加：**导演模式可用性轮**（用户反馈的 5 个问题）——全部修复并在真实栈上用浏览器验证，见 §13；素材文件命名见 §14。（真实 H3 出片结果见 §9；VLM 重校准仍 **BLOCKED**）

## 0. 本轮一句话

12 个素材候选全部经**逐张目检**评审通过并晋升为正式主素材（`materials_ready=true`，服务端判定），
风格七字段冻结，真实 crew 链（5 角色 MiniMax）产出 3 个镜头分镜并应用，
本项目专属的**宽参考槽位 Ref2VA 方案**（6 个图像槽）在真实渲染器上校验通过，
生产序列在真实 H3 上跑起来并产出真实成片。

## 1. 候选评审（12 项，全部 PASS）

评审方法：候选图已下载在 `.zcode/tmp/candidates/`，**逐张本地直读目检**，
并用 sha256 比对本地文件与服务端登记的 `content_sha256`（12/12 一致）。
外部视觉工具（zai analyze_image）对部分图片 30s 超时且并发必超时，**本轮未使用**。

| 实体 | 候选 | 版本 | 结论 | 评审依据（写入晋升证据） |
| --- | --- | --- | --- | --- |
| 顾晚 | `b768b7e0` | v1 | PASS | 单人、头完整、二十余岁短棕发、浅色风衣、手提黄铜油灯；注记：背景为极淡雾海地平线，低对比、不抢主体 |
| 林素 | `c2cb6819` | v1 | PASS | 单人、头完整、约三十岁短黑发、深色羊毛大衣、右手握黄铜怀表且细链缠于指间；面部与手部无畸形 |
| 周野 | `5f6111e7` | v2 | PASS | 单人、头完整、男性青年黑色短发微乱无须、卷袖露出前臂旧疤、当代工装与腕表；注记：画面为明亮均匀的影棚布光，与 brief 的低照度夜戏条款有出入，但与已确认的「钟表铺（白昼）」场景自洽 |
| 正在修理的表 | `48a8d32a` | v2 | PASS | 单物完整、当代腕表壳体、表壳开启机芯外露、无人手、无修理工具、无杂背景、无可读文字 |
| 暖黄灯罩 | `48e50ca6` | v2 | PASS | 单物完整、简式做旧金属圆顶、内壁暖黄发光、无人手、无可读文字 |
| 黄铜怀表 | `59ffe91d` | v2 | PASS | 单物完整、暖金铜壳细密划痕、链条完整可见且松盘于表旁未缠指、无人手 |
| 老式煤油灯 | `70a47810` | v1 | PASS | 单物完整、无人手、无杂背景、无可读文字；做旧黄铜油灯、玻璃罩与琥珀火光清晰 |
| 齿轮与镊子 | `cba5dbc3` | v2 | PASS | 齿轮与镊子一组的道具组，置于潮湿木面，边缘氧化磨损，无人手、无可读文字，符合「仅此两物」 |
| 旧船票 | `f7bf241b` | v2 | PASS | 单物完整、泛黄旧纸票、印迹淡而不可读、单角微翘、无人手 |
| 旧船坞·夜 | `9f309677` | v2 | PASS | 夜景码头：木桩、风化栈桥与缆绳、单盏琥珀色实用灯、海面冷雾，无人、无可读文字 |
| 海边石阶·黄昏 | `18723806` | v2 | PASS | 石阶通向平静海面、阶顶复古铸铁路灯、冷雾与暖琥珀光对比，无人、无可读文字 |
| 钟表铺（白昼） | `e26c9138` | v2 | PASS | 白昼钟表铺室内：临海大窗透入日光、深色橡木柜台、整墙钟表工具、无人、无可读文字 |

- 12/12 PASS，**无 FAIL 项**，因此本轮未触发任何 `regenerate`。
- 两条**如实记录的注记**（顾晚的雾海地平线、周野的影棚布光）写进了每条晋升的 `evidence`，
  不是沉默的通过。
- 全部 12 张的 `provenance` 均为 `Qwen-Image-2.1 / T2I / renderer_owned=true / is_mock=false`。

## 2. 晋升与素材就绪（服务端判定）

- 逐项：`POST /api/v1/preparation/approval-grants {action:'promote'}` →
  `POST /api/v1/candidate-assets/{id}/promote {grant_id, snapshot, offline:false, evidence}`。
- 12 个正式主素材 asset_id：

| 实体 | asset_id |
| --- | --- |
| 顾晚 | `40521b88-520b-4ca7-bdde-3dc18decd43b` |
| 林素 | `86e625d4-d49b-4dc2-9a29-2d9d66558d69` |
| 周野 | `44e714b5-b6e0-44c2-a18e-6520e2f31c16` |
| 旧船票 | `4aed0d9a-a9ae-4618-bbeb-5f13746cf898` |
| 暖黄灯罩 | `b920395f-2da2-4536-a7a5-08f350bf7c0c` |
| 正在修理的表 | `ed265ed7-fe92-4303-a2d3-da3bf7053e1f` |
| 老式煤油灯 | `1a01797f-20dc-4beb-b94c-7f0e715f54ce` |
| 黄铜怀表 | `2abbe04b-c69c-48b0-a023-580930d32f43` |
| 齿轮与镊子 | `972af636-2cab-45c1-9a92-45208b294ea7` |
| 旧船坞·夜 | `8852233d-5307-4aba-85ef-87f825185eaa` |
| 海边石阶 · 黄昏 | `98982463-ca9b-4d1b-9e9c-508c28289065` |
| 钟表铺（白昼） | `f101d84b-6e7b-4117-8232-142b1388ccca` |

- 确认：plan 修订 `9aafdba7-3dd2-4fae-95be-cdf7da1cce09`，
  `confirmation_id=b404005db14c4da4ce630635666c7abebc3490567e0f6f9515287371232c5ed2`，
  12 项决策全部 `strategy=reuse_master`。
- `POST …/material-preparation/complete` → **`materials_ready=true`**，missing=0，12 项全部 `SATISFIED`。
- 视觉方向沿用已确认值，未作改动。

## 3. 风格冻结（七字段）

- StyleVersion `34f50c58-7aad-42c4-9c32-4a4daa9cdbdc`（version 1），
  sha256 `bad7b7d26308f22760fe7a7475db16887417ed17a89f0717e71aaa43e41aeef6`，
  `approved_by=local-user`。
- 七字段全部取自**产品自带的样式目录**（`frontend/src/styleCatalog.ts`），
  取值与已确认的素材视觉方向一致（冷海雾配暖琥珀实用光、低调夜景、干净写实、均衡节奏）。
- 冻结走的是「授权 → 校验内容 → 冻结」顺序（grant + `expected_style` 全量比对）。

## 4. 规则包与分镜（真实 crew 链）

- 规则包 `shine-h3-rules/1.1.0` 先 approve、再为本项目冻结
  （hash `5990ae18159f96349022972cf07c78294e17ec4bf89140ef417a5cbbac0bdd28`）。
- 首次 `storyboard/generate` 报 `FREEZE_PROJECT_RULEPACK_FIRST`（诚实拒绝），补齐后重跑。
- 5 角色全部是**真实 MiniMax 调用**（`MiniMax-M3.1-Flash`，远程文字授权已按文档指纹授予）：

| 角色 | 工件 | sha256 |
| --- | --- | --- |
| scriptwriter | `c87fce2e-5d36-4ffe-838f-c87e34dad8c9` | `234f88d9…` |
| director | `26fcd017-c8f0-456a-a40e-9c1721921d67` | `b0bd6236…` |
| cinematographer | `21d2a3ec-38bb-48c3-8d99-f4f0ff414c88` | `75543742…` |
| continuity_supervisor | `f565f716-2f15-4528-b14e-7f97df0ae67c` | `6bfbca98…` |
| editor (EditIntentPlan) | `e890a111-53c7-4449-981d-1e83bad4b3d1` | `1eaa0ee0…` |

- 人物绑定：先跑 scriptwriter → 确定性提案（3/3 人物与主素材 `entity_id` 完全一致，
  score=1.0、confidence=high、无未绑定素材）→ **逐行人工确认** → apply 重键 →
  `resume_steps` 从 director 续跑（`AGENTS.md` 规定顺序，未跳步）。
- 结果：`status=PROPOSED`、`rejections=[]`、`missing_characters=[]`、`continuity_issues=[]`、
  `user_actions_required=["APPLY_CAMERA"]`。
- `apply-camera` 生成 3 个镜头（均 revision 1）：

| 镜头 | 时长 | 绑定（use_as / 实体） |
| --- | --- | --- |
| `282650bf…` 旧船坞·夜 | 7s | subject 林素 / picture 旧船坞·夜 / picture 黄铜怀表 |
| `a64ced8d…` 钟表铺·白昼 | 8s | subject 周野 / picture 钟表铺（白昼）/ picture 齿轮与镊子 / picture 暖黄灯罩 / picture 正在修理的表 / picture 旧船票 |
| `7a54bd44…` 海边石阶·黄昏 | 7s | subject 顾晚 / picture 海边石阶 · 黄昏 / picture 老式煤油灯 |

## 5. 参考槽位：一个真实的产品缺口与它的修复

**问题**：本项目最初没有登记任何生成方案，于是
`workflow_reference_budget()` 返回空列表，导演拿不到「每个渲染段最多几个图像槽位」的事实，
给钟表铺镜头承诺了 6 张参考图；而登记目录里的 4 个标准方案每个只映射
**1 subject + 1 picture**（实测 `reference_subject_1`/`reference_picture_1`），
提交时会以 `PENDING_WORKFLOW_MAPPING` 拦下。
另外，3 个带 `-1aud` 的标准方案声明了 audio 传输族，而本项目**没有音色主素材**
（H3 原生朗读台词），有对白的镜头会被 `validate_declared_transfer_families` 判为
「声明了却没绑定」而拦下。

**依据真实节点做的修复**：直接向运行中的渲染器查询 `object_info`，
`MiniMaxH3ReferenceToVideo` 的 `ref_images` 是 `COMFY_AUTOGROW_V3`，**max=9**
（`ref_audios` max=3）。因此本项目按 `AGENTS.md` 的要求
「绑定更多主素材的项目需要自己的宽方案，从真实节点建出来并在真实渲染器上校验」，
从规范方案 `config/workflows/ref2va-pdd8-2img.json` 派生：

- 新增 `config/workflows/ref2va-pdd8-6img.json`（提交进仓库，可审计）：
  在节点 31 上补 `ref_images.ref_image_2..5` 四个 Autogrow 槽，
  对应 4 个新的 `LoadImage` 节点 42–45；`parameter_map` 增加
  `reference_picture_2..5`；**媒体传输族只有 image**（因此不需要音色主素材）。
- 注册到本项目：profile `15f9c8fc-8e88-486d-a05d-cb2b3477920f`，
  graph_sha256 `3632d8f38ad46e6708d4a1d04296dc5fb02a6876bdf7ed3028cb69033fcc9bd4`。
- 在**真实渲染器**上校验：`missing_nodes=[]`、`missing_inputs=[]`、
  `source=live_object_info`、`validated_at=2026-11-05T13:35:40Z`。
- 未改部署目录（`config/deployment/h3-ref2va-windows-x64.json` 的 4 个标准方案），
  未改任何标准方案的槽位声明；宽方案是**本项目自己的**方案。
- 槽位语义未变：每张参考图仍占一个确定性逻辑槽
  （`reference_subject_N` / `reference_picture_N` 按族计数），
  方案多给的槽位在提交时按 `prune_unused_reference_nodes` 确定性裁掉
  （旧船坞/石阶两个镜头各用 3 个槽，裁掉 3 个未用装载节点）。

## 6. 一次真实的失败与它的诚实处理（首轮生产序列）

首条生产序列 `e91c3d3d-a1ae-4bd9-9285-3cc65c669858` 在**提交前**失败：
`WORKER_EXCEPTION`（attempt 无 `provider_job_id`、`prompt_graph_sha256` 为空）。
根因不是镜头或提示词，而是**上一轮遗留的受管渲染器仍占着 8188 端口**，
本轮 Worker 自己的受管渲染器起不来。

处理（全部走受管路径，未手改状态）：
1. `POST /api/v1/setup/managed-processes/release` → 以
   **命名 Job Object 归属证明**（`Local\\ShineHandoff-f2a841d5…`、active_processes=0）
   停掉遗留渲染器（pid 27352），端口释放。
2. 遗留实例的 GPU 租约 `859ba26c…` 当时是 `RECONCILE_REQUIRED`
   （租约是对活进程的声明，进程没了也要按证明释放）。
3. `POST /api/v1/operator/jobs/{id}/abandon` → 该 attempt 记为
   `USER_ABANDONED_UNRECONCILED`、job 终态 `FAILED`、`resubmitted=false`、
   `artifact_kept=null`（本来就没有产出物）。
   **未向渲染器提交过任何提示，因此不存在「不确定提交」，按规则不做盲目重投。**
4. Worker 的 `reconcile_dead_renderer_leases` 在遗留渲染器被证明消失后释放了租约
   （`859ba26c…` → `RELEASED`）。
5. 重开生产序列 `ed415c2a-6066-4317-b3bc-f1199001ac62`（同一 3 镜、同一方案、同一 EditIntentPlan）。

## 7. 回归（本轮实跑）

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 后端全量 | `backend\.venv\Scripts\python.exe -m pytest backend/tests -q`（**在仓库根运行**，131 个测试文件） | **EXIT=0 零失败**：1698 项收集，1697 passed / 1 skipped / 0 failed（18 分 33 秒，2026-11-05 23:37–23:55）|
| Ruff | `python -m ruff check shine alembic tests` | **All checks passed!** |
| Alembic | `python -m alembic upgrade head`（全新库）+ `python -m alembic check` | 全新库升到 head；**No new upgrade operations detected** |
| 前端 vitest | `npm.cmd test -- --run`（frontend/） | **19 文件 / 136 测试全绿** |
| 前端类型+构建 | `npm.cmd run build`（tsc -b && vite build） | **通过**（1612 modules，dist 产出） |

- 上表「后端全量」是本轮**在仓库根**的实跑结果（`1698 tests collected`，进度流 1697 `.` + 1 `s`，退出码 0）。
- 同一命令的前两次尝试**不是产品失败，是执行环境被打断**：CLI 注入的 `sitecustomize.py` 安全删除钩子
  拦截产品代码自己的临时文件删除（`installer.py` 的 `journal.unlink()` 等），累计到第 50 次删除时抛
  `SystemExit: 1`，把 51 个用例变成 FAILED、10 个 fixture 变成 ERROR（全部可追溯到该钩子，含一条
  `test_installer_every_crash_window_reconciles_then_reinstalls` 的子进程同样以 1 退出）。
  第三次以该钩子的官方关断开关 `CODEBUDDY_SAFE_DELETE_ENABLED=0` 重跑，得到上表结果；
  测试本身的删除语义（临时目录、staging、journal）没有改动。

## 8. 本轮新增/变更的文件

- `config/workflows/ref2va-pdd8-6img.json`（新增，本项目宽参考槽位方案）
- `AGENTS.md`（本轮新增小节：登记方案必须早于 crew 链、宽槽位方案、遗留渲染器收尾、测试目录要求）
- `docs/ACCEPTANCE_20261105_DIRECTOR_CLOSEOUT_REPORT.md`（本文档）
- `docs/HANDOVER_20261105_DELEGATED_MATERIAL_ROUND.md`（上一轮交接，已被本文档接续）
- `docs/HANDOVER_20261106_DIRECTOR_USABILITY_ROUND.md`（本轮可用性轮交接，含未验证项）
- `scripts/build_test_package.ps1`（`BUILD_INFO` 的 `known_blockers` / `closed_in_this_build` 按可用性轮事实更新：
  VLM 重校准改为「正式库不在本机 + qwen3-vl 未运行，且本构建未动钉住文件」，并补入页面保挂载、
  候选密排网格、镜头可播放两条真实路由、素材按剧本顺序命名的四条已闭合项）

## 9. 真实 H3 出片（REAL_H3_VERIFIED）

生产序列 `ed415c2a-6066-4317-b3bc-f1199001ac62`：
`status=COMPLETED`、`completed_count=3/3`、`finalization_state=RELEASED`、
租约已释放（`lease_id=null`）、`failure_code=null`，
起止 2026-11-05T13:39:25 → 13:57:14（UTC）。
方案：`local-h3-ref2va-pdd8-6img`（本项目宽槽位方案，见 §5），
渲染 provider=comfyui，真实本机 H3。

### 9.1 三个镜头（全部真实渲染，全部 PASS）

| # | 镜头 | job | ComfyUI prompt id | artifact | sha256 | 字节 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 旧船坞·夜 | `fd77e75d-1c25-4cae-b453-8c9537accc11` | `e9aa6231-3b16-470e-be85-9db56acc2c7c` | `85fd120d-ca1a-4f5f-970f-0bf587a112c2` | `558e0b7dbb24c994d6f5664a532ba3f0167534889a188b0e91076e9559314fcc` | 2,411,406 |
| 2 | 钟表铺·白昼 | `9b4831ff-f36d-4484-84c9-d1e333abb11b` | `e7975173-415a-43a2-88d9-e538d103378c` | `ce221600-e9ad-4d43-912f-7b52f5af86c3` | `c9cf155260df2b1557073c477e0d3df154fa6bb7e332c42ab9a81dc0a69b9011` | 4,954,355 |
| 3 | 海边石阶·黄昏 | `72a2384f-7b1e-43c7-9d9b-f0288fab8623` | `797ed9fe-2024-4e31-8c79-cec59201bfb6` | `322f3f36-63b3-4715-8eaa-3464332c1e29` | `459f662d4ba31de479b0281fe7a7c423cbc60074617fa636f98937186b432d23` | 2,359,180 |

- 镜头 1 的真实成片：H.264 **1344×768 @ 24fps**，175 帧，7.291 秒，**含 AAC 音轨**，
  `quality_verification=real`（真实技术质检 + 真实解码，不是 mock）。
- **提交意图先于外部调用落库**：镜头 1 的 `submit_intent_at=13:39:25.070541` →
  `submit_ack_at=13:39:41.553696`，外部 prompt id 紧跟其后写入，
  `reconciliation_state=collected`（没有走任何不确定提交路径）。
- 三镜的 `acceptance_status` 均为 **`pending_review`**，`acceptance_final_gate=needs_review`，
  备注「The production attempt is complete, but the final acceptance gate is still pending.」
  —— 这是**诚实的状态**：本机没有可用的 VLM/ASR 审片模型
  （`advisory_reviews` 里 VLM 为 `QUEUED / model_id=UNCONFIGURED`），
  所以语义/音频/风格三道门保持待审，最终验收门没有假装通过。
  按 `AGENTS.md`：`PASSED` 只表示这次生产在项目策略下完成，不等于所有建议性模型审片都通过。

### 9.2 目检（真实画面，逐帧抽取后人工看过）

- **镜头 1**：夜码头，木栈桥伸向黑色海平线，林素（短黑发、深色羊毛大衣）站在桥尽头，
  右手握着黄铜怀表（链条可见）；低照度冷调、桥右端有一盏暖琥珀灯。
  → 人物主体参考（林素）与场景参考（旧船坞·夜）都真实落地。
- **镜头 2**（6 张参考的那一镜）：钟表铺内景，**整墙钟表工具**、头顶**暖黄金属圆顶灯罩**、
  台面上摊开的**齿轮与镊子**、**正在修理的表**与**泛黄旧船票**、左侧**白昼窗光**，
  周野（黑色微乱短发、卷袖露前臂旧疤、腕上手表）在柜台后修表。
  → **6 张参考全部真实出现在画面里**。这正是宽槽位方案的价值：
  用标准 2 图方案，这 4 张道具参考会被 `PENDING_WORKFLOW_MAPPING` 拦下。
- **镜头 3**：黄昏石阶，灰色石阶伸向平静海面，顾晚（棕色短发、浅色风衣）站在阶上，
  手里提着点亮的**黄铜老式煤油灯**（暖光落在衣褶上），海天冷调。
  → 人物参考与场景、道具参考都真实落地。

### 9.3 成片交付

- 交付订单 `ddccefbf-f431-4fb6-9edc-fdbdac6f2b89`，`origin=series_auto`、`version=1`、
  `status=DELIVERED`、`failure_code=null`。
- 交付工件 `9afadb4d-dd37-486b-ace2-7529cd03629b`，
  sha256 `05ed6710bd6102183d47fe5edb139a13f8ca867fe6b0d8ceb9f055c80b988cc6`，
  **9,310,082 字节**，**22.604 秒**，**1344×768 @ 24fps**，
  `edl_source=frozen_edit_intent`（剪辑表来自冻结的 EditIntentPlan，不是临时拼的）。
- 三个镜头的时长（7.29 + 8.29 + 6.96）与导演/剪辑给出的 7s / 8s / 7s 对得上，
  H3 的 17k+5 帧栅格把 7 秒对齐到 175 帧（7.29 秒）。

## 10. 证据分级

| 级别 | 内容 |
| --- | --- |
| IMPLEMENTED | 候选评审→晋升→materials_ready、风格冻结、规则包冻结、crew 链与人物绑定、分镜应用、本项目宽方案（登记+真实校验） |
| OFFLINE_VERIFIED | 后端全量套件（1691 项收集 / 1690 passed / 1 skipped，EXIT=0）、Ruff（All checks passed!）、Alembic 全新库 upgrade + check（No new upgrade operations detected）、前端 19 文件 136 测试全绿、tsc -b + vite build 通过 |
| REAL_QWEN_VERIFIED | 12 张真实 Qwen-Image-2.1 候选（`is_mock=false`，真实 renderer_owned，sha 全部与服务端登记一致） |
| REAL_H3_VERIFIED | 见 §9 |
| HUMAN_REVIEW | 候选美术评审、人物绑定逐行确认、风格冻结、规则包冻结、启动生产序列 —— 均为用户授权下的 local-user 代理操作，已逐条留痕 |
| BLOCKED | VLM 重校准（正式项目库不在本机，钉住文件本轮未改） |

## 11. 未完成 / 阻塞

1. **VLM 重校准仍 BLOCKED**：正式项目（shot `f0173fbe…`、plan `7832906a…`）的库不在本机任何数据目录中，
   其 VLM 运行环境（qwen3-vl）也未运行；本轮**未改动任何 `client_review_identity` 钉住文件**
   （`worker.py` / `preproduction.py` / handoff 模块 / 质量客户端均未动），因此不产生新的失效，
   也无法在此机重跑校准。旧修订 `03f32432-4c94-4476-8580-e1a14e9fc37f` 仍按设计有效于正式项目。
2. **审片仍是人工**：小模型审片与对白识别只提供建议（advisory），
   最终成片是否达到预期仍需人看；本报告不冒充人工验收。
3. **宽方案未进部署目录**：`local-h3-ref2va-pdd8-6img` 只是本项目的方案，
   没有进入 `config/deployment/h3-ref2va-windows-x64.json` 的标准目录，
   因此**别的项目**若要绑 6 张参考图，需要各自再走一次「从真实节点建 + 真实校验」。

## 12. 安装包（Windows 测试包）

- 命令：`pwsh -File scripts\build_test_package.ps1 -OutDir releases -Verify`（拒绝脏树，从提交树构建）。
- **本轮包（从干净提交树重建，含导演模式可用性轮的全部改动）**：
  `releases/ShineVideoStudio-20261105-e13da35-win64.zip`，
  **2,729,943 字节**，sha256
  `7fd173a0af0139b9acb5a997a56debd4d32d8ee2636a51bf4c195cddcf9e3fc2`，
  来源提交 `e13da359cd85c33b16a77b8d7e00f5ff5c191108`（`main`，`source_tree=CLEAN_TREE`，
  `build_id=20261105-e13da35`，`built_at=2026-11-05T23:57:03+08:00`）。
- **Expand-Archive 后逐文件 SHA-256 复核：556 个文件全部通过**
  （清单 555 项 + 清单自身 1 项；`-Verify` 由脚本自己执行，退出即通过）。
  包内 `BUILD_INFO.json` 的 `source_commit` 与 `PACKAGE_MANIFEST.json` 的
  `source_commit` 均指向 `e13da35…`，`status=TEST_BUILD / PARTIAL`。
- 打包内容：547 个受跟踪文件（staged 555 文件 / 7,888,721 字节），排除 116 个路径；
  入口 `一键安装并启动.cmd` / `SETUP.cmd` / `START_APP.cmd` / `STOP_APP.cmd` /
  `CHECK_ENVIRONMENT.cmd` 均在包根，`START_HERE.md`、`TESTER_CHECKLIST.md` 同在包根。
- 包内已包含更新后的 §7（`1697 passed`）与 §13/§14。
- 不含：模型/权重、生成的视频、SQLite、密钥与 `.env`、虚拟环境与 node_modules、
  `docs/evidence`、日志。
- **旧包全部保留（共 21 个 zip，不覆盖历史发布）**。上一版
  `releases/ShineVideoStudio-20261105-5d9ebee-win64.zip`（2,711,083 字节，sha256
  `98182bcaa8b0433d19e241986b9cc6cdd634fa79793a18aa4e4eba8fe60ddbda`，来源 `5d9ebee`，
  当时复核 552 个文件）**不含本轮（导演模式可用性轮）的界面与命名改动**，
  只作历史对照；再上一版 `ShineVideoStudio-20261105-1daa65b-win64.zip` 同样保留。
- `BUILD_INFO.json` 的 `known_blockers` / `closed_in_this_build` 已按本轮事实同步（12 条 / 47 条）：
  新增四条已闭合项（导演页保持挂载只切 `hidden`、候选密排网格且未出图条目占位、
  `GET /api/v1/jobs/{job_id}/media` 与 `/thumbnail` 两条真实路由使镜头可播放、
  素材文件名按剧本场次编号）；阻塞项中 VLM 重校准改为真实原因
  「正式项目库不在本机、qwen3-vl 未运行，且本构建未动任何钉住文件」，
  秒级计时器一条去掉「本轮的渲染窗口」这一过期限定。

## 13. 导演模式可用性轮（2026-11-06，用户反馈的 5 个问题）

用户反馈的是「导演模式」这一层的工作台体验。本轮逐条定位、修复并在真实栈上用浏览器验证。

| # | 反馈 | 根因（实测） | 修复 | 证据 |
| --- | --- | --- | --- | --- |
| 1 | 素材与风格页切到别的页面再回来又变成「① 读剧本」 | 服务端返回 `stage="materials_ready"`，前端只映射 `select_materials`/`confirm_requirements`，其余落到 `read_script`；而且面板每次切页都卸载重挂 | 补全四个阶段的映射；用户自己展开的阶段记在本地 `openStage`，不被服务端的自动推进覆盖 | 浏览器实测：切到成片中心再回来，仍停在「③ 选择素材」，网格与工具条（已选用 0 项 · 候选 12/21）不变 |
| 2 | AI导演制作页切走再回来回归初始状态 | 页面块在切换时被卸载；阶段聚焦由 `autoStage` 推导，而 `autoStage` 一变就把用户的 `stageOverride` 清空 | 三个导演页面保持挂载、只切 `hidden`；`stageOverride` 只在换作品时清空 | 浏览器实测：来回切换后阶段与已展开的折叠都还在 |
| 3 | 成片导出页被大段文字占满；点预览没反应；成片中心回来没有预览片段 | 预览指向**并不存在**的路由 `GET /api/v1/jobs/{id}/media`（404）；分段卡片把整段 `story_beat` 铺在卡面上 | 新增 `GET /api/v1/jobs/{job_id}/media`（该任务自己的产物，带字节数与摘要校验）与 `GET /api/v1/jobs/{job_id}/thumbnail`（海报帧）；分段改成海报网格，长描述折进「镜头描述」，工程字段折进「技术信息」 | 实机：两个路由分别返回 `video/mp4` 2,411,406 字节与 `image/jpeg` 6,673 字节；浏览器点缩略图弹出播放器并真的播放（0:00 / 0:07） |
| 4 | 素材与风格页的报错与素材占满视觉范围 | 候选按**批次**分行：一行一个批次、里面只有一两张图，其余全是留白；批次 id / attempt / 状态码常驻；风格逐项对照常驻展开 | 全部候选汇入**一个密排网格**（图在下、说明在下）；批次信息折进「批次与技术信息」；风格对照折进可展开项；报错给中文说明并把错误码降为小字 | 浏览器实测：6 列网格，每格一张图一行说明 |
| 5 | 角色用途没有固定、段间衣服与场景不衔接 | 用户澄清后确认：**第一段（林素）与第三段（顾晚）本就是不同人物**，不构成衔接问题 | 核实而非改绑定：每个人物只有一份已批准并锁定的主素材，并在它出现的镜头里以 `use_as=subject` 绑定；三个镜头是剧本给出的三个不同场景，导演给的 `continuity.mode` 全是 `cut`（没有承接关系），因此不存在跨场服装衔接的合同 | 见 §14 的主素材清单（`01-林素` / `04-周野` / `10-顾晚`，各一份，三镜的人物参考图与服装一致） |

附带修掉两处会让整页崩掉的空值读取：风格面板在七项未填时 `style[field].trim()` 会抛
`TypeError`；基础配置面板在环境检测未返回 `gpu`/`ram`/`disk`/`port` 段时会读 `undefined.name`。
面板现在对缺失字段给出可读值，而不是把整棵树带崩。

### 13.1 一个只有实测才会暴露的 CSS 缺陷

上面第 1、2 条的修复第一次并没有生效：DOM 里 `<div id="assets" hidden="">` 确实带着属性，
页面上却照样渲染出素材面板。原因是样式表里只有**作用域规则**
`#produce[hidden]{display:none!important}`，没有通用的 `[hidden]` 规则——
而 `.director-page{display:flex}` 盖过了浏览器默认的 `[hidden]{display:none}`。
改成通用规则 `[hidden]{display:none!important}` 之后，隐藏才真正生效。
（这条同时解释了为什么前端单测全绿却仍有这个现象：jsdom 的默认样式表里 `hidden` 是有效的。）

全量套件已于 2026-11-05 23:55（UTC 15:55）复跑，结果 1698 项收集 / 1697 passed / 1 skipped / 0 failed、
EXIT=0；命令在**仓库根**执行，执行细节与两次被 CLI 安全删除钩子打断的记录见 §7。

## 14. 素材文件命名（新增要求）

要求：生图的文件名自动改成人名 / 物品名 / 场景名称，并在前面配属编号，以便匹配剧本与分镜。

- 规则写在 `backend/shine/materialprep/naming.py`：
  文件名 = `{编号}-{实体名}.png`，编号取该实体**在剧本里第一次出现的场次**
  （不是台账的字母序），没有对应条目的手工素材保留人自己的文件名。
- 出图（`materialprep/execution.py`）与晋升（`comfyops/assets.py`）两处都用这一条规则；
  目录里不再出现 `material-character-766096d4.png` 这种哈希名。
- 本项目已经晋升的 12 个主素材（规则之前生成）按同一规则重命名，并留下
  `MATERIAL_FILENAME_RENAMED` 审计事件。结果：

| 文件名 | 实体 | 类别 |
| --- | --- | --- |
| `01-林素.png` | 林素 | 人物（场一） |
| `02-黄铜怀表.png` | 黄铜怀表 | 道具（场一） |
| `03-旧船坞·夜.png` | 旧船坞·夜 | 场景（场一） |
| `04-周野.png` | 周野 | 人物（场二） |
| `05-旧船票.png` | 旧船票 | 道具（场二） |
| `06-暖黄灯罩.png` | 暖黄灯罩 | 道具（场二） |
| `07-正在修理的表.png` | 正在修理的表 | 道具（场二） |
| `08-齿轮与镊子.png` | 齿轮与镊子 | 道具（场二） |
| `09-钟表铺（白昼）.png` | 钟表铺（白昼） | 场景（场二） |
| `10-顾晚.png` | 顾晚 | 人物（场三） |
| `11-老式煤油灯.png` | 老式煤油灯 | 道具（场三） |
| `12-海边石阶 · 黄昏.png` | 海边石阶 · 黄昏 | 场景（场三） |

- 回归：`backend/tests/test_material_naming.py`（编号随剧本场次、名字可读、Windows 非法字符被替换）。
