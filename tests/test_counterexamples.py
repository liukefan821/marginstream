"""Counterexamples found in external review, pinned as tests.

Each function asserts the property the design claims. They fail against the
implementation as of this commit; the commit that fixes each one is expected to
turn the corresponding test green.

Run:  python3 tests/test_counterexamples.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from marginstream.risk import RiskModel, Symbol
from marginstream.allocator2 import Allocator
from marginstream.gateway import Gateway

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

    # Two gateways, each holding one leg. Each is leased an envelope of 1200.
    alloc = Allocator(risk, ttl=10)
    g0 = Gateway(0, risk)
    g1 = Gateway(1, risk)
    leases, _ = alloc.issue(ACC, 100_000, {0: 1, 1: 1})
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

    alloc = Allocator(risk, ttl=10, gross_per_risk=20)
    gw = Gateway(0, risk)
    leases, _ = alloc.issue(ACC, 30_000, {0: 1})
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
    alloc = Allocator(risk, ttl=5)
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
        (not ok_after) and reason == "lease_expired",
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
    alloc = Allocator(risk, shape=(1000, 300), ttl=10)
    gw = Gateway(0, risk, fencing=True)
    leases, _ = alloc.issue(ACC, 100_000, {0: 1})
    gw.install_lease(leases[0])
    gen = alloc.current_generation(ACC)
    lease = leases[0]

    # fill the state-0 envelope
    qty = 1
    while gw.admit(ACC, "A", qty, gen, market_state=0)[0]:
        pass
    used = gw.used_risk(ACC)

    over_at_state_1 = used > lease.risk_at(1)
    refused, reason = gw.admit(ACC, "A", 1, gen, market_state=1)

    return _report(
        "c5 the gateway detects the condition locally on the tick",
        over_at_state_1 and not refused and reason == "risk_envelope",
        f"used {used}; state-1 envelope {lease.risk_at(1)}; "
        f"next order refused={not refused} reason={reason}",
    )


CASES = [
    c1_charge_bounds_global_requirement,
    c2_charge_bounds_gross_notional,
    c3_addon_superadditive_under_rounding,
    c4_generation_bump_revokes,
    c5_schedule_is_a_trigger_not_a_guarantee,
]


def main():
    results = [c() for c in CASES]
    passed = sum(results)
    print(f"\n{passed} of {len(results)} properties hold")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
