# 本地真实 MiniMax-H3 验收报告（V1.3 轮）

日期：2026-10-25（Asia/Shanghai）。范围：本轮在操作员机器上用真实本地 ComfyUI +
本地 MiniMax-H3 Ref2VA（INT8 ConvRot + PDD 8 步）跑出的三个镜头，以及由这三个真实
镜头装配出的交付样片。同日 11:30（UTC）操作员 `shine-owner` 通过真实操作员 API 完成了
这三个镜头的正式逐项人工审片，交付样片随之重装为 `all_passed`。

关联文档：[本地环境验证报告](LOCAL_ENVIRONMENT_VALIDATION.md)、
[V1.3 收尾报告](LOCAL_V1.3_CLOSEOUT_REPORT.md)、[操作员指南](operator-guide.md)。
证据：`.local-acceptance/evidence/acceptance-evidence.json`、
`.local-acceptance/logs/comfyui-stderr.log`、`.local-acceptance/h3run/`。
本文的最终门禁状态由 `.local-acceptance/evidence/_verify_final_review.py` 与
`.local-acceptance/evidence/_verify_gates.py`（只读查询）复核。

---

## 0. 结论总览

- 本轮产出 **3 个真实 H3 镜头**（A / B / C，均 1344x768、24 fps、175 帧、7.291 秒）和
  **1 个可播放的交付样片**（21.896 秒、525 帧）。
- 三个镜头的**技术门全部真实通过**（`model: ffprobe`，`is_mock: 0`）。
- 2026-10-25 11:30（UTC），操作员 `shine-owner` 通过真实操作员 API
  （`POST /api/v1/operator/jobs/{id}/review`）逐项审阅并签署了这三个镜头。语义门、
  风格门、终审门，以及 B 镜头的音频门，**是由人工评审关闭的，不是由 VLM 或 ASR
  关闭的**——这三个镜头从未被任何模型评审过。三个镜头的最终状态是 `PASSED`，
  `quality_verification = manual_verified`。
- 交付样片的 `review_state` 是 `all_passed`（artifact `e6b264aa…`）。先前的几条样片
  仍以 `pending_review` 原样留在盘上 —— 交付是内容寻址的（第 9 节）。
- A、B 两个镜头的**第一版**是在项目冻结 H3 规则包与 Style Bible v2 **之前**渲染的，
  工作台正确拒绝为它们签署；两镜在当前配置下重渲染之后才被评审与签署（第 2、3、7 节）。
- 本轮暴露并修复了三个只有真实 ComfyUI 才会遇到的缺陷（第 8.1 – 8.3 节）；收尾审片
  阶段又定位并修复了两个新缺陷（8.4 – 8.5 节），并复验了陈旧租约清扫（8.6 节，与
  8.3 是同一处修复）。

## 1. 验收项目与资产继承

验收项目是 V1.2 验收项目的迁移副本：

| 项 | 值 |
| --- | --- |
| 项目 ID | `1f743836-a00c-4787-8343-4f1a24ed9135` |
| 标题 | H3 Ref2VA 本地验收 V1.2 |
| 数据目录 | `.local-acceptance/h3run/`（隔离） |
| 数据库 | `.local-acceptance/h3run/shine.db`（Alembic head `f130a0020001`） |
| `allow_remote_review` | 0 |
| `auto_repair_enabled` | 0 |

迁移方式：原 `.local-data-v12` 目录**只读**；数据库用 SQLite 在线备份 API（WAL 感知）
复制，媒体文件复制进隔离目录。因此本轮没有任何写回原库的动作。

复用的资产全部是**人工在 2026-10-21 已批准并锁定**的主资产，本轮未新建、未替换、
未改审批状态：

| 资产 ID | 文件名 | 角色 / 实体 | 尺寸 | SHA-256 | 审批 |
| --- | --- | --- | --- | --- | --- |
| `ad0ec279-a9f1-4a57-9f0a-08cc093aeca9` | 靓靓战术001.png | character / `CHAR_LIANG` | 1122x1402 | `90b9f8b7…d2554c22` | approved + locked |
| `4b8364f9-4b63-4dc2-b093-2de6f77ef5a3` | 雨夜霓虹都市大道.png | scene / `SCENE_NEON` | 941x1672 | `f47dd755…8ce05877e` | approved + locked |
| `828dde42-2fc1-491f-bc1b-d8a1808249a6` | 不用上班.wav | voice / `VOICE_LIANG` | 3.587 s | `a4e838ba…c024626be` | approved + locked |
| `663d7728-a724-4ad2-b786-26d7ebde34e9` | A_last_frame.png | style_reference / `FRAME_A_LAST` | 480x864 | `73fdac83…59acc9163158b9` | approved + locked |

每-shot 的引用绑定是权威的：一个 job 不得替换成另一套资产。本轮三个镜头的引用清单
（`reference_manifest`）与 `parameter_map` 的槽位一一对应，编译器再把全局资产 ID
重映射成请求内的标签 `<Subject 1>` / `<Picture 2>` / `<Audio 1>`。

## 2. Shot A — `A-PDD8-7s`

被评审并签署的当前版本：

| 项 | 值 |
| --- | --- |
| Shot ID | `b938c504-…`（revision 1，10:44:59 UTC 创建） |
| Job ID | `ba4bb63d-475a-4802-a469-0c4ce166fc97` |
| Attempt ID | `109bd481-4489-4caa-8f54-87d84e45da70`（attempt_no 1） |
| ComfyUI prompt id | `83108e1f-9806-4097-8fff-a9cc27138e39` |
| 工作流档案 | `18e2f8d3-ca1d-4f0d-83ae-0bffc1b4b5f2`（`local-h3-ref2va-pdd8-2img`） |
| 图 SHA-256 | `86a7624e6eaeccd3a3c54cf5024317698cac64cbe083ed10dd158bcaa1e6eaa0` |
| 编译提示词 SHA-256 | `ac4c364155e9950d307e9f05480414d45fc226d15a0b8e4a13e21fbde24ba04c` |
| 提示词图 SHA-256 | `1167f0c8d0f467ef10da0d31b538afd299372f900e0e8c5f378b22cf74fbe5ad` |
| 冻结风格 | **Style Bible v2**，`5380cca5-efb1-4071-bf89-0f9604d41cf9`，sha `1909e253…22f636a8` |
| 状态 | `PASSED`，`quality_verification: manual_verified`，`failure_code: null` |
| `reconciliation_state` | `collected` |
| 提交意图 / 提交回执 | 10:44:59.999 / 10:45:00.115（UTC） |

产物：

| 项 | 值 |
| --- | --- |
| Artifact ID | `75173459-798f-452c-821b-f839c3c3330f` |
| SHA-256 | `b097421032d6093b0abf922e1aa1026e7c70e72b9fda59d196ceabe7a470d8b2` |
| 字节 | 4,249,108 |
| 存储路径 | `artifacts/1f743836-a00c-4787-8343-4f1a24ed9135/ba4bb63d-475a-4802-a469-0c4ce166fc97/b0974210…70d8b2.mp4` |

ComfyUI 运行日志：

```
[INFO] MiniMaxH3PDDAccApply: partition check ok: ref2va file on ref2va model (fl2va 0.0504, ref2va 0.0017)
[INFO] MiniMaxH3PDDAccApply: MiniMax-H3-Ref2VA-Acc-8Step.safetensors (original) steps=8 blocks=4,4,4,4,4,4,4,4, 258 lora modules, heads fused
[INFO] Prompt executed in 230.15 seconds
```

本轮对该文件独立做的 FFprobe / FFmpeg 检查（不依赖系统自带的 QC）：

```powershell
ffprobe -v error -show_entries 'stream=codec_type,codec_name,width,height,r_frame_rate,nb_frames,sample_rate,channels' `
        -show_entries 'format=duration,size' -of default=noprint_wrappers=1 `
        '.local-acceptance\h3run\artifacts\1f743836-a00c-4787-8343-4f1a24ed9135\ba4bb63d-475a-4802-a469-0c4ce166fc97\b097421032d6093b0abf922e1aa1026e7c70e72b9fda59d196ceabe7a470d8b2.mp4'
ffmpeg -v error -i <同一文件> -f null -
```

实测输出：

```
codec_name=h264
codec_type=video
width=1344
height=768
r_frame_rate=24/1
nb_frames=175
codec_name=aac
codec_type=audio
sample_rate=32000
channels=2
duration=7.291000
size=4249108
ffmpeg exit=0
```

即：h264 / 1344x768 / 24 fps / 175 帧 / aac 32 kHz 立体声 / 7.291 秒，**全片解码
exit 0**。技术门以 `model: ffprobe` 真实通过（`is_mock: 0`）。

### 2.1 Shot A 的第一版（已被取代，原样保留）

| 项 | 值 |
| --- | --- |
| Shot ID | `fda8ab9d-3652-420e-8ead-f40b276a4229`（revision 1） |
| Job ID | `ca3754cc-099e-41ce-bb5a-5aac121b7793` |
| Attempt ID | `b16c3b86-98b5-4e3d-9cc7-f38dd0908efd`（attempt_no 1） |
| ComfyUI prompt id | `82af3688-24ca-4cf5-abb3-0773f613e452` |
| 工作流档案 | `18e2f8d3-ca1d-4f0d-83ae-0bffc1b4b5f2`（`local-h3-ref2va-pdd8-2img`） |
| 图 SHA-256 | `86a7624e6eaeccd3a3c54cf5024317698cac64cbe083ed10dd158bcaa1e6eaa0` |
| 编译提示词 SHA-256 | `5b9fd73bf856421790b535815ca9fb8bf168716e77e41aa25b8134ab7f59d3c1` |
| 冻结风格 | Style Bible v1，`6cef48ac-e89e-45cb-8c87-b5d070d516f1`，sha `5204402d…0b990a` |
| 状态 | `NEEDS_REVIEW`，`failure_code: QC_NEEDS_REVIEW` |
| `quality_verification` | `real` |
| `reconciliation_state` | `collected` |
| 提交意图 / 提交回执 | 09:14:36.031 / 09:14:36.173（UTC） |

产物：

| 项 | 值 |
| --- | --- |
| Artifact ID | `3c9e8d66-3e44-4411-978f-b5e447185eac` |
| SHA-256 | `64a65e36287eeebfdb8b46c75374c5a99f27f9fa5175406a5321d5a48ede1515` |
| 字节 | 6,660,241 |
| 存储路径 | `artifacts/1f743836-a00c-4787-8343-4f1a24ed9135/ca3754cc-099e-41ce-bb5a-5aac121b7793/64a65e36…ede1515.mp4` |

ComfyUI 运行日志：

```
[INFO] MiniMaxH3PDDAccApply: partition check ok: ref2va file on ref2va model (fl2va 0.0504, ref2va 0.0017)
[INFO] MiniMaxH3PDDAccApply: MiniMax-H3-Ref2VA-Acc-8Step.safetensors (original) steps=8 blocks=4,4,4,4,4,4,4,4, 258 lora modules, heads fused
  0%|          | 0/8 [00:00<?, ?it/s,  Model Initializing ...  ]
 12%|█▏        | 1/8 [00:27<03:10, 27.15s/it,  Model Initialization complete!  ]
100%|██████████| 8/8 [03:11<00:00, 22.19s/it]
[INFO] Prompt executed in 246.06 seconds
```

本轮对该文件独立做的 FFprobe / FFmpeg 检查（不依赖系统自带的 QC）：

```powershell
ffprobe -v error -show_entries 'stream=codec_type,codec_name,width,height,r_frame_rate,nb_frames,sample_rate,channels' `
        -show_entries 'format=duration,size' -of default=noprint_wrappers=1 `
        '.local-acceptance\h3run\artifacts\1f743836-a00c-4787-8343-4f1a24ed9135\ca3754cc-099e-41ce-bb5a-5aac121b7793\64a65e36287eeebfdb8b46c75374c5a99f27f9fa5175406a5321d5a48ede1515.mp4'
ffmpeg -v error -i <同一文件> -f null -
```

实测输出：

```
codec_name=h264
codec_type=video
width=1344
height=768
r_frame_rate=24/1
nb_frames=175
codec_name=aac
codec_type=audio
sample_rate=32000
channels=2
duration=7.291000
size=6660241
ffmpeg exit=0
```

即：h264 / 1344x768 / 24 fps / 175 帧 / aac 32 kHz 立体声 / 7.291 秒，**全片解码
exit 0**。

这一版在 09:14 渲染，**早于**规则包冻结（09:38:09）与 Style Bible v2（09:39:17），
因此工作台拒绝为它签署：请求早于当前规则包锁、风格也不再是项目的冻结风格。它连同
产物原样保留为历史产物，没有被覆盖或删除。8.1 节记录的 ComfyUI 完成窗口缺陷也是在
这一版上暴露的（`reconciliation_state = collected`、`quality_verification = real`，
库中留下 `poll_after_ack / not_found` 与 `recover_history / completed` 两条对账记录）。

## 3. Shot B — `B-PDD8-7s`

被评审并签署的当前版本：

| 项 | 值 |
| --- | --- |
| Shot ID | `b89a0118-…`（revision 1，10:48:54 UTC 创建） |
| Job ID | `a2718d14-4a81-4046-8416-e16ce2b8598f` |
| Attempt ID | `40049d83-4c3e-424a-acb3-cf143a64406c`（attempt_no 1） |
| ComfyUI prompt id | `66484dc1-a4fe-4b61-ba5b-2e4e2298eb3d` |
| 工作流档案 | `01510286-83e7-4bde-ac2b-09332aeb1435`（`local-h3-ref2va-pdd8-2img-1aud`） |
| 图 SHA-256 | `ea979fba2d3ad02493ee28d7126a624ca8afde06b1895a0e81a6bd50a193ca64` |
| 编译提示词 SHA-256 | `dcd54635c677529acb0490cd8c5835a8090f6e4c56d49e2be9d3a709e70d786e` |
| 提示词图 SHA-256 | `58f51878de094c249cd2d05d021c6bfdc0407b3c51eb1196466b207fa6e2301a` |
| 冻结风格 | **Style Bible v2**，`5380cca5-efb1-4071-bf89-0f9604d41cf9`，sha `1909e253…22f636a8` |
| 状态 | `PASSED`，`quality_verification: manual_verified`，`failure_code: null` |
| `reconciliation_state` | `collected` |
| 提交意图 / 提交回执 | 10:48:54.810 / 10:48:54.938（UTC） |

产物：

| 项 | 值 |
| --- | --- |
| Artifact ID | `1b278511-fb66-46c5-8dbd-0c869913fc06` |
| SHA-256 | `4c6d8afeccb0fe1783e8749f195c68fa4b406c15fc8012c4b4caa4cc3dce10aa` |
| 字节 | 3,390,919 |
| 存储路径 | `artifacts/1f743836-a00c-4787-8343-4f1a24ed9135/a2718d14-4a81-4046-8416-e16ce2b8598f/4c6d8afe…3dce10aa.mp4` |

ComfyUI：`Prompt executed in 228.64 seconds`（8 步采样 `8/8 [03:06<00:00, 23.34s/it]`）。

独立 FFprobe / 解码实测：

```
codec_name=h264 / width=1344 / height=768 / r_frame_rate=24/1 / nb_frames=175
codec_name=aac   / sample_rate=32000 / channels=2
duration=7.291000
size=3390919
ffmpeg exit=0
```

音色参考与台词（与第一版一致，绑定未变）：

- 音色资产 `VOICE_LIANG`（`不用上班.wav`，3.587 s）以 `<Audio 1>` 标签进入引用清单，
  由 `VHS_LoadAudioUpload` 载入（档案的 `media_transfers.audio` 明确声明该节点类）。
- 中文台词「这里人太多，跟我来。」由**唯一批准的 H3 编译器**编进提示词的 `<d>` 对白块，
  时间轴 0.8 s → 3.0 s：

```
[Shot 1] ... At 00:00.800, <Subject 1> (S1) says: <d>[Chinese] 这里人太多，跟我来。</d>
```

- 音频门：`required = 1`。这一版由 worker 记录为 `not_run` + `ASR_NOT_CONFIGURED`
  （没有配置自动 ASR），随后由**操作员的人工逐项审阅**关闭为 `passed`（`listened = true`，
  台词逐字与口型由人工核对）。机器侧的 ASR 复核仍然没有配置。

### 3.1 Shot B 的第一版（已被取代，原样保留）

| 项 | 值 |
| --- | --- |
| Shot ID | `7799c9b6-af8d-4ad9-adfe-b3060a77332b`（revision 1） |
| Job ID | `dee0263c-737d-4dc3-9687-73dbe2768bf2` |
| Attempt ID | `e67b4b9c-e9dd-475f-8d1b-6ec6c6db75f3`（attempt_no 1） |
| ComfyUI prompt id | `8d2c3387-1a0c-4f70-9ccf-3e990dbbf7dd` |
| 工作流档案 | `01510286-83e7-4bde-ac2b-09332aeb1435`（`local-h3-ref2va-pdd8-2img-1aud`） |
| 图 SHA-256 | `ea979fba2d3ad02493ee28d7126a624ca8afde06b1895a0e81a6bd50a193ca64` |
| 编译提示词 SHA-256 | `41dca8ffc0e8a44c321a0239241ca8ce5a0f643bbf5d72b1808bf64c8f698e6a` |
| 冻结风格 | Style Bible v1，`6cef48ac-e89e-45cb-8c87-b5d070d516f1`，sha `5204402d…0b990a` |
| 状态 | `NEEDS_REVIEW`，`failure_code: QC_NEEDS_REVIEW` |
| `quality_verification` | `real` |
| `reconciliation_state` | `collected` |

产物：

| 项 | 值 |
| --- | --- |
| Artifact ID | `707dbc5e-8737-429b-a698-af018a09fe09` |
| SHA-256 | `40affe5911530104dded34ce3acee0ca50062a3da4ac9cd8ddd216e25b43a32e` |
| 字节 | 4,589,710 |

ComfyUI：`Prompt executed in 236.28 seconds`（8 步采样 `100%|...| 8/8 [03:13<00:00, 22.87s/it]`）。

独立 FFprobe / 解码实测：

```
codec_name=h264 / width=1344 / height=768 / r_frame_rate=24/1 / nb_frames=175
codec_name=aac   / sample_rate=32000 / channels=2
duration=7.291000
size=4589710
ffmpeg exit=0
```

音色参考与台词：

- 音色资产 `VOICE_LIANG`（`不用上班.wav`，3.587 s）以 `<Audio 1>` 标签进入引用清单，
  由 `VHS_LoadAudioUpload` 载入（档案的 `media_transfers.audio` 明确声明该节点类）。
- 中文台词「这里人太多，跟我来。」由**唯一批准的 H3 编译器**编进提示词的 `<d>` 对白块，
  时间轴 0.8 s → 3.0 s：

```
[Shot 1] ... At 00:00.800, <Subject 1> (S1) says: <d>[Chinese] 这里人太多，跟我来。</d>
```

- 音频门：`required = 1`，`status = not_run`，`error_code = ASR_NOT_CONFIGURED`。没有
  配置自动 ASR，所以这句台词**没有**被机器逐字核验；音轨存在且能解码，但措辞与口型
  仍需人工判断。

这一版同样在 09:24 渲染，**早于**规则包冻结与 Style Bible v2，因此工作台拒绝为它
签署。job `dee0263c-737d-4dc3-9687-73dbe2768bf2` 与产物 `707dbc5e-…`（SHA-256
`40affe5911530104…`，4,589,710 字节）原样保留为历史产物。

## 4. Shot C — `C-PDD8-7s`（依赖镜头）

| 项 | 值 |
| --- | --- |
| Shot ID | `64c9fde8-c409-4911-8c22-7852e2dfdc32`（revision 1） |
| Job ID | `ffffe1d8-86e7-4593-acaa-91ff453b6b31` |
| Attempt ID | `dd4eda10-f800-4cc6-a341-115dd8adfbe6`（attempt_no 1） |
| ComfyUI prompt id | `5deab8ed-3dd3-4e74-a97e-c619438ddbc8` |
| 工作流档案 | `18e2f8d3-ca1d-4f0d-83ae-0bffc1b4b5f2`（`local-h3-ref2va-pdd8-2img`） |
| 图 SHA-256 | `86a7624e6eaeccd3a3c54cf5024317698cac64cbe083ed10dd158bcaa1e6eaa0` |
| 编译提示词 SHA-256 | `e3acf6238bc022950253b8bd5cbc767586f0c9a975f77baf2570fe48e4698e1b` |
| 冻结风格 | **Style Bible v2**，`5380cca5-efb1-4071-bf89-0f9604d41cf9`，sha `1909e253…22f636a8` |
| 状态 | `PASSED`，`failure_code: null` |
| `quality_verification` | `manual_verified`（人工逐项审阅签署；技术门为真实 ffprobe 实测） |
| `reconciliation_state` | `collected` |

产物：

| 项 | 值 |
| --- | --- |
| Artifact ID | `c2946fa4-9913-4cf5-a295-41a864217782` |
| SHA-256 | `675c7822a15cc6366410816f1b6c3af7d1f321eccb28b0749ddc7f1674f7edaf` |
| 字节 | 4,414,119 |

ComfyUI：`Prompt executed in 220.59 seconds`（8 步 `100%|...| 8/8 [02:59<00:00, 20.71s/it]`）。

独立 FFprobe / 解码实测：

```
codec_name=h264 / width=1344 / height=768 / r_frame_rate=24/1 / nb_frames=175
codec_name=aac   / sample_rate=32000 / channels=2
duration=7.291000
size=4414119
ffmpeg exit=0
```

C 是三个镜头里**唯一没有重渲染**的一个：它在 09:39 创建，既晚于规则包冻结（09:38:09），
也晚于 Style Bible v2 冻结（09:39:17），因此从一开始就在当前配置下成立，也就没有
被"早于规则包锁或风格已变"这条闸门挡下。

连续性处理：

- 该镜头是**依赖镜头**：`continuity.mode = last_frame`，指向上一个已 `PASSED` 的 V1.2
  上游 job `b9670cca-8b08-40c3-8dd5-ea33ca50eaca`，引用清单里带的是已批准的末帧参考
  资产 `663d7728-…`（`FRAME_A_LAST`，`frame_role: last_frame`）。
- 库里存在显式的依赖边：`job_dependencies` 行
  `parent_job_id = b9670cca-8b08-40c3-8dd5-ea33ca50eaca` →
  `child_job_id = ffffe1d8-86e7-4593-acaa-91ff453b6b31`，`kind = continuity:last_frame`。
- 编译后的提示词用英文明确写出连续性意图：

```
The opening composition continues seamlessly from the approved final frame of shot faddbcf8-1e58-4269-a855-a2aceb03079a.
```

- 这只是**软参考连续性**（`SOFT_REFERENCE_CONTINUITY`）。档案声明
  `hard_continuity: PENDING_HARD_CONTINUITY_TEST`、
  `video_reference: SUPPORTED_NODE_UNVERIFIED_TRANSFER`；本轮没有任何东西把它升级成
  严格接续，也不能据此声称首帧严格相等。

## 5. 三个镜头汇总

| | Shot A | Shot B | Shot C |
| --- | --- | --- | --- |
| Job | `ba4bb63d…` | `a2718d14…` | `ffffe1d8…` |
| ComfyUI prompt | `83108e1f…` | `66484dc1…` | `5deab8ed…` |
| 档案 | pdd8-2img | pdd8-2img-1aud | pdd8-2img |
| ComfyUI 耗时 | 230.15 s | 228.64 s | 220.59 s |
| 采样 | PDD 8 步 | PDD 8 步 | PDD 8 步 |
| Artifact | `75173459…` | `1b278511…` | `c2946fa4…` |
| SHA-256 | `b0974210…` | `4c6d8afe…` | `675c7822…` |
| 字节 | 4,249,108 | 3,390,919 | 4,414,119 |
| 分辨率 / 帧率 | 1344x768 / 24 | 1344x768 / 24 | 1344x768 / 24 |
| 帧数 | 175 | 175 | 175 |
| 时长 | 7.291 s | 7.291 s | 7.291 s |
| 音轨 | aac 32 kHz 立体声 | aac 32 kHz 立体声 | aac 32 kHz 立体声 |
| 全片解码 | exit 0 | exit 0 | exit 0 |
| 风格版本 | v2 | v2 | v2 |
| 状态 | `PASSED` | `PASSED` | `PASSED` |
| `quality_verification` | `manual_verified` | `manual_verified` | `manual_verified` |

目标时长是 7.00 秒、容差 0.75 秒，实测 7.291 秒，在容差内。

A、B 的**第一版**（job `ca3754cc…` / `dee0263c…`，产物 `3c9e8d66…` / `707dbc5e…`，
风格 v1）见 2.1 与 3.1 节，它们原样保留为历史产物，未被覆盖或删除。

## 6. 五道质量门

每个镜头有**两组**门禁记录（`quality_gates`，全部 `is_mock = 0`）：worker 在渲染后写
第一组，操作员人工审阅后**追加**第二组。worker 的原始行没有被覆盖。

第一组（worker，机器门禁）：

| 门 | Shot A | Shot B | Shot C | 说明 |
| --- | --- | --- | --- | --- |
| `technical` | **passed** | **passed** | **passed** | `model: ffprobe`；每帧可解码、无超过 2 秒的静音段、时长与分辨率帧率均在容差内 |
| `semantic` | not_run | not_run | not_run | `SEMANTIC_REVIEW_NOT_CONFIGURED`（没有授权远程媒体评审的 VLM 档案，项目 `allow_remote_review = 0`） |
| `audio` | not_applicable | **required 且 not_run** | not_applicable | B：`ASR_NOT_CONFIGURED`；A/C：required = 0 |
| `style` | not_run | not_run | not_run | `STYLE_REVIEW_NOT_COVERED` |
| `final` | needs_review | needs_review | needs_review | `verification: real` |

第二组（操作员人工审阅，2026-10-25 11:30 UTC 追加）：

| 门 | Shot A | Shot B | Shot C | 说明 |
| --- | --- | --- | --- | --- |
| `technical` | **passed** | **passed** | **passed** | 与第一组同源：真实 FFprobe / FFmpeg 实测 |
| `semantic` | **passed** | **passed** | **passed** | 由**人工**逐项审阅关闭，**不是** VLM 评审 |
| `audio` | not_applicable | **passed** | not_applicable | A/C 的 shot spec 无对白，音频门非必需；B 由人工试听与逐字核对关闭 |
| `style` | **passed** | **passed** | **passed** | 由**人工**对照冻结的 Style Bible v2 关闭 |
| `final` | **passed** | **passed** | **passed** | 操作员签署全部必需分项后才置为 passed |

**明确说明**：语义、风格、终审这三道门，以及 B 的音频门，**从来没有任何模型评审过**
—— 本轮没有配置远程 VLM，也没有配置 ASR；它们是由操作员的人工逐项审阅关闭的。
`final` 为 `passed`，是因为操作员把该镜头**全部必需分项**都签了；`technical` 为
`passed`，来自真实 FFprobe / FFmpeg 实测（`model: ffprobe`，`is_mock: 0`）。

对应的人工评审记录（`OPERATOR_REVIEW_RECORDED`，actor `shine-owner`）：

| Shot | request_id | watched | listened | 分项 |
| --- | --- | --- | --- | --- |
| A | `op-review-20261025-A` | true | false | 7 项 PASS（identity / costume / scene_props / camera_action / style / continuity / other），dialogue / voice / lip_sync 为 `NOT_APPLICABLE` |
| B | `op-review-20261025-B` | true | true | 10 项全部 PASS |
| C | `op-review-20261025-C` | true | false | 7 项 PASS，dialogue / voice / lip_sync 为 `NOT_APPLICABLE` |

A 与 C 的对白三项标 `NOT_APPLICABLE`，原因是这两个镜头的 shot spec 里没有对白，
系统内的 audio 门对它们本就是 `not_applicable` 且非必需 —— 不是没看，而是没有这项
可看。B 是唯一有对白的镜头，因此它的 `listened` 为 true，台词「这里人太多，跟我来。」
由人工逐字核对并判定口型同步。

审片接口返回 HTTP 200（`{"status":"PASSED","result":"passed","source":"manual"}`），
同时追加一条 `reviewer = operator-manual` 的 QC 报告（`result = passed`）；worker 的
`reviewer = quality-gates` / `result = needs_review` 报告原样保留。

对照：V1.2 的上游 job `b9670cca-8b08-40c3-8dd5-ea33ca50eaca` 在 2026-10-21 的人工
评审中同样拿到了 `semantic`、`style` 与 `final` 的 passed。

## 7. Style Bible 与冻结规则包的一次真实交互

这是本轮最有价值的产品级证据：规则真的拒绝了请求，而且没有任何东西被悄悄改写。

1. 项目规则包被冻结：`POST /api/v1/projects/{id}/rulepack/freeze` →
   `shine-h3-rules/1.1.0`，hash
   `5990ae18159f96349022972cf07c78294e17ec4bf89140ef417a5cbbac0bdd28`
   （`project_rule_locks` 一行，`frozen_at 2026-10-25 09:38:09`）。

2. 官方 H3 规则 `H3_ENGLISH` 随后拒绝了下一个镜头，HTTP **422**：

   - 原因：`CJK found outside dialogue or quoted visible text`
   - 补救建议：`Describe visuals in English; preserve spoken words and visible text in their original language.`

   根因：冻结的 Style Bible v1（sha `5204402dccbae251…`，人工于 2026-10-21 批准）用
   **中文**书写七个视觉维度，而编译器会原样把它们嵌进提示词的 `detailed_description`。

3. Shot A 和 Shot B 是在规则包冻结**之前**创建的，因此**编译**时不受影响。Shot C 在冻结
   **之后**创建，被正确拒绝 —— 这正是设计中的行为：规则被执行，没有静默重写，响应里
   点名了违规规则、定位位置和补救方式。

4. 按补救建议，冻结了 **Style Bible v2**（`5380cca5-efb1-4071-bf89-0f9604d41cf9`，sha
   `1909e2534f4f0e0c1e09a61c11ccb37803614acf6b69e69c23a1c59222f636a8`），同样七个维度
   改用英文表达。v1 **未被改动**。

   | 版本 | 维度表达 | 引用它的镜头 |
   | --- | --- | --- |
   | v1 | 中文 | A（第一版）、B（第一版） |
   | v2 | 英文 | C、A（当前版）、B（当前版） |

5. 风格影响报告：`affected_shot_count = 9`，`invalidated_job_count = 0`。已有的通过版本
   与产物没有被覆盖。

6. Shot C 随后针对 v2 编译并渲染成功。

7. **审片阶段的连锁后果**：工作台不会为"请求早于当前规则包锁"、或"风格不再是项目
   冻结风格"的 job 签署。A 与 B 的第一版正好落在这两条之外（09:14 与 09:24 创建，
   规则包 09:38 冻结，Style Bible v2 09:39 冻结），因此它们**不能**被签署，在当前
   配置下重渲染之后（10:44 与 10:48）才成为被评审与签署的版本。第一版的 job 与产物
   原样保留为历史产物，没有被覆盖，也没有被删除。C 因为两个变更之后才创建，从头
   就成立，不需要重渲染。

留给操作员的可选项：如果更希望自己重新撰写 Style Bible，这被列为一项待办
（见 [V1.3 收尾报告](LOCAL_V1.3_CLOSEOUT_REPORT.md) 第 8 节）。

## 8. 本轮发现并修复的缺陷

### 8.1 ComfyUI 完成窗口丢弃了一次成功的渲染

**现象**：第一个真实镜头（Shot A）在 worker 里被判失败，`failure_code =
COMFY_JOB_NOT_FOUND`，产物从未被收集。

**根因**：ComfyUI 会先把完成的 prompt 从队列里移除，之后对应的 history 记录才可读。
worker 恰好在这个窗口里采样了一次：`/history/<id>` 为空且 prompt 已不在队列中，于是
把"单次观测到没有记录"当成了"渲染已经不存在"的证明。而这次 ComfyUI 渲染其实**成功**
了（日志 `Prompt executed in 246.06 seconds`，MP4 已落盘）。

**修复**（`backend/shine/worker.py`）：

- 缺失外部记录在 `EXTERNAL_MISSING_GRACE_SECONDS = 30` 秒内视为**暂定**，之后才判定
  提交丢失；
- 失败码 `COMFY_JOB_NOT_FOUND` 归入 `RECONCILABLE_FAILURE_CODES`：这类滞留 job 由
  worker 认领后**对照 ComfyUI history 重新对账**，而不是被丢弃或盲目重投；
- 新增 `_claim_stranded_job()`，用 compare-and-set 保证只有一个 worker 认领。

**影响**：被滞留的真实 job 在修复后的 worker 重启时完成对账，产出产物
`3c9e8d66…`，`reconciliation_state = collected`、`quality_verification = real`。
库中留下两条对账记录：`poll_after_ack / not_found`（09:18:42）与
`recover_history / completed`（09:22:37）。

**回归**：`backend/tests/test_comfy_completion_window.py`（2 项）。

### 8.2 GPU 模型释放确认在 ComfyUI 0.39 上永远不可能成功

**现象**：独占 GPU 租约长期停在 `RECONCILE_REQUIRED`，后续所有 job 被挡住，队列死锁。

**根因**：`backend/shine/comfy/client.py` 原来比较 `torch_vram_total <=
torch_vram_free`。在 ComfyUI 0.39 上，这两个数分别是 torch 分配器的**已保留池**与
**已保留但未分配的部分**，因此"空闲部分"永远不可能大于"总量"，这个条件永远为假。
系统当时做的是安全的事（拒绝认定一个从未被证明的释放），但永远无法推进。

**修复**：确认逻辑改为**版本感知**。

- 0.39 及更新：比较两者之差（即活跃张量字节数）与
  `MAX_LIVE_TENSOR_BYTES = 512 MiB`。一个驻留的 MiniMax-H3 会让这个差值变化超过
  10 GB，两侧余量都很宽。
- 更老的版本：保留原来的严格规则。
- 读数未知或无法解析：继续维持阻断，不假定已释放。

**本机实测**：模型驻留时该差值是 GB 量级；执行 `/free` 之后渲染器报告 67,108,864 已
保留 / 49,017,344 未分配（差值 17.25 MiB），同时 `nvidia-smi` 显示 16,303 MiB 中已用
1,489 MiB。

写文档时又取了一次实时 `/system_stats`（实例未被改动），读数与上面一致：

```
device: cuda:0 NVIDIA GeForce RTX 5080 : cudaMallocAsync
        vram_total= 17066360832  vram_free= 15560602112
        torch_vram_total= 67108864  torch_vram_free= 49017344
```

即空闲部分（49,017,344）比总量（67,108,864）**小** 18,091,520 字节（17.25 MiB）——
旧的 `total <= free` 判定在此永远为假，而修复后的"差值 < 512 MiB"判定通过。这正是该
缺陷在本机上的活证据。

**回归**：`backend/tests/test_gpu_release_confirmation.py`（6 项）。

### 8.3 陈旧的独占租约可以锁死队列

**现象**：租约清扫只检查"正在被恢复的那些 job"的租约，一个 job 已经结束、租约却还在
的记录永远不会被回看。

**修复**（`backend/shine/worker.py`）：清扫现在遍历**所有** `ACTIVE` /
`RECONCILE_REQUIRED` 租约；当租约的 job 不再是活动状态且它的 attempt 已 `COMPLETED`
时，按渲染器的真实内存状态释放它。渲染器正忙（`INSTANCE_BUSY`）时保留租约、稍后重试；
其它异常转 `RECONCILE_REQUIRED`；无法证明释放时不假定已释放。

**本机实测**：第一个真实 job 的租约从 `RECONCILE_REQUIRED` 变为 `RELEASED`，下一个 job
立即被放行。库里三条 H3 租约最终状态全部为 `RELEASED`：

| 租约 ID | 归属 job | 状态 |
| --- | --- | --- |
| `0702da59-4ed4-4897-965c-efc147e7c6c6` | `h3:ca3754cc…` | RELEASED |
| `950ac8cc-fdc6-4cb9-b7dc-3cf817f970ef` | `h3:dee0263c…` | RELEASED |
| `b79a898f-7a65-4329-84e7-0ce58608b066` | `h3:ffffe1d8…` | RELEASED |

### 8.4 LLM 网关从未把 JSON 契约交给模型

**现象**：剧组的四个角色（`scriptwriter` / `editor` / `producer` / `comfyops`）的真实调用
被以 `LLM_FORMAT` 拒绝（HTTP 422）。指令要求模型"只返回所给的 JSON 契约"，而**契约
从未被交给模型**。

**根因**：`LLMGateway.complete_for_role` 只把 schema 交给服务商做校验，模型只拿到上下文，
必须自己猜答案的形状。Mock 服务商用罐头响应作答，把这个问题掩盖了；真实服务商按上下文
作答，于是过不了 schema 校验。

**修复**：把 schema 追加到请求末尾，使不可信上下文无法把它挤掉。

**实测**：对授权的 MiniMax 端点实跑，`scriptwriter` 返回 HTTP **201**，产出有效的
`ScriptBreakdown`。根因、修复与边界详见
[V1.3 收尾报告 5.1](LOCAL_V1.3_CLOSEOUT_REPORT.md#51-llm_format-的根因与修复本轮后续提交)。

### 8.5 启动脚本记的是启动器 pid，而不是占用端口的那个 pid

**现象**：停止之后重启，下一次启动在绑定端口时以 `Errno 10048` 失败，而旧的服务仍在
端口上按**旧的操作员凭据**应答。表现出来的症状是：凭据本身是对的，操作员审片却被
HTTP 403 拒绝。

**根因**：uv 管理的虚拟环境会把基础解释器作为**子进程**重新执行，启动器记下的 pid 与
真正监听端口的 pid 不是同一个。停止时只结束了启动器，真正的服务活了下来。

**修复**：

- `scripts/start_shine.ps1` 在启动前检查端口是否被占用；被占用就**拒绝启动**并给出
  停止命令，不再让新进程带着绑定错误死掉；
- 启动脚本把**真实监听者**的 pid（`listener_pid`）写进 `processes.json`；
- `scripts/stop_shine.py` 结束该监听进程，并在端口仍被占用时打印告警。

### 8.6 收尾审片阶段对租约清扫的复验

收尾审片阶段再次确认了 8.3 描述的那一处清扫修复：**每一个** `ACTIVE` /
`RECONCILE_REQUIRED` 租约都会被扫到，当租约的 job 不再是活动状态、且它的 attempt 已
`COMPLETED` 时，按渲染器的真实内存状态释放。两个重渲染 job 的租约最终同样为
`RELEASED`：

| 租约 ID | 归属 job | 状态 |
| --- | --- | --- |
| `89899faf-49e9-491a-bc0b-ec2055fed9ed` | `h3:ba4bb63d…` | RELEASED |
| `3630aaec-01ce-429b-a501-d84908797eea` | `h3:a2718d14…` | RELEASED |

这与 8.3 是**同一处修复**，不是另一处缺陷；此处只记录收尾轮的复验结果。库里五条
H3 租约最终全部为 `RELEASED`。

## 9. 交付样片（EDL → 可播放成片）

本轮新增了把编辑决策清单变成真实文件的能力：`backend/shine/media/assembly.py` 与
`backend/shine/api_delivery.py`。

- `POST /api/v1/projects/{id}/deliveries`：接受编辑 Agent 的 EDL，用 FFmpeg 逐段裁剪、
  归一到统一画布与帧率后拼接；结果按内容寻址存放在
  `artifacts/<project>/delivery/`，所以交付产物**不可能**覆盖已通过的镜头产物。
- 渲染前对每个来源重新校验：同一项目、非 stale job 的当前 attempt、**已通过的真实
  technical 门**、落盘文件 SHA-256 仍匹配、in/out 落在实测时长内。
- 成片记录每个来源 job 的状态，未评审的镜头不会被当作已验收；只有全部来源 job 都是
  `PASSED` 时 `review_state` 才是 `all_passed`。
- `GET /api/v1/deliveries/{artifact_id}/media` 输出成片。

本轮实际装配结果（最终成片，2026-10-25 11:30 审片后重装）：

| 项 | 值 |
| --- | --- |
| Artifact ID | `e6b264aa-bd7a-465c-9e43-4092b69b8266` |
| SHA-256 | `4ce496542626c0036e44227cae9c5e28d44c734a3b89bb0e6fe76e925d7fb98e` |
| 字节 | 10,893,258 |
| 存储路径 | `artifacts/1f743836-a00c-4787-8343-4f1a24ed9135/delivery/4ce49654…d7fb98e.mp4` |
| 段序 | **A → B → C**，每段 `in_s 0.0 → out_s 7.29` |
| 来源 job | `ba4bb63d…` / `a2718d14…` / `ffffe1d8…`，全部 `PASSED` |
| `review_state` | **`all_passed`** |

独立 FFprobe / 解码实测：

```
codec_name=h264 / width=1344 / height=768 / r_frame_rate=24/1 / nb_frames=525
codec_name=aac   / sample_rate=48000 / channels=2
duration=21.895996
size=10893258
ffmpeg exit=0
```

即 **21.896 秒、525 帧（3 x 175）**、1344x768、24 fps，**恰好一条** h264 视频流与
**一条** aac 音频流（48 kHz 立体声），**全片解码 exit 0**。成片内含来源 job id、
attempt id、artifact id、artifact sha256 与来源 job 状态，因此每一段都可追溯。
`review_state` 是 `all_passed`，是因为三个来源 job 都已经是 `PASSED` —— 它不是被
单独置上的标记。

先前的几条成片仍在原处，未被覆盖（交付是内容寻址的）：

| Artifact ID | SHA-256 | 字节 | 段序 | `review_state` |
| --- | --- | --- | --- | --- |
| `e820bff2-…` | `eeeabdb8…09bac` | 14,085,974 | C → B → A | `pending_review` |
| `110f47e9-…` | `db8e65e8…d41e4` | 14,085,974 | A → B → C | `pending_review` |
| `d67733d1-…` | `4ce49654…d7fb98e` | 10,893,258 | A → B → C | `pending_review` |

前两条用的是 A、B 的**第一版**产物；第三条用的是重渲染后的 A、B，但装配时三个来源
job 还没有被审片签署，因此同样是 `pending_review`。最终成片与第三条的**内容哈希完全
相同**（`4ce49654…`），差别只在来源状态：交付按内容寻址，成片文件没有变。

导出的样片副本：`.local-acceptance/media/delivery-sample.mp4`，实测 SHA-256 与登记的
artifact hash 相等：

```powershell
(Get-FileHash -Algorithm SHA256 '.local-acceptance\media\delivery-sample.mp4').Hash.ToLower()
# 4ce496542626c0036e44227cae9c5e28d44c734a3b89bb0e6fe76e925d7fb98e
```

该文件**刻意不进入 Git**（`.gitignore` 含 `/.local-acceptance/` 与 `*.mp4`）。

**回归**：`backend/tests/test_delivery_assembly.py`（6 项），覆盖可播放成片、跨片段
单音轨、来源产物不被改动，以及技术门缺失、来源文件被篡改、片段超出源时长、跨项目
产物这四种拒绝路径。

## 10. 明确未覆盖的项

| 项 | 状态 | 说明 |
| --- | --- | --- |
| 语义 / 风格 / 终审门的**模型**覆盖 | `BLOCKED / PENDING` | 仍然没有 VLM 评审；这三道门是由操作员的人工逐项审阅关闭的，不是模型覆盖 |
| 音频门的**机器**复核 | `BLOCKED / PENDING` | 仍未配置 ASR；B 的台词与口型由人工试听、逐字核对 |
| 远程媒体评审 | 未启用 | `allow_remote_review = 0`，没有任何媒体离开本机；只有剧组的文本提示词在授权下发往 MiniMax 端点 |
| 严格首尾帧锚定 | `PENDING_HARD_CONTINUITY_TEST` | Shot C 只是软参考 |
| 视频参考传输 | `SUPPORTED_NODE_UNVERIFIED_TRANSFER` | 未验证 |
| 480x864 历史样本 | 仍然拒绝用于正式画质 | 历史结论不改写；新镜头为 1344x768 |
| 多模型 GPU 争用 | 未覆盖 | 只跑了一个本地模型家族 |
| 全片无人值守完成 | 未验证 | 本轮是人工驱动的三镜头序列，不是全自动流程 |
| 3 段以上长片段 | 未验证 | 单段 7 秒 |

## 11. 结论分类

| 结论 | 类别 |
| --- | --- |
| Shot A / B / C 为真实本地 ComfyUI + 本地 MiniMax-H3 产物，产物哈希与落盘一致 | `VERIFIED_REAL_H3` |
| 三个镜头的 technical 门以 ffprobe 真实通过（1344x768 / 24 fps / 175 帧 / 7.291 s） | `VERIFIED_REAL_H3` |
| 独立 `ffmpeg -f null -` 全片解码 exit 0（三个镜头与成片） | `VERIFIED_LOCAL_ENV` |
| 交付样片为真实 FFmpeg 装配产物：21.896 s / 525 帧 / 单视频流 + 单音频流 / `all_passed` | `VERIFIED_REAL_H3` |
| 规则包冻结后 `H3_ENGLISH` 以 HTTP 422 拒绝中文 Style Bible，未发生静默重写 | `VERIFIED_REAL_H3` |
| Style Bible v1 未被改动，v2 以英文冻结，风格影响 9 个镜头 / 0 个 job 失效 | `VERIFIED_REAL_H3` |
| A、B 在规则包冻结与 Style Bible v2 之后重渲染并被签署，第一版 job 与产物原样保留 | `VERIFIED_REAL_H3` |
| Shot C 作为依赖镜头带显式 `continuity:last_frame` 依赖边，仅软参考连续性 | `VERIFIED_REAL_H3` |
| 操作员 `shine-owner` 通过真实操作员 API 逐项审阅并签署三个镜头（`OPERATOR_REVIEW_RECORDED` × 3，`watched` 全为 true，B 另有 `listened = true`） | `VERIFIED_MANUAL` |
| 三个镜头的 semantic / style / final 门与 B 的 audio 门由**人工**关闭；`final = passed` 因为全部必需分项被签署，`technical = passed` 来自真实 ffprobe/ffmpeg | `VERIFIED_MANUAL` |
| 交付样片 `review_state = all_passed`，三个来源 job 全部 `PASSED` | `VERIFIED_MANUAL` |
| ComfyUI 完成窗口、GPU 释放确认、陈旧租约三个缺陷已定位、修复并有回归测试 | `IMPLEMENTED` / `TESTED_OFFLINE` |
| LLM 网关未下发 JSON 契约、启动脚本记错监听 pid 两个缺陷已定位、修复并有实测 | `IMPLEMENTED` / `TESTED_OFFLINE` |
| 交付装配能力（EDL → 成片）与来源再校验 | `IMPLEMENTED` / `TESTED_OFFLINE` |
| 语义 / 风格 / 终审门与音频门**没有模型覆盖**，只由人工关闭 | `BLOCKED / PENDING` |
| 严格连续性、视频参考传输、多模型 GPU 争用 | `BLOCKED / PENDING` |
