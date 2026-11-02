# 历史验收报告（已被 2026-11-02 暂停版取代）

> **当前有效结论：`PARTIAL`，本轮按用户要求暂停；不得签发 `REAL_USER_JOURNEY_ALL_PASS`。** 本文件正文记录的是更早一次验收轮次，包含旧源码 SHA、旧项目和旧测试数字，不是 2026-11-02 当前工作区的验证结果。请以 [当前进度与交接文档](REAL_USER_JOURNEY_PROGRESS_20261102.md) 和 [当前测试报告](REAL_USER_JOURNEY_TEST_REPORT_20261102.md) 为准。旧轮次证据只能作为历史背景，不能替代当前源码复验。

---

## 以下为历史轮次正文，保留作审计记录

# Shine 视频工作台真人模拟验收报告

**结论：`PARTIAL`，不得签发 `REAL_USER_JOURNEY_ALL_PASS`。**

- 验收日期：2026-11-02（Asia/Shanghai）
- 验收基线：`61bb50e0b34fe35a43a85ff2d569f33149ab9828`
- 验收源码提交：`2b70d77500157801edc802ccc8d9970a6309b012`（包含本轮已测试的应用代码）
- 报告更新提交：本文件回填后的后续文档提交；只更新验收信息，不改应用代码
- 本地证据目录：`.local-acceptance/real-user-journey-final/`（被 Git 忽略，不进入发布包）
- 隔离应用数据：`.local-acceptance/real-user-journey-final/data/`
- 远端 CI：`WAIVED_EXTERNAL_BILLING`
- 真实作品的版权、艺术质量及发布授权：`PENDING_HUMAN`

## 对七个验收问题的回答

1. **普通用户现在能否独立从启动做到一部 AI 短剧？** 还不能。本轮完成了新项目、剧本导入、风格建议与冻结、真实 LLM 分镜请求以及已有安装识别；分镜链在创建镜头前正确阻止，因为隔离作品没有经授权可用于测试的人物和场景主资产。ComfyUI 目录中找到一份 H3 Ref2VA 三段式前端工作流，但它不是工作台可直接提交的 API 工作流；运行时、节点版本和参数映射也未完成兼容性核验，因此没有真实 H3 任务。
2. **哪些链路是真实的？** 浏览器操作经过 Web → API；MiniMax M3.1 Flash 真实返回了风格建议与编剧 Agent 结果，记录均为 `is_mock=false`。向导通过 Web UI 将发现的 ComfyUI 和模型明确确认成“已安装”，并复用已有 SHA-256 核验结果；没有复制、下载或重复读取模型。没有 Web → 常驻 Worker → H3 → MP4 的链路，本轮未提交视频任务。
3. **本轮发现和修复了什么？** 修复长时间模型核验被 60 秒前端超时截断、Windows PowerShell 5.1 进程清单解析、剧情事实单行输入、单份已导入剧本未自动选中、中文风格建议返回英文、1280×720 剧情事实输入框过窄，以及安装资料仍要求操作员密码等问题。后端全量测试第一次运行还暴露 `stop_shine.py` 移除 `UNKNOWN` 导入造成恢复测试失败；恢复兼容导出后重跑结果见下方测试矩阵。
4. **哪几处曾让模拟用户停住？** 模型哈希校验超过前端等待时间时，页面先报超时但后台已完成；刷新后单份剧本未选中且提示不清；剧情事实框只能输入单行；风格建议用英文。修复后在浏览器里复验了缓存核验恢复、自动选择剧本、多行事实提交及中文风格建议。素材缺失仍是实际阻断，系统给出补充已批准角色素材的中文原因。
5. **最终影片来自本轮源码和本轮生成吗？** 本轮没有最终影片，也没有原始 H3 镜头、EDL、交付 MP4、视频哈希或浏览器解码证据；不能声称出片或播放通过。
6. **关闭浏览器、重启软件和异常退出后任务安全吗？** 关闭并重开隔离浏览器后，项目、剧本与风格冻结仍存在；重新进入导演制作页可见导入剧本。隔离 API/Worker/Web 通过项目进程身份清单启动和停止，停止后 8110/5191 端口释放。因为没有 H3 生产任务，本轮无法以真实生产任务验证浏览器关闭、Worker 崩溃、ComfyUI 崩溃、第二镜头失败、租约释放或成片响应丢失后的恢复。
7. **剩余限制是什么？** 没有可合法用于隔离验收的人物/场景/声音主资产；已有 ComfyUI 的 Python、节点修订及实际服务契约未验证；隔离数据中没有可用的正式 H3 Workflow Profile。真实 H3、多镜头、ASR/VLM、返工、EDL/FFmpeg、播放和导出因此未运行。发行包已从验收源码提交构建并逐文件 SHA-256 验证；没有双击其中的 `.cmd`，因为它会调用 Windows 默认浏览器，而本轮浏览器 Profile 必须隔离且不能改动用户日常 Profile。

## 验收矩阵

| 范围 | 状态 | 证据与边界 |
|---|---|---|
| Windows 安装器只读环境检查 | `PASS_SOFTWARE` | `install_once.ps1 -CheckOnly` 检查 Windows、磁盘和固定版本工具链；未写文件。它不等于双击安装。 |
| 正式发行包 `.cmd` 首次安装 | `NOT_RUN` | 已构建并校验发行包，但没有双击 `.cmd`；该入口会用 Windows 默认浏览器打开向导，本轮只使用隔离浏览器 Profile，未改动用户日常 Chrome Profile。 |
| 正式发行包构建及哈希检查 | `PASS_SOFTWARE` | `build_test_package.ps1 -Verify` 从源码提交 `2b70d77500157801edc802ccc8d9970a6309b012` 构建；438 项清单经 SHA-256 校验。包内无模型权重、视频、SQLite、`.env` 或操作员凭据文件。 |
| 隔离栈脚本启动/停止 | `PASS_SOFTWARE` | `start_shine.ps1` 在隔离数据目录启动；API `/health` 返回 200；`stop_shine.py` 按 PID 创建身份停止，进程清单移除且 8110/5191 端口释放。最终停止证据见 `logs/isolated-stack-stop-final.log`。启动由工程工具触发，不冒充双击 `.cmd`。用户原有 8000/5173 服务没有停止。 |
| 首次体验与演示/真实入口 | `PASS_REAL_USER` | 首次空项目、演示标识及“启用真实制作”入口可见；演示页明确表示不生成真实影片。 |
| 真实环境激活 | `BLOCKED` | 向导明确显示 ComfyUI 与模型“已安装”，随后以 `EXISTING_INSTALL_RUNTIME_ADOPTION_REQUIRED` 阻止真实激活；阻断原因是运行兼容性与固定节点版本尚未验证。没有把已有安装标成缺失，也没有建议重下或接管。用户目录有 H3 Ref2VA UI 图，但未注册 API Workflow Profile。 |
| 已有 ComfyUI 与权重识别 | `PASS_REAL_USER` | 通过“启用真实制作”→“核验已安装环境”实际操作；界面显示 `INSTALLED_VERSION_MATCH`、约 41 GB 已核验、0 项哈希失败、0 字节复制、0 字节下载，并明确“已恢复此前完成的核验结果，未重复读取权重”。此前 10 个固定组件文件哈希匹配，43,978,717,531 字节。H3 软件版本匹配，但运行时未验证。证据：`screenshots/52-wizard-confirmed-existing-install.png`、`logs/wizard-existing-install-confirmation.json`。 |
| 真实 MiniMax LLM | `PASS_REAL_MODEL` | 通过 UI 配置用户提供的 MiniMax 凭据；MiniMax M3.1 Flash 风格建议和编剧 Agent 请求真实成功，`is_mock=false`。密钥未进入仓库或验收日志。 |
| 创建作品、输入剧本、刷新与重开 | `PASS_REAL_USER` | 通过 UI 创建《Shine真人模拟验收短剧》，从文件选择器导入 547 字符剧本；浏览器关闭重开后项目和剧本仍在。 |
| 素材绑定与正式资产批准 | `BLOCKED` | 隔离作品为 0 个资产；没有可确认允许本轮使用的人物/场景/声音主资产。没有读取或复制正式数据库中的私人素材，没有伪造资产。 |
| 风格 AI 建议 | `PASS_REAL_USER` / `PASS_REAL_MODEL` | 实际 MiniMax 返回中文建议；在 1920、1366、1280 视口复验页面。 |
| 风格冻结 | `PASS_REAL_USER` | 隔离作品通过 UI 冻结 V1；该次操作是明确隔离的自动化模拟操作，不是用户对真实作品的艺术批准，人工发布批准仍为 `PENDING_HUMAN`。 |
| 五 Agent 完整分镜 | `BLOCKED` | 编剧 Agent 的真实 LLM 调用成功；系统随后因缺少已批准并锁定的人物主资产而返回 `BLOCKED_MISSING_MASTERS`，其余 Agent 没有被假报成功。 |
| 现有 H3 工作流到工作台 API 合同 | `BLOCKED` | 用户目录文件为含 95 个节点的 ComfyUI UI 工作流，没有 API `class_type` 节点图；工作台隔离 DB 没有经服务验证的 Workflow Profile。未将 UI 图伪装成 API 工作流或自动编造参数映射。 |
| 真实 H3 单镜头与三镜头序列 | `NOT_RUN` | 没有发送 H3 请求；运行时兼容、Workflow Profile 和可用主资产均未齐备。GPU 未用于 H3 推理。 |
| ASR/VLM、技术 QC、自动返工 | `NOT_RUN` | 没有本轮视频供其处理；未生成假 QC 结果。 |
| EDL、FFmpeg 装配、成片中心、播放和导出 | `NOT_RUN` | 没有本轮原始镜头和成片 MP4；未做文件存在性冒充播放器验证。 |
| 刷新、关闭并重开浏览器 | `PASS_REAL_USER` | 独立浏览器 Profile 重开后，项目保留；点击“AI导演制作”后剧本自动选中并可见。首页恢复到作品概览，导演子页需要用户再次进入。 |
| 1920×1080、1366×768、1280×720 UX 检查 | `PASS_REAL_USER` | 截图与测量显示无水平溢出；1280×720 的事实输入框修复后为全宽。 |
| Python 后端全量测试 | `PASS_SOFTWARE` | 第一次运行 1 项失败；恢复兼容导出后重跑完整 `backend/tests`：1387 passed / 1 skipped。 |
| 前端全量测试与构建 | `PASS_SOFTWARE` | Vitest 12 个文件、75 项通过；`npm run build` 中 TypeScript 与 Vite 均通过。 |
| Ruff | `PASS_SOFTWARE` | `ruff check backend` 通过。 |
| Alembic 隔离数据库升级/一致性 | `PASS_SOFTWARE` | 隔离 DB `upgrade head`、`current` 到 `f150a0030001 (head)`、`check` 无新操作。正式数据库未接触。 |
| 真实用户完整旅程总判定 | `PARTIAL` | H3/成片/人工审片闭环未完成；不得提升为全 PASS。 |
| GitHub Actions 额度 | `WAIVED_EXTERNAL_BILLING` | 不作为本地验收阻断项。 |
| 真实作品版权/艺术质量/发布授权 | `PENDING_HUMAN` | 未模拟为真实人工签字。 |

## 缺陷记录与修复验证

| 编号 / 等级 | 用户复现步骤与影响 | 根因和修复 | 回归证据 |
|---|---|---|---|
| UX-01 / P1 | Setup 页开始校验大型现有模型后约 60 秒显示失败，但后台校验已完成；用户可能重新扫描。 | 前端请求等待上限改为 1 小时；后端按完全一致的安装快照从审计结果恢复，不复制、不重读权重。 | 截图 28–30；浏览器复验恢复已完成结果，显示 10 项匹配和“未重复读取权重”。 |
| WIN-01 / P1 | Windows PowerShell 5.1 启动/停止隔离栈后，进程清单可能不是 PID 身份数组，造成无法安全停止。 | 启动脚本直接解析完整 JSON；停止脚本兼容历史包装数组，只对 PID 与创建身份完全匹配的进程执行停止。 | `isolated-stack-stop.log`；隔离栈启动/停止后进程清单移除且端口释放；`test_stack_operations.py` 11 项通过。 |
| UX-02 / P2 | 导入单份剧本后刷新，再点 AI 分镜会提示“导入或粘贴”，没有指出已有剧本也需要选择。 | 只有一份导入剧本时自动选中；多份时用明确提示要求选择。 | 截图 41–43、51；浏览器刷新/重开复验，剧本自动选中；前端回归通过。 |
| UX-03 / P2 | “锁定剧情事实（每行一条）”原为单行文本框，换行会丢失。 | 改成多行文本框并按行提交；1280×720 下改为独立全宽布局。 | 截图 39、49；UI 中两条事实分别进入请求，三个视口复验。 |
| UX-04 / P2 | 中文用户收到英文的风格方案，难以判断色调、镜头调度和节奏。 | 仅 StyleProposal 的面向用户字段要求简体中文；H3 视觉提示词继续使用英文规则。 | 截图 34、44–45；真实 MiniMax 返回中文；未自动采纳建议或覆盖冻结风格。 |
| DOC-01 / P2 | 安装说明和 Build Info 仍称需生成并抄录操作员密码，与本机免密流程冲突；安装说明也容易让用户以为一定会重下模型。 | 更新安装器文案、包元数据和 tester 文档，明确先检测/确认现有安装；兼容性未验证时停下，只有用户确认新建环境才下载缺项。 | Windows package entrypoint 针对编码、入口、免密及复用说明的回归测试通过。 |
| REG-01 / P2 | 第一次全量后端测试中恢复测试无法导入 `shine_stop.UNKNOWN`。 | 保留 `UNKNOWN` 模块导出，兼容既有故障注入调用。 | 单测 `test_stack_operations.py` 11 项通过，随后重跑全后端套件。 |

### 用户困惑点清单

- **已修复：** 等待较久后 UI 显示“失败”但后台仍在校验；现在可等待并恢复同一快照结果。
- **已修复：** 只有一份已导入剧本时刷新后没有自动选中；现在进入分镜页自动选中。
- **已修复：** “每行一条”的剧情事实只能在单行框输入；现在可多行录入并在小窗口全宽显示。
- **已修复：** 中文风格建议以英文返回；现在给用户的风格提案使用简体中文。
- **仍阻断真实制作：** 向导已按“已安装”确认现有 ComfyUI 和 H3 权重，复用校验且没有重复读取、复制或下载；只有运行兼容性与固定节点版本未验证，因此以 `EXISTING_INSTALL_RUNTIME_ADOPTION_REQUIRED` 停下，不会冒险接管或另下权重。普通用户可见阻断原因和专业设置入口，但此安装暂时没有可在当前向导内完成的自动运行验证路径。
- **可恢复但不完全：** 浏览器重开后工作区返回作品概览；项目、剧本、风格仍保留，用户需再点一次“AI导演制作”回到分镜页。

## 本地证据清单

证据均在 `.local-acceptance/real-user-journey-final/`，不会被提交或打包：

- `traces/real-user-journey.zip`、`traces/browser-reopen-resume.zip`：隔离浏览器 Playwright Trace。
- `screenshots/01-empty-setup-1920x1080-css.jpg`、`03-detected-comfyui-installation-1920x1080-css.jpg`、`30-verification-result-recovered-in-ui.png`、`34-real-minimax-style-proposal.png`、`35-style-frozen.png`、`37-screenplay-imported-through-file-picker.png`、`43-real-llm-storyboard-result-1366.png`、`45-style-chinese-proposal-result.png`、`46-storyboard-1920x1080.png`、`47-storyboard-1366x768.png`、`48-storyboard-1280x720.png`、`49-storyboard-facts-full-width-1280x720.png`、`50-before-browser-close.png`、`51-after-browser-reopen-director.png`、`52-wizard-confirmed-existing-install.png`。
- `logs/full-backend-pytest-final.log`、`logs/package-entrypoints-final.log`、`logs/full-frontend-vitest-rerun.log`、`logs/full-ruff.log`、`logs/alembic-upgrade-head.log`、`logs/alembic-current.log`、`logs/alembic-check.log`、`logs/isolated-stack-stop.log`、`logs/isolated-stack-stop-final.log`、`logs/isolated-start-stop-summary.json`、`logs/package-build-final.log`、`logs/gpu-and-memory-final.log`、`logs/wizard-existing-install-confirmation.json`。
- `package-output-final/ShineVideoStudio-20261102-2b70d77-win64.zip`：本地隔离构建包；SHA-256 `0a7d7f0cf68e5d619030af6e8f3a5e16318ff35db8e6d8ff861fc71d7fa2f904`。它不是已双击安装的证据。
- `logs/comfyui-workflow-inspection.json`：用户目录 H3 Ref2VA 工作流的只读结构检查与 SHA-256；不包含提示词或图像内容。
- `input/rainy-night-page-17.txt`：仅用于隔离验收的虚构剧本；不声称其中人物、场景或声音已是主资产。
- 没有原始 H3 MP4、ASR/VLM 报告、EDL、最终 MP4、视频 SHA-256 或播放器解码证据；这些项目保持 `NOT_RUN`。

## 资源与保护边界

- 真实 ComfyUI 位于用户指定目录，核验为已有安装；本轮没有修改其源代码、节点、工作流或模型文件，也没有启动 H3 推理。
- 已发现一份现有 H3 Ref2VA ComfyUI 前端工作流（95 个节点）；它没有 API `class_type` 图。脱敏报告不写入该用户文件的路径或哈希；本地隔离证据中保留结构检查哈希，不能仅凭 UI 图文件存在就说工作台已有可执行 Workflow Profile。
- MiniMax 凭据只通过隔离 Web 设置输入；最终报告、Git 差异、日志和截图文件名均不包含凭据值。
- 未修改正式 `.local-data` 数据库或既有作品；未导入其素材到隔离验收项目。
- 模型核验期间已用 GPU：RTX 5080 16,303 MiB 显存；关闭隔离应用后观测到 1,417 MiB 占用、GPU 利用率 0%；系统约 21.89 GiB 可用内存。没有 H3 模型加载或显存释放验收。
