"""Start and stop the official Gerrit Docker image for integration tests."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from docker.models.containers import Container

logger = logging.getLogger(__name__)

DEFAULT_IMAGE = "gerritcodereview/gerrit:3.14.0-rc5-ubuntu24"
CONTAINER_NAME = "gerrit-workflow-tools-integration"


@dataclass(frozen=True)
class PublishedPorts:
    """Host ports published for HTTP and SSH."""

    http: int
    ssh: int


def _host_port_from_binding(binding: list[dict[str, str]] | None) -> int | None:
    if not binding:
        return None
    raw = binding[0].get("HostPort")
    return int(raw) if raw else None


def _container_published_ports(container: object) -> PublishedPorts | None:
    """Read host ports for 8080/tcp and 29418/tcp from inspect data.

    Over SSH, ``NetworkSettings.Ports`` is often empty while ``HostConfig.PortBindings`` is
    still populated — reading only the former made stale containers look port-less.
    """
    try:
        c: Container = container  # type: ignore[assignment]
        c.reload()
        ns_ports = (c.attrs.get("NetworkSettings") or {}).get("Ports") or {}
        http_port = _host_port_from_binding(ns_ports.get("8080/tcp"))
        ssh_port = _host_port_from_binding(ns_ports.get("29418/tcp"))
        if http_port is None or ssh_port is None:
            pb = (c.attrs.get("HostConfig") or {}).get("PortBindings") or {}
            http_port = http_port or _host_port_from_binding(pb.get("8080/tcp"))
            ssh_port = ssh_port or _host_port_from_binding(pb.get("29418/tcp"))
        if http_port is None or ssh_port is None:
            return None
        return PublishedPorts(http=http_port, ssh=ssh_port)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.debug("Could not read published ports: %s", e)
        return None


def _port_bindings(host_http_port: int, host_ssh_port: int) -> dict[str, tuple[str, int]]:
    """Publish container ports on all interfaces on the Docker host."""
    return {
        "8080/tcp": ("0.0.0.0", host_http_port),
        "29418/tcp": ("0.0.0.0", host_ssh_port),
    }


def wait_http_ready(http_url: str, *, timeout_s: float = 240.0, poll_s: float = 2.0) -> None:
    """Poll until Gerrit serves static config *and* the REST API on ``/a/``.

    ``/config/server/version`` often returns 200 while Jetty/Gerrit is still wiring REST and
    plugins; unauthenticated ``GET /a/accounts/self`` typically returns ``401`` once REST is
    actually accepting traffic. Until then you may see ``502``/``503`` or connection errors from
    the test host even though the version URL already works.
    """
    base = http_url.rstrip("/")
    version_url = f"{base}/config/server/version"
    rest_url = f"{base}/a/accounts/self"
    deadline = time.monotonic() + timeout_s
    last_err: str | None = None
    while time.monotonic() < deadline:
        try:
            rv = requests.get(version_url, timeout=5)
            if rv.status_code != 200:
                last_err = f"version HTTP {rv.status_code}"
                time.sleep(poll_s)
                continue
            rr = requests.get(
                rest_url,
                headers={"Accept": "*/*"},
                timeout=10,
                allow_redirects=False,
            )
            code = rr.status_code
            if code in (502, 503, 504):
                last_err = f"REST still starting (HTTP {code})"
                time.sleep(poll_s)
                continue
            if code in (401, 403, 200):
                logger.info("Gerrit HTTP + REST ready: %s", base)
                return
            if code in (301, 302, 303, 307, 308):
                logger.info("Gerrit HTTP + REST ready (redirect %s): %s", code, base)
                return
            last_err = f"REST HTTP {code}"
        except OSError as e:
            last_err = str(e)
        time.sleep(poll_s)
    raise RuntimeError(
        f"Gerrit did not become HTTP+REST ready in {timeout_s}s (last: {last_err})",
    )


def _docker_client():
    import docker  # pylint: disable=import-outside-toplevel

    return docker.from_env()


def start_gerrit_container(
    *,
    image: str,
    public_host: str,
    host_http_port: int,
    host_ssh_port: int,
    keep: bool,
) -> tuple[object, PublishedPorts]:
    """
    Run (or reuse) a Gerrit container with published HTTP and SSH ports.

    Returns (container, published_ports).
    """
    client = _docker_client()
    try:
        existing = client.containers.get(CONTAINER_NAME)
    except Exception:  # pylint: disable=broad-exception-caught
        existing = None

    if existing is not None:
        if existing.status != "running":
            existing.start()
        actual = _container_published_ports(existing)
        if actual is not None and (actual.http != host_http_port or actual.ssh != host_ssh_port):
            logger.warning(
                "Container %s publishes HTTP %s and SSH %s but local.env requests %s and %s; recreating",
                CONTAINER_NAME,
                actual.http,
                actual.ssh,
                host_http_port,
                host_ssh_port,
            )
            stop_container(existing, remove=True)
            existing = None
        elif actual is not None:
            ports = actual
            logger.info(
                "Reusing existing container %s (%s) HTTP port %s",
                CONTAINER_NAME,
                existing.short_id,
                ports.http,
            )
            return existing, ports
        elif existing is not None:
            logger.warning(
                "Container %s has no readable port bindings; recreating with HTTP %s SSH %s",
                CONTAINER_NAME,
                host_http_port,
                host_ssh_port,
            )
            stop_container(existing, remove=True)
            existing = None

    canonical = f"http://{public_host}:{host_http_port}"
    logger.info(
        "Starting Gerrit container %s image=%s ports %s->8080 %s->29418",
        CONTAINER_NAME,
        image,
        host_http_port,
        host_ssh_port,
    )
    try:
        container = client.containers.run(
            image,
            detach=True,
            name=CONTAINER_NAME,
            hostname="gerrit",
            ports=_port_bindings(host_http_port, host_ssh_port),
            environment={
                "CANONICAL_WEB_URL": canonical,
                "DEVELOPMENT_BECOME_ANY_ACCOUNT": "true",
            },
            remove=False,
        )
    except Exception as e:  # pylint: disable=broad-exception-caught
        hint = _container_start_failure_hint(host_http_port, host_ssh_port, e)
        raise RuntimeError(
            f"Failed to start Gerrit test container ({CONTAINER_NAME}): {e}\n{hint}",
        ) from e
    ports = PublishedPorts(http=host_http_port, ssh=host_ssh_port)
    return container, ports


def stop_container(container: object | None, *, remove: bool) -> None:
    if container is None:
        return
    try:
        c: Container = container  # type: ignore[assignment]
        c.stop(timeout=10)
        if remove:
            c.remove()
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning("Could not stop/remove container: %s", e)


def tail_logs(container: object, *, n: int = 80) -> str:
    if container is None:
        return ""
    try:
        c: Container = container  # type: ignore[assignment]
        raw = c.logs(tail=n)
        return raw.decode("utf-8", errors="replace")
    except Exception as e:  # pylint: disable=broad-exception-caught
        return f"(could not read logs: {e})"


def set_docker_host_from_env() -> None:
    """If ``GERRIT_IT_DOCKER_HOST`` is set, copy it to ``DOCKER_HOST`` for docker-py."""
    v = os.environ.get("GERRIT_IT_DOCKER_HOST")
    if v:
        os.environ["DOCKER_HOST"] = v


def _container_start_failure_hint(http_port: int, ssh_port: int, exc: BaseException) -> str:
    msg = str(exc).lower()
    lines = [
        "Hints:",
        f"  - Host ports {http_port} (HTTP) and {ssh_port} (SSH) must be FREE on the Docker host "
        "for this test container.",
        "    If another Gerrit already uses 8080/8081 and 29418, set GERRIT_IT_HOST_PORT_HTTP and "
        "GERRIT_IT_HOST_PORT_SSH to other free ports (e.g. 8082 and 29419).",
        "  - These tests start their own Gerrit image; host ports are only for this test container.",
    ]
    if "port is already allocated" in msg or "address already in use" in msg or "bind" in msg:
        lines.insert(1, "  (Likely cause: port already in use on the remote host.)")
    if os.name == "nt":
        lines.append(
            "  - On Windows, if `ssh lenovo docker ps` works but pytest fails Docker, try "
            "GERRIT_IT_DOCKER_HOST=ssh://YOUR_USER@lenovo-pc (real hostname; docker-py may not honor "
            "OpenSSH Host aliases the same as the `ssh` CLI).",
        )
    return "\n".join(lines)
