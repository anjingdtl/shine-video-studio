# 2026-11-03 工作台可用性一轮：必点操作显性化、Agent 分步续跑、人物对照取名

本轮起因是三个真实使用中的问题，都不是渲染或模型本身的问题：

1. 第 2 步的「启用官方规则包」按钮是灰色文字链接，用户看不到必须点它，链路在第一步就被
   `FREEZE_PROJECT_RULEPACK_FIRST` 挡住，界面上只留一个原始错误码。
2. 分镜链里某一步失败后，用户只能从头再点一次生成，前面的真实模型调用全部白费。
3. 素材只有文件名和哈希，没有缩略图，用户分不清哪张图是谁；剧本用 `S1…S6` 这种编号标注
   角色时，人物和素材之间没有任何可用的连接键，链路必然阻断。

## 一、必点操作高对比 + 模块指引

每一页「必须点一次」的操作现在都用同一个橙色高对比区块（`.required-action` +
`.must-button`）呈现，并配一段说明「这一步要做什么 / 为什么必须做」的模块指引
（`.module-guide`）。

| 页面 | 必点操作 | 指引要点 |
| --- | --- | --- |
| 我的作品 | 四步流程总览 | 素材与风格 → AI 导演制作 → 成片中心 → 下载成片 |
| 素材与风格 · 素材 | 确认使用待确认素材 | 名字是分镜绑定剧本人物的唯一连接键 |
| 素材与风格 · 风格 | 启用官方规则包 | 每个项目一次性绑定，绑定后不可更换 |
| 素材与风格 · 风格 | 批准并冻结 | 冻结前先原样保存屏幕上的七项 |
| AI 导演制作 | 生成 AI 分镜方案 | 失败只重跑那一步；人物对不上先做人物对照 |
| 成片中心 | 下载 MP4 | 没成片说明镜头还没全部通过质检 |

深色主题有对应变体，`.must-button` 的高度（38px）与常规按钮（32px）不同，橙色与
页面主色（橄榄绿）形成强对比。

## 二、Agent 分步间隔与从失败处续跑

### 间隔

`SHINE_AGENT_STEP_DELAY_MS`（默认 1200ms）控制两次真实模型调用之间的停顿。
第一次调用后每一步都会等待，失败重试之前也会等待。作用是给同一个服务商连接留出
settle 时间，并把「一步结束、下一步开始」变成界面上可见的边界。

### 续跑

`POST /projects/{id}/storyboard/generate` 新增两个字段：

- `start_role`：`scriptwriter` / `director` / `cinematographer` /
  `continuity_supervisor` / `editor`
- `resume_steps`：`{角色: artifact_id}`

服务端逐个校验被复用的产物：必须属于本项目、kind 与该角色合同一致、未被标记 stale，
否则返回 `STORYBOARD_RESUME_STEP_INVALID:<role>`；缺产物返回
`STORYBOARD_RESUME_STEP_MISSING:<role>`。校验通过后只跑 `start_role` 及其之后的角色。

失败响应现在带 `failed_role` 与 `resume_steps`，界面据此给出「从『导演 Agent · 镜头拆分』
这一步重试」的按钮。被跳过的角色不会再次调用模型。

## 三、剧本人物 ↔ 素材：检索、批量取名、人工确认、再冻结

`entity_id` 是分镜把「剧本里的某个人」和「你的某张图」连起来的唯一键
（`shine/asset_identity.py`）。之前的流程要求用户先猜到模型会用什么名字去填
`entity_id`，这是先有鸡还是先有蛋的问题。

新流程把顺序倒过来：**先有剧本人物，再由系统检索素材并给出名字建议，最后由用户确认，
确认之后才冻结素材。**

### 提案（只读）

`POST /projects/{id}/storyboard/character-binding/proposal`

输入剧本（已导入文档或内联文本），输出该项目的角色对照建议：

- 剧本人物：取自编剧产出的 `beats[].characters` 与 `dialogue[].speaker_id`，
  一格里写了「S1, S2」会被拆成两个名字（之前会被当成一个人名，导致缺失清单失真）。
- 候选匹配分三档：素材 `entity_id` 完全一致（high）→ 标签一致（medium）→
  文件名一致（medium）；剧本用编号标注角色时，按上传顺序给出**最低置信**的候选，
  并明确标注「请人工核对」。系统不会把顺序当结论。
- 建议名字：优先取素材标签里不是角色词的那一部分（标签「主角，小美」→ 建议名「小美」）；
  剧本写的是编号时才需要真名。

提案只读取用户自己的上传件，不批准、不锁定、不替换任何素材。

### 确认（写入）

`POST /projects/{id}/storyboard/character-binding/apply`

请求带用户逐条确认的 `{script_name, asset_id, name}`。服务端校验：

- 素材必须属于本项目、kind=image、role=character；
- 同一批确认里的名字不能重复；
- 名字不能与项目里其它人物素材已有的 `entity_id` 冲突
  （`CHARACTER_NAME_ALREADY_IN_USE`），否则绑定有歧义，镜头会被阻断。

写入后：素材的 `entity_id` 变成确认的名字，同时产生一份**新的** ScriptBreakdown
产物（角色名与对白说话人同步改写），旧产物保留可读。界面上接着用既有的
一次性授权流程 approve + lock 冻结这些素材（已经冻结的保持原版本），然后带着新的
breakdown 从导演这一步续跑。

### 预览图

`GET /api/v1/assets/{id}/media` 返回本机素材字节，素材列表与人物对照都用它渲染缩略图，
用户才能判断「名」和「图」对不对得上。

## 四、改动文件

- 后端：`shine/agents/character_binding.py`（新）、`shine/agents/film.py`、
  `shine/api_film.py`、`shine/asset_identity.py`、`shine/main.py`
- 前端：`components/AssetPanel.tsx`、`components/StylePanel.tsx`、
  `components/StoryboardPanel.tsx`、`components/DeliveryPanel.tsx`、`App.tsx`、
  `styles.css`
- 测试：`backend/tests/test_character_binding.py`（新，8 项）、
  `frontend/src/test/storyboard.test.tsx`（+2 项）、
  `frontend/src/test/simpleAssets.test.tsx`（+1 项）

## 五、边界

- 人物对照只改名字与剧本产物的角色名，不改素材字节、不改素材哈希、不动已冻结版本的
  `sha256`。
- 名字与图的对应最终由用户确认；系统给出的是建议和理由，不是结论。
- 提案与确认都写审计事件（`CHARACTER_BINDING_PROPOSED` / `CHARACTER_BINDING_APPLIED`）。
- 本轮没有触碰渲染、质检、GPU 租约与规则包锁定的语义。
