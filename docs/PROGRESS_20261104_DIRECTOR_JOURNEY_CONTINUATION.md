# 导演模式拟人化穿测续作报告（2026-11-04）

**结论：PARTIAL / NOT ACCEPTED。** 代码路径上的阻塞已修复并经测试，真实 LLM 全链已在工作台
真实跑通（5/5 产物 + 仲裁，MiniMax-M3.1-Flash），但连续生产被两个**真实数据缺口**正确阻断：
本项目没有已批准的 voice master、没有登记并验证的 H3 Ref2VA 工作流。这两项是用户输入，
代码不得伪造；在补齐之前，实际 H3 出片、合成、播放导出保持未验证。不能把本轮任何结果当成
验收通过或发布。

## 1. 现场核对（只读）

- HEAD = `ec8cdeb2aefdc3570bb25bdc6d2290e8b0d1ed05`（codex/workbench-crew-handoff-fix），
  工作树除用户未跟踪剧本外干净。
- 栈状态：交接后（00:28 记录的 8000/5173 栈）全部已死；存活的 worker 19672 是 11-03 22:34
  旧 8110 栈遗物（代码早于最终提交）。已按最新记录的规范命令重建栈：API 8000 / worker /
  web 5173，旧 worker 已停止（当时无活动租约、无 RUNNING 任务、e3a4d22a 之后无新任务）。
- `2eb7d3cf`（ABANDONED/UNKNOWN）与 `e3a4d22a`（NEEDS_REVIEW/LLM_TIMEOUT/
  external_outcome=UNKNOWN）记录与交接一致，五产物（0094faf6/82b4266d/0bb14297/1b78d4ac/
  0da0ab88 + 两份仲裁）全部保留。
- 服务商只读探测（GET /v1/models，不提交生成）：MiniMax 官方 API available=true、
  model_listed=true。这不改变 e3a4d22a 的 UNKNOWN 判定。

## 2. 修复的真实阻塞（每个都有回归测试）

1. **监控区幽灵错误**：`system/status.last_error` 会取已死 worker（11-03 08:25）的历史
   错误并显示为当前系统错误（"job: HTTPException"）。修复：只从在线 worker 取 last_error
   （`shine/main.py`），测试 `test_worker_health.py`。
2. **空凭据被 403**：HTTP 栈把 `"Bearer "` 修剪成 `"Bearer"`，`removeprefix("Bearer ")`
   不匹配而落入口令校验，个人桌面 local-user 路径在 UI 上不可用。修复：`api_operator.py`
   容忍两种形态（空凭据=local-user，非空仍须 ≥32 且匹配预配口令），测试
   `test_local_actions.py`（含被修剪的 "Bearer" 形态）。
3. **弃置按钮门槛过时**：FilmCrewPanel 要求凭据 ≥32 字符才能弃置远端未知响应，而个人桌面
   部署无操作员口令——后端本来就支持 local-user。修复：空凭据放行（非空仍须 ≥32），说明
   文案注明"留空即按本机身份操作"，测试 `frontend/src/test/filmCrewAbandon.test.tsx`。
4. **弃置请求 422**：grant 响应的 `expires_at` 被整体展开进 abandon 请求体，Strict schema
   拒绝额外字段（"expires_at：Extra inputs are not permitted"）——弃置按钮自上线起从未成功
   过。修复：只发送 grant_id/snapshot/evidence，前端测试断言请求体字段集合。

另修复（产品缺口，仅记标记不做回放）：

5. **`local_interruption_verified` 从无写入方**：替换路径要求该标记，但代码库中无人写入，
   上一会话靠一次性脚本直改数据库。修复：`abandon_remote_response` 在弃置成功时写入
   `local_interruption_verified=true` 与证据（无活动租约、非 RUNNING、UNKNOWN），测试断言
   加进 `test_film_unknown_outcomes.py`。e3a4d22a 是修复落地前弃置的，按上一会话先例做了
   一次**可审计补正**（audit 事件 `REMOTE_AGENT_RESPONSE_ABANDON_VERIFICATION_COMPLETED`，
   17:43:39），未重放任何远端请求。

## 3. 真实 UI 旅程记录（全部通过 Web 界面操作）

- 环境检查向导全绿：真实制作环境可用（模型哈希校验通过、ComfyUI 与 41GB 权重核验）。
- 分镜页：读取编剧对照 → 人物绑定 6/6 一致（entity_id 全对上）→ 勾选同意 → 弃置
  e3a4d22a（专业模式弃置按钮，确认框+说明，audit `REMOTE_AGENT_RESPONSE_ABANDONED`）→
  “检查并自动继续导演” → “开始新一轮导演”。
- **真实链路**：01:43–01:46（UTC 17:43–17:45）真实 MiniMax 调用完成全链：摄影/场记/剪辑/
  两轮仲裁 + DirectorialPlan 0d1247cd，5/5 产物 + 仲裁全部 SUCCEEDED（HTTP 200，服务商
  代码 0）。旧五产物与历史记录全部保留；自动重放被拒（AGENT_REQUEST_REPLAY_BLOCKED），
  未发生盲重放。
- 中文报告：冲突与裁定全部中文渲染，含"历史报告为英文时折叠原文"机制；5/5 计数、来源链
  （ScriptBreakdown 3c8b9ac8 → DirectorialPlan 0d1247cd → CameraPlan 70cef0b8 →
  ContinuityReport 88c3c56a → EditIntentPlan ebe46b32 → ContinuityArbitration d3927f32）
  在 UI 可见；8 镜/3 段/15 秒分组保留（4.3/5.1/5.6 秒）。
- 监控区 `job: HTTPException` 已消失；生产门禁如实显示"还没有选定生成方案……请到制作环境
  登记并验证 H3 工作流"。

## 4. 剩余两个真实数据缺口（不是代码缺陷，代码不能替用户补）

1. **无 voice master**：仲裁明确：seg01/seg02/seg03 的 reference_set_status
   policy_compliant=false，必需角色 voice 缺失，无已批准 voice master 可绑定
   reference_audio_1。需要用户上传/批准声音素材。
2. **无本项目工作流登记**：workflow_profiles 中本项目零条目；其他项目的 Ref2VA 配置不能
   按项目规则跨项目套用。需要用户在制作环境登记并验证本项目的工作流（含
   reference_audio_1 槽位映射）。

在这两项补齐前，"应用为正式分镜"被 NEEDS_REVIEW 冲突正确阻断，连续生产、真实 H3 渲染、
合成、播放导出**保持未验证**。

## 5. 已知边界（如实记录，不声明通过）

- "AI 修复并重新检查"按钮对本项目当前状态返回 AGENT_REQUEST_REPLAY_BLOCKED（已弃置的
  修订形状任务 + 仲裁续跑的 lineage 校验相互咬合）。曾实现过一个守卫扩展（接受修订形状），
  因无法在会话内拿到一致的回归验证，**已完整回退**；有界修复机制本身由
  `test_storyboard.py` 的 conflict-repair 系列测试覆盖，且替换轮（真实调用）已完整跑通。
  这条 UI 路径的现场放行留待下一轮专门设计验证。
- "UI 实际继续应用分镜"未发生——不是 UI 缺陷，而是两个数据缺口未解除，NEEDS_REVIEW 门禁
  按设计拒绝应用。
- 连续生产、真实 H3 至少三镜、合成、播放导出：未验证（保持 PARTIAL）。
- 最终完整后端套件在最终代码状态重跑中（`.local-acceptance/backend-full-20261104-final.log`）。

## 6. 测试记录（最终代码状态）

| 检查 | 命令 | 结果 |
|------|------|------|
| 后端定向 | `python -m pytest tests/test_storyboard.py tests/test_film_unknown_outcomes.py` | 44/44 通过 |
| worker 健康 | `pytest tests/test_worker_health.py` | 6/6 通过 |
| 本机操作 | `pytest tests/test_local_actions.py` | 7/7 通过 |
| 前端全套 | `npm.cmd test`（frontend/） | 114/114 通过 |
| 前端构建 | `npm.cmd run build` | 通过（tsc + vite，1.20s） |
| Ruff | `python -m ruff check .` | All checks passed |
| Alembic | `alembic upgrade head`（临时数据目录） | 全链通过，exit 0 |
| 完整后端套件 | `pytest tests -q`（后台，最终代码状态） | ~1492 项全部通过，0 失败，PYTEST_EXIT=0（`.local-acceptance/backend-full-20261104-final.log`） |

## 7. Git

变更未提交，等用户指示。未跟踪文件：用户剧本
`docs/H3_MV_MANSIONS_15S_T2VA_SCRIPT.md`（不提交、不覆盖、不删除）。
修改：`backend/shine/agents/execution.py`、`backend/shine/api_operator.py`、
`backend/shine/main.py`、`backend/tests/test_film_unknown_outcomes.py`、
`backend/tests/test_local_actions.py`、`backend/tests/test_worker_health.py`、
`frontend/src/components/FilmCrewPanel.tsx`、`frontend/src/test/filmCrewAbandon.test.tsx`（新）。
