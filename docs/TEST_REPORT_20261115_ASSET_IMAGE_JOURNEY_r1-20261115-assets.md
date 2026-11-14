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

