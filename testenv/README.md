# CI test bench: Gerrit + Jenkins

A feedback loop for `ger` development: a Jenkins next to an existing Gerrit that votes
`Verified` on a tiny demo project, so `ger log`, `ger inbox` etc. see real CI results.

| Piece | Notes |
|-------|-------|
| Gerrit | Your existing Gerrit in Docker (tested with 3.13) |
| Jenkins | LTS + Python/pytest + Gerrit Trigger, in `jenkins/`, same Docker network as Gerrit |
| Demo project | Gerrit project `ci-demo`: Python + `pytest`, builds in under a second |

## Configure
Copy the `TESTENV_*` block of [`tests/integration/local.env.example`](../tests/integration/local.env.example)
into `tests/integration/local.env` (gitignored) and edit it: the ssh host running Docker, the
hostname you reach Gerrit by, the Jenkins admin password, optional ports and Docker network.

## One-time setup
1. `testenv/deploy.sh` copies `jenkins/` to the Docker host, writes its `.env`, generates an
   ssh key pair for Jenkins there and starts the container. It prints the public key.
2. In Gerrit (as admin):
   - create the user: `ssh -p 29418 <gerrit> gerrit create-account --group "Service Users" --ssh-key - jenkins < key.pub`
   - create the project: `gerrit create-project ci-demo --empty-commit`
   - on `All-Projects` (`refs/meta/config`, via review) let *Service Users* `read` on `refs/*`
     and vote `label-Verified = -1..+1` on `refs/heads/*`. The `Verified` label and
     `streamEvents` for *Service Users* already exist in a stock 3.x `All-Projects`; if not,
     add them.
3. Push a demo project to `ci-demo` (a `demo/calc.py` with `add`/`divide` and
   `tests/test_calc.py` works; anything `python3 -m pytest` can run does) and submit it.

## Use
```bash
./testenv/ci_loop.sh       # push a harmless and a breaking change via ger, wait, print ger log
```
Expected: the harmless change shows `v+1`, the breaking one `v-1  # build failed`.

Missed a build (patchset pushed while Jenkins was down)? Comment `recheck`:
`ssh -p 29418 <gerrit> gerrit review -m recheck <patchset-sha>`.

## How it is wired
- Job `ci-demo` (`jenkins/casc/jobs.groovy`): on patchset-created or a `recheck` comment, fetch
  the patchset, run `python3 -m pytest`, vote `Verified +1/-1` (Gerrit Trigger default).
- The Gerrit server is registered by `jenkins/init.groovy.d/gerrit-server.groovy`
  (SSH `stream-events`); everything else is Jenkins Configuration as Code in `jenkins/casc/`.

## Gotchas
- Branch patterns in the Job DSL need `ant:**`; `plain:**` silently matches nothing.
- `casc/` changes need a Jenkins restart (`deploy.sh` does it); the job is rebuilt from
  `jobs.groovy`.
- `ger push` pushes the whole local stack; already-pushed changes are no-ops.
