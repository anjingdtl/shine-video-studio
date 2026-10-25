# 本轮本机收尾补充报告（2026-10-25）

基线：`7a81aa3`；功能分支：`codex/local-final-closeout`。本报告补充此前
[本机收尾报告](LOCAL_V1.3_CLOSEOUT_REPORT.md)，不改写其历史测试结果。

**结论：工程修复与「真实文本剧组 → 已签署真实 H3 镜头 → 剪辑提案 → Web 装配/播放/下载」闭环通过。完整产品验收仍为 PARTIAL。**
本轮没有重渲染 H3，也没有新增人类审批；使用既有三镜头及原先逐项签署的审片证据。
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
