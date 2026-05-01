"""Low-level HTTP helpers for Gerrit REST (integration tests)."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth


def strip_magic_json_prefix(raw: str) -> str:
    s = raw.lstrip()
    if s.startswith(")]}'"):
        nl = s.find("\n")
        if nl != -1:
            return s[nl + 1 :]
    return raw


class GerritHttpSession:
    """Authenticated session to Gerrit ``/a/`` REST API."""

    def __init__(self, web_base: str, *, user: str, password: str) -> None:
        self.web_base = web_base.rstrip("/")
        self.user = user
        self.password = password
        self._session = requests.Session()
        self._session.headers.update({"Accept": "*/*"})

    def _auth_candidates(self) -> list[object]:
        # Gerrit HTTP passwords are commonly used via Basic auth, while some
        # setups still use/accept Digest.
        return [
            HTTPBasicAuth(self.user, self.password),
            HTTPDigestAuth(self.user, self.password),
        ]

    def request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | list[tuple[str, str]] | None = None,
        body: dict[str, Any] | None = None,
        ok_status: tuple[int, ...] = (200, 201, 204),
    ) -> Any:
        url = f"{self.web_base}/a/{path.lstrip('/')}"
        kwargs: dict[str, Any] = {"timeout": 120}
        if params is not None:
            kwargs["params"] = params
        if body is not None:
            kwargs["json"] = body
            kwargs.setdefault("headers", {})["Content-Type"] = "application/json"
        resp = None
        for auth in self._auth_candidates():
            attempt_kwargs = dict(kwargs)
            attempt_kwargs["auth"] = auth
            resp = self._session.request(method, url, **attempt_kwargs)
            if resp.status_code in ok_status:
                break
            # Retry with the next auth mechanism only when this looks like auth rejection.
            if resp.status_code not in (401, 403):
                break
        assert resp is not None
        if resp.status_code not in ok_status:
            raise RuntimeError(
                f"Gerrit {method} {url} -> {resp.status_code}: {resp.text[:500]}",
            )
        if resp.status_code == 204 or not resp.content.strip():
            return None
        text = resp.text
        try:
            return json.loads(strip_magic_json_prefix(text))
        except json.JSONDecodeError as e:
            raise RuntimeError(f"invalid JSON from Gerrit: {e}: {text[:200]}") from e

    def get_json(self, path: str, *, params: dict[str, str] | list[tuple[str, str]] | None = None) -> Any:
        return self.request_json("GET", path, params=params)

    def put_json(self, path: str, *, body: dict[str, Any] | None = None) -> Any:
        return self.request_json("PUT", path, body=body)

    def post_json(self, path: str, *, body: dict[str, Any] | None = None) -> Any:
        return self.request_json("POST", path, body=body)

    def delete(self, path: str) -> Any:
        return self.request_json("DELETE", path, ok_status=(200, 204))


def quote_change_id(change_id: str) -> str:
    return quote(change_id, safe="")


def change_review_path(change_id: str, revision: str = "current") -> str:
    enc = quote_change_id(change_id)
    rev = quote(revision, safe="")
    return f"changes/{enc}/revisions/{rev}/review"
