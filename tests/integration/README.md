# Integration tests (Docker + Gerrit)

These tests start the official image **`gerritcodereview/gerrit:3.10`** (or `GERRIT_IT_IMAGE`), create projects and accounts, then run `ger` commands against a real server. They cover what a real Gerrit decides and the in-memory `ChangeStore` of the unit tests cannot: relation chains, patchsets Gerrit makes itself, push rejections, permissions, HTTP failures, and one Change-Id on several branches.

Gerrit's event stream over real ssh is **`test_19_event_stream.py`**: it uploads a throwaway ssh key (generated in a temp directory; your `~/.ssh` is neither read nor written) for two test accounts, checks that the one without the *Stream Events* capability is refused once and quietly, grants the capability to a temporary group for the other (and removes the grant afterwards), and follows its events. It needs OpenSSH (`ssh`, `ssh-keygen`) on `PATH` and the container's SSH port (`GERRIT_IT_HOST_PORT_SSH`) reachable from your machine; without OpenSSH it is skipped.

Unit tests (`pytest` with default config) **ignore** this directory; run integration tests explicitly.

## What is covered where

| File | Covers |
|------|--------|
| `test_00_smoke_http.py`, `test_02_seeding.py` | The container answers; seeded branches exist |
| `test_03_push.py` | `ger push`: the ready boundary stops at a `wip` commit, `--all`, branches stay isolated |
| `test_04_review_matrix.py` | Every Code-Review × Verified vote pair as `ger log` shows it |
| `test_05_commands.py`, `test_07_command_smoke.py` | `ger log`, `ger show`, `ger push --dry-run` smoke |
| `test_06_attention_labels.py` | `ger log` attention notes and JSON reasons per review state, chain blocking, abandoned |
| `test_08_set_topic.py` | Setting and clearing a topic over REST |
| `test_09_change_resolution.py` | `ger log` / `ger show` / `ger resolve` agree on cross-branch Change-Ids and after a push; batch query budget |
| `test_10_comment_cache.py` | `ger show` / `ger log` see a comment resolved on Gerrit |
| `test_11_draft_replies.py` | `ger reply` drafts; publishing or keeping them with `ger push` |
| `test_12_review_and_comment.py` | `ger review` staging, chain walk and votes; `ger comment` |
| `test_13_checkout.py` | `ger checkout` of a pushed change and its new patchset; one Change-Id on two branches |
| `test_14_cross_branch_siblings.py` | One Change-Id on `main` and `dev`: `ger log` shows each branch its own change, votes and comments; a bare Change-Id is narrowed or refused (exit 4); triplet and `change:N` name either, and `ger show` of the other branch's sibling shows that change, not the local commit; `ger fix` by Change-Id, triplet or number; `ger push` renews only its branch's change; an abandoned or merged sibling leaves the other alone |
| `test_15_change_states.py` | WIP and `--ready`; private with topic and reviewers; a web-editor patchset and a Gerrit rebase show as outdated and survive a push; pushes Gerrit rejects (abandoned change, no upload permission) exit non-zero with Gerrit's reason |
| `test_16_rewrite_and_repush.py` | `ger push REV`, then the rest, then nothing to push; `ger fix` / `ger reword` / `ger edit` round trip renews only the touched change and those above, Change-Ids kept; a reordered stack |
| `test_17_gerrit_failures.py` | Wrong token (HTTP 401) and an unreachable host for `ger log`, `ger show`, `ger edit -n`, `ger push --dry-run`: one line, exit 5 (push preview: one warning, exit 0), local state shown |
| `test_18_serve.py` | `ger serve` `/stack` and `/overview` agree with `ger log --json`; the outgoing chain comes from Gerrit's relation data |
| `test_reviewer_strategies_rest.py` | `push` / `lazy` / `overwrite` reviewer strategies |

### Writing a test here

Build on `integration_helpers.py` (`prepare_topic_repo`, `push_sibling_changes`, `ger_push`, `ger_json`, `log_commits_by_change_id`, `current_patchset`, REST actions such as `submit_change`, `publish_web_edit`, `rebase_change_on_gerrit`), `repo_builder.py` and `gerrit_seed.py` rather than copying setup. Every clone gets its own `gerrit.webUrl`, user and token from the fixtures; never rely on global git configuration. A module-scoped fixture that runs `ger` must set its own `XDG_CACHE_HOME` (the per-test one does not exist yet), as `test_14_cross_branch_siblings.py` and `test_17_gerrit_failures.py` do. After changing state on Gerrit over REST, call `force_zero_change_trust_window(monkeypatch)` so `ger` does not answer from its cache.

## Prerequisites

- Docker daemon reachable from the machine running pytest (local Docker Desktop, WSL2 Docker, or remote via `DOCKER_HOST`).
- Optional: `curl` or `wget` on `PATH` (to install the Gerrit `commit-msg` hook).
- Python deps: **`uv sync --group integration`** (installs `docker[ssh]`, `requests`, etc.).

## Personal machine settings (not committed)

Copy [`local.env.example`](local.env.example) to **`tests/integration/local.env`** and edit it. That path is **gitignored**; use it for your own hostnames, ports, and `GERRIT_IT_DOCKER_HOST` (e.g. `ssh://lenovo` when your `~/.ssh/config` has `Host lenovo`).

`pytest tests/integration` (and `scripts/run_integration_tests.sh`, which wraps it) loads `tests/integration/local.env` automatically when the file exists.

**Important:** `GERRIT_IT_HOST_PORT_HTTP` / `GERRIT_IT_HOST_PORT_SSH` are the **host** ports for the **test** container. They must be **free** on the Docker machine. If you already run another Gerrit on **8081** and **29418**, pick unused ports for the test instance (e.g. **8082** and **29419**) in `local.env` and open those in the firewall from the PC that runs `git`/`ger`. Integration tests use **HTTP** for Git; the SSH mapping is still required so the container can start without a host port conflict.

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
| `GERRIT_IT_HOST_PORT_SSH` | `29418` | Host port mapped to the container’s Gerrit SSH (**29418/tcp** inside the image). **Must be free** on the Docker host—if another Gerrit already uses **29418**, set e.g. **`29419`**. |
| `GERRIT_IT_IMAGE` | `gerritcodereview/gerrit:3.10` | Override Gerrit image. |
| *(container default)* | `DEVELOPMENT_BECOME_ANY_ACCOUNT=true` | Set automatically for the test container to make admin/account bootstrap reliable on newer Gerrit images. |
| `GERRIT_IT_KEEP_CONTAINER` | `0` | If `1`, the named container is left running after the run (faster reruns). |
| `GERRIT_IT_HTTP_ADMIN_PASS` | *(unset)* | If set, used as the admin HTTP password when REST bootstrap works. |
| `GERRIT_IT_RUN_ID` | *(random)* | Suffix for project names (stable ID when debugging). |

## How to run

```bash
uv sync --group integration
uv run --group integration python scripts/run_integration.py
# or:
uv run --group integration pytest tests/integration -q
# Minimal smoke only (Docker up + HTTP /config/server/version; no project seeding):
uv run --group integration pytest tests/integration/test_00_smoke_http.py -q
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

With a warm container, a full run is typically **~5 minutes** (about 3.5 before files 14–18; was ~6+ before batching the review matrix, caching the commit-msg hook, and skipping redundant `git fetch` after copying seed repos).

### Profiling a run

```bash
GERRIT_IT_PROFILE=1 uv run --group integration pytest tests/integration -q --durations=15
```

Prints per-phase timings (Docker, session seed, each `prepare_topic_repo`, each test body) plus pytest’s slowest tests. With a **warm** container, session setup is often ~30–40s; most wall time is **per-test** `ger push` / REST against `lenovo-pc` (or your host).

## Troubleshooting

| Symptom | Things to check |
|--------|------------------|
| Port / bind errors when starting the container | Another process (including a non-test Gerrit) is using `GERRIT_IT_HOST_PORT_HTTP` or `GERRIT_IT_HOST_PORT_SSH` on the Docker host. Pick unused ports and update `local.env`. |
| `docker ps` over SSH works; pytest still fails Docker on Windows | Use `GERRIT_IT_DOCKER_HOST=ssh://user@real-hostname`. Confirm firewall allows your PC → those TCP ports on the Docker host. |
| HTTP / git clone timeouts from your PC | `GERRIT_IT_PUBLIC_HOST` must be reachable from the machine running pytest (not only `localhost` on the remote). |

## CI test bench (Jenkins)

For exercising `ger` against real CI results (`Verified` votes), see [`testenv/README.md`](../../testenv/README.md). It reuses `local.env` (the `TESTENV_*` block).
