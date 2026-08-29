"""Tests for two-tier extension registry loading (local + global config dir)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from gerrit_workflow_tools.core.ci_strategy import clear_ci_strategy_cache
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.ger_registry import (
    RegistryLoadError,
    clear_extension_registry_cache,
    resolve_registry_callable,
    resolve_tier_callables,
)
from gerrit_workflow_tools.core.git_run import git
from gerrit_workflow_tools.core.ready_calc import compute_ready
from gerrit_workflow_tools.core.ready_strategy import clear_ready_strategy_cache
from gerrit_workflow_tools.core.stack import commits_in_range, merge_base_with_target
from gerrit_workflow_tools.summary_highlight import build_summary_highlighter


@pytest.fixture(autouse=True)
def _clear_registries() -> Iterator[None]:
    clear_extension_registry_cache()
    yield
    clear_extension_registry_cache()


def _write_domain_registry(root: Path, domain: str, body: str) -> None:
    domain_dir = root / domain
    domain_dir.mkdir(parents=True, exist_ok=True)
    (domain_dir / "registry.py").write_text(body, encoding="utf-8")


def _config_root(tmp_path: Path, web: str) -> Path:
    host = web.replace("https://", "").replace("http://", "").split("/")[0].split(":")[0]
    return tmp_path / "config" / "ger" / host


def test_scripts_dir_default() -> None:
    assert Settings.from_map({}).scripts_dir == ".ger"
    assert Settings.from_map({"gerrit.scriptsDir": "ext"}).scripts_dir == "ext"


def test_local_ci_registry_still_loads(stack_repo: Path) -> None:
    _write_domain_registry(
        stack_repo / ".ger",
        "ci",
        """
from gerrit_workflow_tools.core.ci_links import CiLink
def _extract(*, project, checks, messages):
    return [CiLink(label="j", url="https://ci/console", source="checks")]
STRATEGIES = {"testproj": _extract}
""",
    )
    clear_ci_strategy_cache()
    strat = resolve_registry_callable(
        stack_repo,
        "testproj",
        domain="ci",
        package_name="ger_ci",
        settings=Settings.from_cwd(stack_repo),
    )
    assert strat is not None
    assert strat(project="testproj", checks=[], messages=[])[0].url == "https://ci/console"


def test_global_registry_used_when_no_local(stack_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_home = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    web = "https://g.example"
    git("config", "gerrit.webUrl", web, cwd=stack_repo)
    _write_domain_registry(
        _config_root(tmp_path, web),
        "ci",
        """
from gerrit_workflow_tools.core.ci_links import CiLink
def _extract(*, project, checks, messages):
    return [CiLink(label="g", url="https://global/console", source="checks")]
STRATEGIES = {"testproj": _extract}
""",
    )
    clear_ci_strategy_cache()
    settings = Settings.from_cwd(stack_repo)
    strat = resolve_registry_callable(
        stack_repo,
        "testproj",
        domain="ci",
        package_name="ger_ci",
        settings=settings,
        web_base=web,
    )
    assert strat is not None
    assert strat(project="testproj", checks=[], messages=[])[0].url == "https://global/console"


def test_local_overrides_global(stack_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_home = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    web = "https://g.example"
    git("config", "gerrit.webUrl", web, cwd=stack_repo)
    _write_domain_registry(
        _config_root(tmp_path, web),
        "ci",
        """
from gerrit_workflow_tools.core.ci_links import CiLink
def _extract(*, project, checks, messages):
    return [CiLink(label="g", url="https://global/console", source="checks")]
STRATEGIES = {"testproj": _extract}
""",
    )
    _write_domain_registry(
        stack_repo / ".ger",
        "ci",
        """
from gerrit_workflow_tools.core.ci_links import CiLink
def _extract(*, project, checks, messages):
    return [CiLink(label="l", url="https://local/console", source="checks")]
STRATEGIES = {"testproj": _extract}
""",
    )
    clear_ci_strategy_cache()
    settings = Settings.from_cwd(stack_repo)
    strat = resolve_registry_callable(
        stack_repo,
        "testproj",
        domain="ci",
        package_name="ger_ci",
        settings=settings,
        web_base=web,
    )
    assert strat is not None
    assert strat(project="testproj", checks=[], messages=[])[0].url == "https://local/console"


def test_local_without_project_key_falls_through_to_global(
    stack_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_home = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    web = "https://g.example"
    git("config", "gerrit.webUrl", web, cwd=stack_repo)
    _write_domain_registry(
        stack_repo / ".ger",
        "ci",
        "STRATEGIES = {'other': lambda **kw: []}",
    )
    _write_domain_registry(
        _config_root(tmp_path, web),
        "ci",
        """
from gerrit_workflow_tools.core.ci_links import CiLink
def _extract(*, project, checks, messages):
    return [CiLink(label="g", url="https://global/console", source="checks")]
STRATEGIES = {"testproj": _extract}
""",
    )
    clear_ci_strategy_cache()
    local, global_ = resolve_tier_callables(
        stack_repo,
        "testproj",
        domain="ci",
        package_name="ger_ci",
        settings=Settings.from_cwd(stack_repo),
        web_base=web,
    )
    assert local is None
    assert global_ is not None
    assert (
        resolve_registry_callable(
            stack_repo,
            "testproj",
            domain="ci",
            package_name="ger_ci",
            settings=Settings.from_cwd(stack_repo),
            web_base=web,
        )
        is global_
    )


def test_broken_local_registry_fails(stack_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_home = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    web = "https://g.example"
    git("config", "gerrit.webUrl", web, cwd=stack_repo)
    _write_domain_registry(stack_repo / ".ger", "ci", "this is not valid python {{{")
    _write_domain_registry(
        _config_root(tmp_path, web),
        "ci",
        """
from gerrit_workflow_tools.core.ci_links import CiLink
def _extract(*, project, checks, messages):
    return [CiLink(label="g", url="https://global/ok", source="checks")]
STRATEGIES = {"testproj": _extract}
""",
    )
    clear_ci_strategy_cache()
    with pytest.raises(RegistryLoadError):
        resolve_registry_callable(
            stack_repo,
            "testproj",
            domain="ci",
            package_name="ger_ci",
            settings=Settings.from_cwd(stack_repo),
            web_base=web,
        )


def test_runtime_local_failure_falls_back_to_global(
    stack_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_home = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    web = "https://g.example"
    git("config", "gerrit.webUrl", web, cwd=stack_repo)
    _write_domain_registry(
        stack_repo / ".ger",
        "ready",
        """
from gerrit_workflow_tools.core.ready_strategy import BoundaryResult
def _boom(*, commits, stop_pattern, overlay=None):
    raise RuntimeError("local broke")
STRATEGIES = {"testproj": _boom}
""",
    )
    _write_domain_registry(
        _config_root(tmp_path, web),
        "ready",
        """
from gerrit_workflow_tools.core.ready_strategy import BoundaryResult
def _global(*, commits, stop_pattern, overlay=None):
    return BoundaryResult(block_index=0, reason="from global")
STRATEGIES = {"testproj": _global}
""",
    )
    clear_ready_strategy_cache()
    git("config", "gerrit.project", "testproj", cwd=stack_repo)
    result = compute_ready(
        stack_repo,
        stop_pattern=r"^never$",
        project="testproj",
        settings=Settings.from_cwd(stack_repo),
        web_base=web,
    )
    assert result.boundary_reason == "from global"


def test_ready_boundary_strategy_blocks_first_commit(stack_repo: Path) -> None:
    _write_domain_registry(
        stack_repo / ".ger",
        "ready",
        """
from gerrit_workflow_tools.core.ready_strategy import BoundaryResult
def _boundary(*, commits, stop_pattern, overlay=None):
    return BoundaryResult(block_index=0, reason="scripted hold")
STRATEGIES = {"testproj": _boundary}
""",
    )
    clear_ready_strategy_cache()
    git("config", "gerrit.project", "testproj", cwd=stack_repo)
    _fork, _display, target_tip = merge_base_with_target(stack_repo)
    rows = commits_in_range(stack_repo, f"{target_tip}..HEAD", first_parent=True)
    result = compute_ready(
        stack_repo,
        stop_pattern=r"^never-match$",
        project="testproj",
        settings=Settings.from_cwd(stack_repo),
    )
    assert result.pushable_count == 0
    assert result.boundary_sha == rows[0].sha
    assert result.boundary_reason == "scripted hold"


def test_highlighter_uses_ready_boundary_not_stop_regex(stack_repo: Path) -> None:
    _write_domain_registry(
        stack_repo / ".ger",
        "ready",
        """
from gerrit_workflow_tools.core.ready_strategy import BoundaryResult
def _boundary(*, commits, stop_pattern, overlay=None):
    return BoundaryResult(block_index=0, reason="scripted hold")
STRATEGIES = {"testproj": _boundary}
""",
    )
    clear_ready_strategy_cache()
    git("config", "gerrit.project", "testproj", cwd=stack_repo)
    _fork, _display, target_tip = merge_base_with_target(stack_repo)
    rows = commits_in_range(stack_repo, f"{target_tip}..HEAD", first_parent=True)
    from gerrit_workflow_tools.core.ready_strategy import ReadyCommitRow

    ready_rows = [
        ReadyCommitRow(sha=r.sha, short_sha=r.short_sha, subject=r.subject, change_id=r.change_id) for r in rows
    ]
    highlighter = build_summary_highlighter(
        Settings.from_cwd(stack_repo),
        cwd=stack_repo,
        commits=ready_rows,
        project="testproj",
    )
    assert rows[0].sha in highlighter.blocked_shas
    assert rows[1].sha in highlighter.blocked_shas


def test_resolve_registry_callable_none_without_files(stack_repo: Path) -> None:
    assert (
        resolve_registry_callable(
            stack_repo,
            "testproj",
            domain="ready",
            package_name="ger_ready",
            settings=Settings.from_map({}),
        )
        is None
    )
