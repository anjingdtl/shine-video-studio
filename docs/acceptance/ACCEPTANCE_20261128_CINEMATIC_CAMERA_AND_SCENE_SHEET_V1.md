# 验收报告：场景四视图闭环与剧情运镜调度 V1（2026-11-28）

- 方案文档：[plan_20261128_cinematic_camera_and_scene_sheet_v1.md](../plan_20261128_cinematic_camera_and_scene_sheet_v1.md)
- 开发基线：`main` 分支 `d2436d96`（保留本机既有工作区，未修改 `copilot/` 业务模块，未执行 `reset`/`push`）
- 架构遵循：`AGENTS.md` 核心原则「一套事实、一个裁决、一个执行者」、三条禁令、8 分片并行回归及 2026-11-27 日常工作台启动配置交还裁定

---

## 1. Phase 1 + Phase 2 建设摘要

### Phase 1：场景四视图闭环 + 单角原图自动路由 + `H3PromptCompiler 2.7.0`
1. **新增场景四视图核心模块**：[scene_sheet.py](../../backend/shine/asset_center/scene_sheet.py)
   - 直接复用已纳管的 `qwen-image-2.1-lora-multiple-angles`（`<mva>`，SHA-256 `548c57977c3f1c09c2c9e5f4e83e930cbc6f719c878a576ab8666ce29fef39b6`）及唯一裁决口 `evaluate_style_lora_selection()`。
   - 实现 `t2i_to_scene_sheet_v1`：Step 0 文生图产出 `1344×768` 主全景 `front_wide`，Steps 1–3 在同一 GPU 租约内经 Qwen-Image Edit + `<mva>`（`strength=0.8, steps=36, cfg=1.0, euler/simple`）依次生成 `left_quarter`（左 45°）、`right_quarter`（右 45°）、`reverse_wide`（180° 反打）3 张全尺寸空景无人物单角原图；Step 4 由 Pillow 零缩放拼接 `2752×1600`（16:9，32 倍数）四合一展示母版（无文字、无水印），并将 4 张单角原图的 `storage_path` 与 `sha256` 原子存入单条场景主资产 `provenance["scene_sheet"]["views"]`，不拆分资产表、不改 `shot.bindings` 数据库结构。
2. **素材准备与前端对称接入 `scene_sheet_mode`**：
   - 后端：[execution.py](../../backend/shine/materialprep/execution.py) 新增 `_render_scene_sheet_inside_lease()` 与 `_set_scene_sheet_progress()`；[service.py](../../backend/shine/materialprep/service.py)、[runtime.py](../../backend/shine/materialprep/runtime.py)、[api_material.py](../../backend/shine/api_material.py)、[api_streamline.py](../../backend/shine/api_streamline.py)、[api_loras.py](../../backend/shine/api_loras.py) 对称人物五视图接入 `scene_sheet_mode` 持久化偏好、批次队列与实时 LoRA 投影。
   - 前端：[CharacterSheetToggle.tsx](../../frontend/src/shared/CharacterSheetToggle.tsx)、[MaterialPreparationPanel.tsx](../../frontend/src/components/MaterialPreparationPanel.tsx)、[StreamlinePanels.tsx](../../frontend/src/components/StreamlinePanels.tsx)、[MaterialGenerationProgress.tsx](../../frontend/src/components/MaterialGenerationProgress.tsx)、[api.ts](../../frontend/src/api.ts)、[errorActions.ts](../../frontend/src/errorActions.ts) 在同一多视角 LoRA 卡片内对称挂载「场景生成 16:9 四视图母版」开关与进度展示。
   - 经生成器刷新 API Schema 契约产物（`generate_shine_copilot_contracts.py` 与 `generate_copilot_knowledge.py`），`--check` 通过。
3. **唯一视角路由与全链路校验闭环**：
   - [services.py](../../backend/shine/services.py) 在 `build_reference_manifest()` 中新增纯确定性函数 `resolve_scene_angle_views()`：单向/推拉/正反打自动展开为 1 张全尺寸单角原图（`scene_angle:*`）；跨方位摇移/跟拍/环绕/升降（`move in {pan, track, orbit, whip_pan, crane}` 且 `scene_angle_end != scene_angle`）在满足 `MAX_IMAGES (9)` 与 `MAX_TOTAL (12)` 余量时展开为起幅 + 落幅 2 张全尺寸单角原图（`scene_angle_opening:*` + `scene_angle_ending:*`），槽位紧张或缺失文件时安全降级为 1 张或回退主资产；历史普通单图场景 100% 原样透传。同步接入 `manifest_from_shot_bindings()`、`make_render_request()` 与 [package.py](../../backend/shine/director/package.py)。
   - [provider.py](../../backend/shine/comfy/provider.py)、[output_layout.py](../../backend/shine/media/output_layout.py) 与 [preproduction.py](../../backend/shine/comfyops/preproduction.py)（真机首跑 `PREPRODUCTION_REFERENCE_CHANGED` 实测检出并顺手补齐）统一通过 `find_scene_sheet_view_by_sha()` 对展开后的单角原图执行磁盘 SHA-256 严格验真、防冲突上传命名及路径迁移保护。
4. **`H3PromptCompiler` 升级至 `shine-h3-compiler/2.7.0`**：
   - [compiler.py](../../backend/shine/h3/compiler.py) 冻结 `2.6.0` 至 `LEGACY_260_VERSIONS`（`<= 2.6.0` 历史字节零漂移）；`2.7.0` 在 `_subject_definitions()` 与 `_retention()` 中支持 `scene_angle_opening:*` 与 `scene_angle_ending:*` 起落幅空间几何锚定，并在 `_camera_narrative()` 中将 `camera_height` 与 `foreground_parallax` 编织为自然英文（严格屏蔽内部枚举键名裸露进 H3 提示词）。

### Phase 2：剧情张力驱动运镜调度 + 表演窗运镜联动
1. [scene_director.py](../../backend/shine/agents/scene_director.py)：`DirectedCamera` 新增可选字段 `scene_angle`、`scene_angle_end`、`camera_height`、`foreground_parallax`；解除 `static by default`，将 `beat_energy`（`calm / probe / pressure / turn_pause / erupt / aftermath`）映射到推拉摇移转运镜语法、机位高度与前景视差，并在 `landing_rows()` 完整透传至 `shot.camera`。
2. [acting_director.py](../../backend/shine/agents/acting_director.py)：`ActingWindowPatch` 新增可选 `camera_beat`，`build_acting_group_payload()` 注入 `camera_move / camera_movement`，引导动态镜头在表演组内把运镜加速/推进/定格/缓拉精准对齐 `pre_speech / delivery / aftermath` 时间窗；`apply_shot_patch()` 仅采纳合法纯英文且不含离场角色 token 的 `camera_beat`，否则无阻塞安全回退原运镜文本。
3. [crew_salvage.py](../../backend/shine/agents/crew_salvage.py) 与 [continuity_engine.py](../../backend/shine/agents/continuity_engine.py)：补充 `scene_angle / scene_angle_end / camera_height` 字段别名与同义词归一化（未知枚举软降级为 `None` 不触发 `SCHEMA_VALIDATION_FAILED`）；在唯一裁决口补充 `STATIC_CAMERA_MONOTONY`（≥5 镜且连续 ≥4 镜静态）与 `EXCESSIVE_CAMERA_MOTION`（连续 ≥3 镜高幅运动 `orbit/whip_pan/crane`）两条非阻塞 `warning`。

---

## 2. 离线全量回归证据

- **新增单测**：
  - [test_scene_sheet_and_angle_routing.py](../../backend/tests/test_scene_sheet_and_angle_routing.py)（9 用例：四合一零缩放拼接、`<mva>` 空景无人物提示词、历史单图透传、单角/正反打自动推断、跨方位双角展开与 `MAX_IMAGES=9` 槽位保护、缺失文件降级、ComfyUI 上传与 `preproduction.enforce_live_references` 哈希验真、`2.7.0` 双角语义与 `2.6.0` 字节等价回放）— **9/9 通过**。
  - [test_cinematic_camera_grammar.py](../../backend/tests/test_cinematic_camera_grammar.py)（5 用例：运镜提示词张力语法、`crew_salvage` 视角/机位高度同义词折叠、`continuity_engine` 节奏轻量告警、`acting_director` `camera_beat` 联动与非阻塞回退、`landing_rows -> build_reference_manifest -> H3PromptCompiler 2.7.0` 端到端集成）— **5/5 通过**。
- **前端全量**：
  - `npx tsc --noEmit`：**0 错误**
  - `npx vitest run --no-file-parallelism`：**100 文件 / 524 用例全绿**
  - `npm run build`：构建通过（`dist/assets/index-*.js` 1.51s）
- **后端 8 分片全量回归**（含真机检出并修复 `preproduction.py` 后的终轮复跑）：
  - 命令：`CODEBUDDY_SAFE_DELETE_ENABLED=0 .\run_backend_shards.ps1 -WorkRoot <测试根> -Shards 8`
  - 结果：**8/8 分片全部退出码 0，`files=327`，`shards_with_failure_markers=0`，墙钟 `744.4s`**
  - 归档日志：`<测试根>\shards-20261128-162948-595`（`chunk-0.log`..`chunk-7.log`），`bt-*` basetemp 已自动清理。
- **静态检查与契约**：改动及新增的 19 个后端文件 `ruff check` **0 错误**；`generate_shine_copilot_contracts.py --check` 与 `generate_copilot_knowledge.py --check` **通过**；`alembic heads` 单头 `f180f0010010`。

---

## 3. 隔离真机全链路 1 集 60 秒《香积寺大战》验收（RTX 5080 实测）

- **隔离环境**：`DataDir = <测试根>\acceptance\20261128-cinematic-camera-scene-sheet\data`（独立 `SHINE_RUNTIME_ENV_PATH`），端口 API `18829` / Web `15189` / Supervisor `18799`，采用本机规范安装 `<规范渲染器目录>`（端口 `8188`）。
- **付费 LLM 配置**：从本机测试密钥文件读取 DeepSeek 配置（`https://api.deepseek.com` / `deepseek-v4-flash`），仅在内存中注入隔离实例受保护密钥库（API Key 全程脱敏，未写入 Git/SQLite/日志/报告）。
- **剧本与分镜调度实测**：
  - 项目 `fff2f0d6-83ad-4a72-a676-8ade42cdb5da`（《香积寺大战-场景四视图与运镜验收》），1 集目标 `60s ± 10s`（单镜 ≤ 10s 红线）。
  - DeepSeek 全自动走完大纲 → 分镜书 → `SeriesArchitecture` → `DramaSceneScript` → `SceneShotBoard` → **Acting Pass**，生成 **10 镜**，原生运镜即覆盖 **5 种剧情驱动运镜**（`crane: 1, push_in: 4, pull_back: 2, track: 2, handheld: 1`，**0 镜纯静态**），其中 S1 原生生成跨方位双角 `front -> left` + `high_angle` + 前景黄尘视差，9/10 镜自带前景视差，**10/10 镜 Acting Pass 均产出时间窗级 `camera_beat` 联动**。
  - 原始两场合计 `80.5s` 超出 `60±10s` 窗被服务端 `EPISODE_DURATION_OUT_OF_TARGET` 诚实拦截；随后通过正式 `POST /api/v1/shots/{id}/revise` 接口收紧非对白尾窗至 **`63.0s`**（零改动台词），并在安守忠反打镜（S3/S4）补设 `scene_angle=reverse`、在冲锋跟拍镜（S5/S9）补设 `front->left` 与 `left->right`，以在同轮真机中完整覆盖全部 4 个视角与单/双角自动路由。
- **场景四视图与全量主素材实测（本地 Qwen-Image 2.1 + `<mva>`）**：
  - 开启 `scene_sheet_mode=true`，单次租约内真实生成 `香积寺外旷野` 4 张全尺寸 `1344×768` 空景无人物单角图（`front_wide` 2.03 MiB、`left_quarter` 1.80 MiB、`right_quarter` 1.85 MiB、`reverse_wide` 1.82 MiB）并拼接为 **`2752×1600`** 四合一展示母版（主资产 `0faccc4d-53b5-40b5-bb1e-9c8bbe847456`，SHA-256 `5b2e4b987d20...`），同步生成并晋升锁定 `李嗣业`、`安守忠`、`陌刀` 3 项主素材。
  - 四视角母版证据图：[xiangji_scene_4view_sheet.jpg](../evidence/20261128-cinematic-camera-scene-sheet/xiangji_scene_4view_sheet.jpg)（四视角古塔形制、黄土坡地貌、灰白天色与扬尘高度一致，零人物、零文字、零分屏伪影）。
- **真实 H3 渲染参数（严格按指定配置）**：
  - 分辨率：**1.0 标准横屏 `1344 × 768 @ 24fps`**
  - 二采（Second Pass）：**开启**（`second_pass.enabled=true`，`h3-tail-refine-v1`，`denoise=0.25`，`latent_scale=1.0`）
  - 2X 超清化（Upscale）：**关闭**（`upscale.enabled=false`）
  - 加速与模式：**8 步 PDD（`pdd8`）**，`h3-ref2va-int8-convrot`，Ref2VA only，编译器 **`shine-h3-compiler/2.7.0`**
- **实测产出与逐镜单/双角路由明细**：
  - Production Run `632c1634-97ac-4df9-9460-48fbdf968957`，2 个连续批次自动接力，总墙钟 **`3744s`（62.4 分钟）**，状态 **`COMPLETED`**，Episode `ep-01` 状态 **`DELIVERED`**（交付单 `b85e8bfa-0901-428f-bc9f-5404f9e7fc86`，成片 ID `4b0c3d61-d8ac-42ba-8fcd-cef163ebfe86`），活跃 GPU 租约 **`0`**。
  - **10/10 镜头全部 `PASSED`**，无 OOM，无重提，单镜 `ffmpeg -v error -f null -` 全部 10/10 零错通过；全集 30 帧抽帧核验（每镜起/中/尾）**零 2×2 分屏泄漏、地标与服化道稳定**。
  - **60 秒成片实测**：`1344 × 768 @ 24fps`，H.264 + 48kHz AAC，实际片长 **`66.396s`（1,593 帧）**，`41.14 MiB`（`43,139,901` 字节），SHA-256 `2be21572a118...`，`ffmpeg -v error -f null -` 退出码 **`0`**（注：接缝处有一条非致命 muxer 告警 `non monotonically increasing dts to muxer in stream 0: 752 >= 752`，成片完整可播，已如实记录）。
  - 10 镜 30 帧抽帧证据图：[xiangji_10shot_contact_sheet.png](../evidence/20261128-cinematic-camera-scene-sheet/xiangji_10shot_contact_sheet.png)；60s 成片与四视角原图完整留存于本机隔离证据目录 `<测试根>\acceptance\20261128-cinematic-camera-scene-sheet\evidence\`。

| 镜头 | 实测时长 | 运镜 | 机位高度 | 视角路由 | 实际送入 H3 的场景单/双角原图（`role` + 文件） | 总参考槽位 | 状态 / 解码 |
|---|---:|---|---|---|---|---:|---|
| S01 | 6.58s | `crane` | `high_angle` | `front → left` | `scene_angle_opening:front` (`front_wide.png`) + `scene_angle_ending:left` (`left_quarter.png`) | 4 / 9 | `PASSED` / OK |
| S02 | 5.87s | `push_in` | `low_angle` | `front` | `scene_angle:front` (`front_wide.png`) | 3 / 9 | `PASSED` / OK |
| S03 | 5.87s | `push_in` | `eye_level` | `reverse` | `scene_angle:reverse` (`reverse_wide.png`) | 2 / 9 | `PASSED` / OK |
| S04 | 6.58s | `pull_back` | `eye_level` | `reverse`（`last_frame`） | `scene_angle:reverse` (`reverse_wide.png`) + `continuity_derivative` | 3 / 9 | `PASSED` / OK |
| S05 | 6.58s | `track` | `eye_level` | `front → left` | `scene_angle_opening:front` (`front_wide.png`) + `scene_angle_ending:left` (`left_quarter.png`) | 3 / 9 | `PASSED` / OK |
| S06 | 5.17s | `push_in` | `low_angle` | `left` | `scene_angle:left` (`left_quarter.png`) | 2 / 9 | `PASSED` / OK |
| S07 | 6.58s | `push_in` | `low_angle` | `front` | `scene_angle:front` (`front_wide.png`) | 2 / 9 | `PASSED` / OK |
| S08 | 8.71s | `handheld` | `low_angle` | `front` | `scene_angle:front` (`front_wide.png`) | 2 / 9 | `PASSED` / OK |
| S09 | 8.00s | `track` | `low_angle` | `left → right` | `scene_angle_opening:left` (`left_quarter.png`) + `scene_angle_ending:right` (`right_quarter.png`) | 4 / 9 | `PASSED` / OK |
| S10 | 6.58s | `pull_back` | `high_angle` | `front` | `scene_angle:front` (`front_wide.png`) | 2 / 9 | `PASSED` / OK |

### 真机实测中检出并闭环的问题与诚实边界声明
1. **实测检出修复**：首轮派发真实 H3 Run 时，`comfyops/preproduction.py::enforce_live_references()` 将展开后的场景单角 SHA-256 与主资产四合一母版 SHA-256 直接比对，触发 `PREPRODUCTION_REFERENCE_CHANGED`。已在 [preproduction.py](../../backend/shine/comfyops/preproduction.py) 通过同一 `find_scene_sheet_view_by_sha()` 补齐单角原图哈希验真并补写单测与 8 分片回归，重跑即 10/10 全绿。
2. **VLM 审片校准边界**：本轮改动触及 `client_review_identity` 钉住的 `comfyops/preproduction.py`，本轮未重做本地原生 VLM 审片正负样本校准（标记为「未校准」，不复用旧 VLM 校准证据）。
3. **LLM 视角默认倾向**：实测观察到 `deepseek-v4-flash` 在未额外提示双阵营对打方位时，首镜主动给出 `front -> left` 跨角升降，其余镜头多默认 `front`；后续可在 `scene_director` 对阵/正反打提示词中进一步强调对峙双方使用 `front / reverse` 交替。

---

## 4. 竣工清理与日常工作台交还（2026-11-27 裁定）

- **隔离栈停机**：执行 `scripts/stop_shine.py --data-dir <测试根>\acceptance\20261128-cinematic-camera-scene-sheet\data` 成功停机；`18829 / 15189 / 18799 / 8188` 监听数为 `0`，活跃 GPU 租约 `0`。
- **测试根清理与量化占盘**：
  - `scripts/clean_test_workspace.py --work-root <测试根> --apply --prune-links`：清理 **752.2 MiB**（错误 0 条），并核验日常缓存原件 SHA-256 后彻底移除隔离轮次 `data/`（含 304.1 MiB 临时 LoRA 副本，累计释放 **~1,057 MiB**）。
  - 保留本轮证据目录 `<测试根>\acceptance\20261128-cinematic-camera-scene-sheet\evidence`（**90.2 MiB**，含 60s 成片 MP4、四视图母版、4 张 `1344×768` 单角原图、30 帧抽帧对比图与结构化指标 JSON）。
  - 清理后 `<测试根>` 总占用约 **568 MiB**（远低于 `≤ 2 GiB` 预算）。
- **日常配置与原入口复验**：
  - 仓库根 `.env` SHA-256 与开工快照逐字节一致：`931B8BBAAE2E803225ACD56A00E512329509EB00F5F99025B9A98F355C25D5C8`。
  - 通过日常原入口 `启动 Shine 工作台.cmd` 启动并复验：API `http://127.0.0.1:8000/health` 返回 200（`comfyui`）、`worker_online=True`、`data_dir=<日常数据目录>`、Web `http://127.0.0.1:5173/` 返回 200、日常库原有 **18** 个作品（`4ed4bae7-...` 至 `2ef51fa9-...`）完整可见。
