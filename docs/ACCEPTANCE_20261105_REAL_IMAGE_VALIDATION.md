# 2026-11-05 受管路径真实三类小样出图 · 验收记录

- 机器：Windows 11 / RTX 5080（16303 MiB 总量）
- 既有 ComfyUI：`F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI`（含 `custom_nodes/shine_comfy_handoff`，Qwen-Image-2.1 三权重已逐字节校验在位）
- 工作台：本仓库代码，API `127.0.0.1:8133` / Web `5193`，数据目录 `.local-acceptance/live-browser`，
  启动环境 `SHINE_COMFY_PROBE_ROOTS=["F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI"]`、
  `SHINE_COMFY_PROBE_PORTS=[8188]`、`SHINE_ENABLE_LOCAL_EXECUTION=1`
- 驱动：`.local-acceptance/real_image_validation.py`（未纳入 Git；`.local-acceptance/` 被忽略），
  每步打印请求/响应，日志 `.local-acceptance/real_image_validation_run2_20261105.log`。
  写操作统一带 `x-shine-local-action: 1`（个人桌面同源动作标记）。
- 结论：**REAL_QWEN_VERIFIED（角色/场景/道具三类）**——工作台自己拥有、且本次真的启动的受管渲染器
  真实出图，`renderer_owned=true`，技术质检 pass；语义/美术仍待人工评审（见"边界"）。

## 全链 13 步实测

| # | 步骤 | 实测结果 |
| --- | --- | --- |
| 1 | `GET /comfyops/execution-context` | 200；`configuration_hash=d401d7aa…`（签发授权**前**读取） |
| 2 | `POST /preparation/approval-grants`（scope_configure，带 context） | 200，grant 下发 |
| 3 | `POST /comfyops/scope`（roots/ports/previous_hash/grant/snapshot） | 200，白名单与本机管理员白名单一致 |
| 4 | `POST /setup/existing-installations/inspect` | 200；snapshot `94c8ba13…`；软件版本 `INSTALLED_VERSION_MATCH`（rev `b0b7435…`） |
| 5 | `POST /setup/existing-installations/verify` | 200，`INSTALLED_VERIFIED`；逐字节核验复用既有审计证据（同 root+snapshot），H3 侧 43,974,265,807 字节核验在案 |
| 6 | `POST /setup/existing-installations/adopt` | 首次 409 `INSTANCE_IDENTITY_CHANGED`（**缺陷，已修复**，见下）；修复后 200 `ADOPTED`，`replaced_probe_row=true`，受管实例 `22ea7c4e-316c-5ff3-a4aa-632186c81a5e` |
| 7 | `POST /setup/configuration` | 200；受管配方 `comfyops/setup/managed_recipe.json`（sha `4cda62bb…`），`REUSED_EXISTING` 复用既有适配器，GPU `GPU-f3fda412…` |
| 8 | `POST /setup/image-runtime` | 200，config_revision 2；就绪层 `FILES_VERIFIED`（三权重 actual=expected，逐文件 SHA-256） |
| 9 | `POST /setup/image-runtime/start` | 200 `RUNNING`；受管进程 pid 30636 / birth `134383285224270671` / 命名 Job Object `Local\ShineHandoff-0ab92a75…`；队列先关后放行，显存内存实测准入 |
| 10 | `GET /setup/image-readiness` | **`GRAPH_VERIFIED`**（活体 `object_info` 校验：输出节点 461/SaveImage、缺失节点/输入为空） |
| 11 | `POST /setup/image-runtime/validate`（需已批准 asset 权利记录） | 202；run `8289c72e-fbd7-496d-8624-0bb808ecf858`，mode=real，三类固定小样 |
| 12 | Worker 渲染 | run **COMPLETED**，counts 3/3 candidate_ready、0 failed；见下表逐镜证据 |
| 13 | `POST /setup/image-runtime/stop` | 200 `STOPPED`（`active_processes=0`，total=5）；GPU 从渲染占用回落到基线（nvidia-smi 1738/16303 MiB），无孤儿渲染器 |

## 三类小样逐镜证据（台账为准）

| 族 | item | seed | attempt（renderer_owned=true） | 外部 prompt_id | 内容 SHA-256 | 字节 | 候选 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| character | `23740e2f…` | 1000 | `2eb0923a…` | `99689e17…` | `8fc227e5…32e34` | 1,576,560 | `236df4c3…` READY_FOR_REVIEW |
| prop | `378e3cf2…` | 1001 | `638051ce…` | `b461059e…` | `5c08bd6c…ef1d` | 1,160,721 | `1354c79c…` READY_FOR_REVIEW |
| scene | `9c28c340…` | 1002 | `e182f032…` | `43e10096…` | `e17d3ce8…1d0a5` | 1,338,911 | `8e1838d5…` READY_FOR_REVIEW |

- 每个 attempt：提交意图先落库（`submit_intent_at` 先于 `/prompt`），外部 prompt id 立即落库，
  独立 GPU 租约（`6e414055…` / `e2f5be27…` / `79070afe…`），只取声明输出节点（461/SaveImage），
  字节+SHA-256 校验入库，`is_mock=false`。
- QC：`technical=pass`、`decode=pass`（每帧解码无错）、`semantic=PENDING_HUMAN_REVIEW`（如实挂起）。
- 三类就绪层：**RENDER_VERIFIED（第四层）**，`can_generate=true`，证据 attempt id 即上表。
- **第四层的展示绑定活体图验证**：渲染器停止后 `image-readiness` 如实回落到 `FILES_VERIFIED`
  （图未活体验证、当下也不能出图）；台账证据不丢——复证一轮 start → readiness → stop：
  重启后三类**立即回显 `RENDER_VERIFIED`**（指纹与 attempt 记录一致），再停止又是
  `active_processes=0`、GPU 2127/16303 MiB。第二轮 start/stop 同时证明受管生命周期可重复。
- 图片文件为真 PNG（1024×1024 RGBA）；角色小样另做了一次视觉核验（写实半身定妆照、无崩坏），
  采样件留在 `.local-acceptance/evidence_{character,scene,prop}_sample.png`（不入库）。

## 本轮修复的缺陷：探测行挡死收编（P0）

- 现象：上一轮"真实检测"在数据目录留下 `local:http://127.0.0.1:8188` 的**只读探测实例行**；
  `adopt` 以确定性身份查到 prior 行非 adopted 即 409 `INSTANCE_IDENTITY_CHANGED`。
  真实用户"基础配置 → 检测 → 收编"同样会被挡死——检测本来就是收编的前一步。
- 修复（提交 `abf8600`）：未收编、非受管、非 mock 的探测行由**首次收编接管**
  （响应与审计记 `replaced_probe_row=true`）；已收编但快照/根不符、或 prior 行是受管行时仍然拒绝。
- 回归：`backend/tests/test_existing_comfy_adoption.py` 新增 2 条（接管 + 受管 prior 仍拒），
  该文件 7 条全绿。

## 浏览器 E2E 闸门转绿（P1，提交 `1778ef0`）

`scripts/verify_v13_web.py` 最后一段按当前界面重写（reload 后重开折叠区、按显示名选卡、
crew/settings 面板按 pro/setup 视图导航、风格冻结 actor 断言 `local-user`、
思考深度按中文标签断言、删除与统一弹窗策略竞争的冗余 once 处理函数）。
最终连续两轮输出 `MOCK_VERIFIED`、`api_errors: []`、EXIT=0，弹窗噪声消失。

## 全量回归（P2）

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 全量后端 | `uv run --project backend --locked pytest -c backend/pyproject.toml backend/tests` | **EXIT=0**，无 FAILED/ERROR（日志 `.local-acceptance/full_round_20261105.log`、退出码 `.exit.txt`） |
| Ruff | `ruff check backend/shine backend/alembic backend/tests scripts/verify_v13_web.py` | All checks passed |
| Alembic | 隔离数据目录 `upgrade head` + `check` | head `f170a0010001`；No new upgrade operations detected（本轮无新迁移） |
| 前端 | `npm.cmd test` / `npm.cmd run build`（frontend/） | 18 文件 / 130 测试通过；build 成功 |
| 浏览器闸门 | `scripts/verify_v13_web.py` | `MOCK_VERIFIED`，EXIT=0 ×2 |

## 边界（未做/不能自动做的）

- **HUMAN_REVIEW**：候选仍是 `READY_FOR_REVIEW`——构图、美术与"像不像角色"只能由用户评审，
  服务器不替用户下结论；语义 QC 状态如实为 `PENDING_HUMAN_REVIEW`。
- **REAL_H3_VERIFIED**：未做真实 H3 引用新素材（需要用户先晋升候选为主素材）。
- 研究用途边界：Qwen RESEARCH LICENSE（非商业研究/评估）；验证批次权利记录
  `LOCAL_RESEARCH_AND_EVALUATION_ONLY`。晋升不产生任何商业授权。
- 验证批次快样为固定 brief + 固定种子；正式素材批次仍走出品人 brief 链（本轮未动）。
