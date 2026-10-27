from __future__ import annotations

import unittest

from agent.brain.speech_gate import evaluate_speech_segments


class SpeechGateTests(unittest.TestCase):
    def test_confident_segments_cross_duration_threshold_without_returning_transcript(self):
        result = evaluate_speech_segments([
            {
                "start": 0.0, "end": 0.55, "text": "spoken words",
                "no_speech_prob": 0.08, "avg_logprob": -0.3,
            },
            {
                "start": 0.7, "end": 1.2, "text": "another phrase",
                "no_speech_prob": 0.12, "avg_logprob": -0.5,
            },
        ], media_duration_seconds=2.0)
        self.assertEqual(result.decision, "speech_detected")
        self.assertEqual(result.accepted_segments, 2)
        self.assertEqual(result.speech_seconds, 1.05)
        self.assertEqual(result.reason_code, "speech_confident")
        self.assertFalse(hasattr(result, "text"))
        self.assertFalse(hasattr(result, "transcript"))

    def test_empty_successful_transcription_means_no_speech_segments(self):
        result = evaluate_speech_segments([])
        self.assertEqual(result.decision, "no_speech_detected")
        self.assertEqual(result.reason_code, "no_speech_segments")

    def test_high_no_speech_probability_is_not_counted_as_speech(self):
        result = evaluate_speech_segments([{
            "start": 0, "end": 4, "text": "hallucinated words",
            "no_speech_prob": 0.92, "avg_logprob": -0.2,
        }])
        self.assertEqual(result.decision, "no_speech_detected")
        self.assertEqual(result.reason_code, "no_speech_probability_high")
        self.assertEqual(result.accepted_segments, 0)

    def test_low_average_log_probability_remains_uncertain_not_silence(self):
        result = evaluate_speech_segments([{
            "start": 0, "end": 2, "text": "unclear words",
            "no_speech_prob": 0.05, "avg_logprob": -2.8,
        }])
        self.assertEqual(result.decision, "uncertain")
        self.assertEqual(result.reason_code, "speech_confidence_insufficient")

    def test_missing_confidence_fails_closed_to_uncertain(self):
        result = evaluate_speech_segments([{
            "start": 0, "end": 2, "text": "words without confidence metrics",
        }])
        self.assertEqual(result.decision, "uncertain")
        self.assertEqual(result.reason_code, "speech_confidence_missing")

    def test_short_confident_segment_is_uncertain(self):
        result = evaluate_speech_segments([{
            "start": 0.1, "end": 0.3, "text": "yes",
            "no_speech_prob": 0.05, "avg_logprob": -0.4,
        }])
        self.assertEqual(result.decision, "uncertain")
        self.assertEqual(result.reason_code, "speech_below_duration_threshold")

    def test_media_duration_clips_segments(self):
        result = evaluate_speech_segments([{
            "start": 0, "end": 10, "text": "short media",
            "no_speech_prob": 0.05, "avg_logprob": -0.4,
        }], media_duration_seconds=0.5, minimum_speech_seconds=0.4)
        self.assertEqual(result.decision, "speech_detected")
        self.assertEqual(result.speech_seconds, 0.5)

    def test_failed_transcriber_never_reports_no_speech(self):
        result = evaluate_speech_segments([], transcription_succeeded=False)
        self.assertEqual(result.decision, "uncertain")
        self.assertEqual(result.reason_code, "transcription_failed")

    def test_malformed_segments_are_uncertain(self):
        result = evaluate_speech_segments([
            {"start": "bad", "end": 1, "text": "bad time"},
            {"start": 1, "end": 0.5, "text": "negative duration"},
        ])
        self.assertEqual(result.decision, "uncertain")
        self.assertEqual(result.reason_code, "segments_invalid")
        self.assertEqual(result.rejected_segments, 2)

    def test_invalid_thresholds_are_rejected(self):
        with self.assertRaises(ValueError):
            evaluate_speech_segments([], maximum_no_speech_probability=1.5)
        with self.assertRaises(ValueError):
            evaluate_speech_segments([], media_duration_seconds=0)


if __name__ == "__main__":
    unittest.main()
