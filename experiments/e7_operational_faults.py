"""E7: faults injected into the liquidation path.

E6 measures what the delay costs when nothing goes wrong. This measures what
each thing going wrong adds, and, more usefully, which ones add nothing.

The base run is the fenced arm of E6 with a thirty-two tick detection delay,
which E6 shows finishing with a few hundred of equity left. A fault matrix run
comfortably inside its buffer would absorb everything and say nothing, so the
base is put where a fault has room to matter.

Faults:

    none                      the base run
    no_fence                  nothing is fenced at all. The contrast that says
                              what the fence is worth.
    fence_undelivered         the fence is taken at the ordering point and no
                              gateway is told. Both keep their leases and keep
                              submitting.
    cancel_notification_lost  the ordering point recorded every cancel and the
                              news never reached the gateways. The orders are
                              cancelled; only the local view is stale.
    cancel_never_acknowledged the matching side never confirmed the cancels, so
                              there is no record of them. Those orders are
                              still live and still able to fill.
    liquidator_authority_live the settlement is attempted while the liquidator
                              can still commit a basket.
    liquidator_crash          the liquidator dies between the ordering point
                              committing a basket and the transfer being folded
                              in locally. It is rebuilt from the log.
    ingress_crash             an ingress gateway dies part way through the
                              unwind and is rebuilt from a snapshot plus replay.
    seal_undelivered          the terminal seal for one lease never reaches the
                              allocator. The account-wide settlement does not
                              use one, so this should cost nothing.
    basket_replayed           every committed basket is submitted again under
                              the same identifier.
    late_fill                 an order admitted before the fence fills after it.

The two cancel faults are separate on purpose. Collapsing them into one name
would let a run that released nothing look identical to one that released
everything, since the difference is not in what the gateways show but in what
the log holds.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from marginstream.gateway2 import Gateway
from marginstream.execution import execute_fill, execute_basket
from marginstream import liquidation as L
from e6_liquidation_delay import Run, ACC

DELAY = 32
ACK = 1
CAP = 40

FAULTS = ["none", "no_fence", "fence_undelivered",
          "cancel_notification_lost", "cancel_never_acknowledged",
          "liquidator_authority_live", "liquidator_crash", "ingress_crash",
          "seal_undelivered", "basket_replayed", "late_fill"]


def build(seed=1, rate=40):
    r = Run(seed, rate=rate)
    r.load(caps=(None, 20))
    t = 0
    while (L.shortfall(r.risk, r.liq.all_gateways(), ACC, r.acct) == 0
           and t < 4000):
        r.move_market()
        t += 1
    return r, t


def run(fault, seed=1):
    r, trigger_tick = build(seed)
    notes = []
    counters = {"requirement_rises_from_the_basket": 0,
                "requirement_rises_from_everything_else": 0,
                "recovery_checks": 0, "recovery_failures": 0,
                "late_fills_accepted": 0, "duplicate_baskets_landed": 0}
    start_equity = r.acct.equity()
    trigger = {
        "tick": trigger_tick,
        "equity": start_equity,
        "requirement": r.liq.requirement(),
        "shortfall": L.shortfall(r.risk, r.liq.all_gateways(), ACC, r.acct),
    }
    r.drift = r.slip_resting = r.slip_unwind = 0
    r.fee_resting = r.fee_unwind = 0

    for _ in range(DELAY):
        r.tick(admitting=True)

    if fault == "no_fence":
        fenced = []
    else:
        fenced = r.liq.fence_all(deliver=(fault != "fence_undelivered"))
    bound = r.liq.debit_bound()

    for _ in range(ACK):
        r.tick(admitting=True)

    if fault == "cancel_notification_lost":
        r.liq.cancel_all(lose=lambda _oid: True,
                         lose_mode="notification_lost")
    elif fault == "cancel_never_acknowledged":
        # the matching side is unresponsive for these orders: it confirms no
        # cancel and reports no fill. They simply sit there, which is the state
        # the settlement has to keep a reservation for.
        stuck = {oid for g in r.gws for oid in g.live_orders(ACC)}
        r.no_fill |= stuck
        r.liq.cancel_all(lose=lambda oid: oid in stuck,
                         lose_mode="never_acknowledged")
        notes.append(f"{len(stuck)} orders left with no cancel acknowledgement "
                     f"and no fill")
    elif fault == "late_fill":
        held = None
        for g in r.gws:
            live = list(g.live_orders(ACC).items())
            if live:
                held = (g, live[0])
                break
        r.liq.cancel_all(lose=lambda oid: held is not None
                         and oid == held[1][0])
        if held is not None:
            g, (oid, (sym, rem)) = held
            _s, _q, admitted_mark, band, cap, _p = r.seqr.terms[oid]
            price = admitted_mark - band if rem < 0 else admitted_mark + band
            fee = cap * abs(rem)
            r.fill_no += 1
            ok, why = execute_fill(r.seqr, g, r.acct, f"late{r.fill_no}", oid,
                                   ACC, sym, rem, price, fee)
            if ok:
                counters["late_fills_accepted"] += 1
                r.record_fill(sym, rem, price, fee, unwind=False)
            notes.append(f"a fill under a fenced lease: {ok} ({why})")
    else:
        r.liq.cancel_all()

    steps = 0
    crashed = False
    while not r.liq.flat() and steps < 400:
        hook = None
        if fault == "liquidator_crash" and steps == 2 and not crashed:
            def hook():
                raise RuntimeError("lost between commit and fold")

        before_step = r.liq.requirement()
        try:
            out = r.liq.unwind_step(1, 4, lots_cap=CAP, after_commit=hook)
        except RuntimeError:
            crashed = True
            # the basket is in the log and was never folded in locally. the
            # ledger is behind too, so both are rebuilt from the log.
            rebuilt = Gateway.rebuild_from_log(99, r.risk, r.seqr,
                                               incarnation=0)
            counters["recovery_checks"] += 1
            ledger = r.seqr.rebuild_account(r.risk, r.acct.collateral)
            if (rebuilt.filled_positions(ACC)
                    == r.liq.liquidator.filled_positions(ACC)):
                counters["recovery_failures"] += 1
                notes.append("the rebuild did not include the committed "
                             "basket")
            # the legs the crash skipped still moved equity, so they are folded
            # into the decomposition from the log
            last = [e for e in r.seqr.events if e[0] == "basket"][-1]
            for sym, qty, price, fee in last[6]:
                r.record_fill(sym, qty, price, fee, unwind=True)
            rebuilt.install_lease(r.liq.liquidator.lease[ACC])
            r.liq.liquidator = rebuilt
            r.acct.restore(ledger.snapshot())
            notes.append(f"rebuilt from the log after the crash: position "
                         f"{rebuilt.filled_positions(ACC)}")
            r.tick(admitting=True, filling=True)
            steps += 1
            continue

        if out is None or out[0] != "committed":
            break
        _kind, basket_id, legs = out
        for sym, qty, price, fee in legs:
            r.record_fill(sym, qty, price, fee, unwind=True)
        if r.liq.requirement() > before_step:
            counters["requirement_rises_from_the_basket"] += 1

        if fault == "basket_replayed":
            lid = r.liq.liquidator_lease_id
            terms = tuple((r.risk.symbols[s].mark, r.risk.symbols[s].band,
                           r.risk.symbols[s].fee_per_lot)
                          for s, _q, _p, _f in legs)
            seq = r.seqr.last_seq.get(lid, 0) + 1
            position_before = dict(r.acct.positions())
            again, why = execute_basket(r.seqr, r.liq.liquidator, r.acct, lid,
                                        seq, basket_id, ACC, legs, terms)
            if again and dict(r.acct.positions()) != position_before:
                counters["duplicate_baskets_landed"] += 1

        if fault == "ingress_crash" and steps == 3:
            snap = r.gws[0].snapshot()
            rebuilt = Gateway(0, r.risk, sequencer=r.seqr)
            ok, msg = rebuilt.restore(snap, r.seqr)
            reference = Gateway.rebuild_from_log(0, r.risk, r.seqr)
            counters["recovery_checks"] += 1
            if (not ok
                    or rebuilt.state_digest(ACC) != reference.state_digest(ACC)
                    or rebuilt.aggregate_digest(ACC)
                    != reference.aggregate_digest(ACC)):
                counters["recovery_failures"] += 1
                notes.append(f"ingress recovery: {ok} {msg}")
            if ACC in r.gws[0].lease:
                rebuilt.install_lease(r.gws[0].lease[ACC])
            r.gws[0] = rebuilt
            r.liq.gateways[0] = rebuilt

        before_tick = r.liq.requirement()
        r.tick(admitting=True, filling=True)
        if r.liq.requirement() > before_tick:
            counters["requirement_rises_from_everything_else"] += 1
        steps += 1

    refused = r.seqr.rejected

    if fault == "seal_undelivered" and fenced:
        r.liq.seals.pop(fenced[0], None)
    per_lease = r.liq.reconcile()

    settled, settle_why = r.liq.settle(
        fence_liquidator=(fault != "liquidator_authority_live"))
    notes.append(f"settle: {settled} ({settle_why})")
    if fault == "liquidator_authority_live" and not settled:
        r.alloc.settling.discard(ACC)
        settled_after, why_after = r.liq.settle()
        notes.append(f"settle after fencing the liquidator: {settled_after} "
                     f"({why_after})")

    account_view = r.seqr.reconcile_account(ACC, r.risk)
    r.alloc.bump_generation(ACC)
    capacity = r.alloc.solve_scale(ACC, 400_000, {0: 1, 1: 1, 5: 1},
                                   now=trigger_tick + 500)

    end, predicted = r.check_identity(start_equity)
    return {
        "fault": fault,
        "trigger": trigger,
        "flat": r.liq.flat(),
        "live_orders_at_end": r.liq.live_orders_remaining(),
        "unwind_steps": steps,
        "drift": r.drift,
        "execution_cost": (r.slip_resting + r.slip_unwind
                           + r.fee_resting + r.fee_unwind),
        "equity_end": end,
        "identity_ok": end == predicted,
        "identity_gap": end - predicted,
        "insurance_draw": max(0, -end),
        "debit_bound_at_fence": bound,
        "unwind_over_bound": r.slip_unwind + r.fee_unwind - bound,
        "refused_at_ordering_point": refused,
        "settled": settled,
        "settle_reason": settle_why,
        "account_view": list(account_view),
        "capacity_after": capacity,
        "cancels_recorded_only": r.liq.cancels_recorded_only,
        "cancels_never_acknowledged": r.liq.cancels_never_acknowledged,
        "per_lease_reconciliation": {str(k): list(v)
                                     for k, v in per_lease.items()},
        "notes": notes,
        **counters,
    }


def main():
    rows = [run(f) for f in FAULTS]
    base = rows[0]
    print(f"base run: trigger at tick {base['trigger']['tick']}, equity "
          f"{base['trigger']['equity']}, shortfall "
          f"{base['trigger']['shortfall']}; detection delay {DELAY} ticks, "
          f"cancel round trip {ACK} tick, unwind {CAP} lots per transfer\n")

    print(f"{'fault':>26} {'flat':>5} {'live':>5} {'steps':>6} {'drift':>9} "
          f"{'execution':>10} {'end equity':>11} {'draw':>7} {'refused':>8} "
          f"{'M up b/o':>9} {'id':>3}")
    for r in rows:
        print(f"{r['fault']:>26} {str(r['flat']):>5} "
              f"{r['live_orders_at_end']:>5} {r['unwind_steps']:>6} "
              f"{r['drift']:>9} {-r['execution_cost']:>10} "
              f"{r['equity_end']:>11} {r['insurance_draw']:>7} "
              f"{r['refused_at_ordering_point']:>8} "
              f"{r['requirement_rises_from_the_basket']:>4}"
              f"/{r['requirement_rises_from_everything_else']:<4} "
              f"{'ok' if r['identity_ok'] else 'NO':>3}")
    print("\n'live' is orders still able to fill at the end. 'refused' counts "
          "submissions the ordering\npoint turned away. 'M up b/o' counts "
          "steps after which the merged requirement rose:\nfirst from the "
          "liquidator's own basket, then from everything else in the tick.")

    print("\nsettlement:")
    for r in rows:
        print(f"  {r['fault']:>26}: settled {str(r['settled']):>5}, account "
              f"figure (risk, gross, debit) {tuple(r['account_view'])}, "
              f"capacity afterwards {r['capacity_after']}")

    print("\nreason, where the settlement did not run on the first attempt:")
    for r in rows:
        if not r["settled"]:
            print(f"  {r['fault']:>26}: {r['settle_reason']}")

    print("\ncancel outcomes and per-lease reconciliation:")
    for r in rows:
        refused_leases = [k for k, v in r["per_lease_reconciliation"].items()
                          if not v[0]]
        print(f"  {r['fault']:>26}: recorded-only {r['cancels_recorded_only']}, "
              f"never acknowledged {r['cancels_never_acknowledged']}, "
              f"per-lease reconciliation refused for "
              f"{refused_leases or 'none'}")

    print("\nrecovery and idempotence:")
    for r in rows:
        if (r["recovery_checks"] or r["duplicate_baskets_landed"]
                or r["late_fills_accepted"]):
            print(f"  {r['fault']:>26}: recovery checks "
                  f"{r['recovery_checks']}, failures "
                  f"{r['recovery_failures']}, duplicate baskets that moved the "
                  f"position {r['duplicate_baskets_landed']}, fills accepted "
                  f"under a fenced lease {r['late_fills_accepted']}")

    print("\nunwind cost against the bound taken when authority ends:")
    for r in rows:
        print(f"  {r['fault']:>26}: bound {r['debit_bound_at_fence']}, over by "
              f"{r['unwind_over_bound']}")

    for r in rows:
        for note in r["notes"]:
            print(f"  note ({r['fault']}): {note}")

    # what would be a defect rather than the fault doing its job
    bad = []
    for r in rows:
        if not r["identity_ok"]:
            bad.append((r["fault"], "equity identity"))
        if r["recovery_failures"]:
            bad.append((r["fault"], "recovery diverged"))
        if r["duplicate_baskets_landed"]:
            bad.append((r["fault"], "a duplicate basket moved the position"))
        if r["requirement_rises_from_the_basket"]:
            bad.append((r["fault"], "the liquidator's own basket raised the "
                                    "requirement"))
    if rows[FAULTS.index("no_fence")]["settled"]:
        bad.append(("no_fence", "settled with authority still live"))
    if not rows[FAULTS.index("seal_undelivered")]["settled"]:
        bad.append(("seal_undelivered", "an undelivered seal blocked the "
                                        "account-wide settlement"))
    if rows[FAULTS.index("liquidator_authority_live")]["settled"]:
        bad.append(("liquidator_authority_live",
                    "settled while the liquidator could still commit"))
    cna = rows[FAULTS.index("cancel_never_acknowledged")]
    if cna["account_view"][0] == 0 or cna["account_view"][2] == 0:
        bad.append(("cancel_never_acknowledged",
                    "an unacknowledged cancel released its reservation"))
    cnl = rows[FAULTS.index("cancel_notification_lost")]
    if cnl["account_view"] != [0, 0, 0]:
        bad.append(("cancel_notification_lost",
                    "a recorded cancel did not release its reservation"))

    if bad:
        print("\nFAIL")
        for f in bad:
            print(f"  {f[0]}: {f[1]}")
        return 1
    print("\nno fault broke the equity identity, produced a divergent "
          "recovery, landed a duplicate basket,\nor let the liquidator's own "
          "basket raise the requirement. The settlement ran exactly where\nit "
          "should and refused exactly where it should.")
    os.makedirs("results", exist_ok=True)
    with open("results/e7_operational_faults.json", "w") as fh:
        json.dump(rows, fh, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
