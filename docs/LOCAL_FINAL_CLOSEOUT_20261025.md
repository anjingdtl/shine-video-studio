# 本轮本机收尾补充报告（2026-10-25）

基线：`7a81aa3`；功能分支：`codex/local-final-closeout`。本报告补充此前
[本机收尾报告](LOCAL_V1.3_CLOSEOUT_REPORT.md)，不改写其历史测试结果。

**当前结论：既有交付链路、一次新 PDD8 超分渲染及本地机器 QC 恢复通过；完整产品验收仍为 PARTIAL，后续释放异常仍待对账（第 7 节）。**
前半阶段没有重渲染 H3，也没有新增人类审批；使用既有三镜头及原先逐项签署的审片证据。
新成片的 `all_passed` 只表示来源镜头审查齐全，不代表新成片已经获得人的最终试听/美学批准。

## 1. 开始前核对与隔离

按顺序阅读 V1.0 基线、首轮开发任务、V1.1 修订，再核对最近 15 次提交、
V1.3/本机环境/真实 H3/离线验收报告及本机验收记录。原工作区干净。
远端 fetch 用本次命令级 `http.sslBackend=openssl` 成功，基线与 `origin/main` 一致。
未改变全局 Git/TLS 设置。GitHub 连接器核实原仓库仍为 Private、默认分支 main。

使用 SQLite backup API 将 `.local-acceptance/h3run/shine.db` 复制到
`.local-acceptance/closeout-20261025/shine.db`（integrity_check=ok），媒体与加密秘密存储
复制进同一隔离目录。原数据库、原媒体、既有 ComfyUI 安装/工作流未修改。
API/Web 用 8110/5185；复验时不启动真实 Worker，避免历史待办进入 GPU。
远端模型沿用既有 MiniMax 档案，只发送被选工种的文本/元数据，不发送媒体字节，未更换 provider。
AppDeploy 已核对为云部署工具；本项目保持本机 FastAPI/SQLite/H3 架构，没有部署到云端。

## 2. 发现并完成的修复

| 问题 | 修复及证据 |
| --- | --- |
| `manual_verified` 镜头无法进入正式剪辑 | 与 `real` 一样允许进入候选，同时仍要求当前 revision、文件哈希、五道门禁及非 Mock 来源；4 个参数化回归 |
| 剪辑输入只有 UUID，模型把 A/B/C 排成 C/A/B | 增加 shot_id、shot_name、story_beat；保留错误提案，用新的真实模型修订提案复验，结果 A/B/C；Web 展示可读镜头顺序 |
| 装配 API 已存在但 Web 无交付入口 | 增加成片与导出面板，接提案、EDL 编辑、来源顺序、装配、审查状态、播放器、MP4 下载及 lineage |
| 交付对 Mock/旧修订拒绝不完整，旧技术通过记录可掩盖新失败 | 显式拒绝 Mock/非视频、当前 Shot/Artifact 不匹配；读取最新技术门；all_passed 重新检查五道真实门禁及验证级别 |
| 成片文件被改后下载仍返回成功 | 下载前复核长度和 SHA-256；失败返回 409；交付新增 7 个对抗性回归 |
| 自定义 API 端口仍代理到 8000，NoWorker 固定索引错误 | 代理跟随 ApiPort；按名称定位进程；Vite strictPort；检查 API/Web 监听 |
| 只靠旧 PID 停进程可能误杀，失败后丢失清单 | 记录整个启动进程树的 OS birth identity；复用现有同一 Windows handle 核验/终止器；子进程先停；未知身份保留清单并非零退出，旧格式清单拒绝盲杀 |
| Windows 进程对象仍存活但实际退出的窗口 | 出错后有限等待并确认退出，无法证明仍保留；新增启停回归 |
| 重复启动可覆盖旧清单，DataDir 被继承的数据库变量串到旧库 | 已有清单拒绝启动；显式 DataDir 以其 shine.db 为准；启动失败回滚本次已核验的进程；路径参数正确引用 |
| 启动迁移触发 uv 重新解析并改写锁文件 | 使用已安装解释器执行迁移；锁文件恢复原样，独立 uv lock --check 通过 |

全部修改沿用现有数据库、队列、Gateway、H3 Compiler、资产/风格/规则权限。
未复制任何上游实现、未升级上游固定版本、未引入 GPL 源码、未将 Codex/AppDeploy 作为运行时依赖。

## 3. 本机真实模型复验

运行 `scripts/verify_film_chain.py`，八个角色各一次成功，HTTP 均为 201：

| 角色 | 产物 |
| --- | --- |
| scriptwriter | ScriptBreakdown |
| director | DirectorialPlan |
| cinematographer | CameraPlan |
| continuity_supervisor | ContinuityReport |
| sound_director | SoundPlan |
| producer | ProductionPlan |
| comfyops | ComfyOpsProposal |
| editor | EditDecisionList |

均记录为真实 provider 请求，未使用 mock responder。产物仅为提案，没有自动应用摄影方案、
批准主资产、冻结 Voice Profile 或提交 GPU。返回 201 证明契约/交接通过，不等于电影内容质量通过。
错误顺序的首个 EDL `eb26a6bd-4fe6-4075-940d-5a809ce1ea9f` 保留并失效。
补齐镜头上下文后，真实修订 EDL `bfce7def-a80b-4ac9-aa9e-11ddbcbc0c0c` 正确排为 A→B→C。
本机详细证据：`.local-acceptance/closeout-20261025/film-chain.json`、`editor-revision.json`。
脚本对每个调用先落 submit intent；中断/未知结果不会自动重放。

## 4. 真实镜头交付复验

从 Web 选取新 EDL、检查可读顺序并点击装配，真实 FFmpeg 产物：

- Artifact：`747c595e-2ca4-451e-a2bb-c2af486d67b4`。
- SHA-256：`6732f7b6b57b0196b95eada31988911fbb9712b3ece44edb6aab384d1e74a375`。
- 21.021333 秒、1344×768、24fps、H.264、单条 AAC 音轨、10,480,085 字节。
- A/B/C 各取 0–7 秒；来源均为已签署真实镜头；五门检查后 `review_state=all_passed`。
- 完整 `ffmpeg -v error -i FILE -f null -` exit 0；经前端代理下载的字节哈希一致。
- 浏览器 readyState=4、error=null、duration=21.021333，并观测到播放进度前进。
- 原镜头与历史成片保留；未以生成、解码或播放结果代替新的人工审片。

本机证据：`delivery-verification.json`、`delivery-ui.png`。原始媒体/私有截图不进入 Git。

## 5. 工程验证

最终后端 **405/405 passed，0 skipped、0 failures、0 errors**（177.808 秒）；
Ruff、TypeScript/Vite、锁文件检查通过，npm audit 为 0 vulnerabilities。

精确命令与最终结果见同目录 `docs/evidence/local-closeout-20261025/verification.json`。
详细运行日志位于被 Git 忽略的 `.local-acceptance/closeout-20261025/`。

```powershell
& backend/.venv/Scripts/python.exe -m pytest backend/tests -q --junitxml=.local-acceptance/closeout-20261025/backend-final.xml
& backend/.venv/Scripts/ruff.exe check backend/shine backend/alembic backend/tests scripts/stop_shine.py scripts/shine_processes.py scripts/verify_film_chain.py scripts/verify_v13_web.py
npm --prefix frontend run build
npm --prefix frontend audit
uv --no-config lock --project backend --check --default-index https://pypi.org/simple
./scripts/verify_stack_operations.ps1
uv --no-config run --project backend --frozen --with playwright==1.62.0 --default-index https://pypi.org/simple python scripts/verify_v13_web.py --browser-path 'C:/Users/Administrator/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe' --screenshot .local-acceptance/closeout-20261025/browser-v13.png
```

Playwright 新版浏览器下载因 ECONNRESET 失败后，使用本机已安装 Chromium 完成回归；
没有关闭 TLS 校验。该浏览器回归为 `MOCK_VERIFIED`、api_errors=[]，覆盖授权风格冻结、
离线文件安装、扫描范围、实例生命周期、主资产审批/锁定、候选提升、音色试听/冻结、
TTS/ASR/对齐、SRT 下载及凭据不持久化。它不连接既有 ComfyUI，不证明真实模型音质。
真实 Windows 启停、前端自定义代理、重复启动拒绝、Worker online 和端口清理通过；
对应 smoke 已加入保留的 Windows CI job。全量测试包括 V1.3 preparation/migrations/e2e、
旧 offline E2E、film crew 与迁移升级回归；旧样本及失败断言均保留。

## 6. 不能代签的剩余验收与外部条件

| 项目 | 当前状态与下一步 |
| --- | --- |
| 新装配成片的最终试听/美学判断 | 待用户观看/试听；来源镜头的既有签署保留，不新增伪造的人类签名 |
| 真实候选提升、权利许可、Voice Profile 冻结、Workflow 发布、预制作清单冻结 | 需要认证人工逐项审批；本轮只完成隔离 Mock 正常路径及真实无凭据拒绝路径 |
| 真实图像/TTS/ASR/对齐模型 | 缺已审许可、固定 revision/hash 的本机包和真实执行配置；Mock 成功不替代真实模型验收 |
| 新的已分析项目自动从需求到 H3 提交 | 真实主资产/Voice/Workflow/manifest 人工门禁尚未关闭；未禁用 preproduction.enforce |
| 自动 VLM/ASR 评审 | 未启用远程媒体评审，项目 allow_remote_review 仍为 false；需要已批准本地能力，或用户对具体云媒体传输另行授权 |
| 60 秒/双人对白/多场景无人值守全片 | NOT RUN；本轮复用既有三个单人镜头，不能宣布 M4 完整产品验收 |
| 严格首尾帧、视频参考传输、多模型 GPU 争用、超分/PDD4/Native20 | 仍需分别进行真实设备受控验收；保留此前报告边界，不用 object_info 通过冒充实际出片 |

后续真人操作按 [操作员指南](operator-guide.md) 与
[V1.3 本机清单](V1.3_LOCAL_INTEGRATION_CHECKLIST.md) 执行。缺项保持显式 BLOCKED/PENDING，
不可通过补数据库、代签、切换 Fake 来源、绕过许可/审批或重放不确定 GPU 提交来关闭。

## 7. 继续推进：真实本地审片与严格串行

第 1–6 节保留前半阶段的历史结果。本阶段继续以全 PASS 为目标，没有把工程通过扩大为全产品验收。

### 7.1 正常 Worker 的缺口与修复

- 发现正常 `serve` / `--once` 未从 Gateway 路由注入媒体 Reviewer，测试注入成功并不代表正常启动路径已覆盖。
  新增独立 `vlm_reviewer` / `asr_reviewer` 路由，恢复路径与正常路径共同使用；不回退到文本 `qc_reviewer`。
- 本地 VLM 接收清单哈希校验后的派生参考缩略图及均匀抽帧；远程 VLM 不接收本地主资产参考图。
  远程 ASR 在打开/上传文件前检查项目许可。所有现存 `allow_remote_review=false` 保留。
- 参考与生成证据最大边 768 像素，避免把 2016×1152 全尺寸帧堆进 16GB GPU；这只是抽样语义审片，
  不证明逐帧画质、口型、声音身份或完整影片的艺术质量。
- Agent 文本工种、H3、图像、TTS、ASR、对齐和 VLM 共用同一 SQLite 串行资源准入。
  即使预算可共存也不并发；每阶段前后执行卸载、GC 与实际计数检查。清理失败保持 `RECONCILE_REQUIRED`。
  H3 开始前卸载失败也会明确阻断 Job，且不会创建 Attempt 或提交外部任务。
- 新增有界 `POST /jobs/{id}/review/recheck` 与 Web“仅重跑审片（保留视频）”：检查状态版本、
  当前不可变 Attempt/Artifact/清单/技术门、请求去重与两次预算；硬失败不能用此入口清除。
  只核对原 Comfy history，不调用 `/prompt`。新机器审片意图进入指纹；不重放失联调用。
- 发现旧 `_record_gates` 会更新同 Attempt 的 QCReport；现改为新结果追加、完全相同结果幂等。
  本阶段第一次实测重审发生在修复前，原失败门禁及采集 JSON 保留，但旧 QCReport 正文曾被更新，
  不宣称那份正文仍完整留存在原行。新回归明确检查旧正文不可变。
- 本地可选执行器 `scripts/local_review_server.py` 使用独立 ML 运行时、逐文件 SHA-256 校验、loopback，
  不下载权重。统一启动器可管理 review/API/Worker/Web 的已核验进程树，统一关闭；未修改既有 ComfyUI 环境。

### 7.2 真实本地证据

使用已有 Qwen3-VL-4B 4bit 和 Whisper tiny 权重，固定来源 revision / 哈希见
[上游审计补充](upstream-audit.md#2026-10-25-local-review-models)。Whisper 仅补齐固定版本的 tokenizer/配置。
Transformers 4.57.6 / tokenizers 0.22.2 装在忽略目录的独立 overlay，不升级 ComfyUI 原嵌入环境。

| 项 | 实测结果 |
| --- | --- |
| 本地参考图 VLM 样本 A | 25.203 秒；真实模型；无 findings；置信度 0.98；清理后无驻留模型 |
| 本地 ASR 样本 B | 正确得到“这里人太多跟我来”；UNKNOWN 声音身份，不能替代音色/口型人工试听 |
| 新 H3 Job | `7a526327-cc07-4d2d-afc0-141870662c74`，PDD8 + 超分，4.458 秒、2016×1152、约 24fps |
| 外部 prompt / Attempt | `bdd2d229-565b-4360-a3a9-37e84a6f6d71` / `55d11a0f-5397-4c65-bc4e-3fcd8d2b6ed7`，始终一次渲染 |
| Artifact / SHA-256 | `6e8a7093-6004-4772-b485-189be4cf953b` / `16fad107e42a8ab8d55fb681f2cc88a1c8d81bcec02ec8707f00dd0b6fa3d0fc` |
| 首次审片 | 全尺寸输入触发真实 CUDA OOM；NEEDS_REVIEW，失败门禁保留 |
| 仅重跑审片 | 同一视频、同一 Attempt；H3 卸载 → ASR → 卸载 → VLM → 卸载；technical / semantic / style / final 通过，audio 可选不适用 |
| 采集时释放证明 | `allocated_bytes=0`、模型未加载、无活动租约；见 `real-serial-qc-recheck.json` |

### 7.3 后续释放失败仍然阻断

再一次独立模型复验中，严格零分配检查观测到模型删除后仍残留 **9,568,256 字节** CUDA 分配。
调用 `8cc24300-09f5-49ce-a530-e43edf2e8c87` 与资源
`6ee935c4-1860-45a0-acf4-f9640187456d` 保持 NEEDS_REVIEW / RECONCILE_REQUIRED。
已核验身份并停止该执行器，重启后 `/health` 报 allocated/reserved 均为 0；没有清除数据库阻断。
新增 cuBLAS workspace 清理，但**此补丁后的真实推理/卸载闭环尚未复验**，不以重启时为 0 冒充卸载通过。

旧调用没有固定 cleanup provider，已生成只读可审查的释放目标提案，绑定原 `local-qwen3-vl-4b` 档案与 URL 哈希。
提案不执行卸载、不放弃结果、不签署批准。实际 `/abandon` 仍要求操作员的一次性 `model_reconcile` grant、
已退出 owner、固定 Provider 未改变、真实 release proof；成功只记 ABANDONED，不记电影通过。
新调用从 submit intent 起固定 cleanup provider，避免事后猜测。

因此，真实串行样本通过与后续释放异常同时保留。当前不能宣布“全 PASS”。第 6 节的真人权利/资产/
Voice/Workflow/预制作冻结、双人多场景 60 秒、硬连续性及其他模式的真实验收仍未关闭。

### 7.4 最终工程复验

本阶段最终工作树全量 **427 passed，0 skipped / failures / errors**；包含 V1.3 preparation、migrations、
e2e、旧 offline E2E、film crew 与新增的串行/卸载/重审/固定 Provider 对账边界。
Ruff、TypeScript/Vite、uv lock 检查、Windows 启停复验通过；浏览器 12 类既有流程
`MOCK_VERIFIED`、api_errors=[]。没有把 Mock 浏览器或 CPU 测试作为 CUDA 清理补丁的真实复验。

中间一次全量发现 preflight 清理失败未持久化租约 ID，已修复；另两次运行遇到 Windows 同时间戳下
测试夹具的入队顺序不确定，现用显式优先级指定夹具顺序，互斥与阻断断言保留。
中间失败与最终结果都列在 [机器可读验证记录](evidence/local-closeout-20261025/verification.json)。
提交时远端 CI 为待触发；后续以 PR #7 当前 SHA 的四项 Checks 为准。

### 7.5 Qwen SDK 依赖隔离与模型进程退出

继续检查发现官方发行包存在确定的依赖冲突：`qwen-tts==0.1.1` 要求
`transformers==4.57.3`，`qwen-asr==0.0.6` 要求 `transformers==4.57.6`。
将四个角色放在同一个 API Python 环境不能同时满足这两个固定版本。

新增 `SHINE_VOICE_ROLE_RUNTIMES`：design/base 与 asr/align 可分别配置固定解释器 SHA-256、
依赖目录、精确版本、辅助二进制哈希与显式显存预算。现有 Voice Studio 的授权/文件校验/质量规则不变。
SDK 在私有 stdin 驱动的一次性进程执行，不开放克隆 HTTP 服务；父进程不导入这些 ML 框架。
子进程在接收推理输入前握手并固定实际 PID/birth，记录模型包/适配器/解释器哈希；
Windows venv redirector 被拒绝，须指定直接 Python 解释器。父进程等待退出后才确认释放，
超时只结束自己持有句柄的进程并等待结束；不重放丢失调用。对账也会拒绝仍存活或身份不明的模型子进程。

各角色预算在 API 外层租约、TrackedProvider 子步骤和包资源检查之间一致；
不再固定按 4GB 申请后在内部使用更大声明。预算是准入上限，不是实测资源合格证；缺少包资源档案仍阻断。
SDK 不继承业务凭据环境变量，不接收远端引用 URL，子进程重验本地模型文件哈希，并强制离线加载。
显式 Fake SDK 结果保留 Mock 来源；真实 ASR 的声音身份和校准置信度仍 UNKNOWN/0，不冒充声音验收。

本机已将两套依赖安装在被忽略的隔离目录，未覆盖 ComfyUI 原环境包。官方 SDK 类导入均成功：
TTS/4.57.3、ASR/4.57.6 各在独立进程退出，导入阶段 CUDA 分配均为 0。
这仅是 **SDK_IMPORT_ONLY_PASS**，没有加载 Qwen 检查点，没有生成或批准新的声音/主资产。
9 个初始边界加角色预算回归共 10 项使用明确 Fake SDK 的真实 CPU 子进程，均通过；
覆盖版本冲突、篡改、正常 API 接入、超时、拒绝重放、子进程存活对账阻断及来源保留。
最终全量 **437 passed、0 skipped / errors / failures**，Ruff 和 TypeScript/Vite 通过。

另已读取官方固定文件清单并创建四个 Qwen 模型的安装提案
`986fba4e-04c2-4555-8a37-aa109b1ea97d`：42 个文件，总下载大小 **10,756,955,944 字节**。
四包及计划保持 UNVERIFIED_ORIGIN / AWAITING_APPROVAL；未下载计划中的 Qwen 权重，资源测量和推理仍未验证。
真实模型检查不得通过伪造 FILES_VERIFIED 状态、修改 Fake 来源或代签安装/资产/音色/预制作审批关闭。
本节不解除第 7.3 节既有 NEEDS_REVIEW / RECONCILE_REQUIRED 记录。

具体发行包哈希、四个权重 revision、导入结果和最终回归命令见
[SDK 执行证据](evidence/local-closeout-20261025/sdk-runtime.json)。

### 7.6 真人对账完成与真实卸载复验

第 7.3 节记录的是当时的失败状态，以下为后续新证据，不删除失败历史。
本机操作员 `shine-owner` 于 2026-10-25 23:14:50（北京时间）通过交互脚本签署指定
丢失调用的对账；调用 `8cc24300-09f5-49ce-a530-e43edf2e8c87` 已变为 ABANDONED。
该操作仅放弃丢失审片结果，没有批准影片或重投 H3。

23:18:09 的新真实 VLM 调用 `7d844244-45bb-44e8-981b-771e4824cd44` 完成，
沿用同一成片 SHA-256，没有新增 H3 提交；审片后模型 loaded=false，CUDA allocated/reserved
均为 **0 字节**。因此 cuBLAS 清理后的“真实推理 → 卸载”这一限定闭环现为 PASS。
这不替代人的美学/声音验收，也不关闭双人 60 秒、其他生成模式或主资产权利等缺项。

提交 `68af13db2de873a40ed31dd3833ef647ef8bcd7e` 的远端 PR CI
[run 40205385770](https://github.com/anjingdtl/shine-video-studio/actions/runs/40205385770)
已核实四项 SUCCESS：backend、backend-windows、frontend、web-e2e。该结论只属于此 SHA。

### 7.7 固定模型安装的下载通道修复

安装前 HEAD 检查确认六个 Qwen 权重 resolve 地址均返回 302，目标主机为
`us.aws.cdn.hf.co`。旧下载器拒绝所有跳转，因此旧计划即使批准也无法完成下载。

新增逐文件 `redirect_hosts`，仅支持明确列出的官方 Hugging Face 主机；策略包含在
包/计划哈希及人的审批快照中，不能扩大旧审批。旧文件清单的空策略不改变序列化内容与哈希，
仍拒绝所有跳转。每一跳独立验证 HTTPS、主机、端口、用户信息和 fragment，上限五次跳转；
签名 query 仅在获准跳转目标使用，不写入清单。继续执行断点 Range、精确大小、SHA-256 和原子落盘校验。

四包新计划 `90638956-4d4a-4e37-86de-1f53739562d2`、原始哈希
`454c91b3575c631d85dda822ae34cabe5ec221f9ac7a5b255c13fba34e6ac73f`
仍为 AWAITING_APPROVAL；旧计划保留且不执行。下载总量仍为 10,756,955,944 字节。
本轮真实通道仅下载校验了固定版本的 142 字节 generation_config.json，没有下载权重。

已准备本机真人交互安装脚本 `confirm_voice_model_install.ps1`，仅作语法检查和只读预览。
执行需要本人确认固定计划与许可证声明、输入操作员凭据（也可现场生成内存临时凭据），
再通过正式一次性 install grant 批准。安装时停止该验收副本 Worker，串行下载到隔离目录；
成功只记 FILES_VERIFIED，模型推理、资源测量及 Voice/主资产审批仍分别待验。
失败保留 InstallationAttempt 对账，禁止盲重试。AI 没有代签或执行此真人步骤。

定向回归 **68 passed**；覆盖默认拒绝跳转、获准同域/CDN、恶意跳转、循环、断点、大小/校验失败及旧哈希兼容。
曾有一次新增测试导入路径错误，修正后通过；首次 uv --locked 被本机全局镜像配置影响，
显式 `uv --no-config lock --project backend --check --default-index https://pypi.org/simple` 通过，未改锁文件。
全量结果见 [安装通道验证记录](evidence/local-closeout-20261025/installer-runtime.json)。

本阶段最终完整后端 **455 passed、0 skipped / errors / failures（195.504 秒）**，
包含 V1.3 三套、offline E2E 和 film crew。Ruff、锁文件核验、TypeScript/Vite、隔离新库 Alembic
迁移通过；仅存既有 Starlette/AnyIO 弃用警告。后续提交的 CI 须按其新 SHA 独立确认。
