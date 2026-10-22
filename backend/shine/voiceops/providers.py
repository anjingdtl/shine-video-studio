from __future__ import annotations

import base64
import io
import math
import struct
import wave
from typing import Protocol

import httpx

from shine.security import require_loopback_url


class VoiceDesignProvider(Protocol):
    def design(self, description: str, text: str, character: str) -> dict: ...


class VoiceCloneProvider(Protocol):
    def clone(self, reference: bytes, reference_text: str, character: str) -> dict: ...


class TTSProvider(Protocol):
    def synthesize(
        self, text: str, character: str, conditioning: str, emotion: str, speed: float, pause: float
    ) -> dict: ...


class ASRProvider(Protocol):
    def transcribe(self, audio: bytes) -> dict: ...


class ForcedAlignerProvider(Protocol):
    def align(self, audio: bytes, text: str) -> list[dict]: ...


def fake_audio(text, character, speed=1.0, pause=0.0):
    """A test tone, never speech or a real person's voice."""
    duration = min(15, max(1, len(text) * 0.16 / speed + pause))
    buffer = io.BytesIO()
    rate = 16000
    frequency = 300 + sum(character.encode()) % 300
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(
            b"".join(
                struct.pack("<h", int(2000 * math.sin(2 * math.pi * frequency * i / rate)))
                for i in range(int(rate * duration))
            )
        )
    return buffer.getvalue(), duration


class FakeVoiceProvider:
    is_mock = True

    def __init__(self):
        self.transcripts = {}

    def synthesize(self, text, character, conditioning="", emotion="neutral", speed=1.0, pause=0.0):
        import hashlib

        audio, duration = fake_audio(text, character, speed, pause)
        self.transcripts[hashlib.sha256(audio).hexdigest()] = (text, character)
        return {
            "audio": audio,
            "duration_s": duration,
            "speaker": character,
            "language": "zh-CN",
            "conditioning": conditioning,
            "is_mock": True,
            "model": "fake-voice-v1",
        }

    def design(self, description, text, character):
        return self.synthesize(text, character, description)

    def clone(self, reference, reference_text, character):
        return self.synthesize(reference_text, character, "fake-clone")

    def transcribe(self, audio):
        import hashlib

        text, character = self.transcripts.get(hashlib.sha256(audio).hexdigest(), ("", "UNKNOWN"))
        return {
            "text": text,
            "speaker": character,
            "language": "zh-CN",
            "confidence": 0.99,
            "is_mock": True,
            "model": "fake-asr-v1",
        }

    def align(self, audio, text):
        with wave.open(io.BytesIO(audio)) as wav:
            duration = wav.getnframes() / wav.getframerate()
        return [
            {"text": char, "start_s": i * duration / len(text), "end_s": (i + 1) * duration / len(text)}
            for i, char in enumerate(text)
        ]


class LocalVoiceHTTPProvider:
    """Shine voice-service protocol (not an invented upstream Qwen HTTP API)."""

    is_mock = False

    def release_models(self):
        response = self.client.post(self.base_url + "/free", json={"unload_models": True})
        response.raise_for_status()
        if response.json().get("released") is not True:
            raise ValueError("MODEL_RELEASE_UNCONFIRMED")

    def __init__(self, base_url, client=None):
        self.base_url = require_loopback_url(base_url).rstrip("/")
        self.client = client or httpx.Client(timeout=120, follow_redirects=False)

    def prepare_conditioning(self, audio, digest, reference_text):
        response = self.client.post(
            self.base_url + "/conditioning",
            json={"audio_base64": base64.b64encode(audio).decode(), "sha256": digest, "reference_text": reference_text},
        )
        response.raise_for_status()
        if response.json().get("sha256") != digest:
            raise ValueError("VOICE_CONDITIONING_HASH_MISMATCH")

    def design(self, description, text, character):
        response = self.client.post(
            self.base_url + "/design", json={"description": description, "text": text, "character": character}
        )
        response.raise_for_status()
        result = response.json()
        result["audio"] = base64.b64decode(result.pop("audio_base64"), validate=True)
        return result

    def clone(self, reference, reference_text, character):
        response = self.client.post(
            self.base_url + "/clone",
            json={
                "reference_base64": base64.b64encode(reference).decode(),
                "reference_text": reference_text,
                "character": character,
            },
        )
        response.raise_for_status()
        result = response.json()
        result["audio"] = base64.b64decode(result.pop("audio_base64"), validate=True)
        return result

    def synthesize(self, text, character, conditioning, emotion, speed, pause):
        response = self.client.post(
            self.base_url + "/tts",
            json={
                "text": text,
                "character": character,
                "conditioning": conditioning,
                "emotion": emotion,
                "speed": speed,
                "pause": pause,
            },
        )
        response.raise_for_status()
        result = response.json()
        result["audio"] = base64.b64decode(result.pop("audio_base64"), validate=True)
        return result

    def transcribe(self, audio):
        response = self.client.post(self.base_url + "/asr", json={"audio_base64": base64.b64encode(audio).decode()})
        response.raise_for_status()
        return response.json()

    def align(self, audio, text):
        response = self.client.post(
            self.base_url + "/align", json={"audio_base64": base64.b64encode(audio).decode(), "text": text}
        )
        response.raise_for_status()
        return response.json()["words"]


def verify_dialogue(result, transcript, words, text, character):
    errors = []
    if transcript.get("text") != text:
        errors.append("DIALOGUE_TEXT_MISMATCH")
    if transcript.get("speaker") != character or result.get("speaker") != character:
        errors.append("VOICE_CHARACTER_MISMATCH")
    if transcript.get("language") != "zh-CN" or transcript.get("confidence", 0) < 0.9:
        errors.append("ASR_REVIEW_REQUIRED")
    if "".join(w["text"] for w in words) != text:
        errors.append("ALIGNMENT_TEXT_MISMATCH")
    last = 0
    for word in words:
        if not last <= word["start_s"] < word["end_s"] <= result["duration_s"] + 0.001:
            errors.append("ALIGNMENT_TIME_INVALID")
        last = word["end_s"]
    return sorted(set(errors))


def subtitles(words):
    def stamp(seconds, ass=False):
        units = round(seconds * (100 if ass else 1000))
        hours, units = divmod(units, 360000 if ass else 3600000)
        minutes, units = divmod(units, 6000 if ass else 60000)
        secs, fraction = divmod(units, 100 if ass else 1000)
        return (
            f"{hours}:{minutes:02}:{secs:02}.{fraction:02}"
            if ass
            else f"{hours:02}:{minutes:02}:{secs:02},{fraction:03}"
        )

    srt, ass = (
        [],
        [
            "[Script Info]",
            "ScriptType: v4.00+",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, Alignment",
            "Style: Default,Arial,36,&H00FFFFFF,2",
            "[Events]",
            "Format: Layer, Start, End, Style, Text",
        ],
    )
    for i, word in enumerate(words, 1):
        text = word["text"].replace("\n", " ").replace("\r", " ")
        srt.append(f"{i}\n{stamp(word['start_s'])} --> {stamp(word['end_s'])}\n{text}\n")
        escaped = text.replace("\\", "＼").replace("{", "｛").replace("}", "｝")
        ass.append(f"Dialogue: 0,{stamp(word['start_s'], True)},{stamp(word['end_s'], True)},Default,{escaped}")
    return {"srt": "\n".join(srt), "ass": "\n".join(ass)}
