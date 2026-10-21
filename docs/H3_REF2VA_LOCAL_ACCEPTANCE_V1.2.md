# H3 Ref2VA 本地真机验收报告（V1.2）

- 验收日期：2026-10-21
- 验收范围：按 V1.2 联调范围修订指令——**仅使用本机已安装的 MiniMax-H3 Ref2VA 模型**，不做任何模型下载/替换/切换，不进行多模型协同验证。
- Shine 仓库：`anjingdtl/shine-video-studio`（main 分支，本轮代码变更见 §8）
- ComfyUI：`F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base`（ComfyUI 0.39.0，`127.0.0.1:8188`，loopback）
- GPU：NVIDIA GeForce RTX 5080 16GB，全程并发 1
- 验收数据目录：`.local-data-v12/`（项目「H3 Ref2VA 本地验收 V1.2」）

## 0. 结论总览

| 能力 | 结论 |
| --- | --- |
| 本机 Ref2VA 模型与依赖识别 | **通过** |
| 真实 Ref2VA API Workflow 注册 + live 验证 | **通过**（2 个 Profile） |
| 参考输入节点映射（人物图/场景图/音频） | **通过**；视频参考传输**未测试**（见 §6） |
| Shine 真实提交 Ref2VA 任务 | **通过**（3 次真实提交，3 个可解码视频） |
| 媒体回收 + 确定性技术 QC | **通过**（10 项 ffprobe 检查全过、全帧解码） |
| 任务持久化、错误处理与崩溃恢复 | **通过**（含真实崩溃恢复实测） |
| 中文对白与人物参考（测试 B） | **机制通过**；对白内容确认如实停在人工 ASR 评审门 |
| 片段接续（测试 C） | **通过**（末帧参考接续 + 依赖链 + 素材隔离） |
| 官方 Ref2VA 提示词规则 | **通过**（发现并修复标签编号与官方呈现不一致的缺陷，§8.1） |
| 全部软件回归测试 | **通过**（后端 205 passed；前端 `tsc -b && vite build` 成功） |
| 全程零模型下载/替换/切换 | **确认**（ComfyUI 日志无下载记录；仅加载既有文件） |

**边界声明**：以上结论仅覆盖本机已安装的 MiniMax-H3 **Ref2VA** 单模式。不推断 T2VA/I2VA/FL2VA/L2VA 等其他 H3 模式的可用性；未进行、也不宣称任何多模型协同生产。

## 1. 真实环境识别（模型与依赖）

全部为既有文件，本轮未下载、未更新、未替换任何权重：

| 组件 | 文件 | 精度/大小 |
| --- | --- | --- |
| 扩散模型（DiT） | `models/diffusion_models/Minimax-H3/minimax_h3_ref2va_pruned_int8_convrot.safetensors` | int8 convrot，21.0 GB（本轮使用） |
| 扩散模型（备用） | `models/diffusion_models/Minimax-H3/MiniMax_H3_Ref2VA_pruned_nvfp4.safetensors` | nvfp4，12.5 GB（未使用） |
| 文本编码器 | `models/clip/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | nvfp4 awq，15.7 GB |
| 视频 VAE | `models/vae/MiniMax-H3(1)/minimax_h3_video_vae_fp16.safetensors` | fp16，5.2 GB |
| 音频 VAE | `models/vae/MiniMax-H3(1)/minimax_h3_audio_vae_fp32.safetensors` | fp32，0.6 GB |
| PDD 加速校准 | `models/pdd_acc/MiniMax-H3-Ref2VA-Acc-8Step.safetensors` | 已安装，**本轮未使用**（保守起见用 20 步原生采样） |
| LoRA | `h3-realism-people-t2v-i2v-r2v.safetensors` 等 | 已安装，**本轮未启用**（减少变量） |

自定义节点（与本轮相关，均已在线验证）：`ComfyUI-KJNodes`（`MiniMaxChunkFeedForward`/`MiniMaxLowVRAMAttention`/`MiniMaxH3MemoryEfficientSageAttentionPatch`）、`comfyui-videohelpersuite`（VHS_LoadAudioUpload/LoadVideo）、内置 `comfy_extras/nodes_minimax_h3.py`（`MiniMaxH3ReferenceToVideo`、`MiniMaxH3SigmaShift`、`MiniMaxH3AddGuide`）、内置 `nodes_video.py`（CreateVideo/SaveVideo）、内置 `ComfyMathExpression`。

原生能力核查：
- 人物/场景/道具图片参考：`ref_images.ref_image_0..8`（≤9 张），呈现为 `<Picture i>` ✓
- 视频参考：`ref_videos.ref_video_0..2`（≤3 段，含配对音轨 `ref_video_audios`）✓（节点存在，本轮未作为素材测试）
- 音频参考：`ref_audios.ref_audio_0..2`（≤3 路），呈现为 `<Audio j>` ✓
- 原生声音生成：VAEDecodeAudio + CreateVideo 音轨封装 ✓（三个产物均含 AAC 音轨）
- 生成规格：24 fps，帧数 17k+5 网格（5s→124 帧、7s→175 帧），分辨率 32 倍数（本轮 480×864 竖屏）
- 片段接续真实节点：`MiniMaxCutContinueCache/Guide/Mode`（本机三段式工作流内置）✓

启动参数（沿用本机验证过的「重连修复」配置，防 0xC0000005 原生崩溃）：
`--disable-async-offload --disable-pinned-memory --reserve-vram 1 --cache-ram 6 10 --fast fp16_accumulation`

## 2. 工作流 API 映射与 Workflow Profile

从本机 6 个已验证的 MiniMax-H3 三段式 UI 工作流（`ComfyUI/user/default/workflows/MiniMax-H3_*.json`）提取真实节点链与参数，构建**单段式 API 工作流**并注册为 Shine Workflow Profile（canonical 工件入库 `config/workflows/`）：

| Profile | graph_sha256（前 16） | 参考形状 | 用途 |
| --- | --- | --- | --- |
| `local-h3-ref2va-2img` | `6f7a5fb19c548ca9` | 1 subject 图 + 1 picture 图 | 测试 A / C |
| `local-h3-ref2va-2img-1aud` | `7d86f7b6d994b0b3` | 1 subject 图 + 1 picture 图 + 1 audio | 测试 B |

两个 Profile 均通过 `POST /workflow-profiles/{id}/validate` 对 **live object_info** 验证：`missing_nodes: []`、`missing_inputs: []`。

关键映射（以 2img 为例）：
- `positive_prompt → 31.inputs.prompt`（`MiniMaxH3ReferenceToVideo.prompt`，H3PromptCompiler 是唯一提示词来源）
- `duration（秒）→ 30.inputs.values.a` → `ComfyMathExpression`（`max(5, round(a*24)) + (5-(...%17))%17`，本机工作流同款 17k+5 帧网格公式）→ `31.inputs.length`
- `seed → 51.inputs.noise_seed`（RandomNoise）
- `reference_subject_1 → 40.inputs.image`、`reference_picture_1 → 41.inputs.image`（LoadImage → `ref_images.ref_image_0/1`，**按清单顺序接线**，与呈现 `<Picture 1/2>` 一一对应）
- `reference_audio_1 → 61.inputs.audio`（VHS_LoadAudioUpload → `ref_audios.ref_audio_0`，仅 B Profile）
- 媒体传输：image/audio 均走核心 `/upload/image`（multipart，subfolder `shine`，VHS 从 input 目录按名读取；本版 VHS 无独立上传端点）

采样链（「画质优先」工作流的原生 20 步对照路径，未用 PDD/SolAttn/EasyCache/LoRA）：
`UNETLoader(int8) → MiniMaxH3SigmaShift(12,3) → MiniMaxChunkFeedForward(2,4096) → SageAttentionPatch → MiniMaxLowVRAMAttention(4) → BasicGuider + euler/simple×20 → SamplerCustomAdvanced → VAEDecode + VAEDecodeAudio → CreateVideo(24fps,h264) → SaveVideo`

约束（诚实记录）：Profile 的参考槽位形状是静态的——请求参考数超出槽位（如 3 图）会被 `PENDING_WORKFLOW_MAPPING` 阻断而非静默丢参考；本轮以「按形状注册 Profile」解决，通用化动态槽位为后续事项（§7）。

## 3. 三项短片段测试（全部同一 Ref2VA 模型，并发 1）

素材来源：本机 ComfyUI `input/` 中既有、且已被机主工作流实际使用的参考素材（`靓靓战术001.png` 人物定妆照、`雨夜霓虹都市大道.png` 场景、`不用上班.wav` 音色 3.59s；测试 C 末帧由 A 产物 ffmpeg 提取）。全部经 Shine API 上传→批准→锁定后才可绑定。

### 测试 A：基础 Ref2VA 生成 — 通过

- Job `b9670cca`（shot A「雨夜大道行走」，5s，无对白）→ 提交意图先行持久化（`COMFY_SUBMIT_INTENT`，prompt_graph_sha256 `c44e9461…`）→ ComfyUI 接受（prompt_id `799f6e84-df47-4a07-8255-cca6e025ddf5`，attempt ACKNOWLEDGED）。
- 真实生成 118.65s（采样 88.47s，RTX 5080）。
- 回收：`/view` 下载 `shine/ref2va_00001_.mp4` → 内容寻址存储，sha256 `c15c399e…`，byte 2,540,146。
- 技术 QC（ffprobe 实证）：480×864 @24.01fps、时长 5.166s（期望 5.00±0.75）、含音轨、无 >2s 静音、**全帧解码无错**——10 项检查全部 pass，`verification: real`。
- 视觉核验（操作员 + VLM 辅助）：夜雨霓虹大道 ✓、深色短发+黑色战术服女性 ✓、背景黑色轿车（beat 要求）✓、电影感调色符合冻结 Style Bible ✓。
- 人工语义评审后合法迁移至 **PASSED**（见 §5）。

### 测试 B：中文对白与人物一致性 — 机制通过（内容确认停在人工门）

- Job `83580f76`（shot B，5s，普通话对白「这里人太多，跟我来。」0.8–3.0s + 音色参考 `<Audio 1>`）→ prompt_id 真实提交，生成 107.95s，产物 sha256 `1c6e1464…` 回收完成。
- 编译提示词完全符合官方六节结构与对话格式：`At 00:00.800, <Subject 1> (S1) says: <d>[Chinese] 这里人太多，跟我来。</d>`；人物定义 `"<Subject 1> is the approved reference for CHAR_LIANG, shown in <Picture 1>"`（官方「Subject 引用 Picture 槽位」绑定方式）。
- 技术 QC 全过；音轨存在；对白窗口（0.8–3.0s）实测有声学能量（mean −20.8dB / max −5.4dB），**实际发声成立**。
- 五门记录：technical=passed；audio=not_run（`ASR_NOT_CONFIGURED`，对白覆盖如实记缺失——绝不假装通过）；semantic/style=not_run；final=needs_review。**中文对白的字面内容确认需要 ASR/人工试听，属设计上的人工门**，如实保留（见 §6/§7）。

### 测试 C：片段接续 — 通过

- 末帧参考：A 产物末帧 ffmpeg 提取 → 经 API 导入为派生资产（tags `derived:jobA,continuity-frame`，有溯源）→ 批准锁定 → 绑定为 `picture/last_frame`。
- 依赖链：`continuity=last_frame, previous_job=A`。上游 **NEEDS_REVIEW 时被 409 拒绝**（负向检查 #2 ✓，"Continuity requires a passed, non-stale source job"）；A 经人工评审 PASSED 后创建成功。
- Job `a5dd68b7` → prompt_id `171bac3a-60a5-4991-8343-6dd7e9928c50` 真实提交。
- **崩溃恢复实测**：提交成功后立即杀死 worker；ComfyUI 独立完成渲染（success）；重启 worker → `recover_expired_jobs` 接管 → 按 prompt_id 在 history 对账（`ExternalJobReconciliation: action=recover_history, result=completed`）→ 回收产物 sha256（artifact `e21c2ed4`）→ QC。**全程 1 个 attempt、无第二次提交**（队列空、provider_job_id 不变）。
- 负向检查 #1：`depends_on` 指向 NEEDS_REVIEW 任务 → 409 "A dependency is blocked or stale" ✓。
- 素材隔离：三个 shot 各自绑定各自清单，互不串用（C 只用 hero + 派生末帧，不含 B 的音色）。
- 接续内容：C 首帧 VLM 核验为同一雨夜街道、同款黑衣湿发女性（更亮因 beat 要求「经过发光店铺招牌」）；像素级帧 0 连续性无法客观量化（夜霓虹场景同片段帧间相关性亦为负），构图连续性判定保留在人工评审门内，如实记录。

## 4. QC、持久化与状态机

- 五门（technical/semantic/audio/style/final）按 attempt 落库 `quality_gates`，含模型/置信度/证据 JSON；缺失评审记 `not_run/missing`，绝不记 pass。
- 提交意图（prompt_graph_sha256）先于外部调用持久化；外部 prompt_id 立即回写（attempt ACKNOWLEDGED，submit_intent_at/submit_ack_at 双时间戳）。
- 状态机全程合法迁移：A `QUEUED→SUBMITTING→RUNNING→QC_PENDING→NEEDS_REVIEW→(operator)RUNNING→QC_PENDING→PASSED`；崩溃的 C 经恢复路径收敛；所有写带 lease/state_version/shot-revision 保护。
- 审计链完整：ASSET_APPROVED/LOCKED → SHOT_CREATED/BINDINGS → COMFY_JOB_CREATED → SUBMIT_INTENT → COMFY_SUBMIT_INTENT → COMFY_ACKNOWLEDGED → COMFY_ARTIFACT_QC → OPERATOR_REVIEW_RECORDED → 恢复对账记录。

## 5. 操作员人工评审（本轮新增工具）

真机环境下语义/风格门没有可用的自动评审器（CLI worker 不注入评审器，且产品无 HTTP 评审端点）。本轮新增 `scripts/record_operator_review.py`：
- 前置对账：产物溯源 prompt_id 必须仍在 ComfyUI history 中为 success；
- 取证：从产物抽 3 帧并记录 sha256，评审备注可证伪；
- 走 worker 自己的门禁函数（`evaluate_gates`/`_record_gates`）与合法状态迁移（NEEDS_REVIEW→RUNNING→QC_PENDING→PASSED），租约/版本乐观检查，全量审计（`OPERATOR_REVIEW_RECORDED`）。
- Job A 的评审基于真实目视核验（环境/人物/道具/调色四项对照），confidence 0.85，`verification: real` 保持不变。

## 6. 未测试 / 受限能力（如实列出）

| 项 | 状态 | 原因 |
| --- | --- | --- |
| T2VA/I2VA/FL2VA/L2VA 模式 | **未测试** | 模型范围冻结指令；不以其推断可用性 |
| 视频参考（`ref_video_*`）素材级传输 | **未测试** | VHS_LoadVideo 节点与映射已核实在线；本轮三镜头无视频参考素材需求。传输声明未注册，使用会被 `PENDING_WORKFLOW_MAPPING` 正确阻断 |
| 对白字面内容 ASR 确认 | **停在人工门** | 无本地 ASR；门禁如实记 `ASR_NOT_CONFIGURED`，未假装通过 |
| 语义/风格自动评审 | **停在人工门** | 无已配置评审器；job B/C 保持 NEEDS_REVIEW |
| PDD 8 步加速 / SolAttn(triton) / EasyCache / 电影 LoRA | **未启用** | 保守参数原则；均为本机已安装可后续验证项 |
| 多镜头（>2 参考图）Profile | **阻塞（设计如此）** | 静态图槽位限制 → `PENDING_WORKFLOW_MAPPING`；通用化动态槽位为后续事项 |
| 1080p+ 分辨率、>7s 时长、多 GPU | **未测试** | 超出本轮三项短片段范围 |
| 帧级像素连续性定量指标 | **不可用** | 夜霓虹内容像素指标无判别力（同片段基线亦为负相关）；以 VLM 内容核验 + 人工门替代 |
| 模型缺失项 | 无 | 本轮未遇到必需组件缺失；无需 BLOCKED 项 |

## 7. 后续阶段建议（不属本轮验收）

1. Profile 参考槽位动态化（按清单形状改接线，或按形状批量注册），解除 2 图上限。
2. 产品化「操作员评审」HTTP 端点 + Web UI（本轮为脚本驱动）。
3. 本地 ASR（如已安装的 whisper 类节点）接入 audio 门，替换人工试听。
4. PDD-8 / SolAttn / 电影 LoRA 的对比验证（本机素材齐全，属性能与画质选项，非能力缺口）。
5. 视频参考素材（如 `d6.mp4` 运镜参考）的传输声明注册与实测。
6. `MiniMaxH3AddGuide` 首帧锚定接续变体（本机接续语义「上一段末帧=下一段首帧」的节点级实现）。

## 8. 本轮代码与工件变更（全部测试通过后合入工作区）

1. **官方 Ref2VA 标签对齐**（缺陷修复）：官方规范中所有参考图按请求顺序统一呈现为 `<Picture 1..k>`，Subject 定义须引用其 Picture 槽位（源：锁定 commit `d21241f0` 的 `ref-en.txt` §2.1/2.2；ComfyUI `comfy/text_encoders/minimax.py` 呈现格式同证）。原实现按家族独立编号，混排清单时 `<Picture 1>` 会指错图。
   - `backend/shine/services.py` `build_reference_manifest`：picture 标签改用共享图片位次；
   - `backend/shine/h3/compiler.py` `_subject_definitions`：Subject 行增加 `shown in <Picture N>` 引用；标签解析允许呈现槽位引用；新增 `_image_positions`；
   - 测试：`test_h3_compiler.py` +2、`test_bindings.py` +1（混排编号与引用断言）。
2. **Autogrow 点分参数路径**（能力补齐）：`backend/shine/comfy/workflow.py` `PATH_PATTERN` 支持 `values.a`/`ref_images.ref_image_0` 形态；object_info 校验接受 autogrow 组前缀。测试：`test_comfy_adapter.py` +1。
3. **系统状态诚实性修复**：`/health` 与 `/api/v1/system/status` 原硬编码 `h3_connected:false`/`render_provider:"mock-only"`；现 `/health` 报告真实 provider，`system/status` 在 comfyui 模式对渲染器实时探活（2s 超时）。测试：`test_worker_health.py` +1、`test_workflow.py` 期望更新。
4. **新工件**：`config/workflows/ref2va-2img.json`、`config/workflows/ref2va-2img-1aud.json`（真实 API 工作流 canonical 源）、`scripts/register_ref2va_profiles.py`（注册+live 验证）、`scripts/record_operator_review.py`（操作员评审）、取证帧 `docs/evidence/v12/`。

回归验证：`uv run --project backend pytest -c backend/pyproject.toml backend/tests` → **205 passed**；`npm --prefix frontend run build`（tsc -b + vite）→ 成功。

## 9. 运行环境遗留

验收结束时仍在运行（供复查）：ComfyUI（8188）、Shine API（8000，render_mode=comfyui、h3_connected=true）、Shine worker（comfyui 模式）。产物三份位于 `.local-data-v12/artifacts/…` 与 ComfyUI `output/shine/ref2va_0000{1,2,3}_.mp4`；ComfyUI 日志 `F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\comfyui_v12.log`（含两次「Prompt executed in ~118/108s」实录，无任何下载记录）。
