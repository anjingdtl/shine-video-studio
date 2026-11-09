# 导演模式全旅程唯一性对抗审查

2026-11-09，Windows，`main@185c6a6b2df2852aae7182045caaf335eef59767`，开审时工作树干净，与 `origin/main` 一致。沿测试包、依赖与权重、素材中心、剧本与风格、分镜、导演包、H3 调度、成片回看审查。PR #11 已合入，旧报告 U01–U10 的定向回归重新跑过。本报告记录当前版本仍可复现的反例和两处静态交接缺口。风格引擎、W6A8 和多模型路由按当前源码核对。

本轮在独立 SQLite、临时端口和合成数据上执行。浏览器使用实际 React + FastAPI，隔离工作台保持演示状态。测试中用 `comfyui` 任务验证入队、合同和调度决策，外部请求数为零。交接任务见 [本地 Agent 收尾建设提示词](LOCAL_AGENT_DIRECTOR_JOURNEY_CLOSEOUT_185C6A6_20261109.md)。

## 应当由谁说了算

| 事实 | 权威身份 | 消费方 |
| --- | --- | --- |
| 发包与安装 | committed tree、ZIP 清单、规范化物理安装目标、计划授权快照、持久尝试 ID | 安装器、基础配置、首次向导、恢复入口 |
| 权重与工作流 | variant、catalog、文件 revision/size/SHA、受管实例与工作流 graph/parameter map | 五层就绪、候选方案、Job/Series 准入与 Worker |
| 文本调用 | 角色路由、已入队任务的模型快照、子调用的外部回执 | 阶段配置、路由预览、Worker 及恢复 |
| 当前制作内容 | 当前剧本及大纲、来源明确的分集与逐场 apply 回执、镜头 revision、冻结风格与规则、已批准素材版本 | 导演旅程、分镜、素材采用、导演包 |
| 拍摄与成片 | 用户选择的方案、导演包与范围指纹、Run/Series/Job/Attempt、真实媒体及交付版本 | 入队、续跑、阶段状态、回放、下载 |

解阻优先级按影响定。P0 指正常动作可能停摆、串错执行内容或让未知提交失去保护。P1 指可恢复但主要入口恢复失败、发包失败或回归链断开。修复时把现有拒绝移到它所属的动作边界，复用同一判定并提供续办入口。

## 安装、依赖、权重

### J01 · P1 · Windows 短路径导致 ZIP 清单路径错位

- 实跑 `pwsh -NoProfile -File scripts/build_test_package.ps1 -OutDir <dir> -Verify -KeepExpanded`。当 `<dir>` 在 `C:\Users\ADMINI~1\AppData\Local\Temp` 下，ZIP 已生成，首个清单路径却是 `win64/.env.example`，解包验签查找不存在的路径并退出 1。解包实际含根目录 `.env.example`。使用 `C:\Users\Administrator\AppData\Local\Temp` 的长路径重新打包，730 个文件通过验签。
- `New-FileManifest` 对 Resolve-Path 的根长度做字符串裁切，而枚举结果的 FullName 使用长路径。两种路径表示需要落到同一规范，清单项始终相对于包根。
- 定位 [build_test_package.ps1](../scripts/build_test_package.ps1#L137)。

### J02 · P1 · 安装已合并为同一物理尝试，丢回执时却查不到它

- 两个作品在相同安装根请求同一 ComfyUI 组件，一个勾选独立生图伴随计划。分别授权并执行，均返回 `202` 和同一 `attempt_id`。第二次响应的 `plan_hash` 却是第二份计划，详情中的 hash 是第一份计划，且第二次的 `reused=false`。
- `GET /comfyops/install-plans/{id}/attempts` 对第一份计划返回一行，对第二份返回空数组。即使第一份返回了记录，原始 ORM 字段也没有前端恢复查找所用的 `attempt_id/terminal`。丢响应、新浏览器或清空本地缓存时，按计划找回尝试的逻辑无法可靠续上。测试只到入队，安装 Worker 未运行。
- 修复方向是服务端按物理目标提供可恢复回执，区分请求计划和实际尝试的身份。排队、下载、暂停、可恢复失败、需要对账、已完成都要能在原向导继续。保留已核验字节和真实状态。
- 定位 [api_preparation.py](../backend/shine/api_preparation.py#L259)、[installation.py](../backend/shine/comfyops/installation.py#L207)、[setupExecution.ts](../frontend/src/setupExecution.ts#L181)。

### J03 · P0 · 所有文本工种是 H3 下载安装的前置条件

- 静态交接缺口。两个导演安装入口都先读 `/setup/llm-readiness` 的全量汇总。其 `READY` 要求全部 14 个文本角色连接就绪，包含当前下载无关的工种。主安装动作在请求下载网络、自身计划及安装记录前即因 `LLM_SETUP_REQUIRED` 返回。新库浏览器确实显示「还有 14 个工种没配模型」，与素材、H3、文本可以分别准备的实际能力冲突。
- 安装、检测和复用应可并行进行。确实需要 LLM 的按钮消费该动作实际调用的角色与连接证据。已冻结镜头的 H3 生产不因未使用的文本角色、ASR/VLM 或固定音色缺失停下。
- 定位 [RealProductionWizard.tsx](../frontend/src/components/RealProductionWizard.tsx#L169)、[SetupPanel.tsx](../frontend/src/components/SetupPanel.tsx#L725)、[llm_readiness.py](../backend/shine/comfyops/llm_readiness.py#L124)。

### J04 · P0 · W6A8 文件核验可以借用不匹配的注册状态

- 隔离库中种入候选组件的旧核验状态，改为不匹配的 revision、文件清单和记录 SHA，磁盘无相应缓存字节。`GET /setup/variant-readiness` 对这些组件仍给 `verified=true`，`FILES_VERIFIED=DONE`。这个反例模拟证据与当前目录或 pin 脱节，未运行 W6A8。代码还会接受 SHA 命名且大小相符的缓存文件作为证据，未在此路径验证内容。
- `weight_evidence` 按 `component_id` 和状态判断，候选准入又读取其 `prepared`。应重用既有实际文件核验收据，把 revision、每个文件的 size/SHA 和可供当前受管实例消费的位置绑在一起。缺失或变更时指向原位核对/导入入口。可复用仍有效的核验，避免每次渲染重读全部大文件。
- 定位 [variant_readiness.py](../backend/shine/comfyops/variant_readiness.py#L107)、[profile_gate.py](../backend/shine/comfyops/profile_gate.py#L118)、[api_setup.py](../backend/shine/api_setup.py#L165)。

## 模型、剧本、风格、素材

### J05 · P0 · 改派预览漏报已入队的大纲，Worker 随即停住

- 从正常 API 导入剧本，绑定 `scriptwriter=A` 后排入大纲任务。用新批量路由 API 预览并保存 `scriptwriter=B`，成功返回 200。调用一次实际大纲 Worker 入口，旧任务仍记录 A 的 profile ID，但进入 `NEEDS_REVIEW/STORY_PROVIDER_CHANGED`，未请求 A 或 B。
- 路由预览查 `role_id=scriptwriter`，大纲父任务登记为 `story_outline_run`，执行时却重新读取当前路由。新任务用新路由，已排队任务按已钉住的模型继续，子调用恢复仍从原身份对账。若旧配置确实失效，在原步骤保留已成功单元并解释可执行的恢复动作。核对普通分镜、创意成稿和四阶段父任务的同类映射。
- 定位 [api_llm.py](../backend/shine/api_llm.py#L133)、[story.py](../backend/shine/materialprep/story.py#L81)、[story.py 执行入口](../backend/shine/materialprep/story.py#L491)。

### J06 · P0 · 新剧本能与旧分集和镜头拼成可开拍导演包

- 导入剧本 A，确认 A 大纲和按场次物化的分集，镜头来自 A 的 apply 回执。再导入并选定剧本 B，给 B 已确认大纲。创建新包 201，确认 200，`startable=true`。包的 `script.outline_artifact_id` 属于 B，`episode_plan.plan_id` 和镜头仍属于 A。
- 确认分集时有大纲来源校验，建包时重新取当前剧本并只看旧计划镜头是否存在。旅程第一步还可能被项目里任意旧镜头点亮。应统一解析当前依据、分集来源和应用回执。换依据后，从首个真正变化的环节继续，旧包与旧片保留可查。没有导入文档的手工分镜入口保留。
- 定位 [api_director.py](../backend/shine/api_director.py#L206)、[package.py](../backend/shine/director/package.py#L236)、[journey.py](../backend/shine/director/journey.py#L278)。

### J07 · P0 · 素材分配后缺逐镜入口，历史包还能让采用动作反复失败

- 浏览器可进入素材中心和项目分配。全局版本分配到作品后的 UI 源码只有项目素材/使用回执，前端没有调用 `/asset-center/adoptions/preview` 或 `/asset-center/adoptions` 的入口。已落镜头要转用新主素材时，用户只能绕路。已有的项目素材手工绑定是兼容入口，语义与采用时的镜头 revision 不同。
- API 隔离反例中，旧包确认后再建新草案，旧确认包仍挡住逐镜采用，报 `SHOT_IN_CONFIRMED_PACKAGE`。创建新的草案不会改变旧包命中的条件。另一个反例用同一幂等键把第一镜的采用请求改成第二镜，接口仍返回 201 和第一镜旧回执，第二镜实际无绑定。
- 将分配回执接到当前分镜需求和采用预览，沿用已有用户确认。采用只推进明确选中的镜头 revision，并为当前制作依据生成新快照。历史包保留原镜头/素材，已运行或未知的尝试按原身份保护。不同内容的幂等键冲突应明示，避免假成功。
- 定位 [AssetsWorkspace.tsx](../frontend/src/asset-center/AssetsWorkspace.tsx#L1071)、[api_asset_center.py](../backend/shine/api_asset_center.py#L2032)、[api_asset_center.py 采用入口](../backend/shine/api_asset_center.py#L2128)。

## 开拍、续跑、成片

### J08 · P0 · 一键开拍把空槽位当欠缺，先写绑定再中止

- 用实际 React 组件和网络桩复现两例。Ref2VA 图有主体和画面槽，镜头已绑定唯一出镜人物。空画面槽使按钮报「自动适配还缺：场景/画面参考图」，零次 Series POST。独立后端对照用有画面映射的工作流与仅主体的合法镜头，单镜 Job 返回 201。
- 两镜反例中，第一镜先调用 `onSaveBindings` 写入场景图，第二镜缺人物导致整次无 Series POST。自动适配会从项目已锁定的素材按类别/文件名挑选，额外写入的素材可能也非镜头所需。工作流槽位提供容量，引用义务来自分镜与实际模式合同。
- 制作区与导演包分别维护 `profileId`。从代码可见用户在其中一处选定方案后，另一处没有读取同一份选择，仍可能用本地初始值或包内旧值。阶段切换、刷新、显式 W6A8 与推荐质量冲突，应纳入跨入口测试。
- 定位 [ProductionSeriesPanel.tsx](../frontend/src/components/ProductionSeriesPanel.tsx#L134)、[一键入口](../frontend/src/components/ProductionSeriesPanel.tsx#L455)、[DirectorProductionRunPanel.tsx](../frontend/src/components/DirectorProductionRunPanel.tsx#L96)。

### J09 · P0 · 导演包称可启动，实际派发才拒绝同一输出

- 已登记 Ref2VA 方案无宽高映射。提交导演包 `output={width:768,height:1344,fps:24}`，包确认成功且 `startable=true`，Run POST 返回 201。用该 Run 的快照调用实际批次建 Series 函数，报 `BATCH_DISPATCH_REFUSED`，明细为 `WORKFLOW_SIZE_NOT_MAPPED`。同一输出直接发 Series，提前 422。
- 导演包将 `output` 作为宽松字典冻结，静态编译检查与 Series/Job 并非完整共用输入契约。候选包构建还用 `settings=None`、`require_runtime=False` 的变体检查，与实际准入读取的事实不同。把现有输出、profile、素材引用及生产准备判定收束到同一个无副作用的执行预检，供各入口显示。依赖真实前镜的动态引用继续等待，已知的静态缺口才给修复动作。
- 定位 [package.py](../backend/shine/director/package.py#L637)、[profile_gate.py](../backend/shine/comfyops/profile_gate.py#L186)、[runner.py](../backend/shine/production/runner.py#L480)。

### J10 · P0 · 普通提交可排两次，修订还能遮住外部提交未知

- 两次同 body 调用单镜 Job，均 201 且 Job ID 不同。普通工作台 `useStudio` 的请求未带 `request_key`，现有 API 的同键重接保护没有覆盖该入口。
- 同一镜头种入 `NEEDS_REVIEW/COMFY_SUBMISSION_OUTCOME_UNKNOWN` 和带 graph hash 的旧 Attempt，原请求 409。正常镜头修订将旧 Job 置 stale 并覆盖失败码为 `SHOT_REVISION_REPLACED`；之后同一逻辑镜头提交返回 201。证据只到新 Job 入队，没有重复发出 H3 请求。修订使历史拍摄失效和外部提交对账是两件事，应分别保留。
- 入口重试需复用同一请求键，查找活动 Job/Series/Run 的归属，保持显式新拍和真正独立的镜头可继续。对账从原 Attempt/外部回执与已落盘媒体取证，不能由 stale 或改键推导未提交。
- 定位 [useStudio.ts](../frontend/src/useStudio.ts#L374)、[services.py Job 创建](../backend/shine/services.py#L933)、[镜头修订](../backend/shine/services.py#L1228)。

### J11 · P0 · 当前包与旧 Run 混用进度和控制

- 同作品先开拍包 A，再建尚未确认的包 B。实际浏览器第 5 阶段显示第 2 版草案，第 6、7 阶段却显示 A 的「已排队」以及「暂停」「取消」。隔离 DB 中 B 没有 Run，按钮未执行。另一个 API 反例显式传 `?package_id=B&run_id=A的Run`，返回 200，`scope` 同时带 B 和 A 的 Run。
- 页面用项目 Run 列表首项读取进度、事件和控制，脱离了已按包选过范围的 Journey。服务端对显式 Run 只查项目归属。整个阶段都应跟随同一 scope，覆盖指定历史包、部分集 Run 和项目切换。历史成片仍从明确历史入口按原包/集/交付版本播放与下载。
- 定位 [journey.py](../backend/shine/director/journey.py#L191)、[DirectorProductionRunPanel.tsx](../frontend/src/components/DirectorProductionRunPanel.tsx#L146)。

### J12 · P1 · 当前远端浏览器回归在模型管理表单停止

- `185c6a6` CI 的 frontend build 通过，web-e2e 失败。日志指向 `verify_v13_web.py:698`，旧的 `get_by_placeholder("配置名称", exact=True)` 30 秒超时。现表单对该输入使用可访问名「模型配置名称」，placeholder 已改。后续断言尚未从这次运行获得证据。
- 保留实际表单的创建、连接、结构化生成、路由分配和各自证据断言。修定位器之后重跑整条 CI，剩余失败单独分类。 [当前 web-e2e 日志](https://github.com/anjingdtl/shine-video-studio/actions/runs/42575222272/job/129021390623)

## 验证账本

| 项 | 当轮证据 |
| --- | --- |
| API 与 Worker 边界 | 8 个临时 pytest 反例全部复现，涵盖 J02、J04–J07、J09–J11。用于复现的函数有测试 DB fixture，未访问外部 Provider。 |
| 一键开拍 | 2 个临时 Vitest 反例均复现 J08，同文件原 5 项 W6A8 选择回归同时通过。 |
| 浏览器 | 当轮浏览器记录包含空库基础配置、素材首页和分配、创意/导入、导演包、生产、后期。创建合成作品验证入口，导入样本通过隔离 API 入库。运行页与后期页的旧 Run 误挂载已看到。模型调用、拍摄与有视频的交付未在浏览器执行。可访问性仅观察控件名称和阶段入口，完整键盘/读屏验收待跑。 |
| 后端回归 | `test_director_journey_closeout`、`test_director_assets_closeout`、`test_variant_binding_and_gate`、`test_director_styles`、`test_llm_multi_model_routing`、`test_llm_setup_contract`、`test_package_entrypoints`、`test_offline_e2e` 共 111 项通过。第二批覆盖持久安装、配置合同、新机、素材中心、导演入口、四阶段、Series、Alembic，131 通过、1 跳过。 |
| 工程验证 | Ruff 通过，`npm.cmd run build` 通过（Vite 大 chunk 提示），`npm.cmd test` 32 文件 / 201 测试通过，空库 Alembic 升级到 `f180b0060001`。 |
| Windows 包 | 8.3 路径失败、长路径通过 730 文件校验。长路径样包 SHA-256 为 `443bf4cdaeba413f441c0f8a4da629de8abfea22554aae7c107beab2b484091d`。对解包内脚本运行 Windows PowerShell 5.1 的 `install_once.ps1 -CheckOnly` 成功。包在临时目录用于审查。 |
| 远端 CI | 当前运行的 frontend 成功，web-e2e 按 J12 失败。截稿时 backend 与 backend-windows 尚在运行。 |
| 真机范围 | 新机全装、真实权重下载、真实 LLM、Qwen 生图、音色、H3、连续多镜交付及人工观感均待当版验收。完整后端套件未在本轮本机运行。历史单镜证据按原 commit 记载。 |

若本机临时目录还保留反例脚本，可从仓库 `backend` 目录重跑：

```powershell
$env:PYTHON_DOTENV_DISABLED = '1'
$env:SHINE_DATA_DIR = Join-Path $env:TEMP 'shine-test-config-185c6a6'
$env:PYTHONPATH = (Get-Location).Path
$probe = Join-Path $env:TEMP 'test_shine_director_audit_185c6a6.py'
.venv/Scripts/python.exe -m pytest -s -q --rootdir $env:TEMP --confcutdir $env:TEMP $probe
```

这些反例当前以「观察到缺陷」为成功条件。收尾时将断言改成修复后的行为。

现有风格测试覆盖只读预览、草稿采纳、冻结快照、旧风格兼容以及约束进入分镜和编译器。收尾应沿用这条证据链，给换剧本、换风格和重建导演包提供当前依据下的恢复入口。
