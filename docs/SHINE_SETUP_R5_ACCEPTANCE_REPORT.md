# Shine 视频工作台 R5｜基础配置、资源复用、管家收敛与升级全链路 验收报告

> 日期：2026-11-18。分支：`main`（基于 `5246b60` 的待提交工作树收尾交付）。
> 方案：`docs/SHINE_SETUP_R5_THREE_PRINCIPLES_CONVERGENCE_PLAN.md` · 实施记录：`docs/SHINE_SETUP_R5_IMPLEMENTATION_REPORT.md`。
> 分级口径：L1 静态/单元 · L2 隔离集成 · L3 Windows 本机与外部 ComfyUI 复用 · L4 真实 H3 出片 · L5 独立用户机。
> 诚实边界：本机为 RTX 5080 16GB（非用户 RTX 4070）；本轮未发起真实 H3 出片；L4/L5 一律如实记为 `PENDING / NOT_RUN`，后端 8 分片全量在当前待提交工作树单列待验。

## 1. INV-01~16 逐项结果（含 17 个首轮反例 + 7 个跨入口收尾反例 + UI/管家收敛用例）

| INV | 不变量 | 正例 | 负例 / 反例 | 结果 |
| --- | --- | --- | --- | --- |
| 01 | 已核验且原位可消费的模型不因台账未登记而重下 | `test_setup_machine_reuse::test_reconcile_adopts_without_project_or_preinstalled_shine_nodes`；`test_setup_chain_convergence::test_a_verified_search_candidate_is_reused_instead_of_downloaded`；L3 外部 ComfyUI `prepare-plan download_bytes=0` | `test_setup_chain_convergence::test_a_moved_verified_file_stops_counting_as_found` | PASS（L2+L3） |
| 02 | 已核验在盘未落位 ≠ 网络下载；预览与执行同一套下载计划 | `test_setup_missing_verdict::test_cached_bytes_owe_placement_not_download`、`::test_prepare_plan_and_download_step_share_one_planner_without_downloading_unknown_or_unplaced` | `test_setup_missing_verdict::test_partial_machine_label_count_and_bytes_match_the_missing_list` | PASS（L2+L3） |
| 03 | 端口变化不清空哈希证据；无证据不认领他人进程 | `test_setup_machine_reuse::test_port_change_keeps_one_installation_and_its_byte_evidence`、`::test_port_change_invalidates_the_seal_but_not_the_byte_evidence` | `test_setup_machine_reuse::test_generated_adopted_recipe_passes_normal_h3_startup_binding`（端口漂移 409）；L3 端口占用 `409 PORT_OCCUPIED` | PASS（L2+L3） |
| 04 | 多候选显式选定一次，之后不漂移；跨入口安装绑定唯一裁决 | `test_setup_machine_reuse::test_multiple_discovered_installations_ask_once_then_remember`、`::test_same_port_different_root_is_not_a_dead_end`、`::test_generated_adopted_recipe_passes_normal_h3_startup_binding` | 同端口换 root 旧 409 死路与收编配方启动误报 `COMFY_INSTALLATION_BINDING_CHANGED` 已由反例锁定 | PASS（L2+L3） |
| 05 | 切页/重启/建作品/追加出图证据不重复哈希、不打断 H3 配方 | `test_setup_machine_reuse::test_legacy_recipe_stays_valid_after_image_adoption`、`::test_h3_only_recipe_refresh_keeps_existing_image_binding_aligned`；L3 重启后 `third_reused=True, copied=0` | `test_setup_machine_reuse::test_legacy_recipe_stays_valid_after_image_adoption`（端点真变仍 409 失效） | PASS（L2+L3） |
| 06 | 能力互不连坐；勾选清单默认全空由用户显式指定 | `test_setup_capability_gates`（23 例）；`test_two_step_and_edit_validation`（`relayvsr` 落位核验后就绪不强制试镜）；前端 `setupCenter.test.tsx`（默认全不勾选） | `test_setup_capability_gates::CASE-07`（H3 硬件门槛不挡图片下载） | PASS（L2） |
| 07 | 同一快照多端结论一致；无重复状态卡片 | `test_setup_missing_verdict::test_summary_verdict_and_verdict_endpoint_are_one_answer`；`test_resource_resolver_v2::test_variant_availability_reads_the_same_installation_selection`；前端删除重复底栏 `setup-image-test` | 旧实现 `availability` 取首行及底部「真实试镜一次」误调 `prepare/execute` 已被前端单测锁定 | PASS（L2+L3） |
| 08 | 本次图缺节点准确拒绝；历史附加节点封条兼容 | `test_mode_capabilities`（含 `sealed_mode_nodes_compatible` 兼容历史 `VHS_LoadAudioUpload` 封条）；`test_service_validation`（10 例） | `CORE_MODE_NODES_MISSING` / `LIVE_WORKFLOW_CONTRACT_FAILED` 拒绝路径 | PASS（L2） |
| 09 | 精准失效，不整体清空 | `test_runtime_identity_relocation`（9 例）；`test_setup_machine_reuse::test_recipe_step_regenerates_when_the_endpoint_moved` | `test_runtime_identity_relocation::test_a_really_changed_renderer_file_still_blocks` | PASS（L2） |
| 10 | 旧任务/越界进程不覆盖新任务；Supervisor 仅凭有效委托完成核验 | `test_supervisor_lifecycle::test_supervisor_completes_validation_after_worker_saves_pending_delegation`；`test_setup_machine_reuse::test_a_superseded_task_cannot_be_resurrected_by_a_late_worker_write` | `test_supervisor_lifecycle`（任务取消、候选漂移、新 Worker 认领一律 409 拒绝回写） | PASS（L2） |
| 11 | 外部 ComfyUI 严格只读 | `test_existing_comfy_adoption`（8 例）；L3 实测 `F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI` 前后 `21,829` 文件 / `641,611,663,123` 字节 / 最大 `mtime_ns` 完全一致 | `test_existing_comfy_adoption::test_adoption_still_refuses_a_managed_prior_row` | PASS（L2+L3） |
| 12 | 候选不借基线证据 | `test_variant_binding_and_gate`（23 例）；`test_resource_resolver_v2::test_variant_availability_decouples_preparation_from_production` | 候选 `available=false` / `production_ready=false` 断言 | PASS（L2） |
| 13 | 中文/空格/大小写路径单一身份 | `test_setup_machine_reuse::test_root_identity_ignores_windows_case_and_spaces`；L3 数据目录 `F:\ShineTest\R5 L3 验收 20261118` | 路径变为符号链接/越界仍拒绝 | PASS（L2+L3） |
| 14 | 截断/不可访问只得 `UNKNOWN`；未知绝不进下载计划 | `test_setup_missing_verdict::test_prepare_plan_and_download_step_share_one_planner_without_downloading_unknown_or_unplaced`；前端 `setupCenter::R5 B6` | 扫描完整时真实缺件仍具名计入（空机 H3 / 独立出图正常下载） | PASS（L2） |
| 15 | Mock/离线不标真实；管家问答如实引用权威规格与模型路由 | `test_offline_e2e`（2 例）；`test_support_agent`（58 例，含 Ref2VA `1～9` 张参考图问答与 `support_agent` LLM 调用）；前端 `shineCopilot.test.tsx` | 离线模式诚实标注，不伪造已校准或真实成片 | PASS（L2） |
| 16 | 唯一整备执行者；AI 管家与历史 `/setup/reconcile` 全部收束到既有 Worker | `test_support_agent::test_copilot_and_legacy_reconcile_queue_only_reconcile_onto_worker`；`test_support_copilot_v2::test_setup_and_materials_intents_route_without_stealing_episode_prep`；L3 实测 `DELEGATED -> SUCCEEDED` | 同步改库旧路径与 `adopt=True` 误串联 `TOOL_REPAIR/DOWNLOAD/RECIPE` 已被反例锁定 | PASS（L2+L3） |

## 2. 回归与构建验证

### 2.1 后端专项、跨入口反例、AI 管家与离线 E2E（当前待提交工作树已验）

- 命令（含 `CODEBUDDY_SAFE_DELETE_ENABLED=0` 与非系统盘隔离 `--basetemp`，通过后自动清理）：
  `backend\.venv\Scripts\python.exe -m pytest .local-acceptance\r5-independent-20261118\test_r5_closeout_integration.py .local-acceptance\r5-independent-20261118\test_r5_counterexamples.py backend\tests\test_setup_machine_reuse.py backend\tests\test_supervisor_lifecycle.py backend\tests\test_mode_capabilities.py backend\tests\test_service_validation.py backend\tests\test_setup_missing_verdict.py backend\tests\test_setup_capability_gates.py backend\tests\test_setup_chain_convergence.py backend\tests\test_setup_one_stop_v41.py backend\tests\test_setup_preparation_chain.py backend\tests\test_setup_preparation_instance.py backend\tests\test_two_step_and_edit_validation.py backend\tests\test_existing_comfy_adoption.py backend\tests\test_support_agent.py backend\tests\test_support_copilot_v2.py backend\tests\test_support_copilot_v21.py backend\tests\test_support_copilot_v3.py backend\tests\test_managed_run_binding.py backend\tests\test_managed_startup.py backend\tests\test_runtime_activation.py backend\tests\test_runtime_identity_relocation.py backend\tests\test_resource_resolver_v2.py backend\tests\test_audit_event_codes.py backend\tests\test_installer_redirects.py backend\tests\test_variant_binding_and_gate.py backend\tests\test_package_entrypoints.py backend\tests\test_alembic.py backend\tests\test_v13_migrations.py backend\tests\test_offline_e2e.py -q -p no:cacheprovider`
- 结果：**30 个测试模块 / 524 用例（`523 passed, 1 skipped, 0 failed`），exit=0**；其中独立反例套件 `test_r5_counterexamples.py`（17/17）与跨入口收尾套件 `test_r5_closeout_integration.py`（7/7）合计 **24/24 全绿**，并已固化进正式 `backend/tests/test_*.py` 模块。

### 2.2 后端 8 分片全量回归（单列待验）

- 状态：**PENDING（单列待验）**。
- 说明：基线提交 `5246b60` 前一轮曾跑过 8 分片（274 文件 8/8 exit=0），但当前 `5246b60` 之上的收尾待提交工作树按本轮指令先交付待提交差异并单列此项，尚未在最终工作树重跑完整 8 分片。合并/发包前请执行：
  `.\run_backend_shards.ps1 -WorkRoot F:\ShineTest\r5-shards`（遵循 `AGENTS.md` 8 分片均分与自动清理规则）。

### 2.3 前端全量与生产构建（当前待提交工作树已验）

- `npx tsc --noEmit`：**PASS（0 errors，exit=0）**。
- `npx vitest run --no-file-parallelism`：**83 文件 / 431 用例全过，exit=0**（新增覆盖 `#setup` 默认不预勾选、移除底部误导试镜按钮与重复出图条、AI 管家悬浮窗拖拽调宽高持久化、模型选择器同步等）。
- `npm run build`（Vite）：**PASS（exit=0）**。

### 2.4 静态、合同、迁移与锁文件检查（当前待提交工作树已验）

- `backend\.venv\Scripts\ruff.exe check backend scripts\shine_supervisor.py`：**All checks passed（exit=0）**。
- `backend\.venv\Scripts\python.exe backend\scripts\generate_shine_copilot_contracts.py --check`：**`contracts ok: app=2.2.0 actions=14 knowledge=22 discovered=427`（exit=0）**。
- `alembic check`：**`No new upgrade operations detected`（exit=0，无 schema 漂移）**。
- `uv --no-config sync --project backend --locked --dry-run`：**`Would make no changes`（exit=0）**。
- 入口脚本编码门禁：`test_package_entrypoints.py` 全过（`.cmd` 纯 ASCII，含中文 `scripts/*.ps1` 保持 UTF-8 BOM）。

## 3. Windows 本机启动链与外部 ComfyUI 只读复用验收（L3 — PASS）

- 脚本：`.local-acceptance/r5-independent-20261118/l3_real_comfy_accept.py`（Git 忽略）。
- 隔离环境：数据目录 `F:\ShineTest\R5 L3 验收 20261118`（含中文与空格，非系统盘），端口 `API=8005 / Web=5178 / Supervisor=8795`；全程未触碰本机正在运行的日常栈（`D:\Shine\data`，`8000 / 5173 / 8790`）。
- 外部真实 ComfyUI：`F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI`（只读挂载核对）。

| 验收项 | 实测证据 | 结果 |
| --- | --- | --- |
| 1. 隔离整栈启动与三证存活 | `start_shine.ps1` 拉起 `8005/5178/8795`；`/health=200`、Web `200`、`/api/v1/system/status.data_dir` 精确等于隔离目录 | PASS |
| 2. 唯一裁决一致性 | `/api/v1/setup/summary.verdict` 与 `/api/v1/setup/verdict` 五字段逐一相等（初始 `SETUP_REQUIRED / REAL_RUNTIME_NOT_ACTIVATED`） | PASS |
| 3. 外部 ComfyUI 识别与零副作用读取 | 识别 `INSTALLED_VERSION_MODIFIED`、外置解释器 `python_embeded\python.exe`、匹配 `10` 个模型文件（`43,978,717,531` 字节，含 `minimax-h3-*`、`qwen3-asr-0.6b`、`qwen3-vl-8b-fp8`）；`GET /summary` 零 `.receipts` 写入 | PASS |
| 4. 原位字节核验与回执复用 | 首次 `verify`：`verified_files=10, rejected=0, copied_bytes=0`；二次 `verify`：`copied_bytes=0, verification_reused=True` | PASS |
| 5. AI 管家与旧接口收敛到整备 Worker | 管家 `setup_reconcile` 审批后 `DELEGATED -> SUCCEEDED (outcome=VERIFIED)`；旧 `POST /api/v1/setup/reconcile` 返回 `QUEUED, steps=['RECONCILE']` | PASS |
| 6. 零重复下载裁决 | `POST /api/v1/setup/prepare-plan`（`capabilities=["h3"]`）返回 `download_bytes=0, missing_components=[]` | PASS |
| 7. 整栈停止与重启持久化 | `stop_shine.py` 全停 `8005/5178/8795` 并清 `processes.json`；重启后保留选定 `root` 与端口 `8299`，三次 `verify` 仍 `verification_reused=True, copied_bytes=0` | PASS |
| 8. 端口冲突硬门禁 | 在 `127.0.0.1:8298` 绑定真实外部监听后请求 `adopt(port=8298)`，返回 `409 PORT_OCCUPIED` | PASS |
| 9. 外部 ComfyUI 只读零修改与收尾清理 | 前后快照均为 `files=21829, bytes=641611663123, max_mtime_ns=1794910575470412900`；`8790` 宿主栈无扰动；隔离目录已完整清理 | PASS |

## 4. 真实出片、独立用户机与审片身份披露（L4 / L5 / VLM）

- **L4 REAL_RENDER：`PENDING / NOT_RUN`**。本轮未在 GPU 上跑真实 H3 视频生成、素材真实出图或 RelayVSR 后期超分，不声称真实出片已验收。
- **L5 USER_MACHINE：`PENDING / NOT_RUN`**。本机为开发机 RTX 5080 16GB，尚未在独立 RTX 4070 用户机执行拉取升级与真实出片复验。
- **`client_review_identity` 变更披露（必读）**：
  - 本轮为统一 H3 启动/续航与配方的安装绑定裁决及逐图节点校验，修改了钉住文件 `backend/shine/comfyops/handoff_startup.py`。
  - 当前 `review_client_identity()` 哈希已更新为 `c60549f335b3b32559b9af07549269b8b979421cef07dd2954e652418637740e`。
  - 按 `AGENTS.md` 设计约束，旧 VLM 校准修订自动失效：如需正式启用受引导 VLM 审片修订，须重跑真实正/负向校准调用、发布新 VLM 修订并重新分析/冻结前置方案（`advisory_model_reviews` 始终不阻塞受管渲染与成片交付）。

## 5. 旧版本 `git pull` 升级全链路贯通核查结论

1. **入口与环境升级**：`一键安装并启动.cmd` / `start_shine.ps1` 按锁文件指纹同步依赖、自动执行 `alembic upgrade head`（本轮无新表迁移，旧库零摩擦），保留 `.env` 与 `D:\Shine\data` 既有配置。
2. **基础配置整备（`#setup`）**：历史整行哈希配方、新 `runtime_binding_sha256` 配方与携带 `VHS_LoadAudioUpload` 的旧 v2 封条全部兼容保留；能力勾选框默认全空，不再隐式重跑未勾选模块；移除误触重跑整备的「真实试镜一次」按钮与重复出图条。
3. **整备 → 素材出图贯通**：`_step_recipe` 在同一安装已绑定 `image-runtime.json` 时自动复用 `_step_image_bind` 同步新 `recipe_sha256` 与端口，杜绝升级后仅刷新 H3 导致素材出图报 `PENDING_RECIPE`。
4. **素材出图 → 导演包 → 真实出片贯通**：出图空闲渲染器在 H3 启动前由既有 `retire_idle_image_renderer` 自动退场让出端口/显存；H3 首次启动与多镜头系列续航共用 `installation_binding_matches` 唯一裁决，不再因同安装追加出图证据而误报 `409 COMFY_INSTALLATION_BINDING_CHANGED`。
5. **AI 管家识别、问答、修复与代操作贯通**：模型路由页与管家设置页读写同一条 `AgentRoute(role="support_agent")` 并即时生效；问答注入权威 Ref2VA 能力规格并直答用户问题；自然语言「准备制作环境 / 核验已有文件 / 准备素材 / 把第一集准备好」准确分流至对应只读计划与既有后台执行者。
