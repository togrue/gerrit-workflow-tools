"""A stand-in for ``ger`` in the extension's end-to-end run: the real ``ger serve`` over a fake Gerrit.

    fake_ger.py --make-repo DIR     create the fixture repository (a four-commit local stack)
    fake_ger.py serve [options]     what the extension starts: cli_serve over a ChangeStore

The Gerrit side is the test suite's own: the stack is one outgoing review chain with two
unresolved comments on its second change and a failing build on the two top ones; change 900
from somebody else waits for review. Its commit is in the fixture under refs/changes/00/900/1,
where `ger review` fetches it from (the remote is the repository itself).
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from tests.change_store import ChangeStore  # noqa: E402
from tests.ci_fakes import FakeCiHttp, jenkins_build  # noqa: E402
from tests.fixtures import configure_gerrit_target, make_stack_repo  # noqa: E402
from tests.test_inbox import _change  # noqa: E402
from tests.test_serve import WEB, _gerrit  # noqa: E402

from gerrit_workflow_tools.cli_serve import main as serve_main  # noqa: E402
from gerrit_workflow_tools.core.config import Settings  # noqa: E402
from gerrit_workflow_tools.core.git_run import git, git_out  # noqa: E402
from gerrit_workflow_tools.core.inbox import build_to_review_query  # noqa: E402


THEIRS_CHANGE_ID = "I" + f"{900:040x}"
THEIRS_REF = "refs/changes/00/900/1"


def make_repo(path: Path) -> None:
    make_stack_repo(path)
    configure_gerrit_target(path, "main")
    git("remote", "set-url", "origin", ".", cwd=path)
    git("config", "gerrit.project", "testproj", cwd=path)
    git("config", "gerrit.webUrl", WEB, cwd=path)
    # run.ts keeps your global git config out, so the repository brings its own identity.
    git("config", "user.name", "Test", cwd=path)
    git("config", "user.email", "test@example.com", cwd=path)
    # Somebody else's change 900, on main.
    git("switch", "-q", "--detach", "main", cwd=path)
    (path / "uploader.txt").write_text("retries: 3\n", encoding="utf-8")
    git("add", "uploader.txt", cwd=path)
    message = f"Add retry to the uploader\n\nChange-Id: {THEIRS_CHANGE_ID}"
    git("-c", "user.name=alice", "-c", "user.email=alice@example.com", "commit", "-qm", message, cwd=path)
    git("update-ref", THEIRS_REF, "HEAD", cwd=path)
    git("switch", "-q", "feature", cwd=path)


def fake_gerrit(repo: Path) -> ChangeStore:
    """The Gerrit the fixture repository talks to."""
    overrides = [{}, {"unresolved_comment_count": 2}, {"verified": -1}, {"verified": -1}]
    store, keys, _rows = _gerrit(repo, overrides)
    store.set_comments(
        keys[1],
        {
            "b.txt": [
                {
                    "id": "c1",
                    "line": 1,
                    "message": "Please rename this.",
                    "unresolved": True,
                    "patch_set": 1,
                    "author": {"name": "Alice Reviewer"},
                },
                {
                    "id": "c2",
                    "line": 1,
                    "message": "Is this the right design?\n\nI would split it.",
                    "unresolved": True,
                    "patch_set": 1,
                    "author": {"name": "Bob Architect"},
                },
            ]
        },
    )
    theirs = _change(
        number=900,
        sha=git_out("rev-parse", THEIRS_REF, cwd=repo),
        parent=git_out("rev-parse", "main", cwd=repo),
        subject="Add retry to the uploader",
        updated="2026-11-08 09:00:00.000000000",
        attention_since="2026-11-07 09:00:00.000000000",
    )
    theirs.update(project="testproj", id=f"testproj~main~{THEIRS_CHANGE_ID}")
    store.add_change(theirs)
    store.stub_query(build_to_review_query(Settings.from_cwd(repo), projects=[], include_unready=False), [theirs])
    store.set_comments(
        theirs["id"],
        {
            "uploader.txt": [
                {
                    "id": "c900",
                    "line": 1,
                    "message": "Should it back off between retries?",
                    "unresolved": True,
                    "patch_set": 1,
                    "author": {"name": "Bob Architect"},
                }
            ]
        },
    )
    return store


if __name__ == "__main__":
    if sys.argv[1:2] == ["--make-repo"]:
        make_repo(Path(sys.argv[2]))
        raise SystemExit(0)
    if sys.argv[1:2] == ["serve"]:
        raise SystemExit(serve_main(sys.argv[2:], gerrit=fake_gerrit(Path.cwd()), ci_http=FakeCiHttp(jenkins_build())))
    raise SystemExit("usage: fake_ger.py (--make-repo DIR | serve [options])")
