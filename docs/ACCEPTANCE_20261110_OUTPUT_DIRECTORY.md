# 输出目录与可读文件名（2026-11-10）

## 目标

- 所有项目产出的素材、镜头视频、成片和音色放入一个可配置的输出根目录；未配置时使用现有
  默认数据目录。
- 根目录下按「项目 → 素材 / 视频 / 音色」两级归档；无项目的全局素材进入「素材中心」。
- 磁盘文件名统一为 `项目_类型_名称.ext`，不使用内容哈希或随机名；SHA-256 仍是台账身份。
- 用户调整目录时自动迁移，迁移前拒绝打断正在拍摄或生成的任务。

## 实现

- 唯一解析入口：`backend/shine/media/output_layout.py::output_root`。
- 命名规则：`backend/shine/media/output_names.py`（类型常量、Windows 安全字符、保留名、
  长度上限、去除已知媒体后缀但保留名称里的点）。
- 新路径示例：
  - `雾港夜账/素材/雾港夜账_素材_01-易千寒.png`
  - `雾港夜账/视频/雾港夜账_视频_003镜_02集.mp4`
  - `雾港夜账/视频/雾港夜账_视频_001-006镜_02集.mp4`
  - `雾港夜账/音色/雾港夜账_音色_易千寒.wav`
  - `素材中心/素材/素材中心_素材_海报.png`
- 素材中心晋升/分配到项目时，项目主素材会复制一份到 `<项目>/素材|音色/`，素材中心版本仍留在
  `素材中心/...`；反过来「收藏到素材中心」也会复制一份到 `素材中心/...`。两份字节相同、
  SHA-256 相同，项目文件与素材中心文件不互相借用物理路径。
- 迁移：`PUT /api/v1/settings/output-directory`
  - 活动 Job / 活动 series / 活动 Run / 队列中的素材任务存在时返回
    `OUTPUT_DIRECTORY_BUSY`，不写设置、不动文件。
  - 先复制（同卷优先硬链接）并逐字节核验台账 SHA-256；全部通过后一个事务切换所有
    `storage_path`、`JobAttempt.output_path`、连续性来源路径与设置；提交后清理旧文件。
  - 失败返回 `OUTPUT_DIRECTORY_SOURCE_INVALID` / `OUTPUT_DIRECTORY_TARGET_CONFLICT` /
    `OUTPUT_DIRECTORY_COPY_FAILED`，目录不切换。
  - 迁移期间的写入返回 `STORAGE_MIGRATION_IN_PROGRESS`。
- 旧 `job.request.reference_manifest[*].storage_key` 可能过时：读取方使用
  `refresh_reference_paths`；H3 引用校验只按 SHA-256 + content_type 认身份，不改写历史请求。
- 页面：`OutputDirectoryPanel` 同时出现在导演模式基础配置和专业设置，显示当前根、结构、
  文件数、空间、活动阻塞与迁移结果。

## 验证

在仓库根执行：

按影响范围做定向回归，未跑全量后端套件。

| 定向批次 | 结果 |
| --- | --- |
| `test_output_directory.py` + `test_asset_center.py` + `test_workflow.py` + `test_security_windows.py` + `test_material_image_jobs.py` + `test_offline_e2e.py` + `test_video_library.py` | **102 passed** |
| `test_production_series.py` + `test_comfy_completion_window.py` + `test_advisory_queue.py` + `test_continuity_media.py` + `test_cross_series_continuity.py` + `test_job_media.py` + `test_delivery_assembly.py` + `test_delivery_orders.py` + `test_generation_segments.py` | **110 passed** |
| `test_material_preparation.py` + `test_material_image_jobs.py` + `test_material_render_e2e.py` + `test_material_naming.py` + `test_material_worker_loop.py` + `test_v13_preparation.py` + `test_film_crew.py` + `test_four_stage_reacceptance.py` | **180 passed, 1 skipped** |
| `test_reference_transfer.py` + `test_reference_slots.py` + `test_material_render_e2e.py` + `test_material_render_ownership.py` + `test_review_manifest_identity.py` + `test_production_gate_weight_evidence.py` | **73 passed** |
| `test_alembic.py` + `test_v13_migrations.py` + `test_schema_identity.py` | **8 passed** |
| `... -m ruff check shine tests` | All checks passed |
| `npm.cmd run build`（frontend） | 成功 |
| `npm.cmd test`（frontend） | 222 passed（含 `outputDirectory.test.tsx` 2 项）；最后一次只加了面板缺失字段兜底，之后定向重跑 `outputDirectory.test.tsx` 2 项通过 |

> 按用户要求只跑影响范围，不跑全量后端套件。全量套件此前的一次运行在
> `test_director_assets_closeout.py` 的采用 revision 用例上失败；该失败已在干净 HEAD 工作树复现，
> 与本轮改动无关。

`test_output_directory.py` 覆盖：上传素材的可读命名、调整根目录后的素材/成片迁移与媒体读取、
恢复默认目录、活动素材任务拒绝迁移、源文件被篡改时拒绝切换、素材中心归档、镜头命名、
旧候选文件迁移后 `payload` 路径与 `sha256` 一致。

## 边界与未完成

- 本轮没有启动真实 ComfyUI/H3；迁移只使用合成字节验证，不构成真实出片验收。
- 迁移是同步请求；大量视频跨磁盘复制时页面会停留在「正在迁移文件…」，但服务端在提交前
  不会切换台账。个人桌面场景接受这一取舍。
- 成片下载的 `Content-Disposition` 仍沿用既有合同（`项目名-第1集.mp4`），与磁盘文件名是
  两件事；磁盘文件已经按新规则命名。
- 默认目录中更早的历史文件不会自动移动；用户保存任意目录（包括默认目录）时才迁移。
  「整理采用目录」只移动 `current/history` 状态文件夹，不重命名历史文件；可读重命名统一由
  输出目录迁移完成。
- `worker.py` 有改动，按 `client_review_identity` 约定旧 VLM revision 失效；真实正负校准
  未重做，不得声称已校准。
- 仓库基线已存在 `test_director_assets_closeout.py::test_an_assignment_only_reaches_a_shot_through_an_explicit_adoption`
  失败（一次采用触发两次 shot revision 自增，来自 `revise_shot_record` + `set_shot_bindings`）；
  在干净 HEAD 工作树复现，与本轮输出目录改动无关，本轮未修改该行为。
