"""The off-screen narrator is a speaker with no picture reference.

A narrator talks over the picture and is never visible in it.  H3 nonetheless
requires every speaker to own a subject reference, because a bound face is what
keeps an on-screen performer from drifting between takes.  A narrator has no
face to keep, so the compiler treats it as an explicit exception: the voice is
scripted like any other dialogue line, and the prompt states that this speaker
must never appear in frame.

The vocabulary itself lives in ``shine.script_labels`` so the script parsers
and this compiler cannot drift apart.
"""

from __future__ import annotations

from shine.script_labels import (
    NARRATOR_SPEAKER_NAMES,
    is_narrator_speaker,
    normalize_label,
)

_NARRATOR_SENTENCE = (
    "The narration lines are spoken by an off-screen narrator who never appears in the picture: "
    "no performer, face, mouth or body on screen may be used to deliver them, and the narration "
    "must not be lip-synced to any visible character."
)


def narrator_sentence() -> str:
    """The single English sentence every narrated shot carries."""
    return _NARRATOR_SENTENCE


__all__ = [
    "NARRATOR_SPEAKER_NAMES",
    "is_narrator_speaker",
    "narrator_sentence",
    "normalize_label",
]
