# Shine 视频工作台 R5｜基础配置与用户机资源复用 验收报告

> 日期：2026-11-18。分支：`feat/shine-setup-r5-machine-reuse-convergence-20261118`。
> 基线：`origin/main@46c889a`。方案：`docs/SHINE_SETUP_R5_THREE_PRINCIPLES_CONVERGENCE_PLAN.md`。
> 分级口径：L1 静态/单元 · L2 隔离集成 · L3 Windows 本机 · L4 真实 H3 出片 · L5 独立用户机。
> 诚实边界：本机 RTX 5080 16GB（非用户 RTX 4070）；本轮未发起真实 H3 出片；L4/L5 一律 PENDING/NOT_RUN，不冒充 PASS。

## 1. INV-01~16 逐项结果

| INV | 不变量 | 正例 | 负例 | 结果 |
| --- | --- | --- | --- | --- |
| 01 | 已核验且原位可消费的模型不因台账未登记而重下 | test_setup_machine_reuse::test_reconcile_adopts_without_project_or_preinstalled_shine_nodes；test_setup_chain_convergence::test_a_verified_search_candidate_is_reused_instead_of_downloaded | test_setup_chain_convergence::test_a_moved_verified_file_stops_counting_as_found | PASS（L2） |
| 02 | 已核验在盘未落位 ≠ 网络下载 | test_setup_missing_verdict::test_cached_bytes_owe_placement_not_download；test_setup_chain_convergence::test_manual_reconcile_reuses_the_cached_digest_without_a_full_reread（新增） | test_setup_missing_verdict::test_partial_machine_label_count_and_bytes_match_the_missing_list | PASS（L2） |
| 03 | 端口变化不清空哈希证据；无证据不认领他人进程 | test_setup_machine_reuse::test_port_change_keeps_one_installation_and_its_byte_evidence、::test_port_change_invalidates_the_seal_but_not_the_byte_evidence（新增） | test_managed_process_release / test_comfy_handoff_journal（归属三证，既有） | PASS（L2） |
| 04 | 多候选显式选定一次，之后不漂移 | test_setup_machine_reuse::test_multiple_discovered_installations_ask_once_then_remember、::test_duplicate_legacy_rows_resolve_deterministically_to_the_chosen_port、::test_same_port_different_root_is_not_a_dead_end（新增） | 同端口换 root 旧实现 409 死路已被反例覆盖 | PASS（L2） |
| 05 | 切页/重启/建作品不重复采纳/登记/哈希 | test_setup_machine_reuse::test_adding_image_evidence_preserves_h3_and_one_instance；test_setup_chain_convergence::test_manual_reconcile_reuses…（reused=True 断言） | test_existing_installation（字节变化必须重验，既有） | PASS（L2） |
| 06 | 能力互不连坐 | test_setup_capability_gates（21 例）；test_setup_machine_reuse::test_image_only_task_drops_unrelated_h3_variant | test_setup_capability_gates::CASE-07（H3 硬件门槛不挡图片下载） | PASS（L2） |
| 07 | 同一快照四端结论一致 | test_setup_missing_verdict::test_summary_verdict_and_verdict_endpoint_are_one_answer（新增）；test_resource_resolver_v2::test_variant_availability_reads_the_same_installation_selection（新增）；前端 setupSingleExecutor/machineProgress | 旧实现 availability 取第一条行已被反例覆盖 | PASS（L2） |
| 08 | 本次图缺节点准确拒绝；未选图不阻断 | test_mode_capabilities / test_service_validation（既有） | CORE_MODE_NODES_MISSING / LIVE_WORKFLOW_CONTRACT_FAILED 拒绝路径（既有） | PASS（L2） |
| 09 | 精准失效，不整体清空 | test_runtime_identity_relocation（9 例）；test_setup_machine_reuse::test_recipe_step_regenerates_when_the_endpoint_moved / …_active_variant…（新增） | test_runtime_identity_relocation::test_a_really_changed_renderer_file_still_blocks | PASS（L2） |
| 10 | 旧任务不覆盖新任务/不盲重试 | test_setup_machine_reuse::test_a_superseded_task_cannot_be_resurrected_by_a_late_worker_write（新增） | 既有安装 attempt 对账用例（NEEDS_REVIEW 不重投） | PASS（L2） |
| 11 | 外部 ComfyUI 只读 | test_existing_comfy_adoption（7 例）；test_setup_machine_reuse::test_reconcile_adopts…（前后目录树比对） | test_existing_comfy_adoption::test_adoption_still_refuses_a_managed_prior_row… | PASS（L2） |
| 12 | 候选不借基线证据 | test_variant_binding_and_gate（21 例）；test_resource_resolver_v2::test_variant_availability_decouples_preparation_from_production | 候选 available=false / production_ready=false 断言 | PASS（L2） |
| 13 | 中文/空格/大小写路径单一身份 | test_setup_machine_reuse::test_root_identity_ignores_windows_case_and_spaces（新增）、::test_windows_filename_case_does_not_turn_reuse_into_a_copy | 路径变为链接/越界仍拒绝（既有安全门） | PASS（L2） |
| 14 | 截断/不可访问只得 UNKNOWN；未知≠缺件 | test_resource_discovery::test_incomplete_coverage_reports_unknown_not_missing、::test_unreadable_area_also_downgrades_missing_to_unknown（新增）；前端 setupCenter::R5 B6（新增） | 覆盖完整时 missing 仍具名（test_report_keeps_found_and_missing_apart） | PASS（L2） |
| 15 | Mock/离线不标真实 | test_offline_e2e / fake 隔离（既有）；variant availability candidate 断言 | MOCK_AVAILABLE 不证明字节（既有） | PASS（L2） |
| 16 | 无新增隐蔽平行入口 | 前端 simpleSetup（9 例，重写）、imageCapability（3 例，收敛）断言无第二执行入口；后端整备链唯一写入口审计 | 浏览器直建计划按钮已删除（queryByRole 为 null） | PASS（L2） |

## 2. 全量回归

### 2.1 后端 8 分片（AGENTS.md 固定规则）

- 命令：`run_backend_shards.ps1 -WorkRoot <非系统盘隔离目录>`（脚本内置 `CODEBUDDY_SAFE_DELETE_ENABLED=0`、去除 NO_PROXY、按文件均分 8 片）。
- 首轮（`F:/ShineTest/r5-shards-20261118/shards-20261118-084234-852`）：274 文件 / 8 分片全部跑完，160 个模块摘要；唯一失败 `test_audit_event_codes::test_the_frontend_list_covers_every_event_code_the_backend_writes` —— 新增审计码 `ADOPTED_INSTALLATION_ENDPOINT_CHANGED` 未登记前端清单（真实回归，已在 `1fa8f55` 修复并定向复绿）。
- **最终树复跑（`F:/ShineTest/r5-shards-final-20261118/shards-20261118-091505-279`）：8/8 分片 clean，全部 exit 0，`shards_with_failure_markers=0`，160 个模块摘要（274 文件），零 FAILED/ERROR。** 脚本总 exit=0（日志 `/tmp/r5-backend-shards-final.log`）。
- 判定口径：8/8 退出码 0 且全部日志无 FAILED/ERROR。分片日志为 UTF-16（PowerShell `*>` 重定向），失败标记须转码后检查。

### 2.2 前端全量

- `npx vitest run --no-file-parallelism`：**83 文件 / 429 例全过，exit=0**（最终树日志 `/tmp/r5-frontend-final.log`；此前 `/tmp/r5-frontend-full2.log` 亦全绿）。
- `npx tsc --noEmit`：通过。
- `npm run build`（Vite）：通过（日志 `/tmp/r5-vite-build.log`）。

### 2.3 静态与迁移

- `ruff check backend/shine backend/tests`：All checks passed。
- `alembic check`：No new upgrade operations detected（本轮无 schema 变更）。
- Copilot 生成合同 `--check`：contracts ok（app=2.2.0 actions=14 knowledge=22 discovered=426）。

## 3. Windows 启动链验收（L3）

脚本：`.local-acceptance/r5-launch-20261118/accept.ps1`（Git 忽略；UTF-8 BOM，pwsh 7 执行）。
数据目录：`F:\ShineTest\R5 启动验收 20261118`（含中文与空格，非系统盘，隔离）；端口 8005/5178（非默认，启动前确认空闲）；未触碰用户日常库 `D:/Shine/data`（验收前只读确认其无活栈）。

| 步骤 | 结果 |
| --- | --- |
| 1 干净启动（start_shine.ps1 -DataDir -ApiPort -WebPort） | PASS：API/Web/Worker/supervisor(8791) 全起 |
| 2 三证存活 | PASS：/health 200、Web 200、system/status.data_dir 与隔离目录一致 |
| 3 唯一裁决一致 | PASS：summary.verdict 与 /setup/verdict 逐字段相等（空机 SETUP_REQUIRED，如实） |
| 4 空库机器级事实 | PASS：h3/image 能力 MISSING（空机如实），无作品不报错 |
| 5 停止核对 | PASS：stop_shine.py 退出 0，web/worker/api/supervisor 全停，8005/5178/8791 无监听，processes.json 消失 |

- 过程记录：首次以 PowerShell 5.1 执行无 BOM 脚本时中文路径被读成 ANSI（验收脚本编码问题，非产品缺陷）；按 AGENTS.md 编码规则加 BOM 并以 pwsh 7 复跑通过。乱码临时目录已清理（本轮自产 artifact）。
- 日志：`/tmp/r5-launch-accept2.log`（exit=0）。
- 边界：本次为 mock provider 启动链验证，未启动真实渲染器、未占用 GPU、未发起真实出片。

## 4. 真实出片与用户机（L4/L5）

- L4 REAL_RENDER：**PENDING / NOT_RUN**。本轮未发起真实 H3 出片（占用显存与时间）；就绪裁决与试镜入口沿用既有链路，未改写任何历史成片证据。
- L5 USER_MACHINE：**PENDING / NOT_RUN**。本机为 RTX 5080 16GB，无 RTX 4070 用户机；跨用户机首次/重复启动复验需在用户机执行。
- VLM/ASR 真实正负校准：历史未完成，本轮未改钉住文件，不触发新义务，亦不得声称已校准。

## 5. 已知限制与剩余用户机工作

- 历史同 root 多行（旧端口漂移遗留）按确定性偏好收敛显示，但台账保留全部历史行；如需物理清理须另行用户授权（本轮不做）。
- 外部 ComfyUI 无版本声明且服务未运行时，收编仍按既有安全门拒绝（EXISTING_COMFY_VERSION_UNVERIFIED）——不注册猜测。
- 用户机需复验：第三方整合包路径（中文/空格/外置权重 extra_model_paths）、端口被占用时的端点迁移、低显存机「权重存在≠可运行」表述、重启/升级后证据继承。