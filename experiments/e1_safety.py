"""E1: fenced schedule.

Runs the harness with generation checking enabled, over a set of seeds and an
equity path that falls at every epoch boundary. Prints the counters the
harness recorded and writes them to results/e1_safety.json.
"""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from marginstream.sim import Config, run

SEEDS = [1, 2, 3, 4, 5, 6, 7, 8]


def main():
    rows = []
    for seed in SEEDS:
        cfg = Config(seed=seed, fencing=True, equity_drop_bps=1800)
        s = run(cfg)
        rows.append({
            "seed": seed,
            "accepted": s["accepted"],
            "rejected": s["rejected"],
            "stale_rejected": s["stale_rejected"],
            "lease_exhausted": s["lease_exhausted"],
            "violations": len(s["violations"]),
            "final_M": s["final_M"],
            "final_equity": s["final_equity"],
            "subadd_gap": s["subadditivity_gap"],
        })

    total_viol = sum(r["violations"] for r in rows)
    print(f"{'seed':>4} {'acc':>6} {'rej':>6} {'stale':>6} {'exh':>6} "
          f"{'viol':>5} {'M':>10} {'equity':>10} {'gap':>8}")
    for r in rows:
        print(f"{r['seed']:>4} {r['accepted']:>6} {r['rejected']:>6} "
              f"{r['stale_rejected']:>6} {r['lease_exhausted']:>6} "
              f"{r['violations']:>5} {r['final_M']:>10} "
              f"{r['final_equity']:>10} {r['subadd_gap']:>8}")
    print(f"\nseeds={len(SEEDS)}  violations recorded = {total_viol}")

    os.makedirs("results", exist_ok=True)
    with open("results/e1_safety.json", "w") as f:
        json.dump(rows, f, indent=2)
    return 0 if total_viol == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
