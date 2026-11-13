# H3 动态资源整备与受管连接 V2：阶段报告（2026-11-14）

本报告记录当前分支的实际实现与未完成项；**不是 V2 完成或真机验收声明**。基线为 `origin/main@23467957ab7e6c6b9dee10cf0d1b6db011e7dcc1`，对应 PR #14。原方案见 `SHINE_H3_DYNAMIC_RESOURCE_SELF_PREPARATION_V2_MAIN_23467957_20261113.md`。未下载模型、未修改历史成片、未启动或接管外部 ComfyUI。

## 已实施

- W6A8 native20 动态图读取 manifest 所钉的候选工作流哈希，采用该图的模型链及 VAE；拒绝 W6A8 PDD 和二采，未登记的其他变体不借用基线模型链。候选图仍包含 Sage 节点，不能据此声称 SM8.9 兼容。
- 从编译图与变体 catalog 解析资源，比较 manifest 与 catalog 的哈希、大小和固定来源，复用现有校验凭据及缓存证据；区分文件校验和当前实例绑定，保留 ABI、试镜与正式生产未验证状态。
- 计划绑定策略、catalog、配方、扫描快照、实例和来源偏好；沿用一次性 grant、现有安装计划、Worker 和 ControlledDownloader。通用计划不能绕过候选 DiT 身份绑定；实际下载须用户在 UI 中明确确认。
- 镜像与官方优先使用既有白名单及最终完整 SHA；持有现有实例生命周期锁，实例或 GPU 工作不空闲时不替换资源。仅支持安全重试，不将文件完成等同运行时完成。
- 统一状态增加连接、归属及缺失节点信息；对证实租约已释放、原始及重启进程均已退出、端口空闲且没有未知提交的 H3 历史记录做有界收束。保留原始记录和进程身份，不终止或接管未知进程。
- 导演拍摄页允许查看 W6A8 资源并选择来源，待批准后通过既有安装链排队；候选仍禁止开拍。引擎状态说明图片引擎与 H3 的区别。

## 尚未完成，不能标记为生产就绪

- 未完成受管安装目录、用户授权目录、节点与 Python 环境的完整有界自动扫描，未完成已发现但未导入缓存的重命名文件自动复用。
- AUTO 已在获批下载执行时对固定官方源及已审镜像做有界 HEAD 择源；真实网络下的跨源进度展示和恢复尚未完整验收。
- 未建立按变体安全生成与激活的完整启动配置；未验证 SageAttention 对 RTX 4070 / SM8.9 的 ABI，也未审核无 Sage 合法替代图。
- 真实隔离试镜入口及主机限定生产资格尚未实现；不应下载后宣称试镜或生产 READY。RelayVSR 权重 pin 与真实 X2 仍阻断。
- 连接状态尚未覆盖全部 HTTP、进程、实时节点 ABI 的分层证据和脱敏诊断包；对持有待对账租约或无法证明退出的 H3 记录不自动收束。

## 24 项验收状态

下表区分定向离线工程测试和真实主机验收；部分实现不等于整个场景通过。

| 场景 | 当前结果 |
| --- | --- |
| T01 | ENGINEERING_PASS：候选 native20 精确四项权重及组合，离线测试；正式生产仍阻断。 |
| T02 | PARTIAL：候选 PDD/二采拒绝；全部其他组合与 Relay 依赖闭包未验收。 |
| T03 | PARTIAL：现有缓存与凭据做哈希验证；重命名文件自动扫描和复用未完成。 |
| T04 | PARTIAL：输出区分 HASH_VERIFIED 与绑定；真实 renderer 绑定验证未完成。 |
| T05 | PARTIAL：安装计划须 grant；候选与 catalog 不一致和候选 DiT 通用计划旁路有离线门禁。 |
| T06 | PARTIAL：已加有界 AUTO 探测并沿用 Range 与校验逻辑；真实网络跨源恢复未验收。 |
| T07 | PARTIAL：已审 HF 镜像与官方顺序有离线测试；端到端用户场景未完成。 |
| T08 | PARTIAL：复用已有 Attempt 恢复机制，浏览器关闭与崩溃全过程未验收。 |
| T09 | PARTIAL：新增变体/catalog 和候选 DiT 绑定拒绝；全路径对抗验收未完成。 |
| T10 | BLOCKED：未在 RTX 4070 SM8.9 实测 SageAttention ABI。 |
| T11 | PARTIAL：下载与生产资格分离；真实下载后的完整流程未运行。 |
| T12 | PARTIAL：动态图与策略定向回归；本轮真实 H3 回归未运行。 |
| T13 | PARTIAL：沿用外部监听者不接管保护；本轮真实外部进程场景未运行。 |
| T14 | PARTIAL：未核实归属保持阻断；真实身份冲突场景未运行。 |
| T15 | PARTIAL：仅处理租约已释放且退出证据齐全的旧记录；带待对账租约的完整恢复未完成。 |
| T16 | PARTIAL：不确定记录保持 NEEDS_REVIEW；完整 UI 用户演练未完成。 |
| T17 | PARTIAL：已报告启动记录中的缺失节点；动态安装和实时 ABI 全链未完成。 |
| T18 | PARTIAL：沿用 API、Worker 和连接状态区分；完整故障注入未完成。 |
| T19 | PARTIAL：沿用实例锁和现有 GPU Lease；真实交替运行未验收。 |
| T20 | BLOCKED：RelayVSR 权重 SHA 未钉定，真实 X2 未运行。 |
| T21 | PARTIAL：源码未迁移或覆盖历史成片；完整历史数据迁移验收未执行。 |
| T22 | PARTIAL：现有下载器对重定向、路径和 SHA 有离线回归；整个新路径的完整安全审查未完成。 |
| T23 | BLOCKED：本机为 RTX 5080 16 GB，不是 RTX 4070；W6A8 真实试镜未运行。 |
| T24 | NOT_RUN：空目录新装 Windows 用户完整流程未验收。 |

## 回归记录

- 首轮后端 8 分片为 6/8；受管启动测试旧 fixture 无 Ref2VA 参考、使用已变更 profile 名称，另两项旧测试期待旧拒绝码。随后修正测试 fixture 和预期，20 项定向重跑通过。首轮运行期间代码仍有修改，不能作为最终版本的全量验收。
- 前端全量：`npx vitest run --no-file-parallelism`，48 文件 / 279 项通过；后续针对改动文件又通过定向测试。`npm run build` 通过。
- 后端 Ruff 通过；独立临时 SQLite 上 Alembic `upgrade head` 和 `check` 通过。
- 后端最终 8 分片日志：`.local-data/h3-v2-final/`，以该目录退出码和日志为准。
- 真 GPU 试镜：NOT_RUN；人工艺术审核：NOT_RUN；下载候选模型：NOT_RUN。

## WP0 接口与共享代码边界

- 动态图：`shine/comfy/graph_compiler.py` 是现有执行图路径；`resource_resolver.py` 读取其编译结果和 `variant_binding.py` 的 catalog，不新建生产图系统。
- 安装：`api_setup.py` 只创建待批准的 `InstallationPlan`；授权、幂等任务、暂停与恢复仍由 `api_preparation.py`、`installation.py`、`installation_worker.py` 和 `ControlledDownloader` 负责。
- 连接：`runtime_orchestrator` 管理 image 控制意图；H3 的 startup、handoff 和 GPU Lease 仍由 `handoff_startup.py` 及现有控制面拥有。此分支未变更 `worker.py`、H3 提交语义、VLM 校准和历史工件。
- 共享改动冲突点：`api_setup.py`、`deployment.py`、`installer.py`、`installation_worker.py`、`runtime_orchestrator/*` 和导演拍摄 UI；合并前须针对当时的 main 再审查，不能覆盖其他 worktree。
- 来源沿用仓库现有固定的 Comfy-Org/MiniMax-H3 catalog、变体 manifest 和工作流；未增加新的上游源、wheel、节点包或许可证结论。RTX 4070 的 Sage 实现与 wheel 没有经本轮核实，不得自动安装或声明兼容。

## 2026-11-14：同步 main 与回归边界

开发期间 `origin/main` 前进到 `10585e3d752a4496acb8222107c9a004f62752bf`（合并独立 launcher PR #13）。本功能分支已在独立 worktree 合并该提交；共享测试文件自动合并，无冲突。`23467957` 仍是原方案审计基线，但当前代码以合并后的 main 为准。

第二轮后端 8 分片均退出 0、日志没有 FAILED/ERROR，但运行期间修改了安装安全门禁，且之后合并了 main；因此仅作为中间结果。最终版本按文件轮转均分 8 片运行，日志以 `.local-data/h3-v2-main10585/` 为准。该目录是本机测试证据，不纳入 Git。

## 下载中断与租约加固

在合并 main 后的测试期间继续检查下载中断窗口，发现原 Attempt 在大文件哈希或网络长时间无进度时可能超过续租时间，且暂停只在文件之间检查。现沿用现有 Attempt 租约增加心跳；旧 Worker 失去所有权后不得写状态或登记校验结果。传输进度处检查暂停并保留 `.part`。安装 journal 只有在 `DOWNLOADING`、文件清单一致、目的目录状态一致且无 backup 时才自动收束 staging 并续传；任何替换阶段或不一致都保持待核对。

上述测试使用合成字节和注入的 transport，不是模型下载或真实 H3 验收。安装持久化定向 23 项、V1.3 preparation / 下载器 / 资源解析定向 130 项通过。此前 8 分片运行期间修改了这些文件，故也只算中间回归；最终全量结果以 `.local-data/h3-v2-lease-final/` 为准。

## 计划身份与来源复核

资源预览和 Worker 会绕过普通进程内 catalog 缓存，重新读取磁盘清单；计划冻结 manifest、catalog、配方和实例哈希，变化后拒绝执行。AUTO 的真实网络测速与断线切换仍为 NOT_RUN；只在明确批准后的下载任务中执行探测。导演资源卡使用普通用户可读的组件名称。
