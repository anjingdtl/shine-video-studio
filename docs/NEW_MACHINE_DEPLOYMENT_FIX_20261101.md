# 新机部署验收断点修复（2026-11-01）

基线：d20bd6d，建设分支：`codex/new-machine-storage-runtime`。仅修改项目代码、固定部署元数据和隔离测试，不下载大模型、不启动生产 ComfyUI、不调用真实 LLM/H3。

## 已完成

- SetupPanel 使用探测出的磁盘预算；保留服务端不足预算拒绝，不把 0 预算当作许可。
- 安装计划进入可审批列表；实例改用 next-instance-plan 逐步协议。
- 审批后使用服务器返回的新计划哈希执行；按安装/实例计划分流到正确执行端点。
- 后端保留 instance-chain 兼容别名，但其语义仍是逐步计划，不预先伪造四个可执行计划。
- INSTALL_NODE 纳入固定 wheel 包，保留离线依赖身份，不信任无来源的缓存。
- 工作流按 JSON 内的 profile 名定位文件；同名 profile 必须比较图、参数映射、媒体传输与模式，否则 409。
- 修复 PowerShell 探测嵌套数组；npm/ffprobe 由 Node/FFmpeg 包提供，避免要求不存在的独立安装项。
- bootstrap 测试隔离 PATH、LOCALAPPDATA 和继承的 PSModulePath，保留本地 ZIP 安装及哈希错误断言。
- 从 PyPI 固定版本元数据增加审片服务依赖 fastapi/httpx/uvicorn 及其必要依赖，共 10 个 wheel，实际 SHA-256/大小记录于 upstream-pins/wheels.json。目录由生成器重建，文件数上限显式扩为 256。
- 修正 ASR 嵌套 manifest 及 sha256 字段，配置指向隔离 venv site-packages。
- 撤销仅凭环境变量和空目录将 L4 判 READY 的逻辑：实际服务/契约验证完成前保持 PENDING。
- L3 也要求非 Mock 实例、完整安装尝试及其结果哈希、隔离运行文件、锁文件和固定节点版本；空 venv 或仅伪造 real 标记不会通过。
- bootstrap 默认清单成功后初始化本地操作员并调用 start_shine.ps1 -Install；可用 -NoLaunch 安装工具并保存路径但不启动服务。密码安全输入且仅存 SHA-256；保留原有操作员和其他配置，补齐缺失 ID，拒绝无效 hash。既有工具目录不再递归覆盖。
- 前端从 SQLite 计划列表恢复当前项目计划；APPROVED 继续执行不再申请第二次 grant；切换项目清空旧计划，未知/已执行状态禁止再次执行。
- 8 项独立验收合同转入正式 test_deployment_acceptance_regressions.py。磁盘请求按修复后的实际客户端预算构造；不足预算的原有负例保留。

## 验证

首启交接修复后的最新完整回归：**1245 passed / 1 skipped / 2 warnings，exit=0**，命令 `uv run --project backend --locked pytest backend/tests -q`，日志 `.local-acceptance/new-machine-goal-20261101/full-suite-cold-start-final.log`，数字按静默日志状态符统计。另有全目录 Ruff、TypeScript/Vite 构建通过。安装链不再执行普通 START，配置生成按明确实例身份并拒绝运行中/Fake/资源未对账状态；原实现 8 项失败反例和修复后新增 14 项合同见 [首启交接修复](NEW_MACHINE_COLD_START_FIX_20261101.md)。

下载目录阶段回归：`uv run --project backend --locked pytest backend/tests -q`，**1231 passed / 1 skipped / 2 warnings，exit=0**，日志 `.local-acceptance/new-machine-goal-20261101/full-suite-storage-caches-final.log`。包括下载盘设置、编译缓存落点、NoLaunch 配置保留、源码重装及主/内嵌 wheel 元数据区分。当前 pytest 静默配置没有打印数字摘要，数量从该日志逐行状态符统计得出。真实 ComfyUI 探测仍显式 SKIPPED；Starlette/AnyIO 弃用与 SQLAlchemy 空主键两项既有警告保留。1221、1223、1226、1228 项通过的历史快照单独保留，不混充最终结果。

真实依赖环境：154 个固定发行包，共 4,317,653,223 字节，全部按大小和 SHA-256 下载核验；包含 153 个 wheel 与 sox 1.5.0 源码包。初次受控离线安装、sox 构建、最终哈希锁安装、pip check 通过；Qwen3ASRModel、Qwen3VLForConditionalGeneration、结构化解码器及音频库导入通过。CUDA 检测为可用，只证明驱动/运行库可见，不是模型推理验收。状态 `PASS_SDK_ONLY`，证据位于 `.local-acceptance/new-machine-goal-20261101/runtime-verification/`。

目录设置及首次依赖安装/Mock 工作台真实启动关停的范围见 [下载与安装目录](LOCAL_STORAGE_SETTINGS.md)。没有下载 H3/VLM/ASR 权重、生成真实影片或自动批准主资产。

- Ruff：PASS。
- TypeScript/Vite：PASS。
- bootstrap_entry：11 项通过，安装和哈希负例、NoLaunch 路径保存及旧数据配置保留均纳入全量套件。
- 本轮新增操作员初始化/保留/缺失 ID/错误输入负例，连同新机设置合同定向 26 项通过（L3 新增负例纳入随后全量回归）。
- scripts/verify_setup_web.py：PASS_UI_CONTRACT_ONLY。真实 API/SQLite 审批、浏览器刷新恢复、新审批哈希和项目隔离通过；最终 execute 请求被显式拦截，无下载/安装，不充当真实执行证据。截图位于 .local-acceptance/new-machine-goal-20261101/setup-resume.png。
- 正式新机设置合同及新增 8 项合同：通过（全量回归结果见下）。
- 浏览器入口验证：磁盘充足时安装计划 200 且审批按钮可见；工作流登记 200；未安装文件时实例步骤 409 PACKAGE_FILES_NOT_VERIFIED，按预期阻断。未执行 60GB 下载，不能将该结果称为安装验收。
- 全量后端：1201 passed / 1 skipped / 2 warnings，547.81 秒，exit=0。唯一 skip 为显式门控的真实 ComfyUI 启动探测。
- 后续包含启动身份初始化、L3 反例和计划恢复后的完整回归：`uv run --project backend --locked pytest backend/tests -q`，**1206 passed / 1 skipped / 2 warnings，exit=0**。日志 .local-acceptance/new-machine-goal-20261101/full-suite.log；保留相同真实 ComfyUI 探测 skip 及既有 Starlette/SQLAlchemy 警告。
- 同版全目录 Ruff、TypeScript/Vite、git diff --check 通过；隔离新库 Alembic upgrade head/current 通过，head=f140a0030001。

原始验收浏览器脚本首次复用时因等待旧端点而超时，未覆盖原截图；截图时间戳仍为修复前时间。修复后的浏览器证据位于 .local-acceptance/setup-fix-20261101/。旧 HTTP JSON 从保留的原始日志重新写回；报告不混用两轮响应。

## 尚未完成，不能声明新机目标全 PASS

1. Qwen-ASR SDK 固定依赖的初次构建/安装闭包已通过真实 Windows 隔离验证；实际 ASR 模型加载和音频识别尚未验证，不能由导入成功代替。
2. L4 当前保守保持 PENDING；真实服务、节点契约、审片路由和运行配置的综合探测尚未完成。
3. 首次本地执行开关、操作员初始化、刷新恢复计划已有实现及定向验证；完整默认入口冷启动、受管接管和审片路由自动配置仍需端到端建设与验证。
4. 干净 Windows 全量安装、完整 60.8GB 物料下载和真实 H3 推理均 NOT_RUN。已下载的 4.32GB 是 Python 发行包，不能算成权重下载完成。

## 继续建设的依赖核对

在 .local-acceptance/new-machine-goal-20261101/ 保留了固定基础 103 个运行包后解析出的 51 个新增依赖记录。154 个输入现已进入版本化清单和独立审计；Torch 原始 CUDA 钉点没有替换。解析时用同版本基础标签获取元数据，真实安装仍使用原 cu128 wheel，并通过实际 pip check 验证闭包。

sox 1.5.0 已从精确哈希源码通过固定 setuptools/wheel 离线构建，产物包名/版本/哈希及源输入记录在尝试结果中。soundfile 使用 win_amd64 wheel，实际音频格式枚举成功。安装器与目录统一信任 PyTorch 官方索引明确链接的 download-r2.pytorch.org，仍拒绝相似域名。setuptools 的嵌套 vendor 元数据不再被误算成多个主包，多个顶层主元数据仍拒绝。

实测展开 wheel 7,902,222,874 字节，venv 8,159,500,574 字节。预算现已计入实例 wheelhouse、多步恢复快照和临时余量：完整清单 60,804,054,569 字节，安装要求 173,663,952,354 字节（约 161.74GiB），建议准备 180GiB；测量元数据绑定固定发行包清单哈希，清单改变后拒绝继续使用旧测量。

同版本源码重装另做真实 A→B 反例：旧实现保留已安装 A，新实现对经过来源校验的构建产物强制重装后正确得到 B。仅源码构建产物使用强制重装，不重复重装全部固定 wheel。原始反例与修复后结果保留在 `source-reinstall-before/`、`source-reinstall-after/`。

固定 ComfyUI 与五个节点归档共 25,943,309 字节经过下载哈希核验。隔离 CPU 服务真实启动、保持队列 drained，默认 PDD8 两个 profile 的节点/参数名合同核验通过，随后按出生身份关停全部受管进程；未提交采样、未加载权重。可选超分 profile 缺少 `MinimaxH3LatentUpscaler3DRefineHandoff`，仍未通过。证据在 `comfy-cpu-contract-after-name-fix/`；不能提升为 GPU/H3 验收。

因此本轮结论为：确定性接口/合同断点已有修复，完整新机部署目标仍为 PARTIAL，不以测试通过替代实际部署验收。保留现有资产人工批准、风格冻结、未知结果对账与真实影片待审边界。
