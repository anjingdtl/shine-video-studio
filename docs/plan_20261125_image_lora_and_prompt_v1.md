# Shine Video Studio — 统一素材生图可配置 LoRA 与剧本对齐提示词改造方案（V1）

- **文档版本**：v1.0
- **制定日期**：2026-11-25
- **适用基线**：本地 `main`（承接视频侧风格 LoRA V1 `f180f0010009` 与 `docs/ACCEPTANCE_20261125_STYLE_LORA_ASSEMBLY_V1.md`）
- **最高工程纪律**：**一套事实、一个裁决、一个执行者**

## 一、建设目标与范围边界

本次改造将视频侧已验证落地的可配置风格 LoRA 能力，沿着**统一素材生成基座**扩展至全部图像生成链路，并同步解决素材准备阶段「提示词与剧本/人物设定偏离」的根因问题。

1. **双入口统一接入**：同时覆盖**素材中心**（`AssetsWorkspace` + `/api/v1/asset-center/image-jobs`）与**项目内素材准备线**（`MaterialPreparationPanel` + `/api/v1/projects/{id}/material-preparation/*`），共享同一套 LoRA 台账、裁决器、图谱编译器与提示词注入器。
2. **全模式覆盖**：覆盖 Qwen-Image-2.1 全部模型生图模式——`T2I`、`I2I`、`INPAINT`、`MULTIREF`、`WARDROBE` 与模型 `RGBA`；纯本地像素操作（`RGBA` + `MASK_CUTOUT_V1`）明确拦截 LoRA，不做静默忽略。
3. **零手写自动触发词 + 社区固定默认采样参数**：只要挂载 LoRA 或选定内置风格配方，系统自动注入对应的正向触发词（含前置标签如 `<mva>`）与负向过滤词，并按社区推荐值自动应用固定默认采样参数（`steps` / `cfg` / `sampler_name`）；零 LoRA 时保持现行 `25 / 1.0 / euler` 基线不变。
4. **基础设置可选勾选下载**：在「设置 → 制作环境」（`SetupCenter`）中以与 **RelayVSR 2X 精修**完全一致的勾选下载机制，提供首期 3 套风格对应的 4 枚已测通原生 Qwen-Image-2.1 LoRA 权重包（合计约 740 MiB），下载完成后自动发现并纳管进统一台账。
5. **剧本对齐提示词升级（`material-image-v6-qwen21`）**：修复 `brief.py` 缺失剧本人物/场景档案、`image_prompts.py` 注意力层序倒置、棚拍套话与 LoRA 光影冲突及 `brief_basis` 缓存未绑定 LoRA/人物哈希的 4 个根因。

## 二、三原则架构收口总览

| 原则 | 本次改造的唯一承载点 | 禁止事项（反模式红线） |
|---|---|---|
| **一套事实** | SQLite `style_lora_assets` 单表（新增 `base_model` / `negative_trigger_word` / `trigger_position`）+ 只读 `config/image_lora_presets.json` + 独立部署清单 `config/deployment/qwen-image-loras-windows-x64.json` + 实时 `project_style_lora_asset()` 投影 | 禁止为生图另建第二张 LoRA 表；禁止在 SQLite 缓存 ComfyUI 可见性；禁止前端维护静态 LoRA 列表副本 |
| **一个裁决** | `evaluate_style_lora_selection()`（统一校验 `base_model` 匹配、0～3 槽位、去重、权重、SHA-256、实时可见性）+ `_ensure_lora_not_in_use()`（统一覆盖 7 阶、4 阶、素材中心、素材准备线四条在途链路） | 禁止在 `api_asset_center.py` 或 `api_material.py` 另写平行 if/elif 校验；禁止跨基座混挂 H3 与 Qwen LoRA |
| **一个执行者** | `attach_qwen_style_loras()`（T2I 与编辑图共用唯一挂载口）+ `apply_image_lora_prompts()`（预览与 Worker 提交共用唯一自动提示词注入口）+ `setup_preparation.py`（唯一可选下载执行器）+ `StyleLoraAssemblyPanel.tsx`（全站唯一前端装配组件） | 禁止 T2I 与编辑图各自手拼 `LoraLoaderModelOnly` 节点；禁止预览与执行分叉；禁止在素材中心内另起下载器 |

## 三、前后衔接与全域影响面分析

实施前必须先锁定本次改造对既有系统的影响边界与保护策略，避免引入隐性回归：

| 影响域 | 关联模块与机制 | 影响评估 | 保护与衔接策略 |
|---|---|---|---|
| **1. H3 视频侧可配置 LoRA（零回归）** | `graph_compiler.py`、`api_segments.py`、`manual4/*`、`DirectorShootPanel.tsx`、`ScheduleProduceStagePage.tsx` | 存量 `style_lora_assets` 无 `base_model` 列；`api_loras.py` 现有调用不传 `base_model` | 迁移 `f180f0010010` 为 `base_model` 设默认值 `"minimax_h3"`；`GET/POST /api/v1/loras` 缺省仍兼容 `"minimax_h3"`；H3 侧仍执行 `variant_id == INT8` 与 ASCII 触发词规则（禁 `<`/`>` 与时间轴） |
| **2. 生图 `RENDER_VERIFIED` 指纹与重验** | `materialprep/image_prompts.py`（`PROMPT_TEMPLATE_VERSION`）、`materialprep/runtime.py`、`asset_center/evidence.py` | `runtime.py:307` 将 `PROMPT_TEMPLATE_VERSION` 纳入 `runtime_fingerprint()`；从 `v5` 升至 `v6` 会使老机器既有 `RENDER_VERIFIED` 凭证失效 | **刻意保留该失效语义**（模板升级本就应重验）；零 LoRA 时 `BASE_GRAPH_SHA256` 与 `qwen_edit_adapter_hash` 保持字节不变；升级说明中明确引导老环境运行一次 `POST /api/v1/setup/image-runtime/validate` 刷新凭证 |
| **3. VLM 审片校准标识（`client_review_identity`）** | `backend/shine/worker.py`、`backend/shine/comfy/client.py` | 修改 `worker.py` 或 `comfy/client.py` 会令本机 VLM 校准版本自动失效 | 本次生图执行全部在 `asset_center/execution.py` 与 `materialprep/execution.py` 内完成，**不修改 `worker.py` 与 `comfy/client.py`**，保护 VLM 校准不受波及 |
| **4. 托管 ComfyUI 渲染器启动与探测** | `comfyops/managed_recipe.py`、`comfyops/image_renderer.py`、`comfyops/loras.py` | `image_renderer.py` 已复用 `build_renderer_launch()`，启动时已自动注入 `--extra-model-paths-config shine-style-loras.yaml`；但 `probe_live_lora_object_info()` 目前只探测 H3 地址 | 将 `probe_live_lora_object_info()` 改为按 `base_model` 路由：`minimax_h3` 探测 H3 运行时 URL，`qwen_image_21` 探测 `materialprep/runtime.py` 的 `config.base_url`；受管下载落盘的 `isolated-packages/<component_id>` 统一写入 `shine-style-loras.yaml` |
| **5. 新机零 LoRA 生产门禁** | `comfyops/deployment.py`、`comfyops/setup_summary.py`、`test_new_machine_setup.py`、`test_mode_capabilities.py` | 若将 4 枚生图 LoRA 混入默认主清单，会破坏“零 LoRA 基线可直接出图”的门禁 | 独立建清单 `qwen-image-loras-windows-x64.json`，全部组件归入 `OPTIONAL_INSTALL_COMPONENTS` 与 `optional_only_plan()`，未勾选时零下载、零阻塞 |
| **6. 客服/帮助台契约与审计事件码** | `copilot_contracts.json`、`knowledge_manifest.json`、`frontend/src/auditEventCodes.json` | 新增能力键/接口字段或审计事件码若不同步，会导致 `test_support_copilot_v21.py` 或 `test_audit_event_codes.py` 失败 | 改造收尾必须重新执行 `generate_shine_copilot_contracts.py` 与 `generate_copilot_knowledge.py`，并同步更新 `auditEventCodes.json` |
| **7. 既有单测基线** | `test_material_style_injection.py`、`test_material_no_overlay_rule.py`、`test_asset_center.py`、`test_style_lora_assembly.py`、`test_alembic.py`、`setupCenter.test.tsx` | 提示词升级至 `v6` 后，依赖精确层序断言的旧提示词单测需按新规范更新 | 保留所有反水印/反拼图/介质互斥/中文拒收契约，更新提示词层序断言并补充 `v6` 实体档案对齐与 4 枚 LoRA 全流程测试 |

## 四、基础设置可选下载设计（对齐 RelayVSR 2X 机制）

### 4.1 已测通的 4 枚原生 Qwen-Image-2.1 LoRA 钉点清单

以下 4 枚文件均已通过 HuggingFace API 与 `HEAD` 请求实测打通，全部 `302` 跳转至 `us.aws.cdn.hf.co`（与现有 `qwen-image-2.1-windows-x64.json` 重定向白名单一致），合计 **776,384,068 B（约 740.4 MiB）**：

| 组件 ID (`component_id`) | 所属风格 / 用途 | 来源仓库与钉定文件 | 精确大小 (B) | SHA-256 | 协议与官方触发词 |
|---|---|---|---|---|---|
| `qwen-image-2.1-lora-fix` | 电影级写实摄影 + 赛博朋克共用（画质/纹理修复） | [e-n-v-y/Qwen-Image-2.1-Fix-v2.0](https://huggingface.co/e-n-v-y/Qwen-Image-2.1-Fix-v2.0)<br>[qwen2.1-detail-fix-2.0.safetensors](https://www.google.com/search?q=https://huggingface.co/e-n-v-y/Qwen-Image-2.1-Fix-v2.0/resolve/045f682888e1f32977d1d7358e5022228fbac4f8/qwen2.1-detail-fix-2.0.safetensors)<br>revision: `045f682888e1f32977d1d7358e5022228fbac4f8` | `54,800,124` | `3f00361145703a64c7b9812d7797079820a1f7b0dbb5768b9d3df1f3c7750dfa` | 无专用触发词<br>（备选 v1.0 Comfy 版：[qwen-image-2.1-fix-1.0-comfy.safetensors](https://huggingface.co/e-n-v-y/Qwen-Image-2.1-Fix/resolve/5b2c7be6ced92cc2e09f9309ad00567b3a3bb6ce/qwen-image-2.1-fix-1.0-comfy.safetensors)，`111,612,000` B，SHA-256 `e4a369158b957aee3a8316d94dbe00c98f7d648ef3d1926ccf7283915b6db60c`） |
| `qwen-image-2.1-lora-natural-exposure` | 电影级写实摄影（自然曝光 / 光影还原） | [prithivMLmods/Qwen-Image-2.1-Natural-Exposure-LoRA](https://huggingface.co/prithivMLmods/Qwen-Image-2.1-Natural-Exposure-LoRA)<br>[Qwen-Image-2.1-Natural-Exposure-LoRA-4000.safetensors](https://huggingface.co/prithivMLmods/Qwen-Image-2.1-Natural-Exposure-LoRA/resolve/382d066d079854a86c9513f3c5a4026ada21ddbe/Qwen-Image-2.1-Natural-Exposure-LoRA-4000.safetensors)<br>revision: `382d066d079854a86c9513f3c5a4026ada21ddbe` | `83,943,952` | `a8edea397ce55ae6e1a442f3ba963eb78beeb515d4fe7d217d496542317292b1` | 协议：`qwen-research`<br>触发词：`Transform the image with balanced neutral exposure` |
| `qwen-image-2.1-lora-anything2real` | 暗黑奇幻插画（漫转真 / 写实质感增强） | [WarmBloodAban/Qwen-Image-2.1-LoRAs](https://huggingface.co/WarmBloodAban/Qwen-Image-2.1-LoRAs)<br>[Qwen2.1_Anything2RealCharacters.safetensors](https://huggingface.co/WarmBloodAban/Qwen-Image-2.1-LoRAs/resolve/c4ab5473bfdf585fc19cfd1e280f79d2b0c79947/Qwen2.1_Anything2RealCharacters.safetensors)<br>revision: `c4ab5473bfdf585fc19cfd1e280f79d2b0c79947` | `318,820,200` | `5db4dd2376e6017812bf1189b2fc46e52aaa2c0b7e688faac75e83e1c4dce849` | 协议：`apache-2.0`<br>无强制触发词（推荐 `euler`） |
| `qwen-image-2.1-lora-multiple-angles` | 赛博朋克按需挂载（多视角镜头控制 v2 rank-64） | [akhaliq/Qwen-Image-2.1-Multiple-Angles-LoRA](https://huggingface.co/akhaliq/Qwen-Image-2.1-Multiple-Angles-LoRA)<br>[angles_v2_…_000001500.safetensors](https://huggingface.co/akhaliq/Qwen-Image-2.1-Multiple-Angles-LoRA/resolve/175b29b6eb41307473eebc363fca8fc198216b60/checkpoints_v2/angles_v2_full_qwen21_multiple_angles_v2_qwen21_multiple_angles_v2_000001500.safetensors)<br>revision: `175b29b6eb41307473eebc363fca8fc198216b60` | `318,819,792` | `548c57977c3f1c09c2c9e5f4e83e930cbc6f719c878a576ab8666ce29fef39b6` | 协议：`apache-2.0`<br>触发词：**`<mva>`（必须前置）** |

### 4.2 与 RelayVSR 2X 完全一致的勾选下载链路

1. **独立部署清单**：新增 `config/deployment/qwen-image-loras-windows-x64.json`，结构与 `relayvsr-x2-windows-x64.json` 对齐（`catalog_id: "shine-qwen-image-loras-windows-x64"`、`destination_family: "loras"`、`redirect_hosts: ["huggingface.co", "us.aws.cdn.hf.co"]`）。
2. **后端可选能力注册**：
   - `backend/shine/comfyops/capability.py`：注册 `CAPABILITY_IMAGE_LORAS` 与目录映射 `"qwen-image-loras-windows-x64"`。
   - `backend/shine/comfyops/deployment.py`：新增 `IMAGE_LORA_CATALOG`、`IMAGE_LORA_COMPONENTS`，将 4 个组件全部加入 `OPTIONAL_INSTALL_COMPONENTS`，确保 `optional_only_plan()` 返回 `True`。
   - `backend/shine/api_setup.py`：`capabilities` 枚举扩展为 `Literal["h3", "image", "vlm", "asr", "relayvsr", "image_loras"]`（`max_length=6`）。
   - `setup_summary.py`、`setup_preparation.py`、`setup_reconcile.py`、`resource_discovery.py`：复用 RelayVSR 2X 的发现、下载队列、进度条、SHA-256 核验与状态投影逻辑。
3. **落盘与自动纳管**：
   - 下载完成后，文件落盘在 `<comfyops_dir>/isolated-packages/<component_id>/` 并通过 `ensure_style_lora_extra_paths_config()` 统一暴露给 `shine-style-loras.yaml`。
   - `sync_discovered_loras()` 扫描这 4 个受管包目录及 `shine_user/`，按 SHA-256 幂等注册进 `style_lora_assets`（`base_model="qwen_image_21"`、`origin="discovered"`），自动写入对应的 `display_name`、`default_strength`、`trigger_word`、`negative_trigger_word`、`trigger_position`。
4. **前端基础设置 UI**：
   - `frontend/src/components/SetupCenter.tsx` 与 `SetupEnvironmentStatus.tsx` 中 `CapabilityKey` 新增 `'image_loras'`，在可选能力区与「RelayVSR 2X 精修」并列展示 **「生图风格 LoRA 包（写实 / 奇幻 / 赛博，约 0.74 GiB）」**，勾选后点击同一「确认整备所选能力」按钮即可后台下载与核验。

## 五、统一台账、3 套内置风格配方与唯一裁决

### 5.1 数据库加法迁移（`f180f0010010`）

- 迁移文件：`backend/alembic/versions/f180f0010010_image_style_loras.py`（`down_revision = "f180f0010009"`）。
- `style_lora_assets` 表变更：
  - 新增 `base_model: String(32)`，`nullable=False`，`server_default="minimax_h3"`，建索引；
  - 新增 `negative_trigger_word: String(500)`，`nullable=False`，`server_default=""`；
  - 新增 `trigger_position: String(16)`，`nullable=False`，`server_default="suffix"`（取值 `"prefix" | "suffix"`）；
  - 将 `trigger_word` 字段长度扩展至 `String(500)`（SQLite 下通过 `batch_alter_table` 或兼容列定义实现往返迁移无损）。

### 5.2 3 套内置可选风格配方（`config/image_lora_presets.json`）

将「底层 LoRA 权重」与「上层风格配方」解耦，解决 `Qwen-Image-2.1-Fix` 被写实摄影与赛博朋克共用的冲突；采样参数按社区推荐值固化为配方默认值：

| 配方 ID | 名称 | 默认装配 LoRA 与权重 | 固定默认采样参数 | 自动注入正向核心词 | 自动注入负向过滤词 |
|---|---|---|---|---|---|
| `cinematic_photorealism` | 电影级写实摄影 | `qwen-image-2.1-lora-fix` (`0.75`) + `qwen-image-2.1-lora-natural-exposure` (`0.60`) | `steps=35`, `cfg=3.5`, `sampler=euler`, `scheduler=simple` | `Shot on 35mm film, Kodak Portra 400, anamorphic lens, shallow depth of field, cinematic lighting, chiaroscuro, subtle film grain, natural skin texture, raw photo, atmospheric haze, moody color grading, Teal and Orange` | `plastic skin, smooth, 3d render, CGI, oversaturated, airbrushed, watermark` |
| `dark_fantasy_illustration` | 暗黑奇幻插画 | `qwen-image-2.1-lora-anything2real` (`0.55`)（推荐在编辑模式下叠加 `role="style"` 风格参考图） | `steps=30`, `cfg=3.5`, `sampler=euler`, `scheduler=simple` | `Dark fantasy concept art, oil painting texture, visible impasto brushstrokes, epic scale, gothic architecture, ominous atmosphere, volumetric fog, dramatic rim lighting, intricate ornate armor, muted desaturated palette with glowing embers, masterpiece` | `anime, flat color, bright cheerful colors, modern, clean vector, smooth digital art` |
| `cyberpunk_neo_noir` | 赛博朋克 | `qwen-image-2.1-lora-fix` (`0.80`)；按需追加 `qwen-image-2.1-lora-multiple-angles` (`0.80`) | `steps=30`, `cfg=3.5`, `sampler=euler`, `scheduler=simple` | `Cyberpunk aesthetic, neo-noir, rain-slicked asphalt streets, vibrant neon reflections, cyan and magenta lighting, dense futuristic megacity, holographic advertisements, volumetric steam, wide-angle lens, ray tracing reflections, hyper-detailed, high contrast`（挂载 Multiple-Angles 时自动前置 `<mva>`） | `daylight, sunny, minimalist, low detail, washed out, blurry` |

> **零 LoRA / 未选配方基线**：`steps=25`, `cfg=1.0`, `sampler=euler`, `scheduler=simple`，与现行完全一致。

### 5.3 唯一裁决与删除保护升级

- `evaluate_style_lora_selection(db, settings, selection, *, base_model="minimax_h3", variant_id=INT8_VARIANT_ID, object_info=None)`：
  - 当 `base_model == "minimax_h3"`：保持既有全部 H3 规则不变（含 `variant_id == INT8` 拦截与 ASCII/H3 保留标签校验）。
  - 当 `base_model == "qwen_image_21"`：
    - 校验每枚 LoRA 的 `asset.base_model == "qwen_image_21"`，否则抛出 `409 LORA_BASE_MODEL_MISMATCH`；
    - 触发词校验放行中英文可打印字符与 `<mva>` 标签（仅拦控制字符与 500 字符上限）；
    - 继续校验 0～3 槽位、去重、权重 `[0.0, 1.5]`、SHA-256 磁盘一致性、以及实时 `object_info` 可见性。
- `_ensure_lora_not_in_use()`：在既有 7 阶 `ProductionRun`/`Job` 与 4 阶 `M4ProductionRun` 扫描基础上，新增：
  - `LibraryRenderTask`（`kind == "image"` 且 `status IN ('QUEUED', 'RUNNING', 'RECONCILING')`）；
  - `MaterialItem`（`status NOT IN 终态集合`）及其所属活跃 `MaterialRun`。

## 六、统一生图基座：双图谱挂载与自动提示词注入

### 6.1 ComfyUI 图谱动态挂载（`qwen_workflow.py`）

新增唯一挂载函数 `attach_qwen_style_loras(graph, *, unet_node_id="451", cache_node_id, style_loras)`：

- **节点拓扑**：
  - `0` 个 LoRA：原样返回 `graph`（`UNETLoader("451") → QwenImage21Cache("480"/"469") → KSampler("458")`），保证 `BASE_GRAPH_SHA256` 与 `qwen_edit_adapter_hash` 零变化。
  - `1～3` 个 LoRA：动态插入节点 `"4511"` → `"4512"` → `"4513"`（均为 `LoraLoaderModelOnly`），串联在 `UNETLoader("451")` 输出与 `QwenImage21Cache(cache_node_id).inputs["model"]` 之间。
- **双路径共用**：
  - **T2I 基座**：`materialprep/execution.py:_build_graph()` 调用 `attach_qwen_style_loras(..., cache_node_id="480")`，并应用解析后的固定推荐采样参数（`steps` / `cfg` / `sampler_name`）。
  - **编辑系基座**：`asset_center/contracts.py:build_qwen_edit_graph()` 调用 `attach_qwen_style_loras(..., cache_node_id="469")`，同时**补通 `TextEncodeQwenImage21("474").inputs["negative_prompt"]` 参数**（替换现行硬编码的 `""`），并传入推荐采样参数。
- **纯本地操作拦截**：`asset_center/api_asset_center.py` 在 `manual_cutout`（`RGBA` + `MASK_CUTOUT_V1`）分支拒绝非空 `style_loras` / `style_preset`（返回 `422 IMAGE_LORA_UNSUPPORTED_FOR_LOCAL_OPERATION`）。

### 6.2 唯一自动提示词注入（`prompt_source.py`）

新增 `apply_image_lora_prompts(prompt, negative_prompt, style_loras, *, style_preset=None) -> tuple[str, str]`：

1. **前置触发词段**：收集所有 `trigger_position == "prefix"` 的非空触发词（如 `<mva>`），去重后置于最终 `prompt` 最前端（以空格分隔）。
2. **后置触发词与风格核心词段**：按顺序合并风格配方的正向核心词 + 各 LoRA 的 `trigger_position == "suffix"` 非空触发词（大小写不敏感去重），追加在主体与构图提示词之后。
3. **负向过滤词段**：将原有 `negative_prompt` 与风格配方负向词、各 LoRA `negative_trigger_word` 合并去重。
4. **调用时机铁律**：
   - 始终在 `image_prompts.compose()` **之后**执行，避免 `qwen_image_standard.strip_quality_slogans()` 误删 `masterpiece`、`ray tracing`、`hyper-detailed` 等 LoRA 风格词；
   - 编辑模式始终在 `_reference_prompt()` 拼接 `<image1> is ...` **之前**执行，确保参考图槽位映射不被破坏。

## 七、配合 LoRA 的剧本对齐提示词升级（`material-image-v6-qwen21`）

针对「视频流程中准备的素材偏离剧本、人物与剧本不相符」的 4 个代码根因，同步升级 `brief.py`、`image_prompts.py` 与 `qwen_image_standard.py`：

1. **补齐剧本实体档案注入（`brief.py:_load_context`）**
   - 根据 `requirement.family` + `entity_id`（含别名匹配），从 `FourStageGlobalStoryBible`、`StoryOutline` 及已确认 `EntityCatalog` 精确提取该角色/场景/道具的完整档案（角色小传、年龄、性别、族群/地域、五官、发型、标志性服饰、性格气质、关联场次要点），构造结构化 `script_entity_dossier` 传入 `build_context()`。
   - 在 Producer LLM 规则中新增硬约束：**必须逐条忠实转译 `script_entity_dossier` 的不可变身份特征到 `appearance` 首句和 `must_keep`，严禁擅自改成现代服饰或违背剧本族群/年龄设定**。
   - 若本次任务选定了 `style_preset` / `style_loras`，同步传入 `active_style_lora_context`（风格定位与光影基调摘要），防止 LLM 写出与所选 LoRA 冲突的光影/画风描述。
2. **重排 Qwen-Image-2.1 提示词注意力层序（`image_prompts.py` 升至 `material-image-v6-qwen21`）**
   - 调整层序，把主体和硬特征从第 4/9 层提前到最高权重位：
     1. **主体身份与外观**（`subject` + `appearance`）；
     2. **不可漂移硬特征**（去重后的 `must_keep`，紧贴外观，不再沉底）；
     3. **精炼构图与背景规则**（将原本重复 3 遍的 `anchor` / `framing` / `closing` 收拢为 1 句无冗余约束：角色保持单人半身正面清晰五官、场景保持空景无人广角、道具保持单件完整无手持）；
     4. **影片介质与英文时代/世界观锚点**（不再把大段未翻译中文 `world_note` 原样糊在英文 prompt 前部；由 `brief` 转译的英文时代/材质句承载世界观）；
     5. **防叠加约束**（保留原有反水印/Logo/拼图硬规则，纸张与屏幕载体保持合法文字豁免）。
   - 随后统一经过 `apply_image_lora_prompts()` 注入前置触发词、正向风格词与负向过滤词。
3. **LoRA 感知的棚拍/光影协同（`qwen_image_standard.py`）**
   - 零 LoRA 时保持中性棚拍光影；
   - 挂载风格 LoRA / 风格配方时，模板层不再强制硬塞 `"even soft diffused lighting"`，将光影与质感表达权让渡给所选风格配方与 `brief`，同时保留角色/道具参考图的干净低干扰背景，确保下游 H3 Ref2VA 人物一致性与抠像稳定性。
4. **`brief_basis` 缓存失效补全**
   - `brief_basis` 摘要新增纳入：`entity_dossier_sha256`、`style_preset_id`、`style_loras`（`lora_id` + `sha256` + `strength_model`），确保改剧本人物设定或切换 LoRA 风格配方时自动重算 Brief，不再复用过期缓存。

## 八、双入口 API 与前端贯通

| 入口 | 后端接口与落账字段 | 前端接入点 |
|---|---|---|
| **素材中心** | `ImageJobRequest` 新增 `style_preset: str \| None` 与 `style_loras: list[StyleLoraSelection]`；`create_image_jobs` 调用唯一裁决并冻结写入 `LibraryRenderTask.payload`（纳入 `request_hash` 幂等）；`LibraryTaskAttempt.payload` 与 `LibraryVersion.provenance` 完整溯源；`new-attempt` 自动透传 | `AssetsWorkspace.tsx` 嵌入共享 `StyleLoraAssemblyPanel`（`baseModel="qwen_image_21"`），顶部提供 3 套内置风格配方一键装配条；纯本地 `RGBA` 遮罩抠图模式下自动禁用 |
| **项目内素材准备线** | `api_material.py`（`/generate`、`/regenerate`、`/regenerate-all`）接收可选 `style_preset` 与 `style_loras`，写入 `MaterialRun.payload` 与 `MaterialItem.payload`；预览提示词接口走 `prompt_source.py` 展示含 LoRA 自动注入后的最终提示词；候选资产 `provenance` 同步记录 LoRA 快照 | `MaterialPreparationPanel.tsx` 嵌入同一共享 `StyleLoraAssemblyPanel`（`baseModel="qwen_image_21"`），切换配方或 LoRA 后提示词预览即时同步 |
| **共享组件扩展** | `GET /api/v1/loras?base_model=...` 新增返回 `presets`（当 `base_model == "qwen_image_21"` 时附带 3 套配方及每枚 LoRA 的就绪状态） | `StyleLoraAssemblyPanel.tsx` 新增 `baseModel` prop 与可选配方快捷栏；导入表单自动透传 `base_model` 并支持编辑负向过滤词 |

## 九、变更文件清单与分阶段实施里程碑

### 9.1 预计新增文件（5 个）

- `config/deployment/qwen-image-loras-windows-x64.json`
- `config/image_lora_presets.json`
- `backend/alembic/versions/f180f0010010_image_style_loras.py`
- `backend/tests/test_image_lora_assembly.py`
- `backend/tests/test_image_prompt_v6_alignment.py`

### 9.2 预计修改核心文件

- **基础设置可选下载**：`backend/shine/comfyops/capability.py`、`deployment.py`、`resource_discovery.py`、`setup_preparation.py`、`setup_reconcile.py`、`setup_summary.py`、`backend/shine/api_setup.py`、`frontend/src/components/SetupCenter.tsx`、`SetupEnvironmentStatus.tsx`
- **统一 LoRA 台账与裁决**：`backend/shine/comfyops/models.py`、`backend/shine/comfyops/loras.py`、`backend/shine/api_loras.py`
- **统一生图基座与提示词 v6**：`backend/shine/materialprep/qwen_workflow.py`、`qwen_image_standard.py`、`image_prompts.py`、`prompt_source.py`、`brief.py`、`execution.py`、`service.py`、`backend/shine/api_material.py`、`backend/shine/asset_center/contracts.py`、`execution.py`、`backend/shine/api_asset_center.py`
- **前端装配与契约同步**：`frontend/src/shared/StyleLoraAssemblyPanel.tsx`、`frontend/src/asset-center/AssetsWorkspace.tsx`、`frontend/src/asset-center/api.ts`、`frontend/src/components/MaterialPreparationPanel.tsx`、`frontend/src/auditEventCodes.json`、`backend/shine/support/generated/copilot_contracts.json`、`backend/shine/support/knowledge_manifest.json`

### 9.3 分阶段实施与验收矩阵

| 阶段 | 核心任务 | 验收门禁 |
|---|---|---|
| **M1：基础设置可选下载 + 台账升级** | 新增 `qwen-image-loras-windows-x64.json`、接入 `image_loras` 可选下载能力、`f180f0010010` 迁移、泛化 `evaluate_style_lora_selection()` 与 `_ensure_lora_not_in_use()` | Alembic 往返迁移通过；`SetupCenter` 勾选 `image_loras` 可下载并核验 4 枚 LoRA 且自动入库 `base_model="qwen_image_21"`；跨基座混挂返回 `409`；在途生图任务拦截删除返回 `409 LORA_IN_USE` |
| **M2：统一生图基座 + 提示词 v6 升级** | `attach_qwen_style_loras()` 接入 T2I 与编辑图、打通编辑图负向通道、`apply_image_lora_prompts()`、`brief.py` 实体档案注入与 `image_prompts.py` `v6` 层序重构 | 零 LoRA 基线图哈希与既有完全一致；编辑图负向词与推荐采样参数生效；`<mva>` 自动前置；人物档案忠实进入 `appearance` 与 `must_keep`；`brief_basis` 随 LoRA/人设变更正确失效 |
| **M3：双入口前后端贯通 + 契约同步** | 接入 `AssetsWorkspace.tsx`、`MaterialPreparationPanel.tsx` 与 `StyleLoraAssemblyPanel.tsx`；重新生成 Copilot 契约与审计事件码 | `ruff check` 全绿；前端 `tsc` + `vitest` + `vite build` 全绿；后端分片回归全绿（含 `test_support_copilot_v21.py`、`test_audit_event_codes.py`、`test_style_lora_assembly.py`） |
| **M4：真机 GPU 端到端验收** | 本机通过基础设置下载 4 枚 LoRA；先重跑 `image-runtime/validate` 刷新 `v6` 指纹；实测零 LoRA + 3 套配方（T2I 与 I2I/MULTIREF 编辑模式）+ 项目内剧本角色素材生成对比 | 确认 `LoraLoaderModelOnly` 在 `QwenImage21Cache` 之前与 `qwen_image_2.1_int8_convrot` 稳定兼容无报错；3 套风格出图视觉与正负向提示词生效；剧本角色外貌/时代服饰显著贴合原剧本；收尾 GPU 租约零残留 |
