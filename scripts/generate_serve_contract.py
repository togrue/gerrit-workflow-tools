#!/usr/bin/env python3
"""Regenerate the ``ger serve`` contract's TypeScript types and JSON Schema from server/contract.py."""

from __future__ import annotations

from pathlib import Path

from gerrit_workflow_tools.server.contract_export import json_schema_text, typescript


ROOT = Path(__file__).resolve().parents[1]
TYPESCRIPT = ROOT / "vscode" / "src" / "contract.ts"
JSON_SCHEMA = ROOT / "docu" / "spec" / "serve-contract.schema.json"


def main() -> None:
    for path, text in ((TYPESCRIPT, typescript()), (JSON_SCHEMA, json_schema_text())):
        # newline="\n" keeps LF on Windows (default text mode would write CRLF).
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"Wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
