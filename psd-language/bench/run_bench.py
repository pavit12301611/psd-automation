#!/usr/bin/env python3
"""Reproducible benchmark runner for PSD.

Builds each bench/*.psd to a native binary, runs it alongside the Python
reference, times both, and prints a table with the full environment so
results can be compared across machines.

Usage:
    python3 bench/run_bench.py            # full run (long integer loop included)
    python3 bench/run_bench.py --quick    # smoke run (smaller workloads, CI)
"""

import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PSD = [sys.executable, "-m", "psd"]
ENV = dict(os.environ, PYTHONPATH=ROOT)
QUICK = "--quick" in sys.argv


def env_info():
    info = {
        "platform": platform.platform(),
        "python": platform.python_version(),
    }
    try:
        import llvmlite.binding as llvm
        info["llvm"] = ".".join(map(str, llvm.llvm_version_info))
    except Exception:
        info["llvm"] = "unknown"
    for cand in ("cc", "gcc", "clang"):
        path = shutil.which(cand)
        if path:
            out = subprocess.run([path, "--version"], capture_output=True, text=True)
            info["c compiler"] = out.stdout.splitlines()[0]
            break
    cpu = platform.processor()
    if not cpu and os.path.exists("/proc/cpuinfo"):
        m = re.search(r"model name\s*:\s*(.+)", open("/proc/cpuinfo").read())
        cpu = m.group(1).strip() if m else "unknown"
    info["cpu"] = cpu or "unknown"
    return info


def build(psd_file, out):
    r = subprocess.run(PSD + ["build", psd_file, "-o", out],
                       capture_output=True, text=True, cwd=ROOT, env=ENV)
    if r.returncode != 0:
        print(f"build failed for {psd_file}:\n{r.stderr}", file=sys.stderr)
        sys.exit(1)


def timeit(cmd, repeat):
    best = float("inf")
    out = ""
    for _ in range(repeat):
        t0 = time.perf_counter()
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, env=ENV)
        dt = time.perf_counter() - t0
        if r.returncode != 0:
            print(f"run failed: {cmd}\n{r.stderr}", file=sys.stderr)
            sys.exit(1)
        out = r.stdout.strip()
        best = min(best, dt)
    return best, out


def main():
    print("== benchmark environment ==")
    for k, v in env_info().items():
        print(f"  {k:12s} {v}")
    print(f"  mode         {'quick' if QUICK else 'full'}\n")

    # (psd file, python file, timing kind)
    jobs = [
        ("bench/fib.psd", "bench/fib.py", "measured"),
        ("bench/mandelbrot.psd", "bench/mandelbrot.py", "measured"),
    ]
    if QUICK:
        jobs = [(p, y, kind) for (p, y, kind) in jobs]
        loop_psd, loop_py = None, None
    else:
        loop_psd, loop_py = "bench/loops.psd", "bench/loops.py"

    rows = []
    mismatches = []
    with tempfile.TemporaryDirectory(prefix="psd-bench-") as td:
        for psd_file, py_file, kind in jobs:
            name = os.path.basename(psd_file)[:-4]
            exe = os.path.join(td, name)
            build(psd_file, exe)
            n = 3 if QUICK else 5
            t_psd, out_psd = timeit([exe], n)
            t_py, out_py = timeit([sys.executable, py_file], 1)
            agree = out_psd == out_py
            if not agree:
                mismatches.append((name, out_psd, out_py))
            rows.append((name, t_psd, t_py, kind, agree))

        if loop_psd:
            name = "loops"
            exe = os.path.join(td, "loops")
            build(loop_psd, exe)
            t_psd, out_psd = timeit([exe], 1)
            # Python: run a 1e8-iteration sample and extrapolate (1e9 takes minutes).
            small = open(os.path.join(ROOT, loop_py)).read().replace("1000000000", "100000000")
            small_path = os.path.join(td, "loops_small.py")
            open(small_path, "w").write(small)
            t_small, out_small = timeit([sys.executable, small_path], 1)
            t_py = t_small * 10                       # extrapolated wall time
            out_py = str(int(out_small) * 10)         # scaled output: total is linear in N
            agree = out_psd == out_py
            if not agree:
                mismatches.append((name, out_psd, out_py + " (scaled 1e8 x10)"))
            rows.append((name + " [py est.]", t_psd, t_py, "extrapolated", agree))

    print("== results (best of N) ==")
    print(f"  {'benchmark':22s} {'psd':>9s} {'python':>9s} {'speedup':>9s}  kind           outputs agree")
    for name, t_psd, t_py, kind, agree in rows:
        print(f"  {name:22s} {t_psd:8.3f}s {t_py:8.3f}s {t_py / t_psd:8.1f}x  {kind:12s}  {'yes' if agree else 'NO'}")

    if mismatches:
        print("\n== benchmark output mismatches ==")
        for name, got, want in mismatches:
            print(f"  {name}: psd={got!r}  python={want!r}")
        sys.exit(1)


if __name__ == "__main__":
    main()
