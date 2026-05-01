#!/usr/bin/env python3
"""Run integration tests (Docker + real Gerrit). See tests/integration/README.md."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--keep",
        action="store_true",
        help="Set GERRIT_IT_KEEP_CONTAINER=1 so the Gerrit container is left running after the run.",
    )
    p.add_argument(
        "--env-file",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Load KEY=value settings into the environment before pytest. "
            "Default: tests/integration/local.env if that file exists (gitignored)."
        ),
    )
    p.add_argument(
        "pytest_args",
        nargs=argparse.REMAINDER,
        help="Forwarded to pytest (use -- for options, e.g. scripts/run_integration.py -- -k lifecycle).",
    )
    args = p.parse_args()

    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from tests.integration.docker_gerrit import set_docker_host_from_env
    from tests.integration.load_local_env import load_local_env_file

    env_file = args.env_file if args.env_file is not None else root / "tests" / "integration" / "local.env"
    load_local_env_file(env_file)
    set_docker_host_from_env()

    env = os.environ.copy()
    if args.keep:
        env["GERRIT_IT_KEEP_CONTAINER"] = "1"

    # Print a small config summary (non-secret).
    print("Gerrit integration tests")
    print(f"  DOCKER_HOST={env.get('DOCKER_HOST', '(default)')}")
    print(f"  GERRIT_IT_PUBLIC_HOST={env.get('GERRIT_IT_PUBLIC_HOST', 'localhost')}")
    print(f"  GERRIT_IT_HOST_PORT_HTTP={env.get('GERRIT_IT_HOST_PORT_HTTP', '8080')}")
    print(f"  GERRIT_IT_HOST_PORT_SSH={env.get('GERRIT_IT_HOST_PORT_SSH', '29418')}")
    print(f"  GERRIT_IT_KEEP_CONTAINER={env.get('GERRIT_IT_KEEP_CONTAINER', '0')}")
    if env_file.is_file():
        print(f"  (loaded {env_file})")
    print()

    pytest_args = list(args.pytest_args)
    if pytest_args and pytest_args[0] == "--":
        pytest_args = pytest_args[1:]

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(root / "tests" / "integration"),
        *pytest_args,
    ]
    print("Running:", " ".join(cmd))
    return subprocess.call(cmd, cwd=root, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
