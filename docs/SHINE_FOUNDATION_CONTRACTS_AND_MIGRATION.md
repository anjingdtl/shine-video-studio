# Shine V2.0 统一基础底座契约与无损迁移说明

> 日期：2026-11-20  
> 适用范围：`shine/foundation/*`、`shine/platform_*`、`shine/manual4/*`、`shine.db` 与 `manual4.sqlite`

---

## 1. 四大公共底座契约（`backend/shine/foundation/`）

### 1.1 统一环境与能力预检契约（`shine.foundation.environment`）
- **`get_platform_environment_summary(db, settings) -> dict`**：
  - 投影 `shine.environment.v1`，直接复用 `comfyops.setup_summary.build_setup_summary` 与 `runtime_configuration`，零重复探测。
  - 附带 `subsystems.director7` 与 `subsystems.manual4` 健康状态；当 `manual4.sqlite` 损坏或不可用时，`manual4.available = False` 并返回明确错误码，绝不阻断七阶启动。
- **`check_capability_readiness(db, settings, *, capability, variant_id, accelerator_id, source_line, project_id) -> dict`**：
  - 统一返回五态：`ready`、`missing_required`、`optional_missing`、`temporarily_busy`、`unsupported`。
  - `capability="h3"`：复用 `comfyops.resource_resolver.resolve_resources` 与 `GenerationStrategy`；不支持的加速档（如 `turbo4`）明确返回 `unsupported`。
  - `capability="image"`：委托 `build_setup_summary` 的 `"image"` 能力事实（Qwen-Image-2.1 权重核验与运行配置绑定状态）。
- **`resolve_shared_comfy_launch(platform_db, settings, m4_root) -> dict`**：
  - 从平台唯一激活配方 `managed_recipe.json` 或已登记 `ComfyInstance` 解析共享 ComfyUI 根路径、内嵌 Python、自定义节点白名单与 `shine-node-paths.yaml`，为四阶分配独立端口与运行目录 `<m4_root>/comfy-runtime/`。

### 1.2 统一受管进程与 GPU 仲裁契约（`shine.foundation.runtime` & `shine.platform_gpu`）
- **`acquire_m4_gpu_lease(platform_factory, data_dir, *, owner, budget_mb=16384, priority=10, wait_seconds=0.0)`**：
  - 必须以 `m4:` 前缀登记到 `shine.db` 的 `resource_leases` 表（通过 `ResourceScheduler.enqueue` + `admit`），同时获取 OS 命名互斥量 `Global\ShineGpuArbitrationMutex`。
  - 当七阶持有 `h3:*`、`image:*`、`vlm:*`、`asr:*` 活跃租约或待对账租约时，立即拒绝并返回 `409 GPU_BUSY_OTHER_LINE`（含 `blocking_line="director7"`、`blocking_owner`）。
- **`heartbeat_m4_gpu_lease` / `release_m4_gpu_lease`**：
  - 渲染期间后台守护线程定期续期 `expires_at`；释放时在同一事务内将租约置为 `RELEASED` 并调用 `ResourceScheduler.advance_epoch(db)` 推进 `physical-resource-epoch`。
- **`retire_idle_director7_image_renderer(platform_factory, settings, installation_id)`**：
  - 四阶启动 H3 ComfyUI 前，在 `renderer_lifecycle_guard(installation_id)` 保护下安全关闭空闲的七阶出图渲染器；若七阶出图任务正在运行，则拒绝并报 `409 GPU_BUSY_OTHER_LINE`。
- **`launch_owned_comfyui_group(argv, *, cwd, env, pinned_files, job_name)`**：
  - 统一使用 `comfyops.owned_process.OwnedProcessGroup.launch` 与 `isolated_stage.WindowsJob`（支持 `Local\Shine(?:M4)?Handoff-[a-f0-9]{32}`），锁定源码文件并绑定 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`。

### 1.3 公共素材中心契约（`shine.foundation.materials`）
- **`register_uploaded_bytes_in_library(platform_db, settings, *, project_id, source_line, category, title, filename, content, actor)`**：
  - 上传字节经 `verify_upload` 校验后写入公共素材中心 `LibraryAsset` / `LibraryVersion` / `LibraryRights(status="PENDING")` / `LibraryProjectLink`，初始 `approval_status="CANDIDATE"`。
- **`queue_library_t2i_task(platform_db, settings, *, project_id, source_line, category, title, prompt, width, height, seed, idempotency_key)`**：
  - 建立 `LibraryRenderTask(kind="image", mode="T2I", status="QUEUED")`，由共享 Worker（`shine.worker` -> `asset_center.execution.run_one_pending_job`）在统一 GPU 租约下异步执行 Qwen-Image-2.1 出图，严禁在 HTTP 请求线程内同步冷启 ComfyUI。
- **`promote_library_version_with_grant(platform_db, *, version_id, project_id, actor)`**：
  - 人工晋升唯一写入口：签发一次性 `LibraryApprovalGrant`，将 `LibraryVersion.approval_status` 置为 `"PROMOTED"`、`LibraryRights.status` 置为 `"CLEARED"`，并授权当前项目链接。
- **`resolve_library_version_file(platform_db, settings, version_id, *, require_promoted=True)`**：
  - 校验版本已晋升且权利未撤销（`REVOKED` 立即拒绝 `409 LIBRARY_RIGHTS_REVOKED`），返回物理文件路径、SHA-256 与媒体元数据。

### 1.4 公共 H3 制片执行契约（`shine.foundation.h3`）
- **`H3ExecutionRequest` / `H3ExecutionReceipt`**：
  - 强制 `REQUIRED_GENERATION_MODE = "Ref2VA"`；无图像参考立即拒绝 `PROJECT_REQUIRES_REF2VA`；槽位上限 `9 图 / 3 音 / 3 视频 / 总计 12`。
  - 帧数公式固定 `17k + 5`（24fps），画幅固定 `16:9 (1344x768)` / `9:16 (768x1344)` 及 `0.3–1.0` 缩放（16 像素对齐）。
- **`ComfyH3Executor.execute(req, *, on_before_submit, on_submitted)`**：
  - 提交前强制校验 `sha256(req.prompt_text.encode("utf-8")) == req.expected_prompt_sha256` 及所有参考文件磁盘字节 SHA-256。
  - 上传参考文件并完成参数注入后计算最终 `prompt_graph_sha256`，先触发 `on_before_submit(prompt_graph_sha256)` 落库，再调用 `/prompt`，随后立即触发 `on_submitted(prompt_id)` 落库。
- **`reconcile_submission(client, *, prompt_id, prompt_graph_sha256)`**：
  - 兼容真实 ComfyUI `/history` 的 5 元组 `prompt` 结构与字典结构，并支持 `/queue` 运行中/排队中反查，杜绝崩溃恢复时盲目重提。
- **`evaluate_h3_technical_qc(video_path, *, duration_s, expect_audio, ...)`**：
  - 统一调用 `shine.quality.technical.TechnicalQC` 进行容器、流元数据、帧率、时长、音轨静音与 FFmpeg 全帧解码校验。

---

## 2. 数据库 Schema 演进与无损迁移（`m4_schema_meta = 3`）

### 2.1 `manual4.sqlite` 增量幂等迁移（`backend/shine/manual4/storage/migrations.py`）
`ensure_m4_schema(engine)` 在不改动现有表主键与历史行的前提下，幂等补齐以下列并将 `m4_schema_meta` 升级至 `"3"`：
1. `m4_asset_versions`：
   - `library_asset_id VARCHAR(36)`
   - `library_version_id VARCHAR(36)`
   - `library_rights_id VARCHAR(36)`
   - `rights_status VARCHAR(32) DEFAULT 'local_unlinked'`
   - `library_task_id VARCHAR(36)`
2. `m4_material_requirements`：
   - `pending_task_id VARCHAR(36)`
3. `m4_shots`：
   - `content_sha256 VARCHAR(64) DEFAULT ''`
4. `m4_production_runs` / `m4_jobs` / `m4_job_attempts`：
   - `m4_production_runs.storyboard_revision_id VARCHAR(36)`
   - `m4_production_runs.execution_generation INTEGER DEFAULT 2`
   - `m4_jobs.execution_generation INTEGER DEFAULT 2`
   - `m4_job_attempts.execution_generation INTEGER DEFAULT 2`

### 2.2 历史数据无损回填与代际兼容规则
1. **历史四阶素材回填（`backfill_m4_asset_to_library`）**：
   - 当读取或复用旧版 `M4AssetVersion`（`library_version_id IS NULL`）时，自动按物理文件 SHA-256 幂等登记至公共素材中心 `shine.db`。
   - **安全铁律**：仅 `status == "promoted"` 且 `is_mock == False` 的真实历史主素材可在回填时继承 `PROMOTED` / `CLEARED` 状态；任何 `is_mock == True` 或未晋升候选一律保持 `CANDIDATE`，绝不将 Mock 资产混入公共主素材库。
2. **历史生产任务代际兼容（`execution_generation`）**：
   - 历史已存在的 `M4ProductionRun` / `M4Job` 保留 `execution_generation = 1`，其旧快照与产物原样可读；崩溃对账函数 `reconcile_stalled_m4_runs` 同时兼容代际 1 与代际 2。
   - 新创建的生产运行统一标记 `execution_generation = 2`，强制走 `shine.foundation.h3.ComfyH3Executor`。
3. **本机生产数据零破坏保证**：
   - 本机 `D:\Shine\data\shine.db` 与 `D:\Shine\data\manual4.sqlite` 在收尾前后经 SHA-256 全量比对完全一致（`shine.db`：`8c65fbab218fe3c03f0f657030be50b6a9f7327f63aea8449af4267a5b670792`；`manual4.sqlite`：`2bf978d36a510dfbb006dc93161e07d88c6a6f5eac94ae3405791270a49aeb7e`）。
