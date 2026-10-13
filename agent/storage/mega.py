"""MEGAcmd adapter. Credentials are handled by a prior secure mega-login session."""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Callable, Sequence


class MegaStorageError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


Runner = Callable[..., subprocess.CompletedProcess[str]]


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


class MegaStorage:
    """Safe wrapper for MEGAcmd upload, download, listing and delete operations."""

    def __init__(
        self,
        *,
        root: str = "/Rubika-Agent",
        runner: Runner = subprocess.run,
        timeout_seconds: int = 180,
    ) -> None:
        if not root.startswith("/") or ".." in Path(root).parts:
            raise ValueError("invalid_mega_root")
        self.root = root.rstrip("/") or "/"
        self.runner = runner
        self.timeout_seconds = timeout_seconds

    def _run(self, args: Sequence[str]) -> str:
        try:
            result = self.runner(
                list(args), capture_output=True, text=True,
                timeout=self.timeout_seconds, check=False,
            )
        except FileNotFoundError as exc:
            raise MegaStorageError("megacmd_not_installed") from exc
        except subprocess.TimeoutExpired as exc:
            raise MegaStorageError("megacmd_timeout") from exc
        except OSError as exc:
            raise MegaStorageError("megacmd_execution_failed") from exc
        if result.returncode != 0:
            raise MegaStorageError("megacmd_command_failed_" + args[0].replace("mega-", ""))
        return result.stdout or ""

    @staticmethod
    def _safe_name(name: str) -> str:
        if not name or name in {".", ".."} or "/" in name or "\\" in name:
            raise ValueError("invalid_remote_filename")
        return name

    def ensure_directory(self, relative_dir: str = "") -> str:
        parts = [part for part in relative_dir.split("/") if part]
        if any(part in {".", ".."} for part in parts):
            raise ValueError("invalid_relative_directory")
        current = self.root
        self._run(["mega-ls", current])
        for part in parts:
            current = current.rstrip("/") + "/" + part
            try:
                self._run(["mega-ls", current])
            except MegaStorageError:
                try:
                    self._run(["mega-mkdir", current])
                except MegaStorageError:
                    self._run(["mega-ls", current])
        return current

    def upload(self, local_path: str | Path, relative_dir: str = "staging") -> str:
        path = Path(local_path)
        if not path.is_file() or path.stat().st_size <= 0:
            raise MegaStorageError("local_file_missing_or_empty")
        filename = self._safe_name(path.name)
        remote_dir = self.ensure_directory(relative_dir)
        self._run(["mega-put", str(path), remote_dir + "/"])
        return remote_dir.rstrip("/") + "/" + filename

    def download(self, remote_path: str, destination_dir: str | Path) -> Path:
        remote = self._validate_remote_path(remote_path)
        dest_dir = Path(destination_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        self._run(["mega-get", remote, str(dest_dir)])
        result = dest_dir / remote.rsplit("/", 1)[-1]
        if not result.is_file() or result.stat().st_size <= 0:
            raise MegaStorageError("download_output_missing")
        return result

    def verify_roundtrip(self, local_path: str | Path, relative_dir: str = "staging") -> dict[str, str | int]:
        source = Path(local_path)
        remote_path = self.upload(source, relative_dir)
        downloaded = self.download(remote_path, source.parent / ".mega-roundtrip")
        expected = sha256_file(source)
        actual = sha256_file(downloaded)
        if expected != actual:
            raise MegaStorageError("sha256_mismatch")
        return {
            "remote_path": remote_path,
            "sha256": actual,
            "size_bytes": downloaded.stat().st_size,
        }

    def delete(self, remote_path: str) -> None:
        self._run(["mega-rm", self._validate_remote_path(remote_path)])

    def _validate_remote_path(self, remote_path: str) -> str:
        normalized = "/" + "/".join(part for part in remote_path.split("/") if part)
        if normalized != remote_path.rstrip("/") or not normalized.startswith(self.root.rstrip("/") + "/"):
            raise ValueError("remote_path_outside_agent_root")
        if any(part in {".", ".."} for part in Path(normalized).parts):
            raise ValueError("invalid_remote_path")
        return normalized
