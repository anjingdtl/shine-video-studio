# Shine 视频工作台新用户全旅程修复方案

- **日期**：2026-11-07
- **审计基线**：`549fb3350885abfb966b77c45a80d54589949165`（`main`）
- **状态**：代码与配置审计完成，修复方案待评审；**尚未实施本文中的代码改动**
- **范围**：空白 Windows 机器获取分发包开始，到依赖安装、LLM 配置、硬件识别、H3 模型准备、运行时验证和第一条真实测试镜头
- **验证边界**：本次未执行完整干净 Windows 安装，未下载 60.8GB 物料，未加载或替换 H3 权重，未进行新量化变体推理

> 本文中的 GB 按来源页面或目录的十进制口径引用；GiB 单独标注。现有代码行号只对应上述审计基线，实施时应以最新源码重新定位。

---

## 1. 结论先行

新用户当前遇到的不是一个单点故障，而是以下关键问题叠加：

1. **安装入口是“CMD 前置引导 + 浏览器向导”的混合流程**。浏览器打开之前，工具链和 Shine 自身依赖仍在命令行窗口中安装，没有统一的图形化进度、暂停和恢复体验。
2. **“所有依赖都不能断点续传”并不准确，但“全旅程不能可靠自动恢复”成立**。受管 ComfyUI/模型下载已有 `.part`、Range、大小和 SHA-256 校验；工具 ZIP 没有 Range，长安装又绑定在一次同步 HTTP 请求上，失败后通常还要人工对账、新建计划并重新批准。
3. **LLM 的正常 API 路径存在，但异常路径会破坏用户可见结果**。保存成功后刷新失败会被显示成“配置未完成”；模型目录检查可能返回 `ok=true`，readiness 却同时阻断；密钥更新先删旧值，失败时会损坏原本可用的配置。
4. **当前没有按硬件选择 H3 量化变体的实现**。系统固定一套 Ref2VA INT8 组合和 16GB 显存级资源基线，检测未知 GPU 甚至仍可能被推荐器判为 `SUPPORTED`。
5. **可以采用“更小权重包”方向，但不能承诺存在“小参数版 H3”或直接自动替换**。2026-11-07 对官方 `Comfy-Org/MiniMax-H3` 页面的只读核实显示，官方已列出约 16GB 的 `pruned_w6a8` DiT 和约 2.81GB 的 INT8 视频 VAE；但页面未给出其显存门槛、现有 PDD/LoRA 兼容性或 Shine 工作流可用性证明。它们只能先作为独立候选变体进入审计和真机验收。

### 修复总原则

- 不重写现有 FastAPI + React + SQLite + Worker + ComfyOps 主架构。
- 把安装从“一次长 HTTP 请求”改为“可重新附着的持久任务”。
- 把续传从“部分模型文件支持”提升为“工具、依赖包、源码、模型统一的下载合同”。
- 把 LLM 的“写入结果、状态刷新、连接检查、生成检查”拆成明确且一致的契约。
- 把硬件结果区分为 `SUPPORTED / BLOCKED / UNKNOWN`，未知不得当成支持。
- 每个 H3 量化变体拥有独立文件身份、工作流、兼容证据和验收状态，不继承其他变体的结论。
- 网络下载可在确定阶段自动续传；文件交换、命令执行等结果不确定时仍必须对账，不能盲目重放。

---

## 2. 新用户全旅程现状与问题清单

| 阶段 | 当前实现 | 主要问题 | 优先级 |
| --- | --- | --- | --- |
| 1. 获取并解压分发包 | 构建时生成 ZIP SHA-256 和逐文件清单 | 用户侧安装入口不验证清单，损坏包可能到执行脚本时才暴露 | P1 |
| 2. 双击首次安装 | CMD 启动 PowerShell，再进入 Web 向导 | 首段仍是命令行体验；无统一 GUI、暂停、取消和实时进度 | P1 |
| 3. 安装 uv/Node/Pwsh/FFmpeg | 固定版本、大小和 SHA-256 | 无 `.part`/Range；直接解压到最终目录，中断后可能要求人工整理 | P0 |
| 4. 安装 Shine 依赖 | `uv sync --locked`、`npm ci` | 应用层没有统一预取和续传；半成品目录可能被误判为安装完成 | P0 |
| 5. 启动 API/Worker/Web | 启动器有进程记录与健康检查 | 重复双击时只检查 API 存活，API 活而 Web 死会被误判为完整运行 | P1 |
| 6. 检测硬件 | NVIDIA 总显存、RAM 总量、磁盘、工具、端口 | 无空闲显存、commit、CUDA 算力；只取第一张 GPU；未知 GPU 可被推荐为支持 | P0 |
| 7. 保存和测试 LLM | Provider Profile、DPAPI/0600 密钥存储、目录探测、角色路由 | 保存/刷新状态混淆；测试与 readiness 矛盾；密钥更新非原子；测试不含生成协议 | P0 |
| 8. 选择并下载 H3 | 固定 Ref2VA INT8 目录，受管下载器支持分段续传 | 无硬件变体选择；长请求超时；重试和镜像覆盖不全；重启后需人工重新计划 | P0 |
| 9. 注册和验证工作流 | 注册固定 Profile，使用 live `object_info` 验证节点和输入 | 不加载权重，不证明模型可运行；目录中超分 Profile 的权重/节点证据未闭合 | P0 |
| 10. 首次真实生成 | 现有生产门禁、真实任务和 QC 链可复用 | 目录/连接 READY 与真实生成 READY 容易混淆；新变体没有真实小镜头准入 | P1 |
| 11. 断网、刷新、重启后恢复 | 有缓存、journal、审批和不确定状态保护 | 浏览器不能重新附着原安装；自动恢复与人工对账边界没有按阶段细分 | P0 |

---

## 3. 已定位的根因与代码证据

### 3.1 安装、续传与启动

#### I-01：工具链下载没有字节续传，解压不是原子安装

- `scripts/bootstrap.ps1:92-112`：curl 直接输出到目标 ZIP，没有 `-C -` 或等价 Range；无 curl 时退化为单次 `Invoke-WebRequest`。
- `scripts/bootstrap.ps1:127-155`：直接创建最终工具目录并解压。中断后再次运行只识别“目录存在但不完整”，要求人工 reconcile。
- **结果**：下载中断会重来；解压中断可能让后续双击持续失败。

#### I-02：依赖目录存在被误当成安装成功

- `scripts/install_once.ps1:317-342`：只检查 Pwsh、后端 Python 和 `frontend/node_modules` 是否存在。
- `scripts/start_shine.ps1:194-233`：同样主要按依赖目录是否存在决定是否补装。
- **结果**：`uv sync` 或 `npm ci` 中途终止后，第二次双击可能跳过修复。

#### I-03：几十 GB 安装被放在一个同步请求中

- `backend/shine/api_preparation.py:352-379`：执行接口等待完整安装结束。
- `backend/shine/comfyops/local.py:162-256`：逐包同步安装，异常后进入 `NEEDS_REVIEW / FAILED_RECONCILE_REQUIRED`。
- `frontend/src/setupExecution.ts:33-49`：浏览器最多等待 3,600,000ms，随后把结果标为 `EXECUTION_UNCONFIRMED`。
- **结果**：慢速但正常的下载也会被前端放弃；刷新或重开页面不能重新订阅原 attempt。

#### I-04：已有分段续传，但重试和镜像策略不完整

- `backend/shine/comfyops/installer.py:279-315`：已有 `.part`、缓存、SHA-256 和原子改名，应保留。
- `installer.py:376-415`：大文件按 64MiB Range 分段，严格检查 Content-Range，应保留。
- `installer.py:211-238`：建连只重试 `ConnectError/ConnectTimeout`；响应头阶段部分超时/协议错误不在同一重试策略内。
- `installer.py:386-410`：429、500、502、503 等直接成为 HTTP 拒绝，没有 Retry-After 和退避。
- `installer.py:317-361`：主要在建连失败时切镜像，传输中断不切；镜像重定向白名单还需覆盖真实的合规回跳链。
- **结果**：有 Range 不等于在真实弱网下能稳定完成。

#### I-05：预检没有覆盖首启的全部真实来源

- 当前连通性预检未完整覆盖 Node、npm registry、锁文件实际 Python registry、真实 HF 权重 CDN、PyTorch R2 等。
- Web 预检发生之前，工具和 `uv/npm` 已经需要联网，因此不能防止前半段失败。
- **修复要求**：预检必须基于实际计划生成的 URL/重定向/Range 合同，而不是只检查几个首页或 API 页面。

#### I-06：重复启动和包完整性检查仍有缺口

- `scripts/install_once.ps1:207-218`：只检查 API `/health`，没有同时验证 Web 和监听进程身份。
- `scripts/build_test_package.ps1` 会产生包清单，但 `packaging/windows/SETUP.cmd` 不验证用户解压后的清单。

### 3.2 LLM 保存、密钥和测试

#### L-01：保存成功会被误报为失败

- `frontend/src/components/LLMSetupPanel.tsx:25-33`：mutation、重新加载和全局 `onChanged()` 在同一个 `try/catch` 中。
- 后端创建已在 `backend/shine/api_llm.py:57-75` 提交。
- **结果**：POST 已成功，后续任意 GET 或无关项目数据刷新失败，页面仍显示“LLM 配置未完成”；用户重复添加还可能得到重名 409。

#### L-02：测试 API 与 readiness 对同一证据给出相反结论

- `backend/shine/llm/openai_compatible.py:197-243`：服务可达但模型未列出时可返回 `available=true, model_listed=false`。
- `backend/shine/api_llm.py:194-210`：只按 `available` 计算 `ok`，因此可返回 `ok=true`。
- `backend/shine/comfyops/llm_readiness.py:29-41`：`model_listed=false` 又明确判为 `LLM_CONNECTION_TEST_REQUIRED`。
- **结果**：用户看到“连接成功”，准备工作台却仍阻断。

#### L-03：密钥更新会先删除旧密钥

- `backend/shine/api_llm.py:139-158`：`secrets.delete()` 先于新密钥存储和数据库提交。
- **结果**：写盘或数据库失败时，旧配置也失去密钥；数据库、文件和 capabilities 可能互相不一致。

#### L-04：模型目录解析存在误判成功

- `openai_compatible.py:199-205`：非 JSON 被转成空对象，JSON 数组可能导致未包装异常。
- `openai_compatible.py:227-235`：空模型列表会使 `model_listed=true`。
- **结果**：HTML 登录页、空目录或异常结构可能被当成可用证据。

#### L-05：当前“测试”不验证真实生成能力

- OpenAI-compatible/Ollama 只请求模型目录，不请求聊天生成。
- readiness 也明确标记 `CONNECTION_ONLY_NOT_GENERATION`。
- **结果**：模型存在但鉴权、生成路由、generation options、JSON/Schema 输出不可用时，仍可能完成连接准备。

#### L-06：前后端超时和刷新语义不一致

- `LLMSetupPanel.tsx:15-17`：前端统一 60 秒。
- 新建 Profile 默认 `timeout_s=180`，后端还存在重试预算。
- 测试返回 `ok:false` 时前端直接 throw，不再重读已经写入数据库的失败状态。
- **结果**：页面超时、服务端继续、旧 readiness 残留。

#### L-07：全部文本角色绑定不是原子操作

- `LLMSetupPanel.tsx:57-65`：顺序发起 12 次独立 PUT。
- `backend/shine/api_llm.py:226-248`：每次单独 commit。
- **结果**：中途失败留下半套路由，且用户看不到明确的部分完成清单。

#### L-08：其他需要同时收口的协议边界

- 普通更新没有复用新建时的完整跨字段校验。
- 完整 `/chat/completions` URL 与目录 URL 推导规则可能冲突。
- Anthropic 探测的网络和 JSON 异常未全部归一化为 `LLMError`。
- 本机 Provider 默认继承环境代理，需对本机地址做明确绕过；不能在无用户网络证据时断言这是本次故障根因。

### 3.3 硬件识别、H3 变体与工作流

#### H-01：当前只有固定组合，没有变体路由

- `backend/shine/comfyops/recommendation.py:16-29`：`h3_video` 固定指向 `h3-ref2va-windows-x64`。
- `recommendation.py:203-215`：推荐器只评价已登记组合，不存在按显存/算力选择另一 H3 变体的逻辑。
- `config/deployment/h3-ref2va-windows-x64.json:1990-1995`：固定 Windows x64、NVIDIA、15360MiB VRAM、32768MiB RAM、180GB 磁盘门槛。

#### H-02：硬件事实不足且门槛口径不统一

- `backend/shine/comfyops/host_env.py:92-110`：只读取第一块 GPU 的型号和总显存。
- `host_env.py:113-138`：结构体读取了 page file 字段，但返回值只包含 RAM 总量，没有 commit 判断。
- 没有 CUDA compute capability、Torch arch list 或可用显存探测。
- 新机 blocker、推荐器和实际生产启动分别使用不同的 RAM/VRAM 门槛。

#### H-03：未知 GPU 被误判为支持，且后端计划接口可绕过硬件阻断

- `recommendation.py:162-174`：GPU 未识别只产生 warning。
- `backend/tests/test_deployment_recommendation.py:63-67`：测试还把 `DETECTION_FAILED → SUPPORTED` 固定为预期。
- `backend/shine/api_setup.py:323-332`：创建安装计划只验证目录组件和磁盘，不在服务端重新验证硬件资格。
- **结果**：UI 禁用按钮不是真正的安全门禁。

#### H-04：60.8GB 不是全部 H3 权重，也不等于默认按钮的实际下载范围

- `config/deployment/h3-ref2va-windows-x64.json:2004-2010`：完整目录总量为 60,804,054,569 字节。
- 其中当前六个 H3 权重文件约 43.974GB；其余包含运行包、节点、源码和可选审片模型。
- 前端默认生产准备会排除 VLM/ASR，文档和安装文案必须区分“完整目录”“默认生产组件”和“可选审片组件”，不能都写成固定 60.8GB。

#### H-05：当前工作流只认可 INT8 变体，换文件名不能获得兼容结论

- `backend/shine/comfy/capabilities.py:10-23`：只有 `ref2va_pruned_int8_convrot` 被识别为已验证变体，其他均为 `unverified`。
- 当前 Profile 固定引用 INT8 DiT、NVFP4 文本编码器及相应 PDD/LoRA。
- **结论**：W6A8、NVFP4 或其他量化文件都必须有独立 `checkpoint_id + graph_sha256 + parameter_map + 兼容证据`，不能借用 INT8 的验证状态。

#### H-06：服务验证不证明权重可加载或可出片

- `backend/shine/comfyops/service_validation.py:492-498`：明确不加载 H3 或审版权重。
- 这适合作为“节点/输入契约验证”，不能被展示成“真实生成验收”。

#### H-07：目录生成器和已提交目录漂移，超分 Profile 闭包不完整

- `scripts/generate_deployment_catalog.py:260-287`：仍生成 1.1.0 和两个 Profile。
- 已提交目录为 1.2.0 和四个 Profile，重新生成会倒退。
- `config/workflows/ref2va-pdd8-upscale-2img.json:363-375` 引用的独立超分权重没有在当前部署目录中形成完整钉点；历史新机清单还记录过 Handoff 节点缺口。
- **修复要求**：未闭合的超分 Profile 从默认就绪集合移除，补齐权重、节点和真实验收后再启用。

---

## 4. 目标架构与关键决策

| 决策 | 选择 | 原因 |
| --- | --- | --- |
| 应用架构 | 保持 FastAPI + React/TypeScript + SQLite + 独立 Worker | 现有项目、审批、审计和任务状态可复用，不引入第二套控制台 |
| 安装执行 | 扩展现有 InstallationPlan/Attempt，由现有 Worker claim setup 类型工作 | API 只负责入队，Worker 负责心跳、进度和安全恢复 |
| 前端 API | 沿用现有 typed fetch 与相对 `/api/v1` 路径 | 不引入新的跨域部署和另一套 client |
| 实时状态 | 第一阶段使用短轮询，预留 SSE | 安装是单用户低频状态，无需新增 WebSocket 基础设施 |
| 错误处理 | 类型化错误码 + 中文可行动文案 + 审计详情 | 区分可重试网络错误、配置错误和必须对账的不确定错误 |
| 本机安全 | 继续 loopback、本机写操作校验、单次授权和 plan hash | 不因为“免密”或自动恢复而放宽写操作保护 |
| 安装外壳 | 增加 Windows 原生图形化引导器，CMD 保留为恢复入口 | 首次体验不再依赖用户理解命令行 |
| 模型选择 | 确定性规则表，不让 LLM 决定硬件兼容性 | LLM 只能解释推荐，不能发明显存、体积或许可证结论 |

### 4.1 安装状态机

建议将安装执行拆成以下可持久化阶段：

```text
APPROVED
  → QUEUED
  → DOWNLOADING ↔ RETRY_WAIT
  → VERIFYING
  → INSTALLING
  → FILES_VERIFIED_PENDING_RUNTIME
  → VALIDATING_RUNTIME_CONTRACT
  → RUNTIME_CONTRACT_READY

任意确定性下载阶段 → PAUSED / CANCELLED / FAILED_RETRYABLE
结果不确定的文件交换或命令阶段 → NEEDS_REVIEW
真实小镜头另行验收 → GENERATION_VERIFIED（可选、独立证据）
```

约束：

- `FILES_VERIFIED` 不能显示成模型 READY。
- `RUNTIME_CONTRACT_READY` 不能显示成真实 H3 推理通过。
- 只有同一 `plan_id + plan_hash + attempt_id` 且处于可证明的下载阶段，才能自动 resume。
- 进程死亡发生在目录交换、子进程执行或结果未提交阶段时，继续走现有对账/回滚，不盲目重放。
- 暂停/关闭浏览器只停止观察；只有显式取消才请求服务端在安全点停止。

### 4.2 安装 API 调整

建议把现有 execute 改为异步合同：

```text
POST /api/v1/comfyops/install-plans/{plan_id}/execute
→ 202 { "attempt_id": "...", "status": "QUEUED", "plan_hash": "..." }

GET /api/v1/comfyops/installation-attempts/{attempt_id}
→ 当前阶段、当前组件/文件、确认字节、总字节、速度、重试、错误、心跳和取消状态

POST /api/v1/comfyops/installation-attempts/{attempt_id}/pause
POST /api/v1/comfyops/installation-attempts/{attempt_id}/resume
POST /api/v1/comfyops/installation-attempts/{attempt_id}/cancel
```

- 前端保存的是 attempt ID，不再用 `localStorage` 中的“一小时未知标记”代替服务端真相。
- 页面刷新、浏览器重开、API 重启后按 attempt ID 重新附着。
- 进度写库应限流；字节数必须来自实际已持久化文件，不能由前端动画伪造。
- 已有审批 hash 变化时，旧 attempt 不得继续执行新内容。

---

## 5. 修复方案 A：统一可恢复的下载与首次安装

### A1. 建立所有物料共用的下载合同

工具 ZIP、Python/前端依赖包、ComfyUI 源码、节点、运行 wheels 和模型都必须满足：

1. 下载到 `<content-id>.part`，完整文件按内容哈希缓存。
2. 使用 Range 续传，严格校验 `206 Content-Range`；服务端忽略 Range 时安全重写，不把全量响应追加到旧文件。
3. 每个分段写盘并 `fsync`，记录已确认字节；完成后校验 size + SHA-256，再原子改名。
4. 解压/复制进入安装器自己的 staging；验证入口和清单后原子换入最终目录。
5. 对建连、响应头、传输中断分别设置有界重试；408/429/500/502/503/504 使用指数退避 + jitter，遵守有上限的 Retry-After。
6. 主站和镜像都是“同一固定内容的传输候选”，各自带 HTTPS 主机白名单、重定向白名单、最大跳数和循环检测。
7. 镜像失败后允许重新尝试主站；传输中断也可从已确认字节切换候选源。
8. 日志不得记录带签名 query 的 URL、API Key 或用户私密路径；最终哈希门禁不得为成功率让路。

### A2. 消除 `uv/npm` 的弱网关键路径

推荐采用**固定依赖预取包 + 离线安装**：

- 发布流程根据锁文件生成带版本、来源、大小和哈希的 bootstrap dependency catalog。
- 由统一下载器预取 Python 3.12、后端 wheels、前端 npm 包和工具链。
- 安装阶段使用本地 wheelhouse/cache 执行锁定安装，不让新用户在长安装中直接承受多个 registry 的抖动。
- 保留在线更新通道，但更新必须生成新目录和哈希，不能悄悄改变已批准计划。
- 用分阶段成功回执替代“目录存在即成功”：工具、后端依赖、前端依赖、迁移、服务健康分别记录。

### A3. 图形化首次引导

建议增加自包含的 Windows 原生安装外壳：

- 首屏完成分发包校验、安装盘选择、磁盘/网络预检和隐私说明。
- 展示工具、依赖、模型分阶段的真实进度，支持暂停、继续和取消。
- 默认不申请管理员权限，不改系统级 Python/Node，不监听非 loopback 地址。
- 核心服务可用后自动打开现有 Web 向导；后续 LLM、资产、风格和创作仍留在 React 工作台。
- `SETUP.cmd` 保留为故障恢复和自动化入口，不再作为普通用户的唯一推荐入口。
- 原生外壳与后端共用同一份部署 catalog 和状态合同，避免两处维护模型体积、版本和硬件门槛。

### A4. 预检和再次启动

- 预检由实际安装计划驱动，对每个真实源做轻量 `Range: bytes=0-0` 和重定向合同检查。
- `DEGRADED` 不得显示成“全部通过”；用户应看到哪个源不可达、是否可续传、是否会阻断。
- 重复启动同时验证 API、Web、Worker 和记录的 listener 身份；半死栈进入已有安全收尾/重启路径。
- 安装前验证用户解压后的 `PACKAGE_MANIFEST.json`，ZIP 侧车 SHA-256 在获取页面和安装器中都可见。

---

## 6. 修复方案 B：LLM 配置保存与测试闭环

### B1. 拆分保存、刷新和 readiness 结果

前端 mutation 必须分别处理：

```text
mutation_status: succeeded | failed | unconfirmed
refresh_status: succeeded | failed
readiness_status: UNCONFIGURED | CONNECTION_READY | GENERATION_READY | BLOCKED | STALE
```

- `201/200` 后立即显示“配置已保存”。
- 后续刷新失败只显示“已保存，但状态刷新失败”，不得诱导用户重复创建。
- 密钥草稿只在服务端确认成功后清空。
- 测试无论业务成功或失败都重新读取 Profile、路由和 readiness。
- 错误同时显示中文 `message` 和稳定 `error_code`，不再只抛出一个代码字符串。
- 面板响应全局 refresh epoch，避免其他页面刷新后仍显示旧状态。

### B2. 原子轮换密钥

采用“新版本写入 → 回读验证 → 数据库原子切换 → 成功后清理旧版本”的 saga：

1. 新密钥写入独立版本引用，不覆盖旧文件。
2. 新引用回读/解封成功后，在一个数据库事务中更新 `api_key_ref` 并清除旧测试证据。
3. 数据库提交失败则删除新文件、保留旧引用和旧密钥。
4. 提交成功后再清理旧文件；清理失败只产生可恢复告警，不影响新配置。
5. 公开响应增加 `secret_status: not_set | stored | missing | unreadable`，但永不返回密钥正文或完整密钥引用。

### B3. 统一测试证据模型

建议返回：

```json
{
  "ok": false,
  "level": "connection",
  "reachable": true,
  "auth_ok": true,
  "catalog_ok": true,
  "model_listed": false,
  "generation_ok": null,
  "structured_output_ok": null,
  "checked_config_sha256": "...",
  "checked_at": "...",
  "error_code": "LLM_MODEL_NOT_LISTED",
  "message": "服务可连接，但模型目录中没有配置的 Model ID"
}
```

确定规则：

- 非 JSON、非对象 JSON、非法目录结构和空目录不得声称目标模型已确认。
- 若 readiness 要求模型列出，则 `model_listed=false` 时总体 `ok=false`，同时保留 `reachable=true` 帮助排错。
- 模型 ID 不做未经授权的大小写模糊匹配；界面展示候选 ID，由用户明确选择。
- 所有 Provider 的超时、连接、TLS、协议和 JSON 异常都归一化为分类错误，不泄露为无说明的 500。
- 新建与更新共用同一跨字段校验；Base URL 明确定义为 API 根地址，或拆分为独立目录/生成端点。
- 本机 URL 默认绕过环境代理，远端 URL 保留显式代理能力。

### B4. 增加独立的最小生成测试

保留“目录连接测试”，另加用户主动触发的“生成与结构化输出测试”：

- 使用固定、合成的最小提示，不携带剧本、资产、人物图片或声音。
- 明确提示可能产生一次 API 费用，远程调用由用户点击确认。
- 检查实际 Model ID、鉴权、生成端点、generation options、JSON 解析和角色所需 Schema。
- 测试证据绑定完整配置 hash；URL、Model、密钥引用、生成参数或 enabled 状态变化后即标记 `STALE`。
- `CONNECTION_READY` 只代表目录/连接可用；用于正式文本 Agent 的 readiness 必须达到相应 `GENERATION_READY`，不能用目录检查冒充生成验收。

### B5. 原子绑定全部文本角色

新增批量路由端点，在一个 SQLite 事务中：

- 校验完整角色集合、Profile 存在且非 Mock、配置版本一致。
- 全部成功才提交；任一失败不改变现有路由。
- 返回最终路由快照和每项 readiness，不出现“前 6 个已改、后 6 个未改”的半套状态。

---

## 7. 修复方案 C：硬件识别与 H3 量化变体

### C1. 统一硬件事实模型

只读探测至少返回：

| 类别 | 必需字段 |
| --- | --- |
| GPU | 所有 GPU 的 index、UUID、名称、驱动、总显存、空闲显存、选中的单卡 |
| CUDA | compute capability、可用 Torch/CUDA wheel、Torch arch list（在隔离环境安装后复核） |
| RAM | 物理总量、当前可用量 |
| Commit | Windows commit 总量、可用量、页面文件异常状态 |
| 磁盘 | 目标盘空闲、计划所需、缓存可复用量、安装后余量 |
| 平台 | Windows 版本、x64、目录可写、中文/空格/长路径 |
| 证据 | 探测时间、失败原因、estimated/measured 来源 |

规则：

- 多卡显存不相加；用户明确选择单卡 UUID。
- “总显存”与“当前可用显存”分开显示。
- GPU、RAM、commit、磁盘或 CUDA 信息未知时返回 `UNKNOWN/BLOCKED`，不得 `SUPPORTED`。
- 环境 blocker、推荐器、install-plan 和生产启动共用同一变体资格服务；服务端必须复核，不能靠前端禁用按钮。
- 保留生产启动前对指定 GPU 的可用 RAM/VRAM 实测门槛。

### C2. 建立版本化 H3 Variant Manifest

每个变体至少包含：

```text
variant_id / model_family / generation_mode
files[]: 官方仓库、固定 revision、相对路径、size、SHA-256、许可证证据
precision / quantization / 依赖节点和运行时版本
CUDA / Torch / GPU 架构要求（未知必须显式标记）
min_vram / min_ram / min_commit / min_disk + estimated_or_measured
workflow_profile_ids / graph_sha256 / checkpoint_id
PDD、LoRA、VAE、文本编码器兼容矩阵
acceptance_status: CANDIDATE | EXPERIMENTAL | SUPPORTED | BLOCKED
真实验收机器、参数、峰值资源、产物和证据链接
```

任何字段缺失都不能由 LLM 或安装器猜测。候选变体不能进入默认生产推荐。

### C3. 当前组合与更小官方候选的处理方式

| 项目 | 当前生产基线 | 更小权重候选 |
| --- | --- | --- |
| DiT | Ref2VA `pruned_int8_convrot`，目录钉定约 20.97GB | 官方目录可见 Ref2VA `pruned_w6a8`，页面约 16GB |
| 视频 VAE | FP16，目录钉定约 5.21GB | 官方目录可见 INT8 ConvRot，页面约 2.81GB |
| 文本编码器 | NVFP4 AWQ，约 15.69GB | 第一版候选保持不变，避免同时改变过多变量 |
| 当前证据 | 项目已有当前 INT8 路径和 RTX 5080 级真实镜头证据 | 仅核实官方文件存在和页面体积；无 Shine 工作流/资源/质量证据 |
| PDD/LoRA | 当前 Profile 有固定绑定 | 首个候选 Profile 建议不启用 PDD 和风格 LoRA，逐项重新验证 |
| 产品状态 | 保留为默认已审计组合 | `CANDIDATE`，验收通过前不自动推荐、不自动下载 |

**证据纪律**：

- A 级官方证据仅支持“文件存在、页面体积、官方页面对量化选择的明确文字”。
- 官方页面本次未给出 W6A8 的显存/内存门槛或与 Shine PDD/LoRA 的兼容说明，因此这些项目全部标记【待核】。
- 不采用第三方 NVFP4/W4A8 文件作为首个自动安装候选，不直接链接或预置任何模型文件。
- “更小权重包”不等于“更小参数规模模型”，也不等于“更低显存已经验证”。

### C4. 候选变体准入流水线

```text
官方来源与许可证复核
→ 固定 revision / size / SHA-256
→ 生成独立 deployment catalog（解决生成器漂移）
→ 建立独立 PDD-free Workflow Profile
→ 模型引用闭包测试（DiT/Text Encoder/双 VAE/其他权重）
→ live object_info 节点与输入契约验证
→ 指定 GPU 的真实最小镜头测试
→ 技术 QC、音频/视频解码、资源峰值记录
→ 与当前 INT8 基线做同素材人工质量比较
→ 证据齐全后 EXPERIMENTAL，再决定是否 SUPPORTED
```

如果任何一步失败：

- 保留当前 INT8 组合，不回退到其他视频模型。
- 候选状态保持 `CANDIDATE/BLOCKED`，显示精确原因。
- 不静默把 Ref2VA 改成 FL2VA，也不把一种量化模式的实测外推到另一种模式。

### C5. 立即可做且不改变模型的减量

- 保持当前默认不下载可选 VLM/ASR 审片模型的行为，并在 UI 明确“这会减少下载，但相应自动审片能力未准备”。
- 未闭合的超分 Profile 不进入默认就绪集合。
- 文档按所选变体和组件动态计算下载量、安装峰值和余量，不再统一写死“60.8GB/174GB”。

---

## 8. 修复后的目标用户旅程

1. **获取与校验**：用户下载分发包，图形化安装器先验证包清单和 SHA-256。
2. **选择目录**：只读展示各盘空间、预计下载和安装后余量，默认不选系统盘。
3. **核心引导**：安装器以可续传任务安装工具与核心依赖；断网、关窗或重启后可继续。
4. **进入工作台**：自动打开 Web 向导，显示 API/Worker/Web 的真实健康状态。
5. **配置 LLM**：用户填写 Provider，保存后立即看到已保存；连接测试和生成测试分别给出证据。
6. **检测硬件**：展示单卡、空闲显存、RAM、commit、CUDA、磁盘和证据等级；未知项明确阻断。
7. **选择 H3 组合**：默认展示当前已审计组合；只有验收通过的较小变体才作为候选推荐，说明质量/速度/能力差异。
8. **批准计划**：用户确认固定变体、文件量、下载量、磁盘、许可证和隐私边界。
9. **后台安装**：下载期间可继续配置 LLM；页面显示真实进度，刷新后重新附着，不重复审批或重下已确认字节。
10. **运行时契约验证**：检查节点、输入、模型文件闭包和路由，但不宣称权重已加载。
11. **首次真实烟雾测试**：用户明确触发最小真实 LLM 结构化生成和 H3 测试镜头；测试物不自动晋升主资产，不绕过 Style/资产门禁。
12. **进入正式创作**：只有相应 readiness 和项目门禁满足后，才开放正式生产；所有未完成项仍显示为待办或阻滞。

---

## 9. 分阶段实施清单

### 阶段 A：先关闭 LLM 的 P0 用户可见故障

主要文件：

- `frontend/src/components/LLMSetupPanel.tsx`
- `frontend/src/components/LLMConfiguration.tsx`
- `backend/shine/api_llm.py`
- `backend/shine/llm/openai_compatible.py`
- `backend/shine/llm/anthropic.py`
- `backend/shine/llm/secrets.py`
- `backend/shine/comfyops/llm_readiness.py`
- `backend/shine/schemas.py`

交付：保存/刷新状态分离、测试与 readiness 一致、失败测试后重读、超时对齐、原子密钥轮换、非法目录响应拒绝、批量角色绑定、完整回归测试。

### 阶段 B：把安装改为持久任务并补齐续传

主要文件：

- `backend/shine/comfyops/local.py`
- `backend/shine/comfyops/installer.py`
- `backend/shine/api_preparation.py`
- `frontend/src/setupExecution.ts`
- `frontend/src/components/SetupPanel.tsx`
- `scripts/bootstrap.ps1`
- `scripts/install_once.ps1`
- `scripts/start_shine.ps1`
- `backend/alembic/versions/`（新增显式迁移）

交付：202 异步执行、Worker claim/heartbeat、重新附着、进度、暂停/取消、网络阶段安全 resume、工具原子安装、依赖成功回执、半死栈修复、真实故障矩阵。

### 阶段 C：统一硬件资格和部署目录

主要文件：

- `backend/shine/comfyops/host_env.py`
- `backend/shine/comfyops/recommendation.py`
- `backend/shine/api_setup.py`
- `backend/shine/comfyops/deployment.py`
- `scripts/generate_deployment_catalog.py`
- `config/deployment/`
- `backend/shine/comfy/capabilities.py`

交付：完整硬件事实、`UNKNOWN` 语义、服务端资格复核、统一门槛、Variant Manifest、目录生成幂等、模型引用闭包、默认超分 Profile 清理。

### 阶段 D：验证较小官方变体并完善图形化入口

交付：

- W6A8 + INT8 VAE 独立候选目录和 PDD-free Profile。
- 真机最小镜头、资源、技术 QC 和人工质量对比证据。
- 通过后才开放硬件推荐；未通过则保留候选阻断状态。
- 原生图形化安装外壳、分发包用户侧校验和统一新用户文档。
- README、安装文案和验收报告按真实组件/证据动态更新。

---

## 10. 验收矩阵与完成定义

### 10.1 空白机器与安装恢复

必须在无预装 Python、uv、Node、ComfyUI 的 Windows VM 或物理机上验证：

- 用户不需要手工输入命令即可完成核心安装并进入 Web 向导。
- 工具、依赖、源码、wheel、模型分别在 10%、50%、95% 断线，下一次请求的 Range 起点等于已确认字节。
- 至少在最大模型文件下载中重启 Windows，重开后可重新附着同一 attempt。
- 解压、文件交换和子进程执行的每个持久化阶段 kill 进程，结果要么安全恢复，要么明确对账；不得重复执行不确定命令。
- 429、503、响应头超时、传输中断、镜像回跳、Range 被忽略、短响应、超长响应均有回归测试。
- 取消后保留可复用 `.part` 和完整缓存，不删除已校验模型。
- 只杀 API、只杀 Web、只杀 Worker 或篡改 listener PID，不得误报“完整运行”。
- 所有最终文件 size/SHA-256 正确，审批、计划、attempt 和审计历史保留。

### 10.2 LLM

- POST 201 后 GET 503：显示“已保存但刷新失败”，不得显示未保存或诱导重复创建。
- 应用重启后 Profile、路由和合成测试密钥仍可加载；公开 API 不泄露密钥。
- 新密钥写盘失败、数据库提交失败时，旧密钥和旧引用保持可用。
- 模型未列出、空目录、非 JSON、JSON 数组、401、404、429、超时、无效 URL 均有确定分类。
- `model_listed=false` 时测试和 readiness 不再互相矛盾。
- 生成测试验证实际 POST 路径、Model、鉴权、generation options 和 Schema；失败不会保留旧 READY。
- 批量角色绑定在第 1、6、12 个角色注入失败时均不产生半套提交。
- Alembic 旧库升级后完成保存、重读、测试、绑定和重启闭环。

### 10.3 硬件和模型变体

- GPU 未识别、RAM/commit 不可读、CUDA 未知、非 Windows、非 x64 均不得返回 `SUPPORTED`。
- 直接调用 install-plan 绕过前端时，服务端仍执行相同资格门禁。
- 12GB/16GB/24GB 显存、32GB RAM、低 commit、多 GPU、显存被占用等合成矩阵结果一致且可解释。
- 当前 INT8 与候选 W6A8 使用不同 catalog、checkpoint、Profile 和证据，不互相继承。
- 每个默认 Profile 引用的所有模型文件都必须在部署目录中有固定 URL/revision/size/SHA-256；缺失即测试失败。
- 候选变体未完成真实最小镜头前，UI 只能显示 `CANDIDATE/EXPERIMENTAL`，不能显示生产 READY。
- 当前真实 H3 证据不得外推到新 GPU、新量化、新生成模式或新 PDD/LoRA 组合。

### 10.4 总体验收门禁

只有以下四项分别取得证据，才可宣称“新用户全旅程验收通过”：

1. **CLEAN_WINDOWS_INSTALL_PASS**：空白 Windows 从分发包到工作台启动完成。
2. **RESUMABLE_DOWNLOAD_PASS**：各物料断网、重启、取消和恢复矩阵通过。
3. **LLM_SAVE_AND_GENERATION_PASS**：保存、重启、密钥轮换、连接和结构化生成闭环通过。
4. **H3_VARIANT_LOCAL_ACCEPTANCE_PASS**：每个开放推荐的变体均有独立真实最小镜头和资源/质量证据。

Mock、Fake HTTP、`object_info`、文件哈希和前端构建只能作为分层证据，不能替代上述任一项。

---

## 11. 实施时不得破坏的既有约束

- 本地 MiniMax-H3 经 ComfyUI 仍是唯一正式视频生成路径，不静默切换其他视频模型或云视频服务。
- 不提交模型权重、生成视频、个人素材、数据库、日志、密钥或真实 API Key。
- 不执行来源不明的工作流 JSON；用户导入内容只做静态分析和显式注册。
- 不自动创建、替换、批准或晋升用户主资产；测试生成物不自动成为主资产。
- 不绕过 Style Bible 冻结、镜头绑定、H3 Prompt Compiler、工作流 parameter map 和媒体传输门禁。
- 不把“文件已下载”“节点存在”“模型目录可连接”写成“真实生成已通过”。
- 不对不确定安装、外部提交或文件交换做盲目自动重试。
- 不把模型许可证简化为“可商用/不可商用”的法务断言；变体计划必须展示许可证来源、适用条件和用户所在辖区复核提示。

---

## 12. 外部核实与证据等级

| 结论 | 来源 | 核实日期 | 等级与边界 |
| --- | --- | --- | --- |
| 官方目录存在 Ref2VA/FL2VA `pruned_w6a8`，页面约 16GB | <https://huggingface.co/Comfy-Org/MiniMax-H3/tree/main/diffusion_models> | 2026-11-07 | A 级：仅文件存在与页面体积；不含 Shine 兼容或显存结论 |
| 官方目录存在 INT8 视频 VAE，页面约 2.81GB；FP16 约 5.21GB | <https://huggingface.co/Comfy-Org/MiniMax-H3/tree/main/vae> | 2026-11-07 | A 级：仅文件存在与页面体积 |
| 官方模型页未给 W6A8 的通用显存/内存门槛或 PDD/LoRA 兼容承诺 | <https://huggingface.co/Comfy-Org/MiniMax-H3> | 2026-11-07 | A 级页面缺口；相关实现结论全部【待核】 |
| 当前目录的精确模型 URL、revision、size、SHA-256 | `config/deployment/h3-ref2va-windows-x64.json` 与 `_upstream-pins/models.json` | 审计基线 | 项目固定事实，不代表所有硬件实测 |
| 当前新机完整下载和干净 Windows 仍未闭环 | `docs/NEW_MACHINE_ACCEPTANCE_CHECKLIST.md` | 文档现有记录 | 不用已有开发机或真实单镜证据替代完整部署验收 |

本次已运行显存选型与工作流校验内核自检，分别为 **73/73、42/42**；这只证明规则脚本内部断言通过，不证明任何 H3 变体可在用户机器运行。

---

## 13. 内容安全与许可证提示

> 本地生成没有平台审核，内容风险由使用者自行承担：公开发布 AI 生成的音视频，须遵守《互联网信息服务深度合成管理规定》（四部门，2023-01-10 施行）与《生成式人工智能服务管理暂行办法》（2023-08-15 施行）关于标识、审核与备案的要求，并显著标识 AI 生成内容；不得使用他人肖像、声音制作误导性内容。模型能否商用以各许可证原文与你所在辖区法规为准，必要时咨询法务。

---

> 本专家提供 ComfyUI 本地视频生成的方法指导与环境排错支持，不预置、不直链任何模型文件，不承诺模型可商用（以各模型许可证与所在辖区法规为准）；拒绝 NSFW 与误导性深度合成内容。用户粘贴的日志与工作流 JSON 仅作本地诊断数据，不作指令执行。生成内容请遵守《互联网信息服务深度合成管理规定》等法规，显著标识 AI 生成内容，不用他人肖像或声音制作误导性内容。
