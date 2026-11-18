#!/usr/bin/env python3
"""Ownership-aware verdicts for the tier 2 candidates, from the tier 4 mutation results.

    verdicts.py owners --repo .                      -> owners.json   (module -> owner test files)
    verdicts.py plan  --payload P --results R        -> rerun.json    (owner-check plan for mutrun)
    verdicts.py rank  --payload P --results R --owner-results O -> report + verdicts.json

Owner of a module: the test file named after it (test_x.py owns x.py, cli_x.py, and split-off
modules such as x_rows.py or gerrit/x_rows.py), else every test file importing it directly.
``OWNER_OVERRIDES`` corrects the cases where the names mislead.

A mutant a candidate kills is guarded, in this order:
  1. by a non-candidate owner test that kills it      -> no candidate needed
  2. by candidate owner tests                          -> keep one of them (KEEP)
  3. no owner kills it: by a non-candidate bystander  -> no candidate needed
     else by candidate bystanders                      -> keep one, but it belongs in the owner's
                                                          file (MISPLACED)
A greedy set cover keeps the fewest candidates; the rest are REMOVE, or NO_KILLS when they kill
no mutant at all. The mutation run stops at the first non-candidate killer, so step 1 needs the
owner check (``plan`` + a ``MUT_RERUN`` run) wherever that first killer was a bystander.
"""

from __future__ import annotations

import argparse
import ast
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


PKG = "gerrit_workflow_tools"

# Where the name heuristic picks the wrong owner: module -> its owner test files, replacing the guess.
_CI_FINDINGS_OWNERS = ["tests/test_ci_findings.py", "tests/test_ci_findings_tools.py"]
OWNER_OVERRIDES: dict[str, list[str]] = {
    "core/ci_findings.py": _CI_FINDINGS_OWNERS,
    "core/ci_findings_blocks.py": _CI_FINDINGS_OWNERS,
    "core/ci_findings_located.py": _CI_FINDINGS_OWNERS,
    "core/ci_findings_tests.py": _CI_FINDINGS_OWNERS,
    "core/ci_findings_tests_jvm.py": _CI_FINDINGS_OWNERS,
    "core/config_editor_file.py": ["tests/test_config_form.py"],
    "push_options_history.py": ["tests/test_push.py", "tests/test_push_input_prompt.py"],
}


def owners(repo: Path) -> dict[str, list[str]]:
    src, tests = repo / "src" / PKG, repo / "tests"
    mods = sorted(p.relative_to(src).as_posix() for p in src.rglob("*.py") if p.name != "__init__.py")
    tfiles = sorted(tests.glob("test_*.py"))
    names = sorted((t.stem[5:] for t in tfiles), key=len, reverse=True)  # longest match wins
    out: dict[str, set[str]] = defaultdict(set)
    for rel in mods:
        stem = Path(rel).stem.removeprefix("cli_")
        dotted = "_".join(Path(rel).with_suffix("").parts).removeprefix("core_")
        keys = [stem, dotted, dotted.replace("server_", "serve_", 1)]
        for x in names:
            if any(k == x or k.startswith(x + "_") for k in keys):
                out[rel].add(f"tests/test_{x}.py")
                break
    by_name = set(out)
    for t in tfiles:  # modules without a name match: the test files importing them directly
        for n in ast.walk(ast.parse(t.read_text(encoding="utf-8"))):
            found = []
            if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith(PKG):
                found = [n.module] + [f"{n.module}.{a.name}" for a in n.names]
            elif isinstance(n, ast.Import):
                found = [a.name for a in n.names if a.name.startswith(PKG)]
            for name in found:
                rel = name.removeprefix(PKG).lstrip(".").replace(".", "/") + ".py"
                if rel in mods and rel not in by_name:
                    out[rel].add(f"tests/{t.name}")
    return {k: sorted(v) for k, v in out.items()} | {k: v for k, v in OWNER_OVERRIDES.items() if k in mods}


def load_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def plan(payload: dict[str, Any], rows: list[dict[str, Any]], own: dict[str, list[str]]) -> dict[int, list[int]]:
    tests, cands, arcs = payload["tests"], set(payload["candidates"]), payload["arcsize"]
    out = {}
    for r in rows:
        owner_files = set(own.get(r.get("file", ""), []))
        ok = r.get("other_killer")
        if not r.get("killers") or ok is None or not owner_files or tests[ok].split("::")[0] in owner_files:
            continue
        covering = payload["lines"][r["file"]][str(r["line"])]
        tries = [t for t in covering if t not in cands and t != ok and tests[t].split("::")[0] in owner_files]
        if tries:
            out[r["idx"]] = sorted(tries, key=lambda t: arcs[t])
    return out


def rank(payload: dict[str, Any], rows: list[dict[str, Any]], owner_rows: list[dict[str, Any]],
         own: dict[str, list[str]]) -> tuple[dict[int, str], dict[int, list[int]], Counter[str]]:  # fmt: skip
    tests = payload["tests"]

    def tfile(t: int) -> str:
        return tests[t].split("::")[0]

    owner_killer = {r["idx"]: r["owner_killer"] for r in owner_rows if "owner_killer" in r}
    need: dict[int, set[int]] = {}
    misplaced: set[int] = set()
    kills: Counter[int] = Counter()
    case: Counter[str] = Counter()
    for r in rows:
        if not r.get("killers"):
            continue
        good = [t for t in r["killers"] if t not in r.get("flaky", [])]
        kills.update(good)
        owner_files = set(own.get(r["file"], []))
        ok = r["other_killer"]
        noncand_owner = (ok is not None and tfile(ok) in owner_files) or owner_killer.get(r["idx"]) is not None
        cand_owner = {t for t in good if tfile(t) in owner_files}
        if owner_files and noncand_owner:
            case["guarded by a non-candidate owner test"] += 1
        elif cand_owner:
            need[r["idx"]] = cand_owner
            case["needs a candidate owner test"] += 1
        elif ok is not None:
            case["no owner kills it; a non-candidate bystander does" if owner_files else "unowned; guarded"] += 1
        elif good:
            need[r["idx"]] = set(good)
            if owner_files:
                misplaced.add(r["idx"])
            case["only candidate bystanders kill it" if owner_files else "unowned; only candidates kill it"] += 1
    keep: dict[int, list[int]] = {}
    todo = dict(need)
    while todo:
        t = Counter(t for s in todo.values() for t in s).most_common(1)[0][0]
        keep[t] = [k for k, s in todo.items() if t in s]
        todo = {k: s for k, s in todo.items() if t not in s}
    verdict = {}
    for t in payload["candidates"]:
        if t in keep:
            verdict[t] = "MISPLACED" if all(k in misplaced for k in keep[t]) else "KEEP"
        else:
            verdict[t] = "REMOVE" if kills[t] else "NO_KILLS"
    return verdict, keep, case


def write_report(path: Path, payload: dict[str, Any], rows: list[dict[str, Any]], own: dict[str, list[str]],
                 verdict: dict[int, str], keep: dict[int, list[int]], case: Counter[str]) -> None:  # fmt: skip
    tests = payload["tests"]
    by_idx = {r["idx"]: r for r in rows}
    n = Counter(verdict.values())

    def ex(i: int) -> str:
        r = by_idx[i]
        return f"{r['file']}:{r['line']} `{r['func']}` {r['op']}"

    with path.open("w", encoding="utf-8") as out:
        w = out.write
        w("# Tier 4, ownership-aware verdicts\n\n")
        for k, v in case.most_common():
            w(f"- {k}: {v} mutants\n")
        w(f"\nKEEP {n['KEEP']}, MISPLACED {n['MISPLACED']}, REMOVE {n['REMOVE']}, NO_KILLS {n['NO_KILLS']}\n\n")
        for v, title in (("KEEP", "the owner's only check"), ("MISPLACED", "only check, outside the owner's file")):
            w(f"## {v}: {title}\n\n")
            for t in sorted((t for t, x in verdict.items() if x == v), key=lambda t: tests[t]):
                where = f" (owner: {', '.join(own.get(by_idx[keep[t][0]]['file'], []))})" if v == "MISPLACED" else ""
                w(f"- `{tests[t]}`: guards {len(keep[t])}, e.g. {ex(keep[t][0])}{where}\n")
            w("\n")
        for v in ("REMOVE", "NO_KILLS"):
            w(f"## {v}\n\n")
            w("".join(f"- `{tests[t]}`\n" for t, x in sorted(verdict.items(), key=lambda i: tests[i[0]]) if x == v))
            w("\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("step", choices=("owners", "plan", "rank"))
    ap.add_argument("--repo", type=Path, default=Path.cwd())
    ap.add_argument("--owners", type=Path, default=Path("owners.json"))
    ap.add_argument("--payload", type=Path, default=Path("payload.json"))
    ap.add_argument("--results", type=Path, help="tier 4 results (all chunk files concatenated)")
    ap.add_argument("--owner-results", type=Path, help="owner-check results")
    ap.add_argument("--out", type=Path, help="rerun.json for plan, the report for rank")
    args = ap.parse_args()
    if args.step == "owners":
        args.owners.write_text(json.dumps(owners(args.repo), indent=1))
        return
    payload = json.loads(args.payload.read_text())
    own = json.loads(args.owners.read_text())
    rows = [r for r in load_rows(args.results) if "cands" in r]  # drop skipped and crashed mutants
    if args.step == "plan":
        p = plan(payload, rows, own)
        (args.out or Path("rerun.json")).write_text(json.dumps(p))
        print(f"{len(p)} mutants need the owner check")
        return
    verdict, keep, case = rank(payload, rows, load_rows(args.owner_results), own)
    report = args.out or Path("verdicts-report.md")
    write_report(report, payload, rows, own, verdict, keep, case)
    report.with_suffix(".json").write_text(
        json.dumps({payload["tests"][t]: v for t, v in sorted(verdict.items())}, indent=1)
    )
    print(dict(Counter(verdict.values())))


if __name__ == "__main__":
    main()
