# Shine Video Studio · 导演模式七阶段主线测评报告（R1）

- **run-id**：`eval-20261115`
- **执行日期**：2026-11-15
- **执行角色**：小导演（视频创作全旅程测评员）
- **隔离实例**：API `http://127.0.0.1:8010` / Web `http://127.0.0.1:5180`
- **实例启动**：`pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/start_eval_instance.ps1 -DataDir .local-acceptance/eval-20261115 -ApiPort 8010 -WebPort 5180`
- **证据目录**：`.local-acceptance/eval-20261115/shots/`（15 张 PNG）
- **被测代码树**：`main@1e05bfa`，工作树有 30 个他人未提交改动，本轮**未修改任何代码**

## 0. 诚实边界（先说清楚）

| 项 | 实测值 |
| --- | --- |
| `/api/v1/system/status` | `api_online=true`、`worker_online=true`、`mock_provider_available=true`、`h3_connected=false`、`render_mode=mock` |
| `production_mode` | `mode=DEMO`、`runtime_status=NOT_ACTIVATED`、`renderer_online=false` |
| `/health` schema | `schema_revision=schema_head=f180d0010001`、`schema_up_to_date=true` |

- 本轮全部产出都是 **离线模拟（mock）**，**没有发生任何真实 H3 渲染，也不构成成片验收**。
- 本机 **没有任何可用文本模型**：`/api/v1/setup/llm-readiness` 返回 `status=BLOCKED`、`role_count=14`、14 个角色全部 `UNCONFIGURED / LLM_ROUTE_REQUIRED`；产品**明确拒绝** `kind='mock'` 的文本 profile（`REAL_LLM_PROVIDER_REQUIRED`），生产应用也从不装配 `mock_llm_responder`（只在测试里设）。本机无 Ollama、无任何本地 OpenAI 兼容端点。
- 由此：依赖文本模型的阶段（大纲提取、分集确认、分镜生成）在本机**无法被真实驱动**。这是环境事实，**不作为产品缺陷计**，但由此产生的**页面表述问题**计为缺陷（见 D1、D3）。
- 明确**未运行**的项目在第 3 节逐条列出，未做任何推测性描述。

## 1. 七阶段推进表

| # | 阶段 | 路由 | 状态 | 停在哪一步 / 原因 |
| --- | --- | --- | --- | --- |
| 1 | 创意与剧本 | `#d/creative` | **被阻断** | 建作品、导入剧本、授权文字发送均已跑通；卡在主按钮「后台提取并归并总体大纲」永久 disabled，理由与真实阻塞不符 → **D1** |
| 2 | 剧本统筹 | `#d/assets` | **被阻断** | 点「生成分镜书」→ 422 `SCRIPT_OUTLINE_NOT_CONFIRMED`（无动作按钮，D3c）；右侧「去剧本统筹」实际跳导演包（D2） |
| 3 | 镜头拆解 | `#d/produce` | **被阻断** | 点「生成分镜」→ 409，页面只显示裸码 `STORYBOARD_STYLE_NOT_FROZEN`（D3a） |
| 4 | 拍摄素材准备 | `#d/materials` | **被阻断** | 点「按已确认镜头生成素材清单」→ 409 `SCRIPT_OUTLINE_NOT_CONFIRMED`，有中文原因但无动作按钮（D3b） |
| 5 | 导演包 | `#d/package` | **被阻断** | 0 集可选；右侧主按钮「先选择要拍的集」disabled；页面指引让用户点一个**不存在**的按钮（D7）。`startable` 与派发口径**未验证** |
| 6 | 视频生产 | `#d/run` | **被阻断** | 无运行；右侧「第⑥步 · 视频生产」是空卡（D8）。一键开拍**未运行** |
| 7 | 后期成片 | `#d/deliver` | **被阻断** | 无运行记录、无成片；底部显示「全部阶段已完成」与「0 / 7 阶段完成」矛盾（D5） |

**明确未运行（不推测、不脑补）**

1. 大纲提取与确认、分集确认、分镜草案/应用/逐集确认 —— **未运行**（本机无文本模型，链路无法真实驱动）。
2. 「分镜已应用但下游报 `EPISODE_PLAN_NOT_MATERIALIZED`」的双台账不互通 —— **未运行**（从未产生已应用分镜，无从触发）。
3. 一键开拍把空槽位当欠缺（应报 `WORKFLOW_SIZE_NOT_MAPPED` 而非「缺参考图」）—— **未运行**（0 镜；且专业模式排产页明示「连续生产只支持真实 H3，演示态不能启动」，按钮为 disabled）。
4. 幂等键冲突是否假成功 —— **未运行**（全程零 Series / Run / Job 提交）。
5. 真实 H3 渲染与成片验收 —— **未运行**（`h3_connected=false`、`production_mode=DEMO`）。

## 2. 缺陷清单

### D1 · P1 · 创意与剧本：大纲主按钮永久禁用，且给出的阻塞原因与真实原因不符

- **症状**：第 1 阶段右侧卡片的主按钮「后台提取并归并总体大纲」始终为 disabled，旁边固定写着「先在上方授权剧本文字发送」；但用户**已经授权成功**（页面回显「已保存文字发送授权。」）。授权次数与按钮能否解禁无关。
- **可复现步骤**：
  1. 打开 `http://127.0.0.1:5180/` → 「项目管理」页 → 在「给作品起个名字」填入「巷口的灯」→ 点「创建作品」→ 自动进入 `#d/creative`。
  2. 滚到「入口 B · 我已有剧本」→ 点「选择并上传剧本」→ 选 `sample-script.md` → 页面出现「sample-script.md 5e105d71 当前依据」。
  3. 展开「文字模型发送授权」→ 点「允许向上述模型发送本剧本必要文字」→ 页面回显「已保存文字发送授权。」。
  4. 观察右侧卡片「第①步 · 创意与剧本」：主按钮仍 disabled，提示仍为「先在上方授权剧本文字发送」。
  5. 按 F5 整页刷新 → 状态完全不变（重载后按钮依旧 disabled）。
- **期望行为**：主按钮的禁用理由应说明**真实阻塞**（编剧角色没有配置模型），并给出「去配置文本模型」的入口；或直接采用能覆盖真实条件的判定。对照：同产品的「镜头拆解」页对同类问题给的是「AI 分镜能力未就绪：还没有配置模型服务。你仍然可以在专业模式手工新建分镜，或 **去配置模型服务**」——口径正确。
- **实际行为**：提示把用户引向**已完成**的动作；反复点授权也不解锁，主入口彻底卡死。
- **页面上的原始错误码 / 接口证据**：`GET /api/v1/projects/{id}/material-preparation/text-consent/{document_id}` 返回 8 个角色全部 `configured: false`，响应体中**根本不含 `allowed` 字段**；而主按钮的启用条件取的正是 `roles[role='scriptwriter'].allowed`。授权是否落库与按钮能否解禁之间没有这条因果链。
- **截图**：`.local-acceptance/eval-20261115/shots/04-script-imported.png`、`05-consent-granted.png`
- **影响面**：新用户旅程的第一道门。第 1 阶段无法完成 → 第 2–7 阶段全部被上游锁死；用户会反复做无效动作并误以为产品坏了。

### D2 · P1 · 恢复入口指错：按钮写着「去剧本统筹」，实际跳到导演包

- **症状**：第 2 阶段右侧卡片「待处理 · 2」里的按钮文案是「去剧本统筹」，点击后地址栏却变成 `#d/package`（导演包）。
- **可复现步骤**：
  1. 进入 `#d/assets`（剧本统筹）。
  2. 右侧卡片「第②步 · 剧本统筹」→「待处理 · 2」→「分集方案尚未确认」右侧的「去剧本统筹」按钮。
  3. 点击该按钮。
  4. 地址栏由 `http://127.0.0.1:5180/#d/assets` 变为 `http://127.0.0.1:5180/#d/package`。
- **期望行为**：跳到 `#d/assets`（剧本统筹），或者在当前页时干脆不提供这个动作。
- **实际行为**：跳到 `#d/package`（导演包），比来源阶段更靠后。
- **页面上的原始错误码**：`EPISODE_PLAN_NOT_CONFIRMED`。该码已收录于 `frontend/src/errorActions.ts`，映射为「去剧本统筹 / target: 'package-stage'」；而 `App.tsx` 里 `case 'package-stage' → goToDirectorPage('package')`。文案与目标指向不一致。
- **截图**：`.local-acceptance/eval-20261115/shots/14-stage2-blockers.png`
- **影响面**：凡是主阻塞为 `EPISODE_PLAN_NOT_CONFIRMED` 的入口，其恢复动作都把用户送到错误页面，绕圈而非推进。
- **裁定口径（2026-11-15 晚间补充）**：本条只保留**映射正确性**这一半——`EPISODE_PLAN_NOT_CONFIRMED` 的语义目标必须是 `#d/assets`（`plan-stage`），而不是 `#d/package`；映射单测（`target === 'plan-stage'` 且 `!== 'package-stage'`）必须保留，不得靠放宽门禁改绿。至于「目标页恰好就是当前页时按钮是否还该存在」，归 D4 统一裁定，两者不冲突：**异页要跳对，同页不自环**。

### D3 · P1 · 必做动作失败时只回裸错误码 / 没有「去处理」按钮（缺 `.required-action` / `.must-button`）

- **症状**：三个阶段的主按钮失败后，页面上要么只有一串裸错误码，要么有中文原因却没有可点击的下一步。三个实例表现一致。
- **可复现步骤**：
  - **a) 镜头拆解**：
    1. 进入 `#d/produce` → 右侧卡片「第③步 · 镜头拆解」→ 点「生成分镜」。
    2. 网络面板出现 `POST /api/v1/projects/{id}/director/storyboard/generate` → **409 Conflict**。
    3. 页面正文出现一行裸码 `STORYBOARD_STYLE_NOT_FROZEN`，**没有任何解释、没有动作按钮**。
  - **b) 拍摄素材准备**：
    1. 进入 `#d/materials` → 点「按已确认镜头生成素材清单」。
    2. `POST /api/v1/projects/{id}/director/materials/derive` → **409 Conflict**。
    3. 页面出现「先确认剧本大纲，再按剧本分集。（`SCRIPT_OUTLINE_NOT_CONFIRMED`）」——有中文原因，但**没有「去处理」按钮**。
  - **c) 剧本统筹**：
    1. 进入 `#d/assets` → 点「生成分镜书」。
    2. `POST /api/v1/projects/{id}/director/storyboard-book` → **422 Unprocessable Entity**。
    3. 页面出现「先确认大纲，再生成分镜书（`SCRIPT_OUTLINE_NOT_CONFIRMED`）」，同样**没有动作按钮**。
- **期望行为**：按 AGENTS.md 的产品要求，每个必做动作用 `.required-action` / `.must-button` 呈现，说清「要做什么」与「为什么必须做」，并给出可点的下一步；**裸错误码不得是屏上唯一信息**。
- **实际行为**：`ERROR_ACTIONS` 未收录 `STORYBOARD_STYLE_NOT_FROZEN` 与 `SCRIPT_OUTLINE_NOT_CONFIRMED`，`describeBlocker` 落到 fallback 分支；有中文原因的也没有配任何动作。
- **页面上的原始错误码**：`STORYBOARD_STYLE_NOT_FROZEN`、`SCRIPT_OUTLINE_NOT_CONFIRMED`
- **截图**：`12-generate-storyboard-click.png`、`13-materials-409.png`、`15-generate-storyboard-book.png`
- **影响面**：第 2/3/4 阶段的主入口失败后，用户拿不到可执行的下一步，只能自己猜或回去翻文档；同时直接违反产品自己写下的必做动作口径。

### D4 · P1 · 入口自环：「去创意与剧本」把用户送回他已经在的那一页

- **症状**：在 `#d/creative` 右侧卡片上点「去创意与剧本」，地址栏与页面**完全不变**，零进展；而且卡片宣称要解决的「还没有剧本」与服务器说法矛盾。
- **可复现步骤**：
  1. 进入 `#d/creative`（剧本已导入、状态显示 `sample-script.md 5e105d71 当前依据`）。
  2. 右侧卡片「第①步 · 创意与剧本」→ 点「去创意与剧本」。
  3. 地址仍是 `http://127.0.0.1:5180/#d/creative`；页面元素数 159、可见文本与点击前一致。
  4. 对照 `GET /api/v1/projects/{id}/journey`：`script` 阶段 `summary = "已有剧本「sample-script.md」，待整理大纲"`，而同一响应里 `blockers = [ { code: SCRIPT_NOT_PREPARED, message: "输入想法生成剧本，或导入现成剧本并确认大纲" } ]`。
- **期望行为**：已经处在本页时不应提供指向本页的动作；阻塞描述应与同一响应里的 summary 一致。
- **实际行为**：按钮构成**自环**；且 `/journey` 一个响应内部自相矛盾（summary 说已有剧本，blockers 仍说没有剧本）。
- **页面上的原始错误码**：`SCRIPT_NOT_PREPARED`
- **截图**：`04-script-imported.png`
- **影响面**：新用户首屏的「下一步」是死路；任何消费 `/journey` 的前端都可能取到错误的那一半，把「已有剧本」显示成「还没有剧本」。
- **裁定口径（2026-11-15 晚间补充）**：**同页不自环优先于「跳到本页」**。当动作的目标页等于当前页时，前端不应渲染这个跳转动作；该处应改为指向**真正能解开阻塞的下一步**。就本例而言，`/journey` 的 `plan` 阶段 `current_outline_artifact_id` 为空、`script` 阶段 `outline_confirmed=false`，说明 `EPISODE_PLAN_NOT_CONFIRMED` 的真正前置是第 1 阶段的大纲确认——因此在 `#d/assets` 本页应显示「去第 1 阶段确认大纲」，而不是「去剧本统筹」（那才是自环）。与 D2 合起来的完整口径是：**映射指向正确阶段 + 同页不自环 + 同页改指真正的上游前置**，三者同时成立才算修好。

### D5 · P2 · 最后阶段底部显示「全部阶段已完成」，与同屏「0 / 7 阶段完成」矛盾

- **症状**：`#d/deliver` 底部提示写「全部阶段已完成」，而同一屏的步骤条写「0 / 7 阶段完成」，服务器 `next_stage=script`。
- **可复现步骤**：
  1. 进入 `#d/deliver`（后期成片）。
  2. 看底部提示：「全部阶段已完成」。
  3. 看顶部步骤条：「0 / 7 阶段完成」；对照 `/journey`：7 个阶段 `done` 全为 `false`。
- **期望行为**：该位置表达的是「已到最后阶段，没有下一阶段」，不应写成「全部阶段已完成」。
- **实际行为**：文案把「没有下一阶段」说成「全部完成」，与屏幕上的进度自相矛盾。
- **页面上的原始错误码**：无（文案问题）。
- **定位**：`frontend/src/components/JourneyRail.tsx:144` — `{next ? `接下来：${next.text}` : '全部阶段已完成'}`
- **截图**：`.local-acceptance/eval-20261115/shots/11-stage7-deliver.png`
- **影响面**：误导用户以为作品已完工；与 AGENTS.md「没有服务端状态时不得显示『全部完成』」的约束冲突。

### D6 · P2 · 导演模式七阶段没有 `.required-action` / `.must-button` / `.module-guide`

- **症状**：导演层七个阶段页面上，DOM 查询 `[class*=required-action], [class*=must-button], [class*=module-guide]` 返回 **"Element not found"**——必做动作没有统一的呈现块。
- **可复现步骤**：
  1. 进入 `#d/package`（或其余任一阶段页）。
  2. 对页面执行 DOM 查询 `[class*=required-action], [class*=must-button], [class*=module-guide]` → 「Element not found」。
  3. 全仓检索：这三个类名只在 `frontend/src/components/StrategyResourceCard.tsx` 出现（`required-action` 185 行、`must-button` 190 行）；`module-guide` **全仓 0 命中**；`backend/tests` 中也没有任何针对它们的断言。
  4. 对照（专业模式 · 制片准备 `#v/preproduction`）：「这一步要做什么：…」「为什么必须做：…」「必做：冻结这一版风格」三件套齐全，说明这套口径存在，只是没覆盖导演主线。
- **期望行为**：导演七阶段与专业模式共用同一套必做动作块。
- **实际行为**：导演模式没有。
- **页面上的原始错误码**：无（样式/结构问题）。
- **截图**：`08-stage4-materials.png`、`09-stage5-package.png`
- **影响面**：用户不读文档就难以判断下一步该做什么、为什么必须做——正是本轮重点排查的行为 1 未达成。

### D7 · P2 · 导演包指引让用户去点一个页面上**不存在**的按钮

- **症状**：第 5 阶段页面写着「三步：勾选要拍的集 → 确认模型版本 → 点底部『确认拍摄方案并开始真实制作』」，但页面上没有这个按钮。
- **可复现步骤**：
  1. 进入 `#d/package`。
  2. 读第 5 阶段说明：「三步：勾选要拍的集 → 确认模型版本 → 点底部「确认拍摄方案并开始真实制作」」。
  3. 全页可见文本检索「确认拍摄方案并开始真实制作」→ **只出现 1 次**，就是这句指引本身；页面全部按钮为：模型版本 3 个、「检测所需资源」、「画面加速」2 个、「极速 · 4 步」、「先选择要拍的集」（**disabled**）、「上一阶段 / 回到项目管理 / 下一阶段」等，无该按钮。
- **期望行为**：指引指向的按钮必须真实存在并可点；不存在时不应写入指引。
- **实际行为**：指引不可执行；右侧主按钮是「先选择要拍的集」且 disabled，页面没有说明它为什么禁用。
- **页面上的原始错误码**：无。
- **截图**：`.local-acceptance/eval-20261115/shots/09-stage5-package.png`
- **影响面**：第 5 阶段完全不可操作，且用户被一条假指引带偏。

### D8 · P2 · 「视频生产」阶段的右侧任务卡是空卡

- **症状**：`#d/run` 右侧只有「第⑥步 · 视频生产」一个标题，下面没有任何内容、没有主按钮、没有引导；其余六个阶段都有主按钮或明确指向。
- **可复现步骤**：
  1. 进入 `#d/run`。
  2. 看右侧卡片：只有标题行，无正文。
- **期望行为**：空态也应给出当前该做什么（例如「到导演包选择集并确认拍摄方案」）。
- **实际行为**：正文里其实有「还没有运行。到「导演包」选择要拍的集并确认拍摄方案」这句话，但它在**左侧主区**，右侧任务卡是空的。
- **页面上的原始错误码**：无。
- **截图**：`.local-acceptance/eval-20261115/shots/10-stage6-run.png`
- **影响面**：第 6 阶段缺少入口指引，与 D6 的整体口径缺失一致。

## 3. 本轮跑通 / 通过的部分（同样要如实记录）

1. **新用户首屏可自解释**：`#d/projects` 首屏完整列出七阶段并各配一句说明（①创意与剧本「输入想法，或导入现成剧本」… ⑦后期成片「逐集剪辑与交付」），并明确「制作环境不足也不影响创建作品、导入剧本和整理素材」。符合「不读文档就能往下走」的要求。
2. **作品创建链路通畅**：填名 → 创建 → 自动进入 `#d/creative`，无异常。
3. **剧本导入链路通畅**：`.txt/.md/.json/.docx/.pdf` 上传后原文与内容哈希完整入库，页面显示 `sample-script.md 5e105d71 当前依据`，且明确「另一份剧本导入后只是候选，不会自动替换当前依据」。
4. **步骤条确实来自服务端 `/journey`**：阶段徽标上的数字与 `/journey` 的 blocker 个数一致（创意与剧本 1、剧本统筹 2）；页面明示「正在确认七个阶段的真实状态，完成情况只从本机服务端回填，读取完成前不会显示『全部完成』」。**行为 4（前端不另算一套）总体成立**，D4/D5 是该链路上的两处文案/语义缺口，不是整体另算一套。
5. **专业模式对照组明显更好**：制片准备页的「这一步要做什么 / 为什么必须做 / 必做：冻结这一版风格」完整；排产页有「现在为什么不能开始制作」清单并给出「为本作品一键登记生成方案」「去完成并应用分镜」等动作；一键开拍按钮为 disabled 且附「连续生产只支持真实 H3，演示态不能启动」的说明。**这些正确做法应当被导演主线复用**。
6. **专业模式存在绕过 LLM 的手工入口**：「手工新建或修改单个镜头」（AI 分镜之外也可手工建镜头），但前置条件是「风格基准还没冻结，先去『剧本统筹』完成冻结，才能建分镜」；本轮点「批准并冻结」**未发出任何请求**（网络面板无冻结相关 POST），按钮应为 disabled，页面也未说明缺的是哪几项。

## 4. 给修复师的移交建议（按修复顺序）

| 顺序 | 缺陷 | 修复落点（供定位，本轮未改代码） | 定向回归建议 |
| --- | --- | --- | --- |
| 1 | D1 | 主按钮禁用条件取 `role.allowed`；应在角色未配置时给出真实阻塞与「去配置文本模型」入口 | `OutlineConfirmationPanel`：授权成功但角色未配置 → 按钮禁用理由应显示「编剧未配置模型」并带跳转 |
| 2 | D2 | `errorActions.ts` 中 `EPISODE_PLAN_NOT_CONFIRMED` 的 `target: 'package-stage'` 与文案「去剧本统筹」不一致 | `errorActions.ts`：该码应指向 `#d/assets` |
| 3 | D3 | `ERROR_ACTIONS` 补 `STORYBOARD_STYLE_NOT_FROZEN` / `SCRIPT_OUTLINE_NOT_CONFIRMED`（及 `EPISODE_PLAN_NOT_MATERIALIZED`），并让分镜/素材/分镜书三类失败统一走 `describeBlocker` | 三个端点各一条：409/422 → 页面出现可点的「去处理」 |
| 4 | D4 | `/journey` 的 `script` 阶段：有 document 时应报大纲未确认类阻塞，而非 `SCRIPT_NOT_PREPARED`；同页不应给出指向本页的动作 | 导入剧本后 `/journey` 与卡片文案一致，且卡片动作不指向当前页 |
| 5 | D5 | `JourneyRail.tsx:144` 的兜底文案 | 最后阶段底部不应出现「全部阶段已完成」 |
| 6 | D6 | 把 `required-action`/`must-button`/`module-guide` 抽成共用块，复用到导演七阶段 | 导演任一阶段页 DOM 能查到这三类 |
| 7 | D7 / D8 | `#d/package` 指引与实际按钮对齐；`#d/run` 右卡补空态说明 | 文案与按钮一致性检查 |

## 4A. R2 移交覆盖对照与批次状态（2026-11-15 晚间更新）

**移交覆盖**：本报告 8 条缺陷 + 启动器缺陷，修复师侧已全部对应到定向回归。

| 缺陷 | 对应回归 | 状态（截至本次更新） |
| --- | --- | --- |
| D1 | `frontend/src/test/directorOutlineModelGate.test.tsx` | 已修（根因：单一布尔 `scriptwriterAllowed` 同时表达「角色没配模型」与「没授权」；改为三态化，理由按真实阻塞分支） |
| D2 | `frontend/src/test/directorBlockerNavigation.test.tsx` | 映射已改为 `plan-stage`；**用例仍在红**，见下方裁定 |
| D3 | 共用必做块 `frontend/src/components/RequiredAction.tsx` | 已修 |
| D4 | `frontend/src/test/directorSelfLoopAction.test.tsx` + `backend/tests/test_director_journey_script_blockers.py` | 已修（后端 blocker 随事实分叉；前端同页不再给指向本页的动作） |
| D5 | `frontend/src/test/journeyBridgeStageHint.test.tsx` | 已修。**更正一处**：此文件不是「额外覆盖」，它正是 D5 的定向回归（3 项：不再出现「全部阶段已完成」、按钮 title 不再含「交付完成」、停在中间阶段仍指向真正的下一阶段） |
| D6 | `frontend/src/test/directorRequiredAction.test.tsx`、`directorModuleGuide.test.tsx` | 已修（抽出共用块 + 补样式）；**module-guide 用例仍在红** |
| D7 / D8 | `frontend/src/test/directorPackageGuide.test.tsx` | 已修（指引按真实按钮名与禁用原因生成；`#d/run` 无运行时右栏给出「去导演包选择要拍的集」） |
| 启动器 stdin | `backend/tests/test_start_shine_launcher.py` | 已修并 2/2 绿。**过程坑**：`RedirectStandardInput='NUL'` 在 PowerShell 7 会被当普通路径拒绝、`Start-Process` 直接失败（会让工作台完全启动不了），最终改用真实空文件 |

**D2/D4 冲突裁定（由缺陷提出方给出）**：映射单测必须保留、不得放宽门禁改绿；同页不自环的抑制保留；两者合起来要求同页改指真正的上游前置。详见 D2、D4 条目的「裁定口径」。

**批次尚未收口（不能据此派全量回归）**：后端 8 分片回归仍在跑且 shard-1 出现一个 F 未定位；前端全量 67 文件通过 / 2 文件失败，两个失败文件都在并行会话正在改的文件上；D2/D4 冲突未收敛；D4、D7、D8 尚未修；**未提交任何 commit**，工作树 31 项改动，且当前 HEAD 在 `fix/stack-lifecycle-dependencies-20261115` 而非 `main`，本轮成果尚未落在 main。

**诚实边界（不变）**：以上均为**代码层与前端回归**层面的结论。本机 `h3_connected=false`、`production_mode=BLOCKED`，D1–D8 的**真实生产验收尚未开始**，不得据本节宣称真实 H3 渲染或成片验收通过。

## 4B. R2 修复后的浏览器实测复核（2026-11-15 晚间）

**复核环境**：同一隔离实例 `eval-20261115`，已用 `stop_shine.py` + `start_eval_instance.ps1` **重启并装载新后端代码**（本次刻意不挂保活）。

| 缺陷 | 实测结果 | 证据 |
| --- | --- | --- |
| D1 | **已修**。主按钮禁用理由改为「编剧角色还没有配置模型，无法提取大纲」，并给出「去配置文本模型 ›」出口；页面另有一段「为什么必须做：总体大纲由编剧角色调用模型提取。角色没有模型时，授权与提取都不会真正发生，按多少次授权也解不开主按钮。」——与 D1 期望口径一致 | `16-creative-after-fix.png` |
| D4（后端） | **已修**。`/journey` 的 `script` 阶段 blockers 由 `SCRIPT_NOT_PREPARED` 变为 **`SCRIPT_OUTLINE_NOT_CONFIRMED`**，且 summary 仍为「已有剧本「sample-script.md」，待整理大纲」——同一响应内部不再自相矛盾 | `16-creative-after-fix.png` |
| D4（前端） | **已修**。卡片由「还没有剧本 / 去创意与剧本」改为「剧本大纲还没有确认」，指向本页的动作消失，入口自环消除 | `16-creative-after-fix.png` |
| D6 | **已修**。DOM 可查到 `.module-guide`：「这一步要做什么：写想法或导入剧本；确认大纲后进入统筹。/ 为什么必须做：大纲是后面分集、分镜和导演包的共同依据；没确认就没有可拍的依据。」；`#d/assets` 同样有「为什么必须做：分集方案与冻结风格是导演包冻结的前提；没定稿就不能派发。」 | `16-creative-after-fix.png`、`17-assets-after-fix.png` |
| D2 | **部分已修**。按 D2/D4 的裁定口径，`#d/assets` 本页的「去剧本统筹」按钮已消失（自环抑制生效），页面不再提供指向本页的动作 | `17-assets-after-fix.png` |
| 启动器 stdin | **已修且实证**。本次重启**刻意不挂保活**：父 shell 退出后实例仍健康（8010/5180 监听、`/health` ok、Web 200），`web.err.log` 为空、无 EPIPE。修复前同一配方在父 shell 退出后 vite 必然 `read EPIPE` 崩溃、API 一并消失 | `16-creative-after-fix.png` |

**残留缺口（裁定第 3 条未落地）**：`#d/assets` 本页上「分集方案尚未确认」旁**没有**给出指向真正上游前置的「去第 1 阶段确认大纲」动作；用户目前只能靠页面正文「先确认大纲（第 1 阶段），再在这里生成分镜书。」与顶部步骤条的「下一步 创意与剧本」找到路径。建议在 blocker 旁直接给出该动作，否则「同页不自环」只是把死路隐藏成了死胡同。

**诚实边界**：以上仅为界面与接口层的复核。本机 `h3_connected=false`、`production_mode=BLOCKED`，**D1–D8 的真实生产验收仍未开始**，不得据本节宣称真实 H3 渲染或成片验收通过。

## 5. 证据清单（截图）

目录：`.local-acceptance/eval-20261115/shots/`

| 文件 | 内容 | 对应缺陷 |
| --- | --- | --- |
| `01-first-screen.png` | 新用户首屏（七阶段说明） | 通过项 1 |
| `02-project-name-filled.png` | 填写作品名 | — |
| `03-creative-page.png` | 第 1 阶段创意与剧本 | D1 |
| `04-script-imported.png` | 剧本已导入（当前依据）+ 卡片仍写「还没有剧本」 | D1 / D4 |
| `05-consent-granted.png` | 授权已保存，主按钮仍禁用 | D1 |
| `06-projects-list.png` | 项目管理页（1 个作品，下一步=创意与剧本） | — |
| `07-stage3-produce.png` | 第 3 阶段镜头拆解（正确给出「还没有配置模型服务」+ 去配置模型服务） | 对照通过项 5 |
| `08-stage4-materials.png` | 第 4 阶段拍摄素材准备 | D6 |
| `09-stage5-package.png` | 第 5 阶段导演包（主按钮禁用、指引指向不存在的按钮） | D6 / D7 |
| `10-stage6-run.png` | 第 6 阶段视频生产（右卡为空） | D8 |
| `11-stage7-deliver.png` | 第 7 阶段后期成片（底部「全部阶段已完成」） | D5 |
| `12-generate-storyboard-click.png` | 生成分镜 409 → 裸码 `STORYBOARD_STYLE_NOT_FROZEN` | D3a |
| `13-materials-409.png` | 素材清单 409 → `SCRIPT_OUTLINE_NOT_CONFIRMED` 无动作按钮 | D3b |
| `14-stage2-blockers.png` | 第 2 阶段卡片「去剧本统筹」（实际跳导演包） | D2 |
| `15-generate-storyboard-book.png` | 生成分镜书 422 → `SCRIPT_OUTLINE_NOT_CONFIRMED` 无动作按钮 | D3c |
| `16-creative-after-fix.png` | R2 修复后创意页：`.module-guide` 落地、阻塞理由改口、入口自环消除 | 4B：D1/D4/D6 |
| `17-assets-after-fix.png` | R2 修复后剧本统筹页：同页自环按钮已抑制 | 4B：D2 |

## 6. 收工声明

- 本轮**未修改任何产品代码**，未触碰 `.local-data/` 里用户的真实项目与数据；所有业务数据写在隔离目录 `.local-acceptance/eval-20261115/`。
- 未执行 `git reset/checkout/stash`，未覆盖工作树上 30 个他人的在途改动。
- 本轮**没有**真实 H3 渲染、**没有**真实成片；所有涉及生产的结论均为**离线模拟**。
- 未运行的阶段与未验证的目标已在第 1 节与第 3 节逐条列明，不做推测性描述。
