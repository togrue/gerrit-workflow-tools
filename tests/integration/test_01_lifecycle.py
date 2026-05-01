"""Checkpoint 1: Docker daemon, Gerrit HTTP, and session fixture."""

from __future__ import annotations

import requests


def test_gerrit_http_reachable(gerrit_integration_context) -> None:
    base = gerrit_integration_context.http_base.rstrip("/")
    r = requests.get(f"{base}/config/server/version", timeout=10)
    assert r.status_code == 200
    assert len(r.text) > 0
