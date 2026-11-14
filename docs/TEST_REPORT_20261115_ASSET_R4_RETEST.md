# 素材中心 A1/A6 修复后浏览器实景复测与图片链路续查（R4 · 美术师）

- 报告人：美术师（bot_b6f37e74112d）
- 时间：2026-11-15 04:45 – 07:0x（离线模拟）
- 被测实例：素材线 Web `http://127.0.0.1:5190` / API `http://127.0.0.1:8020`（mock provider，`h3_connected=false`，`production_mode=DEMO`）
- 被测代码：共享工作树 HEAD `b74db38`（**已含** PR #32 `4c38d77` 与 PR #34 `e2a4616`，即 A1/A6 修复已在主干）
- 证据目录：`.local-acceptance/r4-20261115-assets/`（`shots/` 19 张截图、`r4_a1_a6.json`、`r4_images_binding.json`、`r4_explore.json`）
- 测试项目：`测评-R4-美术师-01`（project_id `7dc67a12-2b0a-4394-a3e2-123b7d2dadb8`）

**诚实边界（先行声明）**：本轮全部为离线/mock 模拟，未发生真实 H3 渲染，**不构成真实出图/出片验收**。本机三套栈的 `/api/v1/provider-profiles` 均为空数组（无任何文本模型），大纲/分集/分镜链无法真实驱动；凡依赖该链的步骤一律记为「未运行」，不写推测。本轮未执行任何 `npm ci`/`npm install`，未启停任何工作栈、未结束任何进程，未改动工作树中他人的未提交改动。

---

## 1. 结论速览

| 项 | 结论 | 关键证据 |
| --- | --- | --- |
| **A1【P1】项目分配页目标作品回填** | **通过 ✅** | 冷启动深链下 React 内部 value = `7dc67a12…`（不再是空串假象）；分配 201；刷新后回填与使用记录均正确 |
| **A6【P1】同内容重复分配** | **通过 ✅** | 同内容同身份 → 幂等复用（同一个 `project_asset_id`）；同内容异身份 → 具名 409 `ASSET_ALREADY_IN_PROJECT` + 中文说明与下一步；**无裸 500** |
| **A6 补充：内容不同但同名** | **通过 ✅** | 同名（`sample_asset`）不同内容（sha `6c61d734…`）可正常分配，201 |
| **候选素材不自动晋升** | **通过 ✅** | 导入后为「待技术检查」，点「检查并人工晋升」后才「已晋升」 |
| 图片链路三种前置缺失的界面表现 | **发现问题 → R4-01【P2】** | 三种缺失在屏上折叠成同一句「等待本机能力验证」，具名码只在接口层 |
| 角色绑定 proposal→逐行确认→apply | **未运行**（环境阻塞） | 前置阻断具名且带动作按钮（良好形态）；proposal 本体需分镜产物，本机无文本模型 |
| 角色重名拒绝的界面表现 | **代码级证据 → R4-02【P2】（界面未运行）** | 后端 409 为裸码串 `CHARACTER_NAME_ALREADY_IN_USE:<名字>` |

---

## 2. A1 复测【通过】

### 2.1 复现步骤（编号，全部为界面操作）

1. 打开 `http://127.0.0.1:5190/#d/projects` → 「新建作品」输入 `测评-R4-美术师-01` → 点「新建作品」→ 跳转 `#d/creative`
2. 左侧「素材中心」→ 顶部「项目分配」
3. 观察「目标作品」下拉：**显示「测评-R4-美术师-01」**
4. 取证：DOM `select.value` 与 **React 内部 `value`** 均为 `7dc67a12-2b0a-4394-a3e2-123b7d2dadb8`（两者一致）
5. 「全局素材版本」选 `sample_asset2 · V1`；「作品内身份名称」填 `林岚-R4`；点「分配到作品」
6. **结果**：`POST /api/v1/asset-center/assignments` → **201**，`project_asset_id = 036a80f7-5876-462c-86d2-99c3ce4513c6`；页面显示「**已分配到作品。项目素材编号：036a80f7-…**」
7. **刷新页面**（冷启动直连 `#a/assignments`）：
   - 「目标作品」仍为 `测评-R4-美术师-01`（React value 仍 = `7dc67a12…`）
   - 「使用记录」出现「`sample_asset2 → 测评-R4-美术师-01 · generic · 林岚-R4 · local · 036a80f7`」

### 2.2 对照 R3 遗留问题

R3 遗留的 `4c38d77` 未进主干导致的「DOM 显示已选、React state 为空串 → 点击无请求」在本轮**不复现**：冷启动直连深链时 React value 与 DOM value 一致，请求正常发出。

- 截图：`shots/03-a1-before-assign.png`、`shots/04-a1-assigned.png`、`shots/05-a1-after-reload.png`
- 数据：`r4_a1_a6.json` → `a1_backfill_ok = true`、`a1_after_reload`

---

## 3. A6 复测【通过】——三种情形全测

| 情形 | 请求结果 | 界面表现 | 判定 |
| --- | --- | --- | --- |
| **内容完全相同 + 同一身份**（`sample_asset2 · V1`，用途 generic，身份 `林岚-R4`） | **201**，且返回**同一个** `project_asset_id = 036a80f7…`、同一个 assignment id `04c1d654…` | 「已分配到作品。项目素材编号：036a80f7-…」，无报错 | **幂等复用 ✅**（未新建第二条） |
| **内容完全相同 + 异身份**（同版本，身份改为 `沈舟-R4`） | **409** `{"detail":{"code":"ASSET_ALREADY_IN_PROJECT","message":"这份内容已经在项目里（现有用途：generic，现有身份：林岚-R4）。请直接使用项目里已有的那份；若要登记成另一个身份，请先在项目素材里移除现有那份再分配。","existing_asset_id":"036a80f7-…","existing_role":"generic","existing_entity_id":"林岚-R4"}}` | 页面显示同款中文文案并括注 `（ASSET_ALREADY_IN_PROJECT）` | **具名拒绝 ✅**，含现有身份 + 下一步，**无裸 500 / Internal Server Error** |
| **内容不同但同名**（新建 `sample_asset.png`，sha `6c61d734…`，与原 `sample_asset` 的 `2ad5a626…` 同名不同内容） | **201**，`project_asset_id = 5f34eec1-71cf-4d60-ae39-3c242e1f57c9` | 「已分配到作品。项目素材编号：5f34eec1-…」 | **同名不同内容互不冲突 ✅** |

- 截图：`shots/06-a6-same-content-same-identity.png`、`shots/07-a6-same-content-other-identity.png`、`shots/10-a6-same-name-different-content.png`
- 数据：`r4_a1_a6.json` → `a6_same_content_same_identity` / `a6_same_content_other_identity` / `a6_same_name_different_content` / `posts`
- 附带观察：同名不同内容在**素材库**里会并列为两条「sample_asset」（各自的 `6c61d734…` / `2ad5a626…` 版本），**没有**被标题去重或拒绝；进项目后成为两条独立项目素材。

---

## 4. 候选素材不自动晋升【通过】

1. 素材中心 →「全局素材库」→ 导入 `files/sample_asset.png`（64×64，sha `6c61d734…`）
2. **未点任何按钮**：列表显示「`sample_asset` · other · 1 个版本 · **待技术检查**」；详情「**V1 · 待技术检查**」「1 个不可覆盖的版本」「项目使用记录：**尚未分配给影视项目。**」
3. 点「**检查并人工晋升**」→ 详情变为「**V1 · 已晋升**」「**已人工晋升；项目版本引用不会随全局升级而改变。**」
4. 专业模式制片准备页同口径文案：「先读剧本、备素材，再冻结风格。……**AI 只生成候选。晋升为主素材需要你在下面明确确认，AI 不会替你决定。**」

判定：**候选不会被界面显示成已生效的主素材**，晋升必须由用户显式点击 ✅

- 截图：`shots/08-library-imported-same-name.png`（待技术检查）、`shots/09-library-promoted.png`（已晋升）

---

## 5. 图片链路 UI 续查（三种前置缺失）

### 5.1 实测到的界面表现

在 `#a/images` 分别构造三种状态，主按钮与文案**完全一致**：

| 状态 | 主按钮 | 按钮 disabled | 屏上具名码 | 屏上文案 |
| --- | --- | --- | --- | --- |
| 文生图（无参考图、无工作流 profile） | 「等待本机能力验证」 | 是 | 无 | 「Qwen-Image-2.1 T2I 需要本机模型、工作流和真实小样通过。」 |
| 图生图（**无参考图**） | 「等待本机能力验证」 | 是 | 无 | 同上 |
| 图生图（**已选参考图** `sample_asset · V1`） | 「等待本机能力验证」 | 是 | 无 | 同上 |

- 三种状态下 `PENDING_WORKFLOW_MAPPING` / `WORKFLOW_SIZE_NOT_MAPPED` / `CATALOG_VARIANT_UNDECLARED` / `IMAGE_WORKER_LOST` **均不在可见文本中**（脚本实测 `codes_in_text = []`）
- 页面唯一出口是次级按钮「**打开本机出图配置**」；模式按钮「局部修订」额外带「待本机验证」角标

### 5.2 实际阻断点（接口层，页面自身发出的探测）

`POST /api/v1/setup/existing-installations/inspect` → **409**

```
{"detail":{"code":"CATALOG_VARIANT_UNDECLARED",
  "message":"这个部署目录没有声明变体身份，不能确定它该登记哪些生成方案。",
  "catalog_id":"shine-qwen-image-21-windows-x64"}}
```

即：**具名码存在，但只在接口层**；屏上既没有码，也没有区分「缺工作流 profile / 缺参考图 / 槽位未映射」三类缺失 → 记为 **R4-01**。

- 截图：`shots/21-images-t2i-blocked.png`、`shots/22-images-i2i-no-ref.png`、`shots/23-images-i2i-with-ref.png`
- 数据：`r4_images_binding.json` → `images_t2i` / `images_i2i_no_ref` / `images_i2i_with_ref` / `net`

---

## 6. 角色绑定链路（proposal → 逐行确认 → apply）

### 6.1 前置阻断表现（良好形态，作为正向结论记录）

| 入口 | 操作 | 接口 | 界面文案 |
| --- | --- | --- | --- |
| 导演线 `#d/materials` | 点「按已确认镜头生成素材清单」 | **409** `{"detail":"SCRIPT_OUTLINE_NOT_CONFIRMED"}` | 「**先确认剧本大纲，再按剧本分集。（SCRIPT_OUTLINE_NOT_CONFIRMED）**」+ 顶部「下一步：创意与剧本写想法或导入剧本，确认大纲」 |
| 专业模式制片准备 | 点「生成 AI 分镜方案」 | — | 「**③ 镜头拆解还没有完成：素材按定稿分镜准备。先到③逐场生成并应用分镜、确认分集；分镜定稿前本页的清单与出图按钮暂停。去③镜头拆解**」 |

### 6.2 proposal → 逐行确认 → apply 本体：**未运行**

人物对照（proposal）由编剧随分镜一并产出，需先有分镜产物；本机 `/api/v1/provider-profiles` 为**空数组**（无任何文本模型），大纲→分集→分镜链无法真实驱动，因此：

- **未运行**：人物对照 proposal 的逐行核对、逐条改名、「确认并写入名字」、apply 后自动 approve+lock 主素材、以及 `CHARACTER_NAME_ALREADY_IN_USE` 的界面表现。
- 入口位置（代码定位）：专业模式「分镜与任务」的 `StoryboardPanel.tsx`；**素材中心（`#a/*`）内仍无角色绑定入口**（R1 的 A5 维持）。

### 6.3 代码级观察（界面未运行，单列并标注）

- 后端 `backend/shine/api_film.py:1945`：`raise HTTPException(409, f"CHARACTER_NAME_ALREADY_IN_USE:{row.name.strip()}")` —— **裸码串 detail**，不是 `{code, message}` 结构
- 前端 `frontend/src/components/StoryboardPanel.tsx` 的 apply 捕获为 `setMessage(String(e))`，另有两道前置提示（「请为每一位人物选择素材并填写名字，再一起确认。」「同一张人物素材不能同时对应不同人物，请逐条核对。」）

→ 若重名被拒，屏上预计呈现为**裸码串**（形如 `409: CHARACTER_NAME_ALREADY_IN_USE:林岚`），缺少中文说明与下一步 → 记为 **R4-02（代码级证据，界面未运行）**。

- 截图：`shots/25-director-materials.png`、`shots/26-director-materials-after-gen.png`、`shots/27-expert-storyboard-attempt.png`

---

## 7. 新缺陷清单

### R4-01【P2】出图入口把三种不同的前置缺失折叠成同一句「等待本机能力验证」，具名码只在接口层

- **症状**
  图片工坊的主按钮无论处于「无工作流 profile」「无参考图」「槽位未映射（WORKFLOW_SIZE_NOT_MAPPED）」哪种前置缺失，都只显示「**等待本机能力验证**」且不可点击；屏上没有任何具名错误码，也没有区分到底缺哪一项、缺了之后该做什么。用户既看不到 `PENDING_WORKFLOW_MAPPING` / `WORKFLOW_SIZE_NOT_MAPPED`，也看不到唯一出口指向的到底是哪一步。

- **编号复现步骤**
  1. 打开 `http://127.0.0.1:5190/#a/images`（素材中心 → 图片工坊）
  2. 观察主按钮：「等待本机能力验证」，disabled
  3. 点「图生图」→ 主按钮与文案**不变**，无参考图提示
  4. 在「从已有素材继续并保留版本历史（可选）」里选一个已晋升素材 → 主按钮与文案**仍不变**，屏上无任何具名码
  5. 对照接口层：页面自身发出的 `POST /api/v1/setup/existing-installations/inspect` 返回 409 `CATALOG_VARIANT_UNDECLARED`

- **期望行为**
  按团队口径「原始错误码不是屏上唯一信息」「必做动作块」：屏上应显示**具名码 + 中文原因 + 指向具体下一步的动作按钮**（区分缺工作流 profile / 缺参考图 / 槽位未映射），三者不应折叠成同一句话。

- **实际行为**
  三种缺失在屏上完全同形：文案「Qwen-Image-2.1 T2I 需要本机模型、工作流和真实小样通过。」+ 按钮「等待本机能力验证」（disabled）+ 按钮「打开本机出图配置」。`PENDING_WORKFLOW_MAPPING` / `WORKFLOW_SIZE_NOT_MAPPED` / `CATALOG_VARIANT_UNDECLARED` 均不在可见文本中。

- **原始错误码**
  HTTP **409**，`{"detail":{"code":"CATALOG_VARIANT_UNDECLARED","message":"这个部署目录没有声明变体身份，不能确定它该登记哪些生成方案。","catalog_id":"shine-qwen-image-21-windows-x64"}}`（接口层，路径 `POST /api/v1/setup/existing-installations/inspect`）

- **截图路径**
  `.local-acceptance/r4-20261115-assets/shots/21-images-t2i-blocked.png`、`22-images-i2i-no-ref.png`、`23-images-i2i-with-ref.png`

- **影响面**
  素材线最核心的「出图」入口在缺前置时无法自解释：用户只知道「等验证」，不知道缺工作流 profile、缺参考图还是槽位没映射，也不知道下一步该点哪里；R1 的 A4 结论在此得到实证——**CATALOG_VARIANT_UNDECLARED 至今只存在于接口层**。与团队既有口径（`errorActions.ts` 已为 `PENDING_WORKFLOW_MAPPING` / `WORKFLOW_SIZE_NOT_MAPPED` 备好「去基础配置登记」动作，但素材中心图片工坊未接入该映射）冲突。

### R4-02【P2】角色重名拒绝使用裸码串，且该路径界面本轮未运行（代码级证据）

- **症状**
  人物对照 apply 时若人物名已被项目内其他主素材占用，后端返回的是**裸码串** `CHARACTER_NAME_ALREADY_IN_USE:<名字>`，前端按 `setMessage(String(e))` 原样显示，缺少中文说明与下一步动作。

- **编号复现步骤（本轮未运行，需在有分镜产物的环境复现）**
  1. 准备一部有分镜人物对照的作品（需文本模型产出分镜）
  2. 人物对照中把某一人物命名为项目内另一张角色主素材已占用的名字
  3. 点「确认并写入名字」→ 观察屏幕消息

- **期望行为**
  按团队口径应显示具名码 + 中文说明 + 下一步（如「这个名字已被另一张角色素材占用，请改用其他名字或先释放该名字」），而不是把 `409: CHARACTER_NAME_ALREADY_IN_USE:林岚` 直接甩给用户。

- **实际行为（代码级）**
  `backend/shine/api_film.py:1945` `raise HTTPException(409, f"CHARACTER_NAME_ALREADY_IN_USE:{row.name.strip()}")`；`frontend/src/components/StoryboardPanel.tsx` 的 apply catch 为 `setMessage(String(e))`。**界面表现本轮未验证**（见 §6.2 环境阻塞）。

- **原始错误码**
  HTTP **409**，detail 为裸码串 `CHARACTER_NAME_ALREADY_IN_USE:<名字>`（对比：同项目其他错误码已结构化为 `{code, message, ...}`，例如 A6 的 `ASSET_ALREADY_IN_PROJECT`）

- **截图路径**
  本轮无（界面未运行）；相邻页面证据 `shots/27-expert-storyboard-attempt.png`

- **影响面**
  角色对照是「确认名图对应」的关键人工确认环节；若重名，屏幕给的是不可操作的裸码，与 A6 修复后的 `ASSET_ALREADY_IN_PROJECT`（含现有身份与下一步）体验不一致。**证据等级：代码级，未在浏览器实测，修复前需补一条浏览器复现。**

---

## 8. 证据清单

| 类别 | 路径 |
| --- | --- |
| 截图（19 张） | `.local-acceptance/r4-20261115-assets/shots/`：`01`–`10`（A1/A6/候选晋升）、`20-explore-*`（页面探查）、`21`–`27`（图片工坊与角色绑定） |
| A1/A6 数据 | `.local-acceptance/r4-20261115-assets/r4_a1_a6.json`（含每次 POST 的状态码与响应体） |
| 图片/绑定数据 | `.local-acceptance/r4-20261115-assets/r4_images_binding.json`（含控件快照与全部 ≥400 响应） |
| 页面探查 | `.local-acceptance/r4-20261115-assets/r4_explore.json` |
| 可复跑脚本 | `r4_a1_a6.py`、`r4_images_binding.py`、`r4_explore.py`、`diag_project_create.py`、`make_png_r4.py`（同目录） |
| 测试文件 | `files/sample_asset.png`（sha `6c61d734c7dd8d47b3bdd2728ee53bb3884515ab50d154705db774d53b85a3be`，与既有 `sample_asset` 同名不同内容） |

---

## 9. 仍未运行清单（禁脑补）

- 真实出图：候选生成、候选人工晋升（图片侧）、`IMAGE_WORKER_LOST` / 「再生成一版」、GPU 租约提示、槽位家族顺序、图像出图候选的逐张审核 —— 全部**未运行**（出图能力门挡在第 1 步：`CATALOG_VARIANT_UNDECLARED`）
- 人物对照 proposal → 逐行确认 → apply → 冻结主素材、`CHARACTER_NAME_ALREADY_IN_USE` 的界面表现 —— **未运行**（无文本模型，分镜链不可达）
- 大纲提取与确认、分集确认、分镜草案生成与应用 —— **未运行**（本机无任何文本模型，`provider-profiles` 为空数组）
- 幂等键冲突、角色绑定的歧义与重名在真实数据下的表现 —— **未运行**

诚实边界：本轮全部为离线模拟，`h3_connected=false`、`production_mode=DEMO`，未发生真实 H3 渲染，**不构成真实出图/出片验收**。
