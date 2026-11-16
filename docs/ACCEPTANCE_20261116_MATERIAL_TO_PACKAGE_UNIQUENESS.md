# 素材准备 → 导演包唯一链路修复 · 验收记录（2026-11-16）

> 分支 `fix/material-to-package-uniqueness`，基于 `origin/main@a2ea6af`（PR #53 合并后）。
> 开发 worktree `shine-video-studio-director-dev`，隔离数据目录
> `.local-acceptance/dev-20261115/data`，端口 API 8040 / Web 5210 / 监督 8791。
> 真机 RTX 5080 16GB，ComfyUI 收编自 `F:\AIGC2026`。

用户手动出片校验时报了四个缺陷，要求按「一套事实、一个裁决、一个执行者」修复。
逐条定位根因、修复、定向回归 + 真机浏览器复验如下。

## 缺陷 1 · 需求列表缺一键批量勾选处理方式

- **根因**：`ShotMaterialsPanel` 只有逐项下拉，没有批量入口。
- **修复**（`frontend/src/components/StreamlinePanels.tsx`）：新增批量工具栏——全选框、
  逐项勾选框、「批量处理方式」下拉（AI 制作 / 复用现有 / 跳过）、「应用到所选」、
  一键「全部 AI 制作」、「清空选择」。批量与逐项共用**同一份 `decisions`**，提交仍由
  `commit()` 唯一送出；不可用选项按真实事实降级（缺主素材的复用→制作，必需项的跳过→制作）。
- **真机**：素材页出现批量工具栏与逐项勾选框（快照 uid 30_115–30_125）。

## 缺陷 2 · 上传图片后需求列表塌缩只剩一条

- **根因**（后端）：`materialprep/service.py::confirm` 新建 plan 修订时**只把本次决策行**
  迁到新 plan（`row.plan_id = plan.id`），其余需求行滞留旧修订。需求端点读
  `current_material_plan`（最新修订）的行，于是单条提交（上传 / 重新生成走 `commitOne`）
  后清单只剩那一条。整批提交因送出全部行而不塌缩，单条提交必塌缩。
- **修复**：`confirm` 在迁移决策行后，把**源修订剩余的全部需求行**也随新修订迁移
  （保留身份、payload 与既有决策）。清单跨任意单条提交保持完整。
- **回归**：`backend/tests/test_director_material_commit.py::test_a_single_row_commit_keeps_the_whole_requirement_list`
  （单条提交后断言原需求集 ⊆ 当前清单；修复前必红）。
- **真机动态复现**：对「第6场：旧物修复铺」真实上传一张图 → 上传→确认→锁定→单条回绑
  全链跑通，清单仍 **9 项需求**（可复用 1→2），全部 9 行 intact，该行显示新缩略图并
  「已匹配，可复用」（快照 uid 31_111 / 31_299）。**不再塌缩**。验证后已把该行恢复为
  「跳过」、解锁测试素材，清单回到 9 项原状。

## 缺陷 3 · 送素材中心后无进度条；素材中心看不见项目任务

- **根因**：`ShotMaterialsPanel` 提交后只有一行文字回执，没有挂载生成台账；素材中心
  首页「最近任务」只读 localStorage 里工坊自建任务，导演素材批次（`MaterialRun`）不在其中。
- **修复**：
  - 后端新增只读 `GET /projects/{id}/material-preparation/generation-progress`
    （`service.generation_progress`），返回与素材页**同一份 `public_run` 投影**（production
    批次），不含会逐文件哈希的完成判定——进度条每 5s 轮询不重哈希素材库。这是同一台账的
    读投影，不是第二套判定链。
  - 新增共享组件 `frontend/src/components/MaterialGenerationProgress.tsx`：进度条
    （`role=progressbar`， settled/total）+ 候选/渲染中/排队/待核对/失败计数 + 正在处理条目
    + 逐项状态折叠 + 「去素材中心选定并采用 / 核对」入口；只在有批次在途时轮询，落定即停。
  - `ShotMaterialsPanel` 与素材中心 `HomePage` **都挂载这同一个组件**（同一事实、同一执行者
    Worker）。提交后 `refreshKey` bump 立即刷新。
- **回归**：`test_generation_progress_reads_the_same_run_ledger_as_the_material_page`
  （进度端点 run 序列 == 素材页 production runs）；前端 `materialToPackage.test.tsx`
  进度条 aria-valuenow / 候选计数 / 无批次不占位。
- **真机**：素材页与素材中心创作首页都显示「素材生成进度」两条真实进度条
  （候选 2/2 value=100、候选 1/3 · 待核对 2 value=100）与去选定/核对入口
  （快照 uid 29_82 / 32_22）。

## 缺陷 4 · 晋升确认后素材页无下一步通往导演包

- **根因**：素材页没有挂载 journey 的下一步；全局 hero 有引导，但页内无显式「下一步」。
- **修复**（`frontend/src/App.tsx`）：素材页新增由服务端 `journey.next_stage` 驱动的
  「下一步：{阶段}」卡片（`nextStage.page !== 'materials'` 时显示），点击跳转到对应阶段。
  **不前端硬编码就绪**——`assets.done` 与 `next_stage` 都来自 `/journey` 单一事实。
- **回归**：`materialToPackage.test.tsx`「分镜与素材已核对后，素材页给出『下一步：导演包』」。
- **真机**：journey `next_stage=package`、assets done，素材页渲染「下一步：导演包」按钮
  （快照 uid 29_310 / 31_321）。

## 缺陷 5 · 已确认素材，导演包仍报「缺必需素材」（确认链路不一致）

用户复验时报：已确认 3 个人物主素材，导演包仍「等待备料 · 缺 3 项必需素材」，勾选框禁用。

- **根因（多处事实碎片化）**：
  1. 素材中心晋升走 `POST /candidate-assets/{id}/promote`（`comfyops/assets.py::promote`），
     只创建主素材，**不把主素材采纳进需求行与镜头**；需求行仍 `strategy=generate`、
     `bound_asset_id=None`。
  2. 两条确认链路不一致：`/director/materials/commit` 调 `_bind_adopted_masters` 回绑镜头，
     而 `/material-preparation/confirm` 只调 `service.confirm`，**不回绑**。
  3. journey 的 `_shot_material_problems` 把「旁白」也算缺人物素材，而素材清单按
     `is_narrator_speaker` 排除旁白——两处人物需求规则不一致。
  素材清单读派生匹配（matched_master），journey/overview/导演包读 `shot.bindings`，
  主素材独立存在——一个事实被拆成三处状态。
- **修复（一个执行者）**：
  - 把镜头回绑逻辑移进 `materialprep/service.py::bind_adopted_masters_to_shots`，并在
    `service.confirm` 内调用——**所有确认路径**（director commit / material-prep confirm）
    都由这一个执行者回绑。`api_streamline._bind_adopted_masters` 改为委托它的幂等修复包装。
  - `promote` 创建主素材后，经候选 `provenance.item_id → MaterialItem.requirement_id`
    找到需求行，调 `bind_material_master` + `bind_adopted_masters_to_shots`——晋升即采纳。
  - `upload_requirement_master`（main.py）与 `merge_character`（api_material.py）同样补回绑。
  - journey `_shot_material_problems` 排除旁白，与素材清单 `_character_names` 同一规则；
    overview 复用同一函数，导演包页随之同源。
- **回归**：`test_material_preparation_confirm_binds_shots_like_director_commit`
  （material-prep confirm 回绑镜头）、`test_promoting_a_candidate_adopts_it_into_requirement_and_shots`
  （晋升候选→需求转复用+镜头回绑）。
- **真机**：对用户项目已晋升的 3 个主素材（沈砚/老陈/委托人）经产品确认端点采纳后，
  `bound_shots=8`，journey `material_missing_count=0`，overview 分集 `READY_TO_PACKAGE`，
  导演包页第1集由「等待备料 · 缺 3 项」变为「**可打包** · 23 镜」、勾选框可用、
  「确认拍摄方案并开始真实制作（1 集）」按钮启用（INT8 `available=True`）。
  一个状态通达全流程。**未替用户点开拍**（23 镜真实生产由用户掌控）。

## 自动化回归

- **前端**：`npx tsc --noEmit` 通过；`npx vitest run --no-file-parallelism`
  **79 文件 / 401 项全绿**（新增 `materialToPackage.test.tsx` 5 项）；`npm run build` 通过（1652 模块）。
- **后端定向**：`test_director_material_commit.py` 7 项全绿（含 2 条新反例）；
  素材相关 12 模块批跑 **EXIT=0、0 FAILED/ERROR，共 166 例**（`CODEBUDDY_SAFE_DELETE_ENABLED=0`、
  `env -u NO_PROXY`，仓库根）：four_stage_reacceptance 19 / director_streamline 26 /
  director_long_form 27 / director_material_commit 7 / material_derive 4 / asset_center 25 /
  material_prompt_carryover 1 / material_image_jobs 32 / material_scene_safety 12 /
  material_scenes 7 / material_manual_prompt 4 / material_budget_share 2。
  日志 `/tmp/material-regression.log`。
- **Ruff**：改动后端文件全部通过。

## 唯一性原则核对

- **一套事实**：生成进度只读 `public_run`（`MaterialRun` 台账）；就绪/下一步只读 `/journey`；
  需求清单只读当前 plan 修订的 `AssetRequirement` 行。
- **一个裁决**：素材完成判定仍是 `evaluate_materials` / journey，进度端点不做判定。
- **一个执行者**：出图仍由常驻 Worker 执行；前端只提交意图与显示进度。

## 边界声明

- 本轮改 `materialprep/service.py`（confirm  carry-forward）与 `api_material.py`（新增只读
  进度端点），**未改** `worker.py` / `preproduction.py` / handoff / quality 客户端，
  不触发 `client_review_identity` 重校准义务；历史 VLM 正负校准仍未完成，不声称已校准。
- 真机复验在用户正在使用的开发栈上进行（重启加载新后端，无运行中任务时执行）；缺陷 2
  的测试上传已恢复（第6场回「跳过」、测试素材解锁）。该项目留有 1 个未批准未锁定的
  74 字节占位图 `upload-test-scene.png`（无删除素材端点，未绑定任何镜头，无害）。
- A11 完整成片仍未产出（需人工确认锁定人物主素材 → 冻结导演包 → H3 渲染 → 后期 → 人工验收），
  本轮只修素材→导演包链路，不声称整片通过。