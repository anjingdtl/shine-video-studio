# 四阶段二轮复验修复报告 · R01–R08

**2026-11-08 · 审查基线 `main@631d9a07c6be5911a88e138bb037da3d369f3d79`**

| 项 | 值 |
| --- | --- |
| 修复分支 | `feature/four-stage-reaccept-fix-20261108`（本机，未推送） |
| 基线 | `631d9a0`（远端 `origin/main` 同点） |
| 复验依据 | [独立验收](FOUR_STAGE_REACCEPTANCE_631D9A0_20261108.md) · [上一轮账本](FOUR_STAGE_CLOSEOUT_EVIDENCE_20261108.md) |
| 隔离方式 | 独立 feature 分支 + 临时 SQLite（pytest 夹具 / `.local-acceptance/four-stage-browser-r6`） |
| 用户库与运行栈 | 未触碰：`.local-data` 未写入，用户 Worker 进程未重启，8000/5173 未被占用 |

## 0. 结论

R01–R08 全部关闭：每项都有先失败后通过的反例断言，落在仓库里长期可回归。
F01–F11 的既有回归保持通过。软件门禁（后端全量、Ruff、前端测试与构建、Alembic）通过。
真实 LLM、真实 H3、真实卷交付与人工验收本轮**未运行**，边界见第 4 节。

## 1. 逐项结果

### R01 · 单次新尝试授权（P0）

- **根因**：`start_pipeline_new_attempt` 只在旧行写了 `superseded_by_task_id`，没有消费
  `state_version`，同一 `expected_state_version` 连续或并发两次都会各建一个新任务。
- **改法**：`backend/shine/director/pipeline.py` 的 `start_pipeline_new_attempt` 用带
  `state_version` 与 `status` 的 fence UPDATE **在创建新任务的同一事务里**消费授权，旧行
  记录 `new_attempt_authorization`（含 `expected_state_version` / `attempt` / `identity` /
  `new_task_id`）。重复调用返回**同一张回执**（`reused: true`），旧行保持 `UNKNOWN` 历史。
  已被替代的行不再提供任何动作（`task_available_actions`）。
- **审计分离**：新尝试写 `PIPELINE_TASK_NEW_ATTEMPT`，对账写 `PIPELINE_TASK_RECONCILED`，
  两者写各自的 trace 键，不覆盖迟到响应。
- **对账入口**：新增角色匹配的四阶段对账 `POST /projects/{id}/pipeline/tasks/{id}/reconcile`
  （`reconcile_pipeline_task`）。只有结论为 `NOT_SENT_PROVEN` 时才提供普通「继续」，
  `UNKNOWN_REQUIRES_NEW_ATTEMPT` 只提供「核对后重新发送一次」。普通 resume 仍拒绝 UNKNOWN。
- **测试**：`backend/tests/test_four_stage_reacceptance.py::test_r01_one_authorisation_buys_exactly_one_attempt`
  （同一个旧 ID/version 连发两次 → 最多一个新任务、一次 mock transport 发送）、
  `::test_r01_two_concurrent_authorisations_buy_one_attempt`（两个线程同时用同一版本 →
  仍然只有一个新任务）。

### R02 · 局部重做保留其他场次（P0）

- **根因**：`record_storyboard_draft` 用 `db.merge(AppSetting(...))` **整卷覆盖**记录，局部
  重做只剩本次跑过的单元，已应用场次的回执与镜头被抹掉。
- **改法**：新增持久化**逐单元台账** `four-stage-storyboard-units:{project}:{volume}`
  （`storyboard_ledger` / `merge_storyboard_ledger`）。本次跑过的单元只替换自己，其余场次的
  回执、已应用镜头与历史保留。交付范围改从台账读取。
- **测试**：`::test_r02_a_partial_redo_keeps_the_other_scenes`（先应用第一场 → 只重做第二场 →
  第一场回执与镜头仍在 → 交付成功且包内含第一场的镜头）。

### R03 · 当前确认链（P0）

- **根因**：蓝图确认只比计划 hash 与覆盖度，**不比生成时与当前的有序大纲 ID/hash**；
  正式发布与包准入也没有共用这一判断；草稿蓝图写出的场次在发布时没有做依据等价检查。
- **改法**：
  1. `run_bible_task` 把 `basis`（有序 outline_refs + plan revision）写进蓝图产物；
  2. 新增共享函数 `bible_basis_blockers`：确认、读取（总览 `bible_status`）、卷发布、
     包准入四处读**同一个**判断，差异即 `PIPELINE_BIBLE_OUTLINE_SUPERSEDED`；
  3. `bible_basis_equivalent` 覆盖主线、人物、世界、时间线、锁定事实、结局与跨卷开放项；
     草稿蓝图写出的场次在发布时比对，差异即 `PIPELINE_BIBLE_BASIS_CHANGED`。
- **测试**：`::test_r03_a_blueprint_from_an_older_outline_may_not_be_confirmed`、
  `::test_r03_the_read_path_states_that_the_blueprint_moved_on`、
  `::test_r03_a_draft_blueprint_may_only_be_published_when_it_agrees`。

### R04 · 快照幂等（P0）

- **根因**：交付复用键只含 creative lineage，**不含镜头 revision/spec/绑定、冻结风格、
  规则包、工作流图与参数映射**，所以镜头升版后仍然复用旧包。
- **改法**：新增 `delivery_snapshot`：按序绑定 unit 与 apply、每个镜头的 revision 与 spec
  摘要、镜头绑定、冻结风格、规则包锁定、工作流图/checkpoint/parameter_map/media_transfers。
  `handoff_digest` 纳入该快照 → 只有**完全相同**的快照才复用包；镜头升版产生新包，旧包
  保留为历史并由 `package_readiness` 以 `SHOT_REVISION_CHANGED` 阻止新 Run 与未提交批次
  （`production/runner.py` 的建 Run 与每次派发都读 readiness）。
- **旧包兼容复核**：缺 `creative_lineage` 的**四阶段**包进入新生产前被 `CREATIVE_LINEAGE_MISSING`
  拦下；普通旧导演包保留原路径。
- **测试**：`::test_r04_a_changed_shot_revision_produces_a_new_package`（升版后再交付 → 新包、
  `reused=false`；旧包 `startable=false` 且具名 `SHOT_REVISION_CHANGED`，历史仍可读）。

### R05 · 正式分集生产合同（P0）

- **根因**：`_handoff_episodes` 往严格 payload 里塞了未声明的 `episode_script_id` 与
  `episode_script_sha256` → Pydantic `extra_forbidden` → 正式分集交付 422。
- **改法**：`PlannedEpisode` 增加类型化字段 `episode_script_id` / `episode_script_sha256`；
  分集合同身份同时写进导演包的分集清单。合同**时效性**由 `episode_contract_currency` 判定：
  覆盖后的旧大纲、确认后被替代的场次剧本都会让合同失效。
- **具名负例**：`PIPELINE_EPISODE_SCRIPT_SUPERSEDED`、`PIPELINE_EPISODE_OUTLINE_SUPERSEDED`、
  `PIPELINE_EPISODE_COVERAGE_INCOMPLETE`（缺分集）、`PIPELINE_EPISODE_SECTION_OVERLAP`
  （重复/跨集占用）、`PIPELINE_EPISODE_ORDER_NOT_CONTIGUOUS`（非连续顺序）。
  `volume_scope` 仅在**没有任何已确认分集合同**时作为兼容合同保留。
- **测试**：`::test_r05_two_confirmed_episodes_reach_the_volume_delivery`、
  `::test_r05_the_package_manifest_keeps_the_episode_script_identity`、
  `::test_r05_a_superseded_scene_script_refuses_the_episode_delivery`、
  `::test_r05_an_unconfirmed_scene_script_refuses_the_delivery`、
  `::test_r05_a_missing_episode_refuses_the_delivery`、
  `::test_r05_two_episodes_claiming_one_scene_are_refused`、
  `::test_r05_a_non_contiguous_episode_order_is_refused`。

### R06 · 按预期单元覆盖并持久恢复（P0）

- **根因**：异常只进响应 `rejections`，持久记录丢掉失败分片；覆盖只比较**场次集合**，
  于是「一场两片、第 2 片失败」被当成已覆盖，交付照样 201。
- **改法**：`storyboard_units` 现在给出稳定单元身份（场次 revision、来源范围、chunk ordinal、
  `chunk_sha256`、连续性基线）。`expected_storyboard_units` 由此算出**预期单元清单**；
  每个单元的结果、失败、剧组步骤与 apply 句柄都逐单元入库。交付按**单元**核验：
  丢片 `PIPELINE_STORYBOARD_UNIT_MISSING`、重复 `PIPELINE_SHOT_CLAIMED_TWICE`、
  错序/越界 `PIPELINE_STORYBOARD_UNIT_OUT_OF_SCOPE`、失效 `PIPELINE_STORYBOARD_UNIT_STALE`、
  未应用 `PIPELINE_STORYBOARD_NOT_APPLIED`、失败 `PIPELINE_STORYBOARD_UNIT_REJECTED`，
  全部具名拒绝正式交付。台账即刷新、崩溃与重启的回读点。
- **测试**：`::test_r06_a_failed_chunk_is_kept_and_blocks_the_delivery`（一场两片，第 2 片
  注入 422 → 失败位置保留在台账 → 交付具名拒绝）。

### R07 · 浏览器闭环（P1）

- **总览/单卷 GET** 现在返回权威草案、逐单元状态、待应用、失败与对账句柄、发布回执与
  **确切 package ID**：`GET /projects/{id}/pipeline`、`GET /projects/{id}/pipeline/volumes/{vid}`、
  `GET /projects/{id}/pipeline/volumes/{vid}/handoff`。
- **前端**：`FourStagePanel` 不再把分镜草案放在本地 state，而是渲染服务端返回的逐单元状态；
  刷新、崩溃、重启后仍然看得见待应用的镜头。
- **测试**：`frontend/src/test/fourStageRecovery.test.tsx::刷新之后仍能看到待应用的镜头：面板渲染的是服务端状态`；
  **真实 Chromium**（内置浏览器，隔离 FastAPI+SQLite，8129/5175）实测：刷新后单元状态、
  分集合同与交付回执原样恢复（见第 3 节）。
- **现场发现的显示缺陷（已修）**：用户截图指出「已结束的任务的动态条还在继续跑」。原因是
  `<progress>` 没有 value，浏览器就画成**不确定动画**，而阶段文字还停在「落库中」——
  任务其实早已成功。现在：后端 `pipeline_task_summary` 按**状态**给出阶段标签
  （成功=已完成、待核对=待你核对）并给无单元列表的角色一个确定的进度；前端**只在任务真的
  在跑时**渲染进度条。真实 Chromium 复核：进度区不再有任何 `<progress>` 元素，三行分别读
  「编写场次剧本 已完成 / 生成全局故事蓝图 已完成 / 提取大纲 已完成」。
  回归：`::test_r07_a_finished_task_does_not_read_as_running`、
  `frontend/src/test/fourStageRecovery.test.tsx::已结束的任务不再显示会一直动的进度条`。

### R08 · 续跑动作、生产入口与版本选择（P1）

- `reconcileAndRetry` 已删除。新按钮「核对并只重做这一场」按单元调用四阶段对账接口，
  **只有结论允许重试**才重做该场（`section_ids: [该场]`），绝不重跑整卷；失败时显示服务端
  的实际结论。
- 交付回执显示**完整 package ID**，并提供「打开这个导演包」直达 `#d/package?package=<id>`；
  导演包页按该 ID 打开指定包（`DirectorProductionRunPanel` 新增 `packageId`），并可从包列表切换。
- 分集合同与分卷边界接口已接通并显示：分集合同显示「已确认且有效 / 已失效：<原因>」，
  面板可按场分集并确认；卷界调整生成新的分卷方案版本。
- **测试**：`frontend/src/test/fourStageRecovery.test.tsx` 的四条（真实对账只重做该场、
  未知结论不重跑、确切包 ID 与导航、分集合同失效原因）；**真实 Chromium** 实测切卷与
  指定包导航（见第 3 节）。

## 2. 执行的命令与结果

| 命令 | 退出码 | 结果 |
| --- | --- | --- |
| `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_four_stage_closeout.py backend/tests/test_four_stage_reacceptance.py backend/tests/test_pipeline.py backend/tests/test_creative_output_budget.py backend/tests/test_film_unknown_outcomes.py backend/tests/test_material_text_consent.py -q -p no:warnings` | 0 | 82 项（81 通过 + 1 条件跳过） |
| `backend/.venv/Scripts/python.exe -m pytest backend/tests -q -p no:warnings`（全量，2004 项） | 0 | 无失败；2 项条件跳过 |
| `backend/.venv/Scripts/python.exe -m ruff check backend/shine backend/alembic backend/tests`（CI 口径） | 0 | All checks passed! |
| `cd frontend && npm.cmd test -- --run` | 0 | 27 文件 / 177 项通过（含新增 6 项） |
| `cd frontend && npm.cmd run build`（tsc -b + vite build） | 0 | 构建通过（仅既有 >500 kB chunk 警告） |
| `backend/.venv/Scripts/python.exe scripts/verify_four_stage_browser.py --seed-only --data-dir .local-acceptance/four-stage-browser-r6` | 0 | 隔离库合成出 3 卷 / 2 集 / 4 镜 / 已交付包 |
| `SHINE_DATA_DIR=<隔离目录> backend/.venv/Scripts/python.exe -m alembic -c alembic.ini upgrade head`（空库与旧库副本 `.local-acceptance/db/default_local-backup.db`） | 0 | 空库直升 head；旧库副本 `f180a0010001` → `f180b0020001`；`alembic check` = No new upgrade operations detected |

注：`scripts/stop_shine.py` 有一个**既有**的 F401（`UNKNOWN` 未使用），不在 CI 的 lint 口径
（`backend/shine backend/alembic backend/tests`）内，本轮未改动该文件。

## 3. 真实浏览器实测（Chromium · 隔离 FastAPI + SQLite）

隔离栈：`.local-acceptance/four-stage-browser-r6`（Alembic 空库升级）→ API `127.0.0.1:8129`
（`SHINE_RENDER_PROVIDER=mock`）→ 前端 `127.0.0.1:5175`（vite dev 代理到该 API）。
种子经**应用自身代码路径**生成：分卷 → 逐卷大纲 → 全局蓝图 → 场次剧本 → 分镜草案 →
逐场应用 → 两个分集合同 → 卷交付。

| 检查 | 实测 |
| --- | --- |
| 四阶段面板 | 3 卷（第1/2/3卷，剧本均 2/2 已确认）、分集合同 2 个「已确认且有效」、交付回执「导演包 02f58604-e3bd-456d-843b-2ccf611a16e2（第 1 版 · 2 集 · 4 个镜头 · episode_script）」 |
| **刷新恢复（R07）** | 页面 reload 后逐单元状态（镜头已应用 2 个 / 场）、分集合同与交付回执**原样恢复** |
| **切卷（R08）** | 切到第2卷：② 标记「第2卷（当前）」，④ 显示「第2卷 生成分镜草案」，**不带**第1卷的单元与回执 |
| **指定包导航（R08）** | 点击「打开这个导演包」→ 地址栏 `#d/package?package=02f58604-…` → 导演包页显示「第 1 版 · 2 集 · 4 镜 · 已确认」「本机检查通过，可以启动整部作品生产」 |
| 分集方案 | 导演包页显示「第 1 版 · 2 集」，第一集 2 镜、第二集 2 镜 |

未在浏览器内实测：失败单元的「核对并只重做这一场」闭环（隔离栈没有注入失败单元，且
服务态没有挂真实模型替身）、真实 LLM 与真实 H3。

## 4. CI 复核

复核对象：当前 `main` 的 [run 42448890805](https://github.com/anjingdtl/shine-video-studio/actions/runs/42448890805)
与旧基线 `7081104` 的 [run 42441218281](https://github.com/anjingdtl/shine-video-studio/actions/runs/42441218281)。
两个 run 失败作业相同：`backend`（Linux）与 `backend-windows`。

### 4.1 Linux 的 14 项失败：逐项归类

| 失败用例 | 归类 | 依据 |
| --- | --- | --- |
| `test_deployment_acceptance_regressions::test_wizard_install_payload_is_accepted` | Windows 专属断言 | `HOST_NOT_QUALIFIED` / `OS_UNSUPPORTED`（要求 windows-x64，本机 linux-x64） |
| `test_deployment_storage::test_full_deployment_budget_includes_environment_and_snapshots` | Windows 专属断言 | 同上，磁盘预算门禁在资格复核之后 |
| `test_install_plan_qualification::test_a_qualified_host_gets_a_plan_that_records_the_variant` | Windows 专属断言 | 同上 |
| `test_new_machine_setup::test_next_instance_plan_state_machine` | Windows 专属断言 | 同上 |
| `test_image_managed_renderer::test_an_unmanaged_instance_is_refused` | Windows 专属断言 | `IMAGE_RENDERER_WINDOWS_ONLY` ≠ `IMAGE_RENDERER_MANAGED_INSTANCE_REQUIRED` |
| `test_comfy_handoff_journal::test_a_dead_renderer_releases_the_exclusive_lease_it_left_behind` | 平台模拟遗漏 | 死 renderer 的释放靠 Windows 进程/Job Object 身份证明；Linux 上探针取不到证据 |
| `test_service_validation.py`（6 项） | Windows 专属断言 | `SERVICE_VALIDATION_WINDOWS_ONLY` |
| `test_director_setup_contract::test_shipped_service_uses_real_install_contract` | CI 模拟遗漏 | Linux backend 作业未装前端依赖，`scripts/setup_contract_bridge.mjs` 无法 import typescript |
| `test_director_setup_contract::test_shipped_service_uses_real_instance_approval_and_state_machine` | CI 模拟遗漏 | 同上 |

**通用逻辑缺陷：0 项。** 14 项全部落在安装/硬件资格/服务校验/死 renderer 租约这些
Windows 本机能力上，与四阶段流水线无关；旧基线 `7081104` 的失败 node ID 与本次相同
（独立验收已逐项比对），因此不是本轮引入的回归。

### 4.2 Windows 作业的 5 项失败

`test_director_setup_contract`（2 项）、`test_gpu_release_confirmation`（2 项）、
`test_image_installation_catalog`（1 项）。旧基线同样为这 5 项，涉及安装合同、GPU 释放
确认与系统盘限制。本轮改动不触及这些模块。

### 4.3 本轮对 CI 的处理

- 触及文件通过 CI 口径的 `ruff check`（`backend/shine backend/alembic backend/tests`）。
- 未做全仓格式化，避免无关改动；`ruff format --check` 的既有问题保持原样。
- 隔离验收脚本 `scripts/verify_four_stage_browser.py` 走本地 mock，不进入生产包；
  它不写用户数据目录。

## 5. 证据分层

| 层 | 状态 | 依据 |
| --- | --- | --- |
| `OFFLINE/MOCK` | **PASS** | 后端全量 pytest（退出码 0）、定向 80 项、Ruff、前端 176 项 + tsc + build、Alembic 隔离空库升级、真实 Chromium 的隔离栈旅程 |
| `REAL_LLM` | **NOT_RUN** | 本轮未发起任何真实文本模型调用；隔离栈用的是本地 mock 替身 |
| `REAL_H3` | **NOT_RUN** | 未选择卷、未创建 ProductionRun、未占用 GPU、未向 ComfyUI 提交 prompt |
| `VOLUME_DELIVERED` | **NOT_RUN** | 离线路径的卷交付（分集计划 + 导演包 + 回执）已在 mock 下验证；真实成片未产出 |
| `HUMAN_ACCEPTED` | **NOT_RUN** | 无人工艺术验收 |

## 6. 阻塞与下次复跑入口

1. **真实 LLM 旅程**：需要一个已授权可发送的 Profile + 独立端口栈。按
   `docs/PROGRESS_20261108_FOUR_STAGE_LIVE_ACCEPTANCE.md §3 丙` 的断点继续，不要复用
   本轮的隔离临时库。
2. **真实 H3**：需要用户明确的本机授权、空闲 GPU 租约与已登记 workflow profile；
   已有 UNKNOWN 与归属不清的 GPU lease 按既有对账流程处理。
3. **本轮未改** `client_review_identity` 钉住的源码（`worker.py` / `preproduction.py` /
   handoff 模块 / 质量客户端），因此不需要新的 VLM 正负校准与 revision 生命周期。
4. **浏览器补测**：隔离栈服务态没有挂模型替身，失败单元的「核对并只重做这一场」闭环
   只在组件测试中验证；需要真实浏览器复跑时，给
   `scripts/verify_four_stage_browser.py` 的服务态挂上替身再跑一遍。

## 7. 变更清单

| 文件 | 变更 |
| --- | --- |
| `backend/shine/director/pipeline.py` | R01 授权消费与四阶段对账；R02/R06 逐单元台账、预期单元清单与具名覆盖核验；R03 蓝图依据校验与发布前等价检查；R04 交付快照；R05 分集合同时效性 |
| `backend/shine/director/contracts.py` | `PlannedEpisode` 增加类型化分集合同身份字段 |
| `backend/shine/director/package.py` | 包清单保留分集合同身份；包准入接入蓝图依据门禁与旧四阶段包兼容复核 |
| `backend/shine/api_pipeline.py` | 总览返回分卷方案、蓝图当前性、逐单元状态、分集合同与交付回执；单卷状态与交付回执读取路由；四阶段对账路由 |
| `backend/tests/test_four_stage_reacceptance.py` | **新增** R01–R06 的反例回归（13 项） |
| `frontend/src/api.ts` | 四阶段类型与新接口（单卷状态、对账、局部重做、分集合同、卷界、指定包） |
| `frontend/src/components/FourStagePanel.tsx` | 渲染服务端权威状态；逐单元对账与只重做这一场；确切包 ID 与跳转；分集合同与卷界确认 |
| `frontend/src/components/DirectorProductionRunPanel.tsx` | 支持打开指定包并可切换包 |
| `frontend/src/App.tsx` | 地址栏可携带 `package` 深链并恢复 |
| `frontend/src/test/fourStageRecovery.test.tsx` | **新增** R07/R08 的前端反例回归（5 项） |
| `scripts/verify_four_stage_browser.py` | **新增** 隔离浏览器验收的种子与服务脚本 |

## 8. 追加轮：素材后置的阶段重排

**决定（用户裁定）**：素材一律后置 —— 剧本定稿、分镜设计完成后，才到素材库按分镜准备素材，然后拍摄。

**为什么**：每镜需要什么素材是**分镜的产物**，不是剧本的产物（`api_film.py:414-418` 的
`required_asset_ids=intent["asset_ids"]`，来自摄影方案）。原来的六阶段把「素材」挂在统筹阶段，
而它的判据只能按镜头算 —— 判据被放在了它算不出来的阶段；同一个素材门禁又在 ③④ 各判一次。

**改动**：

1. 服务端 journey 由 6 阶段变 7 阶段：`script 创意与剧本 → plan 剧本统筹 → storyboard 镜头拆解
   → assets 拍摄素材准备 → package 导演包 → production 视频生产 → post 后期成片`。
2. ② 只保留**分集方案 + 冻结风格**，不再问任何素材。
3. ③ 只判镜头自身（存在、参数有效、beat 非空），不再判素材。
4. 新的 ④ 拍摄素材准备 承接逐镜素材判据，并新增「镜头点名的角色必须有绑定主素材」
   （`SHOT_CHARACTER_MASTER_MISSING`）。
5. 导演包冻结同样新增这条（`package_readiness`）—— 素材的最终关口仍在冻结时。
6. 四阶段面板：每个单元回执带 `missing_characters`（分镜点名、但还没有主素材的角色），
   面板如实显示，并在素材备齐前**不提供**「应用这一场」。
7. 前端阶段条 7 格，顺序有断言。

**证据**：

- `backend/tests/test_director_long_form.py::test_materials_are_judged_after_the_storyboard`
- `backend/tests/test_director_long_form.py::test_the_material_stage_names_a_character_without_a_master`
- `frontend/src/test/setupEntry.test.tsx`（阶段条次序断言）
- 既有测试同步：阶段 id 列表、阶段名、阶段序号。

**需求链（用户补充的架构，已落地）**：

分镜提出的是**需求**，不是素材 —— 缺哪个角色主素材被如实记下来随答案交给素材层，由素材层准备；
剧本与分镜不因为素材库还没备齐而停下。

- 校验层不再因缺主素材拒绝出方案（`shine/agents/film.py`：原来的
  `CHARACTER_MASTER_BINDING_REQUIRED` 硬拒绝已移除，改为由需求链承担）。
- 需求清单由 API 层如实计算（`_character_coverage`：出场人物 + 台词说话方，按 `entity_id` 解析），
  随答案的 `material_requirements` 交给素材层。
- 拦住未满足需求的是两道门：**拍摄素材准备**这一步（`SHOT_CHARACTER_MASTER_MISSING`）与
  **导演包冻结**（同一判定）。
- 四阶段面板按同一顺序：单元回执带 `missing_characters`，面板如实显示，并在素材备齐前
  不提供「应用这一场」。
- 回归：`backend/tests/test_storyboard.py::test_storyboard_proposes_the_material_requirement_instead_of_blocking`
  （缺主素材时链不停止、需求被记录、镜头仍未落）。

**尚未做的边界（2026-11-08 第二轮收尾后更新；本轮 = R09，见第 9 节）**：

1. ~~**落镜仍是服务端硬门禁**~~ → **本轮已改为提示**：缺素材不再拒绝落镜（`apply-camera` 跳过绑定并回
   `material_notices`），也不再拒绝导演包冻结（`package_readiness` 去掉 `SHOT_CHARACTER_MASTER_MISSING`）。
   见第 9 节。
2. **道具需求还没有载体**（未解决）：角色需求走 `beat.characters`（名字），场景需求走大纲的 production_sections，
   但「这一镜要哪件道具」目前没有类型化字段，只能落在镜头的 `required_asset_ids`（已存在的素材）。
3. ~~导演模式还没有「逐镜绑定素材」的操作界面~~ → **撤回这一条**：逐镜绑定本来就不是人工动作。
   导演角色在分镜里逐镜给出 `asset_ids`，`apply-camera` 逐镜落绑定；「缺哪张图」是**需求**，
   由素材层准备，导演模式不需要另设一个人工绑定界面。人工逐镜绑定仍然只在专业层的 `ShotPanel`。
4. **新增（未解决）**：「一键开拍（自动适配）」这一档仍会在缺参考素材时挡住提交（画面参考与人物主体
   都算缺）。它是排产提交入口，不是落镜、也不是冻结，本轮没有改它。人物主体缺位**应当**继续拦住
   （身份漂移保护：`H3_CHARACTER_REFERENCE_MISSING`）；画面参考缺位是否也降级为提示，要先解决
   Ref2VA 方案「至少一张参考」与无参考时编译模式退化为 T2VA 的匹配问题，否则会变成服务端的模式不匹配。
5. **新增（未解决）**：方案声明 audio 传输族时，**有台词**的镜头仍必须有音频参考
   （`WORKFLOW_MEDIA_FAMILY_NOT_BOUND`）。产品口径是「音色是可选的，H3 自己说话」，与渲染侧这一条
   不一致；本轮没有改，因为「无台词镜头豁免 audio」是上一轮有意加的（实测外语呢喃），放宽有台词镜头
   需要单独一轮验证。

## 9. 第二轮收尾（R09）：素材是可选类需求

**决定（用户裁定）**：素材是**可选类**需求 —— 缺失只提示「无图片素材将由H3自行生成」，不再硬阻滞
落镜/冻结；但**绝不绑定未批准锁定的素材**（未确认的字节不能进渲染），已绑定的参考图一张都不能丢。

**为什么**：分镜提出需求 → 传给素材层 → 素材层准备 → 落镜校验。需求没被满足时，链条应该继续往前走
并把缺口如实说出来；让一条缺素材的链条停在半路，用户看到的只是一个没有解释的拒绝。

**改动**：

1. **落镜**（`backend/shine/api_film.py`）：镜头点名的素材若不存在、不是本项目的、或没有批准锁定，
   就**跳过绑定**并记一条 `MISSING_IMAGE_MATERIAL`（「无图片素材将由H3自行生成」），这一镜照样落；
   绝不绑定未确认的字节。
   - 同时补上一个会让该改动**直接失效**的连带点：被跳过的素材必须同时从这一镜的 `required_asset_ids`
     里去掉。否则 `create_shot_record`（`Shot requires user-approved, locked assets`）与渲染预检
     （`Render references are missing assets required by the shot`）仍会拒掉这一镜——只跳过绑定、
     不改要求，等于把一种 422 换成另一种 422。修好后这一镜的「要求」就是它真正绑上的那些素材。
2. **素材阶段**（`backend/shine/director/journey.py`）：④ 拍摄素材准备 的缺失项进
   `facts.material_notices`，`blockers` 为空，这一步不再堵流程。
3. **导演包冻结**（`backend/shine/director/package.py`）：去掉 `SHOT_CHARACTER_MASTER_MISSING` 这条阻断。
   **仍然保留**「已绑定但没确认锁定的素材」→ `ASSET_NOT_MASTER`：绑了就必须是真主素材。
4. **渲染侧**（`backend/shine/comfy/provider.py`）：方案声明 image 传输族时，这一镜一张图片都没绑就
   **不再**被判 `WORKFLOW_MEDIA_FAMILY_NOT_BOUND`——未用的图片装载节点会在提交的图里被剪掉，不会有
   占位文件名送到渲染器。**只在这一镜没有绑定任何图片时**才豁免；绑定了就一张都不丢。
5. **前端**（`frontend/src/api.ts`、`frontend/src/components/FourStagePanel.tsx`）：「应用这一场」不再
   因为缺素材而禁用；回执里的 `material_notices` 如实显示；面板文案从「备齐后再应用这一场」改成
   「可以先落镜；缺的那个图片将由 H3 自行生成」。
6. **需求说明文案**（`backend/shine/api_film.py` 的 `material_requirements.note`）：不再说「准备确认锁定
   后再落镜」，改成「缺的那张图片由 H3 自行生成，落镜不再因此停下」。
7. **素材阶段的文案也如实**：④ 的 summary 在还有缺口时写明「还缺 N 项，缺的那个图片由 H3 自行生成」，
   阶段条打勾不再意味着「素材都齐了」。

**证据**：

- `backend/tests/test_four_stage_reacceptance.py::test_r09_a_missing_material_is_a_notice_not_a_block`
  —— 角色主素材**上传但未确认**的真实状态：分镜提出需求（`missing_characters == ["Lin"]`）→ 落镜成功
  → 这一镜**没有任何绑定**（未确认的字节没进渲染）→ 素材阶段只报 notice → 导演包照常冻结
  （`readiness.startable is True`，没有 `SHOT_CHARACTER_MASTER_MISSING`）。
- `backend/tests/test_four_stage_reacceptance.py::test_r09_a_revoked_master_is_skipped_and_never_bound`
  —— 计划做完后撤销确认：绑定被跳过、提示如实返回、被撤销的素材**没有**进绑定，也不再是这一镜的要求。
- `backend/tests/test_reference_transfer.py::test_a_shot_without_a_picture_is_not_refused_by_an_image_transfer_family`
  —— 方案声明 image 传输族、无图镜头放行。
- `backend/tests/test_reference_transfer.py::test_a_bound_picture_is_never_dropped_by_the_image_transfer_family`
  —— 绑了两张图，两张都上传，一张都不丢。
- `backend/tests/test_production_series.py::test_a_shot_without_a_picture_is_accepted_by_an_image_transfer_profile`
  —— API 层的同一条。
- `backend/tests/test_director_long_form.py::test_materials_are_judged_after_the_storyboard`、
  `::test_the_material_stage_names_a_character_without_a_master` —— 素材阶段只报 notice、不报 blocker。

**反例证明**：临时去掉 provider 的 image 豁免后，上面两条「无图镜头放行」的断言立刻失败
（`WORKFLOW_MEDIA_FAMILY_NOT_BOUND`）；恢复后全绿。「已绑定的参考图一张都不丢」这条在有无豁免时都通过，
它是防止**过度豁免**的护栏。

**一条必须如实说明的机制**：`asset_context` 只统计**已确认并锁定**的素材，而分镜链的每个任务都
记着当时的这份上下文摘要（`load_inputs` 比对 `task.asset_manifest_hash`）。所以在真实运行里，
主素材若在出方案之后被撤销确认，落镜会先被「输入上下文已变」（`INPUT_CONTEXT_CHANGED` /
`FILM_HANDOFF_REJECTED`）挡住，而不是走到绑定那一步——这是对的：方案本来就是按旧上下文做的，
应当重做方案。本轮把「跳过绑定」做成真正可用的路径（`required_asset_ids` 同步去掉），并用 R09 的
第二条断言覆盖它。

也正因如此，**常态下「素材还没备齐」根本不会走到绑定那一步**：素材未确认时它不在 `asset_context`
里，分镜就没有可指名的素材，于是它提出**需求**（`missing_characters`），镜头没有绑定地落下，
H3 自行生成。R09 的第一条断言走的就是这条真实路径。

**本轮执行的命令与结果**：

| 命令 | 结果 |
| --- | --- |
| `backend/.venv/Scripts/python.exe -m pytest tests/test_four_stage_reacceptance.py tests/test_comfy_handoff_journal.py tests/test_h3_compiler.py tests/test_production_series.py tests/test_reference_transfer.py tests/test_workflow_profile_scope.py tests/test_v13_e2e.py tests/test_director_long_form.py -q` | 定向回归全绿。**第一跑这里有 1 处失败**：是上一轮写好但从未跑过的 R09 用例本身有问题（它假设「翻转素材确认状态」会走到绑定那一步，实际会先被输入上下文校验挡下），重写后才全绿 |
| `backend/.venv/Scripts/python.exe -m ruff check shine alembic tests` | All checks passed!（CI 同口径） |
| `cd frontend && npm.cmd test -- --run` | 27 个测试文件、177 项全部通过 |
| `cd frontend && npm.cmd run build` | `tsc -b && vite build` 通过（构建脚本本身带类型检查） |
| `pytest <第 1 段：按文件名排序的前 78 个测试文件> -q -ra` | 退出码 0；进度行逐格计数 **971 项**：970 通过、1 跳过（`test_four_stage_reacceptance.py:365`「this fixture needs three scenes」）、**0 失败** |
| `pytest <第 2 段：后 78 个测试文件> -q -ra` | 退出码 0；进度行逐格计数 **1110 项**：1109 通过、1 跳过（`test_real_managed_startup.py:85` 需要真实 ComfyUI 安装）、**0 失败** |
| `pytest tests/test_director_long_form.py tests/test_four_stage_reacceptance.py tests/test_pipeline.py tests/test_reference_transfer.py tests/test_production_series.py tests/test_director_long_form_regressions.py tests/test_storyboard.py -q -ra` | 最终代码复跑：133 项，132 通过、1 跳过、**0 失败** |
| `backend\.venv\Scripts\python.exe -m pytest backend/tests/test_image_managed_renderer.py backend/tests/test_managed_comfy_bootstrap.py -q`（**在仓库根**） | 7 项通过。两段是**在 `backend/` 目录里**跑的，而 AGENTS.md 记着「后端测试必须在仓库根运行，否则 `test_image_managed_renderer.py` 会误报失败」——这两个最依赖仓库根相对路径的文件已在仓库根复跑确认 |

**全量后端是分段覆盖，不是「一次性全量通过」**：本机单条 `pytest backend/tests` 约 30 分钟会被截断，
所以按文件清单（156 个）切成两段**顺序**跑，两段没有重叠任务。合计 **2081 项：2079 通过、2 跳过、
0 失败**（跳过的两项都是明确的外部条件：需要三场次夹具、需要真实 ComfyUI 安装）。
两段跑完后只改过一处不改变行为的文案（④ 素材阶段的 summary 字符串），受影响的 7 个文件已按上表
最后一行复跑确认。

**未实跑**：`REAL_LLM`、`REAL_H3`、`VOLUME_DELIVERED`、`HUMAN_ACCEPTED` 仍为 `NOT_RUN`。
本轮没有碰真实 H3、GPU 与租约；「H3 自行生成缺的那张图片」是编译与提交层的合同结论，
真实成片里的实际画面质量仍未验证。
