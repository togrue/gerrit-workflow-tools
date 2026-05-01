"""Minimal check: Docker Gerrit container is up and the web UI responds (no seeding, no REST auth)."""

from __future__ import annotations

import requests


def test_gerrit_container_http_ping(gerrit_docker_session) -> None:
    """GET ``/config/server/version`` returns 200 after :func:`gerrit_docker_session` startup."""
    base = gerrit_docker_session.http_base.rstrip("/")
    r = requests.get(f"{base}/config/server/version", timeout=15)
    assert r.status_code == 200
    assert len(r.text.strip()) > 0
