"""Randomised check across generations.

Each trial runs several generations against one account. Between generations
the trial may lose a lease in delivery, change the gateway weights, retire a
gateway and bring up a replacement, or advance the clock past a lease term.

After every admitted order the oracle recomputes the account's requirement from
the merged position set and compares it against equity **at every scenario in
the grid**, not against collateral alone:

    M(P) <= Collateral - loss(P, k)   for every k

The earlier version compared against collateral only and ran a single issuance,
so it exercised the static algebra and nothing else.
"""
import random
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from marginstream.risk import RiskModel, Symbol, FACTOR_GRID
from marginstream.allocator2 import Allocator
from marginstream.gateway import Gateway

ACC = "X"
TRIALS = 300
GENERATIONS = 6
ORDERS_PER_GEN = 60


def merged(gws):
    out = {}
    for gw in gws.values():
        for sym, qty in gw.local_positions(ACC).items():
            out[sym] = out.get(sym, 0) + qty
    return out


def worst_equity_breach(risk, pos, collateral):
    """Largest amount by which the requirement exceeds equity, over the grid."""
    m = risk.M(pos)
    worst = 0
    for f in FACTOR_GRID:
        equity = collateral - risk.loss(pos, f)
        if m - equity > worst:
            worst = m - equity
    return worst


def one_trial(seed):
    rng = random.Random(seed)
    n_sym = rng.randrange(2, 9)
    pool = list(range(rng.randrange(1, 5)))
    syms = [
        Symbol(f"S{i}", 0, rng.randrange(500, 3000),
               rng.randrange(20, 200), rng.randrange(30, 160))
        for i in range(n_sym)
    ]
    risk = RiskModel(syms, addon_kappa=rng.randrange(0, 3),
                     addon_scale=rng.choice([10 ** 5, 10 ** 6, 10 ** 7]))
    collateral = rng.randrange(20_000, 2_000_000)
    ttl = rng.randrange(2, 20)

    alloc = Allocator(risk, ttl=ttl, gross_per_risk=rng.randrange(5, 60))
    gws = {g: Gateway(g, risk) for g in pool}
    now = 0
    admissions = 0

    for _ in range(GENERATIONS):
        # the gateway set may change between generations
        if rng.random() < 0.3 and len(pool) < 6:
            new_id = max(pool) + 1
            pool.append(new_id)
            gws[new_id] = Gateway(new_id, risk)
        weights = {g: rng.randrange(0, 4) for g in pool}
        if sum(weights.values()) == 0:
            weights[pool[0]] = 1

        floors = {g: gws[g].used_risk(ACC) for g in pool}
        gross_floors = {g: gws[g].used_gross(ACC) for g in pool}
        alloc.bump_generation(ACC)
        leases, _scale = alloc.issue(ACC, collateral, weights,
                                     floors=floors, now=now,
                                     gross_floors=gross_floors)
        gen = alloc.current_generation(ACC)

        # some gateways do not receive their replacement
        for g, lz in leases.items():
            if rng.random() < 0.25:
                continue
            gws[g].install_lease(lz)

        for _ in range(ORDERS_PER_GEN):
            now += 1
            g = rng.choice(pool)
            sym = rng.choice(syms).name
            qty = rng.choice([-40, -13, -5, -1, 1, 5, 13, 40])
            stamped = gen if rng.random() < 0.8 else max(1, gen - 1)
            ok, _reason = gws[g].admit(ACC, sym, qty, stamped, now=now)
            if not ok:
                continue
            admissions += 1
            breach = worst_equity_breach(risk, merged(gws), collateral)
            if breach > 0:
                return (seed, breach, collateral, dict(merged(gws))), admissions

        now += rng.randrange(0, ttl + 2)

    return None, admissions


def main():
    failures = []
    total_admissions = 0
    for seed in range(TRIALS):
        f, adm = one_trial(seed)
        total_admissions += adm
        if f:
            failures.append(f)
    print(f"trials: {TRIALS}, generations per trial: {GENERATIONS}, "
          f"admissions: {total_admissions}")
    print("oracle: M(P) <= collateral - loss(P, k) for every k in the grid")
    if failures:
        print(f"FAIL: {len(failures)} trials breached")
        for f in failures[:3]:
            print(f"  seed {f[0]}: exceeded equity by {f[1]} "
                  f"(collateral {f[2]})")
            print(f"    positions {f[3]}")
        return 1
    print("no trial exceeded equity at any scenario")
    return 0


if __name__ == "__main__":
    sys.exit(main())
