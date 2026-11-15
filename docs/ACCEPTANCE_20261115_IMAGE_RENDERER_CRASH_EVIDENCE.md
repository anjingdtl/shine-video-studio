# 出图渲染器崩溃取证与「不再无限等待」验收（2026-11-15）

## 1. 报障与事实

用户机现场（《诊断日志》2026-11-15 + 本轮开工截图）：

| 项 | 状态 |
| --- | --- |
| API / Web / Worker | ok / 200 / 1 个在线 |
| L0 | BLOCKED（GPU 硬件门槛） |
| L1 / L2 / L3 | READY |
| L4 | PENDING（SERVICE_VALIDATION_REQUIRED） |
| 出图渲染器 | 启动后进程消失（GONE，8188 空） |
| 图片工坊 | 不可用，停在「等待本机能力验证」 |

崩溃签名（Windows Application 事件 1000/1001）：`torch_cpu.dll` 与 `c10.dll`
`0xc0000005`，偏移固定、时间点成组 → **确定性原生崩溃**，不是业务异常、
不是显存准入拦截、不是端口占用。

三条 `SUBMITTED` attempt 无终态：外部已确认 `prompt_id`，渲染器随即消失，
结果无人回收。

## 2. 本轮改了什么（软件侧），没改什么

原生崩溃发生在用户机的 torch 2.7.1+cu128 与自定义节点（SolAttn/triton 等）
的组合里，**仓库代码无法直接修复该原生崩溃**，本轮也**不声称已修复**。
本轮修的是「崩溃发生时软件把什么说成什么、留了什么证据」——也就是把
一个无法定位、无法收敛的阻滞，变成一次可取证、可比对、可恢复的失败。

| # | 缺陷 | 修复 | 位置 |
| --- | --- | --- | --- |
| R1 | 启动时渲染器自己退出，`_wait_for_owned_listener` 死等 600s，最后报 `IMAGE_RENDERER_LISTENER_TIMEOUT`——崩溃被超时掩盖 | 轮询受管进程组退出状态；整组退出即以 `IMAGE_RENDERER_EXITED_DURING_STARTUP` + 内核退出码失败 | `backend/shine/comfyops/image_renderer.py` |
| R2 | 原生崩溃不留任何痕迹：启动参数没落盘、退出码没记录、日志里没有崩溃行 | 启动即记录**启动参数**（解释器、argv、cwd、端口、解释器环境变量、recipe/adapter 摘要、固定 torch wheel）；失败/消失时记录退出码；新增只读 `GET /setup/renderer-evidence` | `image_renderer.py`、`api_setup.py` |
| R3 | 渲染器可证明消失时，就绪度报 `IMAGE_GRAPH_NOT_VERIFIED`——把人指向「节点没核对」这个错方向 | 报 `IMAGE_RENDERER_GONE`，并附退出码、日志路径与启动参数；启动期退出保留自己的具名码 | `backend/shine/materialprep/runtime.py` |
| R4 | 渲染器在 `/prompt` 之后消失，attempt 永远停在 `SUBMITTED`（无终态），条目无法再渲染、无法对账 | 两条出图路径（`materialprep` / `asset_center`）都把 attempt 落到 `UNKNOWN` + `RECONCILE_REQUIRED`，条目置 `NEEDS_REVIEW`，错误码 `IMAGE_RENDERER_GONE`；原始异常类名留在 attempt payload | `materialprep/execution.py`、`asset_center/execution.py` |
| R5 | 新阻塞码在前端没有登记，只能显示码本身 | 三处码表登记 `IMAGE_RENDERER_GONE` / `IMAGE_RENDERER_EXITED_DURING_STARTUP`（中文原因 + 下一步） | `AssetsWorkspace.tsx`、`api.ts`、`ImageCapabilityCard.tsx` |

### 设计取舍

1. **不自动重投**：渲染器消失后外部结果不可证明，`UNKNOWN` 是永久事实，
   对账仍由人发起（`materialprep.reconcile_run` / 资产中心对账动作）。
   本轮**没有**放宽「UNKNOWN 不可重投」这条既有规则。
2. **不把崩溃说成别的**：渲染器消失与工作流未验证是两个原因，折叠成同一句
   「等待本机能力验证」正是本次阻滞被拖长的原因之一，因此拆开具名。
3. **环境值不落盘**：启动记录只留解释器相关变量的**值**（路径类）与变量名，
   交接令牌等环境**值**一律不写进记录、也不进接口响应。
4. **不确定性不是崩溃**：进程组仍在运行、身份读不出、端口读不出时，
   一律不判崩溃、不判消失。

## 3. 验收证据

### 3.1 新增测试

`backend/tests/test_image_renderer_crash_evidence.py`（9 例，全绿）：

- 渲染器启动中退出 → `IMAGE_RENDERER_EXITED_DURING_STARTUP` + 退出码 3221225477（0xC0000005）；
- 运行中的进程组 → 仍是 `IMAGE_RENDERER_LISTENER_TIMEOUT`，不被误判为崩溃；
- 崩溃证据 → 退出码、解释器、`PYTHONPATH`、固定 torch wheel 齐全，令牌不出现；
- 启动失败保留自己的具名码，不被改写成「消失」；
- 从未启动 → 无崩溃可报（`crashed=False`）；
- `GET /setup/renderer-evidence` 只读返回证据；
- 就绪度 → `IMAGE_RENDERER_GONE` 且**不再**出现 `IMAGE_GRAPH_NOT_VERIFIED`；
- 渲染器正常时 → 仍是 `GRAPH_VERIFIED`，阻塞清单为空；
- 端到端：假渲染器 `/prompt` 之后 500 → attempt 落 `UNKNOWN`、
  `RECONCILE_REQUIRED`、错误码 `IMAGE_RENDERER_GONE`，条目 `NEEDS_REVIEW`。

前端 `imageWorkshopBlockers` 新增 2 例：崩溃具名码渲染 + 启动期退出具名码渲染。

### 3.2 回归

- 后端全量：`backend/tests` 按文件均分分片并行（见 §4 口径），全绿，无
  `FAILED` / `ERROR` 行。
- 定向复跑：`test_image_managed_renderer` / `test_material_render_e2e` /
  `test_material_image_jobs` / `test_asset_center` /
  `test_runtime_control_hardening` / `test_setup_capability_gates` 全绿。
- 前端全量 `npx vitest run --no-file-parallelism`、`tsc -b`、`vite build` 通过。
- `ruff check backend/shine backend/tests` 干净。

## 4. 真机边界（本轮未验证、必须在用户机执行的部分）

1. **原生崩溃本身未修复**：`torch_cpu.dll` / `c10.dll` `0xc0000005` 的最终定位
   仍需在用户机完成。本轮提供的抓手是：
   ```powershell
   Invoke-RestMethod "http://127.0.0.1:8000/api/v1/setup/renderer-evidence?instance_id=<instance>" |
     ConvertTo-Json -Depth 6
   ```
   取到后与**最后一次成功启动**的同名字段逐项比对：
   - `launch.interpreter`（是不是 ComfyUI venv 自己的解释器，还是别处的）；
   - `launch.interpreter_env.PYTHONPATH` / `VIRTUAL_ENV`（有没有混入别处的 site-packages）；
   - `launch.pinned_torch` 与实际安装的 torch 构建是否一致；
   - `exit_code`（0xC0000005 = 3221225477）；
   - `renderer_log` 尾部的 import 失败摘录（哪个节点包没加载）。
2. **未改** torch / triton / 自定义节点的安装与版本策略，未改显存门槛与变体
   契约（`min_free_vram_mb` 仍为 8000），未改 H3 受管渲染器生命周期、
   `owned_process` 的 Job Object 与钉住文件机制。
3. **未做**真实出图回归：本沙箱无 Windows、无 GPU、无 ComfyUI，
   真实渲染与真实崩溃复现只能在用户机执行。

## 5. 回滚

全部改动集中在 `backend/shine/comfyops/image_renderer.py`、
`backend/shine/materialprep/runtime.py`、`backend/shine/materialprep/execution.py`、
`backend/shine/asset_center/execution.py`、`backend/shine/api_setup.py` 与三处前端码表；
新增只读接口与新错误码均为增量，回滚即还原这些文件，无迁移、无数据变更。
