"""Script display text is restored without fabricating acoustic observations."""
import pytest

from shine.voiceops.local_sdk import restore_unspoken_text
from shine.voiceops.providers import verify_dialogue

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("text,tokens,expected", [
    ("你好。", ["你", "好"], ["你", "好。"]),
    ("“你好”，再见！", ["你", "好", "再", "见"], ["“你", "好”，", "再", "见！"]),
    ("hello, world!", ["hello", "world"], ["hello, ", "world!"]),
    ("can't go", ["cant", "go"], ["can't ", "go"]),
    ("你好。", ["你", "好", "。"], ["你", "好", "。"]),
])
def test_only_unspoken_script_characters_are_restored(text, tokens, expected):
    raw = [{"text": t, "start_s": i, "end_s": i+1} for i, t in enumerate(tokens)]
    result = restore_unspoken_text(raw, text)
    assert [w["text"] for w in result] == expected
    assert "".join(w["text"] for w in result) == text
    assert [(w["start_s"], w["end_s"]) for w in result] == [(w["start_s"], w["end_s"]) for w in raw]
    assert [w["text"] for w in raw] == tokens


@pytest.mark.parametrize("text", ["你来了", "你来🙂", "你来\u200b"])
def test_missing_spoken_characters_or_symbols_are_not_inserted(text):
    raw = [{"text": "你来", "start_s": 0, "end_s": 1}]
    assert restore_unspoken_text(raw, text) == raw


def test_punctuation_restore_never_passes_a_zero_length_acoustic_span():
    text = "的灯。"
    raw = [{"text": "的", "start_s": 1.12, "end_s": 1.12},
           {"text": "灯", "start_s": 1.12, "end_s": 1.28}]
    words = restore_unspoken_text(raw, text)
    errors = verify_dialogue(
        {"speaker": "QA", "duration_s": 2},
        {"text": text, "speaker": "QA", "language": "zh-CN", "confidence": 1}, words, text, "QA",
    )
    assert errors == ["ALIGNMENT_TIME_INVALID"]
    assert raw[0]["start_s"] == raw[0]["end_s"] == words[0]["start_s"] == words[0]["end_s"]
