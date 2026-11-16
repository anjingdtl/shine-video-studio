# Shine AI 管家 V2.0 工程实施报告

- 日期：2026-11-16
- 建设依据：`docs/SHINE_AI_COPILOT_V2_KNOWLEDGE_AUTOMATION_DIAGNOSTICS_PLAN_20261116.md`
  （方案提交 `3c84c9eedbed1a672ef267d42e26004fe1a7a1c5`，在 V2 分支 cherry-pick 为 `e9198d2`）
- V1 基线：`feat/shine-ai-copilot-support-agent-v1` @ `82df4b534c12609cced97c6cbaf7a1391d3632bf`（PR #55，仍是 V2 的 PR base）
- V2 分支：`feat/shine-ai-copilot-v2-full-capabilities`
- 独立 Worktree：`/workspace/.worktrees/shine-copilot-v2`
- `origin/main` 基线：`a2ea6af`（V2 未直接改 main，也未把 V2 堆叠到 PR #55 分支）
- 迁移：V2 未新增表；复用 `f180f0010004`（V1 的动作级指纹列）

## 1. 架构结论（与 V2 方案逐条对照）

| V2 方案要求 | 本轮落地 | 事实来源 |
| --- | --- | --- |
| 一套事实 | 知识、计划、调参、诊断都读既有台账；未建第二个资源台账或第二套状态 | `support/project_knowledge.py`、`planner.py`、`parameters.py`、`director/overview.py` |
| 一个裁决 | 环境/生产/冻结/资产/交付仍由原有门禁裁决；AI 不产生 READY | `setup_verdict`、`package_readiness`、`GenerationStrategy`、`Policy` 原样复用 |
| 一个执行者 | 每个写动作映射到一个既有服务/路由执行者，无并行执行器 | 能力目录 `executor` 字段；`actions.py` 委托分支 |
| 代码零修改 | 运行时没有 shell/git/patch/任意 HTTP/源码写入工具；工程问题只写受控 MD | `tools.py` 注册表、`reports.py`、能力目录 `forbidden` |
| 不新增重型依赖 | 知识检索为轻量确定性打分；无 embedding、无 GPU、无网络 | `project_knowledge.search`、`official_docs.search` |

## 2. 交付模块

### 2.1 新增

| 文件 | 作用 |
| --- | --- |
| `backend/shine/support/project_knowledge.py` | 版本化项目百科：19 个真实模块条目、`IMPLEMENTED/PARTIAL/PLANNED/BLOCKED/NOT_SUPPORTED` 状态、来源路径/API/测试证据、BM25 风格离线检索、构建 SHA 绑定与 `FRESH/STALE/UNKNOWN` |
| `backend/shine/support/official_docs.py` | ComfyUI / MiniMax-H3 官方来源元数据、许可与再分发策略（`SUMMARY_ONLY` / `LINK_ONLY`）、12 条自编短摘要、H3 官方能力与 Shine 本机能力的边界表 |
| `backend/shine/support/capability_catalog.py` | 权威能力注册中心：10 个可委托动作、7 个只读能力、41 项 UI→API→执行者覆盖矩阵、人工确认/永久禁止/未接入清单与工程原因 |
| `backend/shine/support/planner.py` | 有界只读计划：制作准备、素材缺口、显存释放、调参、建项目、导入原文；步骤只能指向注册能力或人工步骤 |
| `backend/shine/support/parameters.py` | 用户级参数合同：项目口播语言、工作流参数映射副本、生产策略（变体/加速/二次采样/RelayVSR X2）；差异预览与 schema 校验，无写操作 |
| `backend/shine/support/knowledge_manifest.json` | 知识清单与内容哈希（构建时由脚本重新生成并记录提交号） |
| `backend/scripts/generate_copilot_knowledge.py` | 构建/发布时重建知识清单：版本、来源提交、内容哈希 |
| `docs/SHINE_AI_COPILOT_V2_OPERATION_MATRIX_20261116.md` | 全业务操作覆盖矩阵（455 条后端路由的审计结论与 41 项操作映射） |

### 2.2 扩展

| 文件 | 变更 |
| --- | --- |
| `backend/shine/support/tools.py` | 动作白名单新增 `project_create`、`document_import_text`、`project_settings_update`、`story_outline_generate`、`storyboard_generate`、`production_start`；仍无 shell/源码/Git/任意 HTTP |
| `backend/shine/support/actions.py` | 新动作的预览、动作级指纹、授权、委托、回读；执行器拒绝保留原错误码（含裸 `STORYBOARD_STYLE_NOT_FROZEN`）；生产绑定 `run_id` 并读真实 Run/集状态；用户级操作不占 GPU 门禁但保留既有业务门禁 |
| `backend/shine/support/agent.py` | 意图路由（百科/诊断/操作）；答案中加入「项目百科」「操作计划」；计划中的可执行步骤进入 `recommended_actions`（仍需预览确认）；审计记录 intent/plan/知识命中 |
| `backend/shine/api_support.py` | 新增 `GET /knowledge/project`、`GET /knowledge/sources`、`GET /capabilities/actions`、`POST /plans/preview`、`GET /parameters/contracts`、`POST /parameters/preview`、`POST /parameters/confirm`；动作预览/授权扩展到 10 类 |
| `backend/shine/services.py` | 抽出唯一执行者 `create_project`、`update_project_settings`、`import_project_document`、`MAX_SCRIPT_BYTES`，供原路由与管家动作共用（路由行为不变） |
| `backend/shine/main.py`、`api_agent.py` | 路由改为调用上述服务，避免第二套导入/创建逻辑 |
| `frontend/src/components/ShineCopilot.tsx` | 在既有浮窗中展示项目百科引用、操作计划与步骤状态，可直接预览计划步骤；预览面板显示集数、门禁缺项、参数差异与待配置角色 |
| `frontend/src/styles.css` | 计划/百科样式，沿用既有主题变量 |
| `frontend/src/test/shineCopilot.test.tsx` | 新增计划 + 百科 + 计划步骤预览测试 |

### 2.3 未新增（有意为之）

- 没有新的数据库表或第二个任务系统；计划只存在于会话消息与响应中。
- 没有新的 ComfyUI/GPU/安装/工作流调度器。
- 没有浏览器 DOM 点击或硬编码选择器；前端只展示并调用正式 API。
- 没有联网抓取正文；官方知识是自编摘要 + 来源链接，联网检索仍由 V1 的显式开关控制。

## 3. 能力目录与覆盖

| 状态 | 数量 | 含义 |
| --- | --- | --- |
| GUARDED | 14 | 可由自然语言委托，但必须预览 + 用户确认 |
| READ_ONLY | 11 | 只读查询/百科/计划 |
| HUMAN_REQUIRED | 11 | 风格冻结、主资产、备料决策、镜头确认、审片交付等必须人工 |
| NOT_SUPPORTED | 5 | 镜头逐字编辑、候选图批量生成、字幕逐句编辑、节点图数值改写、外部发布/暂停取消等，附工程原因 |
| 合计 | 41 | 逐页面操作矩阵（覆盖导演模式、素材中心、设置、成片库、全局） |

后端共 455 条路由（装饰器统计）。覆盖率不是「全部可代理」的同义词：未列入矩阵的操作不计入覆盖率，NOT_SUPPORTED 项不提供假动作。

## 4. 用户操作链路

```
自然语言 / 失败卡片
→ IntentRouter（百科 / 诊断 / 操作）
→ EvidenceSnapshot（既有裁决 + 台账）
→ ProjectKnowledgeIndex（版本绑定）
→ OfficialDocsIndex（参考 + H3 边界）
→ BoundedPlanner（≤8 步、只读）
→ CapabilityRegistry（action_id + schema + 原执行者）
→ 预览（差异/资源/门禁）→ 用户确认
→ 原服务/原 Worker/原 Run 引擎
→ 回读原任务/原裁决 → 成功 / 失败 / 待核对
→ 无法处理：受控脱敏 MD
```

- 重复点击、刷新、网络重试由 `request_key` + `attempt_no` + `atomic claim` + `target_fingerprint` 挡住。
- UNKNOWN 外部结果不自动重发；开拍前占用判定仍 fail-closed。
- 执行器拒绝时，动作进入 `REFUSED` 并保存原错误码；用户可在原页面重新预览。

## 5. 安全边界

- 运行时工具注册表不存在 shell / Python exec / Git / pip / 源码或节点源码写入 / 任意 HTTP / 任意文件路径。
- 报告只能写入 `<data_dir>/support-reports`，文件名白名单，24 小时指纹去重。
- 官方文档只保留自编摘要与链接；未核实许可前不整库复制，也不打包模型权重。
- 外部文本为 `REFERENCE_ONLY`；注入指令会让动作/知识条目失去执行意义。
- 未替用户确认风格冻结、主资产、备料、镜头确认与最终交付。

## 6. 验证结果摘要

- 后端定向：`tests/test_support_agent.py` 55 项 + `tests/test_support_copilot_v2.py` 24 项全部通过。
- 后端八分片全量：共 **2998** 项参与，**8/8 退出码 0**（分片 371 / 369 / 383 / 497 / 326 / 355 / 402 / 295）。
- 前端：79 个测试文件 / **407 项** Vitest 全部通过；`tsc -b` 与 `vite build` 通过（仅既有 chunk>500kB 提示）。
- Ruff：`shine`、`tests`、`scripts` 全部通过。
- 迁移：V2 未新增迁移；`f180f0010004` 空库升级成功，旧库/Windows 迁移测试继续通过。
- 真实硬件：Windows 真机、真实 ComfyUI/GPU、真实 H3、安装包实机、启动器离线诊断（S29）仍为 NOT_RUN/BLOCKED，未伪造通过。

## 7. 明确未完成 / 边界

1. 镜头逐字改写、字幕逐句编辑、候选图批量生成、暂停/取消生产：API 存在，但缺少稳定的自然语言合同与回读验证，V2 标记 NOT_SUPPORTED。
2. 付费预算：目录标注 paid，复用既有 LLM/任务预算与 completion cap；未新增货币额度系统。
3. 知识索引的提交号需在发布构建时由 `generate_copilot_knowledge.py` 重新生成；未记录提交号时状态为 UNKNOWN，回答会明确提示。
4. S29 启动器离线诊断未实现，本轮未重构启动器。
5. Windows/ComfyUI/H3 真机验收未执行。
