# CLAUDE.md

**Read [AGENTS.md](AGENTS.md) first** — it holds the mindset, the structure, the build
commands and the pointers to detailed docs. Everything there applies here.

Claude-specific notes only:

- Run unit tests with the pytest-on-remote script in `scripts/` when this checkout has it
  (it is personal and not in the repository; see "Build and check" in AGENTS.md). It
  executes on a Linux remote and is much faster than running them on this Windows box.
  Tracebacks will show Linux paths (`/home/...`), not `D:\...`.
- `./check.sh` runs ruff format, ruff check --fix and mypy over `src` and `tests`.
