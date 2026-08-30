"""Randomised check of the safety condition end to end.

Random order sequences are pushed through several gateways holding leases from
one allocator solve. After every admitted order the account's requirement is
recomputed from the merged position set and compared with collateral.

This is the property the five pinned counterexamples are instances of.
"""
import random
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from marginstream.risk import RiskModel, Symbol
from marginstream.allocator2 import Allocator
from marginstream.gateway import Gateway

ACC = "X"
TRIALS = 400
ORDERS = 300


def merged(gws):
    out = {}
    for gw in gws:
        for sym, qty in gw.local_positions(ACC).items():
            out[sym] = out.get(sym, 0) + qty
    return out


def one_trial(seed):
    rng = random.Random(seed)
    n_sym = rng.randrange(2, 9)
    n_gw = rng.randrange(1, 5)
    syms = [
        Symbol(f"S{i}", i % n_gw, rng.randrange(500, 3000),
               rng.randrange(20, 200), rng.randrange(30, 160))
        for i in range(n_sym)
    ]
    risk = RiskModel(syms, addon_kappa=rng.randrange(0, 3),
                     addon_scale=rng.choice([10**5, 10**6, 10**7]))
    collateral = rng.randrange(10_000, 2_000_000)

    alloc = Allocator(risk, ttl=10**9,
                      gross_per_risk=rng.randrange(5, 60))
    weights = {g: 1 for g in range(n_gw)}
    leases, _ = alloc.issue(ACC, collateral, weights)
    gws = [Gateway(g, risk) for g in range(n_gw)]
    for g, lz in leases.items():
        gws[g].install_lease(lz)
    gen = alloc.current_generation(ACC)

    for _ in range(ORDERS):
        g = rng.randrange(n_gw)
        sym = rng.choice(syms).name
        qty = rng.choice([-40, -13, -5, -1, 1, 5, 13, 40])
        ok, _reason = gws[g].admit(ACC, sym, qty, gen)
        if not ok:
            continue
        p = merged(gws)
        if risk.M(p) > collateral:
            return (seed, risk.M(p), collateral, dict(p))
    return None


def main():
    failures = []
    for seed in range(TRIALS):
        f = one_trial(seed)
        if f:
            failures.append(f)
    print(f"trials: {TRIALS}, orders per trial: {ORDERS}")
    if failures:
        print(f"FAIL: {len(failures)} trials breached")
        for f in failures[:3]:
            print(f"  seed {f[0]}: requirement {f[1]} against collateral {f[2]}")
            print(f"    positions {f[3]}")
        return 1
    print("no trial produced a requirement above collateral")
    return 0


if __name__ == "__main__":
    sys.exit(main())
