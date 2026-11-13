# 导演模式 V2.0 · 完整测试与缺陷修复报告（2026-11-13）

> 口径：工程验证 ≠ 真实出片 ≠ 艺术验收。本报告只陈述可复现的工程测试与本次穿测中真实修复的缺陷。
> 分支：`feature/director-autopilot-v2-20261113`；worktree：`F:/ClaudeWorkSpace/projects/shine-autopilot-v2-20261113`。

## 1. 后端

### 1.1 全量

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 收集 | `pytest backend/tests --co -q` | **2482 selected / 0 deselected**（unit 213 / integration 561 / full 1708） |
| 全量 | `pytest backend/tests -q` | **退出码 0，输出无 FAILED** |

第一轮基线 2470；本轮新增 12 项（见 1.2），合计 2482。

> 说明：本仓库的 `conftest.py` 会在终端摘要位置打印逐模块耗时（`[shine-module]`），因此 `-q` 的 “N passed” 汇总行不会出现；判定以退出码与 `FAILED` 行为准。第一次全量出现 1 项失败（旧文案断言，见 §3.1），修复后第二次全量退出码 0、`FAILED` 计数 0。

### 1.2 新增 V2 专项回归

| 文件 | 数量 | 覆盖 |
| --- | --- | --- |
| `test_director_overview.py` | 5 | 两集部分完成不被全局误判；投影两次读取一致（只读）；缺口按实体聚合；单集端点只答本集；服务层可直接调用 |
| `test_director_shoot.py` | 4 | 一集就绪另一集未就绪时只拍就绪集；同选择重放返回同一 Run；未就绪集为具名 409；未知集 404；导演路径自动钉规则包（幂等、不换版本） |
| `test_director_material_commit.py` | 3 | 同请求体重放返回同一 confirmation + batch、不重复建批次；快照过期 409 MATERIAL_PLAN_STALE；需求读取不触发再次派生 |

### 1.3 改动直接相关的定向回归

| 文件 | 数量 | 结果 |
| --- | --- | --- |
| `test_director_streamline.py` | 16 | 通过 |
| `test_four_stage_reacceptance.py` | 19 | 通过（含文案断言更新） |
| `test_material_derive.py` | 4 | 通过 |
| `test_four_stage_closeout.py` | 18 | 通过 |
| `test_film_crew.py` | 36 | 通过 |
| `test_pipeline.py` | 21 | 通过 |
| `test_runtime_activation.py` | 16 | 通过 |
| `test_v13_e2e.py` | 1 | 通过 |
| `test_offline_e2e.py` / `test_production_series.py` | 见全量 | 通过 |

### 1.4 静态检查

`python -m ruff check backend/shine backend/tests` → 全部通过（新增/修改文件逐一复检）。

## 2. 前端

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 类型 | `tsc --noEmit` | 0 错误 |
| 构建 | `tsc -b && vite build` | 成功（dist JS 925 kB / CSS 204 kB） |
| 全量测试 | `vitest run --no-file-parallelism` | **42 文件 / 270 项全部通过** |

测试执行说明（重要，避免误读）：

- 本 worktree 内直接 `vitest run` 会让 tinypool 的 worker 子进程异常退出（`Worker exited unexpectedly`），与代码无关：同一份 `node_modules`（vitest 2.1.9）在对照 worktree 上同样表现；`--no-file-parallelism` 下稳定。
- 为保证“跑的是本轮代码”，前端全量在**同一套已安装依赖**的对照 worktree 上执行：把本轮 7 个改动文件（`App.tsx`、`api.ts`、`styles.css`、`AssetPanel.tsx`、`DeliveryPanel.tsx`、`DirectorProductionRunPanel.tsx`、`StreamlinePanels.tsx`、新增 `DirectorShootPanel.tsx`）与 9 个测试文件复制过去，`tsc` 与全量 vitest 通过后，用 `git checkout -- frontend/src` 还原对照树（已确认干净）。

### 2.1 随入口收敛更新的断言（未删测试）

| 文件 | 变化 | 保留的强度 |
| --- | --- | --- |
| `directorEntries.test.tsx` | ⑥ 页不再断言“一键开拍…全部 N 个镜头”，改为断言该旧文案消失 + 新“第 6 阶段 · 真实拍摄”出现 + 历史 65/72 不混入；② 从旧 `episode-plans` 入口改为“生成分镜书”四参数入口 | 仍是“范围正确、历史不混入、单一入口”的断言 |
| `directorRun.test.tsx` | 重写为 ⑤ 选集/唯一开拍（POST `/director/production/start`）、未就绪集不可选、⑥ 按集进度、NEEDS_REVIEW 安全续跑 | 覆盖开拍幂等、按集隔离、障碍恢复 |
| `produceFocus.test.tsx` | ⑥ 双折叠 → 唯一入口 + 诊断折叠 | 旧入口消失、新入口存在 |
| `shotDraft.test.tsx` / `modeEntry.test.tsx` | 单镜提交从导演⑥移到专业模式（同后端合同、同字段级错误断言） | 422 字段定位、输入保留、防重复提交全部保留 |
| `batchSelection.test.tsx` / `simpleAssets.test.tsx` | 素材确认/锁定/缩略图从导演④移到专业模式 | 批准→锁定顺序、grant 次数、失败保留状态全部保留 |
| `studioStub.ts` | 新增 `EMPTY_OVERVIEW` 与两个只读端点默认响应 | — |
| `four_stage_reacceptance.py`（后端） | notice 文案断言从“无图片素材将由H3自行生成”改为“缺少已确认的参考图…” | 仍断言“撤销的主素材绝不绑定、required_asset_ids 不含它” |

## 3. 穿测中发现并修复的缺陷

### 3.1 D1（P0）导演路径不钉规则包，③ 直接拒绝 `FREEZE_PROJECT_RULEPACK_FIRST`

- 现象：新作品走 ①大纲 → ②分镜书 后，③“生成分镜”两集都 `REJECTED / scriptwriter / FREEZE_PROJECT_RULEPACK_FIRST`，而七步旅程没有任何“冻结规则包”的入口。
- 根因：`FilmAgentRegistry` 要求 `ProjectRuleLock`，但导演 streamline 路径从未建立；旧四阶段/手工路径各自有调用，导演路径漏了。
- 修复：`streamline.ensure_project_rulepack()`，在 `queue_director_storyboard`、`retry_director_episode`、`start_director_production` 三处首用即钉内置不可变版本；已有锁不覆盖，篡改/无效的锁不静默替换（仍由编译器门禁拒绝）。
- 回归：`test_director_path_pins_the_builtin_rulepack_before_storyboarding`（幂等 + 不换版本）。
- 真机验证：修复后同一项目重试第 1 集，`project_rule_locks` 出现，分镜正常落镜。

### 3.2 D2（P0）重新生成分镜书后无法确认新版本

- 现象：②已确认第 1 版（2 集）→ 改集数重新生成（第 2 版 DRAFT）→ 页面仍显示“第 1 版已确认”，确认按钮不出现，只有“生成分镜书”。
- 根因：`StoryboardBookPanel` 的 `confirmedPlan` 只在挂载时读取一次，且“存在任何 CONFIRMED”就压住确认入口；重新生成不重置该 state。
- 修复：加载时比较版本——存在比 CONFIRMED 更新的 DRAFT 时清空确认态并显示“有一版未确认（第 N 版）”；`generate()` 成功后同样清空。
- 真机验证：刷新后出现提示；重新生成第 4 版 6 集并成功确认。

### 3.3 D3（P1）文案承诺错误：“缺图由 H3 自行生成”

- 位置：`journey.py` 素材阶段摘要、`api_film.py` 落镜 notice、`App.tsx` 三处、`StreamlinePanels.tsx` 跳过选项。
- 修复：统一改为“缺的是已确认的参考图；只影响引用它的镜头 / 可选项可跳过”。
- 回归：`test_four_stage_reacceptance.py` 的 notice 断言同步更新（未放宽“绝不绑定未批准素材”的断言）。

### 3.4 D4（P1）素材需求只覆盖角色

- 现状（V2 前）：`derive_shot_materials` 只看 `ShotSpec.characters` 与对白说话方。
- 修复：`director/requirements.py` 增加场景（场次标题/原文）、道具（镜头按 id 承诺、catalog 关键道具、beat 中已登记道具）、别名合并（catalog aliases）、变体；按 profile `parameter_map` 槽位判定必需/可选；voice 不建需求（无槽位，不冒充必需）。
- 真机验证：④ 清单出现「陈默（人物）· 3 镜 · 已匹配可复用」与「第1场：夜，老城钟表铺内（场景 · 可选）· 跳过」，必需缺项 0。

### 3.5 D5（P0）生成批次用 `Date.now()` 幂等键

- 修复：`/director/materials/commit` 由后端按 `plan_revision_id + expected_snapshot + sorted decisions` 计算 request key，批次键再并入 requirement 集合；成功写 `DIRECTOR_MATERIALS_COMMITTED` 审计作为回执，同请求体重放直接返回同一 confirmation/batch。
- 回归：`test_material_commit_replays_the_same_intent_instead_of_buying_twice`。

### 3.6 未修复 / 环境限制（如实记录）

| 项 | 证据 | 影响 |
| --- | --- | --- |
| 出图模型显存不足 | 三类小样批次 `PARTIAL_FAILED`，3 项均 `IMAGE_RENDERER_MEMORY_INSUFFICIENT`（RTX 5080 16 GB） | “AI 制作候选 → 人工采用 → 回绑”链路本轮无法在本机真实跑通；④ 的复用/跳过/必需判定已真机验证 |
| 内置浏览器无文件选择器 | 无法操作 `<input type=file>` | “上传剧本”入口未做真人点击穿测；①仍用文本入口完成大纲并编辑确认。素材文件上传用同一后端合同（multipart + approve/lock + grant）完成 |
| vitest 并行 worker | `Worker exited unexpectedly`（同版本依赖在对照树复现） | 前端全量改为串行执行，结果 270/270 |

## 4. 回归风险与边界

- 未改表结构（alembic head 仍 `f180c0020001`），无迁移风险。
- `worker.py`、`preproduction.py`、quality client 零改动，`client_review_identity` 不受影响。
- 专业模式的四阶段、素材分析、单镜提交、手动排产、任务诊断均有测试覆盖。
- 详情看 `ACCEPTANCE_DIRECTOR_AUTOPILOT_V2_20261113.md` 的真实出片结论。
