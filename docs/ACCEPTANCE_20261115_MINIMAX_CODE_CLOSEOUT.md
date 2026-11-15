# MiniMax Code 遗留收尾验收报告（2026-11-15）

- 执行：Qoder 主会话，接手 MiniMax Code 额度耗尽后的半路工作。
- 口径：用户裁定「梳理遗留 → 复核复现 → 确认 Bug/阻滞 → 制订方案 → 自主修复」；大胆求证，小心实践。
- 方案：`docs/CLOSEOUT_PLAN_20261115_MINIMAX_CODE.md`（随 chore PR #43 入库）。
- 基线：接手时 `origin/main@3a8d2f7`；收尾时 `origin/main@e78abe0`（8 个 PR 全部合并）。

## 1. 复核复现结论（先求证，后动手）

| 项 | 级别 | 求证方式 | 结论 |
| --- | --- | --- | --- |
| D-R4-01 人名对照表保存必然失败 | P1 | 修复自带 4 条合同测试在**未修复代码**上实跑全红，首条即 `422 TERMINOLOGY_ROW_INVALID` | 真 Bug。PR #39 撤下时声称「以并行会话 1cf5811 为准」，但该提交只在本地分支，从未推送/合并——两边都以为对方修了 |
| D-R4-03 审计实体列只有 8 位 id | P2 | PR #42 CI 与合并状态 | 真阻滞：代码完成但与 #41 冲突（DIRTY），L2 悬空 |
| PR #38 素材 R4 复测报告 | 阻滞 | CI 失败日志 | 旧基线 F811，main 已由 #36 修复，rebase 即解 |
| R4-01 出图入口三种缺失折叠 | P2 | 读前后端代码 | 真 Bug：后端已发 `t2i_runtime.blockers`（7 种具名码）与 `graph_report`，前端从不渲染 |
| R4-02 角色重名拒绝裸码串 | P2 | 读错误链 | 真 Bug：`CHARACTER_NAME_ALREADY_IN_USE:<名字>` 前缀形式命不中精确匹配码表 |
| D-R4-05/06 空态/审计刷新 | P3 | 读组件 | 属实 |
| 文档遗留 | — | 工作区核查 | 红线更新 2 处未提交、R3 验收 2 份未跟踪 |

## 2. 修复与合并清单（8 个 PR）

| PR | 内容 | 回归要点 |
| --- | --- | --- |
| #43 | 文档遗留入库（红线更新、R3 验收、收尾方案） | 纯文档 |
| #44 | **P1 术语保存**：cherry-pick `1cf5811`（main.py dump + services.py 归一化 + 4 条合同测试） | 修复前 4 红 → 修复后 22 绿；护栏：两层同撤 → 4 红 |
| #42 | 审计实体名 rebase 解冲突（#41 事件码 + #42 实体名都保留） | test_audit 5 项 + 前端 12 项绿 |
| #38 | 素材 R4 复测报告 rebase | CI 绿 |
| #45 | R4-02 重名拒绝可读化（describeDetail 前缀处理 + 码表兜底） | apiError 6 项绿；护栏：撤前缀处理 → 对应用例红 |
| #46 | R4-01 出图阻塞具名化（只渲染服务端已报告的结论） | 新增 5 项 + 既有素材测试合计 30 绿；护栏：撤清单渲染 → 4 红 |
| #47 | P3 空态说明 + 进交付视图自动刷新审计 | 新增 3 项绿；护栏：撤修复 → 3 红 |
| #48 | 冒烟跟进：术语/字幕审计实体列显示版本号 | test_audit 6 项绿（新增走真实 HTTP 端点） |

所有代码 PR 的 CI（L1 quick / L2 integration / Frontend build）均绿后合并；
合并使用仓库所有者 bypass（ruleset `main-owner-only-push`，AGENTS.md 记载的路径）。

## 3. 全量回归（本机实跑）

- **后端 8 分片全量**（241 个测试文件，按文件均分，`CODEBUDDY_SAFE_DELETE_ENABLED=0`、`env -u NO_PROXY`，仓库根执行）：
  首轮 7 分片 exit 0；ad 分片 1 例失败（`test_weight_verification_receipts::test_same_length_wrong_bytes_never_count_as_verified`，
  并行下核验缓存干扰）。该模块单独跑 6 项全绿，ad 整片复跑 exit 0、20 模块无 FAILED/ERROR。
  本轮改动（术语/审计/前端文案）与权重核验无交集；失败按并行抖动如实记录，未改任何测试预期。
  日志：`/tmp/shine-closeout-chunk-*.log`、`/tmp/shine-closeout-ad-rerun.log`。
  （第一次后台执行因日志路径拼接错误未真正运行，已发现并重跑；旧日志不作证据。）
- **前端全量**：`npx vitest run --no-file-parallelism` 74 文件 / 397 项全绿（P3 分支，含全部新增测试）；
  合并后 main 的前端构建 `npm.cmd run build` 通过；`tsc --noEmit` 通过。
- **Ruff**：`backend/shine backend/tests` 全量通过。
- 定向批次：术语/审计/事件码/人物绑定/分镜/素材中心 6 模块 96 项绿。

## 4. 浏览器冒烟（真实 UI，隔离实例）

实例：`scripts/start_eval_instance.ps1 -DataDir .local-acceptance/closeout-20261115 -ApiPort 8050 -WebPort 5220`
（复用现有依赖，未 npm ci/install；未触碰他人端口与进程；结束后已用 `stop_shine.py` 停止，
8050/5220 实测无监听）。冒烟基线为 `main@edd762d`（#48 之前的 7 个 PR）；
#48 的版本号解析由单元测试覆盖，未重启实例复验。

| 项 | 结果 |
| --- | --- |
| 空库落点导演模式创建页 | ✅ |
| 术语/字幕空态说明（D-R4-05） | ✅ 两处文案如实显示 |
| **术语保存（P1）** | ✅ 填「周砚/周烨」保存成功，「版本 1 · 1 个词条」 |
| 审计进视图自动刷新（D-R4-06） | ✅ 未点刷新即显示 2 条 |
| 事件码中文化（D-R4-02 已合并部分） | ✅ 「保存人名对照表」「创建项目」 |
| 实体名（项目） | ✅ 「项目 · 冒烟收尾验证」 |
| 实体名（术语） | ⚠️ 发现仍回退 id → 跟进 PR #48 |
| 出图阻塞具名化（R4-01，T2I） | ✅ IMAGE_RUNTIME_NOT_BOUND + 中文原因 + 下一步 |
| 缺参考图提示（R4-01，I2I） | ✅ 与能力验证分开说明 |

证据：`.local-acceptance/closeout-20261115/browser_smoke_evidence.json`（Git 忽略）。
限制：内置浏览器面板未打开，pointer/截图不可用，冒烟以真实 DOM 事件驱动（非 API 绕过），未取得截图；
R4-02 重名拒绝的界面表现未冒烟（需文本模型驱动 crew 链），由单元测试覆盖。

## 5. 安全隐患（只报不动）

8000 端口 `python -m http.server`（PID 20348）直接服务仓库根，`.env`/`.git`/`.local-data`
可被本机 HTTP 读取。按团队红线未擅自停止他人进程，**已上报用户，等待处置决定**。

## 6. 诚实边界

- 本轮全部为离线/mock 验证，**未发生真实 H3 渲染，不构成出图或成片验收**。
- 未改 `worker.py` / `preproduction.py`，不触发 `client_review_identity` 重校准义务；
  历史 VLM 正负校准仍未完成，不得声称已校准。
- 遗留未动项：`frontend/node_modules.broken-20261115/` 等 npm 事故残骸、多个测评 worktree
  （`.local-acceptance/*/wt-*`、`.qa-*`）未清理（非本轮产物，清理需用户裁定）；
  8000 静态服务待用户处置。
- 本机多套工作栈并行（8000–8040、5180–5210），本轮未启停任何他人栈。