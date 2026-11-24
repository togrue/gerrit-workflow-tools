"""Keys that moved to ``ger.*`` (#113): the old name still answers, warns once, and ger writes only the new one."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

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
    assert config_warnings() == [
        "git config key gerrit.webUrl is deprecated; move it with: "
        'git config --global ger.webUrl "$(git config --global gerrit.webUrl)" '
        "&& git config --global --unset gerrit.webUrl"
    ]


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
    assert len(config_warnings()) == 4
    assert "git config key inbox.limit is deprecated; use ger.inboxLimit" in config_warnings()


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
