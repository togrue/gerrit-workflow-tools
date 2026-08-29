from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from collections import defaultdict
from collections.abc import Callable, Generator, Sequence
from pathlib import Path

import pytest

from gerrit_workflow_tools.core.git_run import git
from tests.fixtures import (
    configure_gerrit_target,
    finalize_git_template,
    make_gcid_cli_repo,
    make_repo_duplicate_change_id,
    make_stack_repo,
)

# Cumulative fixture setup seconds when GWT_TEST_PROFILE=1 (see pytest_fixture_setup).
_FIXTURE_PROFILE_SECONDS: dict[str, float] = defaultdict(float)
_FIXTURE_PROFILE_COUNTS: dict[str, int] = defaultdict(int)


@pytest.fixture(autouse=True)
def _clear_git_semantic_caches() -> Generator[None]:
    """Process-lifetime Worktree / stack-context memos must not leak between tests."""
    from gerrit_workflow_tools.core.git_run import clear_git_cache

    clear_git_cache()
    yield
    clear_git_cache()


@pytest.fixture(autouse=True)
def _reset_hyperlink_mode() -> Generator[None]:
    """Keep OSC 8 mode off unless a test enables it; avoid leaks into later tests."""
    yield
    from gerrit_workflow_tools.cli_style import set_hyperlink_mode

    set_hyperlink_mode(False)


def pytest_configure(config: pytest.Config) -> None:
    """Point Git at replacement global/system config so tests ignore the real ``~/.gitconfig``.

    The stub global file supplies only ``user.*`` so ``git commit`` works without per-call
    ``GIT_AUTHOR_*`` env. Production uses full ``git config --list`` (standard precedence);
    without this, tests would pick up the developer's ``gerrit.*`` keys and become flaky.
    """
    if os.environ.get("GERRIT_WORKFLOW_TOOLS_NO_GIT_CONFIG_ISOLATION", "").lower() in (
        "1",
        "true",
        "yes",
    ):
        return
    base = Path(tempfile.gettempdir()) / "gerrit-workflow-tools-test-gitconfig"
    base.mkdir(parents=True, exist_ok=True)
    stub_global = base / "stub-global"
    stub_system = base / "stub-system"
    stub_global.write_text(
        "[user]\n\tname = Test\n\temail = test@example.com\n",
        encoding="utf-8",
    )
    stub_system.write_text("", encoding="utf-8")
    os.environ.setdefault("GIT_CONFIG_GLOBAL", str(stub_global))
    os.environ.setdefault("GIT_CONFIG_SYSTEM", str(stub_system))


def _test_profile_enabled() -> bool:
    return os.environ.get("GWT_TEST_PROFILE", "").lower() in ("1", "true", "yes")


@pytest.hookimpl(hookwrapper=True)
def pytest_fixture_setup(fixturedef: pytest.FixtureDef, request: pytest.FixtureRequest) -> Generator[None]:
    """When GWT_TEST_PROFILE=1, accumulate wall time per fixture name."""
    if not _test_profile_enabled():
        yield
        return
    t0 = time.perf_counter()
    yield
    elapsed = time.perf_counter() - t0
    name = fixturedef.argname
    _FIXTURE_PROFILE_SECONDS[name] += elapsed
    _FIXTURE_PROFILE_COUNTS[name] += 1


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if not _test_profile_enabled() or not _FIXTURE_PROFILE_SECONDS:
        return
    rows = sorted(_FIXTURE_PROFILE_SECONDS.items(), key=lambda kv: -kv[1])
    total = sum(_FIXTURE_PROFILE_SECONDS.values())
    print("\nGWT_TEST_PROFILE fixture setup totals:")
    for name, sec in rows[:40]:
        n = _FIXTURE_PROFILE_COUNTS[name]
        print(f"  {sec:8.2f}s  n={n:4d}  avg={sec / n:6.3f}s  {name}")
    print(f"  {'':8}  fixture setup sum={total:.2f}s  (exitstatus={exitstatus})")


def _copy_git_repo(template: Path, dest: Path) -> Path:
    """Clone a session-built template into an isolated per-test directory."""
    shutil.copytree(template, dest)
    return dest


@pytest.fixture(scope="session")
def _stack_repo_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("tpl_stack")
    repo = make_stack_repo(root / "repo")
    finalize_git_template(repo)
    return repo


@pytest.fixture(scope="session")
def _configured_stack_repo_template(
    tmp_path_factory: pytest.TempPathFactory,
    _stack_repo_template: Path,
) -> Path:
    root = tmp_path_factory.mktemp("tpl_stack_configured")
    repo = _copy_git_repo(_stack_repo_template, root / "repo")
    configure_gerrit_target(repo, "main")
    # A relative self-remote remains valid after the template is copied.
    git("remote", "set-url", "origin", ".", cwd=repo)
    git("config", "gerrit.project", "testproj", cwd=repo)
    finalize_git_template(repo)
    return repo


@pytest.fixture(scope="session")
def _dup_repo_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("tpl_dup")
    repo = make_repo_duplicate_change_id(root / "repo")
    configure_gerrit_target(repo, "main")
    git("remote", "set-url", "origin", ".", cwd=repo)
    finalize_git_template(repo)
    return repo


@pytest.fixture(scope="session")
def _gcid_cli_repo_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("tpl_gcid_cli")
    repo = make_gcid_cli_repo(root / "repo")
    finalize_git_template(repo)
    return repo


@pytest.fixture
def gcid_cli_repo(tmp_path: Path, _gcid_cli_repo_template: Path) -> Path:
    """Isolated copy of a small repo with three predictable Change-Ids (see ``GCID_CLI_CHANGE_IDS``)."""
    return _copy_git_repo(_gcid_cli_repo_template, tmp_path / "gcid_cli")


@pytest.fixture
def stack_repo_unconfigured(tmp_path: Path, _stack_repo_template: Path) -> Path:
    """Same graph as make_stack_repo; no branch gerrit config."""
    return _copy_git_repo(_stack_repo_template, tmp_path / "r")


@pytest.fixture
def stack_repo(tmp_path: Path, _configured_stack_repo_template: Path) -> Path:
    """Linear feature branch over main; third commit matches ^test!; upstream origin/main."""
    return _copy_git_repo(_configured_stack_repo_template, tmp_path / "stack")


@pytest.fixture(autouse=True)
def _isolate_xdg_cache_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep Gerrit API cache rows from leaking between tests that share mock hosts."""

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))


@pytest.fixture
def dup_repo(tmp_path: Path, _dup_repo_template: Path) -> Path:
    return _copy_git_repo(_dup_repo_template, tmp_path / "dup")


def run_cli(
    cwd: Path,
    main_fn: Callable[..., int],
    argv: Sequence[str],
    monkeypatch: pytest.MonkeyPatch,
    *,
    catch_sys_exit: bool = False,
    gerrit: object | None = None,
) -> tuple[int, str, str]:
    """Run a CLI main with cwd set; capture stdout and stderr.

    If *catch_sys_exit* is true, ``SystemExit`` (e.g. from ``--help``) is
    turned into a return code instead of propagating.

    Pass *gerrit* to hand the command a ``GerritRest`` implementation (usually a
    :class:`ChangeStore`) instead of letting it build an ``HttpGerritRest``. Commands that
    take no ``gerrit`` parameter must not be given one.
    """
    import io
    import sys

    monkeypatch.chdir(cwd)
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out_buf)
    monkeypatch.setattr(sys, "stderr", err_buf)
    kwargs = {"gerrit": gerrit} if gerrit is not None else {}
    try:
        code = main_fn(list(argv), **kwargs)
    except SystemExit as e:
        if not catch_sys_exit:
            raise
        if isinstance(e.code, int):
            code = e.code
        elif e.code is None:
            code = 0
        else:
            try:
                code = int(e.code)
            except (TypeError, ValueError):
                code = 1
    return code, out_buf.getvalue(), err_buf.getvalue()


def json_stdout(stdout: str) -> dict:
    return json.loads(stdout)
