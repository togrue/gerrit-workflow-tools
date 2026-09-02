# CLAUDE.md

**Read [AGENTS.md](AGENTS.md) first** — it holds the mindset, the structure, the build
commands and the pointers to detailed docs. Everything there applies here.

Claude-specific notes only:

- Run unit tests with `./scripts/sync-and-test-lenovo.sh` — it executes on a Linux
  remote and is much faster than running them on this Windows box. Tracebacks will show
  Linux paths (`/home/...`), not `D:\...`.
- `./check.sh` runs ruff format + check --fix; run `uv run mypy` separately.
