# 新机部署测试记录（2026-10-31/11-01）

> 最新修复与未完成项见 [2026-11-01 修复记录](NEW_MACHINE_DEPLOYMENT_FIX_20261101.md)。完整新机目标仍为 PARTIAL；以下保留原建设轮次记录，不作为最新验收通过声明。

被测代码：`78c622c`（基线）+ 本轮工作树（分支 `codex/new-machine-deployment`）。
测试使用隔离临时目录、独立数据库与空闲端口；未启动真实 ComfyUI，未下载大模型权重，
未触碰生产数据库/素材/模型/已有 ComfyUI 实例。

## A. 新机部署专项（新增）

| # | 测试 | 结果 | 说明 |
|---|---|---|---|
| 1 | test_new_machine_setup.py::test_catalog_is_valid_contract_data | passed | 提交的目录作为合同数据全量校验 |
| 2 | ::test_catalog_rejects_untrusted_url | passed | 非白名单主机 409 UNTRUSTED_CATALOG_URL |
|  | ::test_catalog_rejects_unsafe_relative_path | passed | 越界路径 409 UNSAFE_CATALOG_PATH |
| 3 | ::test_install_plan_via_setup_endpoint | 批准链（grant→approve→execute）经 executor 端口注入验证，FILES_VERIFIED | 不真下载 |
| 4 | ::test_install_plan_requires_real_disk_budget | passed | 磁盘预算不足 409 |
| 5 | ::test_component_content_change_is_detected | passed | 同名异图 409 CATALOG_COMPONENT_CHANGED |
| 下载中断/续传/缓存 | 既有 ControlledDownloader 测试族（断点续传/206 Content-Range 严格比对/缓存命中）+ 本轮按起源重定向策略加强 | passed | |
| 6 | ::test_next_instance_plan_state_machine | passed | CREATE→INSTALL_NODE→BIND_MODELS 状态机与五家族绑定 |
| 7 | ::test_configuration_generation_and_recipe_validation | passed | 配方经 Worker 侧 load_managed_recipe 验证；adapter 身份/GPU UUID/.env/manifest 断言 |
| 8 | ::test_readiness_never_claims_real_inference | passed | L5 恒 NOT_VERIFIED，Mock/Fake 不能点亮 |
| 9 | ::test_open_probes_require_no_operator | passed | 三个只读探针无需操作员 |
| 10 | ::test_environment_probe_reports_this_host | passed | 真实本机探测 |
| B | test_bootstrap_entry.py（3 项：本地 zip 安装→shim 可执行；哈希不符阻断；清单=钉点） | passed | file:// + 真实哈希 |
| C | test_installer_redirects.py 45 项（含新增按起源重定向策略测试） | passed | 起源↔CDN 操作者匹配强于旧合同 |

专项小计：**15 项新增/更新测试全绿（14 新增 + 1 按新合同重写）**。

## B. 回归门禁（截至本记录）

| 检查 | 结果 |
|---|---|
| 定向回归（delivery/v13 三件套/offline e2e/film crew） | 114 passed |
| 全量后端套件（第一次，与源码编辑并发） | 1193 total, 14F+40E —— 判定为并发编辑伪影，重跑 |
| 全量后端套件（第二次，稳定状态） | **1193 total, 1F**（唯一失败=起源策略旧合同测试，按新合同重写） |
| 全量后端套件（第三次，合同测试更新后） | **1194 passed / 0 failed / 0 errors / 1 skipped**（唯一 skip = 既有 SHINE_REAL_COMFY_PROBE 门控的真实 ComfyUI 探测，如实 SKIPPED） |
| Ruff（backend/shine、backend/alembic、backend/tests、scripts） | All checks passed |
| Alembic 新库 upgrade head | PASS，45 表，head=f140a0030001 |
| 前端 tsc+vite build | PASS（SetupPanel 入 335.43 kB bundle） |
| bootstrap.ps1 端到端（本地 zip→哈希→shim→执行） | PASS（手动 + pytest 双覆盖） |
| uv sync --project backend --locked（进程内 UV_DEFAULT_INDEX=PyPI） | PASS（Resolved 42 / Checked 41） |
| verify_local_stack.ps1 -ApiPort 18123 -KeepData | PASS（Mock 全链，从不生成视频） |
| verify_stack_operations.ps1 -ApiPort 18128 -WebPort 15188 | PASS（代理/NoWorker/重复启动拒绝/Worker 重启/验证式关停） |
| npm ci --prefix frontend --ignore-scripts | PASS（0 vulnerabilities） |
| git diff --check | PASS |

## C. 本机实测事实（2026-10-31）
- 6 个 GitHub 归档（~37MB）真实下载并计算 SHA-256；HF 非 LFS 小文件 20 个真实下载核验哈希。
- host_env 本机探测：uv 0.11.2 / node 24.14.1 / pwsh 7.6.5 / ffmpeg 8.1 / npm 11.14.1 全达标；
  RTX 5080（16303 MiB）+ 驱动 621.01；RAM 32418MB；磁盘 466GB 可用；8188 端口空闲 → **零阻断**。
- 更正：旧 bootstrap.ps1 -CheckOnly 的“6 工具全缺失”是探测嵌套数组错误，不是空白机场景证据；2026-11-01 已修复。
- 收编节点 `shine_h3_sampling_mode.py` 与操作员原件代码体逐字节一致（差异仅头部注释）。

## D. 判定边界

最新持久激活回归：**1259 passed / 1 skipped / 2 warnings，exit=0**，命令 `uv run --project backend --locked pytest backend/tests -q`，日志 `.local-acceptance/new-machine-goal-20261101/full-suite-runtime-activation-marker-final.log`，数字按静默日志状态符统计。全目录 Ruff、TypeScript/Vite、55 项激活相关定向合同、47 项监督器合同及浏览器确认按钮合同通过；真实 OS Worker 的配置热读取和变更阻断通过。首轮全量 1258 passed / 1 skipped / 1 error 的空就绪文件竞态及修复证据保留，详见 `NEW_MACHINE_RUNTIME_ACTIVATION_20261101.md`。安装/运行身份冲突、审片首启和真实 H3 仍未完成，整体 PARTIAL。

首启阶段：**1245 passed / 1 skipped / 2 warnings，exit=0**。命令 `uv run --project backend --locked pytest backend/tests -q`，日志 `.local-acceptance/new-machine-goal-20261101/full-suite-cold-start-final.log`，数字按静默日志状态符统计；全目录 Ruff、TypeScript/Vite 构建通过。新增首启身份、停止顺序、部分安装及配置写入阻断共 14 项，原有离线 E2E、film crew、V1.3、Worker/系列合同均在完整后端套件内。详见 `NEW_MACHINE_COLD_START_FIX_20261101.md`。配置激活、审片首启与真实 H3 仍未完成，整体 PARTIAL。
目录阶段（2026-11-01）：1231 passed / 1 skipped / 2 warnings，exit=0。命令 `uv run --project backend --locked pytest backend/tests -q`，日志 `.local-acceptance/new-machine-goal-20261101/full-suite-storage-caches-final.log`；数量从静默日志状态符统计。真实 SDK 依赖安装为 PASS_SDK_ONLY；指定 F 盘的应用依赖安装、Mock 工作台启动/验证式关停、既有 SQLite 保留及浏览器目录保存通过。全目录 Ruff、TypeScript/Vite 构建通过。完整新机目标仍 PARTIAL；详见修复记录与 LOCAL_STORAGE_SETTINGS.md。
2026-11-01 后续修复回归：1206 passed / 1 skipped / 2 warnings，exit=0。Ruff、TypeScript/Vite、隔离 Alembic 新库升级及当前版本核验通过。`scripts/verify_setup_web.py` 验证真实 API/SQLite 计划恢复和审批哈希；最终安装执行被拦截，因此仅 UI 合同 PASS。完整修复、证据路径与剩余建设项见 `NEW_MACHINE_DEPLOYMENT_FIX_20261101.md`，新机完整目标仍为 PARTIAL。

- IMPLEMENTED / TESTED_OFFLINE：软件链路（目录→计划→审批→配置生成→就绪分层）。
- 已单独真实验证：154 个 Python 发行包下载核验、隔离 venv/pip 安装、源码受控构建、SDK 导入；固定 ComfyUI/节点的隔离 CPU 启动与默认 profile 节点/参数名合同。后续记录见修复报告，不能升级为模型推理验收。
- NOT_RUN：完整约 60.8GB 物料下载、实际模型加载、真实 H3 推理、干净 Windows 验证。可选超分 profile 缺少指定 Handoff 节点，仍待建设。
- 模拟证据不冒充真实部署；未承诺无人工审批的全自动艺术裁决。
