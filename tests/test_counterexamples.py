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
from marginstream.allocator import Allocator
from marginstream.shard import Shard

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

    before = {"A": 10, "B": -10}
    after = {"A": 10, "B": 10}
    charged = risk.marginal_R({"B": -10}, "B", 20)
    global_delta = risk.R(after) - risk.R(before)

    return _report(
        "c1 charge bounds the global requirement",
        charged >= global_delta,
        f"charged {charged}, global requirement rose by {global_delta}",
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

    first = risk.marginal_R({}, "A", 100)
    second = max(0, risk.marginal_R({"A": 100}, "B", -100))
    pos = {"A": 100, "B": -100}

    gross_after_first = risk.gross({"A": 100})
    gross_after_second = risk.gross(pos)
    addon_after_second = risk.A(pos)

    return _report(
        "c2 a zero charge does not raise the add-on",
        not (second == 0 and gross_after_second > gross_after_first
             and addon_after_second > risk.A({"A": 100})),
        f"charges {first} then {second}; gross {gross_after_first} -> "
        f"{gross_after_second}; add-on -> {addon_after_second}",
    )


def c3_addon_superadditive_under_rounding():
    """The add-on term must stay super-additive after integer rounding.

    Ceiling rounding at small arguments produces A(2) < A(1) + A(1).
    """
    risk = RiskModel([Symbol("A", 0, 1, 1, 100)], addon_kappa=1,
                     addon_scale=1_000_000)
    a1 = risk.A_of_gross(1)
    a2 = risk.A_of_gross(2)
    return _report(
        "c3 add-on stays super-additive under rounding",
        a2 >= 2 * a1,
        f"A(1)={a1}, A(1)+A(1)={2 * a1}, A(2)={a2}",
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
    alloc = Allocator(risk)
    shard = Shard(0, risk, fencing=True)

    leases, _ = alloc.issue(ACC, {}, 100_000, {0: 1})
    shard.install_lease(leases[0])
    g1 = alloc.current_generation(ACC)
    alloc.bump_generation(ACC)

    ok, _cost, reason = shard.admit(ACC, "A", 1, g1)
    return _report(
        "c4 a generation bump revokes an undelivered lease",
        not ok,
        f"admitted with result={ok} reason={reason} while the allocator was at "
        f"generation {alloc.current_generation(ACC)}",
    )


def c5_schedule_covers_already_spent():
    """Capacity spent at one market state must remain within what a later,
    more adverse state permits, or the design must state that it does not.

    This is a property of the mechanism rather than a code defect: a schedule
    stops further admission and cannot reduce a position already opened.
    """
    syms = [Symbol("A", 0, 1000, 100, 100)]
    risk = RiskModel(syms, addon_kappa=0, addon_scale=1)
    spent_at_state_0 = 100
    allowed_at_state_1 = 49
    return _report(
        "c5 what was spent fits the later state",
        spent_at_state_0 <= allowed_at_state_1,
        f"spent {spent_at_state_0} under the state-0 allowance; the state-1 "
        f"allowance is {allowed_at_state_1}",
    )


CASES = [
    c1_charge_bounds_global_requirement,
    c2_charge_bounds_gross_notional,
    c3_addon_superadditive_under_rounding,
    c4_generation_bump_revokes,
    c5_schedule_covers_already_spent,
]


def main():
    results = [c() for c in CASES]
    passed = sum(results)
    print(f"\n{passed} of {len(results)} properties hold")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
