# LOCAL_TECH_FINAL_CLOSURE_V3 · 测试记录

> **2026-11-02 V2 更新**：真实制作向导审批/执行合同、演示意图一致性及异常恢复已修复；核心 LLM 已纳入新机准备。最终软件检查：后端 **1379 passed / 1 skipped / 2 warnings**，前端 **70 passed**，真实浏览器合同 **3 passed**，TypeScript/Vite、Ruff、Alembic 新库/历史库副本零漂移、Windows 启停均通过。已提交源码 `bb4ce60` 打包验证 436 个文件 SHA-256 一致并正常推送 main，工程范围 **ENGINEERING ALL PASS**；此条之后的旧轮次数字为历史记录，最新结论以 [V2 报告](FINAL_DIRECTOR_WORKBENCH_ACCEPTANCE_V2_20261102.md) 为准。真实 H3/真实 LLM 生成本轮未运行，历史影片审片状态不变。

> **2026-11-02 已有 ComfyUI 复用修复**：本轮新增后端 9 项与前端 2 项回归；完整后端 1330 passed / 1 skipped / 2 warnings，后端定向 20 passed、前端 29 passed，Ruff、TypeScript/Vite 和真实网页代理只读探测通过。命令及精确边界见 [本轮报告](EXISTING_COMFYUI_FIX_20261102.md)。未下载或复制个人模型，未进行 H3 生产验收；浏览器目视检查受工具 URL 识别失败限制。

> **当前复验（2026-11-01）**：`af5d6b6` 加并发修复，完整后端 **1315 passed / 1 skipped / 2 warnings，585.28 秒，exit=0**；前端 **20 passed**，Ruff、TypeScript/Vite 通过；独立 HTTP 服务三轮各 8 个同名全局 POST 均为 1×201、7×409、数据库 1 行，过期风格批准 409。唯一 skip 为真实 ComfyUI 启动探针；额外 `alembic check` 存在两条基线已有索引差异。命令及证据见 [本轮报告](WEB_CONCURRENCY_FIX_20261101.md)，重封装记录见 [REPACKAGE_20261101](REPACKAGE_20261101.md)。以下不作为当前套件数字。

> **最新复验（2026-10-31，基线 `81a92f3` + 本轮工作树修复）**：新增 3 项正式回归修复前全部失败、修复后通过；交付套件与两轮原始反例 **23 passed**，全量后端 **1178 passed / 1 skipped / 2 warnings**（495.15 秒，exit=0）。Ruff、TypeScript/Vite、Windows Mock 栈及起停验证均 PASS，浏览器 MOCK_VERIFIED，历史证据索引 972/972 匹配。唯一 skip 为显式门控的真实 ComfyUI 启动探测。命令、证据与验收边界见 [本轮修复与验证](LOCAL_DELIVERY_FENCING_FIX_20261031.md)；以下为历史测试记录。

> 记录截至 2026-10-31 11:45。功能提交 `a403a62`（基线 `09f17c6`）。
> **2026-10-31 追加轮次**（独立审查基线 `d8ae267` 的两项缺陷与索引自引用，含 5 项新长期回归与全量 1175 passed）见 §F；§D 旧待办已全部关闭并逐项标注。

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

## D. 当时列出的剩余测试（已于后续轮次关闭，保留原清单作历史）

1. ~~竖屏全链重跑（run-d，命令见进度文档 §5.1）~~ → **已关闭**：portrait-d/e/f/g 为失败前轮，最终 `portrait-h` 全链 PASS（§F 之前已由 fd7ebb0 轮完成；报告 §4）
2. ~~负例：无主资产角色剧本 → 断言 `BLOCKED_MISSING_MASTERS` + 缺清单~~ → **已关闭**：负例 PASS（报告 §5，`negative_verdict=PASS`，审查轮只读复核维持）
3. ~~资源释放核验（端口/进程/GPU 基线）+ 存证~~ → **已关闭**（报告 §8 收据 + 追加轮次 §6 端口/进程释放收据）
4. ~~终版 SHA 上：uv sync、全量 pytest、ruff、npm ci/build、verify×2~~ → **已关闭**（fd7ebb0 轮 + 追加轮次 d8ae267 上复验 1175 passed / 1 skipped）
5. ~~证据索引 + 最终报告~~ → **已关闭**（索引修复自引用后 972 项两次独立校验 PASS；报告 §0/§9）
6. ~~`git push` + HEAD 核对~~ → **已关闭**：fd7ebb0 轮已推送核对；追加轮次的 3 文件修复已按用户指令提交并推送至 `origin/main`

## E. 豁免与人工边界

- GitHub Actions：`WAIVED_EXTERNAL_BILLING`（不计入本地技术判定）
- `RIGHTS_APPROVAL / ARTISTIC_REVIEW / FINAL_FILM_APPROVAL = PENDING_HUMAN`

## F. 追加轮次测试记录（2026-10-31，独立审查关闭轮，基线 d8ae267 + 未提交修复）

> 完整命令与退出码见 `.local-acceptance/final-closure-v4/CLOSURE_ROUND.md` §3；以下为结果汇总。
> 反例原文保留于 `.local-acceptance/audit-20261031-d8ae267/`（未修改、未 xfail、未放宽）。

| # | 套件/检查 | 结果 | 备注 |
|---|---|---|---|
| 1 | 审查反例 `test_delivery_review_counterexamples.py` | **2 passed**（修复前 2 failed，已复现） | 断言未放宽；反例文件未被修改 |
| 2 | `backend/tests/test_delivery_orders.py` | **16 passed**（11 旧 + 5 新长期回归） | 新回归：忙等不阻塞他单/重启不忙等且到期可恢复/双会话单领取/丢失令牌不能写回/装配中被重领不能覆盖 |
| 3 | `test_offline_e2e.py` + `test_film_crew.py` | **32 passed** | 按项目 AGENTS.md 要求的必跑套件 |
| 4 | v13 三件套 | **63 passed** | preparation / migrations / e2e |
| 5 | 完整后端套件 `pytest backend/tests` | **1175 passed / 1 skipped / 2 warnings**，exit=0，514.69s | 基线 1170 + 本轮 5 项新回归；唯一 skip = `test_real_managed_startup.py:85`（需 `SHINE_REAL_COMFY_PROBE=1` 且本地 ComfyUI；本轮按任务边界不启动真实 ComfyUI → 如实 SKIPPED，不计 PASS） |
| 6 | `ruff check backend/shine backend/alembic backend/tests scripts` | **All checks passed!** | exit=0 |
| 7 | Alembic 新库 `upgrade head` | exit=0，head=`f140a0030001`，12 迁移链完整 | 临时库已删除；历史升级由套件内 test_alembic / test_migrations_windows / test_v13_migrations 继续覆盖 |
| 8 | `uv sync --project backend --locked` | exit=0，Resolved 42 / Checked 41 | `--locked`（非 --frozen）；必要时进程内设 UV_DEFAULT_INDEX=https://pypi.org/simple |
| 9 | `npm ci --prefix frontend` | exit=0，0 vulnerabilities | --ignore-scripts |
| 10 | `npm --prefix frontend run build` | exit=0 | tsc -b && vite build，327.31 kB |
| 11 | `scripts/verify_local_stack.ps1 -ApiPort 18123 -KeepData` | PASS | 隔离端口 + 一次性数据目录，mock 从不生成视频 |
| 12 | `scripts/verify_stack_operations.ps1 -ApiPort 18128 -WebPort 15188` | PASS | custom proxy / NoWorker / 拒绝重复启动 / Worker 重启 / 确认停栈 |
| 13 | `scripts/verify_v13_web.py`（playwright 1.62.0） | **MOCK_VERIFIED**，api_errors=[] | 拦截与 Fake 场景，不能代替真实模型验收 |
| 14 | 证据索引独立全量校验 ×2 | **PASS**（listed 972 / matched 972 / self_listed=false） | 校验器与本构建脚本零共享代码（`final-closure-v4/verify_evidence_index.py`） |
| 15 | `git diff --check` | exit=0 | 工作树差异仅 3 个修复文件 |
| 16 | 端口/进程释放 | PASS | 7 端口无监听；本轮进程全部退出；剩余 2 个 node 为 ZCode 自身服务 |

两个 warning:Starlette/AnyIO 弃用警告 + 既有 SQLAlchemy NULL 主键查询警告（与审查基线一致,无新增）。
