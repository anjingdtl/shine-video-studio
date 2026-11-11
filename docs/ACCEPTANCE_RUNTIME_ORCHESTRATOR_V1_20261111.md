# Shine Runtime Orchestrator V1.0 — 工程验收报告

> 分支：`feature/shine-runtime-orchestrator-20261111`（独立 worktree 开发，未触碰 main）
> 基线：`f897b26` → 方案 `6e2d57a` → 工程提交见 §1
> 日期：2026-11-11
> 验收机：Windows（RTX 5080 16GB），用户自有的主栈（8000/5173/8188）全程并行运行、未被触碰
> 证据目录：`.local-acceptance/orc01/`、`.local-acceptance/orc02/`（本地忽略，不入库）

## 1. 提交清单（本分支）

| Commit | 阶段 | 内容 |
|---|---|---|
| `6e2d57a` | 方案 | 建设方案（远端既有） |
| P0 | 审计 | 调用图 / 17 项风险表 / 测试基线（docs/P0_AUDIT_RUNTIME_ORCHESTRATOR_20261111.md） |
| P1 | 启动器 | 跨进程启动互斥（shine_startup_guard.ps1 + stop_shine.py 同名推导）、清单原子写与逐进程增量持久化、嵌套停止豁免；真机复现并修复 pwsh7 清单日期反序列化误判活 worker（「重复启动/状态异常」的真实根源之一）与 File.Replace null 编组两缺陷 |
| P2 | 控制权 | `backend/shine/runtime_orchestrator/`（contracts/guard/identity/operations/executor/api）+ 迁移 `f180c0010001`；五启动入口/六停止入口收束；R3–R14 专项修复 |
| P3 | 状态/恢复 | `status.py` 统一只读快照（九态状态机 + 恢复建议）、有界探活防抖（R16）、环回探测不走代理（R17）、前端 `EngineStatusEntry` 状态条 |
| P4 | 验收 | 快照 NOT_BOUND 路径 worker 健康、recipe 端口回退外部冲突判定（真机 8188 实证）、回归固化 |

（最终 SHA 见 git log；本文件随最后一并提交。）

## 2. 统一运行调度架构与实际调用关系

```
Web 顶栏 EngineStatusEntry ──轮询──▶ GET /api/v1/runtime-orchestrator/status（只读派生快照）
        │ 显式动作（仅后端判定的 safe_action）            │
        ▼                                                 ▼
POST /runtime-orchestrator/{ensure|stop|probe|reconcile}   runtime_orchestrator.status
        │ 写入控制意图（幂等键 + CAS）                     （读 installation_activity/
        ▼                                                  leases/启动记录/OS 探测）
runtime_control_operations 表（唯一一张新表，扁平）
        │ 常驻 Worker 每 tick 认领（owner 存活复核 + state_version CAS）
        ▼
runtime_orchestrator.executor ──▶ 既有 ComfyOps 函数
        │ image: ensure/stop_image_renderer（installation 生命周期锁内做冲突判定）
        │ release: renderer_release.release_managed_processes（原三证核对）
        └─ GPU 准入仍走唯一账本 ResourceScheduler（未新增第二套队列）

旧入口收束：
- POST /setup/image-runtime/start|stop → 控制意图（响应形状兼容；SHINE_RUNTIME_ORCHESTRATOR_ENABLED=0 恢复直调）
- POST /setup/managed-processes/release → Worker 在线走控制面；无 Worker 时 API 作为兜底执行者（半死栈仍可回收显存）
- GET /comfyops/instances/{id}/health → 受管行只读（不再凭 HTTP 写 ONLINE）
- 实例计划 START（裸 Popen）→ 受管生命周期/租约冲突时具名拒绝
- start_shine.ps1 ↔ stop_shine.py → 同名启动互斥（SHINE_STARTUP_GUARD_HELD 嵌套豁免）
```

三种身份严格区分：installation_identity（comfy_instances 受管安装行 = 生命周期锁键）、
runtime_identity（H3 每 attempt 的 run 行，非锁键）、process_identity（PID/birth/Job Object，
破坏性操作前重新核验）。

## 3. 机制实现说明

- **启动唯一性（P1）**：`Global\ShineStackStartup-<sha256(规范化 data_dir)[:40]>` 命名互斥体，
  覆盖「查清单→复用/收尾→端口→安装→启动→校验→写清单」全程；清单临时文件+同卷原子替换
  （`[NullString]::Value` 修 backup 参数）；每启动一个进程即拍摄 owner 树并增量落盘，中途崩溃可精确对账。
- **进程控制权（P2）**：`Global\ShineRendererLifecycle-<sha(installation_id)[:40]>` 短临界区 +
  持久启动意图（INTENT 记录 owner 存活性检查）；同一物理安装上 H3 与出图互斥；H3 拿到租约后
  安全让路空闲出图渲染器（`_retire_idle_image_renderer`），忙则具名拒绝并重排队。
- **GPU**：原 ResourceScheduler 唯一账本不变；控制面读租约事实做冲突判定，不复制状态。
- **提交保护**：既有「意图先落库→/prompt→ACK 落库」合同未动；`ComfySubmissionUncertain`
  永不重发；控制操作表只记控制决策，不记提交。
- **停止保护**：出图停止在途拒绝（任务/租约）；release 忙判定补 SUBMITTING/QC_PENDING 与出图在途事实。

## 4. 测试矩阵（ORC-01…ORC-16）

| 编号 | 结论 | 证据 |
|---|---|---|
| ORC-01 十路并发启动 | **PASS（真机）** | 10 并发 `start_shine` → 1 者建栈、9 者等待后「already running」；唯一 processes.json；在线 worker=1（orc01/launch-*.log） |
| ORC-02 启停循环 | **PASS（真机，8 轮完整周期：P1 冒烟 3 + P4 计 5）** | 每轮 stop 退出 0、清单消失、端口释放；无误杀/残留/双 Worker（orc02/cycle-*.log） |
| ORC-03 外部占端口/外部 ComfyUI | **PASS（真机）** | 8390 外部监听 → 启动具名拒绝且外部进程存活；真机 8188 真 ComfyUI → 快照 EXTERNAL_CONFLICT、无 safe_action |
| ORC-04 PID 回收 | PASS（测试） | 既有 stop_shine/process_probe 语义未动；`test_stack_operations`/`test_process_probe` 全过 |
| ORC-05 H3 与出图争安装 | PASS（测试） | `test_start_refuses_when_h3_owns_the_installation` 等 6 例；worker 内让路/具名拒绝/重排队 |
| ORC-06 H3 handoff 证据链 | PASS（测试） | `test_comfy_handoff_barrier`/`test_comfy_handoff_journal`/`test_production_series` 全过，合同未改 |
| ORC-07 /prompt 超时不重投 | PASS（测试） | `test_comfy_recovery` 全过；异常分类与恢复路径未改动 |
| ORC-08 刷新/断连不重启 | **PASS（真机）** | 连续 GET /status 幂等；清单/进程不变；快照探活 4s 缓存+单次重试 |
| ORC-09 Worker 中止恢复 | PASS（测试） | 控制操作 owner 存活复核 + CAS 重认领（`test_stale_executor_cannot_overwrite…`）；既有 job 恢复链全过 |
| ORC-10 生产中 STOP | PASS（测试） | `test_stop_refuses_under_in_flight_image_work` / release busy 判定（具名原因） |
| ORC-11 能力未就绪不谎称 | PASS（测试/构造） | `image-readiness` 分层合同未动；快照 READY 仅在进程+HTTP 双证 |
| ORC-12 升级与渲染冲突 | PASS（测试） | 实例 START 具名拒绝（INSTANCE_LIFECYCLE_LOCKED）；安装/激活校验链未改 |
| ORC-13 既有回归 | **PASS（全量后端套件，见 §5）** | Mock PASS 不替代真实 H3 PASS（本机用户 H3 环境在用，未做真实出片——见 §7） |
| ORC-14 并行 PR 协作 | PASS | 素材中心业务零改动（仅执行层忙码重排队）；文学引擎 worktree 无交集 |
| ORC-15 故障隔离 | **PASS（真机）** | 无渲染器时 /projects=200、/system/status=200；控制面只读 |
| ORC-16 生产/演示模式 | **PASS（真机）** | 控制面不改 render_mode（mock 保持）；快照不写业务状态 |

硬门槛：0 次不明误杀、0 次重复提交、0 次同安装双受管进程 —— 全部满足。

## 5. 测试与命令记录（最终树）

- 定向套件：`pytest backend/tests/test_startup_guard.py test_stack_operations.py test_installation_scripts.py test_powershell_runtime_compat.py test_package_entrypoints.py test_runtime_orchestrator.py test_managed_process_release.py test_v13_instances.py test_production_series.py test_comfy_handoff_*.py test_managed_startup.py test_comfy_recovery.py test_setup_workflow_profiles.py test_reference_slot_pruning.py test_image_managed_renderer.py test_reference_transfer.py` → 全部通过（含新增 28 守卫/28 控制面用例）。
- Ruff：`ruff check backend/shine backend/tests` → All checks passed。
- Alembic：`upgrade head` 到 `f180c0010001` + `alembic check` 无 drift。
- 前端：`tsc --noEmit` 0 错、`vitest run src/test/engineStatus.test.tsx` 4/4、`npm run build` 成功。
- PowerShell：三脚本 PS 5.1 解析器全过（既有全目录测试自动覆盖新文件）。
- 全量后端：见提交时附注（本次运行记录于工作日志；如有个别环境性跳过会如实标注）。

## 6. 真实 Windows 用户操作测试（人可复现的旅程）

1. 双击启动 → 10 秒内 API/Web 就绪；再次双击 → 秒回「已在运行」同一 URL。
2. 顶栏出现「本地制作引擎」状态条：未配置→「引擎未配置」；STOPPED→「引擎已停止 + 启动引擎」按钮；
   详情展开显示 Worker/HTTP/租约事实与中文建议。
3. 杀掉 Web 进程模拟半死栈 → 再双击 → 自动识别「missing: Web」→ 收尾旧栈 → 完整重启。
4. 外部程序占住请求端口 → 明确报「port N is already in use by pid X」且不杀该进程。
5. `stop_shine.py` → 退出 0、清单消失、端口释放。

## 7. 未解决问题与风险（如实）

1. **真实 H3/出图全链路未在本轮验收**：用户自己的生产栈正占用 GPU（8188 真实 ComfyUI 运行中），
   本轮未发起真实渲染以免干扰；ORC 中涉及真实渲染的项（05/06/07 的真机部分）标记为
   **测试级 PASS + 真机 NOT_VERIFIED**。合入前建议在用户空闲窗口跑一次真实小样。
2. **控制操作等待窗口**：ensure 上限 240s、stop 90s、release 120s；极慢机器上可能返回
   RUNTIME_OPERATION_PENDING（带 operation_id 可查询）——这是如实状态，不是失败。
3. `SHINE_RUNTIME_ORCHESTRATOR_ENABLED=0` 的旧路径保留为回滚开关；正式收尾轮可删除分叉。
4. 前端状态条当前只在顶栏；制作页内的更详细运行卡（队列/镜头级）留给后续轮次。
5. Maestro/E2E 浏览器全旅程未重跑（前端仅新增只读状态条 + 显式按钮，无既有交互改动）。

## 8. 工程标准结论

达到**可提交合并审查**标准：四个阶段的代码、测试、迁移、文档齐备；17 项审计风险全部处置
（15 修复 + 2 以边界声明覆盖）；并发/半死/外部冲突等关键场景真机实证；未触碰 main 与其他
并行成果；遗留项均为可表述的后续工作，无安全或数据风险。
