# Shine 视频工作台 R5｜基础配置与用户机资源复用架构终局收束实施方案

> 状态：实施方案，非已完成验收。制定日期：2026-11-18。
> 仓库：anjingdtl/shine-video-studio；远端基线：main@46c889a0739beefaf81123ec726e1cc72e65d22e。
> 方案分支：docs/shine-setup-r5-three-principles-convergence-20261118。
> 适用：Windows 本地 Shine Web 工作台；本地 ComfyUI、MiniMax-H3 Ref2VA、Qwen Image、RelayVSR、工作流、节点、模型资源与启动器的资源互认。
> 决策：只做现有能力的归并、修复与防回归；禁止新建平行检测中心、第二套模型台账、第二套安装队列、第二套生产门禁或第二个 ComfyUI 调度器。

## 0. 目标与不可变原则

目标：用户电脑（包括第三方 ComfyUI 整合包、不同分区、外置权重、多个 ComfyUI、RTX 4070 等）一旦确认安装来源，Shine 应在现有权限与安全边界内自动发现、核验、登记、绑定、复用，保持可解释且稳定的生产资格。以后正常切页、重启、升级、搬迁 Shine 数据目录、创建作品不得无故再次要求下载几十 GB 权重或重新配置；只有真实资源变化、证据失效或具体能力不满足时，针对差异精准修复。

必须执行 AGENTS.md 的最高原则：

1. **一套事实**：每项机器资源只有一份权威持久事实和一份同源读投影；扫描候选、核验回执、实例绑定、运行验证、真实出片证据按证明强度区分，不相互冒充。
2. **一个裁决**：只有服务端既有权威裁决入口得出“对本次操作/选定变体是否可执行”的结论；UI、Agent、启动器不复制判断。
3. **一个执行者**：机器整备和修复由已有持久 setup_preparation Worker 步骤链编排，委派既有 reconcile/adoption/deployment/installer/renderer supervisor 执行各自产权操作。任何入口不得增设竞争写链。

**三条禁止**：不得以新增检查解决检查过多，不得新增状态同步旧状态，不得新增操作入口绕过旧入口。改造优先修改/删除已有实现；仅当证明旧数据合同无法表达必要的稳定身份，才允许最小、带迁移与兼容的补充。

**核心安全约束**：真实视频只能经本地 MiniMax-H3 Ref2VA / ComfyUI；禁止将 Mock 验证冒充真实生成；禁止跳过模型 SHA-256、钉住版本和许可；候选量化版本不得借默认基线提升为 SUPPORTED；硬件不足与文件缺失分开判定；外部 ComfyUI 默认只读，绝不未经授权写入/改包/杀进程；工作流和 H3 生产仍受真实节点、实例归属、租约、参数、媒体槽、风格冻结与质检等既有约束。RelayVSR 经用户已授权的固定权重加性落位仅允许在原有受控路径上实施，不得隐式扩大授权范围或覆盖已有文件。

## 1. 本次代码与历史问题的基线（先读后改）

### 1.1 已有权威入口，禁止重复建设

| 职责 | 当前主仓入口 | R5 应做/不做 |
| --- | --- | --- |
| 已有 ComfyUI 检索、extra_model_paths、节点盘点 | backend/shine/comfyops/existing_installation.py | 统一真实模型可见目录与 Windows 路径语义；不另写扫描器 |
| 机器资源有界深搜和候选索引 | backend/shine/comfyops/resource_discovery.py | 保持扫描“候选≠核验”；截断/不可访问不可推断缺失 |
| 选择已有安装、资源/能力快照 | backend/shine/comfyops/setup_summary.py 的 resolve_installation、summary | 统一只读事实；删除重复状态推演 |
| 资源对账、原位核验、登记、已有安装采纳 | backend/shine/comfyops/setup_reconcile.py，adoption.py，verification_receipts.py | 自动接续、幂等、可恢复、无重复下载 |
| 整备任务总执行 | backend/shine/comfyops/setup_preparation.py | 复用唯一持久任务及 Worker tick；拒绝并行新队列 |
| 实例与模型安装操作 | backend/shine/comfyops/instances.py，installation.py，installer.py，deployment.py | 保留唯一执行与批准/租约/锁 |
| H3 变体与权重资格 | backend/shine/comfyops/variant_binding.py，variant_readiness.py | 保留专业证据；禁止不同版本借证据 |
| 运行配方、服务验证、机器裁决 | backend/shine/comfyops/runtime_configuration.py，service_validation.py，production_mode.py | 统一资格来源、按依赖范围失效 |
| 编译后的工作流和提交调度 | backend/shine/comfy/graph_compiler.py，runtime_orchestrator/，worker.py | 不创第二套调度；保留运行时真核验 |
| Setup/导演/素材中心/AI 管家入口 | backend/shine/api_setup.py，现有 frontend 相关组件与现有 Copilot | 只读同一结果，动作回到已有执行者 |

2026-11-18 基线已有修复：resolve_installation 单一安装选择；分组 YAML/aliases/外部权重；无作品可机器采纳；H3/图片共物理配方；冷启动先启动受管渲染器再编译动态工作流；前端删除部分重复计数；含空格、中文、UNC 路径脱敏。**不得回退或重复开发这些已存在能力**。

### 1.2 历史根因与证据

- 2026-11-02：已有安装仅浅层扫描且不读真实外置模型目录，发现不等于可采纳；见 docs/EXISTING_COMFYUI_FIX_20261102.md。
- PR #18：扫描范围、端口身份、4070 内存档位与工具自举；有界检索结果不得宣称完整盘点。
- PR #23：前端综合就绪错误触发 H3 安装计划，连坐图片能力；能力/变体门禁必须按操作隔离。
- PR #24：现有 ComfyUI 已有有效文件但 bound=false，实例路径和回执割裂；已核验 40+ GiB 被规划重下。
- PR #27：统一快照与自动对账的第一轮；出图绑定配方缺失，重复登记。
- docs/ACCEPTANCE_20261116_DIRECTOR_SETUP_CONSOLIDATION.md：顶部、能力卡、拍摄页、缓存等历史不同裁决导致“已就绪但不能拍”。
- docs/ACCEPTANCE_20261117_R4_RUNTIME_IDENTITY_RELOCATION.md：配置身份与账本路径/worktree/脚本绝对路径耦合，升级/搬迁造成 READY 失效。
- docs/ACCEPTANCE_20261117_MISSING_VERDICT_SUPERVISOR_STOP.md：已核验却报缺少 6 个文件；presence、receipts、下载计划所读目录不同；supervisor 残留导致 DLL 被锁。
- docs/ACCEPTANCE_20261118_SETUP_RUNTIME_CONVERGENCE.md：开发机 UI 真实就绪、后端 8 分片全部 exit 0、前端 431 例，但**本轮未重新真实 H3 出片**；不是跨用户机器 PASS。

### 1.3 剩余重点审查嫌疑（未先验断言均为现存 BUG）

1. instances.py 的 instance_identity(mode,port) 以端口参与主键，adoption.py 按该身份收编；检验端口改变、多个安装及旧行迁移是否产生新实例/错绑定/旧证据失效。禁止仓促换主键导致历史 FK、租约和记录断裂。
2. setup_summary.py 的 candidate_installations 字符串路径比较，与 existing_installation.local_path/Windows 大小写/路径别名/UNC 的语义是否完全一致。
3. setup_summary.capability_report、variant_readiness.readiness、production_mode.machine_verdict、runtime_configuration.runtime_report 是否仍为同一操作产生不同准入；区分“用于展示的分层事实”和“唯一生产允许/拒绝”。
4. setup_preparation 任务 request 的安装选择、port、variant_id、capabilities 与 RECONCILE/DOWNLOAD/INSTANCE/TARGET/RECIPE/VALIDATE 跨步冻结是否一致；失败/取消/被替换的旧 Worker 不得回写新任务。
5. 模型 presence/verified/cache/in-place/unplaced/missing 与实际 ComfyUI 模型加载来源，尤其 YAML 相对/多行路径、models 家族别名、外置权重、不同 catalog、不同量化变体的同名文件。
6. 工作流 JSON 存在、图合同注册、真实 /object_info 节点核验、所选变体和运行配方之间的重复门；不能用文件存在代替运行验证，也不能因为未选的图未校验阻断当前图。
7. 证据缓存 invalidation 是否一致；正常升级/切页不应二次哈希，真实字节改变必须强制复核；文件时间戳/大小不变但内容被恶意替换时，保留既有安全威胁模型，不以性能为名削弱必要的 SHA/信任策略。
8. Web 初次冷态、超时、状态缓存与代理对 localhost 的影响；不能把“后端暂时未返回”写成“本机没有模型”。

## 2. R5 唯一职责边界与数据合同

### 2.1 所有权

- **事实来源**：ComfyInstance 与已确认安装选择、ModelPackage 与 verification_receipts、安装 catalog/variant manifest、WorkflowProfile/规范图、运行配置/服务验证封条、真实 Job/QC、已存在的发现索引。**不得另建 machine_inventory 表或第二份资源账本来镜像**。
- **只读投影**：setup_summary.summary 汇总上述已存在事实（GET 不哈希大文件、不启动进程、不写库）；现有下游裁决按同一份机器事实计算。不得增加另一套长期存储的 READY 状态。
- **唯一操作资格**：production_mode.machine_verdict 及其现有按操作/变体的专业判定函数必须形成唯一调用关系；先审查其当前调用面，再选择现有最小修改点归一。专业模块可以返回证据和原因，但不能独立做 Web/Agent 的最终准入。
- **唯一写链**：setup_preparation 负责冻结授权、编排顺序、持久重试及恢复，调用 setup_reconcile、adoption、受控 installer、deployment.generate_configuration、service_validation、已有 supervisor。读接口不得通过调用隐藏写操作修补数据。
- **业务下游**：Director、Assets、AI 管家、启动器仅消费统一事实与唯一裁决；允许提交“用户意图”，不得绕过部署审批和系统执行器直接补包/绑定/激活。

### 2.2 七个既有证据阶梯，不新增并行状态机

1. FOUND：有界扫描或明确目录的 stat 命中——仅候选。
2. VERIFIED：按对应 catalog 与 variant 的 size/SHA-256 校验有效——有证据。
3. REGISTERED：唯一包台账关联已核验字节——系统内部自动幂等动作。
4. BOUND：该实例当前**真实可消费**的模型目录/配置可达已核验文件——不是“文件在别处”。
5. RECIPE_READY：实例/端口/能力/配方身份一致且能够合法加载。
6. WORKFLOW_VALIDATED：本次实际使用的图所需节点与输入，在该真实服务/已封存且仍有效的证据下核对通过。
7. RENDER_VERIFIED：该变体/图对应真实非 Mock 任务出片并经过相关技术 QC；不同于前六层的“可以试镜”。

这些仅描述事实强度，继续复用现有字段/回执，不要设计第二份落库阶段。UI 只展示“文件是否齐全”“是否可运行”“下一步”；高级诊断展示细节。安装检测成功不代表生产已就绪，未做真实试镜不代表没有模型。

### 2.3 概念性的统一只读响应（示例，不要求新增 API）

~~~json
{
  "installation": {"selected_instance_id": "existing-id", "source": "adopted"},
  "capability": "h3",
  "variant_id": "selected-variant",
  "facts": {
    "FILES_FOUND": true,
    "FILES_VERIFIED": true,
    "RESOURCE_REGISTERED": true,
    "INSTANCE_BOUND": true,
    "WORKFLOW_VALIDATED": true,
    "RENDER_VERIFIED": false
  },
  "decision": {
    "state": "TRIAL_READY",
    "blocking_reasons": [],
    "warnings": ["REAL_RENDER_NOT_DONE"],
    "next_action": "TRIAL"
  }
}
~~~

上述仅为**现有接口需满足的语义**；优先扩展 /api/v1/setup/summary 的现有投影，不因示例新建字段、表、接口或状态。state 应映射到项目既有规范枚举；不要引入并行 TRIAL_READY 数据库状态。

## 3. P0：安装身份稳定与选择记录复用

### 3.1 身份必须区分四个概念

- **物理安装**：可验证的 ComfyUI 根目录及运行环境（不是端口）。
- **逻辑记录**：现有 ComfyInstance 主键及历史引用，不能未经迁移直接推倒重做。
- **服务端点**：127.0.0.1:端口与进程归属证据，可以迁移，但不改变模型字节的真实性。
- **工作台数据目录/仓库**：可搬迁账本，不是物理 ComfyUI 安装身份。

先核查 instance_identity(mode,port) 的全仓引用、主键/外键/审计/租约、迁移和历史 snapshot。优先通过现有身份解析和 alias/记录兼容实现稳定安装复用；若确需新字段或迁移，先给出真实理由、前向/回滚策略、旧记录兼容和一对多冲突裁决，严禁直接重算旧 id 丢失记录。

### 3.2 选择策略

优先：有效激活且归属可证的当前运行实例 → 用户已确认并仍有效的安装 → 已安全采纳实例 → 受管实例 → 有界发现候选。多个可信且不等价候选时，只问一次并持久化；不得因为列表排序或端口探测自动切换用户选择。

Windows 实际路径必须统一用 local_path / comfy_root 一类现有解析；路径展示/审计脱敏不应进入业务比较；大小写、空格、中文、路径分隔符使用 Windows 语义。防止 path alias 造成一个安装分裂成两条候选；对 junction/symlink/UNC 等现有安全不支持路径不得通过规范化绕过安全门。

## 4. P1：模型发现→核验→登记→绑定自动闭环

### 4.1 只读发现与有界覆盖

- 扫描优先读取用户确认安装、上次已确认目录、现有注册目录、ComfyUI 自身 extra_model_paths.yaml，以及已知资源索引；最后才使用有界磁盘扫描。
- 检索命中仅表示候选；扫描没有覆盖某个目录、超时、被截断、访问拒绝，应报告 UNKNOWN/coverage 而非直接 MISSING。
- 完整使用 YAML 分组、多行、base_path 和模型家族别名的**同一个**解析函数；presence、adoption、variant binding、素材出图和下载计划不可再各自解释一遍。

### 4.2 字节事实与核验回执

- 文件的 catalog/variant/组件/路径/size/SHA-256 与原始来源必须可追溯；候选存在不等于校验通过。
- 同一有效字节经一次核验并有可靠回执后，后续调用不得重复哈希 40+ GiB 文件；仅在必要证据失效时精准重新验证。已有 sidecar/内容寻址缓存优先复用，不再造缓存。
- 严格区别 1）served 可直接消费；2）本机已有有效字节但尚未落位；3）真实缺失/损坏。第二类应优先原位安全绑定或受控本地放置，绝不能显示“需重新下载”，下载体积为 0；不能以哈希通过冒充运行时可见。
- 不允许存在“文件已核验”但还要求用户人为登记的正常路径；在允许的原有写链内完成台账 upsert/采纳/绑定。采用不能安全完成时按真实原因停止，不能写 READY。
- 记录完整的错误码、资源来源、证据有效性、建议动作；日志中脱敏绝对路径和敏感信息，脱敏字符串绝不可回写作为业务 identity。

### 4.3 外部 ComfyUI 与本机受管 ComfyUI

- adopted external：默认只读，不创建另一套 ComfyUI，不修改原有节点/启动脚本/模型文件，不终止外部进程；必须证明实例、解释器、节点、模型目录和端口归属。
- managed：原有 INSTANCE 阶梯可在安全前提下完成半成品，而不是任何 instance 行存在就跳过。
- 运行配方：一台物理渲染器可供 H3 与 Qwen Image 复用同一份配方，能力绑定独立，不能互相覆盖权重或运行证据。
- RelayVSR 加性落位沿用既有用户裁定的受控规则，仅匹配钉住文件、明确授权、拒绝覆盖同名不同字节。
- 同一配置资源被其他任务占有时，复用现有排队/租约/对账，不建立新锁系统。

## 5. P2：唯一资格裁决与工作流实际运行合同

### 5.1 按“本次操作×能力×选定变体”裁决

不能有全局 all-capabilities-ready 硬门。必须区分：

| 操作 | 应依赖 | 不得被连坐 |
| --- | --- | --- |
| 文本/文学分镜 | 当前 LLM/文学合同 | H3/超分资源未装 |
| Qwen 出图 | image 资源与 image 运行合同 | H3 INT8 显存门槛 |
| H3 Ref2VA 试镜 | 此次选定量化变体、参考资产、对应图与真实硬件 | 未使用的其他 H3 候选 / LoRA / RelayVSR |
| H3 + 所选 LoRA | H3 基线 + 本镜 LoRA 与编译图 | 其他 LoRA |
| RelayVSR 后期 | 当前 RelayVSR 与原 H3 调度安全约束 | 与本次后期无关的审片模型 |
| 非必需 VLM/ASR | 如实报告 advisory | 不得硬阻断主拍摄 |

不能为了“页面好看”放宽真实生产门禁、GPU/内存准入、候选显式确认和 SHA 校验。UNKNOWN 不等于 PASS，也不等于“文件未下载”。

### 5.2 工作流真实契约

- 用户选择的是 H3 量化版本及适配的加速/超分/LoRA 等可选模块，N 图/N 音/N 视频由既有动态工作流编译器根据分镜与素材自动装配。
- 先依据实际配置编译本次图；随后让唯一服务验证链使用所选实例及真实 object_info 或仍然有效的封存机器证据验证必需节点、参数映射、模型目录与图能力；不得在渲染器冷态下把临时不可达误报成缺文件。
- 沿用机器级已验证规范模板，项目继承该证据；不能因为另一套未选中模板尚未注册或 JSON 发生无关变化，就否定当前 H3 模式。
- 文件在盘、JSON 可解析、某次基线已验证、选定工作流可跑、真实出片是五件不同事实。
- 不绕过工作流的媒体槽映射、H3 Ref2VA 唯一路径、真实节点和流程安全合同。

### 5.3 裁决消费一致

- Setup 页面、顶栏、导演包、拍摄页、素材中心、AI 管家同一事件下不许一个显示 READY、另一个显示缺 6 个文件。
- 显示层没有新的计算模型存在性、VRAM 阈值、就绪 flag 的权限；展示现有事实和后端最终裁决。
- loading/timeout/network unknown 与资源 missing 必须区分。缓存失效需由已有写链对改变的资源/实例/配方主动触发，展示缓存不能成为第二门禁。
- 只有唯一权威判定决定按钮可用与原因；所有页面读取同一版本快照/请求 identity，避免混用不同刷新时点导致“就绪但仍禁止”。

## 6. P3：证据精准失效、生命周期恢复与幂等

### 6.1 精准失效矩阵

| 事件 | 应重验/更新 | 保留有效事实 |
| --- | --- | --- |
| 关闭/启动 ComfyUI | 在线状态与服务进程归属 | 已核验权重字节、安装选择 |
| 端口变更 | 连接端点与身份绑定、旧服务连接证据 | 不变的权重哈希/真实安装 |
| Shine worktree/数据目录迁移 | 账本锚定/启动路径解析 | 仍然相同的物理资源与有效签封 |
| Shine 脚本正常升级 | 受影响运行代码合同 | 外部安装文件与 SHA 凭据 |
| H3 量化变体切换 | 新选变体所需模型/图/硬件能力 | 原变体的合法证据 |
| 新增 LoRA | 新 LoRA 字节/兼容性 | 既有 H3 模型证据 |
| 修改模型字节/文件损坏 | 对应模型内容 SHA 与下游绑定/运行证据 | 不相关模型证据 |
| ComfyUI 节点或 adapter 变更 | 相关节点/图/服务证据 | 不相关模型 SHA |
| 中途取消、崩溃、进程身份不明 | 未提交操作对账，UNKNOWN/NEEDS_REVIEW | 不确定外部提交不得盲重发 |

依赖关系通过现有配置指纹、资源回执、工作流合同 SHA 和运行封条表达，**不是创建新的全局 invalidation engine**。失效沿依赖传播，不允许一变全部清空，亦不能在证据真正失效时保留假 READY。

### 6.2 单一长任务恢复

- setup_preparation.enqueue 的 task_id、capabilities、selected root、port、variant、authorization、当前 step 必须冻结，并在 Worker 认领/运行前核对身份、状态版本、租约。
- 同请求重复点击、切页、重启和断点恢复返回同一在途任务及真实进度；不能新建同目标平行下载或安装。
- 不同请求抢占旧请求时，旧异步步骤的结果必须被 fencing 拒绝，不可复活取消任务、覆盖更新后的目标或配方。
- INSTALLED/VERIFIED/ADOPTED 的部分完成证据应被安全复用；不能因某个后续 VALIDATE 失败而重下已通过的模型。
- 未经进程身份三证不能停止外部或不明归属服务；未知外部提交优先对账，不能盲重试。

## 7. 前端与 AI 管家收束范围

### 7.1 唯一完整入口

现有“设置 → 制作环境”保留为唯一完整配置入口；导演页只展示同一摘要和导航；素材中心与导演包只读取能力是否满足当前操作。高级诊断可以显示工程事实，不得再定义一个生产资格。

用户视图保留两件事：① 文件齐不齐；② 这套方案能不能运行。一个唯一下一步；后台整备展示任务当前步骤及变更证据。多个操作按钮、重复进度条、硬编码“本机没有权重”和重复式登记按钮应删除或合并。

### 7.2 AI 管家

AI 管家只能读取已有 /setup/summary、实际任务状态、错误码和安全诊断，解释当前欠缺什么；修复意图仍提交原有受控整备入口，由用户授权。禁止 AI 管家直接扫盘、重新造登记/资格结论、执行未受控安装命令、修改用户外部 ComfyUI。不得把 AI Agent 本身作为基础配置的必需依赖。

### 7.3 启动器

独立 Launcher 负责应用版本、Windows 本地依赖、正式工作栈的启动/停止；它不拥有模型与生产资格第二份台账。启动后 Web 读取已持久化的机器事实。依赖自举仍需符合 PowerShell 5.1/7 与中文编码规定；完整停止必须正确处理 supervisor、DLL 占用和进程归属。

## 8. 不可回退架构不变量（必须变成测试）

| ID | 必须永久成立的行为 |
| --- | --- |
| INV-01 | 已核验且运行时原位可消费的模型不得因台账未登记而重下 |
| INV-02 | 已核验在盘但未落位的资源不得被当作需要网络重新下载 |
| INV-03 | 端口变化不清空物理模型哈希证据；无证据不得认领另一个进程 |
| INV-04 | 真实多个 ComfyUI 候选必须显式选定一次，之后不无故漂移 |
| INV-05 | 用户切页、重启、创建作品不会重复采纳/登记/哈希 |
| INV-06 | H3、image、RelayVSR、可选审片等互不越权连坐 |
| INV-07 | 同一快照的 Setup/导演/素材中心/顶栏/AI 管家准入结论一致 |
| INV-08 | 编译出的本次图真实缺节点时准确拒绝，未选图不能阻断 |
| INV-09 | 资源或配方改变仅让受影响证据失效，绝不滥用整体失效 |
| INV-10 | 旧下载/安装/取消/中断 Worker 不能覆盖新任务或盲重试 |
| INV-11 | 外部 ComfyUI 只读，既有授权范围外不写、不删、不杀 |
| INV-12 | 新候选变体不能借默认 INT8 权重、图或已有真实出片证据过门 |
| INV-13 | 路径含中文/空格/大小写差异时，实际相同安装只对应一个有效身份 |
| INV-14 | 扫描截断/不可访问只得 UNKNOWN，不能断言“本机没有” |
| INV-15 | Mock/离线断言不能标记真实生产、真实试镜或用户机 PASS |
| INV-16 | 新增功能只能复用既有事实/裁决/执行者，不得新增隐蔽平行入口 |

每一项至少有一个正例和一个负例；必要时加性质化/模型化场景测试。禁止为了 PASS 将断言改弱、移除 409 安全门、跳过失败用例或全局 mock 成功。

## 9. 开发实施顺序与交付物

### R5-0：全仓只读审计（不改代码）

1. 执行 git fetch；记录 origin/main HEAD、工作树和本机历史差异；读本方案、AGENTS.md 和上一轮验收。
2. 追踪真实入口：GET setup summary、POST reconcile/prepare、选择安装、模型核验/实例身份、H3 与 image 可用性、运行配方、服务验证、Worker 冷启动、前端各消费者。
3. 画出权威事实 → 裁决 → 执行者的调用图；列出重复写链、重复门禁、重复路径解析及不稳定身份，每点给当前文件/函数/证据/删除或复用策略。
4. 对每项历史报障写能在当前主线运行的红灯反例，区分已修复 PASS 与仍未解决的 FAIL；禁止凭历史报告断言当前代码必然有 BUG。
5. 形成 docs/SHINE_SETUP_R5_BASELINE_AUDIT.md（后续开发分支内），列出确切改动面、证据和风险。

### R5-1：事实/身份/回执收束

先解决实例身份与端口/路径解耦、已有安装选择持久性、模型目录同源解析、SHA 回执缓存与配置身份精准失效；用最小可回滚改动修复剩余分裂，不推翻 11-18 的成果。对历史数据做非破坏迁移及幂等复测。

### R5-2：唯一执行链与能力裁决

统一 reconcile → 登记 → 采纳/绑定 → RECIPE → VALIDATE 的现有 Worker 编排；去掉前端或专业模块里的第二套“缺件、资格、恢复”分支。确保 H3/出图独立能力共用物理配方但不共用错误资格；动态工作流按本次实际图检查。

### R5-3：UI/Agent/启动器接入与跨机器验收

让所有入口只读同一 summary/verdict、只提交执行意图；新增架构防回归测试。完整回归、隔离新机/已有安装场景、实际 Web 启停、独立 Windows 用户机 L3 测试，真实出片与人工验收分级报告。未完成部分保留 PENDING，不伪装 PASS。

### 必交文件（新增文档控制最小集）

- docs/SHINE_SETUP_R5_BASELINE_AUDIT.md：现状/调用图/真实重复链路/红灯证据。
- docs/SHINE_SETUP_R5_IMPLEMENTATION_REPORT.md：变更文件、旧链删除清单、身份兼容/迁移、证据精准失效与回滚。
- docs/SHINE_SETUP_R5_ACCEPTANCE_REPORT.md：INV-01~16，8 分片/前端/Windows/用户机/真实 H3 的逐项结果与证据路径。
- 不增加多份近似方案；在代码已有测试模块增测，确需独立测试文件才新增。

## 10. 测试与验收矩阵

### 10.1 机器/安装组合

| 场景 | 检测 | 注册/复用 | 试镜资格 | 期望 |
| --- | --- | --- | --- | --- |
| 无 ComfyUI 的 Windows 新机 | 有界识别缺失 | 走已有受控自动整备 | 硬件与实际图判定 | 无重复入口，允许中断恢复 |
| 已有官方便携版/第三方整合包 | 命中或用户指定 | 原位只读采纳 | 不重下同字节 | 可操作且可解释 |
| extra_model_paths 指向外置分区 | 识别真实目录 | 哈希/绑定 | 读同一目录 | 不误报缺件 |
| 中文+空格/大小写路径 | 同一物理安装 | 同一证据 | 重启不变 | 不二次哈希 |
| 多实例/8188 占用/自定义端口 | 识别进程归属 | 显式选择 | 不错绑 | 不杀外部进程 |
| 已有权重、RTX 4070 低显存 | 确认已存在 | 不二次下载 | INT8/候选分别判硬件 | 不把算力门禁伪装成缺件 |
| 原模型被移走或内容损坏 | 发现变化 | 精准重验 | 依赖失效 | 不沿用伪凭据 |
| 新 LoRA/未选 RelayVSR | 按所选依赖 | 不影响 H3 基线 | 只检查实际图 | 无跨能力连坐 |
| 重启、升级、搬迁数据目录 | 复用身份 | 复用证据 | 必要时修正启动路径 | 不无故退回初始配置 |
| 断网、扫描截断、未知进程 | 不虚构 MISSING | 保护已有内容 | UNKNOWN/需处理 | 不启动高风险写操作 |

### 10.2 自动化回归

- **专项**：test_setup_machine_reuse.py、test_existing_installation.py、test_existing_comfy_adoption.py、test_setup_chain_convergence.py、test_setup_missing_verdict.py、test_runtime_identity_relocation.py、test_variant_binding_and_gate.py、test_resource_resolver_v2.py、test_setup_cold_start.py、test_runtime_activation.py、test_service_validation.py、test_runtime_orchestrator.py，以及前端 setup/导演/素材中心相关测试。以仓库实际存在文件为准。
- **Windows 链路**：从仓库外以正式启动入口启动；含中文空格路径、端口占用、多实例/半栈/外部 venv、停止与 supervisor 完整归属清理；不能破坏开发机已有 ComfyUI 与模型。
- **完整后端**：遵守 AGENTS.md 的固定后端八分片按文件并行，Windows 命令：.\run_backend_shards.ps1 -WorkRoot <非系统盘隔离目录>；环境 CODEBUDDY_SAFE_DELETE_ENABLED=0；8/8 exit=0、每个日志 0 FAILED/ERROR 才是 PASS。禁止用单进程串行全量替代。
- **前端**：npx vitest run --no-file-parallelism；TypeScript、Vite build、Ruff、必要时 Alembic check、依赖 lock --dry-run。以现有 package.json 命令为准。
- **端到端**：有资源已有安装全程无需下载；无资源真实缺口只下载需要的文件；机器级准备不必创建作品；冻结方案后执行层要消费相同事实；进程断开/重启后恢复。
- **真实出片**：只有真实 MiniMax-H3 生成、非 Mock 产物与技术 QC 证据才标 REAL_RENDER_PASS；未运行则 PENDING。RTX 5080 开发机不能代替 RTX 4070 用户机真实兼容性。
- **负例**：工作流缺节点、错误字节/同名假模型、候选未批准、端口外部进程、错误配置签封、旧任务晚回包、资源变化、网络不通等须保持安全拒绝。

### 10.3 明确的 PASS 分级

- L1 STATIC/UNIT PASS：类型、构建、静态、单元测试；
- L2 INTEGRATION PASS：隔离数据库、真实目录夹具、安装/绑定/状态全链；
- L3 WINDOWS PASS：真实 Windows 本机的外部 ComfyUI 复用与进程/端口验证；
- L4 REAL_RENDER PASS：当前选定 H3 变体真实出片及技术 QC；
- L5 USER_MACHINE PASS：独立用户电脑在其真实路径、模型、显卡下首次和重复启动复验。

不能用 L1/L2 代替 L3-L5；不具备 RTX 4070 用户机时标 PENDING/NOT_RUN，不能当作失败，也不能当作 PASS。R5 的跨机器长期质量目标以 L5 为准。

## 11. 变更边界、回滚和工程防护

- 基线必须是拉取时最新 origin/main；先比对本文制定时 main@46c889a 的差异并更新审计，禁止盲套旧方案。
- 开发在独立工作分支进行，不改动已有本地 main 或用户数据，不清理用户未知文件，不强制重置工作区。
- 先写红灯反例，再最小修改现有入口；每轮消除一条重复判定/重复写链，不扩大到导演叙事、文学 Agent、画风、全量重构或模型替换。
- 旧台账、已核验模型、用户配置、作品、密钥不可清空；必要迁移需 forward/rollback、并发一致性及升级复测，不能以重装或删 SQLite 作为修复。
- 修改 worker.py / 绑定审片 revision 等关键文件时，遵循 AGENTS.md 现有真实 VLM/ASR 校准与身份复验义务；证据不足保持 PENDING。
- 所有写操作仍遵守受控批准、路径保护、同机 loopback、进程身份、GPU 互斥、fencing、无盲重试；防止“自动修复”成为越权入口。
- 在 PR 中列出旧实现删改清单、INV-01~16 证据、全量日志、现实限制、剩余用户机工作及恢复路径。不得以增加检查或新状态掩盖同类问题。
- 合并目标 main；先推开发分支与创建 PR，PR 经验收后再合并。未获用户额外授权不得强制 push、重写 main 或擅自绕过分支保护。

## 12. R5 完成定义

只有同时满足以下条件，才可以宣告“基础配置架构 R5 代码收束 PASS”：

1. 同一事实、同一资格裁决、同一整备写链的全仓调用关系有审计证据；冗余链路已删除/归并；没有新增同类平行机制。
2. 已有 ComfyUI + 合法 H3 权重 + 必需节点/工作流的电脑，不因内部登记缺失、前端状态冲突、正常重启/切页/作品创建而被迫重下资源或重新注册。
3. H3/图片/RelayVSR/审片各能力的准备、资格、生产证据不互相借用/阻断；所有用户界面与 API 一致。
4. 安装身份迁移、旧回执、端口变化、路径迁移、模型变更、工作流变化、取消/并发/崩溃的正反例全部通过。
5. INV-01~16 自动测试、后端 8/8 分片、前端全量、Windows 真实启动及相应静态检查有可复核日志。
6. 真实 H3 出片和独立用户机测试如未执行，分别诚实标 L4/L5 PENDING，不得宣称全面生产/跨机器验收完成。

**R5 的设计目标是从架构上消除已知故障类别的成因并建立回归护栏，不是保证任何未知软件/硬件环境永不产生新问题。**
