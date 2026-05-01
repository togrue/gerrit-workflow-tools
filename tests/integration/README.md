# Integration tests (Docker + Gerrit)

These tests start the official image **`gerritcodereview/gerrit:3.10`**, create projects and accounts, then run **`ger push`**, **`ger log`**, and **`ger show`** against a real server.

Unit tests (`pytest` with default config) **ignore** this directory; run integration tests explicitly.

## Prerequisites

- Docker daemon reachable from the machine running pytest (local Docker Desktop, WSL2 Docker, or remote via `DOCKER_HOST`).
- Optional: `curl` or `wget` on `PATH` (to install the Gerrit `commit-msg` hook).
- Python deps: **`uv sync --group integration`** (installs `docker[ssh]`, `requests`, etc.).

## Personal machine settings (not committed)

Copy [`local.env.example`](local.env.example) to **`tests/integration/local.env`** and edit it. That path is **gitignored**; use it for your own hostnames, ports, and `GERRIT_IT_DOCKER_HOST` (e.g. `ssh://lenovo` when your `~/.ssh/config` has `Host lenovo`).

`scripts/run_integration.py` and `pytest tests/integration` both load `tests/integration/local.env` automatically when the file exists. Override the path with **`--env-file PATH`** on the runner.

**Important:** `GERRIT_IT_HOST_PORT_HTTP` / `GERRIT_IT_HOST_PORT_SSH` are the **host** ports for the **test** container. They must be **free** on the Docker machine. If you already run another Gerrit on 8081, use e.g. **8082** and **29419** in `local.env` and open those in the firewall from the PC that runs `git`/`ger`.

Example (Lenovo runs Docker; your PC uses HTTP/git against `lenovo-pc`):

```bash
GERRIT_IT_PUBLIC_HOST=lenovo-pc
GERRIT_IT_HOST_PORT_HTTP=8082
GERRIT_IT_HOST_PORT_SSH=29419
GERRIT_IT_DOCKER_HOST=ssh://lenovo
```

Resolve `lenovo-pc` (DNS or `hosts`). From the pytest machine: `docker -H ssh://lenovo ps` (or your chosen URL) must work. If **`ssh lenovo docker ps`** works but **pytest** still cannot talk to Docker on Windows, set `GERRIT_IT_DOCKER_HOST=ssh://YOUR_USER@lenovo-pc` (explicit user and hostname; docker-py may not honor every OpenSSH `Host` alias).

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `GERRIT_IT_DOCKER_HOST` | *(unset)* | Copied to `DOCKER_HOST` (e.g. `ssh://user@host` for a remote engine). |
| `GERRIT_IT_PUBLIC_HOST` | `localhost` | Hostname **your git client** uses to reach Gerrit HTTP (use with SSH `-L` when the daemon is remote). |
| `GERRIT_IT_HOST_PORT_HTTP` | `8080` | Published HTTP port on the Docker **host**. |
| `GERRIT_IT_HOST_PORT_SSH` | `29418` | Published SSH port on the Docker **host**. |
| `GERRIT_IT_IMAGE` | `gerritcodereview/gerrit:3.10` | Override Gerrit image. |
| `GERRIT_IT_KEEP_CONTAINER` | `0` | If `1`, the named container is left running after the run (faster reruns). |
| `GERRIT_IT_HTTP_ADMIN_PASS` | *(unset)* | If set, used as the admin HTTP password when REST bootstrap works. |
| `GERRIT_IT_RUN_ID` | *(random)* | Suffix for project names (stable ID when debugging). |

## How to run

```bash
uv sync --group integration
uv run --group integration python scripts/run_integration.py
# or:
uv run --group integration pytest tests/integration -q
# with keep:
uv run --group integration python scripts/run_integration.py --keep
```

Remote Docker host with local port forward (example):

```bash
ssh -L 8080:localhost:8080 -L 29418:localhost:29418 user@buildhost
export GERRIT_IT_PUBLIC_HOST=localhost
uv run --group integration pytest tests/integration -q
```

## Container name

Tests use a fixed container name: **`gerrit-workflow-tools-integration`**. Remove it manually if it conflicts: `docker rm -f gerrit-workflow-tools-integration`.

## Runtime

First startup can take **2–4 minutes**. Use `GERRIT_IT_KEEP_CONTAINER=1` for repeated runs.

## Troubleshooting

| Symptom | Things to check |
|--------|------------------|
| Port / bind errors when starting the container | Another process (including a non-test Gerrit) is using `GERRIT_IT_HOST_PORT_HTTP` or `_SSH` on the Docker host. Pick unused ports and update `local.env`. |
| `docker ps` over SSH works; pytest still fails Docker on Windows | Use `GERRIT_IT_DOCKER_HOST=ssh://user@real-hostname`. Confirm firewall allows your PC → those TCP ports on the Docker host. |
| HTTP / git clone timeouts from your PC | `GERRIT_IT_PUBLIC_HOST` must be reachable from the machine running pytest (not only `localhost` on the remote). |
