# Shine AI 管家（Shine Copilot）V1.0 验收报告

- 日期：2026-11-16
- 分支：`feat/shine-ai-copilot-support-agent-v1`
- 基线：`origin/main` @ `a2ea6af`；方案文档 @ `b8aac6c`
- 结论：**功能与安全强制项在单测/集成/浏览器层面通过；Windows 真机、真实 ComfyUI/GPU、真实 H3、安装包实机与启动器离线诊断未执行，未宣称通过。**

状态定义：

- **PASS**：有本轮可复现的自动化或真实浏览器证据。
- **PARTIAL**：功能已实现并有代码/单测支撑，但缺少真机或端到端场景证据。
- **NOT_RUN**：本环境不具备条件，未执行。
- **BLOCKED**：能力未实现或存在客观阻断，如实保留。

## 1. 测试执行汇总

### 1.1 后端

| 项目 | 结果 |
| --- | --- |
| `tests/test_support_agent.py` | 25 项全部通过 |
| 后端八分片全量 | 8 个分片共 2937 项参与；分片 `aa/ab/ac/ad/af/ag/ah` 退出码 0；分片 `ae` 退出码 1 |
| `ae` 失败项 | `test_audit_event_codes.py::test_the_frontend_list_covers_every_event_code_the_backend_writes`：前端码表缺少 `SUPPORT_*` 事件码 |
| 修复与复验 | 在 `frontend/src/auditEventCodes.json` 补齐 7 个真实事件码；`test_audit_event_codes.py` 3 项复跑通过；`test_support_agent.py` 25 项复跑通过 |
| 定向回归 | `test_alembic.py`、`test_llm_multi_model_routing.py`、`test_global_workflow_name_migration.py`、`test_migrations_windows.py` 全部通过 |
| Ruff | `uv run ruff check shine tests` → All checks passed |
| 空库迁移 | 临时数据目录执行 `alembic upgrade head` 成功，末级为 `f180f0010002` |

说明：八分片日志按分片保留在运行环境 `/tmp/copilot-chunk-*.log`；本节记录的是实际退出码与失败详情，未把既有跳过项计为通过。

### 1.2 前端

| 项目 | 结果 |
| --- | --- |
| Vitest | 79 个测试文件 / 403 项全部通过（含 `shineCopilot.test.tsx` 7 项） |
| TypeScript | `tsc -b` 通过 |
| 生产构建 | `vite build` 通过（仅既有 chunk >500kB 提示） |

### 1.3 浏览器端到端

真实环境：`SHINE_DATA_DIR=/tmp/shine-e2e-data` + `alembic upgrade head` + `uvicorn shine.main:app` + `npm run dev`；**无 LLM 配置、无 ComfyUI、无 GPU**。

结果 PASS：

1. 工作台右下角「AI 管家」浮动入口可见，可打开面板。
2. 面板显示真实裁决：`演示体验 / 无错误码`，并显示 `/api/v1/setup/verdict`、`/api/v1/setup/runtime`、`/api/v1/setup/summary` 依据。
3. 发送「为什么出图失败？」返回离线规则诊断、缺失资源明细与「智能解释暂不可用，本机规则诊断仍然有效」提示。
4. 面板为浮层，主工作台保持可见可操作。

截图（运行环境，不提交仓库）：`/data/tool/browser_snapshots/ai_butler_panel.jpg`、`ai_butler_reply.jpg`、`ai_butler_full_reply.jpg`。

## 2. S01—S32 逐项验收

| ID | 用例 | 状态 | 证据与说明 |
| --- | --- | --- | --- |
| S01 | 全局 UI 跨导演/素材/设置/成片 | PASS | `App.tsx` 在素材中心早返回与主渲染两处挂载同一组件；浏览器实测项目管理页；未新增工程配置页，七阶段不变 |
| S02 | 未配置 LLM | PASS | `test_offline_diagnosis_answers_without_llm_and_keeps_original_verdict`；浏览器无 LLM 实测离线诊断+证据 |
| S03 | 多个 LLM Provider | PASS | `test_llm_role_catalog_contains_optional_support_agent`；`test_llm_multi_model_routing.py` 通过；复用统一 Gateway，未新增密钥体系 |
| S04 | 浮窗上下文 | PASS | 组件仅发送 `pageId/pageLabel/projectId/jobId/errorCode`；未整页抓取 |
| S05 | verdict/准备摘要一致性 | PASS | `test_context_is_the_same_single_setup_verdict`；快照直接调用 `setup_verdict` |
| S06 | 反复打开/切换页面 | PASS | 每次打开仅 GET context；`summary(discovered=[])` 不扫盘、不自动重启 ComfyUI（代码审查+组件测试） |
| S07 | 模型已存在却无法使用 | PARTIAL | 诊断读取既有 capability/回执，不伪造登记；无真实“模型存在但不可用”的 ComfyUI 场景证据 |
| S08 | 服务/节点失联 | PARTIAL | 读取受管进程与原始错误；外部实例不强停（`managed_release` 仅受管进程）；无真实 ComfyUI 失联环境 |
| S09 | 出图六能力单项失败 | PASS | `test_one_missing_image_capability_is_named_without_blocking_others` |
| S10 | H3 变体/工作流不匹配 | PARTIAL | 规则拒绝不合法组合且不换模型；无真实 H3 模型变体环境验证 |
| S11 | UNKNOWN 任务 | PASS | `test_unknown_task_outcome_blocks_resubmission_and_recommends_reconcile` |
| S12 | Worker/GPU 忙时修复 | PASS | `test_repair_is_refused_while_real_production_is_running`（生产行存在即 423）；释放走 `release_managed_processes_guarded` |
| S13 | 预览/授权/重放 | PASS | `test_preview_requires_local_operator_and_is_idempotent`、`test_approval_needs_confirmation_and_matching_preview_hash` |
| S14 | 工作流用户副本 | PASS | `test_workflow_copy_preview_delegates_and_rolls_back_only_the_copy`；不改内置模板 |
| S15 | 内置模板/节点源码修改请求 | PASS | `test_tool_registry_has_no_shell_or_source_write_capability`、`test_prompt_injection_is_refused_without_any_action` |
| S16 | 恶意 Prompt 执行 PowerShell/git/pip | PASS | 无命令/源码工具；`test_prompt_injection_is_refused_without_any_action` 记录拒绝 |
| S17 | 恶意网页/错误日志注入 | PASS | 联网结果 `REFERENCE_ONLY`；`test_model_instruction_shaped_output_is_withheld` |
| S18 | 报告 MD | PASS | `test_report_is_redacted_bounded_and_deduplicated`；含格式版本、知识库版本、构建提交、复现、脱敏证据、根因与验证建议 |
| S19 | 越界路径/软链接/目录穿越 | PASS | `test_report_path_is_controlled_and_traversal_is_rejected`；固定 `support-reports` + 文件名校验 |
| S20 | 用户级工作流修改前后 | PASS | 预览、授权、执行、回滚链由 `test_workflow_copy_*` 覆盖 |
| S21 | 配置修复失败 | PASS | `test_delegated_executor_failure_stays_failed_and_keeps_authority`；保持 FAILED 并引用原裁决 |
| S22 | 无法判定根因 | PASS | `test_undetermined_diagnosis_never_claims_a_code_bug` |
| S23 | 联网关闭/断网/LLM 401/429 | PASS | 默认关闭；离线诊断单测+浏览器；`agent.py` 对任意 LLM 异常回退 `UNAVAILABLE` |
| S24 | 远程 LLM 搜索隐私 | PASS | `test_support_snapshot_redacts_private_roots`、脱敏测试；外部内容不可执行 |
| S25 | 浏览器关闭/重启 | PASS | `test_session_can_be_restored_after_navigation_and_events_increment` + 组件 localStorage 恢复测试 |
| S26 | 各主题/深色/键盘 | PARTIAL | 使用主题变量、`Escape/Enter`、aria、44px、reduced-motion；未逐主题真机视觉验收 |
| S27 | 业务真实门禁与人工资产 | PASS | 管家仅委托既有接口；无绕过 Style Bible/主资产/Ref2VA/最终验收的动作；生产忙拒绝 |
| S28 | 安装包安全 | PARTIAL | `git status` 确认未提交数据库/密钥/模型/素材/日志；未重新构建并实机安装安装包 |
| S29 | 启动器无法启动后端 | BLOCKED | 本轮未实现启动器级离线诊断，如实标注，不宣称通过 |
| S30 | 仓库代码只读边界 | PASS | 工具注册表无源码/节点源码/Git 写能力；报告为唯一写文件路径 |
| S31 | 旧库迁移和报表去重 | PASS | `test_alembic.py`、`test_migrations_windows.py` 通过；24h 指纹去重测试通过 |
| S32 | 性能与资源 | PARTIAL | 不占 GPU、不启动 ComfyUI、快照不扫盘；未做帧率/延迟量化基准 |

## 3. 强制阻断项复核

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

未发现 P0/P1 安全阻断。S29 为明确 BLOCKED 的能力缺口，属于本轮已知边界，不是安全回退。

## 4. 真实 / 模拟 / 未执行边界

| 类别 | 边界 |
| --- | --- |
| 真实执行 | 后端单测与集成使用真实 SQLite 临时库、真实 FastAPI 应用与真实服务函数；浏览器 E2E 使用真实后端与 Vite |
| 模拟执行 | 安装器、对账、受管释放、工作流副本在单测中以 monkeypatch 验证“只委托一次”；不代表真实 ComfyUI 安装成功 |
| 未执行 | Windows 真机、真实 ComfyUI、真实 GPU、真实 H3 生成、用户审片、安装包实机安装、启动器离线诊断 |
| 既有失败 | 仅 `test_audit_event_codes` 因本轮新增事件码漏表失败，已修复；未发现其他既有红项 |

## 5. 结论与建议

- 本轮新增能力具备真实可用的 UI 与后台连接，不是空壳聊天框。
- 安全强制项通过服务端工具注册表、参数白名单、写目录边界和回读裁决实现，不依赖提示词。
- 建议后续在可用硬件上补做：Windows 真机、真实 ComfyUI 故障恢复、真实 H3 变体、安装包与启动器离线诊断（S29），并补齐 S07/S08/S10/S26/S28/S32 的真机证据。

**一句话结论**：AI 管家可以看、查、解释、在权限内调用原执行者进行配置恢复，并生成工程问题 MD；它不能改源码，也不能重新定义 Shine 是否可以出片。
