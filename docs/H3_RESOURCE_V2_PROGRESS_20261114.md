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

- 已接入受管 ComfyUI `models` 目录的有界扫描与经确认的 SHA-256 校验导入；用户另行授权目录、节点和 Python 环境的完整自动扫描尚未完成。
- AUTO 已在获批下载执行时对固定官方源及已审镜像做有界 HEAD 择源；真实网络下的跨源进度展示和恢复尚未完整验收。
- 未建立按变体安全生成与激活的完整启动配置；未验证 SageAttention 对 RTX 4070 / SM8.9 的 ABI，也未审核无 Sage 合法替代图。
- 真实隔离试镜入口及主机限定生产资格尚未实现；不应下载后宣称试镜或生产 READY。RelayVSR 权重 pin 与真实 X2 仍阻断。
- 已区分显式 HTTP 探测与进程归属，并提供白名单字段诊断包；已能对证明归属的运行实例检查单图样例的节点类和输入名。完整节点 ABI / GPU 兼容性仍未验证；对持有待对账租约或无法证明退出的 H3 记录不自动收束。

## 24 项验收状态

下表区分定向离线工程测试和真实主机验收；部分实现不等于整个场景通过。

| 场景 | 当前结果 |
| --- | --- |
| T01 | ENGINEERING_PASS：候选 native20 精确四项权重及组合，离线测试；正式生产仍阻断。 |
| T02 | PARTIAL：候选 PDD/二采拒绝；全部其他组合与 Relay 依赖闭包未验收。 |
| T03 | PARTIAL：受管目录支持发现重命名文件并经确认哈希复用；其他授权目录尚未接入。 |
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

## 镜像不可用的回退

已审镜像返回 HTTP 拒绝时，只尝试同一文件已审的官方候选源；拒绝不可信跳转和错误哈希的门禁未放宽。下载器的 55 项合成网络测试通过。此前 8 分片中 setup 测试直接运行未领取的 Attempt，现已按 Worker 实际调用顺序先 claim；该模块和安装持久化 38 项重跑通过。

## 受管目录资源复用

当前策略缺失组件会在受管 ComfyUI 的 `models` 目录中有界扫描；大小匹配只标记为未校验候选。明确点击复用后，沿用已有 importer 复制并进行 SHA-256 校验，同时使用现有组件文件锁；错误哈希不入缓存，原文件保持不变。重命名文件、错误哈希和快照变化已有合成文件测试。扫描不包括未授权目录、节点 ABI 或 Python wheel；导入尚未具备可重连进度，不代表正式运行验证。

`c75e8d1` 的 Windows 后端 8 分片退出码均为 0，日志没有 `FAILED` / `ERROR` 行；前端全量 48 文件 / 279 项通过。上述结果不包含后续的受管目录复用增量，后者必须重新回归。

## 扫描一致性与连接诊断

扫描和确认导入时重新比较实例、manifest、catalog 和源文件快照；哈希错误不进入缓存，重复点击不会重复启动导入。候选图使用 V2 compiler 身份，原 INT8 身份保持不变。显式“检查连接”可在不持有凭据的 loopback GET 后分别报告 HTTP 与 listener 归属；HTTP 200 不代表工作台拥有该进程或达到生产资格。状态详情可复制白名单字段的脱敏诊断信息。以上为工程测试，真实外部进程、Sage ABI 和试镜仍未验收。

## 受管实例节点输入检查

对已证明 PID/创建时间/Job Object/listener 归属的单个运行实例，可读取有界 `/object_info` 并检查候选动态图单图样例的节点类和输入名。请求前后 listener 变化、归属不明、schema 缺失或响应异常均不通过；不会启动 ComfyUI，不会把 HTTP 或节点存在转成 GPU ABI、试镜或生产 READY。多参考输入、SageAttention 与目标 GPU 的真实 ABI 仍未验收。

## 下载确认与进度准确性

资源计划除总字节数外，冻结了用户确认时确实缺失的文件 SHA-256 和大小；Worker 执行前重新核对缓存，下载器每次实际传输前再核对文件集合。已确认的缓存消失时，旧计划拒绝下载并要求重新检测；同一 Attempt 重启也不会把其他文件的额度当作授权。缓存复用不计入已下载进度，断点续传以实际 `.part` 字节为依据。上述行为已用合成文件和注入 transport 测试，真实网络下载仍未运行。

连接启动失败会保留符合格式的 COMFY 错误码以供诊断；不把任意异常文本写入启动记录。节点诊断测试的临时替身污染后续模块导入的问题已修复，并以跨模块组合回归验证。前端全量为 48 文件、284 测试通过（npx vitest run --no-file-parallelism）；真实 GPU、目标 4070 节点 ABI 和人工审核仍未运行。

补充修复：handoff 旧恢复路径不再把 PID 查询异常视为进程退出；必须核对命名 Job Object 和空闲端口，已记录的重启进程也必须退出。review 或重启意图尚无法完整证明时保留 RECONCILE_REQUIRED；测试为离线进程证据模拟，真实 Windows 故障注入仍未运行。

通用死进程租约回收也拒绝绕过未完成 handoff；启动 Worker 身份不可读取或缺失时，不得终止 renderer。修改 handoff 客户端源码会使既有 VLM 身份证据失效，真实正负校准、新 revision 与预生产计划重冻结尚未执行，不能复用旧证据。
