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
    from marginstream.execution import execute_fill
    accepted = refused = 0
    for n, oid in enumerate(admitted):
        ok, _ = execute_fill(seqr, gw, acct, f"f{n}", oid, ACC, "A", 1,
                             mark + slip, fee_per_lot)
        accepted += 1 if ok else 0
        refused += 0 if ok else 1

    req = gw.used_risk(ACC) + risk.A_of_gross(gw.used_gross(ACC))
    worst = min(acct.equity_at(f) for f in risk.grid)
    return {"ceiling": leases[0].risk_amount, "admitted": len(admitted),
            "fills_accepted": accepted, "fills_refused": refused,
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


def d4_a_fee_above_the_cap_is_refused():
    """The envelope reserves against the venue's fee cap. Nothing currently
    stops a fill from charging more than that cap."""
    r = _drive(fee_per_lot=2, slip=0, band=0, fee_cap=1)
    return _report(
        "d4 a fee above the policy cap does not reach the account",
        r["breach"] == 0 and r["fills_accepted"] == 0
        and r["fills_refused"] > 0,
        f"cap 1 per lot, charged 2; fills accepted {r['fills_accepted']}, "
        f"refused {r['fills_refused']}; breach {r['breach']}",
    )


def d5_a_fill_outside_the_band_is_refused():
    r = _drive(fee_per_lot=0, slip=2, band=1, fee_cap=0)
    return _report(
        "d5 a fill outside the policy band does not reach the account",
        r["breach"] == 0 and r["fills_accepted"] == 0
        and r["fills_refused"] > 0,
        f"band 1 tick, filled 2 away; fills accepted {r['fills_accepted']}, "
        f"refused {r['fills_refused']}; breach {r['breach']}",
    )


def d6_a_repeated_fill_counts_once():
    """The same fill retried has to land once."""
    syms = [Symbol("A", 0, 1000, 200, 100, 5, 2)]
    risk = RiskModel(syms, addon_kappa=0, addon_scale=1)
    seqr = Sequencer()
    alloc = Allocator(risk, ttl=10 ** 6, gross_per_risk=10 ** 6)
    gw = Gateway(0, risk, sequencer=seqr)
    acct = Account(risk, 100_000)
    leases, _ = alloc.issue(ACC, acct.equity(), {0: 1}, now=0)
    gw.install_lease(leases[0])
    gen = alloc.current_generation(ACC)
    gw.admit(ACC, "A", 1, gen, order_id="o1")

    seqr.record_fill("fill-1", "o1", 1, 1000, 1)
    seqr.record_fill("fill-1", "o1", 1, 1000, 1)          # a retry
    rebuilt = seqr.rebuild_account(risk, 100_000)
    return _report(
        "d6 a fill retried under the same identifier lands once",
        rebuilt.positions() == {"A": 1} and rebuilt.fees == 1,
        f"positions {rebuilt.positions()}, fees {rebuilt.fees}",
    )


def d7_historical_execution_cost_is_not_reserved_twice():
    """Once a lease is issued against an equity that already reflects a cost,
    that cost must stop occupying the envelope. Otherwise capacity falls a
    little every generation and eventually reaches zero."""
    syms = [Symbol("A", 0, 1000, 200, 100, 5, 2)]
    risk = RiskModel(syms, addon_kappa=0, addon_scale=1)
    from marginstream.execution import execute_fill
    seqr = Sequencer()
    alloc = Allocator(risk, ttl=5, gross_per_risk=10 ** 6)
    gw = Gateway(0, risk, sequencer=seqr)
    # a small account so the execution-cost envelope is the binding one
    acct = Account(risk, 3_000)

    headroom = []
    live_orders = []
    n = 0
    now = 0
    prev = None
    for generation in range(12):
        # the protocol between terms: fence the old lease, reconcile it, then
        # issue the next against the equity that fills have already changed
        if prev is not None:
            now += 10
            seal = seqr.fence(prev)
            alloc.release(ACC, prev, seal, seqr)
        alloc.bump_generation(ACC)
        leases, _ = alloc.issue(ACC, acct.equity(), {0: 1}, now=now)
        gw.install_lease(leases[0])
        prev = leases[0].lease_id
        gen = alloc.current_generation(ACC)
        headroom.append(gw.lease[ACC].debit_at(0) - gw.used_debit(ACC))

        for j in range(3):
            n += 1
            oid = f"g{generation}-{j}"
            if not gw.admit(ACC, "A", 1, gen, order_id=oid, now=now)[0]:
                break
            if j < 2:
                execute_fill(seqr, gw, acct, f"x{n}", oid, ACC, "A", 1,
                             1000 + 5, 2)
            else:
                live_orders.append(oid)

    still_live = len(gw.live_orders(ACC))
    # headroom settles at what the live orders reserve rather than falling
    # towards zero as historical cost piles up
    return _report(
        "d7 cost already inside equity stops occupying the envelope",
        headroom[-1] > headroom[0] // 2 and still_live > 0,
        f"debit headroom per generation {headroom}; orders still live "
        f"{still_live}",
    )


CASES = [d1_a_fee_does_not_breach_a_binding_ceiling,
         d2_slippage_does_not_breach_a_binding_ceiling,
         d3_both_together,
         d4_a_fee_above_the_cap_is_refused,
         d5_a_fill_outside_the_band_is_refused,
         d6_a_repeated_fill_counts_once,
         d7_historical_execution_cost_is_not_reserved_twice]


def main():
    results = [c() for c in CASES]
    print(f"\n{sum(results)} of {len(results)} properties hold")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
