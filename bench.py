#!/usr/bin/env python3
"""Measures every fetch-* client against bench-server and prints a Markdown
table. Run from the workspace root after `cargo build --release`.

  python3 -I bench.py [--runs 20] [--inproc 200] [--build-time] [--only a,b]
                      [--https https://example.com/] [--no-build]
                      [--parallel 20]

Builds each client separately first (so that dependency features are not
unified across clients), unless --no-build.

Columns: stripped binary size, crates in the dependency tree, clean build
time (opt-in, slow), cold start (process start to exit for one 1 KiB GET,
median wall time and max RSS), per-request cost inside one process (200
keep-alive GETs), and max RSS for one 8 MiB download. With --https, one GET
of that URL per process: CPU time (user+sys, network latency excluded) and
max RSS, which is where TLS setup and root certificate loading show up.
With --parallel N, N processes run 1 KiB GETs at the same time and the sum
of their PSS (proportional set size, shared pages divided among sharers) is
sampled from /proc: what N instances really cost together, and per instance.
"""

import argparse
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "target" / "release"
SERVER_ADDR = "127.0.0.1:18080"


def clients(only):
    names = sorted(p.name for p in (ROOT / "clients").iterdir() if p.is_dir())
    if only:
        names = [n for n in names if n in only]
    return names


def build(names):
    """Builds every client in its own cargo invocation: building the whole
    workspace at once unifies dependency features across clients (one
    client's rustls backend would end up in another's binary)."""
    for pkg in ["bench-server", "measure", *(f"fetch-{n}" for n in names)]:
        print(f"  building {pkg}", file=sys.stderr)
        subprocess.run(["cargo", "build", "--release", "-p", pkg], cwd=ROOT, check=True,
                       capture_output=True)


def run_measured(cmd):
    """Wall seconds, max RSS in KiB and CPU seconds of one child process.

    Python forks itself before exec, and ru_maxrss covers that pre-exec
    copy of the interpreter, so the child is launched through the tiny
    `measure` binary, which reports what `wait4` saw.
    """
    proc = subprocess.run(
        [TARGET / "measure", *map(str, cmd)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd} exited with {proc.returncode}: {proc.stderr}")
    wall_us, cpu_us, rss = proc.stderr.strip().splitlines()[-1].split()
    return int(wall_us) / 1e6, int(rss), int(cpu_us) / 1e6


def median_cpu(cmd, runs):
    cpu, rss = [], []
    for _ in range(runs):
        _, r, c = run_measured(cmd)
        cpu.append(c)
        rss.append(r)
    return statistics.median(cpu), statistics.median(rss)


def median_of(cmd, runs):
    walls, rss = [], []
    for _ in range(runs):
        w, r, _ = run_measured(cmd)
        walls.append(w)
        rss.append(r)
    return statistics.median(walls), statistics.median(rss)


def parallel_pss(binary, url, count, requests=4000):
    """Max over time of the summed PSS of `count` concurrent processes, KiB."""
    procs = [subprocess.Popen([binary, url, "-n", str(requests)], stdout=subprocess.DEVNULL)
             for _ in range(count)]
    peak = 0
    while any(p.poll() is None for p in procs):
        total = 0
        for p in procs:
            try:
                with open(f"/proc/{p.pid}/smaps_rollup") as f:
                    for line in f:
                        if line.startswith("Pss:"):
                            total += int(line.split()[1])
                            break
            except OSError:
                pass
        peak = max(peak, total)
        time.sleep(0.05)
    for p in procs:
        if p.returncode != 0:
            raise RuntimeError(f"{binary} exited with {p.returncode} in parallel run")
    return peak


def crate_count(name):
    out = subprocess.run(
        ["cargo", "tree", "-p", f"fetch-{name}", "--edges", "normal", "--prefix", "none"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if out.returncode != 0:
        return "-"
    return len({line.split()[0] for line in out.stdout.splitlines() if line.strip()}) - 1


def clean_build_seconds(name):
    target = ROOT / "target" / f"clean-{name}"
    shutil.rmtree(target, ignore_errors=True)
    env = dict(os.environ, CARGO_TARGET_DIR=str(target))
    start = time.perf_counter()
    subprocess.run(["cargo", "build", "--release", "-p", f"fetch-{name}"],
                   cwd=ROOT, env=env, check=True, capture_output=True)
    seconds = time.perf_counter() - start
    shutil.rmtree(target, ignore_errors=True)
    return seconds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--inproc", type=int, default=200)
    ap.add_argument("--build-time", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--https", default="", help="URL for one HTTPS GET per process")
    ap.add_argument("--no-build", action="store_true", help="use the binaries as they are")
    ap.add_argument("--parallel", type=int, default=0, help="N concurrent processes for a PSS sum")
    ap.add_argument("--extra", action="append", default=[],
                    help="label=path of an extra binary to measure (e.g. a lean build)")
    args = ap.parse_args()
    only = {s for s in args.only.split(",") if s}

    names = clients(only)
    if not args.no_build:
        build(names)
    names = [n for n in names if (TARGET / f"fetch-{n}").exists()]
    binaries = {n: TARGET / f"fetch-{n}" for n in names}
    for extra in args.extra:
        label, _, path = extra.partition("=")
        binaries[label] = Path(path).resolve()
    if not binaries:
        sys.exit("no built clients found")
    server = subprocess.Popen([TARGET / "bench-server", SERVER_ADDR], stderr=subprocess.DEVNULL)
    time.sleep(0.3)
    base = f"http://{SERVER_ADDR}"
    rows = []
    try:
        for name, binary in binaries.items():
            # warm the page cache so the first run is not a disk read
            run_measured([binary, f"{base}/small"])
            if args.https:
                run_measured([binary, args.https])
            cold_wall, cold_rss = median_of([binary, f"{base}/small"], args.runs)
            many_wall, _ = median_of([binary, f"{base}/small", "-n", str(args.inproc)], max(3, args.runs // 4))
            per_request_us = (many_wall - cold_wall) / (args.inproc - 1) * 1e6
            _, big_rss = median_of([binary, f"{base}/big"], max(3, args.runs // 4))
            pss_total = None
            if args.parallel:
                pss_total = parallel_pss(binary, f"{base}/small", args.parallel)
            https_cpu = https_rss = None
            if args.https:
                https_cpu, https_rss = median_cpu([binary, args.https], max(5, args.runs // 2))
            row = {
                "client": name,
                "binary_kb": binary.stat().st_size // 1024,
                "crates": crate_count(name),
                "build_s": clean_build_seconds(name) if args.build_time else None,
                "cold_ms": cold_wall * 1e3,
                "cold_rss_kb": cold_rss,
                "req_us": per_request_us,
                "big_rss_kb": big_rss,
                "pss_total_kb": pss_total,
                "https_cpu_ms": None if https_cpu is None else https_cpu * 1e3,
                "https_rss_kb": https_rss,
            }
            rows.append(row)
            print(f"  {name}: {row['cold_ms']:.1f} ms cold, {row['cold_rss_kb']} KiB", file=sys.stderr)
    finally:
        server.kill()

    cols = ["client", "binary KiB", "crates"]
    if args.build_time:
        cols.append("clean build s")
    cols += ["cold start ms", "max RSS KiB", "per request µs", "8 MiB download RSS KiB"]
    if args.parallel:
        cols += [f"PSS of {args.parallel} procs KiB", "PSS per proc KiB"]
    if args.https:
        cols += ["HTTPS CPU ms", "HTTPS RSS KiB"]
    print("| " + " | ".join(cols) + " |")
    print("|" + "|".join("---" for _ in cols) + "|")
    for r in sorted(rows, key=lambda r: r["cold_ms"]):
        cells = [r["client"], str(r["binary_kb"]), str(r["crates"])]
        if args.build_time:
            cells.append(f"{r['build_s']:.0f}")
        cells += [f"{r['cold_ms']:.1f}", str(r["cold_rss_kb"]), f"{r['req_us']:.0f}", str(r["big_rss_kb"])]
        if args.parallel:
            cells += [str(r["pss_total_kb"]), str(r["pss_total_kb"] // args.parallel)]
        if args.https:
            cells += [f"{r['https_cpu_ms']:.0f}", str(r["https_rss_kb"])]
        print("| " + " | ".join(cells) + " |")
    uname = os.uname()
    print(f"\n{uname.sysname} {uname.release} {uname.machine}, {os.cpu_count()} CPUs, "
          f"runs={args.runs}, in-process requests={args.inproc}")


if __name__ == "__main__":
    main()
