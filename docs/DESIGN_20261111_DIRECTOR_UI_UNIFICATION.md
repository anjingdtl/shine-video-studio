# 导演模式七步旅程 UI 统一改造设计方案

- 日期：2026-11-11
- 状态：设计定稿（待实施）
- 范围：`frontend/src`（导演模式 `#d/*` 全部页面与共享样式）；不改任何后端门禁语义
- 用户四项裁定（2026-11-11）：吸顶右侧操作栏 ｜ 橄榄绿主操作按钮体系 ｜ 云端发送改项目级一次同意 ｜ produce 页单阶段聚焦视图

---

## 0. 本方案要解决的六个问题（用户原始诉求 → 代码事实）

| # | 用户诉求 | 代码层事实（本轮探索定位） |
|---|---|---|
| 1 | 按钮散落各处、颜色大小不一 | 三套按钮并行：`must-button` 橙棕 38px（styles.css:814）、`primary-button` 橄榄绿 32px、无类原生按钮（DeliveryPanel.tsx:249）；位置分五种：页头右侧 `run-head-actions`、卡片中部 `required-action`、卡片底部 `section-footer`、全宽 `must-button wide`、行尾 `run-binding-row` |
| 2 | 每页无统一框架、注意力分散 | 各页自行堆叠卡片；produce 页 4 个 `stage-dock` 折叠区同屏争抢；说明文字 9px（styles.css:818）、toast 10px |
| 3 | 缺批量操作；本地项目不该有授权勾选 | 待确认素材（AssetPanel.tsx:185）、生产段落（StoryPreparationPanel.tsx:130）、候选素材（MaterialPreparationPanel.tsx:1002）、逐场应用（FourStagePanel.tsx:714-766）均无全选；分镜流程内两处内容几乎相同的 consent 勾选（StoryboardPanel.tsx:578、:727）；本地操作 `window.confirm` 至少 15 处 |
| 4 | 点击无反馈 | `FourStagePanel.tsx:175` `run()` 静默吞点击；`:284` `applyUnit` 静默 return；FilmCrewPanel.tsx:54-59 只报「操作完成，请查看结果」；全局唯一 toast 5.2s 覆盖式不排队（useStudio.ts:77-82） |
| 5 | 长提示、高行高、视觉干扰 | `module-guide` 9px/1.65 长文两段（FourStagePanel.tsx:363-367）；错误码整句中文用「；」join 成一行（api.ts:27-109 → FourStagePanel.tsx:756-762）；blockers 列表 `slice(0,8)` 截断（DirectorProductionRunPanel.tsx:485-490） |
| 6 | 按钮死锁、时序不通 | `must-button:disabled` 只有 `opacity:.55` 无原因（styles.css:816）；「应用这一场的镜头」无 camera_artifact 时静默禁用；「交付本卷制作」缺 handoffProfile 无引导去登记方案（FourStagePanel.tsx:846）；生成按钮 5 个 disabled 依赖字段无 title（StoryboardPanel.tsx:599-602） |

---

## 1. 设计原则（全案约束）

1. **一页一件事**：每个阶段页面同一时刻只有一个主操作（primary），即「当前必须做的那一件事」。主操作由服务端 `/journey` 的 `next_stage`/`blockers` 推导，前端不自创完成态——延续「服务端状态唯一真相」的既有不变量。
2. **操作归口右侧**：所有改变状态的动作收进右侧吸顶操作栏（ActionRail）；内容区只保留「看」的信息（列表、进度、产物），不放主操作按钮。内容区允许保留行内轻量操作（如单行「重跑这一场」），但必须是 secondary/text 级。
3. **四级按钮，全站唯一**：primary / secondary / danger / text 四级，色彩、尺寸、字号全局唯一定义，废除按页面自定义。
4. **每次点击必有回声**：点击 → 按钮 loading → 结束 → toast/内联结果。禁止静默吞点击、静默 return。
5. **禁用必带出口**：任何 disabled 按钮必须同时给出「为什么 + 去哪补」。不可行动的禁用视为缺陷。
6. **提示按钮化**：错误码、blocker、待办，凡可行动的一律渲染为「一句原因 + 一个动作按钮」，不再渲染长句段落。
7. **不动后端门禁**：UNKNOWN 不重发、素材可选化、门禁 blocker、grant/CSRF 基础设施等服务端语义原样保留；本方案只改呈现层与交互层。

---

## 2. 统一 VI 设计语言

### 2.1 设计 token（styles.css `:root` 扩展）

保留现有品牌基因（橄榄绿、纸质感、DM Sans + Noto Sans SC + DM Mono），补齐缺失的字号/间距/圆角/时序 token：

```css
:root {
  /* 颜色（沿用现有，橙棕降级为警示文字色，不再做按钮底色） */
  --ink:#242522; --muted:#85867f; --line:#e9e8e2; --paper:#fff;
  --olive:#626b47; --olive-dark:#4e5736; --mint:#e8eee4; --sand:#f2ecdf;
  --warn-ink:#b8621d;          /* 橙棕：仅警示文字/图标，不再作为按钮底色 */
  --danger:#b3402f;            /* 破坏性操作 */
  --ok:#4f7a3c;                /* 通过/完成 */

  /* 字号（废弃 9px/9.5px；全站最小可读 11px） */
  --fs-caption:11px; --fs-body:12.5px; --fs-lead:14px;
  --fs-title:16px; --fs-page:20px;
  --lh-tight:1.35; --lh-body:1.55;

  /* 间距（4 的倍数，替换全部硬编码 padding/margin 间距） */
  --sp-1:4px; --sp-2:8px; --sp-3:12px; --sp-4:16px; --sp-5:24px; --sp-6:32px;

  /* 控件 */
  --radius:9px; --radius-sm:7px;
  --btn-h-primary:40px; --btn-h-sec:32px;
  --rail-w:272px;               /* 右侧操作栏固定宽 */
  --dur-fast:120ms; --dur-toast:400ms;
}
```

暗色主题在 `html[data-theme='dark']` 下按同名 token 覆盖，禁止散落写色值。

### 2.2 按钮四级规范（唯一的按钮词汇表）

| 级别 | 类名 | 外观 | 用途 | 数量约束 |
|---|---|---|---|---|
| 主操作 | `.btn-primary` | 深橄榄绿 `--olive-dark` 实心、白字、高 40px、圆角 9px、页面唯一 | 当前阶段必须做的一件事 | **每页 1 个**（ActionRail 顶部） |
| 次级操作 | `.btn-secondary` | 白底 `--line` 描边、`--ink` 字、高 32px | 备选路径（重新生成、换一版、手动模式、展开高级） | 不限，收在 ActionRail 次级列与行内 |
| 危险操作 | `.btn-danger` | `--danger` 描边白底（确认后实心红） | 取消整部生产、放弃任务、删除 | 必须先页内二次确认（armed 模式，见 §6.4） |
| 文字操作 | `.btn-text` | 无底色 `--olive` 文字 | 查看详情、折叠展开、跳转链接 | 不限 |

- `must-button` / `primary-button` / 无类原生按钮**全部废弃**，迁移映射：`must-button`→`btn-primary`（若同页已有 primary 则降 `btn-secondary`）；`primary-button`→`btn-secondary`；`subtle-button`→`btn-secondary`；`text-button`→`btn-text`。
- `.one-click-card` 的金底卡片保留为内容区装饰，内部按钮同样迁移。
- 按钮 loading 态：文案前缀加「正在…」+ `Loader2` 旋转图标；disabled 态：置灰 + 保留可 hover 的原因（见 §6.3）。

### 2.3 提示组件三件套（取代现有 6 种以上散落样式）

1. **`.notice`（信息条，替代 module-guide / 各种 hint 卡）**：一行图标 + 一句话（≤ 30 字）+ 可选「了解详情」折叠。字号 `--fs-caption`，背景 `--sand`，行高 `--lh-tight`，单条最高两行。长文说明一律折叠进 `<details>`。
2. **`.blocker-item`（可行动阻塞，替代 required-action 的长文）**：结构固定为「图标 + 一句原因 + [去处理] 按钮」。来源是服务端 blocker/错误码，映射表见 §7.3。每条一行，禁止整句段落。
3. **进度统一用 `.meter`**：细进度条（高 6px）+「n/m」数字，所有阶段完成度、批次进度、镜头圆点均复用同一组件；禁止各面板自绘进度表示。

---

## 3. 页面骨架：三区结构

七步旅程的每一页统一为同一骨架（`director-page` 内）：

```
┌────────────────────────────────────────────────────────────────┐
│ JourneyRail（现有组件保留：七点轨道 + 下一步上下文条）              │  顶部进度带
├───────────────────────────────────────────┬────────────────────┤
│                                           │   ActionRail       │
│   内容区 .stage-content                    │   ┌──────────────┐ │
│   （flex:1，只放「看」的信息：              │   │ ▶ 主操作按钮  │ │  右侧操作栏
│     状态摘要卡 / 列表 / 进度 meter /        │   ├──────────────┤ │  sticky
│     产物预览；不放主操作按钮）              │   │ 次级操作纵列  │ │  top 吸顶
│                                           │   ├──────────────┤ │
│                                           │   │ 待办/阻塞     │ │
│                                           │   │ (blocker-item)│ │
│                                           │   └──────────────┘ │
├───────────────────────────────────────────┴────────────────────┤
│ JourneyBridge（现有组件保留：上一阶段 / 下一阶段）                 │  页尾衔接
└────────────────────────────────────────────────────────────────┘
```

### 3.1 ActionRail 组件契约（新组件 `src/components/ActionRail.tsx`）

```tsx
type RailAction = {
  label: string;
  onClick: () => void;
  loading?: boolean;
  disabled?: boolean;
  disabledReason?: string;        // 必填当 disabled=true
  guide?: { label: string; go: () => void };  // 禁用时的「去补齐」出口
};

type ActionRailProps = {
  stageId: string;                // 当前阶段，用于标题「第③步 · 镜头拆解」
  primary: RailAction | null;     // 唯一主操作；阶段完成时可为 null
  secondary?: RailAction[];       // 次级操作（≤4 个，超出折叠「更多」）
  blockers?: BlockerItem[];       // 待办/阻塞，渲染为 blocker-item（≤3 条 + 计数）
  progress?: { done: number; total: number; label: string };  // 阶段内 meter
};
```

规则：
- **primary 唯一性由页面装配层保证**：一页承载两个 must-do（如 ② 确认分集 + 冻结风格）时，按旅程顺序取最靠前的未完成项为 primary，其余进 secondary 置顶。
- ActionRail `position: sticky; top: 74px`（顶栏高 + 间距），宽 `--rail-w`，滚动全程可见；≤ 1100px 窄屏退化为底部固定条（同内容、横向排列）。
- 运行中的长任务（series 渲染中、大纲提取中）：primary 显示为不可点的进度态（meter + 当前镜头/场次），**不是 disabled**，避免「看起来卡死」。

### 3.2 produce 页单阶段聚焦视图（最大结构改动）

现状：4 个 `<details className="stage-dock">` 同屏折叠争抢（App.tsx:923/940/972/1059）。改为：

- 顶部**迷你阶段条**（替代现有 `.produce-stages` sticky 条）：4 段「③ 镜头拆解 → ④ 素材准备 → 排产 → 监控」，每段一个圆点状态（done/active/blocked）+ 短名，点击切换聚焦。
- 一次只**完整展开一个阶段**的内容区 + 其对应 ActionRail；其余三段收成一行摘要（如「④ 素材准备 · 12/18 就绪 →」）。
- **挂载不卸载**：沿用现有 `hidden` 属性切换（2026-11-06 轮已确立的约束），收起的阶段 DOM 仍在，运行中的分镜链、轮询、状态不中断——只切视觉。
- 自动聚焦沿用现有 `autoStage` 判定（App.tsx:303-306），用户手选后以 `stageOverride` 为准（现有机制不变）。
- 监控段在有活跃 series 时**不可收起**（点击其他段时若 series RUNNING，迷你条出现「拍摄进行中 · 去看进度」提示钮，用户确认后才切走）。

### 3.3 页面切换与挂载

现有「页面 `hidden` 保持挂载」机制（App.tsx:826-1145）整体保留。`DirectorProductionRunPanel` 等目前条件渲染的重组件改为挂载 + `hidden`（与页面骨架一致），保证跨页往返不丢状态——这是对现有「页面切换不丢状态」约束的补全，不是新约束。

---

## 4. 七步逐页方案

每页统一按「看什么（内容区）/ 主操作（右侧）/ 次级 / 批量 / 死锁修复」定义。主操作随服务端状态机切换：

### ① script 创意与剧本（#d/creative · ScriptEntryPanel + FourStagePanel）

- **内容区**：当前剧本卡（标题 + 场次数 + 来源）；创意简报状态卡（已确认/未写）；大纲确认状态卡（版本 + N 个待裁定问题）。四阶段进度条收为内容区顶部一条 meter。
- **主操作状态机**：无剧本 →「导入或撰写剧本」；有剧本无大纲 →「提取总体大纲」；有大纲未确认 →「确认这版总体大纲」；已确认 → null（rail 显示「② 去剧本统筹」引导钮）。
- **死锁修复**：悬疑剧本的「问题列表」不再以 consent 形式出现；大纲确认被 409 挡时，ActionRail blockers 渲染「剧本有 N 个创作问题待你过目 → [查看问题]」，查看后确认按钮携带 `acknowledge_questions_sha256`（后端既有机制，前端补呈现）。
- **精简**：`OutlineConfirmationPanel` 的确认动作合并进 ActionRail，卡内只留只读信息。

### ② plan 剧本统筹（#d/assets · DirectorProductionRunPanel[plan] + StylePanel）

- **内容区**：分集方案摘要卡（集数/镜头数预估）；风格卡（当前选定风格名 + 七维已就绪 n/7 + 冻结状态徽标）。两卡并排或纵排，各带自己的 meter。
- **主操作**：未确认分集 →「确认分集方案」；已确认未冻结 →「冻结风格」；两者都完成 → null。
- **次级**：重新生成分集草案（armed 确认）、更换风格、查看冻结版本历史。
- **精简**：`StylePanel.tsx:542` 的 AI consent 勾选删除（并入项目级同意，§6.5）；冻结按钮从面板底部 `required-action` 迁入 ActionRail。

### ③ storyboard 镜头拆解（produce 聚焦段一 · StoryboardPanel）

- **内容区**：分镜进度 meter（已生成 n 场 / 已应用 m 镜）；人物对照表（推荐映射已预填，见批量）；分镜列表（只读预览 + 单镜展开）。
- **主操作状态机**：无生成 →「生成 AI 分镜方案」；生成完未对照 →「确认人物对照」；对照完未应用 →「应用为正式分镜」；已应用 → null。
- **批量**：人物对照表头加「一键采纳全部推荐映射」（服务端 proposal 本就是推荐优先，逐行可再改）；分镜列表如需选择性应用，加全选/反选。
- **死锁修复（重点页）**：
  - `StoryboardPanel.tsx:599-602` 生成按钮 5 个 disabled 依赖逐项映射为 blocker-item：断线→「服务未连接，重试连接」；`binding` 占用→「先完成人物对照 → [去对照]」；`scriptTooLong`→「剧本超长，去拆分场次」。
  - 「应用为正式分镜」（:821）disabled 时必须列出缺失的人物主素材清单，每条带「[去准备素材]」跳转到 ④ 段。
  - 两处重复 consent（:578、:727）删除（§6.5）。

### ④ assets 拍摄素材准备（produce 聚焦段二 · MaterialPreparationPanel + AssetPanel）

- **内容区**：素材就绪总 meter（按需求清单 n/m）；候选网格（图在下文字在上，未出图占位格保留——2026-11-06 轮约定）；待确认上传素材列表。
- **主操作状态机**：无分析 →「分析剧本生成素材清单」；有清单未出图 →「生成候选图」；候选就绪 →「确认所选素材为正式素材」（晋升）；缺口全为可选 notice 时 →「跳过缺口，直接继续」（素材可选化，R09 语义）。
- **批量（本页最重）**：
  - 候选网格工具条：「全选 / 按类别全选（人物/道具/场景）/ 反选 / 一键采用全部推荐项」；逐格选用保留。
  - `AssetPanel` 待确认上传素材：头部「全选 + 批量确认」。
  - 「确认使用所选素材」按钮实时显示已选计数（「确认使用所选 12 项素材」）。
- **死锁修复**：`MaterialPreparationPanel.tsx:1048` promote disabled 原因上移到 rail blockers（如「3 项候选仍在生成 · 2 项缺图由 H3 自行生成（可跳过）」）。

### ⑤ package 导演包（#d/package · DirectorProductionRunPanel[package]）

- **内容区**：包身份卡（package_id 短码 + 绑定哈希 + 版本）；readiness 清单（材料就绪/快照一致，每项一行 ✓/✗）。
- **主操作状态机**：无包 →「生成导演包」；有包未确认 →「确认导演包」；已确认 →「启动整部作品生产」（随后跳 ⑥）。
- **修复**：`DirectorProductionRunPanel.tsx:485-490` blockers 取消 `slice(0,8)` 截断，改「前 3 条 + 还有 N 项 [展开]」；每条 blocker-item 带「去处理」。

### ⑥ production 视频生产（#d/run · DirectorProductionRunPanel[run]）

- **内容区**：总进度卡（批次 meter + 镜头圆点行 + 当前镜头计时——沿用 0.2.0 轮既有进度卡）；运行控制不需要散布按钮。
- **主操作**：无活跃 run →「一键开拍（自动适配）」（唯一入口，高级设置折叠进 secondary）；运行中 → primary 为进度态（不可点、非 disabled）；暂停/继续/取消收 secondary，取消为 danger + armed 确认。
- **时序修复**：制作进行中不再渲染「为什么不能开始」清单（0.2.0 轮已隐藏逻辑保留），blockers 只在 READY 之外状态出现且逐条可行动。

### ⑦ post 后期成片（#d/deliver · DirectorProductionRunPanel[post] + DeliveryPanel + EpisodePostEditor）

- **内容区**：交付列表（最新置顶 + 「最新」徽标，既有约定）；逐集卡片（封面 + 时长 + 状态徽标：已交付/待你验收）。
- **主操作**：有待验收集 →「验收这一集」（进入该集 EpisodePostEditor）；全部验收 →「打开成片库」。
- **修复**：`DeliveryPanel.tsx:249` 无类原生按钮及该面板全部按钮迁入四级体系；手工装配收 secondary。

---

## 5. 交互反馈体系（全站统一）

### 5.1 per-button loading（消灭静默吞点击）

- 所有触发网络请求的按钮：点击即 `loading=true`（文案「正在确认…」+ spinner），结束复位。以现有良好样板（StoryboardPanel.tsx:821 applying+Loader2）为标准模式。
- **修改 `FourStagePanel.run()`**：`actionInFlight` guard 命中时不得静默 return——guard 只应防御性兜底，正常情况下按钮已 loading 不可再点；若 guard 生效说明 UI 状态不同步，须 toast「上一操作仍在进行」。
- **修改 `applyUnit`**：`!unit.camera_artifact_id` 早退改为 blocker-item「这一场还没有摄影方案 → [去生成]」，不允许无声无息。

### 5.2 toast 队列改造（useStudio.report）

- 单例覆盖式 → 队列最多 3 条纵向堆叠右下角；success 4s 自动消失，error 常驻 + 手动关闭；字号 12px（现 10px）；同一动作的重复错误去重合并计数。
- 三个错误处理派别（toast/内联 status/内联 alert）统一：**结果性反馈走 toast，上下文性状态走内联 `.notice`**；`applyError`/`pollError` 类内联 alert 保留在卡片内但样式统一为 `.blocker-item`。

### 5.3 disabled 三要素（死锁修复的统一模式）

以 `ProductionSeriesPanel.tsx:506-539`（canStart + blockers/hints 分层）为全站标准：

```
按钮置灰  =  视觉 opacity  +  rail 内一行原因（blocker-item） +  [去处理] 跳转
```

- `disabledReason` 为 ActionRail/RailAction 必填项（disabled 时）；普通按钮退化为 `title` + 相邻 `.notice`。
- 迁移清单（探索定位的 8 处无解释 disabled）：FourStagePanel.tsx:846（缺 handoffProfile →「未登记生成方案 →[去基础配置登记]」）、:823（无场次 →「先在前一步勾选场次」）、AssetPanel.tsx:144（未选 →「全选」按钮就在旁边）、MaterialPreparationPanel.tsx:1048、FilmCrewPanel.tsx:115、StoryboardPanel.tsx:599/:747、ImageCapabilityCard.tsx:291。

### 5.4 危险操作：页内 armed 二次确认（替代 window.confirm）

推广 `FourStagePanel.tsx:429-447` 已验证的 armedScene 模式：第一次点击按钮变红显示「再点一次确认取消」，3s 未确认自动复原。**全部 15 处 `window.confirm` 迁移**到此模式（原生 confirm 阻塞整 tab 且样式不可控）。纯本地操作的 confirm（统一秒数 ProductionSeriesPanel.tsx:385、归档 AssetsWorkspace.tsx:968、删配置 LLMConfiguration.tsx:255 等）直接删除确认，一次点击生效 + toast 可知。

---

## 6. 授权与确认精简（本地桌面信任边界）

### 6.1 删除清单（纯本地、无意义确认）

| 交互 | 位置 | 处置 |
|---|---|---|
| 分镜流程两处文本发送 consent 勾选 | StoryboardPanel.tsx:578、:727 | 删除，并入项目级一次同意 |
| 风格生成 AI consent 勾选 | StylePanel.tsx:542 | 删除，同上 |
| FilmCrewPanel 每次运行的 remoteText 同意 | FilmCrewPanel.tsx:111-114 | 删除，同上 |
| 本地操作 window.confirm | §5.4 列举的本地类 | 直接删除 |
| 本地变更的手填 evidence/grant 双请求 UI | useStudio.ts:287-290 等 | **前端不再要求用户手填**；grant marker 仍自动携带（`x-shine-local-action`/CSRF），后端基础设施不动 |

### 6.2 项目级一次同意（新交互，唯一保留的同意）

- 触发：任一流程**首次**要把剧本文本发往云端文字模型时（后端返回 `REMOTE_AGENT_CONSENT_REQUIRED` 的第一现场）。
- 形态：页内卡（非弹窗）：「发送前需要你知道：剧本文本将发送到你配置的云端文字模型（显示 provider 名与指纹）。[同意并继续] [暂不发送]」。同意写入项目级授权（后端既有 per-project consent 存储），**此后全程不再出现任何 consent 勾选**。
- 撤回入口：基础配置 → 文本模型设置的 ScriptTextConsent 面板（现有组件保留）。
- 边界（不改的红线）：云端资产字节上传、远端 VLM/ASR 审片的项目级同意仍由设置页管理，旅程中不重复询问也不新增默认放行；本方案不触碰「云端传输需明确许可」的产品不变量——只是把「每流程重复勾」收敛为「项目一次同意」。

### 6.3 保留清单（不是授权，是安全/事实确认）

- UNKNOWN 结果的「核对/重发」交互（安全门禁，语义按钮保留，样式统一）。
- 破坏性操作（取消整部生产、放弃任务、删除）armed 确认。
- 冻结风格/确认包这类**不可逆里程碑**不需要额外 confirm（按钮文案已自说明），但完成后 toast + 状态徽标立即可见。

---

## 7. 提示简约化与错误码按钮化

### 7.1 文案规范

- `module-guide` 9px 长文 → `.notice` 一句话（≤30 字）+「了解详情」折叠；全站最小字号 11px。
- `required-action small`、`.series-hint-card ul`、`.material-hint` 等 9-9.5px 提示全部并入 §2.3 三件套。
- 行高统一 token：提示 `--lh-tight`（1.35），正文 `--lh-body`（1.55）；单条提示最高两行，超出折叠。

### 7.2 错误码结构化渲染

- 废弃「；」join 整句一行（api.ts `ERROR_CODE_LABELS` 现约 80 条保留作映射源）。
- 渲染规则：`错误码 → {一句话中文原因, 可选动作}` 映射表（前端 `errorActions.ts` 新文件）；每条一行 `.blocker-item`，有动作的带按钮，无动作的带「复制错误码」。
- blocker 列表超 3 条折叠为「3 条待处理 [展开全部 N 条]」，禁止 slice 截断丢信息。

### 7.3 高频错误码 → 动作映射（首批）

| 错误码 | 一句原因 | 动作 |
|---|---|---|
| `PENDING_WORKFLOW_MAPPING` | 本作品还没登记生成方案 | [去基础配置登记] → `#d/setup` |
| `H3_CHARACTER_REFERENCE_MISSING` | 有出场人物没有主素材 | [去准备素材] → ④ 段 |
| `SCRIPT_TEXT_CONSENT_REQUIRED` | 首次发送剧本到云端需同意 | [查看说明并同意]（§6.2 卡） |
| `SHOT_REVISION_CHANGED` | 镜头已升版，需要新导演包 | [重新生成导演包] |
| `PIPELINE_EPISODE_SCRIPT_SUPERSEDED` | 分集剧本已被新大纲替代 | [重做这一卷分集] |
| `LLM_TIMEOUT` | 云端模型响应超时 | [重试这一场] |
| `MATERIAL_IDENTITY_AMBIGUOUS` | 同一人物在多场外观描述不一致 | [按单场拆分计划] |

---

## 8. 实施分期（每期独立可验收、可回滚）

| 期 | 内容 | 主要文件 | 验收 |
|---|---|---|---|
| **P0 token 与按钮四级** | `:root` token 落地；四级按钮类；全站类名迁移（must/primary/subtle/text → 四级）；废弃 9px 字号 | styles.css、全组件 className | 视觉走查 + vitest 全绿 + `npm.cmd run build` |
| **P1 ActionRail + 三区骨架** | 新组件 ActionRail；七页接入三区布局；主操作收敛到 rail（含一页双 must 的排序规则） | ActionRail.tsx(新)、App.tsx、七页面板 | 每页恰好 1 个 primary 且在右侧栏 |
| **P2 反馈体系** | per-button loading；run()/applyUnit 静默路径修复；toast 队列；disabled 三要素迁移 8 处；armed 确认替代 window.confirm | useStudio.ts、FourStagePanel.tsx 等 | 点击无静默；disabled 全带原因 |
| **P3 produce 单阶段聚焦** | 迷你阶段条；单段聚焦 + hidden 挂载；监控段活跃时防误切 | App.tsx(produce 段) | 运行中 series 切换不中断（复用 seriesOrder 契约） |
| **P4 批量 + 同意精简 + 错误码按钮化** | 5 处批量操作；consent 删除 + 项目级一次同意；errorActions.ts 映射 | 各面板、ScriptTextConsent.tsx | 旅程内 consent 勾选 0 处；批量全覆盖 |

每期完成后跑：`frontend/` 下 `npm.cmd run build`、`npm.cmd test`；受影响契约测试同步更新（见 §9）。

## 9. 测试影响（必须同步的既有契约）

- `src/test/studioStub.ts`：`gotoDirectorPage()` 按真实按钮导航——P1 改按钮位置后 stub 的角色/文案选择器要跟着改（`must-button` 类名消失）。
- `src/test/seriesOrder.test.tsx`：P04「开始制作影片」按序提交/0 镜引导——主操作进 rail 后断言改挂 rail 内按钮。
- `src/test/fourStageRecovery.test.tsx` / `fourStagePanel.test.tsx`：恢复/对账/精确 package_id 契约不动，仅选择器更新；「HARD_BLOCK 时确认禁用但保留中文说明」迁移为 rail blocker 断言。
- `src/test/materialPreparation.test.tsx`、`simpleAssets.test.tsx`、`storyboard.test.tsx`、`directorEntries/directorRun/directorStyle` 等：同上，行为契约保持、选择器更新。
- 新增契约测试：`actionRail.test.tsx`（每页 primary 唯一、disabled 必带 reason、loading 语义）、`batchSelection.test.tsx`（全选/批量确认/批量应用逐场反馈）、`consentOnce.test.tsx`(项目级一次同意后旅程内无 consent 勾选)。

## 10. 风险与边界

- **不改**：后端一切门禁与状态机语义（journey、readiness、UNKNOWN、素材可选化 R09、grant/CSRF 基础设施、运行中任务挂载不中断）。
- **P1 主操作收敛**是行为变更最大的部分：既有多按钮并存页（如 ② 确认分集 + 冻结风格）改为顺序主操作，需在 P1 验收时用真实项目走查七步全旅程。
- produce 聚焦视图必须保住「运行中的分镜链保持挂载不中断」（2026-11-06 约定），P3 验收以 seriesOrder 契约 + 真实运行切换为准。
- 暗色主题所有新 token 需同步覆盖，禁止遗留双主题不一致。
- 本方案不触碰 `client_review_identity` 钉住的后端文件，无 VLM 重校准义务。
