"""The Gerrit connection a first run sets up: the web URL's form, who the credentials sign in as, the hook.

``ger setup`` checks what it is given here before it saves anything, so a setup that cannot work
says so at once instead of at the next command. The check is one ``GET /accounts/self`` through
the service of a run opened on the values as they would be saved. Asking for them is the command's.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.gerrit.rest import GerritRest
from gerrit_workflow_tools.core.git_run import git
from gerrit_workflow_tools.core.run_context import RunContext


INSECURE_HTTP = (
    "an http:// URL sends your user and token unencrypted with every request: "
    "fine for a test Gerrit on your own machine or network, not for a real one"
)
"""Why a plain-http ``ger.webUrl`` needs ``ger.allowInsecureHttp`` before ger talks to it."""


def normalize_web_url(raw: str) -> str:
    """*raw* as ``ger.webUrl`` keeps it: no surrounding blanks, no trailing slash."""
    return raw.strip().rstrip("/")


def web_url_problem(raw: str) -> str | None:
    """What is wrong with *raw* as a Gerrit web URL, or ``None`` when it will do."""
    value = normalize_web_url(raw)
    if not value:
        return "the web URL is required"
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https"):
        return f"the web URL must start with https:// (or http:// for a test Gerrit): try https://{value}"
    if not parsed.netloc:
        return "the web URL must name a host, as in https://gerrit.example.com"
    if parsed.path not in ("", "/"):
        return "the web URL is the server's base URL only, with no path after the host"
    return None


def needs_insecure_opt_in(url: str, settings: Settings) -> bool:
    """Whether *url* is plain http and *settings* do not allow it yet (:data:`INSECURE_HTTP`)."""
    return urlparse(url).scheme == "http" and not settings.gerrit_allow_insecure_http


@dataclass(frozen=True)
class SignedIn:
    """The account Gerrit says the credentials belong to."""

    account_id: int | None
    username: str | None
    name: str | None
    email: str | None

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> SignedIn:
        """From an AccountInfo payload; a field Gerrit left out is ``None``."""

        def text(key: str) -> str | None:
            value = payload.get(key)
            return (value.strip() or None) if isinstance(value, str) else None

        raw_id = payload.get("_account_id")
        return cls(raw_id if isinstance(raw_id, int) else None, text("username"), text("name"), text("email"))


def run_with(cwd: Path, settings: Settings, values: Mapping[str, str], *, gerrit: GerritRest | None) -> RunContext:
    """A run on *settings* with *values* (``ger.webUrl`` …) as they would be once saved, nothing written yet."""
    return RunContext.open(cwd, settings=Settings.from_map({**settings.values, **values}), gerrit=gerrit)


def signed_in(run: RunContext) -> SignedIn:
    """Who *run*'s credentials sign in as: one request, never the cache (it checks them). Raises ``GerritApiError``."""
    return SignedIn.from_payload(run.gerrit.rest.get_account("self"))


def commit_msg_hook_path(cwd: Path) -> Path | None:
    """Where git runs this repository's ``commit-msg`` hook from (``core.hooksPath`` honoured); ``None`` outside one."""
    found = git("rev-parse", "--path-format=absolute", "--git-path", "hooks/commit-msg", cwd=cwd, check=False)
    return Path(found.stdout.strip()) if found.returncode == 0 and found.stdout.strip() else None


def install_commit_msg_hook(run: RunContext, path: Path) -> None:
    """Write Gerrit's ``commit-msg`` hook to *path*, executable. Raises ``GerritApiError`` or ``OSError``."""
    script = run.gerrit.rest.get_commit_msg_hook()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script, encoding="utf-8", newline="\n")
    mode = path.stat().st_mode
    os.chmod(path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
