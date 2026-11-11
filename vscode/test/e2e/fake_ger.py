"""A stand-in for ``ger`` in the extension's end-to-end run: the real ``ger serve`` over a fake Gerrit.

    fake_ger.py --make-repo DIR     create the fixture repository (a four-commit local stack)
    fake_ger.py serve [options]     what the extension starts: cli_serve over a ChangeStore

With GER_E2E_EVENTS naming a file, that file is Gerrit's event stream: each line the suite
appends is one event, and its ``_fake`` part says how the fake Gerrit changed first.

The Gerrit side is the test suite's own: the stack is one outgoing review chain with two
unresolved comments on its second change and a failing build on the two top ones; change 900
from somebody else waits for review. Its commit is in the fixture under refs/changes/00/900/1,
where `ger review` fetches it from (the remote is the repository itself).
"""

from __future__ import annotations

import json
import os
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any


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


class FileEvents:
    """Gerrit's event stream as the suite writes it: lines appended to a file, read from where it ends now."""

    description = "ssh://e2e@gerrit.example:29418"

    def __init__(self, path: Path, store: ChangeStore, changes: dict[int, dict[str, Any]]) -> None:
        self._path = path
        self._store = store
        self._changes = changes

    def connect(self) -> TailedFile:
        return TailedFile(self._path, self._apply)

    def _apply(self, fake: dict[str, Any]) -> None:
        change = self._changes[fake["number"]]
        change.update(fake.get("update", {}))
        if "comments" in fake:
            self._store.set_comments(change["id"], fake["comments"])


class TailedFile:
    """One connection: a first line at once (live without the grace period), then what is appended."""

    def __init__(self, path: Path, apply: Any) -> None:
        self._path = path
        self._apply = apply
        self._offset = path.stat().st_size if path.exists() else 0
        self._closed = threading.Event()

    def lines(self) -> Iterator[str]:
        yield json.dumps({"type": "ger-e2e-connected"})
        pending = ""
        while not self._closed.wait(0.05):
            with self._path.open(encoding="utf-8") as stream:
                stream.seek(self._offset)
                chunk = stream.read()
                self._offset = stream.tell()
            pending += chunk
            *complete, pending = pending.split("\n")
            for line in complete:
                event = json.loads(line)
                fake = event.pop("_fake", None)
                if fake:
                    self._apply(fake)
                yield json.dumps(event)

    def failure(self) -> str:
        return "closed"

    def close(self) -> None:
        self._closed.set()


def fake_gerrit(repo: Path) -> tuple[ChangeStore, dict[int, dict[str, Any]]]:
    """The Gerrit the fixture repository talks to, and its changes an event may change."""
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
    return store, {900: theirs}


if __name__ == "__main__":
    if sys.argv[1:2] == ["--make-repo"]:
        make_repo(Path(sys.argv[2]))
        raise SystemExit(0)
    if sys.argv[1:2] == ["serve"]:
        store, changes = fake_gerrit(Path.cwd())
        events_file = os.environ.get("GER_E2E_EVENTS")
        events = FileEvents(Path(events_file), store, changes) if events_file else None
        raise SystemExit(serve_main(sys.argv[2:], gerrit=store, ci_http=FakeCiHttp(jenkins_build()), events=events))
    raise SystemExit("usage: fake_ger.py (--make-repo DIR | serve [options])")
