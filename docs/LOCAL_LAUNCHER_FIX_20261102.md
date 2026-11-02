# 本机一键启动无法启动：根因与修复

日期：2026-11-02。基线：`ab04222`；本轮工作树变更（`scripts/start_dev.ps1`、`scripts/stop_shine.py`、新增 `backend/shine/comfyops/process_probe.py`）。

## 现象

双击仓库根目录的 `一键启动 Shine 视频工作台.lnk`（指向 `scripts/start_dev.ps1`）无法启动，
控制台停在：

```
  -> 检查开发环境
  [OK] 后端环境与前端依赖已就位
  [注意] 上次运行没有正常退出，正在安全收尾（只结束身份匹配的进程）
[refused] pid 2060: liveness unavailable
Unresolved process identities retained for reconciliation.
  [失败] 无法自动收尾上次的进程，请先手动结束后再试
```

退出码 3。此后每次双击都是同一结果，唯一入口被彻底卡死。

## 根因

`.local-data/run/processes.json` 里 `web` 条目记录的进程身份是 pid 2060。
该进程早已退出，但 **PID 被 Windows 回收给了 SYSTEM 身份的 `svchost.exe`**。

- `shine.comfyops.installer.process_identity` 用 `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION|SYNCHRONIZE)`
  读取创建时刻。本机 PowerShell 以 **Medium 完整性级别**运行，打开 SYSTEM 进程被拒绝
  （`OpenProcess` 返回 0，`GetLastError() == 5`），于是抛出 `LOCK_OWNER_LIVENESS_UNKNOWN`。
- `scripts/stop_shine.py` 把这个异常理解为「无法确认身份」→ 拒绝结束该 PID → 保留记录 → 退出码 1。
- `scripts/start_dev.ps1` 把退出码 1 当作致命错误 → `exit 3`。

代码「拒绝盲杀」本身是对的：pid 2060 现在的持有者是系统服务，**绝不能**按 PID 结束它。
错的是**拒绝之后没有出路**——一条早已过期的记录永久堵死了唯一入口。

这里有两个独立缺陷：

1. **PID 回收被误判为「身份未知」。** 系统其实完全能回答「这个 PID 现在是谁」，
   只是不能用调用者无权打开的句柄去问。
2. **无法核对即致命。** 启动器没有区分「服务还在跑」（不能覆盖）与
   「服务早已不在、只是残留记录没法核对」（不该挡住启动）。

## 修复

### 1. 只读进程探针（新增 `backend/shine/comfyops/process_probe.py`）

新增一个**只读**的第二身份来源：通过 WMI 读取每个可见 PID 的创建时刻
（`Get-CimInstance Win32_Process`，约 0.9 s，一次查询全表）。

- 精度：WMI 创建时刻按**微秒**报告，权威的 `GetProcessTimes` 是 100 ns 精度，
  同一进程实测相差 <10 tick（<1 µs）。因此判定**必须用容差**，
  精确相等反而是错的（实测既有 0 tick 也有 3 tick 的情况）。
- 该模块**只回答“这个 PID 现在还持有记录里的那个进程吗”**，从不结束任何进程。
- `process_identity` 保持不变，仍是权威来源；探针只在权威检查不可用时补充判断。
  真正的结束动作依旧由 `ProcessRunner.stop` 在**同一个句柄上二次核对 birth** 后执行。

判定结果：`recycled`（PID 已被回收，记录释放）、`same`（进程还在但打不开，保留待人工核对）、
`gone`（PID 已空）、`unknown`（查询失败，绝不当作死亡）。

### 2. `scripts/stop_shine.py`：释放被回收的 PID

`identity()` 抛 `ValueError` 时改用只读探针分类：

- `recycled` / `gone` → 打印 `[released]` / `[gone]` 后**释放该条记录，不结束任何进程**。
  记录里的进程已被证明不存在，而现在的 PID 持有者属于别人。
- `same` / `unknown` → 仍然 `[refused]` 并保留记录，等人工核对。

既有的安全语义未变：身份不符仍然拒绝结束；遗留清单仍然保留；
`ProcessRunner.stop` 的句柄二次核对未动。

### 3. `scripts/start_dev.ps1`：无法核对的记录不再卡死唯一入口

停止脚本返回非零时：

- 明确告知「已原样保留且不会盲杀它们」，把无法核对的记录归档为
  `<数据目录>/run/processes.unresolved-<时间戳>.json`（只保留最近 5 份），然后**继续启动**。
- 前提是启动器已经独立确认：API 不在运行（`Test-ApiAlive`）、端口随后再检查为空闲。
- 归档失败（文件被占用等）才退出 3 并给出提示。

## 验证（本机实测）

- 复现：修复前双击 → `exit 3`「无法自动收尾上次的进程」。
- 真实残留记录：修复后 `stop_shine.py` 输出
  `[released] pid 2060: the recorded process is gone; this PID now belongs to another process`，
  退出码 0，清单被删除；**pid 2060 的 svchost 仍在运行**（确认没有误杀系统进程）。
- 一键启动：API 8000 / Web 5173 / Worker 全部拉起，
  `/health` 返回 `{"status":"ok"}`，Web `HTTP 200`，
  `/api/v1/system/status` 显示 `worker_online: true`。
- 重复双击（幂等）：输出「工作台已在运行（API 8000 / Web 5173）」并只打开浏览器，退出码 0。
- 兜底路径（人工造一条「进程还在但无法核对」的记录）：停止脚本 `[refused]` 并保留记录、
  退出码 1；启动器打印「部分进程身份无法确认，已原样保留且不会盲杀它们；本次继续启动。」，
  归档记录后成功启动新栈。
- 正常停止：`stop_shine.py` 结束 api/worker/web 及其子进程，退出码 0。
- 回归测试：新增 `backend/tests/test_process_probe.py`（9 项）与
  `backend/tests/test_stack_operations.py`（4 项），连同 `test_powershell_runtime_compat.py`
  与 `test_package_entrypoints.py` 共 38 passed。
  Ruff check：PASS；`ruff format --check` 对本次三个文件：PASS
  （全仓 152 个既有文件的 format 差异是仓库既有状态，本次未触碰）。
- **测试必须从仓库根目录运行**：从 `backend/` 目录运行会让 13 个 Alembic / 迁移用例
  失败（`No 'script_location' key found in configuration`），这是仓库相对配置导致的，
  与本次改动无关；从仓库根目录复验这些用例全部通过。
- 完整后端：`python -m pytest backend/tests -q`（**从仓库根目录**）→ exit 0，
  1 skipped，2 warnings（AnyIO 弃用提示与旧质检路径的空主键查询，均为仓库既有）。
- 前端：`npm --prefix frontend run build`（`tsc -b` + vite）→ exit 0；
  `npm --prefix frontend test` → 32 passed / 8 files。
- 提交：`d7bff50`（main），已推送到 `origin/main`。

## 封装

- `pwsh -File scripts\build_test_package.ps1 -OutDir releases -Verify` → exit 0。
  产物 `ShineVideoStudio-20261102-d7bff50-win64.zip`（1,942,601 bytes），
  `sha256=1a24e82c32997cd2642342b4501e9981471ba07df34db348a28897e661b78caf`；
  `Expand-Archive` 后按 `PACKAGE_MANIFEST.json` 逐文件 SHA-256 复核，417 个文件全部一致。
- 包内已包含本轮修复（`process_probe.py`、`start_dev.ps1`、`stop_shine.py`、本文件）。
- 包内不含：模型权重、生成媒体、SQLite、`.env`、`operator-credentials.txt`、
  `node_modules`、虚拟环境、`docs/evidence`、嵌套 zip。
- `*.lnk` 为本机快捷方式（含本机绝对路径），已加入 `.gitignore`：
  它既不该进仓库，也不该进包，而且它会让工作树永远不干净、打包脚本一直拒绝执行。

## 边界

- 探针只做只读查询，不结束进程；判定「同一个进程」的容差为 1 秒
  （WMI 为微秒精度、实测同进程差值 <10 tick，PID 回收不可能在 1 秒内发生）。
- 「进程还在但打不开」仍会保留记录交人工处理，只是不再挡住启动。
- 本次未改 `process_identity`、未改 `ProcessRunner.stop` 的句柄二次核对、
  未改 `scripts/start_shine.ps1` 的「已有清单则拒绝启动」保护，
  也未改打包安装器 `scripts/install_once.ps1` 的 exit 14 流程（它现在同样受益于
  `stop_shine.py` 的 PID 回收判定）。

## 可复现命令

```powershell
.\backend\.venv\Scripts\python.exe -m pytest backend/tests/test_stack_operations.py backend/tests/test_process_probe.py backend/tests/test_powershell_runtime_compat.py -q
.\backend\.venv\Scripts\python.exe -m ruff check backend/shine backend/tests
.\backend\.venv\Scripts\python.exe scripts\stop_shine.py --data-dir .local-data
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\start_dev.ps1
```
