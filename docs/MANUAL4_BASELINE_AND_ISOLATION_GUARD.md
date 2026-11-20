# Shine 四阶段独立生产线 — P0 基线冻结与隔离守护清单

> 冻结日期：2026-11-20  
> 基线提交：`main@9be718b22397bea0204f293a5db7677352eb74d4`（`origin/main` 同步一致）  
> 方案来源：`origin/docs/manual4-isolated-four-stage-production-line-v1-20261120` → `docs/SHINE_MANUAL4_ISOLATED_FOUR_STAGE_PRODUCTION_LINE_V1_20261120.md`

---

## 1. 本地基线与环境事实

- **Git 状态（检出方案前）**：
  - 当前分支：`main`（与 `origin/main` 同步在 `9be718b`）
  - 未跟踪本地目录：`.trae/`（保留不动）
- **本机硬件与运行配置（`.env`）**：
  - GPU：`NVIDIA GeForce RTX 5080`（16303 MiB VRAM）
  - Python：`backend/.venv/Scripts/python.exe`（Python 3.12.13）
  - 数据目录：`SHINE_DATA_DIR=D:\Shine\data`（内含 15 个既有七阶项目、`shine.db`、`secrets/`、`comfyops/`；四阶开发与测试绝不改写既有七阶行或文件）
  - ComfyUI 现有受管目录：`F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI`（端口 8188 为七阶受管实例；四阶使用独立端口、独立 `runtime_data_root`、独立 `OwnedProcessGroup` 与日志，仅只读共享模型权重仓）
  - 既有本地模型权重确认：`minimax_h3_ref2va_pruned_int8_convrot.safetensors`、`MiniMax_H3_Ref2VA_pruned_nvfp4.safetensors`、`qwen_image_2.1_int8_convrot.safetensors`、`relayvsr_x2_lightx2v_int8_convrot.safetensors`

## 2. P0 基线测试证据

- `ruff check backend/shine`：通过
- `frontend`: `npm.cmd run build` (`tsc -b && vite build`)：通过（1651 modules）
- `frontend`: `npx.cmd vitest run --no-file-parallelism`：87 文件 / 451 用例全部通过
- `backend`: `.\run_backend_shards.ps1 -WorkRoot F:\ShineTest\manual4-shards`（8 分片、286 文件）：**8/8 分片全部 clean（exit 0，`shards_with_failure_markers=0`）**，日志归档于 `F:\ShineTest\manual4-shards\shards-20261120-102043-189`。

## 3. 七阶零侵入白名单（禁止为四阶修改的业务模块）

以下七阶业务模块与界面在四阶建设全过程中**禁止修改**：

### 后端七阶业务模块（零改动）
- `backend/shine/director/`
- `backend/shine/director_styles/`
- `backend/shine/literary/`
- `backend/shine/materialprep/`
- `backend/shine/production/`
- `backend/shine/media/`
- `backend/shine/asset_center/`
- `backend/shine/agents/`
- `backend/shine/voiceops/`
- `backend/shine/quality/`
- `backend/shine/h3/`
- `backend/shine/comfy/`
- `backend/shine/copilot/`
- `backend/shine/support/`
- `backend/shine/services.py`
- `backend/shine/worker.py`
- `backend/shine/operator_review.py`
- `backend/shine/production_mode.py`
- `backend/shine/api_*.py`（全部七阶业务接口）

### 前端七阶页面与组件（零改动）
- `frontend/src/useStudio.ts`（除最小平台新建分流外不改七阶状态逻辑）
- `frontend/src/asset-center/`
- `frontend/src/components/`（除 `ProjectPortfolio.tsx` 按选线读取概览及新建选线外，其余七阶面板如 `ShotPanel.tsx`、`StoryboardPanel.tsx`、`DirectorShootPanel.tsx`、`DirectorProductionRunPanel.tsx`、`MaterialPreparationPanel.tsx`、`StylePanel.tsx`、`SetupCenter.tsx` 等全部零改动）

## 4. 允许的最小平台壳改动白名单

仅允许平台外壳作最小范围变更（逐文件审查）：

1. `backend/shine/models.py` + `backend/alembic/versions/`：新增平台选线登记表 `project_workflows`（`project_id`, `workflow_kind`, `selection_revision`, `created_at`）；**不修改七阶旧表结构与历史数据**，无登记记录的历史项目一律解释为 `director7`。
2. `backend/shine/platform_projects.py`：平台项目选线注册、版本化模式查询、`POST /api/v1/platform/manual4-projects` 原子创建、`GET /api/v1/platform/projects` 列表查询、以及只读 LLM 连接配置视图（不走七阶 `AgentRoute` / `LLMGateway` 文学任务链）。
3. `backend/shine/platform_gpu.py`：跨生产线系统级物理 GPU 互斥协调器（OS 命名互斥锁 + `shine.db` `resource_leases` 独占租约仲裁，保障 `director7` 与 `manual4` 生产任务互斥、异常退出时校验进程消失后方可释放）。
4. `backend/shine/main.py`：注册 `platform_router` 与 `manual4_router`，初始化 `manual4` 独立数据库（`manual4.sqlite`）与独立数据根目录（`<data_dir>/manual4/`），以及跨线 `/api/v1/projects/{id}/...` 拦截保护中间件（七阶接口遇到 `manual4` 项目直接返回 409）。
5. `scripts/start_shine.ps1`：按需伴随拉起独立 `worker-m4`（`python -m shine.manual4.worker`），由现有 `stop_shine.py` 凭 OS 出生身份安全停止。
6. `frontend/src/App.tsx`、`frontend/src/api.ts`、`frontend/src/components/ProjectPortfolio.tsx`：新建作品选线（默认 `director7`）、列表按选线分流读取摘要（`manual4` 绝不调用七阶 `/journey`）、选中 `manual4` 项目挂载独立 `Manual4App`。

## 5. `manual4` 禁止 import 图（由静态测试强制门禁）

`backend/shine/manual4/**` 内所有 Python 模块**严禁导入**以下七阶业务包/模块（含 `import ...` 与 `from ... import ...`）：

- `shine.director` / `shine.director_styles` / `shine.literary`
- `shine.materialprep` / `shine.asset_center` / `shine.agents`
- `shine.production` / `shine.media` / `shine.quality` / `shine.voiceops`
- `shine.h3` / `shine.comfy` / `shine.comfyops`
- `shine.copilot` / `shine.support`
- `shine.services` / `shine.worker` / `shine.operator_review` / `shine.production_mode`
- `shine.api_*` / `shine.main`

允许 `shine.manual4` 导入的本仓模块仅限于平台壳与通用连接/秘密读取：
- `shine.config`、`shine.platform_projects`、`shine.platform_gpu`、`shine.llm.secrets`
- `shine.manual4.*` 内部模块
- 标准库与通用第三方框架（`fastapi`、`pydantic`、`sqlalchemy`、`httpx` 等）

前端 `frontend/src/manual4/**` 严禁导入七阶业务组件（`../components/*`、`../asset-center/*`、`../useStudio`）；仅可复用主题样式变量与基础图标库（`lucide-react`）。
