"""Account ledger: directed cases and a randomised identity check.

The identity the reporting ledger has to satisfy is

    realised + unrealised(marks) == cash + qty * mark

per symbol and in total. Everything here is integer; there is no division in
the safety path, so there is no rounding direction to depend on.
"""
import random
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from marginstream.risk import RiskModel, Symbol
from marginstream.account import Account


def _report(name, ok, detail):
    print(f"[{'pass' if ok else 'FAIL'}] {name}")
    if not ok:
        print(f"        {detail}")
    return ok


def model(mark_a=1000, mark_b=500):
    syms = [Symbol("A", 0, mark_a, 100, 100), Symbol("B", 0, mark_b, 80, 120)]
    return RiskModel(syms, addon_kappa=1, addon_scale=10 ** 6)


def a1_opening_realises_nothing():
    a = Account(model(), 100_000)
    a.apply_fill(("f", 1), "A", 10, 900)
    return _report("a1 opening a position realises nothing",
                   a.realised_pnl() == 0 and a.unrealised_pnl() == 10 * 100,
                   f"realised {a.realised_pnl()}, unrealised {a.unrealised_pnl()}")


def a2_adding_to_a_position():
    a = Account(model(), 100_000)
    a.apply_fill(("f", 1), "A", 10, 900)
    a.apply_fill(("f", 2), "A", 5, 950)
    return _report("a2 adding on the same side realises nothing",
                   a.realised_pnl() == 0
                   and a.unrealised_pnl() == 10 * 100 + 5 * 50,
                   f"realised {a.realised_pnl()}, unrealised {a.unrealised_pnl()}")


def a3_partial_close_in_profit():
    a = Account(model(), 100_000)
    a.apply_fill(("f", 1), "A", 10, 900)
    a.apply_fill(("f", 2), "A", -4, 1_050)
    return _report("a3 a profitable partial close",
                   a.realised_pnl() == 4 * 150
                   and a.unrealised_pnl() == 6 * 100,
                   f"realised {a.realised_pnl()}, unrealised {a.unrealised_pnl()}")


def a4_partial_close_at_a_loss():
    a = Account(model(), 100_000)
    a.apply_fill(("f", 1), "A", 10, 1_100)
    a.apply_fill(("f", 2), "A", -4, 1_050)
    return _report("a4 a losing partial close",
                   a.realised_pnl() == -4 * 50,
                   f"realised {a.realised_pnl()}")


def a5_full_close():
    a = Account(model(), 100_000)
    a.apply_fill(("f", 1), "A", 10, 900)
    a.apply_fill(("f", 2), "A", -10, 1_000)
    return _report("a5 a full close leaves nothing unrealised",
                   a.realised_pnl() == 10 * 100 and a.unrealised_pnl() == 0
                   and a.positions() == {},
                   f"realised {a.realised_pnl()}, unrealised "
                   f"{a.unrealised_pnl()}, positions {a.positions()}")


def a6_long_through_zero_to_short():
    a = Account(model(), 100_000)
    a.apply_fill(("f", 1), "A", 10, 900)
    a.apply_fill(("f", 2), "A", -25, 1_000)     # closes 10, opens 15 short
    return _report("a6 one fill crosses from long to short",
                   a.realised_pnl() == 10 * 100
                   and a.positions() == {"A": -15}
                   and a.unrealised_pnl() == 15 * (1_000 - 1_000),
                   f"realised {a.realised_pnl()}, positions {a.positions()}, "
                   f"unrealised {a.unrealised_pnl()}")


def a7_short_through_zero_to_long():
    a = Account(model(), 100_000)
    a.apply_fill(("f", 1), "A", -10, 1_100)
    a.apply_fill(("f", 2), "A", 25, 1_000)      # closes 10 short, opens 15 long
    return _report("a7 one fill crosses from short to long",
                   a.realised_pnl() == 10 * 100
                   and a.positions() == {"A": 15},
                   f"realised {a.realised_pnl()}, positions {a.positions()}")


def a8_two_contracts_offset():
    a = Account(model(), 100_000)
    a.apply_fill(("f", 1), "A", 10, 900)        # marked 1000, +1000
    a.apply_fill(("f", 2), "B", 10, 560)        # marked 500,  -600
    return _report("a8 one contract's gain offsets another's loss",
                   a.total_pnl() == 10 * 100 - 10 * 60,
                   f"total {a.total_pnl()}")


def a9_fees_are_counted_once():
    a = Account(model(), 100_000)
    a.apply_fill(("f", 1), "A", 10, 1_000, fee=37)
    a.charge_fee(("fee", 1), 13)
    dup = a.charge_fee(("fee", 1), 13)
    return _report("a9 fees accumulate once and are not double counted",
                   a.fees == 50 and (not dup[0])
                   and a.equity() == 100_000 + a.total_pnl() - 50,
                   f"fees {a.fees}, duplicate charge {dup}, equity {a.equity()}")


def a10_repeated_fill_is_ignored():
    a = Account(model(), 100_000)
    a.apply_fill(("f", 1), "A", 10, 900, fee=5)
    again = a.apply_fill(("f", 1), "A", 10, 900, fee=5)
    return _report("a10 the same fill applied twice changes nothing",
                   (not again[0]) and a.positions() == {"A": 10}
                   and a.fees == 5,
                   f"second apply {again}, positions {a.positions()}, "
                   f"fees {a.fees}")


def a11_snapshot_and_restore_are_exact():
    rng = random.Random(7)
    risk = model()
    a = Account(risk, 500_000)
    for i in range(60):
        a.apply_fill(("f", i), rng.choice(["A", "B"]),
                     rng.choice([-9, -3, 3, 9]), rng.randrange(400, 1200),
                     fee=rng.randrange(0, 12))
    blob = a.snapshot()
    b = Account(risk, 0)
    b.restore(blob)
    return _report("a11 a restored account is identical",
                   a.digest() == b.digest() and a.equity() == b.equity(),
                   f"{a.digest()} vs {b.digest()}; equity {a.equity()} vs "
                   f"{b.equity()}")


def a12_identity_holds_on_random_sequences():
    risk = model()
    worst = None
    for seed in range(500):
        rng = random.Random(seed)
        a = Account(risk, 1_000_000)
        for i in range(40):
            a.apply_fill((seed, i), rng.choice(["A", "B"]),
                         rng.choice([-17, -5, -1, 1, 5, 17]),
                         rng.randrange(300, 1500))
        marks = {"A": rng.randrange(300, 1500), "B": rng.randrange(300, 1500)}
        lhs = a.realised_pnl() + a.unrealised_pnl(marks)
        rhs = a.total_pnl(marks)
        if lhs != rhs:
            worst = (seed, lhs, rhs)
            break
    return _report(
        "a12 realised plus unrealised equals the cash-flow identity",
        worst is None,
        f"seed {worst[0]}: {worst[1]} against {worst[2]}" if worst else "",
    )


def a13_no_division_in_the_safety_path():
    """Every figure the safety condition uses is a sum of products of
    integers. The check is that a sequence built from primes, where any
    division would leave a remainder, still balances exactly."""
    risk = model()
    a = Account(risk, 999_983)
    for i, (q, p) in enumerate([(7, 1013), (-3, 1019), (11, 1021),
                                (-13, 1031), (5, 1033)]):
        a.apply_fill(("f", i), "A", q, p)
    marks = {"A": 1039, "B": 0}
    return _report(
        "a13 the identity is exact on quantities and prices that do not divide",
        a.realised_pnl() + a.unrealised_pnl(marks) == a.total_pnl(marks),
        f"{a.realised_pnl()} + {a.unrealised_pnl(marks)} against "
        f"{a.total_pnl(marks)}",
    )


def a14_the_account_is_a_fold_of_the_authoritative_log():
    """Snapshot and restore only prove the object serialises. What matters is
    that the account can be rebuilt from the ordering point's log after the
    process holding it is gone."""
    from marginstream.sequencer import Sequencer
    from marginstream.allocator2 import Allocator
    from marginstream.gateway2 import Gateway
    rng = random.Random(11)
    risk = model()
    seqr = Sequencer()
    alloc = Allocator(risk, sequencer=seqr, ttl=10 ** 6, gross_per_risk=10 ** 5)
    gw = Gateway(0, risk, sequencer=seqr)
    live = Account(risk, 400_000)
    leases, _ = alloc.issue("X", live.equity(), {0: 1}, now=0)
    gw.install_lease(leases[0])
    gen = alloc.current_generation("X")

    n = 0
    for i in range(80):
        if rng.random() < 0.6:
            gw.admit("X", rng.choice(["A", "B"]),
                     rng.choice([-7, -2, 2, 7]), gen, order_id=f"o{i}")
        else:
            liveorders = list(gw.live_orders("X").items())
            if liveorders:
                oid, (sym, rem) = rng.choice(liveorders)
                if gw.fill("X", oid, rem)[0]:
                    n += 1
                    price = risk.symbols[sym].mark + rng.randrange(-90, 91)
                    fee = abs(rem) * 3
                    seqr.record_fill(oid, rem, price, fee)
                    live.apply_fill(("log", n), sym, rem, price, fee)

    rebuilt = seqr.rebuild_account(risk, 400_000)
    return _report(
        "a14 an account rebuilt from the log matches the live one",
        rebuilt.digest() == live.digest()
        and rebuilt.equity() == live.equity(),
        f"{rebuilt.digest()} vs {live.digest()}; equity {rebuilt.equity()} "
        f"vs {live.equity()}",
    )


CASES = [a1_opening_realises_nothing, a2_adding_to_a_position,
         a3_partial_close_in_profit, a4_partial_close_at_a_loss,
         a5_full_close, a6_long_through_zero_to_short,
         a7_short_through_zero_to_long, a8_two_contracts_offset,
         a9_fees_are_counted_once, a10_repeated_fill_is_ignored,
         a11_snapshot_and_restore_are_exact,
         a12_identity_holds_on_random_sequences,
         a13_no_division_in_the_safety_path,
         a14_the_account_is_a_fold_of_the_authoritative_log]


def main():
    results = [c() for c in CASES]
    print(f"\n{sum(results)} of {len(results)} properties hold")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
