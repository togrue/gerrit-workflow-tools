# Spec: docu/spec/commands/serve.md
"""The ``ger serve`` contract: one source (server/contract.py), its generated files fresh, every route in it.

Every answer any test gets from ``GerApi`` is held against the contract as well
(``tests/serve_contract_check.py``, wired in ``conftest.py``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gerrit_workflow_tools.server import api
from gerrit_workflow_tools.server.contract import ENDPOINTS
from gerrit_workflow_tools.server.contract_commit import Diff
from gerrit_workflow_tools.server.contract_export import json_schema, json_schema_text, problems, typescript
from gerrit_workflow_tools.server.contract_stack import Chain, Stack


REPO = Path(__file__).resolve().parents[1]
REGENERATE = "run `uv run python scripts/generate_serve_contract.py`"


def test_the_extensions_types_are_generated_from_the_contract() -> None:
    on_disk = (REPO / "vscode" / "src" / "contract.ts").read_text(encoding="utf-8")
    assert on_disk == typescript(), f"vscode/src/contract.ts is not what server/contract.py says: {REGENERATE}"


def test_the_json_schema_is_generated_from_the_contract() -> None:
    on_disk = (REPO / "docu" / "spec" / "serve-contract.schema.json").read_text(encoding="utf-8")
    assert on_disk == json_schema_text(), f"docu/spec/serve-contract.schema.json is stale: {REGENERATE}"


def test_every_route_is_an_endpoint_of_the_contract_and_every_endpoint_a_route() -> None:
    routes = {(method, path) for method, path, _ in (*api.ROUTES, *api.UNLOCKED_ROUTES)}
    assert routes == {(endpoint.method, endpoint.path) for endpoint in ENDPOINTS}


def test_every_schema_reference_names_a_definition() -> None:
    schema = json_schema()
    text = json.dumps(schema)
    refs = {part.split('"')[0] for part in text.split('"$ref": "#/$defs/')[1:]}
    assert refs <= set(schema["$defs"])
    assert {"Stack", "ShownCommit", "Hello"} <= set(schema["$defs"])


def test_a_value_that_strays_from_the_contract_says_where() -> None:
    stack: dict[str, Any] = {
        "branch": "main",
        "head": None,
        "rebase_in_progress": "no",
        "rebase": None,
        "push_blocked": None,
        "upstream": None,
        "stack": {"project": "p", "target_branch": "main", "push_branch": "main"},
        "summary": {"commits": 0, "ready": 0, "attention": 0, "needs_edit": 0, "not_on_gerrit": 0},
        "commits": [],
        "surprise": 1,
    }
    assert problems(stack, Stack) == [
        "$.surprise: not in Stack",
        "$.rebase_in_progress: 'no' is not a boolean",
    ]
    del stack["surprise"], stack["summary"]
    stack["rebase_in_progress"] = False
    assert problems(stack, Stack) == ["$.summary: missing from Stack"]


def test_optional_fields_may_be_absent_and_literals_are_held_to_their_values() -> None:
    diff: dict[str, Any] = {"commit": "c", "base": None, "local_commit": True, "files": []}
    assert problems(diff, Diff) == []
    member: dict[str, Any] = {
        "number": 1,
        "change_id": "I1",
        "subject": "s",
        "verified": True,
        "code_review": None,
        "comments_unresolved": 0,
        "attention_reasons": [],
        "severity": "fine",
        "url": None,
        "sha": None,
    }
    chain = {"members": [member]}
    found = problems(chain, Chain)
    assert any(line.startswith("$.members[0].verified: fits none of") for line in found)
    assert any(line.startswith("$.members[0].severity: 'fine' is not one of") for line in found)
