# Shine Runtime Orchestrator V1.0 — 最终工程收尾与全 PASS 验收（2026-11-12）

> 分支：`fix/runtime-orchestrator-final-hardening-20261112`（独立 worktree `F:\ClaudeWorkSpace\projects\shine-orc-hardening`，未触碰 main）
> 基线：`a363a7242412775b3f913ab3110ace252bd43310`（上轮验收基线 `23c57f4` 已在其历史中）
> 验收机：Windows 11 / RTX 5080 16GB / 32GB 内存；ComfyUI `F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base`；MiniMax-H3 Ref2VA INT8
> 本地证据目录：`.local-acceptance/orc-final-20261112/`（gitignored，不入库）
> 文档性质：**工程收尾**，不是新一轮架构重建；所有结论以真实代码、进程证据与测试输出为准

---

## 1. 最终 Commit 与修改文件清单

最终代码提交：`0fbf6386b2c2505ecf52dbafa489af913eb2bfd6`（本文档的 SHA 回填是其后的一个 docs 提交；`git log --stat 0fbf638` 可复核全部代码与测试改动）

| 文件 | 变更性质 |
|---|---|
| `backend/shine/runtime_orchestrator/guard.py` | 生命周期锁：句柄签名修正、失败关闭、线程内可重入、固定锁序文档 |
| `backend/shine/runtime_orchestrator/identity.py` | 记录状态与“OS 可证事实”分离（新增 `live_state`）、H3 记录携带 process/port |
| `backend/shine/runtime_orchestrator/operations.py` | 冲突动作声明式互斥、`expected_state_version` CAS、`state_version_for` |
| `backend/shine/runtime_orchestrator/api.py` | 控制请求可选 `expected_state_version`（409 `RUNTIME_STATE_VERSION_STALE`） |
| `backend/shine/runtime_orchestrator/executor.py` | ensure/stop/release 结果归一 + reconcile 四态结论 + 受保护释放 |
| `backend/shine/runtime_orchestrator/status.py` | 只读快照改用可证事实、ownership 证据化、workflow 健康、当前操作曝光 |
| `backend/shine/comfyops/handoff_startup.py` | H3 启动纳入物理安装锁；锁内让路/拒绝已验证的出图渲染器 |
| `backend/shine/comfyops/image_renderer.py` | 停止纳入安装锁；记录 adapter 摘要；可证死亡记录自动归零；`retire_idle_image_renderer` |
| `backend/shine/comfyops/renderer_release.py` | `release_managed_processes_guarded`：先按序取锁、后开事务 |
| `backend/shine/worker.py` | H3 让路逻辑收敛到 comfyops 单一实现 |
| `backend/shine/api_setup.py` | 无 Worker 兜底释放改走受保护释放（先结束读事务） |
| `scripts/shine_processes.py` | 单个 pid 身份不可读不再毁掉整次启动（记录为不可验证） |
| `frontend/src/api.ts` | 控制请求携带 `expected_state_version`、409 识别、结论/错误码文案 |
| `frontend/src/components/EngineStatusEntry.tsx` | 动作结论一句话反馈、进行中禁用、工作流状态、过期自动刷新 |
| `frontend/src/styles.css` | 结论条样式（沿用既有 token） |
| `frontend/src/test/engineStatus.test.tsx` | +11 条行为用例（共存 15 条） |
| `backend/tests/test_runtime_control_hardening.py` | 新增 24 条针对本次缺口的对抗性用例 |
| `backend/tests/conftest.py` | 测试档案名改用 uuid（`id(dict)` 复用会让长进程出现“同名档案已存在”的假失败） |

无数据库结构变更（`alembic check` 无 drift），未新增迁移。

---

## 2. 上轮五项问题逐条修复

### 2.1 任务一：ComfyUI 物理实例唯一控制权

| 缺口（上轮实际状态） | 本轮修复 |
|---|---|
| H3 启动路径**完全没有**进入安装级生命周期锁：只有 `_installation_evidence` 的 DB 检查，检查与 `Popen` 之间存在窗口 | `start_managed_renderer` 以 `installation.id`（缺省 `instance_id`）为键，把“让路 + 冲突判定 + 写 INTENT”整体放进 `renderer_lifecycle_guard`；进程拉起仍在锁外，由落盘 INTENT 保护 |
| 出图 stop / 受管 release **不取锁**：stop 与 start 并发时，“已停止”的渲染器可能以新 PID 复活 | `stop_image_renderer` 全程持锁；`release_managed_processes_guarded` 先取所有相关安装锁（按 id 升序），再开唯一写事务 |
| `renderer_lifecycle_guard` 在 `CreateMutexW` 失败时 **yield None 静默放行**（fail-open） | 改为 `LifecycleGuardUnavailable` 安全拒绝；`WAIT_FAILED`/异常等待态同样拒绝；`WAIT_ABANDONED` 允许接管 |
| ctypes **未声明 `restype`**：`CreateMutexW` 返回的 64 位句柄被截断为 C int，`ReleaseMutex`/`CloseHandle` 作用在错误句柄上 | 模块级正确声明 `argtypes/restype`，句柄不再被截断；同线程重入用线程局部计数，不重复创建/等待 |
| 出图记录 `stage=RUNNING` 但进程早已死亡时，安装行停在 `RUNNING_PENDING_HEALTH`，此后每次启动都被 `IMAGE_RENDERER_INSTANCE_NOT_STOPPED` 永久拒绝（上轮 R8） | `_provably_gone` 只在“身份可读且不匹配 + 命名组为空 + 端口空闲”或“无 process 且端口空闲”时判定死亡，并在锁内归零；任何不可证情况继续拒绝 |
| H3 与出图的让路步骤与 H3 INTENT 分处不同临界区 | 让路逻辑移入 `image_renderer.retire_idle_image_renderer`，在 H3 锁内再执行一次；锁内遇 `UNVERIFIED` 记录按 `COMFY_INSTALLATION_IMAGE_RENDERER_UNVERIFIED` 具名拒绝 |

三种身份保持严格分离：`installation_identity`（`comfy_instances` 受管行）是锁键；`runtime_identity`（H3 每 attempt 的 run 行）只用于记录；`process_identity`（PID + birth + Job Object）在每次破坏性动作前重新核验。

**锁序**（写入 `guard.py` 文档，禁止反转）：
`安装生命周期互斥（按 id 升序） → SQLite 写事务 → OS 进程句柄/等待`。任何临界区都不在持有写事务时等待互斥量。

### 2.2 任务二：控制指令幂等、版本与并发

| 缺口 | 修复 |
|---|---|
| 同一安装下两个冲突的生命周期动作可被同一批 claim | `claim_pending_operations` 引入 `busy_by_installation` 与 `conflicting_running_operations`：有活跃 owner 的冲突动作在跑、或同批已领取同安装动作时不再领取；`probe`/`reconcile` 永不被挡 |
| 无“过期页面”防护 | `ControlBody.expected_state_version` + `state_version_for()`：不一致即 409 `RUNTIME_STATE_VERSION_STALE`；**同一幂等键的重放不受版本影响**（双击仍是一次意图） |
| 结果语义只靠 `SUCCEEDED/FAILED` | 每个动作的 `result.outcome` 明确为 `STARTED/REUSED`、`STOPPED/ALREADY_STOPPED`、`RELEASED/PARTIAL`、`NOTHING_TO_RECONCILE/RECONCILED/NEEDS_REVIEW/FAILED`；拒绝走 `REFUSED` + `failure_code` |
| 未知状态可能被当作“可重启” | `ensure` 遇 `UNCERTAIN` 记录具名拒绝；`reconcile` 只在“进程可证已死”时清理，且不重发任何 `/prompt` |
| 旧执行者覆盖新结果 | 保留 `worker_id + state_version` CAS；补齐“死 owner 可被新 Worker 接管”的用例 |

未新增第二套 GPU 队列：`ResourceScheduler` 仍是唯一准入账本。

### 2.3 任务三：真实健康状态与进程身份

`status.py` 的判定改为“OS 可证事实优先”：

- `image_renderer_state()`（`managed_process_state` → PID/birth/命名组/端口）而非记录 `stage` 决定 RUNNING；
- 记录 `stage=RECONCILE_REQUIRED` 一律 `BLOCKED_UNVERIFIED`（未核对的外部副作用不会被“进程已死”抹掉）；
- `STARTING` 只在存在 `INTENT/LAUNCHED/LISTENING` 记录且未证 RUNNING 时成立；
- `ownership.verified` 来自真实证明（出图 RUNNING，或 H3 记录的 pid/birth/组同时成立），不再等于“有记录”；
- `health.workflow`：记录内 `adapter_sha256` 与配方固定值比对，得出 `VERIFIED / MISMATCH / UNVERIFIED / NOT_RUNNING`（只比摘要，不每次重算安装哈希）；
- HTTP 短时不可达只降级为 `DEGRADED`（进程仍证存活时），探活 4s 缓存 + 单次重试，轮询不构成探活风暴；
- 快照全程只读：无进程启动、无模型加载、无写库（`test_status_is_read_only_and_shaped` 断言）。

真机观察（真实受管 ComfyUI 运行中）：

```json
{"state": "READY_IDLE",
 "health": {"worker": "UP", "api": "UP", "comfy_http": "UP", "workflow": "VERIFIED"},
 "ownership": {"managed": true, "verified": true, "port": 8188, "pid": 27964},
 "resource": {"active_leases": 0, "reconcile_leases": 0, "waiting_leases": 0},
 "recovery": {"required": false, "safe_action": "stop", "advice": "引擎就绪空闲。"}}
```

### 2.4 任务四：恢复机制与 Web 状态收束

- `reconcile` 从“永远 `reconciled: false`”改为四态结论，并**显式声明不重发**：`NOTHING_TO_RECONCILE` / `RECONCILED`（已清理可证失效记录）/ `NEEDS_REVIEW`（保留原提交证据，等人工）/ `FAILED`（证据读取失败）。
- 未知提交沿用原证据链：`Job.status=NEEDS_REVIEW` 且 `failure_code ∈ UNKNOWN_SUBMISSION_CODES` 只被**列出**，不被处理；操作本身从不 POST `/prompt`。
- 前端状态条：动作后给一句中文结论；`current.operation_id` 存在时动手按钮显示「处理中…」并禁用（只读的「检查连接」不受影响）；409 过期自动刷新并提示「引擎状态已更新，请确认后再操作」；详情区增加「运行配置」一行；不新增工程控制台。
- 停止按钮仍只在 `safe_action === 'stop'` 时出现（后端判定安全才出现）。
- 轮询为纯 GET，快照只读，探活有缓存，不产生重复进程/重复写入。

### 2.5 任务五之外的一个真机缺陷（本轮新发现并修复）

半死栈重启时，`scripts/shine_processes.py` 对进程树里**任意一个** pid 调用 `process_identity`，一旦 OS 拒绝描述（`LOCK_OWNER_LIVENESS_UNKNOWN`，常见于枚举与查询之间刚退出的子进程）就整次启动失败，留下“api/worker 已起、无清单、web 未起”的半死栈。修复：单个 pid 不可读记录为 `birth=None`（既有语义即“不可验证、不据此杀进程”），不再中断启动。真机 8/8 通过（`orc09a.out`）。

---

## 3. 实际控制调用链与互斥机制

```
Web EngineStatusEntry ──GET /runtime-orchestrator/status──▶ status.runtime_snapshot（只读派生）
        │ 显式动作（仅后端给出的 safe_action）+ expected_state_version
        ▼
POST /runtime-orchestrator/{ensure|stop|probe|reconcile|release}
        │ create_or_get_operation：幂等键 + CAS 版本 + 冲突检查
        ▼
runtime_control_operations（唯一新表，扁平）
        │ Worker tick：claim（跳过同安装冲突动作）→ 执行 → CAS 完成
        ▼
runtime_orchestrator.executor
        ├─ ensure/stop → image_renderer（内部持 installation 锁）
        ├─ release     → release_managed_processes_guarded（按 id 升序取锁 → 单事务）
        └─ reconcile   → 只读证据 + 唯一安全清理（不重发）
                     ▼
              comfyops 既有执行器（OwnedProcessGroup / RendererSupervisor / ResourceLease）
```

互斥分三层，各司其职：

1. **启动栈互斥**：`Global\ShineStackStartup-<sha(data_dir)>`（既有），覆盖“查清单→复用/收尾→选端口→拉起→校验→写清单”。
2. **安装生命周期互斥**：`Global\ShineRendererLifecycle-<sha(installation_id)>`，覆盖所有会改变受管进程集合的决定；临界区只含“读事实 + 写 INTENT/状态翻转”，不含模型加载、HTTP 长轮询、`Popen`。
3. **GPU 资源准入**：既有 `ResourceScheduler` 单账本（未新增队列）。

H3 handoff / 封闭队列 / 审片 / 恢复流程未改动合同：`start_managed_renderer` 的 INTENT→LAUNCHED→LISTENING→RUNNING 证据链、`StartupHeartbeat`、`RendererSupervisor.require_closed_restart()`、lease 绑定与审计全部保留，只在最前面加了一层安装锁与让路。

---

## 4. ORC-01～ORC-16 验收矩阵

| 编号 | 结论 | 证据 |
|---|---|---|
| ORC-01 十路并发启动 | **PASS（真机）** | 10 个 `start_shine.ps1` 并发，36.4s 全部退出 0；`created=1 / reused=9`；唯一 `processes.json`（api/web/worker 各 1）；8210 与 5211 各只有 1 个监听且属于记录树；`/health` 200；快照 worker=UP（`orc01-evidence.json`、`orc01-logs/`） |
| ORC-02 20 次启停循环 | **PASS（真机）** | 20/20 轮：启动成功 → `/health` ok → 停止退出 0 → 清单消失 → 8210/5211 释放；每轮约 16.7s，无误杀、无残留、无双 Worker（`cycles-evidence.json`、`orc02.out`） |
| ORC-03 被占端口 / 外部实例 | **PASS（真机）** | 8299 由外部 socket 占用 → 启动非零退出、报错点名该端口、外部监听存活、未写清单（`conflict-evidence.json`） |
| ORC-04 PID 回收 | PASS（测试） | `test_process_probe`/`test_stack_operations` 全过；`shine_processes` 对不可读 pid 改为“不可验证”而非终止 |
| ORC-05 H3 与素材同装竞争 | PASS（测试） | `IMAGE_RENDERER_INSTALLATION_BUSY_H3`、`..._UNCERTAIN`、H3 锁内让路/具名拒绝、`retire_idle_image_renderer` 三条路径均有断言 |
| ORC-06 H3 handoff 证据链 | PASS（测试） | `test_comfy_handoff_barrier/journal`、`test_production_series` 全过；合同未改 |
| ORC-07 `/prompt` 超时不重投 | PASS（测试） | `test_comfy_recovery` 全过；reconcile 只列出 `NEEDS_REVIEW + UNKNOWN_SUBMISSION_CODES`，不重发 |
| ORC-08 刷新/断连不重启 | PASS（测试 + 真机轮询） | 快照只读（无写库、无启动）；ORC-01 检查阶段对同一栈连续轮询未改变任何 pid/监听 |
| ORC-09 Worker 中止/半死栈 | **PASS（真机）** | 杀掉 web → 再启动自动识别 missing 并重整，8/8 断言通过（`halfdead-evidence.json`）；杀掉 worker → 重启成功、单 Worker、快照 worker=UP，6/6（`workerkill-evidence.json`）；同安装冲突动作不被同批领取、死 owner 可被接管（测试） |
| ORC-10 生产中 STOP | PASS（测试） | 出图在途/租约在途具名拒绝；release 忙判定含 SUBMITTING/QC_PENDING。真机部分见 §7 |
| ORC-11 能力未就绪不谎称 | PASS（测试 + 真机） | `health.workflow` 三态断言；真机受管渲染器实测 `VERIFIED`；`STARTING/READY` 只在进程+HTTP 双证 |
| ORC-12 升级与渲染冲突 | PASS（测试） | `_refuse_start_under_managed_lifecycle` 在安装锁内拒绝裸 `Popen` START（`INSTANCE_LIFECYCLE_LOCKED`） |
| ORC-13 既有回归 | **PASS（全量套件）** | 见 §5；Mock 不代替真实出片 |
| ORC-14 并行 PR 协作 | PASS | 素材中心业务模型零改动；仅复用其忙码与执行层 |
| ORC-15 故障隔离 | PASS（真机） | 无绑定/无渲染器时 `/status` 返回 `NOT_BOUND` 且 `/health` 200；控制面不阻塞文字与浏览 |
| ORC-16 生产/演示模式 | PASS（真机） | mock 栈清单 `render_provider=mock`；comfyui 栈 `Worker mode: comfyui`；控制面从不改 `render_provider` |

硬门槛自查：重复受管进程 0；已提交任务错误重复提交 0；不明进程误杀 0；同安装生命周期并发冲突 0；控制层导致的 H3 回归 0；素材中心能力回归 0；迁移/旧库升级破坏 0。

---

## 5. 全量测试的真实数量

| 项 | 结果 |
|---|---|
| 后端全量 `pytest tests -n 8 --dist loadfile` | **2409 passed / 2 skipped / 0 failed**（297s，`pytest-full-3.log`；EXIT=0） |
| 新增定向套件 `test_runtime_control_hardening.py` | 24 passed |
| 控制层定向（orchestrator + image + release） | 49 passed |
| H3/实例定向（production_series / managed_startup / v13_instances / handoff_* / startup_guard） | 112 passed |
| `ruff check backend/shine backend/tests` | All checks passed |
| `alembic upgrade head` + `alembic check` | head `f180c0010001`，No new upgrade operations detected |
| 前端 `tsc --noEmit` | 0 错 |
| 前端 `vitest run src/test/engineStatus.test.tsx` | 11 passed |
| 前端 `npm run build` | 成功（881.87 kB js / 196.58 kB css） |

历史基线 `2286 passed / 2 skipped` 来自 `f897b26`，与当前 main（已合入导演 UI 等新功能）不是同一棵树，**不能直接相减**；本轮以本树 2409 为准确认零回归。

首次全量运行出现 1 例失败 `test_production_preflight_closeout::test_a_size_the_profile_cannot_map_is_refused_at_package_build`，原因是用例默认档案名取自 `id(dict)`，同一进程内地址复用会撞名（409 “profile already exists”）；该用例单独运行必过。已把默认名改为 uuid，重跑全量 2409 全绿。这不是产品缺陷，是测试夹具的稳定性修复。

---

## 6. Windows 真机测试与真实生产证据

### 6.1 已完成（真机、可复现）

1. **十路并发**：`orc01_concurrent.py` → `orc01-evidence.json`（36.4s，1 建 9 复用）。
2. **二十轮启停**：`orc_stack2.py cycles 20` → `cycles-evidence.json`（20/20）。
3. **端口冲突**：`orc_stack2.py conflict` → 外部监听存活、具名拒绝。
4. **半死栈恢复**：`orc_stack2.py halfdead` → 8/8（并因此修复了 `shine_processes.py` 的启动中断缺陷）。
5. **Worker 被杀恢复**：`orc_stack2.py workerkill` → 6/6。
6. **真实受管引擎**：以 `-Provider comfyui` 启动生产栈，Worker 经受管路径（`ensure_image_renderer` → `OwnedProcessGroup` → `managed_comfy_bootstrap.py`）拉起真实 ComfyUI（pid 27964 监听 8188），统一快照给出 `READY_IDLE / verified / workflow VERIFIED`（§2.3 原始 JSON）。快照、就绪判定、ownership 证据链在**真实进程**上成立。

### 6.2 未完成（如实标注）

**真实出图与真实 H3 视频产出：NOT_VERIFIED。**

原因：本会话的执行沙箱在工具调用结束时回收该调用派生的常驻进程树，真实栈无法跨调用存活；一次 `ensure` 完整走完需要 3–5 分钟模型加载，期间的会话轮次会被打断。已尝试两次，均在渲染器就绪后被回收，因此**没有**任何本轮产出的图片或视频可以冒充 PASS。

复现命令（用户在真实桌面环境执行，约 5–12 分钟）：

```powershell
cd F:\ClaudeWorkSpace\projects\shine-orc-hardening
$env:SHINE_BACKEND_ENV = 'F:\ClaudeWorkSpace\projects\shine-video-studio\backend\.venv'
$env:PYTHONPATH        = 'F:\ClaudeWorkSpace\projects\shine-orc-hardening\backend'
pwsh -File scripts\start_shine.ps1 -Provider comfyui -AutoPorts
# 浏览器打开输出的 Web 地址 → 顶栏「本地制作引擎」→ 启动引擎 → 素材中心出图 / 导演模式出片
python scripts\stop_shine.py --data-dir <上一步输出的数据目录>
```

`orc_production.py`（`--step engine|image|stop`）已内置同样的驱动逻辑，可在交互式会话中一次跑完并落 `production-evidence.json`。

---

## 7. 遗留风险与未验证项

1. **真实 H3/出图未产出**（上节）。ORC-05/06/07/10 的“真机渲染”部分维持 **测试级 PASS + 真机 NOT_VERIFIED**。
2. **`shine_processes.py` 的语义变化**：不可读 pid 从“中断启动”变为“记录为空身份”。空身份条目在停止时按既有规则视为不可验证、只归档不杀；若该 pid 实际属于本次启动且仍存活，则需人工回收。相较“整次启动失败留下半死栈”，这是更安全的取舍。
3. **`_make_way_for_h3` 的严格性**：安装上存在 `UNVERIFIED` 出图记录时 H3 会具名拒绝。这是刻意的安全拒绝，但需要用户点一次「核对恢复」才能继续。
4. **CAS 版本粒度**：`state_version` 统计的是该安装的终态操作数，不含进程状态变化；它只用于拒绝“旧页面”，不作为状态真相。
5. **`release` 仍是全局动作**：一次释放会遍历所有受管记录并按安装锁串行；`installation_id=workbench` 只是审计标签。
6. GitHub CI 未运行（本轮以本地全量套件为准，符合任务约定）。
7. 前端状态条未做浏览器 E2E（仅新增只读展示与显式按钮，Vitest 覆盖 11 条行为）。

---

## 8. 结论

**PARTIAL —— 达到“控制层工程收尾完成”，未达到“真实生产全 PASS”。**

- 五项收尾任务中，任务一～四均已落地并有针对性测试与真机证据；任务五的 A（启动唯一性）、B 的引擎侧、C 的异常恢复项已在真机通过。
- 任务五 D（真实图片与真实 H3 视频产出）因本会话沙箱无法维持常驻栈而 **NOT_VERIFIED**，按最终原则不以 Mock 或推断替代。
- 安全硬门槛全部满足：0 重复受管进程、0 错误重投、0 误杀、0 同安装生命周期并发冲突；全量测试与静态检查全绿。

建议合入前在用户桌面环境执行 §6.2 的一次真实小样；若产出 artifact 且技术 QC 通过，即可把 D 项与 ORC-05/06/07/10 的真机部分补记为 PASS，届时可标记为全 PASS。
