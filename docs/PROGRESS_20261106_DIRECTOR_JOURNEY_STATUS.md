# 进度：导演模式全旅程（2026-11-06 20:40）

一句话：**渲染链路已经真机跑通，导演旅程走到"第 1 场正式分镜 + 1 个真实 H3 镜头"；
离"≥20 段成片拼接"还差 26 张场景底图、20 场分镜、导演包和整部作品 Run。**

---

## 1. 三个原始目标的完成度

| 目标 | 完成度 | 真实证据 | 缺什么 |
| --- | --- | --- | --- |
| ① 真实 VLM 正负校准 → 发布新 revision → 弃用旧版 → 重分析重冻结计划 | **未做** | 查清了上一位 agent"80 分钟不返回"的根因：本机 `HTTP_PROXY` 被本地审片客户端继承（同一缺陷让 `/health` 释放证明返回 404）。已修客户端 | 校准本身没跑；本库没有可弃用的旧 revision |
| ② 跨 series 任意媒体续接 | **代码+回归完成，真实链路未跑** | `test_cross_series_continuity.py` 4 镜连续链、每镜一批、四个 series 全过（上一轮） | 需要一次真实多 series 生产才能证明 |
| ③ 真实多集无人值守 H3 生产 + ≥20 段拼接 | **约 15%** | 1 个真实 H3 镜头 PASSED（artifact `92b862e6…`，1024×576 / 24fps / 158 帧 / 6.583s / h264+AAC，`ffmpeg -f null -` 解码 0 错）；第 1 场分镜链 → 镜头 `38d4a1d7` 打通 | 26 张场景图、20 场分镜、materialize、导演包、Run、拼接 |

---

## 2. 这一轮实际做完的（可核对）

### 2.1 解掉 4 个真实缺陷（commit `942ee83`）

1. **中断子调用永久卡死大纲**：`reconcile` 原本是空操作。现在按证据三态判定
   （`RESULT_ADOPTED` / `NOT_SENT_PROVEN` / `UNCONFIRMABLE`），已决失败有上限重试（每场 2 次）。
2. **注销后仍占唯一请求身份** → 新请求 `IntegrityError`：注销时释放 `request_identity`，
   并做了幂等修补（本轮释放 1 条）。
3. **有疑问的大纲永远无法确认**（悬疑剧本死锁）：`confirm` 增加显式"接受保留悬念"的哈希确认，
   未提供仍 409；artifact payload/sha256 不改写，审计记录接受的问题。
4. **出片后 Worker 被打死**：审片释放失败的审计写入 `entity_id=None` 触发 NOT NULL，
   异常在 try 之外直接终止主循环（表现为"显卡空闲、后台卡住"）。已改为挂真实任务 id，
   且该写入失败不再结束这一拍。

外加：本地释放证明/推理不再走宿主代理；`resume` 清除上一次错误码。

### 2.2 真实旅程推进（项目 `3707a9ed…《夜航船 真实长片 20261106》`）

| 阶段 | 结果 |
| --- | --- |
| 剧本 | MiniMax 真实文本模型，20 场，document `2670e301…` |
| 大纲 | `22dd8d5e…` 已确认，21 个生产段落，7 条悬念按作者决定保留 |
| 分集 | v1 `ef996d7a…`，4 集，已确认（未 materialize） |
| 素材 | 主素材：老周 `cb93027d`、沈砚 `86d4c050`、货轮 `85ab1ed7`、甲板 `e670eb38`、码头 `b88e5ea9`、缆绳 `4c6f1004`（本机 Qwen-Image-2.1 真实出图后晋升） |
| 方案 | 4 个 Ref2VA 在真实渲染器校验通过（`missing_nodes=[]`） |
| 分镜 | 第 1 场 `343a8834` SUCCEEDED → 镜头 `38d4a1d7`（15s，绑定 老周=subject / 甲板=picture） |
| 出片 | 手工镜头 `96c6d395` 的 job `aa8baa09` PASSED（上面那份 artifact） |

### 2.3 工程衔接检查（本轮新增，不占显卡）

- 非 GPU 回归 **179 项**全过：故事/素材/审片 80、交付与导演入口 56、生产 series/离线 E2E/跨 series 43。
- `ruff` 全绿；前端 `tsc --noEmit` 与 `vite build` 通过。
- 用真实库验证"场次 → 已应用分镜 → 镜头"解析：S1 → `['38d4a1d7']`，S2/S3 → `SECTION_RUN_MISSING`，
  与 `materialize` 判定口径一致。

---

## 3. 剩下的工作（按顺序，已量化）

| 步 | 内容 | GPU | 预计 |
| --- | --- | --- | --- |
| 1 | 26 张场景底图（每场独立身份，逐场建单场素材计划） | 轻（约 7GB 峰值） | 15–20 分钟 |
| 2 | 20 场正式分镜链 + `apply-camera` | 不用 | 30–60 分钟 |
| 3 | `episode-plans/1/materialize` → 确认 v2 | 不用 | 2 分钟 |
| 4 | 导演包 build → readiness → confirm | 不用 | 2 分钟 |
| 5 | 整部作品 Run（21 镜 × 15s） | 重（约 15GB 峰值） | 1.5–2 小时 |
| 6 | 后期拼接 + 交付单核对（sha / ffprobe / EDL 顺序） | 不用 | 10 分钟 |

精确调用与逐条阻塞处理见 `docs/HANDOFF_20261106_DIRECTOR_JOURNEY_REMAINING.md`。

---

## 4. 已知会挡路的东西（都不许绕过）

**产品缺口（本轮未修）**
1. 同一人物跨场身份歧义：`老周` / `老周（周远山）` / `周远山（老周）` 是三个身份，全片级素材计划
   会 409 `MATERIAL_IDENTITY_AMBIGUOUS`，产品没有"合并同一人物"的用户动作 → 只能单场建计划。
2. 启动阶段失败的 H3 租约死锁：`release_dead_renderer_lease` 对有 attempt 的任务保守拒绝，
   `operator abandon` 又要求先解除租约 → 目前靠重启工作台触发启动对账。

**环境类（本轮实测过处理办法）**
3. 出图渲染器跑完受管实例残留 → H3 提交报 `COMFY_INSTALLATION_BINDING_CHANGED` →
   `POST /setup/managed-processes/release` 后再提交。
4. `LOCKED_FACTS_CHANGED`（模型没逐字回显中文锁定事实）→ resume 重跑，本轮第 2 次即过。
5. `MATERIAL_BRIEF_NEEDS_CLARIFICATION` → 把答案写进英文外观描述后 `regenerate`。
6. 沙箱内跑全量 pytest 会在后半段被删除守卫打断（50 次阈值），失败集中在要删文件的用例；
   只有在沙箱外跑才是有效结果。

---

## 5. 怎么判定"真的完成"（验收口径，不改）

- 每一镜：`artifact.sha256` 与台账一致、`ffprobe` 规格符合、`ffmpeg -f null -` 解码 0 错、
  `prompt id` 与 `prompt graph sha` 已落库。
- 成片：EDL 顺序与分集一致，交付单记录每段来源 `job/attempt/artifact`。
- 审片：`technical` 必须 `passed`；`semantic/style` 未配置只能是 `not_run`，`final` 只能是
  `needs_review`，`acceptance_status` 只能是 `pending_review`——**不许改写成 accepted**。
- 无人值守：Run 由 Worker 自己推进，series 独占租约、跨镜走 idle keeper 转移；脚本手推不算。

## 6. 不能说的话

- 不能说"20 段成片已完成/已验收"——目前只有 1 镜真实成片。
- 不能说"VLM 已重新校准"——本轮只修了导致校准卡死的缺陷。
- 不能拿服务 READY、mock 产物、历史校准当本轮证据。
