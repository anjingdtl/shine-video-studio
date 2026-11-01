# Web 并发缺口精确修复（2026-11-01）

本轮关闭独立复验在 `af5d6b6` 上确认的两处并发缺口：风格批准签发前的内容竞态，以及全局工作流名称缺少数据库唯一性保护。只修改这两条合同及其客户端、迁移和回归；没有改动生成、审片、Worker、交付策略或历史证据。

被测代码：`af5d6b6c5e4455a112391337eb205014509f3731` + 本轮工作树，分支 `codex/fix-style-workflow-concurrency`。证据目录 `.local-acceptance/concurrency-fix-20261101-af5d6b6/`。原验收报告与反例保留于 `.local-acceptance/web-acceptance-20261101-af5d6b6/`。

## 风格批准内容

原路径只发送保存请求和 grant；另一页面在 grant 签发前修改草稿后，grant 会绑定对方内容，原页面仍能将其冻结。

现在 Web 的冻结请求携带本次点击确认的完整 `expected_style`。服务器对带 grant 或预生产保护的批准要求此字段；缺失返回 409 `STYLE_APPROVAL_CONTENT_REQUIRED`。在 `BEGIN IMMEDIATE` 事务内读取最新草稿、逐项比较确认内容、消费 grant 并冻结；不一致返回 409 `STYLE_APPROVAL_CONTENT_CHANGED`，不创建版本、不消费授权，也不覆盖其他页面草稿。签发前、签发后和重复冻结均受保护。

兼容边界：原有未受预生产保护、无 grant 的 legacy API 保持原语义；新 Web 即便在 legacy 项目也会传 `expected_style` 并获得相同的事务内内容检查。受保护 API 的客户端必须提供七项确认内容，不能仅凭一个当前服务器草稿的 grant 代替用户内容确认。既有 V1.3 正常请求测试同步使用显式内容；原有未授权、变更和重放拒绝断言保留。

客户端在提交期间锁定七项编辑，失败保留表单；还会核对返回的冻结快照，不能把不匹配结果报告为“与屏幕一致”。跨项目的过期成功回调不向新项目报告冻结成功。冻结历史与旧镜头引用没有原地修改。

## 全局工作流唯一性

保留私有项目的 `(project_id, name)` 唯一约束，新增 `uq_workflow_profile_global_name` 部分唯一索引，只覆盖 `project_id IS NULL`。API 原有 `IntegrityError`→409 路径同时处理全局和私有登记，预查询仅用于提前提示，不能再绕过数据库约束。

新增 Alembic 迁移 `f150a0020001`，在 `f150a0010001` 之后创建索引，不重建表或重写任何档案 ID、图、哈希和任务引用。升级前如果旧库已存在同名全局档案，明确报 `GLOBAL_WORKFLOW_NAME_CONFLICT`，保留全部行和原 revision，要求显式对账；不自动删除、合并或改名。降级本迁移只移除新增索引，保留所有行。

## 验证

| 检查 | 本轮结果 |
|---|---|
| 修复前反例 | 新后端合同回归 5 failed；前端确认内容请求断言 1 failed / 3 passed。全局并发反例四个请求均 201，证明原预检查不够 |
| 定向后端 | 23 passed：风格前后竞态、缺确认内容、重放、并发冻结、私有/全局作用域、迁移正常/冲突/降级、历史保留及 V1.3 E2E |
| 前端 | 20 passed；TypeScript/Vite 构建通过 |
| Ruff | All checks passed |
| 完整后端 | **1315 passed, 1 skipped, 2 warnings in 585.28s**；原始日志 `backend-full.log` |
| 新库迁移 | 正常本地启动器升级至 `f150a0020001 (head)` |
| 真实 HTTP 反例复验 | 缺确认内容 409；签发前竞态 409；相同确认内容正常冻结 201。三轮各 8 个全局同名 POST，每轮 1×201、7×409、仅 1 行 |
| 实际 Web | 独立新项目中保存 A、编辑 B、批准冻结，回填与服务器快照均为 B；没有拦截成功响应 |

命令：

```powershell
uv run --project backend --locked pytest -c backend/pyproject.toml backend/tests -ra
uv run --project backend --locked ruff check backend/shine backend/alembic backend/tests
npm --prefix frontend test
npm --prefix frontend run build
git diff --check
```

新增后端测试使用真实临时 SQLite。全局并发测试让所有请求完成预查询后才插入，防止调度巧合掩盖错误；屏障只同步预插入调用，不阻塞响应序列化。另以无后端注入的真实 HTTP 服务三轮各 8 请求交叉核验。期间修正了一处新测试导入排序，以及测试屏障误同步序列化阶段的问题，没有削弱业务断言。

完整后端包含原 offline E2E、film crew、V1.3 preparation/migrations/E2E。跳过项为 `test_real_managed_startup.py` 的真实 ComfyUI 探针，需要 `SHINE_REAL_COMFY_PROBE=1` 和本地安装；两项 warning 是 Starlette/AnyIO 弃用提示及既有 `input_assessment.py` 空主键提示，不计为影片验收。

Web 实测截图 `web-style-confirmed.png` 已人工查看；`web-style-server-check.json` 记录服务器冻结快照与屏幕 B 内容一致。测试结束后以项目停止脚本关闭本轮隔离 API、Worker、Web，确认 8057/5227 无监听，未停止其他服务。修复完成时留在本地修复分支；后续按用户指令封装并交付主分支，见 [重封装记录](REPACKAGE_20261101.md)。

## 已确认的独立检查边界

额外运行 `alembic check` 仍失败：缺 `ix_delivery_orders_project_id`、`ix_delivery_orders_series_id` 两个索引。为判断是否由本轮引入，使用 `git archive af5d6b6 backend` 导出的原始代码，在独立新库上重复 upgrade/check，得到**相同两项差异**。本轮新增全局索引与元数据匹配，没有引入新的差异。该交付表既存差异未纳入这次两处并发修复，不能把 `alembic check` 写为通过。

证据：`alembic-check.log`、`baseline-upgrade.log`、`baseline-alembic-check.log`。这与正常 Alembic 升级、回退和数据保留测试通过是不同检查。

## 数据与验收边界

- 本轮数据只在新的隔离目录，HTTP 凭据是公开测试凭据，未操作真实主资产或生产库。
- `scripts/start_dev.ps1` 是开始前已有未跟踪文件，未修改或纳入本轮。
- 本轮不运行 GPU、真实 LLM 或真实影片质量验收；此前影片 `pending_review`、音频 `WRONG_LINE`、干净 Windows 未验收和人工批准边界均保持。
- 本报告的关闭结论只针对这两处并发缺口，不将其升级为项目/影片完整全 PASS。
