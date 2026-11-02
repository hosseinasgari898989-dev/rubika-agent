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
import math
import re
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
MAX_OUT_TOKENS = 8192
MAX_MISSING_RATIO = 0.3
MAX_OUT_BYTES = 49 * 1024 * 1024  # Rubika upload ladder is OK only below ~50 MiB
DURATION_TOLERANCE_MS = 1500
MIN_CUE_MS = 400
MAX_CUE_CHARS = 80  # longer Persian cues are split in two or more timed pieces

_NON_SPEECH_LABEL_RE = re.compile(
    r"^(?:"
    r"(?:background|tense|dramatic|suspenseful|scary|sad|happy|soft|instrumental|upbeat)\s+music"
    r"|music(?:\s*[-:]\s*(?:background|tense|dramatic|suspenseful|scary|sad|happy|soft|instrumental|upbeat))?"
    r"|(?:sound\s+effects?|sfx)(?:\s*[-:]\s*[a-z][a-z\s-]*)?"
    r"|(?:applause|clapping|laughter|laughing|laughs|cheering|crowd\s+cheering|crowd\s+noise|"
    r"sighs?|sighing|gasps?|gasping|coughs?|coughing|crying|sobbing|snoring|whistling|"
    r"footsteps|door\s+slams?|bell\s+rings?)"
    r"|موسیقی(?:[\s\u200c]+(?:تنش[\s\u200c]*زا|هیجانی|دلهره‌آور|پس‌زمینه|پس زمینه|غمگین|شاد|ترسناک|ملایم|حماسی|آرام|متن|زمینه‌ای))?"
    r"|(?:تنش[\s\u200c]*زا|هیجانی|دلهره‌آور|پس‌زمینه|پس زمینه|غمگین|شاد|ترسناک|ملایم|حماسی|آرام)[\s\u200c]+موسیقی"
    r"|(?:جلوه|افکت)(?:های|‌های)?[\s\u200c]+صوتی"
    r"|صدای[\s\u200c]+(?:افکت|موسیقی|تشویق|کف[\s\u200c]+زدن|دست[\s\u200c]+زدن|خنده|گریه|سرفه|فریاد)"
    r"|(?:تشویق|کف[\s\u200c]+زدن|دست[\s\u200c]+زدن|خنده|خندیدن|گریه|گریه‌کردن|سرفه|آه|ناله|فریاد)"
    r")$",
    re.IGNORECASE,
)
_NON_SPEECH_BRACKET_RE = re.compile(
    r"(?:\[|\(|（|【)\s*(?P<label>[^\[\]()（）【】]+?)\s*(?:\]|\)|）|】)"
)
_NON_SPEECH_NOTE_RE = re.compile(r"[♪♫♬]+\s*(?P<label>[^♪♫♬]+?)\s*[♪♫♬]+")
_MUSIC_MARKER_RE = re.compile(r"[♪♫♬]+")


def _is_non_speech_label(value: object) -> bool:
    """Recognize a whole non-speech cue without matching ordinary spoken sentences."""
    text = str(value or "").strip()
    text = re.sub(r"^[\s\[\](){}（）【】「」『』<>]+|[\s\[\](){}（）【】「」『』<>]+$", "", text)
    text = _MUSIC_MARKER_RE.sub(" ", text).replace("\u200c", " ")
    text = re.sub(r"\s+", " ", text).strip(" \t\r\n:：-–—")
    return not text or bool(_NON_SPEECH_LABEL_RE.fullmatch(text))


def strip_non_speech_tags(cues: list) -> tuple[list, int]:
    """Remove non-speech-only cues and inline non-speech labels, returning a count only."""
    cleaned = []
    removed = 0
    for start, end, raw_text in cues:
        text = str(raw_text or "")
        if _is_non_speech_label(text):
            removed += 1
            continue

        cue_removed = 0

        def remove_bracketed(match):
            nonlocal cue_removed
            if _is_non_speech_label(match.group("label")):
                cue_removed += 1
                return " "
            return match.group(0)

        text = _NON_SPEECH_BRACKET_RE.sub(remove_bracketed, text)

        def remove_note_label(match):
            nonlocal cue_removed
            if _is_non_speech_label(match.group("label")):
                cue_removed += 1
                return " "
            return match.group(0)

        text = _NON_SPEECH_NOTE_RE.sub(remove_note_label, text)
        text, music_markers = _MUSIC_MARKER_RE.subn(" ", text)
        if music_markers:
            cue_removed += 1
        text = re.sub(r"\s+([,.;!?،؛؟])", r"\1", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"^[,،;؛:–—-]+\s*|\s*[,،;؛:–—-]+$", "", text).strip()

        if not text:
            removed += max(1, cue_removed)
            continue
        removed += cue_removed
        cleaned.append((start, end, text))
    return cleaned, removed



# ASR tuning: more sensitive VAD (catches quiet/unclear speech) and no context carry-over
# between segments (prevents hallucination loops on music/noise).
ASR_VAD_PARAMETERS = {"threshold": 0.35, "min_silence_duration_ms": 400, "speech_pad_ms": 300}
ASR_OPTIONS = {
    "vad_filter": True,
    "vad_parameters": ASR_VAD_PARAMETERS,
    "condition_on_previous_text": False,
    "word_timestamps": True,
    "hallucination_silence_threshold": 2,
}


class SubtitleError(Exception):
    def __init__(self, code: str, detail: str = "", *, diagnostics: dict | None = None):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail
        self.diagnostics = dict(diagnostics or {})


def audio_cmd(path: str, *, normalize: bool = True) -> list:
    """Decode high-pass-filtered speech to 16 kHz mono float32 PCM."""
    audio_filter = "highpass=f=80,loudnorm" if normalize else "highpass=f=80"
    return ["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-af", audio_filter,
            "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"]


def decode_audio_16k(path: str, run=subprocess.run, *, normalize: bool = True,
                      timeout: float = 600.0):
    """Decode with ffmpeg to float32 PCM; optionally omit loudness normalization."""
    r = run(audio_cmd(path, normalize=normalize), capture_output=True, timeout=timeout)
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


ASR_DIAGNOSTIC_MAX_SEGMENTS = 200
ASR_CUE_GAP_THRESHOLD_MS = 3000


def _segment_range_ms(segment: object) -> list[int | None]:
    """Return a transcript-free [start_ms, end_ms] range for one ASR segment."""
    result = []
    for attribute in ("start", "end"):
        try:
            seconds = float(getattr(segment, attribute))
            if not math.isfinite(seconds):
                result.append(None)
            else:
                result.append(max(0, int(round(seconds * 1000))))
        except (AttributeError, TypeError, ValueError, OverflowError):
            result.append(None)
    return result


def cue_free_gaps_over_3s(cues: list, duration_ms: int,
                          threshold_ms: int = ASR_CUE_GAP_THRESHOLD_MS) -> list[list[int]]:
    """Return leading, interior and trailing cue-free intervals longer than three seconds."""
    duration = max(0, int(duration_ms))
    threshold = max(0, int(threshold_ms))
    if duration <= 0:
        return []
    intervals = []
    for start, end, _text in cues:
        start_ms = min(duration, max(0, int(start)))
        end_ms = min(duration, max(start_ms, int(end)))
        intervals.append((start_ms, end_ms))
    intervals.sort()
    cursor = 0
    gaps = []
    for start_ms, end_ms in intervals:
        if start_ms > cursor and start_ms - cursor > threshold:
            gaps.append([cursor, start_ms, start_ms - cursor])
        cursor = max(cursor, end_ms)
        if cursor >= duration:
            break
    if duration - cursor > threshold:
        gaps.append([cursor, duration, duration - cursor])
    return gaps


def transcribe(video: str, model_size: str = "medium", language: str = "en",
               model_factory=None, runtime_limit_seconds: float = 900.0,
               speech_seconds: float | None = None, duration_ms: int | None = None) -> list:
    """Transcribe and, only for suspiciously sparse medium output, run two bounded retries."""
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
    try:
        speech_seconds_value = float(speech_seconds) if speech_seconds is not None else 0.0
        if not math.isfinite(speech_seconds_value) or speech_seconds_value <= 0:
            speech_seconds_value = 0.0
    except (TypeError, ValueError, OverflowError):
        speech_seconds_value = 0.0
    duration_ms_value = max(0, int(duration_ms or 0))

    def run_model(size: str, audio_input, options: dict, *, watch_runtime: bool) -> tuple[list, dict, float, float, bool]:
        model_started = time.monotonic()
        model = create_model(size)
        segments, _info = model.transcribe(
            audio_input, language=language, beam_size=5, **options,
        )
        iterator = iter(segments)
        cues = []
        before_ranges = []
        after_ranges = []
        dropped_ranges = {
            "empty_segment_text": [],
            "missing_word_timing": [],
            "invalid_word_timing": [],
        }
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
            segment_range = _segment_range_ms(seg)
            before_ranges.append(segment_range)
            text = " ".join(str(getattr(seg, "text", "") or "").split())
            if not text:
                dropped_ranges["empty_segment_text"].append(segment_range)
                continue

            raw_words = getattr(seg, "words", None)
            if raw_words is None:
                dropped_ranges["missing_word_timing"].append(segment_range)
                continue

            word_times = []
            saw_word_entry = False
            for word in raw_words:
                saw_word_entry = True
                word_text = " ".join(str(getattr(word, "word", "") or "").split())
                if not word_text:
                    continue
                try:
                    word_start = float(word.start)
                    word_end = float(word.end)
                except (AttributeError, TypeError, ValueError, OverflowError):
                    continue
                if (
                    math.isfinite(word_start)
                    and math.isfinite(word_end)
                    and word_end > word_start
                ):
                    word_times.append((word_start, word_end))

            if not saw_word_entry:
                dropped_ranges["missing_word_timing"].append(segment_range)
                continue
            if not word_times:
                dropped_ranges["invalid_word_timing"].append(segment_range)
                continue

            start_ms = int(round(word_times[0][0] * 1000))
            end_ms = int(round(word_times[-1][1] * 1000))
            if end_ms <= start_ms:
                dropped_ranges["invalid_word_timing"].append(segment_range)
                continue
            cues.append((start_ms, end_ms, text))
            after_ranges.append(segment_range)

        segment_diagnostics = {
            "segments_before_word_timing_count": len(before_ranges),
            "segments_before_word_timing_ranges_ms": before_ranges[:ASR_DIAGNOSTIC_MAX_SEGMENTS],
            "segments_before_word_timing_ranges_truncated": (
                len(before_ranges) > ASR_DIAGNOSTIC_MAX_SEGMENTS
            ),
            "segments_after_word_timing_count": len(after_ranges),
            "segments_after_word_timing_ranges_ms": after_ranges[:ASR_DIAGNOSTIC_MAX_SEGMENTS],
            "segments_after_word_timing_ranges_truncated": (
                len(after_ranges) > ASR_DIAGNOSTIC_MAX_SEGMENTS
            ),
            "segments_dropped_word_timing": [
                {
                    "reason": reason,
                    "count": len(ranges),
                    "ranges_ms": ranges[:ASR_DIAGNOSTIC_MAX_SEGMENTS],
                    "ranges_truncated": len(ranges) > ASR_DIAGNOSTIC_MAX_SEGMENTS,
                }
                for reason, ranges in dropped_ranges.items() if ranges
            ],
        }
        model_elapsed = max(0.0, time.monotonic() - model_started)
        total_elapsed = max(0.0, time.monotonic() - started)
        if watch_runtime and size == "medium" and total_elapsed >= runtime_limit_seconds:
            exceeded = True
        return cues, segment_diagnostics, model_elapsed, total_elapsed, exceeded

    def summarize_pass(name: str, raw_cues: list, elapsed: float,
                       segment_diagnostics: dict):
        cleaned = clean_cues(raw_cues)
        duplicates = count_dropped_duplicates(raw_cues)
        diagnostics = build_asr_diagnostics(
            raw_cues, cleaned, duplicates, duration_ms_value,
        )
        char_count = sum(len(text) for _start, _end, text in cleaned)
        cue_gaps = cue_free_gaps_over_3s(cleaned, duration_ms_value)
        summary = {
            "name": name,
            "raw_cues": len(raw_cues),
            "chars": char_count,
            "coverage": diagnostics["asr_cue_coverage_ratio"],
            "max_cue_ms": diagnostics["asr_max_cue_ms"],
            "elapsed_seconds": round(max(0.0, elapsed), 1),
            "dropped_duplicates": duplicates,
            **segment_diagnostics,
            "cue_free_gaps_over_3s": cue_gaps[:ASR_DIAGNOSTIC_MAX_SEGMENTS],
            "cue_free_gaps_over_3s_truncated": (
                len(cue_gaps) > ASR_DIAGNOSTIC_MAX_SEGMENTS
            ),
        }
        return summary, cleaned, diagnostics, duplicates

    # Pass A is the existing medium/int8 configuration and remains the default path.
    cues_a, segments_a, elapsed_a, total_elapsed, exceeded = run_model(
        model_size, audio, ASR_OPTIONS, watch_runtime=True,
    )
    model_used = model_size
    fallback_reason = None
    medium_elapsed = 0.0
    fallback_elapsed = 0.0
    pass_a_elapsed = elapsed_a
    summary_a, cleaned_a, diagnostics_a, duplicates_a = summarize_pass(
        "A", cues_a, pass_a_elapsed, segments_a,
    )
    passes = [summary_a]
    candidates = []
    if duplicates_a == 0 and summary_a["chars"] > 0:
        candidates.append(("A", cues_a, summary_a, diagnostics_a))

    chars_per_speech_second_a = (
        summary_a["chars"] / speech_seconds_value if speech_seconds_value > 0 else None
    )
    suspicious = (
        diagnostics_a["asr_quality_warning"] is not None
        or (
            chars_per_speech_second_a is not None
            and chars_per_speech_second_a < 5.0
        )
    )

    # Extra passes share the original 900-second deadline. A pass that reaches the
    # deadline is diagnostic only; its partial cues are never allowed to replace A.
    if suspicious and model_size == "medium" and not exceeded:
        retry_specs = (
            ("B", audio, {**ASR_OPTIONS, "vad_filter": False}),
            ("C", None, {
                **ASR_OPTIONS,
                "vad_filter": True,
                "vad_parameters": {**ASR_VAD_PARAMETERS, "threshold": 0.2},
            }),
        )
        for name, pass_audio, options in retry_specs:
            if time.monotonic() - started >= runtime_limit_seconds:
                break
            if name == "C":
                remaining_seconds = runtime_limit_seconds - (time.monotonic() - started)
                if remaining_seconds <= 0:
                    break
                try:
                    # C deliberately omits loudnorm; only the high-pass filter is applied.
                    pass_audio = decode_audio_16k(
                        video, normalize=False, timeout=min(600.0, remaining_seconds),
                    )
                except (SubtitleError, subprocess.TimeoutExpired):
                    break
                if time.monotonic() - started >= runtime_limit_seconds:
                    break
            retry_cues, retry_segments, retry_elapsed, total_elapsed, retry_exceeded = run_model(
                model_size, pass_audio, options, watch_runtime=True,
            )
            summary, _cleaned, diagnostics, duplicates = summarize_pass(
                name, retry_cues, retry_elapsed, retry_segments,
            )
            passes.append(summary)
            if not retry_exceeded and duplicates == 0 and summary["chars"] > 0:
                candidates.append((name, retry_cues, summary, diagnostics))
            if retry_exceeded:
                break

    # Highest clean character count wins. Ties keep the earlier pass; a duplicate
    # loop is never eligible, and A remains the safe fallback if every pass loops.
    if candidates:
        selected_name, selected_cues, selected_summary, selected_diagnostics = max(
            candidates, key=lambda candidate: candidate[2]["chars"],
        )
    else:
        # No non-looping pass is safe to burn; fail closed rather than publish a repeated cue.
        selected_name = None
        selected_cues = []
        selected_summary = summary_a
        selected_diagnostics = diagnostics_a

    # This rate describes the original A pass that triggered (or avoided) retries.
    chars_per_speech_second = chars_per_speech_second_a
    total_elapsed = max(total_elapsed, time.monotonic() - started)
    report = {
        "asr_initial_model": model_size,
        "asr_model": model_used,
        "asr_fallback_reason": fallback_reason,
        "asr_elapsed_seconds": round(max(0.0, total_elapsed), 1),
        "asr_medium_elapsed_seconds": round(medium_elapsed, 1),
        "asr_fallback_elapsed_seconds": round(fallback_elapsed, 1),
        "asr_passes": passes,
        "asr_selected_pass": selected_name,
        "chars_per_speech_second": (
            round(chars_per_speech_second, 3)
            if chars_per_speech_second is not None else None
        ),
        **selected_diagnostics,
    }
    return TranscriptionResult(selected_cues, report)


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


def build_asr_diagnostics(raw_cues: list, cues: list, dropped_duplicates: int,
                         duration_ms: int) -> dict:
    """Return bounded ASR timing statistics only; never retain cue text."""
    cue_durations_ms = [max(0, int(end) - int(start)) for start, end, _text in cues]
    total_cue_ms = sum(cue_durations_ms)
    max_cue_ms = max(cue_durations_ms, default=0)
    duration_ms = max(0, int(duration_ms))
    coverage_ratio = total_cue_ms / duration_ms if duration_ms > 0 else 0.0
    quality_warnings = []
    if duration_ms > 0 and coverage_ratio < 0.5:
        quality_warnings.append("low_cue_coverage")
    if max_cue_ms > 20000:
        quality_warnings.append("long_cue_duration")
    timeline = [
        [max(0, int(start)), max(0, int(end)), len(str(text))]
        for start, end, text in cues[:200]
    ]
    return {
        "asr_raw_cues": len(raw_cues),
        "asr_clean_cues": len(cues),
        "asr_dropped_duplicates": max(0, int(dropped_duplicates)),
        "asr_max_cue_ms": max_cue_ms,
        "asr_first_start_ms": min((max(0, int(start)) for start, _end, _text in cues), default=0),
        "asr_last_end_ms": max((max(0, int(end)) for _start, end, _text in cues), default=0),
        "asr_cue_coverage_ratio": round(max(0.0, coverage_ratio), 4),
        "asr_timeline": timeline,
        "asr_quality_warning": "_and_".join(quality_warnings) if quality_warnings else None,
    }


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
    """Full pipeline. ASR diagnostics survive every SubtitleError after transcription."""
    wd = Path(workdir)
    wd.mkdir(parents=True, exist_ok=True)
    raw_cues = transcribe_fn(str(video))
    asr_report = getattr(raw_cues, "report", {})
    asr_report = dict(asr_report) if isinstance(asr_report, dict) else {}
    dropped_duplicates = count_dropped_duplicates(raw_cues)
    cues = clean_cues(raw_cues)
    cues, non_speech_tags_removed = strip_non_speech_tags(cues)
    cues = clean_cues(cues)
    asr_diagnostics = build_asr_diagnostics(raw_cues, cues, dropped_duplicates, 0)
    try:
        d_in = probe_duration_ms(str(video), run)
        asr_diagnostics = build_asr_diagnostics(raw_cues, cues, dropped_duplicates, d_in)
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
        try:
            ass_text, _glyph_bounds = st.to_ass_with_glyph_boxes(
                fa_cues, fontsdir=fontsdir, font=font,
            )
        except Exception as exc:
            raise SubtitleError(
                "subtitle_box_render_failed", type(exc).__name__,
            ) from exc
        ass.write_text(ass_text, encoding="utf-8")
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
        d_out = probe_duration_ms(str(out), run)
        if abs(d_in - d_out) > DURATION_TOLERANCE_MS:
            raise SubtitleError("duration_mismatch", f"{d_in} vs {d_out} ms")
        return {
            "out": str(out), "srt": str(srt), "ass": str(ass), "cues": len(fa_cues),
            "missing": stats["missing"], "batches": stats["batches"], "size": size,
            "duration_ms": d_out, **asr_report, **asr_diagnostics,
            "non_speech_tags_removed": non_speech_tags_removed,
        }
    except SubtitleError as exc:
        exc.diagnostics = {
            **asr_report, **asr_diagnostics,
            "non_speech_tags_removed": non_speech_tags_removed,
        }
        raise
