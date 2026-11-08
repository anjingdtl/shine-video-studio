# Upstream source and license audit

## 2026-11-08 四阶段流水线对照

基线为 `7081104a79e6758228eb98e96d74e0bb36da52f8`。本轮核对 Toonflow-app 的章节事件与分镜审阅、Huobao Drama 的集内成员资格校验、NarratoAI 的脚本解析边界和 Temporal 的 Activity 幂等合同。仓库修订、对应模块、许可证与适用范围见 [四阶段对抗审查](ADVERSARIAL_REVIEW_FOUR_STAGE_PIPELINE_20261108.md#4-高星-github-模块的可移植做法)。本轮只记录设计借鉴，产品依赖与上游代码均未变动。

## 2026-11-02 director acceptance V2 and core LLM preparation

Baseline `5b06f5e17784d899230b91a6fd6c438c7bc6e57a`. Reused this repository's
SetupPanel approval contract, FastAPI routes, lifecycle state machines, Gateway,
SecretStore and provider/role routing. No external source code copied, model or
workflow pin changed, or existing ComfyUI modified. The shared LLM form was
extracted from SettingsPanel, not copied from a third-party product.
Playwright 1.64.1 is a temporary test-only overlay; it is not an application
runtime dependency. Browser fixtures explicitly isolate external OS/service
ports and use synthetic bytes, never real-H3 or real-provider acceptance.
See [V2 acceptance](FINAL_DIRECTOR_WORKBENCH_ACCEPTANCE_V2_20261102.md).

## 2026-11-02 existing-installation detection repair

Baseline `351067f`: independently implemented bounded filesystem discovery and pinned-file cache import.
Reused only this repository's `ControlledDownloader.valid`, deployment catalog and isolated installer contracts.
No upstream code copied, dependencies added, pinned revisions changed, or existing ComfyUI code executed.
Runtime adoption is not inferred from discovery; source/node licensing and existing managed installation gates remain.
Validation and limits: [repair report](EXISTING_COMFYUI_FIX_20261102.md).

## V1.1 recheck (2026-10-21)

Re-read the official SKILL.md, base-en.txt and ref-en.txt through the GitHub connector at the
same exact `d21241f0a4b3acbb34c97dae47fa417b7065e438` revision. Independently fetched those
fixed public URLs into memory and recomputed SHA-256: all three matched the digests below.
Also re-read the pinned README and recorded Git blob SHA
`bdfd2473d24434599913d8d9edb9b96a6dc13603` and SHA-256
`7964f3044d3c7efaa054bb823644ad98cf9ea43389f994c51e08977822f16bac`.
Capability limits cite its System Overview / Model Variants tables as OFFICIAL_H3,
separately from Shine policy and local workflow evidence.
Git blob SHAs are now recorded in `shine/h3/rulebook.py`; source section locators accompany
the independently authored rules. No official guide text, upstream implementation, GPL
plugin, model weight or example workflow was copied into this V1.1 distribution. The raw
documentation redistribution license is still unconfirmed. Import of legally acquired
bytes is local-only, user-initiated and hash-checked. No upstream code is reused in V1.1.
Actual workflow/node/checkpoint compatibility and all H3 inference remain PENDING_LOCAL_H3.

Read-only source review on 2026-10-20. Each revision below is the exact shallow-clone `HEAD` inspected. No upstream code, model weights, generated media, prompts, example workflows, or user data were copied into Shine.

## `qiukaihui/comfyui-auto-drama`

- Repository: <https://github.com/qiukaihui/comfyui-auto-drama>
- Inspected commit: `362c8fd860bbbafda4b504de4f7bbe0c9a3d0d24`
- License: the checked-in root `LICENSE` is MIT.
- Inspected code: `console/batch_console.py`, `console/chain_daemon.py`, `workflows/build_api_graphs.py`, and `workflows/assemble.py`.
- Observed interfaces: the console builds API graphs from workflow templates, uploads image references through ComfyUI `/api/upload/image`, submits jobs through `/prompt`, checks `/queue` and `/history`, and downloads outputs through `/view`. Its independent daemon polls the console every 20 seconds and advances waiting chain segments. The console also keeps its own local SQLite task state.
- Reuse decision: **reference only; no files copied**. Shine owns its own job state and ComfyUI adapter boundary. Reusing the console would add a second state store and its submission/retry behavior would need redesign around Shine's durable intent and reconciliation records.
- Risk: this project contains image-generation endpoints and code (`image_gen`, `boogu_generate`, related configuration and helpers). That path is outside Shine's approved-asset policy and is excluded. The current branch also has a Ref2VA-to-FL2VA model fallback when a selected checkpoint is missing; Shine must fail closed instead of silently changing the requested generation mode.
- Verification: the above source paths and the MIT license were read at the inspected commit; no upstream tests or workflows were executed because no code is imported.

## `civilcoco/ComfyUI-MiniMaxH3-Myang`

- Repository: <https://github.com/civilcoco/ComfyUI-MiniMaxH3-Myang>
- Inspected commit: `78d6792bd8415fbf45c5f4daaa7aa12627787492`
- License: repository README and `pyproject.toml` identify the node code as **GPL-3.0-only**. Its README says the continuity implementation includes GPL code adapted from `ComfyUI-H3-Motion-Context`; `THIRD_PARTY_NOTICES.md` records its upstream boundaries.
- Inspected code: `agent_media.py`, `agent_nodes.py` LLM configuration routes, `tools/build_director_workflow.py`, `tools/build_loop_workflow.py`, `example_workflows/Minimax_H3_Myang_Director_CN.json`, and `example_workflows/Minimax_H3_Myang_LongVideo_CN.json`.
- Observed interfaces: the package exposes ComfyUI custom nodes and registers local routes under `/minimax-h3-agent/` for skills and LLM configuration. Its Director workflow keeps per-shot media lists and maps editor references to H3 labels; the README documents segmented continuation, temporal context, trimming, and AV joining.
- Reuse decision: **license isolation**. Do not copy or link GPL implementation files into Shine's source distribution. A separately installed, optional ComfyUI plugin may be evaluated later through ComfyUI's documented workflow/API boundary after distribution-specific license review. No Myang code or workflow was copied.
- Compatibility and risk: its Director expects the plugin, supported ComfyUI nodes, checkpoints, and any optional enhancement packs to be installed locally. Its prompt/media labels and continuation machinery are useful references, but they do not replace Shine's approval ledger, frozen style version, task lease, or external submission reconciliation.
- Verification: source/notice/license metadata was inspected; optional GPU nodes and real workflows were not installed or executed.

## `benjiyaya/Calliope`

- Repository: <https://github.com/benjiyaya/Calliope>
- Inspected commit: `858569cb100eb620e21ec184d50bffd2ba53c785`
- License: the checked-in root `LICENSE` is MIT.
- Inspected code: `calliope-backend/src/calliope/agent/llm.py`, `agent/harness/orchestrator.py`, and `agent/harness/registry.py`.
- Observed interfaces: `LLMClient` accepts a configurable `base_url` and model and sends OpenAI-compatible chat-completion requests. The runtime plans work and invokes a registry of project-scoped tools; the registry filters tools by session origin and user approval policy. The wider checkout also contains its own ComfyUI worker and application subsystems.
- Reuse decision: **architecture reference only; no files copied**. Shine's `ProducerAgentRuntime` accepts an injected structured-model port and returns Pydantic-validated `ShotSpec` data. It has no database handle or tool registry. A broader Agent/LLM integration remains future work; Calliope is not a Shine runtime dependency.
- Compatibility and risk: adopting its runtime or worker would create a second orchestration path and broaden the permissions surface. Shine will retain one job state machine and add only explicit, reviewed tools and provider profiles.
- Verification: the listed source and root MIT license were inspected; Calliope services and its test suite were not installed or executed.

## `MiniMax-AI/MiniMax-H3`

- Repository: <https://github.com/MiniMax-AI/MiniMax-H3>
- Inspected commit: `d21241f0a4b3acbb34c97dae47fa417b7065e438`
- Code/license scope: this GitHub source snapshot has no checked-in root `LICENSE` file. Its README links the H3 model/checkpoint terms to the separate **MiniMax H3 Community License Agreement** hosted with the Hugging Face model. Review that agreement before model download or redistribution; the code repository, model weights, ComfyUI nodes, and generated media have distinct licensing scopes. No official source or prompt guide was copied into Shine.
- Inspected material: `README.md`, `skills/h3-prompt-writing/SKILL.md`, `skills/h3-prompt-writing/references/base-en.txt`, and `skills/h3-prompt-writing/references/ref-en.txt`.
- Observed contracts: the skill requires base mode fields `integrated_multimodal_description`, `overall_soundscape`, and `non_diegetic_music`; Ref2VA uses six ordered sections and stable `<Subject N>`, `<Picture N>`, `<Video N>`, and `<Audio N>` labels. The README states 4–15 second outputs at 24 FPS; Ref2VA limits of 9 images, 3 videos, 3 audio clips, and 12 mixed files; local H3-Base produces 768p-class output, while the complete 2K workflow uses H3-Context-IR and H3-Regenerate-2K services.
- Prompt source digests at the inspected commit: `SKILL.md` SHA-256 `a7000443588ca3f145e3b3fd8900f14e0325dc460bd811268fac89a9dc8e56d0`; `base-en.txt` `2cfebc096a6e08370f288d468d90b60f7f9bcb938f94bf090816e910e48e75fc`; `ref-en.txt` `1e574f356716ad55612247ffb7bbccbcdb484ad96599d63c7dca1af186b1fab7`.
- Reuse decision: **authoritative reference; implemented as a format contract, not copied code**. Shine's `H3PromptCompiler` pins the inspected commit, skill/rule hashes, and official base/Ref2VA section names and label families. It does not copy upstream text into the runtime or embed a "universal suffix". The compiler is deterministic and tested offline; real H3 output quality remains `PENDING_LOCAL_H3`.
- Local validation: **PENDING_LOCAL_H3**. No weights were downloaded and no GPU, ComfyUI instance with H3 nodes, real prompt execution, or real generated output was tested in this build. The real H3 API workflow mapping is `BLOCKED` until the user exports their own workflow JSON.

## Shine license decision

The initial Shine repository contained only project-authored planning documents and no source license. M0 adds an MIT license for the original Shine code because this implementation copies no upstream code. The separate MiniMax H3 model terms and the GPL-only plugin boundary remain applicable to their own components; this choice does not grant rights to those components.


## V1.2.1 local read-only integration check (2026-10-21)

Inspected the installed ComfyUI checkout at commit
`b0b743566f65daafc423b4fea8a2fbda94b3384a`; the inspected file
`comfy_extras/nodes_minimax_h3.py` is clean relative to that checkout and has SHA-256
`5999ed8c22b13c8251084c0629fd82137d2fb52b55106abfc73bf5cafc73841d`.
Read only the ReferenceToVideo autogrow schema/bounds and AddGuide schema/conditioning
behavior. No upstream source or full-text guide was copied. No ComfyUI file, model,
workflow or cache was modified. AddGuide's existence is not HARD_FRAME_ANCHOR acceptance.
Static validation checks the actual 9 image / 3 video / 3 audio bounds. Dynamic graph
expansion stays disabled; video transfer and hard continuity stay pending real testing.
The previous V1.1 acceptance claims above remain historical and are superseded for current
local Ref2VA scope by H3_REF2VA_V1.2.1_FINAL_CLOSEOUT.md. No license conclusion was expanded.

## 2026-10-25 local review models

- [unsloth/Qwen3-VL-4B-Instruct-unsloth-bnb-4bit](https://huggingface.co/unsloth/Qwen3-VL-4B-Instruct-unsloth-bnb-4bit),
  source revision `24b9da6f9f8fddac102e2240fa0d073f98c65b75`; model card declares Apache-2.0.
  Existing local safetensors SHA-256 `69c94bb8382216e327c6a08064497609042d69baeeea6540348a8770f43c417c`,
  4,298,914,465 bytes, matches upstream LFS metadata. No weights downloaded or copied into Git.
- [openai/whisper-tiny](https://huggingface.co/openai/whisper-tiny), revision
  `169d4a4341b33bc18d8881c4b69c2e104e1cc0af`; model card declares Apache-2.0.
  Existing safetensors SHA-256 `7ebd0e69e78190ffe1438491fa05cc1f5c1aa3a4c4db3bc1723adbb551ea2395`;
  matching tokenizer/configuration files fetched at that revision into an ignored acceptance directory.
- A separately installed Transformers 4.57.6 / tokenizers 0.22.2 overlay supports the existing Qwen3-VL
  model without replacing the installed ComfyUI runtime packages. `local_review_server.py` is original
  Shine adapter code using library APIs; no upstream implementation or full-text guide was copied.
- These source/license facts are an audit, not a human grant for asset rights, voice cloning,
  master promotion, production workflow publication or complete audiovisual acceptance.

## 2026-10-25 Qwen SDK role separation

Read official distribution metadata for [qwen-tts 0.1.1](https://pypi.org/project/qwen-tts/0.1.1/)
and [qwen-asr 0.0.6](https://pypi.org/project/qwen-asr/0.0.6/): they pin Transformers 4.57.3 and
4.57.6 respectively. The exact wheel SHA-256 digests are
`11a290d8dabc7ef91a90c54478c8ab19b3edb1d85c0882313721892bdc4af15d` and
`b9c55a38413298f3a990a4475467399daec6e8f4172363053fc42e2166c2dfd3`.
Both were installed from PyPI only into separate ignored dependency directories, sharing the existing
unmodified Torch runtime. Original Shine stdin adapters use their public SDK APIs; no upstream source
or guide was copied into the repository. No package install status or human approval was fabricated.

Official model-card metadata was inspected at these exact model revisions; Apache-2.0 is the
declared card license, not an asset/voice-cloning permission grant:

| Model | Revision | Planned bytes |
| --- | --- | ---: |
| Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign | `5ecdb67327fd37bb2e042aab12ff7391903235d3` | 4,520,162,313 |
| Qwen/Qwen3-TTS-12Hz-0.6B-Base | `5d83992436eae1d760afd27aff78a71d676296fc` | 2,516,104,532 |
| Qwen/Qwen3-ASR-0.6B | `5eb144179a02acc5e5ba31e748d22b0cf3e303b0` | 1,880,618,159 |
| Qwen/Qwen3-ForcedAligner-0.6B | `c7cbfc2048c462b0d63a45797104fc9db3ad62b7` | 1,840,070,940 |

LFS hashes/sizes came from pinned Hugging Face metadata; small metadata files were read from the
same official revision and checked against Git blob identities before calculating SHA-256.
These are pending installation proposals. SDK import success is not checkpoint execution or voice acceptance.

## 2026-10-26 T2VA source preparation (no model execution)

Inspected Comfy-Org/workflow_templates commit `67984637eefcd4825dcf5c134e39034785be1682`,
`templates/video_minimax_h3_t2v.json` blob `b99a270eadd74c371c3c4facd9322da6a99ca4db`.
The repository LICENSE blob `ec6ae8a4821329f417a3e74e6dd108f499d08290` declares MIT.
The original template and LICENSE are in ignored local evidence, not bundled guides or third-party runtime source.
The API draft follows its native false branch, 20 steps, Base FL2VA INT8 ConvRot, no Turbo LoRA or first/last frames.
Native 1344x768 and five seconds are explicit draft choices; structure checks do not verify an actual render.

Comfy-Org/MiniMax-H3 revision `e5eb578a89295337b8ff433a035929ce0279e0b6` identifies the missing
`diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors`, 20,970,379,616 bytes,
SHA-256 `e889202c41dafb67b10d67b97f0d8541508036a6090af23425a5c2615d03c47a`.
Its card declares the custom MiniMax H3 Community License, not MIT/Apache-2.0.
The linked MiniMaxAI/MiniMax-H3 LICENSE was independently pinned at revision
`42ed227ee7df40d41602854ae760620d6eb651fe`, Git blob `c389b45855337ccec8ddeb389f4fe902abcd0b19`,
SHA-256 `59b99642b95ea21630e311198ddbfffbfe05aadba0c2f5d884cbdf4efcc90f44`.
No acceptance of those terms, download approval, model execution or modification of original ComfyUI is inferred.


## 2026-10-26 Qwen installation/runtime follow-up

The four pinned Qwen packages above completed 42 exact file size/SHA-256 checks under plan
`9a844b71-c4e1-48f8-9e1a-41ddb3bbf635`. Actual isolated SDK design, clone, new-utterance synthesis,
ASR and alignment ran and exited; no original ComfyUI dependency was upgraded. Windows torch import
required standard username environment variables; business secrets remain excluded. Model outputs
include a preserved zero-duration alignment failure. A separately hashed same-utterance tempo derivative
passed real text/timing checks; no model timestamp was fabricated and no human voice-quality approval is inferred.
See `docs/evidence/local-closeout-20261025/real-qwen-sdk-and-alignment.json` for exact scope and limits.
The fixed T2VA model/license proposal is being downloaded under explicit delegated operator authority for
local research only; neither Apache/MIT licensing nor real T2VA render acceptance is inferred.


## 2026-10-26 native review file identity preparation

Re-read the fixed Hugging Face repository trees for the two local review revisions above.
All 13 declared upstream Qwen3-VL files and 12 declared upstream Whisper files matched
existing local sizes and upstream LFS SHA-256 or Git blob SHA-1. The local VLM
`configuration.json` and Whisper `source-audit.json` are not files in those fixed trees;
they are excluded from the proposed pinned install scopes, without editing the originals.
The ignored proposals have not yet been registered or installed as native capability
packages. Canonical executor file identities additionally include checked relative names,
SHA-256 and actual byte sizes; they are independent of the old supplied manifest label.
No model source, license conclusion, master-asset grant or human acceptance was expanded.


## 2026-10-26 stronger local VLM candidate and sharded loading

The existing pinned 4B bitsandbytes model was withdrawn from production capability matching
following recorded real false-positive/false-negative style, identity and continuity controls.
Do not reinterpret its old basic calibration as broader semantic acceptance.

The official `Qwen/Qwen3-VL-8B-Instruct-FP8` candidate is pinned to
`9cdc6310a8cb770ce18efaf4e9935334512aee45`; the fixed model card declares Apache-2.0.
The complete upstream file tree lists 13 files totaling 10,605,572,552 bytes.
Weight shards have upstream LFS SHA-256
`e2dea2e85e643ef7045c31a485a426631a1e0a84e463f8b2c7e9c6682a06eafd` and
`3dc64ec934af27a7007d265014e907aff9e16658d409e5cd4cf79869bf2bd8c1`.
Metadata is checked against the fixed tree's Git blob IDs before deriving install SHA-256 values.
Source: https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-FP8/tree/9cdc6310a8cb770ce18efaf4e9935334512aee45

RTX 5080 / 16,303 MiB and the existing isolated Transformers/Triton FP8 adapter were inspected.
14,000 MiB is an ESTIMATED admission budget, not measured consumption or proof the model fits.
No new dependency source is copied into this repository; model files stay in the isolated ignored directory.
Model installation, actual execution, complete adversarial calibration and production publication remain
separate gates. No production-role substitution, new asset, rights claim or human review is inferred.

The owned local executor previously required a monolithic `model.safetensors` file. It now also accepts
a hash-verified `model.safetensors.index.json` only when every declared shard exactly matches the
index's weight map. Missing/unverified/extra shards and mixed monolithic/indexed weights are rejected.
The index and all shard bytes enter the canonical executor identity and cold-load checks.
Nine added structural counterexamples first failed; 44 targeted executor/runtime/offline tests pass after the fix.
These use synthetic bytes, not model inference.

## 2026-10-27: isolated structured review decoder

`lm-format-enforcer`0.11.3 and `interegular`0.3.3: MIT, fixed wheel URLs/SHA-256 and all 37 installed file hashes pinned in `config/review-decoder-lock.json`. Dependencies pydantic/packaging/PyYAML already existed in the separate ML runtime; no third-party source copied into main project, no global environment/model weights changed. Primary sources: https://github.com/noamgat/lm-format-enforcer and versioned PyPI metadata. Qwen tokenizer CPU compatibility passed; numeric ranges are not fully grammar-enforced, so application QC validation remains mandatory. GPU quality/runtime acceptance is separate and pending.
## 2026-10-28: ComfyUI handoff interface investigation

The installed ComfyUI checkout reports commit `b0b743566f65daafc423b4fea8a2fbda94b3384a`,
version 0.39.0, with a GPL-3.0 license file. Read-only inspection found that both
`/prompt` and `/api/prompt` ultimately use the same `PromptQueue.put` method, and that
queue insertion and queue snapshots use the queue's own lock. An optional self-authored
adapter is being developed separately under `integrations/shine_comfy_handoff`; no
ComfyUI source was copied. It is not installed in the user's instance or registered
as a validated production component. Distribution or combined-use license obligations
still require legal review. Other installed extensions may bypass or replace this guard;
the adapter alone is not proof of exclusive ownership or safe process termination.

Windows API 参考（2026-10-29）：[Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)、[OpenJobObjectW](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-openjobobjectw) 和 [Job Object Security and Access Rights](https://learn.microsoft.com/en-us/windows/win32/procthread/job-object-security-and-access-rights)。命名 Job 可由其他进程按权限打开，kill-on-close 仅在最后一个句柄关闭时触发；因此只读检查应及时关闭句柄，不能把监督进程退出直接等同于资源已释放。本项目自行实现调用，未复制第三方代码；实际 Worker 接入和 ACL 边界尚待验证。

## 2026-10-31: new-machine deployment catalog pins

New-machine deployment requires a versioned material catalog. All pins below were
read from the upstreams' own published metadata on 2026-10-31 and recorded under
`config/deployment/_upstream-pins/` (archives/tools/wheels/models). Nothing was
copied into Shine source; source archives are installed as isolated ComfyUI
instance trees under the existing controlled installer, never linked into the
main project. No large model weights were downloaded; HF LFS metadata
(SHA-256/size) and small non-LFS files (bytes downloaded and hashed) provide the
recorded digests.

### Source archives (installed as isolated instance trees, never copied into Shine)
| Component | Pinned revision | License (checked) | Verification |
|---|---|---|---|
| Comfy-Org/ComfyUI (v0.39.0 line) | `b0b743566f65daafc423b4fea8a2fbda94b3384a` | GPL-3.0-only | codeload zip downloaded, SHA-256 recorded |
| kijai/ComfyUI-KJNodes | `ab72eda7ffad6280422d71ab7d54d3fb87c0ba18` | GPL-3.0-only | same |
| Kosinkadink/ComfyUI-VideoHelperSuite | `4d907bee61e92c2e65af3bd6383a4e4d356126d1` | GPL-3.0-only | same |
| Jalen-Brunson/ComfyUI-MiniMax-H3-PDD-Acc | `311a65dd53832d8a5f8177a9d5fb923c09e35a90` | Apache-2.0 | same |
| kijai/ComfyUI-SolAttn_triton | `26d816ebd4f1e43a2c6e4d4759be3137f10a7a73` | Apache-2.0 | same |
| LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler | `40316cf008b2fd8663263270669eb4da23f89d2c` | MIT | same |

KJNodes/VHS/ComfyUI
stay GPL-isolated: they are downloaded and installed into the isolated ComfyUI
instance only, exactly like the Myang plugin boundary. The PDD weights load from
`models/pdd_acc` (verified against the pack's own `nodes.py`), so the isolated
BIND_MODELS families now include `pdd_acc`.

### Wheels (93, Windows py3.12, 4.2GB)
Full dependency closure compiled with `uv pip compile --python-platform windows
--python-version 3.12 --generate-hashes`; per-package win/cp312 (or abi3 /
py3-none) wheel digests fetched from PyPI JSON and cross-checked against the
compiled lock hashes. torch/torchvision/torchaudio 2.7.1+cu128 pins come from
the official cu128 index anchors (`download-r2.pytorch.org` CDN hashes). All 93
digests are real upstream-published values; no hash was computed from partial
bytes. qwen-asr/qwen-tts SDKs are excluded from the closure (sdist-only `sox`,
gradio); they remain optional manual voiceops overlays.

### Models (7 components, 56.5GB planned)
| Component | Repo @ revision | License (checked from card metadata) | Planned bytes |
|---|---|---|---:|
| Ref2VA UNet pruned INT8 ConvRot | Comfy-Org/MiniMax-H3 @ `e5eb578a89295337b8ff433a035929ce0279e0b6` | minimax-h3-community-license | 20,970,379,616 |
| Text encoder nvfp4/awq (its own pinned quantized form) | same | minimax-h3-community-license | 15,687,142,551 |
| video VAE fp16 + audio VAE fp32 | same | minimax-h3-community-license | 5,813,063,304 |
| h3-realism-people LoRA | fal/MiniMax-H3-Realism-People-LoRA @ `039cc8579d7aa357a882d7f4111b25da4f72dccc` | minimax-h3-community-license (card) | 131,229,656 |
| PDD Acc 8-step (original alibaba-pai keys) | alibaba-pai/MiniMax-H3-Acc-LoRAs @ `335001fb9e5455d68a0caa18ec2e319072150328` | minimax-h3-community-license-agreement (card) | 1,372,450,680 |
| Qwen3-VL-8B-Instruct-FP8 (VLM review) | Qwen @ `9cdc6310a8cb770ce18efaf4e9935334512aee45` | Apache-2.0 (card) | 10,605,572,552 |
| Qwen3-ASR-0.6B (ASR review) | HF tree read at `5eb144179a02acc5e5ba31e748d22b0cf3e303b0` | Apache-2.0 (card) | 1,880,619,678 |

Card license names were read from the HF card metadata (`expand[]=cardData`) at
the pinned revisions; community-license cards link the MiniMax H3 Community
License. These are source/license facts, not rights approval, download approval,
or human quality acceptance.

### Tools (bootstrap manifest)
uv 0.9.5 (GitHub release digest), Node 20.19.5 (SHASUMS256.txt), PowerShell
7.4.12 (GitHub release digest), FFmpeg 8.1.3 BtbN build pinned at tag
`autobuild-2026-10-31-12-59` (GitHub release digest). Downloaded archives are
verified by SHA-256 + exact size before extraction; user-level install, no
system-wide changes.

The vendored `shine_h3_sampling_mode.py` is project-authored (operator's own
file), verified byte-identical in its code body to the original on the
developer machine; no third-party code is involved.


## 2026-11-01 审片服务 wheel 补齐

固定来源为 PyPI 各版本 JSON（https://pypi.org/pypi/{name}/{version}/json），记录实际发行 wheel URL、大小及 SHA-256 至 config/deployment/_upstream-pins/wheels.json；生成目录同时更新。新增 fastapi 0.116.1、httpx 0.28.1、uvicorn 0.35.0、starlette 0.47.2、anyio 4.9.0、h11 0.16.0、httpcore 1.0.9、sniffio 1.3.1、click 8.2.1、python-multipart 0.0.20。仅补入受控下载清单，没有复制第三方源码或下载模型。

Qwen-ASR SDK 完整构建/安装闭包仍未验收；不将上述服务依赖补齐称为 ASR 已可运行。详见 NEW_MACHINE_DEPLOYMENT_FIX_20261101.md。

### 2026-11-01 后续：SDK 固定闭包与实际安装

新增 51 个精确发行包，总输入 154 个，实际大小/哈希全部核验。`review-runtime-metadata.lock` 是 Windows CPython 3.12 元数据解析证据，不替代实际 cu128 文件钉点；`review-runtime-audit.json` 保存从已校验发行包中读取的许可证元数据、依赖元数据及其哈希。sox 1.5.0 的 BSD-3-Clause 源包采用受控离线构建；其产物身份、包输入和构建命令进入原安装尝试审计。未将任何第三方源码收编到项目源码树。

PyTorch 官方索引 https://download.pytorch.org/whl/cu128/torch/ 明确链接 download-r2.pytorch.org 的 2.7.1+cu128 Windows CPython 3.12 wheel；三个 CUDA 包仍保留原精确版本和 SHA-256，只统一目录/安装器主机校验规则。

soynlp 0.0.493 的已校验 wheel 自报 License=UNKNOWN。另核验上游 LGPL v3 许可证，精确 revision=264a05c96f0ccd1961f1a669a9df132076a67a15、文件哈希和 47/51 个 Python 文件对照结果见审计 JSON。四个文件未与该历史快照逐字节吻合，未把该快照冒称为发布 wheel 的完整源码身份；发布输入的身份仍是 PyPI wheel 的精确哈希。soxr 是 LGPL-2.1-or-later；这些依赖只在单独安装环境中使用，没有复制 GPL/LGPL 源码到主项目。该记录不构成额外的发行授权结论。

完整 SDK 安装与 pip check/类导入验证已通过，真实权重加载仍 NOT_RUN。下载主机拒绝和 setuptools vendor 元数据误判的原始失败日志均保留于 .local-acceptance/new-machine-goal-20261101/，修复后使用相同已验证输入重验。


## 2026-11-03 分镜交接与制作入口修复

在本仓库 `0aa6246` 的既有 Gateway、FilmAgentDispatcher、人物绑定、React 工作台上修复；没有引入或复用第三方源码，没有变更上游节点、工作流或许可证边界。验证与实机限制见 WORKBENCH_HANDOFF_FIX_20261103.md。


## 2026-11-05 Qwen-Image-2.1 文生图（导演 AI 素材准备）

基线 `bff25ad`（分支 `codex/director-ai-material-preparation`）。本节记录本机 AI
素材准备所使用的上游工作流模板、节点、权重与许可。没有复制任何第三方源码进本仓库，
没有改动既有 H3 钉点，也没有把权重打进发布包。

### 工作流模板（官方 ComfyUI 文生图模板）

- 来源：<https://github.com/Comfy-Org/workflow_templates/blob/main/templates/image_qwen_image_2_1_t2i.json>
- 固定 commit：`9fae1041be7ecb53bbd7c06533c80aa038f4af93`（2026-09-30，"Qwen Image 2.1: Update PE settings (#1298)"，
  经 GitHub commits API 按 path 查询得到，是该文件最近一次变更）
- 文件：78,494 字节，SHA-256 `5f2b50eeaf27e56a45456681ad5319df1ae51243d9653ac3f92c7dc05d1e1503`
- 已核验：按固定 commit 取回的字节与 `main` 分支当次取回的字节逐字节相同（`cmp` 通过）。
  执行仍只认固定 commit，漂移的 `main` URL 仅作发现入口。
- 格式：**UI 工作流 `version 0.4`，含 1 个 subgraph 实例**（外层节点 `459`，类型
  `c291ceec-b98f-4751-9d0b-7bc288f27b30`，名称 "Text to Image (Qwen Image 2.1)"）。
  UI JSON 不能直接提交 `/prompt`；必须展开子图并转成 API 图后再固定 hash。
- 声明的最终输出节点：外层 `461` `SaveImageAdvanced`（输入 `images` ← 子图输出 `IMAGE` ← 内层 `457` `VAEDecode`）。
- **必须排除的预览节点**：内层 `472` `PreviewAny`。它只把提示词字符串透传给
  `TextEncodeQwenImage21.prompt`，不是图片输出；取图只认 `461`，任何"遍历 history 取第一张
  type=output"的实现都会引入歧义。

### 模板节点与本机可用性（已核验源码存在）

本机既有安装 `F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI`，ComfyUI `v0.39.0`，
git commit `b0b743566f65daafc423b4fea8a2fbda94b3384a`。模板所需节点全部已在该版本
**核心 `comfy_extras`** 中，无需安装任何自定义节点包，因此不需要为兼容性升级或隔离 H3 环境：

| 节点 | 本机源码文件 | 文件 SHA-256 |
| --- | --- | --- |
| `TextEncodeQwenImage21`、`QwenImage21Cache` | `comfy_extras/nodes_qwen.py` | `61a5e27f90bb786cd4f6e76ee86920582ffef2ee3e65cde7b2278c7171c559e8` |
| `TextGenerate` | `comfy_extras/nodes_textgen.py` | `36f219001683e4227cef164506360859740107ba6f6c09477d6171bccc548d3b` |
| `SaveImageAdvanced` | `comfy_extras/nodes_images.py` | `7ba09d1d52b34405eb261f7a91b1cd1de37de28ced4f59f077d42d5925d1bd8c` |
| `ComfySwitchNode` | `comfy_extras/nodes_logic.py` | `e565280ed7afb89c46857ed332f11a45e7dfbf80a94c721fb977b4232d74e4f8` |
| `ResolutionSelector` | `comfy_extras/nodes_resolution.py` | `32549929a0b774cb3fe3bee3ae9a704a59c1e0952997ee2260617b33749f9292` |
| `PrimitiveStringMultiline`、`PreviewAny` | `comfy_extras/nodes_primitive.py` | `58c1a304ea206850ab76c8f572afb7d3ebbd405106108b894428ca1150d464d2` |
| `UNETLoader`、`CLIPLoader`、`VAELoader`、`EmptyLatentImage`、`VAEDecode`、`KSampler` | ComfyUI 核心节点 | 随 `v0.39.0` |

`TextEncodeQwenImage21` 的实机输入（源码 `nodes_qwen.py:111-141`）：`clip`、`prompt`、
`negative_prompt`、`vae`(可选)、`resolution`(默认 1024，0–4096，步长 32)、`images`
(Autogrow `image_1..image_16`，可选)。输出 `positive`/`negative`/`latent`。本轮首版为纯文生图，
不绑定 `images`，因此 `vae` 槽留空（模板中该槽本身未连线）。

### 模板审计出的基线参数（不是猜测，来自模板 widget 值与子图输入顺序的确定映射）

子图 18 个输入按序为 `prompt, switch, thinking, max_length, negative_prompt, cfg, steps,
width, height, scheduler, scheduler_1, seed, unet_name, clip_name, vae_name, clip_name_1,
device, dtype_1`；外层 `459` 的 widget 值按同一顺序给出：

| 参数 | 模板值 | 实际落点 |
| --- | --- | --- |
| `switch` / `thinking` | `false` / `false` | **提示词扩写（PE）关闭**，`ComfySwitchNode` 取 `on_false`，用户提示词经 `PreviewAny` 原样进入 `TextEncodeQwenImage21.prompt` |
| `cfg` | `1` | `KSampler.cfg` |
| `steps` | `25` | `KSampler.steps` |
| `scheduler`（COMBO） | `euler` | `KSampler.sampler_name`（模板命名与落点错位，已核对连线） |
| `scheduler_1`（COMBO） | `simple` | `KSampler.scheduler` |
| `seed` | `447606998181262` | `KSampler.seed`；**48 位**，因此台账 seed 列必须是 BIGINT |
| `width`/`height` | 由外层 `13` `ResolutionSelector`（`1:1 (Square)`）连到子图 → `EmptyLatentImage` | 1024×1024 |
| `max_length` | `4096` | `TextGenerate.max_length`（PE 关闭时不生效） |
| `negative_prompt` | `""` | `TextEncodeQwenImage21.negative_prompt`（图支持独立 negative，可用于 `avoid`） |
| `device`/`dtype_1` | `auto` / `default` | `QwenImage21Cache` |

PE 默认关闭这一点对本产品是决定性的：服务端用确定性模板追加构图规则，若 PE 打开，
模型会先改写提示词，确定性就不成立。本轮保持 `switch=false`。

### 权重来源与许可

模板引用的四个文件名**不在** Qwen 官方仓库中，官方仓库发布的是 diffusers 分片格式。
它们的权威来源是 ComfyUI 官方转换仓库（与本机既有 H3 `int8_convrot` 权重同一发布方
`Comfy-Org`）：

- 仓库：`Comfy-Org/Qwen-Image-2.1`，固定 revision `aacf31a6d48e707f352ecba714cee0680da3479b`
- 下表 SHA-256 取自该 revision 的 HF LFS 元数据（LFS `oid` 即文件内容 SHA-256）

| 文件 | 字节 | SHA-256 | 是否必需 |
| --- | --- | --- | --- |
| `diffusion_models/qwen_image_2.1_int8_convrot.safetensors` | 7,256,783,064 | `cb74113cb03faecd79611b01fd7fd642f0aa60d6f0b95086abee214d75eaa57d` | 必需 |
| `text_encoders/qwen3vl_8b_int8_convrot.safetensors` | 9,350,798,360 | `8bfd0f6e12abf2d2d697ecc888e5e90b0d6741d6708f05799f53afa560452e8f` | 必需 |
| `text_encoders/qwen3.5_9b_qwen_image_2.1_pe_t2i.int8_convrot.safetensors` | 9,471,072,252 | `9182abae56fe05459840a86d22abd21f972061c92fce032630af680c8c5178d3` | **不需要** |
| `vae/qwen_image_2.1_vae_bf16.safetensors` | 675,509,688 | `bb21f7473051e1ac368515dd3f2e15cd44d7a11748ee8823e1ddca3e4876b7c9` | 必需 |
| **必需合计** | **17,283,091,112（16.10 GiB）** | | |

**第四个文件（提示词扩写编码器）本产品不安装**：模板出厂 `switch=false`，而 `ComfySwitchNode`
的两个分支都是 `lazy=True`，`check_lazy_status` 只在开关为真时才请求 `on_true`。
既然 PE 关闭（见上节，本产品的确定性模板要求它关闭），该分支永不被求值，API 图里也没有它。
留下这份 9.47 GB 权重只会让 ComfyUI 把它列为可选模型并可能被误选，而且本机那份下载不完整
（9,370,058,752 / 9,471,072,252），故已从 `models/` 删除。上表的「不需要」一行保留它的
字节数与 SHA-256，作为该 revision 的事实记录，不是安装清单。

产品侧的安装清单是 `config/deployment/qwen-image-2.1-windows-x64.json`（3 个必需权重，
逐文件钉死 SHA-256 与字节数，标注研究用途），走既有受控安装路径；
`backend/tests/test_image_installation_catalog.py` 锁住「目录与工作流钉点逐字节一致」。

官方模型仓库（许可与出处，不作为 ComfyUI 单文件权重的来源）：

- `Qwen/Qwen-Image-2.1`，revision `d26bb61231c349cf6b7896fa83353113880e1ba3`，
  lastModified `2026-09-30T02:14:09Z`，仓库总存储 47,363,299,961 字节，28 个文件
  （`transformer/`、`text_encoder/`、`vae/` 分片 + processor/scheduler 配置）。
- 许可证原文已取回核验（7,821 字符）：**"Qwen RESEARCH LICENSE AGREEMENT"，Release Date
  September 20, 2026**，版权方 Hangzhou Tongyi Laboratory Technology Co., Ltd.
  - 第 1.i 条："Non-Commercial" shall mean **for research or evaluation purposes only**。
  - 第 2.a 条：非独占、全球、不可转让、免版税的有限许可。
  - 第 2.b 条：未经另行取得商业许可**不得用于任何商业目的**（联系 model-business@）。
  - 第 2.c 条：再分发副本须在 "Notice" 文本中保留归属声明。
- 本轮用途严格限定为**本地研究与评估**。资产晋升不产生任何商业授权证明，UI 不得宣称
  商业可用。`Comfy-Org` 转换权重的许可边界按其仓库声明执行，安装前须逐文件复核；
  转换件的许可证文本尚未单独取回核验，记为 `PENDING_VERIFICATION`。

### 网络事实（影响安装器）

本机**无法访问 `huggingface.co`**（`curl` 返回 `http=000`，连接失败），
`hf-mirror.com` 可访问并提供同一 LFS 对象（VAE 文件已按上表 SHA-256 完整校验通过）。

安装器本身已具备**逐字节镜像回退**：`installer.mirror_url()` 把 `huggingface.co/<owner>/<repo>/resolve/<rev>/<path>`
改写为同源的 `hf-mirror.com` 地址，`hf-mirror.com` 与 `cas-bridge.xethub.hf.co` 都在
`TRUSTED_DOWNLOAD_HOSTS` 内，回退只允许跟随这两个主机，**每个字节仍按钉死的 SHA-256 与字节数校验**。
因此新的图片安装目录 `config/deployment/qwen-image-2.1-windows-x64.json` 可以继续使用
`huggingface.co` 作为权威来源地址：主站可达时直连，不可达时走镜像，且镜像给出不同字节会**响亮失败**
而不是被接受。既有 `config/deployment/h3-ref2va-windows-x64.json` 同样适用该回退。

### 本节验证边界

已核验：模板字节与固定 commit 一致；模板所需节点全部存在于本机 ComfyUI `v0.39.0` 核心源码；
四个权重文件的权威来源、精确 revision、字节数与 SHA-256；官方许可证原文条款；**三个必需权重已下载到本机
ComfyUI 的 `models/` 并逐文件 SHA-256 校验通过**（VAE、扩散模型、文本编码器，合计 16.10 GiB），
第四个 PE 编码器按上节判定不需要并已从本机 `models/` 删除。

已用**协议忠实的替身渲染器**验证（`backend/tests/test_material_render_e2e.py`）：本地回环 HTTP 服务按
ComfyUI 的真实报文形状回答 `/object_info`、`/prompt`、`/history`、`/view`、`/queue`、`/system_stats`，
工作台用**真实** `ComfyUIClient` 走完全链路——活体节点校验 → 提交意图落库 → `/prompt` → prompt_id 落库 →
轮询 → 只取声明输出节点 → 字节校验 → 候选登记 → 技术质检 → 第四层就绪证据由台账推导。
**这一段验证的是工作台侧，替身渲染器不是真机。**

`PENDING_VERIFICATION`（不得声称已完成）：受管实例的实机 `object_info` 节点校验；真机 `/prompt` 提交与
真机确实产出声明输出节点；三类（角色/场景/道具）真实出图；实际显存占用与
生成耗时（本机 RTX 5080 16 GB、系统内存 31.7 GB，必需权重合计 16.10 GiB，必然涉及 offload，
**未实测前不得给出显存下限数字**）；`Comfy-Org` 转换权重的许可证文本。

### 本轮实际采用范围（2026-11-08 收尾轮）

上节记录的是设计借鉴。本轮在 Shine 既有合同内**独立实现**，未复制上述任一项目的代码、
prompt、UI 或素材；许可证与使用领域约束未因此变化。

| 上游模块 | 实际采用的可验证约束 | 落地位置 |
| --- | --- | --- |
| Toonflow-app（事件来源、分集剧本、分镜回读） | 蓝图输入必须是当前计划逐卷的有序大纲集合；分镜按「集→场→片段→镜」保留场到镜头的映射 | `backend/shine/director/pipeline.py`（`resolve_plan_outlines`、`storyboard_units`、`_section_shot_groups`） |
| Huobao Drama（`validateStoryboardBindings`） | 交付前对卷/集/场/Shot revision 做集合校验，越界在写副作用前拒绝 | `build_volume_handoff`（`PIPELINE_SCENE_WITHOUT_SHOT`、`PIPELINE_SHOT_CLAIMED_TWICE`） |
| NarratoAI（解析与内容验收分层） | 结果分类（NOT_SENT / RESPONSE_RECEIVED / UNKNOWN）与内容验收分离，截断进入未完成 | `backend/shine/materialprep/agent_call.py`、`director/pipeline.py`（`_send_outcome_unknown`） |
| Temporal（Activity 幂等与崩溃窗口） | SQLite 本地 fence + 持久回执 + 孤儿结果唯一采纳；外部副作用从不盲目重放 | `_commit_pipeline_result`、`_adopt_orphan_result`、`start_pipeline_new_attempt` |
