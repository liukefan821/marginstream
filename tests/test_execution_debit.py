"""Execution cost after issuance.

A lease is solved against the equity the account has when it is issued. What
happens next reduces that equity in two ways the envelopes did not account for:
a fill lands at a price away from the mark, and a fee is charged. Neither moves
the position, so neither shows up in the risk or gross envelope, and both lower
the equity the requirement is measured against.

The cases below put the ceiling exactly at the binding point and then apply the
smallest possible execution cost.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from marginstream.risk import RiskModel, Symbol
from marginstream.allocator2 import Allocator
from marginstream.gateway2 import Gateway
from marginstream.sequencer import Sequencer
from marginstream.account import Account

ACC = "X"


def _report(name, ok, detail):
    print(f"[{'pass' if ok else 'FAIL'}] {name}")
    if not ok:
        print(f"        {detail}")
    return ok


def _drive(fee_per_lot, slip, band=None, fee_cap=None):
    """Fill the risk envelope, then fill every order at `slip` away from the
    mark with `fee_per_lot` charged, and report the worst breach."""
    # the venue's price band and fee cap are what bound execution cost; they
    # have to be at least as large as what actually happens
    band = slip if band is None else band
    fee_cap = fee_per_lot if fee_cap is None else fee_cap
    syms = [Symbol("A", 0, 1000, 200, 100, band, fee_cap)]
    risk = RiskModel(syms, addon_kappa=0, addon_scale=1)
    collateral = 100_000
    seqr = Sequencer()
    alloc = Allocator(risk, ttl=10 ** 6, gross_per_risk=10 ** 6)
    gw = Gateway(0, risk, sequencer=seqr)
    acct = Account(risk, collateral)

    leases, _ = alloc.issue(ACC, acct.equity(), {0: 1}, now=0)
    gw.install_lease(leases[0])
    gen = alloc.current_generation(ACC)

    admitted = []
    for i in range(4000):
        ok, _ = gw.admit(ACC, "A", 1, gen, order_id=f"o{i}")
        if not ok:
            break
        admitted.append(f"o{i}")

    mark = risk.symbols["A"].mark
    for n, oid in enumerate(admitted):
        gw.fill(ACC, oid, 1)
        acct.apply_fill(("f", n), "A", 1, mark + slip, fee_per_lot)

    req = gw.used_risk(ACC) + risk.A_of_gross(gw.used_gross(ACC))
    worst = min(acct.equity_at(f) for f in risk.grid)
    return {"ceiling": leases[0].risk_amount, "admitted": len(admitted),
            "requirement": req, "worst_equity": worst,
            "breach": max(0, req - worst), "equity": acct.equity()}


def d1_a_fee_does_not_breach_a_binding_ceiling():
    r = _drive(fee_per_lot=1, slip=0)
    return _report(
        "d1 a fee charged after issuance does not put the account past equity",
        r["breach"] == 0,
        f"ceiling {r['ceiling']}, admitted {r['admitted']}, requirement "
        f"{r['requirement']}, worst equity {r['worst_equity']}, breach "
        f"{r['breach']}",
    )


def d2_slippage_does_not_breach_a_binding_ceiling():
    r = _drive(fee_per_lot=0, slip=1)
    return _report(
        "d2 a fill one tick away from the mark does not put the account past equity",
        r["breach"] == 0,
        f"ceiling {r['ceiling']}, admitted {r['admitted']}, requirement "
        f"{r['requirement']}, worst equity {r['worst_equity']}, breach "
        f"{r['breach']}",
    )


def d3_both_together():
    r = _drive(fee_per_lot=3, slip=2)
    return _report(
        "d3 slippage and fees together do not put the account past equity",
        r["breach"] == 0,
        f"ceiling {r['ceiling']}, admitted {r['admitted']}, requirement "
        f"{r['requirement']}, worst equity {r['worst_equity']}, breach "
        f"{r['breach']}",
    )


CASES = [d1_a_fee_does_not_breach_a_binding_ceiling,
         d2_slippage_does_not_breach_a_binding_ceiling,
         d3_both_together]


def main():
    results = [c() for c in CASES]
    print(f"\n{sum(results)} of {len(results)} properties hold")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
