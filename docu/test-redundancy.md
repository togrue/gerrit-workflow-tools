# Test redundancy

How to tell which tests catch bugs no other test catches, which ones only repeat others, and
how to keep the suite orthogonal: every behaviour checked once, by the tests of the module that
owns it. The tooling is in [`scripts/test_redundancy/`](../scripts/test_redundancy/); the
runs are in [results/](../scripts/test_redundancy/results/), the latest in
[2026-11-18](../scripts/test_redundancy/results/2026-11-18/).

## The metric

Line or branch coverage cannot answer either question. It records what a test *executed*, not
what it *checked*: a test that runs a function without asserting on it covers it just the same.

**Mutation testing** can. A mutant is a small deliberate bug (`<` becomes `<=`, a condition is
negated, a statement deleted, a return value replaced by `None`). A test *kills* a mutant when it
fails on it. That gives two measures:

- **Mutation score**: the share of mutants some test kills. Every surviving mutant is a bug the
  suite would let through.
- **Kill matrix**: which tests kill which mutant. A test that kills no mutant on its own adds no
  bug-finding power; a test whose kills are a subset of another's is redundant with it. The goal is
  a high score with few killers per mutant, each mutant killed by the tests of the module it is in.

Some overlap is right: a test that pins agreement *between* commands (see AGENTS.md) kills the
same mutants as the per-module tests, and also the wiring mutants they cannot see.

## Four tiers

A full kill matrix is expensive: every covering test against every mutant. The tiers spend
mutation only where cheap coverage cannot decide.

| Tier | What | Cost | State |
|------|------|------|-------|
| 1 | Ownership rule: a module's behaviour is asserted in its owner's test file; other tests check wiring | none | convention; the owner mapping exists only as a heuristic (below) |
| 2 | Per-test branch coverage: overlap hotspots, tests whose coverage lies within another single test's | one suite run with coverage | `overlap.py` |
| 3 | Mutation of the lines changed against `main`, first kill only | minutes | not built |
| 4 | Kill matrix for the tier 2 candidates, ownership-aware | hours, on Linux | `mutrun.py`, `mutsup.py`, `verdicts.py` |

Tier 2 can rule a test *in* (it covers a branch no other test reaches, so it is needed) but not
*out*: two tests running the same branches may check different things. Tier 4 decides the rest.

### Ownership

There is no explicit test-to-module mapping, so `verdicts.py owners` derives one from names: a
test file owns the module of its name, the command of that name and modules split off from it
(`test_push.py` owns `core/push.py`, `cli_push.py` and `core/push_checks.py`; `test_gerrit_cache.py`
owns `core/gerrit/cache_rows.py`). A module with no such name is owned by the test files that
import it directly. It is a heuristic, and `OWNER_OVERRIDES` in `verdicts.py` replaces its guess
where the names mislead: `test_ci_findings_tools.py` also owns the `ci_findings_*` modules,
`config_editor_file.py` belongs with `test_config_form.py`, and `push_options_history.py` is unit
tested in `test_push_input_prompt.py`. Before the overrides these made some MISPLACED verdicts in
both runs. A full explicit mapping would make tier 1 checkable and the verdicts exact.

A mutant counts as guarded only when an owner test kills it. A bystander that kills it as a side
effect is not a reason to keep a test, and not a reason to delete the owner's test either.

## Running it

Tier 2, anywhere:

```bash
uv run --with pytest-cov pytest -p no:cacheprovider --cov=gerrit_workflow_tools --cov-branch \
    --cov-context=test --cov-report=
python scripts/test_redundancy/overlap.py --data .coverage --report overlap.md --payload payload.json
```

Tier 4, on a Linux machine with a synced `.venv` in the checkout (the plugin uses `SIGALRM` and
`/proc`). `mutsup.py` runs detached and supervises the workers; its docstring lists the
thresholds and how to change them while it runs.

```bash
setsid nohup python3 scripts/test_redundancy/mutsup.py --payload payload.json --out mutout &
cat mutout/c*.jsonl > results.jsonl
python scripts/test_redundancy/verdicts.py owners
python scripts/test_redundancy/verdicts.py plan --results results.jsonl --out rerun.json
MUT_RERUN=$PWD/rerun.json setsid nohup python3 scripts/test_redundancy/mutsup.py \
    --payload payload.json --out ownout &
cat ownout/c*.jsonl > owner-results.jsonl
python scripts/test_redundancy/verdicts.py rank --results results.jsonl \
    --owner-results owner-results.jsonl --out verdicts.md
```

The payload pins line numbers: run tier 4 on the same tree the coverage was recorded on.

## Results, 2026-11-14

Recorded on the tree of 38f090f plus that day's uncommitted work; tests changed since may no
longer match.

**Tier 2.** 1406 unit tests, 15248 covered arcs. 515 tests keep every arc covered; 385 cover an
arc no other test does. 440 tests cover nothing that a single other, non-sibling test does not
also cover: the candidates. 46 tests exercise no package logic at all (architecture, docs and
parity checks, and the tests of the `ChangeStore` fake).

**Tier 4.** 4872 mutants on the lines the candidates cover; 913 could not be applied and 7 hung or
crashed a worker. This section first blamed the 913 on the closure check, but they were
almost certainly the mutants inside a class. `compile()` rejects those for the reason given under
2026-11-18, on Python 3.12, 3.13 and 3.14 alike. No example mutant in this run is in a method,
and 913 of 4872 (18.7%) matches the 1002 of 5380 (18.6%) in that run. Methods were not assessed in
this run. Plain Plain
redundancy (any other test killing the mutant counts) found 57 candidates that catch a bug nothing
else catches, and 340 removable.

**Ownership-aware**, after the owner check on 527 mutants whose first other killer was a bystander:

| Verdict | Tests | Meaning |
|---------|------:|---------|
| KEEP | 100 | the owner's only test killing some mutant |
| MISPLACED | 12 | the only test killing some mutant, but outside the owner's file: move it there |
| REMOVE | 306 | every mutant it kills is killed by an owner test (or, unowned, by another test) |
| NO_KILLS | 22 | kills no mutant in the code it runs: check its asserts |

41 tests the plain rule called removable are the owner's only check. Per test, with an example
mutant for each KEEP and MISPLACED: [verdicts.md](../scripts/test_redundancy/results/2026-11-14/verdicts.md).

## Results, 2026-11-18

Recorded on the tree of dadb761.

**Tier 2.** 1581 unit tests (1546 exercise package logic), 16379 covered arcs; 402 cover an arc no
other test does; 539 candidates. 5 timing tests fail under coverage tracing and were left out.

**Tier 4.** 5380 mutants; 4378 assessed. 1002 could not be compiled ("AST node line range is not
valid"); this run counted them as crashed because `compile_def` let the error end the worker
(since fixed: such a mutant is skipped). They were every mutant inside a class: the class
`compile_def` wraps a method in had no end line. So methods were not assessed in this run either;
`compile_def` now gives the class an end line. Owner check on 509 mutants.

| Verdict | Tests | Change since 2026-11-14 |
|---------|------:|---------|
| KEEP | 110 | +10 |
| MISPLACED | 13 | +1 |
| REMOVE | 392 | +86 |
| NO_KILLS | 24 | +2 |

Of the 419 tests in both runs, 20 changed verdict, most between KEEP and REMOVE as tests were
added or moved. Of the 120 new candidates, 98 are REMOVE. New ones to read first: three
`test_suggested_edits.py` cases are NO_KILLS (`test_replacement_describes_the_range_it_replaces`
only reaches a method, which this run could not mutate); four are MISPLACED, three there and one
in `test_log.py`.

**Acted on.** MISPLACED: 8 tests moved to their owner's file, 4 were misjudged ownership (now
`OWNER_OVERRIDES`), and the `ger log` case of `test_gerrit_unavailable.py` stays where it is, as one
case of a test that pins agreement between commands. NO_KILLS: 1 strengthened (`apply_ci_strategy`'s
result is pinned) and 1 merged into the parametrisation of the test it repeated. 14 left as they
are: they reach only methods, and each fails on a mutant applied by hand. 8 left because no
operator reaches what they pin (the Change-Id regex, a line that is not JSON). The next tier 4 run,
the first that compiles methods, re-checks the 14. The `test_gerrit_unavailable.py` MISPLACED
verdict will recur: the test is kept on purpose, as above.

## Before removing a test

REMOVE means: no mutant from *this operator set* needs it. The operators change comparisons,
`and`/`or`, `not`, conditions, return values, integer and boolean constants, `+`/`-`, and delete
calls, `raise` and augmented assignments, inside functions only. They never touch strings,
regular expressions, data tables or module-level code, so a test that pins an error message, a
pattern's case-insensitivity or an exact output can show as REMOVE and still be the only guard of
that. Parametrised edge cases (most of the 27 REMOVE verdicts in `test_changeish.py`) are the
typical case. Read each before deleting it; prefer deleting a bystander's assertion over an
owner's test.

## Running mutants in-process

Lessons from the first run, all handled in the scripts now:

- The suite leaks sqlite connections; under coverage the run needs `ulimit -n 65536`.
- `tmp_path` directories (git repositories) pile up inside one pytest session and filled the
  machine's tmpfs. `mutrun.py` empties the base temp directory after every test run.
- A process re-running tests thousands of times grows without bound. Workers handle 150 mutants
  and exit; the supervisor caps their memory, pauses them under memory pressure and keeps
  the machine reachable.
- Mutants loop forever. The per-test alarm raises a `BaseException`, because code under test
  catches `Exception`; a watchdog ends a worker stuck past 60 s; a mutant that ends two workers is
  recorded as crashed and skipped.
- Baselines (each test unmutated) are taken before the first mutant: state left behind by a
  mutant made healthy tests look broken when baselines were taken lazily.
