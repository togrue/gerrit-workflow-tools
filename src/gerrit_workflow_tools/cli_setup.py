"""``ger setup`` — interactively configure Gerrit REST credentials in git config."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import urlparse

from gerrit_workflow_tools.cli_common import init_cli_runtime, run_cli_command
from gerrit_workflow_tools.core.config_keys import ConfigScope, legacy_key
from gerrit_workflow_tools.core.config_write import ConfigWrite, apply_config_write
from gerrit_workflow_tools.core.git_run import git
from gerrit_workflow_tools.core.upstream_interactive import prompt_toolkit_can_run
from gerrit_workflow_tools.core.usage import UsageError


_PROMPTS: tuple[tuple[str, str, str], ...] = (
    (
        "ger.webUrl",
        "webUrl",
        "The web URL used to access your Gerrit instance (e.g. https://gerrit.your.domain/).",
    ),
    (
        "ger.user",
        "user",
        "Your Gerrit username.",
    ),
    (
        "ger.token",
        "token",
        "An HTTP token for the Gerrit REST API (see your user settings in the Gerrit web UI).",
    ),
)


def _git_config_get(*, global_config: bool, key: str, cwd: Path | None) -> str | None:
    """*key* at the setup scope, else its pre-``ger.*`` name, so a re-run offers the old value."""
    for name in (key, legacy_key(key)):
        if name is None:
            continue
        args: list[str] = ["config"]
        if global_config:
            args.append("--global")
        args.extend(["--get", name])
        p = git(*args, cwd=cwd, check=False)
        value = (p.stdout or "").strip() if p.returncode == 0 else ""
        if value:
            return value
    return None


def _git_config_set(*, global_config: bool, key: str, value: str, cwd: Path | None) -> None:
    scope = ConfigScope.GLOBAL if global_config else ConfigScope.LOCAL
    apply_config_write(cwd, ConfigWrite(key, "set", value, scope))


def _normalize_web_url(raw: str) -> str:
    return raw.strip().rstrip("/")


def _validate_web_url(raw: str) -> str | None:
    value = _normalize_web_url(raw)
    if not value:
        return "webUrl is required."
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https"):
        return "webUrl must start with http:// or https://."
    if not parsed.netloc:
        return "webUrl must include a host name."
    if parsed.path not in ("", "/"):
        return "webUrl should be the instance base URL only (no path after the host)."
    return None


def _prompt_value(
    session,
    *,
    label: str,
    description: str,
    default: str = "",
    is_password: bool = False,
    allow_empty: bool = False,
) -> str | None:
    print(description, file=sys.stderr)
    try:
        raw = session.prompt(
            f"{label}: ",
            default=default,
            is_password=is_password,
        )
    except (EOFError, KeyboardInterrupt):
        print("Aborted.", file=sys.stderr)
        return None
    value = raw.strip()
    if not value and not allow_empty:
        print(f"error: {label} is required.", file=sys.stderr)
        return None
    return value


def _run_interactive_setup(*, global_config: bool, cwd: Path | None) -> int:
    if not prompt_toolkit_can_run():
        print(
            "Set git config manually, e.g.:\n"
            "  git config --global ger.webUrl https://gerrit.example.com\n"
            "  git config --global ger.user YOUR_USER\n"
            "  git config --global ger.token YOUR_TOKEN",
            file=sys.stderr,
        )
        raise UsageError("ger setup requires an interactive terminal")

    scope = "global" if global_config else "local"
    print(f"Configure Gerrit credentials ({scope} git config).", file=sys.stderr)
    print("Press Enter on an empty line to abort.\n", file=sys.stderr)

    from prompt_toolkit import PromptSession

    session: PromptSession[str] = PromptSession()
    existing_web = _git_config_get(global_config=global_config, key="ger.webUrl", cwd=cwd)
    existing_user = _git_config_get(global_config=global_config, key="ger.user", cwd=cwd)
    existing_token = _git_config_get(global_config=global_config, key="ger.token", cwd=cwd)

    web_url = _prompt_value(
        session,
        label="webUrl",
        description=_PROMPTS[0][2],
        default=existing_web or "",
    )
    if web_url is None:
        return 1
    web_err = _validate_web_url(web_url)
    if web_err:
        print(f"error: {web_err}", file=sys.stderr)
        return 1
    web_url = _normalize_web_url(web_url)

    user = _prompt_value(
        session,
        label="user",
        description=_PROMPTS[1][2],
        default=existing_user or "",
    )
    if user is None:
        return 1

    token_hint = "Leave empty to keep the existing token." if existing_token else _PROMPTS[2][2]
    token = _prompt_value(
        session,
        label="token",
        description=token_hint,
        default="",
        is_password=True,
        allow_empty=bool(existing_token),
    )
    if token is None:
        return 1
    if not token:
        token = existing_token or ""
    if not token:
        print("error: token is required.", file=sys.stderr)
        return 1

    _git_config_set(global_config=global_config, key="ger.webUrl", value=web_url, cwd=cwd)
    _git_config_set(global_config=global_config, key="ger.user", value=user, cwd=cwd)
    _git_config_set(global_config=global_config, key="ger.token", value=token, cwd=cwd)

    print(
        f"\nConfigured ({scope}): ger.webUrl={web_url!r}, ger.user={user!r}, ger.token=<set>",
        file=sys.stderr,
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ger setup",
        description=(
            "Interactively configure ger.webUrl, ger.user, and ger.token "
            "in git config (required for Gerrit REST API commands)."
        ),
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Write to this repository's git config instead of --global (default).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry for ``ger setup``: prompt for Gerrit web URL, user, and HTTP token."""
    return run_cli_command(lambda: _run(argv))


def _run(argv: list[str] | None) -> int:
    args = _build_parser().parse_args(argv)
    run, _summary_highlighter = init_cli_runtime(args, repository=False)
    cwd = run.cwd
    return _run_interactive_setup(global_config=not args.local, cwd=cwd)
