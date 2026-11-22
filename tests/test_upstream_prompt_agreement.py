# Spec: docu/spec/exit-codes.md, docu/SPEC.md (missing upstream)
# Covers: every command that needs an upstream asks for it on a terminal, the same way

"""A missing upstream is asked for by every command that needs one, wherever the need arises.

``cli_common.run_cli_command`` asks and runs the command again; these pin that commands agree on it:
they ask on a terminal and go on, take a decline as ``REFUSED``, and never ask for ``--json`` output.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from gerrit_workflow_tools.cli_ci import main as ci_main
from gerrit_workflow_tools.cli_comment import main as comment_main
from gerrit_workflow_tools.cli_common import ExitCode
from gerrit_workflow_tools.cli_log import main as log_main
from gerrit_workflow_tools.cli_reply import main as reply_main
from gerrit_workflow_tools.cli_show import main as show_main
from gerrit_workflow_tools.core.git_run import git
from tests.change_store import ChangeStore
from tests.conftest import run_cli


class _StdinTTY:
    def isatty(self) -> bool:
        return True


_NEEDS_UPSTREAM: list[tuple[str, Callable[..., int], list[str]]] = [
    ("show", show_main, []),
    ("show --stack", show_main, ["--stack"]),
    ("ci", ci_main, []),
    ("comment", comment_main, ["-m", "note", "HEAD"]),
    ("log", log_main, []),
    ("reply --publish", reply_main, ["--publish", "--dry-run"]),
]


def _without_upstream(repo: Path, monkeypatch: pytest.MonkeyPatch, answer: str | None) -> list[str]:
    """Unset ``feature``'s upstream, fake a terminal, and answer the upstream prompt with *answer*."""
    git("branch", "--unset-upstream", "feature", cwd=repo)
    monkeypatch.setattr(sys, "stdin", _StdinTTY())
    monkeypatch.setattr("gerrit_workflow_tools.core.upstream_interactive.prompt_toolkit_can_run", lambda: True)
    asked: list[str] = []

    def prompt(_cwd: object, branch: str, **_kw: object) -> str | None:
        asked.append(branch)
        return answer

    monkeypatch.setattr("gerrit_workflow_tools.core.upstream_interactive.prompt_upstream_abbrev_interactive", prompt)
    return asked


def _upstream(repo: Path) -> str | None:
    p = git("rev-parse", "--abbrev-ref", "feature@{upstream}", cwd=repo, check=False)
    return p.stdout.strip() if p.returncode == 0 else None


@pytest.mark.parametrize(("name", "main", "argv"), _NEEDS_UPSTREAM, ids=[c[0] for c in _NEEDS_UPSTREAM])
def test_asks_for_missing_upstream_once_and_goes_on(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch, name: str, main: Callable[..., int], argv: list[str]
) -> None:
    asked = _without_upstream(stack_repo, monkeypatch, "origin/main")
    _code, _out, err = run_cli(stack_repo, main, argv, monkeypatch, gerrit=ChangeStore())
    assert asked == ["feature"], f"{name}: {err}"
    assert "Upstream for 'feature' set to origin/main." in err
    assert "No upstream configured" not in err and "No Gerrit destination branch" not in err
    assert _upstream(stack_repo) == "origin/main"


@pytest.mark.parametrize(("name", "main", "argv"), _NEEDS_UPSTREAM, ids=[c[0] for c in _NEEDS_UPSTREAM])
def test_declining_the_upstream_prompt_is_refused(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch, name: str, main: Callable[..., int], argv: list[str]
) -> None:
    asked = _without_upstream(stack_repo, monkeypatch, None)
    code, _out, err = run_cli(stack_repo, main, argv, monkeypatch, gerrit=ChangeStore())
    assert asked == ["feature"], f"{name}: {err}"
    assert code == ExitCode.REFUSED, f"{name}: {err}"
    assert _upstream(stack_repo) is None


@pytest.mark.parametrize(
    ("main", "argv"),
    [(show_main, ["--json"]), (show_main, ["--ai"]), (ci_main, ["--json"]), (log_main, ["--json"])],
)
def test_machine_output_reports_missing_upstream_without_asking(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch, main: Callable[..., int], argv: list[str]
) -> None:
    asked = _without_upstream(stack_repo, monkeypatch, "origin/main")
    code, _out, err = run_cli(stack_repo, main, argv, monkeypatch, gerrit=ChangeStore())
    assert asked == []
    assert code in (ExitCode.CONFIG, ExitCode.NOT_FOUND), err
    assert "upstream" in err
    assert _upstream(stack_repo) is None


def test_no_terminal_reports_missing_upstream(stack_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git("branch", "--unset-upstream", "feature", cwd=stack_repo)
    code, _out, err = run_cli(stack_repo, show_main, [], monkeypatch, gerrit=ChangeStore())
    assert code == ExitCode.NOT_FOUND
    assert "No Gerrit destination branch for branch 'feature'" in err
