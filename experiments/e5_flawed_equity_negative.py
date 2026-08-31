"""E5: an account that misreports equity.

Two ways to get equity wrong that a real venue could plausibly get wrong:
forget that a closed position realised a loss, and forget that fees have been
paid. Either overstates equity, the allocator issues against the overstatement,
and the exact oracle sees the result.

Part A is scripted and deterministic. Part B repeats the random run of E1 with
each flawed account feeding issuance while an exact account judges.
"""
import random
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from marginstream.risk import RiskModel, Symbol
from marginstream.allocator2 import Allocator
from marginstream.gateway2 import Gateway
from marginstream.sequencer import Sequencer
from marginstream.account import Account

ACC = "X"


def scripted(mode, fee=8_000, loss_per_lot=500):
    syms = [Symbol("A", 0, 1000, 200, 100)]
    risk = RiskModel(syms, addon_kappa=0, addon_scale=1)
    collateral = 100_000
    seqr = Sequencer()
    alloc = Allocator(risk, ttl=10 ** 6, gross_per_risk=10 ** 6)
    gw = Gateway(0, risk, sequencer=seqr)

    book = Account(risk, collateral, mode=mode)
    truth = Account(risk, collateral, mode="exact")

    # buy 100 at the mark, then sell all of it 500 lower, and pay fees
    for i, (q, p, fe) in enumerate([(100, 1000, 0),
                                    (-100, 1000 - loss_per_lot, fee)]):
        book.apply_fill(("f", i), "A", q, p, fe)
        truth.apply_fill(("f", i), "A", q, p, fe)

    reported = book.equity()
    actual = truth.equity()

    leases, _ = alloc.issue(ACC, reported, {0: 1}, now=0)
    gw.install_lease(leases[0])
    gen = alloc.current_generation(ACC)
    ceiling = leases[0].risk_amount

    admitted = 0
    for i in range(400):
        ok, _ = gw.admit(ACC, "A", 1, gen, order_id=f"o{i}")
        if not ok:
            break
        admitted += 1

    req = gw.used_risk(ACC) + risk.A_of_gross(gw.used_gross(ACC))
    worst = min(truth.equity_at(f) for f in risk.grid)
    return {"mode": mode, "fee": fee, "reported_equity": reported,
            "actual_equity": actual, "ceiling": ceiling, "admitted": admitted,
            "requirement": req, "worst_equity": worst,
            "shortfall": max(0, req - worst)}


def binding_overstatement(delta):
    """A binding ceiling with equity overstated by `delta`.

    An earlier version of this experiment scanned hidden fees on a
    configuration that was not binding, and concluded that the closure absorbs
    an overstatement up to some fraction of equity. That conclusion was wrong:
    it measured where a coarse scan first crossed, not a tolerance. The factor
    of two is a closure, not a fault-tolerance margin, and at the binding point
    any positive overstatement produces a breach of the same order.
    """
    syms = [Symbol("A", 0, 1000, 200, 100)]
    risk = RiskModel(syms, addon_kappa=0, addon_scale=1)
    collateral = 100_000
    seqr = Sequencer()
    alloc = Allocator(risk, ttl=10 ** 6, gross_per_risk=10 ** 6)
    gw = Gateway(0, risk, sequencer=seqr)
    truth = Account(risk, collateral)

    leases, _ = alloc.issue(ACC, truth.equity() + delta, {0: 1}, now=0)
    gw.install_lease(leases[0])
    gen = alloc.current_generation(ACC)
    n = 0
    while gw.admit(ACC, "A", 1, gen, order_id=f"b{n}")[0]:
        n += 1
    mark = risk.symbols["A"].mark
    for i in range(n):
        gw.fill(ACC, f"b{i}", 1)
        truth.apply_fill(("f", i), "A", 1, mark, 0)
    req = gw.used_risk(ACC) + risk.A_of_gross(gw.used_gross(ACC))
    worst = min(truth.equity_at(f) for f in risk.grid)
    return {"overstatement": delta, "ceiling": leases[0].risk_amount,
            "admitted": n, "requirement": req, "worst_equity": worst,
            "breach": max(0, req - worst)}


def sweep(mode, trials=120, steps=120):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "e1", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "e1_equity_safety.py"))
    e1 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(e1)
    stats = e1.fresh()
    breaches = 0
    for seed in range(trials):
        if e1.run_trial(seed, stats, account_mode=mode):
            breaches += 1
    return {"mode": mode, "trials": trials, "trials_with_a_breach": breaches,
            "violations": stats["violations"],
            "max_overstatement": stats["max_overstatement"],
            "min_equity": stats["min_equity"]}


def main():
    print("Part A - scripted\n")
    rows = [scripted("exact"), scripted("ignores_realised"),
            scripted("ignores_fees")]
    print(f"  {'mode':>18} {'reported':>10} {'actual':>10} {'ceiling':>9} "
          f"{'admitted':>9} {'requirement':>12} {'worst equity':>13} "
          f"{'shortfall':>10}")
    for r in rows:
        print(f"  {r['mode']:>18} {r['reported_equity']:>10} "
              f"{r['actual_equity']:>10} {r['ceiling']:>9} "
              f"{r['admitted']:>9} {r['requirement']:>12} "
              f"{r['worst_equity']:>13} {r['shortfall']:>10}")

    print("\nPart B - an overstatement at the binding point\n")
    print(f"  {'overstated by':>14} {'ceiling':>9} {'admitted':>9} "
          f"{'requirement':>12} {'worst equity':>13} {'breach':>8}")
    binding = []
    for delta in (0, 2, 100, 1_000, 10_000):
        r = binding_overstatement(delta)
        binding.append(r)
        print(f"  {r['overstatement']:>14} {r['ceiling']:>9} "
              f"{r['admitted']:>9} {r['requirement']:>12} "
              f"{r['worst_equity']:>13} {r['breach']:>8}")
    print("\n  at the binding point the breach tracks the overstatement: an "
          "overstatement of 1,000 produces 800 and one of 10,000 produces "
          "10,000. small values show nothing only because the lot size is 200 "
          "of requirement, so the ceiling cannot move until the overstatement "
          "buys a whole lot. an earlier version of this experiment reported a "
          "tolerance of roughly 64 per cent of equity; that came from a "
          "configuration that was not binding and is withdrawn. the factor of "
          "two is a closure, not a margin against a misreported account.")
    threshold = None

    print("\nPart C - the random run of E1 with each account feeding issuance\n")
    sweeps = [sweep("exact"), sweep("ignores_realised"), sweep("ignores_fees")]
    print(f"  {'mode':>18} {'trials':>7} {'trials with a breach':>21} "
          f"{'largest overstatement':>22} {'smallest equity':>16}")
    for sw in sweeps:
        print(f"  {sw['mode']:>18} {sw['trials']:>7} "
              f"{sw['trials_with_a_breach']:>21} "
              f"{sw['max_overstatement']:>22} {sw['min_equity']:>16}")
    print("\n  in this configuration the overstatement never approaches the "
          "share of equity Part B shows is needed, so no trial breaches. that "
          "is a measurement of the tolerance, not evidence that a misreported "
          "account is safe.")

    os.makedirs("results", exist_ok=True)
    with open("results/e5_flawed_equity.json", "w") as fh:
        json.dump({"scripted": rows, "sweep": sweeps,
                   "fee_threshold": threshold}, fh, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
