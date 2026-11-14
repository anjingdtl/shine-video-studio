# Shine 视频工作台｜制作环境一体化 V4.1 实施报告

- 基线：`main@8e3e1e07850549df57511e0f330eb9ea22543dc2`
- 开发分支：`feat/shine-setup-one-stop-v41-20261114`（独立 worktree：`F:\ClaudeWorkSpace\projects\shine-video-studio-setup-v41`）
- 方案依据：`docs/SHINE_SETUP_ONE_STOP_V41_DESIGN_20261114.md`、`docs/SHINE_SETUP_ONE_STOP_V41_LOCAL_AGENT_PROMPT_20261114.md`（随本分支一并合入）

## 1. 交付概览

| 工作包 | 状态 | 交付物 |
| --- | --- | --- |
| WP0 基线审计与复现 | DONE | 调用链与根因写入架构文档；两个 P0 的 RED 用例先写后修 |
| WP1 唯一事实视图 | DONE | `comfyops/setup_summary.py`、`GET /setup/summary`、`GET /setup/missing-files`、`SetupEnvironmentStatus.tsx` |
| WP2 零人工登记 | DONE | `POST /setup/reconcile`（幂等 + 单飞 + 回执复用）、多候选安装解析与一次性选择 |
| WP3 一次授权与统一身份 | DONE | `POST /setup/prepare/execute` + `setup_preparation.py`（Worker 推进的持久步骤机）、端口/实例身份统一 |
| WP4 下载兜底 | DONE | `missing-files` 真实缺项清单、`POST /setup/manual-files/reconcile`、`<deployment_root>/manual-downloads` 暂存目录 |
| WP5 UI 收束与渲染器接入 | DONE | 设置页唯一闭环、导演页只读摘要、`PENDING_RECIPE` 语义修正、错误文案中文化 |
| WP6 回归与 PR | IN PROGRESS | 前端全量 / Ruff / tsc / build 已过；后端 8 分片执行中；PR 待提交 |

## 2. 新增 API

| 方法 | 路径 | 语义 | 副作用 |
| --- | --- | --- | --- |
| GET | `/api/v1/setup/summary` | 机器级 + 项目级事实快照、5 阶段投影、唯一下一步 | 无（无 DB 写、无文件写、无进程启动） |
| GET | `/api/v1/setup/missing-files` | 由固定清单推导的真实缺项（URL/大小/SHA/保存目录） | 无 |
| POST | `/api/v1/setup/reconcile` | 发现 → 核验 → 登记 →（可选）接管，幂等单飞 | 写 Package/Receipt/Audit；可选写实例行 |
| POST | `/api/v1/setup/manual-files/reconcile` | 暂存目录内的文件校验并导入既有缓存 | 只写 `isolated-packages/.cache/<sha256>` |
| POST | `/api/v1/setup/installation-choice` | 记住"这台机器用哪套 ComfyUI" | 写一条 AppSetting + 审计 |
| POST | `/api/v1/setup/prepare-plan` | 授权前的变更预览（下载量、组件、是否起服务） | 无（不建 plan） |
| POST | `/api/v1/setup/prepare/execute` | 把授权固化成持久任务 | 写任务行；长任务由 Worker 执行 |
| POST | `/api/v1/setup/prepare/cancel` | 取消未完成的准备 | 写任务行 |

## 3. 修改的关键文件

后端：

- `comfyops/image_renderer.py`：新增 `image_recipe_state()`；`load_image_recipe()` 的 409 文案改为面向用户并带 `auto_recovery`。
- `materialprep/runtime.py`：`ImageRuntimeConfig` 增加 `installation_id/port/recipe_sha256`；`readiness()` 增加 `binding_state/runnable`，`PENDING_RECIPE` 时四层全部不成立。
- `comfyops/deployment.py`：`generate_configuration(capability="h3"|"image")`；镜像能力不要求 H3 权重、不写 runtime candidate、不写 `SHINE_SETUP_RECIPE`。
- `comfyops/adoption.py`：能力级证据（`verified_files[].capability`、`adoption.capabilities`）；同一 root 上追加能力被允许，证据丢失仍拒绝。
- `api_setup.py`：新增 facade 路由；`ConfigurationRequest.port`/`InstanceChainRequest.port` 可省略并由 `_runtime_target()` 解析；`/setup/configuration` 透传 `capability`。
- `api_material.py`：`bind_image_runtime()` 写 `installation_id/port/recipe_sha256`，同目标重绑不再递增 revision；返回 `binding_state`。
- `worker.py`：`run_configured_once()` 在安装队列之后推进一次准备步骤（不新开线程/调度器）。

前端：

- `components/SetupEnvironmentStatus.tsx`（新）：唯一事实视图。
- `components/SetupCenter.tsx`：打开即检测；`prepare()` 改为"核验 → 计划 → 交给后台"，不再由浏览器串行驱动配置/登记/核验/激活；新增 `QUEUED` 结论。
- `components/SetupPanel.tsx`：挂载唯一入口，专业排查块保留在同页下方。
- `components/DirectorSetupPanel.tsx`：env 页签改为只读摘要 +「打开设置」。
- `api.ts`：`IMAGE_RENDERER_RECIPE_MISSING` 等文案改为"现状 + 下一步"，不再让用户去"基础配置登记"。

## 4. 与测试同步更新的既存契约

这些不是"为了让测试过"而改代码，而是代码的契约变了，测试必须跟着改：

| 测试 | 旧契约 | 新契约 |
| --- | --- | --- |
| `setupCenter.test.tsx` | 挂载不检测，必须点「检测本机」 | 打开即检测；切页回来重新读服务端 |
| `mainlineV3Setup.test.tsx` C-03 | 前端拿到安装终态并显示「文件已核验」 | 前端只报「已排队，后台继续」，终态由 summary/后台给 |
| `setupCapabilityGates.test.tsx` | 复用走 `/existing-installations/verify` | 复用走 `/setup/reconcile`（能力级） |
| `directorEntries.test.tsx` | 导演页内完成检测+勾选+确认 | 导演页只读摘要，「打开设置」进入唯一入口 |
| `setupEntry.test.tsx` | 导演页有「检测本机」 | 导演页只显示同一份事实 |
| `test_material_render_ownership.py` | 绑定即可渲染 | 必须有可启动配方（否则 `PENDING_RECIPE`） |
| `test_setup_cold_start.py` | 省略端口 = 8188 | 省略端口 = 机器已解析的身份；显式端口的冲突仍 409 |

## 5. 尚未完成 / 需外部条件

| 项目 | 状态 | 原因 |
| --- | --- | --- |
| `REAL_H3_RENDER_PASS` | PENDING | 需要本机 GPU 空闲 + 用户单独授权的真实 H3 出片 |
| 真实 Qwen 小样 | PENDING | 同上（首次真实生成需单独确认） |
| `FULL_AUTOPILOT_PASS` | PENDING | 需要真实多集成片，本轮不做 |
| RTX 4070 真机 | 豁免 | 机器不在本机 |
| 外部 ComfyUI 8188 实机验证 | 部分 | 代码路径与单测覆盖；真实"别人占着 8188"的场景需要现场机器 |

## 6. 已知取舍

1. **图片能力在完全没有实例时不会自动创建 H3 受管实例**。资源（权重）可以先下载，
   但"可出图"仍需要一套 ComfyUI；后台任务的 `TARGET` 会具名报告
   `MANAGED_INSTANCE_REQUIRED`，而不是偷偷拉一整套视频节点。
2. **准备任务的 `VALIDATE` 会启动一次服务**。它在授权范围内执行，且在 `require_idle_configuration`
   保护下；有作业在跑时该步直接失败并保留已完成步骤。
3. **`IMAGE_VERIFY` 会短暂占用 GPU**（起渲染器 → 核对节点 → 释放）。无法启动时按具名错误停下，
   不会把"没验证"写成"已验证"。
