"""E2: the same schedule with order netting instead of worst-fill envelopes.

A gateway configured with `worst_fill=False` nets its live orders into a
position and prices that. The script places two opposite orders that net to
nothing, then fills one of them.
"""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from marginstream.risk import RiskModel, Symbol, FACTOR_GRID
from marginstream.allocator2 import Allocator
from marginstream.gateway2 import Gateway
from marginstream.sequencer import Sequencer

ACC = "X"


def run(worst_fill):
    syms = [Symbol("A", 0, 1000, 100, 100)]
    risk = RiskModel(syms, addon_kappa=0, addon_scale=1)
    collateral = 2_000
    seqr = Sequencer()
    alloc = Allocator(risk, ttl=1000, gross_per_risk=10_000)
    gw = Gateway(0, risk, sequencer=seqr, worst_fill=worst_fill)
    leases, _ = alloc.issue(ACC, collateral, {0: 1}, now=0)
    gw.install_lease(leases[0])
    gen = alloc.current_generation(ACC)
    ceiling = leases[0].risk_amount

    admitted = 0
    i = 0
    while True:
        i += 1
        ok, _ = gw.admit(ACC, "A", 10, gen, order_id=f"b{i}")
        if not ok:
            break
        admitted += 1
        ok, _ = gw.admit(ACC, "A", -10, gen, order_id=f"s{i}")
        if not ok:
            break
        admitted += 1
        if i > 200:
            break

    # every buy fills, no sell does
    for j in range(1, i + 1):
        gw.fill(ACC, f"b{j}", 10)

    pos = gw.filled_positions(ACC)
    m = risk.M(pos)
    gaps = [m - (collateral - risk.loss(pos, f)) for f in FACTOR_GRID]
    return {
        "worst_fill": worst_fill,
        "ceiling": ceiling,
        "orders_admitted": admitted,
        "filled": pos,
        "requirement": m,
        "collateral": collateral,
        "worst_shortfall": max(gaps),
    }


def main():
    rows = [run(True), run(False)]
    print(f"{'mode':>12} {'ceiling':>9} {'admitted':>9} {'requirement':>12} "
          f"{'collateral':>11} {'shortfall':>10}")
    for r in rows:
        name = "worst-fill" if r["worst_fill"] else "netting"
        print(f"{name:>12} {r['ceiling']:>9} {r['orders_admitted']:>9} "
              f"{r['requirement']:>12} {r['collateral']:>11} "
              f"{r['worst_shortfall']:>10}")
    print()
    for r in rows:
        name = "worst-fill" if r["worst_fill"] else "netting"
        print(f"  {name}: filled {r['filled']}")
    os.makedirs("results", exist_ok=True)
    with open("results/e2_naive_netting.json", "w") as f:
        json.dump(rows, f, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
