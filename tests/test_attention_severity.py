"""Severity: how urgent attention reasons are, decided once in ``core.attention`` and served.

VS Code once classified the reasons itself and missed the ones added after it: a commit whose
only problem was an unsquashed fixup wore the green check. Now every reason has a severity in
core, every payload that carries reasons carries the severity beside them, and the extension's
test fixtures read the same table (``vscode/test/attention.json``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gerrit_workflow_tools.core.attention import REASON_SEVERITY, Attention, Severity, attention_severity
from gerrit_workflow_tools.server.api import GerApi
from tests.test_serve import _gerrit, _get


ROOT = Path(__file__).resolve().parents[1]


def _reasons() -> set[str]:
    return {value for name, value in vars(Attention).items() if name.isupper() and isinstance(value, str)}


def test_every_reason_has_a_severity() -> None:
    assert set(REASON_SEVERITY) == _reasons()


def test_the_most_urgent_reason_decides_and_an_unknown_one_is_a_warning() -> None:
    assert attention_severity([Attention.AWAITING_REVIEW, Attention.CI_FAILED]) is Severity.ERROR
    assert attention_severity([Attention.AWAITING_REVIEW, Attention.UNRESOLVED_COMMENTS]) is Severity.WARNING
    assert attention_severity([Attention.NOT_PUSHED]) is Severity.PENDING
    assert attention_severity([]) is Severity.OK
    # A project's attention strategy may name its own reasons: they ask for something.
    assert attention_severity(["license-header-missing", Attention.WIP]) is Severity.WARNING
    # What VS Code once drew as a green check.
    assert attention_severity([Attention.UNSQUASHED_FIXUP]) is Severity.ERROR
    assert attention_severity([Attention.DIVERGED]) is Severity.ERROR


def test_the_extensions_fixtures_use_cores_table() -> None:
    served = json.loads((ROOT / "vscode" / "test" / "attention.json").read_text(encoding="utf-8"))
    assert served == {reason: severity.value for reason, severity in REASON_SEVERITY.items()}, (
        "vscode/test/attention.json must be REASON_SEVERITY: regenerate it from core.attention"
    )


def _attended(payload: dict[str, Any]) -> None:
    assert payload["severity"] == attention_severity(payload["attention_reasons"]).value, payload


def test_every_served_list_of_reasons_has_its_severity_beside_it(stack_repo: Path) -> None:
    store, _keys, _rows = _gerrit(stack_repo, [{}, {"unresolved_comment_count": 1}, {"verified": -1}, {}])
    api = GerApi(stack_repo, gerrit=store)
    overview = _get(api, "/overview")
    commits = overview["local"]["commits"]
    [chain] = overview["sections"][1]["chains"]
    for payload in [*commits, chain, *chain["members"]]:
        _attended(payload)
    assert {c["severity"] for c in commits} >= {"error", "warning"}
    assert overview["headline"]["severity"] == "error"  # CI failed
    for shown in _get(api, "/show", rev=[c["sha"] for c in commits])["commits"]:
        _attended(shown)
