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
from marginstream.execution import execute_fill, execute_cancel

ACC = "X"
TRIALS = 300
STEPS = 160


def requirement(risk, gw):
    """Worst-fill requirement of the account held at this gateway."""
    return gw.used_risk(ACC) + risk.A_of_gross(gw.used_gross(ACC))


def run_trial(seed, stats, account_mode="exact"):
    rng = random.Random(seed)
    # the band and the fee cap are venue policy and are what the execution
    # debit envelope reserves against. the fill generator below stays inside
    # them, and the ordering point refuses anything that does not.
    def mk(i):
        mark = 600 + 220 * i
        return Symbol(f"S{i}", 0, mark, 40 + 15 * i, 60 + 20 * i,
                      band=mark // 8, fee_per_lot=(mark + mark // 8) // 5000 + 1)
    syms = [mk(i) for i in range(3)]
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
                if part:
                    fill_no += 1
                    symb = risk.symbols[sym]
                    price = symb.mark + rng.randrange(-symb.band, symb.band + 1)
                    fee = min(abs(part) * price // 5000,
                              symb.fee_per_lot * abs(part))
                    okf, _ = execute_fill(seqr, gw, truth, f"f{fill_no}", oid,
                                          ACC, sym, part, price, fee)
                    if okf:
                        book.apply_fill(("f", fill_no), sym, part, price, fee)
                        stats["fills"] += 1
                    else:
                        stats["fills_refused"] += 1
        elif r < 0.84:
            live = list(gw.live_orders(ACC))
            if live:
                oid = rng.choice(live)
                if execute_cancel(seqr, gw, ACC, oid)[0]:
                    stats["cancels"] += 1
        else:
            alloc.bump_generation(ACC)
            leases, _ = alloc.issue(ACC, book.equity(), {0: 1}, now=i)
            gw.install_lease(leases[0])
            gen = alloc.current_generation(ACC)
            stats["reissues"] += 1

        stats["max_overstatement"] = max(
            stats["max_overstatement"], book.equity() - truth.equity())
        lease = gw.lease.get(ACC)
        if lease is not None and lease.risk_amount:
            stats["max_risk_use_pct"] = max(
                stats["max_risk_use_pct"],
                gw.used_risk(ACC) * 100 // lease.risk_amount)
        if lease is not None and lease.gross_amount:
            stats["max_gross_use_pct"] = max(
                stats["max_gross_use_pct"],
                gw.used_gross(ACC) * 100 // lease.gross_amount)
        if lease is not None and lease.debit_amount:
            stats["max_debit_use_pct"] = max(
                stats["max_debit_use_pct"],
                gw.used_debit(ACC) * 100 // lease.debit_amount)
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
    return {"admitted": 0, "refused": 0, "fills": 0, "fills_refused": 0,
            "cancels": 0,
            "reissues": 0, "violations": 0, "realised": 0, "unrealised": 0,
            "fees": 0, "max_requirement": 0, "min_equity": 10 ** 18,
            "min_headroom": 10 ** 18, "max_utilisation_pct": 0,
            "max_overstatement": 0, "max_risk_use_pct": 0,
            "max_gross_use_pct": 0, "max_debit_use_pct": 0}


def binding_trial():
    """A deterministic run driven to the ceiling, filling everything at the
    worst price and fee the policy allows. The random trials above stay well
    inside the envelopes; this one does not."""
    mark, band, cap = 1000, 5, 2
    syms = [Symbol("A", 0, mark, 200, 100, band, cap)]
    risk = RiskModel(syms, addon_kappa=0, addon_scale=1)
    seqr = Sequencer()
    alloc = Allocator(risk, ttl=10 ** 6, gross_per_risk=10 ** 6)
    gw = Gateway(0, risk, sequencer=seqr)
    acct = Account(risk, 100_000)
    leases, _ = alloc.issue(ACC, acct.equity(), {0: 1}, now=0)
    gw.install_lease(leases[0])
    gen = alloc.current_generation(ACC)
    lease = leases[0]

    n = 0
    while gw.admit(ACC, "A", 1, gen, order_id=f"b{n}")[0]:
        n += 1
    for i in range(n):
        execute_fill(seqr, gw, acct, f"bf{i}", f"b{i}", ACC, "A", 1,
                     mark + band, cap)

    req = requirement(risk, gw)
    worst = min(acct.equity_at(f) for f in risk.grid)
    return {
        "admitted": n, "requirement": req, "equity": acct.equity(),
        "worst_equity": worst, "breach": max(0, req - worst),
        "risk_pct": gw.used_risk(ACC) * 100 // lease.risk_amount,
        "gross_pct": gw.used_gross(ACC) * 100 // lease.gross_amount,
        "debit_pct": gw.used_debit(ACC) * 100 // lease.debit_amount,
        "requirement_over_equity_pct": req * 100 // acct.equity(),
    }


def main():
    stats = fresh()
    failures = []
    for seed in range(TRIALS):
        f = run_trial(seed, stats)
        if f:
            failures.append(f)
    print(f"trials: {TRIALS}, steps per trial: {STEPS}")
    print("actions: " + ", ".join(
        f"{k}={stats[k]}" for k in ("admitted", "refused", "fills",
                                    "fills_refused", "cancels", "reissues")))
    print(f"peak use of each envelope: risk {stats['max_risk_use_pct']}%, "
          f"gross {stats['max_gross_use_pct']}%, "
          f"debit {stats['max_debit_use_pct']}%")
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
    b = binding_trial()
    print("\nbinding trial, filled at the worst price and fee the policy allows")
    print(f"  admitted {b['admitted']}, requirement {b['requirement']}, "
          f"equity {b['equity']}, worst-scenario equity {b['worst_equity']}, "
          f"breach {b['breach']}")
    print(f"  envelope use: risk {b['risk_pct']}%, gross {b['gross_pct']}%, "
          f"debit {b['debit_pct']}%; requirement is "
          f"{b['requirement_over_equity_pct']}% of equity")
    if b["breach"]:
        return 1
    print("\nno state reached a requirement above equity")
    os.makedirs("results", exist_ok=True)
    with open("results/e1_equity.json", "w") as fh:
        json.dump(stats, fh, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
