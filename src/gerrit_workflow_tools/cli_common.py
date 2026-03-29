from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from gerrit_workflow_tools.git_run import GitError


def cwd_from_env() -> Path:
    return Path.cwd()


def print_json(obj: Any) -> None:
    print(json.dumps(obj, indent=2))


def handle_git_error(e: Exception) -> int:
    if isinstance(e, GitError):
        print(e.args[0], file=sys.stderr)
        return 1
    raise e
