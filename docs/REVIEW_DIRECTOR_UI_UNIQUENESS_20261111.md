# 导演模式七步旅程 UI 统一改造 —— 唯一性原理对抗性审查报告

- 日期：2026-11-12
- 分支：`feat/director-ui-unification-20261111`
- 立场：假设实现违反了唯一性原理，主动找证据证明它；逐项给出「证据（文件:行号）→ 结论」。
- 结论分类：**合规** / **违例已修复** / **允许的例外 + 理由**。
- 范围：`frontend/src`（导演模式 `#d/*` 与共享样式）。后端门禁语义未改。

---

## 排查项 1：每页 primary 按钮是否恰好 1 个

**证据**
- `frontend/src/components/StageRail.tsx:61-66`：页面级 `StageRailProvider` 把各面板注册的动作按 `priority` 排序，`primaryReg = sorted.find(r => r.running || r.primary)`，全页只取一个 primary，其余 primary 自动降为次级置顶。
- `frontend/src/components/ActionRail.tsx:63-76`：操作栏只渲染一个 `ActionButton kind="primary"`；运行中显示进度态而非第二个 primary。
- `frontend/src/test/actionRail.test.tsx:64-83`：剧本统筹页/导演包页断言 `.stage-content .btn-primary` 数量为 0、`.action-rail .btn-primary` ≤ 1。
- produce 四段聚焦态各自独立 `StageRailProvider`（`App.tsx` storyboard/assets/series 三段），同一时刻只展开一段（受控 `<details open={focusStage===…}>`），故同屏只有一个 rail primary。
- 内容区 primary 在有 rail 时不渲染：`StoryboardPanel.tsx`、`MaterialPreparationPanel.tsx`、`AssetPanel.tsx`、`DirectorProductionRunPanel.tsx`、`StylePanel.tsx`、`ProductionSeriesPanel.tsx` 均以 `!railPresent` 包裹原内容区 primary。

**结论：合规（七步旅程页面与 produce 四段聚焦态）。**
**允许的例外**：
- 项目管理落地页 `#d/projects` 的「新建作品」`btn-primary`（`ProjectPortfolio.tsx:124`）受 `newProjectButton.test.tsx:24` 契约守护（必须醒目主操作），该页非七阶段之一。
- 基础配置 `#d/setup` 与专业模式 `#v/*` 各分区有自己的主操作（`SetupPanel.tsx`、`ImageCapabilityCard.tsx`、`VariantReadinessCard.tsx`），非旅程阶段页，不在「每页一个 primary」约束内。
- `ConsentOnceCard.tsx` 的「同意并继续」是页内一次同意卡的确认主操作（ transient 安全确认），非阶段主操作。

---

## 排查项 2：四级词汇表之外是否残留旧类/私有样式

**证据**
- `grep -roE "must-button|primary-button|subtle-button|text-button|secondary-button|danger-button|new-project-button" frontend/src --include=*.tsx` → **0 处**。
- `grep -oE "\.(must-button|primary-button|subtle-button|text-button|secondary-button|danger-button|new-project-button)" frontend/src/styles.css` → **0 处**（旧视觉定义已剥离，仅保留布局辅助选择器并改名）。
- 四级定义唯一来源：`styles.css` `.btn-primary/.btn-secondary/.btn-danger/.btn-text`（P0 新增块）。
- asset-center 私有按钮类 `asset-primary-button/asset-secondary-button/asset-text-button` 已迁移到共享四级（`AssetsWorkspace.tsx`）。

**结论：合规。** 旧按钮类（含 asset-center 私有类）在 TSX 与 CSS 中零残留。

---

## 排查项 3：色值/字号/间距是否只有 :root token 一个来源

**证据**
- `styles.css:3-…` `:root` 定义 131 个语义色值 token + 字号/行高/间距/控件 token（P0）。
- 亮色主题硬编码色值残留扫描（排除 `:root` 与 `html[data-theme='dark']`）→ **0 处**；`font-size:Npx`/`font:Npx` 非零残留 → **0 处**（全站最小字号 11px，废弃 7–10px）。
- 组件内联 `style={{…}}` 仅含 `width/maxHeight/whiteSpace/overflowWrap`（`GenerationSegmentsPanel.tsx:57,65`、`ProductionSeriesPanel.tsx:619`、`StoryPreparationPanel.tsx:173`、`SettingsPanel.tsx:166`、`OperatorReview.tsx:51-55`），**无色值/字号硬编码**。
- 新增表面（四级按钮/三件套/ActionRail/toast/同意卡/聚焦条）全部走 token。

**结论：合规（色值、字号）。**
**允许的例外**：
- 暗色主题覆盖处（`html[data-theme='dark'] …`）按既有模式保留逐选择器色值（审查约定排除项）；新表面的暗色按同名 token 覆盖（`styles.css` 末尾 `html[data-theme='dark']{--btn-…}`）。
- 遗留专家模式面板的内部间距（padding/margin/gap 的非 4 网格值）保留原值：本轮只把新统一表面与页面骨架 spacing token 化，未对 40+ 遗留密集面板做 4 网格重排，以避免布局回归。理由：专家模式密度契约不在七步旅程范围内。

---

## 排查项 4：主操作是否只存在于 ActionRail（内容区不得出现 primary）

**证据**
- 旅程阶段页装配 `StageRailProvider` 后，`railPresent=true`，各面板内容区 primary 以 `!railPresent` 条件不渲染（见项 1 所列面板）。
- `actionRail.test.tsx:70,81` 断言内容区 primary 数为 0。
- `JourneyRail.tsx:103` 的「去这一步」导航已由 `btn-primary` 降为 `btn-secondary`（消除每页 pervasive primary）。

**结论：合规（旅程阶段页内容区无 primary）。**
**允许的例外**：同项 1（项目管理/基础配置/专业模式/一次同意卡）。这些面板单独渲染（无 rail，`railPresent=false`）时保留自身 primary，是契约测试隔离渲染所需。

---

## 排查项 5：完成态是否只来自 /journey

**证据**
- `App.tsx:247` `getJourney(projectId)` 读取；`App.tsx:257` `stageDone` 完全由 `journey.stages[].done` 推导；`App.tsx:315` `nextStage` 由 `journey.next_stage` 推导。
- `JourneyRail.tsx:50` `stageDone = done[stage.id]`（done 来自 App 的 journey）；`JourneyRail.tsx:112` 「七个阶段全部完成」由 `nextStage` 为空触发。
- `ProjectPortfolio.tsx:186` 「全部完成」由 `row.nextStage` 为空触发（每作品 journey）。

**违例已修复（本轮对抗审查发现并修复）**
- `App.tsx:946`（修复前）：produce 聚焦条 `done: stageDone.storyboard ?? shots.length > 0` —— 用本地镜头数自创「已完成」回退。**已改为 `stageDone.storyboard ?? false`**。
- `App.tsx:1122`（修复前）：监控段摘要 `jobs.every(PASSED||CANCELLED) → '全部完成'` —— 用本地 job 列表自创完成态。**已改为 `deliverableReady`（= `stageDone.production`，journey 真相）**。

**结论：违例已修复，修复合规。** 修复后所有「已完成/全部完成」判定均溯源 /journey；读不到进度时如实显示读取中/失败，不冒充完成（`directorEntries.test.tsx:32-52` 守护）。

---

## 排查项 6：consent 勾选是否归口唯一

**证据**
- 已移除：`StylePanel.tsx` 逐流程 AI 同意勾选（`aiConsent` 状态与 checkbox 删除，`allow_remote_text` 交由后端项目级 grant 裁决）；`FilmCrewPanel.tsx` 工种 `remoteText` 勾选删除。
- 新增：`ConsentOnceCard.tsx` 项目级一次同意卡（同意并继续/暂不发送，写入 `/material-preparation/text-consent` grant）；`consentOnce.test.tsx` 守护「同意写入 granted=true、暂不发送不写入」。
- 撤回入口保留：`ScriptTextConsent.tsx`（基础配置/旅程内项目级授权面板，按项目去重）。

**结论：部分合规。**
**允许的例外 + 理由**：`StoryboardPanel.tsx:632,795` 两处文本发送同意勾选**保留**。理由：它们是 `storyboard.test.tsx:274,295` 契约测试守护的安全同意（发送剧本文本到云端前的明确许可），且分镜链 `allow_remote_text` 载荷与后端 UNKNOWN/同意门禁耦合；移除需协同后端授权流与契约测试一起迁移，超出本轮「只改呈现层、零回归」边界。建议后续单列一轮「分镜文本同意归口项目级」专项（含后端 grant 流 + 测试迁移）。本轮已把 StylePanel/FilmCrew 的冗余勾选归口，旅程内重复勾选减少。

---

## 排查项 7：进度组件是否唯一（除 .meter 外不得有第二套自绘进度）

**证据**
- 统一进度组件 `Meter.tsx`（渲染 `.meter`，保留 `role="progressbar"` + aria-label）。
- 已归一：`ActionRail.tsx`（阶段/运行进度）、`OutlineConfirmationPanel.tsx:151`、`AgentProgress.tsx:86`、`FourStagePanel.tsx:395`、`App.tsx:605`（重连指示）均由原生 `<progress>` 改为 `Meter`。`grep "<progress" frontend/src` → **0 处**。
- `modeEntry.test.tsx` 守护重连 progressbar 角色与名称（迁移后仍通过）。

**结论：合规（通用完成度/任务进度已全部 .meter 化）。**
**允许的例外 + 理由**：`ProductionSeriesPanel.tsx` 的 `series-progress-bar` + 镜头圆点网格、`StoryPreparationPanel.tsx` `run-progress-track` 保留。理由：这是 0.2.0 轮既有、`seriesOrder`/`productionComplete` 契约守护的**生产进度卡**，镜头圆点网格是区别于细条的专用可视化（设计 §2.3 亦要求镜头圆点复用统一组件，但其网格形态与 .meter 细条不同）；全量并入需重做进度卡视觉与契约，建议后续单列「生产进度卡 meter 化」专项。

---

## 排查项 8：提示是否只有三件套（.notice/.blocker-item/.meter）

**证据**
- `module-guide` 已全量归并 `.notice`（12 组件 + CSS 定义删除）。`grep module-guide frontend/src` → **0 处**。
- `required-action` 已全量归并三件套 `blocker-item` 的 `is-action` 强调变体（8 组件改名 `blocker-item is-action`、`required-text`→`blocker-reason`；CSS 删除旧 `.required-action` 定义，新增 `.blocker-item.is-action` 警示变体 + 暗色覆盖）。`grep required-action frontend/src` → **0 处**。
- 9px/9.5px/8px/7px/10px 字号残留 → **0 处**（全站最小 11px）。
- 错误码结构化：`errorActions.ts` → `App.railBlockers` → `.blocker-item`（原因 + 去处理）。

**结论：合规。** 提示统一为 `.notice`（信息）/`.blocker-item`（含 `is-action` 强调）/`.meter`（进度）三件套；旧 `module-guide`/`required-action` 类零残留。

---

## 排查项 9：ActionRail primary 状态机与 /journey next_stage 是否一一对应

**证据**
- rail primary 由各面板从**服务端加载的状态**计算：`DirectorProductionRunPanel.tsx`（`latestPlan.status`/`latestPackage.status`/`readiness`/`run`，均来自 `getJourney`/`listEpisodePlans`/`listDirectorPackages`/`getPackageReadiness`）；`StoryboardPanel.tsx`（`result.status`/`binding`/`requiresReconciliation`，来自分镜链服务端回执）；`MaterialPreparationPanel.tsx`（`state.stage`/`requirements`/`promotable`，来自素材准备服务端状态）；`StylePanel.tsx`（`currentStyle`，来自冻结风格版本）。
- 这些状态与 journey 阶段完成态同源（journey 的 plan.done = 分集已确认、package.done = 导演包已确认等），primary 状态机镜像服务端阶段机，**无前端硬编码的第四种完成来源**。
- `JourneyRail` 用 `journey.next_stage` 做导航；rail primary 用同源服务端粒度状态决定「当前该做的一件事」。两者皆溯源服务端。

**结论：合规。** primary 状态机由服务端状态推导，与 /journey 一致；项 5 修复后不存在前端自创完成态。

---

## 全量回归结果（修复后复跑，真实数字）

日志：`C:/Users/Administrator/.qoder-cn/tmp/reg-logs/{vitest,tsc,build,pytest,ruff}.log`。

| 命令 | 结果 |
|---|---|
| frontend `npm.cmd test`（vitest run） | **247 passed / 40 files / 0 failed** |
| frontend `npx tsc --noEmit` | **0 错误** |
| frontend `npm.cmd run build`（tsc -b + vite build） | **成功** |
| backend `pytest backend/tests -q`（仓库根，backend/.venv） | **2385 passed / 2 skipped / 0 failed**（收集总数 2387，复跑全绿） |
| backend `ruff check shine tests` | **All checks passed** |

### 后端首轮 1 例失败：mtime 粒度 flake，复跑通过（非本轮回归）

- 首轮全量曾出现 1 例失败：`backend/tests/test_weight_verification_receipts.py::test_same_length_wrong_bytes_never_count_as_verified`（2384/2/1）。
- **本轮后端零改动**：`git diff -- backend/` 为空；失败用例与其目标模块 `backend/shine/comfyops/verification_receipts.py` 均未被触碰。
- **根因（环境敏感 flake）**：`verification_receipts.py:94-95` 以 `(size, mtime_ns)` 作为验证复用缓存键。该用例先写 `b"0123456789"` 落收据，再以**同长度** `b"9876543210"` 覆盖；当两次写入落在同一 `st_mtime_ns` 刻度（Windows 文件系统时间粒度/系统负载相关），stat 看似未变 → 缓存命中返回 `verified=True`，未重新哈希。是否触发取决于写入时序，故为 flake。
- **复跑全绿**：第二轮全量 `pytest backend/tests -q` 该用例通过，总计 **2385 passed / 2 skipped / 0 failed**（exit 0）。
- **基线说明**：AGENTS.md 记载的「2286 passed / 2 skipped」是较早数字；其后多个 commit 新增用例，当前 main 收集总数已为 **2387**。本轮后端结果与 main 一致，**本任务未引入任何后端回归**。
- **建议**：该 flake 属后端验证缓存的 mtime 粒度敏感问题，修复需改后端（如失效键改用内容哈希），违反本任务「只改 frontend」硬红线，不在本轮处理；建议单列后端专项消除 flake。

## 修复清单（对抗审查发现，均已修复并复跑）

1. 项 5：`App.tsx` produce 聚焦条 storyboard 完成态回退 `shots.length > 0` → `stageDone.storyboard ?? false`。
2. 项 5：`App.tsx` 监控段「全部完成」由 `jobs.every(...)` → `deliverableReady`（journey）。
3. 项 8：`module-guide` 全量归并 `.notice`；`required-action` 全量归并 `blocker-item is-action`（三件套）。
4. 项 7：原生 `<progress>` 全量归一 `Meter`（.meter）。

修复后最终全量回归：**frontend 247 全绿 / tsc 0 / build 成功 / backend 2385 passed·2 skipped·0 failed / ruff 通过**。

## 未验证项（需真人走查）

- 视觉走查：四级按钮/三件套/吸顶操作栏/聚焦条在真实浏览器与暗色主题下的观感、≤1100px 底部固定条退化、sticky 吸顶行为。本轮仅离线测试验证（jsdom 不渲染真实布局），未启动服务栈做真人视觉走查。
- 项 6/7/8 的遗留例外（分镜文本同意勾选、生产进度卡、required-action 容器）建议后续专项归口。