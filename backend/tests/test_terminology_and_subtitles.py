"""The script terminology table and the external subtitle layer.

A Chinese recognizer reads a proper name far more reliably when it is told
which spelling the script uses, and the subtitles have to stay outside the
rendered shot so that a human can revise them without rendering again.

These tests cover both contracts, including the failure windows that a real
recognizer makes expensive to reproduce.
"""

from __future__ import annotations

import pytest

from shine.media.subtitles import (
    SubtitleCue,
    SubtitleError,
    cues_from_alignment,
    parse_srt,
    render_ass,
    render_srt,
    sha256_text,
)
from shine.quality.terminology import (
    TerminologyError,
    parse_table,
    terms_from_dialogue,
)

from .conftest import create_project

# ---------------------------------------------------------------------------
# The terminology table
# ---------------------------------------------------------------------------


def test_a_project_without_a_table_produces_no_hint(api):
    from shine.services import project_terminology_hint

    client, factory, settings = api
    project_id = create_project(client, "无术语表")
    with factory() as db:
        assert project_terminology_hint(db, project_id) == ""


def test_the_hint_names_every_term_and_never_a_mishearing():
    """A mishearing that is named in the context becomes an acceptable answer.

    The measured behaviour of the local recognizer shows that listing the
    known wrong spellings next to the correct one makes it produce the wrong
    characters again, so only the canonical spellings are named.
    """
    table = parse_table([
        {"canonical": "周砚", "aliases": ["周烨", "周严"]},
        {"canonical": "林知远"},
    ])
    hint = table.hint()
    assert "周砚" in hint
    assert "林知远" in hint
    # The hint tells the recognizer which spelling the script uses.
    assert "标准写法" in hint
    # The known mishearings stay with the human and never reach the model.
    assert "周烨" not in hint
    assert "周严" not in hint


def test_a_duplicate_or_empty_term_is_refused():
    with pytest.raises(TerminologyError, match="TERMINOLOGY_TERM_EMPTY"):
        parse_table([{"canonical": "  "}])
    with pytest.raises(TerminologyError, match="TERMINOLOGY_TERM_DUPLICATED"):
        parse_table([{"canonical": "周砚"}, {"canonical": "周砚"}])


def test_a_malformed_row_never_becomes_a_hint():
    with pytest.raises(TerminologyError, match="TERMINOLOGY_ALIASES_INVALID"):
        parse_table([{"canonical": "周砚", "aliases": "周烨"}])


def test_the_table_is_stored_as_a_new_revision_and_never_rewritten(api):
    from shine.services import project_terminology_hint, save_project_terminology

    client, factory, settings = api
    project_id = create_project(client, "术语表")
    with factory() as db:
        from shine.models import Project

        project = db.get(Project, project_id)
        first = save_project_terminology(
            db, project, terms=[{"canonical": "周砚"}], created_by="operator")
        db.commit()
        second = save_project_terminology(
            db, project, terms=[{"canonical": "周砚"}, {"canonical": "林知远"}], created_by="operator")
        db.commit()
        assert first.revision == 1
        assert second.revision == 2
        # The earlier revision is untouched.
        assert first.terms == [{"canonical": "周砚", "aliases": []}]
        expected = parse_table(second.terms, second.notes).hint()
        assert project_terminology_hint(db, project_id) == expected


def test_the_script_mentions_seed_the_table_for_a_human_to_confirm():
    class _Line:
        def __init__(self, text):
            self.text = text

    found = terms_from_dialogue([_Line("把陆清明的笔记本交给周砚")])
    assert "周砚" in found
    assert "陆清明" in found

# ---------------------------------------------------------------------------
# D-R4-01：人名对照表保存必然失败
# ---------------------------------------------------------------------------


def test_saving_a_real_term_through_the_api_succeeds(api):
    """填一条真实词条曾必然 422：模型对象被当成 dict 校验（TERMINOLOGY_ROW_INVALID）。

    表格要真能存进去，ASR 的专名提示才有词条可用。
    """
    client, factory, settings = api
    project_id = create_project(client, "人名对照表")
    response = client.post(
        f"/api/v1/projects/{project_id}/terminology",
        json={"terms": [{"canonical": "周砚", "aliases": ["周烨"]}]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revision"] == 1
    assert body["terms"] == [{"canonical": "周砚", "aliases": ["周烨"]}]


def test_an_empty_table_is_still_accepted(api):
    """留空曾能成功；这条守住修复没有把空表也一起拒掉。"""
    client, factory, settings = api
    project_id = create_project(client, "空表")
    response = client.post(f"/api/v1/projects/{project_id}/terminology", json={"terms": []})
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 1


def test_an_illegal_term_still_fails_with_its_named_error(api):
    """入口防御不得吞掉具名错误：空白词条仍是 TERMINOLOGY_TERM_EMPTY。"""
    client, factory, settings = api
    project_id = create_project(client, "非法词条")
    response = client.post(
        f"/api/v1/projects/{project_id}/terminology",
        json={"terms": [{"canonical": "   "}]},
    )
    assert response.status_code == 422, response.text
    assert "TERMINOLOGY_TERM_EMPTY" in response.text


def test_the_api_hands_the_service_plain_rows(api, monkeypatch):
    """入口必须把模型转成 plain dict：服务层的防御是第二道闸，不是唯一的一道路。"""
    from fastapi import HTTPException

    import shine.services as services_module

    client, factory, settings = api
    project_id = create_project(client, "传递类型")
    captured: dict[str, list[str]] = {}

    def capture(db, project, *, terms, notes, created_by, source):
        captured["types"] = [type(row).__name__ for row in terms]
        raise HTTPException(status_code=500, detail="captured")

    monkeypatch.setattr(services_module, "save_project_terminology", capture)
    response = client.post(
        f"/api/v1/projects/{project_id}/terminology",
        json={"terms": [{"canonical": "周砚"}]},
    )
    assert response.status_code == 500, response.text
    assert captured["types"] == ["dict"]


def test_the_service_accepts_the_models_the_api_hands_over(api):
    """服务层入口的防御：模型也能存，不必每个调用方都记得转换。"""
    from shine.models import Project
    from shine.schemas import TerminologyTerm
    from shine.services import save_project_terminology

    client, factory, settings = api
    project_id = create_project(client, "模型行")
    with factory() as db:
        project = db.get(Project, project_id)
        row = save_project_terminology(
            db, project, terms=[TerminologyTerm(canonical="周砚", aliases=["周烨"])], created_by="operator")
        db.commit()
        assert row.revision == 1
        assert row.terms == [{"canonical": "周砚", "aliases": ["周烨"]}]


def test_the_service_refuses_a_row_it_cannot_read_and_keeps_the_named_errors(api):
    """读不懂的行仍是具名错误；别名不是列表也仍是具名错误，都不被吞。"""
    from fastapi import HTTPException

    from shine.models import Project
    from shine.services import save_project_terminology

    client, factory, settings = api
    project_id = create_project(client, "坏行")
    with factory() as db:
        project = db.get(Project, project_id)
        with pytest.raises(HTTPException) as unreadable:
            save_project_terminology(db, project, terms=["周砚"], created_by="operator")
        assert unreadable.value.status_code == 422
        assert unreadable.value.detail == "TERMINOLOGY_ROW_INVALID"

        with pytest.raises(HTTPException) as bad_aliases:
            save_project_terminology(
                db, project, terms=[{"canonical": "周砚", "aliases": "周烨"}], created_by="operator")
        assert bad_aliases.value.status_code == 422
        assert "TERMINOLOGY_ALIASES_INVALID" in str(bad_aliases.value.detail)


# ---------------------------------------------------------------------------
# The ASR hint reaches the recognizer
# ---------------------------------------------------------------------------


def test_the_asr_reviewer_sends_the_terminology_as_a_hint(api, monkeypatch):
    """The hint is a prompt for the recognizer, never a rewrite of its answer."""
    from shine.quality.semantic import OpenAICompatibleASRReviewer

    client, factory, settings = api
    captured: dict = {}

    class _Client:
        def __init__(self, **kwargs):
            captured["timeout"] = kwargs.get("timeout")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers=None, data=None, files=None):
            captured["data"] = data
            captured["url"] = url

            class _Response:
                status_code = 200
                content = b"{}"

                @staticmethod
                def json():
                    return {"text": "账册在老地方。", "segments": [], "is_mock": False}

            return _Response()

    monkeypatch.setattr("httpx.Client", _Client)
    reviewer = OpenAICompatibleASRReviewer(_config())
    transcript = reviewer.transcribe(_wav(), context="周砚（周烨）")
    assert captured["data"]["prompt"] == "周砚（周烨）"
    assert captured["data"]["response_format"] == "verbose_json"
    # The transcript is exactly what the recognizer returned.
    assert transcript.text == "账册在老地方。"


def test_a_project_without_a_table_sends_no_hint(api, monkeypatch):
    from shine.quality.semantic import OpenAICompatibleASRReviewer

    client, factory, settings = api
    captured: dict = {}

    class _Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers=None, data=None, files=None):
            captured["data"] = data

            class _Response:
                status_code = 200
                content = b"{}"

                @staticmethod
                def json():
                    return {"text": "账册在老地方。", "segments": [], "is_mock": False}

            return _Response()

    monkeypatch.setattr("httpx.Client", _Client)
    reviewer = OpenAICompatibleASRReviewer(_config())
    reviewer.transcribe(_wav())
    assert "prompt" not in captured["data"]


def _config():
    from shine.llm.base import ProviderConfig

    return ProviderConfig(
        profile_id="profile-asr",
        name="local-asr",
        kind="ASR",
        base_url="http://127.0.0.1:8117/v1",
        model="local-qwen3-asr-0.6b",
        api_key=None,
        timeout_s=30,
    )


def _wav():
    import wave
    from pathlib import Path

    path = Path(_tmpdir()) / "evidence.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * 1600)
    return path


def _tmpdir():
    import tempfile

    global _TMPDIR
    try:
        return _TMPDIR
    except NameError:
        _TMPDIR = tempfile.mkdtemp(prefix="shine-terminology-")
        return _TMPDIR


# ---------------------------------------------------------------------------
# The external subtitle layer
# ---------------------------------------------------------------------------


def test_a_subtitle_revision_is_stored_and_kept(api):
    client, factory, settings = api
    project_id = create_project(client, "字幕")
    created = client.post(f"/api/v1/projects/{project_id}/subtitles", json={
        "cues": [
            {"index": 1, "start_s": 0.0, "end_s": 2.5, "text": "账册在老地方。"},
            {"index": 2, "start_s": 2.5, "end_s": 5.0, "text": "别让人看见。"},
        ],
        "created_by": "operator",
    })
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["revision"] == 1
    assert body["status"] == "DRAFT"
    assert body["sha256"] == sha256_text(render_srt([
        SubtitleCue(index=1, start_s=0.0, end_s=2.5, text="账册在老地方。"),
        SubtitleCue(index=2, start_s=2.5, end_s=5.0, text="别让人看见。"),
    ]))

    revised = client.post(f"/api/v1/projects/{project_id}/subtitles", json={
        "srt": "1\n00:00:00,000 --> 00:00:02,500\n账册在老地方。\n",
        "created_by": "operator",
    })
    assert revised.status_code == 201, revised.text
    assert revised.json()["revision"] == 2

    listed = client.get(f"/api/v1/projects/{project_id}/subtitles")
    assert listed.status_code == 200
    assert [row["revision"] for row in listed.json()] == [2, 1]


def test_a_revised_srt_wins_over_the_cue_list(api):
    client, factory, settings = api
    project_id = create_project(client, "字幕修订")
    response = client.post(f"/api/v1/projects/{project_id}/subtitles", json={
        "cues": [{"index": 1, "start_s": 0.0, "end_s": 1.0, "text": "旧台词"}],
        "srt": "1\n00:00:01,000 --> 00:00:03,000\n新台词\n",
        "created_by": "operator",
    })
    assert response.status_code == 201, response.text
    cues = response.json()["cues"]
    assert cues[0]["text"] == "新台词"
    assert cues[0]["start_s"] == 1.0


def test_an_invalid_subtitle_is_refused(api):
    client, factory, settings = api
    project_id = create_project(client, "非法字幕")
    empty = client.post(f"/api/v1/projects/{project_id}/subtitles", json={"cues": []})
    assert empty.status_code == 422
    broken = client.post(f"/api/v1/projects/{project_id}/subtitles", json={
        "srt": "1\nnot a timing\n台词\n",
    })
    assert broken.status_code == 422


def test_only_an_approved_revision_may_be_composited(api):
    client, factory, settings = api
    project_id = create_project(client, "字幕审批")
    created = client.post(f"/api/v1/projects/{project_id}/subtitles", json={
        "cues": [{"index": 1, "start_s": 0.0, "end_s": 1.0, "text": "台词"}],
    })
    assert created.status_code == 201, created.text
    approved = client.post(f"/api/v1/projects/{project_id}/subtitles/1/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED"


def test_the_subtitle_file_is_exported_for_a_human_to_edit(api):
    client, factory, settings = api
    project_id = create_project(client, "字幕导出")
    client.post(f"/api/v1/projects/{project_id}/subtitles", json={
        "cues": [{"index": 1, "start_s": 0.0, "end_s": 2.0, "text": "账册在老地方。"}],
    })
    response = client.get(f"/api/v1/projects/{project_id}/subtitles/1/export?format=srt")
    assert response.status_code == 200
    assert "账册在老地方。" in response.text
    assert "-->" in response.text


def test_the_srt_round_trips_through_a_human_revision():
    cues = [
        SubtitleCue(index=1, start_s=0.0, end_s=2.5, text="账册在老地方。"),
        SubtitleCue(index=2, start_s=2.5, end_s=5.0, text="别让人看见。"),
    ]
    text = render_srt(cues)
    parsed = parse_srt(text)
    assert [cue.text for cue in parsed] == ["账册在老地方。", "别让人看见。"]
    assert parsed[0].start_s == 0.0
    assert parsed[1].end_s == 5.0


def test_the_ass_file_carries_the_timing_that_ffmpeg_composites():
    cues = [SubtitleCue(index=1, start_s=1.5, end_s=3.25, text="账册在老地方。")]
    text = render_ass(cues)
    assert "Dialogue: 0,0:00:01.50,0:00:03.25" in text
    assert "账册在老地方。" in text


def test_the_alignment_is_grouped_into_subtitle_lines():
    words = [
        {"text": "账", "start_s": 0.0, "end_s": 0.2},
        {"text": "册", "start_s": 0.2, "end_s": 0.4},
        {"text": "在", "start_s": 0.4, "end_s": 0.6},
        {"text": "老", "start_s": 0.6, "end_s": 0.8},
        {"text": "地", "start_s": 0.8, "end_s": 1.0},
        {"text": "方", "start_s": 1.0, "end_s": 1.2},
    ]
    cues = cues_from_alignment(words)
    assert "".join(cue.text for cue in cues) == "账册在老地方"
    assert cues[0].start_s == 0.0
    assert cues[-1].end_s == 1.2


def test_a_subtitle_outside_the_media_is_refused():
    cue = SubtitleCue(index=1, start_s=0.0, end_s=9.0, text="台词")
    with pytest.raises(SubtitleError, match="SUBTITLE_TIME_OUTSIDE_MEDIA"):
        cue.validate(duration_s=7.0)


def test_an_empty_subtitle_line_is_refused():
    cue = SubtitleCue(index=1, start_s=0.0, end_s=1.0, text="   ")
    with pytest.raises(SubtitleError, match="SUBTITLE_TEXT_EMPTY"):
        cue.validate()
