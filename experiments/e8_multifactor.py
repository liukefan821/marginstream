"""E8: the E1 oracle on a scenario set with two factors, mixed-sign loadings
and idiosyncratic moves.

Everything except the risk model is E1: random admissions, partial fills at
prices anywhere inside the band, cancels and re-issues, and a check after every
step that the worst-fill requirement is at most the account's equity at every
scenario in the set. A binding trial then fills a hedged pair to the ceiling at
the worst price and fee the policy allows.

The run also records, for the final position of each trial, how far the gross
figure a lease reserves against (each symbol at its own highest mark) sits
above the largest gross any single scenario in the set produces.
"""
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from marginstream.risk import Symbol
from marginstream.scenarios import ScenarioRiskModel, two_factor_table
from marginstream.allocator2 import Allocator
from marginstream.gateway2 import Gateway
from marginstream.sequencer import Sequencer
from marginstream.account import Account
from marginstream.execution import execute_fill, execute_cancel

ACC = "X"
TRIALS = 300
STEPS = 160
BETA1 = (100, 80, -60, 40)
BETA2 = (30, -70, 90, 50)
IDIO = (25, 35, 30, 45)


def make_risk(rng=None, kappa=1, tight_policy=False):
    def mk(i):
        mark = 600 + 220 * i
        if tight_policy:
            return Symbol(f"S{i}", 0, mark, 40 + 15 * i, 100, band=5,
                          fee_per_lot=2)
        return Symbol(f"S{i}", 0, mark, 40 + 15 * i, 100,
                      band=mark // 8, fee_per_lot=(mark + mark // 8) // 5000 + 1)
    syms = [mk(i) for i in range(4)]
    shocks = two_factor_table(syms, BETA1, BETA2, IDIO)
    return ScenarioRiskModel(syms, shocks, addon_kappa=kappa,
                             addon_scale=10 ** 7), syms


def requirement(risk, gw):
    return gw.used_risk(ACC) + risk.A_of_gross(gw.used_gross(ACC))


def run_trial(seed, stats, collateral_range=(200_000, 3_000_000), solve_mult=1):
    rng = random.Random(seed)
    risk, syms = make_risk(kappa=rng.randrange(1, 3))
    seqr = Sequencer()
    alloc = Allocator(risk, sequencer=seqr, ttl=10 ** 6,
                      gross_per_risk=rng.randrange(8, 40))
    gw = Gateway(0, risk, sequencer=seqr)
    collateral = rng.randrange(*collateral_range)
    acct = Account(risk, collateral)
    leases, _ = alloc.issue(ACC, acct.equity() * solve_mult, {0: 1}, now=0)
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
                    s = risk.symbols[sym]
                    price = s.mark + rng.randrange(-s.band, s.band + 1)
                    fee = min(abs(part) * price // 5000, s.fee_per_lot * abs(part))
                    okf, _ = execute_fill(seqr, gw, acct, f"f{fill_no}", oid,
                                          ACC, sym, part, price, fee)
                    stats["fills" if okf else "fills_refused"] += 1
        elif r < 0.84:
            live = list(gw.live_orders(ACC))
            if live and execute_cancel(seqr, gw, ACC, rng.choice(live))[0]:
                stats["cancels"] += 1
        else:
            alloc.bump_generation(ACC)
            leases, _ = alloc.issue(ACC, acct.equity() * solve_mult, {0: 1}, now=i)
            gw.install_lease(leases[0])
            gen = alloc.current_generation(ACC)
            stats["reissues"] += 1
        lease = gw.lease.get(ACC)
        if lease is not None and lease.risk_amount:
            stats["max_risk_use_pct"] = max(stats["max_risk_use_pct"],
                gw.used_risk(ACC) * 100 // lease.risk_amount)
        req = requirement(risk, gw)
        eq = acct.equity()
        if eq > 0:
            stats["max_utilisation_pct"] = max(stats["max_utilisation_pct"],
                                               req * 100 // eq)
        for k in risk.grid:
            head = acct.equity_at(k) - req
            stats["min_headroom"] = min(stats["min_headroom"], head)
            if head < 0:
                stats["violations"] += 1
                return (seed, i, k, req, acct.equity_at(k))
    pos = gw.local_positions(ACC)
    reach = risk.gross_reach(pos)
    best = risk.max_scenario_gross(pos)
    if best > 0:
        stats["reach_over_scenario_pct_max"] = max(
            stats["reach_over_scenario_pct_max"], (reach - best) * 100 // best)
    return None


def binding_trial():
    """Long S0 and short S1 filled alternately to the ceiling, each fill at the
    worst price and fee the policy allows. S0 and S1 carry opposite-sign
    loadings on the second factor, so the pair is hedged on one factor and
    not on the other."""
    risk, syms = make_risk(kappa=0, tight_policy=True)
    seqr = Sequencer()
    alloc = Allocator(risk, sequencer=seqr, ttl=10 ** 6, gross_per_risk=10 ** 6)
    gw = Gateway(0, risk, sequencer=seqr)
    acct = Account(risk, 100_000)
    leases, _ = alloc.issue(ACC, acct.equity(), {0: 1}, now=0)
    gw.install_lease(leases[0])
    gen = alloc.current_generation(ACC)
    lease = leases[0]
    orders = []
    n = 0
    while True:
        sym, q = ("S0", 1) if n % 2 == 0 else ("S1", -1)
        if not gw.admit(ACC, sym, q, gen, order_id=f"b{n}")[0]:
            break
        orders.append((sym, q))
        n += 1
    for i, (sym, q) in enumerate(orders):
        s = risk.symbols[sym]
        price = s.mark + s.band if q > 0 else s.mark - s.band
        execute_fill(seqr, gw, acct, f"bf{i}", f"b{i}", ACC, sym, q, price,
                     s.fee_per_lot)
    req = requirement(risk, gw)
    worst = min(acct.equity_at(k) for k in risk.grid)
    return {"admitted": n, "requirement": req, "equity": acct.equity(),
            "worst_equity": worst, "breach": max(0, req - worst),
            "risk_pct": gw.used_risk(ACC) * 100 // lease.risk_amount,
            "debit_pct": gw.used_debit(ACC) * 100 // lease.debit_amount,
            "requirement_over_equity_pct": req * 100 // acct.equity(),
            "scenarios": len(risk.grid)}


def fresh():
    return {"admitted": 0, "refused": 0, "fills": 0, "fills_refused": 0,
            "cancels": 0, "reissues": 0, "violations": 0,
            "min_headroom": 10 ** 18, "max_utilisation_pct": 0,
            "max_risk_use_pct": 0, "reach_over_scenario_pct_max": 0}


def report(label, stats, failures):
    print(f"{label}: trials {TRIALS}, steps per trial {STEPS}")
    print("  actions: " + ", ".join(f"{k}={stats[k]}" for k in
          ("admitted", "refused", "fills", "fills_refused", "cancels", "reissues")))
    print(f"  peak risk-envelope use {stats['max_risk_use_pct']}%, peak "
          f"requirement as a share of equity {stats['max_utilisation_pct']}%, "
          f"min headroom {stats['min_headroom']}, violations {stats['violations']}")
    print(f"  gross reserved above the largest single-scenario gross, worst "
          f"final position: {stats['reach_over_scenario_pct_max']}%")
    for f in failures[:3]:
        print(f"  seed {f[0]} step {f[1]} scenario {f[2]}: requirement {f[3]} "
              f"against equity {f[4]}")


def main():
    risk, _ = make_risk()
    signs = sorted({(b1 > 0, b2 > 0) for b1, b2 in zip(BETA1, BETA2)})
    print(f"scenario set: {len(risk.grid)} scenarios (3x3 joint moves of two "
          f"factors, plus 8 idiosyncratic), 4 symbols, loading sign patterns "
          f"{signs}")
    stats = fresh()
    failures = [f for f in (run_trial(s, stats) for s in range(TRIALS)) if f]
    report("wide collateral (as E1)", stats, failures)
    tight = fresh()
    tfail = [f for f in (run_trial(s, tight, (5_000, 40_000))
                         for s in range(TRIALS)) if f]
    report("small collateral, envelopes near the ceiling", tight, tfail)
    failures += tfail
    # control: ceilings solved against twice the equity, which is the same as
    # dropping the factor of two. the oracle is expected to report breaches
    # here; if it does not, a zero above says nothing.
    ctrl = fresh()
    cfail = [f for f in (run_trial(s, ctrl, (5_000, 40_000), solve_mult=2)
                         for s in range(TRIALS)) if f]
    print(f"control, ceilings solved against 2x equity: trials with a breach "
          f"{len(cfail)} of {TRIALS}")
    b = binding_trial()
    print("\nbinding trial, hedged pair filled at the worst price and fee")
    print(f"  admitted {b['admitted']}, requirement {b['requirement']}, equity "
          f"{b['equity']}, worst-scenario equity {b['worst_equity']}, "
          f"breach {b['breach']}")
    print(f"  envelope use: risk {b['risk_pct']}%, debit {b['debit_pct']}%; "
          f"requirement is {b['requirement_over_equity_pct']}% of equity")
    os.makedirs("results", exist_ok=True)
    with open("results/e8_multifactor.json", "w") as fh:
        json.dump({"random": stats, "random_small_collateral": tight, "control_2x_breached_trials": len(cfail), "binding": b}, fh, indent=2)
    return 1 if failures or b["breach"] else 0


if __name__ == "__main__":
    sys.exit(main())
