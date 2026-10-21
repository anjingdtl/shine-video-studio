# Upstream source and license audit

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
