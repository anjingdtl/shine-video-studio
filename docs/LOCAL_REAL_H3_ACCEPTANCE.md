# 本地真实 MiniMax-H3 验收报告（V1.3 轮）

日期：2026-10-25（Asia/Shanghai）。范围：本轮在操作员机器上用真实本地 ComfyUI +
本地 MiniMax-H3 Ref2VA（INT8 ConvRot + PDD 8 步）跑出的三个镜头，以及由这三个真实
镜头装配出的交付样片。

关联文档：[本地环境验证报告](LOCAL_ENVIRONMENT_VALIDATION.md)、
[V1.3 收尾报告](LOCAL_V1.3_CLOSEOUT_REPORT.md)、[操作员指南](operator-guide.md)。
证据：`.local-acceptance/evidence/acceptance-evidence.json`、
`.local-acceptance/logs/comfyui-stderr.log`、`.local-acceptance/h3run/`。

---

## 0. 结论总览

- 本轮产出 **3 个真实 H3 镜头**（A / B / C，均 1344x768、24 fps、175 帧、7.291 秒）和
  **1 个可播放的交付样片**（21.896 秒、525 帧）。
- 三个镜头的**技术门全部真实通过**（`model: ffprobe`，`is_mock: 0`）。
- 三个镜头的**语义门、风格门、终审门都没有模型覆盖，也都没有人工签署**；有对白的
  B 镜头的**音频门是必需但未运行**（`ASR_NOT_CONFIGURED`）。因此三个镜头全部停在
  `NEEDS_REVIEW`，没有任何一个被标记为 `PASSED`。
- 交付样片的 `review_state` 是 `pending_review`，不是 `all_passed`。
- 本轮暴露并修复了三个只有真实 ComfyUI 才会遇到的缺陷（第 8 节）。

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

## 3. Shot B — `B-PDD8-7s`

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
| 状态 | `NEEDS_REVIEW`，`failure_code: QC_NEEDS_REVIEW` |
| `quality_verification` | `real` |
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
| Job | `ca3754cc…` | `dee0263c…` | `ffffe1d8…` |
| ComfyUI prompt | `82af3688…` | `8d2c3387…` | `5deab8ed…` |
| 档案 | pdd8-2img | pdd8-2img-1aud | pdd8-2img |
| ComfyUI 耗时 | 246.06 s | 236.28 s | 220.59 s |
| 采样 | PDD 8 步 | PDD 8 步 | PDD 8 步 |
| Artifact | `3c9e8d66…` | `707dbc5e…` | `c2946fa4…` |
| SHA-256 | `64a65e36…` | `40affe59…` | `675c7822…` |
| 字节 | 6,660,241 | 4,589,710 | 4,414,119 |
| 分辨率 / 帧率 | 1344x768 / 24 | 1344x768 / 24 | 1344x768 / 24 |
| 帧数 | 175 | 175 | 175 |
| 时长 | 7.291 s | 7.291 s | 7.291 s |
| 音轨 | aac 32 kHz 立体声 | aac 32 kHz 立体声 | aac 32 kHz 立体声 |
| 全片解码 | exit 0 | exit 0 | exit 0 |
| 风格版本 | v1 | v1 | v2 |
| 状态 | `NEEDS_REVIEW` | `NEEDS_REVIEW` | `NEEDS_REVIEW` |

目标时长是 7.00 秒、容差 0.75 秒，实测 7.291 秒，在容差内。

## 6. 五道质量门

三个镜头的门禁记录（`quality_gates`，全部 `is_mock = 0`）：

| 门 | Shot A | Shot B | Shot C | 说明 |
| --- | --- | --- | --- | --- |
| `technical` | **passed** | **passed** | **passed** | `model: ffprobe`；每帧可解码、无超过 2 秒的静音段、时长与分辨率帧率均在容差内 |
| `semantic` | not_run | not_run | not_run | `SEMANTIC_REVIEW_NOT_CONFIGURED`（没有授权远程媒体评审的 VLM 档案，项目 `allow_remote_review = 0`） |
| `audio` | not_applicable | **required 且 not_run** | not_applicable | B：`ASR_NOT_CONFIGURED`；A/C：required = 0 |
| `style` | not_run | not_run | not_run | `STYLE_REVIEW_NOT_COVERED` |
| `final` | needs_review | needs_review | needs_review | `verification: real` |

**明确说明**：语义、音频、风格、终审这四道门在本轮**没有**任何模型覆盖，也**没有**
人工签署。缺失的评审被记为"缺失"，绝没有被记为通过。三个镜头因此都是
`NEEDS_REVIEW`，QC 报告 `result = needs_review`、`reviewer = quality-gates`（机器门禁
记录，不是人工评审记录）。

对照：V1.2 的上游 job `b9670cca-8b08-40c3-8dd5-ea33ca50eaca` 在历史人工评审中拿到了
`semantic`、`style` 与 `final` 的 passed（其 `OPERATOR_REVIEW_RECORDED` 事件时间为
2026-10-21），状态为 `PASSED`。本轮三个新镜头**没有**对应的人工评审事件。

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

3. Shot A 和 Shot B 是在规则包冻结**之前**创建的，因此不受影响。Shot C 在冻结**之后**
   创建，被正确拒绝 —— 这正是设计中的行为：规则被执行，没有静默重写，响应里点名了
   违规规则、定位位置和补救方式。

4. 按补救建议，冻结了 **Style Bible v2**（`5380cca5-efb1-4071-bf89-0f9604d41cf9`，sha
   `1909e2534f4f0e0c1e09a61c11ccb37803614acf6b69e69c23a1c59222f636a8`），同样七个维度
   改用英文表达。v1 **未被改动**，仍然是 Shot A / Shot B 所引用的版本。

   | 版本 | 维度表达 | 引用它的镜头 |
   | --- | --- | --- |
   | v1 | 中文 | A、B |
   | v2 | 英文 | C |

5. 风格影响报告：`affected_shot_count = 9`，`invalidated_job_count = 0`。已有的通过版本
   与产物没有被覆盖。

6. Shot C 随后针对 v2 编译并渲染成功。

留给操作员的可选项：如果更希望自己重新撰写 Style Bible，这被列为一项待办
（见 [V1.3 收尾报告](LOCAL_V1.3_CLOSEOUT_REPORT.md) 第 8 节）。

## 8. 本轮发现并修复的三个缺陷

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

本轮实际装配结果：

| 项 | 值 |
| --- | --- |
| Artifact ID | `110f47e9-34e1-48d8-a562-defc5f43bf1b` |
| SHA-256 | `db8e65e8db564f9ef7d7ec4b9d06862af62d20f65abbbf8a2e9e4c80c57d41e4` |
| 字节 | 14,085,974 |
| 存储路径 | `artifacts/1f743836-a00c-4787-8343-4f1a24ed9135/delivery/db8e65e8…c57d41e4.mp4` |
| 段序 | **A → B → C**，每段 `in_s 0.0 → out_s 7.29` |
| `review_state` | `pending_review` |

独立 FFprobe / 解码实测：

```
codec_name=h264 / width=1344 / height=768 / r_frame_rate=24/1 / nb_frames=525
codec_name=aac   / sample_rate=48000 / channels=2
duration=21.895996
size=14085974
ffmpeg exit=0
```

即 **21.896 秒、525 帧（3 x 175）**、1344x768、24 fps，**恰好一条** h264 视频流与
**一条** aac 音频流（48 kHz 立体声）。成片内含来源 job id、attempt id、artifact id、
artifact sha256 与来源 job 状态，因此每一段都可追溯。

另一次装配用了**错误的段序**（C → B → A），得到不同的成片
`eeeabdb8d8e5d223cc5d28a66199a0d6d6099e7bb82a5919074cf08a73809bac`（同为 14,085,974
字节）。两个成片都保留 —— 交付路径是内容寻址的，先前的成片没有被覆盖。

导出的样片副本：`.local-acceptance/media/delivery-sample.mp4`，实测 SHA-256 与登记的
artifact hash 相等：

```powershell
(Get-FileHash -Algorithm SHA256 '.local-acceptance\media\delivery-sample.mp4').Hash.ToLower()
# db8e65e8db564f9ef7d7ec4b9d06862af62d20f65abbbf8a2e9e4c80c57d41e4
```

该文件**刻意不进入 Git**（`.gitignore` 含 `/.local-acceptance/` 与 `*.mp4`）。

**回归**：`backend/tests/test_delivery_assembly.py`（6 项），覆盖可播放成片、跨片段
单音轨、来源产物不被改动，以及技术门缺失、来源文件被篡改、片段超出源时长、跨项目
产物这四种拒绝路径。

## 10. 明确未覆盖的项

| 项 | 状态 | 说明 |
| --- | --- | --- |
| 语义 / 音频 / 风格 / 终审门 | `BLOCKED / PENDING` | 无模型覆盖，无人工签署 |
| 正式逐项人工审片 | `BLOCKED / PENDING` | 三个新镜头没有 `OPERATOR_REVIEW_RECORDED` |
| 台词逐字准确性、口型同步 | `BLOCKED / PENDING` | 未配置 ASR；需人工完整观看+试听 |
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
| 交付样片为真实 FFmpeg 装配产物：21.896 s / 525 帧 / 单视频流 + 单音频流 / `pending_review` | `VERIFIED_REAL_H3` |
| 规则包冻结后 `H3_ENGLISH` 以 HTTP 422 拒绝中文 Style Bible，未发生静默重写 | `VERIFIED_REAL_H3` |
| Style Bible v1 未被改动，v2 以英文冻结，风格影响 9 个镜头 / 0 个 job 失效 | `VERIFIED_REAL_H3` |
| Shot C 作为依赖镜头带显式 `continuity:last_frame` 依赖边，仅软参考连续性 | `VERIFIED_REAL_H3` |
| ComfyUI 完成窗口、GPU 释放确认、陈旧租约三个缺陷已定位、修复并有回归测试 | `IMPLEMENTED` / `TESTED_OFFLINE` |
| 交付装配能力（EDL → 成片）与来源再校验 | `IMPLEMENTED` / `TESTED_OFFLINE` |
| 语义 / 音频 / 风格 / 终审门未覆盖、未签署，镜头停在 `NEEDS_REVIEW` | `BLOCKED / PENDING` |
| 正式逐项人工审片 | `BLOCKED / PENDING` |
| 严格连续性、视频参考传输、多模型 GPU 争用 | `BLOCKED / PENDING` |
