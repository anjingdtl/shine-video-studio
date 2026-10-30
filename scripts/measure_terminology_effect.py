"""Measure what the script's terminology table does to the local recognizer.

The same real audio is transcribed twice by the same local Qwen3-ASR model:
once without any hint and once with the script's own terminology.  The two
transcripts are compared against the exact text that the speech was
synthesised from, so the effect of the hint is measured rather than assumed.

Both runs use the real model; nothing here is a mock and no transcript is
rewritten after the fact.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

CASES = [
    {
        "id": "proper-noun",
        "text": "把陆清明的笔记本交给周砚，别让林知远知道。",
        "terms": ["陆清明", "周砚", "林知远"],
        "aliases": {"周砚": ["周烨"], "林知远": ["林之远"]},
    },
    {
        "id": "homophone",
        "text": "这一批货是四箱，不是十箱；经理已经走了。",
        "terms": [],
        "aliases": {},
    },
    {
        "id": "simple",
        "text": "账册在老地方。",
        "terms": [],
        "aliases": {},
    },
]

TRANSCRIBE = '''
import json, sys
sys.path.insert(0, {packages!r})
from qwen_asr import Qwen3ASRModel
model = Qwen3ASRModel.from_pretrained({model_root!r}, device_map="cpu")
context = sys.argv[2]
if context:
    results = model.transcribe(audio=sys.argv[1], language="Chinese", context=context)
else:
    results = model.transcribe(audio=sys.argv[1], language="Chinese")
text = " ".join(str(getattr(row, "text", row)) for row in results) if isinstance(results, list) \\
    else str(getattr(results, "text", results))
sys.stdout.buffer.write(json.dumps({{"text": text}}, ensure_ascii=False).encode("utf-8"))
'''


def normalise(text: str) -> str:
    return "".join(ch for ch in text if not ch.isspace() and ch not in "，。！？、,.!?：:；;\"'“”‘’")


def character_error_rate(expected: str, actual: str) -> float:
    expected, actual = normalise(expected), normalise(actual)
    if not expected:
        return 0.0 if not actual else 1.0
    previous = list(range(len(actual) + 1))
    for index, left in enumerate(expected, start=1):
        current = [index]
        for position, right in enumerate(actual, start=1):
            current.append(min(previous[position] + 1, current[position - 1] + 1,
                               previous[position - 1] + (left != right)))
        previous = current
    return previous[-1] / len(expected)


def build_hint(case: dict) -> str:
    """The hint that is actually sent to the recognizer.

    Only the canonical spellings are named.  The known mishearings are not:
    the measured behaviour of the local recognizer shows that a mishearing
    named in the context becomes an acceptable answer again.
    """
    if not case["terms"]:
        return ""
    return "专有名词标准写法：" + "、".join(case["terms"]) + "。"


def transcribe(python: str, packages: Path, model_root: Path, audio: Path, context: str) -> str:
    script = TRANSCRIBE.format(packages=str(packages), model_root=str(model_root))
    result = subprocess.run(
        [python, "-c", script, str(audio), context],
        capture_output=True, timeout=3600, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace")[-600:])
    return json.loads(result.stdout.decode("utf-8", "replace"))["text"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure the terminology hint on the local recognizer")
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--audio-dir", required=True, help="Directory with the synthesised ground-truth audio")
    parser.add_argument("--python", default=r"F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\python_embeded\python.exe")
    parser.add_argument("--packages", default=str(REPO / ".local-acceptance/asr-runtime-packages"))
    parser.add_argument("--model-root", default=str(
        REPO / ".local-acceptance/closeout-20261025/comfyops/isolated-packages/Qwen3-ASR-0.6B-5eb14417-cdn1"))
    args = parser.parse_args()

    evidence = Path(args.evidence_dir).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    audio_dir = Path(args.audio_dir).resolve()
    packages = Path(args.packages)
    model_root = Path(args.model_root)

    rows = []
    for case in CASES:
        audio = audio_dir / f"{case['id']}.wav"
        if not audio.is_file():
            rows.append({**case, "status": "AUDIO_MISSING", "audio": str(audio)})
            continue
        hint = build_hint(case)
        without = transcribe(args.python, packages, model_root, audio, "")
        with_hint = transcribe(args.python, packages, model_root, audio, hint)
        rows.append({
            **case,
            "status": "OK",
            "audio": str(audio),
            "hint": hint,
            "transcript_without_hint": without,
            "transcript_with_hint": with_hint,
            "cer_without_hint": round(character_error_rate(case["text"], without), 4),
            "cer_with_hint": round(character_error_rate(case["text"], with_hint), 4),
            "exact_without_hint": normalise(case["text"]) == normalise(without),
            "exact_with_hint": normalise(case["text"]) == normalise(with_hint),
        })
        print(f"[{time.strftime('%H:%M:%S')}] {case['id']}: "
              f"cer without={rows[-1]['cer_without_hint']:.3f} with={rows[-1]['cer_with_hint']:.3f}",
              flush=True)

    scored = [row for row in rows if row.get("status") == "OK"]
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": "local-qwen3-asr-0.6b",
        "model_root": str(model_root),
        "cases": rows,
    }
    if scored:
        report["mean_cer_without_hint"] = sum(row["cer_without_hint"] for row in scored) / len(scored)
        report["mean_cer_with_hint"] = sum(row["cer_with_hint"] for row in scored) / len(scored)
        report["improved_cases"] = sum(
            1 for row in scored if row["cer_with_hint"] < row["cer_without_hint"])
    target = evidence / "terminology-effect.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Terminology effect written to {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
