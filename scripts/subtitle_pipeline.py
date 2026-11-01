#!/usr/bin/env python3
"""Subtitle pipeline for Mob X videos.

ASR (faster-whisper) -> batch translation -> SRT/ASS -> ffmpeg burn-in -> verify.

No network code lives here: translation is an injected callable
``translate_fn(prompt: str, *, max_tokens: int) -> str`` so the caller owns the provider
chain (Groq -> OpenRouter -> Worker AI), the daily-cap ledger and error reporting.
faster-whisper is imported lazily, so tests and CI need nothing extra.

Any failure raises SubtitleError(code). The caller decides what to do (e.g. publish the
video without subtitles, or stop and report to the owner); this module never publishes.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

try:
    import subtitle_tools as st
except ImportError:  # imported as scripts.subtitle_pipeline
    from scripts import subtitle_tools as st

MAX_BATCHES = 2
MAX_BATCH_CHARS = 3000
MAX_BATCH_CUES = 60
MAX_OUT_TOKENS = 1200
MAX_MISSING_RATIO = 0.3
MAX_OUT_BYTES = 49 * 1024 * 1024  # Rubika upload ladder is OK only below ~50 MiB
DURATION_TOLERANCE_MS = 1500
MIN_CUE_MS = 400
MAX_CUE_CHARS = 80  # longer Persian cues are split in two or more timed pieces

# ASR tuning: more sensitive VAD (catches quiet/unclear speech) and no context carry-over
# between segments (prevents hallucination loops on music/noise).
ASR_VAD_PARAMETERS = {"threshold": 0.35, "min_silence_duration_ms": 400, "speech_pad_ms": 300}
ASR_OPTIONS = {"vad_filter": True, "vad_parameters": ASR_VAD_PARAMETERS,
               "condition_on_previous_text": False}


class SubtitleError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


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
        raise SubtitleError("audio_decode_failed", tail[-200:])
    import numpy as np  # lazy: installed together with faster-whisper

    raw = r.stdout or b""
    return np.frombuffer(raw[: len(raw) // 4 * 4], dtype=np.float32)


class TranscriptionResult(list):
    """Cue list carrying safe model/runtime metadata for the pipeline report."""

    def __init__(self, cues: list, report: dict):
        super().__init__(cues)
        self.report = dict(report)


def transcribe(video: str, model_size: str = "medium", language: str = "en",
               model_factory=None, runtime_limit_seconds: float = 900.0) -> list:
    """Transcribe with CPU/int8 faster-whisper, falling back to small after 15 minutes."""
    started = time.monotonic()
    audio = video
    if model_factory is None:
        from faster_whisper import WhisperModel  # lazy: only the Mob X workflow installs it

        def create_model(size: str):
            return WhisperModel(size, device="cpu", compute_type="int8")

        audio = decode_audio_16k(video)
    else:
        # Keep the no-argument factory interface used by the existing unit tests.
        def create_model(size: str):
            return model_factory()

    runtime_limit_seconds = max(0.0, float(runtime_limit_seconds))

    def run_model(size: str, *, watch_runtime: bool) -> tuple[list, float, float, bool]:
        model_started = time.monotonic()
        model = create_model(size)
        segments, _info = model.transcribe(
            audio, language=language, beam_size=5, **ASR_OPTIONS,
        )
        iterator = iter(segments)
        cues = []
        exceeded = False
        while True:
            if watch_runtime and size == "medium" and time.monotonic() - started >= runtime_limit_seconds:
                exceeded = True
                close = getattr(iterator, "close", None)
                if callable(close):
                    close()
                break
            try:
                seg = next(iterator)
            except StopIteration:
                break
            text = " ".join(str(seg.text).split())
            if text:
                cues.append((int(seg.start * 1000), int(seg.end * 1000), text))
        model_elapsed = max(0.0, time.monotonic() - model_started)
        total_elapsed = max(0.0, time.monotonic() - started)
        if watch_runtime and size == "medium" and total_elapsed >= runtime_limit_seconds:
            exceeded = True
        return cues, model_elapsed, total_elapsed, exceeded

    cues, model_elapsed, total_elapsed, exceeded = run_model(model_size, watch_runtime=True)
    if model_size == "medium" and exceeded:
        # Discard partial medium output; never mix cues from two ASR models.
        cues, small_elapsed, total_elapsed, _ = run_model("small", watch_runtime=False)
        report = {
            "asr_initial_model": "medium",
            "asr_model": "small",
            "asr_fallback_reason": "medium_runtime_exceeded_15m",
            "asr_medium_elapsed_seconds": round(model_elapsed, 1),
            "asr_fallback_elapsed_seconds": round(small_elapsed, 1),
            "asr_elapsed_seconds": round(total_elapsed, 1),
        }
    else:
        report = {
            "asr_initial_model": model_size,
            "asr_model": model_size,
            "asr_fallback_reason": None,
            "asr_elapsed_seconds": round(total_elapsed, 1),
        }
    return TranscriptionResult(cues, report)


def clean_cues(cues: list) -> list:
    """Drop empty/consecutive-duplicate cues (ASR loops), fix overlaps and tiny durations."""
    out = []
    for s, e, t in sorted(cues, key=lambda c: c[0]):
        t = " ".join(str(t).split())
        if not t or (out and out[-1][2].lower() == t.lower()):
            continue
        if out and s < out[-1][1]:
            ps, _pe, pt = out[-1]
            out[-1] = (ps, max(ps + MIN_CUE_MS, s), pt)
        out.append((s, max(e, s + MIN_CUE_MS), t))
    return out


def count_dropped_duplicates(cues: list) -> int:
    """Count consecutive duplicate cues that clean_cues would discard."""
    previous_text = None
    dropped = 0
    for _start, _end, text in sorted(cues, key=lambda cue: cue[0]):
        normalized = " ".join(str(text).split())
        if not normalized:
            continue
        normalized_key = normalized.lower()
        if previous_text == normalized_key:
            dropped += 1
            continue
        previous_text = normalized_key
    return dropped


def _split_text(text: str, max_chars: int) -> list:
    """Split text at the space nearest the middle until every piece fits max_chars."""
    if len(text) <= max_chars or " " not in text:
        return [text]
    mid = len(text) // 2
    best = None
    for i, ch in enumerate(text):
        if ch == " " and 0 < i < len(text) - 1:
            if best is None or abs(i - mid) < abs(best - mid):
                best = i
    if best is None:
        return [text]
    left, right = text[:best].strip(), text[best + 1:].strip()
    if not left or not right:
        return [text]
    return _split_text(left, max_chars) + _split_text(right, max_chars)


def split_long_cues(cues: list, max_chars: int = MAX_CUE_CHARS) -> list:
    """Split over-long cues into consecutive pieces; time is shared by text length.

    A cue too short to give every piece MIN_CUE_MS is left unchanged.
    """
    out = []
    for s, e, t in cues:
        parts = _split_text(t, max_chars)
        n = len(parts)
        dur = e - s
        if n < 2 or dur < MIN_CUE_MS * n:
            out.append((s, e, t))
            continue
        total = sum(len(p) for p in parts) or 1
        cur = s
        for i, p in enumerate(parts):
            if i == n - 1:
                end = e
            else:
                end = cur + max(MIN_CUE_MS, round(dur * len(p) / total))
                end = min(end, e - MIN_CUE_MS * (n - 1 - i))
            out.append((cur, end, p))
            cur = end
    return out


def build_prompt(numbered_lines: str, glossary: str = "") -> str:
    extra = f" Glossary/notes: {glossary}." if glossary else ""
    return (
        "Translate each numbered English subtitle line into natural, concise Persian (Farsi).\n"
        "Rules: keep the exact 'N|' prefix of every line; output exactly one line per input "
        "line; no notes, no markdown, no extra text; keep proper names, character names and "
        "game titles in English." + extra + "\n\n" + numbered_lines
    )


def translate_cues(cues: list, translate_fn, *, max_batches: int = MAX_BATCHES,
                   max_chars: int = MAX_BATCH_CHARS, max_cues: int = MAX_BATCH_CUES,
                   max_tokens: int = MAX_OUT_TOKENS, glossary: str = "",
                   max_missing_ratio: float = MAX_MISSING_RATIO) -> tuple:
    """Translate cues in capped batches. Returns (new_cues, stats). One call per batch, no retries."""
    batches = st.make_batches(cues, max_chars, max_cues)
    if len(batches) > max_batches:
        raise SubtitleError("too_long", f"{len(batches)} batches > {max_batches}")
    translations = [None] * len(cues)
    for start, texts in batches:
        reply = translate_fn(build_prompt(st.numbered(texts, start), glossary), max_tokens=max_tokens)
        for i, tr in enumerate(st.parse_numbered(str(reply or ""), start, len(texts))):
            translations[start + i] = tr
    new_cues, missing = st.apply_translation(cues, translations)
    if cues and missing / len(cues) > max_missing_ratio:
        raise SubtitleError("translation_incomplete", f"{missing}/{len(cues)} missing")
    return new_cues, {"batches": len(batches), "calls": len(batches), "missing": missing}


def probe_duration_ms(path: str, run=subprocess.run) -> int:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path]
    r = run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise SubtitleError("probe_failed", (r.stderr or "")[-200:])
    try:
        return int(float(json.loads(r.stdout)["format"]["duration"]) * 1000)
    except (ValueError, KeyError, TypeError) as exc:
        raise SubtitleError("probe_failed", repr(exc)) from exc


def build_subtitled_video(video: str, workdir: str, translate_fn, *, fontsdir: str = "assets/fonts",
                          font: str = "Estedad", glossary: str = "", transcribe_fn=transcribe,
                          run=subprocess.run, max_missing_ratio: float = 0.0) -> dict:
    """Full pipeline. Returns {out, srt, ass, cues, missing, batches, size, duration_ms}."""
    wd = Path(workdir)
    wd.mkdir(parents=True, exist_ok=True)
    raw_cues = transcribe_fn(str(video))
    asr_report = getattr(raw_cues, "report", {})
    if not isinstance(asr_report, dict):
        asr_report = {}
    dropped_duplicates = count_dropped_duplicates(raw_cues)
    cues = clean_cues(raw_cues)
    if not cues:
        raise SubtitleError("no_cues")
    # Leave space for numbered cue prefixes/newlines so the full prompt stays <= 3000 chars.
    fa_cues, stats = translate_cues(
        cues, translate_fn, max_batches=MAX_BATCHES,
        max_chars=MAX_BATCH_CHARS - 8 * MAX_BATCH_CUES,
        max_cues=MAX_BATCH_CUES, max_tokens=MAX_OUT_TOKENS,
        glossary=glossary, max_missing_ratio=max_missing_ratio,
    )
    fa_cues = split_long_cues(fa_cues)
    srt, ass, out = wd / "fa.srt", wd / "fa.ass", wd / "subtitled.mp4"
    srt.write_text(st.format_srt(fa_cues), encoding="utf-8")
    ass.write_text(st.to_ass(fa_cues, font=font), encoding="utf-8")
    try:
        cmd = st.burn_cmd(str(video), str(ass), fontsdir, str(out))
    except ValueError as exc:
        raise SubtitleError("unsafe_path", str(exc)) from exc
    r = run(cmd, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0 or not out.exists():
        raise SubtitleError("burn_failed", (r.stderr or "")[-300:])
    size = out.stat().st_size
    if size <= 0:
        raise SubtitleError("burn_failed", "empty output")
    if size > MAX_OUT_BYTES:
        raise SubtitleError("output_too_large", f"{size} bytes")
    d_in, d_out = probe_duration_ms(str(video), run), probe_duration_ms(str(out), run)
    if abs(d_in - d_out) > DURATION_TOLERANCE_MS:
        raise SubtitleError("duration_mismatch", f"{d_in} vs {d_out} ms")
    cue_durations_ms = [max(0, int(e) - int(s)) for s, e, _text in cues]
    total_cue_ms = sum(cue_durations_ms)
    max_cue_ms = max(cue_durations_ms, default=0)
    coverage_ratio = total_cue_ms / d_in if d_in > 0 else 0.0
    quality_warnings = []
    if coverage_ratio < 0.5:
        quality_warnings.append("low_cue_coverage")
    if max_cue_ms > 20000:
        quality_warnings.append("long_cue_duration")
    quality_warning = "_and_".join(quality_warnings) if quality_warnings else None
    timeline = [[int(s), int(e), len(str(text))] for s, e, text in cues[:200]]
    return {"out": str(out), "srt": str(srt), "ass": str(ass), "cues": len(fa_cues),
            "missing": stats["missing"], "batches": stats["batches"], "size": size,
            "duration_ms": d_out, **asr_report,
            "asr_raw_cues": len(raw_cues), "asr_clean_cues": len(cues),
            "asr_dropped_duplicates": dropped_duplicates, "asr_max_cue_ms": max_cue_ms,
            "asr_first_start_ms": min(int(s) for s, _e, _text in cues),
            "asr_last_end_ms": max(int(e) for _s, e, _text in cues),
            "asr_cue_coverage_ratio": round(coverage_ratio, 4),
            "asr_timeline": timeline, "asr_quality_warning": quality_warning}
