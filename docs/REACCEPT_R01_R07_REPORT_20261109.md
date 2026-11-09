# R01–R07 复验收尾对照报告

2026-11-09，Windows。基线 `main@5739e2b`（与 `origin/main` 一致，工作树干净）。
本轮修完二次复验列出的 7 组入口与状态交接缺陷，逐条补了反例与正向回归。

**结论分层**：软件合同已修完并在本机回归通过；真实 Windows 安装、真实权重下载、
真实 LLM/H3 出片、多集交付与人工观感**仍未在本轮验收**（见文末「未覆盖的边界」）。

---

## R01 · P0 · 按镜头分组仍能用 B 的剧本包装 A 的镜头

**根因**：`POST /projects/{id}/episode-plans` 的「按已有镜头分组」分支不记录任何来源，
`plan_basis_blockers` 只在「按场次」模式下才检查来源，所以把 A 的镜头重新分组一次，
新包的 `script` 指向 B、`episode_plan` 仍指向 A。

**修复**：

- 新增 `backend/shine/director/basis.py`：当前制作依据的唯一解析口——显式选中的文档、
  该文档下已确认的大纲，以及镜头**真正来自哪一版大纲**（分节运行 → `film-apply:` 回执 → 镜头）。
- 兼容入口现在记录真实来源（`source.basis = shot_apply_receipts` + 大纲 id/sha），
  而不是留空。
- `plan_basis_blockers` 增加两条：**已证明的**来源不一致 → `EPISODE_PLAN_SCRIPT_CHANGED`；
  来源歧义 → `EPISODE_PLAN_SCRIPT_AMBIGUOUS`。
- 旧行（有可信回执证据）自动衔接；证据缺失只在旅程里给出 `EPISODE_PLAN_SCRIPT_UNATTRIBUTED`
  指针，指向已有的「确认大纲 / 重新拆分镜」，**不新增审批层**。

**验收**：`backend/tests/test_director_basis_grouping.py`（4 项）——
A 分组 → 切到 B → 重新分组被 409 拒绝且旅程点名；按场次物化在切换后仍能物化并开拍；
旧行按回执证据自动衔接；无剧本的手工项目仍可直接按镜头分组。

## R02 · P0 · W6A8 生产准入把「文件存在」当作「SHA 已核验」

**根因**：`profile_gate._weight_blockers` 用 `prepared`（发现字节）当作就位判据，
所以同长错字节的缓存文件（文件名就是期望 SHA）会让生产准入返回 `selectable=true, blockers=[]`。

**修复**：

- `variant_readiness.weight_evidence` 增加部署关系事实：`verified_directory`、
  `installed_directory`、`in_place`（就位原位）、`bound_to_instance`（当前受管实例绑定）、
  `consumable`。
- 准入改为三态具名拒绝：`PROFILE_WEIGHTS_NOT_AVAILABLE`（没就位）、
  `PROFILE_WEIGHTS_NOT_VERIFIED`（没逐字节核验）、`PROFILE_WEIGHTS_NOT_CONSUMABLE`
  （已核验，但当前受管实例不消费那个目录）。`prepared` 退回显示用途。
- 已核验缓存 / 实际绑定目录 / 当前实例是三件事：切换受管实例后按实例的
  `extra_model_paths.yaml` 重查消费关系，不重复全量哈希（回执的 stat 复用保持不变）。
- 候选保持原身份，`auto_download_allowed=false` 未改。

**验收**：`backend/tests/test_production_gate_weight_evidence.py`（5 项）——
同长错 SHA、旧 revision、只在另一目录、只在未绑定缓存、有效原位核验，
并核对真实建包入口给出同一个拒绝码。

## R03 · P0 · 导演包与素材采用接受实际不可执行的条件

**根因（a）**：导演包的 `output` 是裸 dict，未走 `OutputSpec`（fps 固定 24、画幅成对且为 32 倍数、
片段 4–15 秒），所以 `{"fps": 30}` 的包可确认且 `startable=true`，而同一输出直接走 Series 会 422。

**根因（b）**：素材中心采用与导演包就绪都只看 `approved + locked`，不看权利链；
撤销全局授权后，采用预览仍是 `blocked=[]`、采用仍写入新 revision、已确认的包仍显示可开拍。

**修复**：

- 新增 `profile_gate.output_contract_blockers`：用既有 `OutputSpec` 校验冻结的输出，
  不合规 → `OUTPUT_SPEC_INVALID`。草案仍可创建并显示缺项，**确认与开拍**按执行合同拒绝。
- 片段长度还要装得下每一镜的对白与内部剪辑点：
  `production_series.clip_seconds_conflict` 被 Series 入口与导演包就绪共用，
  → `CLIP_TOO_SHORT_FOR_DIALOGUE` / `CLIP_TOO_SHORT_FOR_CUT`。
- 新增 `comfyops.assets.rights_status`（回答而非拒绝）：采用预览对每个镜头给出
  `ASSET_RIGHTS_NOT_APPROVED`，采用被 `ADOPTION_BLOCKED` 拒绝且**不写 revision**；
  导演包就绪对每个已绑定素材给出同名阻断项，撤权后不再显示可开拍。

**验收**：`backend/tests/test_director_execution_contract.py`（6 项）。

## R04 · P0 · 一键请求写入所有绑定，但只准入第一镜

**根因**：`main._create_production_series` 对 `body.bindings` 的每一镜调用 `set_shot_bindings`，
而逐镜静态判定只存在于 `make_render_request`，只对第一镜触发；第二镜缺人物主体也照样 201。

**修复**：

- 请求新增 `expected_shot_revisions`：服务端先核对前端看到的镜头修订，未被其他标签页改写才继续
  （否则 409 `SHOT_REVISION_CHANGED`）。
- 绑定写入后，对**每一镜**跑 `services.shot_static_blockers`（复用单镜入口的同一判定：
  素材母版、镜头自带需求、H3 编译器、参考槽位、画幅映射）。任一镜不可执行 →
  422 `PRODUCTION_SERIES_SHOT_NOT_EXECUTABLE`，带该镜原因与仍可开拍的镜头；
  整个事务回滚，不留半写绑定、不产生意外 Job。
- 只使用现有绑定的镜头不再被重写（`bindings_unchanged`）。
- 动态连续性仍由 Worker 等待：`clip` 之外的非 `cut` 连续镜不参与静态判定。

**验收**：`backend/tests/test_one_click_batch_admission.py`（4 项）。

## R05 · P0 · Series 与单镜 Job 对拍摄归属的判定不对称

**根因**：Series 入口检查「这一镜是否已被占用」，单镜 Job 入口不检查，
所以同一镜可以既被 Series 排队、又被独立请求排一次（两次 201、两个 Job id）。

**修复**：

- 新增 `backend/shine/comfyops/ownership.py`：`shot_ownership` 是「这一镜现在归谁」的唯一答案——
  生产序列的预留（**哪怕还没建 Job**）与活动 Job 都算占用。
- 单镜、Series、Run 派发共用它；独立请求撞上活动拍摄 → 409 `SHOT_ALREADY_IN_PRODUCTION`，
  并指出原任务入口（series_id / job_id）。内部派发按自己持有的预留建 Job
  （`series_item_id`，归属丢失则 409 `SERIES_ITEM_OWNERSHIP_LOST`）。
- 同一请求身份重接已有回执：单镜用既有 `request_key` 幂等；Series 新增 `request_key` 与
  `series-request:<digest>` 回执，重试指向同一个序列而不是再开一次。
- 前端「一键开拍」按范围签名复用自己的请求身份（同一页面内），响应丢失后重试不会开第二次。

**验收**：`backend/tests/test_shoot_ownership.py`（6 项）。
既有 `test_production_series.py::test_a_failing_shot_callback_still_releases_the_renderer`
原来刻意再排一个同镜 Job（这正是本缺陷），改为断言「同镜的第二个入口被拒并指出归属」。

## R06 · P1 · 首次启用真实制作向导仍跳过可重接的安装回执

**根因**：向导读计划列表后只看计划状态（`INSTALLING/SUBMIT_INTENT/NEEDS_REVIEW`）就跳去专业设置，
既没有查目标回执，也没有重接，`零次目标查询、零次重投`。

**修复**：向导改用 `setupExecution.ts` 已有的目标回执读取（与基础配置自动准备共用）：

- 运行中的尝试 → 在向导原地续上并显示阶段与字节进度；
- 已完成的尝试 → 沿原步骤继续准备实例；
- 真正需要对账的尝试 → 显示现有的对账动作并交给专业设置；
- 没有回执 → 保持原有拒绝（不凭计划状态下结论）。

**验收**：`frontend/src/test/realWizard.test.tsx` 新增 2 项（31 项全通过）。

## R07 · P1 · 指定只跑一集，旅程仍把整包镜头算成待制作

**根因**：`compute_journey` 的 `scope_shot_ids` 永远取自导演包或分集方案，
不看当前 Run 冻结的选集，所以只跑一集的 Run 仍然报 `shot_count=2` 与两条 `SHOT_NOT_PRODUCED`。

**修复**：

- 旅程范围跟随当前 Run 冻结的 `ProductionRunEpisode.shot_ids`；
  整包概况移到独立字段 `package_scope`，进度分母、逐镜未产出列表与素材阶段用同一选定范围。
- 前端 `DirectorProductionRunPanel` 增加读取代号（epoch）：快切包、作品或 Run 时丢弃旧异步结果。

**验收**：`backend/tests/test_director_run_scope.py`（3 项）。

---

## 验证账本（本机，2026-11-09）

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 后端完整套件 | `backend/.venv/Scripts/python.exe -m pytest -q`（`SHINE_DATA_DIR` 指向临时目录） | **通过**（1 项跳过，0 失败） |
| 本轮定向套件 | `pytest tests/test_director_basis_grouping.py tests/test_production_gate_weight_evidence.py tests/test_director_execution_contract.py tests/test_one_click_batch_admission.py tests/test_shoot_ownership.py tests/test_director_run_scope.py` | 28 项通过 |
| 受影响既有套件 | `pytest tests/test_production_series.py tests/test_variant_binding_and_gate.py tests/test_director_journey_closeout.py tests/test_director_assets_closeout.py tests/test_output_spec_and_identity.py tests/test_production_preflight_closeout.py tests/test_one_click_start_closeout.py tests/test_director_entries_and_post.py tests/test_director_script_basis_closeout.py` | 全部通过 |
| 合同收紧后的既有套件 | `pytest tests/test_handoff_lease_transfer.py tests/test_managed_process_release.py tests/test_renderer_supervisor.py` | 全部通过（按新合同：旧尝试先终态，新尝试再开始） |
| Ruff | `python -m ruff check shine tests` | 通过 |
| 前端构建 | `npm.cmd run build` | 通过（tsc -b + vite build） |
| 前端单测 | `npm.cmd test` | **32 个文件 / 206 项通过**（基线 201 + 本轮 5 项向导回归） |
| Alembic 空库 | `alembic upgrade head`（新临时库） | 通过，升到 `f180b0060001` |
| Alembic 旧库副本 | `alembic upgrade head`（`.local-data/mig-check/check.db` 的副本） | 通过，升到 `f180b0060001` |
| Windows 测试包 | `pwsh -File scripts\build_test_package.ps1 -OutDir releases -Verify` | 通过：解包后 **750 个文件逐个验签** |
| 浏览器 smoke | `pytest scripts/test_director_browser.py` | **未通过（既有问题）**，见下 |
| 远端 CI | PR #12 | `frontend` 通过；`web-e2e` 在一处**与本轮无关**的旧断言上失败（前一次同代码通过，判定为抖动，已申请重跑）；`backend` / `backend-windows` 见 PR 页面 |

### 测试包

- 文件：`releases/ShineVideoStudio-20261109-5d2f42c-win64.zip`
- SHA-256：`454c3c17e7b54c5aba301ff3ad09762486a12d6283c532d198c42dfc61e221da`
- 大小：4048973 字节；来源提交 `5d2f42c`（分支 `feature/reaccept-r01-r07`）
- 其后只有一个提交：把这一行哈希写进本报告（纯文档），代码与包内容不变。

### 未通过的浏览器 smoke（如实记录）

`scripts/test_director_browser.py` 的三个场景在本轮**仍未跑通**，原因是这个脚本早于本轮就已
与当前契约不一致：

1. **基线提交同样失败**：把 `main@5739e2b` 拉进独立 worktree 跑同一脚本，三个场景
   以同样方式失败（`LLM_SETUP_REQUIRED` 断言、`真实制作已准备好` 断言），证明这不是本轮引入的回归。
2. 本轮已修的三处与当前契约不符的地方：文本模型不再阻断 H3 安装（J03 的新合同）、
   表单可访问名改为 `模型配置名称` / `接口地址`（J12 的定位器）、隔离夹具不再把本机已有
   安装当成可复用项。
3. **剩下的缺口**：脚本最后一步要打开排产制作阶段的「单镜头制作与任务明细（高级）」折叠区，
   需要先切到工作台的生产阶段；本轮未能定位到该导航入口。向导部分（`真实制作已准备好`）
   在 `models_complete` 场景已经走通，失败发生在其后的演示态偏好检查。

### 未覆盖的边界（本轮不声称）

- **真实 Windows 全新机安装**、重复启动、真实依赖与权重下载、断点恢复、已有环境原位复用：未做。
- **真实 Provider 的 LLM / H3 出片**：未做；本轮所有 H3 相关证据都来自隔离 SQLite、网络桩与合成素材。
- **多集连续交付**（至少两集分别产出、解码、下载）：未做。
- **人工观感验收**：未做。
- **W6A8 / Qwen / 固定音色 / 建议审片**的真实能力证据：未做；本轮只改了 W6A8 的生产准入判定。
- 本轮没有改动 `client_review_identity` 钉住的文件（`worker.py` / `preproduction.py` / handoff / quality），
  因此不触发 VLM 重校准；真实正负校准本来就没做过，仍不得声称已校准。

### 交付

- 分支：`feature/reaccept-r01-r07`（`main@5739e2b` 之上），PR：<https://github.com/anjingdtl/shine-video-studio/pull/12>
- 提交：`81e35f4`（七项修复与回归）、`bc8e0d3`（补一项向导回归）
- 报告：`docs/REACCEPT_R01_R07_REPORT_20261109.md`；`AGENTS.md` 增加本轮规则。
- 保留用户现有安装、历史成片、授权与未知提交：未做任何清理或重置。
