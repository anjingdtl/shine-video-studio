# Shine H3 模块化逐镜动态工作流与统一调度融合 V1.0 建设方案

> 日期：2026-11-13（Asia/Shanghai）
>
> 目标仓库：anjingdtl/shine-video-studio
>
> 方案来源：远端 main 的源码审计；方案分支 docs/h3-dynamic-shot-orchestration-relayvsr-x2-second-pass-20261113
>
> 性质：**建设规范，非已实现声明。** 此文档分支仅交付方案，不修改产品代码；本地 Agent 须独立 worktree 实施、复核与验收，再发 PR。
>
> 用户已确定：**用户选 Ref2VA 量化模型与加速、二采和高清策略；系统根据每一镜的分镜和素材自动编译 N 图 N 音 N 视频工作流；所有工作归现有统一 ComfyUI 调度层，不另建调度器；本轮一并完成 RelayVSR X2 和真正的二次采样能力。**

## 0. 结论和不可更改原则

Shine 保持现有导演模式七阶段体验、Ref2VA-only 和单一 Windows 本地真实 ComfyUI 生成路径。改造的核心不是增加 2img、6img、9img 静态 Profile，而是让 **已冻结的单镜需求 + 用户指定的底模/加速/增强策略 + 本机能力与资源证据 → 每镜一份可重现的执行计划 → 运行时合法 ComfyUI 图**。所有生产任务仍走现有 DirectorPackage → ProductionRun → ProductionRunBatch → ProductionSeries → Job/Attempt → Worker/ComfyUI → QC/Delivery；进程与 GPU 资源控制继续走 runtime_orchestrator、comfyops 的既有单一控制平面和租约账本。

硬约束：

1. 用户选的是模型与生产策略，不手选工作流、槽位数量或 ComfyUI 节点。画面参考按镜头自动选择，不能为了填满九图而多塞素材。
2. **每镜独立图，不等于每镜启动一个 ComfyUI。** 相邻镜头图不同，依然在同一受管 ComfyUI、一个受控 Worker / GPU 独占租约中串行运行。不得建立第二套服务启动器、任务队列、GPU 锁或状态机。
3. 保留且复用现有 H3PromptCompiler、按镜资产绑定、Style Bible、Run/Series/Job/Attempt、submit-intent、幂等、外部 UNKNOWN 对账、QC/人工审片、历史成片。
4. 本轮开发必须包含 **真正二次采样**（首轮 latent → 第二轮独立 sampler/调度，非简单增加步数或改名）和 **RelayVSR 原生 2X 视频超分**（后处理，非 H3 LoRA）。两者均具备正式 UI 配置、运行时资源控制、文件产物与失败恢复。真实模型/节点条件不足须如实标记 BLOCKED，并交付明确可执行的验证步骤；不可用 Mock 代签真机。
5. Ref2VA INT8 ConvRot 为现有已验收主基线；其他变体包括 W6A8 候选，必须独立识别、安装、核验并记录可用性，不允许隐式换底模。PDD8、Turbo4 等是不同加速机制，不能随意叠加。
6. 没有既有固定 JSON 时允许从**仓库中已审核、带版本的合法节点配方**自动构建；**没有核心模型、节点、适配证据时不得造假 READY**。不执行未经确认、未钉版本的网络安装，不下载未经审查的权重。
7. 音视频的数量、时长、配套关系和传输必须严格校验。已由用户批准的关键素材不能无声删减或替换；系统应说明精确镜头与具体缺口。
8. 仅更新当前镜头的生成图和有效产物；已通过或已交付的历史 Job/Artifact 不得覆写。变更模型/LoRA/二采/超分创建新版冻结执行意图，并明确下游失效范围。

## 1. main 实际基线与阻塞点（先审计再动手）

本次审阅的 main 最近可见 HEAD：1a55c41509924b898a587901b0aa5aa5ac71cc70。**本地 Agent 开工时必须 git fetch origin，记录实际 origin/main SHA 并对差异复核；若 main 发生变化，以最新代码重新定位入口，不能按本文行号硬贴补丁。**

| 模块/文件 | 已存在能力 | 当前需要消除的耦合 |
|---|---|---|
| backend/shine/director/streamline.py：start_director_production | 一次开拍、按集、幂等运行 | 接收一个 workflow_profile_id，整包选择一条方案 |
| backend/shine/director/package.py：build_package / package_readiness | 冻结目录、素材/规则版本及预检 | binding 只存一个 Profile，全部镜头由其参数槽位把关 |
| backend/shine/director/requirements.py：reference_capacity / derive | 每镜素材需求和实体合并 | 用固定 Profile 槽位反推哪些需求为必需 |
| backend/shine/production/runner.py：_create_series_for_batch | 分批交给 Series | 从包读取单一 Profile |
| backend/shine/comfyops/production_series.py：_create_body / _create_item_job | 按镜创建真实 Job、连续性来源等待、同一租约 | 将 series.workflow_profile_id 复制给每镜 |
| backend/shine/services.py：make_render_request | 唯一 H3 请求、素材绑定、参考 manifest、合同门禁 | 请求前要求登记静态 Profile；按其映射作强校验 |
| backend/shine/comfy/workflow.py | canonical/graph hash、参数填充、未用引用节点裁剪、Autogrow 校验 | 无受审核能力驱动的动态扩槽/图装配 |
| backend/shine/comfy/provider.py | 逐引用校验/上传、确定性映射、图提交、外部证据 | prepare() 从静态 Profile 的 graph/parameter_map 组图 |
| backend/shine/comfyops/service_validation.py | 真实 object_info/服务校验 | _workflow_contracts 强制默认目录每一条固定 Profile 与磁盘同名内容一致 |
| backend/shine/comfyops/runtime_configuration.py | 密封的服务证据、运行激活 | service_evidence_status 遍历固定工作流的哈希，触发 WORKFLOW_CONTRACT_CHANGED |
| backend/shine/runtime_orchestrator/* | 安装实例生命周期 mutex、Worker 控制意图、CAS、审计、真实 OS 归属证据 | **必须复用**；现有控制动作属于生命周期层，不能另造逐镜控制进程 |
| backend/shine/comfyops/handoff_startup.py | 受管 H3 进程启动及单实例校验 | required_workflow_node_types 以所有已登记工作流汇总启动所需节点 |
| backend/shine/models.py：WorkflowProfile / Job / JobAttempt / ProductionSeries | 单一数据库、作业/轮次及提交图哈希 | 需让计划/执行图身份以镜为粒度，不破坏旧字段 |
| frontend/src/components/DirectorShootPanel.tsx | 七阶段⑤⑥一次开拍及按集追踪 | 仍有人工/首条 Ref2VA Profile 选择 |
| scripts/build_h3_workflow_profiles.py | 有 H3 PDD8、H3 3D latent 1.5X + 二段尾部精修构图范例 | 不能把历史静态、未全集成的图直接当所有变体真机已通过 |
| config/workflows/ref2va-pdd8-6img.json | COMFY_AUTOGROW_V3、已存在 6 图的参考节点链 | 静态 6 图不可作为全量动态输入的最终形态 |
| config/deployment/variants/ref2va-lowend-candidate.json | W6A8 量化候选、独立目录/验收门禁 | W6A8+PDD/Turbo 等兼容项仍是 unknown/candidate |

已证实的直接错误：runtime_configuration.py 的 service_evidence_status 对服务证据中保存的 **全部固定 Profile 完整契约哈希**比较，异常标记为 WORKFLOW_CONTRACT_CHANGED:name；service_validation._workflow_contracts 依据默认部署 catalog 的四条固定工作流核验。该启动级校验不得再锁死用户可选择的整个 Ref2VA 能力。

### 1.1 既有资产一定要复用

- Run/Series/Job/Attempt 幂等与 UNKNOWN 对账，继续沿用；不把 WorkflowGraph 当作新生产队列。
- 受管 H3 renderer、ComfyInstance、ResourceLease、runtime_orchestrator.guard/identity/operations/executor 是唯一的运行控制权威。
- 现有 PDD8、20 步、低显存模型变体与已验证模型加载节点/参数、Asset center、FFmpeg 技术质检及单集交付继续有效。
- 旧 Profile、旧导演包、已交付旧视频均兼容只读；历史冻结契约保持原样，不覆盖或重写旧 evidence hash。

## 2. 用户可选项、自动项及能力组合公式

最终逐镜可执行流水线 = **Ref2VA 底模及其完整依赖 + 互斥的加速策略/LoRA + 可选真正二采 + 可选 RelayVSR X2 / 其他经验证后处理 + 标准 H3 节点内核 + 当前镜头自动选取的图片/视频/音频输入**。

| 类别 | 用户可决定 | 项目决定/校验 |
|---|---|---|
| 底层模型 | 已安装且合格的 Ref2VA INT8 ConvRot、W6A8 等量化变体 | checkpoint / VAE / text encoder 的真实版本、硬件可运行性、启动方案 |
| 采样/加速 | 原生/20步、PDD8、Turbo4（仅支持且真实验证的项可开拍） | LoRA/PDD 节点、相容采样器、步数、sigma/shift、CFG |
| 二次采样 | 不启用 / 启用，用户看到“二次精修” | H3 第二轮 sampler 图、denoise/sigma、上下文与 latent 连接、预算与验收 |
| 高清增强 | 无 / RelayVSR X2 / 其他已验收方式，允许与二采组合 | 放大后台、是否需要释放 H3 权重、分块/卸载、音轨与帧一致性 |
| 镜头参考 | **不让用户选择 N 图 N 音 N 视频组合** | 根据已冻结分镜、绑定的主素材与连续性，对每一镜独立选择/排序/编译输入 |
| 画幅/集数 | 继承 V2 剧本统筹已确认设置 | 检查所选策略支持的输出尺寸、资源预算及二采/高清后的尺寸 |

**UI**：在现有基础配置/⑤导演包里用非工程语言呈现“模型版本”“加速”“二次精修”“高清视频”；高级设置可展示所需文件、节点、适配状态。七阶段不变，⑤主按钮仍为唯一“确认拍摄方案并开始真实制作”；无额外确认卡、无输入槽数字选择器。用户变更项目生产策略时提示待生产部分需要重新确认、历史视频不动。

## 3. 正式业务数据合同：最小追加，不造第二流水线

### 3.1 ProjectProductionPolicy（保存于既有项目/导演包版本的政策快照）

~~~json
{
  "schema_version": "1",
  "generation_mode": "Ref2VA",
  "model_variant_id": "h3-ref2va-int8-convrot",
  "accelerator_id": "pdd8",
  "second_pass": {
    "enabled": true,
    "recipe_id": "h3-tail-refine-v1",
    "quality_preset": "balanced"
  },
  "upscale": {
    "engine": "relayvsr",
    "variant": "x2",
    "enabled": true,
    "quality_preset": "balanced"
  },
  "output": {
    "target_aspect": "16:9",
    "base_width": 1344,
    "base_height": 768
  }
}
~~~

上面是**规划合同示例，不是直接可运行的默认组合承诺**；实际可选项必须从经过验证的能力注册表派生。不要把策略参数写死在前端组件、也不要将所有未来版本打包成巨大静态 JSON 工作流排列组合。

### 3.2 PerShotWorkflowIntent（每镜最小可冻结执行意图）

~~~json
{
  "schema_version": "1",
  "shot_id": "shot-001",
  "shot_revision": 4,
  "style_sha256": "<sha256>",
  "policy_sha256": "<sha256>",
  "mode": "Ref2VA",
  "reference_manifest": [
    {"asset_id": "<asset-id>", "family": "image", "role": "subject", "ordinal": 0,
     "sha256": "<sha256>", "required": true},
    {"asset_id": "<asset-id>", "family": "video", "role": "motion", "ordinal": 1,
     "sha256": "<sha256>", "required": true}
  ],
  "continuity": {"mode": "cut", "dependency_job_id": null},
  "requested_outputs": ["h3_base", "h3_second_pass", "relayvsr_x2"],
  "plan_sha256": "<sha256>"
}
~~~

不要复制所有素材字节、提示词全文、模型权重到 JSON。字段引用现有 ShotSpec、ShotAssetBinding、Asset、StyleVersion、PackageVersion 主数据；快照只存不可变身份/引用和必需的运行参数。没有延续素材时不生成伪来源；续镜要等前镜真实 PASSED 后绑定其当前有效 artifact，再形成提交图。

### 3.3 WorkflowExecutionSnapshot（每次真正提交/处理所冻结的实际图）

- 在既有 Job/Attempt 的请求/证据快照中持久保存：model_variant_id、模型组件 digest、加速策略 digest、二采配方 digest、Relay 配方 digest、基准图 digest、引用清单 digest、编译后 graph digest、参数映射 digest、object_info 版本 digest、实际 ComfyUI prompt_id 与最终产物 digest。
- **提交前**写出用户选择与实际编译图身份；**提交后**立即登记外部 prompt id，遵守现有未知状态对账。对于二采/Relay 后处理，尽量复用现有 Job/Attempt 子任务或已存在 delivery/postprocess 订单结构，以“父作业 + 有序阶段及版本化 artifact”组织，不新建一套队列或状态机。是否扩展既有表，先在 WP0 给出最小迁移设计与向前兼容测试。
- 图缓存键 = variant_id + accelerator_recipe_sha + second_pass_recipe_sha + enhancement_recipe_sha + reference_manifest_sha + shot_revision + style_sha + object_info_digest + adapter_version；只复用**精确等价**图。缓存不是运行真相，丢失缓存可以无损重建。
- 旧 Job.workflow_profile_id / ProductionSeries.workflow_profile_id 留兼容字段；新 Job 不得靠整 Series 的单个旧字段决定当前镜头图。

## 4. 模式级能力登记、自动补齐与三类校验

### 4.1 启动时只校验“可用模式能力”，不是列举固定槽位 Profile

新机/运行时 READY 分三个层级：

1. 基础服务（blocking）：受管 ComfyUI 可真实启动/停止、live object_info 可读、已选 Ref2VA 模型依赖和必要 core H3 节点在当前安装实例可用，所选基础 H3 执行配方能编译，并有合法媒体传输路径。
2. 当前用户已选生产策略（conditional blocking）：例如用户启用 RelayVSR X2、二采或 PDD8，必须存在相容配方、权重文件、节点与运行策略证据；不相容只阻断使用该策略的镜头/运行，不阻断其他已经验证的 Ref2VA 路径。
3. 历史/非选定 Profile（advisory）：老版本 2img/6img JSON 哈希漂移、缺节点只标记“历史模板需要重新验证”，**不再升级为整个机器不能启用真实制作**。

实现：
- 在 service_validation._workflow_contracts 中剥离默认 catalog 的“四条静态 Profile 必须全相等”的启动级条件；改为按选定 mode + model variant + active policy 收集核心能力/最小可执行配方。
- runtime_configuration.service_evidence_status 迁移至 v2 mode-capability seal，记录 installation_id、model components、required node ABI digest、recipe version、selected policy digest；历史 v1 seal 保留可复验记录，不能删历史哈希或伪造 v2 READY。
- handoff_startup.required_workflow_node_types 不再把所有旧项目 Profile 的可选节点强加到每次 H3 启动；按当前选定实际管线的必需节点/能力验证。实际每镜编译图在提交前另做完整 validator。
- 旧图与新图不复用假 READY：当 live object_info/模型哈希/配方版本变化，精确标记受影响能力待复验；可通过安全自动重检，不等于跳过校验。
- 新项目默认从已审核内置最小 Ref2VA 生成配方，自动创建逻辑登记身份。没有 JSON 文件不应提示用户“手动做四套配置”；没节点/模型则明确缺项和一键已审物料安装/导入路径（仍走现有安装许可门禁）。

### 4.2 能力注册表（H3CapabilityRegistry，逻辑能力，不是第二调度器）

每个能力条目至少定义：

- kind: model_variant / accelerator / second_pass / upscale_backend / reference_input_adapter；
- key/version/source_revision/file hashes/license/audited_status；
- required_model_components、required_node_classes/required_inputs、transfer adapters；
- supported_generation_mode、兼容的量化模型/LoRA/采样策略/输出尺寸/硬件分级；
- status: VERIFIED / CANDIDATE / MISSING / INCOMPATIBLE / STALE；verified_host_identity + evidence，不能跨机器冒认。
- 明确区分：PDD8 不是通用“加载任意 LoRA 即生效”，还包括 MiniMaxH3PDDAccApply/调度与 sampler；Turbo4 是另一种配方。高清策略 RelayVSR X2 是视频 SR 模型，不是 H3 LoRA；H3 latent upscaler 是另一类模型。

自动装配 **先查相容且 verified 配方 → 没有时从版本化基准节点图组装 → 静态+live 节点合同检查 → 可选真机资格测例 → 只为被选中组合激活**。绝不允许 arbitrary 自造未知节点名、手改 checkpoint 或绕过 W6A8 候选门禁。

## 5. 逐镜 N 图 N 视频 N 音参考编排

### 5.1 Planner（确定性业务逻辑，无新 LLM Agent）

输入：已确认导演分镜 ShotSpec、手动新增/编辑后的镜头、approved+locked 主素材、镜头绑定、角色顺序、场景/道具、镜间 CUT/连续性、冻结风格及已选能力策略。

输出：每镜 reference_manifest、必需项、推荐但可选项、输入顺序、素材 digest、存在缺项的具体 shot_id。

- **不依赖 Profile 的当前槽位大小派生重要性。** 改造 director/requirements.py 的 reference_capacity/_is_critical 与导演材料清单；由镜头承诺、角色/关键道具/连续性决定必需，能力注册表只在生成阶段判上限。
- 先保留人物身份、关键场景、关键道具、前镜连续性等有证据的必要引用；不自动拿更多主素材作为参考，不将全部批准主素材塞满 9 图。
- 图片≤9、视频≤3、独立音频≤3、所有参考文件数≤12；视频/音频每个 2–15 秒、各自总时长≤15 秒；音频不可作为唯一参考输入。按官方 Ref2VA 完整规则实施，视频关联音轨及额外封装文件如何计数以当版本节点的实际官方定义再验证，不猜。
- 单张图片也必须通过 Ref2VA，不能被 H3PromptCompiler 误推断为 I2VA（现有 mode_policy 是唯一约束点）；零可用参考必须精确阻断。
- H3 节点 COMFY_AUTOGROW_V3 的 ref_images.ref_image_0..8、ref_videos.ref_video_0..2、ref_video_audios.ref_video_audio_0..2、ref_audios.ref_audio_0..2 依据 live object_info 读取声明的界限，保留官方提示词标签/参考媒体顺序；reference_video_audio 的配对、时段裁取、上传与转码需有完整测试。
- 当请求违反上限或关键素材缺失，先只对可选非关键参考提出有证据的去冗余建议；关键引用超限/不可编码时阻断当前镜头并说明原因，不静默丢弃。
- 参考素材的哈希、主素材身份、锁定状态和跨项目权限在上传前再验，不能因图可组装而放宽。

### 5.2 WorkflowGraphCompiler（在现有 shine.comfy.workflow/provider 内扩展）

需实现纯函数：
1. select_template(mode, variant, accelerator, second_pass, upscale_stage) 从经审计的配方注册表选合法 H3 基准图；
2. expand_reference_nodes(graph, manifest, object_info, adapter) 为所需 image/video/audio 添加对应 Loader、连接 Ref2VA 节点、确定性 node_id 与参数映射；
3. apply_sampler_strategy() 连接正确的 PDD/原生采样策略，严格拒绝不相容 LoRA/步数/shift；
4. if second_pass enabled: append_h3_second_sampler()；
5. validate_graph + validate_against_object_info + validate_reference_slots + 完整媒体家族契约；
6. compile hashes/save frozen execution snapshot；provider.prepare 按当前镜头这个 snapshot 提交。

复用现有 apply_parameters / prune_unused_reference_nodes / graph_hash / resolve_reference_slots / upload assets，不要复制整套 Comfy provider。任何 loader 复用必须保证连接合法、无残留 placeholder、不会混淆引用顺序。一个系列中的 A 镜 1 图与 B 镜 7 图均可用不同图，但共享 renderer。

## 6. 真正“二次采样”专项：这一轮必须交付

### 6.1 功能定义与边界

“二采”是：**第一次 H3 采样得到合法 latent → 在同一镜头、相同已冻结语义/模型与引用上下文下进入第二个 sampler 精修阶段 → 输出精修后的 latent/视频与音频**。不是简单把第一轮的步数从 8 改为 20，也不是把 RelayVSR 叫二采。

- 同一 H3 生产图中以明确节点连接实现，二采必须使用有依据的 sampler/sigma/denoise 范围；保留正向/参考 conditioning；与视频及音频 VAE 输出逻辑一致。
- PDD8/20步/Turbo4 二采的组合分别有配方，不应拿 PDD8 的 8-step schedule 强行充当所有组合的二段尾部。Comfy H3 res_multistep 的历史仅存在一次 sampling run；第二 sampler 的首步从一阶开始，不得假设历史延续。二采专用尾部需足够有效步数、正确 sigma 区间和终止条件。
- 当前 scripts/build_h3_workflow_profiles.py 已有 PDD8 latent upscale → MinimaxH3LatentUpscaler3DRefineHandoff → 尾部 PDD refine 的**历史参考实现**，但必须与本轮真正二采定义对齐：明确是否包含 latent 尺寸变化，不能把不可用的上采样权重标称为“二采已通过”。
- 作品可单独勾“二采”，也可与 RelayVSR X2 组合。优先支持当前 INT8 + 已验证加速配方，W6A8 等候选不兼容时显示具名阻断，而不是回退 INT8。
- 作品二采由策略驱动；如需个别镜头例外，只能是经过 policy 明确允许、可审计的逐镜精修覆盖，不得由 Agent 悄悄关掉已选二采。
- 首轮 H3 原片与二采后片各自保留版本/hash。二采失败可在已确认的首轮 latent 可恢复情况下重做二采；如果首轮 latent 不存在，明确说明无法只重跑二采，不能虚构可续跑。

### 6.2 二采验收

工程：不同配置生成不同的合法二采图；包含两个真实 sampler；参数/配方变化会更新图哈希；反例验证非法 denoise、sigma、LoRA、不受支持变体、帧数/音频不一致正确阻断。

真机：分别展示同一 seed、相同参考的“无二采”和“启用二采”输出、每段 GPU 执行 evidence、可播放 MP4、原生音频、帧数/时长/分辨率、独立 QC。画质提升需人工对照判断，不能用“生成成功”代替“效果显著”。

## 7. RelayVSR X2 专项：本轮直接接入生产闭环

### 7.1 正确技术定位和上游选择

RelayVSR X2 是独立的视频超分模型，位于 H3 原片/二采成片之后；不是 H3 LoRA，也不改主 H3 参考输入上限。采用优先路径：**ComfyUI 官方 core 原生 RelayVSR X2 节点 + Comfy-Org 提供的 INT8 ConvRot X2 主权重及匹配 Flash VAE**，在同一受管 ComfyUI 安装内独立 stage 运行。

官方 core RelayVSR 支持在 2026-11-05 合并（Comfy-Org/ComfyUI PR #17511），**不代表 Shine 当前被钉定的 ComfyUI revision 已包含节点**。本地 Agent 先读取真实 ComfyUI revision、live object_info 和现有 H3 nodes，实施一项经过风险审查、hash/commit 钉定且可回滚的兼容升级/安装方案；避免为了启用 RelayVSR 盲目升级 ComfyUI 导致 Ref2VA/H3 现有图回归。若经审计的兼容节点插件更安全，也必须运行在这同一受管 ComfyUI 和统一调度层，不得为 Relay 另起一个未受管端口/服务。不同后端不能自动静默互换。

官方参考：
- ComfyUI core 集成：https://github.com/Comfy-Org/ComfyUI/pull/17511
- 官方重新打包权重：https://huggingface.co/Comfy-Org/RelayVSR
- 参考 x2 pair：relayvsr_x2_lightx2v_int8_convrot.safetensors + relayvsr_flash_vae_x2_fp32.safetensors（具体文件 digest/version 需部署 catalog 固定，禁止猜 hash）
- 原始 LightX2V 路径：https://github.com/ModelTC/LightX2V （仅调研或明确验证的备用，不默认建第二套独立进程）
- 不能错用 x4 权重、假 2x 缩放、普通 FFmpeg 插值或仅改容器元数据冒充 RelayVSR X2。

### 7.2 RelayVSR 的后处理阶段数据流

已通过 H3 生成/二采技术 QC 的镜头 MP4 → 只读源 SHA 校验 → 加载时间连续的 RGB 帧及来源 FPS/音轨 → RelayVSR X2 图（匹配主权重+Flash VAE、单步采样与 decoder）→ 输出 2 倍宽高、原 FPS/帧数/时长 → 原音轨无损复制或受控转码 → 独立 FFprobe/FFmpeg 全帧技术 QC → 新版高清镜头 Artifact → 既有后期装配和交付。

- 不能用源视频的“任意一张图”替代完整时间序列；须保留色彩空间、时间戳、音画同步与数据顺序。
- 长镜头采用分块/streaming/CPU offload/受控内存缓存，窗口跨界做实际画质检查；RTX 5080 16GB 机器必须有可审计的保守资源策略（不得声称所有高清分辨率必然足够）。
- 只接受 model+Flash VAE 匹配的一对 X2 权重。选择 X2 后输出宽高严格乘 2；如用户原片 1344×768，期望 2688×1536（若尺寸/帧数超出硬件实测范围，应明确阻断而不是静默改成 1.5 倍）。
- 原片/二采片/Relay 超分片均不可覆盖，存带来源指针、hash、媒体元数据和操作记录的新 artifact；成片拼接使用每镜**已批准的最高阶段最终产物**，不同镜头最终版本不同也要正确排序。
- 对超分帧数变化、帧率错误、音轨丢失、音画漂移、空视频、无效媒体、明显闪烁有明确 QC；机器看不出的艺术问题交用户审片。
- Relay 超分失败，只重试该镜的 Relay 阶段，绝不回头重跑已通过的 H3；用户可以暂停/继续/安全续跑且能查看原片，最终交付不能在用户明确要求 X2 时静默降级为原片。

### 7.3 必须解决 GPU/进程资源交接

当前 ProductionSeries 持续持有真实 H3 独占 GPU lease 直至系列结束；RelayVSR 同样是重模型，**绝对不能和 H3 权重同时驻留或同机并行推理**。

推荐二段式受控处理：
1. 同一系列依次生成各镜 H3（含镜内二采），保持现有 H3 renderer/lease 与重复利用机制。
2. 所有可处理的 H3 镜头完成后，发出同一生产 Run 内的“高清后处理阶段”工作，由现有 Worker 认领；确认 H3 不再有未完成外部任务、H3 模型被真实卸载/停止，并取得 OS 归属与 GPU 使用证据。
3. 在**同一 runtime_orchestrator 控制面、同一物理安装 mutex、同一 GPU 租约所有权规则**下安全切到 RelayVSR 阶段；串行运行该批次的各镜，减少权重反复加载。可复用受管 ComfyUI 进程并显式卸载 H3 权重；如果要求重启，继续经现有受控生命周期路径执行，绝不自建 sidecar daemon。
4. 交接采用既有 ResourceLease 的所有权证明、lease transfer 或等价受控事务；设计一个清晰的 stage owner，但不要创建独立租约表；外部提交未知时维持 RECONCILE_REQUIRED，不能靠超时把 GPU 放行。
5. Relay 后处理完成或安全停止后，Worker 用同一受控发布/回收协议释放 GPU，继续现有逐集 delivery；保留多集断点恢复，不能因为本集正在超分而重新出第 1 集。
6. 任何升级现有 runner/worker 或控制平面必须同步审阅 client_review_identity 与 VLM 正/反样本校准要求；不能为本功能悄悄绕开已冻结审片证据。

不同硬件可在保障显存的前提下选“先 H3、后 Relay”的批次调度；不能让一个镜头 GPU 占用失败导致所有其它独立镜头永久停摆。对 GPU 资源不足可限次调整 chunk/offload 等**同一模型的执行资源参数**，不能擅自更换量化版本或跳过已选高清阶段。

## 8. 唯一统一调度层的集成合同

职责分界：

- **Runtime control plane（已有）**：安装实例的 ensure/stop/probe/reconcile/release、可验证 OS 进程归属、installation mutex、真实资源冲突/lease；负责保证“只有一个被管理的执行者”。
- **Workflow composition（新增为已有控制层核心服务）**：只作纯规划/配方选择/合法图编译/能力检查，不持有外部 GPU、不开进程、不新建 Worker。
- **Production coordinator（已有）**：DirectorPackage/Run/Batch/Series/Job 控制批次、镜头顺序、依赖、重试和 QC；增加 generation→refinement→Relay 阶段进度投影，状态来自现有作业/产物台账。
- **Comfy provider（已有）**：按每镜冻结编译图执行一次真实 prompt 传输/提交/获取，保留 submit intent before /prompt、unknown reconciliation、图 hash 和产物 hash。
- **Media delivery（已有）**：只挑经过阶段 QC 的有效最终版本，负责字幕、音频和镜头剪辑；历史交付指针不逆转。

禁止：
- 新增独立 H3/Relay 调度服务、第二端口、临时后台脚本常驻、第二个 SQLite 生产状态机；
- API 请求线程直接 start/stop ComfyUI 或启动 Relay GPU 任务；
- 仅因渲染图变化便重启 ComfyUI；
- 视频阶段失败后无条件重交外部 prompt；
- 在 SQL 写事务内作视频上传、模型加载、网络请求、GPU 推理；
- 将旧 WORKFLOW_CONTRACT_CHANGED 全部删除后用常量 READY 取代。

## 9. 全量源码改造清单（Agent 按实际 main 校正）

| 工作域 | 首选文件 | 主要变更 |
|---|---|---|
| 基础能力与配方 | backend/shine/comfyops/variants.py、variant_binding.py、profile_gate.py，config/deployment/** | 注册量化模型、加速配方、二采和 RelayVSR X2；升级/授权/权重哈希和兼容证据 |
| 图编排 | backend/shine/comfy/workflow.py、capabilities.py、provider.py | 逐镜图生成、动态媒体装载节点/连线、二采图扩展、GraphSnapshot 验证 |
| 素材规划 | backend/shine/director/requirements.py、streamline.py、materialprep/* | 需求不由固定槽位决定；已确认素材和镜头选择最终 manifest |
| 导演包 | backend/shine/director/package.py、director/contracts.py（如需） | 保存策略与逐镜意图；检查各镜是否能编译，不再以单 Profile 钉全包 |
| 执行交接 | backend/shine/production/runner.py、comfyops/production_series.py、services.py | 分镜各自 Profile/GraphSnapshot；兼容旧 Job；保持 Run/Series 幂等 |
| 调度与 GPU | backend/shine/runtime_orchestrator/*、comfyops/handoff_startup.py、models.py、worker.py | 二采/H3 与 Relay 的单控制平面阶段交接、占用与断点对账 |
| 服务注册/启动 | backend/shine/comfyops/service_validation.py、runtime_configuration.py、api_setup.py | 模式级 READY、实际选定策略的节点合同，旧四静态图改为历史 advisory |
| 高清链 | backend/shine/media/*、comfyops/* 既有后处理入口 | Relay X2 模型与后处理、媒体流保真、阶段 artifact 和 delivery 接线 |
| API | backend/shine/api_streamline.py、api_comfy.py 等现有路由 | 作品生产策略预览/确认、逐镜计划摘要、能力就绪/报错；不新增独立控制 API |
| Web | frontend/src/components/DirectorShootPanel.tsx、DirectorSetupPanel.tsx、导演包/成片页 | 选择模型+加速+二采+Relay；移除普通模式手动选 Profile；每镜可解释计划与阶段进度 |
| 新机目录/依赖 | config/deployment/*、scripts/*、packaging/windows/* | Relay X2 官方版本化模型/节点导入核验及缺失安装提示，不改为自动不受控下载 |
| 验收 | backend/tests/*、frontend/src/test/*、docs/验收报告 | 单元/集成/Windows真机三层证据、负例和旧项目回归 |

以上文件是**实现候选位置，不是硬性要求改动每个文件**。任何数据库迁移优先使用现有 JSON snapshots/生产阶段台账的小范围扩展，避免迁移泛滥。旧逻辑可保留为 fallback 兼容，但普通导演路径必须完全使用新逐镜装配机制。

## 10. 分阶段开发里程碑与交付（全部属本轮范围）

### WP0 基线审计与安全隔离

- fetch 最新 origin/main；独立 worktree + 功能分支，文档分支只读；
- 记录当前模型/目录/真实 ComfyUI 节点 revision、现有 GPU/Series/Run 状态、模型和 node 清单；确认是否包含 RelayVSR core；
- 输出变更矩阵、最小 DB 迁移方案及模型/插件版权许可审查；不得清理真实数据或覆盖当前认可的片段；
- 将验收样本目录、使用的原片、设备和权重候选准备成独立隔离数据，不把权重/影片提交 Git。

PASS：独立 worktree、可复现 baseline SHA、当前所有已验收路径列册、历史无损。

### WP1 模式级注册与安装能力

- 消除默认四条静态图哈希强绑定；
- 实装选定 Ref2VA model+accelerator 能力登记、标准配方自动补齐；
- 建 v2 service evidence 与渐进兼容，实际节点/模型缺失仍具名阻断；
- 完成“缺 JSON 可自装配”“某历史 Profile 漂移不影响其他可用配方”正反测试。

PASS：真实服务 READY 不依赖所有 2img/6img 图逐字节相同；不合法图/缺节点/缺模型仍严格失败；历史 activation 数据正确迁移或可安全复验。

### WP2 模型/加速/高清生产策略与逐镜需求

- 用户可选择 Ref2VA 量化版本、加速配方、二采、RelayVSR X2；变体兼容矩阵不允许隐式替换；
- 逐镜从 ShotSpec/主素材自动得到 reference manifest；不反向依赖固定 Profile 容量；
- 模式/参考上限/时长/序列/依赖/必要性规则及国际化的具名中文错误全部完成。

PASS：每镜 N 图 N 音 N 视频意图不同，用户仅有策略设置，不需要任何工作流模板选择。

### WP3 自动编译 H3 动态多模态执行图

- 合法的动态 Loader/连接/参数映射、媒体传输、模板缓存、hash；
- cut/软延续/已有具备证据的引用和音视频混合输入正确处理；
- H3 编译路径保持唯一，不新增第六个 Agent，不新建 JSON 流水线。

PASS：支持 1~9 图及受限合法混合组合的模拟 ComfyUI contract；真实节点可用性则另做 Windows 证据。任何素材丢失/错配/越界必须失败。

### WP4 二次采样（本轮必须）

- H3 第一次采样→第二 sampler 精修 graph 模块、独立参数/证据/失败恢复；
- 20步/PDD8/选定变体支持矩阵+不相容限制；
- 单独无二采/有二采对照、双采样合法图与真实音视频合一。

PASS：工程全链路含两轮采样，真机合格组合提供独立工件；无法真机测试的组合标 NOT_VERIFIED，不能删测试。

### WP5 RelayVSR X2（本轮必须）

- 建立真正 X2 权重配对、官方 core 或经过同实例验证的配方、安装登记与缺失提示；
- 接入逐镜后处理，保持原 FPS/帧数/时长/音轨，不用 FFmpeg 简单缩放替代；
- 完成尺寸 X2、chunk/offload、资源探测、来源/产物哈希及失败只重试 Relay；
- 真机 RTX 5080 16GB 提供可重现的中低资源路径，成功/不可运行都保留真实日志。

PASS：软件中确有真实 RelayVSR X2 执行图、后处理任务及被选中的最终 Artifact；真机输出必须经过 FFmpeg 全帧核验，若硬件不满足如实 BLOCKED，不得标 REAL_H3_RELAY_PASS。

### WP6 统一调度层融合

- H3 首次与二采、Relay 阶段，均走现有单一 Worker、installation mutex、resource lease 和 OS 归属核验；
- 生产阶段转换、暂停/续跑/取消、UNKNOWN 对账、竞争性点击防重、进程死亡与用户关浏览器恢复；
- API 与 renderer 生命周期无重复启动；不引入第二服务。

PASS：有并发负例、提交未知/已提交区分、资源冲突与恢复测试，二采与 Relay 不重启无关镜头；监控可追踪阶段。

### WP7 UI 与分集成片闭环

- ⑤选底模/加速/二采/Relay，不选 N 图 N 音 N 视频；统一中文提示“系统已为 X 镜自动安排参考素材和生成策略”；
- ⑥按镜显示“原片生成→二次精修→X2高清→质检→完成”，若无选择的阶段自然隐藏；
- ⑦区分可播放原片/二采片/高清成片，最终交付用真实已通过的最终片，历史版本仍保留；
- 按集隔离与非本轮已交付集不重拍、一次开拍幂等等 V2 行为不回退。

PASS：真实浏览器一次开拍后两集不同参考组合不需操作技术设置，可按镜看审核证据，UI 不出现固定 2img/6img 工作流选择框。

### WP8 全量验收、报告与 PR

- 后端定向测试/离线 E2E/完整回归；前端全量；Ruff、Alembic、tsc/build、Windows 启动/安全关停、模型配置身份；
- 必须有实体文件的真实 H3 逐镜测试、二采测试、Relay X2 测试、二者组合测试、人物身份/音画/字幕/成片复核；
- 不具备 H3/Relay 模型时，不许将 Fake ComfyUI 执行冒称真实完成；须分项解释 BLOCKED 与复验命令/权重需求；
- 输出逐 WP 测试报告、完整缺陷修复记录、真实工件证据/哈希（只在本地，不入 Git）、残余风险和 PR；
- 本地 Agent 在授权下可自主创建/修改分支、源码、测试和方案文档，**只通过独立功能分支 PR 合入 main，不直推 main，不强推**。

## 11. 回归与真机验收矩阵

### 11.1 重点测试场景

| ID | 输入/场景 | 必须可观察的结果 |
|---|---|---|
| T01 | 1图、2图、4图、6图、9图（均合法） | 每镜生成不同合法图，参考顺序正确，没有未使用 Loader 残留 |
| T02 | 3图+1视频+1音、6图+3音等合法组合 | 参考族正确映射与上传，数量时长合格、使用已审核媒体 |
| T03 | 图片10张或合计超过12、超时长、零图只音 | 镜头具名拒绝，不能误吞参考 |
| T04 | 两镜不同图但同一 Series | 共享唯一受管实例/GPU lease，不重复启动 |
| T05 | 选 INT8 + PDD8 与 W6A8 候选 | 前者按真实证据可用，后者未验收时必须拒绝，不自动换 INT8 |
| T06 | 现有四固定 Profile 的一个 hash 漂移 | 非当前必需的旧 Profile 标 advisory，已验证 Ref2VA 可开拍 |
| T07 | 缺核心节点/模型/活跃策略能力 | 真实阻断具体缺项，不伪造 READY |
| T08 | 二采关闭/开启，合法与非法采样组合 | 一个/两个 sampler，图哈希变化与越界报错准确 |
| T09 | RelayVSR X2，模型权重缺/版本不符 | 具名缺项；模型真机可用时输出尺寸精确 2× |
| T10 | H3→二采→Relay X2 | 原音轨/时长/帧数保持可验，源与各阶段 artifact 独立保留 |
| T11 | Relay 处理中断/失败/UNKNOWN | 不重跑已通过 H3，恢复只对账或重试有明确安全证据的阶段 |
| T12 | 并发开拍、重复点击、断电后重启、安装实例交接 | 不重复外部 GPU 提交，不重启第二 renderer，lease 最终一致 |
| T13 | 已交付第1集后单独拍第2集 | 第1集 Job/Artifact/MP4 哈希不变，仍可见 |
| T14 | 旧导演包/旧工作流/旧服务证据 | 旧数据可读，迁移后合法重检，不覆写历史和篡改哈希 |
| T15 | Relay 原片的特殊 FPS/音频/转场 | 输出同步、全帧解码、技术 QC；失败只影响该镜 |
| T16 | 16GB 设备 RelayVSR offload/chunk，显存不足 | 要么真实出片，要么明确 MEMORY_INSUFFICIENT 与合理诊断，不能静默降级 |
| T17 | 人工/自动返工镜头 | 仅失效受影响的阶段和下游版本，已批准资产/镜头不被重写 |
| T18 | 用户选择二采+Relay，但某个镜头无相容模式 | 逐镜阻断，准确指出不可用阶段；不把原片冒充最终完成 |

### 11.2 三种验收状态绝不混用

- ENGINEERING_PASS：模型图/合同/调度/回归测试通过，能说明正确编排但不能证明实际 GPU 画质。
- REAL_H3_PASS / REAL_RELAY_X2_PASS：本机独立实测、真正的节点+权重、完整 log、prompt/history、媒体工件和 SHA，人工可打开。
- HUMAN_ART_REVIEW：实际观看对照，人物身份、运镜、声音、口型、连贯性、二采收益、高清细节和闪烁等逐项结论；未签字为 NOT_REVIEWED。

只有全部必需阶段确实通过，才可宣称“所选组合的真实生产链 PASS”。即使原 H3 PASS，也不替代 Relay X2 或二采的真实验收。

### 11.3 测试执行纪律

严格先读 AGENTS.md。后端全量 **8 分片并行按文件划分**，记录每个分片退出码、原始日志与 FAILED/ERROR 行；不可用串行数小时代替。此前本机开发环境须设置 CODEBUDDY_SAFE_DELETE_ENABLED=0 以避免测试环境删文件代理伪失败，但不能以此跳过测试。前端全量使用 no-file-parallelism；另跑 Ruff、Alembic upgrade/check、TypeScript/Vite 构建、既有 test_offline_e2e、test_film_crew、生产/控制层专项。

Windows PowerShell/CMD 入口遵从项目编码策略：cmd ASCII、含中文的 ps1 UTF-8 BOM。所有真实 GPU 测试先查现有任务/服务状态，在隔离数据与已许可模型下执行；没有清晰 OS 进程归属不得 stop/kill 真实后台。

## 12. 风险与防线

1. **RelayVSR core 版本不在当前 ComfyUI pin 中**：优先审查差异和可回滚的精确版本适配；严格保护当前 H3 运行版本，不借核心升级顺便改工作台其它节点。
2. **PDD/Turbo 与量化模型不兼容**：只放行验证组合，候选不“自动降级成功”。
3. **16GB 上 2X VSR OOM**：分块/卸载+显式检测；有界重试同模型资源参数，实在不足准确报告。
4. **二采与潜空间放大混淆**：分为两个能力，双 sampler 证据必须存在；旧 1.5X latent 路线当参考，不替换本轮 Relay X2。
5. **历史证据与包哈希升级**：新旧 schema 可并存，原历史证据只读，不允许改 hash 追认。
6. **GPU 双占、乱停服务**：唯一安装实例 mutex 与 ResourceLease，H3/Relay 不重叠；UNKNOWN 必须对账。
7. **超分破坏音画或人物身份**：保留原片/精修版/高清版，后期用审核后的高阶段产物；自动/人工双 QC。
8. **AI 提示词与参考不符**：继续走官方 H3 编译器与已冻结 Story/Style，绝不把 LLM 文本直接提交模型。
9. **下载、许可证、隐私**：新增节点/模型按具体 Git revision、LICENSE、SHA-256、size 审计；禁止自动拉取未钉文件，不入仓用户媒体/权重。

## 13. 最终通过定义（Definition of Done）

**工程 DoD（全部必须）**
- main 中固定四条 Profile 的契约变化不再使可用的 Ref2VA 模式被全局锁死；同时保持严格的 selected-policy 真实性校验。
- 用户实际能够选择量化底模、加速、二采、Relay X2，系统根据镜头自动生成独立合法的 1~9 图及合法混合参考 H3 执行图。
- 已验收量化版本/策略有真实执行路径，候选版本有准确禁用原因；不捆绑静态 N 图 N 音 N 视频模板。
- 真正二采有二 sampler 连线与持久证据；RelayVSR X2 有完整真实模型适配、资源隔离、后处理、音画和 2× 输出合同。
- 这一切**属于现有单一统一 ComfyUI 调度平面**：没有第二服务/第二 Worker/第二 GPU 锁/第二状态机；Series/Job/QC/Delivery/历史兼容/幂等/未知提交均保持正确。
- 所有前后端、迁移、Windows、8 分片工程回归均有真实结果，变更代码可追溯，并通过独立 PR。
- GPU 实测分项有严格证明：已实测才能勾 REAL PASS，未实测明确 NOT_VERIFIED/BLOCKED，不能在报告里口头替代。

**用户体验 DoD**
- 普通用户只按七阶段导入故事→确认分镜→确认素材→选择模型/加速/二采/Relay X2→一次开拍；不需知道 2img/6img、Profile、节点 ID。
- 同一集连续五镜可以分别是 1图、4图、2图+1音、3图+1视频、7图，只要合法且已准备素材，自动切换不同图，不重启 ComfyUI。
- 二采与 Relay 超分结果均可在成片页追溯/审片/按镜返工，失败镜头和其他已交付集互不污染。

## 14. 文档与实施交付物

Agent 实施分支至少提交：
- 生产代码、测试、兼容性清单和必要 Alembic 迁移；
- 完整的静态/真实 GPU/超分/二采分级验收报告；
- 上游节点与模型的 commit/revision/许可/哈希和安装使用说明；
- 用户可操作 UI 与阶段错误文案及生产演示说明；
- 从当前 main 到最终 PR 的文件变更清单、回归命令、保留问题及安全恢复办法。

**不得**将真实用户资产、运行日志中的秘密、原视频、模型权重、SQLite、GPU 大文件提交 Git。证据用项目本地 .local-acceptance/ 记录并在 Markdown 报告中索引文件及校验摘要。

## 15. 来源与核验起点

本仓库代码：
- https://github.com/anjingdtl/shine-video-studio/blob/main/AGENTS.md
- https://github.com/anjingdtl/shine-video-studio/blob/main/backend/shine/director/package.py
- https://github.com/anjingdtl/shine-video-studio/blob/main/backend/shine/comfy/workflow.py
- https://github.com/anjingdtl/shine-video-studio/blob/main/backend/shine/comfy/provider.py
- https://github.com/anjingdtl/shine-video-studio/blob/main/backend/shine/runtime_orchestrator/contracts.py
- https://github.com/anjingdtl/shine-video-studio/blob/main/backend/shine/comfyops/service_validation.py
- https://github.com/anjingdtl/shine-video-studio/blob/main/backend/shine/comfyops/runtime_configuration.py
- https://github.com/anjingdtl/shine-video-studio/blob/main/scripts/build_h3_workflow_profiles.py
- https://github.com/anjingdtl/shine-video-studio/blob/main/config/deployment/variants/ref2va-lowend-candidate.json

上游事实（本地 Agent 再次验证最新 revision）：
- H3 官方多模态输入约束：https://huggingface.co/MiniMaxAI/MiniMax-H3
- H3 采样机制与多采样器历史说明：https://github.com/Comfy-Org/docs/blob/main/tutorials/video/minimax/minimax-h3.mdx
- RelayVSR core PR #17511（2026-11-05 已合并）：https://github.com/Comfy-Org/ComfyUI/pull/17511
- Comfy-Org RelayVSR X2 INT8 + Flash X2 官方配套权重：https://huggingface.co/Comfy-Org/RelayVSR
- 原项目 LightX2V：https://github.com/ModelTC/LightX2V

---

### 决策摘要

**同一现有统一 ComfyUI 调度层**，接收用户选定的（量化模型 + 加速配方 + 可选二采 + RelayVSR X2）生产策略；每镜按分镜书和已确认素材动态编译自己的 Ref2VA 工作流；所有阶段由现有运行、GPU 所有权、Job/Attempt 与成片流水线一致控制。不给用户暴露 N 图 N 音 N 视频参数，也不另造第二调度器。
