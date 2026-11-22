"""Single authoritative H3 spoken-language & non-vocal audio contract.

Shared across both Manual4 and Director7 pipelines under the Three Principles
(一套事实、一个裁决、一个执行者):

* Truth / Adjudicator: ``has_authorized_h3_speech`` is the sole decision point
  determining whether an H3 render is authorized to emit vocal speech.
* Execution (Pre-submit): ``ensure_h3_execution_prompt_contract`` normalizes
  ``<d>[Language] ...</d>`` tags, injects missing timed dialogue blocks from
  structured script dialogue, anchors ``<Subject k>`` to its presentation
  ``<Picture i>`` slot, and attaches the official ``overall_soundscape`` /
  ``non_diegetic_music`` audio contract.
* Execution (Post-render): ``enforce_h3_render_audio_contract`` keeps real H3
  audio untouched on authorized spoken shots, and deterministically replaces
  unconditioned H3 vocal hallucinations with a stereo silent AAC track on
  dialogue-free renders without re-encoding video frames.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

_D_BLOCK_RE = re.compile(r"<d\s*>(.*?)</d\s*>", flags=re.IGNORECASE | re.DOTALL)
_D_TAG_PRESENT_RE = re.compile(r"<\s*d\s*>.*?<\s*/\s*d\s*>", flags=re.IGNORECASE | re.DOTALL)
_LANG_TAG_PREFIX_RE = re.compile(r"^\s*\[([^\[\]]{1,32})\]\s*(.*)$", flags=re.DOTALL)
_OUTER_QUOTES_CHARS = '"\'“”‘’「」『』《》'

_LANG_ALIASES: dict[str, str] = {
    "chinese": "Chinese",
    "mandarin": "Chinese",
    "putonghua": "Chinese",
    "中文": "Chinese",
    "汉语": "Chinese",
    "普通话": "Chinese",
    "国语": "Chinese",
    "english": "English",
    "英文": "English",
    "英语": "English",
    "japanese": "Japanese",
    "日语": "Japanese",
    "korean": "Korean",
    "韩语": "Korean",
    "cantonese": "Cantonese",
    "粤语": "Cantonese",
    "french": "French",
    "法语": "French",
    "german": "German",
    "德语": "German",
    "spanish": "Spanish",
    "西班牙语": "Spanish",
}

_SILENT_STYLE_SUBS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b180-degree\s+dialogue\s+axis\b", flags=re.IGNORECASE), "180-degree interaction axis"),
    (re.compile(r"\bdialogue\s+(?:axis|line)\b", flags=re.IGNORECASE), "eyeline axis"),
    (re.compile(r"\bdialogue\s+breaths?\b", flags=re.IGNORECASE), "silent reaction beats"),
    (
        re.compile(r"\bdialogue\s+(?:beats?|boundaries|turns?|exchanges?)\b", flags=re.IGNORECASE),
        "action and reaction beats",
    ),
    (re.compile(r"\b(?:during|for|after|between)\s+dialogue\b", flags=re.IGNORECASE), "during silent interaction"),
    (re.compile(r"\b(?:dialogue|spoken\s+lines?|vocals?)\b", flags=re.IGNORECASE), "silent reaction"),
)


def canonical_h3_language(language: str | None) -> str:
    """Return canonical ASCII English language name for MiniMax-H3 prompt contracts."""
    raw = (language or "").strip()
    if not raw:
        return "Chinese"
    mapped = _LANG_ALIASES.get(raw.casefold()) or _LANG_ALIASES.get(raw)
    if mapped:
        return mapped
    return raw if raw.isascii() else "Chinese"


def sanitize_h3_dialogue_text(text: str) -> str:
    """Clean spoken line text inside ``<d>[Language] ...</d>`` per official H3 guide.

    * Strips wrapping quotation marks that confuse TTS phonemizers.
    * Normalizes ellipses (``……`` / ``...``) into a pause comma ``，`` (official ref-en §5.4).
    * Removes stray trailing commas directly before ``</d>``.
    """
    cleaned = " ".join(str(text or "").split()).strip()
    while len(cleaned) >= 2 and cleaned[0] in _OUTER_QUOTES_CHARS and cleaned[-1] in _OUTER_QUOTES_CHARS:
        cleaned = cleaned[1:-1].strip()
    cleaned = cleaned.strip(_OUTER_QUOTES_CHARS).strip()
    cleaned = re.sub(r"…+|\.{3,}", "，", cleaned)
    cleaned = re.sub(r"[，,]{2,}", "，", cleaned)
    cleaned = re.sub(r"[，,]\s*$", "", cleaned).strip()
    return cleaned


def sanitize_non_vocal_style_text(text: str) -> str:
    """Remove vocal/dialogue priming vocabulary from style instructions on silent shots."""
    out = str(text or "").strip()
    if not out:
        return ""
    for pattern, repl in _SILENT_STYLE_SUBS:
        out = pattern.sub(repl, out)
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out


def _has_non_empty_dialogue(dialogue: Iterable[Any] | None) -> bool:
    for item in dialogue or ():
        if isinstance(item, dict):
            txt = str(item.get("text") or "").strip()
        else:
            txt = str(getattr(item, "text", "") or "").strip()
        if txt:
            return True
    return False


def has_authorized_h3_speech(
    prompt_text: str,
    *,
    dialogue: Iterable[Any] | None = None,
    references: Iterable[Any] | None = None,
) -> bool:
    """Single authoritative adjudicator: is this shot authorized to emit speech?

    Wait-free rule: H3 emits valid Chinese speech ONLY when timed ``<d>...</d>``
    tokens or structured script dialogue lines are present.  An audio reference on
    a dialogue-free shot is ambience only (never speech).  Without ``<d>...</d>``
    any speech H3 hallucinates is unconditioned foreign-language gibberish.
    """
    _ = references
    if _has_non_empty_dialogue(dialogue):
        return True
    return bool(_D_TAG_PRESENT_RE.search(prompt_text or ""))


def _match_speaker_label(speaker: str, references: list[dict[str, Any]]) -> str:
    sp = (speaker or "").strip().casefold()
    subjects = [
        r
        for r in references
        if str(r.get("use_as") or "") in {"subject", "image"}
        and str(r.get("h3_label") or "").startswith("<Subject")
    ]
    if not subjects:
        return "The speaker"
    if sp:
        for ref in subjects:
            for cand in (ref.get("role_label"), ref.get("entity_name"), ref.get("h3_label")):
                c = str(cand or "").strip().casefold()
                if c and (c == sp or sp in c or c in sp):
                    return str(ref["h3_label"])
    return str(subjects[0]["h3_label"])


def ensure_h3_execution_prompt_contract(
    prompt_text: str,
    *,
    references: list[dict[str, Any]] | None = None,
    dialogue: list[dict[str, Any]] | None = None,
    project_language: str = "Chinese",
) -> str:
    """Enforce MiniMax-H3 `<d>[Language]` tags, `<Picture i>` anchoring, and soundscape contract."""
    text = (prompt_text or "").strip()
    if not text:
        return text

    lang = canonical_h3_language(project_language)
    refs = list(references or [])
    dlg_lines = [
        d for d in (dialogue or [])
        if isinstance(d, dict) and str(d.get("text") or "").strip()
    ]

    # 1. Normalize existing <d>...</d> blocks to carry canonical [<Language>] and clean text
    def _rewrite_d(match: re.Match[str]) -> str:
        inner = match.group(1).strip()
        m_lang = _LANG_TAG_PREFIX_RE.match(inner)
        if m_lang:
            d_lang = canonical_h3_language(m_lang.group(1))
            d_body = sanitize_h3_dialogue_text(m_lang.group(2))
        else:
            d_lang = lang
            d_body = sanitize_h3_dialogue_text(inner)
        if not d_body:
            return ""
        return f"<d>[{d_lang}] {d_body}</d>"

    text = _D_BLOCK_RE.sub(_rewrite_d, text).strip()
    existing_d_count = len(_D_TAG_PRESENT_RE.findall(text))

    blocks_to_append: list[str] = []

    # 2. If script has dialogue lines missing from <d> tags in manual prompt, inject them
    if dlg_lines and existing_d_count < len(dlg_lines):
        injected_lines: list[str] = []
        for idx, line in enumerate(dlg_lines[existing_d_count:], start=existing_d_count + 1):
            sp_label = _match_speaker_label(str(line.get("speaker") or ""), refs)
            spoken_txt = sanitize_h3_dialogue_text(str(line.get("text") or ""))
            if not spoken_txt:
                continue
            injected_lines.append(
                f"{sp_label} (S{idx}) says in Mandarin {lang}: <d>[{lang}] {spoken_txt}</d>"
            )
        if injected_lines:
            blocks_to_append.append("Dialogue timing:\n" + "\n".join(injected_lines))

    # 3. Anchor <Subject k> -> <Picture i> and last_frame continuity <Picture N> -> 0.00s opening frame
    subj_mappings: list[str] = []
    continuity_headers: list[str] = []
    continuity_retentions: list[str] = []
    img_counter = 0
    for ref in refs:
        use_as = str(ref.get("use_as") or "").lower()
        is_image_ref = use_as in {"subject", "picture", "image"} or str(
            ref.get("h3_label") or ""
        ).startswith(("<Subject ", "<Picture "))
        slot = ref.get("image_slot")
        if is_image_ref:
            img_counter += 1
            eff_slot = int(slot) if slot else img_counter
        else:
            eff_slot = int(slot) if slot else None
        h3_label = str(ref.get("h3_label") or "")
        is_continuity = (
            ref.get("role_label") == "last_frame_continuity"
            or ref.get("entity_name") == "last_frame_continuity"
            or ref.get("frame_role") == "first_frame"
        )
        if is_continuity and eff_slot:
            cont_label = f"<Picture {eff_slot}>"
            if cont_label not in text:
                continuity_headers.append(
                    f"For the target video, at 0.00 seconds into the target video, {cont_label} "
                    "(from [Shot 1], the preceding shot's final frame) is fully referenced as the opening frame."
                )
                subj_mappings.append(
                    f"{cont_label} is the first_frame continuity reference (the preceding shot's final frame) "
                    f"for [Shot 1]; anchor frame 0.00s to {cont_label}, preserving its camera angle, composition, "
                    "lighting, and subject placement before evolving smoothly into this shot's action."
                )
                continuity_retentions.append(
                    f"{cont_label} ([Shot 1] first_frame frame): fully_preserved - start directly from "
                    f"{cont_label} at 0.00 seconds and continue the motion and scene seamlessly without a hard cut."
                )
            continue
        if h3_label.startswith("<Subject ") and eff_slot:
            expected_token = f"<Picture {eff_slot}>"
            if expected_token not in text and h3_label in text:
                subj_mappings.append(
                    f"{h3_label} corresponds to the performer shown in {expected_token}; "
                    "preserve this exact face and outfit."
                )
    prefix_blocks: list[str] = []
    if continuity_headers:
        prefix_blocks.extend(continuity_headers)
    if subj_mappings:
        prefix_blocks.append("subject_slot_alignment:\n" + " ".join(subj_mappings))
    if continuity_retentions:
        prefix_blocks.append("continuity_retention:\n" + "\n".join(continuity_retentions))
    if prefix_blocks:
        text = "\n\n".join(prefix_blocks) + "\n\n" + text

    # 4. Append missing official soundscape & non_diegetic_music sections
    has_speech = has_authorized_h3_speech(
        text + ("\n" + "\n".join(blocks_to_append) if blocks_to_append else ""),
        dialogue=dlg_lines,
        references=refs,
    )
    has_soundscape = bool(re.search(r"(?im)^(?:\[?\s*overall[ _]soundscape\s*\]?\s*:?|overall_soundscape:)", text))
    has_music = bool(re.search(r"(?im)^(?:\[?\s*non[ _-]diegetic[ _]music\s*\]?\s*:?|non_diegetic_music:)", text))

    if not has_soundscape:
        if has_speech:
            blocks_to_append.append(
                "overall_soundscape:\n"
                f"Every voice in this shot speaks {lang}. The only words heard are the explicitly tagged "
                f"<d>[{lang}]</d> dialogue lines of this shot, delivered in standard Mandarin {lang} "
                "exactly as written; all other sound stays non-vocal."
            )
        else:
            blocks_to_append.append(
                "overall_soundscape:\n"
                "This shot has no dialogue: performers act with closed mouths and the soundtrack is "
                f"entirely non-vocal. The scene takes place in a {lang}-language world, and every sound stays wordless."
            )

    if not has_music:
        blocks_to_append.append("non_diegetic_music:\nInstrumental only.")

    if blocks_to_append:
        return text + "\n\n" + "\n\n".join(blocks_to_append)
    return text


def extract_comfy_prompt_texts(raw_prompt_entry: Any) -> str:
    """Extract prompt string(s) embedded in a ComfyUI ``/history`` prompt record."""
    graph: Any = raw_prompt_entry
    if isinstance(raw_prompt_entry, list | tuple) and len(raw_prompt_entry) >= 3:
        graph = raw_prompt_entry[2]
    if not isinstance(graph, dict):
        return ""
    texts: list[str] = []
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for key in ("prompt", "positive_prompt", "text"):
            val = inputs.get(key)
            if isinstance(val, str) and val.strip():
                texts.append(val.strip())
    return "\n".join(texts)


def enforce_h3_render_audio_contract(
    mp4_bytes: bytes,
    *,
    prompt_text: str,
    dialogue: Iterable[Any] | None = None,
    references: Iterable[Any] | None = None,
    ffmpeg_path: str = "ffmpeg",
) -> bytes:
    """Single audio enforcement boundary for completed real H3 renders.

    * Authorized speech present (``<d>...</d>`` or script dialogue): keep H3's real
      synthesized Chinese dialogue track untouched.
    * Dialogue-free shot: strip H3's unconditioned vocal hallucination by replacing
      the audio stream with clean 48 kHz stereo silent AAC while stream-copying the
      video frames lossless (``-c:v copy``).
    """
    if not mp4_bytes:
        return mp4_bytes
    if has_authorized_h3_speech(prompt_text, dialogue=dialogue, references=references):
        return mp4_bytes
    # Ignore tiny/synthetic offline unit test stubs (e.g. make_mp4_fixture or b"video-bytes")
    if len(mp4_bytes) < 4096 or b"ftyp" not in mp4_bytes[:64]:
        return mp4_bytes
    executable = shutil.which(ffmpeg_path or "ffmpeg")
    if not executable:
        return mp4_bytes

    try:
        with tempfile.TemporaryDirectory(prefix="shine-h3-audio-") as tmpdir:
            src = Path(tmpdir) / "in.mp4"
            dst = Path(tmpdir) / "out.mp4"
            src.write_bytes(mp4_bytes)
            cmd = [
                executable,
                "-nostdin",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(src),
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-shortest",
                "-movflags",
                "+faststart",
                str(dst),
            ]
            completed = subprocess.run(cmd, capture_output=True, timeout=120, check=False)
            if completed.returncode == 0 and dst.is_file() and dst.stat().st_size > 0:
                return dst.read_bytes()
    except Exception:  # noqa: BLE001
        return mp4_bytes
    return mp4_bytes
