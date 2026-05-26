#!/usr/bin/env python3
"""Heuristic gap finder: source modules vs test references."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "gerrit_workflow_tools"
TESTS = ROOT / "tests"


def _module_stem(path: Path) -> str:
    return path.stem


def _expected_test_name(stem: str) -> str:
    return f"test_{stem}.py"


def main() -> int:
    py_files = sorted(SRC.rglob("*.py"))
    py_files = [p for p in py_files if p.name != "__init__.py"]

    test_files = {p.name for p in TESTS.rglob("test_*.py")}
    test_text = ""
    for tp in TESTS.rglob("*.py"):
        if tp.name.startswith("test_") or tp.name in ("conftest.py", "fixtures.py", "helpers.py"):
            test_text += tp.read_text(encoding="utf-8", errors="replace") + "\n"

    print("=== Modules without matching tests/test_<stem>.py ===")
    missing_file: list[str] = []
    for path in py_files:
        stem = _module_stem(path)
        expected = _expected_test_name(stem)
        if expected not in test_files and not (TESTS / expected).exists():
            missing_file.append(str(path.relative_to(ROOT)))
    if missing_file:
        for line in missing_file:
            print(f"  {line}")
    else:
        print("  (none)")

    print("\n=== Modules with zero 'test_' references in tests/ ===")
    unreferenced: list[str] = []
    for path in py_files:
        rel = path.relative_to(SRC)
        mod = ".".join(rel.with_suffix("").parts)
        needle = mod if mod.count(".") == 0 else mod.split(".")[-1]
        if not re.search(rf"\b{re.escape(needle)}\b", test_text):
            unreferenced.append(str(path.relative_to(ROOT)))
    if unreferenced:
        for line in unreferenced:
            print(f"  {line}")
    else:
        print("  (none)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
