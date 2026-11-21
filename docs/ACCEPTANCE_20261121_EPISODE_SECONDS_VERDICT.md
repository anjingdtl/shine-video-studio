# 验收记录：每集秒数唯一裁决（2026-11-21 第二轮报障）

用户报障：② 剧本统筹红框里每集显示「3 场 · 计划 593 秒（与目标差 +533 秒）」
「1 场 · 计划 512 秒（与目标差 +452 秒）」——把**场景覆盖秒数**（原文预计）冒充
每集视频计划；⑥ 视频生产集卡沿用同一份错误事实显示「约 593 秒 / 约 512 秒」。
用户裁定：每集行只展示该集的计划秒数；总剧本超出秒数只在头部标识处显示。

## 1. 根因

`derive_episode_plan_from_sections` 的 `planned_seconds` 是场景覆盖估计（原文秒数），
而 ② 预览、⑥ overview、 运行集卡、EDL provenance 都把它当「每集视频计划」读。
一份事实被两种语义共用，显示层无裁决。

## 2. 修复（一套事实、一个裁决、一个执行者）

- 新增唯一裁决 `director/contracts.py::episode_video_seconds`：有落镜时 = 落镜
  `duration_target_s` 之和；未拆镜时 = 用户目标秒数。场景覆盖是另一份事实，
  只供头部覆盖标识与确认门禁。
- 消费点全部改读裁决：
  - `director/streamline.py::storyboard_book_preview`：每集 `planned_seconds` /
    `deviation_seconds` 读裁决；头部 `total_planned_seconds` = 各集视频计划之和；
    `covered_scope`（1105/4910 覆盖标识）保留在头部。
  - `director/overview.py::_episode_node`：`planned_seconds` 读裁决（target 取自
    当前分集计划 payload）。
  - `production/runner.py::episode_public`：读裁决（回退存储值）。
  - `production/episode_delivery.py::build_episode_edl`：rules.planned_seconds 读裁决。
- 前端 `StreamlinePanels.tsx`：每集行删除「与目标差」与每集覆盖边界警告渲染
  （超出信息只留头部）；行展示裁决秒数。⑥/⑦ 集卡数据驱动，无需改渲染。
- 确认门禁不变：`duration_target_verdict` / `EPISODE_PLAN_DURATION_DEVIATION` /
  `EPISODE_DURATION_OUT_OF_TARGET` 仍按各自事实具名拒绝，需显式承认。

## 3. 回归

- 新增 `backend/tests/test_episode_seconds_verdict.py`：未拆镜每集=目标、覆盖只进
  头部；拆镜后 overview 与预览同读落镜之和，且与覆盖值可区分。
- 新增 `frontend/src/test/episodeSecondsDisplay.test.tsx`：即使旧字段带
  deviation/boundary_note，行也不渲染；头部覆盖标识保留。
- 更新 `test_duration_target_chain.py`：预览偏差断言改为新显示契约（未拆镜=目标、
  偏差 0、覆盖进头部），确认门禁拒绝断言保留。
- 定向批次 7 模块 144 例全绿；前端全量 93 文件 / 470 例、tsc、build、Ruff 全绿。
- 后端 8 分片全量：见 §5。

## 4. 真机验证（D:\Shine\data，《女巫1221-7》）

- 重启后 ⑥ overview：ep-01 `planned_seconds=75.1`（9 镜）、ep-02 `106.5`（16 镜）；
  浏览器集卡「3 场 · 9 镜 · 约 75 秒」「1 场 · 16 镜 · 约 107 秒」。
- ② 预览只读复核（不新建草稿）：每集 75.1 / 106.5，头部总时长 181.6，
  覆盖标识仍「原文预计约 1105 / 4910 秒，占 23%」。

## 5. 全量与测试隔离修复

- 首轮 8 分片 7/8 干净；chunk-5 的 11 个 `test_v13_preparation` 失败为**既有测试隔离
  缺陷**被新增测试文件改变分片后暴露：`test_director_setup_contract.py` 在
  `shine.comfyops.local` 首次导入前 patch `installer.LocalInstaller`，同分片后续测试
  首次导入 local 时其模块级 `from … import LocalInstaller` 永久捕获假类
  （`IsolatedInstaller`，无 `.recover`）。与本轮源码改动无关（installer/local 未改）。
- 修复（不弱化任何断言）：该测试先导入 local，再同时 patch installer 与 local 两处
  绑定，teardown 一并恢复。共分片复跑 0 失败；孤立 69 例绿。
- 含修复的最终 8 分片全量：8/8 exit 0、`shards_with_failure_markers=0`、逐分片 0 条
  FAILED/ERROR（291 文件，日志 `F:\ShineTest\shards-20261121-202033-492\chunk-*.log`）。

## 6. 诚实边界

- 历史分集计划/导演包/旧交付的存储 `planned_seconds` 不重写（冻结快照与历史
  provenance 保留）；新显示与新交付读裁决。
- 本轮未改 `worker.py`/`preproduction.py`，不触发 VLM 重校准义务。