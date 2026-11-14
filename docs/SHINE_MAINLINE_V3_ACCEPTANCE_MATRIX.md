# Shine 主线收束 V3.0｜验收矩阵（ACCEPTANCE_MATRIX）

> 实施分支：`fix/shine-mainline-v3-convergence-20261114`（基线 `origin/main@e4a3f198`）
> 本机：Windows 11（10.0.26200），RTX 5080 16GB / 32GB RAM。命令均在仓库根 `F:\ClaudeWorkSpace\projects\shine-video-studio-mainline-v3` 执行。

## 1. 等级总览与签署

| 级别 | 结论 | 依据 |
|---|---|---|
| L0 Static（Ruff / tsc / build / 迁移） | **ENGINEERING_PASS** | 全部命令退出码 0（见 §2） |
| L1 Focused（安装/能力/预检/准入/导演链） | **ENGINEERING_PASS** | 新旧定向测试 0 fail（见 §3） |
| L2 Integration（离线 E2E / 安装队列恢复） | **ENGINEERING_PASS** | 两集离线 E2E 与安装持久化在 8 分片内全绿 |
| L3 Full（8 分片 + 前端全量 + Alembic + 启停） | **ENGINEERING_PASS** | §2，`CODEBUDDY_SAFE_DELETE_ENABLED=0` 已设 |
| L4 Windows/GPU 真实出片 | **PENDING**（本轮未执行真实 H3） | 未运行即不签署，见 §5 |
| FULL_AUTOPILOT_PASS | **PENDING** | 同上 |
| RTX 4070 | **EXEMPT_HARDWARE_UNAVAILABLE** | 非本地硬件 |
| WINDOWS_INSTALL_PASS（安装包） | **未执行** | 本轮不改安装包/启动器，未构建发行 zip |

## 2. L0/L3 完整命令与退出码

| 项 | 命令 | 退出码 | 结果 |
|---|---|---|---|
| Ruff（后端全量） | `backend/.venv/Scripts/python.exe -m ruff check backend` | 0 | All checks passed |
| TypeScript | `frontend: npx tsc --noEmit` | 0 | 无错误 |
| 前端生产构建 | `frontend: npm run build` | 0 | ✓ built in 2.04s（chunk>500kB 为既有警告） |
| 前端全量 | `frontend: npx vitest run --no-file-parallelism` | 0 | **60 文件 / 347 用例全绿** |
| Alembic（隔离库） | `SHINE_DATABASE_URL=sqlite:///<tmp>/fresh.sqlite3 … alembic upgrade head` → `alembic check` | 0 / 0 | 迁移到 head 成功；No new upgrade operations detected |
| 后端全量 8 分片 | `CODEBUDDY_SAFE_DELETE_ENABLED=0`，`split -n r/8` 按文件均分，8 进程并行，每片留日志（`/tmp/v3shards2/chunk-*.log`） | **8/8 exit=0** | **2168 用例，0 FAILED / 0 ERROR** |
| Windows 启停 | `pwsh -File scripts/verify_local_stack.ps1`（mock，一次性数据目录） | 0 | API health ok、mock job PASSED、worker 在线、审计 7 事件；结束后端口 8123 无监听 |

说明：第一轮 8 分片 6/8 绿，`chunk-ad`（host_tool_resolution 5 例）与 `chunk-ae`（production_gate_weight_evidence 1 例）失败——两处均为 **main@e4a3f19 基线回归**（Linux L3 CI run `43585070557` 同样失败后者），已修复（见实施报告 §6）并在同一文件清单重跑 exit=0；随后在最终树上**完整重跑 8 分片全绿**（本表数字）。

## 3. L1 定向测试（新旧）

| 文件 | 结果 |
|---|---|
| `backend/tests/test_mainline_v3_capability_preflight.py`（新增 11） | 全绿 |
| `backend/tests/test_mainline_v3_install_admission.py`（新增 5） | 全绿 |
| `backend/tests/test_setup_capability_gates.py` / `test_connectivity.py` / `test_installation_persistence.py` / `test_v13_e2e.py` / `test_v13_instances.py` / `test_v13_preparation.py` | 全绿（含安装暂停/续传/异常恢复台账用例） |
| 导演链：`test_director_streamline.py`(18) / `test_director_journey_closeout.py`(13) / `test_novel_multiepisode_autopilot.py`(13) / `test_novel_to_two_episodes_e2e.py`(1) / `test_director_worker_scheduling.py`(7) | 全绿 |
| `frontend/src/test/mainlineV3Setup.test.tsx`（新增 4） | 全绿（旧代码上 RED 4/4 → 修复后 GREEN） |
| `frontend/src/test/mainlineV3Director.test.tsx`（新增 2） | 全绿 |
| `frontend/src/test/setupCapabilityGates.test.tsx`(9) / `setupCenter`(7) / `simpleSetup`(9) / `setupEntry`(8) / `fourStageRecovery` / `directorRun` / `directorEntries` | 全绿 |

## 4. WP1 P0 验收矩阵（方案 §WP1 规定的 8 条）

| # | 场景 | 覆盖 |
|---|---|---|
| 1 | 仅图片、H3 未选、H3 站点失联 → 图片可入队 | `mainlineV3Setup` C-01 例1（codeload 失联仍提交图片计划）+ `test_mainline_v3_capability_preflight::test_image_preflight_ignores_unrelated_blocked_sites` |
| 2 | 仅 H3、必需源失联 → 具名阻断、可恢复、无假成功 | `mainlineV3Setup` C-01 例2（0 计划提交、双能力具名结论）+ `test_h3_preflight_blocked_huggingface_names_the_components` |
| 3 | 图片+H3、H3 不合格 → 各自独立结论 | `setupCapabilityGates` CASE-07（既有）+ C-01 例2 |
| 4 | 已有权重复用、不重复下载 | `setupCapabilityGates`「核验没做完不重下」+ SetupCenter 复用路径（既有） |
| 5 | 多计划同目录排队 | `test_installation_queue.py`（8 分片内全绿，WAITING_FOR_TARGET 台账） |
| 6 | 网络中断/重定向/重启恢复 | `test_installer_redirects.py` / `test_installation_persistence.py`（8 分片内全绿） |
| 7 | `FILES_VERIFIED_PENDING_RUNTIME` 只显示文件就绪 | `mainlineV3Setup` C-03 例（「文件已核验 · 运行待验证」，无「资源已就绪」） |
| 8 | 独立能力正常时不需手工删错/重启 | C-02 例（无 H3 方案机器出图按钮可用、进入队列） |

## 5. L4 / 真实边界（如实）

- **未执行**：RTX 5080 真实 H3 单镜/两集、真实 VLM/ASR、Launcher Windows 真人 GUI、发行 zip 构建。以上全部 PENDING，不冒充。
- **已执行的真实边界**：Windows 本机启停（mock 全链路，见 §2）；Alembic 真实迁移（隔离库）。
- main CI：run `43585070557` 的 Linux L3 失败（基线）已由本轮修复覆盖；Windows L3 在本报告时点仍在运行，PR CI 将重新验证两平台。

## 6. 复现命令（摘要）

```bash
# 后端定向
CODEBUDDY_SAFE_DELETE_ENABLED=0 backend/.venv/Scripts/python.exe -m pytest backend/tests/test_mainline_v3_capability_preflight.py backend/tests/test_mainline_v3_install_admission.py -q
# 后端全量 8 分片（AGENTS.md 固定规则）
ls backend/tests/test_*.py > /tmp/all.txt && split -n r/8 /tmp/all.txt /tmp/chunk-
CODEBUDDY_SAFE_DELETE_ENABLED=0 bash -c 'for f in /tmp/chunk-*; do (backend/.venv/Scripts/python.exe -m pytest $(tr "\n" " " < "$f") -q -p no:cacheprovider > "$f.log" 2>&1; echo "$f exit=$?" >> /tmp/exits.txt) & done; wait'
# 前端 / 构建 / 静态
cd frontend && npx vitest run --no-file-parallelism && npx tsc --noEmit && npm run build
# 迁移（隔离库）
SHINE_DATABASE_URL="sqlite:///<abs tmp>/fresh.sqlite3" backend/.venv/Scripts/python.exe -m alembic -c backend/alembic.ini upgrade head && ... check
# Windows 启停
pwsh -File scripts/verify_local_stack.ps1
```
