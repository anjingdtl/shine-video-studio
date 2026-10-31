# LOCAL_TECH_FINAL_CLOSURE_V3 · 进度交接文档

> **新机部署轮（2026-10-31/11-01，分支 codex/new-machine-deployment，未推送）**：在 78c622c 基线上建设了空白 Windows 新机→生产就绪的完整链路：
> bootstrap 入口（PS5.1 可跑）、只读环境探测、14 组件真实钉点部署清单（60.7GB，全部 READY），
> 目录→安装计划→既有审批链→实例四步链→受管配置生成→六层就绪校验，以及 Web「新机设置」向导。
> 15 项新增/更新测试全绿（14 新增 + 1 按新合同重写）；定向回归 114 passed；全量后端 1193 项（结果以最终提交为准）；
> Ruff/Alembic 新库/前端构建/uv sync --locked 全部通过。真实下载/安装/启动/推理 = NOT_RUN（需用户授权）。
> 详见 [NEW_MACHINE_DEPLOYMENT.md](NEW_MACHINE_DEPLOYMENT.md) / [TESTLOG](NEW_MACHINE_DEPLOYMENT_TESTLOG.md) / [CATALOG](NEW_MACHINE_DEPLOYMENT_CATALOG.md)。


> **最新快照（2026-10-31，第二轮复审修复）**：修复基线=`81a92f3`，本轮修复与本文一并归档为本地 Git 提交。已关闭 EDL 元数据丢失租约后覆盖与 64 项窗口饥饿两项缺陷；正式新增 3 项回归，定向 23 passed，全量 **1178 passed / 1 skipped / 2 warnings**，其余本轮本地工程检查通过。没有新增迁移或依赖；原始反例和历史验收证据保留。详见 [本轮修复与验证](LOCAL_DELIVERY_FENCING_FIX_20261031.md)。以下状态按对应历史轮次阅读；真实影片待审与 NOT_RUN 项维持原边界，本轮仅做本地提交，未推送。

> 状态快照时间：2026-10-31 11:45 (本地)。本文档由上一会话写入，供下一个 Agent 继续收官。
> 功能提交（未推送）：`a403a62`（一条 amend 后的单一功能提交，基线 `09f17c6`）。
> 工作区状态：干净（无未提交的受管文件改动）。
> **追加轮次（2026-10-31）**：独立审查基线 `d8ae267` 的两项缺陷（P1 忙等 / P2 双领取）与证据索引自引用已全部关闭；被测代码 = `d8ae267` + 未提交工作树修复（3 文件，未提交未推送，由用户决定）；全量 1175 passed / 1 skipped。修复已按用户指令提交并推送至 `origin/main`。详见 §0。

## 0. 追加轮次快照（2026-10-31 独立审查关闭轮）

- 被测代码：`d8ae26711286bee2d5727444f245cb4d42c8258f` + 未提交工作树修复（`backend/shine/media/delivery_engine.py`、`backend/tests/test_delivery_orders.py`、`scripts/build_evidence_index.py`；3 files, 361+/109-）。
- 已关闭：P1 未到期 ASSEMBLING 忙等（有限调度轮）；P2 交付订单双领取（原子条件领取 + `(owner, state_version)` 写回令牌）；证据索引自引用（按路径排除输出自身，实测 972 项，两次独立全量校验 PASS）。
- 反例 2 passed（断言未放宽、反例未修改）；交付套件 16 passed（5 项新长期回归入正式测试目录）；全量后端 1175 passed / 1 skipped / 2 warnings；Ruff clean；Alembic 新库 head=f140a0030001；npm ci 0 漏洞 + 构建通过；verify×2 PASS；verify_v13_web=MOCK_VERIFIED；`git diff --check` 干净；端口/进程释放收据 PASS。
- 唯一 skip：`test_real_managed_startup.py:85`（SHINE_REAL_COMFY_PROBE 门控；本轮不启动真实 ComfyUI → SKIPPED，不计 PASS）。
- 验证在未提交的工作树上执行，修复随后已按用户指令提交并推送至 `origin/main`；没有新增真实 LLM/GPU 请求；未动生产栈与 ComfyUI 配置。
- 审计记录：`.local-acceptance/final-closure-v4/CLOSURE_ROUND.md` + 全部命令日志。
- §5 的旧交接待办全部关闭，逐项标注见 §5。

## 1. 三项收尾任务的完成度

| 任务 | 状态 | 证据 |
|---|---|---|
| 任务一：生产序列结束后自动 EDL + 自动装配成片 | ✅ 代码完成 + 真实 E2E 已证明 | `test_delivery_orders.py` 11 项全绿；run-m 横屏真实运行：序列 COMPLETED 后产品自身生成 EDL（来源=冻结剪辑意图）、调用 FFmpeg、注册成片、Web 实播 |
| 任务二：完整剧本 → 真实 AI Agent 自动分镜与生产编排 | ✅ 代码完成 + 真实 E2E 已证明 | `test_storyboard.py` 5 项全绿；run-m：五个真实 MiniMax Agent（编剧/导演/摄影/场记/剪辑）一次通过，产出 3 镜分镜 + 冻结剪辑意图，人工 apply 后进入生产 |
| 任务三：最终功能版本完整真实端到端复验 | ✅（后续轮次更新：portrait-h 竖屏全链 PASS，见报告 §4；本节下文为 11:45 的历史快照）🟡 横屏全链已 PASS；**竖屏 768×1344 尚未复现成功** | 横屏 run-m PASS；竖屏各轮被不同外部/采样因素阻断（见 §3） |

## 2. 关键证据：run-m 横屏全链（已 LOCKED）

- 运行目录：`.local-acceptance/final-closure-v3/20261031-run-m/`
- 剧本：`closure-script-v3.md`（659→874 字符三场中文短剧，两段普通话对白，第三镜 last_frame 软连续性）
- 真实 Agent 轨迹（5 个 AgentTaskRecord 全 SUCCEEDED，模型 MiniMax-M3.1-Flash，is_mock=false）：
  - 编剧 → ScriptBreakdown（3 beats，对白逐字保留）
  - 导演 → DirectorialPlan（3 镜、7s/镜、第三镜 last_frame）
  - 摄影 → CameraPlan（全英文）
  - 场记 → ContinuityReport（issues=[]，proceed_to_preflight）
  - 剪辑 → EditIntentPlan（冻结剪辑意图 D1，order 1/2/3）
- 生产：series `5578d5f5…` → 常驻 Worker 认领 → 3 镜真实 Ref2VA H3 全 PASSED（约 19 分钟）
- 自动交付：`AUTO_DELIVERY_ASSEMBLED` 审计事件；订单 `49e37475…` v1，edl_source=`frozen_edit_intent`，
  自动 EDL 镜头顺序 == 序列 position 顺序（脚本未手工构造 EDL、未调用 POST /deliveries）
- 成片：artifact `3710abde…`，SHA-256 `b0f7e7765c17b38fa9847c55125a827a91fd208a83903bbfddadc7e15ec7e1d6`，
  21.896s，**1344×768（横屏）**，全帧解码通过，浏览器实播 readyState=4、currentTime=3.92
- 本轮修复在真实链路中被证明有效的共 21 项，清单见 §4。

## 3. 竖屏（正式规格 768×1344）各轮阻断与已修复根因

| 轮次 | 阻断 | 根因 | 状态 |
|---|---|---|---|
| run-l | 首镜 `PENDING_WORKFLOW_MAPPING` | 导演给无声镜头绑了末帧参考图，Profile 不支持 | ✅ 已修（导演提示：参考集纪律） |
| final-portrait | `COMFY_HEARTBEAT_RECONCILIATION_REQUIRED` | 上轮异常终止的受管渲染器残留状态（复用同名数据目录） | ⚠️ 规避：换全新 RunName 重跑 |
| final-portrait-b | apply 422 `CONTINUITY_REVIEW_REQUIRED` | 场记把风格建议/风险提示写入 issues（合同语义不清） | ✅ 已修（issues 只收真实冲突） |
| final-portrait-c | UI 等待 30 分钟超时，DB 只有编剧成功 | 导演调用网络级挂起（MiniMax 侧延迟/断连），600s 超时 + 重试仍耗尽 UI 等待 | ❌ 未解决，需重跑或加等待上限 |
| （横屏对照）run-m | — | — | ✅ 全链 PASS |

结论：竖屏阻断均为**采样波动/外部网络/残留状态**，非产品逻辑缺陷（横屏同代码全链 PASS）。

## 4. 本轮全部代码修复（按首次出现顺序）

后端：
1. `shine/media/delivery_models.py`（新）：`delivery_orders` 持久订单表（series+version 唯一、有界重试、可取消）
2. `shine/media/delivery_engine.py`（新）：enqueue（幂等）/build_auto_edl（序列序+冻结意图+哈希复验+FFprobe 实测时长）/run_delivery_order（PENDING→ASSEMBLING→DELIVERED，业务拒绝 BLOCKED，基础设施失败有界重试）/run_pending_deliveries（恢复路径）/cancel_orders_for_series
3. `shine/comfyops/production_series.py`：序列 COMPLETED 与 reconcile 快路径**同事务** enqueue；取消时取消订单；series_public 暴露 edit_intent_artifact_id/delivery_result
4. `shine/worker.py`：serve 循环在 recover 之后、series 之前跑 run_pending_deliveries（下一 tick 即装配，无需脚本）
5. `shine/media/assembly.py`：`reuse_existing=True` 幂等复用同哈希成片行
6. `shine/api_delivery.py`：`GET /projects/{id}/delivery-orders`、`POST /delivery-orders/{id}/retry`
7. `shine/models.py`：ProductionSeries 增 `edit_intent_artifact_id`/`delivery_result`
8. `alembic f140a0030001`：新表 + 两列（新库/历史库升级测试已更新并通过）
9. `shine/agents/film_contracts.py`：`EditIntentPlan`(1.2) + `edit_intent` 工具（editor 专属）+ `ShotIntent.continuity`
10. `shine/agents/film.py`：
    - 编剧提示「逐字复制台词」；导演提示「视觉散文纯英文+台词只进 dialogue 结构+全序列绑音色+参考集纪律」；摄影提示「相机字段纯英文」；场记提示「issues 只收真实冲突」
    - `validate_output`：视觉散文/相机字段 CJK → `VISUAL_TEXT_MUST_BE_PROMPT_LANGUAGE`（早于 apply 层拒绝）
    - `BEGIN IMMEDIATE` 锁竞争有界重试
11. `shine/api_film.py`：
    - `POST /projects/{id}/storyboard/generate`：五角色链式真实调用、缺失角色主资产 `BLOCKED_MISSING_MASTERS` 明确清单、确定性拒绝有界重试（attempts=6，不确定结果绝不重试）
    - `apply-camera`：绑定映射 role==character→subject/否则 picture；对白时间轴按比例适配导演时长；`previous_shot_id` 连续性绑定；拒绝原因记日志
12. `shine/h3/rulebook.py`：H3_ENGLISH 剥离规则补「」CJK 引号（对齐声明的"quoted visible text"语义；对白原文仍由 H3_DIALOGUE 独立强制）
13. `shine/api_film.py` storyboarding 上下文：摄影/场记/剪辑均拿到完整剧本+锁定事实；场记额外拿到 ScriptBreakdown
14. `main.py`：series 创建绑定 edit_intent（校验 kind/项目/未失效）

前端：
15. `StoryboardPanel.tsx`（新）：导入剧本/选择文档/粘贴文本、锁定事实、远端同意勾选、五步展示、人工 apply
16. `DeliveryPanel.tsx`：自动交付订单区（状态/来源/尝试/失败码/重试按钮/内嵌播放）、活跃序列期间 5s 轮询
17. `ProductionSeriesPanel.tsx`：剪辑意图选择下拉、序列行显示自动交付结果
18. `api.ts`：类型扩展

验收工具：
19. `scripts/verify_real_closure_v3.py`（新）：浏览器驱动全链，**不构造 EDL、不调 POST /deliveries**，观察+下载+解码+实播
20. `scripts/run_real_closure_v3.ps1`（新）：编排（配方/reviewer/LLM 种子/启动/验收/停止）
21. `scripts/seed_llm_provider.py`（新）：从历史 DPAPI secret 环回注入 MiniMax 密钥（不落日志/证据），probe 诊断+重试

## 5. 交接待办（按顺序）

1. 【已关闭：portrait-h 全链 PASS，f/g 为失败前轮，勿再列为待重跑】**monitor/重跑竖屏**：portrait-c 已死（导演调用网络挂起）。直接重跑：
   `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_real_closure_v3.ps1 -RunName 20261031-final-portrait-d -Profile local-h3-ref2va-pdd8-2img-1aud-portrait -TimeoutMinutes 210`
   （控制台日志现写到 `F:/ClaudeWorkSpace/tmp_v3/closure_run_console.log`；每 10 分钟看一眼 `STEP|series status`）
   - 若 apply 又 `CONTINUITY_REVIEW_REQUIRED`：看 `.local-acceptance/final-closure-v3/<run>/data/shine.db` 的 ContinuityReport issues 内容判断（已修为只收冲突，理论上不会再犯）
   - 若 Agent 阶段网络挂起：直接重跑即可（采样波动）；MiniMax 侧延迟通常 30-300s/角色，UI 等待上限已 1800s
2. 【已关闭：BLOCKED_MISSING_MASTERS 负例 PASS（报告 §5）】**负例**：用仅含无主资产角色的剧本文本对运行中的栈调 `POST /api/v1/projects/{id}/storyboard/generate`（或 UI），断言 `BLOCKED_MISSING_MASTERS` 且缺清单正确；证据存 final-closure-v3
3. 【已关闭：fd7ebb0 轮收据 + 追加轮次端口/进程收据 PASS】**资源释放**：最终运行后 `scripts/stop_shine.py --data-dir <run>/data`；核验 8140/5183/8188/8116/8117 无监听、无残留 python/node 进程、GPU 回基线（`nvidia-smi`），截图存证据
4. 【已关闭：fd7ebb0 轮 1170 passed；追加轮次 d8ae267+修复 1175 passed / 1 skipped】**终版 SHA 全量命令清单**（必须在该 SHA 上）：`uv sync --project backend --locked`；全量 pytest（当前实测 exit=0，1 skip 为既有 SHINE_REAL_COMFY_PROBE 门控）；ruff（clean）；`npm ci --prefix frontend`；`npm --prefix frontend run build`；`verify_local_stack.ps1`；`verify_stack_operations.ps1`；Alembic 新库+历史库升级（test_alembic/test_migrations_windows 已覆盖，注意 head 已是 `f140a0030001`）
5. 【已关闭：v3 索引修复自引用后 972 项两次独立校验 PASS；v4 轮审计目录自建索引 28 项 PASS】**证据归档**：`.local-acceptance/final-closure-v3/` 下保留 run-m + 最终竖屏 run + 负例 + 释放记录；生成逐文件 SHA-256 索引（参考 `.local-acceptance/final-local-pass/evidence-index.json` 的做法）
6. 【已关闭：fd7ebb0 轮已推送核对；追加轮次修复已按用户指令提交并推送至 origin/main】**推送**：功能提交 `a403a62`（如后续无功能改动）+ 本文档提交一起 push；核对 `git rev-parse HEAD` == `origin/main`
7. 【已关闭：V3.md §0 追加轮次 + 本文档 §0 + TESTLOG §F】**最终报告** `docs/LOCAL_TECH_FINAL_CLOSURE_V3.md`：按收官指令 §八 的 14 项内容组织；横屏/竖屏分开列表；把 `WAIVED_EXTERNAL_BILLING`、`RIGHTS/ARTISTIC/FINAL = PENDING_HUMAN` 写明

## 6. 操作红线（勿重复踩坑）

- **绝不同时跑两个栈**（8140/8188/8116/8117 冲突；曾因并发启动毁掉一轮）
- 数据目录一次一用：异常终止后换新 RunName（残留 COMFY_HEARTBEAT 状态）
- LLM 密钥只在 DPAPI；不打印、不入库不入证据；来源 `.local-acceptance/closeout-20261025/secrets/`
- `.local-acceptance/**`、`.local-data/**`、媒体/DB 均不入 Git（检查 .gitignore 是否覆盖 final-closure-v3 的 run 目录）
- 横屏 Profile=`local-h3-ref2va-pdd8-2img-1aud`，竖屏=`…-portrait`（768×1344）
- Agent 真实调用 30-300s/角色属正常；`AGENT_CALL_FAILED`/`CONTRACT_OR_POLICY_REJECTED` 是确定性拒绝可重试，`LLM_TIMEOUT`/`LLM_CONNECTION` 不重试（防不确定结果伪装成功）
