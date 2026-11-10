# Shine 视频工作台｜统一新机安装、旧版升级与完整能力部署 V2.0 工程建设方案

> 状态：实施前设计基线（PLAN ONLY，未声称代码或实机完成）。  
> 文档日期：2026-11-10。  
> 仓库：anjingdtl/shine-video-studio。独立分支：feat/unified-deployment-upgrade-v2-20261110。  
> 写作依据：main 提交 d8c564bdb5be90792cd0a52d5bcc8b58f64816af。**该 SHA 仅是方案分析快照，不是开发期间强制锁定的主线。**  
> 目标平台：Windows 10/11 x64，NVIDIA GPU；保留当前受支持的 H3 Ref2VA INT8 ConvRot 组合。  
> 施工约束：本方案提交仅增加此 MD；代码开发由后续 Agent 在该分支分阶段执行，不修改 main，不覆盖其他开发任务。

## 0. 决策摘要与非目标

**产品目标**：GitHub clone、源码 ZIP、正式发行 ZIP、已经安装的旧版，最终进入**同一个部署管理器**；在用户一次明确确认必要下载、磁盘占用、许可与升级影响后，系统自动完成“检测环境 → 安装/升级/修复依赖 → 部署模型/节点/工作流 → 验证/激活 → 可恢复交付”。界面统一提供“安装依赖、检测环境、检查升级、完整能力部署”四项入口，首次使用另有“自动完成全部准备”。

**P0 核心**：
1. 安装身份注册与旧版发现：新版换目录也能识别旧安装，数据与模型不丢失。
2. 版本化事务：可恢复、可审计、可中断续接，不把启动时直接运行 Alembic 当成安全升级。
3. 完整能力依赖图：权重、ComfyUI 主体、节点、Python 环境、工作流及验证证据绑定为一个可计算闭包。
4. 一键主流程：差量下载、精确身份校验、授权、受管实例/工作流组装、分层真实验证、用户可理解的反馈。
5. 与正在开发的导演模式、素材中心、LLM 配置等其他分支并行，**最小修改共享文件**。

**非目标/禁止事项**：不改变 MiniMax-H3 作为唯一正式视频生成核心；不悄悄改用低显存/其他模型；不绕过人工素材权利审批、风格冻结与未知外部请求对账；不自动安装显卡驱动、不擅自升系统级 Python/CUDA/Node；不越权接管或修改外部 ComfyUI；不自动下载未钉住来源、大小、SHA256 和许可证的资源；不将云端 Codex 变成运行时依赖；不混同 Mock、CPU 合同、GPU 真实推理；不自动覆盖用户自定义工作流、项目数据或凭据。

## 1. 已核实的仓库现状（main@d8c564b）

| 环节 | 现有文件与机制 | 工程判断 |
| --- | --- | --- |
| 开发机根入口 | 根目录“启动 Shine 工作台.cmd” → scripts/start_dev.ps1；scripts/start_shine.ps1 | 依赖已有环境，不等于 Clone 后直接可安装 |
| 正式分发入口 | packaging/windows/一键安装并启动.cmd、SETUP.cmd、scripts/install_once.ps1；由 scripts/build_test_package.ps1 提升到 ZIP 根目录 | 发行 ZIP 路径可用，但 Clone 根入口与发行包入口尚不统一 |
| 系统工具自举 | scripts/bootstrap.ps1、config/deployment/tools-windows-x64.json | 原生 PowerShell 5.1，工具固定版本、哈希、安装盘、缓存；可复用 |
| 基础应用依赖 | scripts/start_shine.ps1：uv.lock 指纹 → uv sync --locked；package-lock.json 指纹 → npm ci | 已有幂等刷新，不构成完整原位升级/失败回退 |
| 数据库 | scripts/start_shine.ps1 启动时执行 Alembic upgrade head；backend/alembic/versions；backend/tests/test_migrations_windows.py | 已有向前迁移测试，未构建跨版本升级前一致性备份、切换、恢复的一体化协调器 |
| 宿主探测 | backend/shine/comfyops/host_env.py、recommendation.py、variants.py；scripts/check_environment.ps1 | GPU、内存、磁盘、工具、版本化准入阈值；必须复用，禁止第二套阈值 |
| 已有 ComfyUI | backend/shine/comfyops/existing_installation.py、adoption.py；api_setup.py | 有只读扫描、哈希核验、模型导入、严格收编接口；浅层目录扫描，不默认追踪外部 model paths |
| 受管安装 | backend/shine/comfyops/installer.py、installation.py、installation_worker.py、instances.py、runtime_dependencies.py | 受控下载、.part 续传、回执、计划审批、实例 SNAPSHOT/RESTORE/ROLLBACK、离线锁依赖；作为底座扩展 |
| 安装管理 UI | frontend/src/components/SetupPanel.tsx、frontend/src/setupExecution.ts、DirectorSetupPanel.tsx | 有首次配置、尝试续接与已有模型检验；普通用户尚未形成旧版升级统一旅程 |
| 模型和工作流 | config/deployment/h3-ref2va-windows-x64.json、qwen-image-2.1-windows-x64.json、variants/、config/workflows/、backend/shine/comfyops/deployment.py、service_validation.py | H3 有版本化组件清单；Qwen 清单主要是权重，需要更完整的能力闭包合同 |
| 完整新机实测 | docs/NEW_MACHINE_ACCEPTANCE_CHECKLIST.md、docs/NEW_MACHINE_DEPLOYMENT_FIX_20261101.md | 历史记录表明干净 Windows 全链、60.8 GB 全量权重首装及首次真机推理尚不能宣称全 PASS |

**已有的正确机制不可推倒重写**：
- 模型用 byte size 筛选候选、SHA-256 确认真身；大小或文件名本身不是验证。
- 当前受支持 H3 组合的真实准入门槛来自 variant manifest，而不是安装器新写的常数。
- 下载器已支持分段/重试/部分文件继续下载；需求是向统一安装状态展示它，而非另造不受控下载器。
- 一次明确下载授权可以覆盖**展示过的固定资源清单和有序安装步骤**，但变更了计划哈希就必须重新授权。
- 真实服务验证使用 live object_info 和服务身份，不把“文件已到位”认成“运行已可用”。
- 受管 ComfyUI 实例的进程、GPU 独占租约和外部请求身份受保护，不允许安装器盲杀。
- 上游 GPL、许可证和用户本地资源边界继续适用，不复制外部 GPL 插件到本仓库。

**待解决的具体缺口**：
- Clone 后根目录没有与发行 ZIP 等效的正式一键安装入口。
- 新版 Clone 到新路径时缺少与旧版稳定 Installation ID 的确定性映射。
- 现有安装“已安装”判断不足以涵盖跨版本应用、数据库 Schema、受管依赖/工作流与运行证据。
- SetupPanel 自动分支对已有外部 ComfyUI 的普通用户路径仍存在“检测到了但不能直接继续”的障碍，尽管后端已有严格收编。
- extra_model_paths、用户授权的外置模型目录与多个 ComfyUI 实例尚未形成统一可见的安全检查计划。
- 固定 180 GiB 选盘策略不能表达升级时真实增量、回滚空间与共存版本所需预算。
- ComfyUI 的模型/自定义节点/工作流之间缺乏全能力、全版本的统一依赖闭包，升级不能只靠旧回执。
- 根启动脚本的 Alembic 自动升级需要与安全升级事务拆分。
- 包、源码和旧版之间缺乏统一应用代码回滚与自动恢复边界。

## 2. 并行开发下的基线与代码所有权策略（必须遵守）

### 2.1 分支策略

本方案分支建立时的 main SHA 为 d8c564b。其他功能分支持续开发，main 可随时变化。Agent 施工前必须：
1. git fetch origin，记录 origin/main 最新 SHA、工作分支 HEAD 和 merge-base。
2. 阅读当前 AGENTS.md、最新变更的接口与迁移头（特别是 api_setup.py、SetupPanel.tsx、models/schema、startup），不能把本方案的历史路径/契约当作永恒事实。
3. 列出本分支即将修改的文件、与 main 新增提交的交集，做到**先复用新增能力，避免双重实现**。
4. 在干净工作树且无冲突时才安全 rebase/merge origin/main；发生共享文件或 Schema 冲突时停下列出冲突，保留两边改动，不使用 reset --hard、force push 或覆盖最新 main。
5. 严禁切到 main 进行开发、提交、推送。用户另行验收前，Agent 不自行 git push、不建 PR、不 merge、不发布 Release；在当前 Agent 工作区允许本地提交并提供清单。

### 2.2 修改范围及冲突控制

**优先新增目录**：
- backend/shine/deployment_manager/：安装发现、manifest 解析、计划、版本切换、恢复、状态与 API 编排（可按项目当前目录命名约定调整）。
- scripts/deployment/：PowerShell 5.1 可运行的预启动发现、启动器/升级器自举、只读环境探测。
- config/deployment/capabilities/：能力包声明/闭包配置；若实际工程已有同等机制，则扩展它而不是并行建立第二套。
- frontend/src/components/DeploymentManager/：统一管理页、进度/升级确认、能力状态。
- backend/tests/test_unified_deployment_*.py、frontend/src/test/deploymentManager*.test.tsx：针对性测试。

**受控修改的现有文件**：
- scripts/install_once.ps1、bootstrap.ps1、setup_wizard.ps1、start_shine.ps1、start_dev.ps1、build_test_package.ps1、packaging/windows/*.cmd：只改入口复用、状态对接、升级事务与必要安全检查。
- backend/shine/api_setup.py、comfyops/deployment.py、existing_installation.py、adoption.py、instances.py、service_validation.py、相关 DB migration：只做薄适配、必要扩展。
- frontend/src/components/SetupPanel.tsx、DirectorSetupPanel.tsx、setupExecution.ts、路由和导航组件：薄适配、统一入口链接，不重做导演业务页面。

**原则上不碰**：导演脚本/分镜/资产冻结、后台 Worker 调度、H3 渲染质量链、LLM Provider 核心路由、素材中心业务实现、审片身份钉点和其他分支正在改造的业务核心。若发现不可避免依赖，先设计局部 API/事件桥接及最小代码差异，不借机重构全站。

## 3. 产品交互：一个入口、四个功能与主流程

### 3.1 用户入口统一

四种来源：
A. GitHub Clone（仓库根目录）。
B. GitHub Source ZIP（解压目录）。
C. 正式 Release ZIP（含 BUILD_INFO.json、PACKAGE_MANIFEST.json）。
D. 本机已存在旧版（旧发行包、旧 Clone 或旧本机环境）。

**统一入口**：目标在源码根目录增加正式、易识别的“一键安装并启动.cmd”及 ASCII 的 SETUP.cmd；发行包保留同语义入口，共同进入 scripts/deployment 下的同一受控执行链。保留“启动 Shine 工作台.cmd”作为开发模式兼容入口，清楚标记；不能使它错误提示“已完成正式安装”。scripts/build_test_package.ps1 应保证根目录双份入口只有一个权威来源并通过逐字节一致性测试，不能出现源码与 ZIP 行为漂移。CMD 入口保持纯 ASCII、CRLF；含中文 PowerShell 5.1 脚本必须 UTF-8 BOM。原生 PowerShell 5.1 可自举，不要求用户先安装 Node、npm、Python、uv、PowerShell 7。

**四个按钮**（同一状态引擎）：
- 安装依赖：自举工具、应用 Python/前端依赖、固定运行时；幂等、只补缺。
- 检测环境：只读重测硬件、工具、旧版、ComfyUI、模型/节点/工作流/存储/网络；不偷偷下载或改文件。
- 检查升级：比较当前安装与选择的目标已验证版本，生成精确差异和备份/停服/影响报告。
- 完整能力部署：对已选受支持能力执行“模型—节点—运行时—工作流—验证”闭环；不默默加入未选可选模型。

首次进入另提供唯一明显主按钮“自动完成全部准备”；目标展示五阶段：
1. 发现当前电脑与旧安装；
2. 选择/确认安装位置及部署能力，展示增量下载、磁盘峰值、授权及可能停机；
3. 预检与生成有哈希的安装/升级计划；
4. 下载、安装、数据库升级、配置和工作流验证（分步骤可暂停/续接/重试）；
5. 出具分层真实就绪报告并进入工作台。

重要：**一次必要确认后全自动**不等于绕过许可证、硬件限制、密钥、用户素材授权或不可确定的运行结果。存在多个旧安装、目标硬件不满足、未授权外部目录和无法安全恢复的状态时，必须展示清楚阻断原因/最少必要操作，而不是假装能够全自动解决。

## 4. 安装注册中心、版本身份与旧版发现

### 4.1 新的稳定安装身份

定义 machine-local Installation Registry。优先位于当前登录用户可稳定访问的固定位置（建议 %LOCALAPPDATA%/Shine/deployments/registry.json，具体与旧目录相容性核实）；系统级注册表仅作可选发现源，不成为安装前置依赖。采用版本化 Schema，锁与原子写临时文件替换、写入校验值、回执备份，防断电破坏。

每条记录至少包含：
- installation_id：安装首次注册时生成的稳定 UUID（与项目 ID、Git commit 无关）；
- channel：clone/source_zip/release/legacy_import，source_revision、build_id、package_manifest_hash、current_version、target_version、previous_version；
- root_install_path、active_app_path、staged_app_path、data_path、database_path、secrets_path、bootstrap_root、runtime_roots、model_cache_roots；
- repo_link（可空）、managed_comfy_instances（精确身份/所有权）、external_comfy_roots（仅授权扫描、只读）；
- Alembic current/head、app dependency lock hashes、runtime profile digests、capability manifest hashes；
- last_healthy_release、last_verified_capabilities、active_upgrade_id、upgrade_state、recovery_receipt、log_location；
- created_at、updated_at，标明该记录的进程/文件所有权范围。

**隐私安全**：不保存 API Key 明文、数据库/用户素材字节、系统敏感信息。密钥只引用现有 secret store；所有路径在使用时重新解析、校验目录身份与允许的 root，拒绝符号链接越界/路径穿越/不可信 ZIP。

### 4.2 旧版发现与接管

升级器按顺序执行：
1. 检索稳定注册中心与本机历史安装回执。
2. 查找明确传入的旧目录及历史配置的 SHINE_DATA_DIR、SHINE_BOOTSTRAP_ROOT、.env、SQLite、PACKAGE_MANIFEST/BUILD_INFO（存在则校验）、受管进程记录。
3. 非侵入性探测常见安装目录和用户显式选择的目录；搜索范围有上限，不做整盘无界扫描。
4. 使用物理目录身份、数据库身份、运行回执、旧配置的交叉证据归并同一安装；不能只凭相同文件夹名认定。
5. 多实例时展示版本/路径/最近活动日期等可核实信息，请用户明确选一个；不可误合并、不可自动“修复”用户主安装。
6. 选定后**导入旧安装索引**，不移动旧媒体、不修改外部 ComfyUI；先生成升级报告与快照，再执行。

新 Clone/ZIP 与旧版不同目录时，新代码视为“候选目标版本”，使用旧 Installation ID 的 data_path 与共享只读资源索引。绝不默认创建空库覆盖旧库、改写旧 .env 或重置旧 secrets。无旧安装时才创建新的 Installation ID。

### 4.3 运行期单一真相

必须明确优先级：显式用户选择的 Installation ID > 经验证的注册记录 > 经证据推断的 legacy 安装 > 空白新安装。与后端 Settings、现有 .env、脚本存储选项集成，避免“管理器认为 A、API 使用 B、Worker 使用 C”。启动后校验 API/Worker/前端的 data_path、release_id、recipe_hash 一致，发现不一致禁止标记升级成功。

## 5. 版本升级事务与回滚

### 5.1 五类版本身份

对比不只看 Git commit。建立下列独立指纹：
1. 应用：commit/build/manifest，以及实际装载源码身份；
2. 业务数据库：Alembic revision heads + 迁移前后完整性；
3. 应用依赖：uv.lock、package-lock.json、运行环境及工具来源；
4. AI 能力：variant/capability manifest、各权重/节点/runtime/工作流文件 SHA、版本依赖；
5. 激活配置：managed_recipe SHA、实例进程身份、用户路由/项目绑定及本地验证回执。

差分输出 KEEP / INSTALL / UPDATE / REBIND / REVALIDATE / BLOCKED。版本不变但数据文件损坏也应生成修复计划，避免仅根据旧 receipts 宣称完好。

### 5.2 可恢复升级阶段

升级状态机至少：
DISCOVERED → PLAN_READY → APPROVED → QUIESCING → BACKUP_VERIFIED → STAGING → COMPONENTS_READY → DB_MIGRATING → CANDIDATE_VALIDATING → ACTIVATING → HEALTH_CHECKING → COMPLETED；异常进入 PAUSED / RETRYABLE / BLOCKED / ROLLBACK_PENDING / ROLLED_BACK / RECONCILE_REQUIRED。

强制约束：
- 每次升级 upgrade_id + origin/target revision/hash + plan_hash + 一次性授权 + 操作员意图 + 写入回执；
- 同一 installation_id 仅允许一个活跃升级；操作加文件锁、SQLite 事务和带身份的 fencing，防并发重复执行；
- 外部下载可以安全续传；不确定的系统命令、已发出的 H3/外部请求不可盲重放；
- 先检查是否有活动 Run/series、GPU lease、受管渲染器、下载/安装尝试及外部回执；仅证实归属并按既有安全协议停止，不能杀用户其他服务；
- 先下好可复用文件并预安装隔离环境，**提交业务数据库迁移前**必须完成停服与一致性备份；
- SQLite 必须用安全的在线 backup API 或停服 checkpoint/数据库复制方案处理 WAL/SHM，不得仅复制正在写入的 shine.db；备份后实际执行 PRAGMA integrity_check，并记录 schema 和 hash；
- 用户配置、Secrets、原数据库、资产/作品路径、旧 release 入口、受管 ComfyUI recipe 必须具备可校验恢复清单；禁止把整份 60GB 权重每次重复备份；
- 应用采用 side-by-side 版本目录和稳定 launcher 切换指针；旧版本至少保留一个可启动版本；运行时/模型尽可能用只读内容缓存与独立绑定目录；
- 数据库升级按 Alembic head 与迁移历史执行；已有历史冲突只报告阻断，不删除行、清库或随意 stamp head；
- 按 API、Worker、Web、数据库版本与管理 recipe 逐一检查健康，检查通过再完成激活；
- 切换前失败：恢复旧 launcher/原运行时；若尚未改变数据库，无须恢复数据库；切换后失败且**新版本尚未接受业务写入**，可以通过经过核验的快照回退数据库；若新版本已经产生新用户写入，绝不能简单用旧库覆盖，必须冻结并提示可审计的 RECONCILE_REQUIRED、保留双方数据与日志；
- 进程重启、关机、网络中断后可从 durable checkpoint 继续或确定性进入人工核对状态。

### 5.3 与旧版目录兼容

不要强制既有用户迁移到新盘。推荐新安装结构（示意）：
    
    ShineVideoStudio/
      launcher/
      versions/<release-id>/
      runtimes/<fingerprint>/
      cache/<sha256>/
      data/       # projects, database, secrets stay persistent
      backups/<upgrade-id>/
      deployment-state.json

但现有安装若已采用其他目录，使用注册中心映射到既有位置，保留原有 .env/快捷方式与专业自定义路径；只有需要改变时提供迁移计划和恢复点。新升级器自身的更新必须采用稳定小型 bootstrap 或旁路 staging，不能在运行中删除/替换自身文件而导致半安装。升级前检验磁盘分区、可写权限、NTFS 重解析点、文件被占用及路径大小写归一化。

## 6. 完整能力包：统一依赖图和资源闭包

### 6.1 新增能力清单 Schema（建议 v1）

每个 Capability Bundle 必须有独立 ID、版本、支持状态与可验证闭包。字段包括：

- capability_id、capability_version、manifest_schema_version、license/consent、purpose、platform；
- support_state: SUPPORTED / CANDIDATE / EXPERIMENTAL / BLOCKED，以及可追溯 real-machine acceptance evidence；
- variant_id、comfy_source_id/revision、minimum_host_requirements（引用现有 variants 模块，不重复硬编码）；
- components：role、kind（model/node/runtime-wheel/source/tool/workflow/service）、component_id、revision、source、license、verified byte size/SHA256、destination family、supported host；
- dependencies、conflicts、runtime_isolation_group、order、preconditions、optional flag、allow_reuse；
- model_filenames/ComfyUI subfolders、extra model paths 受管挂载方案、required node class+input signature+node plugin revision；
- workflow_profiles（版本/graph SHA/parameter_map/media_transfers/required_nodes/output contract）、required assets/scopes、verification recipe；
- readiness gates、human steps、post-install health checks、runtime fingerprint；
- unsupported_reason、unblock_conditions，不能用“已在 UI 中展示”替代支持证据。

使用**有向无环依赖图**求拓扑顺序和闭包，显式检测冲突/环/重复包、同名不同 hash、相同节点类在多插件中的冲突，不允许简单按文件名拼凑安装。Plan 生成需要锁定整个选择闭包的 hash；执行前复核磁盘、模型身份、旧安装配置、许可和资源清单。

### 6.2 三类能力包及边界（按现有目录）

**A. 正式视频核心：MiniMax-H3 Ref2VA INT8 ConvRot。**
- 复用 config/deployment/h3-ref2va-windows-x64.json、variants/h3-ref2va-int8-convrot.json 和现有后端安装器；保留 ComfyUI source、runtime wheels、五个 node 组件、必要模型文件及精确工作流绑定。
- 默认就绪的工作流以该版本 catalog 实际 workflow_profiles 为准；目前为 local-h3-ref2va-pdd8-2img-1aud、portrait 和 local-h3-ref2va-2img-1aud；新增其他工作流须查真实 catalog 与节点能力。
- local-h3-ref2va-pdd8-upscale-2img 当前为 deferred，独立放大模型和 MinimaxH3LatentUpscaler3DRefineHandoff 尚缺完整实证。缺口存在时必须继续标记为不可自动部署的可选能力，不能因安装了普通 upscaler node 就称为支持。
- Lowend/W6A8/NVFP4 等候选组合有独立 variant 身份及未经确认的资源/显存条件；不得静默替换默认 INT8 或加入默认自动下载。

**B. 图像素材：Qwen Image 2.1。**
- 复用 config/deployment/qwen-image-2.1-windows-x64.json 里已钉住的 UNET、文本编码器与 VAE；复用 backend/shine/materialprep/qwen_workflow.py 中的真实图结构和运行指纹。
- 当前 catalog 只有三个模型组件且 workflow_profiles 为空；**P0 必须补齐与目标文生图、图生图、局部修订、编辑/多图合成等功能逐项匹配的 ComfyUI 节点、运行依赖、API 图、参数映射和实测证据**。不得直接宣称全部能力已受支持。
- 若其中某项无可复现来源或真实 GPU 验证，则将该子能力显式标为 DEFERRED/BLOCKED；其他已验证子能力应独立可用。
- 复用 H3 的受管资源调度/独占 GPU 租约机制，必要时隔离 Python/ComfyUI runtime；不能让素材模型下载或任务绕过生产调度。

**C. 辅助审片与声音。**
- Qwen3-VL/ASR 审片模型属于可选建议服务，保持与 H3 渲染关键路径解耦；完整服务依赖、模型与服务协议按现有 review manifests 验证，未就绪不阻断 H3 基础制作。
- 声音设计/克隆/TTS 若尚无可审计固定来源或用户必须提供音色样本，则保留可配置/待验证状态，不虚构“一键自动造出完整音色”。
- LLM Provider API 的云端能力不属于本地模型权重包：安装器可保护/迁移已有用户密钥与 Agent 路由配置，不能自动生成新密钥、变更模型服务商或替用户同意云端上传。

### 6.3 节点、模型与工作流“完整性”定义

仅当以下证据全部成立，某个被选能力才能从 FILES_READY 晋升到 CAPABILITY_READY：
1. 受信来源与许可证审查记录存在；模型和节点包精确 SHA256/大小/版本验证；
2. Python runtime 的依赖来源和 hashes 固定，安装到正确隔离环境，pip check 或等效检查通过；Torch/CUDA/API 版本兼容性有证据；
3. ComfyUI 源码、custom_nodes 和 model files 在**当前真正消费这些文件的受管实例**中，不仅存在于全局缓存；核对路径/绑定/白名单/启动参数；
4. live ComfyUI object_info 返回所有 required_nodes；必要 input/output 类型及参数映射匹配；
5. 工作流的 graph_sha、parameter_map、media_transfers、模型加载选项、输出节点和配置身份与当前 bundle 一致；
6. 已经执行指定等级的真实服务验证；需要模型加载/生成的小样必须有真实 GPU 运行证据（prompt id、运行时指纹、输出可解码和必要资源观测），仅 API 健康/CPU object_info 不得提升为 GENERATION_VERIFIED；
7. 将 current installation、runtime、model files、node versions、workflow hash 与验收回执绑定，资源变更立即作废旧 Ready。

请沿用现有 backend/shine/comfyops/service_validation.py、production gate 与相关回执，不要建立与实际生产准入相矛盾的第二套“ready”标记。

## 7. 复用本机 ComfyUI、模型库与存储空间

### 7.1 三条自动决策路径

- PATH 1 — STRICT_ADOPT：已有 ComfyUI 主体源码、Python、节点、适配器、模型精确匹配，满足现有 adoption.py 的严格检查；在用户同意后受控收编，外部原目录只读，隔离输入/输出/用户目录，复用已验证资源。
- PATH 2 — REUSE_MODELS_ISOLATE_RUNTIME：外部 ComfyUI 版本或节点不匹配，但模型文件 SHA 可核验；导入内容寻址缓存、创建 Shine 自有兼容 ComfyUI 与节点/依赖，**不对外部 ComfyUI 安装 pip、git pull、删除节点或覆盖模型**。
- PATH 3 — FRESH_INSTALL：既无可用 ComfyUI 也无可验证模型；按目标能力包与磁盘预算建立全新隔离环境。

已有 ComfyUI 路径从“扫描到后停止并让用户自己去专业设置”改为能触发上述合法路径的自然向导。严格收编仍保留后端的拒绝条件，不能为了“一键”降低签名/修订版本检查。

### 7.2 扫描 extra_model_paths 与目录外资源

当前 existing_installation.py 有意不默认跟随 Junction、Symlink 或 external extra_model_paths。扩展为：
- 仅发现并**展示**配置引用的外部路径，读取 ComfyUI 支持的 extra_model_paths 配置必须防 YAML 解析执行、相对路径歧义及任意路径越界；
- 用户一次明确同意扫描的额外目录后，白名单式、有上限地遍历；每个根独立记录授予来源、范围、inode/目录身份；
- 默认不跨文件系统软链、不扫描网络共享/UNC 或未经许可目录；防循环、逃逸、硬链接变更，扫描上限/拒绝原因可见；
- 大文件先比对大小再流式 SHA256，结果绑定 stat snapshot，使用时重新校验 TOCTOU；只有精确核验资源才能复用；
- 需要缓存复制时支持受控复制/校验/断点恢复；Windows NTFS 硬链接仅在同卷、权限和安全条件明确且原文件已固定时可选，不默认采用易被外部写入改变的共享可变文件；
- 路径找不到、哈希未知、文件被占用时不猜测，允许只补缺失资源并显示预计下载量。

### 7.3 动态磁盘预算

替换“所有情况均必须先找 180GiB 非系统盘”的无差异判定为版本化、计划驱动预算。保留当前 H3 基线历史测量和安全余量，不能直接降低安全门槛。

需要计算：
- 缺失权重/节点/依赖净下载与 SHA 缓存；已验证资源复用节省；
- ComfyUI、venv、wheelhouse、素材/审片可选 runtime 的展开大小；
- 当前/目标版本并存、旧版本保留、一致性数据库快照、临时文件、实例生命周期快照；
- NTFS 同卷/跨卷实际数据复制成本、磁盘配额与安全余量；
- 峰值空间需按每一阶段的先后实际叠加，而不是将模型文件大小当作总安装峰值。

仅有系统盘的机器，若现行安全策略禁止系统盘，应清楚给出 BLOCKED 和可选的用户引导，不能偷偷改变既有安全准则。不同盘组合按实际可写空间和目标路径判断，不能只选最大盘。

## 8. 统一安装/升级执行器、进度与失败恢复

### 8.1 共用底座

复用 ControlledDownloader、installation_worker 持久化尝试、instances lifecycle、runtime_dependencies 锁定安装以及现有 Setup Execution 恢复。新增一个面向“整机/整套能力”的 Orchestrator，仅负责生成有序子计划、汇总状态和依赖授权，避免把已有工作流另写一遍。

执行步骤：
PRECHECK → ACQUIRE_LOCK → CONSENT → STAGE_RELEASE → DOWNLOAD_AND_VERIFY → INSTALL_BASE_TOOLS → INSTALL_APP_DEPS → QUIESCE_AND_BACKUP (upgrade only) → INSTALL_COMPATIBLE_RUNTIMES → INSTALL_MODELS_AND_NODES → DB_MIGRATE (upgrade only) → REGISTER_AND_VERIFY_WORKFLOWS → CONFIGURE_AND_ACTIVATE → START_AND_VALIDATE → CUTOVER_AND_RECORD。

允许在安全的可前置阶段（下载新模型、新依赖）提前进行，不得无计划先改变现有受管实例。全链路按组件进度、字节数、当前任务、重试原因、错误码、已节约下载量显示。Web 重新打开、进程退出、电脑重启后从持久 journal 恢复；*.part、内容缓存和已验证资源保持可复用。某子步骤失败时只修复该步骤，不重复安装已成功部分。

### 8.2 错误分类

- RETRYABLE_NETWORK：网络短断，保持 .part、退避重试、可更换经过审计的镜像；
- INSUFFICIENT_SPACE：阻断下载并显示计划峰值和建议目标盘；
- UNSUPPORTED_HOST：驱动/GPU/VRAM/内存/Windows 不满足，不伪造能力可用；
- SOURCE_UNVERIFIED / HASH_MISMATCH：拒绝使用该源，隔离损坏文件并记录审计；
- NODE_DEPENDENCY_CONFLICT：回到能力闭包分析，不在全局环境强制 pip install；
- LEGACY_IDENTITY_AMBIGUOUS：多个旧版或旧数据库不明，需用户选择；
- ACTIVE_PRODUCTION / OWNERSHIP_UNVERIFIED：保留业务任务与 GPU lease，不强制停服；
- DB_MIGRATION_FAILED：采用 5.2 的安全恢复条件；
- WORKFLOW_CONTRACT_CHANGED：重新登记/验证，保留用户自定义版本，不强制覆写；
- REAL_VALIDATION_PENDING：明确标示缺失真实权重、GPU 或长耗时测试；
- UNCERTAIN_EXTERNAL_OUTCOME：不自动重发远端 LLM、ComfyUI prompt 或生产任务。

日志按 upgrade_id / installation_id / component_id 组织，敏感字段脱敏；一键导出诊断报告但不包含 secrets、用户原图、影片内容。

## 9. 接口与持久化契约建议

优先沿用 /api/v1/setup 与 /api/v1/comfyops 的既有审批和实例机制。建议面向 UI 只暴露薄 facade（**正式命名前必须检查 main 的现存路由，避免重复**）：

| 动作 | API 建议 | 核心语义 |
| --- | --- | --- |
| GET | /api/v1/deployment/installations | 已识别旧安装、当前软件/数据/版本身份（只读） |
| POST | /api/v1/deployment/discover | 有界扫描，输出只读快照，不做变更 |
| GET | /api/v1/deployment/host-report | 合并现有 host_env / recommendation 事实 |
| POST | /api/v1/deployment/plans | 目标版本+能力选择+当前状态 → 不可变差异计划、预算与风险 |
| POST | /api/v1/deployment/plans/{id}/approve | 与当前已有 operator/plan 授权机制连接，不创建第二套权限 |
| POST | /api/v1/deployment/plans/{id}/execute | 执行可恢复安装/升级/修复 |
| GET | /api/v1/deployment/attempts/{id} | 组件、下载/校验/安装/迁移/验证进度与回执 |
| POST | /api/v1/deployment/attempts/{id}/pause 或 resume | 仅在安全点暂停与恢复 |
| POST | /api/v1/deployment/attempts/{id}/rollback | 严格检查切换阶段、新写入和备份证据 |
| GET | /api/v1/deployment/capabilities | 逐能力 FILES/CONTRACT/RUNTIME/GENERATE 就绪报告 |

数据库/日志设计：安装注册表应在**API 无法启动前也可读取**（bootstrap 本机配置），详细事务审计在现有 SQLite + 持久化 journal 中关联。不可将唯一安装状态存入待升级数据库而导致迁移失败后管理器无法恢复。跨文件/数据库写入必须采用先 durable intent → 文件操作 → durable receipt → 数据库状态的显式重入设计；不能声称文件系统与 SQLite 支持真正原子跨资源事务。

数据对象建议：InstallationIdentity、DeploymentPlan、DeploymentAttempt、UpgradeCheckpoint、CapabilityManifest、CapabilityVerificationReceipt、RollbackArtifact；具体是否扩展 InstallationPlan/InstallationAttempt 由 Agent 复核后决定，尽量复用并避免并行账本。版本 Schema 必须以 Alembic 单头迁移，考虑历史数据兼容、旧插件可忽略新增字段、数据库记录和外部 journal 的 crash reconcile。

## 10. Web/Windows 界面与用户体验验收

统一设置页只展示：
- 顶栏：软件当前版本、目标版本、所在安装目录、最后验证日期；
- 四个主要操作 + “自动完成全部准备”；
- 自动发现：已有项目/数据、ComfyUI、GPU、模型存量、空间，注明“可复用/缺失/冲突”；
- 确认页：本次下载总字节、预计峰值空间、将停用哪些受管服务、备份位置、保留旧版、需同意的许可证与外部访问；
- 执行页：一个总进度 + 可展开的阶段细节 + 暂停/续接/故障定位；不出现需要用户复制的命令；
- 结果页：软件可启动、受管 ComfyUI 可用、每个能力可用、是否真正实机生成/识别成功分别显示。

纯安装器在 Web 尚不能运行时，使用 WinForms 或文字引导承接同一预检/状态逻辑；前端启动后继续同一 deployment attempt，不另建假进度任务。开发用户的专业详细视图可折叠，但普通用户不看 Python venv、pip、端口、workflow hash 等工程语言。错误同时提供中文用户操作建议与可复制的技术错误码，保持专业诊断能力。

## 11. 版本化升级/部署验收矩阵

| ID | 场景 | 必要判定 |
| --- | --- | --- |
| N01 | 全新 Windows，仅有 Git Clone | 双击根目录入口，自举工具与应用服务，进入同一完整部署向导 |
| N02 | GitHub ZIP 与正式 Release ZIP | 安装行为一致；Release 包文件 SHA/BUILD_INFO 验证 |
| U01 | 原目录老版本升级 | Installation ID 保留，项目/SQLite/素材/Secrets/模型不丢失 |
| U02 | 新版下载到不同磁盘/目录 | 找到旧安装及数据路径，不创建空库误当升级完成 |
| U03 | 旧数据库从指定历史 Alembic head 升级 | 先可验证备份，迁移保留业务行、约束与项目引用 |
| U04 | 迁移中途失败/断电 | 稳定启动器进入可恢复流程，不能丢库、不能假成功 |
| U05 | 完成切换后用户又写入数据 | 拒绝自动覆盖恢复旧库，进入 RECONCILE_REQUIRED |
| U06 | 旧版本本地存在未提交代码 | 不覆盖源码、不 reset hard，提示受控升级方案 |
| C01 | 已有完全兼容 ComfyUI | 严格收编，外部原安装/用户数据不受修改 |
| C02 | ComfyUI 不兼容但模型 SHA 匹配 | 隔离新建 ComfyUI/节点，模型精确复用而不重下 |
| C03 | 仅缺某模型/某节点及 Python 依赖 | 只装必要组件并验证完整闭包 |
| C04 | 节点版本变更导致工作流参数变化 | 差量更新后 live object_info 校验通过，否则 BLOCKED |
| C05 | Qwen Image 2.1 多能力 | 子能力分开标记，未经固定/实测的编辑、多图等不能假 READY |
| C06 | H3 延后 upscale 和低显存候选 | 继续 deferred/candidate；禁止自动启用未验证组合 |
| C07 | 受管渲染器 + 图像任务争用 GPU | 无并行违规 GPU 占用、租约泄漏或中断真实生产 |
| F01 | 网络中断与部分文件 | .part 续传，hash 不变，重启恢复已验证结果 |
| F02 | 非系统盘空间不足/多盘/中文路径 | 动态预算准确；不改写不受信任目录 |
| F03 | 外部配置 extra_model_paths 和 Junction | 只对授权目录有界扫描，无路径逃逸/无限遍历 |
| F04 | 旧版运行活动生产任务/租约未知 | 停止升级并保留任务，不能盲杀或重复提交 |
| F05 | 两个安装器同时执行 | 同一 Installation ID 原子互斥；不会双重下载/迁移 |
| F06 | 使用过期批准/变更清单/有坏文件 | 旧批准失效、禁止执行、保留审计与用户数据 |
| R01 | 首次启动、二次双击、端口占用、半启动、正确停止 | AGENTS.md 的强制入口链验收全覆盖 |
| R02 | Windows 包纯 ASCII CMD/PS 5.1 BOM/CRLF | 命令解析正常，中文目录/编码正常 |
| R03 | 真实 GPU H3 最小样片 | 记录运行身份、prompt id、输出解码、资源峰值、QC；不以 Mock/CPU 冒充 |
| R04 | 真实图像生成与审片（所选可用能力） | 相同级别记录独立能力的真实证据 |

验收采用三层测试机制：
- L1 快速：静态检查、Schema 校验、依赖图、单测/差异、编码与目录安全；每次修改。
- L2 受控集成：真实 API+临时 SQLite、真实历史副本迁移、受控临时目录、故障注入、Windows 脚本测试、前端浏览器流程、安装包 manifest 核验；阶段收尾。
- L3 实机：干净 Windows、已有 ComfyUI/旧版数据、真实 NVIDIA GPU 与大型权重、下载中断/升级回退/实际 H3、Qwen 图像推理；只允许有本地机器和审批时执行。

**PASS 分层**：SOURCE_IMPLEMENTED / MOCK_CONTRACT_PASS / PACKAGE_VERIFIED / WINDOWS_INSTALL_PASS / MANAGED_RUNTIME_PASS / REAL_GENERATION_PASS；完整首次安装必须要求 Windows 真实首装 + 被选能力相应等级的真实验证。不允许将已有开发机真实 H3 证据挪作新版干净新机验收，不允许“代码测试通过”推断 60GB 模型均已下载安装。

可复用测试文件包括：backend/tests/test_new_machine_setup.py、test_package_entrypoints.py、test_bootstrap_entry.py、test_installation_scripts.py、test_existing_installation.py、test_migrations_windows.py、test_managed_comfy_bootstrap.py、test_setup_workflow_profiles.py、test_install_plan_qualification.py、test_image_installation_catalog.py、test_deployment_storage.py、test_deployment_acceptance_regressions.py；前端 setupEntry、simpleSetup、realWizard、workflowRegistration 等。Agent 必须根据开发时主线实际路径确认，而不是照搬过时测试清单。

## 12. 分阶段落地与退出条件

### P0-A — 统一入口 + 安装注册中心 + 旧版发现（先完成）

交付：Clone/ZIP/Release 同一脚本；稳定 Installation ID；能发现旧版 Data Path/版本/ComfyUI；只读发现与明确选择；不破坏开发启动器。
退出：N01/N02 的启动入口合同和 U02/C01 的只读路径通过；尚未部署大模型时标注 PENDING。

### P0-B — 安全升级事务 + 数据库备份/回滚

交付：upgrade plan、版本差异、升级锁、快照、staging/cutover、DB 一致性与写入保护、失败恢复、journal 重入。
退出：U01-U06、F04-F06 至少 L2 可重现通过；旧版用户数据正确保留；旧程序仍可恢复。

### P0-C — 能力闭包 + ComfyUI/模型/节点自动配套

交付：能力 manifest、依赖图、动态预算、复用/隔离决策；H3 全量必需节点与默认工作流闭包；Qwen 逐子能力验证和缺口标记；已有 ComfyUI 普通用户向导接通安全收编或模型复用。
退出：C01-C06、F01-F03 至少 L2 通过；不再要求用户自己 pip、放节点、改工作流 JSON。

### P0-D — 一键编排器 + 统一 UI + 真实启动验收

交付：四按钮和“一键自动完成”，安装/升级/修复差异计划，真实进度/断点续接；启动、服务、能力分级状态；文档与 installer 同步。
退出：R01/R02 + 完整安全/故障注入测试通过；实机有硬件时完成 R03/R04，否则保持待验收，不得自称全 PASS。

### P1 — 扩展兼容性与产品化

交付：复杂多盘、用户外部模型目录、跨版本组合升级、自动诊断、可选声学和审片能力包、性能优化。前提：不把 P1 内容伪装成 P0 已完成。

每阶段必须附：实际变更文件、接口/数据迁移清单、风险与恢复步骤、差异测试输出、与最新 origin/main 的冲突复核、完成/未完成/阻断事实、下一阶段进入条件。

## 13. 交付物清单与 Agent 执行规则

**工程交付**：
1. 可复用的统一部署管理器（预启动 + 后端编排 + 前端状态同源）；
2. 版本化安装注册表与旧版迁移/兼容读取；
3. 受管应用升级/恢复事务、实测数据库恢复方案；
4. 能力 Bundle Schema/闭包解析器和 H3/Qwen 子能力完整组件声明；
5. 与 ComfyUI 现有收编、内容缓存、下载、实例、授权、服务验证系统的集成；
6. Git Clone/ZIP/Release 的统一入口、编码规范与包内 manifest 对账；
7. 三层测试、异常注入案例、升级前后对账报告；
8. docs/ 安装/升级说明、已知限制、真机验收表、必要的 AGENTS.md 最小补充。

**禁止的捷径**：
- 不允许改变已有生产模型核心或默换量化模型；
- 不允许把文件存在、计划已批准、下载成功、服务可启动直接映射成完整生产可用；
- 不允许覆盖其他人在 main 上新增的能力、数据库迁移或 UI；
- 不允许使用系统全局 pip、npm -g、修改外部 ComfyUI 源码或删除用户资产；
- 不允许为了测试通过降低安全断言、跳过哈希或真实执行证据；
- 不允许自作主张覆盖生产数据库、执行强制 Git 命令、强制释放不明 GPU 进程；
- 不允许在云端 Agent 自称运行过本地 Windows/RTX 5080/真实下载；
- 不允许自行推送代码、合并主线、开 PR 或创建 Release；方案文档的推送不等同于授权后续 Agent 自动推送。

## 14. 开工前检查清单（Agent 必须首先回复）

- [ ] 已记录 origin/main 最新 HEAD 与本工作分支 merge-base，并列出自方案基线以来主线对安装/部署涉及的修改；
- [ ] 已阅读最新 AGENTS.md、相关产品决策、Windows 启动规范与目录结构；
- [ ] 已确认新机入口、现有部署/收编/回滚/下载功能，列出**直接复用**与确需新建的模块；
- [ ] 已生成拟修改文件清单、潜在共享文件/迁移冲突、实施顺序；
- [ ] 已列出旧安装、模型缓存、密钥、项目/资产/影片和 GPU 租约的保全策略；
- [ ] 已列出 H3 正式支持与 Qwen 各子能力的完整闭包及证据缺口；
- [ ] 已确认三层验收命令、云端无法完成的真实 Windows/GPU 测试及其待验收标记；
- [ ] 已确认只在本开发分支进行本地提交，**不 push、不建 PR、不 merge**。

---

**最终验收原则**：用户不必自行研究安装步骤，不必手动找模型、节点和工作流，也不因换一个 GitHub 目录而丢失旧安装；系统可自动做有证据的安全动作，对不可确认的硬件、来源、许可、外部执行和真实模型能力明确阻断。**“自动化”以资源完整、版本兼容、数据可恢复、真实能力验证为前提。**
