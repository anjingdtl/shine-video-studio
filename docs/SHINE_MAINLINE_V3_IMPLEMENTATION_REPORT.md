# Shine 主线收束 V3.0｜实施报告（IMPLEMENTATION_REPORT）

> 实施分支：`fix/shine-mainline-v3-convergence-20261114`（基线 `origin/main@e4a3f198`）
> worktree：`F:\ClaudeWorkSpace\projects\shine-video-studio-mainline-v3`
> 日期：2026-11-14。测试命令与退出码见配套 `SHINE_MAINLINE_V3_ACCEPTANCE_MATRIX.md`。

## 0. 总览

本轮按 V3 方案完成 WP0–WP5：不重建系统、不建第二套框架，修复三个已证实 P0、一处 P1 双准入、一处 P1 双投影分叉，并修复两个 **main 基线上已存在的 CI 回归**（一个测试建模缺字段、一个 Windows 不可执行的 POSIX 测试）。真实 H3 出片（L4）未执行，按分级如实标注 PENDING。

## 1. 提交清单

| # | commit | 主题 |
|---|---|---|
| 1 | `c44480c` | WP0 基线审计文档 + WP1 P0 修复（按计划预检 / canPrepare 解耦 / FILES_READY 语义） |
| 2 | `7cca5eb` | WP2/C-04a：专业路由 local H3 闭包具名拒绝，H3 只有一套准入 |
| 3 | `9ffe3cf` | WP3/C-06a：journey 跟随深链指名的导演包，同屏同范围同刷新 |
| 4 | `8f84c16` | main 基线回归修复①：synthetic 权重组件补 `destination_family`（PR#23 家族绑定合同） |
| 5 | `eddc333` | main 基线回归修复②：五个 POSIX shim 工具测试改为全主机可测（Windows 注入执行缝） |

全部提交在实施分支，未推 main；每个提交独立可 revert。

## 2. WP1｜安装/下载 P0（C-01/C-02/C-03）

**原表现**：`SetupCenter.prepare()` 对四站点全局 `GET /setup/connectivity` 一刀切（任一 BLOCKED 全部中止）；`canPrepare` 强制 `selectedVariant`（无 H3 方案的机器永远不能只准备出图/审片）；安装终态一律写「READY/资源已就绪」。

**根因**：预检是诊断视图当准入用；能力间共享了 H3 的前置条件；`runPlan` 丢弃 attempt 终态阶段。

**改动**：
- `backend/shine/comfyops/connectivity.py`：`HOST_PROBES`（下载主机→探测族）、`required_sources()`（从服务端清单文件 URL 推导组件闭包的必需来源）、`probe_upstreams(probe_ids=…)` 子集探测（默认行为不变）、`preflight_for_components()` 按计划准入（BLOCKED 才拒；DEGRADED/NOT_PROBED 具名呈现）。
- `backend/shine/api_setup.py`：`POST /setup/connectivity/for-plan`（能力由清单推导，同 `/install-plan`；旧 `GET /setup/connectivity` 原样保留）。
- `frontend/src/components/SetupCenter.tsx`：删除全局拒绝；每能力各自 `preflightSources`（不可用时以持久 attempt 为门，不制造假成功）；`canPrepare` 仅在勾选 H3 时要求变体；`runPlan` 返回终态阶段，终态 `FILES_VERIFIED_PENDING_RUNTIME` → 新 `FILES_READY` 结论（「文件已核验 · 运行待验证」），H3 激活段继续拥有「制作环境已就绪」措辞。
- `frontend/src/styles.css`：`setup-outcome-files_ready` 中性强调。

**测试**：`backend/tests/test_mainline_v3_capability_preflight.py`（11 例，离线）；`frontend/src/test/mainlineV3Setup.test.tsx`（4 例，**已在旧代码上验证 RED（4/4 失败）再 GREEN**）；`setupCapabilityGates.test.tsx` 文案断言同步。P0 验收矩阵 1–8 全部落进测试（见验收矩阵）。

**风险与回滚**：预检端点只读、旧 GET 不动；前端回退即恢复旧行为。revert 单个 commit 即可。

## 3. WP2｜安装入口与 Launcher 合同（C-04a/C-04b/C-08）

**原表现**：`POST /comfyops/install-plans`（专业路由）对任意登记包建 LOCAL 计划，绕过 `/setup/install-plan` 的 H3 变体资格复核与候选确认——第二套更弱的 H3 准入。

**改动**：`deployment.h3_catalog_closure_requested()` 按「目录包行 id=组件 id」识别 H3 目录视频权重；专业路由 `executor=local` 且命中 H3 闭包 → 409 `H3_INSTALL_PLAN_USES_SETUP_ADMISSION`（指名制作环境入口）。fake 演练、审片权重、自定义包不受影响。

**Launcher（C-08）**：不改代码、不构建、不签名。如实登记：根 `ShineLauncher.exe` 为 0.1.0 原型（build-info `source_commit f41e609…` ≠ main），正式 release workflow 模板未安装；详见 MODULE_OWNERSHIP §5。修复回滚：revert 即恢复专家路由旧行为。

## 4. WP3｜导演主线一条事实线（C-06a）

**原表现**：深链 `#d/package?package=<id>` 下 overview 描述**指名包**、`/journey` 无 scope 描述**最新包**，且两者刷新触发不同——同屏两个投影回答不同范围的问题。

**改动**：`App.tsx` journey 调用带同一 `packageId` scope（后端 `package_id` 参数本就支持且未知 id 具名 404）；两投影同一组刷新触发（`dataEpoch/journeyRetry/package 变化`）。标题段合并不跨集、整集自动确认证据前提、一集失败不拖垮另一集经基线测试复核（`test_novel_multiepisode_autopilot.py` 13 例、`test_novel_to_two_episodes_e2e.py` 1 例在本轮全绿）。

**测试**：`frontend/src/test/mainlineV3Director.test.tsx`（2 例）；App 级回归 31 例绿。

## 5. WP4｜Worker 结构（审计裁定：无改动）

审计证明：`run_pending_installation`、`advance_control_operations`、`advance_runs`、`run_pending_deliveries` 全仓唯一非测试调用点都在 Worker 主循环；`serve()` 唯一常驻入口。**没有第二套调度器/安装执行器/交付驱动**，按方案「无证据不强制改代码」，WP4 仅产出 MODULE_OWNERSHIP §4 的次序与职责登记。

## 6. main 基线回归修复（本轮追加）

两处失败都由 main 的 CI（run `43585070557`：Linux L3 fail）与本地 8 分片复现，均与本轮改动无关（本轮 diff 未触碰其依赖链），按授权自主修复：

1. `test_production_gate_weight_evidence::test_verified_bytes_the_current_instance_binds_are_consumable`：PR#23 引入按家族证明绑定（正确收紧），测试合成组件缺 `destination_family`；quick 层不收集该文件所以漏网。修测试建模（补家族字段），负例不受影响。
2. `test_host_tool_resolution.py` 五例：POSIX sh shim 测试在 Windows 结构性不可执行（CreateProcess 不认 shebang），PR#24 只验证过 quick 层。改为：所有主机真实跑解析（`windows=False`，managed 优先/绝对路径）；POSIX 主机照旧真实执行；Windows 主机注入执行缝。显式 `windows=True` 的 exe/cmd 测试不动。

## 7. 未完成 / PENDING / 豁免

- **REAL_H3_RENDER_PASS / FULL_AUTOPILOT_PASS**：本轮未做 5080 真实出片（时间窗与外部条件未满足），不声称。
- **RTX 4070**：`EXEMPT_HARDWARE_UNAVAILABLE`。
- **Launcher 正式发行链**：签名信任链 + Windows 真人验证未做，本轮只登记漂移。
- **main required checks 启用**：治理建议已写入 MODULE_OWNERSHIP §6，需仓库管理员授权后操作。
- C-04b（双 attempt 读取路由）按方案保留兼容并登记，未删。

## 8. 回滚方法

每个工作包一个独立 commit，`git revert <sha>` 走 PR 即可；无 DB schema 变更（Alembic head 未动，`alembic check` 无 drift）；前端改动不涉旧深链/主题。
