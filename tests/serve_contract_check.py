"""Hold every call ``GerApi`` answers against the ``ger serve`` contract, in whichever test it is made.

The answer must be the endpoint's type. So must the body the test sent, and its query may only
name parameters the endpoint's query type has: a call the server takes is one the contract allows.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

import pytest
from typing_extensions import get_type_hints

from gerrit_workflow_tools.server import api
from gerrit_workflow_tools.server.contract import ENDPOINTS, Endpoint, path_pattern
from gerrit_workflow_tools.server.contract_export import problems


def endpoint_for(method: str, path: str) -> Endpoint:
    """The contract's endpoint for a full request path (``/api/v1/chains/12``)."""
    tail = path[len(api.PREFIX) :].rstrip("/")
    for endpoint in ENDPOINTS:
        if endpoint.method == method and re.fullmatch(path_pattern(endpoint.path), tail):
            return endpoint
    raise AssertionError(f"{method} {path} answered, but server/contract.py has no such endpoint")


def assert_keeps_contract(
    method: str,
    path: str,
    answer: Any,
    *,
    query: Mapping[str, list[str]] | None = None,
    body: Mapping[str, Any] | None = None,
) -> None:
    """A call the server answered: *answer*, as it goes over the wire, *body* and *query* are what the contract says."""
    endpoint = endpoint_for(method, path)
    found = [f"answer {line}" for line in problems(json.loads(json.dumps(answer)), endpoint.answer)]
    found += [f"body {line}" for line in problems(dict(body or {}), endpoint.body)]
    known = get_type_hints(endpoint.query)
    found += [f"query ${'.'}{name}: not in {endpoint.query.__name__}" for name in query or {} if name not in known]
    assert not found, f"{endpoint.key} strays from server/contract.py:\n  " + "\n  ".join(found)


def hold_answers_to_the_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    handle, handle_unlocked = api.GerApi.handle, api.GerApi.handle_unlocked

    def checked(
        self: api.GerApi,
        method: str,
        path: str,
        query: Mapping[str, list[str]] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        answer = handle(self, method, path, query, body)
        assert_keeps_contract(method, path, answer, query=query, body=body)
        return answer

    def checked_unlocked(
        self: api.GerApi, method: str, path: str, query: Mapping[str, list[str]] | None = None
    ) -> dict[str, Any] | None:
        answer = handle_unlocked(self, method, path, query)
        if answer is not None:
            assert_keeps_contract(method, path, answer, query=query)
        return answer

    monkeypatch.setattr(api.GerApi, "handle", checked)
    monkeypatch.setattr(api.GerApi, "handle_unlocked", checked_unlocked)
