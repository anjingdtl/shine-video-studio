# Shine 主线收束 V3.0｜基线审计（WP0）

> 审计基线：`origin/main@e4a3f198165aa16b54f515201976deeeb9f258d9`（方案编制基线即开工时最新 main HEAD，无差异需差分）。
> 审计方式：独立 worktree `shine-video-studio-mainline-v3` 只读代码审计 + 方案文件核对；未访问任何真实用户数据库。
> 审计时间：2026-11-14。CI：main push run `43585070557` 审计时 `in_progress`，合并前需复核。
> 行号均为 `e4a3f198` 快照行号。

## 1. Git 与环境事实

- 本地旧开发目录 `shine-video-studio` 的 main（`f8dbd70`）落后 `origin/main` 28 个提交；本地 main 是 origin/main 的祖先，无分叉。本轮一切实施基于 `origin/main@e4a3f19`。
- 待合并 PR：仅 #9（docs 分支，与本轮工程改动无冲突）。
- 方案分支 `docs/shine-mainline-convergence-v3-20261114` 只含方案文件，已完整读取。
- 新 worktree：`F:\ClaudeWorkSpace\projects\shine-video-studio-mainline-v3`，实施分支 `fix/shine-mainline-v3-convergence-20261114`。

## 2. 真实已复现缺陷（本轮必须修）

| ID | 优先级 | 位置 | 事实证据 | 判定 |
|---|---|---|---|---|
| C-01 | P0 | `frontend/src/components/SetupCenter.tsx:468-471` | `prepare()` 在 `downloadBytes>0` 时调 `GET /setup/connectivity`，`blocked` 数组非空即 `throw`，整个准备流程中止。`connectivity.py` 的 `PROBES` 固定 4 站全部探测，与本次选中能力无关 | **已证实**。图片能力 3 个组件只依赖 `huggingface.co`（`config/deployment/qwen-image-2.1-windows-x64.json` 全部文件 URL 均为 HF），codeload/pypi/torch 失联时图片下载今天被整体拦下 |
| C-02 | P0 | `SetupCenter.tsx:440-441` | `canPrepare` 要求 `!!selectedVariant`，与 `caps.h3` 无关 | **已证实**。不勾 H3、只准备出图/审片时，无变体机器上按钮永远禁用 |
| C-03 | P0/P1 | `SetupCenter.tsx:490-499,566-574,614-624`；`setupExecution.ts:165-185` | `runPlan` 丢弃 `waitForInstallationAttempt` 返回的终态视图；安装 attempt 的真实终态是 `FILES_VERIFIED_PENDING_RUNTIME`（`installation_worker.py:459-492`，`RUNTIME_CONTRACT_READY` 仅在常量表中出现、安装器从不写入），但能力结论一律写 `READY`/「资源已就绪」 | **已证实**。文件就绪被表述为能力就绪，违反「文件已核验≠运行已验证」合同 |
| C-04a | P1 | `backend/shine/api_preparation.py:197-249` | `POST /comfyops/install-plans` 内联自建 `InstallationPlan`（依赖闭包手写），**不经过** `/setup/install-plan` 的能力判定/变体资格复核/候选确认（`api_setup.py:725-874`），两套 admission 并存 | **已证实**。绕过 UI 直调该路由可逃逸 H3 生产门禁与候选确认 |
| C-06a | P1 | `frontend/src/App.tsx:312-332` | 导演页同时拉 `/journey`（7 阶段，`director/journey.py:45-53,232`）与 `/director/overview`（10 态 `EPISODE_STATES`，`director/overview.py:39-49`），两者 refresh 依赖不同 epoch（`journeyRetry` vs `directorPackageId`），存在状态不一致窗口 | **已证实（一致性风险）**。同页两套阶段投影 |

## 3. 高风险重复/漂移（需处理或如实登记）

| ID | 优先级 | 事实 |
|---|---|---|
| C-04b | P1 | 尝试读取双路由：`GET /comfyops/install-attempts/{id}`（`api_preparation.py:506`）与 `GET /comfyops/installation-attempts/{id}`（`api_preparation.py:511+`）；`SetupPanel` reconcile 用前者、`setupExecution.ts` 用后者 |
| C-08 | P1 | 根目录 `ShineLauncher.exe`（git 追踪，3.78MB）的 `ShineLauncher-build-info.json` 为 v0.1.0 / `source_commit f41e609…` ≠ 当前 HEAD；正式 release 工作流模板 `launcher/packaging/ci/launcher-release.yml` **未安装**进 `.github/workflows`（现有 `launcher-windows.yml` 只向 `feat/shine-launcher-impl-20261113` 分支回写 exe） |
| C-09 | P2 | `worker.py` 4541 行、`director/pipeline.py` 3933 行、`api_preparation.py` 2278 行、`frontend/src/api.ts` 1973 行、`App.tsx` 1487 行。体积=冲突风险，不是缺陷 |

## 4. 已经解决、不得重复实现的问题

方案编制后 PR #23/#24 已落地以下能力（本轮直接复用，不重做）：

- **按能力收窄的安装计划 admission**：`/setup/install-plan` 按 `capability.py`（`catalog_capability`/`plan_capability`）判定能力；H3 硬件资格（`HOST_NOT_QUALIFIED`）只作用于 H3 能力；图片/审片报告自己的 runtime floors 而不在下载时强制；候选变体显式确认。前端 `SetupCenter` 每能力一份计划、一份结论（`setupCapabilityGates.test.tsx` CASE-01/07 已覆盖）。
- **受控下载队列与重附着**：attempt 台账、`WAITING_FOR_TARGET`、断线 `EXECUTION_UNCONFIRMED` 禁重投（`setupExecution.ts`）。
- **无第二执行器**：`run_pending_installation` 唯一调用点 `worker.py:4263`；`advance_control_operations` 唯一调用点 `worker.py:4350`；`advance_runs` 唯一非测试调用点 `worker.py:4329`；交付唯一驱动 `run_pending_deliveries`（`worker.py:4384`）。Worker 唯一入口 `serve()`（`worker.py:4467`）。
- **UNKNOWN 门禁**：故事/创意/管线任务的核对、一次性授权重发、死亡 worker 判定均已实现并有测试（`test_story_unknown_resend.py`、`test_scene_dead_worker.py` 等）。
- **整份合同调用**：PR #22 已恢复五工种整份合同（`0c978f2`），逐 beat 串行引擎已回退，不得再引入。

## 5. 调用图摘要（权威链）

- **安装**：UI（SetupCenter/SetupPanel/Wizard）→ `POST /setup/install-plan`（唯一带门禁的建单口）→ `deployment.create_install_plan`（target_key 去重）→ approve/execute → attempt 台账 → Worker `run_pending_installation`（`installation_worker.py:85`）→ 隔离目录 + `verification_receipts`。平行口 `POST /comfyops/install-plans`（无门禁）为 C-04a 修复对象。
- **运行时**：API 只提交控制意图（`runtime_orchestrator/api.py`）→ Worker `advance_control_operations`（`executor.py:259`）执行；`ResourceScheduler`（`comfyops/scheduler.py:17`）GPU 互斥；`status.py` 唯一只读快照。
- **导演**：`journey.py compute_journey`（7 阶段，`GET /projects/{id}/journey` 唯一出口）；`streamline.py` + `api_streamline.py`（30+ 路由）；`production/runner.py advance_runs`（Worker 驱动）；交付 `episode_delivery.run_episode_delivery` ← `delivery_engine.run_pending_deliveries` ← Worker。
- **Worker 单拍次序**（`worker.py:4252-4449`）：安装队列 → 文本准备（burst 限流）→ runtime 解析 → 控制操作 → 审片租约对账 → 过期 job 恢复 → Run 推进 → 交付 → H3 series → 创意/故事任务 → 图片项 → RelayVSR → 审片 → 普通 job。

## 6. 尚需实机验证（本轮不做口头结论）

- main push CI `43585070557` 最终结果（合并前复核）。
- RTX 5080 真实 H3 出片（`REAL_H3_RENDER_PASS` 签署需真实运行证据）。
- RTX 4070：`EXEMPT_HARDWARE_UNAVAILABLE`（非本地硬件）。
- Launcher 正式发行链（签名信任链 + Windows 真人验证）：本轮不构建、不签名、不发布。

## 7. 本轮范围裁定

- **修**：C-01、C-02、C-03（WP1）；C-04a（`/comfyops/install-plans` 门禁对齐）；C-06a（同页双投影一致性）；C-08 如实登记不伪造发行。
- **不修**（无证据缺陷，只登记）：C-04b 双读取路由（两者都有调用方，兼容保留并登记）；C-09 文件体积（不做行数驱动重构）。
- **禁止项复核**：不建第二调度器/下载器/状态机；不动真实用户数据；UNKNOWN 门禁不放松；8 分片规则照 AGENTS.md 执行。
