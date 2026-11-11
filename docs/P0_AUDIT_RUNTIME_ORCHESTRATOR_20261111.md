# P0 审计：Shine Runtime Orchestrator（2026-11-11）

> 分支：`feature/shine-runtime-orchestrator-20261111`，基线 `f897b26`，方案提交 `6e2d57a`。
> 本文档是《SHINE_RUNTIME_ORCHESTRATOR_V1_IMPLEMENTATION_PLAN_20261111》的 P0 交付物：
> 真实调用点清单、风险点、兼容性边界与测试基线。全部结论来自对 `6e2d57a` 树的代码阅读
> 与抽查核验；未做运行时复现的条目在风险表中标注「静态」。

## 0. 本轮归属声明

本轮只治理**统一运行调度与控制层**（进程生命周期、启动唯一性、控制权收束、状态快照、
断连恢复）及必要的关联接口。不重写素材中心业务逻辑、四阶段文学引擎、导演风格引擎、
安装器业务；不新增 GPU 队列；不改 H3 提示词/工作流合同。

## 1. 启动入口调用链

### 1.1 Web 栈（API / Worker / Web）

```
双击 lnk / START_APP.cmd / SETUP.cmd
  └─ scripts/start_dev.ps1            （仓库入口：查 processes.json → 半死栈收尾 → 选端口）
       └─ scripts/start_shine.ps1     （真正启动：alembic 迁移 → release_residual_backends →
                                        起 api/review/worker/web → 等 listener/页面 200 →
                                        shine_processes.py 记录出生身份 → 写 processes.json）
scripts/start_shine.ps1               （安装包直连入口，-OpenBrowser -AutoPorts）
scripts/stop_shine.py                 （按 processes.json 的 PID+birth 停止；无法核对→归档）
scripts/release_residual_backends.py  （调 renderer_release.release_managed_processes +
                                        residual_owned_processes 清残留渲染器/审片进程）
```

事实：
- `start_shine.ps1:200-236` 先读 `run/processes.json` 判定「已在运行 / 半死栈」；
  `start_dev.ps1:164-217` 同样先读同一份清单。**两个入口都没有跨进程互斥**：
  并发双击时两边都可能在清单尚未写入时通过检查（TOCTOU），各自选端口、各自拉起一套栈。
  `-AutoPorts` 下两套栈会落在不同端口，用户看到两个工作台。
- `start_shine.ps1:364-376` 启动前调用 `release_residual_backends.py`；该动作若与另一
  启动入口并发执行，可能互相释放对方刚启动的后端（受所有权证明保护，但记录会互相干扰）。
- 就绪判定：API `/health` + 端口监听 PID 与记录一致 + Web 页面 200 + Worker 进程出生身份
  （`start_shine.ps1:444-519`）。清单写入无原子替换（直接 `Set-Content`）。

### 1.2 ComfyUI 受管进程（五个启动入口）

| # | 入口 | 文件:行 | instance 身份 | 租约 | 持久记录 |
|---|---|---|---|---|---|
| A | Worker H3 首启 | `comfyops/handoff_startup.py:553 start_managed_renderer` ← `worker.py:1162` | run 行（installation+attempt 派生） | `h3:<job_id>` 独占租约 | `renderer-startup:<run_id>` |
| B | API 出图直启 | `api_material.py:837` ← `image_renderer.py:125 start_image_renderer` | 安装行 id | **无** | `renderer-startup:image:<安装id>` |
| C | Worker 出图 | `materialprep/execution.py:316` ← `image_renderer.py:247 ensure_image_renderer` | 安装行 id | 外层 `resource_slot("IMAGE")` | 同上 |
| D | 设置校验探针 | `api_setup.py:534/628`、`api_comfy.py:256` ← `service_validation.py:128/236` | recipe 端口 | 无（仅 `require_idle_configuration`） | **无持久记录** |
| E | 旧实例计划执行 | `api_instances.py:61` ← `comfyops/instances.py:345 execute_operation`（裸 Popen） | 安装行 id | 无 | `installation_attempts` |

### 1.3 停止入口（六个）

| # | 入口 | 文件:行 | 保护 |
|---|---|---|---|
| S1 | `POST /setup/managed-processes/release` | `api_setup.py:513` ← `renderer_release.py:391` | IN_FLIGHT 判定 + 三证核对 |
| S2 | `POST /setup/image-runtime/stop` | `api_material.py:855` ← `image_renderer.py:287` | 仅记录状态 RUNNING 才停；**无在途任务保护** |
| S3 | Worker series 收尾 | `worker.py:1405` / `renderer_supervisor.py:179/198` | drain/关闭态 + 归档哈希 |
| S4 | Worker tick 对账 | `worker.py:3298` ← `handoff_startup.py:760/843/389`、`production_series.py:1314` | PID/birth/Job 成员证明 |
| S5 | 启动器残留回收 | `scripts/release_residual_backends.py:78` | 同 S1 + 审片进程命令行识别 |
| S6 | 实例计划 STOP | `api_instances.py` ← `instances.py:227 ProcessRunner.stop` | 需批准计划；单 PID，不杀树 |

## 2. GPU 租约与任务提交链

- `ResourceScheduler`（`scheduler.py`）：owner 键控全局独占租约（`h3:*` / `image:*` /
  `tts:*` / `agent:*` / `review:*`），任何 `ACTIVE`/`RECONCILE_REQUIRED` 存在即拒绝
  一切 admit（`scheduler.py:73-84`）；WAITING 60s TTL；`advance_epoch` 防旧序复活。
  **这是唯一 GPU 准入账本，本轮不新增第二套。**
- 三个外部提交点（全部先落意图、后提交，符合方案要求）：
  - H3：`worker.py:2775-2894`（`attempt.prompt_graph_sha256` + `COMFY_SUBMIT_INTENT`
    审计先 commit，`submit_prepared` 在写事务之外）。
  - 素材中心：`asset_center/execution.py:811-870`（`LibraryTaskAttempt(SUBMIT_INTENT)` 先落）。
  - 物料出图：`materialprep/execution.py:610-652`（`MaterialImageAttempt` 先落）。
- 异常分类（`comfy/client.py:27-72`）：`ComfyUnavailable`=未送达（可重试）；
  `ComfySubmissionUncertain`=结果未知（禁止重发）；恢复从不自动 POST /prompt。
- 恢复入口：worker tick `recover_expired_jobs`（`worker.py:3279`）→
  `adopt_persisted_artifact` / `find_submission`（history 对账）/ NEEDS_REVIEW 留人；
  素材中心 `reconcile_completed_history`（API）；文本链 `NOT_SENT_PROVEN`/`UNCONFIRMABLE`。

## 3. 状态与探活现状

- `GET /api/v1/system/status`（`main.py:590`）：worker 心跳、`_h3_connected`（0.75s 单次
  HTTP 探活）、job 计数、`production_mode` 汇总（RECONCILE_REQUIRED 顶置）。前端
  `useStudio.ts:112` 8s 轮询。
- 探活分级现状：L1 进程层（PID/birth/Job Object，`owned_process.py`、
  `renderer_release.py:271/312`）已有；L2 传输层（HTTP，本机 `trust_env=False` 已在
  client 层强制）；L3 能力层（`materialprep/runtime.py` 指纹/家族验证）。缺口在
  **聚合**：四个「真相」分散在 system/status、image-readiness、instances/health、
  managed-processes，前端各拼各的。

## 4. 风险点表（P1–P3 治理对象）

| # | 风险 | 证据 | 严重度 | 治理阶段 |
|---|---|---|---|---|
| R1 | 启动器无跨进程互斥：并发双击可各起一套栈（AutoPorts 下双栈并存） | `start_shine.ps1:200`、`start_dev.ps1:164` 均锁外读清单 | 高 | P1 |
| R2 | 清单写入非原子；失败路径无「启动中意图」持久化 | `start_shine.ps1:511` | 中 | P1 |
| R3 | B（API 出图直启）无租约、不看 H3 run 行 → 与 A 竞争同一物理端口，可短暂拉起第二个 ComfyUI | `image_renderer.py:144-156` 只查安装行 + 自己的记录 | 高 | P2 |
| R4 | 出图渲染器常驻（B/C 启动后）会以 `COMFY_INSTALLATION_BINDING_CHANGED` 挡住 H3，worker 无自动让路 | 安装行置 `RUNNING_PENDING_HEALTH`（`image_renderer.py:219`） | 高 | P2 |
| R5 | S1 忙判定集合 `{"RUNNING","SUBMITTED"}` 漏掉 `SUBMITTING`/`QC_PENDING`；出图记录无 lease_id 永不算忙 | `renderer_release.py:44`；Job 活跃态见 `services.py:66` | 高 | P2 |
| R6 | S2 停出图渲染器无任何在途保护：worker 正在出图时可直接终止 | `image_renderer.py:287-328` | 高 | P2 |
| R7 | S1/S5 与 S3 可同打一个 Job → `IdleHandoffKeeper._pulse` 失败把交接日志打成 RECONCILE_REQUIRED | `renderer_release.py:448` 按名终止 vs worker 持句柄 | 中 | P2 |
| R8 | 渲染器寿命绑在启动它的进程上（B→API、C→worker）；进程死后记录仍 RUNNING，`ensure` 判 GONE 后重启被 `IMAGE_RENDERER_INSTANCE_NOT_STOPPED` 拒绝，需 S1/S2/S5 修复行 | `image_renderer.py:219/247-260` | 高 | P2/P3 |
| R9 | `GET /instances/{id}/health` 仅凭 HTTP 把安装行写成 `ONLINE`，之后 A/B/D/activate 全被「非 STOPPED」拒绝 | `api_instances.py:87-99` | 高 | P2/P3 |
| R10 | `reconcile_managed_startups` 把 FAILED 出图记录（owner 死后）改写成 RECONCILE_REQUIRED；FAILED 不在它的终态集合 | `handoff_startup.py:47` vs `renderer_release.py:37` | 中 | P2 |
| R11 | `_release_orphan_h3_lease` 端口检查是死代码（`instance.port` 属性不存在，恒 None） | `handoff_startup.py:272-280` | 中 | P2 |
| R12 | E（instances execute_operation）进程对受管体系不可见（无 Job Object、无 app_settings 记录） | `instances.py:217-231` | 中 | P2（边界声明+拦截） |
| R13 | 出图绑定 base_url 与 recipe 端口无一致性校验 → 可能在 recipe 端口起渲染器、却向 config.base_url 提交 | `api_material.py:795-819`（只查回环） | 中 | P2 |
| R14 | 校验探针（D）不留持久记录；`validate_services` 不取 resource_slot | `service_validation.py:128-135` | 中 | P2 |
| R15 | 探活聚合缺失：`renderer_online`/busy/进程状态/外部 prompt 状态无统一快照，前端拼多个真相 | §3 | 高 | P3 |
| R16 | 短时 HTTP 抖动在 `_h3_connected` 单次 0.75s 探测下直接显示断连，无 DEGRADED 缓冲/退避 | `main.py:569-588` | 中 | P3 |
| R17 | `api_material.py:772 _probe_object_info`、`materialprep/execution.py:276 _renderer_answers` 用裸 `httpx.get`（默认 trust_env=True）走系统代理探本机 | 代码事实 | 中 | P3 |

静态推演、未运行时复现的条目：R7/R13 的实际触发窗口、R12 的现存使用频率。其余均有
明确代码路径。方案 §1.1 列出的 7 个危险交叉点与上表对应：1→R1/R2，2→R3，3→R8 前史，
4→R15/R16，5→R15，6→R5（S1 忙判定），7→素材中心并行 PR（见 §5）。

## 5. 兼容性边界与并行开发

- **素材中心**：`asset_center/` 在本分支与 main `f897b26` 一致（无并行改动落 main）；
  并行 worktree `shine-v21`（feat/elastic-literary-v2-low-blocking-20261111）改的是文学
  引擎层，不与本轮文件交集。本轮对 `asset_center/execution.py` 只做「经统一 resolver
  拿实例身份」级别的适配，不动其任务模型。
- **旧 API 兼容**：`image-runtime/start|stop`、`managed-processes/release`、
  `instances/*/health`、`comfy/test|capabilities` 的路由与响应形状保持不变，内部改经
  Orchestrator；`validate-managed`、`services/validate` 保留（校验语义本就是显式动作）。
- **Windows 5.1 编码**：新增/修改的 `scripts/*.ps1` 一律 UTF-8 BOM；`.cmd` 纯 ASCII
  （`test_package_entrypoints.py` 强制）。
- **数据**：本轮新增的持久化只有一张控制操作表（或复用 AppSetting），不迁移既有表、
  不清库、不动模型权重与媒体。

## 6. 测试基线（2026-11-11，worktree `6e2d57a`）

```
命令：F:/ClaudeWorkSpace/projects/shine-video-studio/backend/.venv/Scripts/python.exe \
      -m pytest backend/tests/test_process_probe.py backend/tests/test_owned_process_group.py \
      backend/tests/test_managed_startup.py backend/tests/test_renderer_supervisor.py \
      backend/tests/test_comfy_recovery.py backend/tests/test_comfy_handoff_barrier.py
结果：97 passed（59.42s）
```

全量后端基线在 P1 提交前补跑（基线数记录于各阶段报告）。Ruff/tsc/vitest 基线同此。

## 7. P1–P3 实施映射（按方案 §8）

- P1 = R1/R2：`start_shine.ps1` 命名 Mutex 关键区 + 清单原子写 + 失败回滚保护；
  `start_dev.ps1` 收束为同一受保护路径。
- P2 = R3–R14：`backend/shine/runtime_orchestrator/`（identity / control / status /
  recovery / contracts）+ installation_identity 控制锁 + 操作表 + 五入口收束。
- P3 = R15–R17：RuntimeSnapshot 聚合 API + 前端状态条 + DEGRADED 退避探活 + 统一恢复
  建议映射。

## 8. P1 真机复现补充（2026-11-11，三路并发启动冒烟）

静态审计之外，P1 的并发启动冒烟在本机复现了两个此前只有推演的缺陷（均已修复，
见提交「feat(P1)」）：

1. **pwsh 7 清单日期反序列化误判活进程**：`ConvertFrom-Json` 在 PowerShell 7 下把
   `processes.json` 的 ISO `start_time` 解析成 `[datetime]`，旧比较
   `$process.StartTime.ToString('o') -eq $Entry.start_time` 按当前文化把右侧转成
   `2026/11/11 20:13:48` 一类字符串 → 活着的 worker 被判死 → 每次启动都触发
   「半死栈」重整（停掉健康栈再重启）。存量 `test_installation_scripts.py` 用
   `powershell`（5.1）跑 harness，而 5.1 不解析日期字符串，因此从未暴露。这是
   「重复启动/状态异常」类用户现象的一个真实根源。修复：
   `Test-ShineRecordedProcessAlive` 双侧归一化为 'o' 格式（pwsh 7 回归已加入
   `test_startup_guard.py`）。
2. **`File.Replace` 第三参数编组**：PowerShell 把 `$null` 字符串参数编组成空串，
   `.NET` 拒绝空 backup 路径；清单原子替换首写即抛错。修复：`[NullString]::Value`。
3. 冒烟同时证实了互斥锁链路（Python/PS 同名推导、等待-释放、以及启动器内嵌套
   停止需要豁免标记 `SHINE_STARTUP_GUARD_HELD=1`，否则 120s 超时后有效清单被
   错误归档）。
