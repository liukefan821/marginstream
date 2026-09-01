"""What happens to the requirement when the marks move.

Every earlier round ran at fixed marks, because nothing in them needed the
market to move. The liquidation experiment does, and the first thing repricing
found was that the add-on term was being evaluated at a single point while the
scenario term was evaluated over the whole grid.

m1 reproduces the breach that follows from that, against the definition of
gross the code used before this round. m2 to m5 are the properties of the
replacement, and m4a/m4b together fix the scope of the tightness claim: it
holds in the single-factor, non-negative-loading model configured here, and
does not hold in general.
"""
import itertools
import random
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from marginstream.risk import RiskModel, Symbol
from marginstream.allocator2 import Allocator
from marginstream.gateway2 import Gateway
from marginstream.sequencer import Sequencer
from marginstream.account import Account
from marginstream.execution import execute_fill

ACC = "X"


def _report(name, ok, detail):
    print(f"[{'pass' if ok else 'FAIL'}] {name}")
    if not ok:
        print(f"        {detail}")
    return ok


def gross_at_current_mark(risk, positions):
    """Gross as it was defined before this round: at the mark in force now."""
    return sum(abs(q) * risk.symbols[n].mark for n, q in positions.items())


def _binding_short_book(gross_per_risk, kappa=1, scale=10 ** 6,
                        collateral=10 ** 6):
    """Fill the envelopes with a short position and fill every order at the
    worst price and fee the policy allows."""
    mark, band, cap = 1000, 5, 2
    syms = [Symbol("A", 0, mark, 200, 100, band, cap)]
    risk = RiskModel(syms, addon_kappa=kappa, addon_scale=scale)
    seqr = Sequencer()
    alloc = Allocator(risk, sequencer=seqr, ttl=10 ** 6, gross_per_risk=gross_per_risk)
    gw = Gateway(0, risk, sequencer=seqr)
    acct = Account(risk, collateral)
    leases, _ = alloc.issue(ACC, acct.equity(), {0: 1}, now=0)
    gw.install_lease(leases[0])
    gen = alloc.current_generation(ACC)

    n = 0
    while gw.admit(ACC, "A", -1, gen, order_id=f"b{n}")[0]:
        n += 1
    for i in range(n):
        execute_fill(seqr, gw, acct, f"f{i}", f"b{i}", ACC, "A", -1,
                     mark - band, cap)
    return risk, gw, acct, n


def _sweep_largest_feasible(risk, E0, per_lot_R, per_lot_gross, per_lot_debit):
    """The largest short position the issuance condition admits, given which
    mark gross is measured at. Returns the lot count."""
    n = 0
    while True:
        nxt = n + 1
        lhs = (2 * per_lot_R * nxt
               + risk.A_of_gross(per_lot_gross * nxt)
               + per_lot_debit * nxt)
        if lhs > E0:
            return n
        n = nxt


def m1_gross_at_a_single_mark_breaks_at_the_binding_point():
    """The case that forced the change.

    A short position's adverse scenario raises the mark. Gross rises with it,
    the add-on rises with gross, and equity falls at the same time. Measuring
    gross at the mark in force when the lease was solved reserves for an add-on
    the account will not have.

    Worked on the arithmetic rather than through the simulator, because the
    simulator no longer has the defect. One lot, short, at mark 1000: 200 of
    scenario requirement, 1000 of gross now, 1200 of gross at the widest
    adverse scenario, and 7 of execution cost. The position is filled at the
    worst price and fee the policy allows, so equity at mark m is
    `E0 + 995n - n*m - 2n`.
    """
    mark, band, cap = 1000, 5, 2
    risk = RiskModel([Symbol("A", 0, mark, 200, 100, band, cap)],
                     addon_kappa=1, addon_scale=10 ** 5)
    E0 = 10 ** 6
    reach = risk.mark_plus("A")

    stale_n = _sweep_largest_feasible(risk, E0, 200, mark, band + cap)
    fixed_n = _sweep_largest_feasible(risk, E0, 200, reach, band + cap)

    def state(n):
        equity_after = E0 + (mark - band) * n - reach * n - cap * n
        requirement_after = 200 * n + risk.A_of_gross(reach * n)
        return equity_after, requirement_after, requirement_after - equity_after

    stale_eq, stale_req, stale_breach = state(stale_n)
    fixed_eq, fixed_req, fixed_breach = state(fixed_n)

    return _report(
        "m1 gross measured at one mark understates the requirement",
        stale_breach > 0 and fixed_breach <= 0,
        f"reserving at mark {mark}: {stale_n} lots admitted, requirement "
        f"{stale_req} against equity {stale_eq} once the move happens, over by "
        f"{stale_breach}. reserving at mark {reach}: {fixed_n} lots, "
        f"requirement {fixed_req} against equity {fixed_eq}, inside by "
        f"{-fixed_breach}. the fix costs {stale_n - fixed_n} lots of capacity",
    )


def m2_mark_plus_bounds_every_mark_in_the_grid():
    """`mark_plus` has to be an upper bound on the mark at every scenario, or
    the gross figure it produces is not an envelope."""
    rng = random.Random(7)
    worst = None
    for _ in range(2000):
        sym = Symbol("A", 0, rng.randrange(100, 5000), rng.randrange(10, 300),
                     rng.randrange(1, 200))
        risk = RiskModel([sym], addon_kappa=0, addon_scale=1)
        mp = risk.mark_plus("A")
        for f in risk.grid:
            m = risk.displaced_marks(f)["A"]
            if m > mp:
                return _report("m2 mark_plus bounds every mark in the grid",
                               False, f"{sym} reaches {m} at f={f}, bound {mp}")
            slack = mp - m
            if worst is None or slack < worst:
                worst = slack
    return _report(
        "m2 mark_plus bounds every mark in the grid", True,
        f"smallest slack observed {worst}")


def m3_the_condition_holds_at_the_binding_point_after_the_move():
    """The same binding book, driven through the real components, stays inside
    equity once the market moves to the edge of the grid, in both directions.

    The requirement compared here is the one the account owes at the marks in
    force: `R(P) + A(gross(P))`. The reserve that made it fit was taken at
    `mark_plus`, which is the larger figure.
    """
    detail = []
    ok = True
    for sign in (-1, 1):
        mark, band, cap = 1000, 5, 2
        syms = [Symbol("A", 0, mark, 200, 100, band, cap)]
        risk = RiskModel(syms, addon_kappa=1, addon_scale=10 ** 5)
        seqr = Sequencer()
        alloc = Allocator(risk, sequencer=seqr, ttl=10 ** 6, gross_per_risk=6)
        gw = Gateway(0, risk, sequencer=seqr)
        acct = Account(risk, 10 ** 6)
        leases, _ = alloc.issue(ACC, acct.equity(), {0: 1}, now=0)
        gw.install_lease(leases[0])
        gen = alloc.current_generation(ACC)
        n = 0
        while gw.admit(ACC, "A", sign, gen, order_id=f"b{n}")[0]:
            n += 1
        for i in range(n):
            execute_fill(seqr, gw, acct, f"f{i}", f"b{i}", ACC, "A", sign,
                         mark + band * sign, cap)
        worst_f = max(risk.grid) if sign < 0 else min(risk.grid)
        moved = risk.displaced_marks(worst_f)
        risk.reprice(moved)
        gw.reprice()
        pos = acct.positions()
        req = risk.R(pos) + risk.A_of_gross(risk.gross(pos))
        eq = acct.equity()
        ok = ok and req <= eq
        detail.append(f"sign {sign:+d}: {n} lots, requirement {req}, "
                      f"equity {eq}, headroom {eq - req}")
    return _report(
        "m3 the binding book stays inside equity across the move",
        ok, "; ".join(detail))


def m4a_one_factor_non_negative_loadings():
    """`mark_plus` takes the worst mark per symbol independently. The tight
    figure is the worst over scenarios of the whole sum.

    In the model configured everywhere in this repository there is one factor
    and every loading is non-negative, so all symbols reach their highest mark
    at the same f and the two agree except for the rounding: `max_move` rounds
    up and the displacement rounds down, leaving at most one minor unit per
    lot. That is a property of this configuration, not of the bound. m4b is the
    other side of it.
    """
    rng = random.Random(19)
    worst_gap = 0
    for _ in range(1500):
        n = rng.randrange(1, 5)
        syms = [Symbol(f"S{i}", 0, rng.randrange(100, 3000),
                       rng.randrange(10, 200), rng.randrange(1, 180))
                for i in range(n)]
        risk = RiskModel(syms, addon_kappa=0, addon_scale=1)
        pos = {s.name: rng.randrange(-30, 31) for s in syms}
        per_symbol = risk.gross_reach(pos)
        per_scenario = max(
            sum(abs(q) * risk.displaced_marks(f)[s] for s, q in pos.items())
            for f in risk.grid)
        lots = sum(abs(q) for q in pos.values())
        gap = per_symbol - per_scenario
        if gap < 0 or gap > lots:
            return _report(
                "m4a with one factor and non-negative loadings the bound is "
                "tight to the rounding", False,
                f"{pos}: per-symbol {per_symbol}, per-scenario {per_scenario}, "
                f"gap {gap} against {lots} lots")
        worst_gap = max(worst_gap, gap)
    return _report(
        "m4a with one factor and non-negative loadings the bound is tight to "
        "the rounding", True,
        f"1500 random portfolios; largest excess over the per-scenario "
        f"maximum {worst_gap}, bound is one minor unit per lot")


def m4b_signed_loadings_break_the_tightness_claim():
    """With signed loadings the symbols no longer reach their highest mark at
    the same scenario, and the per-symbol sum can exceed every single
    scenario's gross by far more than the rounding.

    Two symbols with opposite loadings: one peaks at the top of the grid, the
    other at the bottom. `mark_plus` adds both peaks; no scenario delivers
    them together. The bound is still safe, and it is the *tightness* claim
    that does not survive, which is why m4a is scoped to the configuration it
    is measured in rather than stated as a property of `mark_plus`.
    """
    syms = [Symbol("UP", 0, 1000, 200, 100, 5, 2),
            Symbol("DOWN", 0, 1000, 200, -100, 5, 2)]
    risk = RiskModel(syms, addon_kappa=0, addon_scale=1)
    pos = {"UP": 10, "DOWN": 10}
    per_symbol = risk.gross_reach(pos)
    per_scenario = max(
        sum(abs(q) * risk.displaced_marks(f)[s] for s, q in pos.items())
        for f in risk.grid)
    lots = sum(abs(q) for q in pos.values())
    gap = per_symbol - per_scenario
    safe = per_symbol >= per_scenario
    return _report(
        "m4b signed loadings break the tightness claim but not the bound",
        safe and gap > lots,
        f"marks at each scenario "
        f"{[(f, risk.displaced_marks(f)) for f in (min(risk.grid), max(risk.grid))]}; "
        f"per-symbol bound {per_symbol}, best single scenario {per_scenario}, "
        f"excess {gap} against {lots} lots, so the one-unit-per-lot statement "
        f"does not hold here")


def m5_a_reprice_leaves_the_cached_gross_correct():
    """`gross_wf` is maintained incrementally, so a mark change leaves it
    stale until `reprice` runs. Compared against a full rebuild.
    """
    rng = random.Random(23)
    syms = [Symbol(f"S{i}", 0, 800 + 300 * i, 40 + 10 * i, 60 + 30 * i,
                   band=6, fee_per_lot=1) for i in range(3)]
    risk = RiskModel(syms, addon_kappa=1, addon_scale=10 ** 6)
    seqr = Sequencer()
    alloc = Allocator(risk, sequencer=seqr, ttl=10 ** 6, gross_per_risk=10 ** 4)
    gw = Gateway(0, risk, sequencer=seqr)
    leases, _ = alloc.issue(ACC, 10 ** 10, {0: 1}, now=0)
    gw.install_lease(leases[0])
    gen = alloc.current_generation(ACC)
    for i in range(40):
        gw.admit(ACC, rng.choice(syms).name, rng.choice([-7, -2, 2, 7]), gen,
                 order_id=f"o{i}")
    for oid, (sym, rem) in list(gw.live_orders(ACC).items())[:15]:
        execute_fill(seqr, gw, None, f"f{oid}", oid, ACC, sym, rem,
                     risk.symbols[sym].mark, 0)

    before = gw.used_gross(ACC)
    risk.reprice({s.name: s.mark + 137 for s in risk.symbols.values()})
    stale = gw.used_gross(ACC)
    gw.reprice()
    after = gw.used_gross(ACC)
    rebuilt = gw._gross_wf_fullscan(gw._st(ACC))
    return _report(
        "m5 reprice brings the cached gross back onto the marks",
        after == rebuilt and stale == before and after != before,
        f"before {before}, stale after the move {stale}, after reprice "
        f"{after}, full rebuild {rebuilt}")


CASES = [m1_gross_at_a_single_mark_breaks_at_the_binding_point,
         m2_mark_plus_bounds_every_mark_in_the_grid,
         m3_the_condition_holds_at_the_binding_point_after_the_move,
         m4a_one_factor_non_negative_loadings,
         m4b_signed_loadings_break_the_tightness_claim,
         m5_a_reprice_leaves_the_cached_gross_correct]


def main():
    results = [c() for c in CASES]
    print(f"\n{sum(results)} of {len(results)} properties hold")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
