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
from tests.change_store import tag_parallel_jobs
from tests.fixtures import (
    configure_gerrit_target,
    finalize_git_template,
    make_gcid_cli_repo,
    make_repo_duplicate_change_id,
    make_repo_with_merged_side_branch,
    make_stack_repo,
)


# ``ger.webUrl`` of the ``web_stack_repo`` fixture.
WEB_STACK_REPO_URL = "https://g.example"

# Test repositories are thrown away, so git need not flush them to disk: on a real disk
# (anything but a RAM-backed temp directory) the flushes are most of a commit's cost.
_STUB_GLOBAL_GITCONFIG = "[user]\n\tname = Test\n\temail = test@example.com\n[core]\n\tfsync = none\n"
_STUB_DIR_PREFIX = "gerrit-workflow-tools-test-gitconfig-"

# Cumulative fixture setup seconds when GWT_TEST_PROFILE=1 (see pytest_fixture_setup).
_FIXTURE_PROFILE_SECONDS: dict[str, float] = defaultdict(float)
_FIXTURE_PROFILE_COUNTS: dict[str, int] = defaultdict(int)

# The stub global git config of this pytest process (see pytest_configure); None without isolation.
_STUB_GLOBAL: Path | None = None


@pytest.fixture(autouse=True)
def _isolate_xdg_config_home(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point ``$XDG_CONFIG_HOME`` at a directory of this test alone (made only when written).

    Registry trust records and global registries live there: one test's trust must not reach
    another, and none may reach the developer's own ``~/.config/ger``.
    """
    name = "".join(c if c.isalnum() else "_" for c in request.node.nodeid)[-80:]
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.getbasetemp() / "xdg-config" / name))


@pytest.fixture(autouse=True)
def _reset_output_modes() -> Generator[None]:
    """Keep colour and OSC 8 off unless a test enables them; avoid leaks into later tests.

    A command run with ``--color=always`` leaves the mode set, and under xdist the next
    test in that worker is not the next one in the file.
    """
    yield
    from gerrit_workflow_tools.render.style import set_color_mode, set_hyperlink_mode

    set_color_mode(False)
    set_hyperlink_mode(False)


def _is_xdist_worker(config: pytest.Config) -> bool:
    return hasattr(config, "workerinput")


def pytest_configure(config: pytest.Config) -> None:
    """Point Git at replacement global/system config so tests ignore the real ``~/.gitconfig``.

    The stub global file supplies only ``user.*`` so ``git commit`` works without per-call
    ``GIT_AUTHOR_*`` env. Production uses full ``git config --list`` (standard precedence);
    without this, tests would pick up the developer's ``ger.*`` keys and become flaky.

    The stub lives in a directory of this process: two runs at once (two worktrees, two agents)
    must not read each other's writes. Each test then gets its own copy of it
    (``_isolate_git_global_config``), so what ``ger setup`` writes does not reach the next test.
    """
    global _STUB_GLOBAL
    if os.environ.get("GERRIT_WORKFLOW_TOOLS_NO_GIT_CONFIG_ISOLATION", "").lower() in (
        "1",
        "true",
        "yes",
    ):
        return
    inherited = os.environ.get("GIT_CONFIG_GLOBAL", "")
    if _is_xdist_worker(config) and Path(inherited).parent.name.startswith(_STUB_DIR_PREFIX):
        # The controller made the stub and exported it; it also removes it.
        _STUB_GLOBAL = Path(inherited)
        return
    base = Path(tempfile.mkdtemp(prefix=_STUB_DIR_PREFIX))
    stub_global = base / "stub-global"
    stub_system = base / "stub-system"
    stub_global.write_text(_STUB_GLOBAL_GITCONFIG, encoding="utf-8")
    stub_system.write_text("", encoding="utf-8")
    os.environ.setdefault("GIT_CONFIG_GLOBAL", str(stub_global))
    os.environ.setdefault("GIT_CONFIG_SYSTEM", str(stub_system))
    _STUB_GLOBAL = stub_global


def pytest_unconfigure(config: pytest.Config) -> None:
    """Remove this process's stub git config directory."""
    if _STUB_GLOBAL is not None and not _is_xdist_worker(config):
        shutil.rmtree(_STUB_GLOBAL.parent, ignore_errors=True)


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
    git("config", "ger.project", "testproj", cwd=repo)
    finalize_git_template(repo)
    return repo


@pytest.fixture(scope="session")
def _web_stack_repo_template(
    tmp_path_factory: pytest.TempPathFactory,
    _configured_stack_repo_template: Path,
) -> Path:
    root = tmp_path_factory.mktemp("tpl_stack_web")
    repo = _copy_git_repo(_configured_stack_repo_template, root / "repo")
    git("config", "ger.webUrl", WEB_STACK_REPO_URL, cwd=repo)
    return repo


@pytest.fixture(scope="session")
def _merged_side_branch_repo_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("tpl_merged_side_branch")
    repo = make_repo_with_merged_side_branch(root / "repo")
    # A relative self-remote remains valid after the template is copied.
    git("remote", "set-url", "origin", ".", cwd=repo)
    finalize_git_template(repo)
    return repo


@pytest.fixture(scope="session")
def _dup_repo_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("tpl_dup")
    repo = make_repo_duplicate_change_id(root / "repo")
    configure_gerrit_target(repo, "main")
    # A relative self-remote remains valid after the template is copied.
    git("remote", "set-url", "origin", ".", cwd=repo)
    git("config", "ger.project", "testproj", cwd=repo)
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


@pytest.fixture
def web_stack_repo(tmp_path: Path, _web_stack_repo_template: Path) -> Path:
    """``stack_repo`` with ``ger.webUrl`` set to :data:`WEB_STACK_REPO_URL`."""
    return _copy_git_repo(_web_stack_repo_template, tmp_path / "stack")


@pytest.fixture
def merged_side_branch_repo(tmp_path: Path, _merged_side_branch_repo_template: Path) -> Path:
    """Feature branch that merged a side branch (see ``make_repo_with_merged_side_branch``)."""
    return _copy_git_repo(_merged_side_branch_repo_template, tmp_path / "r")


@pytest.fixture(autouse=True)
def _isolate_xdg_cache_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep Gerrit API cache rows from leaking between tests that share mock hosts."""

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))


@pytest.fixture(autouse=True)
def _isolate_git_global_config(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Give each test its own copy of the stub global git config, so a test that writes to it
    (``ger setup``, the recent upstreams) leaves the next test the stub as it was."""
    if _STUB_GLOBAL is None or os.environ.get("GIT_CONFIG_GLOBAL") != str(_STUB_GLOBAL):
        return
    # Not in tmp_path, where a test's repository may be and would see an untracked file.
    own = tmp_path_factory.mktemp("gitconfig") / "global"
    shutil.copyfile(_STUB_GLOBAL, own)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(own))


@pytest.fixture
def parallel_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tag each ``parallel_map`` job's calls, so ``ChangeStore.ordered_calls`` can order them."""

    tag_parallel_jobs(monkeypatch)


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
    ci_http: object | None = None,
) -> tuple[int, str, str]:
    """Run a CLI main with cwd set; capture stdout and stderr.

    If *catch_sys_exit* is true, ``SystemExit`` (e.g. from ``--help``) is
    turned into a return code instead of propagating.

    Pass *gerrit* to hand the command a ``GerritRest`` implementation (usually a
    :class:`ChangeStore`) instead of letting it build an ``HttpGerritRest``. Commands that
    take no ``gerrit`` parameter must not be given one. *ci_http* likewise hands ``ger ci`` /
    ``ger show`` a fake CI HTTP seam.
    """
    import io
    import sys

    monkeypatch.chdir(cwd)
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out_buf)
    monkeypatch.setattr(sys, "stderr", err_buf)
    kwargs: dict[str, object] = {"gerrit": gerrit} if gerrit is not None else {}
    if ci_http is not None:
        kwargs["ci_http"] = ci_http
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
