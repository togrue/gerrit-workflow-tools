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

DEFAULT_IMAGE = "gerritcodereview/gerrit:3.10"
CONTAINER_NAME = "gerrit-workflow-tools-integration"


@dataclass(frozen=True)
class PublishedPorts:
    """Host ports published for HTTP and SSH."""

    http: int
    ssh: int


def wait_http_ready(http_url: str, *, timeout_s: float = 180.0, poll_s: float = 2.0) -> None:
    """Poll ``GET {http_url}/config/server/version`` until HTTP 200."""
    deadline = time.monotonic() + timeout_s
    version_url = f"{http_url.rstrip('/')}/config/server/version"
    last_err: str | None = None
    while time.monotonic() < deadline:
        try:
            r = requests.get(version_url, timeout=5)
            if r.status_code == 200:
                logger.info("Gerrit HTTP ready: %s", version_url)
                return
            last_err = f"HTTP {r.status_code}"
        except OSError as e:
            last_err = str(e)
        time.sleep(poll_s)
    raise RuntimeError(f"Gerrit did not become ready in {timeout_s}s (last: {last_err})")


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
        ports = PublishedPorts(http=host_http_port, ssh=host_ssh_port)
        logger.info("Reusing existing container %s (%s)", CONTAINER_NAME, existing.short_id)
        return existing, ports

    canonical = f"http://{public_host}:{host_http_port}"
    logger.info(
        "Starting Gerrit container %s image=%s ports %s->8080 %s->29418",
        CONTAINER_NAME,
        image,
        host_http_port,
        host_ssh_port,
    )
    container = client.containers.run(
        image,
        detach=True,
        name=CONTAINER_NAME,
        hostname="gerrit",
        ports={
            "8080/tcp": host_http_port,
            "29418/tcp": host_ssh_port,
        },
        environment={
            "CANONICAL_WEB_URL": canonical,
        },
        remove=False,
    )
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
