"""Speech gate: decide whether a video has recognizable speech worth subtitling.

Pure decision logic. The heavy ASR step is injected (or lazily imported in
transcribe()), so importing this module needs no model and no extra packages.
Thresholds are only a starting point; tune them on real Mob X samples.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Optional

# Same VAD sensitivity as scripts/subtitle_pipeline.py so the gate and the subtitle step agree.
ASR_VAD_PARAMETERS = {"threshold": 0.35, "min_silence_duration_ms": 400, "speech_pad_ms": 300}


@dataclass(frozen=True)
class GateConfig:
    min_media_s: float = 3.0
    min_speech_s: float = 3.0
    min_speech_ratio: float = 0.08
    min_language_prob: float = 0.6
    max_no_speech_prob: float = 0.6
    allowed_languages: Optional[tuple] = None  # None = any language


@dataclass(frozen=True)
class GateResult:
    decision: str  # "subtitle" or "skip"
    reason: str
    speech_seconds: float = 0.0
    speech_ratio: float = 0.0
    language: Optional[str] = None
    language_prob: float = 0.0


def probe_args(path: str) -> list:
    return ["ffprobe", "-v", "error", "-print_format", "json",
            "-show_streams", "-show_format", path]


def has_audio_stream(probe: dict) -> bool:
    return any(s.get("codec_type") == "audio" for s in probe.get("streams", []))


def media_duration(probe: dict) -> float:
    for raw in (probe.get("format", {}).get("duration"),
                *[s.get("duration") for s in probe.get("streams", [])
                  if s.get("codec_type") == "audio"]):
        try:
            value = float(raw)
            if value > 0:
                return value
        except (TypeError, ValueError):
            continue
    return 0.0


def _merged_seconds(spans: list) -> float:
    total, cur_s, cur_e = 0.0, None, None
    for s, e in sorted(spans):
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        total += cur_e - cur_s
    return total


def evaluate(probe: dict, segments: list, language: Optional[str],
             language_prob: float, cfg: Optional[GateConfig] = None) -> GateResult:
    """Return the gate decision from ffprobe JSON + ASR segments.

    segments: dicts with start, end (seconds) and optional no_speech_prob.
    """
    cfg = cfg or GateConfig()
    if not has_audio_stream(probe):
        return GateResult("skip", "no_audio_track")
    duration = media_duration(probe)
    if duration < cfg.min_media_s:
        return GateResult("skip", "media_too_short")
    spans = []
    for seg in segments:
        start, end = float(seg["start"]), float(seg["end"])
        if end > start and float(seg.get("no_speech_prob", 0.0)) <= cfg.max_no_speech_prob:
            spans.append((max(0.0, start), min(end, duration)))
    speech = _merged_seconds([sp for sp in spans if sp[1] > sp[0]])
    ratio = min(1.0, speech / duration)
    base = dict(speech_seconds=round(speech, 2), speech_ratio=round(ratio, 3),
                language=language, language_prob=float(language_prob or 0.0))
    if speech <= 0:
        return GateResult("skip", "no_speech", **base)
    if speech < cfg.min_speech_s:
        return GateResult("skip", "speech_too_short", **base)
    if ratio < cfg.min_speech_ratio:
        return GateResult("skip", "low_speech_ratio", **base)
    if base["language_prob"] < cfg.min_language_prob:
        return GateResult("skip", "low_language_confidence", **base)
    if cfg.allowed_languages and language not in cfg.allowed_languages:
        return GateResult("skip", "language_not_allowed", **base)
    return GateResult("subtitle", "ok", **base)


def audio_cmd(path: str) -> list:
    """Decode normalized speech to 16 kHz mono float32 PCM."""
    return ["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-af", "highpass=f=80,loudnorm",
            "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"]


def decode_audio_16k(path: str, run=subprocess.run):
    """Decode with ffmpeg to a float32 numpy array. Bypasses PyAV (av.open metadata_errors bug)."""
    r = run(audio_cmd(path), capture_output=True, timeout=600)
    if r.returncode != 0:
        tail = r.stderr or b""
        tail = tail.decode("utf-8", "replace") if isinstance(tail, bytes) else str(tail)
        raise RuntimeError("audio_decode_failed: " + tail[-200:])
    import numpy as np  # noqa: PLC0415  (installed together with faster-whisper)

    raw = r.stdout or b""
    return np.frombuffer(raw[: len(raw) // 4 * 4], dtype=np.float32)


def transcribe(path: str, model_size: str = "small"):
    """Run faster-whisper (CPU, int8, VAD). Imported lazily: only needed on the runner."""
    from faster_whisper import WhisperModel  # noqa: PLC0415

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segs, info = model.transcribe(
        decode_audio_16k(path), vad_filter=True, vad_parameters=ASR_VAD_PARAMETERS,
        beam_size=1, condition_on_previous_text=False,
    )
    segments = [{"start": s.start, "end": s.end, "text": s.text,
                 "no_speech_prob": s.no_speech_prob} for s in segs]
    return segments, info.language, info.language_probability
