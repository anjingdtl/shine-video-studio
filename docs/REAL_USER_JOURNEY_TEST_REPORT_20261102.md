# 真人模拟验收测试报告（2026-11-02，本轮暂停版）

## 判定

**`PARTIAL`。本轮应用户要求中止，不能签发 `REAL_USER_JOURNEY_ALL_PASS`。** 下表只记录当前源码/本轮操作的证据；暂停前没有结论的测试统一标为 `UNKNOWN/INTERRUPTED` 或 `NOT_RUN`，不以进度百分比、旧报告、页面就绪徽标或 Mock 结果推断通过。

- 项目：`F:\ClaudeWorkSpace\projects\shine-video-studio`
- 分支：`codex/confirm-existing-comfyui-install`
- 暂停时 HEAD：`b943780eb9bdedfad927ca80f10af93d947efc24`
- 暂停时 `origin/main`：`b943780eb9bdedfad927ca80f10af93d947efc24`
- 工作区：大量未提交的本轮代码、测试和文档修改；无提交、无推送。后续必须先审查并保留全部现存差异。
- 隔离验收数据目录：`.local-acceptance/real-user-journey-final/`
- 隔离项目：`Shine真人模拟验收短剧（建议式审片回归）`，ID `b6f22954-258e-4544-ad89-4c2e8862387e`
- GitHub Actions：`WAIVED_EXTERNAL_BILLING`
- 真实作品版权、艺术质量与发布审批：`PENDING_HUMAN`

## 验收矩阵

| 验收项 | 状态 | 本轮证据与边界 |
|---|---|---|
| Windows 一键安装/启动入口 | `NOT_RUN` | 本轮没有从 `.cmd` 正式入口启动的证据。已有浏览器工作台可用不等于 Windows 安装器验收通过。 |
| Codex 内置浏览器打开工作台 | `PASS_REAL_USER` | 使用 Codex 内置浏览器访问本机工作台，完成隔离项目的页面操作；暂停时停在真实制作设置页。 |
| 创建隔离作品、输入/保存剧本、刷新后查看 | `PASS_REAL_USER` | 通过页面创建作品、导入剧本并回到项目查看；不涉及直接写数据库来代替用户操作。 |
| 导入和确认授权测试素材 | `PASS_REAL_USER` | 按用户授权从 `F:\AIGC2026` 素材在隔离项目中经 UI 导入/确认 4 项（人物、场景、道具、声音）。正式作品和既有审批状态未改。 |
| 风格选择与冻结 | `PASS_REAL_USER` | 页面选择“冷调悬疑”并在隔离作品冻结；版本 `82c0362d-fb62-4935-a5a6-6a14eea6e4f8`，哈希前缀 `1c3b045`。 |
| 现有 ComfyUI/H3 安装发现和确认 | `PASS_REAL_USER` | UI 扫描用户指定安装并明确确认复用；10 个候选文件约 41 GB 哈希匹配，H3 固定组件 6 项确认，0 字节复制、0 字节下载。此证据只证明发现和文件校验，不证明运行时或 H3 推理可用。 |
| ASR/VLM 新机状态识别 | `PARTIAL` | 页面枚举了审片资源，但只核验部分清单；仍缺 19 个固定清单文件，ASR/VLM 核心权重未完整。最新 UI 状态修复尚未通过定向 Vitest/浏览器复验，旧“整类已安装”展示可能误导用户。 |
| 默认安装计划不下载 ASR/VLM | `UNKNOWN/INTERRUPTED` | 已修改 SetupPanel 排除可选审片模型并更新中文说明；最后一次定向 Vitest 在用户要求暂停时无最终结果，页面行为没有复验。 |
| 真实 MiniMax LLM 配置与上下文预算 | `PASS_REAL_MODEL` / `PASS_REAL_USER` | 已通过 UI 配置真实 MiniMax M3.1 Flash Profile，页面显示 1,000,000 输入上下文。剧本 Agent、导演 Agent 真实调用成功，`is_mock=false`，载荷分别为 5,035、8,254 UTF-8 字节。 |
| Agent 按真实角色完成分镜 | `BLOCKED` | 最新流程中的摄影 Agent 任务 `b789edde-c08b-4bba-9abd-ffdff9d2e877` 返回 `UNKNOWN/timeout`，无结果哈希、未创建镜头；没有盲目重放。项目当前没有可生产分镜。后续先对账，不能原样重发。 |
| H3 API Workflow Profile | `BLOCKED` | 隔离数据库没有已通过服务验证的 H3 API Workflow Profile。不能把 ComfyUI UI 工作流当作 API workflow，也不能凭空编造节点映射。 |
| Web → API → 常驻 Worker → 本地 Ref2VA INT8 H3 | `NOT_RUN` | 本轮未启动 H3 推理，未生成任何本轮 H3 原始 MP4。不得把页面“真实制作已就绪”徽标解释为真实任务可运行。 |
| 三镜头 768×1344、24fps、约 7 秒生产 | `NOT_RUN` | 无镜头任务、无生成视频、无 ffprobe/全帧解码数据。 |
| ASR/VLM 实际视频分析 | `NOT_RUN` | 没有本轮 H3 视频供分析。ASR/VLM 与小模型审片按用户要求是建议功能，不应成为出片阻断项；实际 findings 也不得假报通过。确定性媒体技术错误仍应阻断。 |
| EDL、FFmpeg 自动装配、成片中心播放/导出 | `NOT_RUN` | 未生成 EDL 或最终 MP4；无本轮浏览器视频播放及导出证据。 |
| 浏览器关闭、Worker/ComfyUI 故障及真实生产恢复 | `NOT_RUN` | 没有运行中的 H3 生产序列，无法验证生产期间恢复和租约释放。 |
| 后端建议式审片聚焦回归 | `PASS_SOFTWARE` | 暂停前命令运行结果为 49 passed。它仅覆盖该组测试，不代表全后端或真实 H3 验收通过。 |
| 后端全量 pytest | `UNKNOWN/INTERRUPTED` | 由用户要求停止时约进行到 86%，pytest 无最终摘要；日志 `.local-acceptance/real-user-journey-final/logs/full-backend-pytest-latest.log`。 |
| 前端全量 Vitest（当前差异） | `UNKNOWN/INTERRUPTED` | 87 passed 是最新 SetupPanel 改动之前的旧结果，不能代表当前源码。新增/调整后的定向测试没有最终结果。 |
| 前端定向 SetupPanel Vitest | `UNKNOWN/INTERRUPTED` | 第一轮 fixture 缺字段；第二轮仍有失败，随后修改了 fixture 与断言，但最后一轮结果未得出。 |
| TypeScript/Vite 构建 | `PASS_SOFTWARE` | 本轮最近一次 `npm run build` 成功；不等于前端测试通过。 |
| Ruff | `FAIL` | `ruff check backend\shine backend\tests` 有 1 项 `I001`，位于 `backend/tests/test_workflow.py`，暂停时未修复。 |
| 隔离 Alembic 升级与一致性检查 | `PASS_SOFTWARE` | 在验收隔离目录执行 `upgrade head` 与 `alembic check` 成功；正式数据库未触碰。 |
| Git 提交/推送与远端同步 | `NOT_RUN` | 没有提交、推送；保留本地差异由后续 Agent 接手。 |
| 总体验收 | `PARTIAL` | 用户旅程未到真实 H3、多镜头、自动装配及播放器验证。 |

## 真实 LLM 上下文来源

- MiniMax M3.1 Flash 官方页面标示 1M 上下文：[MiniMax M3.1 Flash](https://agent.minimax.io/tools/m3.1-flash)。
- DeepSeek 官方模型列表显示 `deepseek-flash` context 1,048,576、最大输出 393,216：[DeepSeek API 模型列表](https://api-docs.deepseek.com/api/list-models/)。
- DeepSeek 官方公告将 `deepseek-flash` 描述为 V4.1 Flash：[DeepSeek V4.1 Flash 公告](https://api-docs.deepseek.com/news/news260910/)。
- 本轮模型路由和 Agent 输入去固定小字符截断的改动已存在于未提交工作区；代码改动及新 SetupPanel 还需要完整回归。

## 已发现的问题及当前修复状态

| 缺陷 | 当前处置 | 当前回归状态 |
|---|---|---|
| 部分 ASR/VLM 文件核验后被 UI 显示为整类已安装 | 已改 SetupPanel 依完整文件数显示状态 | `UNKNOWN/INTERRUPTED`：需定向测试及内置浏览器复验 |
| 默认 H3 安装计划包含 Qwen3-VL/ASR 下载 | 已改默认计划排除可选审片模型，并补充说明 | `UNKNOWN/INTERRUPTED`：需测试、构建及浏览器复验 |
| 真实摄影 Agent 调用超时，外部结果未知 | 保留未知任务；不自动重放相同请求 | 尚待只读对账；不得将超时改成确定失败 |
| Ruff 导入排序失败 | 已定位 `test_workflow.py` 的 `I001` | 尚未修复/回归 |

更早的资产名归一化、时间码、未知 Agent 结果保护等缺陷记录见本地证据目录的 `evidence/bug-tracker.md`；只有当前源码和当前 UI 复验完成后才能标记关闭。

## 后续工作优先级

1. 读取 `docs/REAL_USER_JOURNEY_PROGRESS_20261102.md`、本报告、`evidence/bug-tracker.md`，再核对 Git 差异；保护所有未提交文件。
2. 对摄影任务做只读状态对账；不对未知外部结果原样重放。确认无在途结果且确需继续时，使用显式修订后的请求并留下新版本记录。
3. 完成 SetupPanel 定向前端测试，确认部分 ASR/VLM 不冒充完整安装、默认 H3 计划不含审片模型；再运行前端全套 Vitest、`npm run build`，最后用 Codex 内置浏览器检查实际页面。
4. 修复 Ruff `I001` 并重跑；之后从仓库根目录运行完整后端套件。记录完整摘要和退出码。
5. 确认隔离项目分镜与素材绑定正确，并解决已验证可用的 H3 API Workflow Profile。未具备 Workflow Profile、主资产/冻结风格和安全运行检查时不启动 H3。
6. 仅在可安全执行且不重复模型下载的情况下，从 UI 提交真实系列，经常驻 Worker 使用已有 Ref2VA INT8 完成至少三镜；保留任务、模型、视频及哈希证据。
7. 技术媒体检查仍须真实执行；ASR/VLM/小模型审片仅作为建议和 `pending_review` 证据，不得阻断合规生产或伪造人工通过。随后验证自动 EDL/FFmpeg、浏览器播放与导出。
8. 最后进行所需故障恢复和视口 UX 检查，更新最终矩阵，审核密钥/数据库/个人素材是否入 Git。只有当前源码 UI 旅程全通过才能签发全 PASS；否则保持 `PARTIAL`/`BLOCKED`，不得淡化未完成项。

## 证据边界

本报告是中止时的状态快照。未引用旧版 `REAL_USER_JOURNEY_FINAL_ACCEPTANCE.md` 的历史结论作为本轮通过证据；历史报告已在顶部标记为 superseded。验收证据目录可能包含多个历史轮次，后续 Agent 应按时间和任务 ID 区分，不能混用旧视频、旧测试结果或旧截图。
