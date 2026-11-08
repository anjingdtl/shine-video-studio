# 本机 Agent prompt · 四阶段二轮修复与复验

在 `F:\ClaudeWorkSpace\projects\shine-video-studio` 继续收尾。当前验收基线为 `main@631d9a07c6be5911a88e138bb037da3d369f3d79`。按 `AGENTS.md` 阅读项目基线及 [独立验收](FOUR_STAGE_REACCEPTANCE_631D9A0_20261108.md)、[上一轮证据](FOUR_STAGE_CLOSEOUT_EVIDENCE_20261108.md)。先核对 HEAD 与远端，保留未跟踪样本、用户库、已交付媒体和租约状态。使用隔离 worktree/feature branch 和临时 SQLite。以最小可审阅变更关闭 R01–R08，保留已经通过的 F01–F11 回归。

## 必修顺序与可验结果

1. **R01，单次新尝试授权**。在 `start_pipeline_new_attempt` 和路由原子消费旧 attempt 的授权，使用 status/owner/`state_version`/supersession fence 与幂等回执。已被替代的原任务保持 UNKNOWN 历史，UI 只显示当前可用操作。相同旧 ID/version 连续或并发两次，最多一个新任务和一次 mock transport 发送。正常 resume 仍拒绝 UNKNOWN，确证未发送才可走普通继续。对账与经明确确认的新尝试分别落审计，避免覆盖迟到响应。
2. **R06、R02，按预期单元覆盖并持久恢复**。用当前场次 revision、来源范围、分片 ordinal/hash 和连续性基线生成稳定 unit 清单，逐 unit 存结果、失败、剧组步骤、apply receipt。局部 `section_ids` 请求只更新该范围，保留独立场次、已应用 shots 和旧历史。先跑两场成功、仅重做第二场，再确认第一场回执及镜头保持且交接成功。故障注入一场两片、第二片 422，保留失败位置；任何丢片、重复、错序、越界、失效或未应用均使正式交付具名拒绝。正常应用以实际 CameraPlan/continuity/ShotSpec lineage 为依据。通过现有 Worker 或既有可恢复编排入口推进，给刷新、崩溃及重启提供回读点。
3. **R05，正式分集生产合同**。修复 `_handoff_episodes` 到 `EpisodePlanPayload` 的 schema 映射，在合适的生产 manifest 或类型化字段保留 EpisodeScript 身份。成功用例必须至少两个当前且已确认的 EpisodeScript，严格保留其分集、场次、镜头顺序。对确认后被替代的 SceneScript、旧 outline、缺分集、重复/跨集占用、非连续分集顺序分别做负例。旧 `volume_scope` 只按明确兼容合同保留；正式多集路径必须从用户确认的分集得到。修好 Pydantic 422 后仍要验证当前 revision 和确认资格。
4. **R03、R04，当前确认链与快照幂等**。在蓝图确认、读取、卷发布和包准入时比对生成时与当前的有序卷大纲 ID/hash 和计划 revision；同一 plan 下换大纲立即阻止旧蓝图确认。对草稿蓝图驱动的场次，真正执行依据等价检查，覆盖已确认的重要人物、世界、时间线、锁定事实、结局和跨卷开放项。快照应绑定当前计划/蓝图/场次/分集、按序 unit/apply、Shot revision/spec、绑定、冻结风格、规则包、工作流图及映射；仅同一有效快照可复用。镜头同 ID 升版时可定位到需重审或重交付，完成重审后生成新包；旧包阻止新 Run 和未提交批次，历史 PASS 可读。为已有四阶段包补兼容复核，按来源区分普通旧包。
5. **R07、R08，浏览器闭环**。总览/单卷 GET 返回权威草案、逐单元状态、待应用、失败/对账、发布回执和确切 package ID。真实 Chromium 验证刷新、切项目回包乱序、切卷和跳转到指定导演包。仅对账得到可重试结论才允许对该单元继续，失败显示实际结论。`reconcileAndRetry` 不得吞失败后重跑整卷。显示并接通已存在的分卷边界与分集合同接口，所有确认带所见版本，按钮与服务端准入一致。
6. **CI 与证据**。复核当前 Actions 及 `7081104` 的失败清单。Linux 同 14 项既有失败要逐项区分 Windows 专属断言、平台模拟遗漏、通用逻辑缺陷。平台特定测试明确归属，通用用例和生产门禁保留。检查 Windows job 的完整日志，修复真正回归；触及文件 Ruff format/check，避免无关全仓格式化。更正上轮账本中已经过时的分支与回滚描述，记录当前审查 commit。

## 复验和收工

- 为每个复现增加反向断言。先跑 `test_four_stage_closeout.py`、`test_pipeline.py`、`test_creative_output_budget.py`、`test_film_unknown_outcomes.py`、`test_material_text_consent.py`，随后跑 `AGENTS.md` 指定 Worker/H3 回归、全量后端、前端测试与构建、Alembic 空库和旧库副本升级、Linux/Windows CI。使用 Playwright + 隔离 FastAPI/SQLite 验证短篇、至少三卷及两集以上的合成旅程，含刷新恢复、局部重做、选卷二、确切包导航以及应用后交付。
- 软件门禁通过后，用已获同意的 Profile 和合成文本完成真实 MiniMax 文本链。获得实际本机运行与素材授权后，确认权威 DB、进程归属、当前队列、lease、工作流图与参数槽，使用默认多参考 Ref2VA 验证选定卷的多批次、逐集装配和播放。使用现有 H3PromptCompiler、ProductionRun/Series、五门 QC 与历史保留合同。已有 UNKNOWN、归属不清的 GPU lease、外部 ComfyUI 留待既有对账；需要变动审片 identity 时按项目规定处理校准及 revision 生命周期。
- 交付可审查提交与验收报告。每个 R 项附测试名、实际命令、退出码及关键服务端状态；分别记录 `OFFLINE/MOCK`、`REAL_LLM`、`REAL_H3`、`VOLUME_DELIVERED`、`HUMAN_ACCEPTED`。有未验或阻塞就写清实际边界与下次复跑入口。保留用户库和现有运行栈。
