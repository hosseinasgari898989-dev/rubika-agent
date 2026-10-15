from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from agent.storage.mega import MegaStorage, MegaStorageError


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.existing = {"/Rubika-Agent"}

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        command = args[0]
        target = args[-1]
        if command == "mega-ls":
            if target in self.existing:
                return subprocess.CompletedProcess(args, 0, "ok", "")
            return subprocess.CompletedProcess(args, 1, "", "not found")
        if command == "mega-mkdir":
            self.existing.add(target)
            return subprocess.CompletedProcess(args, 0, "ok", "")
        if command in {"mega-put", "mega-get", "mega-rm"}:
            return subprocess.CompletedProcess(args, 0, "ok", "")
        return subprocess.CompletedProcess(args, 0, "", "")


class MegaSafetyTests(unittest.TestCase):
    def test_validates_root_and_file_names(self):
        with self.assertRaises(ValueError):
            MegaStorage(root="/Rubika-Agent/../private")
        client = MegaStorage(runner=FakeRunner())
        with self.assertRaises(ValueError):
            client._safe_name("../token.txt")
        with self.assertRaises(ValueError):
            client._validate_remote_path("/Rubika-Agent/../secret.txt")

    def test_missing_or_empty_file_is_rejected(self):
        client = MegaStorage(runner=FakeRunner())
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(MegaStorageError):
                client.upload(Path(folder) / "missing.mp4")
            empty = Path(folder) / "empty.mp4"
            empty.touch()
            with self.assertRaises(MegaStorageError):
                client.upload(empty)

    def test_remote_path_must_stay_in_agent_folder(self):
        client = MegaStorage(runner=FakeRunner())
        self.assertEqual(
            client._validate_remote_path("/Rubika-Agent/staging/video.mp4"),
            "/Rubika-Agent/staging/video.mp4",
        )
        with self.assertRaises(ValueError):
            client._validate_remote_path("/somewhere-else/video.mp4")

    def test_storage_adapter_uses_cli_and_does_not_pass_credentials_as_arguments(self):
        fake = FakeRunner()
        client = MegaStorage(runner=fake)
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "sample.mp4"
            file.write_bytes(b"safe demo")
            remote = client.upload(file, "staging")
        self.assertEqual(remote, "/Rubika-Agent/staging/sample.mp4")
        self.assertTrue(all("password" not in " ".join(call).lower() for call in fake.calls))
        self.assertIn(["mega-put", str(file), "/Rubika-Agent/staging/"], fake.calls)


if __name__ == "__main__":
    unittest.main()
