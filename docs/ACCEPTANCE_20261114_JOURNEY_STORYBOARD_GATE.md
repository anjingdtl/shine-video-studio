# 旅程第 ③ 阶段门禁读错计划修复（2026-11-14）

## 症状

导演模式第 3 阶段面板显示「2/2 集已确认（v5，13 镜）」，右侧旅程却报
`EPISODE_PLAN_NOT_MATERIALIZED`「按剧本分好的集还没有全部拆成镜头」，
第 ③ 步不绿、无法推进。用户确认动作（含自动确认）实际已生效。

## 根因

两条链路读的不是同一份计划：

- **自动确认 / 面板**：每集落镜后 `streamline._auto_confirm_completed_episode`
  → `confirm_episode_shots` 把镜头写入**新版本** CONFIRMED 计划（旧版转
  SUPERSEDED）；面板 `EpisodeStoryboardPanel` 读最新 CONFIRMED 计划的
  `shot_ids`，故显示 2/2。
- **旅程门禁**：`journey.py` 规定作品里只要存在导演包，就读**包绑定的旧计划**
  （`package.episode_plan_id`）。旧逻辑对该旧计划的**全部集**检查 `shot_ids`。
  该包是早先「只冻第 1 集」的范围包（冻结门禁 `package.build_package` 只要求
  **所选集**已落镜，故合法），第 2 集在旧版计划里为空 → 门禁永久阻塞。

即：gate 拿旧包冻住的旧计划做当前验收，与面板的当前台账脱节。

## 修复

`backend/shine/director/journey.py` 第 ③ 阶段：

- `storyboard_plan = latest_confirmed_plan(db, project_id) or plan`：
  计划级判定（mode / 落镜 / 逐集事实）改读最新确认计划，与面板同源。
- 落镜门禁范围收窄为**导演包实际选定的集**（`DirectorPackageEpisode.episode_id`）；
  无包时仍验整计划。只冻第 1 集的包不再被第 2 集拖住，符合「每集独立推进」。
- 新增 fact `storyboard_plan_version`，门禁读的是哪版计划可直接核对。
- 镜头级范围（`scope_shot_ids`）仍按包 / Run 冻结范围，不变。

## 回归

新增 `backend/tests/test_director_streamline.py::test_journey_follows_the_current_plan_beside_an_older_scoped_package`：
冻第 1 集的范围包 → 第 2 集重试落镜并自动确认 → 旅程必须转绿、2/2、
`storyboard_plan_version` 等于最新确认版。

定向执行（仓库根，`env -u NO_PROXY -u no_proxy CODEBUDDY_SAFE_DELETE_ENABLED=0`）：

- 批次 1：`test_director_streamline` / `test_director_run_scope` /
  `test_director_entries_and_post` —— 32 项全过（含新回归）。
- 批次 2：`test_four_stage_reacceptance` / `test_four_stage_section_bridge` /
  `test_director_journey_closeout` / `test_director_assets_closeout` /
  `test_director_basis_grouping` / `test_director_script_basis_closeout` /
  `test_director_long_form` —— 77 项，exit 0，无 FAILED/ERROR（1 个既有 skip）。
- `ruff check` 两个改动文件通过。

## 边界

- 报障作品不在本机栈上；根因由代码机制确认并以离线回归完整复现，**未真机验证**。
- 未改 `worker.py` / `preproduction.py`，不触发 VLM 重校准义务；前端零改动。
- 旧安装临时解法：到导演包步骤重冻一个覆盖全部集的新包（旧包保留为历史），
  冻结门禁读最新计划，成功后旅程即解阻。