# Shine V2.0 统一基础底座与双生产线收尾建设方案

> 日期：2026-11-20  
> 核查远端：anjingdtl/shine-video-studio，main@c1c69941e4b8ae339da5a490944206ab4cfb78a0（2026-11-20 07:58:12 UTC）  
> 状态：设计与实施任务书，尚非已实现或已验收声明。  
> 开发环境：Windows，F:\ClaudeWorkSpace\projects\shine-video-studio；现有 ComfyUI 根目录由已登记配置决定，不硬编码开发机路径。  
> 目标：一次基础配置，七阶段 AI 导演与四阶段 H3 自主制片共同使用公共素材中心和公共 H3 制片底座；业务编排独立，基础能力不重复建设、不交叉阻滞。

---

## 0. 最高边界（强制执行）

1. 架构正式从“七阶与四阶分别镜像所有业务和基础实现”修订为：“一套统一基础配置 + 公共素材中心底座 + 公共 H3 制片底座 + 两条独立业务生产线”。
2. 用户已经完成一次 ComfyUI、Python、custom_nodes、模型、LoRA、工作流基础配置后，新增四阶不需要第二次下载、探测、登记、配置向导或重复安装。双方使用同一根目录、同一 Python/节点和模型仓；按运行任务保留独立的受管实例端口、input/output/temp/user/log、项目数据、Job/Run 业务状态。
3. 三原则必须分层：环境与资源只有一套事实、一个裁决、一个整备执行者；素材中心只有一份不可变资产版本事实、一个技术/授权裁决、一个实际生成入口；H3 底座只有一份能力/工作流契约、一个真实资源仲裁与提交执行入口；七阶和四阶各保留各自唯一的业务阶段裁决与业务编排。
4. 共享底座不能等同于共享业务数据。七阶的文学创作、导演风格、AI 分镜和导演包不暴露给四阶作为必需前置；四阶的人工分镜、手写 H3 提示词、排片版本和确认事实不被七阶修改。
5. 七阶现有 API、UI、原有数据库行、导演包与出片行为必须兼容；不可为了所谓统一而把七阶已有成熟生产主链一次性重写。
6. 四阶现有 manual4.sqlite、素材文件、提示词 SHA、Job、Run、交付与人工验收证据全部保留。旧作业在途时不切换执行者；新任务渐进改走公共底座。严禁迁移期间两边双写、重投或删除旧媒体。
7. 一次配置的能力判断必须基于本次选择的真实配方：缺少可选超分、LoRA 或未选择的低显存模型，不能阻止已有基础 H3 路线；缺 Qwen 时，用户应仍可上传图片，完成 H3 生成。
8. 四阶失败不能阻断七阶启动。公共基础能力发生故障需明确报告公共故障域，不伪称两条线完全物理隔离。
9. 任何修复只在真实必要责任层实施。禁止为修复阻滞新增第二检查链路、第二下载器、第二登记器、第二 GPU 仲裁和第二 ComfyUI H3 提交入口。

## 1. 目标架构与职责

统一平台：项目创建、生产线选择、基础设置、资产库入口、受管资源与硬件状态。

七阶段 AI 导演业务：小说/创意 -> 文学与剧本 -> AI 分镜和导演风格 -> 自动素材需求与调用 -> AI H3 提示词 -> 导演包/排片 -> 生产、后期与交付。保留当前七阶业务状态、原有用户交互、既有历史项目。

四阶段 H3 自主制片业务：人工分镜导入/编辑/冻结 -> 素材需求/引用 -> 手写 H3 提示词/批量排片/冻结 -> 成片/返工/交付。保留当前四阶业务状态、人工创作边界、独立 UI 和项目数据。

公共素材中心：统一上传、文生图/图像编辑（仅开放真实已验证能力）、声音素材能力、素材版本、内容 SHA、授权和 QC、生成任务执行、跨项目显式分配。同一素材版本可被两条线按权限引用，但两条线镜头绑定/需求确认独立。

公共 H3 制片底座：工作流能力和配方、图/音/视频参考槽、模型量化和 LoRA、加速/二采/超分、ComfyUI 受管提交、GPU 独占、失败对账、技术质量回执。分别接受 AI 自动编译提示词和用户手写冻结提示词两种来源，绝不混淆。

统一基础配置：现有 ComfyUI 根目录、Python、custom_nodes、H3/Qwen/其他模型、工作流登记、哈希核验与缺项自动整备；两线只有一份已确认事实，更新由现有唯一 Setup Worker 执行。

## 2. 远端代码现状：已核查模块及衔接策略

| 当前代码位置 | 已有能力/现状 | V2 收束方向 |
|---|---|---|
| backend/shine/comfyops/runtime_configuration.py | load_runtime_recipe、resolve_runtime、configuration_identity、runtime_report | 作为统一环境配置事实权威 |
| backend/shine/comfyops/resource_resolver.py | resolve_resources、validate_resource_plan | 提供统一按配方能力裁决 |
| backend/shine/comfyops/scheduler.py | ResourceScheduler、resource_leases GPU 账本 | 保留成熟资源裁决，扩展跨线统一入场 |
| backend/shine/platform_gpu.py | 四阶新加 OS Mutex 与独立 acquire_m4_gpu_lease，亦读写 resource_leases | 收束为原 GPU 调度的公共互斥与兼容代理，取消双裁决 |
| backend/shine/asset_center/models.py | LibraryAsset、LibraryVersion、LibraryRights、LibraryRenderTask 等 | 资产/生成的共享权威，不复制新资产中心 |
| backend/shine/asset_center/service.py | 素材媒体版本、权利、晋升、项目分配 | 给两线提供中立能力契约 |
| backend/shine/asset_center/execution.py | 既有素材生成与 Worker 执行 | 保留唯一素材任务执行入口 |
| backend/shine/api_asset_center.py | 素材上传、搜索、生成、晋升、引用等现有接口 | 原七阶兼容；四阶走平台中立适配器而非调用七阶项目业务接口 |
| backend/shine/comfy/graph_compiler.py | compile_ref2va_workflow 与成熟 H3 图编译配方 | 公共 H3 配方/图编译的现有核心候选 |
| backend/shine/runtime_orchestrator/ | 已有受管运行操作和服务恢复控制 | 运行实例控制与整备入口唯一化 |
| backend/shine/production/runner.py | 七阶 ProductionRun 依赖已确认导演包 | 只共享底层执行能力，不要求四阶创建七阶导演包 |
| backend/shine/manual4/materials/service.py | 四阶独立 Qwen 生成、资产版本、晋升/绑定 | 保留分镜需求和引用绑定；图像生产/共享版本迁往公共素材底座 |
| backend/shine/manual4/comfy/h3_adapter.py | 四阶独立 H3 配方和对象图 | 逐步转公共 H3 合同，避免维护两套配方 |
| backend/shine/manual4/comfy/managed_instance.py | 已读取既有 managed_recipe.json，共用 ComfyUI 根和节点，但自己做运行环境适配/启停 | 保留可隔离运行目录和端口；去掉独立环境事实/重复受管控制 |
| backend/shine/manual4/worker.py | 四阶独立 Run 编排并直接向 ComfyUI 提交 | 保留业务编排和历史作业兼容；新底层提交改走统一 H3 执行入口 |
| backend/shine/manual4/status/readiness.py | 四阶业务阶段+部分技术就绪条件 | 四阶只判业务状态，资源与参考技术合法性由底座裁决 |
| backend/shine/main.py | create_app 无条件初始化 manual4.sqlite | 四阶不可用时需要可观测降级，不能拖垮七阶 |
| backend/shine/platform_projects.py | 统一项目选线，历史项目默认 director7 | 选线维持不变，跨 DB 创建恢复要验收 |

上一版 V1 的“manual4 严禁导入所有 comfyops/asset_center/h3 模块”静态规则必须精确修订：四阶可以通过正式公共能力门面使用底座服务，**但不得越过门面直接依赖七阶导演/文学/导演包业务内部实现**。原七阶行为不得被静态白名单更新改变。

## 3. 统一环境事实与一次配置合同

唯一入口以现有成熟资源配方/安装整备事实为基准，建议在轻量 public facade 中暴露：

- EnvironmentSnapshot：config_revision、配置内容 SHA、ComfyUI 安装根、Python、custom_nodes、模型与 LoRA 清单、注册工作流清单、能力摘要、来源证据及检测时间。
- CheckCapability(recipe, source_line, project_id)：只核验该次选择的 H3/Qwen/LoRA/二采路线，返回 ready / missing_required / optional_missing / temporarily_busy / unsupported，附唯一修复动作和出处。
- PrepareMissing(recipe, authorized_task)：调用原项目唯一安装 Worker/下载器/断点续传/哈希校验/登记逻辑，绝不由四阶直接再下载或要求手动登记。
- ResourceIdentity：实际模型路径、内容 hash、节点 ID 和真实 workflow 版本统一引用，不依赖四阶前端硬编码判断。
- RuntimeInstanceRequest：双方运行实例可隔离端口、进程、input/output/temp/user/log；底层 Python/custom_nodes/权重等始终共享统一安装根。

禁止新建第二套持久化环境权威数据库。临时缓存必须可从唯一环境事实重新得到；不得依赖四阶私有登记表来断言“电脑配置完成”。

用户体验验收：原七阶配置完成的电脑新建四阶项目，不再弹出安装/配置引导；模型文件已存在则自动识别和登记；Qwen 不存在不妨碍上传图片后用 H3 拍摄；未选择的超分模型不阻断基础 H3。

## 4. 公共素材中心合同与双业务适配

建议稳定的素材请求合同（可调整名称）：

- AssetUseRequest：requesting_line、requesting_project_id、source_revision、entity_name/variant、media_family、intended_usage、required_rights。
- AssetVersionRef：library_version_id、content_sha256、media_kind、rights_snapshot/授权 ID、technical_qc、provenance、version_hash。
- 公共操作：Search/Upload/Generate/Promote/Resolve/Grant/GetImmutableMedia，实际生成只通过现有 asset_center/execution.py 等唯一执行入口。
- 生产线业务决定何时使用与选择哪个版本，素材中心不解读 AI 剧本、四阶分镜，也不擅自更换用户镜头绑定。
- 跨项目可复用的前提是明确的素材归属与授权；不能直接把七阶当前镜头的可变素材记录交四阶共用。
- 四阶旧 M4AssetVersion：以不可变媒体 SHA、版本和权限事实构建一对一或有历史版本的映射；历史四阶资产和文件保留，不重新生成。只对新生成任务切换到公共资产写入口；禁止长期双写两份“正式素材版本”。
- 已晋升与已绑定是不同事实：公共素材中心管理媒体技术和权利，四阶业务管理需求/绑定/用户采纳，七阶业务也独立管理自己绑定。
- 现有四阶对素材中心的调用不得通过关闭七阶项目路由守卫来“打通”；建立明确平台中立入口，原七阶 API 行为保持。
- 图生图、局部修订、多图合成、音色等能力只在现有实际生产工作流已经稳定支持时展示，缺失时明确显示不支持，禁止虚假可用。

## 5. 公共 H3 制片合同与原文保护

统一执行请求建议包含：

- H3ExecutionRequest：line_id、project_id、source_job_id、idempotency_key、source_revision、prompt_source(ai_compiled/user_frozen)、prompt_utf8、prompt_sha256、target_seconds、aspect_ratio、resolution、reference_manifest（ID、内容 hash、role、slot、label）、continuity_mode、recipe（量化模型、加速、LoRA、二采、超分）、workspace_namespace、选定工作流版本。
- H3ExecutionReceipt：唯一底层任务/外部 prompt_id、graph_sha256、recipe_sha256、模型工作流内容身份、来源业务线/镜头、提交状态、进程/lease 归属、视频内容 SHA、FFprobe 事实、技术 QC 结果、错误码、时间戳。
- AI 提示词来源保持七阶导演风格与既有编译方法；人工提示词来源必须原文原样输入，不允许模板替代。提交到 ComfyUI 的 positive_prompt SHA 与冻结 SHA 必须一致。
- H3 底座负责“工作流能否执行”和“实际执行”，两条业务线分别负责“为什么拍、拍哪一镜、何时冻结、哪个成片被采用”，四阶不需要七阶导演包。
- 参考图/音/视频数量、索引和合法性来自底座工作流能力；V1 四阶已验证 H3 INT8/PDD8 不得被夸大为所有量化/视频参考都支持，也不得用四阶有限验收范围反向禁掉七阶已有能力。
- 用户设置镜长为权威，不硬编码最多 10s；镜间 CUT、last_frame、镜内 CUT、剪辑 CUT 是不同概念。
- 以同集或显式连续组界定上镜引用；禁止把第二集首镜自动接到第一集末镜。未经过验证的跨集锚点不自动启动。
- H3 真实执行、ComfyUI 提交、GPU 使用和技术回执应有唯一底座入口；两线的业务 Worker 可以各自管理 Run 意图，但不得各自实现一套互不相识的 H3 /prompt 执行者。
- 外部提交 ACK 不明时先对账，不能重复提交。历史四阶旧作业继续沿旧快照收尾，新任务按稳定的代际版本切到统一执行入口，且不可长期保留两个可自由竞争的新任务入口。

## 6. GPU 仲裁收束（优先级 P0）

现状：七阶 comfyops/scheduler.py:ResourceScheduler 和四阶 platform_gpu.py:acquire_m4_gpu_lease 都管理 resource_leases；虽然共用 SQLite 账本，但可能分别作入场、过期和恢复裁决。必须收束。

- 先列明七阶 H3 批次、图片生成、模型加载、二采和四阶素材/H3/批量 Run 的所有 GPU 入场路径。
- 以原七阶成熟的 ResourceScheduler 独占事务与 resource_leases 账本为主事实，按实际需要扩展系统级 Windows mutex 和进程身份互斥；四阶仅调用该统一仲裁，不再自己作第二次准入结论。
- 保留原有七阶“一个连续生产系列持续持有租约”的能力；新共享底座不得让七阶每镜反复装模型/启停实例。
- 两个 Windows 进程并发申请的租约必须原子，仅一方占用。另一线正常查看项目和成片，但不能启动 GPU 生产或硬停对方进程。
- 崩溃、租约过期、重启均需先证明真实进程不存在/外部提交可对账，才能释放；无法证明进入 RECONCILE_REQUIRED，禁止盲目清租约。
- 共享环境升级/安装时由同一个 Setup Worker 判断双线是否空闲；禁止任意一线运行中修改节点或 Python 依赖。

## 7. 当前代码已见缺陷的定点修复

这些问题不是另起架构，而是本轮必须关闭的真实风险：

1. backend/shine/main.py 的 create_app 无条件初始化 manual4.sqlite：要改为四阶数据库失败可诊断降级，旧七阶 API/项目/Worker 能正常启动；不能在 API 请求中反复尝试副作用迁移。
2. backend/shine/manual4/production/service.py 在 Run 创建时跨镜沿用 prev_job_id：按集与连续组边界截断；同集 last_frame 保留，跨集不能意外串镜。
3. backend/shine/manual4/storyboard/service.py 的冻结提示词在镜头摘要/对白变化而时长不变时可能继续沿用：以语义修订/引用版本作为失效依据；不变镜头及旧视频不应被误失效。
4. backend/shine/manual4/status/readiness.py 不能只凭“有任意绑定”认定 Ref2VA 就绪：使用公共底座确认已晋升、权利有效、真实媒体 hash 一致、至少一张合法图片且符合槽位要求；音频/视频不能替代必需参考图。
5. backend/shine/manual4/storyboard/service.py 的 LLM 辅助读取前 12000 字且仅在没有抽出任何集镜时触发：对长分镜未解析片段/歧义做分块处理，保留原文偏移，避免重复镜头或瞎补时长，结果用户确认后冻结。
6. 已冻结四阶手写 H3 由于镜头、时长、素材、引用改变必须显式失效，不准静默回退七阶自动提示词。生成完成的旧媒体及其验收可保留历史版本。
7. 真实媒体装配按当前已确认同集镜头顺序和用户选择的 PASSED 产物，确保返工、单镜替换和新版本交付不破坏历史。
8. 以上由原责任域的一个裁决器修复，禁止叠加平行检查器或额外配置页面。

## 8. 各模块保留/合并/退役策略

| 原模块 | 目标与处理方式 |
|---|---|
| 原 comfyops 环境、资源登记与 Setup Worker | 继续作为唯一基础环境入口，非必要不改成熟行为 |
| 原 asset_center/models/service/execution | 继续作为唯一素材权威版本和实际生产能力 |
| 原 comfy/graph_compiler 与受管 H3 能力 | 继续作为共享 H3 配方和执行基础候选，先以兼容门面承接 |
| 原七阶 production/runner、导演包和七阶 UI | 保留七阶业务语义；只在必要、已验证接口上接底座 |
| 新四阶 manual4/materials | 保留需求、选择、绑定状态；新 Qwen 生产改公共素材生成 |
| 新四阶 manual4/comfy/h3_adapter | 逐项收束独立配方/参考槽位判断，改为共享工作流能力接口 |
| 新四阶 manual4/comfy/managed_instance | 保留运行实例身份与目录隔离，受管启动/恢复转公共入口 |
| 新四阶 manual4/worker | 继续管理四阶 Run/镜头任务意图和恢复；新底层 H3 作业提交改公共制片底座 |
| 新四阶 manual4/prompts/storyboard/delivery | 保留人工业务与版本，不强行变成七阶导演包 |
| 新四阶 manual4/status/readiness | 保留业务阶段判断，技术就绪仅消费统一底座事实 |
| platform_gpu | 归一化成公共 GPU 资源仲裁兼容层，避免与 ResourceScheduler 重复裁决 |
| main.py 和平台选线入口 | 最小化可靠降级与两线路由，不把业务混成一条线 |

停止“镜像能力”扩张。删除重复实现前先确认旧运行任务与历史记录不再依赖它；可临时保留只读兼容路径，**不允许两个活跃的基础能力写入口**。

## 9. 历史数据无损接入及回退

- 冻结四阶业务数据库、七阶数据库及媒体目录基线，先备份并校验 SHA 和项目/素材/Job/Run/交付条目数量。
- 四阶 M4AssetVersion 映射到共享 LibraryVersion：按内容 hash、类型、真实 QC、权利及项目授权生成版本化关联；不默认跨线共享版权，不把 mock 文件晋升成真实素材。
- 旧 M4 素材路径和记录保留，旧镜头提示词的 reference_manifest_sha256 与槽位标签不得因为后台资产 ID 映射而悄悄改变。若确实会改变生成输入，拒绝投产并请求用户重新确认。
- 四阶所有在途 H3 Job/ComfyUI prompt_id/Run 维持原执行代际处理，不自动补投。旧产物仍可预览、人工验收与装配。
- 新生成请求只有一个权威写入途径；只读比对可以临时存在，不允许资源模型、资产版本或视频回执长期双写。
- 版本回退不得删除新生成资产或恢复旧表覆写新数据；优先回退适配器的路由选择并保留已提交任务唯一所有者。
- 禁止 reset --hard、clean -fd、删除本地模型和用户素材等破坏性操作。

## 10. 五阶段收尾开发顺序（每阶段独立提交）

### P0：冻结事实、隔离与迁移准备

- 审查最新 main 与本地未提交修改，记录现有基础环境、七阶/四阶调用图、源数据事实、底层任务与 GPU 资源关系。
- 输出模块责任地图、旧实例归属与历史任务/媒体备份，明确原有生产能力基线。
- 完成七阶和四阶现有测试；不要把历史存在的失败误记为本次建设失败。
- 过闸：无用户数据损失、可回退、知道每一个底层执行写入口。

### P1：统一环境事实与唯一 GPU 仲裁

- 四阶只读已配置的 ComfyUI/模型/节点/工作流事实，不新增自身整备；统一 ResourceScheduler/OS 互斥，统一安装节点与模型的 Setup Worker。
- 解决四阶 DB 损坏拖垮七阶启动的风险。
- 过闸：配置过七阶的电脑进入四阶无二次配置；真实 GPU 双进程互斥，四阶故障不阻断七阶。

### P2：统一素材中心

- 建平台中立素材能力合同，四阶经统一素材中心执行生成/上传/晋升/引用；实现历史四阶素材无损版本映射。
- 七阶素材中心仍按原行为运行，不强制大改其 UI。
- 过闸：一次生成、多线授权复用、两线业务绑定独立，旧素材不重新生成、不重复写入新任务。

### P3：统一 H3 制片执行底座

- 建标准 H3ExecutionRequest/Receipt；七阶原有成熟 H3 配方和调度合同保持兼容；四阶新任务转唯一真实执行入口。
- 同时修复跨集 last_frame、人工原文 SHA、参考槽位和执行对账。
- 过闸：七阶/四阶各自能真实单镜及多镜出片；两个业务 Run 独立，唯一底层执行与 GPU 账本，无二次提交。

### P4：收束四阶缺陷、UI 和状态

- 修复分镜修订失效、长文本解析、真实图像 Ref2VA 技术就绪及后期选片。
- 两线 UI 仅显示统一基础配置事实，不互相查询业务状态；所有阻滞提供单一定位与修复入口。
- 过闸：至少两集多镜、跨集边界、人工原文冻结、历史工程回放完整通过。

### P5：全量回归、退役重复能力、最终验收

- 审计重复代码与运行调用图，在没有旧作业依赖后停止旧四阶重复的环境/图像生产/工作流编译/底层 H3 /prompt 提交入口；必要历史只读兼容保留。
- 运行全量测试、共享配置/素材/H3 底座专项、真实 RTX 5080 出片及数据库故障注入。
- 过闸：一次配置贯通两线、双基础底座唯一、七阶无回归，提交可核查的真实媒体和报告。

## 11. 最终验收清单（逐条留证，不能只报一句 PASS）

| ID | 场景 | 通过要求 |
|---|---|---|
| A01 | 已配置七阶用户创建四阶作品 | 无第二次 ComfyUI/Python/节点/模型路径配置 |
| A02 | 本机已经安装 H3 模型但旧登记缺失 | 自动识别与登记，不重复下载/手动打点 |
| A03 | 仅缺未选择的超分 LoRA | 已可用的基本 H3 任务不阻断 |
| A04 | Qwen 缺失但用户上传合法参考图 | 四阶 H3 仍可实际生产 |
| A05 | 素材中心生成一个人物参考图 | 一个真实生成任务、一个权威资产版本和 SHA |
| A06 | 七阶与四阶分别选择同一素材 | 各自绑定独立、权利许可有效、版本有据可查 |
| A07 | 现有四阶素材/冻结提示词/成片迁移 | 原件原 SHA 不变、可继续播放和使用 |
| A08 | 七阶完整业务工作流 | 创作/导演包/素材/H3/成片均无回归 |
| A09 | 四阶完整人工创作工作流 | 分镜/素材/手写 H3/排片/成片全链真实通过 |
| A10 | 七阶与四阶同时抢 GPU | 同一原子租约只允许一个执行，无误停另一线 |
| A11 | ComfyUI /prompt ACK 未知、Worker 崩溃 | 对账而非重投，租约不误释放 |
| A12 | 手工损坏或拒绝访问四阶数据库 | 七阶主 API/原项目/Worker 仍正常启动 |
| A13 | 至少两集多镜且第二集首镜设置衔接 | 不会误引用第一集末镜 |
| A14 | 修改画面摘要/对白/时长/引用 | 受影响人工 H3 提示词正确失效、不能自动替换 |
| A15 | 只有音频没有合法图像引用 | Ref2VA 统一裁决指出缺图，不能假通过 |
| A16 | 12000 字以上混合结构分镜书 | 保持原镜头与来源偏移、补充解析未识别部分 |
| A17 | 输入 H3 原文带引用标签 | 最终真实提交文本 SHA 与用户冻结 SHA 一致 |
| A18 | 同一业务任务重复点击/网络重试 | 底层只有一次真实执行，幂等 |
| A19 | 双业务前端刷新/项目切换 | 业务阶段互不影响，基础能力事实一致 |
| A20 | 切换执行入口时已有在途四阶任务 | 原任务不丢、不盲重投，历史作业能对账 |
| A21 | 基础节点、模型缺项自动整备 | 原唯一 Setup Worker 执行、两线同步读取结果 |
| A22 | 双线共用同一 ComfyUI 根目录 | 单套 Python/节点/权重，独立端口/工作目录/进程归属 |

后端：运行原有八分片脚本 run_backend_shards.ps1，并单独执行 pytest backend/tests/manual4/ -q；注意八分片脚本只搜 backend/tests/test_*.py，不能当成自动覆盖 manual4 子目录。同步执行项目现有七阶导演/Comfy/素材/资源专项测试、Ruff、TypeScript build、前端 Vitest。

真机：RTX 5080 上按实际可用模型做七阶和四阶各自真实素材准备、H3 出片与成片 FFprobe/文件 SHA 验收；不能以 mock 代替真视频，也不能以 5080 伪称 4070/5090 通过。保存产物位置、时长、尺寸、帧率、音视频轨和解码质量、GPU 租约日志、Job/Run/外部 ComfyUI prompt_id、提示词 SHA。

## 12. 停止红线、风险处理和测试签收

阻断级：新增四阶导致二次基础配置；公用模型和节点需要重复下载/登记；七阶 API 因四阶 DB 失败无法启动；两个 GPU 准入裁决并存；用户素材/旧成片丢失；手写 H3 被替换；跨集串镜；七阶真实生产回归失败。

可列为后续：没有真实硬件的低显存/其他 GPU 专项、未被验证的原视频引用/加速/高级 LoRA/可选超分与复杂图像编辑。这些功能需准确显示“未支持或未验收”，不能阻断其他可用路线。

交付文件：
- docs/SHINE_FOUNDATION_BOUNDARIES_AND_CALL_GRAPH.md：模块责任与实际唯一事实/执行入口清单。
- docs/SHINE_FOUNDATION_CONTRACTS_AND_MIGRATION.md：公共合同、旧四阶素材与 Job 代际映射、回退与故障恢复。
- docs/SHINE_FOUNDATION_DUAL_PIPELINE_FINAL_ACCEPTANCE.md：A01–A22 逐项 PASS/FAIL/未验证及日志、媒体证据、Git SHA。
- 本地 main 分阶段提交记录和最终变更文件清单、真实验收视频及 SHA、已知风险说明。

只有两条线共用基础能力成功、七阶无回归、四阶真实可用、历史数据无损和共享资源唯一裁决均有可检查证据时，才签发最终 PASS。

## 13. 本地 Agent 执行契约

本文件所在分支只保存收尾设计文档。开发机应先保护本地 main 未提交更改，安全同步 origin/main，再从本文独立文档分支仅检出该 MD 文件到本地 main；不得将整个 docs 分支合并覆盖本地主线。

按 P0–P5 自主开发、测试、根因修复、分阶段提交本地 main。遵守最小侵入、兼容旧七阶、保护历史四阶、公共能力只做一套。不得新增并行第二检查/安装/下载/登记/生产/调度链。禁止 reset --hard、clean -fd、覆盖用户数据或直接变更用户共享 ComfyUI Python/节点，除非经过既有受管整备授权并确认作业空闲。

完成后输出提交 SHA、变更清单、验收证据、真实出片路径、失败项和硬件限制。**未经用户明确指示不得自行推送远端 main，也不得自动合并 PR。**

---

## 14. 架构冻结结论

**Shine 的七阶段 AI 导演与四阶段 H3 自主制片是业务相对独立的两条生产线；素材中心与 H3 制片是平台级相对独立的两套公共能力底座；ComfyUI、Python、custom_nodes、模型、LoRA、工作流、环境事实及整备入口只有一套。两线仅在创作与编排方式上不同，共同调用统一底座。一次配置贯通两线，避免重复建设与配置阻滞。**
