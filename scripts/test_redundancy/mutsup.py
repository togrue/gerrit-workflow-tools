#!/usr/bin/env python3
"""Supervisor for the tier 4 mutation run. Linux, stdlib only; run it detached on the test machine.

    setsid nohup python3 scripts/test_redundancy/mutsup.py --payload payload.json --out mutout &

It splits the mutants into chunks and runs each chunk in a short-lived ``pytest -p mutrun``
worker (from ``.venv`` at ``--repo``). Workers resume from what they already wrote, so any worker
may be stopped at any time. Every couple of seconds it reads the machine and acts:

- headroom (MemAvailable, load, /tmp free)        -> start another worker, up to max_workers
- memory low, swap growing or /tmp low            -> SIGSTOP the largest worker (stops its growth)
- recovered                                       -> SIGCONT a paused worker
- critical                                        -> kill the largest worker; its chunk is requeued
- a worker over rss_cap_mb                        -> recycle it; a recycle without progress raises
                                                     that chunk's cap 1.5x, up to three times
- no result written for stall_min minutes         -> kill it (the poison-mutant rule in mutrun.py
                                                     records a mutant that ends two workers)

Thresholds are ``key=value`` arguments and can be changed while it runs by writing JSON to
``<out>/sup.cfg.json``. ``touch <out>/STOP`` drains it: no new workers, running ones finish.
Progress: ``<out>/sup.log`` and ``<out>/status.json``. ``MUT_RERUN`` in the environment is passed
through to the workers (owner-check mode).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
CFG: dict[str, Any] = {
    "chunk": 150,
    "max_workers": 3,
    "poll": 2.0,
    "start_mb": 2200,  # MemAvailable needed to start another worker
    "pause_mb": 1300,  # below: pause the largest worker
    "resume_mb": 2000,  # above: resume a paused worker
    "kill_mb": 700,  # below: kill the largest worker
    "rss_cap_mb": 1300,  # per-worker recycle threshold
    "swap_grow_mb": 200,  # swap growth since start that counts as pressure
    "tmp_min_mb": 800,
    "load_max": (os.cpu_count() or 1) + 0.5,
    "stall_min": 10,
    "max_retries": 4,  # exits without progress before a chunk is given up
}


def meminfo() -> dict[str, int]:
    out = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        k, v = line.split(":")
        out[k] = int(v.split()[0]) // 1024
    return out


def rss_mb(pid: int) -> int:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 0


class Supervisor:
    def __init__(self, repo: Path, payload: Path, out: Path) -> None:
        self.repo, self.payload, self.out = repo, payload, out
        self.workers: dict[int, dict[str, Any]] = {}
        self.events = dict.fromkeys(("paused", "resumed", "killed_mem", "recycled", "stalled", "failed"), 0)
        self.noprog: dict[int, int] = {}
        self.cfg_mtime = 0.0

    def log(self, msg: str) -> None:
        with (self.out / "sup.log").open("a") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")

    def cmd(self, basetemp: str) -> list[str]:
        py = str(self.repo / ".venv/bin/python")
        flags = ["-q", "-p", "no:cacheprovider", "-p", "mutrun", "-W", "ignore", f"--basetemp={basetemp}"]
        return ["nice", "-n", "15", py, "-m", "pytest", *flags]

    def env(self, **extra: str) -> dict[str, str]:
        return dict(os.environ, PYTHONPATH=str(HERE), MUT_PAYLOAD=str(self.payload), **extra)

    def result(self, cid: int) -> Path:
        return self.out / f"c{cid:03d}.jsonl"

    def lines(self, cid: int) -> int:
        p = self.result(cid)
        return len(p.read_text().splitlines()) if p.exists() else 0

    def done(self, cid: int) -> bool:
        return Path(f"{self.result(cid)}.done").exists()

    def kill(self, pid: int, why: str) -> None:
        w = self.workers.pop(pid)
        os.kill(pid, signal.SIGCONT)
        w["proc"].kill()
        w["proc"].wait()
        shutil.rmtree(w["basetemp"], ignore_errors=True)
        self.queue.insert(0, w["chunk"])
        self.log(f"KILL chunk {w['chunk'][0]} pid {pid}: {why}")

    def reload_cfg(self) -> None:
        live = self.out / "sup.cfg.json"
        if live.exists() and live.stat().st_mtime != self.cfg_mtime:
            self.cfg_mtime = live.stat().st_mtime
            try:
                upd = {k: type(CFG[k])(v) for k, v in json.loads(live.read_text()).items() if k in CFG}
                CFG.update(upd)
                self.log(f"cfg update: {upd}")
            except (ValueError, OSError) as e:
                self.log(f"cfg update ignored: {e}")

    def run(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        count = self.out / "count"
        if not count.exists():
            subprocess.run(
                self.cmd(f"/tmp/mut-count-{os.getpid()}"), cwd=self.repo, check=False,
                env=self.env(MUT_OUT=str(self.out / "count.jsonl"), MUT_COUNT_OUT=str(count)),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )  # fmt: skip
        total = int(count.read_text())
        chunks = [(i, s, min(s + CFG["chunk"], total)) for i, s in enumerate(range(0, total, CFG["chunk"]))]
        self.queue = [c for c in chunks if not self.done(c[0])]
        retries = dict.fromkeys((c[0] for c in chunks), 0)
        self.log(f"start: {total} mutants, {len(chunks)} chunks, {len(self.queue)} to do, cfg={CFG}")

        def stop(*_: object) -> None:
            for pid in list(self.workers):
                self.kill(pid, "supervisor stopped")
            sys.exit(0)

        signal.signal(signal.SIGTERM, stop)
        swap0: int | None = None
        last_status = 0.0
        while self.queue or self.workers:
            self.reload_cfg()
            m = meminfo()
            avail, swap = m["MemAvailable"], m["SwapTotal"] - m["SwapFree"]
            swap0 = swap if swap0 is None else min(swap0, swap)
            load = os.getloadavg()[0]
            tmp_free = shutil.disk_usage("/tmp").free // 2**20
            for pid, w in self.workers.items():
                w["rss"] = rss_mb(pid)
                w["peak"] = max(w["peak"], w["rss"])

            for pid, w in list(self.workers.items()):  # reap
                if w["proc"].poll() is None:
                    continue
                del self.workers[pid]
                shutil.rmtree(w["basetemp"], ignore_errors=True)
                cid = w["chunk"][0]
                if self.done(cid):
                    self.log(f"done chunk {cid} in {time.time() - w['started']:.0f} s (peak rss {w['peak']} MB)")
                    continue
                retries[cid] = 1 if self.lines(cid) > w["lines0"] else retries[cid] + 1
                if retries[cid] <= CFG["max_retries"]:
                    self.log(f"chunk {cid} exited {w['proc'].returncode} unfinished; requeue ({retries[cid]})")
                    self.queue.append(w["chunk"])
                else:
                    self.events["failed"] += 1
                    self.log(f"chunk {cid} FAILED after {CFG['max_retries']} exits without progress")

            for pid in list(self.workers):  # per-worker memory cap
                w = self.workers[pid]
                cid = w["chunk"][0]
                cap = CFG["rss_cap_mb"] * 1.5 ** self.noprog.get(cid, 0)
                if w["rss"] > cap:
                    self.events["recycled"] += 1
                    progressed = self.lines(cid) > w["lines0"]
                    self.kill(pid, f"rss {w['rss']} MB > cap {cap:.0f}" + ("" if progressed else " (no progress)"))
                    if not progressed:
                        self.noprog[cid] = self.noprog.get(cid, 0) + 1
                        if self.noprog[cid] > 3:
                            self.queue.remove(w["chunk"])
                            self.events["failed"] += 1
                            self.log(f"chunk {cid} FAILED: no progress even at cap {cap:.0f} MB")

            running = [p for p, w in self.workers.items() if not w["paused"]]
            paused = [p for p, w in self.workers.items() if w["paused"]]
            pressure = avail < CFG["pause_mb"] or swap - swap0 > CFG["swap_grow_mb"] or tmp_free < CFG["tmp_min_mb"]
            if (avail < CFG["kill_mb"] or tmp_free < CFG["tmp_min_mb"] // 2) and self.workers:
                self.events["killed_mem"] += 1
                pid = max(self.workers, key=lambda p: self.workers[p]["rss"])
                self.kill(pid, f"critical: avail {avail} MB, /tmp {tmp_free} MB")
            elif pressure and running:
                pid = max(running, key=lambda p: self.workers[p]["rss"])
                os.kill(pid, signal.SIGSTOP)
                self.workers[pid]["paused"] = True
                self.events["paused"] += 1
                self.log(f"PAUSE chunk {self.workers[pid]['chunk'][0]}: avail {avail} MB, swap +{swap - swap0} MB")
            elif not pressure and paused and avail > CFG["resume_mb"]:
                pid = min(paused, key=lambda p: self.workers[p]["rss"])
                os.kill(pid, signal.SIGCONT)
                self.workers[pid]["paused"] = False
                self.events["resumed"] += 1
                self.log(f"RESUME chunk {self.workers[pid]['chunk'][0]}: avail {avail} MB")
            elif (
                self.queue and not paused and not pressure and not (self.out / "STOP").exists()
                and len(self.workers) < CFG["max_workers"] and avail > CFG["start_mb"] and load < CFG["load_max"]
            ):  # fmt: skip
                self.start(self.queue.pop(0), avail, load)

            if (self.out / "STOP").exists() and not self.workers:
                self.log("STOP present and no workers left")
                break
            now = time.time()
            if now - last_status > 30:
                last_status = now
                self.check_stalls(now)
                self.status(len(chunks), total, avail, load, tmp_free)
            time.sleep(CFG["poll"])
        self.log(f"all done: {self.events}")

    def start(self, chunk: tuple[int, int, int], avail: int, load: float) -> None:
        cid, lo, hi = chunk
        basetemp = f"/tmp/mutw-{os.getpid()}-c{cid:03d}"
        env = self.env(MUT_START=str(lo), MUT_END=str(hi), MUT_OUT=str(self.result(cid)))
        with (self.out / f"c{cid:03d}.log").open("a") as log_file:
            proc = subprocess.Popen(self.cmd(basetemp), cwd=self.repo, env=env, stdout=log_file, stderr=log_file)
        self.workers[proc.pid] = {
            "chunk": chunk, "proc": proc, "paused": False, "started": time.time(), "basetemp": basetemp,
            "rss": 0, "peak": 0, "lines0": self.lines(cid), "seen": self.lines(cid), "seen_at": time.time(),
        }  # fmt: skip
        self.log(f"start chunk {cid} [{lo},{hi}) pid {proc.pid}: avail {avail} MB, load {load:.1f}")

    def check_stalls(self, now: float) -> None:
        for pid in list(self.workers):
            w = self.workers[pid]
            n = self.lines(w["chunk"][0])
            if n != w["seen"]:
                w["seen"], w["seen_at"] = n, now
            elif not w["paused"] and now - w["seen_at"] > CFG["stall_min"] * 60:
                self.events["stalled"] += 1
                self.kill(pid, f"no result for {CFG['stall_min']} min")

    def status(self, n_chunks: int, total: int, avail: int, load: float, tmp_free: int) -> None:
        written = sum(len(p.read_text().splitlines()) for p in self.out.glob("c*.jsonl"))
        done = sum(1 for p in self.out.glob("c*.jsonl.done"))
        workers = [{"chunk": w["chunk"][0], "rss_mb": w["rss"], "paused": w["paused"]} for w in self.workers.values()]
        st = {
            "chunks_done": done, "chunks": n_chunks, "mutants_written": written, "mutants": total,
            "avail_mb": avail, "load": round(load, 1), "tmp_free_mb": tmp_free, "events": self.events,
            "workers": workers,
        }  # fmt: skip
        (self.out / "status.json").write_text(json.dumps(st, indent=1))
        self.log(f"status {done}/{n_chunks} chunks, {written}/{total} mutants, avail {avail} MB, load {load:.1f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=Path.cwd(), help="checkout with a synced .venv")
    ap.add_argument("--payload", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("overrides", nargs="*", metavar="key=value", help=f"thresholds: {', '.join(CFG)}")
    args = ap.parse_args()
    for kv in args.overrides:
        k, v = kv.split("=", 1)
        CFG[k] = type(CFG[k])(v)
    Supervisor(args.repo.resolve(), args.payload.resolve(), args.out.resolve()).run()


if __name__ == "__main__":
    main()
