# 下载与安装目录

在工作台「模型、ComfyUI 与本机状态」设置区的「下载与安装目录」中选择磁盘、填写完整路径并保存。新机设置区会显示实际安装路径和该盘的剩余空间；生成和执行安装计划时，后端也检查目标盘，而不是数据库所在盘。

完整清单约 60.8GB，当前首次链路还预留实例环境、wheelhouse 和三份恢复快照，共需约 173.66GB（161.74GiB）。清单建议准备 180GiB 可用空间。环境探针中的 5GiB 是基础运行余量，不能代替安装计划的完整容量门禁；安装后不再把原先的 120GiB 下载空间当成始终必须空闲的运行条件。

默认不允许自动下载到系统盘。若确实要使用系统盘，必须在设置区明确勾选使用系统盘并保存；这不是默认选项。建议使用专用空文件夹，例如 `F:\ShineVideoStudio\Runtime`。

所选目录包含：

- `isolated-packages/.cache`：经过大小和 SHA-256 校验的下载缓存。
- `isolated-packages`：固定版本的模型、节点源归档和依赖发行包。
- `instances`：ComfyUI、其独立 Python 环境、运行缓存和临时文件。
- `instance-snapshots`：安装步骤的恢复快照。
- `setup`：受管启动配置和审片模型清单。

目录偏好保存在当前数据目录的 `deployment-storage.json`。API 与独立 Worker 读取同一份偏好，项目数据库和已有资产不迁移。目录变更会取消未执行的安装计划，必须按新目录重新生成和批准；已有下载、实例或未对账尝试时，系统拒绝自动切换或搬动文件。

## 第一次启动之前

空白 Windows 上运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 -StorageRoot F:\ShineVideoStudio
```

省略 `-StorageRoot` 时先询问安装目录，选择之前不下载工具。默认引导要求非系统盘；`-CheckOnly` 只检查环境。

该根目录同时承载工具、uv 管理的 Python、uv/pip/npm 缓存和临时目录。新机应用依赖位于 `app/backend-env` 与 `app/frontend/node_modules`，前端通过目录联接使用选定盘上的依赖。项目源码位置不决定依赖缓存位置。已有前端依赖会保留为 `node_modules.before-storage-*`，不自动删除。

首次数据目录默认是所选根目录的 `data`，模型部署目录默认是其 `runtime`。已有数据目录配置会保留；如需指定另一数据目录，可以显式传入 `-DataDir`。已有部署文件不会被引导脚本重定向到新目录。

`-NoLaunch` 也会保存所选目录，供下次启动使用；它不初始化操作员或启动服务。已有 `.env` 数据目录和 SQLite 地址会保留，启动器不会悄悄创建空库替代原项目。

之后运行 `scripts/start_shine.ps1` 会读取本地 `.env` 中的引导路径。启动器输出的停止命令使用选定目录里的 Python，避免另开终端执行 `uv run` 时又创建项目内的默认环境。

## 已验证范围

2026-11-01：正式 API 合同覆盖目录保存、刷新/Worker 读取、系统盘默认拒绝、旧审批计划作废、缓存/包/实例实际落点、已有文件保护，以及真实子 Python 的临时目录和缓存环境。浏览器验证已覆盖设置页保存和刷新恢复。

单元合同显式模拟非系统盘与充足容量，避免依赖 CI 临时目录的位置和容量；系统盘默认拒绝、实际目标盘不足分别保留反例。本机实际 F 盘安装证据独立记录，未将模拟容量算成物理安装通过。

受管安装、渲染和审片子进程还显式设置 Triton、TorchInductor、Torch 扩展、CUDA、Numba 与 Python 字节码缓存目录，启动前创建目标文件夹。真实已安装 SDK 的缓存路径接口已核验指向 F 盘，证据位于 `.local-acceptance/new-machine-goal-20261101/compiler-cache-probe-valid-hash/`；这不代表执行过 GPU 编译或模型推理。

在隔离源码副本上真实安装了后端和前端依赖，核验它们及 uv/npm 缓存位于选定的 F 盘目录，启动 API、Worker、Web 后核验接口并通过进程出生身份校验关停。证据位于 `.local-acceptance/new-machine-goal-20261101/storage-stack-verification/`。第一次 npm 下载曾发生 ECONNRESET；隔离副本最初漏复制 `shine_processes.py`，补齐后重验成功。两次原始失败日志均保留，不作为通过证据。

此验证是本机依赖安装与 Mock 工作台启动验证，不证明干净 Windows、真实 ComfyUI、模型加载或 H3 无人值守制作已经通过。

随后以既有 SQLite 地址再次启动隔离工作台，实际从 API 读取预先插入的项目，确认进程清单使用该数据库，并验证式关停。结果见同目录 `legacy-database-result.json`。
