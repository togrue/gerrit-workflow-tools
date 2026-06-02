"""Pytest fixtures: Docker Gerrit, seeded projects, and clone paths."""

from __future__ import annotations

import logging
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.integration.docker_gerrit import (
    DEFAULT_IMAGE,
    set_docker_host_from_env,
    start_gerrit_container,
    stop_container,
    wait_http_ready,
)
from tests.integration.gerrit_bootstrap import ensure_admin_password
from tests.integration.gerrit_http import GerritHttpSession
from tests.integration.gerrit_seed import (
    add_verified_label_to_project_meta,
    create_account,
    create_project,
    delete_project,
    grant_registered_users_branch_create,
    seed_repo_with_branches,
)
from tests.integration.load_local_env import load_local_env_file
from tests.integration.profile import format_report, phase, profiling_enabled, reset

logger = logging.getLogger(__name__)


def pytest_configure(config: pytest.Config) -> None:
    """Apply gitignored ``local.env`` before collection (same keys as ``run_integration.py``)."""
    load_local_env_file(Path(__file__).resolve().parent / "local.env")
    set_docker_host_from_env()
    if profiling_enabled():
        reset()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if profiling_enabled():
        report = format_report()
        if report:
            tr = session.config.pluginmanager.get_plugin("terminalreporter")
            if tr is not None:
                tr.write_line(report)
            else:
                print(report, flush=True)


@dataclass(frozen=True)
class GerritDockerSession:
    """Gerrit container is up and HTTP answers (no projects or admin seeding)."""

    http_base: str
    public_host: str
    host_http_port: int
    host_ssh_port: int
    container: object | None


@dataclass(frozen=True)
class GerritIntegrationContext:
    """Shared session state for integration tests."""

    run_id: str
    http_base: str
    public_host: str
    host_http_port: int
    host_ssh_port: int
    admin_user: str
    admin_password: str
    dev_user: str
    dev_password: str
    rev_alt_user: str
    rev_alt_password: str
    project_verified: str
    project_plain: str
    seed_repo_verified: Path
    seed_repo_plain: Path
    container: object | None


def _fail(msg: str) -> None:
    pytest.fail(msg)


def _docker_ping_or_fail() -> None:
    try:
        import docker  # pylint: disable=import-outside-toplevel

        docker.from_env().ping()
    except Exception as e:  # pylint: disable=broad-exception-caught
        _fail(
            f"Docker is not reachable (DOCKER_HOST={os.environ.get('DOCKER_HOST', '(default)')}): {e}. "
            "Use `uv sync --group integration` and ensure the Docker daemon is running.",
        )


@pytest.fixture(scope="session")
def gerrit_docker_session() -> GerritDockerSession:
    """Start the Gerrit container and wait until HTTP serves ``/config/server/version``."""
    pytest.importorskip("docker")
    pytest.importorskip("requests")

    set_docker_host_from_env()
    with phase("docker: ping"):
        _docker_ping_or_fail()

    public_host = os.environ.get("GERRIT_IT_PUBLIC_HOST", "localhost")
    host_http = int(os.environ.get("GERRIT_IT_HOST_PORT_HTTP", "8080"))
    host_ssh = int(os.environ.get("GERRIT_IT_HOST_PORT_SSH", "29418"))
    image = os.environ.get("GERRIT_IT_IMAGE", DEFAULT_IMAGE)
    keep = os.environ.get("GERRIT_IT_KEEP_CONTAINER", "").lower() in ("1", "true", "yes")

    with phase("docker: start or reuse container"):
        container, published = start_gerrit_container(
            image=image,
            public_host=public_host,
            host_http_port=host_http,
            host_ssh_port=host_ssh,
            keep=keep,
        )

    http_base = f"http://{public_host}:{published.http}"
    with phase("docker: wait HTTP + REST ready"):
        wait_http_ready(http_base, timeout_s=240.0)

    ctx = GerritDockerSession(
        http_base=http_base,
        public_host=public_host,
        host_http_port=published.http,
        host_ssh_port=published.ssh,
        container=container,
    )

    yield ctx

    if not keep and container is not None:
        stop_container(container, remove=True)


@pytest.fixture(scope="session")
def gerrit_integration_context(
    gerrit_docker_session: GerritDockerSession,
    tmp_path_factory: pytest.TempPathFactory,
) -> GerritIntegrationContext:
    """Bootstrap admin, seed two projects and template repos (reuses ``gerrit_docker_session``)."""
    pytest.importorskip("docker")
    pytest.importorskip("requests")

    http_base = gerrit_docker_session.http_base
    public_host = gerrit_docker_session.public_host
    host_http = gerrit_docker_session.host_http_port
    host_ssh = gerrit_docker_session.host_ssh_port
    container = gerrit_docker_session.container

    container_id: str | None = None
    if container is not None:
        cid = getattr(container, "id", None)
        container_id = str(cid) if cid else None

    run_id = os.environ.get("GERRIT_IT_RUN_ID") or secrets.token_hex(4)

    with phase("seed: admin HTTP password"):
        admin_pw = ensure_admin_password(http_base, container_id=container_id)
    admin_session = GerritHttpSession(http_base, user="admin", password=admin_pw)

    dev_pw = f"dev-{secrets.token_hex(8)}"
    dev_user = f"u_{run_id}"
    with phase("seed: create dev account"):
        create_account(
            admin_session,
            dev_user,
            email=f"{dev_user}@example.com",
            http_password=dev_pw,
        )

    rev_alt_user = f"rev_alt_{run_id}"
    rev_alt_password = f"pw-{secrets.token_hex(8)}"
    with phase("seed: create rev_alt account"):
        create_account(
            admin_session,
            rev_alt_user,
            email=f"{rev_alt_user}@example.com",
            http_password=rev_alt_password,
        )

    pv = f"it_v_{run_id}"
    pn = f"it_nv_{run_id}"

    with phase("seed: projects + ACLs"):
        for name in (pv, pn):
            delete_project(admin_session, name)
        create_project(admin_session, pv)
        create_project(admin_session, pn)
        grant_registered_users_branch_create(admin_session, pv)
        grant_registered_users_branch_create(admin_session, pn)

    work_root = tmp_path_factory.mktemp("gerrit_seed")
    with phase(f"seed: git branches ({pv})"):
        seed_v = seed_repo_with_branches(
            work_root=work_root,
            http_base=http_base,
            admin_user="admin",
            admin_password=admin_pw,
            project=pv,
            branches=("main", "dev", "hotfix_123"),
        )
    with phase(f"seed: git branches ({pn})"):
        seed_p = seed_repo_with_branches(
            work_root=work_root,
            http_base=http_base,
            admin_user="admin",
            admin_password=admin_pw,
            project=pn,
            branches=("main", "dev", "hotfix_123"),
        )

    with phase("seed: Verified label REST"):
        add_verified_label_to_project_meta(
            repo_dir=seed_v,
            http_base=http_base,
            admin_user="admin",
            admin_password=admin_pw,
            project=pv,
        )

    return GerritIntegrationContext(
        run_id=run_id,
        http_base=http_base,
        public_host=public_host,
        host_http_port=host_http,
        host_ssh_port=host_ssh,
        admin_user="admin",
        admin_password=admin_pw,
        dev_user=dev_user,
        dev_password=dev_pw,
        rev_alt_user=rev_alt_user,
        rev_alt_password=rev_alt_password,
        project_verified=pv,
        project_plain=pn,
        seed_repo_verified=seed_v,
        seed_repo_plain=seed_p,
        container=container,
    )


@pytest.fixture
def gerrit_admin_session(gerrit_integration_context: GerritIntegrationContext) -> GerritHttpSession:
    return GerritHttpSession(
        gerrit_integration_context.http_base,
        user=gerrit_integration_context.admin_user,
        password=gerrit_integration_context.admin_password,
    )


@pytest.fixture
def gerrit_dev_session(gerrit_integration_context: GerritIntegrationContext) -> GerritHttpSession:
    return GerritHttpSession(
        gerrit_integration_context.http_base,
        user=gerrit_integration_context.dev_user,
        password=gerrit_integration_context.dev_password,
    )


@pytest.fixture(autouse=True)
def _profile_test_duration(request: pytest.FixtureRequest) -> None:
    """Record per-test body time when ``GERRIT_IT_PROFILE=1``."""
    if not profiling_enabled():
        yield
        return
    from tests.integration.profile import record  # pylint: disable=import-outside-toplevel

    t0 = time.monotonic()
    yield
    name = request.node.nodeid.split("::", 1)[-1]
    record(f"test: {name}", time.monotonic() - t0)


pytestmark = pytest.mark.integration
