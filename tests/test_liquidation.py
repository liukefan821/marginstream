"""The liquidation path.

An account can only end up with a requirement above its equity through a move
larger than the scenario grid covers, because every move inside the grid is
what the closure reserves for. So these cases start from a book at the binding
point, move the market past the grid, and then exercise the path.

The unwind is an atomic internal transfer, not a set of orders on the sharded
books. l3 is why: leg at a time cannot reduce a hedged book, and a basket that
could partially fill across shards would leave the account somewhere the check
never approved.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from marginstream.risk import RiskModel, Symbol
from marginstream.allocator2 import Allocator
from marginstream.gateway2 import Gateway
from marginstream.sequencer import Sequencer
from marginstream.account import Account
from marginstream.execution import execute_fill, execute_basket
from marginstream import liquidation as L

ACC = "X"


def _report(name, ok, detail):
    print(f"[{'pass' if ok else 'FAIL'}] {name}")
    if not ok:
        print(f"        {detail}")
    return ok


def _venue(kappa=1, scale=10 ** 7, collateral=400_000, gross_per_risk=8,
           ttl=10 ** 6, n_gateways=2):
    syms = [Symbol("A", 0, 1000, 200, 100, 5, 2),
            Symbol("B", 0, 1500, 150, 80, 7, 2)]
    risk = RiskModel(syms, addon_kappa=kappa, addon_scale=scale)
    seqr = Sequencer()
    alloc = Allocator(risk, sequencer=seqr, ttl=ttl, gross_per_risk=gross_per_risk)
    gws = [Gateway(i, risk, sequencer=seqr) for i in range(n_gateways)]
    acct = Account(risk, collateral)
    leases, _scale = alloc.issue(ACC, acct.equity(),
                                 {i: 1 for i in range(n_gateways)}, now=0)
    for g in gws:
        g.install_lease(leases[g.id])
    return risk, seqr, alloc, gws, acct, leases


def _liquidator(risk, seqr, alloc, gws, acct):
    liq_gw = Gateway(99, risk, sequencer=seqr, fencing=False)
    liq_gw.install_lease(alloc.issue_liquidation_lease(ACC, 99))
    return liq_gw, L.Liquidation(risk, seqr, alloc, ACC, acct, gws, liq_gw)


def _load_short(risk, seqr, alloc, gws, acct, leave_resting=3, cap=None):
    """Build a short position, leaving a few orders live.

    `cap` stops short of the ceiling, for the cases that need an admission to
    be refused by the fence rather than by the envelope.
    """
    gen = alloc.current_generation(ACC)
    n = 0
    for g in gws:
        while g.admit(ACC, "A", -1, gen, order_id=f"g{g.id}:{n}")[0]:
            n += 1
            if cap is not None and n >= cap:
                break
    mark = risk.symbols["A"].mark
    for g in gws:
        live = list(g.live_orders(ACC).items())
        keep = live[len(live) - leave_resting:] if leave_resting else []
        keep_ids = {oid for oid, _ in keep}
        for oid, (sym, rem) in [x for x in live if x[0] not in keep_ids]:
            execute_fill(seqr, g, acct, f"f{oid}", oid, ACC, sym, rem,
                         mark - risk.symbols[sym].band, 2 * abs(rem))
    return n


def _move(risk, gateways, grid_multiples):
    """Move the marks by `grid_multiples` times the widest grid step."""
    f = max(risk.grid) * grid_multiples
    risk.reprice(risk.displaced_marks(f))
    for g in gateways:
        g.reprice()


def _unwind(liq, limit=500):
    steps = 0
    while not liq.flat() and steps < limit:
        r = liq.unwind_step(1, 4)
        if r is None or r[0] != "committed":
            break
        steps += 1
    return steps


def l1_a_fence_stops_admission_without_reaching_the_gateway():
    """The gateway keeps its lease, its clock is inside the term, and it has
    not been told anything. The ordering point is where it stops."""
    risk, seqr, alloc, gws, acct, leases = _venue()
    gen = alloc.current_generation(ACC)
    liq_gw, liq = _liquidator(risk, seqr, alloc, gws, acct)
    _load_short(risk, seqr, alloc, gws, acct, cap=40)

    before, _why = gws[0].admit(ACC, "A", -1, gen, order_id="pre")
    liq.fence_all(deliver=False)
    still_installed = gws[0].lease.get(ACC) is not None
    after, why = gws[0].admit(ACC, "A", -1, gen, now=0, order_id="post")

    return _report(
        "l1 a fence stops admission without reaching the gateway",
        before and (not after) and still_installed and why == "lease_fenced",
        f"before the fence {before}; after {after} ({why}); the gateway still "
        f"holds its lease: {still_installed}")


def l2_a_fence_does_not_lower_the_requirement():
    """Fencing ends authority. The orders already on a book are still able to
    fill, so the exposure is exactly where it was."""
    risk, seqr, alloc, gws, acct, leases = _venue()
    liq_gw, liq = _liquidator(risk, seqr, alloc, gws, acct)
    _load_short(risk, seqr, alloc, gws, acct, leave_resting=4)

    before = liq.requirement()
    live_before = sum(len(g.live_orders(ACC)) for g in gws)
    liq.fence_all(deliver=False)
    after = liq.requirement()
    live_after = sum(len(g.live_orders(ACC)) for g in gws)

    oid, (sym, rem) = list(gws[0].live_orders(ACC).items())[0]
    ok, why = execute_fill(seqr, gws[0], acct, "postfence", oid, ACC, sym, rem,
                           risk.symbols[sym].mark, 0)

    return _report(
        "l2 a fence does not lower the requirement or the live orders",
        before == after and live_before == live_after and ok,
        f"requirement {before} then {after}; live orders {live_before} then "
        f"{live_after}; a fill after the fence: {ok} ({why})")


def l3_one_leg_at_a_time_is_refused_on_a_hedged_book():
    """c9 with the liquidator in the gateway's place, and the reason a basket
    has to settle as one record: closing one leg while its offset stays put
    raises the account's requirement."""
    syms = [Symbol("A", 0, 1000, 200, 100, 5, 2),
            Symbol("B", 0, 1000, 200, 100, 5, 2)]
    risk = RiskModel(syms, addon_kappa=1, addon_scale=10 ** 7)
    seqr = Sequencer()
    alloc = Allocator(risk, sequencer=seqr, ttl=10 ** 6, gross_per_risk=20)
    gws = [Gateway(0, risk, sequencer=seqr), Gateway(1, risk, sequencer=seqr)]
    acct = Account(risk, 10 ** 6)
    leases, _ = alloc.issue(ACC, acct.equity(), {0: 1, 1: 1}, now=0)
    gws[0].install_lease(leases[0])
    gws[1].install_lease(leases[1])
    gen = alloc.current_generation(ACC)
    for i in range(37):
        gws[0].admit(ACC, "A", 1, gen, order_id=f"a{i}")
        gws[1].admit(ACC, "B", -1, gen, order_id=f"b{i}")
    for i in range(37):
        execute_fill(seqr, gws[0], acct, f"fa{i}", f"a{i}", ACC, "A", 1, 1005, 2)
        execute_fill(seqr, gws[1], acct, f"fb{i}", f"b{i}", ACC, "B", -1, 995, 2)
    liq_gw, liq = _liquidator(risk, seqr, alloc, gws, acct)

    single_ok, single_before, single_after = liq.check({"A": -10})
    basket = liq.propose(1, 4)
    basket_ok, basket_before, basket_after = liq.check(basket)

    liq.fence_all()
    liq.cancel_all()
    steps = _unwind(liq)

    return _report(
        "l3 a single leg is refused on a hedged book, the basket is not",
        (not single_ok) and basket_ok and liq.flat() and liq.stalls == 0,
        f"single leg: requirement {single_before[0]} -> {single_after[0]}, "
        f"refused {not single_ok}; basket {basket}: {basket_before[0]} -> "
        f"{basket_after[0]}; unwound to flat in {steps} transfers")


def l4_the_unwind_cost_stays_inside_its_arithmetic_bound():
    """The liquidator is not covered by the debit envelope, which was sized for
    the order set that existed before the trigger. What does bound it is that
    it only reduces: at most `sum_s lots_s * (band_s + fee_s)` over the
    reachable position."""
    risk, seqr, alloc, gws, acct, leases = _venue()
    liq_gw, liq = _liquidator(risk, seqr, alloc, gws, acct)
    _load_short(risk, seqr, alloc, gws, acct)
    _move(risk, gws + [liq_gw], 2)

    liq.fence_all()
    liq.cancel_all()
    bound = liq.debit_bound()
    equity_at_trigger = acct.equity()
    marks_at_trigger = acct.marks()

    steps = _unwind(liq)

    # the marks have not moved during the unwind, so the whole equity change is
    # execution cost
    spent = equity_at_trigger - acct.equity(marks_at_trigger)
    return _report(
        "l4 the unwind cost stays inside its arithmetic bound",
        liq.flat() and 0 <= spent <= bound,
        f"bound {bound}, spent {spent}, transfers {steps}, flat {liq.flat()}")


def l5_the_requirement_never_rises_during_the_unwind():
    risk, seqr, alloc, gws, acct, leases = _venue()
    liq_gw, liq = _liquidator(risk, seqr, alloc, gws, acct)
    _load_short(risk, seqr, alloc, gws, acct)
    _move(risk, gws + [liq_gw], 2)
    liq.fence_all()
    liq.cancel_all()

    seen = [liq.requirement()]
    steps = 0
    while not liq.flat() and steps < 500:
        r = liq.unwind_step(1, 4)
        if r is None or r[0] != "committed":
            break
        seen.append(liq.requirement())
        steps += 1
    rises = [(a, b) for a, b in zip(seen, seen[1:]) if b > a]
    return _report(
        "l5 the merged requirement never rises during the unwind",
        not rises and liq.flat(),
        f"{len(seen)} observations from {seen[0]} to {seen[-1]}; "
        f"rises {rises[:3]}")


def l6_an_unreconciled_fence_keeps_its_capacity():
    """Fencing produces the seal. It does not release anything on its own: the
    position is still there, and a per-lease release is what lowers that
    lease's figure. A replacement brought up before that gets nothing."""
    risk, seqr, alloc, gws, acct, leases = _venue()
    liq_gw, liq = _liquidator(risk, seqr, alloc, gws, acct)
    _load_short(risk, seqr, alloc, gws, acct)
    usage = {g.id: (g.used_risk(ACC), g.used_gross(ACC)) for g in gws}
    alloc.observe_usage(ACC, usage, seq=1)
    liq.fence_all()

    alloc.bump_generation(ACC)
    fresh, _scale = alloc.issue(ACC, acct.equity(), {0: 1, 1: 1, 2: 1}, now=1)
    replacement = fresh.get(2)

    outcomes = liq.reconcile()

    return _report(
        "l6 a fence on its own does not hand capacity to a replacement",
        replacement is not None and replacement.risk_amount == 0
        and all(ok for ok, _why in outcomes.values()),
        f"replacement ceiling before reconciliation "
        f"{replacement.risk_amount if replacement else None}; "
        f"per-lease reconciliation {outcomes}")


def l7_a_replayed_basket_lands_once():
    """A basket is committed under an identifier. A retry carrying the same
    payload succeeds again and moves nothing; a retry carrying different
    figures is a conflict and moves nothing either."""
    risk, seqr, alloc, gws, acct, leases = _venue()
    liq_gw, liq = _liquidator(risk, seqr, alloc, gws, acct)
    _load_short(risk, seqr, alloc, gws, acct)
    _move(risk, gws + [liq_gw], 2)
    liq.fence_all()
    liq.cancel_all()

    basket = liq.propose(1, 4)
    basket_id, legs = liq.commit(basket)
    after_first = dict(acct.positions())

    lid = liq.liquidator_lease_id
    terms = tuple((risk.symbols[s].mark, risk.symbols[s].band,
                   risk.symbols[s].fee_per_lot) for s, _q, _p, _f in legs)
    seq = seqr.last_seq.get(lid, 0) + 1
    again, why_again = execute_basket(seqr, liq_gw, acct, lid, seq, basket_id,
                                      ACC, legs, terms)
    after_retry = dict(acct.positions())

    tampered = tuple((s, q, p + 1, f) for s, q, p, f in legs)
    conflict, why_conflict = execute_basket(seqr, liq_gw, acct, lid, seq,
                                            basket_id, ACC, tampered, terms)
    after_conflict = dict(acct.positions())

    return _report(
        "l7 a replayed basket lands once and a tampered one lands not at all",
        again and why_again == "idempotent_retry"
        and after_first == after_retry == after_conflict
        and not conflict and why_conflict == "conflicting_basket_payload",
        f"retry {again} ({why_again}), conflict {conflict} ({why_conflict}); "
        f"position {after_first} then {after_retry} then {after_conflict}")


def l8_the_seal_covers_every_admission_the_log_holds():
    risk, seqr, alloc, gws, acct, leases = _venue()
    liq_gw, liq = _liquidator(risk, seqr, alloc, gws, acct)
    _load_short(risk, seqr, alloc, gws, acct)
    _move(risk, gws + [liq_gw], 2)
    fenced = liq.fence_all()
    liq.cancel_all()
    _unwind(liq)

    mismatches = []
    for lease_id in fenced:
        seal = liq.seals[lease_id]
        recorded = seqr.last_seq.get(lease_id, 0)
        if seal.terminal_seq != recorded:
            mismatches.append((lease_id, seal.terminal_seq, recorded))
    outcomes = liq.reconcile()

    return _report(
        "l8 every seal covers the admissions the ordering point recorded",
        liq.flat() and not mismatches
        and all(ok for ok, _why in outcomes.values()),
        f"leases {fenced}, mismatches {mismatches}, flat {liq.flat()}, "
        f"per-lease reconciliation {outcomes}")


def l9_per_lease_occupancy_does_not_net_and_the_account_view_does():
    """After a liquidation the account is flat, but each lease reconciles to
    its own gross position: the shorts sit under the ingress leases and the
    offsetting longs under the liquidator's basket. Summing them says the
    account is fully occupied when it holds nothing."""
    risk, seqr, alloc, gws, acct, leases = _venue()
    liq_gw, liq = _liquidator(risk, seqr, alloc, gws, acct)
    _load_short(risk, seqr, alloc, gws, acct)
    _move(risk, gws + [liq_gw], 2)
    fenced = liq.fence_all()
    liq.cancel_all()
    _unwind(liq)

    per_lease = [seqr.reconcile(lid, risk) for lid in fenced]
    summed = (sum(a for a, _b in per_lease), sum(b for _a, b in per_lease))
    account_view = seqr.reconcile_account(ACC, risk)
    position = acct.positions()

    return _report(
        "l9 per-lease occupancy does not net, the account view does",
        liq.flat() and not position and summed[0] > 0
        and account_view == (0, 0, 0),
        f"position {position}; per-lease figures {per_lease} summing to "
        f"{summed}; the account's own figure (risk, gross, debit) "
        f"{account_view}")


def l10_settling_needs_the_barrier_and_returns_the_capacity():
    """The fence is the evidence, not a clock and not a report. The figures
    compared are the allocator's own committed exposure, before and after: a
    per-lease release sets it without netting, the settlement replaces it with
    the account's figure taken from the log at the barrier."""
    risk, seqr, alloc, gws, acct, leases = _venue()
    liq_gw, liq = _liquidator(risk, seqr, alloc, gws, acct)
    _load_short(risk, seqr, alloc, gws, acct)
    _move(risk, gws + [liq_gw], 2)

    not_settling, why_not = alloc.settle(ACC, seqr, risk)

    liq.fence_all()
    liq.cancel_all()
    _unwind(liq)
    liq.reconcile()

    committed_before = sum(v[0] for v in alloc.committed[ACC].values())
    alloc.bump_generation(ACC)
    scale_before = alloc.solve_scale(ACC, 60_000, {0: 1, 1: 1}, now=10 ** 6)

    ok, msg = liq.settle()
    committed_after = sum(v[0] for v in alloc.committed[ACC].values())
    alloc.bump_generation(ACC)
    scale_after = alloc.solve_scale(ACC, 60_000, {0: 1, 1: 1}, now=10 ** 6)

    return _report(
        "l10 settling needs the barrier and returns the capacity",
        (not not_settling) and why_not == "not_settling" and ok
        and liq.flat() and committed_before > 0 and committed_after == 0
        and (scale_after or 0) > (scale_before or 0),
        f"outside a settling window: {not_settling} ({why_not}); "
        f"settlement: {ok} ({msg}); committed risk {committed_before} -> "
        f"{committed_after}; capacity at 60,000 of equity {scale_before} -> "
        f"{scale_after}")


def l11_a_live_liquidator_blocks_the_settlement():
    """Every ingress lease is fenced and the position is flat, but the
    liquidator can still commit a basket. That is admission authority like any
    other, and compaction is refused until it is terminal too."""
    risk, seqr, alloc, gws, acct, leases = _venue()
    liq_gw, liq = _liquidator(risk, seqr, alloc, gws, acct)
    _load_short(risk, seqr, alloc, gws, acct)
    _move(risk, gws + [liq_gw], 2)
    liq.fence_all()
    liq.cancel_all()
    _unwind(liq)

    ingress = sorted(liq.fenced)
    all_ingress_fenced = all(seqr.is_fenced(l) for l in ingress)
    refused, why = liq.settle(fence_liquidator=False)
    liquidator_still_live = not seqr.is_fenced(liq.liquidator_lease_id)

    # and it really could still act: the refusal is the right answer because
    # the authority is not hypothetical
    alloc.settling.discard(ACC)
    still_commits, _legs = liq.commit({"A": -1})

    ok, msg = liq.settle()
    return _report(
        "l11 a live liquidator blocks the settlement",
        all_ingress_fenced and (not refused)
        and why.startswith("authority_still_live")
        and liquidator_still_live and still_commits is not None and ok,
        f"ingress leases {ingress} all fenced: {all_ingress_fenced}; "
        f"settlement refused: {refused} ({why}); the liquidator could still "
        f"commit: {still_commits is not None}; after fencing it: {ok} ({msg})")


def l12_a_cancel_recorded_but_not_notified_releases_the_order():
    """The ordering point recorded the cancel and the gateway was never told.
    Nothing can fill against that order, so the settlement releases it. The
    gateway's local view is stale and the log is not."""
    risk, seqr, alloc, gws, acct, leases = _venue()
    liq_gw, liq = _liquidator(risk, seqr, alloc, gws, acct)
    _load_short(risk, seqr, alloc, gws, acct, leave_resting=4)
    _move(risk, gws + [liq_gw], 2)
    liq.fence_all()
    liq.cancel_all(lose=lambda _oid: True, lose_mode="notification_lost")
    _unwind(liq)

    stale_local_view = liq.live_orders_remaining()
    ok, msg = liq.settle()
    figure = seqr.reconcile_account(ACC, risk)

    return _report(
        "l12 a cancel recorded but not notified releases the order",
        ok and liq.cancels_recorded_only > 0 and stale_local_view > 0
        and figure == (0, 0, 0),
        f"{liq.cancels_recorded_only} cancels recorded with no notification; "
        f"the gateways still show {stale_local_view} orders live; the "
        f"settlement figure (risk, gross, debit) is {figure}; {msg}")


def l13_a_cancel_never_acknowledged_keeps_its_reservation():
    """The matching side never confirmed, so there is no record and the order
    can still fill. The settlement keeps its worst-fill risk and the execution
    cost still ahead of it. Fencing does not help: it stops new admissions and
    does nothing to an order already resting."""
    risk, seqr, alloc, gws, acct, leases = _venue()
    liq_gw, liq = _liquidator(risk, seqr, alloc, gws, acct)
    _load_short(risk, seqr, alloc, gws, acct, leave_resting=4)
    _move(risk, gws + [liq_gw], 2)
    liq.fence_all()
    liq.cancel_all(lose=lambda _oid: True, lose_mode="never_acknowledged")
    _unwind(liq)

    still_live = liq.live_orders_remaining()
    ok, msg = liq.settle()
    r, g, d = seqr.reconcile_account(ACC, risk)

    expected_debit = 0
    for gw in gws:
        for _oid, (sym, rem) in gw.live_orders(ACC).items():
            expected_debit += abs(rem) * risk.debit_per_lot(sym)

    return _report(
        "l13 a cancel never acknowledged keeps its reservation",
        ok and liq.cancels_never_acknowledged > 0 and still_live > 0
        and r > 0 and d == expected_debit and d > 0
        and alloc.settled_debit[ACC] == d,
        f"{liq.cancels_never_acknowledged} cancels never acknowledged; "
        f"{still_live} orders still live; the settlement figure "
        f"(risk, gross, debit) is {(r, g, d)} against an expected debit of "
        f"{expected_debit}; {msg}")


def l14_a_crash_between_commit_and_fold_recovers_from_the_log():
    """A basket is one record. A process that dies after the ordering point
    committed it and before it folded the transfer locally leaves a local state
    behind the log; a rebuild from the log is what the position actually is."""
    risk, seqr, alloc, gws, acct, leases = _venue()
    liq_gw, liq = _liquidator(risk, seqr, alloc, gws, acct)
    _load_short(risk, seqr, alloc, gws, acct)
    _move(risk, gws + [liq_gw], 2)
    liq.fence_all()
    liq.cancel_all()
    _unwind(liq, limit=3)

    crashed = {}

    def die():
        crashed["at"] = seqr.position()
        raise RuntimeError("liquidator lost between commit and fold")

    try:
        liq.unwind_step(1, 4, after_commit=die)
    except RuntimeError:
        pass

    unfolded = dict(liq_gw.filled_positions(ACC))
    rebuilt = Gateway.rebuild_from_log(99, risk, seqr, incarnation=0)
    from_log = dict(rebuilt.filled_positions(ACC))
    ledger_from_log = seqr.rebuild_account(risk, acct.collateral)

    # the recovered gateway carries on, and the basket that was already
    # committed is not applied a second time
    liq.liquidator = rebuilt
    rebuilt.install_lease(liq_gw.lease[ACC])
    after_recovery = dict(rebuilt.filled_positions(ACC))

    return _report(
        "l14 a crash between commit and fold recovers from the log",
        "at" in crashed and from_log != unfolded
        and after_recovery == from_log
        and ledger_from_log.equity() != acct.equity(),
        f"the fold never happened locally: gateway held {unfolded}, the log "
        f"implies {from_log}; after recovery {after_recovery}; the ledger "
        f"rebuilt from the log has equity {ledger_from_log.equity()} against "
        f"the unfolded {acct.equity()}")


CASES = [l1_a_fence_stops_admission_without_reaching_the_gateway,
         l2_a_fence_does_not_lower_the_requirement,
         l3_one_leg_at_a_time_is_refused_on_a_hedged_book,
         l4_the_unwind_cost_stays_inside_its_arithmetic_bound,
         l5_the_requirement_never_rises_during_the_unwind,
         l6_an_unreconciled_fence_keeps_its_capacity,
         l7_a_replayed_basket_lands_once,
         l8_the_seal_covers_every_admission_the_log_holds,
         l9_per_lease_occupancy_does_not_net_and_the_account_view_does,
         l10_settling_needs_the_barrier_and_returns_the_capacity,
         l11_a_live_liquidator_blocks_the_settlement,
         l12_a_cancel_recorded_but_not_notified_releases_the_order,
         l13_a_cancel_never_acknowledged_keeps_its_reservation,
         l14_a_crash_between_commit_and_fold_recovers_from_the_log]


def main():
    results = [c() for c in CASES]
    print(f"\n{sum(results)} of {len(results)} properties hold")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
