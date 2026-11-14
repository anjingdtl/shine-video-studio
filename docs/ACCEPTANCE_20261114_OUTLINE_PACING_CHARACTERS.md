# 大纲节奏三档 + 登场人物介绍（2026-11-14）

## 症状（用户报障）

第一阶段「创意与剧本」生成的大纲没有登场人物介绍；且用户希望大纲生成可选三种
节奏（紧凑/均衡/舒缓），并据此编写内容以适配不同拍摄要求。

## 根因

- 旧 `OutlinePart` 合同只有 summary/story_line/timeline/relationships/questions，
  最终大纲 `characters` 仅 name/entity_id/stable_identity，**没有介绍文本**；
  `outline_to_markdown` 也不渲染人物，故正文看不到登场人物介绍。
- 大纲生成没有节奏参数，叙事密度固定，无法适配快切/慢切拍摄。

## 修复

- 新增 `shine/materialprep/pacing.py`：三档 `compact/balanced/relaxed`
  （紧凑/均衡/舒缓），各带 label/summary/英文 merge 指令；`normalize_pacing`
  只认三档，其余回退默认 balanced。
- `queue_outline(..., pacing)`：pacing 进入请求身份（切换节奏=新大纲输入）与 trace。
- `_merge(..., pacing)`：system 提示词追加节奏指令 + 要求为每个真实登场人物写
  grounded 介绍；context 的人物从「仅名字」升级为「名字+描述」；pacing 进入 merge 身份。
- `OutlinePart` 增加 `characters: [{name, introduction}]`。
- 最终大纲 payload：`characters` 携带 `introduction`（merge 介绍优先，回退叶子描述），
  并带 `pacing`/`pacing_label`；不改动 source 分段/锁定事实/人物编号/production_sections。
- `outline_to_markdown`/`markdown_to_outline` 增加 `## 登场人物` 锚点，往返无损；
  编辑人物只改介绍，保留 entity_id/stable_identity。
- API `OutlineRequest.pacing`（Literal 三档，默认 balanced，非法 422）。
- 前端 `OutlineConfirmationPanel` 与 `StoryPreparationPanel` 增加三档节奏选择器
  （`constants.ts::pacingOptions`），提取时提交 pacing；横幅显示当前大纲节奏标签。

## 定向回归（用户裁定不做全量）

后端：`test_story_preparation`（+2：pacing 身份/人物介绍/非法 pacing）、
`test_director_streamline`、`test_story_outline_unblock` 共 33 例；
merge/OutlinePart 影响面 `test_pipeline`/`test_story_section_run`/`test_story_continuity`/
`test_literary_memory`/`test_literary_low_blocking`/`test_director_basis_grouping`/
`test_director_long_form`/`test_material_scenes` 共 82 例。0 FAILED/ERROR。Ruff 通过。
前端：`outlineVersions`（+1 节奏选择器）/`storyOutlineUnblock`/`materialPreparation` 32 例、
`directorEntries`/`setupEntry`/`directorContinuation` 22 例；tsc/build 通过。

## 浏览器实测（真实 MiniMax）

隔离作品 outline-version-live-test：选「紧凑剧情」重新提取 → 新任务（身份含 pacing，
cfec1e2c ≠ 旧 3fc355f0）→ 完成后横幅「待你审阅 · 紧凑剧情」，正文出现 `## 登场人物`
且每个人物带 grounded 介绍（模型对未说明的外貌如实标注「未说明」，不臆造）。

## 边界

- 节奏只影响归并叙事投影，不改 source 分段/锁定事实/人物编号/production_sections；
  叶子事实提取与素材准备共用 `scenes.prepare_scenes`，**不受 pacing 影响**（素材行为不变）。
- 未改 `worker.py`/`preproduction.py`，不触发 VLM 重校准；校准仍未做。
- 单叶（single-leaf）路径人物介绍回退叶子描述；该路径 questions 为对象的历史形状未 overhaul（超出本轮范围）。