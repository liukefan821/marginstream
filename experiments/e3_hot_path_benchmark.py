"""E3: cost of the admission path.

Measures admission, partial fill and cancel acknowledgement at two scenario
grid widths, and compares the incremental gateway against one that recomputes
the envelopes from the full order set on every call.

Wall-clock timings on a shared machine are noisy, so the run reports both the
median and the 95th percentile over many repetitions, and states the
interpreter and platform. What the numbers are for is the shape of the curve
against grid width and order count, not an absolute latency claim.
"""
import os
import platform
import statistics
import sys
import time
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from marginstream.risk import RiskModel, Symbol
from marginstream.allocator2 import Allocator
from marginstream.gateway2 import Gateway

ACC = "X"
GRID_7 = (-3, -2, -1, 0, 1, 2, 3)
GRID_16 = tuple(range(-8, 8))
REPS = 4000


def build(grid, n_sym=8):
    syms = [Symbol(f"S{i}", 0, 1000 + 50 * i, 60 + 3 * i, 90 + (i % 5) * 8)
            for i in range(n_sym)]
    return syms, RiskModel(syms, addon_kappa=0, addon_scale=1, grid=grid)


def timed(fn, reps):
    samples = []
    for _ in range(reps):
        t0 = time.perf_counter_ns()
        fn()
        samples.append(time.perf_counter_ns() - t0)
    samples.sort()
    return (statistics.median(samples),
            samples[int(0.95 * (len(samples) - 1))])


def bench(grid_name, grid, worst_fill, open_orders):
    syms, risk = build(grid)
    alloc = Allocator(risk, ttl=10 ** 9, gross_per_risk=10 ** 6)
    gw = Gateway(0, risk, worst_fill=worst_fill)
    leases, _ = alloc.issue(ACC, 10 ** 12, {0: 1}, now=0)
    gw.install_lease(leases[0])
    gen = alloc.current_generation(ACC)

    # build up a book of live orders
    for i in range(open_orders):
        gw.admit(ACC, syms[i % len(syms)].name, 1 if i % 2 else -1, gen,
                 order_id=f"pre{i}")

    counter = [0]

    def admit_once():
        counter[0] += 1
        gw.admit(ACC, syms[counter[0] % len(syms)].name, 1, gen,
                 order_id=f"m{counter[0]}")

    adm = timed(admit_once, REPS)

    fill_ids = [f"m{i}" for i in range(1, REPS + 1)]
    fi = [0]

    def fill_once():
        gw.fill(ACC, fill_ids[fi[0]], 1)
        fi[0] += 1

    fill = timed(fill_once, min(REPS, len(fill_ids)))

    for i in range(open_orders):
        pass
    ci = [0]
    cancel_ids = [f"pre{i}" for i in range(open_orders)]

    def cancel_once():
        if ci[0] < len(cancel_ids):
            gw.cancel_ack(ACC, cancel_ids[ci[0]])
            ci[0] += 1

    cancel = timed(cancel_once, min(REPS, max(1, open_orders)))

    return {
        "grid": grid_name,
        "scenarios": len(grid),
        "mode": "incremental" if worst_fill else "full recompute",
        "open_orders": open_orders,
        "admit_p50_ns": adm[0], "admit_p95_ns": adm[1],
        "fill_p50_ns": fill[0], "fill_p95_ns": fill[1],
        "cancel_p50_ns": cancel[0], "cancel_p95_ns": cancel[1],
    }


def main():
    print(f"python {platform.python_version()} on {platform.system()} "
          f"{platform.machine()}; {REPS} repetitions per figure")
    rows = []
    for grid_name, grid in (("7", GRID_7), ("16", GRID_16)):
        for open_orders in (50, 500):
            for worst_fill in (True, False):
                rows.append(bench(grid_name, grid, worst_fill, open_orders))

    hdr = (f"{'grid':>5} {'orders':>7} {'mode':>16} {'admit p50':>10} "
           f"{'admit p95':>10} {'fill p50':>9} {'cancel p50':>11}")
    print(hdr)
    for r in rows:
        print(f"{r['scenarios']:>5} {r['open_orders']:>7} {r['mode']:>16} "
              f"{r['admit_p50_ns']:>10} {r['admit_p95_ns']:>10} "
              f"{r['fill_p50_ns']:>9} {r['cancel_p50_ns']:>11}")
    print("\nfigures in nanoseconds")
    os.makedirs("results", exist_ok=True)
    with open("results/e3_hot_path.json", "w") as f:
        json.dump(rows, f, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
