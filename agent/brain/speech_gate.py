"""Conservative, dependency-free gate for speech segments returned by an ASR adapter.

This module never runs a model, opens media, stores transcripts, or logs transcript text.
Callers must provide segment objects with start/end/text and at least one confidence signal.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SpeechGateResult:
    decision: str
    speech_seconds: float
    accepted_segments: int
    rejected_segments: int
    reason_code: str


def _field(segment: Any, name: str) -> Any:
    if isinstance(segment, Mapping):
        return segment.get(name)
    return getattr(segment, name, None)


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def evaluate_speech_segments(
    segments: Iterable[Any] | None,
    *,
    media_duration_seconds: float | None = None,
    transcription_succeeded: bool = True,
    minimum_speech_seconds: float = 0.8,
    maximum_no_speech_probability: float = 0.65,
    minimum_average_log_probability: float = -1.25,
) -> SpeechGateResult:
    """Classify ASR segments without returning or retaining their text.

    Decisions are speech_detected, no_speech_detected or uncertain.
    Missing/invalid confidence metrics fail closed to uncertain. An uncertain
    result must not be treated as proof that the clip contains no speech.
    """
    min_speech = _finite_float(minimum_speech_seconds)
    max_no_speech = _finite_float(maximum_no_speech_probability)
    min_log_probability = _finite_float(minimum_average_log_probability)
    duration = (
        _finite_float(media_duration_seconds)
        if media_duration_seconds is not None
        else None
    )
    if (
        min_speech is None or min_speech <= 0
        or max_no_speech is None or not 0 <= max_no_speech <= 1
        or min_log_probability is None
        or (media_duration_seconds is not None and (duration is None or duration <= 0))
    ):
        raise ValueError("speech_gate_threshold_invalid")

    if not transcription_succeeded:
        return SpeechGateResult("uncertain", 0.0, 0, 0, "transcription_failed")
    if segments is None:
        return SpeechGateResult("uncertain", 0.0, 0, 0, "transcription_result_missing")
    try:
        rows = list(segments)
    except (TypeError, ValueError):
        return SpeechGateResult("uncertain", 0.0, 0, 0, "transcription_result_invalid")
    if not rows:
        return SpeechGateResult("no_speech_detected", 0.0, 0, 0, "no_speech_segments")

    accepted = 0
    rejected = 0
    high_no_speech = 0
    plausible = 0
    unknown_confidence = 0
    invalid = 0
    speech_seconds = 0.0

    for row in rows:
        start = _finite_float(_field(row, "start"))
        end = _finite_float(_field(row, "end"))
        segment_text = _field(row, "text")
        if start is None or end is None or end <= start or not isinstance(segment_text, str):
            rejected += 1
            invalid += 1
            continue
        if not " ".join(segment_text.split()):
            rejected += 1
            invalid += 1
            continue

        start = max(0.0, start)
        if duration is not None:
            end = min(duration, end)
        if end <= start:
            rejected += 1
            invalid += 1
            continue

        plausible += 1
        no_speech_raw = _field(row, "no_speech_prob")
        log_probability_raw = _field(row, "avg_logprob")
        no_speech_probability = _finite_float(no_speech_raw)
        average_log_probability = _finite_float(log_probability_raw)

        if no_speech_raw is not None and (
            no_speech_probability is None
            or not 0.0 <= no_speech_probability <= 1.0
        ):
            rejected += 1
            invalid += 1
            continue
        if log_probability_raw is not None and average_log_probability is None:
            rejected += 1
            invalid += 1
            continue
        if no_speech_probability is None and average_log_probability is None:
            rejected += 1
            unknown_confidence += 1
            continue
        if (
            no_speech_probability is not None
            and no_speech_probability > max_no_speech
        ):
            rejected += 1
            high_no_speech += 1
            continue
        if (
            average_log_probability is not None
            and average_log_probability < min_log_probability
        ):
            rejected += 1
            continue

        accepted += 1
        speech_seconds += end - start

    rounded_seconds = round(speech_seconds, 3)
    if accepted and speech_seconds >= min_speech:
        return SpeechGateResult(
            "speech_detected", rounded_seconds, accepted, rejected, "speech_confident"
        )
    if plausible and high_no_speech == plausible:
        return SpeechGateResult(
            "no_speech_detected", rounded_seconds, accepted, rejected,
            "no_speech_probability_high",
        )
    if accepted:
        return SpeechGateResult(
            "uncertain", rounded_seconds, accepted, rejected,
            "speech_below_duration_threshold",
        )
    if not plausible and invalid:
        return SpeechGateResult("uncertain", 0.0, 0, rejected, "segments_invalid")
    reason = (
        "speech_confidence_missing"
        if unknown_confidence
        else "speech_confidence_insufficient"
    )
    return SpeechGateResult("uncertain", 0.0, accepted, rejected, reason)
