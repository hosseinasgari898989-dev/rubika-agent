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


class SubtitleError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def audio_cmd(path: str) -> list:
    """ffmpeg argv: 16 kHz mono float32 PCM on stdout (what faster-whisper expects)."""
    return ["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", "16000",
            "-f", "f32le", "-"]


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


def transcribe(video: str, model_size: str = "small", language: str = "en",
               model_factory=None) -> list:
    """Return raw cues [(start_ms, end_ms, text)] using faster-whisper (CPU, int8)."""
    audio = video
    if model_factory is None:
        from faster_whisper import WhisperModel  # lazy: only the Mob X workflow installs it

        def model_factory():
            return WhisperModel(model_size, device="cpu", compute_type="int8")

        audio = decode_audio_16k(video)
    model = model_factory()
    segments, _info = model.transcribe(audio, language=language, vad_filter=True, beam_size=1)
    cues = []
    for seg in segments:
        text = " ".join(str(seg.text).split())
        if text:
            cues.append((int(seg.start * 1000), int(seg.end * 1000), text))
    return cues


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
    cues = clean_cues(transcribe_fn(str(video)))
    if not cues:
        raise SubtitleError("no_cues")
    # Leave space for numbered cue prefixes/newlines so the full prompt stays <= 3000 chars.
    fa_cues, stats = translate_cues(
        cues, translate_fn, max_batches=MAX_BATCHES,
        max_chars=MAX_BATCH_CHARS - 8 * MAX_BATCH_CUES,
        max_cues=MAX_BATCH_CUES, max_tokens=MAX_OUT_TOKENS,
        glossary=glossary, max_missing_ratio=max_missing_ratio,
    )
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
    return {"out": str(out), "srt": str(srt), "ass": str(ass), "cues": len(fa_cues),
            "missing": stats["missing"], "batches": stats["batches"], "size": size,
            "duration_ms": d_out}
