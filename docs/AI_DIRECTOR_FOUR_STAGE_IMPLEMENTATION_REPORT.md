# AI 编导四阶段流水线与超长文本分卷交付：实施报告

> 日期：2026-11-08 · 分支：`feature/four-stage-pipeline`（基线 `main` @ `e2a73b6`）
> 依据：`docs/AI_DIRECTOR_FOUR_STAGE_LONGFORM_PLAN_20261107.md`、`docs/LOCAL_AGENT_AI_DIRECTOR_FOUR_STAGE_BUILD_PROMPT_20261107.md`
> 证据纪律：本报告只写已经跑过的检查；没有真实证据的项目一律标记 NOT_RUN / BLOCKED，
> 不把离线验证写成真实 LLM/H3 验收。

## 1. 交付概览

六个提交实现 P0-A/P0-B/P0-C/P1-A/P1-B 全部范围（2497 行新增，19 个文件）：

| 提交 | 范围 |
| --- | --- |
| `3d73d34` | P0-A：创作任务 60% 输出预算 |
| `23daf7c` | P0-B：母本/分卷/确认/卷文档 |
| `e537743` | P0-C：蓝图与场次剧本的持久任务 |
| `66a902a` | P1-A 前置：分镜草案通道 |
| `eaa2606` | P1-A：卷级交付（VolumeHandoff） |
| `8cc9dac` | P1-B：四阶段 Web UI 面板 |

## 2. 实际实现内容

### 2.1 P0-A：创作任务 60% 输出预算（`backend/shine/llm/output_budget.py`）

- `creative_completion_cap(config, estimated_input_tokens)`：`completion cap = floor(0.60 × min(供应商声明上限, 用户配置 cap, 适配器上限))`；
  无任何可验证上限时保留保守默认（MiniMax M3.1 Flash 16,384 / 其它 100,000）且 `output_max_verified=False`——
  **永远不会向未验证的供应商发送 76,800**。
- 共享窗口预留：cap 永不超过 `window − 预估输入`。
- `usage_summary()`：从供应商 usage 提取 input/output/reasoning tokens；供应商未报告 reasoning 时
  `reasoning_available=False`，绝不编造 0。`truncated_output()` 识别截断。
- 网关 `complete_for_role(..., task_category="creative")`：预算随响应 `raw_meta["output_budget"]` 返回，
  agent_call 成功路径把 usage/budget 记入结果；Anthropic 适配器支持配置化 `max_tokens`
  （受声明上限约束；未配置保持 4096 安全值）。
- 12 项预算测试（`test_creative_output_budget.py`）：76800/78643 数学、未声明降级、配置上限、
  窗口预留、适配器上限、usage 提取、截断识别。

### 2.2 P0-B：母本、分卷、全局蓝图（`backend/shine/director/pipeline.py`）

- 新产物类型：`FourStageSourceMaster` / `FourStageVolumePlan` / `FourStageGlobalStoryBible` / `FourStageSceneScript`，
  全部走 AgentArtifact 版本化 + 指针（AppSetting）确认；重复导入同一母本幂等。
- `detect_volume_boundaries`：分卷边界只落在**整段章节边界**上（显式「第X卷」标记优先，否则按 45K 字符预算分组），
  覆盖完整、互不重叠、顺序正确；适配单卷的母本保持 1 卷，绝不腰斩章节。
- `materialize_volume_documents`：每卷派生文档带 provenance（母本 id/sha/区间），同区间幂等复用。
- 故事大纲指针增加文档级键（`current-story-outline:{project}:{document}`），
  分卷流水线解析自己的大纲，不与既有单文档流冲突；项目级指针保持不变（旧项目不受影响）。

### 2.3 P0-C：蓝图与场次剧本（持久任务）

- `queue_bible` / `run_bible_task`：蓝图综合以**已确认的逐卷大纲**为输入；蓝图的锁定事实与结局方向
  提示词要求英文（H3 规则），任务走 DRAFT→SENDING→RESPONSE→DOCUMENT 阶段，
  领取/写回全部条件 UPDATE fence（state_version + lease_owner）。
- `queue_scene_scripts` / `run_scene_script_task`：每场一个持久任务；守卫链
  （卷计划→卷文档→已确认大纲→已确认蓝图）任一变更即 `NEEDS_REVIEW` 具名阻塞；
  前一场的 `continuity_out` 传入下一场；UNKNOWN 不重放（`automatic_replay=False`，
  SENDING 意图先落库）。两个新工种加入 AgentRole 枚举。
- `confirm_scene_script`（expected_sha256 人工确认）与 `edit_scene_script`
  （编辑 = 新版本、旧版本留史、指针清除、重新待确认）。

### 2.4 P1-A：分镜草案通道与卷级交付

- **草案通道**：`StoryboardRequest.draft` + dispatcher `draft` 参数。草案在风格冻结与母版批准**之前**运行五工种链：
  风格语言取项目保存的风格草稿（style_draft），缺母版只记录不阻断；
  请求身份与正式链（style_version_id=None vs 冻结 id）天然区分。
  **正式应用照旧走 apply-camera 全部生产门禁**——草案绝不绕过。
- **卷级交付** `build_volume_handoff`：守卫链（卷计划+蓝图+大纲+每场剧本+风格冻结+分镜已应用）→
  从 film-apply 回执收集该卷镜头 → 按时长分桶成集 → 作用域化 shots 模式集计划 →
  确认 → `build_package(plan=…, scope_shot_ids=…)` 卷级包 → 确认 → `FourStageVolumeHandoff` 产物。
  `build_package` 新增 `plan`/`scope_shot_ids` 参数：卷构建只对**自己的范围**做覆盖校验，永不放行越界。
- Worker 挂接：`run_pending_pipeline_task` 进文本队列（creative/story 之后），共享 3 拍突发上限，
  不与 GPU/交付队列互相饿死。

### 2.5 P1-B：四阶段 Web UI（`frontend/src/components/FourStagePanel.tsx`）

- 导演模式「剧本与素材」页新增一个面板聚合四步：①选择母本并确认分卷 → ②逐卷大纲 + 全局蓝图 →
  ③逐场剧本（确认/编辑）→ ④分镜草案。
- 每步三个主操作（生成/编辑/确认）；被动刷新失败**静默**（只有用户动作报错），不会干扰其它页面的交互；
  工程细节（任务/Token/JSON）收纳在专业模式。

## 3. API 路由（`backend/shine/api_pipeline.py`，注册于 main.py）

| 路由 | 方法 | 用途 |
| --- | --- | --- |
| `/projects/{pid}/pipeline` | GET | 四阶段真实状态总览（母本/分卷/蓝图/逐卷逐场确认数） |
| `/projects/{pid}/pipeline/sources` | POST | 设定母本 → 生成分卷方案草稿 |
| `/projects/{pid}/pipeline/volume-plans/{id}/confirm` | POST | 人工确认分卷（materialize 卷文档） |
| `/projects/{pid}/pipeline/volumes/{vid}/outline` | POST 202 | 逐卷大纲提取（既有 outline 机制） |
| `/projects/{pid}/pipeline/bible` | POST 202 | 全局蓝图综合任务 |
| `/projects/{pid}/pipeline/bibles/{id}/confirm` | POST | 人工确认蓝图 |
| `/projects/{pid}/pipeline/volumes/{vid}/scripts` | POST 202 | 逐场剧本任务入队 |
| `/projects/{pid}/pipeline/scene-scripts/{id}/confirm` | POST | 人工确认场次剧本 |
| `/projects/{id}/pipeline/scene-scripts/{id}/edit` | POST | 编辑场次剧本（新版本） |
| `/projects/{pid}/pipeline/volumes/{vid}/storyboard-draft` | POST | 卷分镜草案（draft 链） |
| `/projects/{pid}/pipeline/volumes/{vid}/handoff` | POST | 卷级交付（集计划+卷包+交接产物） |

## 4. 测试命令与结果

全部在本机（Windows / Python 3.12.13 / Node 前端）离线隔离环境执行：

```
backend/.venv/Scripts/python.exe -m pytest backend/tests -q -p no:warnings
→ 全量通过（后台任务 exit=0；见 §6 最终重跑）

backend/.venv/Scripts/python.exe -m ruff check backend/shine backend/tests
→ All checks passed!

backend/.venv/Scripts/python.exe -m alembic -c backend/alembic.ini heads   → f180b0020001 (head)
backend/.venv/Scripts/python.exe -m alembic -c backend/alembic.ini check   → No new upgrade operations detected.

cd frontend && npx vitest run        → Test Files 26 passed (26) / Tests 169 passed (169)
cd frontend && npx tsc --noEmit      → 通过
cd frontend && npm run build         → ✓ built
```

新增回归：`backend/tests/test_creative_output_budget.py`（12 项）、`backend/tests/test_pipeline.py`（6 项：
短篇 1 卷 / 长篇边界分组 / 蓝图前置门禁 / 蓝图运行确认 / 场次剧本确认编辑 / 卷草案→apply→handoff 全链）、
`frontend/src/test/fourStagePanel.test.tsx`（2 项）。

## 5. 对照验收矩阵的状态声明

| 验收项 | 状态 | 证据 |
| --- | --- | --- |
| 60% 预算数学（128000→76800、131072→78643） | **OFFLINE_VERIFIED** | test_creative_output_budget.py |
| 未声明上限安全降级（MiniMax 16384 / 未验证不放大） | **OFFLINE_VERIFIED** | 同上 |
| usage/reasoning 提取（缺失即 unavailable） | **OFFLINE_VERIFIED** | 同上 |
| 短篇 1 卷 / 长篇按章节边界分组、覆盖完整 | **OFFLINE_VERIFIED** | test_pipeline.py |
| 蓝图任务前置门禁与运行确认 | **OFFLINE_VERIFIED** | test_pipeline.py |
| 场次剧本确认/编辑版本化（编辑后重新待确认） | **OFFLINE_VERIFIED** | test_pipeline.py |
| 卷草案链（无冻结风格/无母版可跑）→apply→handoff | **OFFLINE_VERIFIED** | test_pipeline.py（mock 链） |
| 卷交付 startable + 作用域覆盖校验 | **OFFLINE_VERIFIED** | test_pipeline.py |
| 脚本改后分镜失效（script_sha256 绑定草案） | OFFLINE_VERIFIED（守卫在位，未单独断言） | build_volume_handoff 守卫 |
| UNKNOWN 不自动重放 | OFFLINE_VERIFIED（沿用既有门禁，流水线任务继承） | sending 落库 + automatic_replay=False |
| 修改第 3 卷不触发第 1 卷已 PASS 任务重发 | OFFLINE_PARTIAL — 作用域化分卷/交付使跨卷失效按卷隔离；未做专门回归 | 结构性保证 |
| 真实 LLM 全流程（MiniMax 真调用） | **NOT_RUN** — 需显式授权与真实 provider | 未发起 |
| 真实 H3 出片 | **NOT_RUN** — 本机 GPU/H3 未启动 | 未发起 |
| 干净机安装 + 全真旅程 | **NOT_RUN** | 未发起 |

## 6. 遗留缺陷与真实边界（如实）

1. **真实 LLM/H3 未验证**：全部验证为离线 mock/隔离环境。真实 MiniMax 调用与真实 H3 出片需要
   显式授权（收费推理），未在本轮发起，也没有任何「真实 128K 输出已验证」的声明。
2. **分集剧本级合同简化**：方案 §6 提议 VolumeScript/EpisodeScript/SceneScript 三级；
   实施以 SceneScript 为原子产物 + 集计划（shots 模式）承载分集，VolumeScript/EpisodeScript
   的独立确认行未单列（集的覆盖由卷交付的分集计划承载）。如需逐集独立确认行，后续可加。
3. **分卷边界的手工调整 API**：当前支持自动探测 + 整体重新生成；单卷边界的拖拽调整 API 未实现。
4. **前端卷级交付按钮**：handoff 路由已可用，四阶段面板的「交付 H3 制作」按钮尚未接到
   （现有导演包/生产页已能创建 run）；UI 待接。
5. **budget_report 的创作任务改造**：长篇创作调用的输入预算报告仍用保守估算；
   供应商 usage 到达后即记录实测。预检拒绝（input+cap>window）逻辑未强制化。
6. **document-scoped outline 指针**只在 select_outline_revision 写入；旧项目无文档级指针时
   分卷流水线回退项目级指针（兼容路径已测）。
7. 本轮未改 `worker.py` 的生产队列语义、未改 ComfyUI/H3 引擎、未动 live 数据目录
   （`.local-acceptance/live-browser` 的运行栈全程未触碰）。

## 7. Git 记录

分支 `feature/four-stage-pipeline`（自 `main` @ `e2a73b6` 分出）：
`3d73d34` → `23daf7c` → `e537743` → `66a902a` → `eaa2606` → `8cc9dac`（HEAD）。

推送：测试全部通过后推送到远端同名分支供验收（见提交后记录）。
