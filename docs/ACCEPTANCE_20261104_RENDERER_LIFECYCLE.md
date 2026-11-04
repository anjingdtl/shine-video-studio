# 渲染环境生命周期报告（2026-11-04）

**结论：ComfyUI 与本地审片服务已正式归入工作台生命周期——网页可一键关闭并释放显存，
根目录一键启动会先清理残余后台再干净启动。真实机器上已实测清掉两条存活数日的孤儿审片
进程；本轮没有真实 H3 出片，GPU 释放只到「进程与端口已释放」这一层证据。**

## 1. 用户报告的现象与实测根因

用户报告：关闭 web 后 ComfyUI 后台没有跟着关闭，显存一直被占。

现场只读核对（本机，11-04 16:5x）：

| 现象 | 实测 |
| --- | --- |
| 孤儿审片服务 | `local_review_server.py` 两个进程（pid 28504 / 18620，占用 8116 / 8117），父进程 11324 已死，任何 `processes.json` 里都没有它们 |
| 陈旧的渲染记录 | `renderer-startup:*` 共 19 条全部停在 `stage=RUNNING`，但记录的进程早已不存在 |
| 陈旧的实例行 | `comfy_instances` 19 行停在 `RUNNING_PENDING_HEALTH`，`payload.process` 指向已死的进程 |
| 租约 | `resource_leases` 全部 `RELEASED`，但上述记录仍声称「渲染器在运行」 |

三个根因：

1. **启动器每次启动都会覆盖进程记录。** `scripts/start_shine.ps1` 把 review 进程写进
   `processes.json`，而下一次启动若没有 review、或记录被替换，旧 pid 就此丢失——它既不在
   记录里，也就躲过了每一次停止。
2. **中断启动的对账会抹掉进程身份。** `reconcile_managed_startups` 在标记
   `RECONCILE_REQUIRED` 时整条重写记录，只留 `stage/last_confirmed_stage/reason`，
   **丢掉了 `process`、`port`、`lease_id`**。此后既无法证明归属，也无法把租约对回渲染器，
   记录就永久卡住（现场那 19 条就是这种形状）。
3. **工作台没有「关闭后台」这个动作。** ComfyUI 由 worker 在受管 Job Object 里启动，
   正常会随 worker 一起死；但审片服务由启动器以普通子进程方式拉起，不在任何受管组里，
   也不会随栈退出。

## 2. 本轮改动

### 2.1 归属判定与释放（新增 `backend/shine/comfyops/renderer_release.py`）

- `managed_processes(db)`：读取 `renderer-startup:*` 与 `review-startup:*` 两类受管记录。
- `describe_managed_processes(db)`：给出每个受管进程的状态（`RUNNING` / `GONE` /
  `UNVERIFIED` / `STOPPED`）、端口、pid、租约与镜头状态。
- `release_managed_processes(db, actor, reason)`：
  - 归属证明要**两件事同时成立**：记录的 PID/birth 身份 + 命名 Job Object
    （`Local\ShineHandoff-<uuid>`）成员资格；
  - 正在出片的渲染器（租约对应的镜头处于 `RUNNING`/`SUBMITTED`）**绝不停止**，只报告；
  - 归属无法证明的进程**绝不触碰**，报告为 `UNVERIFIED`；
  - 已确证的进程用 `terminate_named_group` 终止并确认组成员归零，随后释放租约、
    把启动记录写成 `STOPPED`、把对应 `comfy_instances` 行置为 `STOPPED`；
  - 全程写审计 `COMFY_MANAGED_PROCESSES_RELEASED`。

### 2.2 中断对账不再丢身份

`shine/comfyops/handoff_startup.py::reconcile_managed_startups` 现在在写回
`RECONCILE_REQUIRED` 时保留原有字段（`**current`）。回归测试
`test_an_interrupted_startup_keeps_the_identity_it_needs_to_be_released` 断言
`process.pid`、`process.job_name`、`lease_id`、`port` 全部仍在。

> **钉住的审片身份变了，如实记录。** `shine/quality/client_identity.py` 把
> `shine/comfyops/handoff_startup.py` 列入按内容哈希固定的审片客户端集合，本文件这一处
> 修复改变了该哈希。按 AGENTS.md 的规则，这**按设计**作废了既有 VLM 修订的资格。
> 本项目实测：本项目数据库里**没有任何**记录钉住 `client_review_sha256`，全部本地审片
> 模型包仍是 `FILES_VERIFIED_PENDING_RUNTIME`（从未做过运行时资格），本轮也没有产生任何
> 审片证据，因此**没有既有资格被作废**。将来若要用本地审片模型，必须重新做一次运行时
> 资格与正/负样本标定。
> 另外：释放动作写回的 `STOPPED` 阶段会被下一次对账改写成 `RECONCILE_REQUIRED`
> （该阶段不在对账的终态集合内），但身份已被保留，记录仍可被判定与释放。

### 2.3 启动器先清残余，再干净启动

新增 `scripts/release_residual_backends.py`，由 `scripts/start_shine.ps1` 在起栈前调用：

- 先按受管记录释放（并把陈旧记录与陈旧实例行收敛为 `STOPPED`）；
- 再按本机用过的审片端口扫描残留进程：命令行指向**本仓库自己的**
  `local_review_server.py` / `local_review_isolated_server.py` 才算归属，可停；
- 栈仍在运行时不动作（避免打断正在生产的 worker）。

### 2.4 工作台里的一键关闭

- `GET /api/v1/setup/managed-processes`：列出本工作台拥有的后台进程。
- `POST /api/v1/setup/managed-processes/release`：关闭它们并释放内存（沿用本机
  `local-user` 信任边界）。
- 前端「基础配置」页新增「本机后台进程（ComfyUI / 审片服务）」区块与
  「关闭本机后台并释放显存」按钮，并说明「正在出片的进程不会被强行中断」。

## 3. 真实机器验证

在运行中的真实数据目录（`.local-acceptance/real-user-journey-final/data`）实测：

```
[released] managed backend 47850162…   （19 条陈旧渲染记录）
[released] managed backend 2aad8173…
… 共 19 条 …
[refused]  managed backend 32f4e64e…: the recorded identity does not prove this installation owns it
[refused]  managed backend e9f16c4e…: the recorded identity does not prove this installation owns it
[refused]  managed backend 57d25f04…: the recorded identity does not prove this installation owns it
[stopped]  residual backend pid 28504
[stopped]  residual backend pid 18620
```

- 两条孤儿审片服务**被停止**（命令行指向本仓库脚本，归属可证）。
- 三条审片记录**被拒绝**：它们记录的 pid 早已不在，但 8116/8117 上还有别的进程在听，
  归属无法证明 → 不碰。这正是应有的保守行为。
- 清理后核对：`local_review_server*.py` 进程数 0；8116 / 8117 / 8188 监听数 0。

## 4. 启动器四条验收（开发机根目录一键启动）

| 检查 | 结果 |
| --- | --- |
| #1 干净重启 | 通过：`.cmd` 入口 → 环境检查通过、迁移执行、API 8000 / Web 5173，`/health` 200，网页 200，记录写入真实端口 |
| #2 默认端口被外部进程占用 | 通过：外部进程占住 8000/5173 时，启动器**没有**误报「已在运行」，自动改用 API 8001 / Web 5174，两者均 200，且外部进程未被误杀 |
| #3 半启动残栈 | 通过：只杀掉 web 后再次启动，启动器识别「上次只启动了一部分服务」，清理陈旧记录、释放残余后台、完整重启，浏览器落在活着的 web 端口 |
| #4 停止 | 通过：`stop_shine.py --data-dir <数据目录>` 退出码 0，`processes.json` 消失，8000/5173/8116/8117/8188 监听数 0，残留后台进程数 0 |

## 5. 测试

- 新增 `backend/tests/test_managed_process_release.py`（10 项，全部通过）：
  已确证消失的渲染器 → 记录 `STOPPED` + 租约释放；正在出片的渲染器 → 拒绝停止；
  归属确证的渲染器 → 停止并释放租约；归属无法证明 → 绝不触碰且租约保持；
  工作台可查询受管进程；本机操作者可一键释放；栈仍在运行时残余清理拒绝动作；
  中断对账必须保留进程身份；不确定记录按物理事实判定；已释放记录不再被对账复活。
- 后端定向批次（剧组 107 项 + 生命周期 10 项 + 提示词契约 16 项）全部通过；
  前端 `npm.cmd test` 115/115、`npm.cmd run build` 通过；`ruff check .` 全绿。
- **最终代码状态的完整后端套件**：`pytest tests -q` → **1564 项全部通过、0 失败、exit 0**
  （源码冻结后重跑）。
- 顺带修掉 3 项 **HEAD 上就存在**的失败（本轮改动无关，stash 后仍失败）：
  `scripts/update_dev_shortcut.ps1` 非 ASCII 内容缺 UTF-8 BOM（PS 5.1 读成 ANSI 后
  第 31 行解析报错）、`test_v13_migrations.py` 把 alembic head 写死成旧版本号。

## 6. 仍未验证 / 边界

- **真实 GPU 释放没有量化证据**：本轮证明的是「进程已终止、端口已释放、租约已释放」，
  没有用 `nvidia-smi` 记录显存回落数值。
- **真实 H3 出片未跑**：关闭动作针对受管渲染器，但本轮没有排入一次真实渲染再关闭。
- **本机审片服务仍在启动参数里**：启动器仍会按 `-StartReview` 拉起审片服务；本轮解决的是
  「它不再孤儿化」，没有改成由工作台 API 统一托管它的启停。
- **外部 ComfyUI 不归本工作台**：用户自己安装并手动启动的 ComfyUI 不在受管记录里，
  本工作台不会去动它。
