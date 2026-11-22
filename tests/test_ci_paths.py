# Spec: docu/spec/ci-content.md § Finding paths
"""Logged paths resolve to the commit's files, whatever directory the build agent ran in."""

from __future__ import annotations

from pathlib import Path

from gerrit_workflow_tools.core.ci_content import CiFinding
from gerrit_workflow_tools.core.ci_content_service import CiContentService
from gerrit_workflow_tools.core.ci_content_store import CiContentStore
from gerrit_workflow_tools.core.ci_jenkins import JenkinsProvider
from gerrit_workflow_tools.core.ci_links import CiLink
from gerrit_workflow_tools.core.ci_paths import TrackedPaths, resolve_finding_paths, resolve_paths, tracked_paths
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.git_run import git
from tests.ci_fakes import JENKINS, FakeCiHttp, jenkins_build


TRACKED = TrackedPaths.of(
    [
        "setup.py",
        "src/core/utils.py",
        "src/cli/utils.py",
        "src/main.c",
        "include/app.h",
        "java/src/test/java/com/x/FooTest.java",
    ]
)


def _resolve(*logged: str) -> dict[str, str]:
    return resolve_paths(logged, TRACKED)


def test_a_tracked_path_is_itself_and_backslashes_do_not_matter() -> None:
    assert _resolve("src/main.c", "src\\core\\utils.py", "./setup.py") == {
        "src/main.c": "src/main.c",
        "src\\core\\utils.py": "src/core/utils.py",
        "./setup.py": "setup.py",
    }


def test_the_workspace_directory_is_stripped_whatever_it_is() -> None:
    assert _resolve("/home/jenkins/workspace/job@2/src/core/utils.py") == {
        "/home/jenkins/workspace/job@2/src/core/utils.py": "src/core/utils.py"
    }
    assert _resolve("C:\\ws\\job\\src\\main.c") == {"C:\\ws\\job\\src\\main.c": "src/main.c"}
    assert _resolve("checkout/src/main.c") == {"checkout/src/main.c": "src/main.c"}


def test_a_lone_root_level_match_outside_any_known_workspace_is_not_ours() -> None:
    # An installed package's setup.py is not the repository's.
    assert _resolve("/usr/lib/python3/site-packages/other/setup.py") == {}
    assert _resolve("/usr/include/stdio.h") == {}


def test_findings_that_agree_on_a_workspace_vouch_for_a_root_level_file() -> None:
    assert _resolve("/ws/job/setup.py", "/ws/job/src/main.c") == {
        "/ws/job/setup.py": "setup.py",
        "/ws/job/src/main.c": "src/main.c",
    }


def test_a_path_relative_to_a_subdirectory_resolves_only_when_one_file_ends_with_it() -> None:
    assert _resolve("main.c", "../include/app.h", "FooTest.java", "utils.py") == {
        "main.c": "src/main.c",
        "../include/app.h": "include/app.h",
        "FooTest.java": "java/src/test/java/com/x/FooTest.java",
    }


def test_resolving_moves_the_fingerprint_with_the_path() -> None:
    one = CiFinding(tool="cc", kind="error", message="boom", path="/ws/job/src/main.c", line=3)
    two = CiFinding(tool="cc", kind="error", message="boom", path="/ws/job@2/src/main.c", line=9)
    outside = CiFinding(tool="cc", kind="warning", message="x", path="/usr/include/stdio.h")
    [a, kept] = resolve_finding_paths([one, outside], TRACKED)
    [b] = resolve_finding_paths([two], TRACKED)
    assert (a.path, a.line, b.path) == ("src/main.c", 3, "src/main.c")
    assert a.fingerprint == b.fingerprint != one.fingerprint
    assert kept is outside
    assert resolve_finding_paths([one], None) == [one]


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    (repo / "demo").mkdir(parents=True)
    (repo / "demo" / "calc.py").write_text("def divide(a, b):\n    return a / b\n", encoding="utf-8")
    env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    git("init", "-b", "main", cwd=repo, env=env)
    git("add", ".", cwd=repo, env=env)
    git("commit", "-m", "calc", cwd=repo, env=env)
    return repo, git("rev-parse", "HEAD", cwd=repo).stdout.strip()


def test_tracked_paths_come_from_the_commit_and_only_for_an_object_id(tmp_path: Path) -> None:
    repo, sha = _repo(tmp_path)
    tracked = tracked_paths(repo, sha)
    assert tracked is not None and tracked.paths == {"demo/calc.py"}
    assert tracked_paths(repo, "HEAD") is None
    assert tracked_paths(repo, "0" * 40) is None


def test_the_service_resolves_against_the_commit_but_stores_what_the_log_said(tmp_path: Path) -> None:
    repo, sha = _repo(tmp_path)
    log = "/var/agents/7/ws/demo/calc.py:2:12: error: Unsupported operand types  [operator]\nFinished: FAILURE\n"
    link = CiLink(label="jenkins", url=f"{JENKINS}/console", source="message")
    service = CiContentService(
        providers=[JenkinsProvider()],
        http=FakeCiHttp(jenkins_build(log=log)),
        settings=Settings.from_map({}),
        store=CiContentStore(tmp_path / "store"),
        cwd=repo,
    )
    resolved = service.fetch_one(link, commit=sha)
    assert resolved.findings[0].path == "demo/calc.py"
    cached = service.fetch_one(link, commit=sha)
    assert cached.provenance.cached and cached.findings[0].fingerprint == resolved.findings[0].fingerprint
    # Without a commit, or with one that is not here, the path stays as logged.
    assert service.fetch_one(link).findings[0].path == "/var/agents/7/ws/demo/calc.py"
    assert service.fetch_one(link, commit="0" * 40).findings[0].path == "/var/agents/7/ws/demo/calc.py"
