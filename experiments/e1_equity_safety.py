"""E1: end-to-end safety against real account equity.

Earlier versions compared the requirement with collateral. A venue does not
have collateral to spend; it has equity, which is collateral plus what trading
has done, less fees. The allocator is given that figure at issuance, and the
oracle checks, at every scenario in the grid,

    worst-fill requirement  <=  equity at that scenario

where equity at scenario f is equity now less the loss the filled position
takes at f, because the grid displaces the marks rather than replacing them.

The random process fills at prices away from the mark, fills partially, closes,
crosses through zero, charges fees, and trades several contracts so that a gain
on one offsets a loss on another.
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
TRIALS = 300
STEPS = 160


def requirement(risk, gw):
    """Worst-fill requirement of the account held at this gateway."""
    return gw.used_risk(ACC) + risk.A_of_gross(gw.used_gross(ACC))


def run_trial(seed, stats, account_mode="exact"):
    rng = random.Random(seed)
    syms = [Symbol(f"S{i}", 0, 600 + 220 * i, 40 + 15 * i, 60 + 20 * i)
            for i in range(3)]
    risk = RiskModel(syms, addon_kappa=rng.randrange(1, 3),
                     addon_scale=10 ** 7)
    seqr = Sequencer()
    alloc = Allocator(risk, ttl=10 ** 6, gross_per_risk=rng.randrange(8, 40))
    gw = Gateway(0, risk, sequencer=seqr)

    collateral = rng.randrange(200_000, 3_000_000)
    book = Account(risk, collateral, mode=account_mode)   # what the venue uses
    truth = Account(risk, collateral, mode="exact")       # what the oracle uses

    leases, _ = alloc.issue(ACC, book.equity(), {0: 1}, now=0)
    gw.install_lease(leases[0])
    gen = alloc.current_generation(ACC)
    fill_no = 0

    for i in range(STEPS):
        r = rng.random()
        if r < 0.42:
            sym = rng.choice(syms).name
            qty = rng.choice([-25, -9, -3, 3, 9, 25])
            ok, _ = gw.admit(ACC, sym, qty, gen, order_id=f"{seed}:{i}")
            stats["admitted" if ok else "refused"] += 1
        elif r < 0.72:
            live = list(gw.live_orders(ACC).items())
            if live:
                oid, (sym, rem) = rng.choice(live)
                part = rem if rng.random() < 0.45 else (rem // 2 or rem)
                if part and gw.fill(ACC, oid, part)[0]:
                    fill_no += 1
                    mark = risk.symbols[sym].mark
                    price = mark + rng.randrange(-mark // 8, mark // 8 + 1)
                    fee = abs(part) * price // 5000
                    seqr.record_fill(oid, part, price, fee)
                    book.apply_fill(("f", fill_no), sym, part, price, fee)
                    truth.apply_fill(("f", fill_no), sym, part, price, fee)
                    stats["fills"] += 1
        elif r < 0.84:
            live = list(gw.live_orders(ACC))
            if live:
                oid = rng.choice(live)
                if gw.cancel_ack(ACC, oid)[0]:
                    seqr.record_cancel(oid)
                    stats["cancels"] += 1
        else:
            alloc.bump_generation(ACC)
            leases, _ = alloc.issue(ACC, book.equity(), {0: 1}, now=i)
            gw.install_lease(leases[0])
            gen = alloc.current_generation(ACC)
            stats["reissues"] += 1

        stats["max_overstatement"] = max(
            stats["max_overstatement"], book.equity() - truth.equity())
        req = requirement(risk, gw)
        stats["max_requirement"] = max(stats["max_requirement"], req)
        eq_now = truth.equity()
        if eq_now > 0:
            stats["max_utilisation_pct"] = max(
                stats["max_utilisation_pct"], (req * 100) // eq_now)
        stats["min_equity"] = min(stats["min_equity"], eq_now)
        for f in risk.grid:
            head = truth.equity_at(f) - req
            stats["min_headroom"] = min(stats["min_headroom"], head)
            if head < 0:
                stats["violations"] += 1
                return (seed, i, f, req, truth.equity_at(f))
    stats["realised"] += truth.realised_pnl()
    stats["unrealised"] += truth.unrealised_pnl()
    stats["fees"] += truth.fees
    return None


def fresh():
    return {"admitted": 0, "refused": 0, "fills": 0, "cancels": 0,
            "reissues": 0, "violations": 0, "realised": 0, "unrealised": 0,
            "fees": 0, "max_requirement": 0, "min_equity": 10 ** 18,
            "min_headroom": 10 ** 18, "max_utilisation_pct": 0,
            "max_overstatement": 0}


def main():
    stats = fresh()
    failures = []
    for seed in range(TRIALS):
        f = run_trial(seed, stats)
        if f:
            failures.append(f)
    print(f"trials: {TRIALS}, steps per trial: {STEPS}")
    print("actions: " + ", ".join(
        f"{k}={stats[k]}" for k in ("admitted", "refused", "fills", "cancels",
                                    "reissues")))
    print("account: " + ", ".join(
        f"{k}={stats[k]}" for k in ("realised", "unrealised", "fees")))
    print(f"max requirement {stats['max_requirement']}, "
          f"min equity {stats['min_equity']}, "
          f"min headroom {stats['min_headroom']}, "
          f"peak requirement as a share of equity "
          f"{stats['max_utilisation_pct']}%, "
          f"violations {stats['violations']}")
    print("oracle: worst-fill requirement against account equity at every "
          "scenario")
    if failures:
        for f in failures[:3]:
            print(f"  seed {f[0]} step {f[1]} scenario {f[2]}: requirement "
                  f"{f[3]} against equity {f[4]}")
        return 1
    print("no state reached a requirement above equity")
    os.makedirs("results", exist_ok=True)
    with open("results/e1_equity.json", "w") as fh:
        json.dump(stats, fh, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
