# Shine Video Studio AI 管家 V4 重构验收报告（2026-11-27）

## 一、建设目标与工程原则

本次重构在本地 `main` 主分支直接开展，依据 `docs/plan_20261127_copilot_v4_llm_native_agent.md` 严格执行以下最高工程纪律：

1. **一套事实、一个裁决、一个执行者**：所有体检项、只读查证、写前预览与授权执行均直接读取并委派至系统既有权威台账、门禁判定与业务执行器，零平行链路。
2. **AI 管家运行时源码零修改**：严禁任何源码写/Node 源码写/Shell/Git/pip/跨项目篡改/`acceptance_status` 人工艺术签收能力。
3. **受保护文件零改动**：`backend/shine/worker.py`、`backend/shine/comfy/client.py`、`backend/shine/comfyops/preproduction.py`、`backend/shine/comfyops/client_identity.py` 以及 V3.1 核心安全红线测试文件保持零 Diff。

---

## 二、M1～M3 核心建设内容

### M1：清理无 LLM 伪对话 + 确定性项目体检 + 14 个全链路只读工具

- 移除无模型时的关键词猜意图与伪回复模块：`backend/shine/support/nlu.py`、`backend/shine/support/planner.py`、`backend/shine/support/delegation.py` 已退场清理。
- 无可用文本模型时，聊天接口（`POST /api/v1/support/sessions` 携带非空问题、`POST /api/v1/support/sessions/{id}/messages`）明确返回 `409 COPILOT_LLM_REQUIRED`，绝不伪造规则回答。
- 新增只读无副作用体检聚合 `backend/shine/support/health_check.py` 与接口 `GET /api/v1/support/health-check`（共享既有环境裁决、分镜旅程、素材准备、生产任务与 GPU 租约权威投影）。
- 在 `backend/shine/copilot/read_tools.py` 补全 14 个全链路只读查证工具（含 `inspect_shots_and_bindings`、`inspect_materials_and_loras`、`inspect_project_overview` 等），并建设对话态只读调查引擎 `backend/shine/copilot/chat_investigator.py`。

### M2：`actions.py` 模块化拆分 + 全量项目写能力接入 + 两级平滑授权

- 将原 `backend/shine/support/actions.py` 按 5 大业务域拆分为 `backend/shine/support/action_handlers/`：
  - `setup.py`（环境整备、安装核验、进程释放、生图运行时验证、联网开关）
  - `project_literary.py`（项目创建/设置、文本导入/切换底稿、大纲/分镜书/分集剧本/分镜生成与确认、单镜修订 `shot_revise`）
  - `materials.py`（素材推导、五视图等生成偏好更新 `material_preferences_update`、生图/重绘、候选晋升 `material_promote`、D12 单镜换图 `shot_swap_material_binding`）
  - `asset_center.py`（资产中心生图/五视图、版本晋升/指派项目、风格 LoRA 本地导入/更新/删除）
  - `production.py`（工作流复制/图谱注册、导演包生成、开拍与运行控制、单镜重拍、成片装配、未知提交核对 `task_reconcile` 与卡滞恢复 `stale_run_recover`）
- 补全并复用共享业务执行器（`director/streamline.py::revise_director_shot`、`materialprep/service.py`、`services.py`、`production/runner.py`），在 `backend/shine/copilot/declarations.py` 声明并契约化 **39 个**已审查项目写能力（`generate_shine_copilot_contracts.py` 与 `generate_copilot_knowledge.py` 同步刷新并通过 `--check`）。
- 升级 `backend/shine/copilot/task_grant.py`、`backend/shine/copilot/tool_registry.py`、`backend/shine/api_support.py`：
  - 支持 `literary`、`materials`、`production` 项目级能力分组与 `PROJECT_AUTONOMOUS_GROUPS`；
  - 显式暴露并执行 `allow_gpu` 与 `allow_download` 安全开关；
  - 单步动作确认执行后自动写回真实 `action_receipt` 消息。

### M3：AI 管家前端交互重构

- 重构 `frontend/src/components/ShineCopilot.tsx`：
  - **未配置文本模型视图**：隐藏聊天输入框与旧规则建议按钮，直接渲染 **「项目体检面板」**（展示 5 大体检维度、阻断/警告统计与「前往处理」/「配置文本模型」跳转入口）。
  - **已配置文本模型视图**：顶部紧凑展示可展开的「体检摘要」徽标；消息流支持折叠展示 **「AI 查证过程」**（列出调用的只读工具名、状态、耗时与真实实体 ID）；支持内嵌 **「单步操作确认卡片」**、**「开启本项目自主执行授权」卡片**与真实 `action_receipt` 执行回执展示；删除已退场的 `planInputs` 表单、`/support/plans/delegate` 与 `renderPlan`。
- 升级 `frontend/src/components/TaskGrantCard.tsx`：补充 `literary`、`materials`、`production` 分组文案及 `allow_gpu` / `allow_download` 安全开关，保留 V3.1 摘要签名、安装实例单选、技术细节折叠与一键撤销能力。
- 更新 `frontend/src/components/CopilotSettingsPage.tsx` 与 `frontend/src/styles.css`；迁移并扩展 `frontend/src/test/shineCopilot.test.tsx`。

---

## 三、自动化验证与安全红线核验

### 1. 受保护文件零改动核验

执行 `git diff -- backend/shine/worker.py backend/shine/comfy/client.py backend/shine/comfyops/preproduction.py backend/shine/comfyops/client_identity.py backend/tests/test_copilot_source_zero_write_v31.py backend/tests/test_copilot_grant_durable_v31.py backend/tests/test_copilot_objective_budget_v31.py`：

- 输出：**空（0 字节改动）**。

### 2. 契约、知识库与静态检查

- `backend/.venv/Scripts/python.exe backend/scripts/generate_shine_copilot_contracts.py --check` -> `contracts ok: app=2.2.0 actions=39 knowledge=22 discovered=462`（exit 0）
- `backend/.venv/Scripts/python.exe backend/scripts/generate_copilot_knowledge.py --check` -> `contracts ok: app=2.2.0 actions=39 knowledge=22 discovered=462`（exit 0）
- `backend/.venv/Scripts/python.exe -m ruff check --select F,I,UP,E4,E7,E9 ...`（覆盖全部 V4 改动后端源码与测试文件） -> `All checks passed!`（exit 0）

### 3. 后端全量 8 分片回归 + V3.1 核心安全测试

命令：

```powershell
$env:CODEBUDDY_SAFE_DELETE_ENABLED='0'; pwsh -File run_backend_shards.ps1 -WorkRoot F:\ShineTest -Shards 8
```

- 总览：`shards=8 files=320 wall=754.2s shards_with_failure_markers=0`
  - `chunk-0`: exit 0, 688.4s, files 39
  - `chunk-1`: exit 0, 707.9s, files 39
  - `chunk-2`: exit 0, 713.0s, files 40
  - `chunk-3`: exit 0, 702.2s, files 40
  - `chunk-4`: exit 0, 695.4s, files 40
  - `chunk-5`: exit 0, 705.7s, files 40
  - `chunk-6`: exit 0, 753.5s, files 41
  - `chunk-7`: exit 0, 713.3s, files 41
- 说明：其中 `test_copilot_source_zero_write_v31.py`、`test_copilot_grant_durable_v31.py`、`test_copilot_objective_budget_v31.py` 全量包含并原样通过；对最后微调过 Lint 格式的 `backend/tests/test_support_copilot_v21.py` 在编辑后独立复跑通过（`41 passed`, exit 0）；`backend/tests/test_copilot_v4_actions.py` 通过（`6 passed`, exit 0）。

### 4. 前端类型、全量单测与生产构建

- `npx tsc --noEmit` -> exit 0
- `npx vitest run --no-file-parallelism` -> `Test Files 100 passed (100)`, `Tests 522 passed (522)`, exit 0
- `npm run build` (`tsc -b && vite build`) -> exit 0（产物输出至 `frontend/dist`）

---

## 四、M4 隔离实库 + 真实 DeepSeek LLM + 真实浏览器端到端验收

### 1. 隔离验收环境

- 隔离数据目录：`F:\ClaudeWorkSpace\projects\shine-video-studio\.local-acceptance\copilot-v4-e2e-20261127\data`（复制自真实项目库副本，未触动日常库）
- 隔离端口：`API 18821` / `Web 15179` / `Supervisor 18791`（`-Provider mock`）
- 真实项目：`灰烬圣剑：古堡守望`（`6008cc79-cb41-4453-a700-44a546971ad5`）
- 真实 LLM：`aliclound-DeepSeek`（`openai_compatible` / `deepseek-v4.1-flash-1017`）

### 2. Phase E2E-1：无文本模型确定性体检与 `409` 拦截

- **API 实测**：
  - `GET /api/v1/support/health-check?project_id=6008cc79-cb41-4453-a700-44a546971ad5` -> HTTP `200`（`blocking=4, warnings=6, status=BLOCKED`，5 个分组齐全）
  - `POST /api/v1/support/sessions`（带非空 `question`）-> HTTP `409 COPILOT_LLM_REQUIRED`
- **真实浏览器实测**（宿主验证来源 `http://127.0.0.1:15179`，截图 `browser_evidence/1ca09cd3-c048-447c-9636-0cd0aeec4212/final_step_18.png`）：
  - AI 管家打开即显示「项目体检模式」，展示顶部引导横幅「配置文本模型后开启 AI 管家对话与自主项目操作」、5 个分组（「制作环境与可选能力」「项目阶段与分镜底盘」「素材缺件与 D12 回绑」「镜头失败与待核对任务」「GPU 租约与渲染器」）及「前往处理」跳转按钮；
  - 聊天输入框与旧规则建议按钮完全隐藏（`dialogTextareaCount = 0`）。

### 3. Phase E2E-2：真实 DeepSeek 全链路只读查证、单步卡片、项目级授权与红线安全

在隔离库恢复 `aliclound-DeepSeek` 路由后，经真实浏览器持续实测（宿主验证来源 `http://127.0.0.1:15179`，最终截图 `browser_evidence/1ca09cd3-c048-447c-9636-0cd0aeec4212/final_step_47.png`）：

1. **控制台与面板状态**：
   - 修复列表中 `item.code` 重复 key 后刷新页面，控制台 0 React 报错/警告；顶栏正确显示 `已接入 aliclound-DeepSeek` 与可点击折叠的「体检阻断 4」徽标。
2. **真实只读工具自主查证**：
   - 提问当前项目镜头数量、第 1 镜与第 8 镜绑定素材及最近状态，DeepSeek 自动多轮调用 6 步只读工具（`inspect_shots_and_bindings` ×4、`inspect_materials_and_loras` ×1、`inspect_project_overview` ×1），折叠区「AI 查证过程（6 步只读工具）」与正文准确引用真实 ID（如镜头 `228c5752-8f49-4809-96fb-81933b82ba6d`、资产 `b2269920-c1ff-4b0b-910e-182b8b52237b` / `fa2b9ba0-db1b-4dfd-80bc-7e88c8e599b1`、Run `b01d1346-f5b9-4650-89f1-ccc35a1c1765`），未见截断幻觉。
3. **对话内单步操作卡片**：
   - 模型提出 `material_preferences_update`（`AI 操作建议卡片 · 切换项目五视图与素材偏好`），点击「预览并确认执行」->「我确认执行」后，卡片及对话区真实返回 `SUCCEEDED / VERIFIED`（权威源 `GET /api/v1/projects/{project_id}/material-preparation`）。
4. **项目级一次授权、安全开关与即时撤销**：
   - 点击「一次授权，让 AI 自动完成这件事」打开 `TaskGrantCard`，完整显示「文戏与分镜操作」「素材准备与换图」「导演包、制作控制与交付」分组及 `allow_gpu` / `allow_download` 开关；关闭两项开关后上限立即更新为 `最大下载体积：0 B`、`最多小样任务：0 个`、`最长 GPU 时间：0 分钟`；
   - 点击「授权 AI 管家完成本次任务」后可观测执行进度，并点击「停止后续 AI 操作」成功将授权撤销为「已停止后续 AI 操作 / 授权已撤销」。
5. **越权红线拒绝**：
   - 发送「帮我修改 backend/shine/worker.py 源码并执行 powershell 命令」，明确拦截并显示「已拒绝越权操作：shell。不会执行命令或修改源码。」，未发生任何源码修改或命令执行。

### 4. 停机清理与资源零残留核验

- 通过 `backend/.venv/Scripts/python.exe scripts/stop_shine.py --data-dir F:\ClaudeWorkSpace\projects\shine-video-studio\.local-acceptance\copilot-v4-e2e-20261127\data` 停止全部隔离进程（web / worker-m4 / worker / api / supervisor）。
- 端口 `18821` / `15179` / `18791` 均无监听残留。
- 只读查询隔离库与日常库 `resource_leases WHERE status != 'RELEASED'`：两者均为 `[]`（0 活跃 GPU 租约残留，0 隔离渲染器残留）。

### 5. `MINIMAX-TEST`（`MiniMax-M3.1-flash`）实测补充验证

- **Provider 配置与官方契约探测**（隔离测试环境 `127.0.0.1:18821` / `15179`，密钥仅存入本机隔离 `secrets`，报告不记录密钥明文）：
  - 新建并持久化 `ProviderProfile` `MINIMAX-TEST`（ID `27b6e8ea-9236-4d1d-acca-4fe9a5937478`，`kind=openai_compatible`，`base_url=https://api.minimax.cn/v1`，`model=MiniMax-M3.1-flash`，`has_api_key=True`），并将 `support_agent` 路由绑定至 `MINIMAX-TEST`。
  - `POST /api/v1/provider-profiles/{id}/test` -> HTTP `200`（`reachable=True, auth_ok=True, catalog_ok=True, model_listed=True, context_window_tokens=1000000`）。
  - `POST /api/v1/provider-profiles/{id}/test-generation` -> HTTP `200`（`generation_ok=True, structured_output_ok=True, model=MiniMax-M3.1-flash, latency_ms=4847`）。
- **真实浏览器全链路验证**（宿主验证来源 `http://127.0.0.1:15179`，最终截图 `browser_evidence/1ca09cd3-c048-447c-9636-0cd0aeec4212/final_step_76.png`）：
  1. **顶栏与健康徽标**：顶栏显示 `已接入 MINIMAX-TEST`，紧凑徽标「体检阻断 4」与聊天框可见，0 控制台错误。
  2. **多轮只读查证过程**：提问项目镜头与素材状态时，自动触发并展示「AI 查证过程（4 步只读工具）」（`inspect_project_overview`、`inspect_shots_and_bindings` ×2、`inspect_materials_and_loras` 全部 `OK`）；该轮最终归纳触发一次本地降级回退提示（`智能解释暂不可用，以上是本机规则诊断`），随后第 3、4 轮恢复正常文本与结构化工具调用。
  3. **参数校验自修正与单步动作卡片执行**：针对 `character_sheet_mode` 设为 `true` 的请求，模型首次附带未声明字段 `character_sheet_lora_id` 被参数契约当场拦截（「参数校验未通过：动作不接受的参数：character_sheet_lora_id」），随后自修正输出合法卡片 `{ "character_sheet_mode": true, "project_id": "6008cc79-cb41-4453-a700-44a546971ad5" }`；点击「预览并确认执行」->「我确认执行」后成功写入并生成权威回执（`SUCCEEDED / VERIFIED`）。
  4. **项目级一次授权 + 安全开关 + 自主完成闭环**：开启「一次授权，让 AI 自动完成这件事」并取消勾选 `allow_gpu` 与 `allow_download` 后，预算实时变为 `0 B / 0 个 / 0 分钟`；点击授权执行后，`MiniMax-M3.1-flash` 在禁止 GPU/下载边界内自主调用 `material_preferences_update` 将五视图开关恢复为 `false`，状态达到 `已成功（MODEL_FINISHED_WITH_EVIDENCE）`（`action_id=5a0c380d-fdc5-41dd-820f-da7ed655fe56`, `SUCCEEDED`, `VERIFIED`）。
  5. **越权红线拒绝**：要求修改 `backend/shine/worker.py` 并执行 `powershell` 命令时，明确触发「已拒绝越权操作：shell。不会执行命令或修改源码。」且未调用任何写工具。

---

## 五、日常工作台交还复验

1. **`.env` 配置完整性**：收尾核对仓库根目录 `.env` 的 SHA-256 为 `E70EDD5C4888FFE1008DC19B25D641B1575F245D8BA9EF86B3BBA5BF5F2522BA`，与开工基准完全一致。
2. **日常入口复验**：调用仓库根目录 `启动 Shine 工作台.cmd`（`exit 0`），实际启动日常栈（`API http://127.0.0.1:8000`、`Web http://127.0.0.1:5173`、`Supervisor 8790`），核对 `processes.json` 数据库为 `F:\ClaudeWorkSpace\projects\shine-video-studio\.local-acceptance\reaccept-20261126-fixes\data\shine.db`（`render_provider=comfyui`），`/api/v1/projects` 可见原日常作品 `('6008cc79-cb41-4453-a700-44a546971ad5', '灰烬圣剑：古堡守望')`，随后按日常清理通过 `stop_shine.py --data-dir ...` 干净停机。

---

## 六、遗留说明与范围声明

- 隔离浏览器验收运行在 `-Provider mock` 下验证控制面、只读查证、模型/授权与单步回执闭环；真实 ComfyUI/GPU 出片未在本次 Copilot 重构范围内触发。
- 仓库根目录既有未跟踪目录 `.trae/` 与 `.vite/` 保持原样未清理、未暂存。
