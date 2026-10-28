import struct
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import subtitle_pipeline as sp  # noqa: E402
from agent.brain import speech_gate as sg  # noqa: E402

try:
    import numpy as np
except ImportError:  # CI base image may not have numpy
    np = None


class AudioCmd(unittest.TestCase):
    def test_argv_is_16k_mono_f32_pipe(self):
        for cmd in (sp.audio_cmd("/tmp/a.mp4"), sg.audio_cmd("/tmp/a.mp4")):
            self.assertEqual(cmd[0], "ffmpeg")
            self.assertIn("-nostdin", cmd)
            self.assertEqual(cmd[cmd.index("-ar") + 1], "16000")
            self.assertEqual(cmd[cmd.index("-ac") + 1], "1")
            self.assertEqual(cmd[cmd.index("-f") + 1], "f32le")
            self.assertEqual(cmd[-1], "-")

    def test_failure_raises_in_both_modules(self):
        run = lambda cmd, **k: SimpleNamespace(returncode=1, stdout=b"", stderr=b"bad file")  # noqa: E731
        with self.assertRaises(sp.SubtitleError) as cm:
            sp.decode_audio_16k("x.mp4", run=run)
        self.assertEqual(cm.exception.code, "audio_decode_failed")
        with self.assertRaises(RuntimeError) as cm2:
            sg.decode_audio_16k("x.mp4", run=run)
        self.assertIn("audio_decode_failed", str(cm2.exception))

    @unittest.skipIf(np is None, "numpy not installed")
    def test_pcm_to_float32_array_and_trailing_bytes(self):
        raw = struct.pack("<3f", 0.0, 0.5, -0.5) + b"\x00\x01"
        run = lambda cmd, **k: SimpleNamespace(returncode=0, stdout=raw, stderr=b"")  # noqa: E731
        for fn in (sp.decode_audio_16k, sg.decode_audio_16k):
            arr = fn("x.mp4", run=run)
            self.assertEqual(arr.dtype, np.float32)
            self.assertEqual(arr.tolist(), [0.0, 0.5, -0.5])


if __name__ == "__main__":
    unittest.main()
