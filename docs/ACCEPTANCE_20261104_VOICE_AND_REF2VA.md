# 2026-11-04 验收报告：voice master、H3 Ref2VA 工作流与连续生产全链

**结论：真实 H3 连续生产与合成导出已在本机跑通（3/3 镜头 PASSED，成片 15.52s 已交付）。
最终质量门仍为 `needs_review`，因此镜头与成片的 `acceptance_status` 保持 `pending_review`，
未被改标为 `accepted`。** 上一轮的两个真实数据缺口（无 voice master、无本项目工作流登记验证）
已关闭；期间发现并修复了 6 个真实缺陷，全部带回归测试。

本报告只记录本机（Windows + RTX 5080 16GB + 本地 ComfyUI）实测证据。
没有把任何离线/模拟结果当作真实出片。

---

## 1. 环境与对象

| 项 | 值 |
|---|---|
| 项目 | `b28fcd5c-b120-4ef8-8425-17f83287478c`「苏镇歌舞团」 |
| 剧本 | `H3_MV_MANSIONS_15S_T2VA_SCRIPT.md`（15.00s / 8 镜 / 6 个角色） |
| 渲染器 | 已接管的本机 ComfyUI `F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI`（端口 8188，`is_mock=false`） |
| GPU | NVIDIA GeForce RTX 5080，16303 MiB |
| 文本模型 | MiniMax-M3.1-Flash（远端，真实调用） |
| 语音模型 | Qwen3-TTS 12Hz 1.7B VoiceDesign（本机，真实推理） |
| 审片模型 | qwen3-vl-4b-bnb4（8116）、whisper-tiny（8117），均 `is_mock=false` |

---

## 2. 缺口一：本项目的 voice master

### 2.1 真实生成

上一轮仲裁的结论是"必需角色 voice 缺失，参考集不合规"。本轮用**本机真实模型**生成 6 个角色的
音色参考，全部走产品的 rights → 预览 → QC → 晋升 → 冻结链：

| 角色 | 声音设定（剧本人物表） | VoiceProfile | Asset | 时长 | is_mock |
|---|---|---|---|---|---|
| 阿德 | 温暖略带沙哑的男中音 | `09da324b…` | `4378d10b…` | 3.04s | false |
| 小米 | 明亮清透的女高音 | `eb715900…` | `2d02c89c…` | 2.32s | false |
| 小婉 | 温润的中音 | `178a8762…` | `e1d2db8e…` | 1.68s | false |
| 小溪 | 微哑带烟火气的声线 | `efa1b955…` | `a010973a…` | 2.56s | false |
| 小美 | 甜亮音色 | `5d9ffef1…` | `28f2c2dc…` | 1.04s | false |
| 欣欣 | 低沉烟嗓中音 | `5a511b0c…` | `6eba0d54…` | 3.20s | false |

- 6 个 VoiceProfile 全部 `FROZEN`（`voice_freeze` 授权 + 试听证据），6 个 Asset 全部
  `approved=1 / locked=1 / role=voice`。
- 候选 QC：`technical=pass`（真实 FFmpeg 解码），`semantic=PENDING_HUMAN_REVIEW`
  （语义门需人工，未冒充模型评审）。
- rights 记录：项目级 `voice_design` 与 `asset` 两条授权记录，均 `APPROVED`。

### 2.2 已知边界（如实记录）

- 规则包要求 H3 的音频参考长度在 **2–15 秒**。上表中**小婉 1.68s** 与**小美 1.04s** 低于下限，
  因此不能作为 H3 参考音频。本轮导演方案没有选中这两个（见 §5.3），所以渲染成立；
  但这两个 master 本身不能承担该角色的参考位。
- 音色参考**不等于**演唱音色的验收；真实发音的准确度仍需人工听审。
- 已晋升 master 不可就地修改（`PROMOTED_ASSET_IMMUTABLE_CREATE_NEW_VERSION`），
  角色的 voice 只能通过新版本取代。

---

## 3. 缺口二：本项目的 H3 Ref2VA 工作流登记与验证

### 3.1 登记结果（4 个，全部实机验证通过）

| Profile | 图像槽 | 音频槽 | `validated_against.source` | 缺节点 | 缺输入 |
|---|---|---|---|---|---|
| `local-h3-ref2va-pdd8-2img-1aud` | 2 | 1 | `managed_live_object_info` | 0 | 0 |
| `local-h3-ref2va-pdd8-2img-1aud-portrait` | 2 | 1 | `managed_live_object_info` | 0 | 0 |
| `suzhen-ref2va-3img-1aud-portrait` | 3 | 1 | `managed_live_object_info` | 0 | 0 |
| `suzhen-ref2va-9img-1aud-portrait`（本轮新增） | 6 subject + 3 picture | 1 | `managed_live_object_info` | 0 | 0 |

验证是**在真实受管渲染器上**跑的：启动受管 ComfyUI → 读取真实 `object_info` → 比对节点的
Autogrow 参考输入族 → 停止渲染器。`generation_verified` 仍为 `false`（没有把一次校验说成"已出片"）。

### 3.2 为什么新增 9 槽 profile

本机节点的 `MiniMaxH3ReferenceToVideo` 用的是 Autogrow 组 `ref_images.ref_image_N`（N < 9），
即**节点本身支持最多 9 张参考图**；工作台此前只是自己写死了 2–3 个加载节点。
本项目每个分段要绑定 6 个角色 master（全部是 subject 槽）+ 1 个场景（picture 槽）+ 1 个 voice
（audio 槽），旧 profile 映射不到 `reference_subject_2..6`，必然 `PENDING_WORKFLOW_MAPPING`。
新 profile 用真实节点能力构建，并在真实渲染器上验证通过。
`config/workflows/` 里仓库自带的 catalog profile **未改动**。

---

## 4. 集成到"LLM 基础环境搭建能力"的代码

| 文件 | 内容 |
|---|---|
| `backend/shine/voiceops/runtime_config.py`（新增） | 本机语音运行时配置：角色→模型包路由 + 解释器/依赖覆盖层运行时；环境变量优先，其次 `<data>/comfyops/voice-runtime.json` |
| `backend/shine/comfyops/llm_readiness.py` | `voice_readiness` 从"只看路由"升级为真实门禁：路由 + 包已按哈希校验 + 能力声明 + 角色运行时 + 文件是否在盘 |
| `backend/shine/api_setup.py` | `GET /setup/voice-readiness`、`GET /setup/voice-runtime`、`POST /setup/voice-runtime`、`POST /setup/profiles/validate` |
| `backend/shine/api_preparation.py` | `voice_provider` 读取上述配置，配置变化即重建 provider（不再缓存过期 provider） |
| `frontend/src/components/SetupPanel.tsx` | 环境摘要里逐角色中文说明语音能力与缺什么；新增「验证本项目工作流」按钮 |
| `backend/shine/comfyops/local.py` | 安装核对推进已校验包状态 + 幂等重验证 |
| `backend/shine/comfyops/runtime_configuration.py` | 空闲门禁只拦"真在跑"的系列 |
| `backend/shine/agents/film.py`、`backend/shine/api_film.py`、`backend/shine/schemas.py`、`backend/shine/h3/compiler.py`、`backend/shine/h3/rulebook.py`、`backend/shine/comfy/provider.py`、`backend/shine/comfy/workflow.py` | 合唱行多说话人处理、参考槽位裁剪、绑定族序 |

### 4.1 期间发现并修复的 6 个真实缺陷（均带回归测试）

1. **安装核对不推进包状态**（`local.py`）
   核对已经把 VoiceDesign 包记为 `MATCHES_PINNED_FILES`，注册表却仍停在 `UNVERIFIED_ORIGIN`，
   于是语音能力永远"未就绪"。现在按文件集合实测结果推进到 `FILES_VERIFIED_PENDING_RUNTIME`
   （文件已校验 ≠ 运行已验证），并允许对已核对过的尝试做**幂等重验证**（只读目录、不联网、不删除）。
   → `backend/tests/test_v13_preparation.py`

2. **空闲门禁误阻**（`runtime_configuration.py`）
   `require_idle_configuration` 把 `NEEDS_REVIEW` 系列当成活工作，导致**别的项目**一个被卡住的
   系列就让本机所有运行期重新验证都无法进行（真实卡住了本轮的工作流验证）。
   现在只拦 Worker 真能认领的系列状态（`QUEUED/CLAIMED/RUNNING/PAUSED/PAUSE_REQUESTED/RECONCILING`）。
   → `backend/tests/test_idle_configuration.py`

3. **合唱行被当成一个名字**（4 处）
   剧本里第一镜是六人合唱，编剧把说话人写成 `"S1, S2, S3, S4, S5, S6"`。
   绑定重写、导演的人物绑定校验、`ShotSpec` 的说话人校验、规则包剔除台词行的正则，**四处**都把
   整串当成一个名字，于是重键后的产物仍带占位标签 → 永远 `BLOCKED_MISSING_MASTERS`。
   现在四处用同一个拆分规则（`split_character_names`），合唱行仍是**一行一个 `<d>` 块**
   （规则包 H3_DIALOGUE 要求"每个原始台词行一个语言标注块"，拆成 6 行反而违规）。

4. **未用参考槽位让渲染被拒**（`comfy/provider.py` + `comfy/workflow.py`）
   profile 提供 9 个槽位而某镜只绑定 7 个时，多余的 `LoadImage` 保留占位文件名
   （`shine-reference-9.png`），ComfyUI 直接 400 拒绝整条提示词（`COMFY_PROMPT_REJECTED`，
   首次尝试 `7718deba…` 就是这样失败的）。现在提交图会确定性地剔除未用槽位的加载节点及其输入。

5. **静态槽位顺序必须与清单顺序一致**（`api_film.py`）
   H3 用一个图像序列编号，静态 profile 的物理槽位必须与参考清单顺序一致。现在镜头绑定按族
   排序（subject → picture → video → audio），族内保持方案原序。

6. **参考音频长度下限被实测发现**
   规则包要求音频参考 2–15 秒；生成时两个角色的样本（1.68s / 1.04s）低于下限。
   导演方案按实际时长选中了合规的 master，因此渲染成立（见 §5.3）。

---

## 5. 验收：应用分镜 → 连续生产 → 合成导出

### 5.1 导演链重跑（真实 MiniMax 调用）

编剧 → 导演 → 摄影 → 场记 → 剪辑 → 仲裁，整链真实跑通。

- 编剧产物经**用户已确认的人物映射**重键为新产物 `5d5c7b48…`（S1–S6 → 阿德/小米/小婉/小溪/小美/欣欣）。
- 导演方案 `8e1b88f3…`：3 个渲染段，切点偏移 2.4 / 1.7+3.4 / 1.7+3.4 与规则包的编辑时序一致；
  每段参考集 = 该段角色 master + 场景 + **一个** voice master，落在已验证工作流的预算内。
- 场记报告与仲裁：**无冲突**（上一轮的"缺 voice / 工作流未登记"两条冲突均已消除）。

### 5.2 应用分镜

`POST /projects/b28fcd5c…/film-artifacts/50efd07e…/apply-camera` → **3 个镜头**：
`6122dc8e…` / `1fd23a3e…` / `f6e1aa3a…`。

### 5.3 连续生产（真实 H3）

生产序列 `90d61461…`，工作流 `suzhen-ref2va-9img-1aud-portrait`（`4a338583…`）：

| 镜头 | Job | 状态 | 产物 | 时长 | 画面 | 音轨 |
|---|---|---|---|---|---|---|
| segment_01 | `20d7c32f…` | PASSED | `6668e8a1…` | 4.458s | 768×1344 h264，107 帧 | AAC |
| segment_02 | `373f7308…` | PASSED | `43fd9ec4…` | 5.166s | 768×1344 h264，124 帧 | AAC |
| segment_03 | `ae899a4e…` | PASSED | `a8925ed9…` | 5.874s | 768×1344 h264，141 帧 | AAC |

- 三个作业全部 `is_mock=0`（真实渲染产物，`media_artifacts.is_mock=0`）。
- 镜间由空闲守租人交接（`shine-handoff/drain` → `resume`），受管渲染器全程同一实例。
- 序列结束时**租约释放且只释放一次**（`lease_id=null`，`finalization_state=RELEASED`）。
- 每镜五道质量门（`quality_gates`，`is_mock=0`）：
  `technical=passed`（`model: ffprobe`，真实解码/时长/分辨率/静音检查）；
  `semantic=not_run (SEMANTIC_REVIEW_NOT_CONFIGURED)`、
  `audio=not_run (ASR_NOT_CONFIGURED)`、
  `style=not_run (STYLE_REVIEW_NOT_COVERED)`（三门为咨询门，`required=0`，按设计不阻断）；
  **`final=needs_review`**（`quality_verification: real`）。
- 咨询评审已按**真实回环路由**入队并执行：`qwen3-vl-4b-bnb4`（VLM）与 `whisper-tiny`（ASR），
  均为 `advisory=true`、`replay=NEVER_AUTOMATIC`，不占渲染器也不占 GPU 租约。
  **实测结果（如实记录）**：三镜的 VLM 评审全部 `VLM_TIMEOUT`、ASR 评审全部
  `ASR_UNAVAILABLE`（本机 whisper 审片服务的 `POST /v1/audio/transcriptions` 返回 404），
  记为 `UNAVAILABLE` 咨询证据，**没有阻断**镜头、系列或交付——这正是"咨询评审不在关键路径上"
  的设计。因此本轮**没有任何模型评审通过**：三道咨询门仍然是 `not_run`。
  本机的 qwen3-vl-4b 在 768×1344 视频帧上跑带结构化 schema 的评审，超过了评审超时上限；
  这是**本机算力/时限**的事实，不被写成"评审通过"。

### 5.4 合成导出

交付订单 `722ea25d…`（`origin: series_auto`，`edl_source: frozen_edit_intent`）→ **DELIVERED**：

| 项 | 值 |
|---|---|
| 产物 | `55cca29e…`（`artifact_kind=delivery`，`is_mock=0`） |
| 路径 | `.local-acceptance/real-user-journey-final/data/artifacts/b28fcd5c…/delivery/3020641d….mp4` |
| 时长 | **15.521s**（剧本目标 15.00s；3 段真实素材 4.458 + 5.166 + 5.874） |
| 画面 | h264 768×1344，372 帧 @24fps |
| 声音 | AAC 立体声 48kHz，731 帧 |
| 体积 / 码率 | 12 855 790 B / 6.63 Mbps |
| SHA-256 | `3020641d894c39fa8408d54bf8f0d92699a9200b606f46ecabae1e6a00d23e2e`（与库中登记值一致，已复算） |
| review_state | `pending_review`（终审未由人工/模型关闭，按规则不改标） |

### 5.5 抽帧目视核对（仅为记录，不构成质量验收）

从成片抽取 4 帧（1.5s / 5.0s / 8.5s / 13.5s，见 `.local-acceptance/_frames/`）：

| 时间 | 画面 | 与剧本的对应 |
|---|---|---|
| 1.5s | 挑高大厅、水晶吊灯、大理石地面、弧形楼梯；男主深色西装立于楼梯，五位女主发型/服装各异（酒红、米白、黑亮片、薄荷绿双辫、铂金短发紫衣） | 镜 1–2：主角推门而入 + 群舞起手 |
| 5.0s | 女主在壁炉前的近景，黑色亮片裙与长靴，背景为燃烧的壁炉 | 镜 5：女主 C 在燃烧的壁炉旁 |
| 8.5s | 女主在拱形落地窗前，银白缎面长裙 | 镜 4：女主 B 在拱形落地窗前 |
| 13.5s | 六人回到大厅中央群舞，举手收尾，吊灯在画面中央 | 镜 8：六人群舞收尾 |

抽帧只说明**画面内容与剧本对得上**，不代表身份一致性、口型或声音质量通过；
这三条仍由未关闭的 `semantic` / `audio` 门负责，本轮它们没有关闭。

---

## 6. 明确未声明的内容

- **没有声称任何模型评审通过。** 本机的咨询评审在本轮实测为 `VLM_TIMEOUT`（三镜）与
  `ASR_UNAVAILABLE`（三镜，whisper 审片服务缺少 `/v1/audio/transcriptions`），因此三道咨询门仍是
  `not_run`。`final` 门为 `needs_review`，三个镜头与成片的 `acceptance_status` 保持
  `pending_review`。语义/风格/终审需要人工逐项签署（操作员签署会**追加**一组门禁记录，
  worker 原来那组原样保留），本轮未由人工签署。
- **没有声称发音准确。** 语音 master 只是音色参考；脚本自带的提示词也写明"中文口音未经验证"。
- **没有声称群像身份稳定。** 6 人同框的身份漂移是模型本身的已知风险，本轮未做主观验收。
- 首镜（`7718deba…`）因提交路径缺陷被 ComfyUI 在入队前拒绝，已按产品路径留为
  `NEEDS_REVIEW / HUMAN_REVIEW_REQUIRED` 历史记录，未被改写成通过。
- 仓库自带的 2 槽 catalog profile 未改动；9 槽 profile 只登记给本项目。
- `docs/H3_MV_MANSIONS_15S_T2VA_SCRIPT.md`（用户剧本）保持未跟踪，未提交、未覆盖、未删除。

---

## 7. 复现命令

```powershell
# 栈（API 8000 / worker / web 5173）
$env:SHINE_DATA_DIR='F:\ClaudeWorkSpace\projects\shine-video-studio\.local-acceptance\real-user-journey-final\data'
$env:SHINE_SETUP_RECIPE="$env:SHINE_DATA_DIR\comfyops\setup\managed_recipe.json"
$env:SHINE_ENABLE_LOCAL_EXECUTION='1'; $env:SHINE_RENDER_PROVIDER='comfyui'
.\backend\.venv\Scripts\python.exe -m uvicorn shine.main:app --host 127.0.0.1 --port 8000 --app-dir backend
$env:PYTHONPATH='backend'; .\backend\.venv\Scripts\python.exe -m shine.worker

# 环境能力
Invoke-RestMethod http://127.0.0.1:8000/api/v1/setup/voice-readiness
Invoke-RestMethod http://127.0.0.1:8000/api/v1/setup/voice-runtime
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/setup/profiles/validate `
  -ContentType 'application/json' -Body '{"project_id":"b28fcd5c-b120-4ef8-8425-17f83287478c"}'

# 生产与交付
Invoke-RestMethod http://127.0.0.1:8000/api/v1/production-series/90d61461-db1b-4cb5-a0f6-c33e9acdf5b1
Invoke-RestMethod http://127.0.0.1:8000/api/v1/projects/b28fcd5c-b120-4ef8-8425-17f83287478c/delivery-orders
Invoke-RestMethod http://127.0.0.1:8000/api/v1/deliveries/55cca29e-80d2-4558-9da0-71b1aa3f07d6/media -OutFile reel.mp4
```

本轮脚本（可复跑，均只走产品 API）：`.local-acceptance/run_voice_setup.py`、
`run_voice_masters.py`、`run_wide_profile.py`、`run_binding_and_chain.py`、
`run_chain_resume.py`、`run_apply_only.py`、`run_series2.py`、`run_delivery.py`。

---

## 8. 测试

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端全量 | `pytest tests -q`（backend/） | **1522 通过 / 1 跳过 / 0 失败 / 0 错误**（exit 0，`.local-acceptance/backend-full-20261104b.log`） |
| Ruff | `python -m ruff check backend/shine` | All checks passed |
| 前端测试 | `npm.cmd test`（frontend/） | 16 文件 / **114 用例全通过** |
| 前端构建 | `npm.cmd run build` | 通过（tsc + vite，1.42s） |
| 新增回归 | `test_setup_voice_readiness.py`(9) `test_setup_workflow_profiles.py`(4) `test_reference_slot_pruning.py`(4) `test_idle_configuration.py`(6) `test_character_binding.py`(13，含合唱行重键) `test_film_reference_budget.py`(3) | 全通过 |
| 行尾 | 改动文件全部保持仓库的 LF | 无 CRLF 混入 |
