# 验收：素材绑定→整备→开拍唯一链路收敛（2026-11-16 第二轮）

工作副本：`shine-video-studio-director-dev`，分支 `fix/material-to-package-uniqueness`，
核验基线 `69793dd`。本轮按「**一套事实、一个裁决、一个执行者**」做减法，修复五个反例，
不新增平行检查/状态/入口。专业模式已下线，右上角只跳转基础配置，沿用该结构。

## 缺陷与修复

### 缺陷1：同一人物主素材 A→B，镜头同时保留 A、B

- **根因**：采纳执行者 `bind_adopted_masters_to_shots` 只「追加」确认的主素材；需求改选 B
  后镜头仍留 A，一个人物绑了两份主体参考。打包时另有 `apply_missing_character_bindings`
  自动补写人物绑定，是第二条写链路。
- **修复**：
  - `materialprep/service.py::bind_adopted_masters_to_shots` 改为**替换**该人物旧主体引用：
    找出 `role==entity`（或无 role 且 `asset.entity_id==entity`）的 subject 绑定，丢弃旧的、
    绑入 B，并同步 `shot.spec.required_asset_ids`（去掉被替换的 A，`set_shot_bindings`
    再合并 B）。幂等：已恰好是 B 时不动。
  - 保护收敛为一个共享判定 `services.py::protected_shot_ids`（冻结进确认导演包的镜头 +
    在途拍摄 `shot_ownership` + 未对账 UNKNOWN 提交）。`apply_missing_character_bindings`
    与 `bind_adopted_masters_to_shots` 共用它；另保留「当前修订已 PASSED 的镜头不动」。
  - **撤掉导演包打包时的自动补写**：`director/package.py::build_package` 删除
    `fill_missing_character_bindings` 参数与 `apply_missing_character_bindings` 调用；
    `api_director.py::PackageBody`、`streamline.py` 两处建包、前端
    `DirectorProductionRunPanel.buildPackage` 与 `api.ts::createDirectorPackage` 同步删除该字段。
    导演包读取素材确认流程已写好的 `shot.bindings`。
  - **保留 four-stage 卷交接自身的绑定合同**（`director/pipeline.py`）：该流程里用户直接
    `approve_lock` 主素材（不走导演素材页），交接时由 `apply_missing_character_bindings`
    补齐**缺位**主体；它只补缺、保护冻结/在途镜头，绝不替换素材流程已确认的绑定，
    因此不会 reintroduce A+B。既有 `test_volume_handoff_pins_a_character_confirmed_after_the_storyboard_landed`
    钉住这一合同，保持通过。
- **回归**：`test_director_material_commit.py::test_reconfirming_a_different_master_replaces_the_old_shot_reference`
  （A→B 后镜头只绑 B、`required_asset_ids` 同步）、`test_binding_adoption_never_revises_a_frozen_shot`
  （冻结镜头保留打包时绑定，`bound_shots==0`）。

### 缺陷2：指定目录搜到改名权重，整备仍计划下载

- **根因**：缺件判定有两套——`reconcile/inspect` 按尺寸+哈希找到改名权重并登记为
  `FILES_VERIFIED_PENDING_RUNTIME`，但 `capability_report.missing_files` 仍按**文件名**
  （`model_file_presence`）另算一套，把已登记的改名权重当成缺件 → 下载步计划重下。
  前端基础配置又自查 `installations[0]` 并另算缺件/下载量。
- **修复**：
  - `setup_summary.py::capability_report` 收敛到同一份核验事实：已被 reconcile 登记核验的
    组件（`_registered_components`）其字节视为已在盘上，`missing`/`missing_bytes`/`FILES_FOUND`/
    `FILES_VERIFIED` 都据此计算。下载步（`_step_download`）与基础配置读同一个结论。
  - 前端 `SetupCenter.tsx`：缺件与下载量改读后端 `summary.capabilities[key].facts/missing_bytes`
    （`capFacts`/`capDownloadBytes`），删除自查第一套 ComfyUI 的出图二次 inspect（`imageScan`）
    与 `IMAGE_CATALOG/IMAGE_COMPONENTS` 常量；扫描只留作「检测明细」只读展示。
- **回归**：`test_variant_aware_summary.py::test_a_renamed_weight_verified_by_reconcile_is_not_downloaded_again`
  （改名权重经 reconcile 登记后 `missing_files==[]`、`missing_bytes==0`、FILES_FOUND/VERIFIED True）。

### 缺陷3：确认落库后、入队前中断，重试报 MATERIAL_PLAN_STALE

- **根因**：`service.confirm` 与 `service.create_batch` 各自 `db.commit()`。确认先单独落库
  （需求行迁移到新修订），入队前中断 → 批次与回执都没有；重试对着已迁移的需求行报
  `MATERIAL_PLAN_STALE`，库里永远没有生成任务。
- **修复**：`confirm`/`create_batch` 增加 `commit=True` 开关；`api_streamline.py::commit_director_materials`
  以 `commit=False` 把**确认 + 入队 + 回执**放进同一 SQLite 事务，末尾一次 `db.commit()`，
  任一步 `HTTPException` 整体 `db.rollback()`。沿用现有 `request_key` 请求标识与 Worker。
  提交前中断什么都不留 → 同一意图干净重提；提交后丢响应 → `_material_receipt` 取回原回执；
  全程最多一个批次。外部生成结果不明仍走现有 `reconcile_run`（不重投）。
- **回归**：`test_director_material_commit.py::test_an_interrupt_before_commit_leaves_nothing_and_resubmits_cleanly`
  （monkeypatch 入队中断 → 计划修订数/批次/回执均不变；重试 201、再试 replayed、批次恰 1）。

### 缺陷4：卡住一键整备的无关条件

- **根因(a)**：`_step_instance` 把 `None` 项目转成字符串 `"None"`，`next_instance_plan`
  的项目门禁对着不存在的作品报 `Project not found`；`_step_recipe` 同样 `str(None)`。
- **根因(b)**：`_step_download` 把可选审片（vlm/asr）下载的 PENDING/FAILED 当成整个下载步
  的结论，H3 文件齐备也被无关审片下载卡死。
- **修复**：
  - 机器级依赖在建作品前就能整备：`InstanceOperation.project_id: str|None`、
    `instances.plan_operation` 与 `deployment.next_instance_plan`/`generate_configuration`
    对 `None` 放行（`_require_project` 本就容忍 None）；`_step_instance`/`_step_recipe`
    直接传 `task.get("project_id")`。作品级步骤（PROFILES/VALIDATE/IMAGE_SAMPLES）无项目时如实跳过。
  - 审片沿用自己的安装记录：`_step_download` 以 `review_caps={vlm,asr}` 区分，审片下载中/失败/
    计划被拒只记录 `review_state`，不卡生产链；生产链只按自己的实际依赖推进，仅生产下载
    排队时才返回 PENDING。已 `done` 的能力不再回查安装记录。
- **回归**：`test_setup_preparation_instance.py::test_instance_step_on_empty_database_passes_no_project`、
  `test_machine_level_instance_plan_does_not_require_a_project`、
  `test_optional_review_download_does_not_block_the_production_chain`（ASR FAILED/INSTALLING 均 DONE）。

### 缺陷5：页面直连执行的旁路

- **根因**：基础配置里补工具（`repairTools` 长请求）、出图验证（`testImage` 直接启动渲染器+
  验证小样）是页面直连执行；H3「真实试镜一次」对 H3 是空点（`onTrial` 只处理 image）。
- **修复**：
  - 补工具折进一键整备链：`setup_preparation.py` 新增 `TOOL_REPAIR` 步（复用既有
    `environment_repair.execute_repair`，工具达标跳过），与下载/实例/核验共用一个后台任务。
    前端「自动补装」按钮改为「交给一键准备补装」→ `prepare()`，删除 `repairTools`。
  - 出图验证交给后台：删除 `testImage` 与 `imageTesting` 状态，「测试出图」按钮改为
    「交给后台验证出图」→ `prepare(['image'])`（IMAGE_VERIFY/IMAGE_SAMPLES 步）。
  - 撤掉「真实试镜一次」：`SetupEnvironmentStatus.tsx` 删除该按钮与 `onTrial` prop，
    UNVERIFIED 运行态落到「一键绑定渲染器并核对运行合同」→ `onPrepare`；TRIAL 动作收敛到
    PREPARE_ENVIRONMENT。首次真实 H3 运行走现有「开拍」。
  - 保留目录选择作为检索输入；素材中心正常生成/创作操作不变。
- **回归**：`test_setup_preparation_chain.py::test_tool_repair_is_one_step_of_the_single_preparation_task`；
  前端 `setupCenter.test.tsx`「不再有空点的真实试镜一次」「出图验证交给后台一键准备」
  （断言无 `image-runtime/start`、`image-runtime/validate` 直连，prepare/execute capabilities=['image']）。

## 删掉/合并的重复路径

| 重复路径 | 收敛到 |
| --- | --- |
| 打包自动补写人物绑定（build_package fill） | 删除；导演包读 `shot.bindings`（素材确认流程写入） |
| 采纳只追加不替换（A+B） | `bind_adopted_masters_to_shots` 替换 + 同步 required_asset_ids |
| 冻结/在途保护两套 | `services.protected_shot_ids` 一个判定，两处共用 |
| 缺件按文件名另算一套 | `capability_report` 消费 reconcile 登记核验事实 |
| 前端自查 installations[0] + 出图二次 inspect + 另算下载量 | 读 `summary.capabilities`；扫描仅只读展示 |
| 确认/入队/回执三次 commit | 同一 SQLite 事务一次 commit |
| 实例/配置把 None 项目转 "None" | 机器级 None 放行 |
| 审片下载卡生产链 | 审片沿用自己安装记录，生产链按自身依赖推进 |
| 补工具/出图验证页面直连 + H3 空点试镜 | 一键整备链 TOOL_REPAIR/IMAGE_VERIFY + 开拍 |

## 回归执行

- 后端定向：`test_director_material_commit`（12）、`test_one_click_batch_admission`（6）、
  `test_shoot_ownership`（6）、`test_variant_aware_summary`（5）、`test_setup_preparation_instance`（11）、
  `test_setup_preparation_chain`（11）、`test_relayvsr_and_profiles`（6）、
  `test_four_stage_reacceptance`（19）、`test_director_streamline` + `test_director_execution_contract` 均通过。
- 后端全量：按 `AGENTS.md` 8 分片（`split -n r/8` 按文件均分，`CODEBUDDY_SAFE_DELETE_ENABLED=0`，
  `env -u NO_PROXY -u no_proxy`，仓库根运行）。首轮三处失败均已修复并重跑对应分片确认：
  1. `test_relayvsr_preparation_only_reconciles_and_downloads`：TOOL_REPAIR 入链后的步骤序列断言更新。
  2. `test_volume_handoff_pins_a_character_confirmed_after_the_storyboard_landed`：恢复 four-stage
     卷交接自身的绑定合同（只补缺、不替换已确认绑定），保持既有流程通过。
  3. `test_prepare_execute_is_idempotent_and_survives_the_browser`：整备首步 current 断言更新为 TOOL_REPAIR。
  修复后 ab/ad 分片重跑 exit=0，其余六分片首轮即 exit=0。**8 分片全部 exit=0、零 FAILED/ERROR，
  合计 2930 用例**。
- 前端：`npx vitest run --no-file-parallelism` 79 文件 / 403 项通过；`tsc --noEmit` 干净；
  `npm run build` 通过。Ruff 改动文件全部通过。
- **pytest 导入的是 worktree 代码**（`backend/tests` 为包，rootdir 注入 worktree/backend，
  已用 `shine.__file__` 含 `director-dev` 断言确证）；venv 是指向主仓的符号链接，运行栈默认
  导入主仓 shine，浏览器复验因此以 `PYTHONPATH=worktree/backend` 启动隔离实例。

## 浏览器复验（隔离实例，PYTHONPATH=worktree/backend）

隔离数据目录 `.local-acceptance/eval-uniqueness-1116`，API 8050 / Web 5220，mock。

- **空库整备**：首 run 显示「没有作品也可以先整备机器」；一键准备受理（无 project_id）入队，
  步骤含 `SCAN, TOOL_REPAIR, RECONCILE, DOWNLOAD, INSTANCE, ...`；`TOOL_REPAIR` 如实跳过
  （基础工具已达标）；机器级受理不再报 `Project not found`。
- **无试镜旁路**：基础配置能力卡无「真实试镜一次」按钮；UNVERIFIED 运行态显示
  「一键绑定渲染器并核对运行合同」。
- **能力读后端结论**：H3/出图能力卡读 `summary.capabilities` 事实；下载量按后端缺项。
- 边界：mock 引擎下 DOWNLOAD 步的真实安装准入返回 `INSTALL_APPROVAL_REQUIRED_OR_PLAN_HASH_CHANGE`
  （模拟引擎不执行真实下载/安装，属预期限制，与本轮改动无关；下载逻辑由后端定向回归覆盖）。

## 首片当前进度

正式第 1 集 23 镜上一轮已到 `READY_TO_PACKAGE`（3 主素材确认、`missing_count==0`）。
本轮未发起正式开拍（由用户从已有「开拍」入口发起）；未改动正式项目数据。

## 诚实边界

- **真实 H3 成片 / 合成 / 人工审片：NOT RUN**。本轮全部为离线/隔离回归与 mock 浏览器复验，
  不构成真实出片或人工验收结论。
- 本轮未改 `worker.py` / `preproduction.py` / handoff / quality 客户端，不触发
  `client_review_identity` 重校准义务；历史 VLM 正负校准仍未完成，不得声称已校准。
- W6A8 候选 / 12GB 真机路径仍 BLOCKED/NOT_RUN（本机 RTX 5080 16GB）。