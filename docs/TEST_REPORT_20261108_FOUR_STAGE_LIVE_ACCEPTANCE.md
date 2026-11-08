# 四阶段流水线：验收修复轮与实测轮测试报告

> 日期：2026-11-08 · 分支：`feature/four-stage-pipeline`（合并 main 前最终态）
> 证据纪律：只写已执行的检查；每项标注精确命令与结果。真实 LLM 与离线验证分级标注。

## 1. 后端（Windows 本机，Python 3.12.13，隔离 tmp 数据库）

```
backend\.venv\Scripts\python.exe -m pytest backend/tests -q -p no:warnings
→ 合并前最终全量：[100%] 全部通过，EXIT=0（1 个预期 skip），2026-11-08 实测
backend\.venv\Scripts\python.exe -m ruff check backend/shine backend/tests
→ All checks passed!
backend\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini check
→ No new upgrade operations detected.（head f180b0020001，本轮无新迁移）
```

验收修复轮新增回归（`backend/tests/test_pipeline.py` 17 项 + `test_creative_output_budget.py` 14 项）：

| 回归 | 证明 |
| --- | --- |
| `test_sending_crash_never_replays_the_provider` | SENDING 意图落库后崩溃 → NEEDS_REVIEW(UNKNOWN)，响应器零调用 |
| `test_persisted_answer_is_adopted_without_a_second_call` | 已落库答案 → 采纳并 SUCCEEDED，响应器零调用 |
| `test_cross_project_artifact_reference_is_refused` | 污染 trace 指向别项目大纲 → 拒绝且零调用 |
| `test_manual_volume_adjustment_and_master_isolation` | 人工边界=新版本+校验链；坏边界/腰斩章节/换母本全部具名拒绝 |
| `test_overlong_serialized_script_is_refused_by_name` | 60K 具名拒绝 |
| `test_oversized_single_chapter_volume_carries_a_warning` | >55K 单章卷计划警告 |
| `test_episode_script_contract_with_continuity_check` | 分集合同钉修订+连续性失配具名 |
| `test_pipeline_overview_names_latest_outline_and_pending` | 概览读模型 |
| `test_task_summary_and_soft_bible_basis` | 进度摘要+蓝图草稿依据+恢复 fence |
| `test_blocked_runtime_never_starves_the_text_queue` | **实测 1/3 卡死的根因回归**：冻结计数器+拒绝运行时 → 归零并可领取 |
| `test_bible_queue_only_reads_this_project_outlines` | **实测抓到的跨项目队列过滤丢失**回归 |
| 预算 +2 | 超窗具名拒绝（NOT_SENT 证据）；网关分类为本地拒绝 |

## 2. 前端（Windows 本机，Vite/Vitest/jsdom）

```
npx vitest run   → Test Files 26 passed (26) / Tests 169 passed (169)
npx tsc --noEmit → 通过
npm run build    → ✓ built
```

新增：`src/test/fourStagePanel.test.tsx`（2 项：分卷确认流、剧本确认携带所见 sha256）。
既有 4 文件回归修复记录：面板被动刷新失败改静默，不再以 report() 打断其它页面交互。

## 3. 真实 LLM 实测（MiniMax M3.1 Flash，内置浏览器拟人操作）

| 测试点 | 结果 | 证据 |
| --- | --- | --- |
| 新建作品 + 步骤条点亮 | PASS | DOM 快照 |
| 面板① 选母本→分卷方案→确认 | PASS | DOM 快照 |
| 大纲提取被同意门禁拦截 | PASS（门禁按设计） | DOM 快照 + t3_consent_block.png |
| 面板「授权并继续」一键恢复 | PASS | DOM 快照（EXTRACT · 0/3 推进） |
| 真实大纲提取完成（3 段+归并） | PASS | 任务 SUCCEEDED（台账） |
| 面板内确认大纲（questions_sha256） | PASS | DOM 快照「大纲已确认」 |
| 动态进度条/阶段信息/恢复按钮 | PASS | DOM 快照 + 截图 |
| 截图（第二次尝试起） | RUNTIME BLOCKED | IAB 截图面 3s 超时，DOM 快照代偿，如实记录 |
| 蓝图生成（真实 LLM） | NOT_RUN（队列过滤修复后未重跑） | — |
| 场次剧本/分镜草案/卷交付 | NOT_RUN | — |

实测真实 LLM 消耗：大纲提取 3 段调用+归并；蓝图队列被守卫拒绝未消耗。

## 4. 发现的缺陷（全部已修复并有回归）

1. 四阶段 API 客户端双重 `/api/v1` 前缀 → 面板被动刷新全 404（`164bf85`）。
2. **Worker 文本队列饿死**：渲染运行时配置变更冻结 3 拍突发计数器，大纲卡 1/3 而 LLM 答案已返回
   （`a0da58c`）——用户报告的问题，根因与修复均经回归验证。
3. 蓝图队列大纲扫描丢失项目过滤 → 跨项目引用被执行守卫拦截（`6c9c098`）。
4. `_authoring_task` 身份对相同 trace 冲突唯一约束 → 时间戳加盐。

## 5. 遗留与边界

- 浏览器实测止步于「蓝图生成前」；T4–T7 NOT_RUN（见进度文档 §3-丙）。
- 渲染运行时 `ACTIVE_RUNTIME_CONFIGURATION_CHANGED` 是此前真实 H3 会话遗留的激活记录与当前
  固定文件不一致所致；按项目规则需经工作台「基础配置」重新检查，本轮未绕过也未清除该记录。
- PR #8 的 CI 随每次推送运行；结果以 GitHub Actions 记录为准，本报告不代替 CI 结论。
