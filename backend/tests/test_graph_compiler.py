"""Per-shot dynamic graph compiler: legality, limits, second pass, identity.

Covers the WP3/WP4 contract:

* any legal image/audio mix compiles to a distinct valid graph with the
  references in manifest order and no leftover loader;
* the official limits are named refusals, never silent truncation (T03);
* a compiled graph is deterministic - same recipe in, same hash out - and a
  changed recipe changes the hash (T08);
* the true second sampling stage is a second scheduler + model patch +
  refine handoff fed by the first pass' latent, with the first pass' audio
  decode reused, and its parameters are bounded;
* the compiled workflow feeds the existing slot resolver so the provider
  chain keeps working unchanged (T01/T02);
* variants pick their own checkpoint from the audited manifest, never a
  silent substitution (T05).
"""
import pytest

from shine.comfy.graph_compiler import (
    ACCELERATORS,
    GraphCompileError,
    SecondPassSpec,
    compile_ref2va_workflow,
)
from shine.comfy.provider import resolve_reference_slots
from shine.comfy.workflow import graph_hash


def _fake_object_info(graph) -> dict:
    """An object_info that knows exactly the classes and inputs of a graph."""
    info: dict = {}
    for node in graph.values():
        cls = node["class_type"]
        required: dict = {}
        for key in node.get("inputs") or {}:
            if "." in key:
                # Autogrow groups are declared once by their leading segment.
                required.setdefault(key.split(".", 1)[0], ["COMFY_AUTOGROW_V3"])
            else:
                required.setdefault(key, [])
        info.setdefault(cls, {"input": {"required": required, "optional": {}}})
    return info


@pytest.mark.parametrize("images", [1, 2, 4, 6, 9])
def test_legal_image_counts_compile_distinct_graphs(images):
    compiled = compile_ref2va_workflow(image_count=images)
    assert compiled.graph_sha256
    assert len([k for k in compiled.parameter_map if k.startswith("reference_image_")]) == images
    loaders = [n for n in compiled.graph.values() if n["class_type"] == "LoadImage"]
    assert len(loaders) == images
    # Every loader is wired into the reference node in order.
    ref_inputs = compiled.graph["31"]["inputs"]
    for index in range(images):
        assert ref_inputs[f"ref_images.ref_image_{index}"] == [str(40 + index), 0]
    # No placeholder survives: each loader is used by its own mapping.
    used = {path.split(".", 1)[0] for path in compiled.parameter_map.values()}
    assert all(node_id in used for node_id in (str(40 + i) for i in range(images)))


def test_each_image_count_has_its_own_graph_hash():
    hashes = {
        compile_ref2va_workflow(image_count=n).graph_sha256 for n in (1, 2, 4, 6, 9)
    }
    assert len(hashes) == 5


@pytest.mark.parametrize("images,audio", [(3, 1), (6, 3), (1, 1)])
def test_legal_audio_mixes_declare_audio_transfer_and_slots(images, audio):
    compiled = compile_ref2va_workflow(image_count=images, audio_count=audio)
    assert "audio" in compiled.media_transfers
    assert len([k for k in compiled.parameter_map if k.startswith("reference_audio_")]) == audio
    assert "VHS_LoadAudioUpload" in compiled.required_nodes
    for index in range(audio):
        assert compiled.graph["31"]["inputs"][f"ref_audios.ref_audio_{index}"] == [str(23 + index), 0]


def test_audio_free_graph_has_no_audio_transfer():
    compiled = compile_ref2va_workflow(image_count=2)
    assert "audio" not in compiled.media_transfers
    assert "VHS_LoadAudioUpload" not in compiled.required_nodes


def test_ten_images_is_a_named_refusal():
    with pytest.raises(GraphCompileError) as exc:
        compile_ref2va_workflow(image_count=10)
    assert exc.value.code == "IMAGE_REFERENCE_LIMIT_EXCEEDED"


def test_zero_images_audio_only_is_refused():
    with pytest.raises(GraphCompileError) as exc:
        compile_ref2va_workflow(image_count=0, audio_count=2)
    assert exc.value.code == "REF2VA_IMAGE_REFERENCE_REQUIRED"


def test_four_audio_is_a_named_refusal():
    with pytest.raises(GraphCompileError) as exc:
        compile_ref2va_workflow(image_count=1, audio_count=4)
    assert exc.value.code == "AUDIO_REFERENCE_LIMIT_EXCEEDED"


def test_video_reference_is_refused_by_name_until_verified():
    with pytest.raises(GraphCompileError) as exc:
        compile_ref2va_workflow(image_count=2, video_count=1)
    assert exc.value.code == "VIDEO_REFERENCE_LOADER_UNVERIFIED"


def test_compilation_is_deterministic():
    a = compile_ref2va_workflow(image_count=3, audio_count=1)
    b = compile_ref2va_workflow(image_count=3, audio_count=1)
    assert a.graph_sha256 == b.graph_sha256
    assert a.recipe_sha256 == b.recipe_sha256
    assert graph_hash(a.graph) == graph_hash(b.graph)


def test_second_pass_adds_a_real_second_sampler_stage():
    spec = SecondPassSpec()
    compiled = compile_ref2va_workflow(image_count=2, second_pass=spec)
    # The refine handoff consumes the first pass latent via VRAM_Debug's
    # pass-through input and its own scheduler/model patch.
    assert compiled.graph["60"]["inputs"]["any_input"] == ["53", 0]
    assert compiled.graph["61"]["class_type"] == "MiniMaxH3PDDAccScheduler"
    assert compiled.graph["61"]["inputs"]["denoise"] == spec.denoise
    assert compiled.graph["62"]["class_type"] == "MiniMaxH3PDDAccApply"
    handoff = compiled.graph["63"]
    assert handoff["class_type"] == "MinimaxH3LatentUpscaler3DRefineHandoff"
    assert handoff["inputs"]["latent"] == ["60", 0]
    assert handoff["inputs"]["noise"] == ["51", 0]
    assert handoff["inputs"]["sigmas"] == ["61", 0]
    assert handoff["inputs"]["model"] == ["62", 0]
    assert handoff["inputs"]["positive"] == ["31", 0]
    # The packed audio is the FIRST pass decode; the refine stage never
    # re-synthesizes audio.
    assert compiled.graph["65"]["inputs"]["audio"] == ["55", 0]
    # The single-pass output branch is gone.
    assert "54" not in compiled.graph and "56" not in compiled.graph and "57" not in compiled.graph
    assert {n for n in compiled.required_nodes} >= {
        "MinimaxH3LatentUpscaler3DRefineHandoff",
        "MiniMaxH3PDDAccScheduler",
        "VRAM_Debug",
    }


def test_second_pass_parameter_change_changes_graph_hash():
    plain = compile_ref2va_workflow(image_count=2)
    tuned = compile_ref2va_workflow(image_count=2, second_pass=SecondPassSpec(denoise=0.35))
    assert plain.graph_sha256 != tuned.graph_sha256
    assert plain.recipe_sha256 != tuned.recipe_sha256


def test_second_pass_scale_changes_output_size_contract():
    scaled = compile_ref2va_workflow(
        image_count=2, second_pass=SecondPassSpec(latent_scale=1.5))
    assert scaled.graph["63"]["inputs"]["scale"] == 1.5
    same_size = compile_ref2va_workflow(image_count=2, second_pass=SecondPassSpec())
    assert same_size.graph["63"]["inputs"]["scale"] == 1.0


@pytest.mark.parametrize("kwargs", [
    {"denoise": 0.0}, {"denoise": 1.5}, {"latent_scale": 0.5}, {"latent_scale": 3.0},
])
def test_second_pass_bounds_are_refused(kwargs):
    with pytest.raises(GraphCompileError) as exc:
        compile_ref2va_workflow(image_count=1, second_pass=SecondPassSpec(**kwargs))
    assert exc.value.code in {"SECOND_PASS_DENOISE_OUT_OF_RANGE", "SECOND_PASS_SCALE_OUT_OF_RANGE"}


def test_native20_omits_pdd_nodes_and_rewires_sigmas():
    compiled = compile_ref2va_workflow(image_count=2, accelerator_id="native20")
    classes = {n["class_type"] for n in compiled.graph.values()}
    assert "MiniMaxH3PDDAccApply" not in classes
    assert "ShineH3SamplingMode" not in classes
    assert compiled.graph["50"]["inputs"]["model"] == ["16", 0]
    assert compiled.graph["53"]["inputs"]["sigmas"] == ["17", 0]
    assert compiled.graph["17"]["inputs"]["steps"] == 20
    assert compiled.graph_sha256 != compile_ref2va_workflow(image_count=2).graph_sha256


def test_unverified_accelerator_is_refused_by_name():
    with pytest.raises(GraphCompileError) as exc:
        compile_ref2va_workflow(image_count=2, accelerator_id="turbo4")
    assert exc.value.code == "ACCELERATOR_UNVERIFIED"
    assert "turbo4" in ACCELERATORS  # declared so the UI can name it


def test_unknown_accelerator_is_refused():
    with pytest.raises(GraphCompileError) as exc:
        compile_ref2va_workflow(image_count=2, accelerator_id="pdd99")
    assert exc.value.code == "ACCELERATOR_UNKNOWN"


def test_candidate_variant_uses_its_own_declared_checkpoint():
    compiled = compile_ref2va_workflow(image_count=2, variant_id="ref2va-lowend-candidate", accelerator_id="native20")
    assert compiled.checkpoint_id.endswith("minimax_h3_ref2va_pruned_w6a8.safetensors")
    assert compiled.graph["10"]["inputs"]["unet_name"] == compiled.checkpoint_id
    baseline = compile_ref2va_workflow(image_count=2)
    assert baseline.checkpoint_id != compiled.checkpoint_id
    assert baseline.graph_sha256 != compiled.graph_sha256


def test_unknown_variant_is_refused_not_substituted():
    with pytest.raises((GraphCompileError, FileNotFoundError)):
        compile_ref2va_workflow(image_count=2, variant_id="no-such-variant")


def test_compiled_parameter_map_resolves_via_existing_slot_resolver():
    """The provider's positional resolver must accept any compiled mix."""
    from types import SimpleNamespace

    compiled = compile_ref2va_workflow(image_count=5, audio_count=2)
    manifest = (
        [SimpleNamespace(use_as=SimpleNamespace(value="subject"), asset_id=f"a{i}") for i in range(2)]
        + [SimpleNamespace(use_as=SimpleNamespace(value="picture"), asset_id=f"p{i}") for i in range(3)]
        + [SimpleNamespace(use_as=SimpleNamespace(value="audio"), asset_id=f"au{i}") for i in range(2)]
    )
    slots = resolve_reference_slots(compiled.parameter_map, manifest)
    assert len(slots) == 7
    # Images share one positional sequence; audio keeps its own family keys.
    image_params = [param for _logical, param, _item in slots
                    if param.startswith("reference_image_")]
    audio_params = [param for _logical, param, _item in slots
                    if param.startswith("reference_audio_")]
    assert sorted(image_params) == [f"reference_image_{i}" for i in range(1, 6)]
    assert sorted(audio_params) == ["reference_audio_1", "reference_audio_2"]
    # Every resolved parameter really exists in the compiled map.
    assert all(param in compiled.parameter_map for _logical, param, _item in slots)


def test_validation_against_object_info_reports_missing_nodes():
    compiled = compile_ref2va_workflow(image_count=2)
    good = _fake_object_info(compiled.graph)
    assert compiled.validated_against is None  # object_info not supplied
    checked = compile_ref2va_workflow(image_count=2, object_info=good)
    assert checked.validated_against == {"missing_nodes": [], "missing_inputs": []}
    stripped = {k: v for k, v in good.items() if k != "SolAttnPatch"}
    broken = compile_ref2va_workflow(image_count=2, object_info=stripped)
    assert "SolAttnPatch" in broken.validated_against["missing_nodes"]


def test_execution_identity_snapshot_is_complete_and_hashable():
    compiled = compile_ref2va_workflow(
        image_count=3, audio_count=1, second_pass=SecondPassSpec(denoise=0.3))
    identity = compiled.execution_identity()
    assert identity["compiler"] == "shine.graph_compiler.v1"
    assert identity["reference_counts"] == {"image": 3, "audio": 1, "video": 0}
    assert identity["second_pass"]
    assert identity["recipe_sha256"] == compiled.recipe_sha256
    assert graph_hash(identity)


def test_candidate_stack_uses_pinned_recipe_and_refuses_unverified_combinations():
    compiled = compile_ref2va_workflow(image_count=2, variant_id="ref2va-lowend-candidate", accelerator_id="native20")
    assert compiled.execution_identity()["compiler"] == "shine.graph_compiler.v2"
    assert compiled.graph["21"]["inputs"]["vae_name"].endswith("minimax_h3_video_vae_int8_convrot.safetensors")
    assert not any(node["class_type"] in {"LoraLoaderModelOnly", "SolAttnPatch"} for node in compiled.graph.values())
    assert "MiniMaxH3MemoryEfficientSageAttentionPatch" in compiled.required_nodes
    for accelerator, second in (("pdd8", None), ("native20", SecondPassSpec())):
        with pytest.raises(GraphCompileError, match="候选模型"):
            compile_ref2va_workflow(
                image_count=2, variant_id="ref2va-lowend-candidate",
                accelerator_id=accelerator, second_pass=second,
            )
