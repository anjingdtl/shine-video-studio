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

**尚未做的边界（明确记录，不假装完成）**：

1. **落镜仍是服务端硬门禁**：`apply-camera` 要求参考素材已批准锁定（`api_film.py:445-450` 的
   `ASSET_APPROVAL_CHANGED`）。这与新次序一致（先备素材、再落镜），但还没有「落镜前按角色名
   核对」的具名拒绝。
2. **道具需求还没有载体**：角色需求走 `beat.characters`（名字），场景需求走大纲的 production_sections，
   但「这一镜要哪件道具」目前没有类型化字段，只能落在镜头的 `required_asset_ids`（已存在的素材）。
3. 导演模式还没有「逐镜绑定素材」的操作界面（逐镜绑定目前只在专业层的 `ShotPanel`）。
