# 给 Shine 本地开发机 Agent 的任务指令｜V4.1 制作环境一体化闭环（含 ComfyUI / Qwen 接入）

你是 Shine 视频工作台本地研发 Agent。此前 V3.1“补按钮与预检”的提示词、V4.0 初稿均被本 V4.1 指令替代。必须以 `SHINE_SETUP_ONE_STOP_V41_DESIGN_20261114.md` 为主建设方案，先读取全文；不得将工程简化为几个链接、修几个 if 或新建第二套安装器。 方案已正式推送到独立远端分支 `docs/shine-setup-one-stop-v41-20261114`，**不在 main 中**。必须先执行 `git fetch origin main docs/shine-setup-one-stop-v41-20261114`，再用 `git show origin/docs/shine-setup-one-stop-v41-20261114:docs/SHINE_SETUP_ONE_STOP_V41_DESIGN_20261114.md` 读取完整原方案。不要用 V3.1 或 V4.0 初稿替代。

## 基线与开发隔离

- GitHub: `anjingdtl/shine-video-studio`。
- 原项目：`F:\ClaudeWorkSpace\projects\shine-video-studio`。
- 方案编制时 main：`8e3e1e07850549df57511e0f330eb9ea22543dc2`；开工时必须 `git fetch origin` 并以**新的最新 `origin/main`** 为基线，重新检查最新 PR/CI/AGENTS.md。
- 新独立 worktree：`F:\ClaudeWorkSpace\projects\shine-video-studio-setup-v4`。
- 独立分支建议：`feat/shine-setup-one-stop-v4-20261114`。
- 禁止直接 push main、force push、删除原模型、接管外部进程、重写其他 Agent 工作树。

## 建设目标

**彻底消除“反复检测、重复下载、用户人工登记、切换页面寻找阻断”**。唯一完整操作页为「设置 → 制作环境」；导演基础配置与其它向导只使用**同一服务端状态事实**，保留跳转和高级诊断，但不能再独立决定“已就绪”或另开一条安装链。

完成如下闭环：

`自动检测(只读) → 发现已存在字节 → 流式哈希核验 → 自动登记既有 Package/Receipt 事实 → 确认缺失 → 一次变更授权 → 缺文件自动下载/缓存复用/手动补档 → 原 Worker 完成安装 → 自动补全项目 Profile → 已获授权的实例绑定和服务核对 → 真实试镜独立授权 → 当前证据决定是否可投产`。

必须尊重“有文件 ≠ 核验成功 ≠ 已登记 ≠ 实例已绑定 ≠ 工作流验证 ≠ 真实投产”的分层。不得给候选 H3 模型或未真机出片打 `PRODUCTION_READY`。

## 最高优先级：不要再让用户自己选择“接入受管渲染器”

用户截图出现「还没有为本机生成受管运行配置，出图渲染器无法启动」与 `IMAGE_RENDERER_RECIPE_MISSING`，另有提示要求用户选 A「硬链接转受管」或 B「直接用现有方案」。**这些都是内部设计方案，不允许成为普通用户操作选项**。必须由本次改造收束到同一个「设置→制作环境」的自动校验与恢复流程。

经 main 代码初审，已经确认：`api_material.bind_image_runtime()` 在缺少 recipe 时仍允许 `image-runtime.json` 保存；`image-readiness` 的 configured 只要求绑定字段；`comfyops/image_renderer.load_image_recipe()` 真正启动却强制需要 `setup/managed_recipe.json`。此外 `SetupCenter.prepare()` 在 `next-instance-plan` 传端口/variant，到了 `/setup/configuration`、`/setup/profiles` 又遗漏该身份，默认配置端口可能回退 8188、变体回退默认。先写 RED 回归测试复现，再修真实链路，禁止只改错误中文。

本次必须完成：

1. **一份机器级安装/运行身份**贯穿检测、adopted 外部安装确认、模型权重与 Package/Receipt 自动登记、H3 variant/Profile、真实端口、Qwen Image runtime 绑定、受管 recipe、服务节点验证及真实小样。严禁各入口自行采用默认 8188 或默认 H3 变体导致分叉。
2. **自动补 recipe**：在一次变更授权覆盖且实例空闲、身份可证明时，由现有 Worker/统一运行时控制链自动生成（或合法重建）`<deployment_root>/setup/managed_recipe.json` 并同步/核对 `image-runtime.json`。文件已绑定但 recipe 尚未生成只能报告 `PENDING_RECIPE`，绝不能算图片生产 READY；已验证记录直接复用。
3. **已有外部 ComfyUI 优先无损复用资源**：先确定是否能用既有 `adoption.py` 严格条件复用其安装、模型和节点。符合条件则只做首次必要身份授权，复用已有 `deployment.generate_configuration()` adopted 分支，禁止要求用户重复下载/手工登记或一律“转受管”。不满足时在获得明确授权下安全准备 Shine 可证明自己拥有的运行会话，优先复用现有已核验模型文件与受信路径映射，避免硬链接/复制大模型成为默认步骤。
4. **保护 8188 上的外部进程**：实机查进程 PID/所有权和运行中的任务；非 Shine 所有绝不强杀、强接管或标记为 `renderer_owned=true`。若 GPU/端口占用无法安全继续，用户仅看到“发现另一程序正在使用 ComfyUI；请关闭它后重试，原模型不会丢失”，且只有必要时才要求操作。不能声称可通过“检测到”就取得运行时所有权。
5. **真正的图片链路**：从 Qwen 固定清单和哈希到实例模型家族绑定 → 当前真实 `/object_info` → 图参数合同 → 授权一次真实 character/scene/prop 小样 → 技术 QC 和有效证据持久化。H3 与图片分别显示状态、共用物理实例/同一 GPU 调度，无第二套 renderer/controller。图片验证 PASS 不等于 H3 真实出片 PASS，非受管外部服务输出不得冒充 owner-verified 正式证据。
6. **错误自愈和中文交互**：从设置页消除要求选择 A/B、手工“生成受管配方”、裸 `IMAGE_RENDERER_RECIPE_MISSING`。能在当前授权和空闲条件内自动修复就自动修；不可修时只展示系统已确定的唯一人类动作。所有其他入口统一展示服务器状态并跳往该设置页。

## 开工前必须查透的代码

- `frontend/src/App.tsx`
- `frontend/src/components/SetupCenter.tsx`
- `frontend/src/components/SetupPanel.tsx`
- `frontend/src/components/DirectorSetupPanel.tsx`
- `frontend/src/components/RealProductionWizard.tsx`
- `frontend/src/setupExecution.ts`
- `backend/shine/api_setup.py`
- `backend/shine/comfyops/existing_installation.py`
- `backend/shine/comfyops/adoption.py`
- `backend/shine/comfyops/verification_receipts.py`
- `backend/shine/comfyops/variant_readiness.py`
- `backend/shine/comfyops/deployment.py`
- `backend/shine/comfyops/connectivity.py`
- `backend/shine/comfyops/installer.py`
- `backend/shine/comfyops/storage.py`
- `backend/shine/comfyops/installation.py`
- `backend/shine/comfyops/installation_worker.py`
- `backend/shine/comfyops/environment_repair.py`
- `backend/shine/comfyops/resource_resolver.py`、`/setup/resources/install-plan` 的动态工作流资源链
- `backend/shine/comfyops/image_renderer.py`
- `backend/shine/comfyops/managed_recipe.py`
- `backend/shine/comfyops/runtime_configuration.py`
- `backend/shine/comfyops/deployment.py::generate_configuration`
- `backend/shine/api_material.py`（`/setup/image-readiness`、`/setup/image-runtime`、`start`、`validate`）
- `backend/shine/materialprep/runtime.py`
- `backend/shine/materialprep/execution.py`
- `backend/tests/test_image_managed_renderer.py`
- `backend/tests/test_material_render_ownership.py`
- `backend/tests/test_material_render_e2e.py`
- `backend/tests/test_image_worker_loss_recovery.py`
- `backend/shine/worker.py`、`backend/shine/runtime_orchestrator/*`
- `scripts/bootstrap.ps1`
- `docs/SHINE_MAINLINE_V3_{BASELINE_AUDIT,IMPLEMENTATION_REPORT,ACCEPTANCE_MATRIX,MODULE_OWNERSHIP}.md`

注意：当前 `/setup/existing-installations/verify` 已会调用 `register_verified_packages`，不能复制出一个新的权重登记系统；`SetupCenter.detect()` 当前只使用 `existing.installations?.[0]`，是必修风险；设备级资源不应该随项目反复下载；当前 `SetupPanel` 和 `SetupCenter` 两处还有重复安装流程，需真正收束在设置页。

## 工作包

**WP0｜基线审计与复现**。生成同一台机器、多个项目、多个 ComfyUI 安装的身份/状态调用图；检查后端现有门禁、所有设置入口、SQLite/receipt/attempt 模型；设计失败用例，先 RED 再修。

**WP1｜唯一事实与唯一页面**。聚合服务端快照，分离设备级资源和项目级 Profile/Job；不让前端临时缓存作资格判断；打开设置页自动无损检测，不需要先手动点“检测”。任何页面只从统一快照映射状态和唯一下一步，不新建第二份状态机。

**WP2｜零人工登记与自动复用**。在受信根目录内发现所有实际候选，而非固定第一个；已选择的外部安装身份记住并复用。先限速/后台哈希，再复用现有 `verification_receipts`、`register_catalog_packages`、`register_verified_packages` 等记录完整内容证据。自动修复缺失事实记录，幂等；原文件不改动。对于缓存但未安装、安装但未绑定、工作流未注册，按事实分别处理。外部实例接管需首次明确授权，禁止自动改外部 ComfyUI。

**WP3｜一次授权、后台持续推进和 H3/Qwen 统一身份、配方补建**。变更预览一次清楚显示下载字节、组件、来源、许可证、部署目录和影响；使用现有 grant、setup/install-plan、installation attempt、Worker、runtime_orchestrator、GPU lease；浏览器离页不应中断；成功的步骤永远不重复。工具安装/模型下载/补绑定/工作流登记/运行配方自动生成与合法重建/Qwen image-runtime 对齐/服务合同验证均在已授权范围内按差异补齐；检查非默认端口与所选 H3 变体不会丢失。运行时正忙、断网、电脑重启等继续现有恢复/UNKNOWN 规则。

**WP4｜自动+手动共用缓存**。动态 H3 工作流（加速 LoRA、RelayVSR X2、二次采样）的资源缺项必须复用现有 resource_resolver/安装计划与统一 Worker；不可要求下载所有非当前选用的可选资源。按缺失文件做下载预检，官方失联镜像可达时 AUTO 能回退，已有完整哈希缓存时不做联网准入。设置页缺项按组显示逐文件固定可信下载地址、大小、SHA、真实目录、复制/打开按钮。建立 `<deployment_root>\manual-downloads\<catalog>\<component>\<filename>` 暂存目录并自动受控对账，用户放文件后系统校验并导入 `<deployment_root>\isolated-packages\.cache\<sha256>`，随后原 Worker 自动继续，无第二下载器、无再次手动“登记”。基础工具 ZIP 要改造原 bootstrap 受控复用，不得用模型缓存逻辑假装已支持。

**WP5｜统一体验、外部 ComfyUI 接入与异常**。设置页展示状态、5 阶段进度、能力卡、唯一下一步、缺失文件详情和“诊断展开”；导演其它入口只读摘要和导航；区分「文件已核验」「工作流已验证」「真实试镜已通过」「可以投产」。只在需要人判断或计划变化时请求授权。深浅及六个既有主题都要可读。大型 wheel 列表默认折叠。对原外部 ComfyUI 8188、缺 recipe、已绑定但不能启动、Qwen 小样待验证等情况，用户只看到可理解的状态与单一步骤，不再接触工程 A/B 策略。

**WP6｜回归验收与 PR**。确保 0 重复登记、0 重复下载、同机多个 ComfyUI 首次明确选择后不再反复询问、浏览器关闭继续任务、人工补档不用命令行、失败可追溯。实现方案 MD §7 的全部用例（包含新增 23–32），并检查旧 V3 问题和 H3 准入递归闭包。

## V4.1 追加硬验收——必须有具体测试与真实证据

- 先故意造成 `image-runtime.json` 存在而 `managed_recipe.json` 缺失：旧流程应 RED，新流程能报告 `PENDING_RECIPE` 并在批准范围下自动补建，最终生成真实可执行的匹配配方，不能误报图片 READY。
- `next-instance-plan` 选择 8189，后续配置/Profile/图片绑定均使用一致 installation/variant/port；后端不能静默退回 8188。
- 已有外部 ComfyUI 8188 正在运行且 PID 不是 Shine：零 kill、零假 owner、零异常覆盖，能保留当前文件/用户数据；必要时单一用户动作；允许合法的零拷贝模型文件复用。
- 已有被批准 adopted 安装（包括符合身份条件的 H3/Qwen 配置）：后台完成自动登记、复用安装根、recipe 构建、图工作流绑定，无手动“转受管”。adoption 源码/节点/adapter/端口/身份不一致时严格拒绝并说明原因。
- Qwen Image 可独立准备；一套已运行的 H3 不得和 Qwen 重复抢 GPU；第一次真实小样须用户明确允许。测试状态阶梯：文件核验、recipe、renderer ownership、图节点校验、`RENDER_VERIFIED` 各不相互冒充。
- 测试先覆盖 README/用户指南中的实际点击路径，确认用户从“设置”到“真实出图”无需手工登记、改端口、改 JSON、找文件或选择技术方案。

## 安全/验收要求

- 仅读取的 `GET` 不得悄悄写文件、启动服务或登记；自动事实登记通过可审计的 POST/Worker 对账任务执行。
- 每个已发现文件必须按固定 SHA-256/size/revision 验证，不能因历史数据库状态判定真实可用。
- 使用当前 storage root，不能写死开发机 F 盘或用户 D 盘。
- 用户首次外部实例确认只确认“采用哪台 ComfyUI”，不是重做模型登记。
- 安装仍按唯一 target lease、批准 hash、H3 variant gate、candidate ack、UNKNOWN 机制运行。
- 对真实 H3 单镜测试须证据和资源授权；RTX 4070 不具备本地硬件，豁免真机测试；不得用 mock 冒充真实 PASS。
- 后端全量 8 分片按 `AGENTS.md` 且 `CODEBUDDY_SAFE_DELETE_ENABLED=0`，前端全量 Vitest + tsc + build，Ruff、Alembic 隔离库与 Windows 启停均必测。
- 生成 `docs/SHINE_SETUP_V4_ARCHITECTURE.md`、`docs/SHINE_SETUP_V4_IMPLEMENTATION_REPORT.md`、`docs/SHINE_SETUP_V4_ACCEPTANCE_MATRIX.md`、面向普通用户的一页操作说明；对新用户数据库升级可兼容，不得破坏真实安装。

你被授权自主进行非破坏性的代码修改、重构、运行测试、修复故障、分步提交、推送独立分支、创建 PR。必须在工程测试/审查门槛通过后通过 PR 合并主分支，并校验新 `main` SHA；未通过或未执行的检查按 PENDING 报告。不要把“制作环境 V4”做成第二套控制系统，也不要只加一个手动下载按钮。现在先做事实审计与调用图，然后按工作包连续完成。