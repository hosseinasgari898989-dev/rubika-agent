"""Public Mega link export for files already stored under the agent root (MEGAcmd)."""
from __future__ import annotations

import re

from agent.storage.mega import MegaStorage, MegaStorageError

_URL_RE = re.compile(r"https://mega\.(?:nz|io)/[^\s'\"<>]+", re.IGNORECASE)


def export_public_link(storage: MegaStorage, remote_path: str) -> str:
    """Return a public mega.nz/mega.io link for remote_path.

    Tries `mega-export -a -f` first; if that fails (e.g. already exported) falls back to
    `mega-export <path>` which lists the existing link. Raises MegaStorageError("export_link_missing")
    when no URL can be parsed.
    """
    remote = storage._validate_remote_path(remote_path)
    for args in (["mega-export", "-a", "-f", remote], ["mega-export", remote]):
        try:
            output = storage._run(args)
        except MegaStorageError:
            continue
        found = _URL_RE.search(output or "")
        if found:
            return found.group(0).rstrip(".,;)")
    raise MegaStorageError("export_link_missing")
