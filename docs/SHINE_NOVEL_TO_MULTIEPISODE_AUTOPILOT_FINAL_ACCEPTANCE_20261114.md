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
| LLM_STORYBOARD_PASS | **进行中(真实调用成功)** | 真实 MiniMax-M3.1-flash:大纲提取 SUCCEEDED、分镜书确认+风格冻结、五工种链 scriptwriter/director 已 SUCCEEDED、cinematographer 在途;真实数据验证 WP1 标题段合并 |
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

边界:真实 LLM 长响应耗时较长(升级预算+宿主代理),完整 4 单元拆镜仍在进行;UNKNOWN 安全门禁全程生效(不盲重发,核对+一次性授权重发)。

## 5. RTX 5080 真实 H3 双集(RTX5080_MULTIEPISODE_PASS,NOT_RUN)

环境已就绪:H3 运行时 **ACTIVE / services_verified=true**(真实 ComfyUI 校验 1281 节点,4 个 Ref2VA 方案,43.97GB 权重逐件核验,INT8 ConvRot DiT 在位)；VLM/ASR 为 advisory NOT_READY(不阻断确定性生产)。
受真实 LLM 拆镜耗时阻塞,尚未进入真实 H3 拍摄；**无任何真实 H3 attempt/artifact/成片证据**,不声称真实出片。
复验步骤:拆镜完成后→素材 derive/commit(复用老周主素材)→`director/production/start`(image-only pdd8-6img 方案)→Worker 自动受管启动 ComfyUI 逐镜拍摄→逐集装配交付→ffprobe/全解码/SHA 取证。

## 6. 失败注入与恢复(第四轮)

工程层由定向回归覆盖:`test_film_unknown_outcomes`、`test_scene_dead_worker`、`test_story_unknown_resend`、`test_material_scene_safety`、`test_agent_retry_and_rate_limit`、`test_novel_multiepisode_autopilot`(后处理故障重试)等,均在全量内通过。
真实层本轮实测:LLM_FORMAT 有界重试(superseded 旧行)、UNKNOWN 核对门禁(STORYBOARD_UNKNOWN_REQUIRES_RECONCILIATION 正确生效)、后处理故障重试授权。真实 Worker 崩溃/H3 中断的注入待真实拍摄阶段执行。

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