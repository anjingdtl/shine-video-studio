# Shine Assets 云端实施与验收报告

状态：Phase 0–4 云端实现与第一性原理对抗验收完成。Windows RTX 5080 / ComfyUI 真实推理未运行，所有依赖本机证据的能力仍为 `PENDING_LOCAL_VALIDATION`；后端全量运行中有 14 项既有 Windows/本机条件用例在 Linux 云端失败，未绕过其门禁。

## 基线与 Git

- 规范：`docs/SHINE_ASSETS_CENTER_FULL_BUILD_PLAN_20261108.md`，只从未合并规范分支读取，提交 `76a4e3abf3c4b20d32eb28925656820c2d3b9e3c`；该分支未合入功能分支。
- `origin/main` 起始 SHA：`631d9a07c6be5911a88e138bb037da3d369f3d79`。
- 功能分支：`feature/shine-assets-center-20261108`。
- 起始迁移 head：`f180b0020001`；新增 head：`f180b0030001`。
- 起始工作树：干净；没有打开或修改生产数据库、密钥、用户图片/声音或 H3 任务。
- 远端规范 PR #9 仍为 open；功能分支保持独立，目标为 `main`。
- Draft PR #10：目标 `main`，保持 open/draft/未合并；GitHub 当前 head ref 为 `feature/shine-assets-center-20261108`。本机 RTX 5080 验收证据不存在，状态为 `NOT_RUN`。

## 实现范围

- 新增无 `project_id` 的全局素材、媒体、版本、来源引用、权利、审批授权、声音、任务尝试和项目分配记录。新增项目绑定删除时只清理项目本地身份；项目级 RightsRecord 随所属项目删除，独立全局权利、正式版本和内容寻址文件继续保留。
- 新增 Shine Assets 顶层路由和独立布局：首页、图片工坊、声音工坊、全局素材库、项目分配；保留 Director 六阶段和现有专业设置入口。
- 图片和声音 GPU 任务从现有 Worker 串行执行，共用排他 ResourceLease；图片/声音全局队列每个 Worker tick 只执行一项，H3 保持高优先级。ComfyUI 需确认空队列及无活跃 CUDA tensor；声音任务需确认推理子进程退出或本机 HTTP 服务返回释放确认。TTS 在 ASR、词级对齐前分别确认模型释放；任何未知释放状态都阻断后续阶段并要求对账。T2I 复用旧固定图；edit 适配器固定官方 ComfyUI Qwen-Image-2.1 edit 样例 revision/hash。I2I、局部修订、多图、穿戴、原生 RGBA、2K 均以真实本机证据门控。
- INPAINT 传递真实 PNG 遮罩字节并记录 SHA/尺寸/语义；因上游图无 mask 节点，输出在收获后按实际像素遮罩合成。透明抠图另外提供确定性手工 MASK_CUTOUT_V1，未宣称 AI 分割。
- 声音任务复用当前 provider、TTS ResourceLease、授权过期/撤销检查、ASR/词级对齐和人工试听冻结；音效/音乐只管理导入。
- Director→Assets 搜索全局正式版本，分配创建新项目 Asset 身份；项目锁定素材可经明确权利确认保存为仅限当前项目的全局候选。旧项目冻结引用保留其原始版本。
- 新增版本读取 API，支持本机验收工具读取不可变元数据并独立校验下载文件 SHA 与解码尺寸。

## 图片与声音能力矩阵

| 能力 | 软件状态 | 云端结论 |
|---|---|---|
| T2I | 复用已审计 Qwen-Image-2.1 T2I graph；validation-only 必须走真实 Worker/lease | 本机 GPU `NOT_RUN`；没有宣称 READY |
| I2I | 有序图像字节上传、角色槽、版本谱系、官方 edit graph adapter | 本机 object_info / 真图 `NOT_RUN`，`PENDING_LOCAL_VALIDATION` |
| INPAINT | 有像素遮罩上传、精确白改黑保留的收获后合成、空/全幅/尺寸错拒绝 | 本机真图/人工边缘检查 `NOT_RUN`，`PENDING_LOCAL_VALIDATION` |
| MULTIREF | `image_1…image_10` 显式映射，10 张总数上限，逐文件 hash/顺序保留 | 本机模型合成 `NOT_RUN`，`PENDING_LOCAL_VALIDATION` |
| WARDROBE | MultiRef 的人物/服装/鞋帽/配饰/道具语义预设 | 人物/服装视觉质量 `NOT_RUN`，`PENDING_LOCAL_VALIDATION` |
| RGBA | 手绘 mask 确定性生成真 alpha PNG；Qwen 原生透明输出另行检查 alpha 像素 | 手绘操作软件测试可 PASS；Qwen 原生透明 `NOT_RUN` |
| 2K | 尺寸合同和本机 sample gate；2K 只有验收后才开放 | 16GB VRAM/OOM/输出尺寸 `NOT_RUN`，`PENDING_LOCAL_VALIDATION` |
| Voice design | 本地 provider + task ledger + 试听/人工冻结 | 本机声音模型和试听 `NOT_RUN` |
| Authorized clone | WAV/用途/许可/主体/期限快照，提交前重查授权 | 真实克隆/撤销/过期硬件流程 `NOT_RUN` |
| Mandarin TTS | 冻结音色、现有 TTS provider、ASR/对齐/hard QC | 本机对白质量 `NOT_RUN` |
| SFX/music | 导入、分类、试听、检索 | 无生成 provider，不生成假结果 |

## 20 项验收矩阵

最终状态与证据在 Phase 4 完成后按实际结果更新。`PASS` 表示软件合同/离线反例成立，不代表真实 GPU 推理通过。

| ID | 云端状态 | 证据/边界 |
|---|---|---|
| AC-01 | PASS | `test_projectless_import_persists_and_asset_metadata_uses_compare_and_swap`；Assets 独立首页无项目 UI 用例 |
| AC-02 | PASS | URL 深链与素材中心跳转/返回导演页面的前端回归；六阶段状态依旧由 Director API 驱动 |
| AC-03 | PASS | `test_project_assignment_is_scoped_immutable_and_idempotency_binds_identity` 覆盖 QC、一次性授权、人工晋升及正式版本；assignment 只接受 `PROMOTED` 版本 |
| AC-04 | PASS / 本机 NOT_RUN | `test_edit_adapter_uploads_every_reference_byte_and_preserves_slot_order`；本机 Comfy 实际生成未执行 |
| AC-05 | PASS / 本机 NOT_RUN | mask 像素对齐、空/全幅拒绝、实际上传 SHA 与像素合成测试；鼠标/触控本机绘制验收未执行 |
| AC-06 | PASS / 本机 NOT_RUN | 十个输入槽映射和内容上传顺序反例；live graph/model 尚未执行 |
| AC-07 | 软件 PASS / 视觉 NOT_RUN | WARDROBE 走 MULTIREF 且强制人物+服装语义；Windows 画面一致性与第二候选质量未执行 |
| AC-08 | PASS / Qwen 本机 NOT_RUN | alpha 像素验证及手动遮罩转透明 PNG；Qwen 原生 alpha 未执行 |
| AC-09 | 软件 PASS / 本机 NOT_RUN | 声音能力状态、许可快照、TTS 冻结音色合同；真克隆、试听和声音文件链待本机验收 |
| AC-10 | PASS | `test_project_assignment_is_scoped_immutable_and_idempotency_binds_identity` 验证同一全局文件 SHA 可创建不同项目 Asset ID，项目实体身份独立 |
| AC-11 | PASS | `test_project_assignment_is_scoped_immutable_and_idempotency_binds_identity` 删除项目 B；A 的 V1 link、项目身份/权利行和文件内容仍存在，B 本地绑定和权利行随项目删除 |
| AC-12 | PASS | requirement hash 被 Director API 输出并在分配时核对；陈旧 hash 返回 409 |
| AC-13 | PASS | 请求幂等键、版本 CAS、语义 unique、刷新仅读及并发落库约束 |
| AC-14 | PASS | `test_unknown_image_task_cannot_be_retried_and_confirmed_not_sent_can_get_new_identity`；陈旧 UNKNOWN 不入队 |
| AC-15 | 软件 PASS / 本机 BLOCKED | `test_voice_task_returns_to_queue_when_h3_holds_the_shared_resource_lease`、`test_voice_tts_releases_each_gpu_model_before_invoking_the_next_stage` 与 GPU release confirmation/serial lease 测试；RTX 5080 上 H3 + Assets 并发仍 `NOT_RUN` |
| AC-16 | PASS（离线）/本机 NOT_RUN | readiness fingerprint/adapter hash 不匹配不允许继续生成；live node/model 漂移需本机验证 |
| AC-17 | 代码回归 PASS / 14 项主机条件失败 | Director/Four-stage 回归与迁移测试包含在 1,859 项通过中；4 项 Windows 主机资格、2 项本机执行开关、1 项 Windows renderer、6 项 Windows service validation、1 项磁盘容量用例无法在此 Linux 云端按原条件通过 |
| AC-18 | PASS | 上传真实解码、哈希、尺寸、用途、文件名/大小限制及 project scope 错误码测试 |
| AC-19 | PASS / 本机推理 NOT_RUN | `test_revoked_global_rights_cannot_be_restored_by_scope_patch`、`test_authorized_voice_clone_rejects_revoked_or_expired_consent` 与来源衍生权限测试覆盖撤销、过期及派生限制 |
| AC-20 | PASS | 中文主流程、中文能力状态和错误下一步；Assets 空库无需专业设置的 UI 用例 |

## 实际测试日志

区分 Linux 软件/Mock 测试与真实 Windows/GPU 验收。完整后端结果按本次 pytest 最终摘要记录。

| 检查 | 命令 | 退出码与结果 |
|---|---|---|
| Backend tests | `.venv/bin/pytest -c pyproject.toml tests --tb=short`（`backend/`） | exit 1：1,859 passed、137 skipped、14 failed，用时 31:15.81（31 分 15.81 秒）。4 项主机资格要求 Windows，2 项需要本机执行开关，1 项 image renderer 与 6 项 service validation 为 Windows-only，1 项 image-weight 安装计划因云端磁盘容量不足失败；deployment budget 用例的 OS gate 先于磁盘断言。没有放宽门禁。 |
| Ruff | `.venv/bin/ruff check shine alembic tests`（`backend/`） | exit 0：All checks passed |
| Alembic heads | `.venv/bin/alembic -c alembic.ini heads`（`backend/`） | exit 0：单 head `f180b0030001` |
| Fresh DB + old DB copy | `.venv/bin/pytest -c pyproject.toml tests/test_alembic.py tests/test_migrations_windows.py -q --tb=short`（`backend/`） | exit 0：7 个迁移用例通过，覆盖新库、旧版本升级和 downgrade/re-upgrade |
| Frontend tests | `npm test -- --run`（`frontend/`） | exit 0：27 个测试文件、183 tests passed |
| TypeScript | `npx tsc --noEmit`（`frontend/`） | exit 0 |
| Production build | `npm run build`（`frontend/`） | exit 0：Vite 产物完成；754.46 kB 主 JS 触发 >500 kB 提示，gzip 234.45 kB |
| Browser interaction smoke | Chromium + Playwright inline Node; local API responses stubbed to an empty workspace | PASS：无项目打开素材首页、导航到图片工坊、刷新恢复路由、往返 Director；0 failed requests、0 page errors。此项只验 UI 流程，不作为后端或 GPU 证据。 |
| Windows RTX 5080 + ComfyUI samples | `docs/SHINE_ASSETS_LOCAL_QWEN_VALIDATION_20261108.md` | `NOT_RUN`：云端无 Windows/RTX 5080/本机 ComfyUI 权重 |

后端 14 个非通过项：`test_wizard_install_payload_is_accepted`、`test_full_deployment_budget_includes_environment_and_snapshots`、`test_shipped_service_uses_real_install_contract`、`test_shipped_service_uses_real_instance_approval_and_state_machine`、`test_the_product_can_plan_the_image_weight_installation`、`test_an_unmanaged_instance_is_refused`、`test_a_qualified_host_gets_a_plan_that_records_the_variant`、`test_next_instance_plan_state_machine`、`test_service_validation_binds_reviewer_models_and_l4_evidence`、`test_service_validation_rejects_live_workflow_contract_missing_node`、`test_missing_review_models_and_runtime_are_advisory_and_do_not_block_renderer`、`test_unavailable_review_probes_are_advisory_after_clean_process_exit`、`test_review_probe_cleanup_failure_keeps_core_environment_unconfirmed`、`test_service_validation_requires_candidate_and_operator`。前四个主机资格用例受 Linux/Windows 资格差异影响；两个 Director setup 用例受本机执行开关影响；image weight 用例因磁盘不足；renderer 与六个 service validation 用例受 Windows-only guard 阻断。deployment budget 用例被 OS gate 提前拦截，未到磁盘断言。

## 合并前检查与风险

- Qwen 官方工作流 UI graph 已锁 commit/hash，但本地 API graph 与 live `object_info` 适配尚未在 Windows ComfyUI 上实测。相关模式没有本机样本前仍应保持 `PENDING_LOCAL_VALIDATION`。
- 官方编辑图没有原生 mask 输入。局部修订采用模型编辑后确定性像素合成，边界质量和人物/服装一致性需要人工验收。
- 2K 峰值显存、10 图合成、H3 长片 lease 排队和 OOM 恢复需 RTX 5080 隔离实测。
- 上游模型为 research license；所有第三方素材、模型、肖像、声音克隆权利单独核验，商业用途不默认许可。
- H3、Windows 安装器和真实 ComfyUI 安全门禁保持原语义，不在云端修改证据或伪造 hardware READY。
- 对抗性检查确认：项目级图片任务每轮只处理一项；全局 image/voice 队列按时间顺序、每个 Worker tick 只处理一项；H3 保持优先级且共享排他 `ResourceLease`。ComfyUI 必须确认运行/等待队列为空并通过 `/system_stats` 显存释放检查；声音 TTS 在 synth→ASR、ASR→align 前分别要求 provider 释放确认。无法确认时停止后续阶段并保留不确定结果供对账，不自动重新提交。
