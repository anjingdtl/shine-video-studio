# 本轮本机收尾补充报告（2026-10-25）

Strict local dependency check (2026-10-27): the unscoped local default failed because its Aliyun index conflicted with the lock's official PyPI registry, rather than because dependency versions changed. `uv sync --project backend --locked --dry-run --default-index https://pypi.org/simple` and an offline `uv lock --check` with the same explicit index passed. The original lock was restored byte-identically, all versions/wheel hashes were preserved, and global index settings were not changed. Hosted CI still requires an actual run after the account block is resolved.

Independent input review continuation (2026-10-27): complete backend **663 tests passed**, zero failures/errors/skips; Ruff and fresh TypeScript/Vite passed. The isolated browser now verifies **18 Mock flows**, including exact selected-shot input submission and per-call remote text consent reset. Playwright download timed out before execution; the successful run reused the verified existing 1.62.0 cache without changing dependencies.

The explicit `qc_reviewer` film role now emits a typed `StyleInputAssessment` from frozen Style and ShotSpec text only. Frozen quotes and quoted shot evidence are validated, seven criteria are required, and conflicts/uncertainty cannot recommend proceeding. New analyzed real manifests pin `independent-text-v1` and require this evidence; Job creation selects the latest exact matching report and pins its identity/hash, while the shared Worker gate rechecks current input, result, route, provenance and staleness before submission. Mock reports cannot qualify real execution. Historical frozen plans and passed data are preserved. The page offers current-project shot selection; no media bytes go to this text role. This is not pixel/audio QC or human approval. Mock provenance is monotonic across text transport and role records, including non-Mock configured endpoints advertising a Mock result. **Actual text-model controls are still pending; VLM remains withheld and the full product remains PARTIAL.** See [independent input evidence](evidence/local-closeout-20261025/independent-text-input.json).

Actual seven-field continuation (2026-10-26): **VLM qualification remains FAILED / withheld**. The real positive and masked-face negative passed. The empty-street call returned an unusable received response (`FAILED`, released lease, no retry); its initial cleanup hit a missing executor-identity field, after which an explicit continuation verified the exact owner and physically exited the process. The remaining walking control ran once separately and still falsely passed: its checklist calls walking toward camera consistent with the unchanged frozen sitting requirement. Re-reading both source requests confirmed the same frozen Style snapshot; this is not a substituted-style control. Coverage alone therefore does not establish correct input reasoning. No VLM capability was published, no production gate/history was rewritten, and no failed H3 task was replayed. All stages are released and ComfyUI has been restored idle. Current ASR qualification is unchanged. See [actual controls and failure evidence](evidence/local-closeout-20261025/style-input-real-calibration.json). Next work must separate text input consistency from pixel review; full-product acceptance remains **PARTIAL**.

Current protocol hardening (2026-10-26): **639 backend tests passed**, zero failures/errors/skips; Ruff and a fresh TypeScript/Vite build passed. A red wire counterexample reproduced acceptance of a positive response with no explicit frozen-style input comparison. New VLM responses must cover all seven Style fields, and any declared input conflict/uncertainty must have a corresponding style finding and non-pass verdict. Missing/duplicate/inconsistent coverage is a received failure with a response digest and no automatic retry. Native qualification and production evidence check the same contract; immutable historical records remain readable. This is software verification only: the four unchanged real-media controls still require new serial calls, and VLM production qualification remains withheld. Current ASR qualification is unchanged. See [protocol evidence](evidence/local-closeout-20261025/style-input-contract.json).

Current continuation (2026-10-26, actual completed execution): **PARTIAL**.

- Eight fixed model packages / 82 files / 46,803,197,151 bytes are now file-verified. The 8B FP8 package installation completed; it is no longer running.
- Five actual local calibration calls completed serially. Every lease was released, and the owned review process exited and was replaced between stages. No H3 job was resubmitted and production rows/gates were preserved.
- 8B positive and masked-face negative controls behaved correctly. The empty-street control fails the actual application style gate through `STYLE_*` codes; the private observer's case-sensitive category check was a false negative. The original receipt remains unchanged.
- The walking/sitting frozen-style conflict still falsely passed. One rejected sample also incorrectly treated a static camera as evidence against an uninterrupted take. These failures remain visible; **8B VLM is not published or production-qualified**.
- ASR actual fixed-line CER=0 qualified the current runtime. Revision `86702102-652d-4b03-ade2-6a6408617c91` is now `PRODUCTION_APPROVED`; this is technical recognition evidence, not voice identity or human listening acceptance.
- The owned ComfyUI was restored with its original launch arguments and unchanged overlay, verified idle (`RESTORED_IDLE_NO_RENDER_REPLAY`). API/Web remain available; no resident Worker was started.
- Software evidence remains 625 backend tests with zero failures/errors/skips; no application source changed in this continuation. Product-wide acceptance remains PARTIAL due VLM reliability, formal preproduction/human rights/voice/final viewing, and remote Actions billing/quota.

See [actual 8B calibration](evidence/local-closeout-20261025/vlm8b-actual-calibration.json). Earlier sections below are historical evidence, not the current qualification state.

10-26 最新实机续轮：完整后端 **625 项、零跳过/失败/错误**；Ruff 通过。前端沿用当前代码 TypeScript/Vite 与 17 类隔离 Mock 浏览器证据。
七个固定模型包 69 文件、36,197,624,599 字节已校验；真实原生 VLM 正反例、ASR 校准和认证发布完成。
后续更强的真实对抗性复核暴露 4B VLM 错误终审：曾漏报风格冲突，又误把匿名虚构人物解读为禁止可见五官/服装。
该 VLM 能力已正式 **DEPRECATED**；ASR 旧发布记录保留，但新适配器运行时需重新校准。真实预制作不把旧指纹当作当前合格能力。
两个独立真实 T2VA 样本均完成 20 步和逐帧完整解码（ComfyUI 墙钟 486.64/478.61 秒），但均未通过内容终审；不重放旧任务，不覆盖旧产物/判定。
第二例使用冻结风格一致的技术输入，并非已批准人物主资产或正式双人影片。所有模型已释放，活动租约为空；API/Web 可用，未启动常驻 Worker。
真实 Qwen 设计/克隆/新对白/识别/对齐已有证据；CER=0 与同句降速副本严格时间对齐不替代声音身份、真人试听或音色冻结。
用户自主授权以 user-delegated-codex 留账，不冒充人在场。**全产品仍 PARTIAL**：VLM 可靠性、正式预制作、M4 人工场景/权属/音色/最终观看试听与远端 CI 尚未关闭。
当前代码 CI 四项未启动，GitHub 注释明确为账户付款/额度原因；未修改付款设置。
见 [当前实机及失败证据](evidence/local-closeout-20261025/native-runtime-acceptance.json)、[逐项完成条件](LOCAL_ALL_PASS_REQUIREMENTS_20261026.md)。下文保留历轮结果。

前一续轮记录：完整后端 **474 passed**，隔离浏览器 **15 类 MOCK_VERIFIED**。
真实模型安装与指定失败对账仍待本人批准，输出补丁后的真实反例尚未复验，远端 CI 受账户付款/额度阻断。
**全产品仍为 PARTIAL**；逐项完成条件见 [全 PASS 核对表](LOCAL_ALL_PASS_REQUIREMENTS_20261026.md)。

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

### 7.8 安装 TLS 失败后的正式对账与连接容错（10-26 续）

`dfbc54c14a5fd97f63298b761388510a4cf61127` 的四项远端 CI 已全部 SUCCESS，
[run 40207758692](https://github.com/anjingdtl/shine-video-studio/actions/runs/40207758692)。
随后真人运行安装脚本，原计划取得正式一次性批准并开始执行。安装尝试
`b993f2d6-8906-4163-9479-22b03885be6c` 在首包 ASR 的权重连接处遇到
`SSL UNEXPECTED_EOF_WHILE_READING`。已缓存五份固定元数据，但没有已完成模型包或权重文件；
原尝试保留 FAILED_RECONCILE_REQUIRED，原计划 NEEDS_REVIEW。没有自动重试。

只读复查同一固定权重 URL：源站 HEAD 返回 302、获准 CDN HEAD 返回 200。
这表明复查时连通，不能冒充大权重 GET 成功。下载器现对**响应建立前**的 ConnectError/
ConnectTimeout 最多尝试三次；响应体错误不自动重放。保留已知 partial 供另行受控恢复，
错误只返回固定错误码与异常类型，避免把临时签名 URL 带入 API 错误或日志。

新增正式失败安装对账 API，要求操作员鉴权和一次性 `install_reconcile` grant。
在 SQLite 事务及各包 OS 文件锁下检查：原计划/注册包/嵌入清单哈希一致，无活动安装尝试，
无 journal/staging/rollback；目标目录只能缺失或与全部固定文件完全匹配，额外文件和篡改均阻断。
成功只记 RECONCILED_NO_RETRY，保留原始错误和观察结果，不改文件，不升级模型可用状态，不重试旧计划。
未知状态拒绝时不消耗批准令牌。新执行必须创建并另行批准新计划。

当前新安装提案 `95826b02-8a75-4937-95ba-083085958822`、原始哈希
`63abb7fd71249996e06144d04673979f53848f0281d9c699571c4f6245acdbbe`：
仍为同样 42 个文件、路径、版本、大小和跳转主机，增加每个串行模型阶段 **12000 MiB 的 ESTIMATED
准入预算**，用于首次本机运行前的显式资源配置。这不是实测消耗或资源验收；真实加载前仍检查物理空闲显存。
原 UNKNOWN 包及早先未批准提案均保留，未篡改其资源状态。

真人恢复脚本 `confirm_voice_model_recovery.ps1` 已准备并只读预览：先正式对账旧失败，
再批准并执行此新计划；不复用旧 grant，不扩展模型文件范围。尚未代签、对账或执行此步骤。

### 7.9 T2VA 页面入口与官方草稿准备

Web 原先将真实任务硬编码为 Ref2VA，并隐藏其他模式档案。现按用户明确选择的已注册 Profile
确定 mode，并在确认框和选项中显示该模式；没有选择或选项已失效时阻断，不自动换档案或删除引用。
隔离浏览器验证 T2VA/Ref2VA 的取消和确认参数，拦截全部提交后核对后台任务仍为空。
浏览器 13 类流程 MOCK_VERIFIED，api_errors=[]；不以该 UI 证据声称真实 T2VA 已运行。

已读取 Comfy-Org/workflow_templates 固定提交 `67984637eefcd4825dcf5c134e39034785be1682`
的 `templates/video_minimax_h3_t2v.json`，模板仓库为 MIT。源文件与许可证仅保留在被忽略的本机证据目录。
整理出 16 节点原生 20 步 T2VA API **草稿**：保留 INT8 ConvRot Base FL2VA 路径、原始帧数表达式，
显式使用 1344×768、5 秒，不使用 Turbo LoRA，不绑定首尾图。Prompt 输入保留编译器占位，未自由填写后提交。
当前 ComfyUI 节点、输入名及连接输出下标结构核对通过；这不等于 `/prompt` 接受或模型运行。

当前只缺 Base 权重 `minimax_h3_fl2va_pruned_int8_convrot.safetensors`。
Comfy-Org/MiniMax-H3 固定 revision `e5eb578a89295337b8ff433a035929ce0279e0b6` 中该文件
大小 20,970,379,616 字节，SHA-256
`e889202c41dafb67b10d67b97f0d8541508036a6090af23425a5c2615d03c47a`。
模型使用 [MiniMax H3 Community License 固定版本](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/42ed227ee7df40d41602854ae760620d6eb651fe/LICENSE)，
不能将模板 MIT 或 Qwen Apache-2.0 误用为该权重许可证。缺失权重及固定 LICENSE 的隔离安装提案
`006f5586-797e-442f-80a1-beb57ec29444` 保持 AWAITING_APPROVAL；没有下载权重、注册真实 Profile 或提交 H3。
原 Ref2VA 工作流和生产 ComfyUI 配置保持原状。

### 7.10 真实 VLM 反例发现截断输出；未将其算作通过

续轮服务监听均消失，旧 ComfyUI/验收服务 owner 已退出。逐个核对旧 PID/birth 后归档失效清单，
未终止复用 PID 的新 owner；按历史实际 argv 恢复原 8188 ComfyUI，`/system_stats` 核对
`--disable-async-offload --disable-pinned-memory` 仍在。验收 API/Web 已恢复，Worker 暂不启动。
首次反例准备在 ComfyUI 离线预检处停止，模型调用数为零，记 NOT RUN；没有重放模型请求。

恢复后进行一次新、明确的测试意图：沿用已校验视频和真实参考缩略图，仅在内存构造一条
“白色摄影棚正午的红色卡通恐龙，不能出现人、街道、夜景”的矛盾剧情。没有修改数据库中的生产 Shot、
Style、Asset、Artifact、Attempt 或门禁，未提交 H3。真实 VLM 输出被 1024-token 上限截断，
客户端 JSON 解析失败，因此该反例的语义结果**没有通过验收**。卸载复验仍为 loaded=false、
allocated/reserved=0；临时审片进程按持有句柄结束。调用
`ff6d7832-52ed-442d-af75-4d03ee8f5d1b` 保留 NEEDS_REVIEW，资源租约已 RELEASED。

本次测试封装漏传 reviewer_kind，导致该旧调用没有绑定释放档案。已通过现有正式提案接口固定原
`local-qwen3-vl-4b` 档案供真人对账；没有代签 ABANDON。今后 TrackedProvider 按实际 VLM/ASR
类型自动固定 Provider，封装是否显式传 reviewer_kind 不再决定该关键证据是否存在。

修复输出链：客户端明确请求 2048 tokens 并要求精炼、合并重复观察，但不得为篇幅遗漏独立问题；
本地服务接受 128–4096 的明确预算，返回 stop/length 和 token 使用量。长度截断、损坏 JSON 或不合 schema
全部拒绝作为 QC 证据，不补造闭合 JSON、不删除发现项或制造 pass。

新增“已完整收到 HTTP 响应但结果不合契约”的有类型错误，持久化响应 SHA-256、原因和不自动重试标记。
只有实际收到回复且卸载确认的这种确定失败记 FAILED；超时、未知提交及卸载失败仍保持 NEEDS_REVIEW /
RECONCILE_REQUIRED。既有不确定记录不会被此补丁追溯改成 FAILED，也不会自动重跑。
6 个新增软件回归覆盖这一区别、隐式 Provider 固定及预算边界，连同 semantic/runtime/serial/offline E2E
共 30 项通过。**此输出修复后的真实反例复验仍待旧调用的本人对账，不声称已运行。**

已将指定截断结果的对账并入同一个真人恢复脚本预览，再接安装失败对账和新固定计划批准。
三个动作分别走各自一次性 grant，不批准影片、音色或主资产；仅准备，尚未执行。

本续轮最终完整后端 **473 passed、0 skipped / errors / failures（202.547 秒）**，包含新增安装恢复、
响应分类和全部既有 V1.3/offline/film crew 测试。安装恢复定向 80 项、审片响应及旧 E2E 定向 30 项通过；
Ruff（含两个本地脚本）、TypeScript/Vite、显式 PyPI 锁文件核验通过。
浏览器曾因 label 的完整可访问名称匹配方式超时，修正定位后全部 13 类流程通过，未改弱业务断言。
具体命令、旧失败、当前新计划、真实反例的未通过结果与服务恢复见
[续轮验证记录](evidence/local-closeout-20261025/recovery-and-review.json)。当前全产品仍为 PARTIAL。

### 7.11 当前提交的远端 CI 账户阻断

工程修复及本节前的证据已推送 `0a3589127428bb1b48b3476771c8f67ccfb02d71`。
其 PR CI [run 40245130085](https://github.com/anjingdtl/shine-video-studio/actions/runs/40245130085)
四个 Job 均返回 failure，步骤记录为空、作业日志不存在。已逐项读取 backend、backend-windows、
frontend 和 web-e2e 的 Check annotations，四者均明确指出：账户近期付款失败或消费额度需要提高，
Job 因此没有启动，需要核对账户 Billing & plans。

这轮远端代码测试**没有运行**，不得将本机 473 项或旧 SHA 的绿色 CI 移作当前提交通过证据。
账户状态需仓库所属账户操作员处理，未修改账单、未移除 CI 门禁、未反复重跑未启动作业。
账户恢复后须对届时 PR 当前 head 重新执行四项检查；当前仍为 PARTIAL。
完整检查 ID、原始 annotations 和范围见 [CI 账户阻断证据](evidence/local-closeout-20261025/ci-account-block.json)。

### 7.12 页面安装恢复及跨项目迟到响应修复

补齐 `/comfyops/install-plans/{id}/attempts` 历史读取，并为计划列表增加可选项目过滤。
“模型与节点”现在只展示当前项目计划；可查看尝试，使用正式 `install_reconcile` 批准核验失败，
再从原固定包清单新建 AWAITING_APPROVAL 计划。旧错误及尝试记录保留，页面不自动重试或批准新计划。
模拟模式下对账按钮禁用；来源、包文件及运行验证等级不会因对账改变。

隔离浏览器构造了正常生产安装算法的 Fake NoNetwork 故障，使用测试操作员在页面完成上述流程。
验证旧尝试仅有一条、状态 RECONCILED_NO_RETRY，新计划尚无尝试，目标包目录不存在。
这证明接口/页面与确定文件状态核验连接正确，不是生产模型安装或真人对账已完成。

检查项目切换时又构造出合同反例：延迟旧项目素材响应，切换到空的新项目，再释放旧响应，
页面错误显示旧项目 **3 条素材**。保留此失败案例，随后为 App 素材/风格/镜头/任务、工作流档案、
审计加载及周期轮询增加当前项目归属检查；切换时清理已加载列表和旧预览/工作流选择。
相同延迟反例复验通过，新项目素材为零、旧镜头不可见，没有修改原始数据库或资产。
预制作列表还拒绝旧项目/旧页面请求的迟到结果。

最终完整后端 **474 passed，0 skipped / failures / errors（204.130 秒）**；
V1.3 preparation 定向 **59 passed**，Ruff、TypeScript/Vite、配置样例 JSON 检查通过。
最终隔离浏览器 **15 类 MOCK_VERIFIED，api_errors=[]**；保留首次项目切换失败与后续通过证据。
仅按已记录 PID/birth 重启本轮 API/Web 副本后，真实只读 API 的项目过滤/原失败历史读取通过，
H3 已连接，Worker 保持未启动，未触发生产任务。

配置说明纠正了“所有端点均未认证”的过时描述，补充运行时变量与惰性角色配置样例；
样例路径/零摘要/Torch 版本占位均需替换，12000 MiB 是示例准入预算，不自动启用、不批准模型或代替测量。
逐项需求、当前真实数据库批准缺口和后续顺序见 [全 PASS 核对表](LOCAL_ALL_PASS_REQUIREMENTS_20261026.md)，
精确证据见 [页面恢复验证记录](evidence/local-closeout-20261025/installation-recovery-web.json)。


## 7.13 明确授权后的真实审片反例与显式结论合同

用户明确授权离机期间自主推进。恢复与安装以 `user-delegated-codex` 审计，并记录授权来源及
`human_physically_present=false`；没有冒充原操作员、声称本人试听或自动晋升主资产。
旧截断调用 `ff6d7832-52ed-442d-af75-4d03ee8f5d1b` 已正式 ABANDONED，释放租约保留为 RELEASED。

补丁后对既有真实视频做错误故事探针（内存中要求红色卡通恐龙/白色午间影棚），先后得到两个反例：
模型摘要正确描述违规，却给出空 findings；第一次整体置信度为 0，融合逻辑仍通过；补整体置信度门后，
第二次置信度升为 1，仍以空 findings 掩盖明确违规。这两次实际回复与失败证据均保留，没有弱化探针。
先构造 4 个失败回归，再补低整体置信度阻断。随后增加新的 VLM wire 必填 `verdict` 和 `findings`，
去掉提示 Schema 中的空列表默认值；`failed/needs_review` 即使没有结构化 findings 也阻断放行。
缺失/null verdict 是已收到但不可用的回复，记录响应哈希、不得默认通过。历史 JSON 保留可读，不重写旧门禁。
整体置信度表示审查完整性，与单个问题的置信度分别处理；硬失败仍优先。没有用摘要关键词虚构 findings。

新合同下，真实错误故事探针返回 `verdict=failed`，列出身份、场景、风格三项硬问题；
原镜头故事的真实正常对照返回 `verdict=passed`、空 findings。用正常 named-gate evaluator 复核同一实际回复：
负例 technical passed、semantic/style/final failed；正例 technical/semantic/style/final passed；两者 audio 均不适用。
每次 allocated/reserved 归零，调用 COMPLETED、资源已释放；生产 job 状态/version、9 个尝试、70 道门、14 个 artifact
保持不变，H3 提交数为零。仅关闭这一受控正反例与误放行漏洞，不代表所有语义/声音/美学要求全通过。

## 7.14 大模型固定下载分段与本机回归

固定四包原范围仍为 42 文件、10,756,955,944 字节。旧 TLS/ReadTimeout/协议断开尝试逐个在 OS 锁下核验、
正式对账后才建立新批准计划；旧尝试没有重放。ASR、ForcedAligner、TTS Base 文件集已落盘，
VoiceDesign 仍在计划 `9a844b71-c4e1-48f8-9e1a-41ddb3bbf635` 下载中，尚无真实 Qwen 语音推理证据。

同一固定配套权重的只读诊断验证了 1024 字节和 64MiB 精确 Range：HF→指定 CDN 返回 206、区间/长度正确；
64MiB 完整读取消耗 4.047 秒。这些诊断不写安装缓存、不代表完整模型校验。
生产下载改为大文件最多 64MiB 一段，逐跳重验已批准主机，固定 identity encoding，核对精确区间与收取长度。
每段完成前最多 3 次流中断；仅在整段验证且文件推进后重置计数，请求数有固定文件大小决定的有限上界。
服务器忽略 Range 时必须从零提供完整对象；非法区间、超量、短段和 SHA 错误不自动重试。
最终仍须整个文件长度和固定 SHA-256 同时吻合才原子提升；没有放宽安装/模型调用的对账要求。

完整后端 **508 passed，0 skipped/failures/errors（203.346 秒）**；下载/准备定向 **102 passed**，
QC/正常 Worker/旧 offline E2E 定向 **49 passed**，Ruff、TypeScript/Vite 通过。
前端未修改，此前 15 类隔离浏览器仍单列为 MOCK_VERIFIED。当前远端 `6a72298` 四项 CI 未启动，
已逐一读取 annotations；账户包含额度耗尽，未变更付款/付费预算。工程通过不能替代当前远端 CI 或真实成片验收。
机器记录见 [正反例与分段下载](evidence/local-closeout-20261025/qc-verdict-and-bounded-ranges.json)。


## 7.15 续传固定块边界反例

进一步构造“每个连接仅交付短前缀就断开”的反例，发现按 `offset + segment_bytes` 重算终点会让目标不断后移，
即使累计前进也无法完成原块，提前耗尽预算。保留失败用例后，终点改为固定文件块的边界；重试只取该块剩余字节。
原先的 Range/长度/预算/整文件 SHA-256 检查均保留，没有通过扩大次数掩盖问题。

同一反例转绿，下载/准备定向 **103 passed**；完整后端 **509 passed，零 skipped/failures/errors**。
正在执行的模型安装未被中途重启，仍由其启动时的模块完成；固定边界补丁在下一次受控启动生效。
最后一个语音包仍在传输，尚不计为四包安装或语音质量通过。
`0aa25a4` 的四项远端 CI 均未启动，已再逐项读取 annotations；账户额度阻断未改变。
记录见 [固定块证据](evidence/local-closeout-20261025/fixed-block-download.json)。


## 7.16 四包文件完成、真实 SDK 与字幕接口修复

Qwen 固定计划 `9a844b71-c4e1-48f8-9e1a-41ddb3bbf635` 的尝试 `faacf044-87e5-4fa5-a7a6-81e15279f872`
完成 42 文件、10,756,955,944 字节大小/SHA-256 校验。首个真实 SDK 调用随后暴露环境过滤遗漏：
Torch 导入调用 Windows getpass，但子进程没有 USERNAME。CPU-only 配置/分词器预检复现相同 OSError，
未加载检查点或生成声音。保留隔离进程失败用例，补标准用户名变量，密钥变量仍拒绝传入；真实 CPU 预检转绿。
原失败调用 `1b682963-0952-430b-b152-8e816f213739` 在父子进程均退出后正式 ABANDONED，没有盲重放。

修复后的新意图实际完成 design/clone/prepare_conditioning/synthesize/asr/align 六阶段，其中 prepare 仅登记 PCM，
其余五次为实际模型推理。三个语音样本 4.8/5.12/3.28 秒、24kHz 单声道，完整 FFmpeg 解码通过，采样削波比例为 0。
识别新台词 CER=0；但对齐“的”字给出 1.12→1.12 秒，原探针按严格约束失败。这不是全通过样本，原 WAV 与时间戳保留。

又复现了官方对齐器省略标点、工作台要求锁定原文的接口差异。桥接仅在可听字符完全一致时恢复原文标点/空白，
字词时间戳逐项不变，原始 token 保存在 VoiceRender.alignment_evidence；漏字、符号改变和零长度时间仍拒绝。
没有删除反例、虚构时间戳或修改 ASR 原始转写。

一次明确记录的 CPU 技术派生对同一合成 WAV 做 atempo=0.75，保留原文件/来源 SHA/FFmpeg SHA/参数和派生 SHA，
不是新的主资产或隐式的原生语速功能。派生时长 4.3460416667 秒；再用真实 Qwen ASR/ForcedAligner 串行复验，
完整原文保留、每段 start<end 且在实际时长内。原未降速时间戳仍被 verify_dialogue 拒绝。
剩余 ASR_REVIEW_REQUIRED/VOICE_REVIEW_REQUIRED 明确保留：识别器不提供校准置信度或声音身份，用户未本人试听，
未晋升候选、冻结音色或通过口型/美学验收；SDK 对非中性情绪及原生变速仍明确不支持。

所有新子进程实际退出、资源租约已释放，GPU 回到约 1.5GiB 桌面/服务基线。
完整后端 **520 passed，零 skipped/failures/errors（196.712 秒）**；SDK/字幕/准备定向 **80 passed**，Ruff 通过。
T2VA 原尝试连接失败后按原清单核验，1024 字节精确 Range 再次成功；新固定范围计划继续下载，尚无新 H3 生成。
当前仍 PARTIAL；见 [完整机器记录](evidence/local-closeout-20261025/real-qwen-sdk-and-alignment.json)。


## 7.17 独立审片/ASR 执行器文件指纹（软件与 CPU 文件层）

- 真实门禁核对发现：现有能力登记只接受 ComfyUI 历史，独立媒体执行器没有相应的原生证据路径。
  本步先补其必要的文件/运行时身份链路，未以虚构 ComfyUI 图、直接改库或关闭 enforce 填补空缺。
- 文件指纹由实际校验的相对路径、SHA-256、字节数计算，不再把操作者提供的 manifest 标签当作文件集合证明。
  文件别名重复、声明大小错误及实际内容变化均拒绝；每次冷加载也重新核验，防止启动后文件被换掉却仍返回旧指纹。
- VLM/ASR 实际响应携带类型化执行器身份，已完成调用才写入账本；响应与指定模型不符、畸形指纹或
  Mock 响应声称真实身份均明确失败，不重试推理。没有扩展字段的兼容端点不获得此类能力证据。
- 构造反例还复现了外层 Mock 标记被内层 VLM JSON 覆盖的问题；现已保留两层来源的单调性。
  定向软件回归 39 项通过，包含 Mock HTTP、实际 API/SQLite 账本、加载前文件替换和资源释放断言。
  完整回归以本报告页首及机器记录为准；测试中的模型、解码器和传输均为隔离替身，未冒充真实推理。
- 本机原有 VLM 14 文件、4,314,817,300 字节；ASR 13 文件、155,454,810 字节已实际重新校验。
  固定上游树逐文件核对后，VLM 13/ASR 12 个上游文件均大小及哈希匹配；本机附加 configuration.json、
  source-audit.json 未冒充上游文件。原文件和既有生产数据保持原样。
- 运行中的 T2VA 安装尚未结束，未为部署补丁重启 API/审片服务；本步没有新推理，没有改写旧调用。
  原生 Workflow 能力登记、固定角色绑定与新的带指纹校准仍待后续完成，完整产品仍为 PARTIAL。


## 7.18 原生审片 / ASR 能力与冻结生产绑定

- 补齐同一 Workflow Registry 内的类型化 native_provider：固定角色、模型、URL 摘要、已登记包、
  运行文件集合与执行器指纹；不伪造 ComfyUI 图，不支持借此登记 H3 或丢弃引用。
- 真实静态验证读取无推理的 `/identity` 实时复验；能力采纳只接受本项目已完成、非 Mock、租约已释放的调用。
  VLM 要求不同输入的明确正/反例及高置信度硬反例；ASR 要求实际文本校准。返回结果摘要必须和账本一致。
  `workflow_validate` 与发布各消耗单次快照授权；发布后角色、包、运行时或调用变化都会阻断准备检查。
- 仅有真实 ComfyUI 审片图但未绑定正常 Worker 实际提供者的记录，不再满足生产预制作门禁。
  Worker 在 H3 释放后、每个实际审片子调用的前后再次核对任务冻结版本、路由和返回指纹，防止长渲染期间换模型。
  已收到但被拒绝采用的结果摘要仍留账；包状态撤销、工作流或包内容与记录 SHA 不符也会在调用后阻断。
  6 个变更期间反例曾确实失败，修复后通过；能力匹配也拒绝已经变更的角色，未以历史发布状态假报当前可用。
  未知提交与释放失败保持单独对账，不重投 H3。
- Web 增加原生验证 / 校准入口并隐藏不适用的 Fake 控件；修复 `job_id` 被误送普通 History 路径的问题，
  保留 H3 强证据入口。JSON 对象和项目范围先验证再申请授权，证据编辑区扩展为可读宽度。
- 当前完整后端 **589 passed，零失败/错误/跳过，222.990 秒**；定向回归结果见机器记录（包含 V1.3、旧 offline E2E、
  film crew 与新 48 项原生/生产绑定反例），Ruff、TypeScript/Vite 通过。
  隔离浏览器 **17 类 MOCK_VERIFIED，api_errors=[]**；原生与 H3 路由探针被拦截，未登记真实能力或提交影片，
  截图已实际查看。首次浏览器定位暴露了编辑区缺少稳定可访问名称，修复后完整复验通过。
- 本步仍未重启正在进行固定 T2VA 安装的验收服务；未运行新指纹校准、发布新真实 Workflow 或冻结本项目清单。
  两个既有审片模型的固定隔离包提案已准备；安装须等当前尝试终止后串行执行。
  另查得历史调用 e6d718c1-ad78-43a1-9d48-b048332f4cdd 仍 NEEDS_REVIEW：原进程已死、租约已 RELEASED，
  后续仍须明确释放目标和正式 ABANDON，不以资源释放冒充调用通过。
- 机器记录：[原生能力记录](evidence/local-closeout-20261025/native-review-capabilities.json)。
  M4 人工场景/权属/试听观看、当前项目真实准备闭环及远端 CI 边界均未关闭，完整产品仍为 PARTIAL。


## 2026-10-26 后续实机结果与场景审片修复

- 七个固定模型包的 69 个文件、36,197,624,599 字节已实际校验。T2VA Base INT8、原生 Qwen3-VL 与 Whisper 的受控安装完成；包文件校验状态与运行能力分别留证，不手改包验证等级。
- 原生 VLM 正例通过、硬反例失败及 ASR 八字逐字一致，三个新调用均真实完成、租约释放。VLM/ASR 能力已通过认证委托发布（5c49f760… / 31db7d50…）；项目的 PreProductionPlan 尚未冻结。旧 e6d718c1… 已正式 ABANDONED，不重放旧调用。
- 新 T2VA Job `90370ed1-595c-499f-ab3c-02f2fa8b430a` 经编译器、真实 Profile、Worker 和正常审片：20 步，ComfyUI 墙钟 486.64 秒，1344×768/24fps/5.167 秒，3,904,601 字节，完整解码通过。它的空景/静态广角要求与原冻结风格冲突，且出现车辆/标识；style/final 失败、REPAIR_PENDING，旧产物和判定保留。此处不是合格影片，也不是 M4 验收。
- 实测暴露空景语义门错误地记为不适用，以及可选门已观察到的硬错误/不确定/Mock 来源未完整参与终审。六个反例先失败再修复；启用语义 QC 时所有故事场景都要求审片，实际观察到的错误不能因可选配置被隐藏。
- 审片提示明确 CUT 指镜头之间的转场，不能要求单镜头内部必须剪切；未降低置信阈值或过滤已返回的失败。新实机复核另留记录，不覆盖旧审片。
- 当前完整后端 **595 项，零失败/错误/跳过**，定向质量/离线 E2E/V1.3/评审回归 **53 passed**，Ruff 通过。前端未在本步改动，沿用当前提交的 TypeScript/Vite 与 17 类 Mock 浏览器通过证据。
- GitHub 当前 4eba47f 的四项 CI 均未启动：账户付款/额度限制；没有更改账单或将本机测试冒充远端 CI。人工场景、权属、音色冻结、最终观看试听与正式项目预制作仍待真实证据，整体 PARTIAL。
- 可复核机器摘要：[实机安装、校准、发布及 T2VA](evidence/local-closeout-20261025/native-runtime-acceptance.json)。自主授权记录为 user-delegated-codex，不冒充人工在场或内容批准。


### 单镜头审片的转场语义输入

真实 CUT 复核第一次仍误判内部剪切，说明仅补充文字说明不足；该失败完整保留，未把它记为修复通过。现在仅在发送给单镜头审片器的视图中，将机器枚举 cut 表达为“独立镜头、无前镜头帧约束”，不改原 ShotSpec、编译器、引用绑定或历史记录；last_frame 等其余约束不变。没有角色/资产绑定本身也不构成身份或服装违规，已有必需身份缺失仍需披露。最新提示变更定向 55 项及 Ruff 通过；上面的全量 595 项对应前一提交。后续真实结果单独记账。


## 真实审片对抗性复核与资格撤下（2026-10-26）

- 第二个独立 T2VA Job `41f20ac6-6d8b-4912-8f8b-60095c709410`、Attempt `ab1ea473-e2b1-4ed5-97d0-0b95aa0934b7`、Comfy prompt `2e0667f9-965f-4f4f-887d-d638911147b1`：1344×768、24fps、5.167 秒、1,554,281 字节，SHA-256 `a176267078f107bd8a39f4678a57f6445dc2ab5c12065bc0e4108d0fff48224a`。技术门/完整解码通过，真实 ASR 已运行；semantic/final 失败、style 需复核，仍 REPAIR_PENDING，未自动重投。
- 真实审片把没有指定人物身份或服装目标的匿名虚构人物判为必须没有五官/衣物，并错误解释单长镜头/转场。调整规范说明和 system/user 指令边界后，现有视频的两个独立新校准调用仍复现误判；这不是已解决的模型质量问题。旧风格冲突视频的一次复核也曾错误通过，记录保留，未写回生产五道门。
- 新代码明确单镜头与跨镜头语义、缺少绑定不等于禁止面部/服装、冻结 Style 独立检查；没有降低阈值、删失败 findings、改冻结风格、修改历史 ShotSpec 或人造人工批准。正确输入契约不能证明小模型已经服从它。
- VLM Workflow `5c49f760-5132-4cf9-8fbb-1f9e243bb4ff` 经 workflow_deprecate 单次快照授权正式撤下，不能再被真实 Factory 匹配。ASR `31db7d50…` 保持 PRODUCTION_APPROVED；历史真实调用、先前发布事实、全部旧 Job/Artifact/门禁均保留。正式预制作因缺少合格 VLM 保持阻断。
- 最终所有租约 RELEASED，审片服务 loaded/asr_loaded=false，allocated/reserved=0，ComfyUI queue_running/queue_pending 为空。操作发生于隔离验收副本；未改原 ComfyUI 模型/工作流文件，本次撤下未再安装其他模型或换远端服务绕过质量问题。
- 当前限制需要更可靠的本机审片模型或更完整、能通过这些固定反例的校准；不得用基础恐龙反例的通过覆盖这里的失败。正式人物、权利与声音批准及 M4 人工观看边界仍独立存在。

最终提示范围修订后再次运行完整后端：595 项、零 failures/errors/skipped，231.840 秒；Ruff 与 git diff --check 通过。真实媒体质量失败及 VLM 撤下资格不因软件回归通过而改变。


## 官方较大 VLM 候选与分片模型支持（2026-10-26）

- 已在已登录 GitHub 账户页面实际核对：Actions 2,000/2,000 分钟、0.5/0.5 GB，免费额度用尽，超额预算为零并显示 Usage blocked；未改变预算、支付信息或仓库可见性，也没有为此重跑无法启动的 CI。旧 Qwen 恢复脚本没有重放。
- 为继续解决审片质量问题，准备官方 Qwen3-VL-8B-Instruct-FP8 固定 9cdc6310… 候选，13 文件 10,605,572,552 字节。显存 14,000 MiB / RAM 22,000 MiB 均为 ESTIMATED，不是实测值；当前尚未开始权重安装或新模型推理。
- 本机执行器补齐严格分片索引校验：索引和每个分片全部纳入哈希身份链，不允许未声明权重、缺片、多余分片或单文件/分片混用。九个反例先失败，修复后定向 44 项通过。完整后端 **604 项、零失败/错误/跳过，232.678 秒**；Ruff 通过。未改前端，沿用已记录的构建和 17 类 Mock 页面证据。
- 上游词表 GET 超时后，以原有相同词表重现上游 JSON 编码；仅在 4,957,462 字节和固定上游 Git blob ID 完全匹配后用于精确缓存。没有修改词表含义、权重或安装范围。其余小文件同样核对固定 Git blob ID，权重由上游 LFS SHA-256 固定。
- 后续只允许串行安装和对抗性校准；更换适配器运行时也要求 ASR 重新校准/发布，不能继承旧指纹。旧 VLM 仍 DEPRECATED，项目预制作没有冻结，完整产品仍 PARTIAL。
- 机器记录：[分片与候选资格记录](evidence/local-closeout-20261025/sharded-review-qualification.json)。


## 较大原生模型预算与校准租约一致性（2026-10-26）

- 新增 7 个反例先复现失败：原生 binding 拒绝合法分片；固定小预算无法表达较大模型；较小的旧租约可能被用于较大的新能力；已收到模型结果后活动租约预算变化未被拒绝。
- 原生包和工作流预算现在严格一致，保留 VLM 8,000 / ASR 4,000 MiB 最低估算和 16,384 MiB 上限。当前角色的有效 STATIC_VALIDATED/REAL_TESTED/PRODUCTION_APPROVED 声明为实际配置的本地审片器提供保守准入预算；静态声明仅供校准准入，不意味着生产批准。
- 真实执行证据采纳要求已释放租约覆盖工作流预算，生产审片前后也检查活动租约是否仍足额。收到但因后置检查被拒绝的结果摘要保留，不触发重投。分片格式仍由执行器对索引和全部实际文件逐字节复验。
- 完整后端 **611 项，零失败/错误/跳过**；Ruff 与差异空白检查通过。定向验证通过，不改动下载器、前端、数据库结构或已有数据。新的预算代码在受控下载期间完成软件验证，后续服务重启及实机校准使用新版本，不能把旧适配器指纹继承给新运行时。
- 固定官方候选已登记并批准隔离计划 `1b2b9d23-d884-4d4d-bbdd-3450a1585ca8`，唯一安装尝试 `2663f819-f427-41b5-b7a0-6ccf470a7e2b` 仍 SUBMIT_INTENT。累计接收约 1.92 GB，仅为下载进展，未完成文件校验或推理。不得重放旧安装或未知审片。
- 本次初始服务重启已改变适配器运行时指纹。旧 ASR 发布记录保留，但不再据其旧指纹认定当前可用；下载完成后将正式弃用旧运行时版本并重新实际校准、发布。旧 4B VLM 已 DEPRECATED，新 8B 尚无生产资格，项目预制作仍阻断。
- 已准备只停本任务持有的 ComfyUI PID+OS birth 的串行释放/恢复工具；执行前必须完成安装、核验空队列/租约、保存已有两次完整 History 和校验归档。归档不冒充实时能力证据，不重放历史渲染，不改原模型/工作流文件。当前尚未执行停机。


## 校准输入设计复核（2026-10-26）

重新逐字段核对发现，旧 A (`7a526327…`) 的 Story/camera 是行走、中景、缓慢推进，而其冻结 Style 文案是坐姿、中近景、轻微手持。这个未消解的输入差异使它不适合作为“严格符合该冻结风格”的无争议正例。旧模型实际返回 passed 的事实和旧证据仍保留，但不能据此评价较大模型必须通过；此前 VLM 资格已经撤下，未恢复。

尚未执行的新模型校准设计已纠正：采用输入与冻结风格一致的匿名虚构人物 T2VA 样本作为正向候选；同一视频的内存评估规格加入完全遮挡面部的白色面具要求作为明确服装反例；空街和旧 A 行走请求分别作为冻结风格冲突观察。所有原 Job/ShotSpec/Style/门禁保持原样，未生成新人物主资产或批准新道具。正向候选若失败，要先区分真实生成缺陷和评审误判，不能为了凑正例修改冻结风格或删除 findings。最终校准和真实影片验收仍未完成。


## 本机加载闭包与重复 CI 修复（2026-10-26）

- 实际检查隔离 Transformers 加载器确认：它优先选择单文件权重，再选择分片索引；PEFT 配置还可能把加载路径改到另一基座。因此仅校验清单内文件不足以证明实际加载源固定。
- 新增根目录加载输入检查，拒绝清单外权重、词表、模板和 JSON 配置；Windows 的词表文件名按大小写不敏感识别。当前完整模型执行器不支持可跳转未验证外部基座的 PEFT 布局，即使其 adapter_config 已列入清单也明确阻断。已有本机 source-audit/configuration 附注保持非加载元数据边界，没有删除或更改任何模型文件。
- 13 个新增阻断反例先失败、修复后通过，另有 1 个良性附注兼容回归；VLM/ASR 冷加载都在调用模型前重新检查。定向 **59 项通过**，完整后端 **625 项、零失败/错误/跳过**；Ruff 通过。以上是隔离结构/Mock 合同验证，不冒充新模型推理。
- CI 原先对全部分支 push 和 PR 都运行，导致同一 feature 提交重复四项检查。现在仅 main push 与 PR 触发，并取消同一 PR 的过时运行；已实际解析 YAML、对比四个 job 全部内容和只读 permissions，均未改动，Windows 保留。额度已耗尽的账户不会因此自动恢复，仍未声称远端 CI 通过。
- 本次固定 8B 两个分片已在下载缓存完成完整校验；唯一安装尝试尚在隔离复制/最终验证，不是整体安装或运行验收通过。不得因所有字节接收完毕而跳过后续终态、进程和实机校准。
- 已准备逐次校准后的进程退出：确认调用完成/租约释放/模型卸载后，按 OS birth 停止本任务审片进程，记录 Windows 工作集与私有提交峰值，再启动相同冷服务。仅在辅助进程自身做了内存计数读取自检，尚无新 VLM/ASR 的峰值或结果，不把 Windows 进程数据当作 GPU 峰值。
