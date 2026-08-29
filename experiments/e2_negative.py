"""E2: the same schedule with generation checking disabled.

Part A is a scripted case with no randomness. One shard is held without a
lease update while equity falls, then spends the lease it still holds. The
case is run twice, once with fencing enabled and once disabled, and the two
outcomes are printed side by side.

Part B repeats the seeded sweep from E1 with fencing disabled.
"""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from marginstream.risk import RiskModel, Symbol
from marginstream.allocator import Allocator
from marginstream.shard import Shard
from marginstream.invariants import Oracle, Violation
from marginstream.sim import Config, run

ACCOUNT = "A1"


def scripted(fencing):
    """Two shards, one symbol each. Both receive a lease at epoch 0. Equity is
    halved at epoch 1 and only shard 0 receives the new lease. Both shards are
    then offered orders stamped with the current generation."""
    symbols = [
        Symbol(name="S0", shard=0, mark=1000, scan=100, beta=100),
        Symbol(name="S1", shard=1, mark=1000, scan=100, beta=100),
    ]
    risk = RiskModel(symbols, addon_kappa=1, addon_scale=10_000_000)
    alloc = Allocator(risk, drift_bps=0, residual=0)
    shards = {0: Shard(0, risk, fencing=fencing), 1: Shard(1, risk, fencing=fencing)}
    oracle = Oracle(risk, alloc, shards)

    equity = 20_000
    log = []

    alloc.epoch = 0
    leases, budget0 = alloc.issue(ACCOUNT, oracle.portfolio(ACCOUNT), equity, {0: 1, 1: 1})
    for g, lz in leases.items():
        shards[g].install_lease(lz)
    gen0 = alloc.current_generation(ACCOUNT)
    log.append(f"epoch 0  equity={equity}  budget={budget0}  "
               f"lease0={leases[0].amount}  lease1={leases[1].amount}  gen={gen0}")

    alloc.advance_epoch()
    equity //= 2
    merged = oracle.portfolio(ACCOUNT)
    if risk.M(merged) > equity:
        alloc.bump_generation(ACCOUNT)
    leases, budget1 = alloc.issue(ACCOUNT, merged, equity, {0: 1, 1: 1})
    shards[0].install_lease(leases[0])
    gen1 = alloc.current_generation(ACCOUNT)
    log.append(f"epoch 1  equity={equity}  budget={budget1}  "
               f"lease0={leases[0].amount}  shard 1 not updated  gen={gen1}")

    accepted, refused, violation = 0, 0, None
    for _ in range(60):
        for g in (0, 1):
            ok, _cost, _reason = shards[g].admit(ACCOUNT, f"S{g}", 1, gen1)
            if ok:
                accepted += 1
                try:
                    oracle.check_solvency(ACCOUNT, equity)
                except Violation as v:
                    violation = str(v)
                    break
            else:
                refused += 1
        if violation:
            break

    merged = oracle.portfolio(ACCOUNT)
    log.append(f"accepted={accepted}  refused={refused}  "
               f"M={risk.M(merged)}  equity={equity}  "
               f"shard0_spent={shards[0].total_spent(ACCOUNT)}  "
               f"shard1_spent={shards[1].total_spent(ACCOUNT)}")
    return log, violation


def main():
    print("Part A - scripted case\n")
    for fencing in (True, False):
        log, viol = scripted(fencing)
        print(f"  fencing={'on' if fencing else 'off'}")
        for line in log:
            print("    " + line)
        print(f"    violation: {viol if viol else 'none recorded'}\n")

    print("Part B - seeded sweep, fencing disabled\n")
    rows = []
    for seed in [1, 2, 3, 4, 5, 6, 7, 8]:
        cfg = Config(seed=seed, fencing=False, equity_drop_bps=1800)
        s = run(cfg)
        rows.append({"seed": seed, "accepted": s["accepted"],
                     "violations": len(s["violations"]),
                     "final_M": s["final_M"], "final_equity": s["final_equity"]})
    print(f"  {'seed':>4} {'acc':>6} {'viol':>6} {'M':>10} {'equity':>10}")
    for r in rows:
        print(f"  {r['seed']:>4} {r['accepted']:>6} {r['violations']:>6} "
              f"{r['final_M']:>10} {r['final_equity']:>10}")

    os.makedirs("results", exist_ok=True)
    with open("results/e2_negative.json", "w") as f:
        json.dump(rows, f, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
