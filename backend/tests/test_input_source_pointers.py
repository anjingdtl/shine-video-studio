"""V2 judgments cite input paths; immutable quotations belong to the server."""

import copy

import pytest

from tests.test_style_input_assessment import setup_inputs


@pytest.mark.parametrize("pointer", ["/story_beat", "/missing", "/duration_target_s", "/camera", "/story_beat/0"])
def test_v2_resolves_only_existing_text_sources_without_model_quote_override(api, pointer):
    client, _, _ = api
    project, _, spec, old = setup_inputs(client)
    checks = [{k: v for k, v in row.items() if k not in {"style_text", "shot_text"}} for row in old["checks"]]
    for check in checks:
        check["shot_path"] = None
    checks[1]["shot_path"] = pointer
    response = dict(protocol="independent-text-v2", checks=checks, recommendation="needs_review")
    client.app.state.mock_llm_responder = lambda *_: copy.deepcopy(response)
    result = client.post(f"/api/v1/projects/{project}/film-agents/qc_reviewer/run", json={"shot_spec": spec})
    if pointer == "/story_beat":
        assert result.status_code == 201, result.text
        check = result.json()["payload_json"]["checks"][1]
        assert check["style_text"] == "The character sits centre-left."
        assert check["shot_text"] == spec["story_beat"]
        assert check["shot_path"] == pointer
    else:
        assert result.status_code == 422, result.text


def test_role_metadata_distinguishes_model_wire_from_stored_evidence(api):
    client, _, _ = api
    role = next(r for r in client.get("/api/v1/film-agents").json() if r["role"] == "qc_reviewer")
    assert role["output_kind"] == "StyleInputAssessment"
    assert role["model_response_protocol"] == "independent-text-v2"
    assert role["model_response_schema"]["properties"]["protocol"]["const"] == "independent-text-v2"
    assert "style_text" in role["output_schema"]["$defs"]["StyleInputComparison"]["properties"]
    assert "style_text" not in role["model_response_schema"]["$defs"]["StyleInputJudgment"]["properties"]


@pytest.mark.parametrize("pointer", ["/locked_story_facts/0", "/locked_story_facts/-1", "/locked_story_facts/00",
                                    "/camera/~2bad", "/camera/a~1b", "/camera/a~0b"])
def test_pointer_lists_and_escaping_are_strict_existing_data_only(pointer):
    from shine.agents.film import input_text_at

    spec = {"locked_story_facts": ["original fact"], "camera": {"a/b": "slash source", "a~b": "tilde source"}}
    valid = {"/locked_story_facts/0": "original fact", "/camera/a~1b": "slash source", "/camera/a~0b": "tilde source"}
    if pointer in valid:
        assert input_text_at(spec, pointer) == valid[pointer]
    else:
        with pytest.raises(ValueError, match="STYLE_INPUT_SHOT_PATH_INVALID"):
            input_text_at(spec, pointer)
