# Shine H3 动态工作流资源自整备与受管连接修复 V2.0（基于最新 main）

> 2026-11-13 | **设计/实施交接文档；不是功能代码或真机 PASS 声明**
>
> **唯一审计基线**：`origin/main` commit `23467957ab7e6c6b9dee10cf0d1b6db011e7dcc1`（2026-11-13 15:29 UTC，Merge PR #14: H3 dynamic per-shot workflows with true second sampling and RelayVSR X2）。
>
> 参照：原 V1 规范 `docs/h3-dynamic-shot-orchestration-relayvsr-x2-second-pass-20261113` 分支中的 `SHINE_H3_DYNAMIC_SHOT_WORKFLOW_UNIFIED_ORCHESTRATOR_V1_20261113.md`；以及 **main** 已合入的 `docs/IMPLEMENTATION_H3_DYNAMIC_SHOT_WORKFLOW_20261113.md` / `docs/ACCEPTANCE_H3_DYNAMIC_20261113.md`。上一版自整备草案（基于 `1a55c415`）只能作为设计参考，不能照抄代码修改点。
>
> **本轮首个样例**：W6A8 `model_variant_id=ref2va-lowend-candidate`，`accelerator_id=native20`，`second_pass.enabled=false`，`upscale.enabled=false`，`source_preference=AUTO`。其他策略遵从原 Registry/GraphCompiler 的支持性与相容性证据。
>
> **隔离纪律**：本方案独立文档分支，基于上述 main SHA，**不能直接推送 main、不能覆盖其他 worktree**。后续独立 feature worktree 实施，并仅通过 PR 合并。Agent 开工需先 fetch origin，重新核对最新 SHA/已合并 PR。

## 0. 结论和目标

**这轮只补资源交付与连接修复，不重建 PR #14 的生产架构。** 用户在导演模式选择 H3 模型/加速/二采/高清后，系统计算真实执行所需模型、VAE、文本编码器、LoRA、节点、Python 包及受管启动参数 → 有界本机扫描/校验 → 缺项列表与准确下载量 → 用户明确授权 → 已审官方/国内可信镜像 AUTO 下载、断点续传 → SHA-256 校验复用 → 安全激活受管 ComfyUI → live ABI/模型绑定检查 → 一镜真实试跑 → 分级允许开拍。

同时修复 4070 用户“连接反复失败”的**安全诊断/恢复**体验：只要可读，应分别解释 HTTP 可达、OS 进程归属、受管启动记录、实际节点、工作流、GPU/硬件门槛、Worker/受管 API 状态；可自动修复的必须经身份证据验证和现有 control plane，不能无条件结束 python.exe 或清空数据库。

不得新增第二下载器、第二安装台账、第二 Worker/GPU 锁/ComfyUI 实例、第二工作流编译器、任意来源安装、对候选模型伪造 SUPPORTED。

## 1. main 代码事实与旧方案的集成差异

| 现有事实（main） | 源码 | V2 的正确增量 |
| --- | --- | --- |
| `GenerationStrategy` 已贯通前端和包/Run/Series/Job/Worker | `backend/shine/strategy.py`、`api_streamline.py`、`frontend/src/components/DirectorShootPanel.tsx` | 资源预检接在**现有**策略选择器及准备页面，不新建拍摄选项或覆盖策略冻结 |
| `compile_ref2va_workflow` 已能按镜构图、N图/N音、真二采；视频参考配方仍报 `VIDEO_REFERENCE_LOADER_UNVERIFIED` | `backend/shine/comfy/graph_compiler.py` | 读取真实选定配方的模型/节点依赖；动态编译器只接收 ready 能力，视频 loader 不因下载能力而自动解锁 |
| v2 mode-capability seal、基线固定模板 drift advisory 已合入 | `mode_capabilities.py`、`service_validation.py`、`runtime_configuration.py` | 扩充**当前选定策略**的依赖和节点 live 校验；不恢复所有旧 Profile 全局硬门 |
| RelayVSR X2 阶段 Job/租约/产物已接入，**真实模型未钉 SHA 且本机 core 节点缺失** | `relayvsr.py`、`relay_stage.py`、官方验收文档 | 仅在固定来源/版本/哈希/许可证后进入资源目录；旧硬阻断保留，真实二倍超分未试过不可宣布 PASS |
| `ControlledDownloader` 具有 `.part`、Range 分段、SHA-256 验证、HF 镜像回退 | `installer.py` / `installation.py` / `installation_worker.py` | 增加正式 `AUTO` / `MIRROR_FIRST` / `OFFICIAL_FIRST` 来源偏好与跨源续传可追溯，**不换下载器** |
| `existing_installation`、`verification_receipts` 有有界扫描与真实文件哈希证据 | `existing_installation.py`、`variant_readiness.py` | 从默认 INT8 扩至选择的完整策略依赖闭包、受管真实模型目录/可信缓存/已授权其他目录、venv 和节点 ABI |
| W6A8 仍为 `CANDIDATE`、`auto_download_allowed=false` | `config/deployment/variants/ref2va-lowend-candidate.json`、`api_setup.py` | 拆开“可按用户批准下载”与“已通过正式生产”的资格；**不改现有候选字段伪装 SUPPORTED** |
| 当前 `generate_configuration` 使用默认 INT8 catalog/requirement/whitelist/argv | `deployment.py` | 受管启动配方改按选中变体精确生成；不得让 4070 因 INT8 15GB 门槛无缘无故阻断 W6A8 候选下载 |
| 连接检查和实际 H3 受管启动是不同流程 | `api_comfy.py`、`runtime_orchestrator/executor.py`、`handoff_startup.py` | 新增“连接/归属/节点/运行配置”分层诊断与修复引导；顶部启动引擎实际走 image renderer，不等价 H3 正式开拍 |

**已实测证据**：main 自带验收报告记录 Windows RTX 5080 16GB 上 INT8+PDD8 单采和二采真实出片、live object_info 通过；Relay X2 实测 BLOCKED；W6A8/4070 尚无已核实的真实 PASS。这个结论不得由自动下载改变。

## 2. 本轮最重要的隐性架构 BUG：动态图仅替换了 DiT，并未替换完整模型栈

当前 `graph_compiler._variant_checkpoint()` 仅给非 INT8 变体替换 UNET；`_model_stack()`、`CLIP`、`VIDEO_VAE`、`AUDIO_VAE`、`REALISM_LORA` 与 Sage/SolAttn 等模型链**仍硬编码 INT8 基线**。

具体冲突：

- W6A8 候选传统图 `config/workflows/ref2va-lowend-pdd-free-2img.json` 用 W6A8 DiT + **INT8 ConvRot 视频 VAE** + FP32 音频 VAE + NVFP4 文本编码器，且 PDD-free；当前动态图却引用 INT8 基线的 **FP16 视频 VAE** 与默认人物 realism LoRA/额外注意力节点。这不只是文件缺失，还存在错误组合风险。
- W6A8 manifest 的 `files` 部分包含可选 Turbo LoRA；其 `required_component_ids=[]`。必须按实际配方及节点输入解析**确切必需组件**，而非直接安装 catalog 全部 5 个模型组件，或因 required=[] 认为零依赖。
- 动态编译器 `BASE_MODEL_NODES` 目前无条件包含 `MiniMaxH3MemoryEfficientSageAttentionPatch`，用户 RTX 4070 SM8.9 报其 `sageattention ... architecture` 错。若当前目标 4070 无兼容包，需**另行审核合法无 Sage 的 W6A8 图配方**，而不是直接删节点绕过。
- W6A8 native20 **不允许**隐式套用 INT8 PDD8、turbo4 或未认证第二采样策略。相容矩阵按变体+加速+二采+Relay+hardware+node ABI 联合定义。
- 若修动态编译器，更新 `execution_identity` / `recipe_sha256` / real graph hash，保持已发布旧 Job/Artifact 的身份不动、不得覆写历史镜头。基线 INT8 现有出片路径须完整回归。

**P0 必须先完成 VariantModelStack / audited recipe 解析：** 从已审核变体 manifest/catalog 解析 `DiT + text encoder + video VAE + audio VAE + optional LoRA + required_nodes + launch preset`，供 ResourceResolver 与 GraphCompiler 共用一套解析结果；避免重复维护模型文件名。Registry 只存引用和配方版本，不给前端传任意未审核 URL。

## 3. 资源依赖解析合同（示意：非当前已运行 API）

### 3.1 输入

`ResourceResolveInput` = 项目 ID、当前生产策略（`GenerationStrategy`）、目标受管 `instance_id`、本机扫描目录授权范围、资源镜像偏好 `AUTO`、当次真实 `catalog/registry/compiler` 版本。

策略变更必须重算依赖，资源解析不能冻结或变更导演包，下载行为与正式提交意图区分。自动扫描只做本地受限 I/O，绝不能静默启动 ComfyUI、加载模型或产生新的生产任务。

### 3.2 输出

~~~json
{
  "schema": "shine.resources.v2",
  "variant_id": "ref2va-lowend-candidate",
  "accelerator_id": "native20",
  "source_preference": "AUTO",
  "policy_sha256": "<derived>",
  "catalog_sha256": "<derived>",
  "compiled_recipe_sha256": "<derived>",
  "instance_id": "<current-managed-instance>",
  "components": [
    {
      "component_id": "minimax-h3-diffusion-w6a8",
      "kind": "model",
      "state": "MISSING",
      "pinned_sha256": "<catalog-hash>",
      "size_bytes": 15983746636,
      "reuse_bytes": 0,
      "action": "DOWNLOAD_WITH_APPROVAL"
    }
  ],
  "node_runtime_gaps": [
    {
      "node_class": "MiniMaxH3MemoryEfficientSageAttentionPatch",
      "state": "ABI_UNVERIFIED",
      "code": "SAGEATTENTION_SM89_UNVERIFIED"
    }
  ],
  "missing_download_bytes": "<computed>",
  "required_disk_bytes": "<computed>",
  "download_eligible": true,
  "trial_ready": false,
  "production_ready": false,
  "snapshot_sha256": "<derived>"
}
~~~

字段必须由真实目录、工作流图、文件哈希和本机 Probe 填充，不能把此示例 JSON 当本机扫描结论。不存在精确 source+revision+size+SHA 的依赖返回 `RESOURCE_PIN_MISSING` 而不是猜 URL。

### 3.3 三道彼此独立的门禁

1. **DOWNLOAD_ELIGIBLE**：来源/许可证/文件哈希可审查，用户看到组件清单和磁盘预算后明确批准；允许下载候选 W6A8，且 `auto_download_allowed=false` 仍禁止无人授权下载；不能将下载资格等价为 `HOST_QUALIFIED`。
2. **TRIAL_ELIGIBLE**：完整文件与节点 ABI 已验证、启动配方/硬件不存在已知硬性不兼容、受管进程归属有证据，用户授权隔离短镜测试；GPU 占用仍走现有 Worker/Lease。试镜失败保留失败类型和原始证据。
3. **PRODUCTION_READY**：被选中的实际组合在**这台设备、这一运行时和这些版本**有足够真实生成/QC 证据，当前证据未失效；未达到不得解除 `strategy_blockers` 生产闸门。可登记主机限定的批准证据，但不得全局把候选 manifest 改为 SUPPORTED。

## 4. 自动扫描：必须解决“本机存在却找不到”

扫描优先级：① Shine 内容哈希缓存（包括可证明的分段残留）；② 受管 ComfyUI 实际 `extra_model_paths` 和 models（包括新量化底模与各 VAE）；③ 精确版本的 custom_nodes；④ 受管 ComfyUI venv 的 dist-info/Torch/CUDA/SageAttention；⑤ 用户明确允许读取的现有 ComfyUI/本机模型目录；⑥ 当前绑定的安装实例/启动配方/服务证据。

- `existing_installation.discover / inspect / verify_models` 和 `verification_receipts` 为复用基础；目录扫描有界、不自动递归整个盘符、不走网络/未知 junction/符号链接。
- 名称、路径、大小只产生 `PRESENT_UNVERIFIED`；复用需全文件 SHA-256 一致。不因文件名相同就读错量化模型。
- 资源文件“已验证缓存”和“当前 renderer 可消费”分开：所有绑定需映射到受管模型目录，修改映射受 maintenance window 控制。
- 自定义节点不仅验证 folder/git commit，还通过当前受管 `/object_info` 校验节点类和 inputs ABI。`/object_info` 失败若是实例没有运行，标 `LIVE_CHECK_PENDING`，**不强迫只为扫描启动 GPU 进程**。
- Python 包须在受管 venv 内核实轮子/版本与 CUDA/Torch/SM，不能用系统 Python 的 `pip show` 来代替；Sage 未兼容时显示“节点实现不兼容”，不是“模型没有下载”。
- 证据以 `catalog_sha + component_sha + node_abi + instance_id + OS host fingerprint + launch_preset + compiler_recipe_sha` 钉扎，变化后仅对应能力变 STALE，保留历史成片与审核。

## 5. W6A8 作为实例：精确清单而非整 catalog 盲下

当前 W6A8 catalog：`config/deployment/h3-ref2va-lowend-candidate-windows-x64.json`，传统配方：`config/workflows/ref2va-lowend-pdd-free-2img.json`。

基础（不用 turbo4、不用二采、不用 Relay）应经真实配方闭包至少检查：

| 组件 | catalog 组件 ID | 体积（锁定文件） |
| --- | --- | ---: |
| W6A8 Ref2VA DiT | `minimax-h3-diffusion-w6a8` | 15,983,746,636 B |
| NVFP4/AWQ 文本编码器 | `minimax-h3-text-encoder-nvfp4` | 15,687,142,551 B |
| INT8 ConvRot 视频 VAE | `minimax-h3-vae-int8` | 2,811,065,184 B |
| FP32 音频 VAE | `minimax-h3-audio-vae-fp32` | 605,254,808 B |

四文件合计 **35,087,209,179 字节 ≈ 32.68 GiB**（仅这些权重，不含其它程序、解压、Python wheel、磁盘预算）；按已验证复用计算**实际缺失字节数**。传统图有 SageAttention 与 LowVRAMAttention，但能否运行取决于当前 RTX4070 SM8.9 的实际节点实现/运行参数，不因权重小就认为 12GB 一定可跑。

本轮样例绝不自动下载安装 Turbo4/PDD8/RelayVSR/额外角色 LoRA；只有用户选中已审核相容策略时才加入对应依赖。

## 6. AUTO / 国内镜像 / 国际源与断点续传

**复用** `comfyops.installer.ControlledDownloader` + `InstallationPlan` / `InstallationAttempt` / `installation_worker`、内容哈希缓存、分块 Range 与 `.part`、暂停/恢复、审计，不新增并行下载器。

- `AUTO`：对**已经列入白名单**的官方固定 revision 源及相同字节镜像作轻量有界连通性/时延检查，按可用性选择，源错误时在校验合理的 Range/ETag 语义下换线路续传；元信息不可信时安全丢弃部分数据重取。
- `MIRROR_FIRST`：HF 已审文件可优先采用 `hf-mirror.com`；无法访问再回官方。社区镜像不是官方保证。
- `OFFICIAL_FIRST`：优先 `huggingface.co`，用户允许回退时切到已审 HF 镜像。
- GitHub/PyPI/PyTorch 节点/轮子源**不能套 HF 域名替换**；没有可信镜像则明示仅官方源，不能允许任意用户 URL/任意 redirect host。
- 下载前预览：项目/选中策略、缺项、来源、许可证、每文件体积、复用/待下大小、剩余空间、实验与生产资格的区别。只有显式批准并用现有审批 token/plan identity 才能下载。
- 安装计划冻结 `strategy_sha、variant_id、catalog_id、component IDs/sha、source_preference、scan snapshot、target instance`，后台重新审核变体与 catalog 精确一致。禁用“无 variant_id 用非默认 catalog 绕过门禁”、禁止 candidate 借 SUPPORTED 安装计划审核。
- 稳定显示分文件及总进度、正在使用线路、校验中、暂停/继续、重试次数和准确错误原因；关闭浏览器或断网后恢复**同一个 Attempt**。
- 镜像只作数据传输替代；上传/导入到现有受管 ComfyUI 时仍做完整 SHA、size、安全目标路径、许可证核验。安装节点/更换 venv/重启 ComfyUI 需要安全维护窗口。

## 7. 连接失败与受管实例“反复点击没有效果”的专项修复

### 7.1 已有截图日志信息（需与当次机器复核）

历史 4070 用户记录可见 `/shine-handoff/status` HTTP 200，但随后出现：
- `COMFY_HANDOFF_OWNERSHIP_UNVERIFIED`：受管 PID + OS 创建时间 + Windows Job Object 成员 / handoff 身份不匹配；
- `COMFY_REQUIRED_NODE_MISSING`：当前启动的实例缺少真正要求的 ComfyUI 节点；
- `COMFY_STARTUP_ALREADY_RECORDED`：同 instance 的启动日志未收束，重试被防重门拦截。

这几项不能归因于网络超时，也不能通过继续点击“连接”解决。新 main 的 `renderer_supervisor._verify_binding/_control` 有严格归属验证；`handoff_startup.start_managed_renderer` 在写新意图前检查既有 `renderer-startup:*`；任何已有记录都可能使重复启动拒绝。现有 `runtime_orchestrator.executor._reconcile` 主要针对**image** renderer 的死记录做安全清理，H3 记录另有 `handoff_startup.reconcile_managed_startups` 等流程；必须审查跨入口对账是否可达、是否正确归一到同一个 UI 操作。

### 7.2 三层连接事实（UI 与 API 强制区分）

1. `TRANSPORT`：API/Web 是否活着；目标地址/8188 listener 是否存在；ComfyUI `/system_stats` / `/object_info` 是否 HTTP 可达。
2. `OWNERSHIP`：listener PID、创建时间、进程是否由 Shine 当前有效 Job Object 管控；受管 handoff token digest、adapter hash/状态是否一致；是否为另一个用户手工启动的外部实例。
3. `CAPABILITY`：本次已选模型路径可消费、必需节点与输入 ABI、venv、显存/内存、策略所需版本是否 ready。

前端分别展示：“服务连接正常但不是当前受管实例”、“原实例不属于 Shine，不能自动接管”、“节点缺失/需安装”、“遗留启动记录正在核对”、“引擎已就绪”。不再把所有错误统一显示“连接失败”。

### 7.3 必须实现的安全恢复

- `CHECK_CONNECTION` / `probe` 只能探测，不修改外部进程/证据，也不能冒称“连接后马上可开拍”。
- 在现有 `runtime_orchestrator` 新增**受控恢复分支或增强原 reconcile**，对 H3 与图片渲染器共享安装实例做**同一套状态投影**。仅能在已验证 PID/creation/Job membership、port free、无 in-flight / UNKNOWN / GPU Lease 争议时安全完成“已退出旧记录收束”；有未知提交时仍 `NEEDS_REVIEW`。
- 外部进程占用 8188 时，提示用户手动关闭自己启动的 ComfyUI/选择安全受管安装配置；禁止 Shine 无归属证据直接结束外部 `python.exe`，也不能自动接管未知运行进程。
- live node 缺失，报告 **exact node classes**（例如 H3/Sage/Relay），引导到本次资源解析器补齐；不要清空启动/运行数据库来掩盖 `COMFY_REQUIRED_NODE_MISSING`。
- UI 的“启动引擎”当前走 `runtime_orchestrator.executor._ensure() -> ensure_image_renderer`，不等于正式 H3 镜头启动（H3 由 Worker/Series 受管启动）。需要显式文案及同一安装实例的状态解释，避免“图片引擎已启动 → H3 应该可用”的错误推断。
- 错误结果应显示 operation_id、简短原因、可用的唯一恢复动作和可复制的**脱敏诊断包**（版本、实例 ID/端口/进程身份摘要/状态码/节点缺失；不含 token、完整用户文件、API key）。
- 同一 state_version + idempotency，重复点击只能观察**同一次控制操作**，不不断创建新 startup intent；不改变当前 ResourceLease / 意图先于外部提交的安全纪律。

### 7.4 不接受的“修复”

禁止任意 `taskkill /F /IM python.exe`、删除所有 `renderer-startup` SQLite 记录、删除历史 Job/Lease、改成“HTTP 200 就自动绑定”、绕过 ownership/UNKNOWN 检查、另起第二 8188 服务、自动升级全部 ComfyUI 节点。必须先还原确切因果链，再做最小修复。

## 8. API/前端集成位置（建议语义；以实际合并代码为准）

只扩展现有 `api_setup.py` / `runtime_orchestrator/api.py` / `api_streamline.py`，不新增独立 API 服务器：

| API 语义 | 建议路径 | 输入/输出与副作用 |
| --- | --- | --- |
| 解析真实策略资源 | `POST /api/v1/setup/resources/resolve` | project + strategy + instance → 缺件、复用、节点 ABI/venv 和 blockers，只读 |
| 预览下载计划 | `POST /api/v1/setup/resources/preview` | resolve SHA + `AUTO`，返回分组件大小/源/许可/磁盘；无网络下载 |
| 生成授权计划 | `POST /api/v1/setup/resources/install-plan` | 用户点击同意 + snapshot/hash，后台重核真实 catalog、variant 及资格；复用原 InstallPlan |
| 下载执行与恢复 | **已有** install execute/status/pause/resume/cancel | 不另建 endpoint / 数据表 |
| 安全绑定/运行配置 | `POST /api/v1/setup/resources/activate` | 通过已有 lifecycle operations，busy 则安全延期 |
| 真实实验试镜 | `POST /api/v1/setup/resources/trial` | 走原 Worker/Job/Attempt/ResourceLease，严格与生产批准隔离 |
| 连接事实与安全修复 | 扩充 **已有** `/runtime-orchestrator/status` / `probe` / `reconcile` | 连接三层事实+原因/可安全动作；不替代 Worker、不跳过所有权证明 |

导演 UI：`DirectorShootPanel` 策略选择器增加“资源检测”一块及“查看并下载缺失资源”，若选 W6A8 未安装仍允许查看它和下载入口，但“开拍”必须被原 `strategy_blockers` 拦住直至证据充分。`DirectorSetupPanel` 继续入口；`EngineStatusEntry` 明确引擎类型、状态、错误原因和建议唯一恢复动作；专业设置保留可读的具体节点/实例列表。

普通用户无需 N 图/N音/N视频、JSON、手动 pip、人工选择节点，不在导演模式再加第八个阶段。

## 9. 开发工作包与独立 worktree 建议

- **V2-WP0（半天以内可分解，不承诺工时）**：读取 `AGENTS.md`、最新 main、PR #14 实施/验收、报错案例；输出真实调用链/共享文件冲突矩阵。固定基线 SHA；建立独立 worktree 与增量 feature branch。
- **V2-WP1**：先实现共享 `VariantModelStack`/ResourceDependencyResolver（配方与 catalog 精确匹配）及 W6A8 组合修正，增加负向测试，保证 INT8 基线完全不回退。
- **V2-WP2**：精确扫描/哈希/节点/venv/当前受管实例、missing bytes 报告、资源复用，零无授权下载。
- **V2-WP3**：候选“明确批准后下载”独立于生产认证；AUTO/国内优先/国际优先、原下载器跨镜像 Range+SHA、pause/resume、多按防重。
- **V2-WP4**：运行配置按变体生成、受管安全激活，Sage/SM8.9 固定 wheel 或具名合法替代图的验证，用户授权进行真实短镜；不得抹掉候选状态。
- **V2-WP5（紧急故障修复，可与 WP1 前置并行设计、改共享文件时串行落地）**：连接三层诊断 + H3/image 两条启动日志的安全 reconcile + UI“重复点连接”反馈 + 端口归属冲突/节点缺失路由补齐。
- **V2-WP6**：Relay 模型在核实固定哈希和 ComfyUI core 兼容后进入相同自整备；若不能钉 pin 则输出 UNPINNED/BLOCKED 而非假 READY。
- **V2-WP7**：导演设置/环境卡接入、真实用户模拟、所有正反回归、PR；保留后端 8 分片并行与前端 no-file-parallelism 验收纪律。

文件所有权优先：新增 `backend/shine/comfyops/resource_resolver.py`、`resource_inventory.py`、`download_sources.py`（若 main 尚无同义模块）；小范围修改 `graph_compiler.py`、`strategy.py`、`api_setup.py`、`installer.py`、`deployment.py`、`variant_readiness.py`、`runtime_configuration.py`、`handoff_startup.py`、`runtime_orchestrator/*` 和导演/引擎前端。所有共享代码的变更要有回归及证据，旧 schema/历史快照不能强制迁移覆盖。

## 10. 验收矩阵（必须逐项报告，不混用工程/真机）

| ID | 场景 | PASS / 阻断标准 |
| --- | --- | --- |
| T01 | W6A8 native20 选择后依赖解析 | 精确列 W6A8 DiT、NVFP4 text、INT8 video VAE、FP32 audio VAE；不能混入 INT8 FP16 VAE/无关人物 LoRA |
| T02 | 用户开启 PDD/Turbo/二采/Relay | 只增加已审核相容配方依赖；未知组合具名拒绝 |
| T03 | 资源存在/文件改名/同名错哈希 | SHA 正确复用、错字节不可复用 |
| T04 | 缓存有文件但当前 renderer 未绑定 | 展示 HASH_VERIFIED 而非 CONSUMABLE/READY |
| T05 | 未授权或来源/许可/hash 不可信 | 零下载、零安装、无生产资格 |
| T06 | AUTO 官方不可达，镜像可达，Range 断流 | 可信源切换且部分续传，最终 size+SHA 匹配 |
| T07 | 用户主动国内/国际源优先；某源没有可信镜像 | 符合选择，仅已有白名单源，不猜 URL |
| T08 | 浏览器关闭/重复确认/崩溃恢复 | 单 InstallPlan/Attempt，恢复原 .part，不双下 |
| T09 | 变体与 catalog 不一致、candidate 省略 variant_id | 服务端阻止漏过门禁；不能把 candidate 当 production |
| T10 | RTX 4070 12GB SM8.9 缺/不兼容 SageAttention | 明确 NODE_RUNTIME_INCOMPATIBLE；如无合法修法保留 BLOCKED |
| T11 | W6A8 下载后仍未真机试跑 | download PASS、trial PENDING、production BLOCKED |
| T12 | 两镜不同图、基线 INT8 PDD8 二采 | 原 PR #14 的真实/离线功能回归完全保持 |
| T13 | 8188 上外部 ComfyUI 已在运行 | 不接管、不杀进程、说明如何手动让出端口 |
| T14 | `COMFY_HANDOFF_OWNERSHIP_UNVERIFIED` | 准确展示身份不一致；不可错误释放 GPU lease |
| T15 | `COMFY_STARTUP_ALREADY_RECORDED`：旧进程证实已退出且端口空闲 | 通过原控制平面安全收束旧记录，随后启动成功、不双启 |
| T16 | 旧启动记录无法确认进程归属 | 严格阻断且有可操作人工说明，不重复向外部提交 |
| T17 | `COMFY_REQUIRED_NODE_MISSING` | 列具体缺失节点，按目录权限提供已审节点安装，不冒称 HTTP 不通 |
| T18 | 无 Worker、API down、Comfy HTTP down | 分别报告，不重复生成死循环控制意图 |
| T19 | H3 与 Image 同一安装实例交替 | 唯一真实受管 renderer/port/ResourceLease、无双启动 |
| T20 | RelayVSR X2 缺两权重 pin/旧 ComfyUI revision | 不可自动乱下载，显示精确 blockers、补齐后真机 X2 QC |
| T21 | 旧 Job/已交付视频/旧 profile/历史 evidence | 只读无损，不因新配方覆盖 |
| T22 | 安全性：恶意跳转/路径穿越/坏 SHA/缺许可证 | 严格拒绝、日志脱敏、不删除用户资源 |
| T23 | 4070 真机（内存/pagefile/节点未知） | 实际试镜成功才标该机 PASS，失败保留根因/峰值与工件，不假绿色 |
| T24 | 新装 Windows 普通用户从空目录开始 | 导演设置里扫描→下载确认→进度→校验→安全启动→试镜全程不写 JSON/CLI |

三类报告必须分开：`ENGINEERING_PASS`（代码/离线/接口）；`REAL_HOST_TRIAL_PASS`（精确主机+版本+真实工件）；`PRODUCTION_READY_FOR_THIS_HOST`（完整已选策略/当前机器真实合格）。各未运行项为 NOT_RUN、受阻项 BLOCKED。不得用 Mock/HTTP 200 代签真机。

## 11. 交付与保护

1. 独立 feature 分支及 PR，设计文档与源码差异、节点/模型来源审计、详细部署说明、失败恢复；
2. 完整单元/集成/前端与 8 分片后端回归日志；Windows 真机证据独立落于 .local-acceptance，绝不提交模型、影片、SQLite、密钥、隐私日志；
3. 必须证明旧 INT8 主路径、不使用 Relay 的出片、H3 二采、素材中心正常，不因低显存增量更复杂；
4. 代码放行条件：W6A8 资源路径可被发现、经用户同意后安全下载、可精确绑定；未知 GPU 兼容处保持真实阻断；连接问题可诊断、可在证据充分时安全恢复。
5. CI 因额度不能跑则标 NOT_RUN，不造 PASS；产品 Ready 不能由 schema 改名或测试 mock 冒充。

## 12. 开发 Agent 提示词提纲

拉取最新 origin/main、V2 文档，先比对 PR #14 真正落地的 GraphCompiler / strategy / relay / runtime control plane，再实施 WP0-WP7。此处 **最急需先做**：W6A8 完整 stack 与启动配方的解耦、安装授权门禁、当前 4070 故障的归属-节点-启动日志三层诊断。明确禁止重新造旧 V1 能力、强行删除 startup 记录、直接杀外部 Python 进程和放开候选生产门禁。交付独立 PR、分级完整验收报告。