#!/usr/bin/env python3
"""Tier 2: per-test coverage overlap, and the payload for the tier 4 mutation run.

Input: a coverage.py data file recorded with per-test contexts and branch coverage:

    uv run --with pytest-cov pytest -p no:cacheprovider --cov=gerrit_workflow_tools \
        --cov-branch --cov-context=test --cov-report=

Unit of coverage is an arc (file, from-line, to-line). A test is all its phases (setup, run,
teardown) merged. Arcs that at least 95% of tests hit (autouse fixtures) carry no signal and are
dropped. A *candidate* is a test whose arc set is contained in another single test's, and that
other test is not one of its own parametrised siblings.

Writes a Markdown report and ``payload.json``: the candidates plus, for every source line a
candidate covers, the tests covering it. ``mutrun.py`` and ``verdicts.py`` read that payload.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path


FLOOR_SHARE = 0.95
PKG_MARK = "/src/gerrit_workflow_tools/"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", default=".coverage", help="coverage data file")
    ap.add_argument("--report", default="overlap-report.md")
    ap.add_argument("--payload", default="payload.json")
    args = ap.parse_args()

    db = sqlite3.connect(args.data)
    ctx_test = {cid: name.rsplit("|", 1)[0] for cid, name in db.execute("select id, context from context") if name}
    files = {fid: p.split(PKG_MARK)[-1] for fid, p in db.execute("select id, path from file")}
    tests = sorted(set(ctx_test.values()))
    tidx = {t: i for i, t in enumerate(tests)}

    arc_id: dict[tuple[int, int, int], int] = {}
    arc_tests: list[int] = []  # bitset of tests per arc
    test_arcs: list[set[int]] = [set() for _ in tests]
    for fid, cid, a, b in db.execute("select file_id, context_id, fromno, tono from arc"):
        if cid not in ctx_test:
            continue
        k = arc_id.setdefault((fid, a, b), len(arc_tests))
        if k == len(arc_tests):
            arc_tests.append(0)
        t = tidx[ctx_test[cid]]
        arc_tests[k] |= 1 << t
        test_arcs[t].add(k)

    floor = {k for k, bits in enumerate(arc_tests) if bits.bit_count() >= FLOOR_SHARE * len(tests)}
    for arcs in test_arcs:
        arcs -= floor
    for k in floor:
        arc_tests[k] = 0
    active = [t for t, arcs in enumerate(test_arcs) if arcs]
    no_src = [t for t, arcs in enumerate(test_arcs) if not arcs]

    def base(t: int) -> str:
        return tests[t].split("[", 1)[0]

    def tfile(t: int) -> str:
        return tests[t].split("::", 1)[0]

    subsumed: dict[int, list[int]] = {}
    for t in active:
        acc = ~0
        for k in test_arcs[t]:
            acc &= arc_tests[k]
            if acc == 1 << t:
                break
        others = acc & ~(1 << t)
        if others:
            subsumed[t] = [i for i in range(len(tests)) if others >> i & 1]
    candidates = {t: s for t, s in subsumed.items() if any(base(o) != base(t) for o in s)}
    unique_tests = {bits.bit_length() - 1 for bits in arc_tests if bits and bits & (bits - 1) == 0}

    remaining, chosen = set(range(len(arc_tests))) - floor, []
    pool = {t: set(test_arcs[t]) for t in active}
    while remaining and pool:
        t = max(pool, key=lambda t: len(pool[t] & remaining))
        if not pool[t] & remaining:
            break
        chosen.append(t)
        remaining -= pool.pop(t)

    mult = sorted(bits.bit_count() for bits in arc_tests if bits)
    with open(args.report, "w", encoding="utf-8") as out:
        w = out.write
        w("# Tier 2: per-test arc coverage overlap\n\n")
        w(f"- tests exercising src logic: **{len(active)}** of {len(tests)}; arcs: **{len(mult)}**\n")
        w(f"- tests per arc: median **{mult[len(mult) // 2]}**, p90 **{mult[int(len(mult) * 0.9)]}**\n")
        w(f"- greedy minimal suite keeping every arc: **{len(chosen)}** tests\n")
        w(f"- tests with a unique arc (needed): **{len(unique_tests)}**\n")
        w(f"- candidates (arc set within another single, non-sibling test's): **{len(candidates)}**\n")
        w(f"- tests exercising no src logic beyond the shared floor: **{len(no_src)}**\n\n")
        w("| test file | tests | candidates |\n|---|--:|--:|\n")
        per_file = Counter(tfile(t) for t in active)
        cand_file = Counter(tfile(t) for t in candidates)
        for f, n in cand_file.most_common():
            w(f"| {f} | {per_file[f]} | {n} |\n")
        w("\n## Candidates (smallest test containing it)\n\n")
        for t in sorted(candidates, key=lambda t: tests[t]):
            best = min(candidates[t], key=lambda o: len(test_arcs[o]))
            w(f"- `{tests[t]}` ({len(test_arcs[t])} arcs) within `{tests[best]}` ({len(test_arcs[best])})\n")

    line_bits: dict[tuple[str, int], int] = defaultdict(int)
    for (fid, a, b), k in arc_id.items():
        for ln in (a, b):
            if arc_tests[k] and ln > 0:
                line_bits[(files[fid], ln)] |= arc_tests[k]
    cand_mask = sum(1 << t for t in candidates)
    lines: dict[str, dict[int, list[int]]] = defaultdict(dict)
    for (f, ln), bits in line_bits.items():
        if bits & cand_mask:
            lines[f][ln] = [i for i in range(len(tests)) if bits >> i & 1]
    payload = {
        "tests": tests,
        "arcsize": [len(a) for a in test_arcs],
        "candidates": sorted(candidates),
        "lines": lines,
    }
    Path(args.payload).write_text(json.dumps(payload))
    print(f"{len(candidates)} candidates, {sum(len(v) for v in lines.values())} lines -> {args.payload}")


if __name__ == "__main__":
    main()
