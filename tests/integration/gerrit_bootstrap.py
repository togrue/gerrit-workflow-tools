"""Bootstrap HTTP credentials for Gerrit (development image + REST)."""

from __future__ import annotations

import logging
import os
import subprocess
import urllib.parse

import requests

from tests.integration.gerrit_http import GerritHttpSession

logger = logging.getLogger(__name__)


def _materialize_admin_session(http_base: str) -> requests.Session:
    """Hit the development login URL so account 1000000 exists in the session cookie jar."""
    s = requests.Session()
    login = f"{http_base.rstrip('/')}/login/{urllib.parse.quote('#/', safe='')}?account_id=1000000"
    try:
        s.get(login, timeout=30)
    except OSError as e:
        logger.debug("materialize admin login GET failed (may be ok): %s", e)
    return s


def try_put_http_password_digest(
    http_base: str,
    *,
    username: str,
    password_guess: str,
    new_password: str,
) -> bool:
    """Try to set HTTP password using digest auth. Returns True if successful."""
    from requests.auth import HTTPDigestAuth

    url = f"{http_base.rstrip('/')}/a/accounts/self/password.http"
    r = requests.put(
        url,
        json={"http_password": new_password},
        auth=HTTPDigestAuth(username, password_guess),
        headers={"Content-Type": "application/json", "Accept": "*/*"},
        timeout=60,
    )
    if r.status_code in (200, 204):
        return True
    logger.debug(
        "password PUT user=%r guess_len=%d -> %s %s",
        username,
        len(password_guess),
        r.status_code,
        r.text[:200],
    )
    return False


def bootstrap_admin_via_container_exec(container_id: str, new_password: str) -> None:
    """Run curl inside the container to set HTTP password on loopback (digest, empty admin password)."""
    # Escape password for sh single-quoted JSON — use hex-only passwords
    inner = (
        "curl -sf -X PUT -H 'Content-Type: application/json' "
        + '-d \'{"http_password":"'
        + new_password.replace("'", "'\\''")
        + "\"}' "
        + "--digest -u admin: http://127.0.0.1:8080/a/accounts/self/password.http"
    )
    subprocess.run(
        ["docker", "exec", container_id, "sh", "-c", inner],
        check=True,
        timeout=120,
    )


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
    _materialize_admin_session(http_base)

    for guess in ("", "admin", "Admin", "secret"):
        if try_put_http_password_digest(http_base, username="admin", password_guess=guess, new_password=new_pw):
            logger.info("Set admin HTTP password via digest (guess len=%d)", len(guess))
            try:
                GerritHttpSession(http_base, user="admin", password=new_pw).get_json("accounts/self")
                return new_pw
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.debug("verify new password failed: %s", e)

    if container_id:
        logger.info("Trying docker exec curl to set admin HTTP password")
        bootstrap_admin_via_container_exec(container_id, new_pw)
        GerritHttpSession(http_base, user="admin", password=new_pw).get_json("accounts/self")
        return new_pw

    raise RuntimeError(
        "Could not bootstrap admin HTTP password. Install Docker with a reachable daemon, "
        "or set GERRIT_IT_HTTP_ADMIN_PASS to a working admin HTTP password.",
    )
