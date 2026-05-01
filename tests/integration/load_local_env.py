"""Load optional ``local.env`` (gitignored) for integration test machine settings."""

from __future__ import annotations

import os
from pathlib import Path


def load_local_env_file(path: Path) -> bool:
    """
    Parse simple ``KEY=value`` lines into ``os.environ`` (later lines override).

    Ignores empty lines and ``#`` comments. Strips one pair of surrounding ``'`` or ``"``.
    Returns True if *path* existed and was read.
    """
    if not path.is_file():
        return False
    raw = path.read_text(encoding="utf-8")
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "=" not in s:
            continue
        key, _, rest = s.partition("=")
        key = key.strip()
        val = rest.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key:
            os.environ[key] = val
    return True
