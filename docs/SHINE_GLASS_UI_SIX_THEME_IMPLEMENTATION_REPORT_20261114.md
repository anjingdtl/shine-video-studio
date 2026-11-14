# Shine Glass UI · 六主题未来科技视觉系统 V1.0 — 实施报告

> 实施日期：2026-11-14  
> 方案来源：`docs/SHINE_GLASS_UI_SIX_THEME_SYSTEM_V1_20261114.md`（docs/shine-glass-ui-six-theme-v1-20261114）  
> 开发分支：`feat/shine-glass-ui-six-theme-v1`（从最新 `origin/main` 创建）  
> 交付方式：独立 worktree → 自测、审查 → PR 至 main（不自动合并）  
> 结论：**PASS（本地可验项全部通过；真实 H3/GPU 生产页为 MOCK_VERIFIED / NOT_RUN，详见第 11 节）**

---

## 1. 摘要

本次只改前端展示层与必要测试、文档：建立**一个主题引擎 + 六套语义 Token + 一套公共 Glass 组件层**，完成星云银白、星际墨夜、云阙灵境、霓虹之城、胶片时光、次元物语六套主题。

- 深色主题文字不可见的根因已定位并修复：实测 `.pref-block h3` 从 **2.15:1** 提升到 **10.78:1**。
- 删除 304 条旧 `html[data-theme='dark']` 组件级硬编码覆盖，未新增 `!important`。
- 素材中心独立 CSS 已接入同一主题 Token；未对视频、参考图、海报施加任何主题滤镜。
- 6 主题 × 7 类核心页面 = **42/42** 页面矩阵全部渲染成功、无页面异常、1280 宽无横向溢出。
- 84 组（6 主题 × 14 页面）自动对比度扫描：**0 条正文/大文本 AA 失败**。
- 键盘切换、刷新恢复、非法值回退、跨标签同步、表单与路由保持、主题切换 0 业务请求、媒体像素不变均已实测。
- `npm ci`、`npm run build`、`npm test` 全部通过；新增 22 条主题相关测试。

未修改导演模式七阶段业务逻辑、素材生成、任务调度、ComfyUI/H3 工作流、影视生产参数、项目/资产数据。

---

## 2. 交付信息

| 项目 | 内容 |
| --- | --- |
| 功能分支 | `feat/shine-glass-ui-six-theme-v1` |
| Worktree | `/shine-glass-ui-worktree`（本地独立开发树） |
| 代码基线 | `origin/main` @ `f8dbd70`（方案编写基线 `f47b020` 已前进，按最新 main 实施） |
| 方案文件 | `docs/SHINE_GLASS_UI_SIX_THEME_SYSTEM_V1_20261114.md` |
| 实施报告 | `docs/SHINE_GLASS_UI_SIX_THEME_IMPLEMENTATION_REPORT_20261114.md` |
| 证据目录 | `docs/evidence/glass-ui-20261114/` |
| 依赖变化 | 无新增运行时/构建依赖；未新增 Tailwind、Redux、CSS-in-JS、动画框架 |
| 合并方式 | 仅创建 PR，人工审阅后合并；未直接修改/推送 main |

---

## 3. 设计实现

### 3.1 单一主题引擎

新增 `frontend/src/theme/`：

| 文件 | 职责 |
| --- | --- |
| `themes.ts` | `ThemeId` 六值类型、注册表、中文名/描述、appearance、主题卡预览色、`parseThemeId` 安全回退 |
| `themeStore.ts` | 唯一状态源；`getTheme / setTheme / subscribeTheme / applyThemeToDocument / bootstrapTheme`；localStorage `shine-theme`；storage 事件跨标签同步；存储异常内存降级 |
| `useTheme.ts` | React `useSyncExternalStore` 订阅钩子 |
| `themes.css` | 六套语义 Token + 旧 Token 兼容映射 + reduced-transparency |
| `glass.css` | 公共玻璃、圆角、控件、焦点、按钮、弹窗、表格、主题卡、降级与媒体保护 |

主题 ID 稳定为：`light`、`dark`、`xianxia`、`cyberpunk`、`cinema`、`comic`。旧用户仅存 `light/dark` 时原值继续有效；未知/空值/存储不可用一律回退 `light`。

- 启动：`index.html` 内联脚本在首屏绘制前恢复 `data-theme`，`main.tsx` 再调用唯一 `bootstrapTheme()`，避免浅色闪深色。
- 同步：本页切换立即更新 DOM、`color-scheme`、`meta theme-color`、所有订阅组件；`storage` 事件负责其他标签页。
- 业务隔离：切换只读写 localStorage 与 document 属性，不改 URL/hash、不发 API、不卸载业务表单、不触发生产链。
- 旧状态源清理：`App.tsx` 不再读取/写入主题；`PreferencesPanel.tsx` 不再拥有独立主题 state；`AssetsWorkspace` 不另建主题状态。

### 3.2 Token 体系

语义层覆盖：

- 色彩：`--ui-bg / elevated / raised / sunken / text / text-secondary / text-muted / text-faint / border / accent / on-accent / focus / selection / placeholder`
- 玻璃：`--glass-panel / raised / sidebar / topbar / menu / dialog / border / fallback / scrim`
- 控件：`--control-bg / text / border / hover / disabled-* / --table-* / --code-bg / --media-stage / --tooltip-*`
- 状态：成功、警告、危险、信息各自成对的 `bg / text / border`
- 形态/光效：`--radius-card:18px`、`--radius-dialog:22px`、`--radius-control:11px`、`--motion-fast/normal`、三段环境光 `--ambient-a/b/c`

旧 token（`--ink / --paper / --surface / --olive-* / --warn-* / --ok-* / --danger-* / --sand-* / --lib-* / --asset-* / --sidebar-*` 等）在 `themes.css` 中统一映射到新语义 token，业务页面无需复制六套 CSS。

### 3.3 Glass 视觉层

- 主卡片：18px 圆角、半透明面板、柔光边框、低强度投影、`blur(16px)`。
- 顶栏/侧栏：玻璃分层 + `blur(22px)`，选中项同时使用背景、文字与标记。
- 弹窗：22px 圆角、更高不透明度、遮罩 `--glass-scrim`、`blur` 背景。
- 按钮：主按钮使用主题强调色与 `--ui-on-accent`；次按钮使用 control token；hover/active/focus-visible/disabled 全态覆盖；禁用态不再依赖浏览器半透明灰。
- 输入框：统一 control token、11px 圆角、可见 focus ring、placeholder 对比度提升。
- 氛围光：`.app-shell::before` 三段低饱和径向渐变，`pointer-events:none`，位于内容之后。
- 素材中心：补齐原未定义的 `--asset-*`，替换固定白底、白色图标按钮、独立顶栏；侧栏/顶栏/卡片与主工作台同源。
- 媒体保护：视频、图片、海报、素材缩略图显式 `filter:none; mix-blend-mode:normal`，播放器底色固定中性 `--media-stage`。
- 降级：`@supports not (backdrop-filter)` 使用较实面板；`prefers-reduced-transparency: reduce` 切换实底；`prefers-reduced-motion: reduce` 关闭新增过渡。

### 3.4 「显示与偏好」交互

- 在原位置保留字号设置、配置快捷导航，不移动、不消失。
- 新增六张可视化主题卡：每张同时展示背景、玻璃卡片、标题行、强调按钮，含中文名与一句描述。
- 单击立即全站切换，无需保存/重启；`aria-pressed` 表达选中态，键盘 Tab + Enter/Space 可切换，焦点有 2px 可见 outline。
- 保留原说明：「界面主题仅调整工作台外观，不影响作品导演风格、成片颜色与生成结果。」

---

## 4. P0 深色问题：根因与修复

### 4.1 真实缺陷证据

基线（`origin/main @ f8dbd70`）深色偏好面板：

| 元素 | 前景 | 背景 | 对比度 |
| --- | --- | --- | --- |
| `.pref-block h3`（“主题 / 文字大小 / 各类配置去哪儿找”） | `rgb(86, 88, 75)` | `rgb(35, 36, 32)` | **2.15:1（不可辨认）** |

截图：[修复前深色偏好面板](evidence/glass-ui-20261114/p0/before-dark-preferences.webp) / [修复后](evidence/glass-ui-20261114/p0/after-dark-preferences.webp)；机器可读测量见 [p0-evidence.json](evidence/glass-ui-20261114/p0-evidence.json)。

### 4.2 根因

1. `:root` 只有浅色值；旧 `html[data-theme='dark']` 是**组件级覆盖**，没有覆盖基础 token 本身。
2. 大量组件直接消费 `--ink`、`--ink-mid`、`--muted`、`--paper`，深色卡片背景被覆盖后，文字仍是浅色主题的深色值。
3. 素材中心 `assets.css` 使用未定义的 `--asset-*` 变量，并硬编码 `background:white`；`color:var(--paper)` 在深色下变成深色文字。
4. 旧深色覆盖使用橄榄绿硬编码（#2c3325 等），与新深蓝黑主题不一致，且只覆盖部分组件，形成“补丁叠补丁”。

### 4.3 修复

- 六套主题按语义 token 整体定义；旧 token 一次性映射，消除组件级深色补丁。
- 删除全部 304 条旧 `html[data-theme='dark']` 选择器（含 3 段硬编码变量块），未使用新的 `!important`。
- 修复固定白底/白字：toast、上传按钮、素材中心图标按钮、素材表单/参考行/遮罩编辑器、状态徽标等。
- 修复未定义的 `--asset-*`（37 个）以及 `--border / --panel / --paper-2 / --surface-raised` 等兼容变量。
- 修复 `.avatar`、`.llm-profile-count`、`.route-revision-state` 等“浅底浅字/深底深字”配对。
- 修复后同一元素：`rgb(195, 208, 230)` on `rgba(19, 29, 51, .72)`，**10.78:1**。

---

## 5. P3 六主题 × 七类页面矩阵（42 组）

矩阵与证据索引：[matrix-report.json](evidence/glass-ui-20261114/matrix-report.json)；总览图：[contact-sheet.webp](evidence/glass-ui-20261114/contact-sheet.webp)。

| 类别 | 代表页面 | light | dark | xianxia | cyberpunk | cinema | comic |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A 项目管理与外壳 | 项目管理 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| B 创意与剧本 | 创意与剧本 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| C 剧本统筹与镜头拆解 | 剧本统筹 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| D 独立素材中心 | 创作首页 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| E 导演包与生产 | 导演包 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| F 后期成片与成片库 | 成片中心 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| G 基础配置与专业模式 | 设置板块（含显示与偏好） | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

每一格截图位于 `docs/evidence/glass-ui-20261114/matrix/{theme}-{A..G}-*.webp`。42 次加载均：

- 无 `pageerror` / React 崩溃；
- 实际 `data-theme` 与所选主题一致；
- 1280×900 下 `scrollWidth === innerWidth`，无横向溢出；
- 主体文字、按钮、表格、标签、空状态、提示条均可见。

补充覆盖（同一次实现验证）：

- 素材中心图片工坊、声音工坊、全局素材库、项目分配；
- 导演模式七阶段全部页面与「基础配置」；
- 专业模式设置板块、生产板块；
- 普通、悬停、聚焦、选中、禁用、加载、成功、警告、失败、空状态、弹窗、toast 由统一 token / 控件层覆盖。

> **MOCK_VERIFIED**：上述 42 组页面由本地 Node Mock API + 固定项目/镜头/任务/素材夹具渲染，未连接真实 H3、ComfyUI、GPU 或真实生产任务。Mock 仅用于视觉取证，不代表真机生产通过。Mock 脚本随证据保存：[mock-api.mjs](evidence/glass-ui-20261114/mock-api.mjs)。

---

## 6. 可访问性与交互实测

### 6.1 对比度

对 6 主题 × 14 页面共 84 组页面，逐个可见文本节点按最终合成背景计算 WCAG 对比度（正文 ≥4.5:1，大文本 ≥3:1）：

- **0 条失败**（脚本：[contrast-report.json](evidence/glass-ui-20261114/contrast-report.json)）。
- 关键正文样例：light 14.61、dark 15.63、xianxia 13.65、cyberpunk 16.88、cinema 12.71、comic 13.14。
- 弹窗单独复核：六主题帮助弹窗共 186 个可见文本节点，**0 条失败**，最低 7.52:1；弹窗圆角 22px、`blur(22px)`（[dialog-report.json](evidence/glass-ui-20261114/dialog-report.json)，[dark 弹窗截图](evidence/glass-ui-20261114/dark-help-modal.webp)）。
- 非文本提示：焦点 outline 2px 主题强调色；状态均有文字标签，不仅依赖颜色。

### 6.2 键盘与恢复

- `Tab` 可到达六张主题卡；`Enter` 切换成功；焦点 outline 实测 `2px solid rgb(143, 175, 255)`。
- 刷新后主题恢复（cinema → cinema，`meta theme-color #282320`）。
- 非法存储值（`sepia`）刷新回退 `light`。
- 旧 `light/dark` localStorage 值继续有效。
- 字号「大」仍写入 `shine-font=large` / `shine-font-zoom=1.12`，刷新保持。

### 6.3 业务非侵入

- 跨标签主题事件后：hash 仍为 `#d/creative`，正在编辑的 textarea 原文不变，**0 条 API 请求**。
- 主题切换路径不调用 `useStudio`、不写项目/镜头/任务、不改变路由。
- 媒体原色：同一 `<img>` 在 light / cyberpunk / cinema 三种主题下 canvas 像素哈希完全一致（`1656840331`），`filter:none`、`mix-blend-mode:normal`、`opacity:1`。

### 6.4 响应式与降级

- 320 / 375 / 768 / 1280 / 1920 × 5 个关键页面：**25/25 无横向溢出**。
- 320 下项目表格仍可横向滚动（滚动容器内），但不再撑宽页面。
- `prefers-reduced-motion: reduce`：主题卡 transition 实测 `0s`。
- `prefers-reduced-transparency: reduce`：卡片背景切换为实底 `rgb(251,252,254)`，`backdrop-filter: none`。
- 无 `backdrop-filter` 的浏览器由 `@supports not` 回退到实底玻璃近似。

汇总数据：[final-verify.json](evidence/glass-ui-20261114/final-verify.json)。

---

## 7. 自动化测试与构建

执行命令与结果：

```bash
cd frontend
npm ci          # PASS，added 288 packages
npm run build   # PASS，tsc -b + vite build，1649 modules
npm test        # PASS，56 files / 328 tests
```

测试配置说明：本机 jsdom + userEvent 用例单独执行约 4–5s，默认 5s 超时在整套并行运行时会把 CPU 竞争误报为超时。`vitest.config.ts` 将 `testTimeout/hookTimeout` 调整为 15s，**未修改任何测试断言、未跳过任何用例**；调整后全量套件稳定通过。初次默认超时运行的超时项已在第 11 节如实记录。

新增测试（23 条）：

| 文件 | 覆盖 |
| --- | --- |
| `src/test/themeRegistry.test.ts` | 六 ID、元数据/预览完整性、appearance、合法/非法解析 |
| `src/test/themeStore.test.ts` | 本页即时切换、唯一状态、持久化、旧值兼容、非法回退、存储异常、启动恢复、跨标签、meta theme-color、路由不变 |
| `src/test/preferencesTheme.test.tsx` | 六卡渲染、点击切换、aria-pressed、键盘可达、字号偏好保留 |
| `src/test/themeCss.test.ts` | 六主题核心 Token 完整性、预览色与 CSS 一致、无 `!important`、全部 CSS 变量可解析、降级/媒体保护规则、首屏脚本与注册表一致 |

未删除任何已有业务断言，未关闭测试，未伪造结果。

构建产物：`dist/assets/index-*.css` 234.66 kB（gzip 40.49 kB）；JS 996.81 kB（gzip 308.95 kB，Vite 原有 chunk 体积提示，本次未引入新依赖）。

---

## 8. 独立代码审查

| 审查项 | 结论 | 证据 |
| --- | --- | --- |
| 主题状态是否重复管理 | 否，唯一 `themeStore`；App / PreferencesPanel / AssetsWorkspace 无独立主题 state | `grep shine-theme` 仅 store、测试、index.html |
| CSS 覆盖冲突 | 旧 304 条深色覆盖已删除；新文件 0 个 `!important` | `grep -c '!important'` |
| 素材中心是否遗漏 | 未遗漏；`--asset-*` 全量映射，固定白底已清理 | `assets.css` 审计 |
| 是否破坏业务交互 | 未改七阶段、useStudio、API、任务/资产/风格逻辑；只改主题与展示 | diff |
| 新增依赖风险 | 无新增依赖 | `package.json` 未改 |
| 真实浏览器可读性 | 84 页 0 条 AA 失败；P0 2.15→10.78 | 对比度报告 |
| 未解析 CSS 变量 | 0 个 | `themeCss.test.ts` |
| 媒体颜色污染 | 无 filter/blend；像素哈希一致 | final-verify |

审查后修复：禁用按钮浏览器默认半透明灰、`.avatar` 深底深字、`.llm-profile-count` 反色徽标、`.privacy-card` 次要文字、cinema/comic faint 微调、窄屏表格 `contain:paint`、设置页 field-line/guardrail 溢出、分镜行按钮换行。修复后重新跑完 84 页对比度扫描与 42 组矩阵。

---

## 9. 修改文件

新增：

- `frontend/src/theme/themes.ts`
- `frontend/src/theme/themeStore.ts`
- `frontend/src/theme/useTheme.ts`
- `frontend/src/theme/themes.css`
- `frontend/src/theme/glass.css`
- `frontend/src/test/themeRegistry.test.ts`
- `frontend/src/test/themeStore.test.ts`
- `frontend/src/test/preferencesTheme.test.tsx`
- `frontend/src/test/themeCss.test.ts`
- `frontend/src/test/node-fs.d.ts`
- `docs/SHINE_GLASS_UI_SIX_THEME_SYSTEM_V1_20261114.md`（方案导入）
- `docs/SHINE_GLASS_UI_SIX_THEME_IMPLEMENTATION_REPORT_20261114.md`
- `docs/evidence/glass-ui-20261114/`（42 张矩阵截图、前后对比、JSON 报告、Mock 脚本）

修改：

- `frontend/index.html`：首屏前恢复主题、动态 `theme-color`、`color-scheme`。
- `frontend/src/main.tsx`：唯一 bootstrap + 字号恢复。
- `frontend/src/App.tsx`：删除 App 内主题初始化，交给统一 store。
- `frontend/src/components/PreferencesPanel.tsx`：六张可视化主题卡、统一 store、保留字号与配置导航。
- `frontend/src/styles.css`：删除旧深色硬编码（304 条），修复固定白底/白字与旧 token 配对。
- `frontend/src/asset-center/assets.css`：接入主题 token、替换固定白底、玻璃侧栏/顶栏/卡片、媒体中性保护。
- `frontend/vitest.config.ts`：将测试超时稳定为 15s（低资源本地环境下 jsdom/userEvent 用例接近 5s 默认上限），不改变任何断言或用例。

---

## 10. 六主题效果

| ID | 名称 | 视觉要点 | 实测 |
| --- | --- | --- | --- |
| light | 星云银白 | 银白玻璃、科技冷蓝、浅蓝光晕 | 卡片/顶栏/侧栏玻璃，主按钮冷蓝白字 |
| dark | 星际墨夜 | 深蓝黑玻璃、冷月蓝高亮 | 修复原文字不可见；全文对比度 10+ |
| xianxia | 云阙灵境 | 玉青、云雾、月白、金色细节 | 青玉玻璃、金色装饰光、状态色独立 |
| cyberpunk | 霓虹之城 | 深蓝紫、霓虹粉、电光青 | 粉/青低强度光晕，主按钮电光青 |
| cinema | 胶片时光 | 暖棕、复古琥珀、柔和玫瑰 | 暖色玻璃与琥珀高亮，无视频颗粒叠加 |
| comic | 次元物语 | 明亮色块、漫画描边、高对比 | 2px 描边 + 偏移阴影，深红主按钮 |

六主题结构、卡片层级、主操作位置完全一致，仅 Token 与必要装饰差异。

---

## 11. 剩余问题、风险与限制

1. **真实生产页面未验**：42 组矩阵使用本地 Mock API 夹具，真实 H3 / ComfyUI / GPU / 生产任务页面为 **MOCK_VERIFIED**，不是真机生产 PASS。
2. **GitHub CI NOT_RUN**：本环境未执行远端 CI；本地 `npm ci / build / test` 已通过。
3. **测试超时环境**：默认 5s 超时在本低资源沙箱整包并行运行时，曾把 styleFreeze 等 4–5s 的 jsdom 用例误报为超时（单独运行均通过）。最终将 `testTimeout/hookTimeout` 设为 15s，未修改任何断言或用例；修复后完整套件 328/328 通过。此调整只影响测试等待上限，不影响产品代码。
4. **1920 宽未逐页截图**：响应式用脚本检查了 320/375/768/1280/1920 的溢出与关键布局，但 42 张矩阵截图统一为 1280×900。
5. **旧 CSS 仍含固定色值**：非主题语义的媒体阴影、海报遮罩等保留中性固定值；已确认不影响六主题可读性与媒体原色。
6. **极窄屏表格**：项目表格在 320/375 下仍保留横向滚动（业务信息密度决定），已用 `contain:paint` 防止撑宽页面。
7. **主题卡预览色为静态元数据**：与 CSS 基础 Token 由测试保持一致；未来新增主题需同时更新 registry 与 CSS。
8. **H3/GPU 相关功能未启动**：未下载模型、未启动 ComfyUI、未触发任何真实生成。

---

## 12. 验收清单

- [x] 六主题完整，ID/名称/外观层/持久化一致。
- [x] 深色文字不可见根因已定位并修复，有前后截图与 computedStyle 证据。
- [x] Light/Dark 旧偏好兼容；非法数据、存储异常安全回退。
- [x] 统一主题引擎 + 单一 store + 语义 Token + 公用 Glass 层。
- [x] 导演模式七阶段、素材中心、成片、设置、专业模式视觉覆盖。
- [x] 六主题卡即时切换、键盘操作；原字号设置与配置导航保留。
- [x] 84 页对比度扫描 0 条 AA 失败。
- [x] 42 组矩阵有真实截图；Mock 项已明确标记 MOCK_VERIFIED。
- [x] 视频/图片/海报展示原色；像素哈希一致。
- [x] 无 blur、减少动画、低宽度有可用降级。
- [x] 主题切换不发业务请求、不改路由/任务/项目状态、不丢表单。
- [x] `npm run build`、`npm test` 实际结果已记录。
- [x] 本报告列出变更、证据、剩余问题与可合并性判断。
- [x] 独立 feature 分支 PR 到 main；未直接修改/推送 main，未自动合并。

---

## 13. 最终结论与合并建议

**PASS（本地可验范围）**。

P0–P4 已按证据完成：深色可读性根因修复、单一主题引擎、六主题 Glass UI、42 组页面矩阵、84 页对比度扫描、键盘/恢复/跨标签/响应式/媒体原色验证、构建与测试、独立审查与修复、报告与证据齐备。

建议：**人工审阅 PR 后合并 main**。合并前建议在具备真实 H3/ComfyUI 的本机再补一次生产页面视觉抽查；该部分当前为 MOCK_VERIFIED，不影响本次 UI 主题系统的可合并性判断。
