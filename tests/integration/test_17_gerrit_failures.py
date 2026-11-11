"""Integration: a Gerrit that refuses the credentials, or is not there at all.

Against the real server a wrong ``gerrit.token`` is an HTTP 401, and a dead host a refused
connection. Either way every command says so in one line and never guesses (exit-codes.md,
``GERRIT``): ``ger log`` and ``ger show`` still show the local commit with its Gerrit state
unknown and exit 5, ``ger edit`` refuses to walk on unknown state, and the ``ger push``
preview warns once and still lists what it would push. ``tests/test_gerrit_unavailable.py``
pins the same with a fake seam; this proves the real HTTP failures map onto it.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterator
from pathlib import Path
from urllib.parse import urlparse

import pytest

from gerrit_workflow_tools.cli_edit import main as ger_edit_main
from gerrit_workflow_tools.cli_log import main as ger_log_main
from gerrit_workflow_tools.cli_push import main as ger_push_main
from gerrit_workflow_tools.cli_show import main as ger_show_main
from gerrit_workflow_tools.core.git_run import git
from tests.integration.conftest import GerritIntegrationContext
from tests.integration.integration_helpers import ger_push, prepare_topic_repo, run_ger
from tests.integration.repo_builder import build_linear_chain


UNREACHABLE_WEB_URL = "http://127.0.0.1:9"


@pytest.fixture(scope="module")
def pushed_repo(gerrit_integration_context: GerritIntegrationContext, tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("failures")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("XDG_CACHE_HOME", str(root / "xdg-cache"))
        repo = prepare_topic_repo(gerrit_integration_context, root, f"down_{secrets.token_hex(4)}")
        build_linear_chain(repo, ["pushed before Gerrit went away"])
        ger_push(repo, mp)
    return repo


@pytest.fixture(params=["wrong-token", "unreachable"])
def broken_gerrit(
    request: pytest.FixtureRequest, pushed_repo: Path, gerrit_integration_context: GerritIntegrationContext
) -> Iterator[str]:
    """Break *pushed_repo*'s Gerrit configuration one way; yield the message ger must print."""
    ctx = gerrit_integration_context
    if request.param == "wrong-token":
        key, value = "gerrit.token", "not-the-token"
        message = f"Gerrit at {urlparse(ctx.http_base).netloc} rejected the credentials (HTTP 401)"
    else:
        key, value = "gerrit.webUrl", UNREACHABLE_WEB_URL
        message = f"Gerrit unreachable at {urlparse(UNREACHABLE_WEB_URL).netloc} (connection refused)"
    original = {"gerrit.token": ctx.dev_password, "gerrit.webUrl": ctx.http_base}[key]
    git("config", key, value, cwd=pushed_repo)
    try:
        yield message
    finally:
        git("config", key, original, cwd=pushed_repo)


@pytest.mark.parametrize(
    ("main_fn", "argv"),
    [(ger_log_main, ["--color", "never"]), (ger_show_main, ["--color", "never", "HEAD"])],
    ids=["log", "show"],
)
def test_log_and_show_print_the_local_commit_and_one_gerrit_error(
    main_fn: Callable[..., int],
    argv: list[str],
    pushed_repo: Path,
    broken_gerrit: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code, out, err = run_ger(pushed_repo, main_fn, argv, monkeypatch)
    assert code == 5
    [line] = err.strip().splitlines()
    assert line.startswith(f"gerrit error: {broken_gerrit}")
    assert "pushed before Gerrit went away" in out and "Gerrit state unknown" in out


def test_edit_walk_refuses_to_guess_with_one_gerrit_error(
    pushed_repo: Path, broken_gerrit: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, _out, err = run_ger(pushed_repo, ger_edit_main, ["-n"], monkeypatch)
    assert code == 5
    [line] = err.strip().splitlines()
    assert line.startswith(f"gerrit error: {broken_gerrit}")


def test_push_preview_warns_once_and_still_lists_the_commits(
    pushed_repo: Path, broken_gerrit: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, out, err = run_ger(pushed_repo, ger_push_main, ["--dry-run", "--no-rebase-check"], monkeypatch)
    assert code == 0
    assert err.count(broken_gerrit) == 1
    assert "pushed before Gerrit went away" in out and "git push" in out
