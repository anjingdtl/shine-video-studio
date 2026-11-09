# W6A8 · 低显存候选档（导演模式）实现与验收报告

日期：2026-11-09
分支：`codex/w6a8-lowend-variant`（基线 `888956edeac6e98f032f0e883e3e83d1a7ca92bb`）
平台：Windows x64 · 本机 `F:\ClaudeWorkSpace\projects\shine-video-studio`

---

## 1. 范围与结论

本轮把**官方 W6A8 低显存候选**接进导演模式：它在「基础配置 → 模型与生成方案」里有自己的条目、自己的五层就绪状态和**一个**下一步；它在排产区有自己的质量档，并在本机真的能跑之前保持禁用、被拒时具名说明原因。

**结论一览**

| 项 | 状态 | 证据 |
| --- | --- | --- |
| 配置入口（登记 / 校验 / 就绪 / 下一步） | 已实现并真机核对 | §5、§7 |
| 生产选档门禁（服务端 + 浏览器） | 已实现，候选在本机为禁用 | §4、§7 |
| 同一身份链（方案 → 导演包 → 系列 → Job） | 已实现并离线验证 | §3、§6 |
| 原有 INT8 默认路径 | 未改动，已回归 | §6 |
| **4070 Super 12GB 真机出片** | **BLOCKED**（无该硬件 + 无已批准权重） | §8 |
| 5080 上的 W6A8 出片 | **BLOCKED**（同上：权重未就位，候选不进自动下载路径） | §8 |
| VLM 正负校准 | 未做（本轮未改 `client_review_identity` 钉住的文件） | §9 |

---

## 2. 复用的三个对象与它们的状态

| 对象 | 状态 | 本轮改动 |
| --- | --- | --- |
| `config/deployment/variants/ref2va-lowend-candidate.json` | 变体清单 | **补 `checkpoint_id` 与 `graph_sha256`**，其余不变（见下） |
| `config/deployment/h3-ref2va-lowend-candidate-windows-x64.json` | 部署目录 | 未改（已声明 `variant_id` 与 `workflow_profiles`） |
| `config/workflows/ref2va-lowend-pdd-free-2img.json` | 工作流图 | 未改 |

### 2.1 变体清单为什么必须补两处

清单原文把 `checkpoint_id` 写成 `unknown`，理由是「要等这一变体拥有独立工作流图并被钉定后才能写下来」。**这个条件现在已经满足**：该变体有独立的 PDD-free 20 步工作流图，且该图与官方权重都已钉住。所以：

- `checkpoint_id` → `Minimax-H3\minimax_h3_ref2va_pruned_w6a8.safetensors`（与工作流图里 `UNETLoader.unet_name` 一致）；
- `graph_sha256` → `{"local-h3-ref2va-lowend-pdd-free-2img": "596d4891…f715"}`（该方案图的 `graph_hash`）。

补上之后，`shine/comfy/capabilities.py::profile_metadata` 才能从图反查出变体身份，注册出来的方案因此带：

```
variant_id = ref2va-lowend-candidate
variant_acceptance_status = CANDIDATE
variant_verified = false
```

`variant_verified` 仍是 `false`：候选变体没有真机验收，**不继承** INT8 基线的验收结论。清单里对应的 `unknown_fields` 条目同时删除，`variants.unknown_paths == declared_unknown_paths` 仍然成立。

**未改动的清单字段**：`acceptance_status` 仍是 `CANDIDATE`、`default_recommended` 仍是 `false`、`auto_download_allowed` 仍是 `false`、`source` 仍是 `OFFICIAL_PINNED`、`tier` 仍是 `lowend_12gb`。

---

## 3. 身份链

```
变体清单 ref2va-lowend-candidate
  └─ catalog_id  h3-ref2va-lowend-candidate-windows-x64
       └─ workflow_profiles  local-h3-ref2va-lowend-pdd-free-2img
            └─ 图 graph_sha256 596d4891…f715 · parameter_map 7 键 · media_transfers {image}
                 └─ WorkflowProfile（项目作用域）
                      model_identity.variant_id = ref2va-lowend-candidate
                      checkpoint_id / graph_sha256 / parameter_map / media_transfers
                       └─ 导演包 binding（同一 id + 同一图哈希 + 同一 checkpoint）
                            └─ ProductionSeries.workflow_profile_id
                                 └─ Job.workflow_profile_id
                                      └─ Worker → ComfyUI /prompt（同一 parameter_map）
```

三处新增的解析都读**同一个**变体清单，任何一处不一致都会被具名拒绝，不会静默换栈：

- `shine/comfyops/variant_binding.py`（新）——变体 → 目录 → 工作流文件的唯一解析处；
- `shine/comfyops/variant_readiness.py`（新）——五层就绪与唯一下一步；
- `shine/comfyops/profile_gate.py`（新）——生产门禁。

---

## 4. 配置入口与生产入口

### 4.1 配置入口（导演模式「基础配置 → ② 模型与生成方案」）

新增卡片 **「W6A8 · 低显存候选」**（`frontend/src/components/VariantReadinessCard.tsx`），数据来自新端点：

- `GET /api/v1/setup/variant-readiness?project_id=&variant_id=`
- `POST /api/v1/setup/profiles`（带 `variant_id`）
- `POST /api/v1/setup/profiles/validate`（带 `variant_id`）

卡片显示：本机 GPU / 显存 / 内存 / **Windows commit 余量** / CUDA 算力 / Torch / 磁盘；五层状态；**当前唯一下一步**及其原因。

五层就是用户给的那五层，依赖顺序：

| 层 | 含义 | 判定依据 |
| --- | --- | --- |
| `WEIGHTS_PENDING` | 待准备权重 | 变体方案图里真正加载的每个模型件都在本机 |
| `FILES_VERIFIED` | 文件已核验 | 包台账已过 `UNVERIFIED_ORIGIN`，或已核验缓存里存在同 SHA-256 同字节数的文件 |
| `WORKFLOW_REGISTERED` | 工作流已登记 | 该方案行属于**当前作品** |
| `RUNTIME_VALIDATED` | 本机运行环境已验证 | `validated_against` 存在（受管渲染器 `live_object_info` 核对过） |
| `REAL_RENDER_VERIFIED` | 真实出片已验证 | 有一个非 mock 的 `PASSED` 任务用了该方案 |

> 权重要求**跟着图走**：变体目录里那个可选的 4 步 turbo LoRA 不在这个方案的图里，因此不算缺件。

### 4.2 生产入口

三处可达入口都接上了同一套门禁：

- **ProductionSeriesPanel**（排产制作）：新增质量档「低显存候选 · W6A8」；方案下拉里不可用的候选显示「（本机暂不可用）」并在下方面具名说明原因；一键开拍与「开始制作影片」在提交前检查门禁。
- **DirectorProductionRunPanel**（整部作品生产）：导演包的方案下拉同样禁用不可用候选，「生成导演包」在门禁未过时拒绝并说明原因。
- **FourStagePanel** → `build_volume_handoff` → `build_package`：导演包这一层已接门禁，四阶段交接因此自动继承。

**服务端门禁**（`shine/comfyops/profile_gate.py`）只在**未验收**变体上加码；已验收变体（含 INT8 基线）的行为**完全不变**：

| 阻断码 | 含义 |
| --- | --- |
| `PROFILE_SCOPE_MISMATCH` | 方案属于另一个作品 |
| `PROFILE_NOT_REGISTERED_FOR_PROJECT` | 候选方案还没登记到当前作品 |
| `PROFILE_GRAPH_IDENTITY_CHANGED` | 方案图哈希与变体清单登记的不一致 |
| `PROFILE_NOT_VALIDATED` | 没在本机真实渲染器上核对过节点 |
| `PROFILE_WEIGHTS_NOT_AVAILABLE` | 权重没就位或没逐字节核验 |
| `MANAGED_INSTANCE_NOT_CONFIGURED` | 本机没有受管运行时配置 |
| `WORKFLOW_SIZE_NOT_MAPPED` | 方案没接宽高，无法按所选分辨率渲染 |

**不替换**：任何一个阻断码触发时都是 409 + 原因，**不会**改用 INT8、不会改成 FL2VA、不会换参考资产、不会换 checkpoint。请求在**改镜头规格、自动补绑定、入队之前**就被拦下（门禁在 `create_series` 与 `create_render_job` 里都排在最前面）。

**服务端是唯一的门**：浏览器读不到结论时**不发明拒绝**（避免用一个没读到的结论挡住人），由服务端裁决并给出它自己的原因。

### 4.3 显式选定就保持同一身份

- 拍摄质量新增独立档「低显存候选 · W6A8」，W6A8 方案**不会**被当成「高质量 · 原生20步」，自动适配不会在 INT8 与 W6A8 之间静默换权重。
- 一键开拍里**显式选中的候选方案优先于质量档**：要么用它，要么明确说不能用，不会悄悄换成基线。
- 导演包钉住 `workflow_profile_id / graph_sha256 / checkpoint_id / parameter_map / media_transfers / validated_against`；生产系列与 Job 带同一个 `workflow_profile_id`。

### 4.4 登记与安装

- `POST /setup/profiles` / `/setup/profiles/validate` 新增可选 `variant_id` 与 `catalog`。**不传时行为完全不变**（默认目录 + 原来的三个方案）。
- 传 `variant_id` 时登记的是**该变体自己的**方案（`local-h3-ref2va-lowend-pdd-free-2img`），不碰基线的三个方案。
- 同名同项目内容不同 → 仍然 `PROFILE_CONTENT_CHANGED`；重复登记 → `ALREADY_REGISTERED`（幂等）；变体与目录不一致 → `VARIANT_CATALOG_MISMATCH`（409，不合并、不回退）。
- **候选的下载仍然是关闭的**：`auto_download_allowed` 仍是 `false`，安装计划仍以 `VARIANT_NOT_DOWNLOADABLE` 拒绝。**没有**改 `SUPPORTED`、**没有**改 `auto_download_allowed`、**没有**跳过资格检查。
- 已批准的准备路径（既有的「检测已有安装 / 导入模型 / 核验」）现在**按变体自己的目录**比对 revision、字节数与 SHA-256（`/existing-installations/inspect|import-models|verify` 新增 `variant_id` / `catalog`），因此官方 W6A8 权重可以走这条路就位并被核验，而不是被产品下载。

---

## 5. 具体权重与运行环境

### 5.1 权重（全部来自官方 `Comfy-Org/MiniMax-H3`，revision `54472221d27c695fca9621b0c3ed52f06c4af707`）

| 角色 | 文件 | 字节数 | SHA-256 |
| --- | --- | --- | --- |
| DiT | `diffusion_models/minimax_h3_ref2va_pruned_w6a8.safetensors` | 15,983,746,636 | `ece96bbbce76670ec782de84acc888fd9d9b210bd2ab97c1957b39896734f9f1` |
| 视频 VAE | `vae/minimax_h3_video_vae_int8_convrot.safetensors` | 2,811,065,184 | `52a2c8c73583c86e4f41cdcce3a6ad0ea562987bc0bf3d60a0cef5f5c8e60c0e` |
| 音频 VAE | `vae/minimax_h3_audio_vae_fp32.safetensors` | 605,254,808 | 目录内已钉 |
| 文本编码器 | `text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | 15,687,142,551 | `35a88d51044231fe332301d7a62aa81e3f2cba62febeb446e2c1e3e0ef76f2c6` |
| （可选，未启用）4 步 turbo LoRA | `loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors` | 1,956,193,000 | `5b9ab5ade15d0775676d01a907268a69a1468dc6033b3b0d3ded5502f3ebb84c` |

方案是 **PDD-free 20 步、无 turbo、无风格 LoRA**，显式映射 `prompt / duration / seed / width / height / 1×reference_subject_1 / 1×reference_picture_1`，`media_transfers` 只声明 `image`（没有音色主素材的项目可用）。

### 5.2 运行环境（本机实测）

```
GPU        GeForce RTX 5080 · 16303 MiB（可用 14435 MiB）
RAM        32418 MiB（可用 22454 MiB）
Commit     72418 MiB（可用 56543 MiB，PAGE_FILE_PRESENT）
CUDA       算力 12.0
Torch      隔离运行时未安装（pinned wheel torch 2.7.1+cu128）
磁盘       可用 286.25 GB
受管实例  F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI（端口 8188）
```

本轮**没有**新建受管环境，也没有改 ComfyUI / Torch / CUDA / comfy-kitchen / 节点版本：候选复用既有受管实例与同一套目录，**5080 既有配置保持可用**。

已安装的 H3 权重（受管实例 `models/`）：

```
diffusion_models/Minimax-H3/minimax_h3_ref2va_pruned_int8_convrot.safetensors  20.97 GB
diffusion_models/Minimax-H3/MiniMax_H3_Ref2VA_pruned_nvfp4.safetensors          12.53 GB
```

**W6A8 的 `minimax_h3_ref2va_pruned_w6a8.safetensors` 不在本机。**

---

## 6. 离线验证

### 6.1 新增用例

`backend/tests/test_variant_binding_and_gate.py`（21 项）：

| 用例 | 覆盖 |
| --- | --- |
| `test_registering_the_candidate_registers_its_own_profile_not_the_baseline` | 登记候选拿到的是候选自己的方案，基线三个方案不受影响 |
| `test_registering_the_candidate_twice_is_idempotent` | 登记幂等 |
| `test_a_variant_and_a_catalog_that_disagree_are_refused` | catalog 与 variant 错配 → `VARIANT_CATALOG_MISMATCH`，且没有半成功 |
| `test_a_same_name_profile_with_different_content_is_refused` | 同名异内容 → `PROFILE_CONTENT_CHANGED` |
| `test_an_unknown_variant_is_refused_by_name` | 未知变体 404 |
| `test_the_candidate_profile_carries_its_own_variant_identity` | 身份链起点：variant_id / 验收状态 / 图哈希 / checkpoint |
| `test_the_gate_names_what_is_missing_instead_of_claiming_a_pass` | 缺权重、缺节点核对、无受管运行时，逐条具名 |
| `test_the_gate_reports_the_missing_nodes_and_the_missing_weights` | 缺件指名到 component |
| `test_the_gate_reports_a_runtime_that_is_not_there` | 运行时失效；受管运行时到位后门禁放行 |
| `test_a_candidate_profile_of_another_project_cannot_be_used` | 项目切换 → `PROFILE_SCOPE_MISMATCH` |
| `test_the_gate_leaves_the_audited_baseline_alone` | 原有 INT8 默认路径零新阻断 |
| `test_the_candidate_cannot_be_queued_by_the_api` | 绕过界面直接调接口同样 409，且没有生成序列 |
| `test_the_candidate_cannot_be_queued_as_a_single_job` | 单任务入口同样被拦 |
| `test_an_explicitly_selected_candidate_keeps_one_identity` | 显式选中 → 导演包钉住同一 id/图哈希/checkpoint → 生产系列带同一 id |
| `test_a_candidate_the_machine_cannot_run_never_reaches_a_director_package` | 不可执行的候选不进导演包 |
| `test_the_readiness_report_has_five_layers_and_one_next_step` | 五层 + 唯一下一步 + 候选不可下载 |
| `test_the_readiness_next_step_moves_forward_with_the_evidence` | 下一步随证据推进（权重 → 登记 → 节点 → 真实出片） |
| `test_the_readiness_report_states_the_host_facts_it_read` | 主机事实字段齐 |
| `test_the_existing_install_path_compares_against_the_candidate_catalog` | 本地导入路径按候选自己的目录比对（含 15,983,746,636 字节的 DiT） |
| `test_the_resolution_helper_refuses_a_mismatched_catalog` | 解析层拒绝错配 |
| `test_the_resolution_helper_without_a_variant_keeps_the_default_catalog` | 不传变体 → 默认目录不变 |

`frontend/src/test/variantCandidate.test.tsx`（4 项）：候选有自己的质量档且不与原生档混用；本机不能执行时选项被禁用并说明原因；门禁通过后显式选中的候选带着同一个身份进生产序列（`workflow_profile_id === 'wf-w6a8'`，不是基线）；门禁未通过时候选不会被提交。

### 6.2 命令与结果

全部命令在**仓库根目录**执行（`backend/tests` 的用例按相对路径解析仓库根脚本）。

| 命令 | 结果 |
| --- | --- |
| `backend\.venv\Scripts\python.exe -m pytest backend/tests/test_variant_binding_and_gate.py -q` | **21 passed** |
| `backend\.venv\Scripts\python.exe -m pytest backend/tests/test_deployment_recommendation.py test_deployment_acceptance_regressions.py test_setup_workflow_profiles.py test_workflow_profile_scope.py test_director_setup_contract.py test_production_series.py test_new_machine_setup.py test_variant_manifest.py test_install_plan_qualification.py -q` | **126 passed** |
| `backend\.venv\Scripts\python.exe -m pytest backend/tests/test_variant_binding_and_gate.py test_offline_e2e.py test_production_series.py test_workflow_profile_scope.py -q` | **65 passed** |
| `backend\.venv\Scripts\python.exe -m pytest backend/tests -q`（完整后端套件） | 见 §6.3 |
| `backend\.venv\Scripts\python.exe -m ruff check backend/shine backend/tests` | **All checks passed!** |
| `alembic upgrade head && alembic check`（临时库） | **No new upgrade operations detected.**（本轮无迁移） |
| `npm.cmd test -- --run`（frontend） | **31 files / 198 tests passed** |
| `npm.cmd run build`（frontend） | **✓ built**（`tsc -b && vite build`） |

### 6.3 完整后端套件

见 §6.4（最终结果随报告一并记录）。

---

## 7. 真机证据（本机 RTX 5080，只读探测）

用真实数据目录 `F:\ClaudeWorkSpace\shine-workbench\data` 与真实受管实例跑了一次**只读**探测（脚本 `.local-acceptance/w6a8-real-probe.py`，只读不写）：

```
variant_id      ref2va-lowend-candidate
catalog_id      shine-h3-ref2va-lowend-candidate-windows-x64
acceptance      CANDIDATE · auto_download_allowed = false

项目 75af5142-3f70-492b-89e2-a31f22fc2cd7（本机联调）
  reached_layer  WEIGHTS_PENDING
  production_ready  false
  next_step     VARIANT_NOT_DOWNLOADABLE（blocked = true）
                「这个变体还没有进入自动下载路径（验收状态是 CANDIDATE）。
                  不能用改 SUPPORTED、改 auto_download_allowed 或跳过资格检查的方式解锁下载；
                  请用已有的「检测已有安装 / 导入模型」流程，把官方权重按 revision、字节数与 SHA-256 校验后放进来。」
                action = EXISTING_INSTALLATION_IMPORT

  缺件（方案图真正加载的四个模型件，全部 registry_status = null）
    minimax-h3-diffusion-w6a8        15,983,746,636 B
    minimax-h3-vae-int8               2,811,065,184 B
    minimax-h3-audio-vae-fp32           605,254,808 B
    minimax-h3-text-encoder-nvfp4    15,687,142,551 B
    （4 步 turbo LoRA 不在图里，因此不算缺件）

  该作品既有的三个 INT8 方案
    local-h3-ref2va-pdd8-2img-1aud          h3-ref2va-int8-convrot  SUPPORTED  selectable=true  blockers=[]
    local-h3-ref2va-pdd8-2img-1aud-portrait h3-ref2va-int8-convrot  SUPPORTED  selectable=true  blockers=[]
    local-h3-ref2va-2img-1aud               h3-ref2va-int8-convrot  SUPPORTED  selectable=true  blockers=[]
```

主机事实同一次探测读出：RTX 5080 / 16303 MiB / RAM 32418 MiB / commit 72418 MiB / CUDA 12.0 / Torch 未装 / 磁盘 286.25 GB。

**这条证据说明三件事**：候选在真机上被正确识别为候选而不是基线；缺什么、下一步做什么、为什么不能自动下载都写清楚了；**基线的三个方案一个都没受影响**。

---

## 8. 真机出片：BLOCKED（附原因）

用户给的验收条件是「具备 4070 Super 12GB、已批准权重、空闲 GPU 和隔离测试数据」。逐条核对：

| 条件 | 事实 | 结论 |
| --- | --- | --- |
| 4070 Super 12GB | 本机只有一张 **RTX 5080 16GB**（`nvidia-smi` 实测，CUDA 算力 12.0） | ✗ 目标机器不在本机 |
| 已批准的 W6A8 权重 | 受管实例 `models/` 里**没有** `minimax_h3_ref2va_pruned_w6a8.safetensors`；只有 INT8 与一个第三方 NVFP4 文件（且其字节数与本轮候选的钉点不同） | ✗ 权重未就位 |
| 空闲 GPU | 可用显存 14435 MiB | ✓ |
| 隔离测试数据 | `.local-acceptance/` 可用 | ✓ |

**因此：**

- **4070 Super 12GB 上的 W6A8 真实出片 = NOT RUN / BLOCKED。** 官方没有公布这一组合的显存门槛（清单里 `min_vram_mb` 仍是 `unknown`），本机也没有该型号的卡，因此「12GB 能不能跑」仍然是待核，不是结论。
- **5080 上的 W6A8 出片 = 同样 BLOCKED。** 不是显卡不够，而是**权重没有就位**：候选变体 `auto_download_allowed=false`，产品按设计以 `VARIANT_NOT_DOWNLOADABLE` 拒绝下载。要在 5080 上出这一档，必须由人按已批准路径把官方权重放进来（`/setup/existing-installations/inspect|import-models|verify`，按 revision、字节数、SHA-256 核验），**本轮没有绕过这个门禁，也没有改 `SUPPORTED` / `auto_download_allowed`**。
- 因此**没有**任何 W6A8 真实产物的证据：没有 attempt、没有 artifact、没有显存峰值、没有解码记录、没有人工观感。**本报告不声称任何真实出片结论。**

**5080 上做过的、与本档无关的真机事实**（仅用于说明环境，未产出本档画面）：既有 INT8 基线在 5080 上的真实出片证据见仓库既有记录（`docs/ACCEPTANCE_20261106_DIRECTOR_UNBLOCK_REAL_H3.md` 等），本轮**没有**改动那条路径，也**没有**重跑它。

---

## 9. 证据边界

| 证据 | 等级 | 说明 |
| --- | --- | --- |
| 离线用例（21 项后端 + 4 项前端） | **OFFLINE_VERIFIED** | 门禁、幂等、错配、身份链都是真跑的断言 |
| 真机只读探测（§7） | **MEASURED（只读）** | 真实数据库 + 真实主机事实 + 真实受管实例目录；没有写任何业务数据 |
| 候选的**渲染**能力 | **未验证** | 没有权重、没有目标显卡，因此没有任何 attempt/artifact/峰值记录 |
| 变体的**验收状态** | **CANDIDATE（未变）** | 只有真机出片才能把它抬成 SUPPORTED；本轮没有 |
| 社区 INT8 + 4 步 turbo 的实测 | **仅作对照** | 不是本项目的实测，不作为任何结论的依据 |
| VLM 正负校准 | **未做** | 本轮**没有**改 `client_review_identity` 钉住的文件（`worker.py` / `preproduction.py` / handoff / quality），因此按设计不触发重校准；但校准本来就没做过，**不得声称已校准** |
| 人工艺术验收 | **未做** | 没有本档画面可看 |

---

## 10. 剩余阻塞

1. **W6A8 权重未就位** → 第 1 层 `WEIGHTS_PENDING` 未过，真实出片无法进行。解锁方式只有一条：人把官方权重按钉点放进受管实例，走已有的导入/核验路径。
2. **没有 4070 Super 12GB** → 12GB 可行性无法在本机验证，官方也没公布门槛；清单里的 `min_vram_mb` / `min_ram_mb` / `min_commit_mb` / `min_disk_gb` 仍是 `unknown`（诚实保持）。
3. **候选的自动下载仍然关闭** → 这是设计，不是缺陷；要打开必须有人先给出可核实的官方门槛与一次真机验收。
4. **本档没有真实产物** → 因此也没有显存峰值、提交内存峰值、耗时、音视频全解码、人工观感与 attempt/lease 释放记录可报告。
5. **VLM 正负校准仍未做**（仓库既有欠账，本轮未触碰）。
6. **启动器四条验收与安装包未重跑**：本轮没有改启动方式、端口、依赖或数据目录，按 AGENTS.md「改动启动逻辑时四条全跑」的判据，本轮不触发；但**发布前**仍需按 §B 构建安装包。

---

## 11. 回退方法

本轮是**纯增量**的，回退不需要迁移、不需要清数据：

```powershell
# 回退代码（分支未合并 main 时）
git checkout main
git branch -D codex/w6a8-lowend-variant

# 回退代码（已合并时）
git revert <merge-commit>
```

回退后需要知道的：

- **数据库不需要回滚**：本轮**没有**任何迁移（`alembic check` → 无新操作）。新登记的候选方案行会留在库里，但它们只是普通 `workflow_profiles` 行，不会被默认路径引用（默认路径只看默认目录的三个方案名）。
- **变体清单的改动可单独回退**：`config/deployment/variants/ref2va-lowend-candidate.json` 回到 `checkpoint_id: "unknown"` / `graph_sha256: {}`，并把对应的 `unknown_fields` 条目加回去即可；这只影响候选的身份反查，不影响任何已生产内容。
- **已冻结风格、历史渲染、任务台账**：本轮**没有**触碰 `style_versions`、`artifacts`、`jobs`、`production_series` 的任何既有行；`build_package` 的门禁只在**新建**包时生效，既有包保持原样。
- **既有 INT8 路径**：回退后行为与本轮之前完全一致（默认目录 + 三个方案 + 原有门禁）。

---

## 12. 变更清单

**新增**

- `backend/shine/comfyops/variant_binding.py`——变体/目录/工作流文件的唯一解析处
- `backend/shine/comfyops/variant_readiness.py`——五层就绪 + 唯一下一步
- `backend/shine/comfyops/profile_gate.py`——生产门禁
- `backend/tests/test_variant_binding_and_gate.py`——21 项离线用例
- `frontend/src/components/VariantReadinessCard.tsx`——基础配置里的 W6A8 卡片
- `frontend/src/components/variantGate.ts`——前端门禁读取
- `frontend/src/test/variantCandidate.test.tsx`——4 项前端用例

**修改**

- `backend/shine/api_setup.py`——`variant_id` / `catalog` 参数、变体感知登记、两个新端点、本地导入路径按变体目录比对
- `backend/shine/comfyops/production_series.py`——入队前的生产门禁
- `backend/shine/services.py`——单一任务入口的生产门禁
- `backend/shine/director/package.py`——导演包的生产门禁
- `config/deployment/variants/ref2va-lowend-candidate.json`——`checkpoint_id` 与 `graph_sha256`
- `frontend/src/components/DirectorSetupPanel.tsx`——挂载 W6A8 卡片
- `frontend/src/components/ProductionSeriesPanel.tsx`——低显存质量档、门禁、显式选择优先
- `frontend/src/components/DirectorProductionRunPanel.tsx`——门禁与禁用项
- `AGENTS.md`——本轮规则

**未改动**：`config/deployment/h3-ref2va-lowend-candidate-windows-x64.json`、`config/workflows/ref2va-lowend-pdd-free-2img.json`、默认部署目录、默认目录登记的三个方案、Worker、预生产、五道 QC、租约与提交意图机制、导演样式引擎。