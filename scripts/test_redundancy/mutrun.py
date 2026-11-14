"""Tier 4 mutation runner, as a pytest plugin: ``pytest -p mutrun`` with this directory on PYTHONPATH.

A mutant is applied by swapping one function's ``__code__`` in place, so every reference to the
function (imports, bound methods) sees it without reloading anything. Only code inside functions
and methods is mutated. For each mutant on a line some candidate test covers:

1. run every candidate covering the line (which candidates kill it);
2. if one did, run the other covering tests, smallest first, until one fails;
3. a candidate kill nothing else matched is re-run unmutated, to rule out a flaky test.

With ``MUT_RERUN`` set (a plan from ``verdicts.py plan``) it runs the owner check instead: for
each listed mutant, the given owner tests in order until one kills it.

Every test run is guarded: a 5 s alarm that ``except Exception`` cannot swallow, a 60 s watchdog
that ends the worker, and cwd, ``os.environ``, lru caches and ``tmp_path`` restored afterwards.
``<out>.cur`` names the mutant in flight; a mutant that ends two workers is recorded as crashed.

Environment:
  MUT_PAYLOAD  payload.json from overlap.py (required)
  MUT_OUT      results, JSON lines, appended; mutants already in it are skipped (required)
  MUT_START, MUT_END   global mutant index range (default: all)
  MUT_RERUN    owner-check plan; switches mode
  MUT_COUNT_OUT  write the mutant count to this file and stop
"""

from __future__ import annotations

import ast
import copy
import gc
import json
import os
import shutil
import signal
import sys
import threading
import time
import types
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from _pytest.runner import runtestprotocol


PKG = "gerrit_workflow_tools"
TEST_TIMEOUT_S = 5.0
WATCHDOG_S = 60
CMP: dict[type, type] = {
    ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq, ast.In: ast.NotIn, ast.NotIn: ast.In, ast.Is: ast.IsNot, ast.IsNot: ast.Is,
}  # fmt: skip


def log(*a: object) -> None:
    print(*a, file=sys.__stderr__, flush=True)


class MutantTimeout(BaseException):
    """A BaseException, so code under test that catches ``Exception`` cannot swallow it."""


def _alarm(signum: int, frame: object) -> None:
    raise MutantTimeout()


# --- mutant enumeration --------------------------------------------------------------


def defs_in(body: list[ast.stmt], cls: str | None = None) -> Iterator[tuple[ast.FunctionDef, str | None]]:
    """Top-level functions and (nested) class methods, with the enclosing class name."""
    for n in body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield n, cls  # type: ignore[misc]
        elif isinstance(n, ast.ClassDef):
            yield from defs_in(n.body, n.name)
        elif isinstance(n, (ast.If, ast.Try)):
            yield from defs_in(n.body, cls)
            yield from defs_in(n.orelse, cls)


Site = tuple[int, str, Any, int]  # (walk index, operator, operator argument, line)


def sites(fn: ast.FunctionDef) -> Iterator[Site]:
    """Mutation sites in a function: a small selective operator set."""
    skip = {id(x) for x in ast.walk(fn.args)}
    for d in fn.decorator_list:
        skip |= {id(x) for x in ast.walk(d)}
    if fn.returns:
        skip |= {id(x) for x in ast.walk(fn.returns)}
    for i, n in enumerate(ast.walk(fn)):
        if id(n) in skip:
            continue
        ln = getattr(n, "lineno", 0)
        if isinstance(n, ast.Compare):
            yield from ((i, "cmp", j, ln) for j, op in enumerate(n.ops) if type(op) in CMP)
        elif isinstance(n, ast.BoolOp):
            yield i, "andor", 0, ln
        elif isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not):
            yield i, "dropnot", 0, ln
        elif (
            isinstance(n, ast.Return)
            and n.value is not None
            and not (isinstance(n.value, ast.Constant) and n.value.value is None)
        ):
            yield i, "retnone", 0, ln
        elif isinstance(n, ast.Constant) and isinstance(n.value, bool):
            yield i, "flipbool", 0, ln
        elif isinstance(n, ast.Constant) and type(n.value) is int:
            yield i, "incr", 0, ln
        elif isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub)):
            yield i, "addsub", 0, ln
        if isinstance(n, (ast.If, ast.While, ast.IfExp)) and not isinstance(
            n.test, (ast.Compare, ast.BoolOp, ast.UnaryOp)
        ):
            yield i, "negcond", 0, n.test.lineno
        for field in ("body", "orelse", "finalbody"):
            stmts = getattr(n, field, None)
            if isinstance(stmts, list):
                for k, s in enumerate(stmts):
                    if isinstance(s, (ast.Raise, ast.AugAssign)) or (
                        isinstance(s, ast.Expr) and isinstance(s.value, ast.Call)
                    ):
                        yield i, "delstmt", (field, k), s.lineno


def apply(fn: ast.FunctionDef, site: Site) -> ast.FunctionDef:
    c = copy.deepcopy(fn)
    c.decorator_list = []
    i, kind, arg, _ = site
    n: Any = list(ast.walk(c))[i]
    if kind == "cmp":
        n.ops[arg] = CMP[type(n.ops[arg])]()
    elif kind == "andor":
        n.op = ast.Or() if isinstance(n.op, ast.And) else ast.And()
    elif kind == "dropnot":
        n.operand = ast.UnaryOp(ast.Not(), n.operand)  # not x -> not not x
    elif kind == "retnone":
        n.value = ast.Constant(None)
    elif kind == "flipbool":
        n.value = not n.value
    elif kind == "incr":
        n.value = n.value + 1
    elif kind == "addsub":
        n.op = ast.Sub() if isinstance(n.op, ast.Add) else ast.Add()
    elif kind == "negcond":
        n.test = ast.UnaryOp(ast.Not(), n.test)
    elif kind == "delstmt":
        field, k = arg
        getattr(n, field)[k] = ast.Pass()
    return c


def _find_code(code: types.CodeType, name: str, line: int) -> types.CodeType | None:
    for k in code.co_consts:
        if isinstance(k, types.CodeType):
            if k.co_name == name and k.co_firstlineno == line:
                return k
            found = _find_code(k, name, line)
            if found:
                return found
    return None


def compile_def(fn: ast.FunctionDef, cls: str | None, filename: str) -> types.CodeType | None:
    """Compile one def; inside a class of the same name, so ``super()`` and name mangling match."""
    node: ast.stmt = fn
    if cls:
        node = ast.ClassDef(
            name=cls, bases=[], keywords=[], body=[fn], decorator_list=[], type_params=[],
            lineno=fn.lineno, col_offset=0,
        )  # fmt: skip
    mod = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    return _find_code(compile(mod, filename, "exec"), fn.name, fn.lineno)


# --- runner ----------------------------------------------------------------------------


class Runner:
    def __init__(self, session: pytest.Session) -> None:
        self.p = json.loads(Path(os.environ["MUT_PAYLOAD"]).read_text())
        self.tests: list[str] = self.p["tests"]
        self.cand = set(self.p["candidates"])
        self.items = {it.nodeid: it for it in session.items}
        self.base: dict[int, bool] = {}
        self.caches: list[Any] = []
        self.t_start: float | None = None
        self.out_path = Path(os.environ["MUT_OUT"])
        self.cur_path = Path(f"{self.out_path}.cur")
        threading.Thread(target=self._watchdog, daemon=True).start()

    def _watchdog(self) -> None:
        """A test run past 60 s means the alarm cannot get through: end the worker."""
        while True:
            time.sleep(5)
            s = self.t_start
            if s is not None and time.time() - s > WATCHDOG_S:
                log(f"[watchdog] a test run took over {WATCHDOG_S} s; exiting the worker")
                os._exit(3)

    def run(self, t: int) -> bool | None:
        """Run one test; True when it fails (or times out), None when it is not collected."""
        it = self.items.get(self.tests[t])
        if it is None:
            return None
        for c in self.caches:
            c.cache_clear()
        cwd, env = os.getcwd(), dict(os.environ)
        self.t_start = time.time()
        signal.setitimer(signal.ITIMER_REAL, TEST_TIMEOUT_S, 1.0)  # re-fires if swallowed
        try:
            failed = any(r.failed for r in runtestprotocol(it, log=False, nextitem=None))
        except MutantTimeout:
            failed = True
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            self.t_start = None
            # tmp_path dirs (git repos) pile up within one session and fill /tmp otherwise
            for child in it.config._tmp_path_factory.getbasetemp().iterdir():  # type: ignore[attr-defined]
                shutil.rmtree(child, ignore_errors=True)
            if os.getcwd() != cwd:
                os.chdir(cwd)
            if dict(os.environ) != env:
                os.environ.clear()
                os.environ.update(env)
        return failed

    def baseline(self, t: int) -> bool:
        if t not in self.base:
            self.base[t] = self.run(t) is False
        return self.base[t]

    def mutants(self) -> list[tuple[Any, ...]]:
        src = Path(sys.modules[PKG].__file__ or "").parent
        funcs: dict[tuple[str, int, str], list[types.FunctionType]] = {}
        for o in gc.get_objects():
            if isinstance(o, types.FunctionType) and o.__code__.co_filename.startswith(str(src)):
                c = o.__code__
                funcs.setdefault((c.co_filename, c.co_firstlineno, c.co_name), []).append(o)
        self.caches = [
            o
            for o in gc.get_objects()
            if type(o).__name__ == "_lru_cache_wrapper" and getattr(o, "__module__", "").startswith(PKG)
        ]
        out = []
        for rel, lmap in sorted(self.p["lines"].items()):
            path = str(src / rel)
            covered = {int(k): v for k, v in lmap.items()}
            for fn, cls in defs_in(ast.parse(Path(path).read_text(encoding="utf-8")).body):
                targets = funcs.get((path, min([fn.lineno] + [d.lineno for d in fn.decorator_list]), fn.name))
                for s in sites(fn) if targets else ():
                    if s[3] in covered:
                        out.append((rel, path, fn, cls, targets, s, covered[s[3]]))
        return [(*m, i) for i, m in enumerate(out)]  # stable global index last

    @contextmanager
    def swapped(self, targets: list[types.FunctionType], code: types.CodeType) -> Iterator[None]:
        originals = [f.__code__ for f in targets]
        try:
            for f in targets:
                f.__code__ = code
            yield
        finally:
            for f, c in zip(targets, originals, strict=True):
                f.__code__ = c

    def write(self, row: dict[str, Any]) -> None:
        with self.out_path.open("a") as f:
            f.write(json.dumps(row) + "\n")

    def select(self, muts: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
        """This worker's range, minus mutants already written, minus a mutant that ended two workers."""
        lo, hi = int(os.environ.get("MUT_START", 0)), int(os.environ.get("MUT_END", len(muts)))
        done = set()
        if self.out_path.exists():
            done = {json.loads(line)["idx"] for line in self.out_path.read_text().splitlines() if line}
        self.deaths: dict[int, int] = {}
        if self.cur_path.exists():
            cur = json.loads(self.cur_path.read_text())
            cur["deaths"] += 1
            if cur["deaths"] >= 2:
                self.write({"idx": cur["idx"], "crashed": True})
                done.add(cur["idx"])
                self.cur_path.unlink()
                log(f"[mut] mutant {cur['idx']} ended the worker twice; recorded as crashed")
            else:
                self.cur_path.write_text(json.dumps(cur))
                self.deaths[cur["idx"]] = cur["deaths"]
        return [m for m in muts[lo:hi] if m[-1] not in done]

    def mark(self, idx: int) -> None:
        self.cur_path.write_text(json.dumps({"idx": idx, "deaths": self.deaths.get(idx, 0)}))

    def finish(self, n: int, t0: float) -> None:
        self.cur_path.unlink(missing_ok=True)
        Path(f"{self.out_path}.done").write_text("ok")
        log(f"[mut] done: {n} mutants in {time.time() - t0:.0f} s")

    def loop(self) -> None:
        signal.signal(signal.SIGALRM, _alarm)
        muts = self.mutants()
        if os.environ.get("MUT_COUNT_OUT"):
            Path(os.environ["MUT_COUNT_OUT"]).write_text(str(len(muts)))
            return
        muts = self.select(muts)
        t0 = time.time()
        if os.environ.get("MUT_RERUN"):
            self.owner_check(muts, json.loads(Path(os.environ["MUT_RERUN"]).read_text()))
        else:
            self.mutation_run(muts)
        self.finish(len(muts), t0)

    def mutation_run(self, muts: list[tuple[Any, ...]]) -> None:
        for t in sorted({t for m in muts for t in m[6]}):  # baselines first, before any mutant
            self.baseline(t)
        for rel, path, fn, cls, targets, site, covering, idx in muts:
            self.mark(idx)
            code = compile_def(apply(fn, site), cls, path)
            if code is None or any(code.co_freevars != f.__code__.co_freevars for f in targets):
                self.write({"idx": idx, "skipped": True})
                continue
            cands = [t for t in covering if t in self.cand]
            others = sorted((t for t in covering if t not in self.cand), key=lambda t: self.p["arcsize"][t])
            killers: list[int] = []
            other_killer, tried = None, 0
            with self.swapped(targets, code):
                killers = [t for t in cands if self.base[t] and self.run(t)]
                for t in others if killers else ():
                    if self.base[t]:
                        tried += 1
                        if self.run(t):
                            other_killer = t
                            break
            flaky = [t for t in killers if other_killer is None and self.run(t) is not False]
            row = {
                "idx": idx, "file": rel, "line": site[3], "op": site[1], "func": fn.name, "cands": cands,
                "killers": killers, "other_killer": other_killer, "tried": tried, "flaky": flaky,
            }  # fmt: skip
            self.write(row)

    def owner_check(self, muts: list[tuple[Any, ...]], plan: dict[str, list[int]]) -> None:
        muts = [m for m in muts if str(m[-1]) in plan]
        for t in sorted({t for m in muts for t in plan[str(m[-1])]}):
            self.baseline(t)
        for _rel, path, fn, cls, targets, site, _covering, idx in muts:
            self.mark(idx)
            code = compile_def(apply(fn, site), cls, path)
            killer, tried = None, 0
            if code is not None:
                with self.swapped(targets, code):
                    for t in plan[str(idx)]:
                        if self.base[t]:
                            tried += 1
                            if self.run(t):
                                killer = t
                                break
            self.write({"idx": idx, "owner_killer": killer, "tried": tried})


@pytest.hookimpl(tryfirst=True)
def pytest_runtestloop(session: pytest.Session) -> bool | None:
    if session.config.option.collectonly:
        return None
    Runner(session).loop()
    return True
