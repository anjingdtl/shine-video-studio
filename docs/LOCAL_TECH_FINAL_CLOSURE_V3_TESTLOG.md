# LOCAL_TECH_FINAL_CLOSURE_V3 · 测试记录

> 记录截至 2026-10-31 11:45。功能提交 `a403a62`（基线 `09f17c6`）。

## A. 软件测试（离线，FFmpeg 合成测试图样，不含真实模型）

| 套件 | 结果 | 备注 |
|---|---|---|
| `pytest backend/tests`（全量 80 文件） | **exit=0，0 failed，1 skipped** | skip 为既有门控 `SHINE_REAL_COMFY_PROBE`；最后一轮完整输出存 `F:/ClaudeWorkSpace/tmp_v3/final_suite_precommit2.txt` |
| `backend/tests/test_delivery_orders.py`（新） | 11 passed | 幂等交付/返工新版本/崩溃恢复/有界重试/耗尽报告/损坏源阻断/取消/未通过镜头阻断/EDL 顺序=序列序/冻结意图继承/Worker 循环驱动 |
| `backend/tests/test_storyboard.py`（新） | 5 passed | 五 Agent 全链/缺主资产阻断/风格+路由+同意守卫/edit_intent 权限与覆盖/序列绑定冻结意图 |
| `backend/tests/test_film_crew.py` | 全绿 | 1 项对抗性测试更新：中文摄影字段改为在 Agent 合同层拒绝（`VISUAL_TEXT_MUST_BE_PROMPT_LANGUAGE`），比原 apply 层断言更早、更严 |
| `ruff check backend/shine backend/alembic backend/tests` | clean | 含全部新文件 |
| Alembic（test_alembic / test_migrations_windows / test_v13_migrations） | 全绿 | head 固定断言更新为 `f140a0030001` |
| `npm ci && npm run build`（frontend） | 成功 | dist 326KB，无类型错误 |

## B. 真实端到端（run-m，横屏 Profile，全部真实）

时间线（见 `.local-acceptance/final-closure-v3/20261031-run-m/logs/real-closure.log`）：

| 步骤 | 结果 |
|---|---|
| LLM 种子 | MiniMax-M3.1-Flash 环回注入，probe ok（llm-provider-seed.json） |
| 项目/资产 | 4 个历史已批准主资产重绑（角色/场景/音色/末帧），人工凭据批准+锁定（API 复核 approved&&locked） |
| 风格冻结 | sha256 `b32a7722fc17…` |
| 规则包冻结 | shine-h3-rules/1.1.0，hash `5990ae18…` |
| 剧本导入 | 874 字符中文三场短剧（document sha 在 record） |
| **五 Agent 真实调用** | 5/5 SUCCEEDED，is_mock=false，模型 MiniMax-M3.1-Flash；产出 ScriptBreakdown/DirectorialPlan/CameraPlan/ContinuityReport/EditIntentPlan |
| 人工 apply | 3 镜创建，预检（H3 编译+规则包）全过 |
| 序列启动 | series `5578d5f5…`，Worker 常驻认领，RUNNING |
| **三镜真实 H3** | 全 PASSED（Ref2VA INT8 剪枝版，受管渲染器，独占租约） |
| **自动 EDL** | 订单 `49e37475…` v1，edl_source=`frozen_edit_intent`，clips 顺序==序列 position，审计 `AUTO_DELIVERY_ASSEMBLED` |
| **自动装配** | FFmpeg 完成，成片 `3710abde…` SHA-256 `b0f7e7765c17b38fa9847c55125a827a91fd208a83903bbfddadc7e15ec7e1d6`，21.896s，1344×768，全帧解码 OK |
| Web 实播 | `#delivery .auto-delivery video` readyState=4，currentTime=3.92，duration=21.896 |
| 脚本纪律 | 验收脚本零次构造 EDL、零次调用 POST /deliveries（代码可审计） |

## C. 竖屏轮次（正式规格 768×1344）——未完成，逐轮记录

| 轮次 | 结果 | 阻断点 | 事后处置 |
|---|---|---|---|
| run-l | 序列 NEEDS_REVIEW | 首镜 PENDING_WORKFLOW_MAPPING（末帧图被绑进镜头） | 导演提示加参考集纪律 → 已修 |
| final-portrait | 序列 NEEDS_REVIEW | COMFY_HEARTBEAT_RECONCILIATION_REQUIRED（残留状态） | 规避=换新目录 |
| final-portrait-b | apply 422 | 场记把风格建议写进 issues | 合同语义已修（只收冲突） |
| final-portrait-c | UI 等待 30min 超时 | 导演调用网络挂起（DB 仅编剧 SUCCEEDED） | 待重跑（portrait-d） |
| （对照）run-m 横屏 | **全链 PASS** | — | 同一代码，证明产品逻辑无竖屏特有缺陷 |

## D. 待执行的剩余测试（下一 Agent）

1. 竖屏全链重跑（run-d，命令见进度文档 §5.1）
2. 负例：无主资产角色剧本 → 断言 `BLOCKED_MISSING_MASTERS` + 缺清单
3. 资源释放核验（端口/进程/GPU 基线）+ 存证
4. 终版 SHA 上：uv sync、全量 pytest、ruff、npm ci/build、verify_local_stack.ps1、verify_stack_operations.ps1
5. 证据索引（逐文件 SHA-256）+ `docs/LOCAL_TECH_FINAL_CLOSURE_V3.md` 最终报告
6. `git push` + 本地/远端 HEAD 核对

## E. 豁免与人工边界

- GitHub Actions：`WAIVED_EXTERNAL_BILLING`（不计入本地技术判定）
- `RIGHTS_APPROVAL / ARTISTIC_REVIEW / FINAL_FILM_APPROVAL = PENDING_HUMAN`
