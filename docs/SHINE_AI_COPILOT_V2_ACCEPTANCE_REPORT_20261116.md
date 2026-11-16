# Shine AI 管家 V2.0 验收报告（V201—V240）

- 日期：2026-11-16
- 分支：`feat/shine-ai-copilot-v2-full-capabilities`
- 基线：V1 @ `82df4b5`（PR #55，仍 OPEN）；`origin/main` @ `a2ea6af`
- 结论：**V2 的四项能力（版本化项目百科、能力注册与自然语言代操作、智能诊断/受控调参、工程 MD 移交）已完成代码与自动化验收；真实 Windows/ComfyUI/GPU/H3 与启动器 S29 仍为 NOT_RUN/BLOCKED，未宣称通过。**

状态口径：PASS = 有本轮自动化证据；PARTIAL = 已实现且合同有测试，但缺真机/端到端证据；NOT_RUN = 本环境不具备；BLOCKED = 客观受阻。

## 1. 测试执行

| 项目 | 结果 |
| --- | --- |
| `tests/test_support_agent.py`（V1） | 55 项全部通过 |
| `tests/test_support_copilot_v2.py`（V2） | 24 项全部通过（覆盖 V201—V240 合同） |
| 后端八分片全量 | 见 §1.1，8/8 退出码 0 |
| 前端 Vitest | 79 个文件 / **407 项全部通过**（含 `shineCopilot.test.tsx` 11 项） |
| TypeScript / Vite | `tsc -b`、`npm run build` 通过（仅既有 chunk>500kB 提示） |
| Ruff | `shine`、`tests`、`scripts` 全部通过 |
| 迁移 | V2 无新迁移；`f180f0010004` 空库/旧库测试通过 |
| 浏览器 UI | 本轮未重跑真实浏览器截图；新增计划/百科交互由真实 DOM 的 Vitest 覆盖；**真实浏览器场景本轮 NOT_RUN** |

### 1.1 后端八分片

- 命令：按文件均分 8 片，`CODEBUDDY_SAFE_DELETE_ENABLED=0`，`pytest -q -p no:cacheprovider`。
- 结果：**8/8 退出码 0，日志无 `FAILED`/`ERROR`**。
- 规模：共 **2998** 项参与；分片 a=371 / b=369 / c=383 / d=497 / e=326 / f=355 / g=402 / h=295。
- 日志：运行环境 `/tmp/v2final3/chunk-*.log` 与 `/tmp/v2final3/shards-exit.txt`（最终代码复跑）。

## 2. V201—V240 逐项验收

| ID | 用例 | 状态 | 证据 |
| --- | --- | --- | --- |
| V201 | 回答当前构建真实功能与七阶段 | PASS | `test_v201_v202_v203_project_knowledge_is_version_bound`；`/support/knowledge/project?q=导演模式` 命中 `director-journey` |
| V202 | 构建 SHA 与知识索引一致 | PASS | 同上：临时清单 + `build_commit=abc1234` → `FRESH` |
| V203 | 版本不一致时明确过期 | PASS | 同上：`build_commit=def5678` → `STALE` + 「不一致」提示 |
| V204 | 官方 H3 与本机支持边界准确 | PASS | `test_v204_official_h3_is_separated_from_this_build`；Ref2VA=true，FL2VA/2K=false |
| V205 | ComfyUI 官方 API/工作流文档可溯源 | PASS | `test_v205_v206_comfy_official_references_are_licence_safe`；摘要带 `docs.comfy.org` 与 `REFERENCE_ONLY` |
| V206 | 无许可时不全量打包官方资料 | PASS | 同上；`reuse=SUMMARY_ONLY`，许可政策「不整库复制」 |
| V207 | 离线百科正常 | PASS | `test_v207_v208_offline_knowledge_answers_unknown_without_inventing`；模块无网络、无 GPU |
| V208 | 无来源时回答 UNKNOWN | PASS | 同上；`answered=false` + `unknown_reason` |
| V209 | 不泄露私有素材与密钥 | PASS | V1 隐私/报告测试 + 报告脱敏；知识条目只含仓库路径与公开 API |
| V210 | 注入不能使知识条目获得执行权 | PASS | `test_v209_v210_injection_never_becomes_a_capability`：`policy_refusal`、plan/knowledge 为空、无动作 |
| V211 | action catalog 无未说明的直通 HTTP | PASS | `test_v211_catalog_has_no_generic_http_or_source_write_action` |
| V212 | 每个核心 UI 操作对应唯一执行者或 NOT_SUPPORTED | PASS | `test_v212...`；41 项矩阵，14 GUARDED / 11 READ_ONLY / 11 HUMAN / 5 NOT_SUPPORTED；`docs/...OPERATION_MATRIX...md` |
| V213 | 只读查询无需确认 | PASS | `test_v213_reads_need_no_confirmation_but_writes_do`；写预览无 Operator 头 → 403 |
| V214 | 轻量变更 diff 正确 | PARTIAL | `test_v233...` 语言 diff；工作流映射 diff 有合同测试，但未覆盖所有 UI 设置项 |
| V215 | 高成本 GPU 生成必须明确授权 | PASS | `production_start` 为 `EXECUTE_GUARDED`；预览显示 GPU 影响；`test_production_start_binds_the_original_run...` |
| V216 | 超出授权范围停止 | PASS | `test_v216_out_of_scope_episode_is_rejected`（非本方案集 → 400，无预览） |
| V217 | 原业务门禁不被绕过 | PASS | `test_storyboard_executor_gate_is_not_bypassed`：缺冻结风格 → 原执行器 409 → 动作 `REFUSED`，保留 `STORYBOARD_STYLE_NOT_FROZEN` |
| V218 | 原始任务 ID 与结果回读 | PASS | 同一测试：`original_task_id=run-fixed-1`，`authority=GET /api/v1/production-runs/{run_id}`；V1 安装任务绑定仍通过 |
| V219 | 页面刷新不重复提交 | PASS | `test_v219_v220_repeat_and_parallel_approval_execute_once` + V1 幂等测试 |
| V220 | 并发授权不重复 | PARTIAL | 原子 `UPDATE ... WHERE status='PREVIEWED'` + 双击测试；真实多线程竞争未在本轮压测 |
| V221 | UNKNOWN 不自动重发 | PASS | `test_v221_v229_busy_or_unknown_state_blocks_production`：`UNRESOLVED_OUTCOME` → 预览 blocked，授权 423 `REFUSED` |
| V222 | 跨项目委托拒绝 | PASS | `test_v222_cross_project_document_import_is_refused` |
| V223 | 未冻结风格不得代确认 | PASS | `test_v223_v224_human_confirmations_stay_human`；计划为 HUMAN_REQUIRED，无动作 |
| V224 | 主资产不可代确认 | PASS | 同上；能力目录列 `确认主资产/候选图` 为 HUMAN_REQUIRED |
| V225 | 导航不依赖 DOM 点击 | PASS | `test_v225_navigation_is_a_semantic_bridge_not_dom_clicks`；工具注册表无 DOM/click；前端只调 API |
| V226 | 不支持动作报告为 NOT_SUPPORTED | PASS | `test_v212...`；未接入清单附原因 |
| V227 | 不得代理源码/节点/模板 | PASS | `test_v211...`；`forbidden.write_source/write_node_source` |
| V228 | 付费调用显示用量预算且超限停止 | PARTIAL | 能力目录标注 `paid`；复用既有 LLM 任务预算与 completion cap；未新增货币额度系统 |
| V229 | 原执行器繁忙时安全暂停 | PASS | `test_v221_v229...`；生产/租约忙 → 预览 blocked + 授权拒绝 |
| V230 | 取消委托不误取消外部任务 | PARTIAL | V2 没有 cancel 能力（矩阵 NOT_SUPPORTED）；因此 AI 无法取消外部任务，但「取消委托」链路未实现 |
| V231 | 错误任务自动带入原始 job/attempt/error_code | PASS | `test_v231_v232_v238...`；V1 失败卡片绑定测试 |
| V232 | 按需多层证据，无扫盘/GPU 竞争 | PASS | 工具轨迹 ≤8；知识/官方摘要离线静态；诊断不启服务、不全量哈希 |
| V233 | 参数变更只允许 schema 合法值 | PASS | `test_v233_v234...`；非法语言 422；策略由 `GenerationStrategy` 校验 |
| V234 | 不得修改已冻结影视风格 | PASS | 同测试：冻结后改语言，`frozen_style_version_id` 不变 |
| V235 | 工作流副本与原模板独立 | PASS | `test_v235_workflow_parameter_copy_is_independent`；源 `parameter_map` 不变；V1 副本/回滚测试 |
| V236 | 预览后配置变化旧授权失效 | PASS | `test_v236_preview_is_stale_when_the_target_facts_change`；409 `PREVIEW_STALE`，可重新预览成功 |
| V237 | 失败后能重新预览或原流程 reconcile | PASS | `test_v237_v240...`：大纲角色未配置 → REFUSED `TEXT_ROLE_NOT_READY`，随后新预览可用 |
| V238 | 已确认与猜测根因分离 | PASS | V1 报告「已确认事实 / 疑似原因」结构；`test_v231...` 检查置信度与假设 |
| V239 | 报告目录固定且脱敏，含原任务证据 | PASS | `test_v239_report_is_controlled_and_redacted`；`support-reports/`、构建提交、开发验收建议 |
| V240 | 全链路用户故事真实运行一次 | PARTIAL | 建项目 → 导入原文 → 大纲（角色缺失被拒并可恢复）已跑通；**真实 GPU/H3 出片 NOT_RUN** |

## 3. V1 S01—S32 回归

V2 未改变 V1 的裁决、执行器与安全边界；`test_support_agent.py` 55 项全部通过，受影响迁移/安装/释放/对账/工作流测试在八分片全量中通过。S01—S32 结论沿用 V1 验收报告（`docs/SHINE_AI_COPILOT_V1_ACCEPTANCE_REPORT_20261116.md`）：

- PASS：S01–S06、S09、S11–S21、S22–S25、S27、S30、S31
- PARTIAL：S07、S08、S10、S26、S28、S32（缺真机/量化基准）
- BLOCKED：S29（启动器离线诊断未实现）

V2 新增知识/计划/调参不改变以上状态；S29 仍不重构启动器。

## 4. 安全与权限验收

| 项目 | 结果 |
| --- | --- |
| 运行时无 shell/git/pip/源码写入/任意 HTTP | PASS（注册表 + 能力目录测试） |
| 外部文档只作参考、不作为指令 | PASS（`REFERENCE_ONLY`、注入测试） |
| 报告写入受控目录、脱敏、去重 | PASS |
| 未绕过风格/资产/生产/UNKNOWN 门禁 | PASS（`STORYBOARD_STYLE_NOT_FROZEN`、`UNRESOLVED_OUTCOME` 原样保留） |
| 用户级参数不触碰冻结风格 | PASS |
| 跨项目作用域 | PASS |

## 5. 真实硬件与浏览器边界

| 项目 | 状态 |
| --- | --- |
| Windows 真机部署/安装包 | NOT_RUN |
| 真实 ComfyUI / GPU 显存租约 | NOT_RUN（合同与拒绝路径有测试） |
| 真实 MiniMax-H3 出片 | NOT_RUN |
| 真实浏览器 UI 截图 | NOT_RUN（本轮以真实 DOM 组件测试覆盖） |
| S29 启动器离线诊断 | BLOCKED |

## 6. 结论

V2 已把「项目百科 + 能力注册 + 自然语言计划 + 受控调参 + 工程移交」接入既有事实、裁决与执行链；新增动作全部可预览、可授权、可回读，失败可重新预览，UNKNOWN 与人工确认不被绕过。真实硬件与浏览器实机验收仍未执行，因此**未宣称全量真机通过**。代码与自动化证据达到可安排最终远端验收的状态；PR 保持 OPEN，不自行合并。
