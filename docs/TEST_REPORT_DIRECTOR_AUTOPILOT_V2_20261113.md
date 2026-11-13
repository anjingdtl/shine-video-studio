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
| `test_director_overview.py` | 6 | 两集部分完成不被全局误判；投影两次读取一致（只读）；缺口按实体聚合；单集端点只答本集；服务层可直接调用；**拍第 2 集不隐藏第 1 集成片、也不把第 1 集显示成在拍** |
| `test_director_shoot.py` | 4 | 一集就绪另一集未就绪时只拍就绪集；同选择重放返回同一 Run；未就绪集为具名 409；未知集 404；导演路径自动钉规则包（幂等、不换版本） |
| `test_director_material_commit.py` | 5 | 同请求体重放返回同一 confirmation + batch、不重复建批次；快照过期 409 MATERIAL_PLAN_STALE；需求读取不触发再次派生 |

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
| 全量测试 | `vitest run --no-file-parallelism` | **42 文件 / 273 项全部通过** |

测试执行说明（重要，避免误读）：

- 本 worktree 内直接 `vitest run` 会让 tinypool 的 worker 子进程异常退出（`Worker exited unexpectedly`），与代码无关：同一份 `node_modules`（vitest 2.1.9）在对照 worktree 上同样表现；`--no-file-parallelism` 下稳定。
- 早期为绕开该问题，曾把改动复制到对照 worktree 跑全量；本轮已在本 worktree 内直接 `vitest run --no-file-parallelism` 跑通全量（42/273），不再需要复制，结论直接对应当前分支代码。

### 2.1 随入口收敛更新的断言（未删测试）

| 文件 | 变化 | 保留的强度 |
| --- | --- | --- |
| `directorEntries.test.tsx` | ⑥ 页不再断言“一键开拍…全部 N 个镜头”，改为断言该旧文案消失 + 新“第 6 阶段 · 真实拍摄”出现 + 历史 65/72 不混入；② 从旧 `episode-plans` 入口改为“生成分镜书”四参数入口 | 仍是“范围正确、历史不混入、单一入口”的断言 |
| `directorRun.test.tsx` | 重写为 ⑤ 选集/唯一开拍（POST `/director/production/start`）、未就绪集不可选、⑥ 按集进度、NEEDS_REVIEW 安全续跑 | 覆盖开拍幂等、按集隔离、障碍恢复 |
| `produceFocus.test.tsx` | ③④同页双折叠 → 两个独立页面（`#d/produce` / `#d/materials`）；新增旧深链 `stage=assets` 归并断言 | 旧折叠消失、两页互斥显示、地址规范化 |
| `setupEntry.test.tsx` | 新建作品后自动进入①（建项目入口包装） | 七阶段按钮在建项后出现 |
| `studioStub.ts` | `gotoDirectorPage` 先回项目管理再从作品卡进入（与真实用户一致） | — |
| `shotDraft.test.tsx` / `modeEntry.test.tsx` | 单镜提交从导演⑥移到专业模式（同后端合同、同字段级错误断言） | 422 字段定位、输入保留、防重复提交全部保留 |
| `batchSelection.test.tsx` / `simpleAssets.test.tsx` | 素材确认/锁定/缩略图从导演④移到专业模式 | 批准→锁定顺序、grant 次数、失败保留状态全部保留 |
| `studioStub.ts` | 新增 `EMPTY_OVERVIEW` 与两个只读端点默认响应 | — |
| `directorRun.test.tsx`（新增 2 项） | ⑤ 已交付集默认不勾选、重拍需手勾并提示；⑦ 第 2 集开拍后第 1 集成片仍在册且 `<video>` 指向其交付产物 | 直接对应用户报障 D8/D9/D10 |
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

### 3.5b D6（P1）同一人物两份主素材时派生报「缺」

- 现象：项目里存在两份 approved+locked 的「陈默」主素材（上传 + 早前生成各一份），④ 清单显示「必需，缺」，派生匹配返回 None。
- 根因：`resolve_character_asset` 在候选不唯一时保守返回 None（本意是防止误绑），派生把它当成「没有素材」。
- 修复：不唯一时取**最近确认并锁定**的一份（两份都保留）；无匹配仍返回 None，不编造。
- 回归：`test_master_match_prefers_the_latest_when_one_entity_has_two_masters`。

### 3.5c D7（P1，已修复）第 2 集的场次镜头没有进入派生清单

- 现象：第 2 集已落 1 镜，但④ 重新派生后只出现第 1 集的「陈默」，第 2 集的「穿灰雨衣的女人」与两处场景需求不出现。
- 已修：`_section_apply_evidence` 过去只读「全局最新」的导演分镜任务；一个更新、但没有跑过这一场的任务会把上一轮已落镜的场次抹成未落镜。现在按创建时间倒序逐个检查最近 10 个导演任务，取第一个**真的为这一场产出过镜头**的；都没有时才用最新任务的缺失信息。
- 另一半根因：`materialprep/service.create_draft_plan` 会按 request_hash 复用旧草稿，但旧草稿的需求集合可能只是本次需求的子集（多场次交错 derive 时），于是"复用"给出了一份缺项的清单。现在复用前要求**已存实体集合 ⊇ 本次需求实体集合**，否则新建草稿。
- 真机验证（2026-11-13 追补）：重新派生后清单同时含 `ep-01 陈默（3 镜）` 与 `ep-02 穿灰雨衣的女人（1 镜）`，两项都显示 matched（`a46abd6a…` / `66b61b9e…`）；第 2 集随后成功开拍并出片。

### 3.5d D8（P0，用户报障）拍第 2 集后第 1 集成片消失

- 现象：第 1 集已交付、⑦ 可播放；在 ⑤ 开拍第 2 集后，⑦ 只剩第 2 集，第 1 集成片"不见了"。
- 根因：⑦ 的审片列表来自"当前这一次运行"（`run.episodes`），而第 2 集的新运行只含 ep-02；第 1 集的交付在另一次运行里，于是被整体隐藏。
- 修复：新增 `overview._latest_delivery()` 按集跨运行读最近一次交付；⑦ 列表改为项目级按集投影（`overview.episodes`），且不再依赖 `activeRun` 是否存在；`delivery.run_id` 标明交付来自哪次运行。
- 回归：`test_starting_episode_two_never_hides_the_take_episode_one_delivered`（后端）、`⑦ 逐集成片 > 开始拍第二集后，第一集已交付的成片仍然在册并可播放`（前端）。
- 真机验证：两集同时在册，第 1 集 `<video>` 播放真实画面。

### 3.5e D9（P0，用户报障）⑤ 默认勾选包含已交付的集

- 现象：第 1 集已交付后进 ⑤，勾选框默认把第 1 集一起勾上；点「确认拍摄方案并开始真实制作」等于把已出片的集重拍一遍（重复占用 GPU、覆盖既有交付）。
- 根因：默认勾选集合 = 后端允许开拍的集合（含 `DELIVERED`/`REVIEW_READY`）。
- 修复：默认集合拆为 `pendingShoot`（只含 `READY_TO_SHOOT`/`READY_TO_PACKAGE`）；已交付集仍可手动勾选重拍，但行内显示「勾选即重拍这一集」。
- 回归：`⑤ 续拍下一集 > 已交付的集不默认勾选，拍第二集不会顺手重拍第一集`（断言 POST 只带 ep-02）。

### 3.5f D10（P1，与 D8 同源）每集进度借用无关运行

- 现象：开拍第 2 集后，第 1 集卡片显示「可开拍 · 已通过 0/3 镜」。
- 根因：`_episode_node` 用"当前运行"的 `run_episode` 计算通过镜数；当前运行不含该集时落回 0。
- 修复：新增 `_latest_run_episode()`，本集不在当前运行时回落到它自己最近一次运行；`production.in_current_run` 供 UI 标注「（最近一次运行）」。
- 真机验证：`overview` 现在对 ep-01 返回 `run=bd6c59d5 / COMPLETED / pass=3/3 / in_current_run=false`，对 ep-02 返回 `run=07d83dcf / COMPLETED / pass=1/1 / in_current_run=true`。

### 3.6 未修复 / 环境限制（如实记录）

| 项 | 证据 | 影响 |
| --- | --- | --- |
| 出图模型显存不足 | 三类小样批次 `PARTIAL_FAILED`，3 项均 `IMAGE_RENDERER_MEMORY_INSUFFICIENT`（RTX 5080 16 GB） | “AI 制作候选 → 人工采用 → 回绑”链路本轮无法在本机真实跑通；④ 的复用/跳过/必需判定已真机验证 |
| 内置浏览器无文件选择器 | 无法操作 `<input type=file>` | “上传剧本”入口未做真人点击穿测；①仍用文本入口完成大纲并编辑确认。素材文件上传用同一后端合同（multipart + approve/lock + grant）完成 |
| vitest 并行 worker | `Worker exited unexpectedly`（同版本依赖在对照树复现） | 前端全量改为串行执行，结果 42 文件 / 273 项通过 |

## 4. 回归风险与边界

- 未改表结构（alembic head 仍 `f180c0020001`），无迁移风险。
- `worker.py`、`preproduction.py`、quality client 零改动，`client_review_identity` 不受影响。
- 专业模式的四阶段、素材分析、单镜提交、手动排产、任务诊断均有测试覆盖。
- 详情看 `ACCEPTANCE_DIRECTOR_AUTOPILOT_V2_20261113.md` 的真实出片结论。
