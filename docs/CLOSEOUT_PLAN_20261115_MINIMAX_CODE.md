# MiniMax Code 遗留收尾建设方案（2026-11-15）

- 发起：用户裁定「梳理遗留 → 复核复现 → 确认 Bug/阻滞 → 制订方案 → 自主修复」，口径：大胆求证，小心实践。
- 执行：Qoder 主会话（接手 MiniMax Code 额度耗尽后的半路工作）。
- 基线：`origin/main@3a8d2f7`（PR #41 合并后）。

## 1. 复核复现结论（均已求证）

| 项 | 级别 | 求证 | 结论 |
| --- | --- | --- | --- |
| D-R4-01 人名对照表保存必然失败 | P1 | 修复自带 4 条合同测试在未修复代码上实跑全红，首条即 `422 TERMINOLOGY_ROW_INVALID` | 真 Bug；修复提交 `1cf5811` 仅在本地分支 `fix/terminology-save-contract-20260715`，未推送未合并；PR #39 撤下说明声称「以并行会话为准」，但并行实现从未进主干 |
| D-R4-03 审计实体列只有 8 位 id | P2 | PR #42：L1/前端绿、L2 悬空、合并状态 DIRTY | 真阻滞；与 PR #41 在 AuditPanel/api 上冲突 |
| PR #38 素材 R4 复测报告 | 阻滞 | CI 日志：`test_node_pack_gate_semantics.py:58 F811` | 旧基线问题；main 已由 #36 修复，rebase 即解 |
| R4-01 出图入口三种缺失折叠成一句话 | P2 | 后端 `_capabilities` 已返回 `t2i_runtime.blockers`（IMAGE_RUNTIME_NOT_BOUND / IMAGE_RENDERER_RECIPE_MISSING / IMAGE_MODEL_ROOT_NOT_CONFIGURED / IMAGE_MODEL_FILE_MISSING / IMAGE_MODEL_SIZE_MISMATCH / IMAGE_MODEL_HASH_MISMATCH / IMAGE_GRAPH_NOT_VERIFIED）；前端类型含 `t2i_runtime` 但从不渲染 | 真 Bug；纯前端可修 |
| R4-02 角色重名拒绝裸码串 | P2 | `api_film.py:1945` 抛 `CHARACTER_NAME_ALREADY_IN_USE:<名字>`；`describeDetail` 精确匹配码表命不中前缀形式；`StoryboardPanel.applyBinding` catch 直接 `String(e)` | 真 Bug（代码级证据链完整） |
| D-R4-05 术语/字幕无空态说明 | P3 | 读组件确认 | 属实 |
| D-R4-06 审计进视图不自动刷新 | P3 | `loadAudit` 仅在 refreshAll 与手动按钮触发 | 属实 |
| 文档遗留 | — | AGENTS.md / EVAL_TEAM_MINIMAX_BOT.md 红线更新未提交；两份 R3 验收报告未跟踪 | 属实 |

**安全隐患（只报不动）**：8000 端口 `python -m http.server`（PID 20348）直接服务仓库根，
`.env`/`.git`/`.local-data` 可被本机 HTTP 读取。按团队红线不擅自停他人进程，已上报用户。

## 2. 修复清单与顺序

每项独立分支 + PR + 定向回归；护栏口径：撤掉修复对应用例当场红，恢复后绿。

1. 文档先行（chore）：红线更新、R3 验收两份、本方案。
2. D-R4-01（P1）：cherry-pick `1cf5811`（main.py dump + services.py 归一化 + 4 条合同测试）到最新 main 新分支；回归 `test_terminology_save_contract.py` + `test_terminology_and_subtitles.py`。
3. PR #42：rebase 解冲突（#41 事件码与 #42 实体名都保留）；回归 `test_audit.py` + 前端 auditEntityLabel/auditEventLabels。
4. PR #38：rebase 到最新 main（纯文档）。
5. R4-02：`describeDetail` 处理 `CHARACTER_NAME_ALREADY_IN_USE:` 前缀 → 中文说明 + 冲突名字 + 下一步；新增 vitest。
6. R4-01：图片工坊渲染服务端真实 blockers（具名码 + 中文原因 + 下一步动作），按模式补「缺参考图」提示；只展示服务端已报告的结论，不发明；新增 vitest。
7. P3：术语/字幕空态说明；进入交付视图自动加载审计。
8. 收尾：前端全量 vitest（--no-file-parallelism）/tsc/build；后端 8 分片全量（CODEBUDDY_SAFE_DELETE_ENABLED=0、env -u NO_PROXY）；Ruff；隔离实例浏览器冒烟；验收报告。

## 3. 约束自查

- 不改 `worker.py` / `preproduction.py` → 不触发 `client_review_identity` 重校准义务。
- 不执行 `npm ci` / `npm install`；不抢占他人端口（当前 8000–8040、5180–5210 均有栈）；不停他人进程。
- 浏览器冒烟用隔离数据目录 `.local-acceptance/closeout-20261115/` 与空闲端口，结束即停。
- 全程中文；离线/mock 边界如实声明，不构成真实 H3 出片验收。