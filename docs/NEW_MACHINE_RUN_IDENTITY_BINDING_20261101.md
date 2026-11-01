# 运行身份与安装身份绑定

目标仍为完整新机 PASS。本轮补齐「持久安装实例」与「每次生产运行」在同一数据库内的身份绑定，使首次与后续生产都能被验证。绑定不代表真实模型加载、真实 H3 推理或影片验收通过。

## 上一轮记录的断点

`docs/NEW_MACHINE_RUNTIME_ACTIVATION_20261101.md` 的「未完成」记录了下一个断点：生成配方的 `renderer.instance_id` 是持久安装实例的 ID，Worker 直接把它传给 `start_managed_renderer`。该函数登记运行时负载时拒绝同 ID、不同负载的已有实例（`COMFY_STARTUP_INSTANCE_CHANGED`），且启动意图对同一 ID 只允许记录一次（`COMFY_STARTUP_ALREADY_RECORDED`）。因此第二次生产运行必然与第一次冲突，唯一"绕过"方式是删除安装证据或旧意图，两者都被产品基线禁止。

Codex 已写出一版未提交、未测试的实现（安装绑定 + 每次运行身份）。本轮在其基础上补齐验证、修掉一处回归，并补上继续运行路径上的复验。

## 当前行为

- **安装证据与运行身份分离。** 安装实例行（`instance_identity('local', port)`）保持为不可被运行改写的证据；每次生产运行使用 `managed_run_identity(installation_id, attempt_id)`（uuid5）作为自己的实例 ID 与启动意图键，因此第二次及以后的运行不会与安装行或第一次的记录冲突。两次运行的记录同时存在，谁也不覆盖谁。
- **绑定在同一个数据库内成立。** 运行启动前校验：绑定指向的安装实例存在、是该端口的本地实例、摘要与确认时一致、非 Mock、处于停止状态、没有残留进程、根目录与配方根一致，且安装凭证完整（非 Mock、COMPLETED、结果与实例负载一致、依赖锁与固定节点/模型绑定齐全）。任一不成立即 `COMFY_INSTALLATION_BINDING_CHANGED`，不写启动意图、不建进程、不动租约。
- **绑定不可借用。** 绑定里的 attempt 必须属于本次任务的 JobAttempt，且运行实例 ID 必须等于该 attempt 推导出的运行身份；两者都由代码推导，不能由调用方指定。
- **继续运行复验。** `verify_managed_startup` 在每次激活时复验该运行所绑定的安装仍然成立，因此系列内后续镜头不会在安装证据已经变动的情况下继续使用同一个渲染器。
- **审片服务属于运行。** 审片服务的启动键按运行身份派生，并要求父渲染器启动已处于 RUNNING、租约与任务一致；审片服务不再与安装或另一次运行共用记录。
- **配方与激活必须带绑定。** 生成配置写入 `installation`（安装实例 ID 与摘要）；配方读取时校验形状（必须指向自身 `instance_id`，摘要必须是 64 位十六进制）；`configuration_identity` 要求绑定与当前安装实例完全一致，否则 `RUNTIME_INSTALLATION_BINDING_REQUIRED`，`resolve_runtime` 随即报 `ACTIVE_RUNTIME_CONFIGURATION_CHANGED`。
- **就绪层只认安装。** L3 环境层排除运行记录（有 `installation` 绑定的行），因此一次生产运行之后 L3 仍然解析到安装实例本身，不会因多行而变成"歧义"。
- **旧路径不变。** 没有绑定的手工 `SHINE_MANAGED_RECIPE` 配方继续使用配方自身的实例 ID，不进入绑定路径。
- **审计。** 每次绑定写入一条 `COMFY_INSTALLATION_BOUND`，记录运行身份、安装身份、安装摘要和 attempt。

无新增数据库表、队列或迁移；全部复用既有的 `comfy_instances`、`installation_attempts`、`app_settings` 与审计表。

## 本轮验证

新增 `backend/tests/test_managed_run_binding.py`，15 项定向合同：

- 首次生产运行拥有自己的运行身份，安装证据的负载、摘要与停止状态原样保留，运行记录写在运行身份下；
- 后续生产运行使用不同的运行身份，两次运行的启动意图与实例记录并存，安装证据仍是原来那份；
- 安装证据变动（负载变更、凭证缺失、凭证为 Mock、实例为 Mock）一律阻断，且不写启动意图、不建实例行、不把租约改成待对账；
- 安装仍持有进程时阻断；
- 绑定借用别的 JobAttempt 时阻断；运行身份与 attempt 不匹配时阻断；
- 同一次 attempt 永不启动第二个渲染器（保留 `RECONCILE_REQUIRED` 记录）；
- 继续运行复验：安装证据变动后 `verify_managed_startup` 拒绝；
- 就绪层忽略运行记录，仍解析到安装实例；
- 配方形状：绑定指向别的安装、摘要不是摘要、缺字段都被拒；
- 激活层：去掉绑定或把摘要改旧都不构成可激活身份；
- 未绑定的旧配方保持原有身份。

与离线 E2E、film crew、受管启动和激活合同合跑共 75 项全通过，日志 `.local-acceptance/new-machine-goal-20261101/run-identity-binding-targeted.log`，exit=0。本轮改动了 `backend/shine/worker.py`，AGENTS.md 要求的离线 E2E 已随之重跑。

**变异校验**（日志 `.local-acceptance/new-machine-goal-20261101/run-identity-binding-mutation.log`）：把运行身份临时改回安装身份后重跑，15 项中 9 项失败（exit=1），说明这些合同确实约束了"两次生产运行互不冲突"这一行为，而不是恒真断言。改回后 15 项重新全通过。

**修掉的回归**：Codex 未提交版本的就绪层筛选用"有 name 且有 root"来排除运行记录，导致一条只有 `root` 的合成安装行从 `COMPLETED_REAL_INSTALL_EVIDENCE_REQUIRED` 变成笼统的 `REAL_INSTANCE_MISSING_OR_AMBIGUOUS`，`test_deployment_acceptance_regressions.py` 失败。改为"有 root 且没有 installation 绑定"，运行记录仍被排除，具体阻断原因恢复。回归日志 `run-identity-binding-readiness-regression.log` 保留。

完整后端回归：`uv run --project backend --locked pytest backend/tests -q`，**1274 passed / 1 skipped / 0 failed / 0 error，exit=0**，日志 `full-suite-run-identity-binding.log`，数字按静默日志状态符统计（与上一轮 1259 相比多出的 15 项即本文件新增合同）。唯一 skip 仍是门控的真实 ComfyUI 探测；Starlette/AnyIO 与 SQLAlchemy 两项既有警告保留。

**第一次完整回归的失败与原因**：第一次全量运行在 `test_native_capabilities.py` 的 STRUCTURED_VLM 参数上出现至少 25 个 ERROR（该次只保留了尾部 25 行，总数可能更多）。原因是本轮在该次回归进行中执行了 `ruff check --fix`，它重写了 `backend/shine/worker.py` 的 import 块；而 `shine/quality/client_identity.py` 把 `shine/worker.py` 与 `shine/comfyops/handoff_startup.py` 列入按内容哈希固定的代码身份，进程内身份在 import 时固定、校验时重算，源文件中途变动即报 `REVIEW_CLIENT_SOURCE_CHANGED_SINCE_LOAD`，STRUCTURED_VLM 的原生校准因此无法通过。这是本轮自身的测试编排失误，不是产品缺陷，也说明该固定机制确实生效。停止在回归期间改动受固定文件后重跑，全量转绿；重跑日志为上述 `full-suite-run-identity-binding.log`。

Ruff：`uv run --project backend --locked ruff check backend/shine backend/alembic backend/tests`，通过。前端：`npm --prefix frontend run build`（tsc -b + vite），通过。Alembic：`uv run --project backend alembic -c backend/alembic.ini upgrade head`，无新增迁移，库可正常升到 head。

## 已知边界

- 运行记录每次生产运行追加一行 `comfy_instances`（带 `installation` 绑定）。前端不自动列举实例，因此界面上无新增噪声；`/api/v1/comfyops/instances` 会同时返回安装行与运行行。
- 主机清点接口（`api_preparation` 的 inventory refresh）按 URL 推导身份，会落到安装行上并改写它的负载与摘要。若在渲染器运行时执行清点，已确认的运行身份随即变为 `INVALID_REQUIRES_REVIEW`，需要重新生成并确认配置。本轮未改清点行为，只记录该交互。
- 真实 H3 生产、完整物料下载、真实模型加载与最终影片验收仍未完成，L4/L5 保持 PENDING / NOT_VERIFIED。审片服务首启、实时身份/能力探测与路由自动配置仍未闭环。

## 未完成

- 真实 GPU/H3 硬件验收未做，本轮全部是本地合同与合成证据。
- 真实模型权重、完整物料下载与最终成片验收仍待完成。
- 审片服务首启与实时能力探测仍需闭环。
