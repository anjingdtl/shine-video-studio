# 导演模式 V2.0 架构收束 · 工程实施报告（2026-11-13）

> 规范来源：`docs/SHINE_DIRECTOR_AUTOPILOT_V2_ARCHITECTURE_CONVERGENCE_PLAN_20261113.md`
> 实施分支：`feature/director-autopilot-v2-20261113`（本地，未推送、未开 PR、未合并 main）
> 独立 worktree：`F:/ClaudeWorkSpace/projects/shine-autopilot-v2-20261113`
> 基线：`ce479318bf21c536225691a08e765c875ede0f95`（origin/main，含第一轮收束 3a532cd）
> 口径：工程验证 / 真实 H3 出片 / 人工艺术验收 三者分开，不互相代签。

## 0. 一句话结论

V2 把导演模式从「七个阶段各有一套重复入口」收成「一份只读投影 + 一条主路径」：项目与分集状态只有一个聚合来源，素材需求只从已确认镜头派生并按真实工作流槽位区分必需/可选，开拍只有一个显式动作，素材提交与开拍请求都用确定性身份重放。后端新增 11 项 V2 专项回归，前端全量 270 项保持通过（断言随入口收敛更新，未删测试）。

## 1. 基线与隔离

- `git fetch origin` 后核对：本地 main 落后 origin 6 个提交（含第一轮 streamline 3a532cd 与报告 ce47931）；V2 文档分支 `origin/docs/director-autopilot-v2-20261113` 仅新增两份文档，无代码。
- 以 `ce47931` 建独立 worktree 与功能分支，主工作区与三个既有 worktree 均未改动。
- V2 文档两份随分支纳入（`docs/`），作为规范来源而非运行代码。
- 真实穿测使用隔离数据副本 `.local-acceptance/v2-20261113`（由 `witch-main-20261111` 复制，714 MB，含 ComfyUI 受管配置、LLM 密钥、两个历史项目），原始生产数据未写入。
- 后端测试沿用主工作树 venv 并复制为本 worktree 的 `backend/.venv`（修正 editable `.pth` 指向），保证 `import shine` 解析到 V2 代码。

## 2. WP 完成表

| WP | 目标 | 状态 | 主要落点 |
| --- | --- | --- | --- |
| WP0 | 冻结基线、复验真实旅程 | 完成 | 基线核对、隔离数据、环境重验（1281 节点 / 4 个 Ref2VA 方案 READY） |
| WP1 | 统一导演旅程投影与按集状态 | 完成 | `backend/shine/director/overview.py`（新）、`api_streamline.py` 两个只读端点 |
| WP2 | 裁减重复入口、保留专业能力 | 完成 | `App.tsx`、`StreamlinePanels.tsx`、`DirectorShootPanel.tsx`（新） |
| WP3 | 角色/风格/镜头需求合同归一 | 完成 | `backend/shine/director/requirements.py`（新）、`director/streamline.py` |
| WP4 | 素材中心一次交接与稳定幂等 | 完成 | `api_streamline.py` commit/requirements 端点、`materialprep/service.py` |
| WP5 | 一处开拍、按集调度 | 完成（工程） | `streamline.start_director_production`、`/director/production/start` |
| WP6 | 成片衔接与验收视图归一 | 完成（工程） | `DirectorProductionRunPanel.tsx`、`DeliveryPanel.tsx` |
| WP7 | 兼容、全量回归、真实两集 | 部分完成（真实出片见验收报告） | 测试报告与验收报告 |

## 3. WP1：一份只读投影

新增 `backend/shine/director/overview.py`：

- `build_overview(db, project, package_id=None, run_id=None)` → 项目级：计划版本、风格版本、分集数/已确认/可拍/已交付、镜头与通过数、每集节点、唯一下一动作、当前包与运行。
- `build_episode_overview(...)` → 单集：分镜落镜/确认、必需素材缺口、包就绪与阻塞、运行进度、成片指针、下一动作。
- 状态机（每集一个）：`WAITING_STORYBOARD / STORYBOARD_RUNNING / STORYBOARD_BLOCKED / SHOTS_TO_CONFIRM / MATERIAL_MISSING / READY_TO_PACKAGE / READY_TO_SHOOT / SHOOTING / REVIEW_READY / DELIVERED`。

设计约束（与 V2 §4.1/§5 对齐）：

1. **不写任何状态**：所有字段来自既有台账（`DirectorEpisodePlan`、`AgentTaskRecord.trace`、`Shot`、`AssetRequirement`/`Asset`、`DirectorPackageVersion` + `package_readiness`、`ProductionRun(Episode)`、`delivered_artifact_id`）。两次读取结果一致，前端刷新不能制造进度。
2. **按集隔离**：`package_readiness` 的 blocker 按 `episode_id` / `shot_id` 归属过滤；包级 blocker（如风格被替换）才作用于所有集。
3. **素材缺口按实体聚合**：同一缺失主素材被 200 镜引用时是一行「罗兰 等 62 镜缺主素材」，不是 200 行。
4. **下一动作只指向能推进的工作**：优先级 `SHOTS_TO_CONFIRM → MATERIAL_MISSING → READY_TO_PACKAGE → READY_TO_SHOOT → REVIEW_READY → STORYBOARD_BLOCKED → ...`。阻塞集是并列待办，不是全局停止。
5. **技术完成 ≠ 艺术认可**：有 `delivered_artifact_id` 但集行未 `DELIVERED` → `REVIEW_READY`。

端点（`api_streamline.py`）：

- `GET /api/v1/projects/{project_id}/director/overview`
- `GET /api/v1/projects/{project_id}/director/episodes/{episode_id}/overview`

旧 `/journey` 与全部专业端点保持不变（回归覆盖）。

## 4. WP2：一条主路径

导演七步保留，但每一步只剩一个业务入口：

| 阶段 | 之前 | 现在 | 专业模式 |
| --- | --- | --- | --- |
| ① | `ScriptEntryPanel` + `FourStagePanel`（折叠） | 仅 `ScriptEntryPanel` + 一句说明 | `FourStagePanel` 在「生产板块」 |
| ② | `StoryboardBookPanel` | 不变（唯一） | 旧 `episode-plans` 入口 |
| ③ | `EpisodeStoryboardPanel` + `StoryboardPanel`（折叠） | 仅 `EpisodeStoryboardPanel` | `StoryboardPanel` |
| ④ | `ShotMaterialsPanel` + `MaterialPreparationPanel` + `AssetPanel` | `ShotMaterialsPanel` + 轻量「项目素材」区块（计数 + 素材中心入口） | `MaterialPreparationPanel` + `AssetPanel` |
| ⑤⑥ | `DirectorProductionRunPanel`(package/run) + `ProductionSeriesPanel` + `ShotPanel` | `DirectorShootPanel`（唯一主按钮） | `ProductionSeriesPanel`、`ShotPanel`（「单镜头制作与任务明细（高级）」）、`ProductionPanel` |
| ⑦ | `DirectorProductionRunPanel`(post) + `DeliveryPanel` | 审片卡 + 文件/历史导出（职责分开） | 同 |

新增 `frontend/src/components/DirectorShootPanel.tsx`：

- `EpisodeCards`：每集状态卡（状态、场/镜数、缺口、下一动作按钮），复用于导演首页与⑥。
- `DirectorShootPanel`：集选择（默认只选可拍集）、本机 Ref2VA 方案选择、唯一主按钮「确认拍摄方案并开始真实制作」、运行进度、暂停/继续/核对/安全续跑、按集进度。
- 防重：`submitting` ref + 主按钮在提交期间禁用；选择状态显式（用户改过就不再被刷新覆盖）。

导演首页（`#d/projects`）新增「当前作品 · 全剧进度」区块：集数/已确认/可拍/已交付 + 唯一下一步 + 集卡。

## 5. WP3：需求只从已确认镜头派生

新增 `backend/shine/director/requirements.py`，`streamline.derive_shot_materials` 改为其调用方：

- **来源**：已确认分集计划 → 场次 → 已落镜的 `ShotSpec`；`MaterialEntityCatalog` 工件（存在时）提供规范名/别名/变体；项目已批准锁定的主素材；`shot.bindings`；`shot.required_asset_ids`。
- **零新增模型调用**：catalog 只读复用（basis 未变不重算），不重新分析剧本。
- **别名合并**：catalog 的 `aliases` → `character_reference_key` 归一，避免「罗兰 / Roland」被拆成两个主素材。
- **家族**：`character`（主体）、`scene`（场景，场次标题+首句）、`prop`（道具：镜头按 id 承诺的、catalog 的关键道具、beat 中出现的已登记道具）。**voice 不建需求**：当前 H3 合同原生说对白、素材中心无 voice 槽位，建「必需声音参考」是渲染器兑现不了的承诺（V2 §7.2「没有配置实际素材槽位的需求不得冒充必需项」）。
- **必需/可选按真实槽位**：`reference_capacity(profile)` 从已绑定 profile 的 `parameter_map` 数 `reference_subject_N / reference_picture_N / reference_audio_N`。角色主体在声明了 subject 槽位时为必需；场景/道具只有在镜头按 id 承诺该素材时才是必需，其余为可选。
- **回写**：`AssetRequirement.payload` 增加 `used_shot_ids / used_episodes / critical / optional / variants / matched_master_id`；`optional` 项允许 `skip`，`critical` 项不允许（`service.confirm` 原有校验）。

同时修正「缺图由 H3 自行生成」的错误承诺：

- `journey.py` 素材阶段摘要 → 「N 项必需素材缺失，只影响引用它的镜头」。
- `api_film.py` 落镜提示 → 「这一镜缺少已确认的参考图；开拍前需要补上，或改为不使用该参考」。
- 前端三处文案与「跳过（可选项，不影响生成）」。

## 6. WP4：一次交接与稳定幂等

前端 `ShotMaterialsPanel` 重写：

- 读取：`GET /director/materials/requirements`（只读台账，不再自动 derive）。
- 生成：`POST /director/materials/derive`（显式动作）。
- 提交：`POST /director/materials/commit` —— 确认决策 + 排队候选，**一个请求**。
- 移除 `Date.now()` 型幂等键（原 `director-${Date.now().toString(36)}`）。

后端 `commit_director_materials`：

- 请求身份 = `graph_hash(project, plan_revision_id, expected_snapshot, sorted decisions)`；批次键在其后追加 `graph_hash(requirement_ids)`。
- **回执台账**：成功提交写 `DIRECTOR_MATERIALS_COMMITTED` 审计（含 request_key / confirmation / batch run_id）。重放同请求体先查回执，直接返回同一 confirmation + batch（`replayed=true`），不重复确认、不重复出图。
- 部分失败：确认已提交但批次被拒时返回 `batch_error`，不谎称整体失败。
- `GET /director/materials/requirements` 支持 `episode_ids` 过滤。

## 7. WP5：一个开拍动作

`streamline.start_director_production(db, project, settings, episode_ids, workflow_profile_id)`：

1. **按集校验**：从 `build_overview` 读每集状态；未就绪集返回 `409 EPISODE_NOT_SHOOTABLE` + 逐集明细，不影响其他集。
2. **包复用**：找 `status=CONFIRMED, stale=false` 且**集集合完全等于所选**的包；没有才建新包（`build_package(episode_ids=...)`，Ref2VA 时 `fill_missing_character_bindings`）。
3. **预检**：新包 `readiness.ready=false` → `409 DIRECTOR_PACKAGE_NOT_READY` + blockers，包保留可见，不建 Run。
4. **确认 + 开拍**：`confirm_package` → `create_run`，幂等键 = `director:graph_hash(project, package.manifest_sha256, sorted episodes)`。重复点击返回同一 Run（`created=false`）。
5. 审计 `DIRECTOR_PRODUCTION_STARTED`。

端点：`POST /api/v1/projects/{project_id}/director/production/start`。

## 8. WP6：成片视图

- 审片卡（`DirectorProductionRunPanel` post 模式）每集补充：成片文件 SHA 前 10 位、字幕版本（`post_contract.subtitle_revision` 或「无字幕轨」）、「历史版本见成片库」。
- `DeliveryPanel` 标题改为「成片文件与导出（历史版本）」，与审片卡职责分开。
- 未新建装配/QC 流水线：仍由既有 Run/EDL/字幕/技术 QC 生成 `delivered_artifact_id`。

## 9. 兼容与边界

- 旧项目：`overview` 对无计划/无镜头项目返回空集与 `next_action`，不抛错；`journey` 端点未改。
- 专业模式：四阶段、素材分析、单镜提交、手动排产、任务诊断全部保留（App.tsx 生产板块）。
- 未新建表、未改 schema（alembic head 仍为 `f180c0020001`），只读投影不落库。
- `worker.py` / `preproduction.py` / quality client 零改动（`client_review_identity` 不受影响）。
- ComfyUI 仍要求环回；`SHINE_ALLOW_REMOTE_COMFYUI` 未使用。
- 未自动晋升任何候选；候选仍走 one-use grant + promote + assignment。

## 10. 已知遗留

1. 前端内置浏览器无法操作文件选择器，「上传剧本」入口的真人穿测改用①页文本入口（详见验收报告）；上传链路本身由既有测试覆盖。
2. 真实 H3 双集出片结论见 `ACCEPTANCE_DIRECTOR_AUTOPILOT_V2_20261113.md`；本报告不替代该结论。
3. `derive` 的 scene/prop 需求依赖场次标题与 catalog；无 catalog 时场景名取场次标题，可能比人工命名更朴素（不臆造）。
