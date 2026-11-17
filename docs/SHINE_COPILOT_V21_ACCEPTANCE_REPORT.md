# Shine AI 管家 V2.1 — 工程验收报告

- 日期：2026-11-17
- 分支：`feat/shine-copilot-v21-auto-sync`
- 基线：V2 `6fd3d8c`（PR #56，OPEN）；V1 `82df4b5`（PR #55，OPEN）；`main` `f066357f`（V2.1 未直接修改）
- 应用版本：`2.1.0`；契约版本：`shine-copilot-contracts-2026.11.17-v21`
- 结论：**知识与控制契约已改为构建期从正式声明自动生成；运行时只加载打包产物，按动作精确降级；CI 契约门禁、升级兼容、旧委托保护、真实浏览器验证均通过。真实 Windows/ComfyUI/GPU/H3 与 S29 仍为 NOT_RUN/BLOCKED，未宣称通过。**

状态口径：PASS = 有本轮自动化或真实浏览器证据；PARTIAL = 已实现且合同有测试，但缺真机/完整端到端；NOT_RUN = 本环境不具备；BLOCKED = 客观受阻。

## 1. 测试结果

| 项目 | 结果 |
| --- | --- |
| `tests/test_support_agent.py`（V1） | 55 项全部通过 |
| `tests/test_support_copilot_v2.py`（V201—V240） | 24 项全部通过 |
| `tests/test_support_copilot_v3.py`（V2 P0/P1） | 27 项全部通过 |
| `tests/test_support_copilot_v21.py`（本轮专项） | **41 项全部通过** |
| 后端八分片全量 | **255 个测试模块 / 8 片全部退出码 0 / 0 失败** |
| 前端 Vitest | **79 文件 / 414 项全部通过**（管家组件 18 项，含 2 项构建提示） |
| TypeScript / Vite | `tsc -b`、`vite build` 通过（仅既有 chunk>500kB 提示） |
| Ruff | `backend/shine`、`backend/scripts`、`backend/tests` 全部通过 |
| 迁移 | 新增 `support_delegations`（`f180f0010005`）；空库与旧库升级成功，head 一致 |
| 契约一致性 | `generate_shine_copilot_contracts --check` 通过（14 动作 / 21 知识 / 425 DISCOVERED） |
| 真实浏览器 | Chromium headless：构建身份 FRESH、知识引用、项目创建完成，见 §4 |

### 1.1 后端八分片

- 命令：255 个 `backend/tests/test_*.py` 按文件均分 8 片，`pytest -q -p no:cacheprovider --shine-runner-tier full`。
- 结果：**8/8 退出码 0，0 失败**（包含 `test_support_copilot_v21.py` 41 项、V1/V2/V3 回归、迁移与真实浏览器外的全部后端合同）。
- 日志：`/tmp/v21shards/shard-1..8.log`、`shard-1..8.exit`（本轮最终代码运行；隔离环境临时目录，不入库）。

## 2. 本轮验收场景逐项

### 2.1 知识同步

| 场景 | 结果 | 证据 |
| --- | --- | --- |
| 新增正式功能后构建器自动发现 | PASS | `test_new_feature_declaration_reaches_the_runtime`（真实声明 → 生成 → 加载 → 查询/执行） |
| 更新功能描述后索引随构建更新 | PASS | 生成器输出 `content_sha256`；`--check` 比较 |
| 删除功能后旧知识不再声明已实现 | PASS | 知识条目集合 == 声明集合（`test_knowledge_and_capabilities_are_derived_not_hand_maintained`） |
| 同一代码重复生成指纹一致 | PASS | `test_generation_is_deterministic`（两次字节一致） |
| 旧版 Shine 使用旧版知识 | PASS | 产物随安装包；`source_commit` 构建注入，清单与产物同版本 |
| 索引缺失/损坏安全降级 | PASS | `test_damaged_index_degrades_to_unknown_not_crash`（UNKNOWN + `CONTRACT_UNAVAILABLE`） |
| 文档来源不能覆盖业务裁决 | PASS | `test_docs_do_not_promote_a_partial_feature`（H3 仍 PARTIAL/HARDWARE_REQUIRED） |

### 2.2 控制同步

| 场景 | 结果 | 证据 |
| --- | --- | --- |
| 新增已审核只读能力自动识别 | PASS | 同上（`read_v21_probe` 进入目录） |
| 参数类型/必填/枚举/长度/默认来自原 Pydantic Schema | PASS | `test_resolved_parameter_contract_comes_from_the_request_schema` |
| 新增真实可选参数只需改 Schema + 声明 | PASS | `test_new_optional_schema_parameter_flows_to_the_executor`（预览→执行器收到 `subtitle`） |
| 不认识/未声明参数明确拒绝 | PASS | `test_unknown_parameter_is_rejected_not_silently_dropped` |
| 完整 API 请求合同指纹 | PASS | `test_api_schema_digest_ignores_documentation_and_order`、`test_api_contract_diff_classifies_field_changes` |
| 兼容 API 变更不失效旧授权 | PASS | `test_api_contract_state_blocks_incompatible_and_allows_compatible` |
| 类型/枚举/必填/默认/嵌套/权限/删除变更分类 | PASS | 同上（7 类对抗用例） |
| 未审核写 API 不自动开放执行 | PASS | `test_undeclared_or_unreviewed_api_never_executes`（`CONTRACT_REVIEW_REQUIRED`） |
| API 增加兼容参数目录更新 | PASS | `param_contract` 由声明生成；`--check` 通过 |
| API 删除参数旧动作不误执行 | PASS | `test_contract_check_fails_when_a_parameter_is_removed` + `CONTRACT_CHANGED` |
| 权限等级提高旧授权失效 | PASS | 合同指纹纳入 digest；`test_contract_change_invalidates_a_stored_authorization` |
| 执行者变更必须通过校验 | PASS | `test_executor_and_readback_drift_is_a_build_failure` |
| 回读缺失不能宣称成功 | PASS | 同测试（read-back route 不存在 → CI FAIL） |
| 原业务门禁继续有效 | PASS | V2 `STORYBOARD_STYLE_NOT_FROZEN`、`TEXT_ROLE_NOT_READY`、`UNRESOLVED_OUTCOME` 回归 |
| 不产生第二套生产系统 | PASS | 声明只引用既有 executor；只新增委托授权元数据表 `support_delegations`，不新增生产台账/调度器/执行器 |

### 2.3 委托与升级

| 场景 | 结果 | 证据 |
| --- | --- | --- |
| 升级前创建委托，升级后能读取 | PASS | 统一存 `support_delegations`；旧 `context_json` 信封自动迁移 |
| 合同兼容可合法继续 | PASS | `test_delegation_continues_after_a_compatible_upgrade` |
| 合同不兼容暂停未执行步骤 | PASS | `test_delegation_pauses_when_the_contract_changed`（`PAUSED` + 未创建项目） |
| 升级不重复提交已完成任务 | PASS | `test_completed_delegation_is_never_resubmitted` |
| 旧计划不能授权新计划 | PASS | 每次预览生成不可变 `plan_id`/`plan_hash`；旧按钮 → `PLAN_CHANGED` |
| 重复授权整份委托 | PASS | `authorization_key` 唯一；不重复建项目/开生产 |
| 并发授权 | PASS | 数据库唯一约束拒绝第二个同键授权 |
| 进程重启/新会话恢复 | PASS | DONE/WAITING/PAUSED/FAILED/CANCELLED 新会话可读；已完成步骤不重提 |
| UNKNOWN 必须等待原任务核对 | PASS | V3 `test_p1_unknown_task_is_reported_but_never_resubmitted` |
| 不跨项目复用授权 | PASS | V3 `test_p0_01_historical_authorization_cannot_be_replayed_elsewhere` |
| 旧委托无合同不执行 | PASS | `test_legacy_delegation_without_a_contract_is_not_executed`（`CONTRACT_UNVERIFIED`） |

### 2.4 构建与发布

| 场景 | 结果 | 证据 |
| --- | --- | --- |
| CI 识别关联业务合同修改 | PASS | L1 执行 `--check`；CI 在推送后运行（见 §6） |
| 未更新静态说明可发现差异 | PASS | 知识指纹/清单比较 |
| 高风险合同失配 CI FAIL | PASS | 声明参数、执行者、回读、权限、路由、API 请求体不兼容变化均失败 |
| Windows 安装包包含正确版本知识与合同 | PARTIAL | `build_test_package.ps1` 已在 staging 生成并 `--check`；本环境无 Windows，实机 NOT_RUN |
| 安装包知识版本与 BUILD_INFO 一致 | PARTIAL | 生成器写入 `--commit`；构建身份同时核对 artifact/manifest/知识/合同/API Schema；实机安装 NOT_RUN |
| 发布构建不受 Git 自引用影响 | PASS | 生成器不再回退 `git rev-parse HEAD`；产物 `source_commit` 由打包注入 |
| 用户无须运行脚本 | PASS | 运行时只读打包产物；`/support/build` 与前端提示 |
| 单一动作失配局部降级 | PASS | `test_single_action_contract_mismatch_degrades_only_that_action`（`PARTIAL` + 该动作 `CONTRACT_STALE`） |
| 全量合同失配不再显示 FRESH | PASS | `test_full_api_contract_mismatch_is_not_reported_fresh` |
| 构建身份四指纹交叉核对 | PASS | `test_build_identity_detects_manifest_and_artifact_commit_mismatch` |

### 2.5 安全对抗

| 场景 | 结果 | 证据 |
| --- | --- | --- |
| 提示词注入不能注册新能力 | PASS | V2 `test_v209_v210_injection_never_becomes_a_capability`；本文件 `test_natural_language_cannot_invoke_an_undeclared_api` |
| 官方文档不能改变控制权限 | PASS | `test_official_docs_cannot_change_permission_or_grant_tools` |
| 本地知识不能授予 Shell/Git/源码写入 | PASS | 能力目录 `forbidden`；V2 `test_v211` |
| 未登记 API 不可自然语言调用 | PASS | 同上 |
| 新增高风险操作不得自动授权 | PASS | `availability=REVIEW_REQUIRED` 门禁 |
| 运行时修改清单不能提权 | PASS | `test_runtime_artifact_cannot_self_grant`（`CONTRACT_NOT_DECLARED`） |

### 2.6 新增受控写能力的真实端到端证据

本轮新增一个受控写能力 `support_web_search_update`（原 `POST /api/v1/support/preferences` 正式 API + `PreferenceUpdate` 请求模型，此前矩阵中仅为 GUARDED 且无 action）：

| 步骤 | 真实结果 |
| --- | --- |
| 1. 正式 API / Schema | `POST /api/v1/support/preferences` + `shine.api_support:PreferenceUpdate` |
| 2. 正式能力声明 | `declarations.py` 一次声明；`AVAILABLE` + 唯一执行者 `shine.support.agent.set_web_search` |
| 3. 构建期自动生成 | `copilot_contracts.json` 生成 `param_contract`（`web_search: boolean`）、知识、矩阵与 API 合同 |
| 4. 前端获取参数 | `GET /api/v1/support/capabilities/actions` 返回 `support_web_search_update` 及其 `param_contract` |
| 5. 自然语言计划 | 「帮我开启管家联网检索」→ `WEB_SEARCH` 计划，步骤 `READY`，参数 `web_search=true` |
| 6. 预览并授权 | 计划 `plan_id` + `plan_hash` → 委托 → 每步仍走 `preview → approve` |
| 7. 调用新能力执行者 | `shine.support.agent.set_web_search(db, enabled=True)` 真实写入 |
| 8. 回读真实结果 | `GET /api/v1/support/preferences` 返回 `{"web_search": true}` |
| 9. 旧计划保护 | 旧 `plan_id` 授权新计划 → `PLAN_CHANGED`，不执行 |

证据：`test_new_write_capability_is_generated_planned_and_executed`。未审核的新写 API 仍只能 `DISCOVERED`/`REVIEW_REQUIRED`，由 `test_undeclared_or_unreviewed_api_never_executes` 与 `test_unreviewed_new_write_api_stays_discovered` 覆盖。

## 3. V1 / V2 回归

- V1 S01—S32：`test_support_agent.py` 55 项通过；结论沿用 V1 报告（PASS 大部分；S07/S08/S10/S26/S28/S32 PARTIAL；S29 BLOCKED）。V2.1 未重构启动器。
- V2 V201—V240：24 项全部通过；唯一更新的测试是 `test_p1_delegation_resumes_from_the_stored_envelope`（信封现在必须携带合同指纹，属于 V2.1 的升级保护）。
- P0-01 / P0-02：V3 27 项全部通过，未回归。

## 4. 真实浏览器证据（Chromium headless）

环境：`uvicorn shine.main:app --port 8137` + `vite --port 5199` + 全新 SQLite（`f180f0010005`），`SHINE_BUILD_COMMIT` 与清单提交一致（模拟安装包）。

| 步骤 | 真实结果 |
| --- | --- |
| `/api/v1/support/build` | `state=FRESH`、`api_matches=true`、14 写 / 8 读、`app_version=2.1.0` |
| 打开 AI 管家 | 顶部显示「AI 管家知识已匹配当前版本。」 |
| 问「Shine 当前版本支持哪些导演模式能力？」 | 返回带来源的版本化引用：`导演模式七阶段`、`AI 管家契约自动同步（V2.1）`、`导演风格引擎与冻结` |
| 说「建立一个名叫《雾港回声》的短剧项目。」 | 计划 → 预览 → 确认 → `已完成并通过权威复核`；`GET /api/v1/projects` 出现 `雾港回声` |
| 本轮复验：打开 AI 管家 | 面板顶部 `p.copilot-build` 显示「AI 管家知识已匹配当前版本。」；控制台无 error，55 个请求无 4xx/5xx |
| 本轮复验：自然语言新能力 | 输入「帮我开启管家联网检索」→ 出现「开启管家联网检索」计划步骤、预览与授权按钮；无 error |

截图：`/tmp/v21e2e/01-build-notice.png`、`02-knowledge-answer.png`、`03-project-created.png`（运行环境临时目录，不入库）。

控制台仅出现 `127.0.0.1:8790`（桌面运行时编排器）连接被拒，属沙箱缺少该本机组件；无应用错误。未伪造 GPU/H3 结果。

## 5. 未完成与未验证

| 项目 | 状态 | 说明 |
| --- | --- | --- |
| Windows 真机安装包构建与完整性 | NOT_RUN | 本环境无 Windows；脚本路径与 `--check` 已更新 |
| 真实 ComfyUI / GPU 租约 / MiniMax-H3 出片 | NOT_RUN | 无 GPU/H3；合同与拒绝路径有测试 |
| 启动器 S29 离线诊断 | BLOCKED | 未实现，保持独立依赖 |
| 真实多线程并发授权压测 | PARTIAL | 数据库 `authorization_key` 唯一约束 + 重复/旧计划授权测试；未做操作系统级多进程压测 |
| 安装包内 FRESH 实机验证 | PARTIAL | 生成/校验链路已真实运行；安装后启动未在本环境执行 |

## 6. CI 与交付

- 分支：`feat/shine-copilot-v21-auto-sync`（基于 V2 HEAD），不新建其它分支、不改 main。
- PR：`#59`（https://github.com/anjingdtl/shine-video-studio/pull/59），base 为 V2 分支，保持 OPEN，不自行合并。
- CI：run `44010753962`（`7258ce1`）— Frontend build、L1 backend quick（含
  `generate_shine_copilot_contracts --check`）、L2 backend integration **全部 success**；
  L3 full/Windows/browser E2E 按 PR 级跳过。
- 本轮未改 `main`（`f066357f`）；PR #55、#56 均保持 OPEN，未自行合并。
- PR 堆叠关系：#55（V1 → main）→ #56（V2 → #55）→ #59（V2.1 → #56）。
- 与最新 `main`（`f066357f`）的合入检查：#55 与 main 在 `frontend/src/components/FourStagePanel.tsx`（main 删除、V1 修改）冲突；#56 同样在该文件冲突；#59 在 `AGENTS.md` 内容冲突并叠加该删除/修改冲突。冲突属于 UI/文档合并面，不改变本分支后端合同；按“不擅自合并”要求保持现状。
