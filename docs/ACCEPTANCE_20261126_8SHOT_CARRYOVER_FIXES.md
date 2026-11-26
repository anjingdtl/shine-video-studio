# Shine Video Studio — 2026-11-26 八镜穿测遗留项修复与本地 H3 复验报告

- **基准主线**：`eebd89538cb386c49d5233601252f693cba0eb6a`（含 `98be89f` 代码修复及 `eebd895` 验收证据）
- **复验样片**：《灰烬圣剑：古堡守望》（`project_id = 6008cc79-cb41-4453-a700-44a546971ad5`）
- **历史基线保留**：
  - 原八镜只读库：`.local-acceptance/medieval-8shot-20261126/data/shine.db`（SHA-256 `6B9AAE536CB51FB9A364315290411D9D666E76BF8BD0833D2C9748BD5F6817D1`，全程只读未改动）
  - 原导演包：`b32a0d17-0a06-41c7-802c-af4db7af7a7a`（`CONFIRMED`）
  - 原运行：`b01d1346`（原 1–6 镜通过产物保留并直接复用）
  - 原交付单 v1：`e882a0fc-bcce-47c1-8737-5b8b339f4b79`（产物 `e5cf798f-57b0-41ea-9989-eee3afe0835d`，SHA-256 `cc49a38025b2538c543225fa473335256f0927d93f6a9c67322c80e75d8bcad3`，完整保留）
- **隔离复验目录**：`.local-acceptance/reaccept-20261126-fixes/`（原库与媒体的完整隔离副本）
- **审片身份边界**：未改动 `backend/shine/quality/client_identity.py` 所锁定的任何文件（重合数 `0/27`）。

---

## 一、逐项修复与验收结论总览

| 序号 | 优先级 | 问题项 | 状态 | 关键证据与核查结果 |
| :--- | :--- | :--- | :--- | :--- |
| 1 | P1 | 修复第 6→7 镜重复举剑 | **PASS** | `continuity_engine.check_continuation_opening` 在导演确认/修订共用入口拦截 `CONTINUATION_OPENING_CONFLICT`（`CUT` 放行）；`shine-h3-compiler/2.3.0` 将 `last_frame` 开场锚点置顶 `[Shot 1]`。真实 H3 第 7 镜（`32bc14ad`）开场 0.00s–1.50s 从举剑姿态单调压低，无二次抬剑；归档图哈希 `dba5d4de...fc4` 实测引用 `last_frame` PNG `6143ed20`（`f4e75b1fb86f...`，与第 6 镜末帧 `SSIM=1.0000`）。 |
| 2 | P1 | 关闭 D12 并打通换素材后修订重拍 | **PASS** | 晋升回绑 `0` 时显式告知「已入库但以下镜头仍用旧图」；镜头绑定投影（`shot_binding`）真实反映旧图与新图分离。通过显式冻结镜修订换图将新圣殿底图 `bca2133a` 仅绑定到第 8 镜（第 5–7 镜保持 `9616e8b2`），真实 H3 第 8 镜（`7b8e8d7b`）节点 `42` 载入 `shine_bca2133a-6eba-4654-9a8b-319205b09efe.png`，消除多余提灯、保留收剑与人物一致性，并从 Web 端选取第 7/8 镜完成第 2 版整集交付（`40fa40fc`，60.479s）及浏览器播放。 |
| 3 | P1 | 收尾编译器版本错配 | **PASS** | `COMPILER_VERSION` 升级至 `shine-h3-compiler/2.3.0`；`1.2.1`–`2.2.0` 已发布版本逐字节稳定重放。新增受限可审计历史兼容 `2.1.0 -> 2.2.0`（仅当 `HistoricalReplayEvidence` 同时具备冻结快照、64 位十六进制 `prompt_graph_sha256` 与非 mock 真实 attempt）。原库 11 条历史任务逐一核验：3 条原生 2.1.0 严格通过，8 条错标 2.1.0 经真实证据通过 `2.2.0` 兼容并保留原标签与提示词字节，11 条篡改样例全部拒绝；两条新建 2.3.0 任务严格一致通过。 |
| 4a | P2 | 逐镜事实裁剪收敛 | **PASS** | `2.3.0` 对单人/零道具镜剔除未出场角色外貌（如 `Velen` / `维伦`）及英文 `and`/`&` 串联多道具行（`Props: rune sword and brass lantern.`），同时保留 `Era:` / `Tone:` / 时代 / 基调等世界观事实，并保留 `2.1.0` / `2.2.0` 既有行为。 |
| 4b | P2 | 项目管理当前镜数口径 | **PASS** | 统一读取 `confirmed_shot_count` 投影：项目管理页对《灰烬圣剑：古堡守望》显示 **`8 镜`**（不再把历史失效行算作 `35 镜`），覆盖重拆、换版本与空作品场景。 |
| 4c | P2 | 五视图开关刷新持久化 | **PASS** | 复用既有 `AppSetting`（`material-prefs:{project_id}`）按作品持久化 `character_sheet` 偏好（审计码 `MATERIAL_PREFERENCES_UPDATED`），缺失多角度 LoRA 时自动关闭并拦截，浏览器刷新后保持选中态。 |
| 5 | P2 | Windows 后端分片回归代理兼容与清理 | **PASS** | `run_backend_shards.ps1` 增加 `Resolve-LoopbackNoProxy`，为回归子进程注入 `127.0.0.1,localhost,::1` 并清洗 HTTPX 无法解析条目（`<local>`、`[::1]`、`127.*`、`*` 等），不改动系统代理及产品运行配置；修复 `$shouldKeepBasetemp` 变量大小写覆盖 `[switch]$KeepBasetemp` 的缺陷。8 分片回归 `8/8` 全绿、`bt-*` 清理归零。 |

---

## 二、核心设计与修改清单（遵循「一套事实、一个裁决、一个执行者」）

### 1. 第 6→7 镜连拍开场连续性与 `H3PromptCompiler` 2.3.0
- 唯一连续性开场裁决：`backend/shine/agents/continuity_engine.py`（`check_continuation_opening`）与 `backend/shine/director/streamline.py` 共用，在 `continuity.mode == "last_frame"` 时检出「接拍开场姿态逆转前镜收尾姿态（如前镜已举剑而本镜 `entry_state`/`blocking` 写压低）或开场自相矛盾」，返回 HTTP 409 `CONTINUATION_OPENING_CONFLICT`；`CUT` 镜不受限，显式尊重导演切镜判断。
- 提示词生成：`backend/shine/agents/scene_director.py` 更新接拍约束提示；`backend/shine/h3/compiler.py` 升版 `shine-h3-compiler/2.3.0`，在 `last_frame` 下将「0.0s 承接前镜末帧姿态、直接延续后续动作」置顶 `[Shot 1]` 并在 `retention_analysis` 补强首帧道具手持高度保留，同时补充 `_continuity_mode()` 兼容字典态 `continuity`。

### 2. D12 真实绑定投影、换图修订、重拍与整集 v2 装配
- 真实采纳反馈与镜头绑定投影：`backend/shine/materialprep/service.py`、`backend/shine/comfyops/assets.py`、`backend/shine/api_material.py`、`backend/shine/api_asset_center.py` 复用 `bind_adopted_masters_to_shots`，扩展 `adoption` 与 `shot_binding` 投影（`adopted_but_unbound_shots` / `bound_shots`），当 `rebound_count == 0`（如镜头已冻结）时显式提示新图已入库及仍在沿用旧图的镜头，并在 `frontend/src/components/MaterialPreparationPanel.tsx` 与 `StreamlinePanels.tsx` 同步渲染。
- 冻结镜显式修订换图：`backend/shine/api_segments.py`、`backend/shine/services.py`（按场景位原地替换 `scene` 绑定并更新 `required_asset_ids`，保留角色与道具绑定）及 `GenerationSegmentsPanel.tsx` / `StreamlinePanels.tsx` 支持单镜选定旧图→新图修订；已通过的旧包 `b32a0d17`、历史镜头产物与 v1 交付保持不动，用户可在前端按镜独立勾选需修订镜头，重拍后经既有 `ProductionRunBatch` / `_ensure_episode_delivery` 装配第 2 版成片。

### 3. 编译器版本溯源兼容（2.1.0 ↔ 2.2.0 与 2.3.0）
- `backend/shine/h3/rulebook.py` 引入 `HistoricalReplayEvidence` 与 `historical_replay_evidence(db, job)`，并作为唯一入口接入 `recompile_for_provenance` 与 `verify_request_provenance`。
- 兼容仅限「存档版本为 `2.1.0`、实际匹配 `2.2.0`、且具备真实非 mock 尝试记录 + `64` 位 hex `prompt_graph_sha256` + 完整冻结快照」的历史错标请求，重放时写入带原文详情的 `SHINE_PROVENANCE` 警告并保留原始版本号与提示词字节；缺失证据、mock 产物或任意字节篡改一律拒绝。新任务严格核对并落盘 `shine-h3-compiler/2.3.0`。

### 4. 逐镜事实裁剪、项目当前镜数与五视图开关持久化
- `backend/shine/h3/compiler.py`（`2.3.0`）：在英文 `and` / `&` 串联道具语句上识别已/未绑定道具集合并裁剪无关道具，剔除未在当镜出场的纯粹外貌行，同时保留 `Era:`、`Tone:` 等全局设定；`2.1.0`/`2.2.0` 旧版本行为不变。
- `backend/shine/director/journey.py` 与 `frontend/src/components/ProjectPortfolio.tsx`：统一以 `confirmed_shot_count` 作为当前项目镜数来源（`8 镜`），不再统计历史作废镜头行。
- `backend/shine/api_streamline.py`、`backend/shine/api_material.py`、`frontend/src/App.tsx`、`frontend/src/components/MaterialGenerationProgress.tsx`、`frontend/src/auditEventCodes.json`、`frontend/src/constants.ts`：新增并同步 `MATERIAL_PREFERENCES_UPDATED` 审计码，按作品读写 `material-prefs:{project_id}`，缺失 LoRA 时强制关闭并受门禁保护。

### 5. Windows 后端分片回归脚本代理兼容
- `run_backend_shards.ps1`：新增 `Resolve-LoopbackNoProxy` 为子进程写入干净 `NO_PROXY`/`no_proxy`；将局部变量重命名为 `$shouldKeepBasetemp`，修复 PowerShell 变量大小写不敏感导致的 `[switch]$KeepBasetemp` 误判；保留 UTF-8 BOM、全绿自动删除 `bt-*`、失败自动保留及 `-KeepRuns` 清理策略。

---

## 三、历史 11 条 H3 任务溯源全量复核（原库只读）

来源证据：`.local-acceptance/reaccept-20261126-fixes/provenance-recheck-11jobs.json`

- 总计：**11/11** 在真实历史证据下通过兼容性溯源复核（无一例误杀）
  - **3 条原生 `2.1.0` 严格通过**（0 警告）：`ac55dc2c`、`711c8b3b`、`ecfac935`
  - **8 条错标 `2.1.0`（实际 `2.2.0`）历史兼容通过**（带 `SHINE_PROVENANCE` 警告，原始标签与提示词字节保持原样）：`cb0b1917`、`72f0396d`、`8fe79262`、`2102abbd`、`c0e54257`、`805b6c5b`、`a7386424`、`c1d7d9c2`
  - **11/11 篡改测试样本全部拒绝**（`tamper_rejected_count = 11`）

---

## 四、隔离真实 MiniMax-H3 与浏览器端到端复验证据

证据目录：`.local-acceptance/reaccept-20261126-fixes/`

### 1. Web UI 与设置链验证（隔离端口 API `127.0.0.1:8001`、Web `127.0.0.1:5174`）
- Setup 重配置至隔离目录并激活验证：`/api/v1/setup/verdict` 返回 `READY`（`services_verified: true`，`node_count = 1281`）。
- 浏览器子代理实测（`BROWSER_HOST_FACTS` 已核实访问 `http://127.0.0.1:5174`，无拦截源，截图 `final_step_22.png`、`final_step_34.png`、`final_step_55.png`、`final_step_98.png`）：
  1. 项目管理页显示《灰烬圣剑：古堡守望》为 **`8 镜`**（不再显示 `35 镜`）。
  2. 素材准备页开启五视图开关并经 `PUT /api/v1/projects/6008cc79-cb41-4453-a700-44a546971ad5/material-preparation/preferences` 返回 200；执行完整页面 `reload` 后仍保持勾选，随后正常关闭还原。
  3. D12 状态诚实展示 `圣殿残垣祭坛` 为 `已入库·镜头仍用旧图（0/4）`，随后在前端「按修订换图」仅勾选第 8 镜（`47cfe359`，rev 8→9）确认换用 `bca2133a`，界面同步刷新为 `部分镜头已绑定（1/4）`（第 5–7 镜保持 `9616e8b2`）。
  4. 第 7 镜（`b8654676`，rev 7→8）修订为开场保持举剑、随后压低剑尖，保留原 `last_frame` 指向（第 6 镜 `980b3dfe` / `805b6c5b`）、原中文对白「你把封印藏在了祭坛之下？」及 5 项绑定。
  5. 在 `#d/deliver` 仅对第 7 镜、第 8 镜排队重拍，并在成片通过后先后点击第 7 镜与第 8 镜「选为当前装配版本」，自动触发并完成第 2 版整集交付（`DELIVERED`），随后在浏览器 `<video>` 播放器内真实播放从 `0:02 / 1:00` 推进至 `0:42 / 1:00`（`HTTP 206` 分片拉流），`我已完整观看这一版成片（含声音）` 保持未勾选，留待用户本人艺术签收。

### 2. 真实 H3 请求与产物身份

| 对象 | ID / 关键字段 | 实测结果 |
| :--- | :--- | :--- |
| **第 7 镜新版（`32bc14ad`）** | `job_id`: `32bc14ad-d2a2-42b1-b081-4fc428c0130b`（`shot_revision=8`）<br>`attempt_id`: `07277e5c-0339-4eca-87ac-027738b8fcda`（attempt 1）<br>`provider_job_id`: `f24b169a-c437-4e67-9945-0d6e9c0e8867`<br>`prompt_graph_sha256`: `dba5d4de01dab1b697644db113b21410b95be4dd4c6e06fda35aa16be1c13fc4`（归档图逐字节匹配）<br>`prompt_sha256`: `ba1b205672485cf7d55c420c54a3c8576229faefc63ac3c266d130caa6abd472`<br>`compiler_version`: `shine-h3-compiler/2.3.0`（溯源 `passed=true`，0 警告 0 违规）<br>`artifact_id`: `b4ac9e1f-690b-4721-81ae-b31082f65278`<br>MP4 路径：`.../视频/current/灰烬圣剑：古堡守望_视频_007镜_01集_v2.mp4`（`2,216,636` 字节，`1344×768` H.264 / 24fps + AAC 32kHz，`7.999s`）<br>MP4 SHA-256: `137dc4c3d70f2d1beec7ebbf2b75894b9f61f2fe83394cda3068582252574e45`<br>全文件解码：`ffmpeg -v error -i ... -f null -` 返回 `rc=0` | `PASSED`（真实 GPU H3）；节点 `45` 为 `shine_6143ed20-11bd-477f-be29-85b057d06500.png`（`last_frame` 参考）；对比图 `shot6-to-7-recheck.jpg` 验证开场 `0.00s` 举剑衔接前镜末帧（`SSIM=1.0000`）并单调压低剑尖，无重复举剑。 |
| **第 8 镜新版（`7b8e8d7b`）** | `job_id`: `7b8e8d7b-fe50-48a6-8ef2-617857155e16`（`shot_revision=9`）<br>`attempt_id`: `cc11736b-875a-4e26-9ae4-47ab842b23bb`（attempt 1）<br>`provider_job_id`: `1a230d86-101d-4b50-846b-96686d758681`<br>`prompt_graph_sha256`: `d866f6034de17a4e36130eb0dd05d7b9173120b7a72ad3398c3a1f7da7699aa8`（归档图逐字节匹配）<br>`prompt_sha256`: `e1625a313b2095f1296d8dd45c7aed6ba2d441ce5e4ac4cabf99086dd5aaf76f`<br>`compiler_version`: `shine-h3-compiler/2.3.0`（溯源 `passed=true`，0 警告 0 违规）<br>`artifact_id`: `8598c419-455d-4e31-bb5b-acd5e84b8b79`<br>MP4 路径：`.../视频/current/灰烬圣剑：古堡守望_视频_008镜_01集_v2.mp4`（`3,627,555` 字节，`1344×768` H.264 / 24fps + AAC 48kHz，`7.998s`）<br>MP4 SHA-256: `1a3726e226f2ce053787d0779f8d0a1e3dc45dcd5067b5b39e4d74140577986e`<br>全文件解码：`ffmpeg -v error -i ... -f null -` 返回 `rc=0` | `PASSED`（真实 GPU H3）；节点 `42` 为 `shine_bca2133a-6eba-4654-9a8b-319205b09efe.png`（新圣殿底图）；对比图 `shot8-before-after.jpg` 验证新圣殿斜倒石柱场景生效、多余提灯消除、长剑入鞘动作及男女主外观保持一致。 |
| **第 2 版整集交付（v2）** | `delivery_order_id`: `40fa40fc-5b82-4651-954b-928775d87bec`（`version=2`, `origin="segment_revision"`）<br>`artifact_id`: `cc9eb8d6-c430-4056-ab01-357a8a58b177`<br>MP4 路径：`.../视频/delivery/history/灰烬圣剑：古堡守望_视频_001-008镜_01集_v2.mp4`（`23,341,516` 字节，`1344×768` H.264 / 24fps + AAC 48kHz，`60.479329s`）<br>MP4 SHA-256: `79e1a7c6323a47fa85795d8409f89f0ce8f11c216a7b640b917f3e0f4f50b368`<br>全文件解码：`ffmpeg -v error -i ... -f null -` 返回 `rc=0` | `DELIVERED`；旧第 1 版 `e882a0fc`（`e5cf798f`）及旧第 7/8 镜产物文件均保留未覆写。 |

> **收尾清理与租约/进程核查**：通过 `stop_shine.py --data-dir ...` 停止全部隔离进程后，本机 `8001` / `5174` / `8790` / `8188` 端口监听为 `0`，隔离库 `resource_leases` 表中非 `RELEASED` 租约数 **`0`**，GPU 空闲（0% / 1977 MiB）；原库 `medieval-8shot-20261126/data/shine.db` 保持字节一致。注：隔离验证阶段调用官方 Setup 激活时，按产品既有设计同步将工作区根 `.env` 中的 `SHINE_DATA_DIR` 与 `SHINE_SETUP_RECIPE` 更新为本次隔离目录（`.env` 属 `.gitignore` 忽略项，未纳入 Git 变更）。

---

## 五、最终提交树全量回归结果

所有检查均在最终修改完成后执行并通过：

1. **Copilot 契约一致性校验**：
   - 命令：`backend\.venv\Scripts\python.exe backend\scripts\generate_shine_copilot_contracts.py --check`
   - 结果：`exit 0`（`contracts ok: app=2.2.0 actions=16 knowledge=22 discovered=483`）
2. **Ruff 静态检查**：
   - 命令：`backend\.venv\Scripts\ruff.exe check backend/shine backend/tests`
   - 结果：`exit 0`（`All checks passed!`）
3. **Alembic 迁移头检查**：
   - 命令：`Push-Location backend; ..\backend\.venv\Scripts\alembic.exe -c alembic.ini check; Pop-Location`
   - 结果：`exit 0`（`No new upgrade operations detected.`）
4. **前端串行全量回归与生产构建**：
   - 测试命令：`Push-Location frontend; npm.cmd exec --offline -- vitest run --no-file-parallelism` -> **`exit 0`（100 个测试文件 / 521 个用例全部通过，145.34s）**
   - 构建命令：`npm.cmd run build`（`tsc -b && vite build`） -> **`exit 0`（1.47s，1662 modules）**
5. **后端 8 分片全量回归**：
   - 命令：
     ```powershell
     $env:CODEBUDDY_SAFE_DELETE_ENABLED='0'
     pwsh -NoProfile -File .\run_backend_shards.ps1 -WorkRoot F:\ShineTest -Shards 8 -KeepRuns 3
     ```
   - 结果：**`exit 0`**，`shards=8, files=318, wall=716s`
   - 分片明细：`chunk-0..7` 全部 `clean (exit 0)`（日志目录 `F:\ShineTest\shards-20261127-020738-351`），`shards_with_failure_markers=0`，日志中 `^FAILED` / `^ERROR` 行数 **`0`**，本轮 `bt-*` 临时目录残留 **`0`**（`basetemp removed (all passed)`）。

---

## 六、其余按要求实测状态说明（不夸大、不缺报）

| 能力 / 环节 | 实测状态 | 说明 |
| :--- | :--- | :--- |
| **技术门禁（`technical`）** | **PASS**（第 7/8 镜实测） | 第 7、8 镜 `technical = passed`（`ffprobe` + 全文件解码 `rc=0`）；整集 v2 全文件解码 `rc=0`。 |
| **语义审片（`semantic` / `style`）** | **NOT RUN**（未配置模型路由） | 隔离环境未配置 VLM 审片路由，门禁真实记录为 `not_run`（`SEMANTIC_REVIEW_NOT_CONFIGURED` / `STYLE_REVIEW_NOT_COVERED`），并有对应 `unavailable`（`VLM_ROUTE_NOT_CONFIGURED`）记录；`final` 门禁记为 `needs_review`，未伪造通过。 |
| **对白 / 语音核对（`audio` / ASR）** | **NOT RUN / NOT APPLICABLE** | 第 7 镜带对白，因未配置 ASR 路由记录 `audio = not_run`（`ASR_NOT_CONFIGURED`）；第 8 镜无对白，主门禁记 `audio = not_applicable`。第 7 镜与成片 v2 含完整 AAC 音轨，但自动语音内容识别未运行。 |
| **人工艺术签收（`acceptance_status`）** | **PENDING USER** | 浏览器端已演示并验证成片 v2 播放推进（`0:42 / 1:00`），刻意未勾选「我已完整观看这一版成片（含声音）」，人工艺术签收保留给用户本人决定。 |
| **RelayVSR 超分与二次精修（2nd pass）** | **NOT RUN** | 本轮拍摄策略保持 `second_pass.enabled = false`、`upscale.enabled = false`（`h3-ref2va-int8-convrot` + `native20`）。 |
| **新机安装包打包（`build_test_package.ps1`）** | **NOT RUN** | 本轮穿测遗留项修复未重新构建分发压缩包；入口编码校验 `test_package_entrypoints.py` 在后端回归中通过。 |
