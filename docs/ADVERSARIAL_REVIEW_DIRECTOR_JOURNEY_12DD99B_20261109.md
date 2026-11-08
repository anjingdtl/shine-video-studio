# 导演模式全旅程唯一性对抗审查

2026-11-09，基线 `main@12dd99bc9e037c53fce9f19aea68cbe342fece03`。开审时工作树干净，`git ls-remote origin refs/heads/main` 与本地 HEAD 一致。四阶段编导和独立素材中心分别由 PR #8、#10 合入，七阶段 UI 和作品列表自动恢复已在基线中。前轮 R01–R09 的记录作为回归背景，本轮结论来自当前代码和隔离反例。 [PR #8](https://github.com/anjingdtl/shine-video-studio/pull/8) · [PR #10](https://github.com/anjingdtl/shine-video-studio/pull/10) · [基线提交](https://github.com/anjingdtl/shine-video-studio/commit/12dd99bc9e037c53fce9f19aea68cbe342fece03)

**结论 `NOT_ACCEPTED`。** 确认冻结的导演包可以混入不同剧本来源，变更工作流映射后仍显示可开拍，或在启动后才遇到可提前确定的 H3 拒绝。撤销的全局素材授权仍可穿透没有制片准备计划的真实 Job 准入。当前范围的成片进度还会混入演示镜头及旧 Run。安装与浏览器阶段另有可复现断点。严重度按是否可能产生错拍、越权、重复提交或虚假交付判定，证据范围逐项标明。

## 权威对象与交接边界

| 旅程 | 权威对象和唯一决策 | 允许存在的投影与兼容入口 |
| --- | --- | --- |
| Windows 首装、复用、下载 | 安装实例与选定数据根、固定 catalog/variant、内容哈希与文件长度、数据库持久尝试和目标占用。服务探测与真实推理各自出具证据。 | 启动器负责引导，浏览器展示已批准计划、断点与后续动作。临时传输源共享相同字节身份。 |
| 导入、分卷、分集、分镜 | 用户选定的文档 ID/hash → 确认的大纲及有序场次/分卷 → 分集及有效逐单元 apply 回执 → 镜头 revision。项目、作品范围与来源随链携带。 | 短剧普通入口、想法入口、长文本四阶段可以并存，进同一生产合同。手工镜头入口显式标识来源。 |
| 素材与风格 | 全局版本及现行权利 → 显式项目分配 → 明确的逐镜参考绑定。用户确认的不可变 StyleVersion 和 RulePack 贯穿下游。 | 项目独立 ID 有隔离价值，来源版本、项目范围与授权须可追溯。素材需求可先于绑定；目录和草稿均可演进。 |
| 开拍与恢复 | 导演包冻结内容与可执行准入 → Run → Series → Job → Attempt。所有真实镜头统一经 H3PromptCompiler、Worker、管理态 ComfyUI 和 GPU 租约。 | 连续制作与专业单镜入口复用绑定、权利、提交占用和对账，重拍、技术重试和重复投递各有身份。 |
| 出片与交付 | 与当前范围匹配的生产台账、校验后的 Artifact、选片与 EDL/DeliveryOrder。人工确认状态独立于技术完成。 | 历史包、历史 Run 和旧交付继续可定位。阶段进度、下载和审核引用相同的范围及版本。 |

依据见 V1.0 第 3.1 节及当前 `AGENTS.md`。后续报告按 `已复现`、`静态证据` 和 `NOT_RUN` 区分。 [V1.0 基线](Shine视频工作台_V1.0_项目建设方案.md#31-唯一性原则硬约束) · [项目约束](../AGENTS.md)

## 逐段反例

### U01 · P1 · 同一安装内容可同时拥有两个待执行尝试

**已复现，隔离 Windows API，队列层。** 同一个项目、组件集合、存储根与磁盘预算连续调用两次 `POST /setup/install-plan`，批准前 SHA 相同，plan ID 不同。分别以同一测试操作员取得 grant、批准并执行，两次都返回 `202 QUEUED`，attempt ID 不同。当前排他检查只查 `plan_id`。这证明了物理目标的重复排队，磁盘上是否重复传输、覆盖或发生并发写仍需故障注入验证。

建议把物理执行占用锚定到规范化存储根和会被写入的目标。相同内容跨标签页、刷新与项目能够重接收据，冲突计划等待占用释放或对账。项目是操作来源，机器上的同一写入目标需要共享排他。 [部署计划](../backend/shine/comfyops/deployment.py#L392) · [尝试准入](../backend/shine/comfyops/installation.py#L143) · [同 plan 的现有回归](../backend/tests/test_installation_persistence.py#L103)

### U02 · P1 · 安装选择与发包事实由不同来源表达

**静态证据及实包内容核对。** `DeploymentRecommendationCard` 产生 `h3_video`、`image_prep` 两项选择。导演基础配置仅用 `image_prep` 控制图片能力卡片是否显示，`SetupPanel` 从固定 catalog 自取默认 H3 组件创建计划。界面选择因此不能作为最后授权计划的依据。另一个可直接核对的差异是打包脚本固定写 `fixed_materials.packages = 154`，基线完整 H3 catalog 按 `components[].files[]` 计有 **189 条文件声明**，总量仍为 **60,804,054,569 bytes**。如果包需要统计其他口径，应给出明确定义和可重算字段。Qwen Image 独立 catalog 为 **3 条文件声明 / 17,283,091,112 bytes**，应单独显示其适用范围。

建议由后端生成可确认的部署组合，统一推荐、实际执行计划、包清单与用户所见的体积和就绪阶段。已登记、文件验证、节点验证、真实生成验证分别显示。 [推荐选择](../frontend/src/components/DeploymentRecommendationCard.tsx#L42) · [选择消费](../frontend/src/components/DirectorSetupPanel.tsx#L145) · [实际计划请求](../frontend/src/components/SetupPanel.tsx#L715) · [包元数据](../scripts/build_test_package.ps1#L177)

### U03 · P1 · 普通剧本导入的下一步藏在后置素材区

**已复现，Chromium → Vite → FastAPI → 空白隔离库。** 在 `#d/creative` 导入含人物和道具的 `audit-script.txt`，原文成功入库，`/journey` 仍指向 `script`。第一阶段主区只有保存创意简报和刷新，另有折叠的高级四阶段入口。第二阶段显示「先在创意与剧本确认大纲」，实际普通大纲动作「后台提取并归并总体大纲」藏在 `#d/produce` → 素材准备 → 选文件之后。第二阶段还提前渲染第 5 阶段的导演包表单。

建议将同一个普通大纲任务入口移动到第一阶段，将同一场次分镜入口移动到第三阶段。保留四阶段的分卷工作台，使用同一文档上下文和去向回执。验收需从空作品只按主流程点击。 [创意、统筹和分镜布局](../frontend/src/App.tsx#L810) · [大纲组件的实际挂载点](../frontend/src/components/MaterialPreparationPanel.tsx#L691)

### U04 · P0 · 普通导演包能确认互不相干的剧本、大纲与镜头

**已复现，隔离 API，真实上传加已确认大纲夹具。** 导入 A 并准备 A 的已确认大纲和镜头，随后导入 B。再次生成普通导演包，公开回执里的 `script.document_id` 变成 B，`outline_artifact_id` 仍为 A，镜头也来自原方案，确认返回 200。A 的旧包也保持 `ready`。单纯上传可以只表示增加备选，缺陷在于新包把最新上传自动写成来源，同时接受其他来源的上游依据。

`_script_ref` 分别取最新文档和最新未 stale 的大纲，`package_readiness` 的创作 lineage 校验主要覆盖四阶段包。普通场次计划虽在确认时校验大纲，落镜回执、包与 Run 仍需要同一个当前来源判据。应让用户选择决定是否切换生产依据，按实际 lineage 生成并核对快照。 [剧本快照](../backend/shine/director/package.py#L69) · [计划来源校验](../backend/shine/api_director.py#L182) · [包时效检查](../backend/shine/director/package.py#L353)

### U05 · P1 · 素材后置与旧分镜链互相等待，分配回执也未交接到镜头

**已复现，两个独立反例。** 第一，默认分镜 UI 发 `confirm_characters=true`。模拟角色尚未备料时，实际 API 返回 `201 AWAITING_CHARACTER_CONFIRMATION`，仅有 `scriptwriter` 步骤；UI 的继续动作要求每个人都有素材。这和后置备料冲突。现有缺素材可继续的后端测试走默认 `false`，覆盖不到这条 UI 请求。第二，先落一个点名 Lin 的镜头，再通过素材中心批准、QC、晋升并分配 Lin 的全局版本，项目主素材已创建，原镜头 `GET /bindings` 仍为空，旅程仍报告 `SHOT_CHARACTER_MASTER_MISSING:Lin`。

分配和镜头采用本来是两个不同的动作，应把分配回执接到可见的、带镜头版本的采用动作。角色、场景和道具需求要可重读并保留每镜来源，区分可缺需求与已经选用的参考槽。手工素材对照可继续作为主动选择，分镜提需求可以先完成。 [UI 请求](../frontend/src/components/StoryboardPanel.tsx#L246) · [API 分支](../backend/shine/api_film.py#L1063) · [项目分配写入](../backend/shine/asset_center/service.py#L758) · [旧缺素材测试](../backend/tests/test_storyboard.py#L611)

### U06 · P0 · 全局素材撤权可穿透普通项目的真实渲染准入

**已复现，真实 FastAPI 写入与 Worker 的制片准入函数。** 上传合成图片，经授权、QC、grant 晋升并分配给项目，绑定入镜。撤销全局 rights 后，素材中心当前权利校验拒绝该版本。对没有制片准备计划的项目，正式 provider `comfyui` 的 Job API 仍返回 201，请求清单仍包含该素材，调用 Worker 共用的 `enforce_job` 也通过并返回 `None`。本次止于队列与准入，GPU 上传和 H3 调用为 `NOT_RUN`。

授权链校验目前挂在计划路径内，普通路径只看项目素材的批准锁定状态。项目分配时的权利快照应保留用于审计，当前使用权应在所有消费者触发外部副作用前从权威来源重查。已完成的历史保留原收据，撤权后的允许读取或交付范围按既有政策分开裁定。 [项目权利链](../backend/shine/comfyops/assets.py#L47) · [无计划引用路径](../backend/shine/comfyops/preproduction.py#L469) · [Job 创建](../backend/shine/services.py#L821) · [全局撤权](../backend/shine/api_asset_center.py#L841)

### U07 · P0 · 导演包可开拍与同一镜头的 H3 准入互相矛盾

**已复现，隔离 API。** 冻结风格、建立无绑定且点名人物的镜头，生成并确认选择 Ref2VA 的导演包，`readiness.startable=true`；同一个镜头、profile、mode 走 Job 创建即 422 `H3_MODE_REFERENCE_MISMATCH`。其他缺口如人物映射、尺寸映射、传输槽、引用来源与 Continuity 需要同组回归。动态连续性素材在前序产出后才可生成，它的等待状态应与静态不可执行分开。

R09 的缺图提示继续允许做分镜、备料和保存创作快照。可真实执行须满足已选模式的条件。默认保持多参考 Ref2VA。只有明确选择且已验证的其他模式才可作为单独合同执行。固定音色保持可选，已绑定并获准的参考全部进入映射或具名拒绝。 [包门禁](../backend/shine/director/package.py#L468) · [编译入口](../backend/shine/services.py#L589) · [模式约束](../backend/shine/h3/compiler.py#L205)

### U08 · P0 · 原地更改工作流参数后，已确认包的执行指纹不变

**已复现，使用公开接口。** 注册声明了宽高输入但未映射的测试 profile，确认导演包；调用 `POST /workflow-profiles/{id}/standard-parameters` 增加映射，图 SHA 保持原值。旧包仍 `startable=true`，`config_fingerprint` 完全不变。快照存了映射，当前准入只比较图 SHA 与映射非空。Run 派发又加载当前 profile，冻结承诺存在漂移。实际出片参数验证为 `NOT_RUN`。

把影响执行的完整快照用于包、Run 和派发共用的验证。参考字节、输入角色/槽、模型与工作流版本、项目语言与冻结风格均应按各自契约判定。 [映射变更](../backend/shine/api_comfy.py#L265) · [包冻结字段](../backend/shine/director/package.py#L236) · [当前检查](../backend/shine/director/package.py#L396) · [派发读取](../backend/shine/production/runner.py#L495)

### U09 · P0 · 直接提交能隐式写绑定，同一拍摄请求可重复入队

**已复现，隔离 API。** 保存绑定为空，向单镜 Job API 传参考，得到 201，随后 GET 显示参考已被写入镜头。相同请求再发得到另一个 201 和另一 Job ID。将首个 Job 置成带尝试和 prompt-graph hash 的 `COMFY_SUBMISSION_OUTCOME_UNKNOWN` 夹具状态，再提交依然得到第三个 201。证据止于准入，外部发送次数为零。连续制作已有部分 live Job 排斥，普通入口与其他入口的统一预留仍要验证。

新拍摄、传输重放、受控重拍和 UNKNOWN 对账应有显式且持久的不同身份。参考修改由明确的镜头绑定操作产生 revision。保留老调用者需要的迁移桥接时，执行前必须满足相同版本、批准和占用合同。 [直接 Job API](../backend/shine/main.py#L975) · [请求与保存绑定](../backend/shine/services.py#L497) · [隐式写绑定](../backend/shine/services.py#L692) · [Series 排斥](../backend/shine/comfyops/production_series.py#L251)

### U10 · P0 · 阶段完成态混用项目镜头、任意已通过 Job 和旧 Run

**已复现，隔离 SQLite 与 FFmpeg 合成媒体。** 先按旧计划完成并装配 1 集 2 镜，再确认含新增镜头的计划及生成新包。新的旅程显示生产 2/3 且未完成，后期却用旧 Run 的 1/1 标为完成。独立反例中，把已通过的夹具 Job 和 Artifact 标为 mock，七阶段仍显示 1/1 生产完成。

`compute_journey` 独立使用最新确认计划、最新包、所有项目的当前 PASSED 镜头及最新 Run。浏览器另以最新 Run 展示成片，并在指定包找不到时回退到最新包。当前确认范围应有统一 ID 传递给进度、播放、EDL、下载和审核，历史深链明确展示其原范围。独立 Series 的可复用镜头应经显式选片与校验。 [生产完成判定](../backend/shine/director/journey.py#L41) · [后期判定](../backend/shine/director/journey.py#L305) · [Run 概要](../backend/shine/production/runner.py#L1814) · [面板选择](../frontend/src/components/DirectorProductionRunPanel.tsx#L125)

## 验证账本

| 层级 | 本轮证据 |
| --- | --- |
| 后端回归 | 仓库根运行 `backend/.venv/Scripts/python.exe -m pytest -o addopts='' backend/tests/test_package_entrypoints.py backend/tests/test_director_setup_contract.py backend/tests/test_new_machine_setup.py backend/tests/test_installation_persistence.py backend/tests/test_asset_center.py backend/tests/test_four_stage_reacceptance.py backend/tests/test_director_long_form.py backend/tests/test_director_long_form_regressions.py backend/tests/test_offline_e2e.py -q --tb=short`。**155 passed / 1 skipped**。 |
| 对抗复现 | 隔离 pytest **9 passed**，断言当前缺陷可重现；Chrome 实际访问隔离 HTTP 服务、上传文件、截图及定位大纲。合成 fixture 的 Run/Job 只用于验证状态投影与准入。 |
| 前端和静态 | `npm.cmd test -- --run` **28 文件 / 191 通过**；`npm.cmd run build` 通过，Vite 报 >500 kB chunk 提示；`ruff check backend/shine backend/alembic backend/tests` 通过。 |
| SQLite 迁移 | 隔离空库 `alembic -c backend/alembic.ini upgrade head` 到唯一 head `f180b0030001`；`alembic check` 无未生成操作。旧库副本迁移留给收尾验收。 |
| Windows 包 | 干净基线 `pwsh -NoProfile -File scripts/build_test_package.ps1 -OutDir <隔离目录> -Verify` 通过，解压校验 690 文件，ZIP SHA-256 为 `b647c45fc5d5085a8cfb5236b7f60b72e57718b9ea9c667dd69f7f4d35228230`。这是包完整性证据。 |
| GitHub CI | 审查时同 SHA 的 `frontend` 已通过，`web-e2e` 已失败，Windows/Linux backend 在运行。GitHub CLI 当时无法读取仍在运行的整条 workflow 中的失败日志，失败原因待取证。 |
| 真机与人工 | `CLEAN_WINDOWS_INSTALL`、真实全量下载和断网续传、`REAL_LLM`、`REAL_QWEN_IMAGE`、`REAL_VOICE`、`REAL_H3`、真实多集交付、`HUMAN_ACCEPTED` 均为本轮 `NOT_RUN`。全量后端也为本轮 `NOT_RUN`。 |

GitHub 状态是审查时的快照。历史的真机样片、环境校验与前轮软件回归，按各自 SHA、宿主和执行路径使用。 [基线 CI](https://github.com/anjingdtl/shine-video-studio/actions/runs/42496535584) · [失败的 web-e2e job](https://github.com/anjingdtl/shine-video-studio/actions/runs/42496535584/job/128780037357) · [素材中心证据](SHINE_ASSETS_IMPLEMENTATION_REPORT_20261108.md)

本地辅助证据在 `.local-acceptance/director-uniqueness-12dd99b/`，包括 `test_counterexamples.py`、`api-counterexamples.txt`、`browser_probe.py`、`browser-probe.txt`、三张创意/统筹/素材区截图、安装页截图及测试 ZIP。该目录为本机隔离证据，收尾 PR 的回归应转写成正式测试。执行清单见 [本地 Agent 收尾提示词](LOCAL_AGENT_DIRECTOR_JOURNEY_CLOSEOUT_PROMPT_20261109.md)。
