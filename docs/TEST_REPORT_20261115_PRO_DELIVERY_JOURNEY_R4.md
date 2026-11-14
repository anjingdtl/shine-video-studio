# R4 专业模式 + 交付板块穿测报告（小导演）

| 项 | 值 |
| --- | --- |
| 报告文件 | `docs/TEST_REPORT_20261115_PRO_DELIVERY_JOURNEY_R4.md` |
| 轮次 / 角色 | R4 · 小导演（只测不改代码，代码归修复师） |
| 日期 | 2026-11-15 |
| 被测栈 | 导演线 Web `http://127.0.0.1:5180` / API `http://127.0.0.1:8010` |
| 运行模式 | `production_mode=DEMO`、`render_provider=mock`、`mock_provider_available=true`、`h3_connected=false`、队列空（pending/running/failed/blocked 均 0） |
| 测试项目 | **测评-R4-小导演-01**（`a9f6cc6f-cd0c-48e3-a6ed-5712fae5e737`），经界面「项目管理 → 新建作品」创建 |
| 证据目录 | `.local-acceptance/r4-20261115-director/`（24 张截图 + 每页 JSON 文本 + 驱动脚本 + 标签差集脚本） |
| 工具 | 自写 CDP 驱动 `r4_cdp.mjs`（Node 24 原生 WebSocket/fetch，**未执行任何 npm 安装**）；**未启动/停止/重启任何工作栈** |

---

## 0. 诚实边界

- 全程离线 / mock。本轮只测界面与接口层，**未发生真实 H3 渲染，不构成出图 / 成片验收**。
- 本机没有任何文本模型（三套栈的 `/api/v1/provider-profiles` 均为空数组）。依赖文本工种的链路（AI 自动分镜、AI 编导四阶段、影视制片团队工种运行）本轮**无法真实驱动**，一律记「未运行」，**没有自造模型配置绕过**。
- 未改任何产品代码；未碰 `.local-data/` 用户数据；测试项目统一命名「测评-R4-小导演-01」。
- 有一处**我自己的操作错误**如实记录，不计入缺陷：字幕第一次保存报 `SUBTITLE_EMPTY`，原因是我的脚本「先填值、后添加行」，按正确顺序复测**保存成功**（见 D-R4-01 的对照实验说明与推进表「已跑通」第 10 条）。

---

## 1. 范围与一个必须先纠正的前提

**专业模式导航的 14 个条目不是 14 个页面。** 实测：`App.tsx:55-86` 的 `VIEW_GROUPS` 把 14 个导航项映射到 4 个 `ViewId`（setup / production / delivery / pro），每个 `ViewId` 渲染**一个长页面**，导航项只是页内锚点滚动（`resolveHash` → `document.getElementById(anchor).scrollIntoView()`）。实测证据：

| 专业模式导航项 | 实际落点 | 页内锚点 id |
| --- | --- | --- |
| 制作环境 / 模型与系统 / 显示与偏好 | 设置板块（同一个长页） | `setup` / `settings` / `preferences` |
| 项目概览 / 素材与风格 / 制片准备 / AI 自动分镜 / 人名与字幕 / 分镜与任务 / 多镜头连续生产 | 生产板块（同一个长页） | `overview` / `assets` / `preproduction` / `storyboard` / `terminology` / `production` / `production-series` |
| 成片与导出 / 审计追踪 | 交付板块（同一个长页） | `delivery` / `audit` |
| 影视制片团队 / H3 官方规范 | 专业板块（同一个长页） | `film-crew` / `h3-rulebook` |

导演模式另加两页：`#d/deliver`（成片中心）、`#d/library`（成片库）。

这意味着：14 个导航项的「页面级」差异必须靠**锚点定位后的截图**区分，单看 `document.body.innerText` 拿到的是整页文本——本次报告的每条结论都锚定到具体锚点截图，未把整页文本误当作单页内容。

---

## 2. 推进表

### 2.1 已跑通（10 项）

| # | 环节 | 证据 |
| --- | --- | --- |
| 1 | 导演模式 → 项目管理 → 新建作品「测评-R4-小导演-01」（走界面填表 + 点按钮） | `setup/shots/01-create-project.png` |
| 2 | 专业模式 4 个长页全部打开；14 个导航锚点全部可达并逐点截图 | `sweep/shots/02..14-*.png`（13 张） |
| 3 | 成片与导出空态（专业模式 交付板块）：「镜头全部通过后，这里自动出现成片；没出现就回上一步看卡住的镜头。」+「尚无制作系列。选定生产范围并开拍后，这里显示每个生成分段。」+「还没有片段；选择剪辑方案或展开高级编辑。」+「暂无交付成片。」 | `sweep/shots/11-delivery.png` |
| 4 | 审计追踪：读取 + 手动刷新，3 条记录的时间 / 主体 / 动作 / 对象四列均可见 | `audit/shots/01-audit-refreshed.png` |
| 5 | 导演模式成片中心 `#d/deliver` 空态：「这一步要做什么 / 为什么必须做」+「还没有整部作品的运行记录。先在「导演包」阶段确认并启动。」 | `sweep/shots/15-d-deliver.png` |
| 6 | 导演模式成片库 `#d/library` 空态 + 四类计数（0 条视频 / 当前采用 0 / 历史 0 / 待审 0 / 缺失异常 0）+「这部作品还没有落盘视频。完成一次真实出片后，单镜和成片都会出现在这里。」 | `sweep/shots/16-d-library.png` |
| 7 | 影视制片团队 9 个工种卡片：输入一致性审核 / 环境与能力工程师 / 编剧 / 导演 / 摄影指导 / 剪辑师 / 场记 / 声音导演 / 制片统筹，各自显示输出类型名（StyleInputAssessment / ComfyOpsProposal / ScriptBreakdown / DirectorialPlan / CameraPlan / EditDecisionList / ContinuityReport / SoundPlan / ProductionPlan）、权限声明与角色模型状态 | `sweep/shots/13-film-crew.png` |
| 8 | H3 官方规范中心：规则包 `shine-h3-rules/1.1.0` + **SHA-256 5990ae18…** +「规则包已批准」；4 个源文件各自 SHA-256 且诚实标 `SOURCE_UNAVAILABLE`；机器规则检索带来源文件与章节号 | `sweep/shots/14-h3-rulebook.png` |
| 9 | 人名对照表：**留空词条**时保存成功（版本 1 · 0 个词条）——同时证明该功能**只能存 0 词条**（见 D-R4-01） | `write2/shots/01-term-empty-save.png` |
| 10 | 字幕（外挂可修订）：添加一行 → 填文本/起止秒 → 保存成功（版本 1 · DRAFT · 1 行，附「批准此版本」按钮） | `write2/shots/03-subtitle-save.png` |

### 2.2 被阻断（环境限制，非产品缺陷）

| 环节 | 阻断原因（界面原文） |
| --- | --- |
| AI 自动分镜 | 「尚未配置 AI 助手，预设和手工选择照常可用。」——本机零文本模型，14 个工种未分配模型 |
| AI 编导四阶段（导入→大纲→剧本→分镜） | 同上；四步全部依赖文本工种 |
| 影视制片团队「运行工种」 | 每个工种显示「请配置模型路由 / 未配置，不会偷偷换别的模型 · 最近一次：未运行」 |
| 多镜头连续生产「一键开拍」 | 「还没有应用分镜，制作列表为 0 镜；先完成并应用分镜。」+「还没有生成方案；点下方按钮一键登记。」+「当前选择演示，请先切回真实制作。」 |
| 成片与导出的生成分段 / 剪辑方案 / 装配成片 | 需要真实分镜与真实 H3 输出；当前 0 镜 |

### 2.3 未运行

- 真实 H3 渲染、真实出图、真实成片验收（`h3_connected=false`、`production_mode=DEMO`）。
- 一切依赖文本工种的真实产出（大纲 / 分集 / 分镜 / 审片建议）。
- 真实安装包执行、节点包门禁在本栈的触发路径。

### 2.4 本轮跳过

| 对象 | 原因 |
| --- | --- |
| 导演模式 `#d/setup`（设置向导）与 `RealProductionWizard` | 修复师本轮在改 |
| 素材中心 `#a/home`、`#a/images`、`#a/voices`、`#a/library`、`#a/assignments` | 美术师本轮在测 |
| 专业模式「制作环境」区块的**质量评价** | 与修复师改动区重叠：只取证、只报事实（见 D-R4-04），不判定、不排期 |

---

## 3. 缺陷清单

### D-R4-01 【P1｜产品缺陷】人名对照表保存必然失败：`TERMINOLOGY_ROW_INVALID`

**症状**
专业模式 → 生产板块 → 人名与字幕 → 「人名对照表」，填入任一真实词条后点「保存为新版本」，页面顶部红色提示「人名对照表保存失败：TERMINOLOGY_ROW_INVALID」，版本列表停在「版本 1 · 0 个词条」。

**编号复现步骤**
1. 浏览器打开 `http://127.0.0.1:5180/#v/production/terminology`
2. 「专有名词 1」填 `周砚`；「易混写法 1」填 `周烨`
3. 点「保存为新版本」
4. 观察页面顶部提示 + 下方版本列表

**期望**：保存成功，版本列表新增「版本 2 · 1 个词条」。
**实际**：提示裸错误码；版本列表仍为「版本 1 · 0 个词条」（即只保存了空表）。

**原始错误码**：`TERMINOLOGY_ROW_INVALID`（HTTP 422）

**截图**：`.local-acceptance/r4-20261115-director/write2/shots/02-term-filled-save.png`

**影响面**：人名对照表功能整体不可用。ASR 的专名提示（`backend/shine/services.py:1142 project_terminology_hint`）永远拿不到词条，识别模型把专有名词读错时无从纠正；该功能在「人名与字幕」这一整块的主要产出为零。

**根因（只读核实，未改代码）**
`backend/shine/main.py:1004-1006` 把 `body.terms`（`list[TerminologyTerm]`，**Pydantic 模型**）原样传给 `save_project_terminology`；`backend/shine/services.py:1184` 调 `parse_table(terms, notes)`；`backend/shine/quality/terminology.py:93` 判 `if not isinstance(row, dict): raise TerminologyError("TERMINOLOGY_ROW_INVALID")`。Pydantic 模型不是 `dict` ⇒ **只要词条非空就必然 422**。

**对照实验**：词条留空时保存**成功**（版本 1 · 0 个词条），证明当前实现只接受「0 词条」。

**建议修法**：`main.py:1005` 改传 `[t.model_dump() for t in body.terms]`；或让 `parse_table` 接受 `Mapping`。另建议前端把 422 的裸码换成中文说明（「词条格式不被接受」+ 指向填写要求）。

---

### D-R4-02 【P2｜产品缺陷】审计追踪把后端事件码直接显示为裸大写码（中文覆盖率 14.5%）

**症状**
交付板块 → 审计追踪，点「刷新操作记录」后 3 条记录中：
- 「保存字幕」显示为 `SUBTITLE_REVISION_SAVED`
- 「保存人名对照表」显示为 `PROJECT_TERMINOLOGY_SAVED`，且实体列显示 `project_terminology · ca968ffb…`（**实体类型也是裸码**）

**编号复现步骤**
1. 打开 `http://127.0.0.1:5180/#v/production/terminology`，在字幕区点「添加一行字幕」→ 填文本与起止秒 → 「保存为新版本」（成功，出现「版本 1 · DRAFT · 1 行」）
2. 打开 `http://127.0.0.1:5180/#v/delivery/audit`
3. 点审计区右上角「刷新操作记录」
4. 读三行记录的「动作」列与「对象」列

**期望**：动作列显示「保存字幕」「保存人名对照表」，实体列显示「字幕」「人名对照表」。
**实际**：见上（裸码）。

**原始错误码**：非报错；界面原样透出 `SUBTITLE_REVISION_SAVED` / `PROJECT_TERMINOLOGY_SAVED` / `project_terminology`。

**截图**：`.local-acceptance/r4-20261115-director/audit/shots/01-audit-refreshed.png`

**影响面（已量化）**
- 后端 `add_audit` 实参可产生 **242** 个事件码（脚本 `.local-acceptance/r4-20261115-director/audit_label_diff.py` 解析 `backend/shine` 全量）；前端 `frontend/src/constants.ts:234 auditEventLabels` 只收录 **35** 个 ⇒ 覆盖率 **14.5%**，缺 **233** 个。
- 另有 **27** 个前端标签后端已不再产生（`TERMINOLOGY_SAVED`、`SUBTITLE_SAVED`、`JOB_QUEUED`、`JOB_PASSED` 等）⇒ 标签表与后端**双向漂移**。
- `label()` 未命中即回退显示原码，所以缺 233 个＝绝大多数真实动作在界面上都是裸大写码。
- 实体标签同样错位：前端有 `terminology`，后端实际用 `project_terminology` ⇒ 裸码。

**建议修法**：以后端事件码为唯一事实源生成标签表（上述脚本可直接产出差集，建议做成 CI 检查）；实体标签补 `project_terminology`；或在审计响应里由后端直接带中文标签。

---

### D-R4-03 【P2｜产品缺陷】审计追踪只显示 8 位截断 id，看不出对象是谁

**症状**
「创建项目」这条记录的对象列显示「项目 · a9f6cc6f…」，而同一页面顶部作品名是「测评-R4-小导演-01」。用户无法把记录对应到具体对象，更无法在多作品之间分辨。

**编号复现步骤**
1. 打开 `http://127.0.0.1:5180/#v/delivery/audit`
2. 读任一行记录的最后一列

**期望**：显示可读的对象名（项目名 / 素材名 / 分镜名 / 版本号）。
**实际**：`项目 · a9f6cc6f…`（固定 8 位截断）。

**原始错误码**：无。

**截图**：`.local-acceptance/r4-20261115-director/audit/shots/01-audit-refreshed.png`

**影响面**：`frontend/src/components/AuditPanel.tsx:31` 固定 `item.entity_id.slice(0, 8)`；交付板块宣称「全程留痕」，但留痕内容对用户不可读，与该主张不符。

**建议修法**：审计响应补对象显示名（project.title / asset.name / shot.name），前端优先显示显示名，无显示名时才回退到截断 id。

---

### D-R4-04 【P2｜产品缺陷｜与修复师本轮改动区重叠】设置板块用户可见文本里出现裸错误码

**症状**
专业模式 → 设置板块，「制作环境」区块的用户可见文本直接露出裸码，例如：
「本机阻断项：`TOOL_MISSING_INSTALLABLE(npm)`。可安装项运行：pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/bootstrap.ps1」；同页还可见 `AUTO_REPAIRABLE`、`REAL_INSTANCE_MISSING_OR_AMBIGUOUS`、`GENERATE_RUNTIME_CONFIGURATION_FIRST`。

**编号复现步骤**
1. 打开 `http://127.0.0.1:5180/#v/setup/preferences`（或 `#v/setup/settings`）
2. 展开「制作环境」区块，读「本机阻断项」一行与「1 · 环境检查」表

**期望**：给中文说明——缺什么、为什么卡住、下一步做什么。
**实际**：裸码直接露出。

**原始错误码**：`TOOL_MISSING_INSTALLABLE` / `AUTO_REPAIRABLE` / `REAL_INSTANCE_MISSING_OR_AMBIGUOUS` / `GENERATE_RUNTIME_CONFIGURATION_FIRST`

**截图**：`.local-acceptance/r4-20261115-director/sweep/shots/03-preferences.png`

**影响面**：违反 AGENTS.md「用户可见文本不得只留裸错误码」硬要求。**该区块属设置向导（SetupPanel），与修复师本轮改动区重叠 → 建议转交修复师随本轮一并处理，本轮不另行排期。**

---

### D-R4-05 【P3｜观察项】术语 / 字幕面板在无数据时没有任何空态说明

**症状**
「人名对照表」与「字幕（外挂可修订）」两个面板在尚无任何版本时，只显示字段与「添加一行」「保存为新版本」按钮，**没有「还没有词条 / 还没有字幕版本」之类说明**，用户无法判断这是「还没配过」还是「加载失败」。

**编号复现步骤**
1. 打开 `http://127.0.0.1:5180/#v/production/terminology`（新作品、无任何版本）
2. 读「人名对照表」与「字幕（外挂可修订）」两段

**期望**：空态说明为什么没有、下一步做什么。
**实际**：只有表单，无空态文案。

**原始错误码**：无。

**截图**：`.local-acceptance/r4-20261115-director/sweep/shots/08-terminology.png`

**影响面**：与 AGENTS.md 必做块要求同源。**对照本轮通过项**：`#d/deliver`（成片中心）、`#d/library`（成片库）、成片与导出三个空态都写清了「为什么没有 + 下一步做什么」，此处明显落后，是同一产品里的不一致。

---

### D-R4-06 【P3｜观察项】审计追踪进入视图时不自动刷新

**症状**
保存字幕与人名对照表后切到审计区，仍显示「共 1 条 · 当前显示 1」（服务端此时已是 3 条），需手动点「刷新操作记录」才更新。

**编号复现步骤**
1. 在 `#v/production/terminology` 保存一条字幕
2. 打开 `http://127.0.0.1:5180/#v/delivery/audit`
3. 读「共 N 条」

**期望**：与后台一致或自动刷新。
**实际**：需手动刷新（刷新按钮接线本身是对的：`App.tsx:1469` → `loadAudit(projectId, 0)`）。

**原始错误码**：无。

**截图**：`.local-acceptance/r4-20261115-director/write2/shots/04-audit-final.png`

**影响面**：低，属体验观察项；不单列修复。

---

## 4. 本轮重点问题逐条回答

**1) 成片与导出 / 成片中心在没有真实成片时，用户看到的是什么？空态是否说清「为什么没有」和「下一步做什么」？**
**通过，未发现缺陷。** 三个空态都达标：
- 专业模式 成片与导出：「镜头全部通过后，这里自动出现成片；没出现就回上一步看卡住的镜头。」／「尚无制作系列。选定生产范围并开拍后，这里显示每个生成分段。」／「还没有片段；选择剪辑方案或展开高级编辑。」／「暂无交付成片。」
- 导演模式 成片中心 `#d/deliver`：「这一步要做什么 / 为什么必须做」+「还没有整部作品的运行记录。先在「导演包」阶段确认并启动。」+ 底部「第⑦步 · 后期成片 / 打开成片库 / 上一阶段」。
- 成片库 `#d/library`：「0 条视频 / 当前采用 0 / 历史 0 / 待审 0 / 缺失·异常 0」+「这部作品还没有落盘视频。完成一次真实出片后，单镜和成片都会出现在这里。」

**2) 审计追踪页：能否查到「谁在什么时候做了什么」，时间、主体、动作是否可读，中文文案是否与真实字段一致？**
**部分可用，动作列不可读。**
- 时间可读（zh-CN 本地时间，如 `2026/11/14 22:32:16`）✓
- 主体可读（`用户` / `操作员`，与后端 `actor=user|operator` 对得上）✓
- **动作不可读**：242 个后端事件码中前端只覆盖 35 个，缺 233 个 → 绝大多数显示裸大写码（实测 `SUBTITLE_REVISION_SAVED`、`PROJECT_TERMINOLOGY_SAVED`）。→ D-R4-02
- **对象不可读**：只有 8 位截断 id，没有对象名。→ D-R4-03
- 数据本身完整：`GET /api/v1/projects/{id}/events` 返回 3 条，字段齐全（actor/event_type/entity_type/entity_id/payload/created_at）。

**3) 人名与字幕：术语与字幕面板在无数据 / 有数据两种情况下是否自洽，是否出现与后端字段对不上的文案？**
**有数据时字幕自洽；术语面板因缺陷不可用；两者均缺空态说明。**
- 术语面板（无数据）：只有字段与按钮，**无空态说明** → D-R4-05
- 术语面板（填词条）：**保存必然失败** → D-R4-01；且失败提示直接是裸码 `TERMINOLOGY_ROW_INVALID`
- 字幕面板（无数据）：同样无空态说明 → D-R4-05
- 字幕面板（有数据）：**自洽** —— 保存成功显示「版本 1 · DRAFT · 1 行」+「批准此版本」按钮 + 指纹；后端 `status=DRAFT/已定稿`、`source=human`、`sha256` 字段与界面文案一致（`constants.ts:254 subtitleStatusLabels`）
- 与后端字段对不上的文案：**有** —— 审计追踪把后端事件码 `PROJECT_TERMINOLOGY_SAVED` / `SUBTITLE_REVISION_SAVED` 原样透出，前端表里却写的是 `TERMINOLOGY_SAVED` / `SUBTITLE_SAVED` → D-R4-02

**4) 影视制片团队与 H3 官方规范页：规则说明是真实规则包内容还是占位文案？规则版本与哈希是否显示？**
**通过，均为真实内容，版本与哈希都在。**
- 影视制片团队：9 个工种，每个显示真实角色名、输出类型名（StyleInputAssessment / ComfyOpsProposal / ScriptBreakdown / DirectorialPlan / CameraPlan / EditDecisionList / ContinuityReport / SoundPlan / ProductionPlan）、权限声明「仅结构化建议；无主资产/风格审批、数据库写入或渲染提交权限」、角色模型状态「请配置模型路由 / 未配置，不会偷偷换别的模型 · 最近一次：未运行」。不是占位文案。
- H3 官方规范中心：规则包 `shine-h3-rules/1.1.0`、**SHA-256 `5990ae18159f96349022972cf07c78294e17ec4bf89140ef417a5cbbac0bdd28`**、「规则包已批准」；4 个源文件各自带 SHA-256 并如实标 `SOURCE_UNAVAILABLE`（官方原文未缓存，不分发）；「检索机器规则」列出 OFFICIAL_H3 规则条目并标注来源文件与章节号。**版本与哈希都显示，且未缓存的来源诚实标注。**

**5) 每个需要用户动手的步骤，是否用 `.required-action` / `.must-button` / `.module-guide` 说明「要做什么、为什么必须做」？**
**说明性文案基本达标，但 class 名未统一；确实出现裸错误码的地方已单列缺陷。**
- 导演模式：`#d/deliver`、`#d/library` 的 `.module-guide` 计数为 8，页面可见「这一步要做什么：…」「为什么必须做：…」✓
- 专业模式：14 个导航锚点对应的 4 个长页里，**CSS class 计数为 0**（`.required-action` / `.must-button` / `.module-guide` 全 0），但**文案上等价内容已存在**：「这一步要做什么：上传你有权使用的素材并选好用途…」／「为什么必须做：人物对照确认后才调用导演…」／「必做：生成 AI 分镜方案」／「会卡在哪里：某个工种失败只重跑那一步…」／「现在为什么不能开始制作」三条原因并给出口。
  → 因此**不按 class 名判缺陷**，改为记录为**一致性观察项**（若要按 AGENTS.md 硬要求统一 class，建议由修复师统一命名后加一条 CI 断言）。
- 真正只有裸错误码、没有必做块的：**人名对照表保存失败提示**（D-R4-01）与**设置板块的裸码文本**（D-R4-04）。

---

## 5. 证据清单

| 路径 | 内容 |
| --- | --- |
| `.local-acceptance/r4-20261115-director/r4_cdp.mjs` | 本轮自写 CDP 驱动（截图落盘 + 页面结构抽取），**未 npm 安装** |
| `.local-acceptance/r4-20261115-director/audit_label_diff.py` | 后端 242 个审计事件码 × 前端 35 个标签的差集脚本（D-R4-02 量化依据） |
| `.local-acceptance/r4-20261115-director/audit_events.py` | 后端审计事件码提取脚本 |
| `.local-acceptance/r4-20261115-director/sweep/shots/02..16-*.png` | 15 张专业模式与导演模式视图截图 |
| `.local-acceptance/r4-20261115-director/sweep/*.json` | 每页的完整文本 / 标题 / 按钮 / 表格 / 空态候选 / 必做块计数 |
| `.local-acceptance/r4-20261115-director/write2/shots/01..04-*.png` | 术语留空保存 / 术语填词条保存 / 字幕保存 / 保存后审计区 |
| `.local-acceptance/r4-20261115-director/audit/shots/01-audit-refreshed.png` | 刷新后的审计追踪（裸码实证） |
| `.local-acceptance/r4-20261115-director/setup/shots/01-create-project.png` | 界面新建测试作品 |

**取证纪律说明**：全部结论来自界面实测 + 同栈后端只读接口（`/api/v1/setup/summary`、`/api/v1/setup/variant-availability`、`/api/v1/projects/{id}/events`、`/api/v1/projects/{id}/subtitles`、`/api/v1/openapi.json`）与源码只读定位；未启动/停止任何工作栈，未改任何产品代码，未造凭据。
