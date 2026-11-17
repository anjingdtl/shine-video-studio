# 主仓可移植性验收

基线为 `main@c36616a95f35a1b76eeec750fe96532c03cb8739`。修复在当前主仓完成。启动实测用隔离数据目录、Mock 和已装好的依赖。验收目标是让新拉取的仓库按当前源码、当前安装配置和当前发布输入运行。

## 修复

- Windows 安装、启动和停止共用路径解析。桌面启动入口交给 `start_shine.ps1` 处理端口、依赖回执、存活和半栈恢复。监督进程用当前解释器控制 Worker 和重启，用进程记录里的端口。单 Worker 启停读写进程记录时沿用整栈启停锁。虚拟环境的旧 editable 安装无法决定实际加载的源码。
- Python 依赖配置与锁文件都使用公开 PyPI，42 个锁条目的版本相同。Windows Launcher 的生效工作流按触发 commit 构建并上传产物。旧功能分支回写任务与重复构建定义已删除。
- 维护验收脚本的机器专属数据库、模型、包、素材和密钥引用改用明确的输入。Windows 后端分片脚本从自身定位仓库，测试目录由维护者传入。
- 补齐素材拆解已有事件的中文标签。三处旧测试按当前产品契约准备数据和断言，覆盖多单元素材拆解、调用间隔，以及旧导演包旁边的新分集方案。

## 实测

| 项目 | 结果 |
| --- | --- |
| 根目录桌面入口 | `启动 Shine 工作台.cmd` 实测退出码 0。保留隔离的 8000 监听时，API 使用 8001，Web 使用 5173，空库创建了首次配置项目。 |
| 包含中文和空格的启动数据目录 | 通过。从仓库目录外启动，外置 Python 启动前加载另一份源码，工作台启动后 API 使用当前源码，Worker 在线，Web 返回 200。SQLite 迁移至 `f180f0010001`，落在显式指定的数据目录。 |
| 端口占用、重复启动、半栈、监督进程重启和停止 | 通过。一个隔离监听占用请求的 API 端口，启动选择可用端口。重复启动复用 PID，Web 停止后由现有入口恢复，监督进程复用记录的端口，单独恢复 Worker 时使用同一个外置解释器。现有停止脚本清理了隔离工作栈。 |
| 定向后端回归 | 68 项通过，覆盖启动与停止、PowerShell 5.1 引导、打包入口、监督进程和运行身份迁移。此前验收发现的四个失败点复验通过，对应四个文件或用例合计 6 项。 |
| 前端 | `npm.cmd run build` 通过。`node.exe node_modules/vitest/vitest.mjs run --no-file-parallelism` 共 78 文件、395 项通过。 |
| Python 锁解析 | `uv sync --project backend --locked --dry-run` 与 `uv --no-config sync --project backend --locked --dry-run` 均成功。 |
| 全新缓存联网安装 | PARTIAL。`uv sync --project backend --locked --python 3.12` 解析成功，下载 `files.pythonhosted.org` 软件包时超时。`npm ci` 的全新缓存验证也在 `registry.npmjs.org` 超时。工作栈实测复用了准备好的同版本依赖。 |
| H3 出片 | 本轮未重跑。本轮验收覆盖安装与启动可移植性。 |

后端全量走 `.\run_backend_shards.ps1 -WorkRoot <非系统盘上的测试目录>`，每轮有独立日志与分片退出码。
Windows 交付校验从提交后的文件树执行 `pwsh -File scripts/build_test_package.ps1 -OutDir <验收输出目录> -Verify`。
