# 素材与图片链路实测报告（R1 · 美术师）

- 报告文件：docs/TEST_REPORT_20261115_ASSET_IMAGE_JOURNEY_r1-20261115-assets.md
- run-id：`r1-20261115-assets`
- 隔离实例：API `http://127.0.0.1:8020` / Web `http://127.0.0.1:5190`
- 隔离数据目录：`.local-acceptance/r1-20261115-assets`（未触碰 `.local-data/`）
- 证据目录：`.local-acceptance/r1-20261115-assets/shots/`（39 张 PNG）、脚本 `assets_journey*.py`、日志 `journey_log*.json`
- 测评人：美术师（bot_b6f37e74112d）；**本轮未修改任何业务代码**，只新增本报告与隔离目录内证据
- 日期：2026-11-15

## 0. 诚实边界（先声明）

本轮**全部为离线模拟**。实例基线（`GET /api/v1/system/status`，只读实测）：

```
api_online=true, worker_online=true, mock_provider_available=true,
h3_connected=false, render_mode=mock,
production_mode = {mode: DEMO, runtime_status: NOT_ACTIVATED, renderer_online: false}
```

**没有发生任何真实 H3 渲染、没有真实出图、没有成片验收**。本报告中凡涉及「出图 / 渲染 / 租约」的结论，要么是界面静态文案与请求层观察，要么明确标注「未运行」。

另：本文档由测评员撰写，只描述**在界面上真实点出来的现象**。对前端源码的定位仅用于解释已复现的现象，不作为缺陷成立的前提。

---

## 1. 素材与图片链路实测清单（推进表）

| 环节 | 状态 | 卡在第几步 / 说明 |
| --- | --- | --- |
| 素材中心可达性（#a/home、#a/images、#a/voices、#a/library、#a/assignments） | **已跑通** | 五页均可直达并渲染 |
| 新建作品（导演模式首页 → 起名 → 创建作品） | **已跑通** | 作品名「测评-r1-assets-01」，落在 `#d/creative` |
| 全局素材：导入图片 | **已跑通** | 导入后版本状态「待技术检查」，未自动生效 |
| 全局素材：人工晋升为正式版本 | **已跑通** | 点「检查并人工晋升」→ 状态变「已晋升」（见 A3） |
| 项目素材：上传 → 勾选 → 「确认使用」 | **已跑通** | 弹原生确认框；接受后 `approved=true, locked=true` 落库 |
| 全局已晋升版本 → 分配到作品 | **已跑通（A1 修复后）** | 直接进入已能分配成功；项目内已有同内容素材时服务端 500（A6，见 §7） |
| 图片工坊：文生图 / 图生图 / 局部修订 | **被阻断（第 1 步）** | 主按钮 disabled「等待本机能力验证」；出图配置返回 `CATALOG_VARIANT_UNDECLARED`（A4） |
| 出图候选 → 人工晋升为主素材 | **未运行** | 依赖真实出图，被能力门挡在第 1 步 |
| 工作流参考槽位映射 `PENDING_WORKFLOW_MAPPING` | **未运行** | 同上，无法触达提交阶段 |
| 图像槽位家族顺序（主体/画面/视频/音频） | **未运行** | 同上 |
| `IMAGE_WORKER_LOST` / 「再生成一版」显式按钮 | **未运行** | 同上 |
| 出图被 GPU 租约卡住时的说明与下一步 | **未运行** | 同上 |
| 角色绑定：proposal → 人工逐行确认 → apply | **被阻断（第 1 步）** | 本机无文本模型 → 无法生成 AI 分镜方案 → 无 breakdown → 无人物对照/proposal（A5） |
| `CHARACTER_NAME_ALREADY_IN_USE` 重名拒绝 | **未运行（界面层）** | 后端 `api_film.py:1945` 确有 409 抛出，但界面上走不到该步 |
| 导演模式「拍摄素材准备」#d/materials 生成素材清单 | **被阻断（第 1 步）** | 具名阻断 `SCRIPT_OUTLINE_NOT_CONFIRMED`（见 §3 正向结论） |
| 风格冻结（专业模式「批准并冻结」） | **被阻断** | 按钮 disabled，未能推进 |

**一句话总结**：素材中心自身的「导入 → 待技术检查 → 人工晋升 → 确认使用 → 锁定」链路**全部跑通且不自动生效**；卡住的是**一切需要真实出图**和**一切需要文本模型**的下游环节。

---

## 2. 缺陷清单

### A1 【P1】直接进入「项目分配」页时，分配必然失败（界面显示已选，内部状态为空）

> **状态：已修复并复测通过（2026-11-15 02:3x）。** 修复方改动 `frontend/src/asset-center/AssetsWorkspace.tsx` + `frontend/src/test/assetsWorkspace.test.tsx`（后端未动）。本轮在隔离实例上复测「全新会话直接进入 `#a/assignments`」：请求正常发出并返回 **201**，页面显示「已分配到作品。项目素材编号：…」，台账新增使用记录。复测证据见 §7。

- **症状**
  从素材中心导航直接打开 `#a/assignments`：页面上「目标作品」显示着「测评-r1-assets-01」、「全局素材版本」已选「sample_asset · V1」、「作品内身份名称」已填「林岚」，三个控件看上去都是就绪的；点「分配到作品」后**没有任何网络请求**，页面只出现一条红色提示：
  「请选择作品和已晋升版本，并填写作品内素材身份。」

- **编号复现步骤**
  1. 浏览器打开 `http://127.0.0.1:5190/#a/assignments`（全新会话，**不先访问导演模式任何页面**）
  2. 「全局素材版本」下拉选「sample_asset · V1」
  3. 「作品内身份名称」填「林岚」
  4. 点「分配到作品」
  5. 观察：**无网络请求**，页面出现上述提示

- **对照实验（A/B）**
  | 入口路径 | 结果 |
  | --- | --- |
  | 直接 `#a/assignments` | 失败，无请求，弹「请选择作品和已晋升版本…」 |
  | 先 `#d/projects` → 再 `#a/assignments` | 成功，`POST /api/v1/asset-center/assignments` 200，提示「已分配到作品。项目素材编号：…」 |

  A/B 两次点击前读取的 DOM 值完全相同（目标作品 select 值 = `43a83a6f…`），差异只在**进入路径**。

- **期望行为**
  直接进入素材中心时也能完成分配；或控件应如实呈现「未选择作品」的状态，而不是看上去已选。

- **实际行为**
  控件显示与内部状态不一致：受控下拉的 `targetProject` 初始值取自 `useState(projectId || projects[0]?.id || '')`，直接进入时父组件传入的 `projectId` 为空、`projects` 又在首帧之后才异步加载完成，state 不会被回填；`select` 找不到匹配 option 时浏览器自动回落到第一项，于是**看上去已选中，实际 state 为空**，提交前的校验因此失败。
  （定位依据：`frontend/src/asset-center/AssetsWorkspace.tsx:1028,1070` 与 `frontend/src/App.tsx:696` 的 `projectId={projectId}` 传参。）

- **页面原始错误码**
  无服务端错误码——请求根本没有发出，页面只显示前端本地文案「请选择作品和已晋升版本，并填写作品内素材身份。」

- **截图路径**
  - 失败：`.local-acceptance/r1-20261115-assets/shots/41-assign-direct-entry.png`
  - 成功对照：`.local-acceptance/r1-20261115-assets/shots/42-assign-after-director.png`
  - 补充：`37-assign-click-result.png`、`40-assign-with-second-project.png`

- **影响面**
  素材中心「项目分配」从**素材中心自身主入口 100% 不可用**；只有先在导演模式选定作品、再跳回分配页才可用。所有需要把全局已晋升版本分配到作品的流程都会卡死，且没有可自行绕过的界面出路。

---

### A2 【P2】分配失败的报错文案把三个条件混写，且与实际不满足的条件不符

- **症状**
  失败提示为「请选择作品和已晋升版本，并填写作品内素材身份。」，把「作品」「已晋升版本」「作品内素材身份」三个条件并列抛出。

- **编号复现步骤**
  1. 同 A1 第 1～4 步
  2. 观察错误文案

- **期望行为**
  只提示**实际未满足**的那一个条件，或逐字段定位。

- **实际行为**
  实际只有「目标作品」未满足（版本已选、身份名称已填，界面均已就绪）；用户按提示去反复检查「作品内素材身份」毫无帮助，只能靠试错猜入口顺序。

- **页面原始错误码**
  同 A1（前端本地文案，无服务端码）

- **截图路径**
  `.local-acceptance/r1-20261115-assets/shots/41-assign-direct-entry.png`

- **影响面**
  与 A1 叠加：用户既拿不到可执行的下一步，也拿不到准确的失败原因。

---

### A3 【P2】「检查并人工晋升」单击直达，界面未把「技术检查」与「人工晋升」分开呈现

- **症状**
  素材中心「全局素材库」→ 选中导入的图片 → 点「检查并人工晋升」：**没有确认弹窗、没有检查结论摘要**，版本状态立刻从「待技术检查」变成「已晋升」，随后出现一行「已人工晋升；项目版本引用不会随全局升级而改变。」

- **编号复现步骤**
  1. `#a/library` → 点「导入图片」→ 选择一张 PNG
  2. 列表出现该素材，版本行显示「V1 · 待技术检查」
  3. 点「检查并人工晋升」
  4. 观察：**无二次确认**，状态直接变为「已晋升」，按钮消失

- **期望行为**
  按钮文案承诺的是「检查 + 人工晋升」两步，界面应至少先展示技术检查结论，再由用户确认晋升。项目侧同类操作（「确认使用」）就有原生确认框，两条路径的确认强度不一致。

- **实际行为**
  一次点击即完成晋升；用户看不到「检查」的结论，也就无法基于结论做「晋升 / 不晋升」的判断。

- **页面原始错误码**
  无（成功路径，无错误码）

- **截图路径**
  `.local-acceptance/r1-20261115-assets/shots/16-library-selected.png`（待技术检查）、`17-promote-dialog.png`（点后已晋升）

- **影响面**
  候选 → 主素材的人工确认强度在两条路径上不一致；容易让用户以为「检查」与「晋升」是同一步。

---

### A4 【P2】素材出图在本机无法登记方案，出图按钮永久禁用，且错误码未进入用户可见文本

- **症状**
  素材中心「图片工坊」：文生图/图生图/局部修订等主按钮一律 **disabled**，按钮文字是「等待本机能力验证」，旁边有「打开本机出图配置」。进入基础配置后，「素材出图」一行显示「已找到」，但后台探测本机部署目录时返回 409。出图因此没有任何可执行入口。

- **编号复现步骤**
  1. `#a/images` → 观察主按钮为禁用，文案「Qwen-Image-2.1 T2I 需要本机模型、工作流和真实小样通过。」
  2. 点「打开本机出图配置」→ 跳到基础配置页
  3. 在网络层观察：`POST /api/v1/setup/existing-installations/inspect`（body 含 `catalog=qwen-image-2.1-windows-x64`）→ **409**
  4. 响应体：`{"code":"CATALOG_VARIANT_UNDECLARED","message":"这个部署目录没有声明变体身份，不能确定它该登记哪些生成方案。","catalog_id":"shine-qwen-image-21-windows-x64"}`

- **期望行为**
  阻塞原因应能落到界面上，并给出可操作的下一步（声明变体身份 / 改选部署目录 / 换方案目录）。

- **实际行为**
  `CATALOG_VARIANT_UNDECLARED` 只出现在接口响应里，用户可见文本只有「等待本机能力验证 / 需要本机模型、工作流和真实小样通过」，无法据此定位到「部署目录缺少变体身份声明」这一具体原因。

- **页面原始错误码**
  `CATALOG_VARIANT_UNDECLARED`（HTTP 409，仅接口层可见）

- **截图路径**
  `.local-acceptance/r1-20261115-assets/shots/13-images-t2i-blocked.png`、`14-images-i2i-tab.png`、`36-images-open-setup.png`

- **影响面**
  本机一切出图能力不可用；连带 `PENDING_WORKFLOW_MAPPING`、`IMAGE_WORKER_LOST`、GPU 租约三项**在界面上无法被触达验证**（本轮据此写「未运行」）。注：这台机器的 ComfyUI 部署目录属环境问题，产品侧已有「打开本机出图配置」的出口，故定为 P2 而非 P1。

---

### A5 【P1】角色绑定链路在素材相关页面上没有入口，且被「无文本模型」卡在第一跳

- **症状**
  「角色绑定」只存在于后端接口（`/projects/{id}/storyboard/character-binding/proposal` 与 `.../apply`）和「AI 自动分镜」流程内部。素材中心五个页面、专业模式「素材与风格 / 制片准备」都没有任何可点的角色绑定控件，只有文字说明：「在『AI 导演制作』由编剧提出名字和名图对应，再由你二次确认。」想走通 proposal → 逐行确认 → apply，必须先跑通 AI 分镜。

- **编号复现步骤**
  1. `#d/creative` 导入剧本 `sample_script.txt` → 上传成功（「当前依据」已更新）
  2. 观察第 1 阶段：「后台提取并归并总体大纲」被挡住，提示「编剧角色还没有配置模型，无法提取大纲」+「去配置文本模型」；七个工种全部显示「未配置」
  3. `#d/produce` → 「生成 AI 分镜方案」不可用：「AI 分镜能力未就绪：还没有配置模型服务」
  4. 素材准备区（专业模式 → 制片准备）→「② 确认需求 · 0 项」「③ 选择素材 · 0 项候选」；页面给出「③ 镜头拆解还没有完成：素材按定稿分镜准备」
  5. 因此 **人物对照 / proposal 界面在本轮无法出现**

- **期望行为**
  素材准备阶段应能直接进入角色对照，或至少在无 breakdown 时给出「先完成 AI 分镜」的可点击出口（专业模式里已有「去③镜头拆解」按钮，素材中心侧没有）。

- **实际行为**
  素材中心侧没有任何入口，也没有失败原因；用户只会看到「0 项候选 / 0 项需求」这类结果性数字，看不出缺的是 AI 分镜。

- **页面原始错误码**
  本轮界面上依次观察到：`SCRIPT_OUTLINE_NOT_CONFIRMED`（#d/materials 生成清单）、「编剧角色还没有配置模型」（无独立错误码）。`CHARACTER_NAME_ALREADY_IN_USE` 在后端 `api_film.py:1945` 存在（409），**本轮无法在界面上验证**。

- **截图路径**
  `.local-acceptance/r1-20261115-assets/shots/18-creative-after-upload.png`、`15-materials-generate-list.png`、`32-advanced-preparation-open.png`

- **影响面**
  「角色按 entity_id 关联脚本角色与主素材、tags 不参与匹配」「歧义/重名以 CHARACTER_NAME_ALREADY_IN_USE 拒绝而不是替用户猜一个」这两条核心不变量，本轮**无法在界面上端到端验证**，属链路级阻断。

---

## 3. 本轮实测通过的（正向结论，同样是证据）

1. **候选不会被自动晋升成主素材。** 导入图片后版本状态是「待技术检查」，只有显式点击「检查并人工晋升」才变「已晋升」（`shots/10-library-after-import.png` → `shots/17-promote-dialog.png`）。界面没有把「建议」显示成「已生效」。
2. **项目素材必须人工确认才会锁定。** 上传 → 勾选「全选待确认素材」→ 点「确认使用」→ **弹原生确认框**（文案「确认使用这 N 项素材？请确认内容无误且你有权使用。确认后将固定素材版本。」）→ 接受后接口层 `approved=true, locked=true`、`approved_at/locked_at` 落库。未确认前始终是「待你确认」。
3. **分配请求是幂等且带身份的。** `POST /api/v1/asset-center/assignments` 请求体含 `project_id / library_version_id / role / entity_id / idempotency_key`。⚠️ 更正：R1 期间我曾把「经 `#d/projects` 进入能发出分配请求」写成「可成功」——那是错的，那次同样返回 500，只是我当时的文本过滤条件漏掉了裸错误。详见 §7。
4. **导演模式第 4 阶段的阻断是具名且可操作的。** 点「按已确认镜头生成素材清单」→ 提示「先确认剧本大纲，再按剧本分集。（SCRIPT_OUTLINE_NOT_CONFIRMED）」，并给出「去确认大纲」按钮以及「这一步要做什么 / 为什么必须做」两段解释——符合团队对「必做动作块」的要求。
5. **隔离性良好。** 基础配置页「作品输出目录」显示的正是本次隔离目录 `.local-acceptance/r1-20261115-assets`，未写入 `.local-data/`。
6. **素材中心五页的「本机能力」是分项如实显示的**：文生图/图生图/多图合成/局部修订/透明 PNG 标「待本机验证」，声音标「本机声音运行时未就绪」，没有把未验证说成可用。

---

## 4. 复现环境与命令

```
起实例（隔离，不碰 .local-data）：
  pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/start_eval_instance.ps1 \
       -DataDir .local-acceptance/r1-20261115-assets -ApiPort 8020 -WebPort 5190

浏览器驱动（仓库既定配方，Chromium 已装）：
  uv run --with playwright==1.64.1 python .local-acceptance/r1-20261115-assets/assets_journey13.py
  # 注：脚本内的 chromium 路径按 %LOCALAPPDATA%\ms-playwright 下已装版本取用
```

脚本清单（全部只做界面操作，无 API 直改业务状态）：
`assets_journey.py`（八入口页巡检）、`assets_journey2.py`（导入/晋升/清单）、`assets_journey3.py`（弹窗/剧本/专业模式）、`assets_journey4~14.py`（上传确认/分配/A-B 对照/冻结风格）。日志 `journey_log*.json`、截图 `shots/*.png`。

---

## 5. 移交建议（按可修优先级）

1. **A1** 修 `AssignmentsPage` 的 `targetProject` 初值与作品列表异步加载之间的竞态（并让下拉在无作品时显示未选），A2 顺带把错误文案拆到字段级。
2. **A5** 给素材中心补一个「角色对照 / 先完成 AI 分镜」的出口，让用户知道缺的是分镜而不是素材。
3. **A4** 把 `CATALOG_VARIANT_UNDECLARED` 这类探测失败接进用户可见文案与动作按钮。
4. **A3** 评估「检查并人工晋升」是否应拆成「看检查结果 → 确认晋升」两步，与项目侧「确认使用」的确认强度对齐。

---

## 6. 附录：环境事故披露（测评员自身操作失误）

本轮 00:46 的**第一次**实例启动，我用的是 `scripts/start_shine.ps1`，该脚本在隔离数据目录没有依赖回执时会执行 `npm ci`——它先删除 `frontend/node_modules`，在本机 vite 占用 `esbuild.exe` 的情况下以 EPERM 失败，**共享的 node_modules 当时被清空（vite 整目录消失）**。这违反了团队红线「绝不执行 npm ci / npm install」，责任在我。该目录已于 01:03 由他人用 `npm --prefix frontend ci` 还原（194 包），此后本轮所有操作均改用 `scripts/start_eval_instance.ps1`（复用现有依赖、不重装），并在 8020/5190 上完成全部测评。
记录在此，避免下一位测评员重蹈；也请总控确认该次依赖重装未影响用户 8000/5173 的真实工作台数据。

---

## 7. R2 复测与更正（2026-11-15 02:3x）

### 7.1 A1 复测：**已修复并通过**

修复方改动：`frontend/src/asset-center/AssetsWorkspace.tsx`、`frontend/src/test/assetsWorkspace.test.tsx`（后端未动、无接口改动）。

本轮在**隔离实例**（API 8020 / Web 5190，仓库同一份源码）上按原始复现路径实测：

1. 全新浏览器上下文 → **直接**打开 `http://127.0.0.1:5190/#a/assignments`（不先访问任何导演页面）
2. 读取控件状态：「目标作品」已回填为「测评-r1-assets-01」（值 = `43a83a6f-9608-4e55-a4ca-479f5ee1fcda`，**不再是空值 + 浏览器回落的假象**）
3. 「全局素材版本」选 `sample_asset2 · V1`、「作品内身份名称」填「林岚3」
4. 点「分配到作品」
5. **实测结果**：`POST /api/v1/asset-center/assignments` → **HTTP 201**，响应体含 `project_asset_id`；页面显示「**已分配到作品。项目素材编号：9ad5ded3-473b-4d08-825e-fc4d68486aa9**」；「使用记录」出现「素材 → 43a83a6f · generic · 林岚3 · local · 9ad5ded3」

证据：`.local-acceptance/r1-20261115-assets/shots/49-assign-distinct-file.png`、`47-assign-direct-entry-after-fix.png`

### 7.2 更正：R1 期间我对 A1 的「B 路径可成功」结论是错的

R1 里我用「发出请求即算成功」判定过「经 `#d/projects` 进入能成功」。复测证明：**那条路径同样返回 500**，我当时的文本过滤只认「已分配 / 请选择 / 编号」，漏掉了界面上的裸英文「500 Internal Server Error」。台账 `GET /api/v1/asset-center/assignments` 在当时返回 `{"items":[]}`，其实一条都没落库。

**更正后的真实结论**：分配链路当时**从未成功过**；A1 只是把「请求根本没发出去」变成了「请求发出后被服务端打回」。

### 7.3 新缺陷 A6【P1】「分配到作品」在项目已有同内容素材时服务端 500，界面显示裸英文

- **症状**
  当项目里已经存在**同一份内容**（同 sha256）的素材时，把全局已晋升版本分配进该项目，服务端抛未处理异常返回 **HTTP 500**，界面把裸英文「**500 Internal Server Error**」直接显示给用户，没有任何中文说明、没有可操作的下一步。

- **编号复现步骤**
  1. `#a/library` 导入一张 PNG → 「检查并人工晋升」→ 已晋升（得到一个全局已晋升版本）
  2. `#v/assets`（专业模式）上传**同一张 PNG** → 「确认使用」→ 该素材在项目内已批准并锁定
  3. `#a/assignments` 选这个全局已晋升版本 → 填身份名 → 点「分配到作品」
  4. **实际**：`POST /api/v1/asset-center/assignments` → **500 Internal Server Error**；页面显示「500 Internal Server Error」；台账无新增记录

- **对照实验（已实测）**
  | 场景 | 结果 |
  | --- | --- |
  | 项目内**没有**该内容（换一张内容不同的 PNG） | **201**，成功，页面显示「已分配到作品。项目素材编号：9ad5ded3-…」，台账新增记录 |
  | 项目内**已有**该内容（同 sha256） | **500**，页面裸英文错误，台账无记录 |

- **期望行为**
  同一份内容重复分配应当**幂等复用**已有项目素材，或以具名错误码明确拒绝（例如 `ASSET_ALREADY_IN_PROJECT`）并给出下一步（如「该内容已在项目中，直接使用现有素材」）。按团队口径，未处理异常不应直接变成用户可见的裸 500。

- **实际行为**
  `backend/shine/asset_center/service.py:788-819` 无条件 `db.add(project_asset); db.flush()`，对 `(project_id, sha256)` 唯一约束冲突没有任何复用或具名拒绝逻辑：

  ```
  sqlalchemy.exc.IntegrityError: (sqlite3.IntegrityError) UNIQUE constraint failed: assets.project_id, assets.sha256
  [SQL: INSERT INTO assets (id, project_id, filename, kind, content_type, sha256, ...)]
  ```

  异常自 `api_asset_center.py:1507 → service.py:818 → db.flush()` 冒泡到 FastAPI 默认处理器 → 500。

- **页面原始错误码**
  HTTP **500**，响应体 `Internal Server Error`（未带业务错误码）。前端 `errorText(reason)` 的兜底把这段英文原样渲染到页面上。

- **截图路径**
  `.local-acceptance/r1-20261115-assets/shots/47-assign-direct-entry-after-fix.png`（500 裸英文态）、`49-assign-distinct-file.png`（201 成功对照）

- **影响面**
  「全局已晋升版本 → 分配到作品」在**目标项目已有同内容素材**这一常见情形下彻底失败，且失败信息完全不可操作（裸英文、无下一步）。同时暴露前端的一个通用问题：**服务端 500 时没有任何中文兜底与动作按钮**（与团队「原始错误码不是屏上唯一信息」「必做动作块」口径不符）。建议同时补后端幂等/具名拒绝与前端错误兜底两层。

### 7.4 本轮复测后仍未运行的阶段（不变）

`PENDING_WORKFLOW_MAPPING`、`IMAGE_WORKER_LOST` / 「再生成一版」显式按钮、GPU 租约说明与下一步、图像槽位家族顺序、出图候选人工晋升——仍全部**未运行**，原因不变：本机出图能力门（`CATALOG_VARIANT_UNDECLARED`）把链路挡在第 1 步，无真实出图可做。诚实边界不变：全程离线模拟，`h3_connected=false`、`production_mode=DEMO`，未发生真实 H3 渲染，不构成真实链路验收。


---

## 8. R2 第二轮复测：A6 关闭、A7 新增（合并缺口型回归）（2026-11-15 03:5x–04:2x）

### 8.1 A6 复测：**修复通过** ✅

修复方本轮改动（仍在共享工作树**未提交**状态）：`backend/shine/asset_center/service.py`（+74/-1，幂等复用 + 具名拒绝）、`frontend/src/api.ts`（+6，只替换 FastAPI 默认 500 文案）、`frontend/src/test/apiError.test.ts`（+9）、新增 `backend/tests/test_asset_assignment_duplicate.py`。

浏览器实测（隔离实例 API 8020，Web 5191/5192）：

| 场景 | 预期 | 实测 |
| --- | --- | --- |
| 项目内**已有**同内容素材（sha256 相同） | 幂等复用或具名拒绝 | **409**，响应体 `{"detail":{"code":"ASSET_ALREADY_IN_PROJECT","message":"这份内容已经在项目里（现有用途：generic，现有身份：林岚3）。请直接使用项目里已有的那份；若要登记成另一个身份，请先在项目素材里移除现有那份再分配。","existing_asset_id":"9ad5ded3-473b-4d08-825e-fc4d68486aa9","existing_role":"generic","existing_entity_id":"林岚3"}}` |
| 界面呈现 | 中文说明 + 现有身份 + 下一步 | 页面显示「**这份内容已经在项目里（现有用途：generic，现有身份：林岚3）。请直接使用项目里已有的那份；若要登记成另一个身份，请先在项目素材里移除现有那份再分配。（ASSET_ALREADY_IN_PROJECT）**」 |

R1 的裸英文「500 Internal Server Error」已消失，取而代之的是具名错误码 + 中文说明 + **现有身份**与**下一步动作**（先移除再分配）。符合团队「原始错误码不是屏上唯一信息」「必做动作块」口径。

证据：`.local-acceptance/r1-20261115-assets/shots/64-a1fix-cold-deeplink-duplicate.png`、`63-a7-spa-navigation.png`；`a6_ui_retest.json`、`a7_repro.json`、`a1_verify_5192.json`

### 8.2 A7【P1】**未合入即回归**：`#a/assignments` 深链入口在主干上依然分配不出去，且失败提示指错了原因

- **症状**
  直接打开或刷新 `#a/assignments`（冷启动，非界面内跳转）时，「目标作品」下拉**看起来已选中**「测评-r1-assets-01」，「分配到作品」按钮**也是可点的**（未禁用），但点下去**不产生任何网络请求**，页面只显示「**请选择作品和已晋升版本，并填写作品内素材身份。**」——而「目标作品」明明显示着已选，用户被指向了一个错误的原因。

- **编号复现步骤**
  1. 用干净浏览器上下文打开 `http://127.0.0.1:5191/#a/assignments`（5191 = 当前共享工作树的源码，冷启动、不先访问任何导演页面）
  2. 观察「目标作品」下拉：显示「测评-r1-assets-01」
  3. 「全局素材版本」选 `sample_asset2 · V1`、「作品内身份名称」填「林岚」
  4. 点「分配到作品」
  5. **实际**：**没有任何 `POST /api/v1/asset-center/assignments` 请求**；页面出现「请选择作品和已晋升版本，并填写作品内素材身份。」；台账无新增
  6. 对照：先在导演工作台逛过、再界面内跳转进素材中心（同一 SPA 会话内的同文档导航），同样的选择与点击 → **请求正常发出**

- **期望行为**
  目标作品的初值应在作品列表异步到达后回填，或控件在无有效值时显示「请选择作品」空选项并禁用提交按钮；失败提示不得指向与真实原因相反的方向。

- **实际行为与机理（已取证到 React 内部状态）**
  | 观测点 | 冷启动直接深链 | 界面内跳转 |
  | --- | --- | --- |
  | DOM `select.value` | `43a83a6f-9608-4e55-a4ca-479f5ee1fcda` | 同左 |
  | DOM `select.selectedIndex` | `0` | `0` |
  | **React 内部 `value`（读 `__reactProps$`）** | **`""`（空串）** | `43a83a6f-…` |
  | 点击后是否发请求 | **否** | 是 |

  成因三步：
  1. `frontend/src/asset-center/AssetsWorkspace.tsx:1025` `useState(projectId || projects[0]?.id || '')` —— 初值**只在首屏取一次**；
  2. `frontend/src/App.tsx:686` `if (ui === 'assets') return <AssetsWorkspace …>` —— assets 早返回**不受 `projectsLoaded` 门禁**，深链冷启动时 `useStudio` 的 `/projects` 还没回来，此时 `projects=[]`、`projectId=''`，组件就在空数据上挂载；
  3. 受控 `<select value={''}>` 找不到匹配 option，浏览器回落到显示第一项，而 React state 仍是空串 → 控件与状态不一致；提交按钮的 `disabled` 只看 `selectedVersion`，因此按钮可点却必然被守卫拦下。

- **原始错误码**
  无 HTTP 错误码——**请求从未发出**，纯前端守卫：`AssetsWorkspace.tsx:1041` `if (!targetProject || !selectedVersion) { setError('请选择作品和已晋升版本，并填写作品内素材身份。'); return; }`

- **归属：合并缺口，不是新写的代码缺陷**
  `chore/asset-assign-a1-20261115` 的 `4c38d77`「fix(assets): 项目分配页在作品列表异步到达后回填目标作品」正是这三步成因的修复（`useEffect` 随 `projects` 回填 `targetProject` + 补「请选择作品」空选项）。但该提交**只在分支上**，`git branch -a --contains 4c38d77` 仅返回 `chore/asset-assign-a1-20261115`；`origin/main` 仍是 `63f50be`，当前共享工作树 HEAD 是 `fix/node-pack-gate-offline-fixture-20261115`（`9e34736`），二者都不含它。

- **修复树上的实测复测（通过）**
  用 `git worktree` 拉出 `4c38d77`（目录 `.local-acceptance/r1-20261115-assets/wt-a1`），起独立 vite 于 **5192**（不碰共享依赖、不占他人端口），冷启动直连 `http://127.0.0.1:5192/#a/assignments`：
  - `target_react = "43a83a6f-…"`（**非空**，回填生效）
  - 点击 → **正常发出 POST** → 同内容分配得 **409 ASSET_ALREADY_IN_PROJECT**（文案与 8.1 一致）
  - 即：**A7 的代码修复有效，缺的是合入 main**。合入后需由测评方按 §8.6 矩阵复测。

- **截图路径**
  `.local-acceptance/r1-20261115-assets/shots/62-a7-cold-deeplink.png`（冷启动：DOM 有值 / React 空串 / 点击无请求 / 守卫文案）、`63-a7-spa-navigation.png`（界面内跳转：请求正常发出）、`64-a1fix-cold-deeplink-duplicate.png`（修复树冷启动：回填生效 + 409）、`65-a1fix-cold-deeplink-new-asset.png`

- **影响面**
  素材中心最常用的入口（深链 / 刷新 / 从桌面或消息直接打开）在这条路径上**完全无法完成分配**，且失败提示把用户指向错误的原因（让人去动一个看起来没问题的下拉），属于「用户会反复重试却永远不通」的静默死路；按钮可点会让它更像偶发而非必然，更难自查。同时它暴露按钮禁用条件（`!selectedVersion`）与守卫条件（`!targetProject || !selectedVersion`）不一致。

### 8.3 对我自己在 §7.1 的结论的澄清（不是推翻）

§7.1 记录「A1 复测：已修复并通过」——**该结论成立**：当时 5190 跑的工作树正带着 `4c38d77` 的未提交改动，冷启动直连深链实测「目标作品已回填、POST 201」。本轮新增的事实是：**那批改动后来只被提交到 `chore/asset-assign-a1-20261115` 分支，工作树随后切到别的分支，主干与当前工作树都不再含它**。所以今天的准确记账是：

- A1 的**前端修复有效**（两棵带修复的树实测均通过：5190 于 02:4x、5192 worktree 于本轮）；
- A1 的**后端修复有效**（本轮 A6 复测连带确认：POST 正常发出并落库）；
- 但**两者都尚未进入 main**（A6 那批还在共享工作树未提交，A1 前端在 `4c38d77` 未合），因此对用户而言 `#a/assignments` 深链入口**此刻仍然是坏的**；
- 记账方式应为「**A1 = 已修待合（后端在工作树未提交、前端在 `4c38d77` 未合）**」，而不是「A1 已关闭」。

### 8.4 代码现状盘点（供合并决策，2026-11-15 04:0x 实测）

| 位置 | 内容 | 状态 |
| --- | --- | --- |
| `chore/asset-assign-a1-20261115`（已推送 origin） | `4c38d77` 前端 A1/A7 修复 + `frontend/src/test/assetsWorkspace.test.tsx`（+74）+ `842a247` 本报告 | **待合 main** |
| 共享工作树 HEAD `fix/node-pack-gate-offline-fixture-20261115`（`9e34736`） | node-pack 门禁离线降级 | 已提交于分支 |
| 共享工作树未提交 | `backend/shine/asset_center/service.py` +74/-1、`frontend/src/api.ts` +6、`frontend/src/test/apiError.test.ts` +9、未跟踪 `backend/tests/test_asset_assignment_duplicate.py`（= A6 修复） | **未提交，未落分支** |
| `origin/main` | `63f50be` | 不含 A1/A7、不含 A6 |

两条线触及文件不重叠，但 `chore/asset-assign-a1-20261115` 的基线是 `afaef8a`，合 main 前需把 `9e34736` 一并带入，否则 node-pack 门禁降级修复会掉。

### 8.5 环境事故披露（二）：共享 `frontend/node_modules` 被他人 `npm install` 洗坏

2026-11-15 03:14，Qoder CN 会话在 `frontend` 下执行了 `npm install`，导致共享 `node_modules` 顶层仅剩 136 项、`@babel` 整个目录缺失 → 任何**新启动**的 vite 报 `Cannot find package '@babel/core'`。后果：我的 5190 web 已死，小导演 5180 因进程未重启而侥幸存活。**我的重启没有触发 npm**（日志无 npm 记录、`package-lock.json` 未改、依赖回执指纹匹配）。恢复需要装依赖，按红线**我没有擅自执行 npm**。

本轮全部验证改用「目录联接 + 独立 `cacheDir` + 独立端口」的隔离前端完成，**未改共享依赖、未占用他人端口**（8010/5180/8000 均未触碰）：

| 端口 | 源码来源 | 用途 |
| --- | --- | --- |
| 5191 | 目录联接 → 共享工作树 `frontend/src` | 复现 A7（主干现状） |
| 5192 | 目录联接 → `4c38d77` worktree `frontend/src` | 复测 A1/A7 修复树 |

### 8.6 复测矩阵（合入后照此三连复测）

| 入口 | 树 | 预期 |
| --- | --- | --- |
| 冷启动直开 `#a/assignments` | 含 `4c38d77` | 「目标作品」回填为真实 id；点击发出 POST；同内容 → 409 `ASSET_ALREADY_IN_PROJECT`；异内容 → 201 + 「已分配到作品。项目素材编号：…」 |
| 界面内跳转（导演工作台 → 素材中心 → 项目分配） | 同上 | 同上（不得因有回填而退化） |
| 作品列表为空 / 后端不可达 | 同上 | 必须给出可解释文案或禁用按钮，**不得**再出现「看着已选、点了无请求」 |

### 8.7 结论与未运行清单

- **A1**：已修待合（后端在工作树未提交、前端 `4c38d77` 未合 main）。
- **A6**：**复测通过**（409 具名 + 中文说明 + 现有身份 + 下一步）。
- **A7【P1】**：**新增**，代码修复已在 `4c38d77` 且实测有效，**待合 main**；合入前 `#a/assignments` 深链入口对用户仍坏。
- **A2/A3/A4/A5**：维持 R1 结论，未在本轮变更（A2 按「一次只修一个」未动）。
- **仍未运行（禁脑补）**：`PENDING_WORKFLOW_MAPPING`、`IMAGE_WORKER_LOST` / 「再生成一版」显式按钮、GPU 租约说明与下一步、图像槽位家族顺序、出图候选人工晋升、角色绑定 proposal→确认→apply、`CHARACTER_NAME_ALREADY_IN_USE` 界面验证。原因不变：本机出图能力门（`CATALOG_VARIANT_UNDECLARED`）把链路挡在第 1 步，且本机无可用文本模型。诚实边界不变：全程离线模拟，`h3_connected=false`、`production_mode=DEMO/NOT_ACTIVATED`，未发生真实 H3 渲染，不构成真实出图链路验收。

### 8.8 投递状态

`post_group`（群「Shine 实景测评组」`grp_e251e930ccd2`）本轮 **7 次**被平台并发守卫拒收（`群正在讨论中` / `无法确认工具调用来自哪个正在进行的回复`），`message_bot` 私信同样被拒；只读类调用（`list_team`）正常，判定为**平台派发通道问题、非我的参数错误**。本轮结论正文已落盘：

- `.local-acceptance/r1-20261115-assets/PENDING_GROUP_POST_A1-verify-A6.md`（群回执可直接粘贴正文）
- `.local-acceptance/r1-20261115-assets/PENDING_GROUP_POST_round2-A6-A7.md`（本轮 A6/A7 摘要，可直接粘贴正文）

通道恢复后请据上述文件补投；补投前以本报告 §8 为准。


### 8.9 追加（2026-11-15 04:1x–04:2x）：共享依赖已恢复，A7 在官方实例上复核仍复现

§8.5 记录的环境事故已有后续：共享 `frontend/node_modules` 被隔离为 `frontend/node_modules.broken-20261115`（03:14:32 的损坏态留档），并于 **03:54:14 重新安装恢复**（`.package-lock.json` 存在、`@babel/core` 到位）。恢复由他人执行，**我未执行任何 npm 命令**。

恢复后我做两件事：

1. **重启自己的 5190 前端实例**（旧 vite 进程 26628 在依赖损坏前启动、模块图已废，`/src/main.tsx` 持续返回 500；其守护脚本 `bg_f378b564` 于 04:14 因 3600s 上限被中止）。按 `scripts/start_shine.ps1` 的原配方重启：cwd = `frontend`，`SHINE_VITE_API_TARGET=http://127.0.0.1:8020`，`vite --host 127.0.0.1 --port 5190 --strictPort`（新 PID 15820）。结果：`http://127.0.0.1:5190/src/main.tsx` → **200**，实例恢复可用。API 8020 未重启、未受影响（`/health` 200）。
2. **在恢复后的官方实例上复核 A7**：干净浏览器上下文冷启动直连 `http://127.0.0.1:5190/#a/assignments`，选 `sample_asset2 · V1`、填「林岚」→ 点「分配到作品」。实测：DOM `select.value = 43a83a6f-…`（看起来已选中）、**React 内部 `value = ""`**、按钮未禁用、点击后**无任何 POST**、页面显示「请选择作品和已晋升版本，并填写作品内素材身份。」→ **A7 在恢复后的主干工作树上依然成立**（属 §8.2 的合并缺口，非依赖事故的副产物）。
   证据：`.local-acceptance/r1-20261115-assets/shots/66-a7-recheck-5190.png`、`a7_recheck_5190.json`；复核脚本 `a7_recheck.py`（带端口参数，可对任意实例复用）。

结论不变：A6 复测通过；A7 代码修复在 `4c38d77` 且实测有效，**待 PR #32 合入 main**；A1 记账口径为「已修待合」。依赖恢复后前端 `vitest` 应可正常运行，建议修复师在干净环境补跑前端全量后再合 A6 的 PR。

