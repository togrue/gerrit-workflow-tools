"""Bootstrap HTTP credentials for Gerrit using development mode REST login."""

from __future__ import annotations

import logging
import os
import urllib.parse

import requests

from tests.integration.gerrit_http import GerritHttpSession

logger = logging.getLogger(__name__)


def _materialize_account_session(http_base: str, account_id: int) -> requests.Session:
    """Create a Gerrit session cookie by becoming ``account_id`` in development mode."""
    s = requests.Session()
    login = f"{http_base.rstrip('/')}/login/{urllib.parse.quote('#/', safe='')}?account_id={account_id}"
    try:
        r = s.get(login, timeout=30, allow_redirects=True)
        logger.debug(
            "dev login account_id=%s -> %s; cookies=%s",
            account_id,
            r.status_code,
            list(s.cookies.keys()),
        )
    except OSError as e:
        logger.debug("materialize login GET failed (may be ok): %s", e)
    return s


def try_put_http_password_cookie(session: requests.Session, http_base: str, new_password: str) -> bool:
    """Set HTTP password with cookie auth from dev-mode login.

    Gerrit behavior differs by version/config:
    - some accept ``/a/...`` directly with session cookie
    - some require non-``/a`` endpoint + ``X-Gerrit-Auth`` (XSRF token)
    """
    payload = {"http_password": new_password}
    base = http_base.rstrip("/")

    # Attempt 1: direct cookie call against /a endpoint.
    url_a = f"{base}/a/accounts/self/password.http"
    r = session.put(
        url_a,
        json=payload,
        headers={"Content-Type": "application/json", "Accept": "*/*"},
        timeout=60,
    )
    if r.status_code in (200, 201, 204):
        return True

    # Attempt 2: cookie auth with XSRF token and non-/a endpoint.
    xsrf = session.cookies.get("XSRF_TOKEN")
    headers = {"Content-Type": "application/json", "Accept": "*/*"}
    if xsrf:
        headers["X-Gerrit-Auth"] = xsrf
    url_plain = f"{base}/accounts/self/password.http"
    r2 = session.put(
        url_plain,
        json=payload,
        headers=headers,
        timeout=60,
    )
    if r2.status_code in (200, 201, 204):
        return True

    logger.debug(
        "password PUT (cookie session) failed: /a -> %s %s ; plain -> %s %s ; xsrf=%s",
        r.status_code,
        r.text[:200],
        r2.status_code,
        r2.text[:200],
        bool(xsrf),
    )
    return False


def ensure_admin_password(http_base: str, container_id: str | None = None) -> str:
    """Return a working admin HTTP password (existing env or newly set)."""
    env_pw = os.environ.get("GERRIT_IT_HTTP_ADMIN_PASS")
    if env_pw:
        try:
            GerritHttpSession(http_base, user="admin", password=env_pw).get_json("accounts/self")
            logger.info("Using GERRIT_IT_HTTP_ADMIN_PASS for admin REST")
            return env_pw
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.debug("GERRIT_IT_HTTP_ADMIN_PASS did not work: %s", e)

    new_pw = env_pw or f"it-admin-{os.urandom(6).hex()}"
    dev_session = _materialize_account_session(http_base, account_id=1000000)

    if container_id:
        logger.debug("container_id=%s provided; bootstrap uses external REST only", container_id[:12])

    if not try_put_http_password_cookie(dev_session, http_base, new_pw):
        raise RuntimeError(
            "Could not set admin HTTP password via development session. "
            "Ensure DEVELOPMENT_BECOME_ANY_ACCOUNT=true for the integration container, "
            "or set GERRIT_IT_HTTP_ADMIN_PASS to a known working password.",
        )

    try:
        GerritHttpSession(http_base, user="admin", password=new_pw).get_json("accounts/self")
        logger.info("Set and verified admin HTTP password via development session")
        return new_pw
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.debug("verify new admin password failed: %s", e)

    raise RuntimeError(
        "Could not verify admin HTTP password after development-session bootstrap. "
        "Set GERRIT_IT_HTTP_ADMIN_PASS to a working admin HTTP password.",
    )
