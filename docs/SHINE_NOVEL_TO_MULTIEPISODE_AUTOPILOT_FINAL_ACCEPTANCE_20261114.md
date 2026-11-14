# Shine 小说→多集全自动成片最终收束建设与真实验收报告（2026-11-14）

> 分级口径（互不代签）：**ENGINEERING_PASS** / **LLM_STORYBOARD_PASS** /
> **RTX5080_MULTIEPISODE_PASS** / **FULL_AUTOPILOT_PASS**。
> 本报告只陈述有证据的结论;无证据的部分标 NOT_RUN / BLOCKED 并给复验步骤。
> 功能分支:`feature/novel-multiepisode-autopilot-final-pass-20261114`;
> 独立 worktree:`F:/ClaudeWorkSpace/projects/shine-multiepisode-autopilot-20261114`;
> 基线:`origin/main = 6fbf498`(PR#20)。RTX 4070 真机验收豁免(EXEMPT_HARDWARE_UNAVAILABLE)。

## 0. 结论摘要

| 维度 | 结论 | 依据 |
| --- | --- | --- |
| ENGINEERING_PASS | **通过** | 小说→两集交付全旅程 E2E(mock LLM+假渲染边界,真实 FFmpeg 装配);后端 8 分片全量(仅 3 个基线预存失败,已对干净 origin/main 复核);前端 52 文件/305 项;tsc/build/Ruff/Alembic 全绿 |
| LLM_STORYBOARD_PASS | **未签署(ep-01 整集 APPLIED,ep-02 阻塞)** | 真实 MiniMax:大纲 SUCCEEDED、分镜书+风格冻结、**ep-01 整集 APPLIED(17 镜,五工种全链+自动落镜+WP3 自动确认)**；ep-02 cinematographer length+UNKNOWN(输出预算截断,WP7 目标问题)待核对授权重发;WP1 标题段合并真实生效 |
| RTX5080_MULTIEPISODE_PASS | **NOT_RUN(待 LLM 拆镜完成后开拍)** | H3 运行时已 ACTIVE/services_verified(1281 节点,4 方案,43.97GB 权重核验);受真实 LLM 拆镜耗时阻塞,尚未进入真实 H3 拍摄 |
| FULL_AUTOPILOT_PASS | **未签署** | 需前述真实两集 H3 出片完成后方可签署 |

**最关键的一条**:工程闭环已证明——普通用户导入小说,完成创作确认(大纲/分集/风格)、素材授权、生产授权后,系统可自动拆镜落地、自动确认分集、自动装配导演包、由常驻 Worker 自动 Run/Series/Job/QC/装配并逐集交付。本轮在真实生产中暴露并修复了 2 个代码缺陷,且未引入任何新的回归失败。

## 1. 问题矩阵与根因(真实数据取证)

取证来源:`.local-acceptance/witch-main-20261111/shine.db`(只读)。

| # | 问题 | 根因位置 | 真实证据 | 改造 |
| --- | --- | --- | --- | --- |
| P0-1 | 纯标题/空段落成为五工种创作单元 | `story.py:57 source_sections` 按卷/章标题切段;`streamline.py queue_director_storyboard` 逐段建单元 | witch 段落「第一卷 邪月之歌」=12 字、「前言」=24 字,无正文 | WP1:创作单元形成层叙事有效性检查+确定性合并 |
| P0-2 | `finish_reason=length` 重试同预算再截断 | `openai_compatible.py:297`→LLM_FORMAT;`api_film.py` 重试同预算;`budget.py` structured goal=0.30×input | 任务陈述+前轮 | WP1/WP2:有界预算升级一次到已核实硬上限 |
| P0-3 | schema 拒绝不可诊断 | `film.py` 只记 diagnostic_category,schema_errors=null | witch director SCHEMA_VALIDATION_FAILED 无字段路径 | WP2:记录字段路径(不含 provider 文本)+定向纠错 |
| P0-4 | 每集需手动 confirm_episode_shots | `streamline.py:898`;journey ③ 需全部集 materialized | V2 旅程逐集确认 | WP3:单元落镜后自动确认完成集 |
| 真实-1 | 五工种 style 校验对非字符串值抛 TypeError | `film.py:450` 对 `prompt_style.model_dump().values()` 全部 `re.search` | 真实 MiniMax 返回含 int 值,scriptwriter AGENT_CALL_FAILED/TypeError | 仅对 str 值检查 CJK |
| 真实-2 | 后处理故障任务被重试门禁误判 UNKNOWN | `streamline.py:699` 仅认 external_outcome==RESPONSE_RECEIVED | FAILED 任务有 received_result_sha256 但 external_outcome=None→STORYBOARD_UNKNOWN_REQUIRES_RECONCILIATION | received_result_sha256 即视为已回答拒绝 |

**已就绪能力(V2 轮,本轮复用未重建)**:自动落镜、集隔离、幂等开拍、跨运行交付保留、规则包自动钉、素材派生/确认、导演包自动装配确认、统一 GPU Lease、Run/Series/Job 调度。

## 2. 模块收束与修改清单(最小改造,未新建第二套引擎)

| 文件 | 改动 |
| --- | --- |
| `materialprep/source_spans.py` | 新增 `narrative_body`/`is_narrative_fragment`/`merge_non_narrative_fragments`:标题/标记/空碎片确定性合并进相邻叙事单元,保持原文覆盖完整、不跨集边界 |
| `director/streamline.py` | `queue_director_storyboard` 单元形成接入合并(记录 `merged_section_ids`);新增 `_auto_confirm_completed_episode`(单元全落镜后自动确认本集,幂等、审计 local-user);重试门禁认 received_result_sha256 |
| `materialprep/story.py` | `_section_apply_evidence`/`section_storyboard_shot_ids` 识别 merged 段(返回 merged_into,不重复计镜);`verified_production_context` 允许合并段的连续范围(仅非叙事段可被吸收) |
| `director/journey.py` | ③ 逐集进度把 merged 段视为已满足(非 pending) |
| `agents/film.py` | schema 拒绝记录字段路径(无 provider 文本);style CJK 校验仅对 str；`escalate_output_budget` 透传 |
| `api_film.py` | 截断(finish_reason=length)一次有界预算升级;schema 拒绝定向纠错注记携带字段路径 |
| `literary/budget.py` | `plan_call_budget` 增 `output_floor_tokens`(向已核实硬上限升级,绝不超出) |
| `tests/test_director_overview.py` | 断言更新:ep-01 自动确认后 state 推进(保留「一集可用另一集阻塞、互不借用」强度) |
| `tests/test_novel_multiepisode_autopilot.py`(新) | 13 项:合并逻辑/预算升级/schema 诊断/标题段合并集成/自动确认/后处理故障重试 |
| `tests/test_novel_to_two_episodes_e2e.py`(新) | 小说→两集交付全旅程 E2E(mock LLM+假渲染边界,真实 FFmpeg) |

## 3. 工程验收(ENGINEERING_PASS)

### 3.1 全旅程 E2E(小说→两集交付)
`test_novel_to_two_episodes_e2e.py`:原创小说导入→确认大纲→确认分镜书+冻结风格→五工种拆镜(自动落镜+自动确认)→素材复用已批准主素材→一次开拍→常驻 Worker tick 驱动 Run/Series/Job/QC/装配→**两集 DELIVERED**,每集≥2 镜,每集真实 FFmpeg 装配 reel(>10s),每镜恰好渲染一次,无手工标 PASSED、无手工补交付。证据等级 OFFLINE_VERIFIED(渲染边界 REPLACED)。

### 3.2 后端全量(8 分片,按文件均分,CODEBUDDY_SAFE_DELETE_ENABLED=0)
224 个测试文件,8 分片。结果:**本轮改动零新增失败**。仅 3 个失败,且经干净 `origin/main`(6fbf498)独立 worktree 复核为**基线预存**:
- `test_deployment_recommendation.py::test_low_memory_blocks_and_reports_the_host_amount`
- `test_deployment_recommendation.py::test_a_32gb_class_machine_is_not_rejected_by_a_round_gib_floor`
- `test_install_plan_qualification.py::test_an_unqualified_host_is_refused_even_when_the_call_bypasses_the_ui`

(均为硬件/目录 RAM 门槛相关,与本轮改动模块无交集;git diff 确认未触及 comfyops/deployment、recommendation、install qualification。)
`test_v13_e2e` 首轮失败为本会话 `NO_PROXY` 含 `[::1]` 的 httpx 假失败,`env -u NO_PROXY` 复跑通过(AGENTS.md 已记录)。

**PR #22 CI**:PR 运行 `43577689848` **完整 success(23m22s,L1 backend quick + L2 backend integration + Frontend build 全绿)**。首轮 L1 失败仅因新测试 import 排序(Ruff I001),已修复(3af7643)。WP7 基础提交 683b86b 后 PR CI 重跑 queued。

### 3.3 前端 / 静态 / 迁移
- `tsc --noEmit`:0 错误。
- `vitest run --no-file-parallelism`:**52 文件 / 305 项通过**。
- `npm run build`:成功。
- `ruff check`(改动文件):All checks passed。
- Alembic:临时新库 `upgrade head`→`f180d0010001`,`check` 无 drift。

## 4. 真实 LLM 验收(LLM_STORYBOARD_PASS,进行中)

隔离环境:`.local-acceptance/autopilot-final-20261114`(独立数据目录,端口 8010/5183,不干扰正式栈)；
真实 MiniMax-M3.1-flash(已授权 secret,14 角色路由,远端文字同意已授)；原创测试小说《雨夜钟表铺》(4 章,跨集人物老周/灰雨衣女人,跨集线索怀表+铜钥匙)。

已验证:
- 大纲提取 **SUCCEEDED**(真实 MiniMax 逐场提取+归并),大纲确认。
- 分镜书确认(2 集)+风格冻结(style V1)。
- **WP1 真实生效**:拆镜 unit[0] 把 7 字标题段「雨夜钟表铺」合并进第一章单元(`merged_section_ids` 记录),五工种不再对纯标题出镜；4 单元/2 集。
- 五工种链**首单元完整成功**(真实 MiniMax):scriptwriter→director→cinematographer→continuity_supervisor→editor 全 SUCCEEDED,`apply_camera` 自动落镜,cursor 推进 1/4,ep-01 IN_PROGRESS。
- 真实生产暴露并修复 2 个缺陷(§1 真实-1/真实-2),修复后拆镜不再立即阻塞。
- **ep-01 整集 APPLIED**(2 单元,8+9=17 镜,真实 MiniMax 五工种链 + 自动落镜 + WP3 整集自动确认)；素材缺口为可选 notice(`H3_CHARACTER_REFERENCE_MISSING`/`H3_SPEAKER_REFERENCE_MISSING`,按 2026-11-08 素材可选裁定不硬阻滞)。
- **ep-02 BLOCKED**(unit_index 2,cinematographer):`LLM_FORMAT` + `provider_finish_reason=length` + `external_outcome=UNKNOWN`。根因:cinematographer `completion_cap=9007`,9 镜 CameraPlan + reasoning 超上限→流截断→无可用答案(`received_result_sha256=None`,phase 停 WAITING_PROVIDER)→判 UNKNOWN。**这是输出预算截断问题,正是 WP7 逐镜小调用要消除的**(§10)。

边界:真实 LLM 长响应耗时较长(升级预算+宿主代理)；ep-02 cinematographer 为 length+UNKNOWN,按门禁需核对+一次性授权重发(不盲重发),重发仍可能撞同一上限,需 WP7 逐镜拆解或提高 cinematographer 预算方能稳定通过。完整 4 单元拆镜未全绿,LLM_STORYBOARD_PASS 未签署。

## 5. RTX 5080 真实 H3 双集(RTX5080_MULTIEPISODE_PASS,NOT_RUN)

环境已就绪:H3 运行时 **ACTIVE / services_verified=true**(真实 ComfyUI 校验 1281 节点,4 个 Ref2VA 方案,43.97GB 权重逐件核验,INT8 ConvRot DiT 在位)；VLM/ASR 为 advisory NOT_READY(不阻断确定性生产)。
受真实 LLM 拆镜耗时阻塞,尚未进入真实 H3 拍摄；**无任何真实 H3 attempt/artifact/成片证据**,不声称真实出片。
复验步骤:拆镜完成后→素材 derive/commit(复用老周主素材)→`director/production/start`(image-only pdd8-6img 方案)→Worker 自动受管启动 ComfyUI 逐镜拍摄→逐集装配交付→ffprobe/全解码/SHA 取证。

## 6. 失败注入与恢复(第四轮)

工程层由定向回归覆盖:`test_film_unknown_outcomes`、`test_scene_dead_worker`、`test_story_unknown_resend`、`test_material_scene_safety`、`test_agent_retry_and_rate_limit`、`test_novel_multiepisode_autopilot`(后处理故障重试)等,均在全量内通过。
真实层本轮实测:LLM_FORMAT 有界重试(superseded 旧行)、UNKNOWN 核对门禁(STORYBOARD_UNKNOWN_REQUIRES_RECONCILIATION 正确生效)、后处理故障重试授权。**天然真实失败样本**:ep-02 cinematographer `completion_cap=9007` 不足→`finish_reason=length` 流截断→无可用答案→`external_outcome=UNKNOWN`,门禁正确拦下不盲重发(需核对+一次性授权)。此样本印证 WP7 输出预算根因(§10)。真实 Worker 崩溃/H3 中断的注入待真实拍摄阶段执行。

## 7. 安全门禁保留(未放宽)

用户文字远端发送同意、素材批准锁定、UNKNOWN 不盲重发(核对+一次性授权)、风格冻结、生产资源互斥、真实视频技术 QC、项目数据完整性、既有作品交付历史——全部保留。本轮未改 `worker.py`/`preproduction.py`,不触发 `client_review_identity` 重校准义务；VLM 正负校准仍未做,不声称已校准。

## 8. 未通过与豁免清单

| 项 | 状态 | 说明 |
| --- | --- | --- |
| 3 个后端基线预存失败 | 预存(非本轮) | 硬件/RAM 门槛相关,干净 origin/main 复核一致 |
| RTX5080_MULTIEPISODE_PASS | NOT_RUN | 待真实 LLM 拆镜完成后开拍 |
| FULL_AUTOPILOT_PASS | 未签署 | 需真实两集 H3 出片 |
| RTX 4070 真机 | 豁免 | EXEMPT_HARDWARE_UNAVAILABLE |
| VLM 正负校准 | 未做 | 本轮未改 worker/preproduction,不触发义务;不声称已校准 |
| 人工艺术审片 | 待审 | 真实出片后 acceptance_status=pending_review,不自动 ACCEPTED |

## 9. 事故与恢复记录(诚实披露)

本轮在建立基线对照 worktree 时,`git worktree remove --force` 沿 venv 目录联接误删了共享 venv 的部分 site-packages。已用 `uv sync --frozen --reinstall` 从 uv.lock 精确恢复全部依赖,核心导入与全量回归验证通过；正式栈(另一 agent,端口 8000)全程未受影响(其模块已载入内存,且使用独立 uv python)。验收栈随后正常重启。

## 10. WP7 拆镜时延根因与逐镜小调用基础(本轮新增)

**根因实测(验收库 `agent_task_records`/`llm_call_attempts`)**:每次拆镜输入仅 4–39KB(1.3–10.7K tokens),切输入到 0.3M 无效；时延由 **reasoning tokens** 主导:

| 角色 | 输入 tok | 可见答案 | reasoning tok | 单次耗时 |
| --- | --- | --- | --- | --- |
| scriptwriter | 4366 | ~2159 | 12882 | 106s |
| director | 6522 | ~1891 | 10476 | 84.5s |
| editor | 9359 | ~1010 | 1411 | **13.9s** |
| scene_prep | 5633 | ~2392 | 15353 | 135s |

同款模型 low 档 editor 秒级,scriptwriter/director 一次产出整份合同烧 10–13K 思考。MiniMax-M3.1-Flash 强制思考、`reasoning_effort=low` 已最低、无硬 token 上限,`max_completion_tokens` 含思考(设小→length 空答案)。差在**单次任务粒度**,即编程 agent「小步聚焦调用」经验。ep-02 cinematographer 的 length+UNKNOWN(§4)正是此问题的真实失败样本。

**已交付并独立验证的基础**(commit `683b86b`):
- `shine/agents/crew_decompose.py`:scriptwriter 一次全局字段调用 + N 次逐 beat 小调用,每次外部调用独立子任务落 durable intent + UNKNOWN 门禁(独立 role_id 隔离 resume/replay),server 拼装回原 ScriptBreakdown,kind/sha/lineage 不变。
- `tests/test_crew_decompose.py`:schema 感知 mock + 真实 DB 两项回归绿；Ruff 干净。

**主路集成留待下一专项(用户裁定)**:接入实测为 breaking change——scriptwriter 1→N 调用需迁移 ~30+ 耦合测试合同(test_film_crew 13 处 + test_crew_prompt_contract/test_director_streamline/test_novel_multiepisode_autopilot 等多文件);段落预切分把 beat 数量从模型创作决定改为 server 段落决定,需 two-pass(先定 beat 边界再逐 beat 填)保创作分段。为守全量回归绿,主路接入已回退,保留已验证 helper 为基础。剩余:测试合同迁移 + two-pass + director 逐镜 + 真实 A/B 实测。**时延降低尚未真正交付,不声称已优化。**
## 11. WP7 主链集成与复验进度（2026-11-14）

- 功能分支以 merge commit `70414de` 并入 `origin/main@4e96cf6`，无冲突。WP7 实现提交 `7116a84`，已推送 PR #22 功能分支。合并状态仍为 OPEN。
- 小说 production context、无固定剪辑表的提案使用小合同。编剧先定原文范围，再逐 beat 填写。导演先定分组、时长和 cut，再逐镜写视觉与参考。摄影指导逐镜填写 CameraShot。服务端保留原产物合同、验证和 H3 Prompt Compiler，固定剪辑表及导演连续性定向返修沿用既有合同。
- 子调用记录发送前 intent、ledger、结果 SHA 及可校验 checkpoint。UNKNOWN 级联到父任务，核对回执含子任务 ID。原子记录一次性重发授权后，同输入、同 prompt、同 provider 的已完成子任务仍可复用。单测验证第二次请求只询问未决子任务。
- 环境为隔离 8010/5183。现有项目的最高版本为已确认的两集方案 v6，前 5 版均 SUPERSEDED。已对原摄影指导 UNKNOWN 核对并授权重发，旧记录仍为 UNKNOWN。第 3 单元新摄影指导任务 `b5146ec0-d30f-4b4b-8651-9b7e60a920fa` 完成 7 个真实调用，合计 160.936 秒 / 14,178 reasoning tokens，整单 CameraPlan 通过并落 7 镜。原来整单因截断失败，该结果说明本次能够完成，不证明速度提升。
- 编剧同输入 A/B 的整单臂已完成，6 个 beat，438.42 秒 / 10,512 reasoning tokens。这次存在远端间歇性延迟；单次耗时不可代表典型性能。另有历史同单元整单样本约 106 秒 / 12,882 reasoning tokens。
- 根据本轮用户授权，真实人物素材生成已入队。复用「老周」主素材，生成「女人」候选。验证小样 `042e2d2e-8ef4-471d-bed8-dade119f04a5`，正式候选批次 `9819ce44-f6ae-4e30-8818-a08d6cf3e97a`。此记录写入时常驻 Worker 仍在处理第 4 单元，出图及 H3 双集验收尚未签署。
- `npx.cmd vitest run --no-file-parallelism` 57 文件 / 337 项通过。`npm.cmd run build`、`ruff check backend`、隔离新库 `alembic upgrade head` + `alembic check` 通过（head `f180d0010001`，无 drift）。后端 8 分片基线日志位于 `.local-data/wp7-baseline/`，退出码依次为 0 / 0 / 0 / 1 / 0 / 0 / 0 / 0，剩两例已知的 `test_deployment_recommendation.py` RAM 基线失败，原第三例已由 main 上 PR #23 修复。首轮 WP7 全量定位出三处 `test_story_continuity.py` mock 合同迁移遗漏，迁移后该模块与 film crew、prompt contract、decomposition、offline E2E 的定向 60 项通过。完整复跑日志位于 `.local-data/wp7-final/`。
- RTX 4070 为 EXEMPT_HARDWARE_UNAVAILABLE，VLM 正负校准未做，本轮没有更改 `worker.py` / `preproduction.py`。

- 本轮用户将时延验收收窄到文学拆镜。A/B 计时覆盖结构与逐项生成，记录墙钟、reasoning tokens、调用次数和合同校验结果。素材队列继续保留在隔离栈，H3 拍摄不作为本轮计时或验收动作。PR #22 仍按原合并门禁保持开放。
- 全量定位还发现 `test_story_section_run.py` 的三处旧 mock 合同，迁移后 3 项通过。后端 RAM 用例补上实测 `installed_mb`，保留低 RAM 预期，另测低于门槛且无实测容量的 `RAM_UNVERIFIED`。这两处只改测试，部署门槛保持现行语义。最终源码 `594541b` 的完整分片日志位于 `.local-data/wp7-head-594541b/`。

### 11.1 同输入编剧实测

同一已确认小说单元、同一 MiniMax-M3.1-flash 路由，在两个 SQLite 快照中串行比较。整单 1 次调用，438.42 秒，10,512 reasoning tokens，6 个 beat 通过合同校验。逐 beat 到第一条填充返回时，结构 377.981 秒 / 8,013 reasoning tokens，首 beat 364.865 秒 / 3,142 reasoning tokens，已完成调用累计 **742.846 秒 / 11,155 reasoning tokens**，至少比整单耗时多 **69.4%**，仍有后续 beat。复查时，结构与前三条 beat 已累计 **1,424.760 秒 / 15,782 reasoning tokens**，基准耗时的 **3.25 倍**。远端等待计入真实墙钟。该样本已证伪本次串行细分能降低总时延，不能把单次输出更小或从截断恢复写作提速通过。导演同输入 A/B 尚未完成。

- 第 4 单元真实编剧逐 beat 通过原合同，11 次调用、1,275.354 秒、37,842 reasoning tokens。该单元无匹配整单基线。后续导演结构调用 `3442cd42-9ee6-4fc4-a95b-b6fddf2171c5` 断连，父任务 `2c7b1244-e956-412c-bc4e-ed11d14c4a87` 继承 UNKNOWN，API 核对回执返回同一子任务 ID。此次未授权重发。ep-01 为 APPLIED（17 镜），ep-02 为 BLOCKED（已落 7 镜）。
- 最终源码的 8 个后端分片全部退出 0，零 `FAILED` / `ERROR` 摘要，共选中 2,721 项（含按现有条件跳过的用例）。按文件排序后 `split -n r/8`，各分片执行 `python -m pytest -q -p no:cacheprovider <文件列表>`，启用 `CODEBUDDY_SAFE_DELETE_ENABLED=0` 并从子进程环境去掉 `NO_PROXY`。后端、前端和静态检查提供离线工程证据。
- 人物验证小样与项目「女人」候选均为真实 Qwen 出图，两批 COMPLETED，`renderer_owned=true`、`is_mock=false`，文件 SHA 与台账一致，技术 QC 和全解码通过。项目候选 `0f2981af-aa07-4f33-9ca2-cc54ddb5db83` 的 SHA-256 为 `765bf0905fa0fad7488f98e647484c2ae9e63b380832e9995f77954c2996634d`。目检着装为绿外套，原设定为灰雨衣，保留 READY_FOR_REVIEW。受管出图实例经自身停止接口确认零活动进程，未开始 H3。

## 12. 恢复整单拆镜（2026-11-14，用户裁定）

编剧、导演和摄影指导恢复每工种每次尝试生成一份完整合同。删除串行逐 beat／逐镜实现和对应 mock 合同，保留原有有界预算升级、schema 诊断、UNKNOWN 核对门禁及服务端合同校验。第 11 节记录的是撤回前的实测。历史子任务的 checkpoint 仍不随查询接口返回。
