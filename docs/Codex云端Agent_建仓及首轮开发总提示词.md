> **仓库就绪更新（2026-10-20）：** 私有仓库 `anjingdtl/shine-video-studio` 已存在且 main 分支已初始化。运行此提示词的 Codex 应直接在此仓库执行源码审计与 M0/M1 开发，跳过新建仓库步骤；不得覆盖或删除现有文件。

# Codex 云端 Agent 总任务提示词：创建 Shine 视频工作台仓库并开始实质开发

> **使用方式**：将本文件全文发送给 Codex Cloud。**同时上传**《Shine视频工作台_V1.0_项目建设方案.md》，并在 Codex 中选择具有 GitHub 写入权限的环境。若 Codex Cloud 环境不能创建 GitHub 仓库，参照下文“权限降级路径”先在现有工作区执行代码交付；用户创建/连接空仓库后再推送。不要向 Agent 提供私人 API Key 或直接上传角色原始素材。

---

你现在担任《Shine视频工作台》（Shine Video Studio）项目的**技术负责人、资深 Python/React 全栈工程师、Agent 编排架构师及测试负责人**。你的任务不是仅写设计文档，而是在我的 GitHub 账户或我指定的组织下新建一个独立 GitHub 仓库，并按照随任务提供的《Shine视频工作台 V1.0 项目建设方案》**开始真正开发可运行的 Windows 本地 Web 应用**。

## A. 产品目标（不得曲解）

1. 软件名称：**Shine视频工作台 / Shine Video Studio**；仓库建议名：`shine-video-studio`。
2. **本地 MiniMax-H3 是正式视频生产的唯一核心生成引擎**；ComfyUI 作为首选 H3 执行后端。不能偷偷换成其它视频模型，也不得把调用在线 MiniMax 视频 API 假装为本地模型渲染。
3. 最重要的业务目标是减少我制作多镜头、多集短剧时的**人工值守**：从项目书/剧本和人工素材出发，用户先批准并冻结全片影视风格，然后独立 Agent 自动分镜、调配素材、编译 H3 官方提示词、渲染、质检、诊断、有限返工、剪辑、导出。
4. 应用是**独立 Agent 软件**，支持配置多家 LLM/VLM API，包括 OpenAI-compatible Base URL、API Key、Model ID、本地 LM Studio/Ollama 与可选的原生接口适配；**Shine 运行时禁止依赖 Codex、Claude Code、OpenCode**。Codex 只负责本次开发。
5. 部署：**Windows 本地 Web 应用**（FastAPI 后端 + React/TypeScript 前端 + SQLite 持久任务 + 后台 Worker），中文优先、支持中文路径。
6. 生产策略：**速度/质量平衡**，按镜头重要性分级；人物串脸、服装漂移、剧情/对白错误、资产越权和文件损坏是硬性错误，绝不可为了速度直接放行。
7. 资产策略：**只使用用户手工导入并批准的角色、场景、道具、声音主资产**。Agent 严禁自行调用生图工具制作、替换、覆盖、批准主资产。允许从已批准视频派生末帧/接缝参考，但必须可追溯并不得自动转成主身份素材。
8. 风格门禁：在生产线启动前，用户须完成摄影语言、镜头调度、色调、影调、镜头质感、剪辑节奏、表演声音的**Style Bible 审批与冻结**。Agent 不能擅自修改已冻结风格；每次镜头任务引用不可变风格版本 Hash。
9. 坚持**唯一性原则**：唯一项目/资产主数据库、唯一任务 Orchestrator、唯一 H3 Prompt Compiler、唯一审计记录体系。不能完整并行拼接数个开源项目的控制台或运行两个相互竞争的任务队列。

## B. 第一步：核查访问权限，创建独立 GitHub 仓库

1. 检查工作区 Git 上下文、`git remote -v`、当前分支、`gh --version` / `gh auth status`、可使用的 GitHub API 或连接器及其**创建仓库权限**。不要打印、泄露或记录任何 Token。
2. 若有创建仓库的权限且目标仓库不存在，在我已授权的 GitHub 个人账户或明确指定的组织下新建 **`shine-video-studio`** 仓库；**默认为 Private**，使用 `main` 默认分支，写入描述“Local-first autonomous MiniMax-H3 short-drama production workbench”。绝不能删除同名仓库或覆盖已有内容。若同名仓库已存在，先核对 owner/权限；未经确认不要破坏已有代码。
3. 若云端容器具有授权的 `gh`，可以使用 GitHub CLI 创建仓库；如果具备 GitHub 连接器的 Create Repository 功能，也可使用已授权的动作。严禁编造“已创建”或 GitHub URL。
4. **权限降级路径：** 如果 Codex Cloud 只能针对已连接仓库工作、没有 `gh` 认证、没有新建权限、网络受限、或者无法在当前任务内切换新建仓库，不得停在概念说明。请在**当前可写工作区**初始化完整源码与 Git 历史，提交可复用内容，输出用户需要创建/连接空仓库的最少操作（例如在 GitHub 建立私有 `shine-video-studio` 后重新选择 Codex 云端环境），并说明本轮已经完成的实际提交/分支/PR。不能擅自推到无关项目仓库，也不能声称已推送。
5. 若新仓库创建成功且 Cloud 环境允许推送，按权限配置 `origin` 并推送主干或功能分支，尽可能提出 PR。若在 Codex 当前环境不能直接打开新仓库，应提供可复用初始化提交及准确后续操作，不得伪造 PR 链接。

## C. 第二步：真实阅读并审计上游源码（非仅 README）

先审计以下四个公开项目，记录所见 Commit SHA、许可证、接口、源码复用路径、兼容性和风险：

- https://github.com/qiukaihui/comfyui-auto-drama ：重点研究 `console/batch_console.py`、`console/chain_daemon.py`、H3 工作流构建/提交/下载、错误码和 FFmpeg 合成。该项目包含 AI 生图，Shine 必须禁用这条路径。
- https://github.com/civilcoco/ComfyUI-MiniMaxH3-Myang ：重点研究 H3 长视频导演、媒体引用、续接和音画接缝。GPL-3.0-only 节点仅以**独立可选的 ComfyUI 插件/适配器**集成；不得在未经许可证审查时复制 GPL 实现到 Shine 主仓库。
- https://github.com/benjiyaya/Calliope ：研究独立 Agent 运行、模型 API 路由、项目管理和分镜结构，仅提取适合 Shine 的设计或许可证兼容代码，不引入 Calliope 作为必须启动的第二套服务。
- https://github.com/MiniMax-AI/MiniMax-H3 ：尤其是 `skills/h3-prompt-writing/SKILL.md`、`references/base-en.txt`、`references/ref-en.txt` 和官方本地 H3-Base 能力限制。这是 H3 Prompt 格式和模式校验的权威来源。外部规则必须固定验证过的版本/哈希。

请输出真实的 `docs/upstream-audit.md`，区分 `可复用已验证`、`可参考未移植`、`许可证隔离`、`待本机 GPU 验证`。如果网络无法读取公开源码，就据实记为 `BLOCKED_SOURCE_ACCESS`，为相关适配器建立契约与模拟测试；不得凭空编造源码细节。

## D. 第三步：进行架构收束并建立可运行代码仓库

严格按照附件建设方案，优先采用**模块化单体 + 独立后台 Worker**：

- `backend`：Python 3.12、FastAPI、Pydantic v2、SQLAlchemy/Alembic、SQLite WAL、pytest；
- `frontend`：React + TypeScript + Vite，中文 UI；
- `Agent Runtime`：单一中央制片 Agent + 按需的规划/分镜/素材/审片/修复能力；Agent 返回 Pydantic/JSON Schema 数据，不能直改 DB；
- `LLM Gateway`：Provider Profiles 和能力探测，初期至少 mock + OpenAI-compatible，预留原生 Provider 适配；默认无需真实 API Key；
- `Asset Registry`：用户素材上传、元信息、批准/锁定、哈希、角色/场景/道具绑定、派生 lineage；
- `Style Studio`：七类风格参数、草案/审批/冻结版本、禁止修改已冻结快照；
- `ShotSpec & H3 Compiler`：结构化镜头规格、镜头对白时间码、模式/参考校验、局部 Picture/Audio/Video 映射、官方 Skill 规范字段；
- `Render Adapter`：MockComfyUIProvider 和 RealComfyUIProvider 清晰分离；
- `Orchestrator`：事务任务租约、心跳、依赖图、任务生命周期、提交意图记录、恢复对账、限流与限制重复渲染；
- `Quality Engine`：先实现确定性 preflight/technical QC，语义 QC 使用可插拔 VLM/ASR 契约与模拟审片；
- `Delivery`：FFmpeg 合成接口、阻滞清单与工程导出。

不要一开始引入 Redis、微服务、Kubernetes、复杂第三方 Agent 框架或多种重叠数据库。若选择从某个上游提取代码，务必说明具体模块、版权保留和迁移测试。

### 必须体现的核心业务不变量

- **未冻结 Style Bible，不允许创建正式渲染任务**；用户可明确跳过预演，但必须亲自批准风格冻结。
- **未经批准的人工作品主资产，不能被渲染使用**；Agent 禁止自动生成人物/场景/道具/音色主资产。
- **同一镜头局部 `<Picture N>` / `<Video N>` / `<Audio N>` 引用不等于全局资产 ID**；提交前校验文件哈希和每次 `reference_manifest`。
- H3 官方基线：片段 4–15 秒、24 FPS；Ref2VA 图像 ≤9、视频 ≤3、音频 ≤3、合计文件 ≤12；实际能用的模式以部署的节点、checkpoint 和运行时探测为准。
- 本地 H3-Base 不能冒称已拥有未开源的完整官方 H3-Context-IR/H3-Regenerate-2K 链路。
- 镜头级任务与尝试记录分开；外部提交不确定时先对账，严禁盲目重复提交。
- 前镜若被返工替换，依赖其输出的后镜必须失效/复核；其它独立合格镜头不得重算。
- 每镜语义返工默认为最多 2 次（首次生成不计入这 2 次）；基础设施网络重试和语义返工分开计数。
- VLM 低置信度不得直接通过关键镜头，也不得无限自动返工；允许 `NEEDS_REVIEW` 局部阻滞并继续其它镜头。
- Web 关闭后后台任务继续；Windows 掉电/休眠无法保持物理渲染，应在恢复后对账续跑。

## E. 首轮必须完成的真实开发任务（不是 TODO 文档）

请在本轮至少完成 **M0 + M1**，不必冒险声称 M2–M4 已经完成。

### M0：项目初始化与源码结构

- 创建基础仓库、合规的 `.gitignore`（排除主资产、模型文件、输出视频、DB、日志、API Key）、根目录 `README.md`, `AGENTS.md`, `LICENSE`（实际代码许可核查后选定）, `docs/` 和技术 ADR。
- 建立可启动的 FastAPI 后端（至少 `/health` 和 `/api/v1/...`）、React 前端（至少项目、素材、风格、监控基础页面）、标准配置模板、Windows 启动/环境检查脚本。
- 建立数据库 Schema 与 Alembic migration，创建测试数据脚本（只能使用合成元数据/占位文件，不提交用户素材）。
- 设置 GitHub Actions：后端测试、基础 lint/type-check、前端构建；不依赖 GPU、ComfyUI、真实模型或秘密密钥。

### M1：唯一状态库与可恢复调度

- 实现真实的 Project/Asset/Style API：资产上传/哈希/审批与锁定；用户冻结 Style Bible 的原子操作、不可变版本快照和准入门禁。
- 实现 `ShotSpec` / `RenderRequest` / `QCReport` / `RepairPlan` 的 Pydantic/JSON Schema 初版及序列化测试。
- 实现 SQLite 持久任务状态机：`PLANNED/WAITING_ASSET/READY/QUEUED/SUBMITTING/RUNNING/QC_PENDING/PASSED/REPAIR_PENDING/NEEDS_REVIEW/FAILED`，禁止非法跳转；带任务依赖、Lease、Heartbeat、Audit Events。
- 实现**可控制的 Mock H3 Provider**：模拟成功、失败、延迟、队列重复/提交不确定、崩溃恢复、生成工件入库，测试无需 GPU。
- 实现基础生产控制页：模拟项目任务列表、状态和失败原因、暂停/继续、人工审批风格与资产；页面必须能调用真实后端接口，而不是完全静态假数据。
- 编写最少以下自动化测试：风格未冻结禁止生产；未批准资产禁止使用；跨镜引用不串位；重复提交不盲重试；Worker 重启恢复；前镜替换使后继依赖失效；返工预算上限；敏感 Key 不写日志。

### 如果 M0+M1 完成且剩余时间允许

再开始 M2 的真实接口：H3 Prompt Compiler、ComfyUI API 适配器、Provider Profile 能力探测、FFprobe 校验；能做多少做多少，必须把功能状态真实标明 `IMPLEMENTED`, `MOCK_VERIFIED`, `PENDING_LOCAL_VALIDATION`, `NOT_STARTED`。不要将不可靠的局部 stub 标成完成。

## F. 质量、对抗性审查与验收

1. 编码前先写 `docs/architecture.md` 与 `docs/contracts.md`，列出主对象/状态和唯一责任边界；同时尽快开始代码，不要一直停在写方案。
2. 做对抗性检查：重复 Worker、ComfyUI 已接受但网络应答丢失、相同文件名不同哈希、非法 Picture 标签、Style 冻结后的变更、权限越界、传入路径穿越、云端 VLM 未经批准收到用户图片、重试预算耗尽、坏 MP4 被判 PASSED 等。
3. 让模拟生产能够从创建项目、批准素材、冻结风格、生成队列、执行模拟、QC 校验、状态显示到退出重启恢复走完闭环。
4. **不得谎称有 GPU/H3 真机验收**。当前云端如果无法连接本地 Windows H3，请生成 `docs/local-h3-acceptance.md` 的精准步骤、需要用户提供的 ComfyUI 工作流模板、模型版本、节点能力、样片与期望日志；真正 H3 连通性标为 `PENDING_LOCAL_VALIDATION`。
5. 保护隐私：默认仅监听 127.0.0.1，用户资产不上传云端，API Key 不入库明文或前端；本地凭据存储以安全机制实现或标为未完成阻滞。
6. 许可证与上游审计不可省略；不要直接把 GPL-3.0-only 的沐阳源文件复制成 MIT 项目代码，也不要把全部四个上游仓库直接合并。
7. 对未完成项目/硬件阻滞提供精确的问题列表，不要为了宣称完成而删除测试或放宽硬门禁。

## G. 结果交付格式

最后必须给我一份真实开发回报，按以下顺序：

1. **GitHub 仓库创建结果**：实际 owner/repo URL、Private 状态、分支、提交 SHA、PR URL（如确实存在）；无权限则说明原因和下一步最少操作，不得伪造。
2. **源代码复用审计结果**：实际上游版本、检查的源码模块、是否复制代码、许可边界。
3. **已经完成的真实功能**：可运行 API、页面、数据库表、Worker、测试与其证据；不要罗列尚未实现的目标当成成果。
4. **完整测试命令与结果**：成功/失败数量、不能测试的原因。
5. **Windows 本地运行说明**：环境要求、启动命令、mock 演示步骤、将来连接 H3 的步骤。
6. **剩余 M2/M3/M4 backlog**：按优先级输出 Issue 建议及风险，明确 `PENDING_LOCAL_VALIDATION` 的本地验收清单。
7. **安全检查**：没有上传模型权重、用户原始资产、API 密钥或私有文件到 GitHub。

**执行优先级**：验证 GitHub 权限 → 可创建则创建仓库 → 读取并锁定上游版本 → 完成 M0+M1 的真实代码和测试 → 提交/推送或提供可复用提交 → 输出真实结果。除非遇到明确的权限障碍，不要因为需求规模大而只交空仓库或 TODO。