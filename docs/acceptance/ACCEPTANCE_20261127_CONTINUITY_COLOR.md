# 验收报告：镜间衔接与色调连续性专项治理（2026-11-27）

基线 commit：`88f985e1056abbdaf349eb95d43617e5d4d56308`（main，未提交改动，未 push）。

## 1. 问题与根因（Test B：672×384 + PDD 8 步，无二采、无 2X）

| 边界 | 现象 | 实测（基线） |
|---|---|---|
| S2→S3 | 第三镜开头回溯重放第二镜约 3 秒 | `MAE=28.4`，S3[0..42] 匹配 S2[34..36] |
| S3→S4 | 色调跳变 + 回溯约 3.8 秒 | `MAE=31.9`，dLuma `+14.2`，d(R-B) `+2.9`，S4[0..48] 匹配 S3[14..52] |

根因：
1. `last_frame` 镜没有真正的首帧引导，只靠 `<Picture k>` 参考图，模型会回退重放。
2. 空场景板 `<Picture 2>` 以"环境与光照参考"身份进入，压过上一镜末帧的曝光与色温。
3. 跨场景时 `previous_shot` / `previous_shot_lighting` 未传入，台词/光线可倒退（S2→S3 的"光已离开脸"→"光重新触到眉心"）。
4. 成片装配 `-ss` 在输入前，亚秒裁切不帧精确；接缝没有确定性分析与重复首帧裁剪。

## 2. 改动（按"一套事实、一个裁决、一个执行者"收敛，Ref2VA only 不变）

- 图编译：`backend/shine/comfy/graph_compiler.py` 新增 `first_frame_guide_slot`，接入 `MiniMaxH3AddGuide(frame_idx=0)`，复用连续性参考的 `LoadImage`，不另开入口；`resolve_frozen_graph_execution_form` 兼容旧冻结哈希。`vram_guard.py`、`foundation/h3.py`、`services.py`、`worker.py`、`manual4/*` 同步透传。
- 提示词：`backend/shine/h3/compiler.py` 升到 `shine-h3-compiler/2.4.0`。`last_frame` 接缝时空场景板降级为"建筑与空间布局"，末帧锚点锁定曝光/白平衡/调色。`cut` 镜与 2.1.0–2.3.0 字节一致。
- 连续性裁决：`agents/continuity_engine.py` 新增 `CONTINUATION_OPENING_CONFLICT`（`lighting_rewinds_predecessor`）与 `CONTINUATION_CHAIN_LONG`（≥3 连续 `last_frame`）；`scene_director.py`、`film.py`、`api_film.py`、`materialprep/story.py` 对齐并传入 `previous_shot`。
- 装配：新增 `media/seam.py`（确定性接缝分析与安全裁剪）；`media/assembly.py` 的 `-ss` 改为输入后；`delivery_engine.py`、`episode_delivery.py` 写入 `edit_rules["seam_report"]`，尊重 `locked_shot_ids`。
- 测试：`test_graph_compiler.py`、`test_h3_compiler.py`、`test_literary_to_h3_consistency.py`、`test_continuity_engine.py`、新增 `test_seam_continuity.py`；`test_style_lora_assembly.py` 的假 ComfyUI 补声明 `MiniMaxH3AddGuide` 并加断言。

## 3. RTX 5080 A/B 探针（seed=77，4s）

| 方案 | f0 MAE | dLuma | d(R-B) | 回溯 |
|---|---|---|---|---|
| 基线 S3→S4（无 AddGuide） | 31.9 | +14.2 | +2.9 | 有，回到 S3[14] |
| `g1_reuse_with_scene`（AddGuide 复用 `<Picture 3>` + 场景板降级） | 2.73 | -2.4 | +0.7 | 无 |

采用 `g1_reuse_with_scene`。

## 4. 隔离真机复验（API 18824，单 Worker，4 镜）

- 项目 `a67571f9-0bd9-43a7-8a85-d24714bbfc19`，series `9aba623a-d3a0-4163-802c-61eecec4909c`，约 429 秒 `COMPLETED`/`RELEASED`，交付单 `fa853b66-a30c-4125-b854-cda519ac10c7` `DELIVERED`。
- 4 个 job 均 `PASSED`，`compiler_version=shine-h3-compiler/2.4.0`。
- 图哈希：S1/S2 `c7823987b2a4…`（无 AddGuide）；S3/S4 `30a53f529719…`（含 `MiniMaxH3AddGuide`，`frame_idx=0`）。

### 接缝实测（`seam_report` 与逐帧像素）

| 边界 | 基线 MAE | 新 MAE（裁后） | dLuma（裁后） | d(R-B)（裁后） | 回溯 |
|---|---|---|---|---|---|
| S2→S3 | 28.4 | 4.36（原始 4.43） | -4.1 | +0.7 | 无（`curr[0..48]` 最近邻均为 `prev[106]`） |
| S3→S4 | 31.9 | 2.67（原始 2.63） | -1.79 | +1.4 | 无（前 24 帧内单调靠近末帧，无重放） |

- EDL：第 3、4 段 `in_s=0.167`（跳过 4 帧重复首帧，`dedupe_anchored_opening_frame`）。
- S1→S2 为 `cut`，`MAE=58.7`（60.86 像素复测），属独立开场，不在治理范围。
- 判定函数：对 Test B 原 S2→S3 规格调用 `check_continuation_opening`，返回 `CONTINUATION_OPENING_CONFLICT`（`lighting_rewinds_predecessor`）；S3→S4 无冲突。
- 解码：4 个分片与成片 `ffmpeg -v error -f null -` 返回码 0；成片 420 帧、17.52 秒、672×384、24fps。
- 证据图：[S2→S3](../evidence/20261127-continuity-color/seam_s2_s3_comparison.png)、[S3→S4](../evidence/20261127-continuity-color/seam_s3_s4_comparison.png)。

### 未证实 / 需如实说明

- S4 在镜内逐渐变暗（均值亮度 70→32），这是剧情要求的"暮色渐暗"，不是接缝问题。
- S3→S4 接缝后第 36、48 帧的最近邻落在 `prev[40]`、`prev[52]`：画面本身是静止站姿，镜内内容相似，不是重放；以 `seam_report.backtrack_detected=False` 为准。
- 只验证了 1 组 4 镜、1 个种子。未做多种子、多场景统计，不能据此声称所有衔接都已无抖动。
- 未做人工逐帧观感评审；视觉判断仅基于上面两张对比图。

## 5. 回归

- 后端 8 分片：首轮 7/8 通过，`chunk-7` 失败 1 例（`test_style_lora_assembly`，夹具缺 `MiniMaxH3AddGuide` 声明，已修）；重跑 8/8 退出码 0，`shards_with_failure_markers=0`（命令 `run_backend_shards.ps1 -WorkRoot F:/ShineTest -Shards 8 -KeepRuns 3`，日志 `F:\ShineTest\shards-20261127-220729-216`）。
- 前端：`vitest run --no-file-parallelism` 100 文件 / 524 用例通过；`tsc --noEmit` 0；`vite build` 0。
- `alembic heads`：`f180f0010010 (head)`，单头，无新迁移。
- `ruff check --ignore E501`：改动与新增文件全部通过。

## 6. 日常工作台交还

- 开工前备份根 `.env`，SHA-256 `931B8BBAAE2E803225ACD56A00E512329509EB00F5F99025B9A98F355C25D5C8`；收尾复核一致。
- 隔离栈已停：18824/15182/18794/8188 无监听，隔离库无未释放 GPU 租约。
- 用 `启动 Shine 工作台.cmd` 重启：`:8000` `data_dir=D:\Shine\data`、`api_online=True`、`worker_online=True`；`:5173` HTTP 200；日常 18 个作品 ID 与开工快照完全一致。`D:\Shine\data` 未被写入。
- 复验过程中曾一次 Worker 重启导致两个 Worker 并存、第 2 镜 `HANDOFF_IDLE_CLAIM_LOST`；已整栈清理重建后重跑，上表数据来自重跑结果。

## 7. 未提交说明

改动均为未提交状态，未执行 `git push`。保留 `.trae/`、`.vite/`。
