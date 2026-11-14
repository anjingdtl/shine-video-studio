# Shine Glass UI · 六主题未来科技视觉系统 V1.0

> 文档类型：正式建设方案（非实现报告）  
> 编制日期：2026-11-14  
> 仓库：anjingdtl/shine-video-studio  
> 方案分支：docs/shine-glass-ui-six-theme-v1-20261114  
> 代码核查基线：main @ f47b0207b6a2d35be9ad6f52a154b7924b3b430e  
> 交付方式：本地 Agent 独立 worktree → 独立 feature 分支 → 自测、审查 → PR 至 main（禁止自动合并）  

## 0. 建设目标和边界

Shine Web 工作台建立统一的「未来科技感 + 大圆角 + 半透明毛玻璃 + 柔和光晕」视觉系统，在既有浅色、深色基础上，增加仙侠、赛博朋克、文艺电影、漫画四套主题，合计六套。所有工作区共享主题引擎，一键即时切换、跨页面一致、本地持久化并启动恢复。

**优先级：P0 深色文字不可见修复 → P1 单一主题状态与语义 Token → P2 六主题 Glass UI → P3 全页面可访问性与回归 → P4 工程验收与 PR。**

硬约束：

1. 只改前端展示与必要测试、文档。禁止改业务状态机、导演七阶段逻辑、后端接口、LLM、ComfyUI/统一调度、H3、安装器、模型下载、任务调度、资产版本、产线状态、项目数据。
2. **UI 显示主题与作品导演风格完全隔离**：切主题不影响风格冻结、分镜、镜头参数、生成工作流、影片视觉风格或任何已生成媒体。
3. 不重建页面，不加操作步骤，不为六主题复制组件/页面。只允许一个主题引擎、六套 Token、共用 Glass CSS 和少量必要的主题特例。
4. 现有 light/dark 本地用户设置兼容。新值未知或存储失败安全回退 light。
5. 玻璃、光效、动画不得牺牲字幕/表单/视频审片内容可读性；状态语义不能只有颜色。
6. 与并行开发严格隔离：本地独立 worktree，禁止直接改/推 main，禁止覆盖他人的 PR/工作树，禁止 force push，禁止自动 merge。

## 1. 已核实的远端 main 现状

| 位置 | 核实情况 | 建设要求 |
| --- | --- | --- |
| frontend/package.json | React 19.1、Vite 6.4、TypeScript 5.8、Vitest 2.1、lucide-react | 复用现有技术栈，无须重型 CSS/状态框架 |
| frontend/src/App.tsx | 页面加载时读取 shine-theme，仅识别 light/dark，写入 HTML data-theme | 取消双值特判，改统一 bootstrap |
| frontend/src/components/PreferencesPanel.tsx | 主题状态类型为 light/dark；useEffect 写入 localStorage；原有文字缩放 | 改为六主题卡片与统一 store，保留文字大小、配置导航 |
| frontend/src/styles.css | :root 有大量旧颜色变量；数百条 html[data-theme='dark'] 的组件级覆盖 | 必须收束颜色语义，修复级联/背景文字配对，不可只堆 !important |
| frontend/src/asset-center/AssetsWorkspace.tsx | 独立引用 ./assets.css | 素材中心所有页面都要接受同一主题 |
| frontend/src/asset-center/assets.css | 具有 --asset-*、--lib-*、固定白背景、独立顶栏 blur | 映射 Token、清理不适配暗色的硬编码、不得给素材图上色 |
| frontend/src/main.tsx | 统一挂载 React 并导入 styles.css | 放置统一主题初次初始化 |
| frontend/index.html | 静态 theme-color #f5f4f0 | 主题切换时更新并保留首屏回退 |
| frontend/src/components/StageRail.tsx | 现有页面主操作栏具有独立的功能/状态注册逻辑 | 不得为换肤更改操作语义与状态 |

上述是设计依据，不代表已经浏览器复现每个缺陷。**真正开发前必须 fetch 最新 main 并复核。** 重点排查 --ink / --paper / --surface / --field / --sidebar-* / --lib-* / --asset-* 在暗色下是否仍继承浅色值，以及固定 hex、CSS 级联和 specificity 互相覆盖。深色「白字与底纹不可分辨」要通过截图与 getComputedStyle 确认真实前景/背景组合后逐处修复。

## 2. 六套主题的设计合同

| ID | 中文名称 | 基底 bg | 正文 ink | 主强调 accent | 主按钮 on-accent | 装饰/氛围 |
| --- | --- | --- | --- | --- | --- | --- |
| light | 星云银白 | #F1F5FB | #1B273C | #315CDF | #FFFFFF | 霜银、浅蓝光 |
| dark | 星际墨夜 | #0D1424 | #EEF4FF | #8FAFFF | #0B1530 | 深蓝黑、冷月蓝 |
| xianxia | 云阙灵境 | #0D262A | #EFFDF9 | #7BDCC3 | #092925 | 灵玉青、月华金 #D9BC81、云雾 |
| cyberpunk | 霓虹之城 | #090B22 | #F6F3FF | #55E8FF | #081522 | 霓虹粉 #FF4FBF、紫 #8073FF |
| cinema | 胶片时光 | #282320 | #FFF0E3 | #D7A577 | #2D211A | 旧玫瑰 #B78E95、暖胶片 |
| comic | 次元物语 | #FFF4DF | #243044 | #B62E2B | #FFFFFF | 漫画红 #FF6659、蓝 #4B9EF5、黄 #FFCC50 |

- light、comic 视为浅色；dark、xianxia、cyberpunk、cinema 视为深色。
- 上表是候选视觉配色，不等于所有实际文本/玻璃组合已通过对比度校验。由开发者做实测并允许适度调色。
- 霓虹粉主要作光效和局部强调，避免亮粉底白字；漫画用深红作可读主按钮，亮红只作装饰；状态成功/警告/失败颜色不能被主题的主色取代。
- 仙侠有玉青层叠、细金线、轻云影；赛博有冷青/紫粉低强度光晕；电影有克制暖棕及淡颗粒的视觉意象（**不叠加视频颗粒**）；漫画有清晰的彩块、适度粗线与圆角，但不过度卡通化业务区域。
- 六主题形态共用、仅 Token 与必要装饰差异；卡片结构和主操作位置保持一致。

## 3. 统一 Design Tokens

建议将新变量放到 frontend/src/theme/themes.css；旧变量通过兼容映射逐批迁移。

### 3.1 语义层必须覆盖

**色彩**：--ui-bg、--ui-bg-elevated、--ui-text、--ui-text-secondary、--ui-text-muted、--ui-text-inverse、--ui-border、--ui-border-strong、--ui-accent、--ui-accent-hover、--ui-on-accent、--ui-link、--ui-focus、--ui-selection、--ui-placeholder。

**玻璃**：--glass-panel、--glass-panel-raised、--glass-sidebar、--glass-topbar、--glass-menu、--glass-dialog、--glass-border、--glass-inner-highlight、--glass-shadow、--glass-blur、--glass-fallback、--glass-scrim。

**控件**：--control-bg、--control-text、--control-border、--control-hover、--control-active、--control-disabled-bg、--control-disabled-text、--table-header、--table-row-hover、--table-selected、--code-bg、--media-stage、--tooltip-bg、--tooltip-text。

**状态**：--status-ok-bg/text/border、--status-warning-bg/text/border、--status-danger-bg/text/border、--status-info-bg/text/border。语义配色必须成对/成组，不能只替换文字或背景。

**形态和光效**：--radius-card、--radius-dialog、--radius-control、--ambient-a/b/c、--theme-glow、--motion-fast、--motion-normal。

必须建立「旧 Token → 新语义 Token → 消费位置 → 是否存在覆盖」清单。旧 --olive、--ink、--paper、--surface、--sidebar-*、--lib-*、--asset-*、--fx-* 等不得直接一键删除。业务状态 token 与主题装饰色不能混用；从浅色迁移 dark 时尤其注意旧变量未覆盖部分。

### 3.2 Glass 组件基线

| 对象 | 规格 |
| --- | --- |
| 主面板/卡片 | 16–20px 圆角；半透明、柔光边框、低强度投影 |
| Modal/dialog | 20–24px 圆角；高不透明度；清晰遮罩，不透出干扰文字 |
| 主/次按钮 | 10–12px 圆角；主色与按钮文字至少 AA 对比，危险动作保留独立警告 |
| 文本/输入框 | 10–12px 圆角；文本区比装饰面板更实；focus 可见 |
| 左导航与顶栏 | 玻璃分层明确，选中项同时使用文字、边框/背景和标记 |
| 氛围背景 | 渐变光低饱和、pointer-events:none；不能盖住表格/工具条 |
| 播放器与媒体内容 | 中性底色，禁止给视频帧、参考图、海报、色彩样本叠滤镜或混合模式 |

建议 blur(16px) 常规玻璃、blur(20px) 弹窗，最多 2–3 层半透明，过渡 120–220ms，真实参数以可读性和滚动性能为准。不要另建 WebGL/Canvas 粒子系统。

### 3.3 回退和性能

- 同时考虑 backdrop-filter 与 -webkit-backdrop-filter，缺失时 @supports 回退较实面板，不得透明到无法阅读。
- prefers-reduced-motion: reduce 禁止无必要动画/位移；prefers-reduced-transparency 在支持的浏览器中使用高不透明层。
- Windows 本地 WebView 或弱显卡不可因毛玻璃失去功能；避免滚动列表每条卡片都上大 blur 或高频 box-shadow 动画。
- 控件状态必须有 hover/active/focus/selected/disabled/error/empty/loading 各态；不能仅套默认皮肤。
- 合理使用 z-index token；不能让背景装饰伪元素盖住 dialog、popover、sticky 操作栏、滚动条或播放器。

## 4. 主题管理架构与文件建议

架构：六主题注册表 → 单一存储/订阅服务 → document.documentElement.dataset.theme → 语义 CSS Token → 兼容旧变量层 → 公共 Glass 样式 → 导演/素材/成片/配置页面。

建议按实际代码布局实施，避免无谓造轮子：

~~~text
frontend/src/
  theme/
    themes.ts             # ThemeId、主题元数据、合法值检查
    themeStore.ts         # get/set/subscribe、localStorage/跨标签同步
    themeBootstrap.ts     # 首帧前恢复、防闪烁
    useTheme.ts           # React 订阅钩子
    themes.css            # 六套语义变量
    glass.css             # 公共玻璃、圆角、阴影、控件层
  components/
    PreferencesPanel.tsx  # 六卡片可视化选择器；保留字号/配置导航
  App.tsx                 # 删除双值专属初始化，使用统一 theme service
  main.tsx                # 仅在必要时调用 bootstrap
  styles.css              # 清理重复暗色硬编码，改 Token
  asset-center/assets.css# 同一主题支持，固定白背景审计
frontend/index.html       # 浏览器颜色与首帧降闪烁
~~~

**不要新增 Tailwind、Redux、CSS-in-JS、重型动画框架。** 可以按项目现状精简文件，但一定保留单一状态源和测试可达性。

### 4.1 ThemeStore 接口合同

~~~ts
export type ThemeId =
  | 'light' | 'dark' | 'xianxia' | 'cyberpunk' | 'cinema' | 'comic';

export type ThemeAppearance = 'light' | 'dark';
export type ThemeDefinition = {
  id: ThemeId;
  name: string;
  description: string;
  appearance: ThemeAppearance;
  preview: { background: string; surface: string; accent: string; text: string };
};

export function parseThemeId(value: unknown): ThemeId;
export function getTheme(): ThemeId;
export function setTheme(id: ThemeId): void;
export function subscribeTheme(listener: () => void): () => void;
export function applyThemeToDocument(id: ThemeId): void;
~~~

要求：

1. 继续使用 localStorage **shine-theme**（旧 light/dark 原值有效）；其他四值使用上述稳定 ID；非法/空值回退 light。
2. 存储不可访问时 catch 并用内存态，不能致 App 白屏。
3. 本页按钮切换立刻更新 DOM、所有主题订阅组件及偏好 UI；storage 事件负责跨标签同步（注意原标签写入不会收到自己的 storage 事件）。
4. App、PreferencesPanel、AssetsWorkspace 不可再各自拥有独立的主题 state 与写入 effect。
5. 初始化必须在主 UI 首次绘制前尽可能恢复已存主题，避免 light 闪 dark；唯一 bootstrap，避免多处并发写入。
6. 设置 data-theme 时对应 color-scheme、浏览器 meta theme-color 一起更新；appearance 应被清晰定义。
7. 切换不改变 URL/hash、不发业务请求、不刷新路由、不卸载正在编辑的表单，不触发 ComfyUI/生产链。
8. 六个 ID 应在 TS 类型、registry、CSS token 定义与主题卡中完整一致。

### 4.2 迁移策略（避免 CSS 特例爆炸）

- 先扫描所有 frontend CSS/TSX 的原始色值、CSS variables 和 html[data-theme='dark'] 选择器。
- 现有 dark 是大量组件覆盖而非完整语义 Token；优先解决可读性根因，删除/替换已经迁移的深色覆盖，未迁移的不得粗暴删掉。
- 不允许通过全局 color: white 或大量 !important 补丁掩盖问题。
- 优先改主壳层 .app-shell、.sidebar、.main-content、.topbar、.card、.metric-card、.section-card，以及按钮、表单、弹窗、表格等复用组件。
- 把 asset-center/assets.css 的 --asset-*、--lib-*、header blur、icon-button 硬编码白底纳入统一语义 Token。
- 不修改已有 CSS 中描述媒体审片的真正中性底色（如视频播放器背景），不把主题光晕应用到图片本身。
- 有条件时将历史硬编码转换为少量兼容 Token；新增模块不得继续裸用固定白底与浅色正文变量。
- 记录每一步是否改变既有 light 样式；轻微视觉升级可接受，操作布局和易读性退化不可接受。

## 5. 「显示与偏好」交互方案

- 保留现有页面与字号偏好，原配置快捷导航不移动、不消失。
- 增加六张小型可视化主题卡（不仅是三个颜色点）：能同时看到背景、玻璃卡片、标题、强调按钮。
- 卡片根据视口响应式显示 1/2/3 列，中文主题名与简短描述；选中态、hover、focus、aria-pressed / radio 语义准确。
- 单击直接全站切换，不需要「保存」按钮和页面重启；当前主题明确标识。
- 附短说明：「界面主题仅调整工作台外观，不影响作品导演风格、成片颜色与生成结果。」
- 低性能、失去 backdrop-filter 能力时显示实底玻璃近似效果，无需给普通用户额外配置步骤。
- 如认为顶栏快捷切换必要，可作为可选增量，但不能挤占导演主操作。

## 6. 全局验收覆盖

### 6.1 6 × 7 = 42 组核心视觉矩阵

| 类别 | 必验内容 | 风险重点 |
| --- | --- | --- |
| A 项目管理与外壳 | 主导航、顶栏、作品列表、新建作品 | 侧栏、弹窗、通知、激活项 |
| B 创意与剧本 | 导入文本、文本编辑、大纲、提示信息 | 长文本、placeholder、对比度 |
| C 剧本统筹与镜头拆解 | 分镜书、表格、阶段栏、镜头列表 | 密集行/状态/吸顶操作区 |
| D 独立素材中心 | 图片工坊、声音工坊、素材库、项目分配 | assets.css、缩略图、独立导航 |
| E 导演包与生产 | 策略选择、准备状态、启动/暂停、任务进度 | 状态色、按钮禁用、错误提示 |
| F 后期成片与成片库 | 播放器、导出、视频卡片、预览弹窗 | 真实媒体颜色不受主题影响 |
| G 基础配置与专业模式 | 新机设置、连接测试、LLM 配置、显示偏好、专业页 | 各类输入、菜单、复杂表格 |

对每一组主题×类别需要截图或屏幕录像证据，并抽查 hover、focus、active、disabled、selected、loading、success、warning、error、empty、dialog、toast、滚动、长字段折行等状态。没有真实 H3 / GPU 时允许 UI mock/演示页面进行视觉取证，但必须标记 MOCK_VERIFIED；不将其写为真机生成 PASS。

### 6.2 可访问性硬门槛

- WCAG AA 常规正文文本与最终实际背景对比度 ≥ 4.5:1，大文本 ≥ 3:1，关键非文本视觉提示（边界、焦点等）目标 ≥ 3:1。
- 对玻璃透明和渐变区域检测最终合成效果，不能只比较 HEX 色板。
- 避免白字白底、暗字暗底、半透明叠层造成不可见、disable 文字消失、placeholder 看不清。
- 状态一律使用颜色 + 图标/文本，不以红绿颜色作为唯一成功/失败提示。
- 键盘 Tab 焦点可见，现有 dialog/popover 操作行为和屏幕阅读器标签保持正常。
- 窄屏 320/375/768，中大屏 1280/1920 基础抽测，不能因视觉换肤造成主要按钮遮挡或滚动失控。
- 不在媒体预览上施加 filter、mix-blend-mode、opacity 蒙层改变画面色彩。

### 6.3 自动化测试计划

1. Theme registry：枚举完整、解析合法/非法 ID、主题元数据/预览完整且唯一、appearance 正确。
2. Store：本页切换立即反映、storage 不可用不崩溃、跨标签 storage 事件、启动预恢复、老用户 light/dark 数据兼容。
3. PreferencesPanel：六张预览卡切换、按钮状态、键盘可达、字号偏好不丢失。
4. CSS 检查：所有主题核心语义 Token 定义完整、无未解析属性；新增关键区域不得硬编码不适配的白色背景。
5. 浏览器检查：实际页面 computedStyle、对比度采样、焦点顺序、对话框堆叠、模糊降级及截图索引。
6. 业务非侵入：切换主题前后的项目/镜头 ID、编辑文本、生产状态、页面路由不改变；浏览器网络面板无主题驱动的业务 API 调用。
7. 性能：避免无限动画和重型特效，正常滚动不卡滞；低性能设备或无 backdrop-filter 下仍可用。
8. 回归：至少覆盖主页面、素材中心、成片与基础配置。检测不能建立的真实场景必须记录 NOT_RUN/BLOCKED，禁止假报 PASS。

## 7. 实施阶段与交付物

### P0 — CSS 审计与深色根因修复

交付：现有 Token 及暗色选择器清单、真实缺陷位置截图/DOM 样式证据、dark 组件背景/文字配对修复、light/dark 回归检查。

验收门槛：深色核心操作区域不再出现不可读文字、按钮或提示；根因与修复能对应，不依靠无节制 !important。

### P1 — 单一主题引擎与兼容层

交付：六主题元数据、ThemeStore、启动恢复/跨标签/异常回退、六套基础 Token、必要兼容映射、单元测试。

验收门槛：6 ID 稳定、可切换、刷新后恢复、原用户旧配置继续适用、切换不丢表单或重启业务组件。

### P2 — Glass UI 与六主题选择器

交付：公共圆角毛玻璃组件/层级、四套新风格、六张主题预览卡、素材中心主题统一、浏览器降级。

验收门槛：六风格视觉差异明显，页面布局和主按钮不变，显示偏好页保持简单易用。

### P3 — 全站适配与浏览器质检

交付：42 组矩阵视觉证据、交互状态检查、对比度校验报告、低宽度/性能/降低动画降级记录、问题关闭表。

验收门槛：无 P0/P1 可读性阻滞；没浏览器截图的项目不标 PASS；不可测试项可带证据列为 BLOCKED/NOT_RUN。

### P4 — 工程自验、收束、PR

交付：构建/测试输出、实施报告、变更文件清单、风险及回退说明、独立 feature 分支和 PR。

推荐运行（以本机实际依赖情况为准）：

~~~powershell
cd frontend
npm ci
npm run build
npm test
~~~

实现报告位置建议：docs/SHINE_GLASS_UI_SIX_THEME_IMPLEMENTATION_REPORT_20261114.md。报告需要写清楚构建、Vitest、浏览器截图/对比验证、无法测试项，不允许以 MOCK 代替 H3 真机验收；GitHub CI 因额度未运行时明确 NOT_RUN。

## 8. 本地独立 worktree 开发协议

**文档分支只是方案载体，不能长期作为功能分支的代码基底。** 在本地从最新 origin/main 创建工作树和 feat/shine-glass-ui-six-theme-v1 分支，再从文档分支取出本方案单文件。必须避免干扰其他 Agent 的 worktree。

建议命令：

~~~powershell
cd F:\ClaudeWorkSpace\projects\shine-video-studio
git status --short
git fetch origin main docs/shine-glass-ui-six-theme-v1-20261114
git worktree add -b feat/shine-glass-ui-six-theme-v1 F:\ClaudeWorkSpace\projects\shine-glass-ui-worktree origin/main
cd F:\ClaudeWorkSpace\projects\shine-glass-ui-worktree
git restore --source origin/docs/shine-glass-ui-six-theme-v1-20261114 -- docs/SHINE_GLASS_UI_SIX_THEME_SYSTEM_V1_20261114.md
~~~

如本地已有同名 worktree/分支，先检查并选择安全的新名称，绝不重置或覆盖它。分阶段提交、逐步测试，PR 前同步 main 并精准解决冲突，不得 force push 或直接推 main。必要的文件冲突必须以**最新源码**为准，而不是复制旧文件覆盖其他分支成果。

## 9. 风险、回滚和禁止事项

| 风险 | 规避措施 |
| --- | --- |
| 旧 CSS 与新 token 优先级冲突 | 基于 computedStyle 定位；分批替换并清理深色硬编码 |
| 一次性全站换肤导致业务 UI 退化 | P0→P3 分阶段小提交、各阶段可单独回退 |
| 素材中心遗漏 | 独立资产样式纳入 42 组矩阵 |
| 页面主题污染影片审美 | 只存浏览器设置，播放器/图片中性保护 |
| 低端浏览器 blur 不支持 | @supports fallback + 更实背景 |
| localStorage 异常或旧偏好失效 | catch、解析校验、light 安全回退 |
| 主分支并行代码更新 | 独立 worktree、PR 前同步 main、冲突审查 |
| 无法执行 CI/浏览器测试 | 保留真实证据，未运行就标 NOT_RUN 而非 PASS |

禁止：新后端接口、重写七阶段导演逻辑、做两套 theme state、修改影片导演风格存储、针对每主题复制全部页面 CSS、强制 GPU/模型下载以完成 UI 验收、删掉别人正在使用的功能入口、伪造真机/测试证据、未经许可自动合并。

## 10. 最终验收清单

- [ ] 六主题完整且主题名/ID/外观层/持久化一致。
- [ ] 深色文字不可见根因已定位并修复，有重现与修复证据。
- [ ] Light/Dark 旧偏好兼容，非法数据及存储异常可恢复。
- [ ] 统一主题引擎 + 单一 store + 语义 Token + 公用 Glass 层。
- [ ] 导演模式七阶段及素材中心/成片/设置/专业模式视觉全覆盖。
- [ ] 六主题卡支持即时切换、键盘操作；原字号设置与配置导航保留。
- [ ] 正文 ≥4.5:1，大字/关键非文本提示 ≥3:1 的目标有实测证据。
- [ ] 全 42 组页面矩阵有真实截图或明确 BLOCKED/NOT_RUN。
- [ ] 视频/图片/海报展示原色；主题不改变媒体预览像素。
- [ ] 无 blur、减少动画、低宽度与旧 WebView/浏览器有可用降级。
- [ ] 主题切换不发送业务请求、不改变路由/任务/项目状态。
- [ ] frontend npm run build、npm test 的实际结果已记录。
- [ ] 实施报告列出变更、证据、剩余问题、可合并性判断。
- [ ] 独立 feature 分支 PR 到 main；无直接修改主分支或自动合并。

**PASS 定义**：P0–P4 按证据完成，六主题无重大可读性问题，旧功能无回归，浏览器截图/测试可追溯，PR 可供人工审阅。  
**PARTIAL 定义**：主要实现已做，但仍有缺测或非阻断问题；必须附清单和限制。  
**FAIL 定义**：任何主控件文字不可见、主题切换导致编辑/生产状态丢失、媒体颜色被污染或 main 被违规修改。

> 本文是基于上述 main 快照的建设方案。真正开始编码前应以远端 main 实时状态校准所有受影响文件，并在实施报告写明与本方案不同的发现。
