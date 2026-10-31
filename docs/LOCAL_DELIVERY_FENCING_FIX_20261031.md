# 2026-10-31 自动交付边界修复与验证

## 范围与版本

本轮修复复审基线 `81a92f3138cfbbba7444ece820b09e341dbd17a5` 上的两项缺陷：失去租约的 Worker 覆盖 EDL 元数据，以及 64 个未到期装配订单挡住后续可执行订单。被测版本为该提交加本轮工作树修复；验证后按用户指令与本报告一并归档为本地 Git 提交，未推送。

业务代码仅修改 `backend/shine/media/delivery_engine.py`，正式回归修改 `backend/tests/test_delivery_orders.py`；没有新增依赖、迁移或变更质量策略。测试使用隔离数据库和空闲端口，没有启动真实 ComfyUI 或新增真实 LLM/H3 请求。

本轮证据目录：`.local-acceptance/fix-20261031-81a92f3/`（不入 Git）。`tested-source-hashes.json` 与 `code.patch` 标识被测业务代码和测试文件，不把基线 SHA 本身冒充修复后的提交。

## 缺陷与修复

### P1：失去租约后仍提交 EDL 元数据

旧实现将 `edl_payload`、`edl_source`、`edit_rules` 赋给跨装配存在的 ORM 事务。旧 Worker 的完成状态被令牌守卫拒绝后，正常返回仍会自动提交这些脏字段，从而覆盖新 owner 的剪辑证据与更新时间。

修复后准备阶段只读取数据库并构建局部值，读会话在装配前关闭。EDL 三项元数据与 `DELIVERED`、产物关联和版本递增进入同一条受 `(owner,state_version)` 保护的条件写回；失去令牌时既不能写状态，也不能通过旧事务提交元数据。

新增正式回归 `test_lost_claim_cannot_commit_edl_metadata_after_successful_assembly`：A 成功完成 FFmpeg 合成前，B 按过期路径重领取并写入自己的元数据。要求 A 返回 B 的权威状态，B 的 owner、版本、尝试数、三项元数据和更新时间保持不变，订单不挂上 A 的产物，series 不被 A 宣告交付。

### P2：占用订单填满 64 项窗口

旧查询先选择 PENDING/ASSEMBLING，再排序取前 64 项。未到期的占用订单时间不变，会在每轮反复占满窗口，挡住后面的 PENDING 订单。

修复将资格条件置于 LIMIT 之前：仅选择 PENDING，或 `updated_at <= now - 2400s` 的 ASSEMBLING。保留每轮 64 项的有限预算、按更新时间调度和过期恢复，并以订单 ID 稳定处理同时间项的顺序。

新增参数化回归 `test_held_assemblies_cannot_fill_the_scheduling_window[64/65]`：64 或 65 个独立 series 的未到期占用订单均不消耗窗口，后面的正常 PENDING 订单在第一轮完成真实 FFmpeg 测试图样装配；占用订单状态与预算保持不变。

既有重启回归加强为 `attempts == []`：未到期订单不再发起无操作领取；过期后仍须成功恢复。原始审查反例文件与断言均未改动。

## 验证

| 检查 | 本轮实测 |
|---|---|
| 新增正式回归，修复前 | **3 failed**：64/65 队列遮挡与旧 owner 元数据覆盖均复现 |
| 正式交付套件 + 两轮原始审查反例，修复后 | **23 passed / 1 warning**，15.76 秒；含正式交付 19 项、外部原始反例 4 项 |
| 全量后端（含 offline E2E、film crew、V1.3、Alembic 升级） | **1178 passed / 1 skipped / 2 warnings**，495.15 秒，exit=0 |
| `uv sync --project backend --locked` | PASS，42 resolved / 41 checked；本进程使用 PyPI index |
| Ruff：backend/shine、backend/alembic、backend/tests、scripts | PASS |
| `npm --prefix frontend run build` | PASS，TypeScript + Vite，1594 modules；前端依赖与锁文件没有变更 |
| `verify_local_stack.ps1 -ApiPort 18123 -KeepData` | PASS，独立临时数据库完成 Alembic 新库升级及 Mock 调度；不生成视频 |
| `verify_stack_operations.ps1 -ApiPort 18128 -WebPort 15188` | PASS，代理/NoWorker/重复启动拒绝/Worker 重启/停栈 |
| `verify_v13_web.py`（Playwright 1.62.0） | MOCK_VERIFIED，api_errors=[]；包含 Fake/拦截场景，不计真实模型验收 |
| 旧 V3 证据索引独立复核 | PASS，972/972 匹配，索引不包含自身；没有重建或改动历史证据 |
| `git diff --check` | PASS |

主要命令（仓库根目录）：

```powershell
$env:UV_DEFAULT_INDEX = 'https://pypi.org/simple'
uv sync --project backend --locked
$env:PYTHONPATH = (Resolve-Path backend).Path
uv run --project backend --locked pytest -c backend/pyproject.toml backend/tests/test_delivery_orders.py .local-acceptance/audit-20261031-81a92f3/test_followup_review.py .local-acceptance/audit-20261031-d8ae267/test_delivery_review_counterexamples.py -ra --tb=short
uv run --project backend --locked pytest -c backend/pyproject.toml backend/tests -ra --junitxml=.local-acceptance/fix-20261031-81a92f3/full-suite.xml
uv run --project backend --locked ruff check backend/shine backend/alembic backend/tests scripts
npm --prefix frontend run build
pwsh -NoProfile -File scripts/verify_local_stack.ps1 -ApiPort 18123 -KeepData
pwsh -NoProfile -File scripts/verify_stack_operations.ps1 -ApiPort 18128 -WebPort 15188
uv run --project backend --with playwright==1.62.0 python scripts/verify_v13_web.py --screenshot .local-acceptance/fix-20261031-81a92f3/browser.png
```

日志与收据：`before-fix.log`、`targeted-after-fix.log`、`full-suite.log/xml`、`mock-stack.log`、`stack-operations.log`、`browser.log/png`、`historical-index-check.json`。

## 判定边界

两项复审缺陷已关闭；本轮已执行的本地工程检查 **PASS**。全量后端唯一跳过项为 `test_real_managed_startup.py:85`（需要显式设置 `SHINE_REAL_COMFY_PROBE=1` 并指定本地 ComfyUI），如实记录为 **SKIPPED**。两个 warning 分别为既有 Starlette/AnyIO 弃用警告和 SQLAlchemy NULL 主键查询警告，无新增类型。Mock 栈所用 18123/18128/15188 端口在结束后均无监听。

已有真实产物验收维持：run-m 为 all_passed；portrait-h 为 pending_review。没有改写 needs_review、人工审批、历史成片或既有质量门禁。艺术/权属/最终影片确认继续为 PENDING_HUMAN，硬帧锚定和真实视频参考连续性继续为 NOT_RUN。GitHub 状态不在本轮范围。
