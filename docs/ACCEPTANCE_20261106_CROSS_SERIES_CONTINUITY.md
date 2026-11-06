# 2026-11-06 跨批次续接与真实校准轮（跨 series 续接 / 部署目录缺陷）

## 1. 本轮实际做了什么

### 1.1 跨 series 任意媒体续接（代码闭环 + 端到端回归）

续接不再只是提示词里的一句话，而是一条**真实媒体**链路：

| 环节 | 实现 | 证据 |
| --- | --- | --- |
| 来源身份 | `shine/media/continuity.py` 的 `ContinuityReference` 带 lineage（源 job / shot / revision / artifact / sha256 / 字节数） | `backend/tests/test_continuity_media.py` |
| 末帧/视频/音频派生 | FFmpeg 真实解码：末帧 PNG、视频段 MP4、音频段 WAV（2–15 秒窗口，越界拒绝而非截断） | 同上（`derive_continuity_media`） |
| 依赖失效 | 源 job 必须 PASSED、未失效、shot revision 未变、attempt 与 artifact 的 sha/字节数一致 | `CONTINUITY_SOURCE_NOT_VERIFIED` |
| Worker 加载 | `worker._load_comfy_context` 按 `role=continuity_derivative` 装载 `MediaArtifact`，与用户批准的主素材严格区分 | `backend/shine/worker.py` |
| preproduction 门禁 | 派生物不进入人工绑定集合，也不进入冻结清单的 links；改由 lineage 校验 | `backend/shine/comfyops/preproduction.py::_enforce_continuity_reference` |
| ComfyUI 引用 | 上传前重新核验**源文件字节**（源被改动即拒绝），派生物与主素材一样进 manifest | `backend/shine/comfy/provider.py::_verify_asset_for_upload` |
| 跨批次调度 | 导演包策略新增 `split_continuity`（冻结在包里，重启不改计划）；批次边界可落在连续性链上 | `backend/shine/director/contracts.py`、`production/runner.py` |
| 首镜可推迟 | 以续接开场的批次不立即建 job，由 Worker 解析真实来源、在写事务之外派生引用 | `comfyops/production_series.py::create_series` |
| 来源解析顺序 | 同 series 前序镜 → 本 Run 自有已通过 job（跨集）→ 声明的 previous_job_id → 项目台账里该镜最近一次真实通过的 job | `_resolve_item_source` |

新增回归：

- `backend/tests/test_cross_series_continuity.py`：**Worker 自己的调度入口**驱动 4 镜连续链、每镜一批、四个 series；
  断言三次派生物都在 manifest 里、lineage 指向前一镜真实通过的 job、派生字节是真 PNG、渲染器确实收到了派生物上传。
  同文件还包含反例：来源没有真实通过时**不得**发起渲染、不得产生 job。
- `backend/tests/test_cross_series_source.py`：来源必须来自本 Run 且必须在前；更晚的其他生产 job 不得被采用。

### 1.2 修掉一个真实缺陷：部署目录无法声明审片模型的能力与显存预算

**症状**：本机 `qwen3-vl-8b-fp8` 已安装、文件已校验、审片服务 READY，但 `POST /workflow-capabilities/drafts`
带 `native_provider` 时被拒：`NATIVE_MODEL_PACKAGE_MISMATCH` / `NATIVE_RESOURCE_BUDGET_MISMATCH`。
于是**原生 VLM 能力永远无法发布**，冻结的生产计划也永远缺 `VLM_REVIEW`/`ASR` 能力。

**根因**：`PackageSpec` 有 `capabilities` / `resource_mb` / `resource_measurement`，
但 `CatalogComponent`（`extra="forbid"`）**没有这三个字段**，目录 JSON 也没写；
`_catalog_package_spec()` 于是把审片模型登记成 `capabilities=[]`、`resource_mb=None`、`resource_measurement=UNKNOWN`。
历史库里的 `88df7904`（4B VLM）与 `8511983d`（ASR）是旧路径登记的，才带着 `['VLM_REVIEW']/8000/ESTIMATED`。

**修复**：

- `CatalogComponent` 增加 `capabilities` / `resource_mb` / `resource_measurement`；
- `_catalog_package_spec()` 透传这三个字段；
- 目录 `config/deployment/h3-ref2va-windows-x64.json` 显式声明：
  `qwen3-vl-8b-fp8 → ['VLM_REVIEW'], 8000 MiB, ESTIMATED`；`qwen3-asr-0.6b → ['ASR'], 4000 MiB, ESTIMATED`
  （这两个数字就是 `docs/contracts.md` 写明的本机基线，不是实测峰值）；
- `register_catalog_packages()` 增加**只改声明**的升级路径：钉住的文件逐字节相同、只有声明变化时，
  更新 payload 与 sha256、保留已校验状态并写 `PACKAGE_DECLARATION_UPDATED` 审计；文件变了仍然
  `CATALOG_COMPONENT_CHANGED`。

**代价与处理**：目录哈希变化 → 运行时身份变化 → 服务验证证据失效。
本轮按受控流程重跑：`POST /setup/configuration` → `POST /setup/services/validate`（真实拉起本地服务并校验，
VLM `qwen3-vl-8b-fp8` READY，ASR `UNAVAILABLE / REVIEW_EXECUTOR_IMPORT_FAILED`）→ `POST /setup/runtime/activate` → ACTIVE。

### 1.3 ASR 的 Python ABI 问题（仍未解决，单独记录）

本机 ComfyUI 内嵌 Python 是 **3.13.11**，而已固定并校验的 `nagisa` wheel 是 **cp312**，
`qwen_asr` 导入失败 `ModuleNotFoundError: No module named 'nagisa_utils'`。
因此 ASR 能力在本机**不可用**（`REVIEW_EXECUTOR_IMPORT_FAILED`）。

- ASR 是 advisory：不阻断 H3 渲染、不阻断交付、不改变质量门禁强度；
- 不得伪报 READY；服务验证如实记录 `UNAVAILABLE`；
- 因此**本机无法冻结一份同时钉住 VLM 与 ASR 的生产计划**（readiness 的 `CAPABILITY_MISSING` 对 ASR 是硬阻断），
  这是一个具名、可复现的阻塞，不是可以绕过的门禁。

## 2. 本轮没有完成的（具名）

| 项 | 状态 | 原因 |
| --- | --- | --- |
| 真实 VLM 正负校准 → 发布新 revision → 弃用旧版 | **未完成** | 审片服务 READY、8B 模型真实加载成功（`Loading checkpoint shards 100%`），但**推理未在验收窗口内结束**（GPU 持续 100%、显存 15.8/16.3 GiB，超过 80 分钟仍未返回）。已中止该次调用以把独占显卡让给真实生产，未产生任何校准记录、未发布任何 revision。 |
| 正式计划重新分析并重新冻结 | **未完成** | 依赖上一行的新 revision；且 ASR 能力在本机不可用，冻结会被 `CAPABILITY_MISSING` 阻断。 |
| 真实多集无人值守 H3 生产 + ≥20 段成片拼接 | **进行中** | 见第 3 节。 |

## 3. 浏览器旅程（导演模式）真实进度

用内置浏览器在 `http://127.0.0.1:5173/` 的**导演模式**新建作品《夜航船 真实长片 20261106`
（project `3707a9ed-eced-435b-b69a-0b1ecdd5f146`）：

1. **创意与剧本**：写入 20 场梗概 + 锁定事实（主角老周/沈砚/船长郑海）→ 保存为创意简报 → 确认这一版 →
   **真实调用 MiniMax 文本模型**生成剧本（`creative_script_run` SUCCEEDED，产出 20 场剧本文档 `2670e301…`）。
2. **读剧本**：后台提取并归并总体大纲（`story_outline_run`），产出 **21 个生产段落**；
   首次因 `REMOTE_AGENT_CONSENT_REQUIRED` 停在 NEEDS_REVIEW，已按产品路径授予文字模型发送授权
   （`material-preparation/text-consent`，六个角色全部 `allowed=true`）并恢复任务，任务已推进。
3. **定风格**：选择「冷调悬疑」并**批准并冻结**（Style V1 `1c3b0453…`），同一次冻结绑定了官方规则包
   `shine-h3-rules/1.1.0`（hash `5990ae18…`）。

素材（GPU 出图）、正式分镜（LLM 链）、导演包与整部作品生产在本文档定稿时仍在推进中，
状态以库内 `agent_task_records` / `production_runs` / `jobs` 为准，不以本文档为准。

## 3.1 导演模式推进过程中的可见性与两个真实阻塞（本轮新增）

用户反馈「LLM 推进的过程中应该要有个进度条，不然用户会很疑惑」。排查后发现**进度条只是表象，
底下有两个真实缺陷**，都已修掉：

### 缺陷 A：文字准备队列在运行时 ACTIVE 时永远不被推进

`worker.run_configured_once()` 原来只在 **H3 运行时无效** 的异常分支里调用
`run_pending_creative_task()` / `run_pending_story_task()`：

```python
except HTTPException as exc:
    if not str(exc.detail).startswith("ACTIVE_RUNTIME_"): raise
    ... run_pending_creative_task(...) / run_pending_story_task(...) ...
```

于是**一旦运行时被正常激活，长剧本大纲与创意剧本这两个持久队列就再也没有人推进**：
本轮的大纲任务正是在运行时 INVALID 期间推进到 cursor=1，运行时一激活就永远停在原地。

修复：文字准备是**纯文字**工作，不使用也不校验视频运行时，因此移到运行时解析**之前**执行；
并且用 `TEXT_PREPARATION_BURST = 3` 限流，保证长篇大纲不会饿死真实 H3 队列
（运行层不得独占 tick，同一条纪律）。

### 缺陷 B：任务租约 30 分钟，中断后看起来像"卡死"

故事任务的认领租约是 30 分钟，续租线程每 10 秒续一次。Worker 崩溃后，任务要等 30 分钟
才能被接管，而页面上只显示"RUNNING"、没有任何进度，人只能干等。

修复：

- 租约改为 `STORY_CLAIM_SECONDS = 120`（续租线程兜底，状态版本围栏保证旧 owner 写不进来）；
- 认领条件与创意任务对齐：**没有租约即视为无人持有**，可以立即接管；
- 页面上给出"上一次推进被中断，Worker 已接管或即将接管"的提示与接管有效期倒计时。

### 前端进度条

- 后端 `public_task()` 新增 `summary`：阶段中文名、**已完成/总数**、百分比、租约剩余秒数、是否等待接管；
  `story.py` 额外记录 `segment_total`（源段落总数），进度不再只有"已处理 0 个"这种没有分母的数字。
- 前端 `StoryPreparationPanel.tsx`：新增进度条 + "已完成 N/M（x%）" + 阶段说明 + 接管提示；
  任务状态从英文枚举改为中文（排队中/进行中/已完成/需要你处理/失败/已暂停/已取消）；
  大纲摘要行同样显示百分比。样式在 `frontend/src/styles.css` 的 `.run-progress`。

## 4. 回归

- `backend/tests/test_continuity_media.py`、`test_cross_series_source.py`、`test_production_series.py`、
  `test_review_runtime_preparation.py`、`test_service_validation.py`：50 项通过。
- `test_cross_series_continuity.py`（新增）：2 项通过。
- `test_director_long_form_regressions.py`、`test_director_worker_integration.py`、
  `test_director_worker_scheduling.py`、`test_director_entries_and_post.py`：42 项通过。
- `ruff check backend/shine backend/tests`：通过。

全量套件在目录修改**之前**启动的那一轮有 8 项失败，全部是同一条原因：
测试进程加载的是改动前的 `CatalogComponent`，而磁盘上的目录 JSON 已经是新的，
`extra_forbid` 拒绝了新字段——这是**运行中改文件**造成的竞态，不是回归。
目录与代码一致后的全量回归结果见本轮结束时的最终记录。
