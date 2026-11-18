# 2026-11-19 「文件找到却用不了」收敛轮验收

用户报障（真机截图）：项目修复后素材出图反而又被阻滞——「① 文件已找到，待核验 /
② 跑不通 · 资源尚未绑定到本机渲染器或运行合同未核对」，顶栏深搜 10% 不动。
用户裁定：按三原则（一套事实、一个裁决、一个执行者）重新梳理，不允许再出现
「文件找到，但是又用不了」这类死胡同。

## 1. 根因（三条分裂链路，均在台账实证）

数据目录 `F:\ShineTest\整备复验 20261118 hlrl5484`（与截图同一台账）只读核对：

1. **SCAN 活锁**。整备任务停在第一步 SCAN（`剩余 1 批`），其后 RECONCILE→IMAGE_BIND→
   IMAGE_VERIFY 全部 PENDING。根因：`setup_preparation._step_scan` 旧逻辑一看到检索
   状态 DONE 就 `start_scan` 重搜全部固定盘；而 Worker 常规 tick 也在推进同一个检索
   队列（`worker.py` 的 `advance_scan`），两者交替使「完成→重启」每轮循环，SCAN 步
   永远等不到自己观察到 DONE。本机一轮深搜要走约 750 万目录（含数百个测试残留
   ComfyUI），每轮数分钟，用户看到 10% 永不动。
2. **图层证据不持久**。`materialprep/runtime.readiness(object_info=None)` 在渲染器
   停止后一律报 `IMAGE_GRAPH_NOT_VERIFIED`；受管启动写下的 `graph_verified` 只存在
   启动记录里，readiness 不读它。卡片在「刚证明过自己」的机器上回退「跑不通」。
3. **adopted 只读复用被启动口拒绝**。用户机器是「你自己的安装（只读复用）」
   （adopted+managed 行，端口 8196）。`start_image_renderer` 对端口上的活体监听一律
   `EXTERNAL_INSTANCE_ON_PORT` 拒绝——用户自己的 ComfyUI 开着时，IMAGE_VERIFY 与出图
   执行在这类机器上永远失败；产品文案却承诺只读复用。

## 2. 收敛改动（一处事实 / 一处裁决 / 一处执行者）

| 链路 | 收敛 |
| --- | --- |
| 检索 | `start_scan(reuse_completed=True)` 复用已完成覆盖（检索复用），显式重检索入口语义不变；每轮检索带 `round_id`。`_step_scan` 的完成裁决唯一：本任务发起的轮次 DONE 即 DONE，**完成的轮次是终态，绝不重启**；排队中的轮次也记录身份。 |
| 图层 | 新增唯一写入点 `runtime.record_graph_verification`（受管启动、IMAGE_VERIFY、image-readiness 活体探测三处调用同一函数），证据按运行时指纹键控存 `image-graph-verification`；`readiness` 无活体时读同一份持久证据，换绑/改模型/改模板指纹变化即如实失效。 |
| 渲染器 | 新增唯一裁决 `image_renderer.adopted_live_renderer`：adopted 且非 mock、登记端口被环回活体 ComfyUI 服务、且无本工作台 RUNNING 受管记录 → 该监听就是这套安装的渲染器，**只读复用，不启动不停止**；`start_image_renderer` 在外部端口拒绝前先过这一裁决。非 adopted 实例绝不复用外部监听。GPU 忙 / H3 占用 / 另有启动者在途 → 具名 PENDING 等待，不是失败。 |
| 投影 | `_two_step_projection(preparing=…)`：整备任务活跃且覆盖该能力时，② 投影为「后台整备中，完成后自动可用」（PREPARING），不再显示「跑不通」死胡同与只会重排同一任务的按钮；事实仍读同一份整备台账。前端对应圆点样式、隐藏按钮。 |

改动文件：`comfyops/resource_discovery.py`、`comfyops/setup_preparation.py`、
`comfyops/image_renderer.py`、`comfyops/setup_summary.py`、`materialprep/runtime.py`、
`frontend/src/components/SetupEnvironmentStatus.tsx`、`frontend/src/styles.css`。
**未改** `worker.py` / `preproduction.py` / handoff / quality 客户端，不触发
`client_review_identity` 重校准义务。

## 3. 回归

- 新增 `backend/tests/test_image_found_runnable_convergence.py` 9 例：SCAN 完成即
  DONE 不重启、新整备复用覆盖、自身推进完成、图层证据停止后仍在且换绑失效、
  adopted 活体只读复用（不写受管记录）、非 adopted 不复用外部监听、IMAGE_VERIFY
  复用并持久证据且不停用户渲染器、GPU 忙等待、summary 整备中投影 PREPARING。全绿。
- 定向批 1（整备/检索/投影）：`test_setup_preparation_chain`、`test_resource_discovery`、
  `test_setup_chain_convergence`、`test_setup_missing_verdict`、`test_worker_batch_priority`、
  `test_setup_machine_reuse` —— 86 例全绿。
- 定向批 2（出图渲染器/素材执行）：`test_image_managed_renderer`、
  `test_image_renderer_crash_evidence`、`test_material_render_ownership`、
  `test_material_render_e2e`、`test_material_image_jobs`、`test_setup_one_stop_v41`
  —— 65 例全绿。
- 前端定向 4 文件 20 例（含新增 `setupPreparingProjection.test.tsx` 2 例）全绿；
  全量 `vitest run --no-file-parallelism` 84 文件 / 435 例全绿；`tsc --noEmit`、
  `npm run build` 干净。
- `ruff check` 改动文件干净；Copilot 合同 `--check` 干净（本轮未改路由/声明）。
- 后端全量 8 分片：见 §5 补记。

## 4. 真机复验（同一台账，真实启动链）

1. `stop_shine.py --data-dir …` 退出码 0（含 supervisor 收尾）→ `start_shine.ps1`
   重启加载新代码；`/health` schema `f180f0010006` 无 drift。
2. 存量整备任务在新 Worker 下从 SCAN 续跑，**全部步骤 DONE**：
   SCAN（复用覆盖）→ TOOL_REPAIR SKIPPED → RECONCILE `image:RECONCILED` →
   DOWNLOAD 无需下载 → TARGET（8196）→ RECIPE 沿用 → IMAGE_BIND 对齐 →
   IMAGE_VERIFY「出图节点已核对（端口 8196），渲染器已释放显存」→
   IMAGE_SAMPLES 三类真实小样生成 → IMAGE_EDIT_SAMPLES 五模式
   （I2I/INPAINT/MULTIREF/WARDROBE/RGBA）全部真实验证。
3. 图层持久证据落库：fingerprint `ede62ef0b704…`，source `managed_startup`，
   `complete=true`；停止后 `readiness(object_info=None)` 层 `GRAPH_VERIFIED`、
   binding `READY`、blockers `[]`。
4. 生产门事实：当前指纹下 `render_verified_families` = character/scene/prop 三类
   各有真实 attempt 证据（`b383f698…` / `7b0762fe…` / `520e1e6d…`）。
5. 真实浏览器 `#d/setup`：顶栏 100% 绿「整备完成 · 真实制作已就绪」；素材出图卡
   「已真实验证」，① 文件已就绪并核验 ② 已验证可运行；H3 卡已就绪；可选增强
   （画面审片/语音核对）仍如实报缺件与下载入口，不冒充就绪。
6. 复验时用户自己的 ComfyUI 未监听，真机走的是**受管启动分支**；8196 现存监听者
   pid 2816 与受管启动记录一致（工作台自有，可「关闭本机后台并释放显存」一键释放，
   H3 开拍时自动让位）。**adopted 活体只读复用分支**由单元回归覆盖（含「不写受管
   记录、不停用户渲染器、非 adopted 不复用」三条反例）。

## 5. 边界与补记

- 历史产物、旧风格、旧交付不重写；本轮只收敛整备/投影/复用裁决。
- 可选审片能力未下载仍报「未准备」，是诚实状态，不是阻滞（不占机器级下一步）。
- 后端全量 8 分片（`run_backend_shards.ps1 -WorkRoot F:\ShineTest`，按文件均分）：
  8/8 退出码 0，日志零 `FAILED`/`ERROR` 行，合计 2367 例
  （312/339/299/275/296/303/215/328）。跑完脚本自动删除各分片 basetemp；
  分片日志目录在本轮收尾时按用户授权清理，数字以本记录为准。
