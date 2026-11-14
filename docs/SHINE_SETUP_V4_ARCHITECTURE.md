# Shine 视频工作台｜制作环境一体化（V4.1）架构与所有权

> 基线：`main@8e3e1e07850549df57511e0f330eb9ea22543dc2`（2026-11-14）。
> 本文只描述**已经写进代码的事实**；未真机执行的验收项在验收矩阵中标 PENDING。

## 1. 一句话结构

一个事实账本、一个页面、一个后台推进者：

```
设置 → 制作环境（唯一完整入口）
        │  只读
        ▼
GET  /api/v1/setup/summary ──► comfyops/setup_summary.py
        │                         │ 读：ComfyInstance / ModelPackage / 回执 / 目录 stat / 目录清单
        │                         └ 投影：5 阶段进度、能力卡、唯一下一步
        │  用户一次授权
        ▼
POST /api/v1/setup/prepare/execute ──► AppSetting['setup-preparation-task']（一条任务行）
        │
        ▼
既有常驻 Worker（worker.serve → run_configured_once）
        │  每次 tick 推进一步，不新开线程/进程/调度器
        ├─ TARGET      解析安装身份与端口（永不默认 8188）
        ├─ RECIPE      deployment.generate_configuration(capability=h3|image)
        ├─ IMAGE_BIND  对齐 image-runtime.json（同 instance/port/recipe_sha256）
        ├─ PROFILES    api_setup._register_project_profiles（项目级幂等）
        ├─ VALIDATE    service_validation.validate_services → activate_runtime
        └─ IMAGE_VERIFY start_image_renderer → 节点核对 → 释放显存
```

## 2. 模块所有权（新增部分）

| 模块 | 职责 | 明确不做 |
| --- | --- | --- |
| `backend/shine/comfyops/setup_summary.py` | 聚合既有证据成唯一快照；解析"这台机器用哪套 ComfyUI"（多候选时要求选一次）；从固定清单推导缺失文件 | 不哈希、不写文件、不启动进程；唯一写操作是 `record_installation_choice`（显式选择，审计） |
| `backend/shine/comfyops/setup_reconcile.py` | 幂等对账：`inspect` → `verify_models`（回执缓存）→ `register_verified_packages` →（可选）`adopt`；手动下载文件的 SHA 校验与内容寻址导入 | 不下载、不建第二套安装器；同目标并发只跑一次（`IN_PROGRESS`） |
| `backend/shine/comfyops/setup_preparation.py` | 把一次授权固化成可恢复的步骤机，由常驻 Worker 推进 | 不在 API 里同步做长任务；不新开队列；不改用外部进程 |
| `frontend/src/components/SetupEnvironmentStatus.tsx` | 唯一事实视图：总状态、5 阶段、能力卡、唯一下一步、缺失文件与保存目录、后台进度 | 不做资格判定（只读 `/setup/summary`）；不做第二套状态 |

## 3. 事实分层（任何一层都不得冒充上一层）

| 层 | 由什么证明 | 自动动作 | 禁止推广 |
| --- | --- | --- | --- |
| `FILES_FOUND` | 固定路径 + 固定字节数存在（stat） | 排队核验 | 不等于哈希正确 |
| `FILES_VERIFIED` | 全量 SHA-256 + size + revision，落回执 | 幂等登记 Package/Receipt | 不等于已装进运行时 |
| `RESOURCE_REGISTERED` | `ModelPackage.status = FILES_VERIFIED_PENDING_RUNTIME` + 回执 | 自动 upsert | 不等于实例可消费 |
| `INSTANCE_BOUND` | 实例登记的模型目录里读得到这些文件 | 在授权内对齐绑定 | 不等于运行合同成立 |
| `RECIPE_READY` | `<deployment_root>/setup/managed_recipe.json` 存在且实例/端口匹配 | 由 Worker 在授权内生成 | 不等于可启动 |
| `WORKFLOW_VALIDATED` | 真实 `/object_info` 覆盖图与参数映射 | 空闲时启动一次并释放 | 不等于真实出片 |
| `RENDER_VERIFIED` | 本机真实 Job 成功 + 技术 QC + 非 mock | 仅由用户单独授权 | 不等于整机可投产 |

## 4. P0 根因与修复位置

### P0-B：`IMAGE_RENDERER_RECIPE_MISSING`

修复前链路的三个分叉：

1. `api_material.bind_image_runtime()` 在 recipe 缺失时仍写 `image-runtime.json`，并返回成功；
2. `materialprep.runtime.readiness()` 的 `configured` 只看 `base_url/instance_id`，于是"已配置"被当成"可运行"；
3. `comfyops.image_renderer.load_image_recipe()` 真正启动时才要求 recipe，错误在启动瞬间才暴露。

修复：

- `runtime.readiness()` 新增 `binding_state`（`NOT_BOUND` / `PENDING_RECIPE` / `READY`）与 `runnable`；`PENDING_RECIPE` 时阻断层为 `NOT_CONFIGURED`，四类家族一律 `can_generate=false`。
- `image_renderer.image_recipe_state()` 把 recipe 变成可读事实（`MISSING/INVALID/READY`），调用方不再靠捕获异常判断。
- `deployment.generate_configuration(capability="image")` 可以只为图片能力写配方：不要求 H3 权重、不覆盖 H3 的 runtime candidate、不写 `SHINE_SETUP_RECIPE`。
- `adoption` 增加能力维度：`verified_files[].capability` 与 `adoption.capabilities`；`adopted_installation_blockers(instance, catalog, capability=...)` 只校验该能力的字节，所有权链（root/revision/python/nodes/adapter）始终全量校验。
- `POST /setup/prepare/execute` 的 `RECIPE → IMAGE_BIND` 由 Worker 顺序完成，`IMAGE_BIND` 在配方未就绪时具名失败而不是伪造。

### P0-A：反复要求"登记"

修复前：核验/登记只在 `/setup/existing-installations/verify` 里发生，而它只在用户点过"检测本机 → 复用"链路后才会被调用；检测本身只看 `installations[0]`。

修复：

- `POST /setup/reconcile` 在服务器侧完成"发现 → 核验 → 登记 →（可选）接管"，幂等且同目标单飞；回执 sidecar 保证第二次不重读几十 GB。
- `/setup/summary` 每次都把"已核验并登记"的事实读出来，页面不再有"登记"按钮。
- 多候选安装不再取 `[0]`：排序依据是"正在服务 → 你确认过 → 已接管 → 受管 → 磁盘发现"，多个等价候选时要求选一次并持久化。

### 端口/变体身份统一

`ConfigurationRequest.port`、`InstanceChainRequest.port` 改为可省略；省略时由 `_runtime_target()` 从机器上已解析的安装读回端口与实例名，再走原有端口探测。一个准备流程里的 plan / profiles / recipe / 图片绑定 / 服务核验读同一个身份，不再出现"计划 8189、配置 8188"。

## 5. 安全边界（未被放宽）

- 受管渲染器只能在端口空闲、身份可证明时启动；外部监听一律 `EXTERNAL_INSTANCE_ON_PORT`，绝不杀进程、绝不写外部目录。
- `GET /setup/summary` 与 `GET /setup/missing-files` 不产生副作用：不写配方、不登记、不启动。
- 真实 H3 出片、真实小样仍需单独授权；候选变体不因"能准备"而变成 `SUPPORTED`。
- 手动下载目录固定为 `<deployment_root>/manual-downloads`，服务端不接受前端传入的任意路径/URL；只导入 SHA 匹配的字节到既有 `.cache/<sha256>`。

## 6. 入口收束

| 入口 | 行为 |
| --- | --- |
| 设置 → 制作环境（`SetupPanel`） | 唯一完整整备：检测、能力勾选、一次确认、后台进度、缺失文件详情；专业排查保留在同页下方，不另判成功 |
| 导演 → 基础配置（`DirectorSetupPanel` env 页签） | 只读摘要（同一 `/setup/summary`）+「打开设置」，不再自行生成安装计划 |
| `RealProductionWizard` | 只做导航/恢复，不驱动安装器 |
| 高级诊断 | 保留原按钮与错误码，用于运维定位，不参与"是否就绪"的判定 |
