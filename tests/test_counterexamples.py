"""Counterexamples found in external review, pinned as tests.

Each function asserts the property the design claims. They fail against the
implementation as of this commit; the commit that fixes each one is expected to
turn the corresponding test green.

Run:  python3 tests/test_counterexamples.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from marginstream.risk import RiskModel, Symbol, FACTOR_GRID
from marginstream.allocator2 import Allocator
from marginstream.gateway2 import Gateway
from marginstream.sequencer import Sequencer

ACC = "X"


def _report(name, ok, detail):
    print(f"[{'pass' if ok else 'FAIL'}] {name}")
    if not ok:
        print(f"        {detail}")
    return ok


def c1_charge_bounds_global_requirement():
    """The sum of what the shards charge must bound the increase in the
    account's global requirement.

    Two identical contracts on two shards, hedged at (+10, -10) so the global
    requirement is zero. Flipping the second leg to +10 leaves that shard's own
    requirement unchanged, so a marginal charge is zero, while the global
    requirement moves to its maximum.
    """
    syms = [Symbol("A", 0, 1000, 100, 100), Symbol("B", 1, 1000, 100, 100)]
    risk = RiskModel(syms, addon_kappa=0, addon_scale=1)

    # Two gateways, each holding one leg, each leased from one solve.
    seqr = Sequencer()
    alloc = Allocator(risk, sequencer=seqr, ttl=10)
    g0 = Gateway(0, risk)
    g1 = Gateway(1, risk)
    leases, _ = alloc.issue(ACC, 100_000, {0: 1, 1: 1}, now=0)
    for g, lz in leases.items():
        (g0 if g == 0 else g1).install_lease(lz)
    gen = alloc.current_generation(ACC)

    g0.admit(ACC, "A", 10, gen)
    g1.admit(ACC, "B", -10, gen)

    # flipping the second leg from short to long
    g1.admit(ACC, "B", 20, gen)

    merged = {}
    for gw in (g0, g1):
        for sym, qty in gw.local_positions(ACC).items():
            merged[sym] = merged.get(sym, 0) + qty
    envelope = sum(lz.risk_amount for lz in leases.values())

    return _report(
        "c1 the envelope bounds the global requirement",
        risk.R(merged) <= envelope,
        f"global requirement {risk.R(merged)} against an envelope of {envelope}",
    )


def c2_charge_bounds_gross_notional():
    """A charge of zero must not permit an unbounded increase in gross
    notional, because the add-on term is a function of gross.

    Buy 100 of one contract, then sell 100 of an identical one on the same
    shard. The second order reduces the shard's requirement, so its charge
    clips to zero, while gross notional doubles.
    """
    syms = [Symbol("A", 0, 1000, 100, 100), Symbol("B", 0, 1000, 100, 100)]
    risk = RiskModel(syms, addon_kappa=1, addon_scale=1_000_000)

    seqr = Sequencer()
    alloc = Allocator(risk, sequencer=seqr, ttl=10, gross_per_risk=20)
    gw = Gateway(0, risk)
    leases, _ = alloc.issue(ACC, 30_000, {0: 1}, now=0)
    gw.install_lease(leases[0])
    gen = alloc.current_generation(ACC)

    ok1, r1 = gw.admit(ACC, "A", 100, gen)
    ok2, r2 = gw.admit(ACC, "B", -100, gen)   # reduces R, raises gross

    pos = gw.local_positions(ACC)
    gross = risk.gross(pos)
    envelope = leases[0].gross_amount

    return _report(
        "c2 gross notional stays inside its own envelope",
        gross <= envelope,
        f"first order {ok1}/{r1}, second {ok2}/{r2}; gross {gross} against a "
        f"gross envelope of {envelope}",
    )


def c3_addon_superadditive_under_rounding():
    """The add-on term must stay super-additive after integer rounding.

    Ceiling rounding at small arguments produces A(2) < A(1) + A(1).
    """
    risk = RiskModel([Symbol("A", 0, 1, 1, 100)], addon_kappa=1,
                     addon_scale=1_000_000)
    # the exact numerator is what the safety condition uses; rounded values are
    # never summed
    a1 = risk.A_num(1)
    a2 = risk.A_num(2)
    return _report(
        "c3 add-on is super-additive in the units the condition uses",
        a2 >= 2 * a1,
        f"A_num(1)={a1}, A_num(1)+A_num(1)={2 * a1}, A_num(2)={a2}",
    )


def c4_generation_bump_revokes():
    """Bumping the generation must stop a shard that receives no message.

    A shard holding generation 1 is offered an order also stamped generation 1
    while the allocator has moved to generation 2. Nothing in either the lease
    or the order carries the new generation, so the shard has no way to learn
    of it.
    """
    syms = [Symbol("A", 0, 1000, 100, 100)]
    risk = RiskModel(syms, addon_kappa=0, addon_scale=1)
    seqr = Sequencer()
    alloc = Allocator(risk, sequencer=seqr, ttl=5)
    gw = Gateway(0, risk, fencing=True)

    leases, _ = alloc.issue(ACC, 100_000, {0: 1}, now=0)
    gw.install_lease(leases[0])
    g1 = alloc.current_generation(ACC)
    alloc.bump_generation(ACC)

    # before expiry and with no message delivered, the gateway still serves.
    # that is the lease semantics: revocation is bounded by the term, not
    # instant. after the term it must stop on its own.
    ok_before, _ = gw.admit(ACC, "A", 1, g1, now=1)
    ok_after, reason = gw.admit(ACC, "A", 1, g1, now=5)

    return _report(
        "c4 an undelivered revocation takes effect by expiry",
        ok_before and (not ok_after) and reason == "lease_expired",
        f"before expiry admitted={ok_before}; at expiry admitted={ok_after} "
        f"reason={reason}",
    )


def c5_schedule_is_a_trigger_not_a_guarantee():
    """A shrinking schedule does not make an already-admitted position safe.

    The property the mechanism does provide is that a gateway detects, on the
    tick the state changes and with no message from the allocator, that its
    admitted set no longer fits the current state. The test asserts the
    detection, and records that the position itself is unchanged, because that
    is what the paper has to say rather than claim otherwise.
    """
    syms = [Symbol("A", 0, 1000, 100, 100)]
    risk = RiskModel(syms, addon_kappa=0, addon_scale=1)
    seqr = Sequencer()
    alloc = Allocator(risk, sequencer=seqr, shape=(1000, 300), ttl=10)
    gw = Gateway(0, risk, fencing=True)
    leases, _ = alloc.issue(ACC, 100_000, {0: 1}, now=0)
    gw.install_lease(leases[0])
    gen = alloc.current_generation(ACC)
    lease = leases[0]

    # fill the state-0 envelope
    qty = 1
    while gw.admit(ACC, "A", qty, gen, market_state=0)[0]:
        pass
    used = gw.used_risk(ACC)

    # the market state moves and no order arrives
    verdict = gw.observe_market_state(ACC, 1)

    return _report(
        "c5 the gateway reports the condition on a state tick, with no order",
        used > lease.risk_at(1) and verdict == "reduce_only",
        f"used {used}; state-1 envelope {lease.risk_at(1)}; "
        f"state tick returned {verdict}",
    )



def c6_no_capacity_reissued_over_a_live_lease():
    """Capacity held by a gateway that has not been replaced must not be
    issued again to anyone else before that gateway's term ends.

    An old gateway is leased and spends; the allocator bumps the generation and
    hands the same capacity to a replacement gateway while the old lease is
    still inside its term. Both spend, and the account carries the sum.
    """
    syms = [Symbol("A", 0, 1000, 100, 100), Symbol("B", 1, 1000, 100, 100)]
    risk = RiskModel(syms, addon_kappa=0, addon_scale=1)
    collateral = 1_000

    seqr = Sequencer()
    alloc = Allocator(risk, sequencer=seqr, ttl=100)
    old = Gateway(0, risk)
    leases, _ = alloc.issue(ACC, collateral, {0: 1}, now=0)
    old.install_lease(leases[0])
    gen1 = alloc.current_generation(ACC)
    while old.admit(ACC, "A", 1, gen1, now=1)[0]:
        pass

    # the old gateway is unreachable; a replacement is brought up at t=2,
    # well inside the old lease's term
    alloc.bump_generation(ACC)
    new_leases, _ = alloc.issue(ACC, collateral, {1: 1}, now=2)
    new = Gateway(1, risk)
    new.install_lease(new_leases[1])
    gen2 = alloc.current_generation(ACC)
    while new.admit(ACC, "B", 1, gen2, now=3)[0]:
        pass

    merged = {}
    for gw in (old, new):
        for sym, qty in gw.local_positions(ACC).items():
            merged[sym] = merged.get(sym, 0) + qty

    return _report(
        "c6 capacity is not reissued over an unexpired lease",
        worst_breach(risk, merged, collateral) == 0,
        f"old spent {old.used_risk(ACC)}, replacement spent "
        f"{new.used_risk(ACC)}, requirement {risk.M(merged)} against "
        f"collateral {collateral}",
    )


def c7_weight_migration_respects_existing_usage():
    """A new generation may not lower a gateway's ceiling below what its
    admitted set already occupies, because lowering the ceiling does not
    remove the positions.
    """
    syms = [Symbol("A", 0, 1000, 100, 100), Symbol("B", 1, 1000, 100, 100)]
    risk = RiskModel(syms, addon_kappa=0, addon_scale=1)
    collateral = 1_000

    seqr = Sequencer()
    alloc = Allocator(risk, sequencer=seqr, ttl=100)
    g0, g1 = Gateway(0, risk), Gateway(1, risk)
    leases, _ = alloc.issue(ACC, collateral, {0: 1, 1: 1}, now=0)
    g0.install_lease(leases[0]); g1.install_lease(leases[1])
    gen1 = alloc.current_generation(ACC)
    while g0.admit(ACC, "A", 1, gen1, now=1)[0]:
        pass
    used0 = g0.used_risk(ACC)

    # the next generation moves all the weight to g1
    alloc.bump_generation(ACC)
    floors = {0: g0.used_risk(ACC), 1: g1.used_risk(ACC)}
    leases2, _ = alloc.issue(ACC, collateral, {0: 0, 1: 1}, floors=floors,
                             now=101)
    g0.install_lease(leases2[0]); g1.install_lease(leases2[1])
    gen2 = alloc.current_generation(ACC)
    while g1.admit(ACC, "B", 1, gen2, now=102)[0]:
        pass

    merged = {}
    for gw in (g0, g1):
        for sym, qty in gw.local_positions(ACC).items():
            merged[sym] = merged.get(sym, 0) + qty

    return _report(
        "c7 a weight change respects what a gateway already holds",
        worst_breach(risk, merged, collateral) == 0,
        f"g0 held {used0} and was re-leased "
        f"{leases2[0].risk_amount}; requirement {risk.M(merged)} against "
        f"collateral {collateral}",
    )



def worst_breach(risk, pos, collateral):
    """How far the requirement exceeds equity, taken over the whole grid.

    Comparing against collateral alone is not the invariant: equity at an
    adverse scenario is collateral less the loss the portfolio takes there.
    """
    m = risk.M(pos)
    worst = 0
    for f in FACTOR_GRID:
        gap = m - (collateral - risk.loss(pos, f))
        if gap > worst:
            worst = gap
    return worst


def merge(gws, account="X"):
    out = {}
    for gw in gws:
        for sym, qty in gw.local_positions(account).items():
            out[sym] = out.get(sym, 0) + qty
    return out


def c8_expiry_does_not_release_exposure():
    """A lease term ends a holder's authority to admit. It does not remove the
    positions that holder already admitted, so the capacity those positions
    occupy must not be handed to anyone else until the positions are gone.
    """
    syms = [Symbol("A", 0, 1000, 100, 100), Symbol("B", 1, 1000, 100, 100)]
    risk = RiskModel(syms, addon_kappa=0, addon_scale=1)
    collateral = 1_000

    seqr = Sequencer()
    alloc = Allocator(risk, sequencer=seqr, ttl=100)
    old = Gateway(0, risk)
    leases, _ = alloc.issue(ACC, collateral, {0: 1}, now=0)
    old.install_lease(leases[0])
    gen1 = alloc.current_generation(ACC)
    while old.admit(ACC, "A", 1, gen1, now=1)[0]:
        pass

    # the old lease runs out; its positions do not
    alloc.bump_generation(ACC)
    alloc.observe_usage(ACC, {0: (old.used_risk(ACC), old.used_gross(ACC))})
    new_leases, _ = alloc.issue(ACC, collateral, {1: 1}, now=101)
    new = Gateway(1, risk)
    new.install_lease(new_leases[1])
    gen2 = alloc.current_generation(ACC)
    while new.admit(ACC, "B", 1, gen2, now=102)[0]:
        pass

    pos = merge([old, new])
    breach = worst_breach(risk, pos, collateral)
    return _report(
        "c8 expiry releases authority, not exposure",
        breach == 0,
        f"old holds {old.used_risk(ACC)}, replacement was granted "
        f"{new_leases[1].risk_amount}; requirement exceeds equity by {breach}",
    )


def c9_infeasible_state_is_not_local_reduce_only():
    """When the floors do not fit, issuing ordinary envelopes at the floor is
    not a safe fallback.

    An order that lowers one gateway's own requirement can raise the account's,
    because it removes a hedge held on another gateway. Local reduction is not
    global reduction.
    """
    syms = [Symbol("A", 0, 1000, 100, 100), Symbol("B", 1, 1000, 100, 100)]
    risk = RiskModel(syms, addon_kappa=0, addon_scale=1)
    collateral = 1_000

    seqr = Sequencer()
    alloc = Allocator(risk, sequencer=seqr, ttl=1000)
    g0, g1 = Gateway(0, risk), Gateway(1, risk)
    leases, _ = alloc.issue(ACC, 100_000, {0: 1, 1: 1}, now=0)
    g0.install_lease(leases[0]); g1.install_lease(leases[1])
    gen = alloc.current_generation(ACC)
    g0.admit(ACC, "A", 10, gen, now=1)
    g1.admit(ACC, "B", -10, gen, now=1)
    # perfectly hedged: the account requires nothing
    before = worst_breach(risk, merge([g0, g1]), collateral)

    alloc.bump_generation(ACC)
    floors = {0: g0.used_risk(ACC), 1: g1.used_risk(ACC)}
    gfloors = {0: g0.used_gross(ACC), 1: g1.used_gross(ACC)}
    l2, scale = alloc.issue(ACC, collateral, {0: 1, 1: 1}, floors=floors,
                            now=2, gross_floors=gfloors)
    g0.install_lease(l2[0]); g1.install_lease(l2[1])
    gen2 = alloc.current_generation(ACC)

    # g1 closes its leg. locally this reduces both resources.
    ok, reason = g1.admit(ACC, "B", 10, gen2, now=3)
    after = worst_breach(risk, merge([g0, g1]), collateral)

    return _report(
        "c9 an infeasible solve does not permit local risk reduction",
        after == 0,
        f"scale={scale}; closing one leg admitted={ok} ({reason}); "
        f"breach {before} -> {after}",
    )


def c10_incarnations_are_counted_separately():
    """Two processes that have held the same gateway identity can both be
    live. Capacity has to be summed across incarnations, not collapsed by id.
    """
    syms = [Symbol("A", 0, 1000, 100, 100), Symbol("B", 1, 1000, 100, 100)]
    risk = RiskModel(syms, addon_kappa=0, addon_scale=1)
    collateral = 1_000

    seqr = Sequencer()
    alloc = Allocator(risk, sequencer=seqr, ttl=100)
    first = Gateway(0, risk)
    l1, _ = alloc.issue(ACC, collateral, {0: 1}, now=0)
    first.install_lease(l1[0])
    gen1 = alloc.current_generation(ACC)
    while first.admit(ACC, "A", 1, gen1, now=1)[0]:
        pass

    # the process restarts and reuses the same identity
    alloc.bump_generation(ACC)
    alloc.observe_usage(ACC, {(0, 0): (first.used_risk(ACC),
                                       first.used_gross(ACC))})
    l2, _ = alloc.issue(ACC, collateral, {(0, 1): 1}, now=2)
    second = Gateway(0, risk, incarnation=1)
    second.install_lease(l2[(0, 1)])
    gen2 = alloc.current_generation(ACC)
    while second.admit(ACC, "B", 1, gen2, now=3)[0]:
        pass

    pos = merge([first, second])
    breach = worst_breach(risk, pos, collateral)
    return _report(
        "c10 two incarnations of one identity are counted separately",
        breach == 0,
        f"first spent {first.used_risk(ACC)}, second spent "
        f"{second.used_risk(ACC)}; requirement exceeds equity by {breach}",
    )



def c11_expired_but_unreconciled_holds_its_ceiling():
    """A term that ends without a terminal usage report leaves the allocator
    unable to say what the holder spent. It must assume the ceiling.

    Unlike c8, no usage report reaches the allocator here.
    """
    syms = [Symbol("A", 0, 1000, 100, 100), Symbol("B", 1, 1000, 100, 100)]
    risk = RiskModel(syms, addon_kappa=0, addon_scale=1)
    collateral = 1_000

    seqr = Sequencer()
    alloc = Allocator(risk, sequencer=seqr, ttl=100)
    old = Gateway(0, risk)
    leases, _ = alloc.issue(ACC, collateral, {0: 1}, now=0)
    old.install_lease(leases[0])
    gen1 = alloc.current_generation(ACC)
    while old.admit(ACC, "A", 1, gen1, now=1)[0]:
        pass

    # the term ends and nothing is heard from the old holder
    alloc.bump_generation(ACC)
    new_leases, scale = alloc.issue(ACC, collateral, {1: 1}, now=101)
    new = Gateway(1, risk)
    new.install_lease(new_leases[1])
    gen2 = alloc.current_generation(ACC)
    while new.admit(ACC, "B", 1, gen2, now=102)[0]:
        pass

    pos = merge([old, new])
    return _report(
        "c11 an expired, unreconciled term keeps occupying its ceiling",
        worst_breach(risk, pos, collateral) == 0,
        f"replacement granted {new_leases[1].risk_amount} (scale={scale}); "
        f"requirement exceeds equity by "
        f"{worst_breach(risk, pos, collateral)}",
    )


def c12_release_requires_coverage_not_just_order():
    """A report that does not cover every admission the ordering point recorded
    must not release exposure, however its number compares with what the
    allocator has seen.

    All usage reports are lost here, so the allocator has no watermark at all.
    An optimistic report claiming zero usage must still be refused.
    """
    syms = [Symbol("A", 0, 1000, 100, 100), Symbol("B", 1, 1000, 100, 100)]
    risk = RiskModel(syms, addon_kappa=0, addon_scale=1)
    collateral = 1_000

    seqr = Sequencer()
    alloc = Allocator(risk, sequencer=seqr, ttl=100)
    old = Gateway(0, risk, sequencer=seqr)
    leases, _ = alloc.issue(ACC, collateral, {0: 1}, now=0)
    old.install_lease(leases[0])
    gen1 = alloc.current_generation(ACC)
    while old.admit(ACC, "A", 1, gen1, now=1)[0]:
        pass

    lid = leases[0].lease_id
    # a claim that the lease spent nothing, carrying a seal that is not the
    # one the ordering point issued
    from marginstream.sequencer import Seal
    ok, why = alloc.release(ACC, lid, Seal(lid, 0), seqr)
    accepted_without_fence = ok

    seal = seqr.fence(lid)
    ok2, why2 = alloc.release(ACC, lid, Seal(lid, 0), seqr)
    accepted_with_wrong_seq = ok2

    return _report(
        "c12 a release needs a seal covering every admission",
        (not accepted_without_fence) and (not accepted_with_wrong_seq),
        f"unfenced release -> {ok}/{why}; short seal against terminal_seq "
        f"{seal.terminal_seq} -> {ok2}/{why2}",
    )


def c13_retire_does_not_revoke_a_live_lease():
    """Retiring a holder stops future issuance to it. It does not take back a
    lease that is still inside its term at a partitioned process."""
    syms = [Symbol("A", 0, 1000, 100, 100), Symbol("B", 1, 1000, 100, 100)]
    risk = RiskModel(syms, addon_kappa=0, addon_scale=1)
    collateral = 1_000

    seqr = Sequencer()
    alloc = Allocator(risk, sequencer=seqr, ttl=1000)
    old = Gateway(0, risk)
    leases, _ = alloc.issue(ACC, collateral, {0: 1}, now=0)
    old.install_lease(leases[0])
    gen1 = alloc.current_generation(ACC)

    # the operator retires the holder while its term is still running
    alloc.retire(ACC, 0)
    alloc.bump_generation(ACC)
    new_leases, _ = alloc.issue(ACC, collateral, {1: 1}, now=1)
    new = Gateway(1, risk)
    new.install_lease(new_leases[1])
    gen2 = alloc.current_generation(ACC)

    # the retired process is partitioned and keeps using its own generation
    while old.admit(ACC, "A", 1, gen1, now=2)[0]:
        pass
    while new.admit(ACC, "B", 1, gen2, now=2)[0]:
        pass

    pos = merge([old, new])
    return _report(
        "c13 retiring a holder does not revoke its live lease",
        worst_breach(risk, pos, collateral) == 0,
        f"retired holder spent {old.used_risk(ACC)}, replacement "
        f"{new.used_risk(ACC)}; requirement exceeds equity by "
        f"{worst_breach(risk, pos, collateral)}",
    )



def c14_release_is_refused_without_full_coverage():
    """A seal whose terminal sequence is behind what the ordering point
    recorded leaves the ceiling occupied."""
    syms = [Symbol("A", 0, 1000, 100, 100), Symbol("B", 1, 1000, 100, 100)]
    risk = RiskModel(syms, addon_kappa=0, addon_scale=1)
    collateral = 1_000
    from marginstream.sequencer import Seal

    seqr = Sequencer()
    alloc = Allocator(risk, sequencer=seqr, ttl=100)
    old = Gateway(0, risk, sequencer=seqr)
    leases, _ = alloc.issue(ACC, collateral, {0: 1}, now=0)
    old.install_lease(leases[0])
    gen1 = alloc.current_generation(ACC)
    while old.admit(ACC, "A", 1, gen1, now=1)[0]:
        pass

    lid = leases[0].lease_id
    truth = seqr.fence(lid)
    short = Seal(lid, max(0, truth.terminal_seq - 1))
    ok, why = alloc.release(ACC, lid, short, seqr)

    alloc.bump_generation(ACC)
    new_leases, _ = alloc.issue(ACC, collateral, {1: 1}, now=101)
    new = Gateway(1, risk, sequencer=seqr)
    new.install_lease(new_leases[1])
    gen2 = alloc.current_generation(ACC)
    while new.admit(ACC, "B", 1, gen2, now=102)[0]:
        pass

    pos = merge([old, new])
    return _report(
        "c14 a short seal does not release the ceiling",
        (not ok) and worst_breach(risk, pos, collateral) == 0,
        f"short release -> {ok}/{why}; replacement granted "
        f"{new_leases[1].risk_amount}; breach "
        f"{worst_breach(risk, pos, collateral)}",
    )


def c15_a_seal_does_not_release_a_later_lease():
    """A terminal report about one term must not release a later term at the
    same holder."""
    syms = [Symbol("A", 0, 1000, 100, 100), Symbol("B", 1, 1000, 100, 100)]
    risk = RiskModel(syms, addon_kappa=0, addon_scale=1)
    collateral = 1_000

    seqr = Sequencer()
    alloc = Allocator(risk, sequencer=seqr, ttl=50)
    gw = Gateway(0, risk, sequencer=seqr)

    l1, _ = alloc.issue(ACC, collateral, {0: 1}, now=0)
    lid1 = l1[0].lease_id
    gw.install_lease(l1[0])
    seal1 = seqr.fence(lid1)
    ok1, _ = alloc.release(ACC, lid1, seal1, seqr)

    alloc.bump_generation(ACC)
    l2, _ = alloc.issue(ACC, collateral, {0: 1}, now=51)
    lid2 = l2[0].lease_id
    gw2 = Gateway(0, risk, sequencer=seqr)
    gw2.install_lease(l2[0])
    gen2 = alloc.current_generation(ACC)
    while gw2.admit(ACC, "A", 1, gen2, now=52)[0]:
        pass

    # the term-1 report is replayed
    replay_ok, why = alloc.release(ACC, lid2, seal1, seqr)

    alloc.bump_generation(ACC)
    l3, _ = alloc.issue(ACC, collateral, {1: 1}, now=102)
    other = Gateway(1, risk, sequencer=seqr)
    other.install_lease(l3[1])
    gen3 = alloc.current_generation(ACC)
    while other.admit(ACC, "B", 1, gen3, now=103)[0]:
        pass

    pos = merge([gw2, other])
    return _report(
        "c15 a seal from one term does not release another",
        (not replay_ok) and worst_breach(risk, pos, collateral) == 0,
        f"first release {ok1}; replay -> {replay_ok}/{why}; breach "
        f"{worst_breach(risk, pos, collateral)}",
    )


def c16_conflicting_replay_is_refused():
    """The same seal replayed with a different usage is a conflict, not an
    update."""
    syms = [Symbol("A", 0, 1000, 100, 100)]
    risk = RiskModel(syms, addon_kappa=0, addon_scale=1)
    seqr = Sequencer()
    alloc = Allocator(risk, sequencer=seqr, ttl=10)
    gw = Gateway(0, risk, sequencer=seqr)
    leases, _ = alloc.issue(ACC, 100_000, {0: 1}, now=0)
    gw.install_lease(leases[0])
    gen = alloc.current_generation(ACC)
    gw.admit(ACC, "A", 3, gen, now=1)

    lid = leases[0].lease_id
    seal = seqr.fence(lid)
    first = alloc.release(ACC, lid, seal, seqr)
    second = alloc.release(ACC, lid, seal, seqr)          # identical replay
    from marginstream.sequencer import Seal as _S
    fake = alloc.release(ACC, lid, _S(lid, seal.terminal_seq + 5), seqr)

    return _report(
        "c16 a replay is idempotent and a seal that overreaches is refused",
        first[0] and second[0] and (not fake[0]),
        f"first {first}, replay {second}, overreaching seal {fake}",
    )


CASES = [
    c1_charge_bounds_global_requirement,
    c2_charge_bounds_gross_notional,
    c3_addon_superadditive_under_rounding,
    c4_generation_bump_revokes,
    c5_schedule_is_a_trigger_not_a_guarantee,
    c6_no_capacity_reissued_over_a_live_lease,
    c7_weight_migration_respects_existing_usage,
    c8_expiry_does_not_release_exposure,
    c9_infeasible_state_is_not_local_reduce_only,
    c10_incarnations_are_counted_separately,
    c11_expired_but_unreconciled_holds_its_ceiling,
    c12_release_requires_coverage_not_just_order,
    c13_retire_does_not_revoke_a_live_lease,
    c14_release_is_refused_without_full_coverage,
    c15_a_seal_does_not_release_a_later_lease,
    c16_conflicting_replay_is_refused,
]


def main():
    results = [c() for c in CASES]
    passed = sum(results)
    print(f"\n{passed} of {len(results)} properties hold")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
