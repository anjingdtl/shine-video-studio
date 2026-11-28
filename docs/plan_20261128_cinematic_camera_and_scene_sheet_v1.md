# Shine Video Studio — 场景四视图与剧情运镜调度建设方案（V1）

> **文档编号**：`plan_20261128_cinematic_camera_and_scene_sheet_v1`
> **建议落盘路径**：`docs/plan_20261128_cinematic_camera_and_scene_sheet_v1.md`
> **开发基线**：本地 `main` 分支（基于 `d2436d96` 及本机最新提交，当前编译器版本 `shine-h3-compiler/2.6.0`）
> **架构遵循**：`AGENTS.md` 核心原则「一套事实、一个裁决、一个执行者」、三条禁令、8 分片并行回归及 2026-11-27 日常工作台启动配置交还裁定
> **真机验收基线**：《香积寺大战》1 集 / 60 秒（±10s 弹性）、1.0 分辨率（`1344×768` 768P 标准横屏）、开启二采（`second_pass=true`）、关闭 2X 超清（`upscale=false`）、8 步 PDD（`pdd8`）、LLM 配置读取 `C:\Users\Administrator\Desktop\AIstudio\deepseek-test.txt`（严禁落库/入仓/打印密钥）

---

## 一、背景与建设目标

上一轮 `H3 演技驱动 V1`（`2.5.0 / 2.6.0`）已解决人物微表情、1 秒空拍与四维声学控制问题。当前与头部 AI 短剧相比，剩余最大的两块质感差距集中在：

1. **场景空间单一导致正反打与运镜飘移**
   - 目前 `materialprep/image_prompts.py` 对 `family == "scene"` 只生成 1 张单视角全景图。一旦分镜切到 180° 反打、侧向跟拍或摇移镜头，H3 缺乏对应墙面/方位参考，容易在摇镜中途凭空重画背景或让大场景地标漂移。
   - 仓库内 `backend/shine/asset_center/character_sheet.py` 已完整打通基于 `qwen-image-2.1-lora-multiple-angles`（`<mva>`）的「先分别生成多角度全尺寸图 → 再 Pillow 拼接多视图」成熟底座，且已在本地 RTX 5080 上验证稳定。
2. **摄像机调度过于保守，缺少剧情驱动的推拉摇移转**
   - `backend/shine/agents/scene_director.py:287` 明确规定 `camera.move is static by default`，且 `acting_director.py` 仅原样抄写固定运镜文本，导致绝大多数镜头呈现三脚架静态观感，缺乏随剧情张力起伏的推（`push_in`）、拉（`pull_back`）、摇（`pan / tilt`）、移（`track`）、转（`orbit`）及前景视差与机位高度变化。

本方案将这两项能力整合为两个清晰可独立验收的阶段：
* **Phase 1（场景四视图闭环 + 单角原图自动路由 + `shine-h3-compiler/2.7.0`）**：复刻人物五视图成熟架构，为场景生成 4 张全尺寸单角原图并合成 16:9 四合一展示母版；出片时按镜头朝向 / 摇镜起落幅**自动送 1–2 张全尺寸单角原图给 H3 Ref2VA**，零分屏风险、零分辨率损失。
* **Phase 2（剧情张力驱动运镜语法 + 表演时间窗运镜联动）**：解除 `static by default` 束缚，将 `beat_energy` 映射到推拉摇移转运镜语法、机位高度、前景视差与 `scene_angle / scene_angle_end`，并在 `acting_director.py` 中实现运镜起幅/推进/定格与表演时间窗的精准同步。

---

## 二、`AGENTS.md` 合规审计与并发开发保护

### 2.1「一套事实、一个裁决、一个执行者」审计表

| 架构维度 | 唯一落点 | 合规说明（严守三条禁令） |
| :--- | :--- | :--- |
| **一套事实（场景多视角）** | 同一条场景主资产 `Asset`（`provenance["scene_sheet"]["views"]` 存证 4 张单角原图路径与 SHA-256）+ 既有 `shot.bindings` | **绝不将 1 个场景拆成 4 个孤立资产**，也不改 `shot.bindings` 数据库表结构。UI 与素材中心仍只预览/晋升/锁定 1 个场景主资产（显示 4 合 1 四视图）；4 张全尺寸单角原图随主资产原子存证、同步晋升与失效。 |
| **一个裁决（LoRA 与运镜校验）** | `evaluate_style_lora_selection()`（多视角 LoRA 唯一裁决）+ `continuity_engine.py`（分镜与运镜唯一裁决） | 场景四视图直接复用 `qwen-image-2.1-lora-multiple-angles` 同一台账与裁决函数；运镜节奏告警（如连续多镜全静或连续大幅运动）仅作为轻量 `warning` 挂入 `continuity_engine.py`，不叠新门禁。 |
| **一个执行者（视角展开与编译）** | `backend/shine/services.py`（`build_reference_manifest` 唯一清单元组装口）+ `H3PromptCompiler`（升级至 `shine-h3-compiler/2.7.0`） | 仅在组装 H3 `reference_manifest` 时由纯确定性函数 `resolve_scene_angle_views()` 将 1 个场景主资产展开为 1–2 张全尺寸单角原图；`2.6.0` 及以前版本冻结在 `LEGACY_260_VERSIONS`，保证历史请求逐字节可回放。 |

### 2.2 并发进程保护与安全红线

1. **保留本机 `main` 既有最新进程**：开工前运行 `git status --short` 与 `git log -n 5 --oneline`，严禁 `git reset --hard`、`git checkout -- .` 或触碰非重叠模块（如 `copilot/`）。
2. **LLM 密钥零泄漏**：读取 `C:\Users\Administrator\Desktop\AIstudio\deepseek-test.txt` 仅用于向隔离验收栈配置测试 LLM，**严禁将 API Key、明文凭据或该文件内容写入 Git、SQLite、验收报告、日志文件或前端响应**。
3. **日常工作台启动配置交还（2026-11-27 裁定）**：开工留存日常 `.env` 字节与 SHA-256 快照、隔离 `-DataDir` 与非日常端口验收、收尾 `stop_shine.py --data-dir` 停机并恢复 `.env`，最后通过 `启动 Shine 工作台.cmd` 复验原日常作品可见。

---

## 三、Phase 1 详细设计：场景四视图生成 + 单角原图自动路由 + `H3PromptCompiler 2.7.0`

### 3.1 新增 `backend/shine/asset_center/scene_sheet.py`（复刻 `character_sheet.py` 底座）

直接复用 `qwen-image-2.1-lora-multiple-angles`（`MULTIPLE_ANGLES_LORA_ID` / `MULTIPLE_ANGLES_LORA_SHA256`、`strength=0.8, steps=36, cfg=1.0, euler/simple`），构建场景四视图管线 `t2i_to_scene_sheet_v1`：

| 步骤 | `view_key` | 视角定位 | 使用 LoRA | 参考源 | 建议全尺寸分辨率 | 空间职责 |
| :---: | :--- | :--- | :---: | :--- | :---: | :--- |
| Step 0 | `front_wide` | 主全景（正打基准） | 否（T2I `material-image-v6-qwen21`） | 无 | `1344 × 768`（16:9） | 确立核心地标、时代建筑、地形/光影色温与主轴线基准 |
| Step 1 | `left_quarter` | 左 45° 广角 | 是（`<mva>`） | `front_wide` | `1344 × 768`（16:9） | 锁定左侧空间延伸、侧翼地形/墙面与纵深关系 |
| Step 2 | `right_quarter` | 右 45° 广角 | 是（`<mva>`） | `front_wide` | `1344 × 768`（16:9） | 锁定右侧空间延伸、侧翼建筑/战阵与纵深关系 |
| Step 3 | `reverse_wide` | 180° 反打 / 高位全景 | 是（`<mva>`） | `front_wide` | `1344 × 768`（16:9） | 锁定反打方向背景（或宏大战场的高位鸟瞰/对阵反面），避免正反打背景飘移 |
| Step 4 | `composite_sheet` | 16:9 四合一展示母版 | Pillow 确定性拼接 | 上述 4 图 | `2752 × 1600`（32 倍数，含细边距） | 作为素材中心主资产缩略与人工审核预览图，无文字水印 |

* **强护栏**：
  * 所有 4 张单角图继续严格保留 `no people, no human figure, no text, no collage, no split panel`（空景无人物，避免干扰 H3 `<Subject>` 角色绑定；宏大战争场景中可通过场景环境描述保留战旗、烟尘、营寨、寺庙建筑、地势与远景氛围，具体大将与士兵冲锋由镜头与人物参考驱动）。
  * 每一步均在单次 GPU 租约内串行执行（参照 `materialprep/execution.py` 的 `_render_character_sheet_inside_lease`），失败显式报错，**绝不静默降级为无 LoRA 生图**。
  * 完成后将 4 张全尺寸单角原图落盘至同一资产存储目录，并在主资产 `provenance` 中写入结构化存证：
    ```json
    {
      "pipeline": "t2i_to_scene_sheet_v1",
      "scene_sheet": {
        "layout_version": "scene-sheet-16x9-quad-v1",
        "artifact_role": "composite_sheet",
        "views": {
          "front": {"key": "front_wide", "storage_path": "...", "sha256": "...", "width": 1344, "height": 768},
          "left": {"key": "left_quarter", "storage_path": "...", "sha256": "...", "width": 1344, "height": 768},
          "right": {"key": "right_quarter", "storage_path": "...", "sha256": "...", "width": 1344, "height": 768},
          "reverse": {"key": "reverse_wide", "storage_path": "...", "sha256": "...", "width": 1344, "height": 768}
        }
      }
    }
    ```
  * **前端与素材准备开关**：在素材准备页与项目素材偏好中提供与人物五视图对称的 `scene_sheet_mode` 开关（复用同一 `qwen-image-2.1-lora-multiple-angles` 就绪状态检查，无需单独配置 LoRA）。

### 3.2 唯一视角路由点：`resolve_scene_angle_views()`（`backend/shine/services.py`）

在 `backend/shine/services.py` 的 `build_reference_manifest()` 中，对 `use_as == "picture"` 且 `asset.role == "scene"` 的绑定执行确定性视角展开：

1. **输入信号（均可选，向后兼容）**：
   - `camera.scene_angle`：`Literal["front", "left", "right", "reverse"]`（未填时由服务器根据 `transition_motivation == "shot_reverse_shot"` 或人物朝向自动推默认 `front`）；
   - `camera.scene_angle_end`：仅当 `camera.move in {"pan", "track", "orbit", "whip_pan", "crane"}` 且镜头跨越不同空间方位时有效（且 `scene_angle_end != scene_angle`）。
2. **展开规则（严守 `MAX_IMAGES = 9` 与 `MAX_TOTAL = 12`）**：
   - 若该场景资产无 `provenance["scene_sheet"]["views"]`（历史单图场景）→ 原样透传 1 个 `ReferenceManifestItem`，**100% 兼容旧项目**；
   - 若存在四视图存证，且仅需单角（定镜 / 推拉 / 正反打 / 未指定 `scene_angle_end`）→ 将该场景绑定替换为对应 `views[scene_angle]` 的 **1 张全尺寸单角原图**（`storage_key` 与 `sha256` 指向该单角文件，`role = f"scene_angle:{scene_angle}"`）；
   - 若为跨方位摇移/环绕（存在有效 `scene_angle_end != scene_angle`）且当前请求图片数留有余量（展开后总图片数 `<= MAX_IMAGES` 且总参考数 `<= MAX_TOTAL`）→ 展开为 **2 张全尺寸单角原图**：
     * 第 1 张：`role = f"scene_angle_opening:{scene_angle}"`（起幅空间参考）；
     * 第 2 张：`role = f"scene_angle_ending:{scene_angle_end}"`（落幅空间参考）；
   - 若当前镜头已绑多角色+多道具导致槽位不足以塞 2 张场景图 → 自动安全降级为只送起幅 `1` 张单角原图，**绝不触发 `H3_REFERENCE_LIMIT` 阻滞**。
3. **文件真实性校验**：展开前核验单角原图文件存在且可读；若异常缺失则回退至主资产 `storage_path` 并记录告警，不崩溃。

### 3.3 `H3PromptCompiler` 升级至 `shine-h3-compiler/2.7.0`

修改 `backend/shine/h3/compiler.py`：
* 冻结 `LEGACY_CONTINUITY_CLEAN_V26_COMPILER_VERSION = "shine-h3-compiler/2.6.0"` 与 `LEGACY_260_VERSIONS`，确保 `<= 2.6.0` 所有历史请求逐字节可回放。
* `COMPILER_VERSION = "shine-h3-compiler/2.7.0"`，新增 `scene_angle_v27: bool = False`。
* 在 `_subject_definitions()` 与 `_retention()` 中识别 `scene_angle_opening:*` 与 `scene_angle_ending:*`：
  - 单角场景图（`scene_angle:*`）：保持既有单场景清晰锚定（`last_frame` 下仍遵循 2.4.0+ 仅参考建筑几何与材质、不覆写尾帧光影的规则）；
  - 双角摇移场景图：
    * `scene_angle_opening:*` → `"{label} is the opening camera-angle architectural and terrain reference for [Shot 1]; establish the opening wall/horizon geometry and materials from it."`
    * `scene_angle_ending:*` → `"{label} is the destination camera-angle architectural and terrain reference for [Shot 1]; as the camera moves, transition smoothly into this direction's geometry and landmarks without warping or repeating structures."`

---

## 四、Phase 2 详细设计：剧情张力驱动的推拉摇移转运镜调度

### 4.1 扩展 `DirectedCamera` 与解除 `static by default`（`backend/shine/agents/scene_director.py`）

1. **数据结构扩展（向后兼容，空值不破坏旧 JSON）**：
   - 在 `DirectedCamera` 增加可选字段：
     * `scene_angle: Literal["front", "left", "right", "reverse"] | None = None`
     * `scene_angle_end: Literal["front", "left", "right", "reverse"] | None = None`
     * `camera_height: Literal["eye_level", "low_angle", "high_angle", "overhead", "ground_level"] | None = None`
     * `foreground_parallax: str = Field(default="", max_length=160)`（例如 `passing wooden pillar / billowing war banner edge / foreground spear tips`）
   - 在 `landing_rows()` 中将这些字段透传写入 `shot.camera`（并在 `compiler.py` 的 `_camera_narrative()` 中按自然英文编织，避免把内部枚举键名如 `scene_angle: left` 裸露进提示词文本）。

2. **剧情张力 → 推拉摇移转运镜语法（替换原 `static by default`）**：
   在 `scene_director_prompt()` 中建立明确的电影运镜语法表：

| `beat_energy` / 戏剧场景 | 推荐 `camera.move` 与机位高度 | 空间视角路由（`scene_angle → scene_angle_end`） | 电影感要点 |
| :--- | :--- | :--- | :--- |
| **`calm`（建置/列阵/入场）** | `crane` / `pan` / `track` / 缓推 `push_in`；`high_angle` 或 `eye_level` | `front → left` 或 `left → right`（大场景全景横摇） | 交代宏大空间尺度、兵阵/地形纵深与主地标 |
| **`probe`（试探/对峙/侦察）** | 轻微 `handheld` / 缓 `track` / OTS `eye_level` | `front` 与 `reverse` 交替正反打 | 保持 180° 轴线，借助前景遮挡增强临场压迫 |
| **`pressure`（逼问/大军压境）** | 持续缓推 `push_in` / 侧翼 `track`；`low_angle` 抬升气势 | `front` 或 `left / right` | 逐步收紧景别，压缩心理空间 |
| **`turn_pause`（1 秒空拍转折）** | 空拍期缓推 `push_in` 并在开口瞬间定格，或微仰 `tilt` | 锁定特写/近景对应方位（`front` / `reverse`） | 运镜落点精准卡在微表情蓄力完成与开口那一帧 |
| **`erupt`（冲锋/厮杀/爆发）** | 动态 `track`（跟拍冲锋）/ 强 `handheld` / 动机 `whip_pan` / `orbit`（3/4 环绕大将）；`low_angle` / `ground_level` | 跨方位运镜填 `scene_angle → scene_angle_end`（双单角图锁定空间） | 配合前景兵刃/尘烟/战旗视差，拒绝纸片平移 |
| **`aftermath`（战后/余韵/抽离）** | 缓拉 `pull_back` / 缓升 `crane`；`eye_level → high_angle` | `front` 或 `reverse` | 把人物重新放回宏大战场/空旷环境制造苍凉感 |

### 4.2 表演时间窗与运镜节奏同步（`backend/shine/agents/acting_director.py`）

* 在 `ActingWindowPatch` 增加可选字段 `camera_beat: str = Field(default="", max_length=200)`（默认空字符串，缺省时沿用现有 `camera` 文本，零阻滞风险）。
* 当镜头拥有动态 `camera.move`（非 `static`）时，引导 Acting Pass 在 2–4 镜表演组内把运镜起幅、加速/推进、定格/余韵与 `pre_speech / delivery / aftermath` 窗口对齐（例如 `pre_speech` 窗口缓慢推近至紧特写 → `delivery` 窗口定格锁住眼神与口型 → `aftermath` 窗口轻微呼吸稳住或缓拉）。
* `apply_shot_patch()` 优先采纳合法英文且不含离场角色的 `camera_beat`，否则安全回退到原 `existing_timeline[idx]["camera"]`。

### 4.3 `continuity_engine.py` 运镜护栏与轻量节奏提示

* 保留既有硬护栏：有 `internal_cuts` 的镜头仍禁止 `crane / orbit / zoom / whip_pan`（`BIG_MOVES_IN_CUT_SEGMENT`）；动态运镜仍需填写 `camera.motive` 且 `camera.movement` 包含对应动词。
* 新增两条**非阻塞 `warning`**（不新增硬拒门禁）：
  1. `STATIC_CAMERA_MONOTONY`：整场 ≥ 5 镜且连续 ≥ 4 镜均为 `static` 时发出提示；
  2. `EXCESSIVE_CAMERA_MOTION`：连续 ≥ 3 镜均为高幅度运动（`orbit / whip_pan / crane`）时发出提示，提醒保持“动—静—动”节奏对比。
* 在 `crew_salvage.py` 中补充 `scene_angle / scene_angle_end / camera_height` 的容错归一化（如 `"front_wide" → "front"`、`"back" → "reverse"`、`"low" → "low_angle"`），严防 LLM 枚举拼写漂移导致 `SCHEMA_VALIDATION_FAILED`。

---

## 五、受影响模块清单与净代码量预估

| 阶段 | 文件路径 | 变更类型 | 预估净增/改行数 | 说明 |
| :--- | :--- | :--- | :---: | :--- |
| **Phase 1** | `backend/shine/asset_center/scene_sheet.py` | **新增** | ~360 | 复用 `<mva>` LoRA 的场景四视图生成步骤、提示词与 16:9 Pillow 四合一拼接器 |
| **Phase 1** | `backend/shine/materialprep/execution.py` & `service.py` & `runtime.py` | 修改 | ~240 | 接入 `_render_scene_sheet_inside_lease`、`scene_sheet_mode` 开关与进度投影 |
| **Phase 1** | `backend/shine/services.py` | 修改 | ~110 | 在 `build_reference_manifest()` 实现 `resolve_scene_angle_views()`（1–2 张全尺寸单角原图展开与槽位保护） |
| **Phase 1** | `backend/shine/h3/compiler.py` | 修改 | ~75 | 升级 `shine-h3-compiler/2.7.0`，冻结 `2.6.0`，新增起幅/落幅双角场景语义锚定 |
| **Phase 1** | `frontend/src/components/`（素材准备开关与预览） | 修改 | ~85 | 对称人物五视图增加“场景四视图”开关与就绪提示 |
| **Phase 1** | `backend/tests/test_scene_sheet_and_angle_routing.py` | **新增** | ~280 | 四视图拼接、单/双视角路由、槽位不足降级、`2.6.0` 字节回放与 `2.7.0` 语义单测 |
| **Phase 2** | `backend/shine/agents/scene_director.py` | 修改 | ~120 | `DirectedCamera` 新增 `scene_angle / scene_angle_end / camera_height / foreground_parallax` 与剧情运镜语法 |
| **Phase 2** | `backend/shine/agents/acting_director.py` | 修改 | ~70 | `ActingWindowPatch.camera_beat` 与表演窗运镜联动（非阻塞安全回退） |
| **Phase 2** | `backend/shine/agents/crew_salvage.py` & `continuity_engine.py` | 修改 | ~95 | 视角/机位高度枚举归一化 + 静态单调/过度运镜轻量 `warning` |
| **Phase 2** | `backend/tests/test_cinematic_camera_grammar.py` | **新增** | ~240 | 运镜语法校验、视角归一化、`camera_beat` 同步与跨墙双角展开集成测试 |

* **业务代码净增量预估**：Phase 1 约 **870 行**，Phase 2 约 **285 行**，合计约 **1,155 行**。
* **测试代码净增量预估**：约 **520 行**，总计约 **1,675 行**。

---

## 六、分阶段验证与《香积寺大战》真机验收方案

### 6.1 离线全量回归（Phase 1 & Phase 2 完成后）
1. 后端新增单测全绿：`test_scene_sheet_and_angle_routing.py`、`test_cinematic_camera_grammar.py`、`test_h3_compiler.py`、`test_h3_compiler_v24_acting.py`、`test_acting_director_pass.py`、`test_film_crew.py`、`test_offline_e2e.py`。
2. 前端全量：`npx tsc --noEmit`、`npx vitest run --no-file-parallelism`、`npm run build` 全绿。
3. 后端 8 分片全量回归：按 `AGENTS.md` 固定规则运行 `.\run_backend_shards.ps1 -WorkRoot <固定非系统盘目录> -Shards 8`，确认 8/8 全绿并自动清理临时目录。

### 6.2 隔离真机全链路 1 集 60 秒《香积寺大战》验收（GPU 实测）
* **隔离环境**：使用 `start_shine.ps1 -DataDir <隔离测试目录>` 与非日常端口启动；复用本机规范 ComfyUI 渲染器与模型权重（不复制权重）。
* **LLM 配置**：读取本机 `C:\Users\Administrator\Desktop\AIstudio\deepseek-test.txt` 里的 DeepSeek LLM 配置（按受保护密钥机制注入隔离实例，**严禁在任何日志、报告或仓库文件中暴露明文 Key**）。
* **测试剧情与规模**：《香积寺大战》（重点展现唐军/叛军双方大将带领大军在香积寺古刹外旷野列阵、冲锋、白刃交锋与阵前对峙的宏大战争场景），**共 1 集，目标片长 60 秒（±10s 弹性窗，单镜 ≤ 10s 红线）**。
* **素材准备验收**：
  - 开启**场景四视图（`scene_sheet_mode=true`）**，用本地 Qwen-Image 2.1 + `<mva>` 实跑香积寺战场主场景的 4 张全尺寸视角图（`front / left / right / reverse`）并合成 4 合 1 展示母版，检查四视角建筑/地形/天色一致性与无分屏/无人物合规性；
  - 同步生成并晋升双方大将人物主素材（及必要关键兵器/战旗道具）。
* **真机 H3 渲染参数（严格按用户指定）**：
  - **分辨率**：**1.0 分辨率（768P 标准横屏 `1344 × 768 @ 24fps`）**
  - **二采（Second Pass）**：**开启**（`second_pass.enabled = true`）
  - **2X 超清（Upscale）**：**关闭**（`upscale.enabled = false`）
  - **加速步数**：**8 步 PDD**（`pdd8`），Ref2VA only
* **核心验收指标**：
  1. **运镜多样性**：全集镜头包含至少 3 种不同剧情动机运镜（如宏观建置 `pan/crane`、大将冲锋 `track/handheld`、阵前对峙 `push_in`、战局余韵 `pull_back`），无 `static` 贯穿全片；
  2. **单/双角原图路由实证**：验收日志记录每镜实际绑定给 H3 的单角视角文件名与 `role`（验证正反打自动切换 `front / reverse`，跨方位摇移镜头自动绑定 2 张全尺寸单角原图且未超 `MAX_IMAGES=9`）；
  3. **成片物理与视觉质量**：全部镜头 `PASSED`（无 OOM、无 2×2 分屏泄漏、无场景剧烈漂移），`ffmpeg -v error -f null -` 解码通过，输出抽帧对比图与 60s 成片留存至本机隔离证据目录；
  4. **日常工作台交还**：停机隔离栈、清理测试临时文件、核对仓库根 `.env` SHA-256 还原，并通过 `启动 Shine 工作台.cmd` 复验原日常作品完整可见。

---

## 七、可直接发给本机 Agent（Antigravity）的执行提示词

```text
请直接在当前本地 `main` 分支上，严格按照仓库根目录 `AGENTS.md`（核心原则「一套事实、一个裁决、一个执行者」、8 分片并行回归、以及 2026-11-27 日常工作台启动配置交还裁定）完整实施 `docs/plan_20261128_cinematic_camera_and_scene_sheet_v1.md`（Phase 1 + Phase 2），并完成离线回归与《香积寺大战》隔离真机验收。

【开工前保护与安全红线】
1. 开工前先运行 `git status --short` 和 `git log -n 5 --oneline` 盘点当前 `main` 最新进程，严禁 `git reset --hard`、`git checkout -- .` 或修改/回退非重叠模块（如 `copilot/` 等既有开发成果）。
2. 严格执行日常工作台启动配置留存：开工前快照仓库根 `.env` 原始字节与 SHA-256，记录日常入口数据目录与作品数；验收全程使用隔离 `-DataDir` 与非日常端口，结束前必须停机隔离栈、恢复 `.env` 并用 `启动 Shine 工作台.cmd` 复验日常作品。
3. 真机验收所需的 LLM 配置请从 `C:\Users\Administrator\Desktop\AIstudio\deepseek-test.txt` 读取并仅注入隔离测试实例的受保护密钥配置中；**严禁在代码、Git、SQLite、日志、终端输出或验收报告中打印或留存任何明文 API Key**。

【Phase 1：场景四视图闭环 + 单角原图自动路由 + H3PromptCompiler 2.7.0】
1. 参照 `backend/shine/asset_center/character_sheet.py` 新增 `backend/shine/asset_center/scene_sheet.py`：
   - 复用已纳管的 `qwen-image-2.1-lora-multiple-angles`（`<mva>`）及 `evaluate_style_lora_selection()` 唯一裁决口；
   - 实现 `t2i_to_scene_sheet_v1`：先分别生成 4 张全尺寸 16:9（`1344×768`）空景无人物单角原图（`front_wide` / `left_quarter` / `right_quarter` / `reverse_wide`），再由 Pillow 拼成 16:9 四合一展示母版作为主资产图，并将 4 张单角原图的 `storage_path` 与 `sha256` 原子存入主资产 `provenance["scene_sheet"]["views"]`；
   - 在 `materialprep/execution.py`、`service.py`、`runtime.py` 与前端素材准备面板对称人物五视图接入 `scene_sheet_mode` 开关，绝不分裂 `shot.bindings` 数据库表结构。
2. 在 `backend/shine/services.py` 的 `build_reference_manifest()` 落地确定性路由函数 `resolve_scene_angle_views()`：
   - 定镜/推拉/正反打自动展开为对应 `scene_angle` 的 1 张全尺寸单角原图；
   - 跨方位摇移/环绕（`scene_angle_end != scene_angle`）且图片槽位 `<= MAX_IMAGES (9)` 时自动展开为起幅 + 落幅 2 张全尺寸单角原图，槽位紧张时安全降级为 1 张；历史普通单图场景 100% 原样透传。
3. 将 `backend/shine/h3/compiler.py` 升级至 `shine-h3-compiler/2.7.0`（冻结 `2.6.0` 至 `LEGACY_260_VERSIONS` 保持历史字节零漂移），在 `_subject_definitions()` 与 `_retention()` 中支持 `scene_angle_opening:*` 与 `scene_angle_ending:*` 的起落幅空间几何锚定。

【Phase 2：剧情张力驱动运镜调度 + 表演窗运镜联动】
1. `backend/shine/agents/scene_director.py`：为 `DirectedCamera` 新增可选字段 `scene_angle`、`scene_angle_end`、`camera_height`、`foreground_parallax`；解除 `static by default`，将 `beat_energy`（`calm / probe / pressure / turn_pause / erupt / aftermath`）映射到推拉摇移转运镜语法、机位高度与前景视差，并将自然英文运镜透传至 `compiler.py`（禁止把内部枚举键名裸露进 H3 提示词）。
2. `backend/shine/agents/acting_director.py`：在 `ActingWindowPatch` 增加可选 `camera_beat`，使动态镜头在 2–4 镜表演组内把运镜推进/定格/缓拉精准对齐到 `pre_speech / delivery / aftermath` 窗口（缺省时安全回退，绝不阻塞）。
3. `backend/shine/agents/crew_salvage.py` & `continuity_engine.py`：增加视角/机位高度枚举同义词归一化；在唯一裁决口补充 `STATIC_CAMERA_MONOTONY` 与 `EXCESSIVE_CAMERA_MOTION` 两条轻量 `warning`。

【验证与《香积寺大战》真机 60 秒全链路验收】
1. 新增 `backend/tests/test_scene_sheet_and_angle_routing.py` 与 `backend/tests/test_cinematic_camera_grammar.py`，跑通前端 `tsc` + `vitest` + `build` 及后端 8 分片全量回归（`.\run_backend_shards.ps1`）。
2. 在隔离 `-DataDir` 启动本地真机栈，使用 `deepseek-test.txt` 里的 LLM 配置，以《香积寺大战》（双方大将带领大军在香积寺外旷野列阵、冲锋厮杀的宏大战争场景）为剧本，设定 **共 1 集、片长 60 秒**（±10s 弹性窗，单镜 ≤ 10s 红线）：
   - 开启场景四视图，真实生成香积寺战场四视角全尺寸图 + 四合一母版并晋升锁定，同时生成并锁定双方大将及关键道具；
   - 真实 H3 渲染参数严格设置为：**1.0 分辨率（`1344×768` 768P 标准横屏）、开启二采（`second_pass.enabled=true`）、关闭 2X 超清（`upscale.enabled=false`）、8 步 PDD（`pdd8`）**；
   - 核验并记录：各镜运镜类型分布、每镜路由的单/双角原图清单、无 OOM 与无分屏泄漏、全集解码通过、抽帧图与 60s 成片留存至隔离证据目录，输出验收报告 `docs/acceptance/ACCEPTANCE_20261128_CINEMATIC_CAMERA_AND_SCENE_SHEET_V1.md`。
3. 完成后执行隔离栈停机、临时目录清理、`.env` 恢复与 `启动 Shine 工作台.cmd` 日常入口复验，并如实汇报全部命令结果。
```
