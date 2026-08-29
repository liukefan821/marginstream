"""E5: market-state suppression.

The conditional lease reads the published market state on the market-data path.
This experiment feeds the shards a state index that differs from the state the
invariant checker uses, standing in for an account that can hold the published
mark below where the market actually is for a bounded window.

Three shard configurations are compared on the same order sequence and the same
true market path:

  no_curve   scalar lease, unaffected by the state index
  naive      the curve is evaluated at the state carried by the current message
  ratchet    the curve is evaluated at the most adverse state observed since
             the lease was installed

The counter reported is the number of ticks spent with the requirement above
equity, where equity is computed at the true state.
"""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from marginstream.risk import RiskModel, Symbol
from marginstream.allocator import Allocator, CurveAllocator
from marginstream.shard import Shard

ACCOUNT = "A1"
N_SHARDS = 3
N_SYMBOLS = 6
EPOCHS = 8
TICKS = 60
COLLATERAL = 200_000

FACTOR_OF_STATE = (0, -1, -2, -3)
TRUE_PATH = [0] * 15 + [1] * 15 + [2] * 15 + [3] * 15
SHAPE = (1000, 700, 450, 250)

# ticks on which the published state is held at 0 regardless of the true state
SUPPRESS_FROM, SUPPRESS_TO = 20, 50


def observed_state(tick, suppress):
    if suppress and SUPPRESS_FROM <= tick < SUPPRESS_TO:
        return 0
    return TRUE_PATH[tick]


def build():
    syms = [
        Symbol(name=f"S{i}", shard=i % N_SHARDS, mark=1000 + 100 * i,
               scan=60 + 6 * i, beta=90 + 10 * (i % 3))
        for i in range(N_SYMBOLS)
    ]
    return syms, RiskModel(syms, addon_kappa=1, addon_scale=20_000_000)


def portfolio(shards):
    merged = {}
    for sh in shards.values():
        for sym, qty in sh.local_positions(ACCOUNT).items():
            merged[sym] = merged.get(sym, 0) + qty
    return merged


def equity_at(risk, positions, state):
    return COLLATERAL - risk.loss(positions, FACTOR_OF_STATE[state])


def liquidate(risk, shards, state):
    for pct in (85, 70, 55, 40, 25, 10, 0):
        trial = {}
        for sh in shards.values():
            for sym, qty in sh.local_positions(ACCOUNT).items():
                trial[sym] = trial.get(sym, 0) + (qty * pct) // 100
        if risk.M(trial) <= equity_at(risk, trial, state):
            for sh in shards.values():
                pos = sh.local_positions(ACCOUNT)
                for sym in list(pos):
                    pos[sym] = (pos[sym] * pct) // 100
            return pct
    return 0


def run_mode(mode, suppress):
    syms, risk = build()
    by_shard = {}
    for s in syms:
        by_shard.setdefault(s.shard, []).append(s.name)

    use_curve = mode != "no_curve"
    if use_curve:
        alloc = CurveAllocator(risk, SHAPE, FACTOR_OF_STATE)
    else:
        alloc = Allocator(risk)
    shards = {g: Shard(g, risk, fencing=True, ratchet=(mode == "ratchet"))
              for g in range(N_SHARDS)}
    weights = {g: 1 for g in range(N_SHARDS)}

    accepted = breach_ticks = liquidations = 0

    for epoch in range(EPOCHS):
        alloc.epoch = epoch
        merged = portfolio(shards)
        if risk.M(merged) > equity_at(risk, merged, 0):
            alloc.bump_generation(ACCOUNT)
            liquidate(risk, shards, 0)
            liquidations += 1
            merged = portfolio(shards)

        if use_curve:
            leases, _ = alloc.issue_curve(ACCOUNT, merged, COLLATERAL, weights)
        else:
            leases, _ = alloc.issue(ACCOUNT, merged,
                                    equity_at(risk, merged, 0), weights)
        for g, lz in leases.items():
            shards[g].install_lease(lz)
        gen = alloc.current_generation(ACCOUNT)

        for t in range(TICKS):
            true_state = TRUE_PATH[t]
            seen = observed_state(t, suppress)
            g = t % N_SHARDS
            sym = by_shard[g][t % len(by_shard[g])]
            ok, _c, reason = shards[g].admit(ACCOUNT, sym, 30, gen,
                                             market_state=seen)
            if ok:
                accepted += 1
            elif reason == "reduce_only":
                liquidate(risk, shards, true_state)
                liquidations += 1
                alloc.bump_generation(ACCOUNT)
                merged = portfolio(shards)
                if use_curve:
                    leases, _ = alloc.issue_curve(ACCOUNT, merged, COLLATERAL, weights)
                else:
                    leases, _ = alloc.issue(ACCOUNT, merged,
                                            equity_at(risk, merged, true_state), weights)
                for gg, lz in leases.items():
                    shards[gg].install_lease(lz)
                gen = alloc.current_generation(ACCOUNT)

            merged = portfolio(shards)
            if risk.M(merged) > equity_at(risk, merged, true_state):
                breach_ticks += 1

        alloc.advance_epoch()

    merged = portfolio(shards)
    return {
        "mode": mode,
        "suppressed": suppress,
        "accepted": accepted,
        "liquidations": liquidations,
        "breach_ticks": breach_ticks,
        "total_ticks": EPOCHS * TICKS,
        "final_M": risk.M(merged),
    }


def main():
    rows = []
    for mode in ("no_curve", "naive", "ratchet"):
        for suppress in (False, True):
            rows.append(run_mode(mode, suppress))

    print(f"{'mode':>10} {'suppressed':>11} {'acc':>5} {'liq':>5} "
          f"{'breach_ticks':>13} {'of':>5} {'final_M':>9}")
    for r in rows:
        print(f"{r['mode']:>10} {str(r['suppressed']):>11} {r['accepted']:>5} "
              f"{r['liquidations']:>5} {r['breach_ticks']:>13} "
              f"{r['total_ticks']:>5} {r['final_M']:>9}")

    os.makedirs("results", exist_ok=True)
    with open("results/e5_adversarial.json", "w") as f:
        json.dump(rows, f, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
