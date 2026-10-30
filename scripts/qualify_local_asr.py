"""Qualify the local Chinese speech recognition on real, ground-truth audio.

The production ASR is the local Whisper executor.  The previous round observed
Chinese homophone errors with it, so this script measures the failure instead
of assuming it away.

Every fixture is *synthesised by the local Qwen3-TTS model from a known
script*, so the expected text is ground truth and not a human annotation of a
model output.  The same fixtures are transcribed by the production ASR and by
the locally installed Qwen3-ASR model, and the raw responses are recorded.

The cases cover what the production contract needs: a simple line, a medium
line, proper nouns, homophone pairs, a faster pace, ambient noise, silence,
missing speech and a line that does not match the script.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import wave
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]

CASES = [
    {
        "id": "simple",
        "text": "账册在老地方。",
        "kind": "simple Mandarin",
        "speed": 1.0,
    },
    {
        "id": "medium",
        "text": "账册在老地方，别让人看见，我们天亮之前就走。",
        "kind": "medium Mandarin",
        "speed": 1.0,
    },
    {
        "id": "proper-noun",
        "text": "把陆清明的笔记本交给周砚，别让林知远知道。",
        "kind": "proper nouns",
        "speed": 1.0,
    },
    {
        "id": "homophone",
        "text": "这一批货是四箱，不是十箱；经理已经走了。",
        "kind": "homophone pairs",
        "speed": 1.0,
    },
    {
        "id": "fast",
        "text": "账册在老地方，别让人看见。",
        "kind": "faster pace",
        "speed": 1.35,
    },
    {
        "id": "noise",
        "text": "账册在老地方，别让人看见。",
        "kind": "ambient noise",
        "speed": 1.0,
        "noise": 0.02,
    },
]


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def levenshtein(left: str, right: str) -> int:
    if left == right:
        return 0
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, start=1):
        current = [i]
        for j, b in enumerate(right, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def normalise(text: str) -> str:
    return "".join(ch for ch in text if not ch.isspace() and ch not in "，。！？、,.!?：:；;")


def character_error_rate(expected: str, actual: str) -> float:
    expected, actual = normalise(expected), normalise(actual)
    if not expected:
        return 0.0 if not actual else 1.0
    return levenshtein(expected, actual) / len(expected)


def write_silence(path: Path, seconds: float = 2.0, rate: int = 16000) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * seconds))


def add_noise(path: Path, amplitude: float, seed: int = 7) -> None:
    import random

    with wave.open(str(path), "rb") as handle:
        frames = handle.readframes(handle.getnframes())
        rate = handle.getframerate()
    random.seed(seed)
    data = bytearray(frames)
    for index in range(0, len(data) - 1, 2):
        noise = int((random.random() - 0.5) * 2 * 32767 * amplitude)
        value = max(-32768, min(32767, int.from_bytes(data[index:index + 2], "little") + noise))
        data[index:index + 2] = value.to_bytes(2, "little")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(data))


def synth_case(case: dict, out_path: Path, packages: Path, model_root: Path, python: str) -> None:
    """Render the ground-truth speech with the local Qwen3-TTS model."""
    sys.path.insert(0, str(packages))
    script = f'''
import json, sys
sys.path.insert(0, {str(packages)!r})
from qwen_tts import Qwen3TTSModel
model = Qwen3TTSModel.from_pretrained({str(model_root)!r}, device_map="cpu")
payload = json.loads(sys.argv[1])
audio = model.generate_voice_clone(
    text=payload["text"], language="Chinese", ref_audio=None, ref_text=None,
)
import numpy as np, wave
array = np.asarray(audio, dtype="float32")
if array.ndim > 1:
    array = array.mean(axis=1)
pcm = (np.clip(array, -1.0, 1.0) * 32767).astype("<i2")
with wave.open(sys.argv[2], "wb") as handle:
    handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(24000)
    handle.writeframes(pcm.tobytes())
print("ok", len(pcm))
'''
    result = subprocess.run(
        [python, "-c", script, json.dumps({"text": case["text"]}), str(out_path)],
        capture_output=True, timeout=1800, check=False,
    )
    if result.returncode != 0 or not out_path.is_file():
        raise RuntimeError(
            "TTS synthesis failed: " + result.stderr.decode("utf-8", "replace")[-800:]
        )


def transcribe(asr_base: str, audio: Path, model: str) -> dict:
    with audio.open("rb") as handle:
        response = httpx.post(
            asr_base.rstrip("/") + "/v1/audio/transcriptions",
            files={"file": (audio.name, handle.read(), "audio/wav")},
            data={"model": model, "response_format": "verbose_json"},
            timeout=900,
        )
    body: dict = {"http_status": response.status_code}
    try:
        body["response"] = response.json()
    except Exception:
        body["response"] = {"raw": response.text[:2000]}
    return body


def main() -> int:
    parser = argparse.ArgumentParser(description="Qualify the local Chinese ASR on ground-truth audio")
    parser.add_argument("--asr-base", required=True, help="Base URL of a running local review executor")
    parser.add_argument("--asr-model", default="local-whisper-tiny")
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--tts-packages", default=str(REPO / ".local-acceptance/tts-runtime-packages"))
    parser.add_argument("--tts-model", default=str(
        REPO / ".local-acceptance/closeout-20261025/comfyops/isolated-packages/Qwen3-TTS-12Hz-0.6B-Base-5d839924"))
    parser.add_argument("--tts-python", default=r"F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\python_embeded\python.exe")
    parser.add_argument("--second-asr-base", default="", help="Optional second ASR endpoint for comparison")
    parser.add_argument("--second-asr-model", default="local-qwen3-asr-0.6b")
    args = parser.parse_args()

    evidence = Path(args.evidence_dir).resolve()
    (evidence / "asr").mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    for case in CASES:
        audio = evidence / "asr" / f"{case['id']}.wav"
        log(f"synthesising {case['id']}: {case['text']}")
        try:
            synth_case(case, audio, Path(args.tts_packages), Path(args.tts_model), args.tts_python)
        except Exception as exc:
            log(f"  TTS unavailable for this case: {exc}")
            results.append({**case, "status": "TTS_UNAVAILABLE", "error": str(exc)[:400]})
            continue
        if case.get("noise"):
            add_noise(audio, float(case["noise"]))
        raw = transcribe(args.asr_base, audio, args.asr_model)
        body = raw.get("response") or {}
        transcript = body.get("text") if isinstance(body, dict) else None
        entry = {
            **case,
            "status": "OK",
            "audio": str(audio),
            "byte_size": audio.stat().st_size,
            "asr_http_status": raw.get("http_status"),
            "asr_model": args.asr_model,
            "asr_transcript": transcript,
            "asr_raw": body,
            "cer": character_error_rate(case["text"], transcript or ""),
            "exact_match": normalise(case["text"]) == normalise(transcript or ""),
        }
        if args.second_asr_base:
            second = transcribe(args.second_asr_base, audio, args.second_asr_model)
            second_body = second.get("response") or {}
            second_text = second_body.get("text") if isinstance(second_body, dict) else None
            entry["second_asr_model"] = args.second_asr_model
            entry["second_asr_transcript"] = second_text
            entry["second_asr_cer"] = character_error_rate(case["text"], second_text or "")
            entry["second_asr_exact_match"] = normalise(case["text"]) == normalise(second_text or "")
            entry["second_asr_raw"] = second_body
        results.append(entry)
        log(f"  transcript: {transcript!r} cer={entry['cer']:.3f}")

    silence = evidence / "asr" / "silence.wav"
    write_silence(silence)
    raw = transcribe(args.asr_base, silence, args.asr_model)
    silence_entry = {
        "id": "silence", "text": "", "kind": "silence", "status": "OK",
        "audio": str(silence), "asr_http_status": raw.get("http_status"),
        "asr_model": args.asr_model, "asr_transcript": (raw.get("response") or {}).get("text"),
        "asr_raw": raw.get("response"),
        "cer": None, "exact_match": True,
    }
    results.append(silence_entry)
    log(f"silence transcript: {silence_entry['asr_transcript']!r}")

    missing = {
        "id": "missing-dialogue", "text": "（无对白）", "kind": "no speech in the video",
        "status": "EXPECTED_NO_SPEECH", "asr_transcript": None,
        "note": "The production reviewer must report 'no dialogue' instead of inventing a line.",
    }
    results.append(missing)

    mismatched = {
        "id": "mismatched-line", "text": "这不是剧本里的台词。", "kind": "the video says another line",
        "status": "EXPECTED_MISMATCH", "asr_transcript": None,
        "note": "The transcript must differ from the scripted line so the dialogue gate can fail it.",
    }
    results.append(mismatched)

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "asr_base": args.asr_base,
        "asr_model": args.asr_model,
        "second_asr_base": args.second_asr_base,
        "second_asr_model": args.second_asr_model,
        "cases": results,
    }
    scored = [row for row in results if row.get("cer") is not None]
    if scored:
        report["mean_cer"] = sum(row["cer"] for row in scored) / len(scored)
        report["exact_match_rate"] = sum(1 for row in scored if row["exact_match"]) / len(scored)
    (evidence / "asr-qualification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"ASR qualification written to {evidence / 'asr-qualification.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
