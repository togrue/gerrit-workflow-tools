#! /bin/bash
set -euo pipefail

uv run ruff format .
uv run ruff check --fix .

uv run mypy src
uv run mypy tests
