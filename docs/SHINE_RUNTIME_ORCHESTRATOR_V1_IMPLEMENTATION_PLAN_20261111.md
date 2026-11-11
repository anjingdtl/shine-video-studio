# Shine Runtime Orchestrator V1.0：Web—ComfyUI 统一运行调度与控制层建设方案

> 文档状态：待本地 Agent 实施（方案已推送，尚非工程验收结果）
>
> 日期：2026-11-11
>
> 仓库：anjingdtl/shine-video-studio
>
> 唯一基线：main @ f897b26a1a0d701aa44f4ea808835e31f7ef5fab
>
> 方案分支：feature/shine-runtime-orchestrator-20261111
>
> 目标平台：Windows 本地 Web 工作台，RTX 5080 16GB / 系统内存 32GB 基线
>
> 原则：效率与质量平衡；MiniMax-H3 Ref2VA INT8 是视频核心；保留现有 ComfyOps、Worker、SQLite 资源租约及真实生产证据链；禁止另造生产队列、重复进程监督器或大规模中间 JSON 流水线。

## 0. 交付目标和非目标

### 0.1 用户问题

用户报告 Web 工作台与 ComfyUI 后端间经常发生断连、重复开启、状态异常和控制失灵。当前只完成远端静态代码审查，**不宣称这些现象已经在本机被复现**；应以本地日志、进程清单和竞争场景测试确认根因。

### 0.2 完成标准

- 一次启动：同一 Shine 安装同一时刻最多一套有效 API / Worker / Web 运行栈，同一**物理 ComfyUI 安装与监听端点**最多一个受管渲染进程控制者；连续双击仅返回既有工作台。
- 单一控制权：Web、素材中心、导演模式、安装配置、后台 Worker 不再各自决定 ComfyUI 的启停；进程变更必须经过统一控制入口。
- 连接可恢复：网页刷新、短时网络错误、模型加载期探活失败不得触发重复进程启动；已进入 ComfyUI 的任务不得因为 HTTP 断线而盲目二次提交。
- 资源安全：H3、Qwen-Image 出图、放大等 GPU 任务共享原有 ResourceScheduler 串行租约；模型换载、质检隔离、受控释放仍依照现有证据链执行。
- 简单 UX：普通用户只看“制作引擎状态、当前任务、可操作的恢复建议”；工程诊断隐藏在专业设置。
- 强恢复：不可判明的外部副作用进入局部 RECONCILE_REQUIRED，而不是静默标记失败或直接重试；可证明的无害短暂断连自动恢复。
- 可回滚：分阶段开关，旧行为与新行为**不能同时拥有执行权**；数据迁移可回滚，升级不破坏本机媒体、模型与配置。

### 0.3 非目标

不开发新的 ComfyUI、不重构 MiniMax-H3 推理内核、不改变 H3 提示词/工作流、不将 ComfyUI 强制常驻、不强行接管第三方 ComfyUI 进程、不增加独立 GPU 任务队列、不重写四阶段文学流水线、不替换安装器/素材中心/导演风格等在研模块业务逻辑，不新增 Redis/Celery/Kubernetes。

## 1. 当前远端代码审计与事实基线

核查时间：2026-11-11；HEAD f897b26（上一个父提交 60f5b516）。HEAD 的最新变更主要涉及前端长剧本分段生成分镜，以下控制链仍以该 HEAD 下路径为依据。

| 现有模块 | 关键路径 | 静态审查事实 | 本轮治理 |
|---|---|---|---|
| 启动/停止栈 | scripts/start_dev.ps1、scripts/start_shine.ps1、scripts/stop_shine.py | 一键启动已支持已有栈复用、进程身份核对、端口选择、processes.json；完整清单在启动后写入 | 增加**跨进程启动锁**，将检查、收尾、端口分配、拉起与记录纳入同一受保护区间 |
| 残留后端回收 | scripts/release_residual_backends.py、backend/shine/comfyops/renderer_release.py | 已以进程出生身份、Job Object、占用状态进行安全释放，无法验证时拒绝杀进程 | 继续复用；从统一控制权入口调用，不能绕过在途任务保护 |
| H3 管理渲染 | backend/shine/worker.py、backend/shine/comfyops/handoff_startup.py | H3 由 Worker 持有租约，按启动意图、受管组、受限队列、物理显存/节点证明启动；不确定结果保留核对 | 保留 H3 handoff/lease/受控重启合同，纳入同一物理安装控制锁 |
| 图片渲染 | backend/shine/comfyops/image_renderer.py、backend/shine/asset_center/execution.py | 图片渲染有独立 start/ensure/stop 路径；素材中心任务使用 ResourceScheduler 与 ComfyUIClient；与 H3 共用物理安装身份 | 收束图片生命周期入口，确保 H3 与图片互斥，防止共享实例状态污染 |
| 图片配置控制 API | backend/shine/api_material.py | /setup/image-runtime/start、/stop 直接调用图片进程控制函数 | API 变为受理**幂等控制意图**，由统一执行者实际执行 |
| 其他运行/实例 API | backend/shine/api_setup.py、backend/shine/api_instances.py、backend/shine/api_comfy.py | 存在 runtime、managed-processes/release、instance-plans/execute、comfy/test、capabilities 等不同入口 | 统一汇聚当前运行状态及控制边界；安装/升级专属动作保留既有审核 |
| GPU 租约 | backend/shine/comfyops/scheduler.py | SQLite ResourceLease 使用 BEGIN IMMEDIATE、独占资源、超期 RECONCILE_REQUIRED | **唯一 GPU 准入账本**，不新建第二种 GPU 资源池 |
| 外部提交 | backend/shine/comfy/client.py、backend/shine/worker.py、backend/shine/asset_center/execution.py | POST /prompt 超时后可能已被接受；现有未知结果、prompt_id/历史核对、提交意图保护机制 | 严禁改成失败即重试；保留并统一暴露恢复结果 |
| 运行配置 | backend/shine/comfyops/runtime_configuration.py、backend/shine/production_mode.py | 安装/固定配方/节点证明与实际生产状态分开记录；状态可能来自不同读取路径 | 建立只读聚合快照，不重复持久化第二套“真相” |

### 1.1 已知危险交叉点（设计风险，尚待复现）

1. scripts/start_dev.ps1、start_shine.ps1 都有“先查进程清单、后启动”流程：若两次点击并发，processes.json 尚未写入时存在 TOCTOU 竞争窗口。不能把现有“正常重复点击不会再启动”误当作“并发唯一性已被证明”。
2. image_renderer.py 和 handoff_startup.py 都能为同一 ComfyUI 物理安装拉起受管进程；H3 的 run instance_id 可由 installation + attempt 派生，图片的 instance_id 可使用安装本身 ID。因此**只对 run instance_id 加锁不够**。
3. image_renderer.py 已有注释解释“出图运行修改共享安装身份会让 H3 报 ACTIVE_RUNTIME_CONFIGURATION_CHANGED”的历史问题；保留“安装身份不可随任务运行过程改写”的修复原则。
4. api_comfy.py 的连接探活以 HTTP 访问为主；HTTP 通不代表监听进程就是当前安装所拥有的正确 ComfyUI。
5. UI 的 renderer_online/生产可用、操作占用、物理进程状态、外部 prompt 状态不是同一个概念；不能用单个布尔值驱动启停/恢复。
6. start_shine.ps1 在启动完整栈之前尝试释放残留后端；该动作应继续遵守任务仍在进行、身份无法证明则拒绝释放的保护，且不能与另一个启动入口并发执行。
7. assets 独立素材中心已有并行 PR 工作；对其 API/执行层变动须按最新分支差异核对，优先提供后向兼容适配层。

### 1.2 范围边界

- Windows 本地部署默认；开发测试可以 Fake/Mock，但生产 PASS 必须有真实 ComfyUI 与真实资源证据。
- 远端 ComfyUI 或任意用户自启实例不由本控制器杀进程；没有可核实本机 Job Object 所有权则只能显示 EXTERNAL / UNVERIFIED。
- 允许不同 ComfyUI 安装注册，但本机单 GPU 保持 GPU 租约全局串行；对同一**物理安装 identity**实施严格单进程控制。
- “安装文件复用”“模型文件复用”和“接管正在运行的外部实例”是三种不同操作，不能混为一谈。

## 2. 推荐架构与约束

采用**进程内轻量统一控制门面 + 现有常驻 Worker 单一执行者 + 原 SQLite ResourceLease**，不新增常驻调度服务。

~~~mermaid
flowchart TB
    UI["Web：导演 / 素材中心 / 设置 / 安装"]
    API["FastAPI：运行控制门面及状态查询"]
    OP["轻量控制意图记录（SQLite，同库）"]
    WORKER["现有常驻 Worker：唯一受管生命周期执行权"]
    READ["只读运行状态聚合器"]
    LEASE["现有 ResourceScheduler + ResourceLease"]
    OWN["现有 OwnedProcessGroup + RendererSupervisor"]
    H3["H3 handoff / 真实提交 / 质检"]
    IMG["图片/素材中心图像执行"]
    POST["超分与后期任务适配（仅在现有能力确认后接入）"]
    COMFY["同一物理安装的受管 ComfyUI"]
    UI -->|"控制意图 / 查询"| API
    API --> OP
    API --> READ
    OP --> WORKER
    WORKER --> LEASE
    WORKER --> OWN
    OWN --> COMFY
    WORKER --> H3
    WORKER --> IMG
    WORKER -.后续.-> POST
    H3 --> COMFY
    IMG --> COMFY
    READ -.只读聚合.-> OP
    READ -.只读物理证明.-> OWN
    READ -.只读资源状态.-> LEASE
~~~

重要边界：

- API 和 Web 只可**请求**运行状态变化；不得自行创建/终止 ComfyUI 进程。
- Worker 拥有操作执行权，但继续使用 H3 当前的任务级启动与 HandoffSession；统一层不得取代其具体生产逻辑。
- 控制意图记录仅用于短生命周期动作 START / STOP / PROBE / RECONCILE，不承载镜头、素材或影视业务任务；镜头和素材任务仍走现有 Worker/生产表。
- GPU 租约代表资源准入；**不是** OS 进程锁。两种保护都必要，且锁顺序固定：先确认控制意图/物理安装所有权，后进行现有 GPU 租约准入与业务启动（避免持 SQLite 写事务等待外部启动）。
- 仅保存一份权威的生命周期控制操作记录；RuntimeSnapshot 每次从源事实派生，不新增长期并行的“状态真相”。
- 普通断连只能刷新连接状态，**绝不能**悄悄触发“启动另一份 ComfyUI”。

## 3. 唯一所有权：身份、锁、状态

### 3.1 三种 identity 必须明确区分

1. installation_identity：受管安装的持久物理身份（规范化 ComfyUI root + 端口 + 安装固定身份/版本证明）；是**所有生命周期锁**作用域。
2. runtime_identity：某次运行或使用者绑定的运行身份，可为 H3 某次 attempt 派生；不是物理安装锁键。
3. process_identity：PID + OS creation/birth 身份 + Windows named Job Object + 监听端口 PID + adapter/recipe 证明；任何破坏性操作均必须重新核验。

同一安装的 H3 与图片即使 runtime_identity 不同，也必须竞争同一个 installation_identity 控制锁。

### 3.2 两把不同的锁

- StackStartupGuard：Windows 跨进程命名 Mutex（按 data_dir 规范化键派生），由**实际执行启动的** start_shine.ps1 在“检查记录→决定复用/安全收尾→端口决议→启动→校验→写清单”整个期间持有。start_dev.ps1 只能调用共同的受保护路径，不得提前在锁外删除或归档可能被另一个启动器使用的进程清单；注意嵌套调用避免同锁自死锁。
- RendererLifecycleGuard：以 installation_identity 为键的跨进程独占锁；由统一 Worker 执行生命周期动作时使用，保护 START/STOP/RESUME/HANDOFF/RECONCILE 的关键跨进程边界。**H3 handoff 内部受控重启不得视作第二个外部控制者**。数据库操作序号/epoch 用于拒绝旧请求；SQLite BEGIN IMMEDIATE 只用于短事务，不能在长时间模型启动期间占用写锁。

锁释放后，数据库仍保留不确定启动意图；下一位执行者必须先核对物理进程、端口、租约、任务结果，不得因锁空闲直接重启。

### 3.3 统一状态（只读派生）

- STOPPED：既有所有权与物理证据证明无受管进程。
- STARTING：有进行中的启动意图，物理进程/监听可能尚未就绪。
- READY_IDLE：已证明正确受管进程、HTTP 和所需节点/固定运行合同就绪、无执行中任务。
- BUSY：受管实例在处理有效任务/资源租约；不能以“忙”判为断连。
- DRAINING：生产或 handoff 的受控排空中。
- STOPPING：受保护的停止中。
- DEGRADED：进程仍在、HTTP 探测暂时失败或能力尚未就绪；优先重新探测，不自启动。
- RECONCILING：恢复程序正通过原有记录核实任务、启动、关停的结果。
- BLOCKED_UNVERIFIED：进程/端口/租约/外部提交结果有无法证明的关键冲突；禁止危险重启与强制清理。
- EXTERNAL_CONFLICT：端口上运行的是无法证明归属当前 Shine 的外部实例，禁止接管与终止。

严禁以状态字面值直接控制进程，所有操作必须再次核对 live facts 和 operation_version。

### 3.4 单一运行快照（建议合同）

~~~json
{
  "schema_version": 1,
  "installation_id": "stable-installation-id",
  "state": "BUSY",
  "health": {"stack": "UP", "worker": "UP", "api": "UP", "comfy_http": "UP", "workflow": "VERIFIED"},
  "ownership": {"managed": true, "verified": true, "port": 8188, "pid": 12345, "process_birth": "opaque"},
  "current": {"owner_kind": "h3", "task_id": "opaque", "operation_id": "opaque"},
  "resource": {"lease_state": "ACTIVE", "gpu_budget_source": "existing_scheduler"},
  "recovery": {"required": false, "reason_code": null, "safe_action": null},
  "last_observed_at": "ISO8601",
  "state_version": 12
}
~~~

字段为**拟定对外接口**，不是声称远端已有该 API；敏感 handoff token / 原始密钥不进 Web 响应；process_birth 可仅返回验证布尔而不暴露细节。兼容旧状态 API，但在后端由统一快照提供业务字段，不让前端拼多个“真相”。

## 4. 启动与关闭策略

### 4.1 Stack 启动：真正的双击幂等

1. start_dev.ps1 转交到唯一的启动关键区；start_shine.ps1 先获取 StackStartupGuard。
2. 同一 data_dir 下已有健康 API+Worker+Web 且进程出生身份匹配：直接返回既有 web_port；不启动第二套。
3. 记录缺失或部分残留：只读核验所有登记身份，确认可安全结束才调用现有 stop_shine.py、release_residual_backends.py；不可证实则明确告警并保留证据，禁止因选择 AutoPorts 就另开第二套。
4. 端口空闲不代表身份正确；端口选定后确认监听人和进程树；启动 API、Worker、Web 并收齐三者就绪证据后持久化 processes.json（必要时先持久化启动中意图）。
5. 写清单尽可能采用临时文件 + 同卷原子替换；异常仍保存能够核对的记录。仅成功后释放 Mutex。
6. **不得仅以 API+Web 可用就判定完整栈可用**；Worker 必须有有效进程/心跳证据。浏览器多个标签页只访问同一 Web。

### 4.2 ComfyUI 启动：受管按需，不强制常驻

~~~mermaid
flowchart TD
    A["收到 H3 / 图片 / 超分能力需求"] --> B{"物理安装控制权是否可获得"}
    B -->|不可获得| Q["复用正在执行的操作 / 返回占用信息"]
    B -->|可获得| C["读取原有安装、进程、租约、启动记录"]
    C --> D{"物理状态"}
    D -->|已证明 READY| E["直接复用；不重新启动"]
    D -->|STARTING| F["关联同一操作，等待状态"]
    D -->|STOPPED 且无未知副作用| G["按现有配方和租约受管启动"]
    D -->|外部端口冲突| X["EXTERNAL_CONFLICT：不杀进程"]
    D -->|身份不明 / 不确定提交| R["RECONCILING：核对事实，禁止盲启"]
    G --> H["绑定 PID + birth + Job Object + 端口 + 节点/配方"]
    H --> I{"证据完整"}
    I -->|是| E
    I -->|否| R
~~~

- ComfyUI 慢启动采用有界探测与启动中状态，不把模型加载期当成失败。
- 不能仅凭 /system_stats 返回 200 就宣称是受管实例；必须比对启动意图、监听 PID、进程身份与适配器合同。
- 已运行的图片渲染器不能被 H3 直接抢占；由当前任务按原流程完成释放，另一个进入等待。需要留在同一安装上的受控重启，必须通过已持有控制权的 Worker 内部机制执行。
- H3 封闭队列/handoff 的 drain/resume/模型物理释放流程不允许被普通“确保服务在线”操作打断。

### 4.3 停止/释放

- Web 关闭页面：**不是停 ComfyUI 指令**。
- 用户显式点“停止制作引擎”：仅对已证明受管、无在途提交或正在进行中的真实工作、无未知结果的实例执行安全停止。
- 应用完全关闭：按原 release_managed_processes 执行所有权证明与活跃任务保护；对不确定状态保留 BLOCKED_UNVERIFIED 并记录原因，不做 PID-only kill。
- 旧版本安装/新机升级在操作前检测 RuntimeSnapshot；运行任务、未核对提交、启动过渡期禁止热替换程序/插件/工作流，给出安全迁移步骤。

## 5. API、命令执行与后向兼容

建议新增 backend/shine/runtime_orchestrator/ 下少量独立 Python 文件：identity.py、control.py、status.py、recovery.py、contracts.py（按实际简化，避免重复包装）；现有 ComfyOps 保留为底层执行器。

### 5.1 对外 API 草案

| 方法 | 路径（/api/v1 前缀） | 功能 | 属性 |
|---|---|---|---|
| GET | /runtime-orchestrator/status | 统一快照和人类可读状态 | 只读；不触发启动 |
| POST | /runtime-orchestrator/ensure | 请求指定受管安装就绪；返回既有或新建控制操作 ID | 幂等；异步受理 |
| POST | /runtime-orchestrator/stop | 安全停机意图；有任务返回拒绝原因 | 显式操作；不盲杀 |
| POST | /runtime-orchestrator/probe | 无副作用重新检测 | 不改变进程 |
| POST | /runtime-orchestrator/reconcile | 在证据允许时核对不确定状态 | 不允许自行重投已提交任务 |
| GET | /runtime-orchestrator/operations/{operation_id} | 查询本次控制操作结果及诊断 | 只读 |

所有写请求需要本地操作者权限、Idempotency-Key、installation_id、expected_state_version（或等价 CAS 版本），返回 202 + operation_id / 已存在 operation_id；无效版本 409 且携带最新快照。禁止新增暴露到公网的任意启动/杀进程接口，沿用现有 loopback 限制与权限模型。

### 5.2 最小持久化合同

只新增**一个扁平持久化的控制操作表**（若现有 AppSetting + 审计足以满足唯一键、并发 CAS、安全索引，可直接复用现有结构；本地 Agent 以可验证的最小方案决定），存储：

operation_id、installation_id、action、idempotency_key、expected_version、status、owner_worker_id、owner_birth、started_at、updated_at、failure_code、evidence_ref、result_digest。

避免大体积嵌套 JSON、每个探活都写日志、重复保存进程真相；进程/租约/任务权威记录继续来自现有表和 OS。真实提交使用现有 JobAttempt / LibraryTaskAttempt 与 COMFY_SUBMIT_INTENT 等证据，不新增一份提交账本。

同一 installation_id 下可执行的生命周期操作必须原子串行化；相同 idempotency_key + 同一操作语义始终返回相同 operation_id；相同 key 不同参数明确 409；控制执行者重启时按证据恢复操作，不自动重放无法判断副作用的 START/STOP。

### 5.3 旧 API 收束矩阵

- api_material.py 的 image-runtime/start|stop：保留旧路由响应兼容，通过 orchestrator 请求执行并返回 operation_id/状态；不得再直接从 HTTP 请求线程调用 start_image_renderer 或 stop_image_renderer。
- api_setup.py 的 managed-processes/release：改为受控 release 申请或只读预览，复用原证据核对与任务忙判定；不能绕过操作互斥锁。
- api_instances.py 的 CREATE/START/STOP/ROLLBACK：安装计划与授权校验保留；真实运行生命周期与受管安装的变更冲突由统一锁拦截。**不把安装事务转成普通用户运行按钮**。
- Worker/H3 handoff_startup.py：保留内部函数，新增安装级执行边界和一次性控制令牌，避免其与图片渲染操作并行；禁止不经测试就更改 H3 的固定配方或交接证明。
- worker.py/asset_center/execution.py：任务执行沿用原调度表，使用统一 resolver 获得受管实例身份与运行就绪证明；保留原有不能确定提交结果时的分支。
- api_comfy.py /comfy/test /capabilities 与 system/status：提供只读状态与诊断汇总，不把连接探测“没响应”当作启动命令；兼容旧字段。
- scripts/release_residual_backends.py：保留 OS 身份校验，只在统一控制关键区内运行。

实施时先做调用图与 import/endpoint 搜索，列出所有启动、停止、free_models、interrupt、POST /prompt、端口探活调用点，再逐项决定迁移；对纯只读 HTTP 检测和业务提交不必机械地强制改写成调度层代理。

## 6. 健康检查、自动重连与无阻滞恢复

### 6.1 三层检查

- L1 进程层：processes.json、OS PID+birth、Job Object、监听 PID、Worker 心跳。
- L2 传输层：ComfyUI HTTP 的 system_stats、queue/history 等有界只读检测，不经过系统代理访问本地 127.0.0.1。
- L3 业务能力层：所选固定配方、节点清单、adapter 身份、模型/工作流合同、GPU 准入；真实制作可用必须从权威证据派生。

短时连接失败先进入 DEGRADED；采用有界重试和退避，例如忙时 3–5 秒观察、空闲 10–20 秒观察，并防抖（参数以实测调整）。禁止 GET /status 偷偷触发启动、加载模型、下载节点或写大量 DB 事务。健康检查绝不能与真实生成争抢长期 SQLite 写锁。

### 6.2 断点恢复矩阵

| 故障 | 处理动作 | 禁止动作 |
|---|---|---|
| Web 刷新/标签关闭 | 从后端快照重建 UI | 重启 ComfyUI、重复提交 |
| HTTP 连接抖动但受管 PID 仍在 | DEGRADED → 重新探活 → READY/BUSY | 盲目 Popen |
| H3/图片模型加载长耗时 | 保留 STARTING、进度及有界超时 | 当作“已死”反复 START |
| Worker 进程退出 | 新 Worker 先重建操作所有权和提交证据 | 重新执行整个镜头 |
| /prompt 请求超时或 5xx | 查原提交意图、prompt_id、queue/history 和运行实据 | 未核对即自动 POST /prompt |
| OS PID 被复用 | 以 birth/Job Object 拒绝误操作，保留证据 | 按 PID 杀系统/其他用户进程 |
| 端口有非受管 ComfyUI | EXTERNAL_CONFLICT；展示冲突信息 | 抢端口或终止外部进程 |
| 租约到期、操作状态不明 | 使用现有 RECONCILE_REQUIRED 与审计核对 | 仅凭 TTL 释放 GPU/提交 |
| 在途图片与 H3 竞争 | 保持原串行资源准入和等待提示 | 同时拉起两份同安装进程 |
| 启动中突然断电 | 恢复前先核对 OS、账本、队列与历史 | 用过期启动意图重复拉起 |

自动修复仅限**可证实安全**的动作。不可核实的情况需要阻断该安装或冲突 GPU 资源，不应把所有文字创作、媒体浏览、项目管理模块一起锁死。

## 7. 前端交互收束

- Web 顶部或制作界面统一“本地制作引擎”状态：未启动 / 准备中 / 已就绪 / 制作中 / 连接不稳定 / 需要处理。
- 展示当前任务类别、等待队列数（从业务表与资源账本汇总）、当前启动操作、推荐动作；不要暴露不必要的 Job Object、lease_id、原始 JSON、运行指纹。
- 用户可操作项：检查连接、安全启动/恢复、查看故障详情；只有满足安全条件才显示停止按钮，危险动作明确提示原因。
- 多页面对同一个状态 API 读取；优先轮询聚合快照并去重，若未来增加 SSE 应是可选的传输优化，不再创造第二状态源。
- 错误提示映射：已被其他任务占用、正在启动、连接暂不可用、外部实例冲突、进程身份待核查、已提交任务待恢复，避免笼统“ComfyUI 未连接”。
- 保留现有简洁用户 UI 与专业详情区；不新增“大型工程控制面板”。

## 8. 分阶段开发任务

### P0：真实调用点审计、失败基线与合同冻结

- 从最新 main 对比本分支 HEAD；建立只读调用关系清单：启动入口、H3/图片/未来放大生命周期、显存租约、任务提交、安装变更、残留释放及测试。
- 识别冲突分支/PR（尤其素材中心）；声明**本轮归属：仅生命周期和状态治理**，不要顺手重写资产/导演模块。
- 编写兼容性测试与失败复现场景；记录尚未验证的假设，禁止写“已修复”。
- 验收：输出调用路径表、风险点表、冒烟基线，文档与实际代码一致。

### P1：Stack 启动唯一性，先消灭重复 Web/API/Worker

- 对 start_shine.ps1 建立真正跨进程 Mutex；start_dev.ps1 的检查/清理步骤并入受保护路径，消除并发归档/端口选择竞争。
- 校验 Worker + API + Web 全部真实进程身份与就绪；加失败后的进程清单恢复/安全回滚；绝不凭端口或普通 GET 误认自己的实例。
- 验收：同一目录并发启动 10 次只形成一套完整栈；连续运行 20 次打开/关闭成功；故意占用 8000/5173、残留 PID、身份不可读、半死栈均能报告事实且不误杀。

### P2：单一受管 ComfyUI 控制权

- 增加 installation_identity、RendererLifecycleGuard、最小操作记录与控制门面。
- 让图片 start/stop API、ensure_image_renderer、H3 Worker 内部启动、受管 release 路径共同服从**同一物理安装控制边界**。
- 保留 H3 原有 per-attempt identity、受管开关队列、handoff、模型物理释放、StartupHeartbeat 等保护；不要把它们简化成普通的“永远在线”守护进程。
- 对只读能力探测与业务提交仅收敛配置与安全校验，不必额外包一层网络代理。
- 验收：同时申请图片与 H3、双页面点击、控制器/Worker 崩溃恢复、同端口外部实例竞争均无重复受管进程或未经证据释放；无新 GPU 队列。

### P3：统一只读快照与智能恢复

- RuntimeSnapshot 聚合进程/传输/能力/租约/任务/操作证据；保持老 API 字段兼容，提供新 API/前端状态条。
- 实施可证明安全的断连恢复、探活退避、防抖；核对未知外部提交时沿用原队列/历史与审计，不自动重投。
- 区分引擎故障与业务任务阻塞；在素材中心/导演界面展示明确状态和恢复入口。
- 验收：刷新 Web 不改变后端进程；HTTP 短时断连后不重复启动；未知提交不出现第二条 /prompt；恢复结果明确。

### P4：端到端 Windows 本机验收与回滚

- 使用 RTX 5080 / 16GB VRAM 基线，选用**真实**固定 ComfyUI 配方和实际已安装模型；未安装模型绝不能凭 Mock 代替生产 PASS。
- 覆盖一次 H3 Ref2VA 真实产出、图片文生图（及现有已可用的图编/局修）、图片→H3 交接、GPU 释放、任务续跑、用户重复点击、应用关闭再启动。
- 超分/二采功能如在当前分支尚未合并，则只验证调度接入点和接口预留，不虚构其已经具备真实生产能力。
- 形成每阶段测试证据、已知缺陷、未覆盖项、最终真实用户端到端回执；回滚过程需证明旧项目/媒体/模型和配置未损坏。

## 9. 测试与验收矩阵（最低要求）

| 编号 | 场景 | PASS 标准 |
|---|---|---|
| ORC-01 | 10 路并发双击启动 | 同一 data_dir 只有 1 套健康 API/Worker/Web；所有调用获得同一端口 |
| ORC-02 | 20 次启停及再启动 | 无误杀、无错误残留、无重复 Worker |
| ORC-03 | 被占端口 / 外部 ComfyUI | 不杀外部进程、不误接管，返回冲突原因 |
| ORC-04 | 模拟 PID 回收 | birth 与 Job Object 不匹配绝不终止该 PID |
| ORC-05 | H3 与素材中心同时请求同一安装 | 不出现并存的同安装受管渲染器；按原租约串行 |
| ORC-06 | H3 handoff 排空、质检、恢复 | 原证据链 PASS，不允许控制层破坏队列封锁 |
| ORC-07 | /prompt 发送后超时 | 无法核实的结果不得重投；可从 history 对账 |
| ORC-08 | 浏览器刷新/断连 | 正在运行的真实任务与进程不被重启 |
| ORC-09 | Worker 被中止后恢复 | 操作、租约、任务状态与 OS 事实可重建，无盲启 |
| ORC-10 | 正在生产时手动 STOP | 返回忙碌及具体关联任务，不杀生产 |
| ORC-11 | ComfyUI 未准备好节点 | 显示能力未就绪，不谎称 READY |
| ORC-12 | 安装升级与正在运行的渲染冲突 | 安全阻止热替换，保留安装授权与媒体 |
| ORC-13 | 既有 Mock 测试与核心业务回归 | 保持无回归；Mock PASS 不代替真实 H3 PASS |
| ORC-14 | 独立素材中心在研 PR 协作 | 不覆盖其他分支开发成果；冲突显式上报 |
| ORC-15 | 故障隔离 | ComfyUI 故障时文本创作和资产浏览仍可用 |
| ORC-16 | 生产/演示模式 | 控制层不擅自改变用户明确选择的真实制作模式 |

以 0 次不明误杀、0 次重复提交、0 次同安装双受管进程作为硬门槛；无法在当前机器验证的项目明确标为 NOT_VERIFIED，不得以“理论 PASS”代替。

### 9.1 推荐测试命令（按当前目录/环境核对）

~~~powershell
cd F:\ClaudeWorkSpace\projects\shine-video-studio
.\backend\.venv\Scripts\python.exe -m pytest backend/tests/test_process_probe.py backend/tests/test_owned_process_group.py backend/tests/test_managed_startup.py backend/tests/test_renderer_supervisor.py backend/tests/test_comfy_recovery.py backend/tests/test_comfy_handoff_barrier.py -q
.\backend\.venv\Scripts\python.exe -m pytest backend/tests -q
.\backend\.venv\Scripts\python.exe -m ruff check backend/shine backend/tests
~~~

命令仅为参考；本地 Agent 应先确认虚拟环境、真实测试文件和可运行性。并按仓库当前三层测试机制分别执行快速单元 / 跨模块合同 / 真实 Windows 联调，不以 GitHub CI 额度为阻滞理由。

## 10. 工程兼容、安全与数据治理

- 在本分支开发，禁止修改 main；本地源码有未提交变更时先备份/报告，不覆盖。
- 每一阶段小批量提交，说明变更文件、接口、测试和可回滚方式。不得修改模型权重、自动下载无关模型、删除媒体、清空数据库或杀掉非 Shine 所属进程。
- 全部新增控制命令必须 loopback、鉴权、校验 installation_id、限定操作枚举和带审计；严禁传入客户端任意 shell 命令、进程 PID 作为无条件 kill 对象、任意远端 URL。
- SQLite 事务不得跨越 Popen、HTTP 长轮询、模型加载和大型文件 IO；使用短事务 CAS 与 OS 锁，优先复用原审计和资源 epoch 证明。
- 不以 RuntimeSnapshot 的状态字符串取代任何可追溯的进程/租约/提交/工作流证据。
- 新功能开关建议 SHINE_RUNTIME_ORCHESTRATOR_ENABLED：0=完整旧路径、1=统一路径，**不能**在同一运行中同时允许旧入口与新入口直接操作同一受管进程。正式收尾后可删除旧分叉。
- 新机安装/升级需兼容旧 processes.json/安装记录，迁移按缺失字段安全降级；不要让旧安装必需重装整套 ComfyUI。
- 真实产出仍须经过原技术 QC；没有真实媒体和 H3 节点证明不得宣称生产可用。
- GPU 显存值必须依据实际探测与现有固定合同判断；不因为“16GB”就允许多模型并行。
- 运行日志留在本地可读目录，状态、错误码和操作 ID 用于排障；不要将 token、密钥、原始私人素材路径上报远端。

## 11. 交付物与阶段性报告格式

预期实现最少包含：

1. 本 MD 对应的审计补充或独立 docs/ 进度文件（阶段、基线、已修改文件、证据、风险）。
2. 控制入口/安装身份/操作序列化/只读快照/恢复逻辑与必要最小迁移。
3. 改造后的启动脚本、图片及 H3 兼容接入、旧 API 代理。
4. 精简的前端统一状态入口及故障说明。
5. 定向并发和异常测试、既有回归测试、Windows 实机端到端验收报告。
6. 每阶段可回退提交和所有未完成项表；不允许把“未复现/没跑”标成 PASS。

每阶段报告：
- HEAD、上游基线和当前本地修改；
- 本阶段修正的具体旧入口与映射；
- 成功与失败测试原始数量、日志/证据路径；
- 已验证/尚未验证的风险；
- 下一阶段前置条件和必要的最小变更。

## 12. 明确的实施顺序和停止条件

先 P0 审计 → P1 启动器互斥 → P2 控制权收束 → P3 状态/恢复/UI → P4 真机验收，不要一口气大规模重写 Worker。

以下任一情况必须先保留证据并停止危险动作（但可以继续安全的只读诊断/文档/测试）：
- 无法证明目标 PID 属于 Shine 安装；
- ComfyUI 已有外部提交且结果未知；
- 另一 Worker 或控制操作持有不可回收的有效执行权；
- 旧进程与新启动器发生端口/Job Object 所有权冲突；
- 安装配方/固定节点/安装哈希发生未知变化；
- 其他并行开发分支与当前核心文件存在尚未解决的冲突。

最终验收口径：**从网页发出一次制作请求到真实 ComfyUI 执行全过程的进程唯一、控制唯一、连接可诊断、GPU 安全串行、已提交任务不重投、异常可恢复、用户 UI 简洁。**

---

## 附录 A. 本地 Agent 首轮审计的必查入口

- scripts/start_dev.ps1
- scripts/start_shine.ps1
- scripts/stop_shine.py
- scripts/release_residual_backends.py
- backend/shine/main.py
- backend/shine/api_setup.py
- backend/shine/api_instances.py
- backend/shine/api_material.py
- backend/shine/api_comfy.py
- backend/shine/production_mode.py
- backend/shine/worker.py
- backend/shine/comfy/client.py
- backend/shine/comfyops/instances.py
- backend/shine/comfyops/owned_process.py
- backend/shine/comfyops/renderer_supervisor.py
- backend/shine/comfyops/renderer_release.py
- backend/shine/comfyops/runtime_configuration.py
- backend/shine/comfyops/handoff_startup.py
- backend/shine/comfyops/handoff_idle.py
- backend/shine/comfyops/handoff_activation.py
- backend/shine/comfyops/image_renderer.py
- backend/shine/comfyops/scheduler.py
- backend/shine/comfyops/resource_epoch.py
- backend/shine/asset_center/execution.py
- backend/shine/materialprep/image_runtime.py
- frontend/src/api.ts
- frontend/src/setupExecution.ts
- 相关 tests/、packaging/windows/ 与 Windows launcher 测试。

附录注意：API 路由与文件列表以实施时真实代码为准；若上游已有更近提交或素材中心/新机安装等 PR 合入，应先对比差异、修正文档实施映射，**不盲目覆盖**。
