"""``ger setup`` — configure the Gerrit server and credentials, check them, and say what comes next."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from gerrit_workflow_tools.cli_common import ExitCode, init_cli_runtime, run_cli_command
from gerrit_workflow_tools.cli_terminal import confirm, prompt_toolkit_can_run
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.config_keys import ConfigScope, gerrit_host_key, legacy_key
from gerrit_workflow_tools.core.config_write import ConfigWrite, apply_config_write, scope_unscoped_secrets
from gerrit_workflow_tools.core.gerrit.rest import GerritApiError, GerritRest
from gerrit_workflow_tools.core.gerrit_setup import (
    INSECURE_HTTP,
    needs_insecure_opt_in,
    normalize_web_url,
    run_with,
    signed_in,
    web_url_problem,
)
from gerrit_workflow_tools.core.git_run import git
from gerrit_workflow_tools.core.run_context import RunContext
from gerrit_workflow_tools.core.usage import Refusal, UsageError
from gerrit_workflow_tools.render.wrap import say


_WHAT = {
    "webUrl": "The web URL of your Gerrit server (e.g. https://gerrit.your.domain).",
    "user": "Your Gerrit username.",
    "token": "An HTTP password for the Gerrit REST API (Gerrit's web UI: Settings, HTTP Credentials).",
}
_INSECURE = "ger.allowInsecureHttp"


@dataclass
class _Values:
    """What setup is about to save: the URL, that host's user and token (#111), and the http opt-in when given."""

    url: str = ""
    user: str = ""
    token: str = ""
    insecure: bool = False

    def as_config(self) -> dict[str, str]:
        values = {"ger.webUrl": self.url, self.key("user"): self.user, self.key("token"): self.token}
        return {**values, _INSECURE: "true"} if self.insecure else values

    def key(self, name: str) -> str:
        """``ger.<webUrl>.<name>``: where this host's *name* (``user``, ``token``) is saved."""
        return gerrit_host_key(self.url, name)


class _Aborted(Refusal):
    """An empty answer, end of input or Ctrl-C: nothing was saved."""


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


def _saved(url: str, *, global_config: bool, cwd: Path | None) -> tuple[str, str]:
    """The user and token saved for *url* at the setup scope: the host's, else the unscoped ones."""

    def get(name: str) -> str:
        keys = (gerrit_host_key(url, name), f"ger.{name}") if url else (f"ger.{name}",)
        return next((v for key in keys if (v := _git_config_get(global_config=global_config, key=key, cwd=cwd))), "")

    return get("user"), get("token")


def _ask(session: Any, label: str, *, default: str = "", secret: bool = False, keep: bool = False) -> str:
    """One answer from the prompt session; empty (unless *keep*: then the existing value stays) aborts."""
    try:
        value = session.prompt(f"{label}: ", default=default, is_password=secret).strip()
    except (EOFError, KeyboardInterrupt):
        value = ""
    if not value and not keep:
        raise _Aborted("setup aborted: nothing was saved")
    return value


def _ask_url(session: Any, settings: Settings, start: _Values) -> tuple[str, bool]:
    """The web URL, asked until it will do; with whether plain http was allowed for it here."""
    print(_WHAT["webUrl"], file=sys.stderr)
    default = start.url
    while True:
        url = normalize_web_url(_ask(session, "webUrl", default=default))
        if problem := web_url_problem(url):
            print(f"{problem}.", file=sys.stderr)
            default = f"https://{url}" if "://" not in url else url
            continue
        if not needs_insecure_opt_in(url, settings) or (start.insecure and url == start.url):
            return url, start.insecure and url == start.url
        print(f"{INSECURE_HTTP}.", file=sys.stderr)
        if confirm(f"Allow plain http for {url} ({_INSECURE} true)?", default=False):
            return url, True
        print("Not over plain http, then: give the server's https:// URL.", file=sys.stderr)
        default = "https://" + url.removeprefix("http://")


def _ask_values(
    settings: Settings, saved: Callable[[str], tuple[str, str]], start: _Values, *, url: bool = True
) -> _Values:
    """The values from the prompts, *start* (what was typed before), else what is *saved* for the URL, as the defaults.

    Not the URL when *url* is false: Gerrit answered there, only the credentials were refused.
    """
    from prompt_toolkit import PromptSession

    session: PromptSession[str] = PromptSession()
    values = _Values(url=start.url, insecure=start.insecure)
    if url:
        values.url, values.insecure = _ask_url(session, settings, start)
    saved_user, saved_token = saved(values.url)
    print(_WHAT["user"], file=sys.stderr)
    values.user = _ask(session, "user", default=start.user or saved_user)
    kept = start.token or saved_token
    print("Leave it empty to keep the token you have." if kept else _WHAT["token"], file=sys.stderr)
    values.token = _ask(session, "token", secret=True, keep=bool(kept)) or kept
    return values


def _flag_values(args: argparse.Namespace, saved_url: str, saved: Callable[[str], tuple[str, str]]) -> _Values:
    """The values from the flags, else as saved; refused, before anything is saved, when one is missing or wrong."""
    token = Path(args.token_file).read_text(encoding="utf-8") if args.token_file not in (None, "-") else None
    if args.token_file == "-":
        token = sys.stdin.read()
    url = normalize_web_url(args.url or saved_url)
    saved_user, saved_token = saved(url)
    values = _Values(
        url=url,
        user=(args.user or saved_user).strip(),
        token=(token or saved_token).strip(),
        insecure=args.allow_insecure_http,
    )
    missing = [
        flag
        for flag, value in (("--url", values.url), ("--user", values.user), ("--token-file", values.token))
        if not value
    ]
    if missing:
        raise UsageError(
            f"no terminal to ask in: give {', '.join(missing)} "
            "(e.g. ger setup --url https://gerrit.example.com --user YOU --token-file - <token.txt)"
        )
    if problem := web_url_problem(values.url):
        raise UsageError(problem)
    return values


def _save(values: _Values, *, global_config: bool, cwd: Path | None) -> None:
    """Write the values at the setup scope, say where, and move the unscoped secrets to the host."""
    scope = ConfigScope.GLOBAL if global_config else ConfigScope.LOCAL
    for key, value in values.as_config().items():
        apply_config_write(cwd, ConfigWrite(key, "set", value, scope))
    keys = ", ".join(key for key in values.as_config() if key != values.key("token"))
    where = "global" if global_config else "repository"
    print(f"saved to your {where} git config: {keys} and {values.key('token')}", file=sys.stderr)
    _scope_unscoped_secrets(values.url, scope=scope, cwd=cwd)


def _scope_unscoped_secrets(web_url: str, *, scope: ConfigScope, cwd: Path | None) -> None:
    """Remove ``ger.token`` / ``ger.password`` (and old names) at *scope*: they go to any host.

    The token was just written for the host; a password moves to ``ger.<webUrl>.password``
    unless the host has one, so no secret is lost.
    """
    for key in scope_unscoped_secrets(cwd, web_url, (scope,)):
        name = key.new.rsplit(".", 1)[1]
        print(f"Moved the unscoped ger.{name} to {key.new}: it went to any host ger.webUrl named.", file=sys.stderr)


def _run_setup(args: argparse.Namespace, run: RunContext, gerrit: GerritRest | None) -> int:
    global_config = not args.local
    cwd = run.cwd
    saved_url = _git_config_get(global_config=global_config, key="ger.webUrl", cwd=cwd) or ""
    saved = partial(_saved, global_config=global_config, cwd=cwd)
    flags_given = bool(args.url and args.user and args.token_file)
    interactive = not flags_given and prompt_toolkit_can_run()
    if interactive:
        where = "your global" if global_config else "this repository's"
        print(f"Configure ger for your Gerrit ({where} git config).", file=sys.stderr)
        print("An empty answer stops; nothing is saved until Gerrit accepts the credentials.\n", file=sys.stderr)
        values = _Values(saved_url, insecure=args.allow_insecure_http)
    else:
        values = _flag_values(args, saved_url, saved)
        if needs_insecure_opt_in(values.url, run.settings) and not values.insecure:
            raise UsageError(f"{INSECURE_HTTP}; to use it anyway, add --allow-insecure-http ({_INSECURE} true)")
    # Gerrit refused the credentials at the URL in use (saved here, or the repository's): those alone are asked.
    if args.credentials and not values.url:
        values.url = run.settings.gerrit_web_url or ""
    ask_url = not (args.credentials and values.url)
    while True:
        if interactive:
            values = _ask_values(run.settings, saved, values, url=ask_url)
        checked = run_with(cwd, run.settings, values.as_config(), gerrit=gerrit)
        if args.no_check:
            break
        try:
            account = signed_in(checked)
        except GerritApiError as error:
            # Gerrit answered: only the credentials need asking again. Otherwise the URL may be wrong too.
            refused = error.status in (401, 403)
            host = urlparse(values.url).netloc
            why = f"Gerrit at {host} did not accept user {values.user} with that token" if refused else str(error)
            print(f"{why}; nothing was saved", file=sys.stderr)
            if not interactive:
                print("check the values, or `ger setup --no-check` saves them without asking Gerrit", file=sys.stderr)
                return int(ExitCode.GERRIT)
            what = "the user and token" if refused else "the values"
            print(f"Type {what} again (Enter keeps what is shown; the token is asked anew):\n", file=sys.stderr)
            ask_url, values.token = not refused, ""
            continue
        who = account.name or account.username or values.user
        email = f" <{account.email}>" if account.email else ""
        print(f"Signed in as {who}{email} on {values.url}", file=sys.stderr)
        break
    _save(values, global_config=global_config, cwd=cwd)
    say("next: `ger log` shows your stack and its reviews; `ger inbox` the changes waiting for you")
    return int(ExitCode.OK)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ger setup",
        description=(
            "Configure ger.webUrl and that host's user and token (ger.<webUrl>.user, ger.<webUrl>.token) in git "
            "config (what every Gerrit command needs), and check them with Gerrit before saving. Asks in a "
            "terminal; without one, takes the flags."
        ),
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Write to this repository's git config instead of --global (default).",
    )
    parser.add_argument(
        "--url", metavar="URL", help="The Gerrit web URL (no prompts once --user and --token-file are given too)."
    )
    parser.add_argument(
        "--credentials",
        action="store_true",
        help="Ask only for the user and token: the saved web URL stays (Gerrit refused the credentials there).",
    )
    parser.add_argument("--user", metavar="USER", help="Your Gerrit username.")
    parser.add_argument("--token-file", metavar="FILE", help="Read the HTTP password from FILE ('-': stdin).")
    parser.add_argument(
        "--allow-insecure-http",
        action="store_true",
        help=f"Accept an http:// URL ({_INSECURE} true): only for a test Gerrit.",
    )
    parser.add_argument(
        "--no-check", action="store_true", help="Save without asking Gerrit whether the credentials work."
    )
    return parser


def main(argv: list[str] | None = None, *, gerrit: GerritRest | None = None) -> int:
    """CLI entry for ``ger setup``: ask for (or take) the Gerrit web URL, user and token, check them, save them."""
    return run_cli_command(lambda: _run(argv, gerrit=gerrit))


def _run(argv: list[str] | None, *, gerrit: GerritRest | None) -> int:
    args = _build_parser().parse_args(argv)
    run = init_cli_runtime(args, repository=False)
    return _run_setup(args, run, gerrit)
