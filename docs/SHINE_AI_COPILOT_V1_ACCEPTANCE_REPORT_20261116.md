# Shine AI 管家（Shine Copilot）V1.0 验收报告

- 日期：2026-11-16（第二轮收束）
- 分支：`feat/shine-ai-copilot-support-agent-v1`
- 基线：`origin/main` @ `a2ea6af`；方案文档 @ `b8aac6c`
- 迁移头：`f180f0010003`
- 结论：**第二轮 P0/P1 修复完成，安全强制项与生产正确性强制项通过单测/集成/真实浏览器验证；Windows 真机、真实 ComfyUI/GPU、真实 H3、安装包实机与启动器离线诊断未执行或仍受阻，未宣称通过。**

状态定义：

- **PASS**：有本轮可复现的自动化或真实浏览器证据。
- **PARTIAL**：功能已实现并有代码/单测支撑，但缺少真机或端到端场景证据。
- **NOT_RUN**：本环境不具备条件，未执行。
- **BLOCKED**：能力未实现或存在客观阻断，如实保留。

## 1. 测试执行汇总

### 1.1 后端

| 项目 | 结果 |
| --- | --- |
| `tests/test_support_agent.py` | 39 项全部通过 |
| 后端八分片全量 | 8 个分片共 2958 项参与，**全部退出码 0**（本轮重新完整执行，未用定向复测替代） |
| 分片规模 | a=302 / b=379 / c=397 / d=469 / e=312 / f=357 / g=386 / h=356 |
| 迁移 | `test_alembic.py`（含空库升级、旧库升级、`f180f0010002 → f180f0010003` 授权行保留与回填）、`test_migrations_windows.py`、`test_global_workflow_name_migration.py` 全部通过 |
| 事件码一致性 | `test_audit_event_codes.py` 通过（新增 `SUPPORT_ACTION_TASK_BOUND` 已登记） |
| Ruff | `uv run ruff check shine tests` → All checks passed |
| 空库迁移 | `SHINE_DATA_DIR=/tmp/shine-e2e-r2 alembic upgrade head` 成功，`alembic_version=f180f0010003` |

分片日志保留在运行环境 `/tmp/copilot2-chunk-*.log` 与 `/tmp/copilot2-shards-exit.txt`；跳过项（平台/真机相关）按实际计数，不记为通过。

### 1.2 前端

| 项目 | 结果 |
| --- | --- |
| Vitest | 79 个测试文件 / 405 项全部通过（含 `shineCopilot.test.tsx` 9 项） |
| TypeScript | `tsc -b` 通过 |
| 生产构建 | `vite build` 通过（仅既有 chunk >500kB 提示） |

### 1.3 浏览器端到端（第二轮）

真实环境：`SHINE_DATA_DIR=/tmp/shine-e2e-r2` + `alembic upgrade head` + `uvicorn shine.main:app` + `npm run dev`；**无 LLM、无 ComfyUI、无 GPU**。结果 PASS：

1. 浮动「AI 管家」可打开，干净页面不出现伪造的绑定行。
2. 「为什么出图失败？」返回离线诊断，含 `/api/v1/setup/verdict`、`/api/v1/setup/runtime`、`/api/v1/setup/summary` 的真实依据与缺失文件数量。
3. 面板渲染「唯一裁决」「运行合同」「本页面事实」「本地知识库」等证据标签。
4. 控制台 error 为 0，无阻塞性错误。

失败卡片 → 管家的绑定路径由前端自动化测试覆盖（见 §3），本轮无真实失败任务可构造，未宣称真机截图证据。

## 2. S01—S32 逐项验收

| ID | 用例 | 状态 | 证据与说明 |
| --- | --- | --- | --- |
| S01 | 全局 UI 跨导演/素材/设置/成片 | PASS | 两处渲染路径挂载同一管家；会话键改为「项目 + 故障指纹」，切页/七阶段继承；未新增工程配置页 |
| S02 | 未配置 LLM | PASS | 离线诊断与浏览器实测；`llm.status=NOT_CONFIGURED` 时证据仍有效 |
| S03 | 多个 LLM Provider | PASS | `support_agent` 可选角色；复用统一 Gateway、ProviderProfile 与密钥托管；路由测试通过 |
| S04 | 浮窗上下文 | PASS | 仅发送页面 ID、项目、任务、尝试、错误码与具名原因；无整页 DOM 抓取 |
| S05 | verdict/准备摘要一致性 | PASS | 快照直接调用 `setup_verdict`；执行后复核同一权威能力裁决 |
| S06 | 反复打开/切换页面 | PASS | 上下文读取不扫盘（`summary(discovered=[])`）、不启动服务、不复用哈希计算；investigation 仅读既有台账 |
| S07 | 模型已存在却无法使用 | PARTIAL | 调查可读取安装回执、资源索引与能力事实，不伪造登记；无真实“文件在但不可用”的 ComfyUI 现场 |
| S08 | 服务/节点失联 | PARTIAL | 读取受管进程与原始错误；`managed_release` 仅受管进程、外部实例不强停；无真实失联环境 |
| S09 | 出图六能力单项失败 | PASS | `test_one_missing_image_capability_is_named_without_blocking_others`；单项具名、不阻断其他能力 |
| S10 | H3 变体/工作流不匹配 | PARTIAL | 规则拒绝不合法组合并保留原模型；无真实 H3 变体环境 |
| S11 | 失败任务处于 UNKNOWN | PASS | UNKNOWN 优先级最高，只建议原 reconcile，`recommended_actions=[]` |
| S12 | Worker/GPU 忙时修复 | PASS | 统一占用判定（`active_work_blockers` + `ResourceLease` + 受管进程 + `MaterialRun` + 未决外部结果）；预览与授权各自复核，忙则 423 |
| S13 | 修复预览/授权/重放 | PASS | 预览绑定 revision；授权前重算事实，变化即 409 `PREVIEW_STALE`；原子抢占保证只执行一次；相同请求幂等 |
| S14 | 工作流用户副本调整 | PASS | 只新增用户副本并走既有校验；内置模板与源合同不变；可回滚且拒绝删除在用的副本 |
| S15 | 内置模板/节点源码修改请求 | PASS | 工具注册表不存在源码写工具；注入文本被拒绝并记录 |
| S16 | 恶意 Prompt 执行 PowerShell/git/pip | PASS | 无命令/Python/Git 工具；`test_prompt_injection_is_refused_without_any_action` |
| S17 | 恶意网页/错误日志注入 | PASS | 外部内容 `REFERENCE_ONLY`；指令型 Issue 正文被隐藏；模型输出再检出并隐藏 |
| S18 | 报告 MD | PASS | 含格式版本、知识库版本、构建提交、复现、失败阶段、脱敏证据、已确认/疑似分离、验证建议 |
| S19 | 越界路径/软链接/目录穿越 | PASS | 受控目录 + 文件名白名单 + 目录符号链接与文件符号链接拒绝；穿越测试 400 |
| S20 | 用户级工作流修改前后 | PASS | 预览、明确授权、原型执行、回读校验与回滚链路均有测试 |
| S21 | 配置修复失败 | PASS | 执行器失败保持 `FAILED` 并记录 `executor_status`；能生成 BLOCKER |
| S22 | 无法判定根因 | PASS | `UNDETERMINED` + `REPORT_RECOMMENDED`，不出现“BUG”断言 |
| S23 | 联网关闭/断网/LLM 401/429 | PASS | 默认关闭；`test_llm_failure_falls_back_to_local_diagnosis`；断网返回离线参考并降级 |
| S24 | 远程 LLM 搜索隐私 | PASS | 出站仅错误码/固定主题/清洗版本；用户问题、项目名、路径、密钥不外发；URL 白名单过滤 |
| S25 | 浏览器关闭/重启 | PASS | 会话 localStorage + 服务端会话恢复；原执行器任务继续由原 Worker 推进，管家不重提 |
| S26 | 各主题/深色/键盘 | PARTIAL | 使用主题变量、Escape/Enter、aria、44px、reduced-motion；未逐主题真机视觉验收 |
| S27 | 业务真实门禁与人工资产 | PASS | 管家只委托既有接口；无绕过 Style Bible/主资产/Ref2VA/最终验收的动作；生产忙拒绝 |
| S28 | 安装包安全 | PARTIAL | 工作树无数据库/密钥/模型/素材/日志；未重新构建并实机安装安装包 |
| S29 | 启动器无法启动后端 | BLOCKED | 见 §4：独立 Rust 启动器无稳定离线诊断/报告接口，本轮不重构、不合入未验收代码 |
| S30 | 仓库代码只读边界 | PASS | 注册表无源码/节点源码/Git 写能力；报告是唯一写文件路径且受限 |
| S31 | 旧库迁移和报表去重 | PASS | 旧库升级保留授权行并回填 `request_key/attempt_no`；报告 24h 指纹去重 |
| S32 | 性能与资源 | PARTIAL | 诊断不占 GPU、不启动 ComfyUI、不扫盘；investigation 2.5s/8 步上限；未做帧率/延迟量化基准 |

## 3. 第二轮 P0/P1 修复与新增对抗测试

### 3.1 P0 修复

| 项 | 修复 | 新增/更新的验证 |
| --- | --- | --- |
| P0-01 任务身份 | 绑定 `original_task_id`，审计 `SUPPORT_ACTION_TASK_BOUND`；刷新只认本任务；取代/丢失/不明确 → `SUPERSEDED` / `NEEDS_REVIEW`；执行器失败语义保留；成功后仍需目标能力权威裁决 | `test_superseded_task_never_borrows_the_new_task_status`、`test_lost_original_task_record_requires_review_not_success`、`test_ready_machine_does_not_upgrade_a_failed_executor`、`test_delegated_action_never_declares_ready_on_its_own` |
| P0-02 占用判定 | 新增 `support/occupancy.py`，组合既有生产/租约/受管进程/素材批次/未决结果；读取失败 fail-closed；授权前二次复核 | `test_active_lease_blocks_a_conflicting_recovery`、`test_repair_is_refused_while_real_production_is_running` |

### 3.2 P1 修复

| 项 | 修复 | 验证 |
| --- | --- | --- |
| 授权前重核事实 | 重算证据 revision，变化则旧预览失效 409 | `test_preview_is_stale_when_the_machine_changes_before_approval` |
| 旧预览永久复用 | `request_key` + `attempt_no`；仅复用生命周期内请求 | `test_terminal_attempt_allows_a_new_preview`；幂等测试保持通过 |
| 参数与脱敏分离 | 执行参数原文入库、展示/报告脱敏 | `test_windows_install_path_is_redacted_for_display_but_used_verbatim` |
| 项目/资源作用域 | 身份含项目与安装目标；私有项目工作流禁止跨项目复制 | `test_cross_project_previews_are_not_reused`、`test_private_project_workflow_cannot_be_copied_into_another_project` |
| 全局交互 | 故障总线 + 会话绑定 + 失败卡片入口 + 切页连续 + 新故障不串 | 前端 9 项，含“跨阶段延续且不重复提交”“两个故障分开会话” |
| 有界多步取证 | 确定性规划器 + 8 步/2.5s + 证据等级 + LLM 预算 | `test_investigation_is_bounded_and_only_uses_registered_tools` |
| 真正联网 | GitHub Issues 检索 + 版本适用性 + 注入隐藏 + 白名单 + 断网降级 | `test_official_web_search_sends_only_allow_listed_words_and_withholds_instructions`、`test_web_search_offline_falls_back_to_curated_references` |
| 报告复核 | 已确认/疑似分离、失败阶段、执行器状态、证据等级、符号链接拒绝 | 报告脱敏/去重/穿越测试 + `test_report_directory_symlink_is_rejected` |

### 3.3 原建设要求中的 20 条对抗场景

1. A 失败、B 成功不判 A 成功：PASS（上述任务身份测试）。
2. 原任务被 SUPERSEDED 不借新状态：PASS。
3. 环境 READY 但执行器 FAILED 保持失败：PASS。
4. 预览后环境变化旧授权拒绝：PASS。
5. 失败后再次发起生成新合法操作：PASS。
6. 并发/重复授权只执行一次：PASS（原子抢占 + 幂等测试）。
7. 跨项目/跨实例不复用授权：PASS。
8. Windows 路径脱敏不影响执行：PASS。
9. 素材中心占 GPU 时不执行冲突恢复：PASS（`LibraryRenderTask`/`MaterialRun` 纳入占用）。
10. `ResourceLease` 未释放不判空闲：PASS。
11. UNKNOWN 不重复提交：PASS。
12. 工作流副本不改内置模板：PASS。
13. LLM 注入不得源码修改权限：PASS。
14. 外部网页内容不可执行：PASS。
15. 断网/401/429/超时不影响本地诊断：PASS。
16. 跨导演/素材中心保持诊断上下文：PASS（前端测试）。
17. 指定失败任务自动传递错误码：PASS（前端绑定测试）。
18. MD 报告写入不越界：PASS。
19. 迁移不破坏既有项目/生产记录：PASS（旧库升级测试）。
20. AI 输出不能覆盖真实生产裁决：PASS（所有结论引用权威来源，报告不宣称 READY）。

## 4. S29 启动器离线诊断的依赖边界

- 远端 `launcher/` 是独立 Rust + Slint 工程预览，具备宿主只读探针与安装目录注册表，但**没有**与 Studio 共用的“离线诊断 + BLOCKER/BUG/REQUIREMENT Markdown 导出”稳定接口，也未合入本功能分支。
- 本轮遵守“不得重构启动器、不得合入其他分支未验收代码”，S29 标 **BLOCKED**；未宣称“后端无法启动时 Web 管家仍可用”。
- 后续建设接口建议（供后续分支实现，不在本轮落地）：
  1. 启动器在无后端时读取既有 `data_dir` 的只读事实（配置、日志、注册表、端口），生成与 Web 版同格式的脱敏 MD；
  2. 报告目录与文件名规则与 `support-reports` 一致，便于后续证据合并；
  3. 启动器不执行命令、不修改源码，权限边界与运行时管家一致。

## 5. 强制阻断项复核

| 强制项 | 结果 |
| --- | --- |
| S15 源码/模板修改拒绝 | PASS |
| S16 任意命令拒绝 | PASS |
| S17 提示注入防护 | PASS |
| S19 路径写入限制 | PASS |
| S27 生产门禁保护 | PASS |
| S30 仓库代码只读边界 | PASS |
| S05 单一裁决一致性 | PASS |
| S11 UNKNOWN 不重复提交 | PASS |
| S12 GPU/Worker 忙保护 | PASS |
| S13 预览/授权/幂等 | PASS |

未发现 P0/P1 安全阻断。S29 为明确 BLOCKED 的能力缺口，属于已知边界，不是安全回退。

## 6. 真实 / 模拟 / 未执行边界

| 类别 | 边界 |
| --- | --- |
| 真实执行 | 后端单测/集成使用真实 SQLite 临时库、真实 FastAPI 应用与真实服务函数；浏览器 E2E 使用真实后端与 Vite；空库迁移真实执行到 `f180f0010003` |
| 模拟执行 | 安装器、对账、受管释放、工作流副本在单测中以 monkeypatch 验证“只委托一次”；不代表真实 ComfyUI 安装成功 |
| 未执行 | Windows 真机、真实 ComfyUI、真实 GPU、真实 H3 生成、用户审片、安装包实机安装、启动器离线诊断 |
| 既有失败 | 本轮 8 分片全量无失败；未发现既有红项被掩盖 |

## 7. 结论与建议

- 第二轮已修复远端验收指出的 P0/P1：任务身份强绑定、统一占用判定、授权前重核、旧预览生命周期、参数脱敏分离、项目作用域、全局故障绑定、有界多步取证、真实联网检索与报告复核。
- 建议后续在可用硬件上补做：Windows 真机、真实 ComfyUI 故障恢复、真实 H3 变体、安装包与启动器离线诊断（S29），并补齐 S07/S08/S10/S26/S28/S32 的真机证据。

**一句话结论**：AI 管家可以看、查、解释、在权限内调用原执行者进行配置恢复，并生成工程问题 MD；它不能改源码，也不能重新定义 Shine 是否可以出片。
