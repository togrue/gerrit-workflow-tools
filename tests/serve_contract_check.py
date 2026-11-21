"""Hold every answer of ``GerApi`` against the ``ger serve`` contract, in whichever test it is given."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

import pytest

from gerrit_workflow_tools.server import api
from gerrit_workflow_tools.server.contract import ENDPOINTS, Endpoint
from gerrit_workflow_tools.server.contract_export import problems


def endpoint_for(method: str, path: str) -> Endpoint:
    """The contract's endpoint for a full request path (``/api/v1/chains/12``)."""
    tail = path[len(api.PREFIX) :].rstrip("/")
    for endpoint in ENDPOINTS:
        if endpoint.method == method and re.fullmatch(re.sub(r"\{\w+\}", "[^/]+", endpoint.path), tail):
            return endpoint
    raise AssertionError(f"{method} {path} answered, but server/contract.py has no such endpoint")


def assert_keeps_contract(method: str, path: str, answer: Any) -> None:
    """*answer*, as it goes over the wire, is what the contract says the endpoint answers."""
    endpoint = endpoint_for(method, path)
    found = problems(json.loads(json.dumps(answer)), endpoint.answer)
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
        assert_keeps_contract(method, path, answer)
        return answer

    def checked_unlocked(
        self: api.GerApi, method: str, path: str, query: Mapping[str, list[str]] | None = None
    ) -> dict[str, Any] | None:
        answer = handle_unlocked(self, method, path, query)
        if answer is not None:
            assert_keeps_contract(method, path, answer)
        return answer

    monkeypatch.setattr(api.GerApi, "handle", checked)
    monkeypatch.setattr(api.GerApi, "handle_unlocked", checked_unlocked)
