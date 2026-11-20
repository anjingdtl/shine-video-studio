# Shine V2.0 双生产线统一基础底座边界与调用图

> 日期：2026-11-20  
> 基线提交：`main@c1c69941e4b8ae339da5a490944206ab4cfb78a0`  
> 依据方案：[SHINE_UNIFIED_FOUNDATIONS_DUAL_PIPELINES_CLOSEOUT_V2_20261120.md](file:///f:/ClaudeWorkSpace/projects/shine-video-studio/docs/SHINE_UNIFIED_FOUNDATIONS_DUAL_PIPELINES_CLOSEOUT_V2_20261120.md)

---

## 1. 最高架构边界

1. **统一平台外壳**：`platform_projects.py`（项目创建与不可变选线）、`platform_gpu.py`（统一 GPU 互斥与租约协调入口）、`shine/foundation/`（双线共用公共能力门面）。
2. **七阶段 AI 导演业务线（`director7`）**：小说/创意 -> 文学剧本 -> 导演风格与 AI 分镜 -> 自动素材需求与备料 -> AI 编译 H3 提示词 -> 导演包/排片 -> 生产、后期与交付。既有表结构、API、UI 与生产主链保持兼容。
3. **四阶段 H3 自主制片业务线（`manual4`）**：① 人工分镜导入/编辑/冻结 -> ② 素材需求/引用 -> ③ 手写 H3 提示词/批量排片/冻结 -> ④ 成片/返工/交付。仅通过正式平台门面（`shine.platform_*`、`shine.foundation.*`、`shine.config`）调用公共底座，严禁直接导入七阶业务模块（`director*`、`literary`、`materialprep`、`production`、`services`、`worker`、`api_*` 等）。
4. **公共能力唯一事实 / 裁决 / 执行者**：
   - **统一基础配置与运行环境**：唯一安装与配方事实（`comfyops/setup_summary.py`、`comfyops/runtime_configuration.py`、`comfyops/managed_recipe.py`、`comfyops/resource_resolver.py`），唯一整备执行者（`comfyops/setup_preparation.py` + `comfyops/installation_worker.py`），唯一生命周期控制器（`runtime_orchestrator/` + `comfyops/image_renderer.py`）。
   - **公共素材中心**：唯一不可变版本与权利/QC 台账（`asset_center/models.py`、`asset_center/service.py`），唯一实际出图/声音生成执行入口（`asset_center/execution.py`、`asset_center/voice_execution.py`）。两条线各自管理需求/采纳/镜头绑定。
   - **公共 H3 制片底座**：唯一能力与配方编译（`comfy/graph_compiler.py`、`h3/mode_policy.py`、`h3/output_frame.py`），唯一 GPU 原子入场账本（`comfyops/scheduler.py:ResourceScheduler` + `platform_gpu.py` OS Mutex + `advance_epoch`），唯一 ComfyUI 提交/对账/技术质检语义。分别接受七阶 AI 编译文本与四阶用户手写冻结文本，绝不混用。

---

## 2. 模块责任矩阵与收敛清单（P0 基线审计结论）

| 域 | 现有权威实现（保留并作为唯一事实） | P0 审计发现的平行/分裂链路 | V2 收敛目标（P1–P5） |
|---|---|---|---|
| **运行环境与配置** | `comfyops/runtime_configuration.py`, `comfyops/managed_recipe.py`, `comfyops/setup_summary.py`, `comfyops/resource_resolver.py`, `comfyops/setup_preparation.py` | `manual4/comfy/managed_instance.py` 自行硬编码 `settings.data_dir/comfyops/...`、`whitelist_nodes`、忽略 `runtime_data_root` / `extra-model-paths` / `ACTIVE_KEY` / `storage_root`，自建 `verify_model_file` 死代码与 `comfy_probe_roots` 探测 | P1 建立 `shine/foundation/environment.py`，`manual4` 只读唯一环境事实与按配方能力裁决（`ready / missing_required / optional_missing / temporarily_busy / unsupported`），缺项交由原 Setup Worker 执行 |
| **受管进程与端口** | `comfyops/owned_process.py`, `comfyops/renderer_supervisor.py`, `comfyops/image_renderer.py`, `runtime_orchestrator/guard.py` | `manual4/comfy/owned_process.py` 复制 Job Object 启停且不做 `CreateFileW` 锁定、`prepare_process_environment()` 清理、`IsProcessInJob` 端口归属验证；启动四阶实例前不退休七阶空闲出图渲染器 | P1 通过 `shine/foundation/runtime.py` 共享环境净化、安装生命周期锁（`renderer_lifecycle_guard`）、七阶空闲出图渲染器退休、受管实例启停与端口排除，同时保留四阶独立端口（8198+）与独立目录（`<m4_root>/comfy-runtime/`） |
| **GPU 原子仲裁** | `comfyops/scheduler.py:ResourceScheduler` (`resource_leases` + `advance_epoch`), `comfyops/handoff_startup.py` | `platform_gpu.py:acquire_m4_gpu_lease` 绕过 `ResourceScheduler` 排队/epoch；OS Mutex 仅在四阶侧持有；`heartbeat_m4_gpu_lease` 从未被调用导致 >60s 真机渲染必过期成 `RECONCILE_REQUIRED`；七阶 `reconcile_dead_renderer_leases` 不识别 `m4:` 前缀 | P1 收束 `platform_gpu.py` + `ResourceScheduler` 为唯一仲裁入口：双线统一获取 OS Mutex + `resource_leases` + `advance_epoch`；四阶补齐后台租约续期心跳线程；死渲染器对账同时支持 `m4:` 与七阶前缀 |
| **素材中心** | `asset_center/{models,service,execution,voice_execution}.py` | `manual4/materials/service.py` 独立实现 Qwen T2I 同步生成（在 HTTP 请求内冷启 ComfyUI）与独立 `M4AssetVersion`；`api_asset_center.py` 的 `/assignments`、`/visual-facts`、`/image-jobs`、`/adoptions` 缺少 `manual4` 线隔离校验，可能误写七阶表 | P2 建立 `shine/foundation/materials.py` 平台中立合同与历史 `M4AssetVersion -> LibraryVersion` 无损映射；新 Qwen 生成走公共素材中心任务入口；封堵 `api_asset_center.py` 跨线误写；保留四阶 `M4ShotAssetBinding` 独立绑定与 `M4MaterialRequirement` 需求裁决 |
| **H3 制片底座** | `comfy/graph_compiler.py`, `comfy/client.py`, `comfy/workflow.py`, `h3/mode_policy.py`, `h3/output_frame.py`, `quality/technical.py` | `manual4/comfy/h3_adapter.py` 复制图编译且仅限 `int8`；`manual4/worker.py` 在 `/prompt` 前写入的是模板 `graph_sha256` 而非参数化终图 SHA；无外部 `/history` 对账；`production/service.py` 跨集串 `prev_job_id`；`extra_last_frame` 用 `idx > 0` 跨集误判 | P3 建立 `shine/foundation/h3.py` 统一 `H3ExecutionRequest` / `H3ExecutionReceipt` 契约与执行底座；保留旧代际（`execution_generation=1`）兼容对账，新任务（`execution_generation=2`）走统一编译器/执行器；修复跨集 `last_frame`、提示词 SHA 强校验与 ComfyUI 对账 |
| **启动隔离与四阶缺陷** | `main.py`, `platform_projects.py`, `manual4/{storyboard,status,delivery}/*`, `frontend/src/*` | `main.py` 无条件初始化 `manual4.sqlite`（损坏拖垮七阶）；`storyboard/service.py` 仅时长变化才作废提示词且仅 12k 截断 LLM；`readiness.py` 仅凭任意绑定判 Ref2VA 就绪、按条而非按集统计交付验收；`delivery/service.py` 选片不校验修订/真实度；前端存在跨线串调 | P1 修复 `main.py` 四阶 DB 故障隔离降级；P4 修复分镜修订失效、长文本分块 LLM、Ref2VA 真图+权利+SHA 就绪、后期选片与跨线前端防护 |

---

## 3. P0 基线冻结记录

### 3.1 本机数据基线备份
- 备份目录：`F:\ShineTest\backup-v2-p0-20261120`（独立于仓库与 `D:\Shine\data`）
- `D:\Shine\data\shine.db`：`32,051,200` 字节，SHA-256 `8c65fbab218fe3c03f0f657030be50b6a9f7327f63aea8449af4267a5b670792`（15 个项目、90 个资产、442 个镜头、43 个作业、64 个媒体产物、8 个公共素材资产）
- `D:\Shine\data\manual4.sqlite`：`356,352` 字节，SHA-256 `2bf978d36a510dfbb006dc93161e07d88c6a6f5eac94ae3405791270a49aeb7e`（当前 0 业务行，仅 `m4_schema_meta=2`）
- `D:\Shine\data\comfyops\setup\managed_recipe.json`：`3,752` 字节，SHA-256 `8dcd6d5f34ec83ad6a885258ccaff3d88d2ad56a8183eceebb86df15aaee881f`
- `D:\Shine\data\comfyops\image-runtime.json`：`3,779` 字节，SHA-256 `5f0c73dbb32875c8f84afcf2a299ed02b1b62ff93c676fc797379a215da65de2`

### 3.2 本机共享 ComfyUI 环境（不重装、不重复配置）
- 已采纳实例 ID：`22ea7c4e-316c-5ff3-a4aa-632186c81a5e`
- ComfyUI 根目录：`F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI`
- 显卡：NVIDIA GeForce RTX 5080（16303 MiB VRAM）

---

> 本文档将在 P5 收尾时补充最终验证后的完整调用图、模块退役清单与 A01–A22 对应入口索引。
