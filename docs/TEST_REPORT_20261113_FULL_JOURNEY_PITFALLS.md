# 全旅程穿测踩坑测试报告（2026-11-13）

> 方法：可见浏览器、仅以普通用户身份点页面按钮/表单/确认弹窗推进；命令行只用于
> 启动/重启本机服务、只读查日志与台账、修代码、跑回归、校验文件。
> 作品：`766cc9fe-3759-4410-b896-a2fbb4201b4d`（放开那个女巫·两集穿测 1112）。
> 数据目录：`.local-acceptance/witch-main-20261111`。
> 本报告只陈述已验证事实；未做到的明确列为遗留，不夸大。

## 0. 结论摘要

- 本轮定位并修复 **6 类阻断缺陷**（③入口死循环、双台账不互通、孤儿 image 租约、
  镜像 worker 崩溃无恢复、crew 长连接被掐断、台词合同过严），全部带定向回归。
- ③ 镜头拆解：12 个场次单元中 **10 个已应用**（前言/第1卷/0001–0008），
  0009 仍被合同拒绝、0010 待应用 → **③ 尚未完成**。
- ④ 素材：v5 批次 35 候选就绪 + 1 丢失待核对；晋升/绑定未做。
- ⑤⑥⑦（导演包/排产/交付）**未运行**，**两集成片未产出**。
- VLM 正负校准**未做**（按 AGENTS.md 约定不得声称已校准）。

## 1. 缺陷与修复（症状 → 根因 → 修复 → 回归）

### D1 ③ 长剧本入口死循环（UI）
- 症状：③ 主按钮对 >20000 字剧本禁用，出口「去选择生产段落」指向 ④ 的段落勾选；
  分集归并后该勾选与「为所选生产段落生成正式分镜」被隐藏 → 死循环。真正闭环
  （四阶段逐场草案→应用→重做）埋在 ① 的「可选/高级」折叠里。
- 修复：抽出 `StoryboardDraftSection` + `DirectorStoryboardStage` 挂到 ③（导演/专业两模式），
  含逐场「重做这一场」、应用后「把分集落到镜头」(materialize)+「确认这一版分集」；
  `autoStage` 跟随 journey（③ 未完成停在 ③）；④ 主操作受 `storyboardReady` 门控并给「去③」入口。
- 文件：`frontend/src/components/StoryboardDraftSection.tsx`(新)、`FourStagePanel.tsx`、
  `App.tsx`、`StoryboardPanel.tsx`、`MaterialPreparationPanel.tsx`。
- 回归：`frontend/src/test/storyboardStageBridge.test.tsx`(3)、`storyboard.test.tsx`、全量 270。

### D2 双台账不互通（materialize/basis/continuity 只读 section-run）
- 症状：四阶段写 `four-stage-storyboard-units` 台账，而 materialize/basis/continuity 只读
  `story_section_run` → 即使全部应用，③ 仍报 `EPISODE_PLAN_NOT_MATERIALIZED`。
- 修复：`story.py::_section_apply_evidence` 双台账取新者；`basis.py::shot_script_evidence`
  与 `compute_section_continuity` 同步桥接；未应用场具名 `STORYBOARD_NOT_APPLIED`。
- 回归：`backend/tests/test_four_stage_section_bridge.py`(3)。

### D3 孤儿 image GPU 租约永久阻塞
- 症状：worker 死在取得租约后，image 启动记录无 `lease_id`，死渲染器证明匹配不到 →
  租约 `RECONCILE_REQUIRED` 永久挡住后续出图。
- 修复：`handoff_startup.py::release_dead_renderer_lease` 增加 image 台账回退
  （PID/birth、Job Object、端口三重死亡证明不变）。
- 回归：`backend/tests/test_image_worker_loss_recovery.py`(4，含活渲染器不释放反例)。

### D4 镜像 worker 崩溃无恢复
- 症状：worker 死在 /prompt 与收割之间，attempt `SUBMITTED/ACKNOWLEDGED`、item 永久 `RUNNING`。
- 修复：`execution.py::recover_lost_image_items` 记 `UNKNOWN`+`NEEDS_REVIEW`
  (`IMAGE_WORKER_LOST`，不重投)，由用户显式「再生成一版」；活 owner 不触碰。
- 回归：同 D3 文件。

### D5 crew 长连接被掐断（LLM_CONNECTION/UNKNOWN 反复）
- 症状：上万 token 非流式长连接思考期无字节，被代理/网关切断 → UNKNOWN → 场次阻塞。
- 根因链：① `streaming_enabled` 默认 off；② 仅 literary 通道读它；③ crew/agent 通道
  `agent_output_config` 根本没设 streaming。
- 修复：`transport_policy` 默认 on；`gateway.agent_output_config` 接入 streaming；
  流式 429/5xx 直接分类（不回退二次 POST）、400/422 才回退一次；流式路径 429 退避重试；
  分类前先 `response.read()`。
- 回归：`test_llm_streaming_default.py`(5)、`test_llm_setup_contract.py`、
  `test_agent_retry_and_rate_limit.py`、`test_llm_gateway.py`、`test_film_crew.py`。

### D6 台词合同过严（DIALOGUE_NOT_IN_USER_SCRIPT）
- 症状：ratio 未设置时要求逐字子串，标点/省略号变体即拒；拒绝无证据、重试不引用。
- 修复（按用户裁定放宽）：归一化（空白/省略号/破折号）回退比较；ratio 未设置走改编对白
  检查（说话方须在 beat 与原文、时码在窗内），ratio=0 仍严格；拒绝携带 `offending_line`，
  纠错说明引用该行。
- 回归：`test_dialogue_contract_repair.py`(6)、`test_storyboard.py`、`test_crew_prompt_contract.py`。

## 2. 回归汇总（均通过）

- 后端定向：bridge 3 + worker-loss 4 + streaming 5 + dialogue 6 = 18 新增。
- 后端批次：four_stage_reacceptance 19 / basis_grouping 4 / story_preparation 9 /
  material_derive 4 / long_form_regressions 32 = 68；managed_process_release 21 /
  material_image_jobs 32 / material_preparation 25 / asset_center 25 = 103；
  llm_setup_contract 36 / agent_retry 9 / llm_gateway 19 / film_crew 36 /
  storyboard 33 / crew_prompt_contract 17。
- 前端：全量 270 通过；`tsc --noEmit` 干净；`npm run build` 成功。
- `ruff check` 改动文件全过。

## 3. 遗留阻断（未完成，如实记录）

1. **0009 仍被拒**：最后一道为 `SCHEMA_VALIDATION_FAILED`/`CONTRACT_OR_POLICY_REJECTED`
   （模型 JSON 不合合同，`external_outcome=RESPONSE_RECEIVED`）。本轮已放宽台词但**未放宽
   schema**；按用户「json 规则降级」指示，下一步可对落镜 schema 做受控降级（保留 H3 官方硬约束）。
2. **0010 待应用**；③ 未确认 → ④ 晋升/绑定、⑤ 导演包、⑥ 排产、⑦ 交付均未运行。
3. **两集成片未产出**；不声称任何真实 H3 验收。
4. **VLM 正负校准未做**；改动 `worker.py`/`preproduction.py` 的既有约定仍要求重校准后方可真实生产验收。
5. 素材丢失项（`IMAGE_WORKER_LOST`）需用户在素材页显式「再生成一版」。

## 4. 边界声明

- 所有修复均经定向回归与（部分）浏览器实测；0009/0010 与后续阶段**未**走完。
- 未以 API 绕过界面推进业务；未手工拼中间产物、未改任务状态、未盲重投 UNKNOWN。
- 素材/视频/数据库/密钥均留在本机。