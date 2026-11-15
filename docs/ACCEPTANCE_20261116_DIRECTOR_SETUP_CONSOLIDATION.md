# 导演模式与本机环境收束 · 验收报告（2026-11-16）

> 分支 `feat/director-setup-consolidation`，基于 `origin/main@351f3a4`，本轮 17 个提交
> （`7573dc7`…`a0be128`）。开发 worktree：`shine-video-studio-director-dev`；
> 隔离数据目录 `.local-acceptance/dev-20261115/data`，端口 API 8040 / Web 5210 / 监督 8791。
> 方案依据 `SHINE_DIRECTOR_SETUP_CONSOLIDATION_PLAN_20261115.md`（commit f64e4b5）。

## 1. 工作包完成情况

| 工作包 | 状态 | 关键提交 |
|---|---|---|
| 1 基线与调用关系 | 完成 | `7573dc7` 承接解阻轮+分镜链真跑轮未提交修复 |
| 2 权威状态与导航 | 完成 | `ad25afb` 导演工作台唯一入口，专业配置迁入「高级设置」 |
| 3 统一检索与资源闭包 | 完成 | `536e5db` 机器级深搜索引 + 可空项目安装计划（迁移 f180f0010001） |
| 4 后台统一整备 | 完成 | `8174f1e` 一次点击持久任务；`9cc7555` 服务核验委托监督进程解互斥 |
| 5 能力与业务接续 | 完成 | 出图全六模式统一验证；LLM 失败恢复（见 §4） |
| 5b PDD8/精修/4步/RelayVSR | 完成 | `ee51667` 工作流与节点整备下载能力 |
| 6 安装/升级/交付验证 | 完成 | 安装包构建+SHA 验证（见 §3） |

用户追加三项需求亦完成：
- 需求1 就绪状态持久化、切页不重检、全局一致进度条（`fe4eeba`）。
- 需求2 清理高级设置与基础配置重叠项（`fe4eeba`、`a0be128`）。
- 需求3 出图全能力（T2I/I2I/INPAINT/MULTIREF/WARDROBE/RGBA）归一化到基础配置验证登记核对
  （`f487af5`、`85415ba`、`fc18016`）。
- 能力状态归一为「①文件齐不齐 / ②跑不跑得通」两步，跑不通配一键修复按钮（`f487af5`、`cfa68f1`）。

## 2. 自动化回归

- **后端 8 分片全量**（`CODEBUDDY_SAFE_DELETE_ENABLED=0`，仓库根，按文件均分 8 片并行）：
  共 **2149 例**。干净一轮（开发栈停止、无并发干扰）**7/8 分片 exit=0**；
  chunk-aa 仅 1 例 `test_owned_process_group::test_managed_group_accepts_explicit_unicode_environment`
  为 8 进程并发时序假失败，**单独复跑通过**。日志：`.local-acceptance/dev-20261115/shards/chunk-*.log`。
- **前端全量** `npx vitest run --no-file-parallelism`：**78 文件 / 396 例全绿**（exit=0）。
  日志：`.local-acceptance/dev-20261115/frontend-full-final2.log`。
- **TypeScript** `tsc --noEmit` 通过；**生产构建** `npm run build` 通过。
- **Ruff** 改动文件全部通过。
- **Alembic**：空库升级至 head `f180f0010001`，`alembic check` 无 drift；
  真实旧库（含 installation_plans/attempts 行）升级后逐行保留、`foreign_key_check` 干净。

## 3. 安装包

- `pwsh -File scripts/build_test_package.ps1 -OutDir releases -Verify` 通过。
- 产物 `releases/ShineVideoStudio-20261116-a0be128-win64.zip`（7,723,840 B），
  SHA-256 `6e999fd5f3a4f610ea2aacf3fa865a5d52ac531a328642fc7c9f0abf0a44be24`；
  解压后 **1118 文件逐文件 SHA-256 校验一致**。

## 4. 真机实证（RTX 5080 16GB，本机 ComfyUI F:\AIGC2026）

- **一次点击整备**：浏览器点「确认并完成配置」→ 单个持久任务
  `RECONCILE→DOWNLOAD→TARGET→RECIPE→IMAGE_BIND→PROFILES→VALIDATE→IMAGE_VERIFY→IMAGE_SAMPLES→IMAGE_EDIT_SAMPLES`。
  已有权重零下载（`固定清单的文件都已在本机，无需下载`）。
- **服务核验互斥解决**：VALIDATE 委托监督进程「停 Worker→真实渲染器核对节点→激活→自动恢复 Worker」，
  判定 `READY`、`services_verified=True`。
- **出图全六模式真实验证**：T2I 三类小样 + I2I/INPAINT/MULTIREF/WARDROBE/RGBA 均经
  与素材中心同一台账真实渲染；素材中心 `QWEN_IMAGE_T2I/I2I/INPAINT/MULTIREF/WARDROBE` 均 `READY`。
  RGBA 须输出含非零透明像素才算就绪（权威判定，不冒充）。
- **出图中断治本**：受管渲染器无头运行 tqdm flush 已关闭 stdout 致 KSampler
  `I/O operation on closed file`；启动脚本保持 stdout/stderr 引用存活 + `TQDM_DISABLE=1` 修复，
  三类小样真实落盘（带 SHA-256）。
- **失败恢复（A10）**：镜头拆解页对 `LLM_CONNECTION/UNKNOWN` 阻塞显示
  「核对这次请求并重试」；点击后第 1 集由 `BLOCKED` 恢复为 `IN_PROGRESS` 并最终
  **已确认（v2，23 镜）**，剧组链跑完全部 6 场。截图
  `.local-acceptance/dev-20261115/a10-recovery-episode-confirmed.png`。

## 5. A01–A12 验收矩阵

| 编号 | 状态 | 证据 |
|---|---|---|
| A01 空库/安装器/旧书签 | 通过 | 导演唯一入口；`#setup`/`#v/…` 映射；高级设置可达（浏览器快照） |
| A02 深目录/多盘/多 ComfyUI | 通过 | 深搜 7/7 盘，发现 1 套 ComfyUI、33 权重候选、23 工作流 |
| A03 改名/缺分片 | 通过 | 按大小+SHA 匹配，已有文件零下载（后端回归 + 真机） |
| A04 不可读/慢盘/重复链接 | 通过 | 覆盖报告具名 3 个不可读区域，分批继续 |
| A05 一次准备/精确变体 | 通过 | 单任务贯穿，非默认端口/变体一致（真机） |
| A06 断网/关页/重启/重复点击 | 通过 | 持久任务幂等复用，恢复点续跑（后端回归） |
| A07 外部 ComfyUI/GPU 忙/遗留 | 通过 | 受管归属证明，验证等待安全时机（后端回归 + 真机） |
| A08 只有图片能力 | 通过 | 六模式真实小样，H3 缺失不阻断图片（真机） |
| A09 只有 H3/可选审核失败 | 通过 | 可选审片独立记录，不阻断（后端回归） |
| A10 文本/修订/场次恢复 | 通过 | 失败场次浏览器恢复并落镜（真机，§4） |
| A11 完整真实制作（约六镜成片） | **未完成** | 见 §6 阻塞 |
| A12 状态一致/升级/重装 | 通过 | 三处同源判定；旧库升级保留；安装包复验 |

## 6. A11 进展与剩余步骤

整备与分镜恢复已真机通过，journey 已达 **4/7 阶段完成**（剧本/统筹/分镜/素材），
素材清单已按 23 镜生成。**用户裁定的「一键整备含节点核对」已实现**：

- **模型可用性矛盾——已修复**：`variant-availability` 原先每请求重哈希数 GB 权重
  致超时、导演包误显「本机没有」。修复三处：① 台账证明安装即跳过目录扫描；
  ② 核验回执回退哈希贯穿 `cache_dir`（sidecar 缓存生效）；③ 整体结果加项目级 60s
  缓存（切页即返回）。
- **一键整备自动核对本作品节点**：监督进程核验激活后逐方案
  `validate_registered_workflow`（实测「已核对 6 个生成方案节点」）；VALIDATE 步
  跳过条件改为「合同已激活且本作品方案均已核对」。修复后导演包 INT8 显示
  `available=True`「本机已就绪」，按钮阻塞原因由「本机没有这套权重」变为「至少选择一集」。
  截图 `a11-model-ready-after-oneclick.png`（快照存证）。

**剩余步骤（A11 完整成片尚未产出）**：
1. 角色主素材准备：清单派生出 32 条 `SHOT_CHARACTER_MASTER_MISSING` 提示
   （沈砚/委托人/老陈/安娜等）。按产品规则缺图由 H3 自行生成、不硬阻断，但人物
   主素材需生成候选并人工确认锁定（素材中心出图能力已就绪可直接生成）。
2. 勾选第 1 集 → 冻结导演包 → 启动生产运行（H3 逐镜渲染 23 镜）→ 后期合成 →
   可解码成片 + 人工验收。

本轮未声称 A11 完整成片通过；真实推理与人工验收均按实际证据记录。

## 7. 边界声明

- 本轮未改 `worker.py`/`preproduction.py` 的 `client_review_identity` 钉住语义之外的
  审片合同；历史 VLM 正负校准仍未完成，不声称已校准。
- 所有真实产物、密钥、数据库、私有验收数据留在本机隔离目录，未进安装包。