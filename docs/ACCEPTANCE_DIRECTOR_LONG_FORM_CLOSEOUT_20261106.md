# 全片持续生产收尾修复：验收报告（2026-11-06）

- 分支：`codex/longform-closeout-20261106`（自 `main / 8a4e72b` 切出；本轮全部改动在该分支工作树，未提交）。
- 依据：[收尾修复提示词 V2](LOCAL_AGENT_DIRECTOR_LONG_FORM_CLOSEOUT_FIX_PROMPT_V2_20261106.md)、
  [第二次独立验收报告](ACCEPTANCE_DIRECTOR_LONG_FORM_RECHECK_20261106.md)（18 条反例）、
  [建设方案](DIRECTOR_WORKFLOW_AND_LONG_FORM_PRODUCTION_PLAN_20261105.md)。
- 结论：**独立 18 条反例全部修复并迁入正式回归套件；G01–G04 主需求落地；T01–T04 落地或查明边界。
  离线全量全绿；真实模型/H3/长时验收按授权边界如实记为未运行。**
- 本轮未修改正式数据库、未触碰 8133/5193 原栈、未启动真实 H3。隔离验证使用独立数据目录与新端口
  （API 8141 / Web 5201 / 新库 `f180b0010001`）。

## 1. 修复总览：F01–F08

| 编号 | 修复内容 | 等级 |
| --- | --- | --- |
| F01 | 交付归属与完整范围：新增 `run_owned_passed_jobs`（episode→batch→series→item→job 是唯一产物来源，全局最新通过 job 永不入选）；`_ensure_episode_delivery` 要求**计划内全部批次**已创建且 PASSED；EDL/摘要只解析本 Run 产物 | IMPLEMENTED + OFFLINE_VERIFIED |
| F02 | 暂停/预算传播：`_pause_child_series`（QUEUED→PAUSED、RUNNING→PAUSE_REQUESTED，记录 `control_reason`）；预算/存储暂停走同一传播；resume 只恢复本 Run 暂停的子 series；`claim_series` 拒绝领取属于非活跃 Run 的 series | IMPLEMENTED + OFFLINE_VERIFIED |
| F03 | CAS fencing：预算/存储暂停改为条件 UPDATE（状态+版本双条件），陈旧对象复活 CANCELLED 被拒绝并回滚；`advance_run` 领取后 refresh 版本 | IMPLEMENTED + OFFLINE_VERIFIED |
| F04 | 冻结合同：`package_readiness` 校验根 manifest/scope/policy 三个哈希（`PACKAGE_MANIFEST_TAMPERED` 等）；每次批次派发前重跑 readiness，不可启动→具名阻塞 | IMPLEMENTED + OFFLINE_VERIFIED |
| F05 | 政策合同：`PackagePolicyContract`（strict 类型、范围、extra=forbid，API 边界 422）；政策经 series 落到每个 job（`max_semantic_retries`/`auto_repair` 覆盖项目默认） | IMPLEMENTED + OFFLINE_VERIFIED |
| F06 | NEEDS_REVIEW 汇总与恢复：series NEEDS_REVIEW→batch BLOCKED→episode/Run 阻塞；`_unblock_resolved_episodes` + reconcile/resume 的公开恢复路径；误标 COMPLETED 的 Run 由对账纠正为 NEEDS_REVIEW（保留事件与审计） | IMPLEMENTED + OFFLINE_VERIFIED |
| F07 | 取消两段式：有在途外部工作→`CANCEL_REQUESTED`（series 转 CANCEL_REQUESTED，worker 收完当前镜头后收尾）；beat 观察到外部工作落定才最终 CANCELLED；死 worker 的 CANCEL_REQUESTED 由对账收尾 | IMPLEMENTED + OFFLINE_VERIFIED |
| F08 | 心跳与进展分离：`last_progress_at` 只在真实动作/状态变化时前移；心跳另记 `heartbeat_at` | IMPLEMENTED + OFFLINE_VERIFIED |

**独立反例**：`.local-acceptance/longform-recheck-20261106/test_recheck.py`（SHA-256 `ca245176…`）在修复后
**18/18 通过**；同套断言迁入 `backend/tests/test_director_long_form_regressions.py`（18 条业务断言逐字保留，
脚本比对 0 缺失），另加暂停/恢复、取消收尾、误完成纠正、T01/T02 共 24 项回归。

## 2. 主需求：G01–G04

| 编号 | 交付 | 等级 |
| --- | --- | --- |
| G01 双入口 | `shine/director/creative.py` + 路由：想法→版本化 `CreativeBrief`（AgentArtifact，人工创作留痕）→确认→`creative_script_run` 持久任务（scriptwriter 角色，远程文本需一次性同意并钉 provider 指纹；UNKNOWN→NEEDS_REVIEW 不重放）→生成普通 `ProjectDocument` 进入既有大纲链路。前端 `ScriptEntryPanel` 挂在①页：入口 A 表单（想法+锁定事实，通栏输入框）+ 入口 B 上传（原文+哈希入库）；`journey` 第 1 阶段如实显示简报/大纲状态 | IMPLEMENTED + OFFLINE_VERIFIED + 真实 Web（见 §4） |
| G02 先分集 | `EpisodePlanPayload.mode="sections"`：按已确认大纲的场次分集（`derive_episode_plan_from_sections`，场不跨集、覆盖恰一次）；`materialize` 把每集落到该场次**已应用**分镜的镜头（读 apply 回执）；导演包按物化结果冻结并记录 `section_ids` 血缘；按镜头分组保留为兼容入口 | IMPLEMENTED + OFFLINE_VERIFIED + 真实 Web |
| G03 机器推荐 | `DeploymentRecommendationCard` 接入 `POST /setup/deployment-recommendations`（此前前端零调用）：真机检测→组合可满足/阻塞（具名原因+需求数值）→证据等级（本机实测/相近硬件/估算）→下载/复用量→用途选择（可跳过生图）→交给既有安装计划逐项确认 | IMPLEMENTED + REAL（本机真机探测，见 §4） |
| G04 后期合同 | `EpisodePostContract`（入出点+字幕版本；音频混音按名拒绝 `EDIT_AUDIO_NOT_SUPPORTED`）；入出点对照实测媒体校验；字幕钉 revision+sha、时码超出剪辑长度拒绝装配；合同进 `input_digest`→修订=新交付版本、旧版保留、**不重跑 H3**（计数断言）；运行中的装配不被打断，PENDING 旧版按 `POST_CONTRACT_REVISED` 取消 | IMPLEMENTED + OFFLINE_VERIFIED（真实 FFmpeg 装配） |

## 3. 综合风险：T01–T04

| 编号 | 处理 | 等级 |
| --- | --- | --- |
| T01 长装配领取 | 新增 `AssemblyHeartbeat`/`renew_assembly_claim`：装配期间每 60s 刷新 `updated_at`（不动 claim token），活跃装配跨 2400s 租期不再被第二 Worker 抢走；真正失联的装配可被收回，旧 owner 的迟到回写被 claim token 拒绝（既有 `_write_back` fencing）。回归覆盖“活着不被抢 / 静默被收回+迟到回写被拒” | IMPLEMENTED + OFFLINE_VERIFIED |
| T02 跨批连续性 | 首版边界已证：`_batch_plan` 只在 `continuity_mode=cut` 的镜头前切批，6 镜连续链+限制 2 时整链留同批（回归固化）。任意跨 series 媒体续接仍为**未实现的首版边界**，报告如实保留 | 边界已证明 / 跨 series 续接 NOT_IMPLEMENTED |
| T03 版本/入口 | `/health` 增加 `schema_revision/schema_head/schema_up_to_date`（读不到=unknown，绝不冒充最新）；前端 `SchemaNotice` 在库落后于程序时给出可读提示与升级动作。旧库副本升级见 §5 | IMPLEMENTED + OFFLINE_VERIFIED |
| T04 journey | loading/error/ready 三态区分，读不到进度显示原因+重试，绝不显示“全部已交付”；清掉“五步走完”旧文案与“先去制作页上传剧本”旧引导；建作品后的下一步提示改为剧本双入口 | IMPLEMENTED + OFFLINE_VERIFIED + 真实 Web |

## 4. 真实 Web 验收（隔离栈，浏览器逐项执行）

栈：新库（`f180b0010001`）+ API 8141 + Vite 5201，`SHINE_RENDER_PROVIDER=mock`；未启动 Worker/真实模型。
证据目录：`.local-acceptance/longform-closeout-20261106/`（api/vite 日志、迁移日志）。

| 步骤 | 结果 |
| --- | --- |
| 空库首启 | 六阶段首启清单（创意与剧本→…→后期成片）正确渲染 |
| 入口 A | 填想法+锁定事实→保存简报 v1→确认→「生成剧本」→如实拒绝：`CREATIVE_SCRIPT_ROLE_NOT_CONFIGURED`（提示去基础配置配置文本模型） |
| 入口 B | 页面上下文真实上传《雨夜钟表铺.txt》→201，原文+sha256 入库；入口 B 列表显示文件名与哈希前缀 |
| G02 门控 | 无已确认大纲时「生成分集草案」禁用，提示先在①确认剧本大纲（剧本先分集顺序成立） |
| G03 真机 | 检测本机（GeForce RTX 5080 / 15.9 GiB / 31.7 GiB / 321.4 GB）→ H3 与生图组合均“本机可满足”，证据如实标“估算（尚未实测）”，展示要求/下载量与“多卡显存不相加”说明 |
| 布局 | 用户反馈的“想法”输入框已改为整卡通栏（1183/1185px，5 行可拖拽），截图与量测确认 |

**未在 Web 上运行（如实记录）**：完整创作→分镜→开拍→逐集交付主链路。原因：隔离栈按授权不接真实文本模型与
H3（mock 文本无法通过剧本/大纲合同、mock 产物被分集交付如实拒绝、run 派发要求 comfyui provider）。
该链路属于真实模型/H3 验收层（§6）。

## 5. 回归与迁移证据

命令（仓库根，`backend\.venv\Scripts\python.exe`）与结果：

| 检查 | 结果 |
| --- | --- |
| 独立反例 `.local-acceptance/longform-recheck-20261106/test_recheck.py` | **18 passed**（修复前 18 failed） |
| `pytest backend/tests`（全量，JUnit 留档 `backend-full-final.xml`） | **1768 passed / 0 failed / 1 skipped（既有）/ EXIT=0** |
| `ruff check backend/shine backend/tests` | **All checks passed** |
| 新隔离库 `alembic upgrade head` + `check` | **f180b0010001 (head)**，无 drift |
| 旧 schema 副本（`f170a0030001` 起）升级 | 依次执行 `f180a0010001`、`f180b0010001`，成功 |
| **正式库副本**升级（`.local-acceptance/live-browser/shine.db` 只读快照→副本） | 48 表 2074 行**零行数变化**、`foreign_key_check` 0 违规、新列就位；原库未动 |
| 前端 `npm.cmd test -- --no-file-parallelism` | **21 文件 / 148 passed**（新增 `directorEntries.test.tsx` 10 项） |
| 前端 `npm.cmd run build`（含 TypeScript） | 通过（bundle>500kB 既有警告保留） |
| 迁移回归（test_alembic / test_global_workflow_name_migration / test_migrations_windows / test_v13_migrations） | 全部通过（head 字面量随新迁移更新为 `f180b0010001`） |

本轮首跑全量曾出现 5 项失败（3 个迁移测试钉住旧 head、1 个 `/health` 形状断言、及由此暴露的问题），
均已按合同更新并复绿；全过程未删除测试、未 xfail、未降低断言。

## 6. 分级结论

| 等级 | 范围 |
| --- | --- |
| IMPLEMENTED / OFFLINE_VERIFIED | F01–F08 全部；G01/G02/G04 全链（含真实 FFmpeg 装配）；G03 前端接入；T01/T03/T04；T02 首版边界证明 |
| REAL（本机，无模型调用） | G03 真机探测与推荐展示；Web 双入口/分集门控/上传/布局 |
| REAL_IMAGE / REAL_H3_VERIFIED | **NOT_RUN**——本轮未启动生图/H3；真实小样、三集多批次自动交付按授权窗口另行执行 |
| LONG_RUN_VERIFIED（≥8h / ≥24h） | **NOT_RUN**——被真实模型层阻塞，不以空等补数 |
| HUMAN_REVIEW | 剪辑/字幕合同的人工艺术验收未做（无真实成片）；G03 推荐组合在本机未实测，保持 estimated |
| BLOCKED/边界 | 跨 series 媒体续接（任意长度）为已声明的首版边界；正式栈重启/迁移需用户在维护窗口执行（副本已验证） |

## 7. 当前运行与遗留

- 仍在运行（供查看）：隔离验收栈 API `127.0.0.1:8141`、Vite `127.0.0.1:5201`（数据目录
  `.local-acceptance/longform-closeout-20261106/web-data`）；原 8133/5193 栈未被触碰。无需时结束两个后台进程即可。
- Git：全部改动在 `codex/longform-closeout-20261106` 工作树，**未提交/未推送**（等待授权）；未构建 Windows 包。
- 下一步建议：①授权后提交并构建发布包；②维护窗口重启正式栈完成 `f180b0010001` 迁移（副本已验证无损）；
③授权窗口内执行真实小样与 ≥3 集多批次自动交付，再排 8h/24h 长时验收。
