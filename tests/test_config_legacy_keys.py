"""Keys that moved to ``ger.*`` (#113): the old name still answers, warns once, and ger writes only the new one."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import pytest

from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.config_form import resolve_config_form
from gerrit_workflow_tools.core.config_keys import LEGACY_KEYS, ConfigScope, legacy_key
from gerrit_workflow_tools.core.config_write import ConfigWrite, apply_config_write
from gerrit_workflow_tools.core.git_run import git
from gerrit_workflow_tools.core.upstream import append_recent_upstream_abbrev, read_recent_upstream_abbrevs


def test_an_old_key_answers_and_warns_once_with_the_command_that_moves_it(
    config_warnings: Callable[[], list[str]],
) -> None:
    settings = Settings.from_map({"gerrit.webUrl": "https://g.example"}, scopes={"gerrit.webUrl": ConfigScope.GLOBAL})
    assert settings.gerrit_web_url == "https://g.example"
    assert settings.scope("ger.webUrl") is ConfigScope.GLOBAL
    assert settings.get("ger.webUrl") == "https://g.example"
    assert config_warnings() == ["old git config key gerrit.webUrl: run ger config --migrate"]


def test_the_new_key_wins_and_silences_the_old_one(config_warnings: Callable[[], list[str]]) -> None:
    settings = Settings.from_map({"ger.remote": "gerrit", "gerrit.remote": "origin"})
    assert settings.gerrit_remote == "gerrit"
    assert config_warnings() == []


def test_every_renamed_family_falls_back(config_warnings: Callable[[], list[str]]) -> None:
    settings = Settings.from_map(
        {
            "gerrit.push.remotePolicy": "warn-not-rebased",
            "inbox.limit": "7",
            "branch.Feature.gerritTarget": "dev",
            "branch.Feature.gerritReviewers": "alice",
        },
        scopes={"inbox.limit": ConfigScope.COMMAND},
    )
    assert settings.push_remote_policy == "warn-not-rebased"
    assert settings.inbox_limit == 7
    assert settings.branch_gerrit_target("Feature") == "dev"
    assert settings.branch_gerrit_reviewers("Feature") == "alice"
    # One line for them all, once: the keys on disk move with one command; a `git -c` one is renamed.
    assert config_warnings() == [
        "old git config keys gerrit.push.remotePolicy, branch.Feature.gerritTarget, "
        "branch.Feature.gerritReviewers: run ger config --migrate; rename git -c inbox.limit -> ger.inboxLimit"
    ]


def test_legacy_names_are_the_documented_ones() -> None:
    assert legacy_key("ger.pushRemotePolicy") == "gerrit.push.remotePolicy"
    assert legacy_key("ger.inboxToReviewQuery") == "inbox.toReviewQuery"
    assert legacy_key("ger.weburl") == "gerrit.webUrl"  # as git spells it
    assert legacy_key("branch.a.b.gerTarget") == "branch.a.b.gerritTarget"
    assert legacy_key("ger.reviewCheckout") is None
    assert all(new.startswith("ger.") for new in LEGACY_KEYS)


def test_ger_config_offers_only_new_keys() -> None:
    sections = resolve_config_form(Settings.from_map({}), branch="main")
    keys = [s.spec.key for section in sections for s in section.settings]
    assert keys and all(k.startswith("ger.") or ".ger" in k for k in keys), keys


def test_writing_a_key_removes_its_old_name_at_that_scope(stack_repo: Path) -> None:
    git("config", "gerrit.wrapWidth", "80", cwd=stack_repo)
    apply_config_write(stack_repo, ConfigWrite("ger.wrapWidth", "set", "100", ConfigScope.LOCAL))
    assert Settings.from_cwd(stack_repo).values.get("gerrit.wrapwidth") is None
    assert Settings.from_cwd(stack_repo).wrap_width == 100

    git("config", "gerrit.stopPattern", "^x", cwd=stack_repo)
    apply_config_write(stack_repo, ConfigWrite("ger.stopPattern", "unset", scope=ConfigScope.LOCAL))
    assert Settings.from_cwd(stack_repo).get("ger.stopPattern") is None


def test_recent_upstreams_move_to_the_new_key_on_the_next_write(stack_repo: Path) -> None:
    git("config", "--global", "--add", "gerrit.recentUpstreamAbbrev", "origin/old", cwd=stack_repo)
    assert read_recent_upstream_abbrevs(stack_repo) == ["origin/old"]
    append_recent_upstream_abbrev(stack_repo, "origin/main")
    assert (
        git("config", "--global", "--get-all", "gerrit.recentUpstreamAbbrev", cwd=stack_repo, check=False).stdout == ""
    )
    assert set(read_recent_upstream_abbrevs(stack_repo)) == {"origin/main", "origin/old"}


def test_ger_config_migrate_moves_every_old_key_without_a_terminal(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch, config_warnings: Callable[[], list[str]]
) -> None:
    from gerrit_workflow_tools.cli_config import main as config_main
    from tests.conftest import run_cli

    git("config", "gerrit.webUrl", "https://g.example", cwd=stack_repo)
    git("config", "--global", "--add", "gerrit.recentUpstreamAbbrev", "origin/a", cwd=stack_repo)
    git("config", "--global", "--add", "gerrit.recentUpstreamAbbrev", "origin/b", cwd=stack_repo)
    git("config", "branch.feature.gerritTarget", "dev", cwd=stack_repo)
    git("config", "ger.project", "new", cwd=stack_repo)
    git("config", "gerrit.project", "old", cwd=stack_repo)
    git("config", "--global", "gerrit.user", "alice", cwd=stack_repo)
    code, out, err = run_cli(stack_repo, config_main, ["--migrate"], monkeypatch)
    assert code == 0, err
    *moved, total = out.splitlines()
    assert total == "moved 5 keys"
    assert set(moved) == {
        "gerrit.webUrl -> ger.webUrl (local)",
        "gerrit.recentUpstreamAbbrev -> ger.recentUpstreamAbbrev (global)",
        "branch.feature.gerritTarget -> branch.feature.gerTarget (local)",
        "gerrit.project removed; the new name was set already (local)",
        "gerrit.user -> ger.user (global)",
    }
    settings = Settings.from_cwd(stack_repo)
    assert (settings.gerrit_web_url, settings.gerrit_project, settings.get("ger.user")) == (
        "https://g.example",
        "new",
        "alice",
    )
    assert settings.branch_gerrit_target("feature") == "dev"
    assert config_warnings() == []
    assert read_recent_upstream_abbrevs(stack_repo) == ["origin/a", "origin/b"]
    code, out, _err = run_cli(stack_repo, config_main, ["--migrate"], monkeypatch)
    assert (code, out.strip()) == (0, "nothing to migrate: no old key names")


def test_the_notice_prints_as_one_plain_line_and_diagnostics_keep_their_prefix() -> None:
    from gerrit_workflow_tools.cli_logging import configure_logging

    configure_logging(False)
    [handler] = logging.getLogger("gerrit_workflow_tools").handlers
    notice = logging.LogRecord("gerrit_workflow_tools.core.config", logging.WARNING, "", 0, "old key", None, None)
    notice.notice = True
    plain = logging.LogRecord("gerrit_workflow_tools.core.git_run", logging.WARNING, "", 0, "odd", None, None)
    assert handler.format(notice) == "warning: old key"
    assert handler.format(plain) == "WARNING: gerrit_workflow_tools.core.git_run: odd"


def test_ger_serve_says_which_old_keys_stand_in_and_serves_the_command_that_moves_them(
    stack_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What every command logs once, the editor shows once: the same line, and ``ger config --migrate`` to run."""
    from gerrit_workflow_tools.cli_config import main as config_main
    from gerrit_workflow_tools.server.api import GerApi
    from tests.change_store import ChangeStore
    from tests.conftest import run_cli

    store = ChangeStore({}, web_base="https://g.example")
    git("config", "ger.webUrl", "https://g.example", cwd=stack_repo)
    assert GerApi(stack_repo, gerrit=store).handle("GET", "/api/v1/health")["warnings"] == []
    git("config", "--unset", "ger.project", cwd=stack_repo)
    git("config", "gerrit.project", "testproj", cwd=stack_repo)
    [warning] = GerApi(stack_repo, gerrit=store).handle("GET", "/api/v1/health")["warnings"]
    message = "old git config key gerrit.project: run ger config --migrate"
    assert warning == {"message": message, "argv": ["config", "--migrate"]}
    command, *args = warning["argv"]
    assert command == "config"
    code, _out, err = run_cli(stack_repo, config_main, args, monkeypatch)
    assert code == 0, err
    assert GerApi(stack_repo, gerrit=store).handle("GET", "/api/v1/health")["warnings"] == []
