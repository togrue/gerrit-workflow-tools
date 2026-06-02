# How to run tests

## Prerequisites

```bash
uv sync
```

Installs the dev group (`pytest`, `ruff`, etc.). Integration tests need an extra group (see below).

## Unit tests (default)

```bash
uv run pytest -q
```

- Collects only `tests/` **except** `tests/integration/` (`--ignore=tests/integration` in `pyproject.toml`).
- Fast; no Docker required.

Explicit unit-only (same as default):

```bash
uv run pytest -m "not integration" -q
```

## Integration tests (optional)

Requires Docker + Gerrit. See [tests/integration/README.md](../tests/integration/README.md).

```bash
uv sync --group integration
uv run --group integration pytest tests/integration -q
```

Or use the runner script:

```bash
uv run --group integration python scripts/run_integration.py
```

Run only integration-marked tests:

```bash
uv run --group integration pytest -m integration -q
```

Minimal HTTP smoke (container up, no project seeding):

```bash
uv run --group integration pytest tests/integration/test_00_smoke_http.py -q
```

## Pytest markers

| Marker | Meaning |
|--------|---------|
| `integration` | Docker + live Gerrit (`tests/integration/`) |
| `slow` | Intentionally slow unit fixtures (reserved) |

List registered markers: `uv run pytest --markers`

## Git config isolation

Unit tests replace global/system git config with a stub so your `~/.gitconfig` does not affect results (`tests/conftest.py`).

To disable isolation (debugging only):

```bash
export GERRIT_WORKFLOW_TOOLS_NO_GIT_CONFIG_ISOLATION=1
uv run pytest -q
```

## Fixture repositories

Reusable repo builders live in [tests/fixtures.py](../tests/fixtures.py):

| Helper | Purpose |
|--------|---------|
| `make_stack_repo` | Linear `feature` over `main`; commit 3 matches default stop pattern `^test!` |
| `make_repo_duplicate_change_id` | Two commits sharing one Change-Id |
| `make_repo_malformed_cid` | Invalid Change-Id footer |
| `make_gcid_cli_repo` | Three commits for `ger change-id` CLI tests |
| `make_repo_with_merged_side_branch` | Feature + merged side branch (first-parent vs full DAG) |

Session fixtures in `tests/conftest.py` copy templates per test (`stack_repo`, `dup_repo`, etc.).

## Contributing tests

### Regression tests

When fixing a bug, make intent obvious:

- Name: `test_<behavior>_regression_<short_slug>`, **or**
- Docstring: `Regression: <one sentence>.`

Link to a GitHub issue when applicable.

### CLI output assertions

- Prefer `--color=never` when asserting on stdout text (content, not ANSI).
- Tests that **verify highlighting** may use `--color=always` and assert escape codes.
- Prefer substring / regex on stable tokens (Change-Id, exit messages) over full golden files.
- Use `strip_ansi()` from [tests/helpers.py](../tests/helpers.py) when normalizing colored output.

### Spec traceability

Major command test modules should start with a comment block:

```python
# Spec: docu/spec/commands/push.md
# Covers: --dry-run, duplicate Change-Id error, ...
```

## Coverage audit (optional)

```bash
uv run python scripts/audit_test_coverage.py
```

Lists source modules with weak or missing test-file heuristics (stdout only).
