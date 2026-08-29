"""E4: intra-epoch market movement and the latency of the reduce-only trigger.

An epoch is divided into ticks and the published market state index advances
during the epoch, so equity falls while leases issued at the start of the epoch
are still in force.

A lease cannot undo an admission it already granted. What differs between the
two modes is when a shard can tell, on its own, that the account has moved past
what the current market state allows.

  scalar : one amount per shard for the epoch. The condition is visible to the
           allocator at the next epoch boundary.
  curve  : a non-increasing amount per market state. A shard compares its own
           consumption against the curve at the state it reads on the
           market-data path, and reports the condition on the same tick.

Both modes run the same order sequence and the same market path. The counter
reported is the number of ticks spent with the requirement above equity.
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
MARKET_PATH = [0] * 15 + [1] * 15 + [2] * 15 + [3] * 15
SHAPES = {
    "flat":  (1000, 1000, 1000, 1000),
    "mild":  (1000, 700, 450, 250),
    "steep": (1000, 450, 180, 60),
}


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


def run_mode(mode, shape_name="mild"):
    syms, risk = build()
    by_shard = {}
    for s in syms:
        by_shard.setdefault(s.shard, []).append(s.name)

    if mode == "curve":
        alloc = CurveAllocator(risk, SHAPES[shape_name], FACTOR_OF_STATE)
    else:
        alloc = Allocator(risk)

    shards = {g: Shard(g, risk, fencing=True) for g in range(N_SHARDS)}
    weights = {g: 1 for g in range(N_SHARDS)}

    accepted = reduce_only_hits = breach_ticks = liquidations = 0

    for epoch in range(EPOCHS):
        alloc.epoch = epoch
        merged = portfolio(shards)
        if risk.M(merged) > equity_at(risk, merged, 0):
            alloc.bump_generation(ACCOUNT)
            liquidate(risk, shards, 0)
            liquidations += 1
            merged = portfolio(shards)

        if mode == "curve":
            leases, _ = alloc.issue_curve(ACCOUNT, merged, COLLATERAL, weights)
        else:
            leases, _ = alloc.issue(ACCOUNT, merged, equity_at(risk, merged, 0), weights)
        for g, lz in leases.items():
            shards[g].install_lease(lz)
        gen = alloc.current_generation(ACCOUNT)

        for t in range(TICKS):
            state = MARKET_PATH[t]
            g = t % N_SHARDS
            sym = by_shard[g][t % len(by_shard[g])]
            ok, _c, reason = shards[g].admit(ACCOUNT, sym, 30, gen,
                                             market_state=state)
            if ok:
                accepted += 1
            elif reason == "reduce_only":
                reduce_only_hits += 1
                # the shard reports the condition on the tick it observes it.
                # the allocator reduces the account, bumps the generation so
                # every outstanding lease is void, and re-issues.
                liquidate(risk, shards, state)
                liquidations += 1
                alloc.bump_generation(ACCOUNT)
                merged = portfolio(shards)
                if mode == "curve":
                    leases, _ = alloc.issue_curve(ACCOUNT, merged, COLLATERAL, weights)
                else:
                    leases, _ = alloc.issue(ACCOUNT, merged,
                                            equity_at(risk, merged, state), weights)
                for gg, lz in leases.items():
                    shards[gg].install_lease(lz)
                gen = alloc.current_generation(ACCOUNT)

            merged = portfolio(shards)
            if risk.M(merged) > equity_at(risk, merged, state):
                breach_ticks += 1

        alloc.advance_epoch()

    merged = portfolio(shards)
    return {
        "mode": mode if mode != "curve" else f"curve:{shape_name}",
        "accepted": accepted,
        "reduce_only_hits": reduce_only_hits,
        "liquidations": liquidations,
        "breach_ticks": breach_ticks,
        "total_ticks": EPOCHS * TICKS,
        "final_M": risk.M(merged),
    }


def main():
    rows = [run_mode("scalar")]
    for name in ("flat", "mild", "steep"):
        rows.append(run_mode("curve", name))

    print(f"{'mode':>14} {'acc':>5} {'ro_hits':>8} {'liq':>5} "
          f"{'breach_ticks':>13} {'of':>5} {'final_M':>9}")
    for r in rows:
        print(f"{r['mode']:>14} {r['accepted']:>5} {r['reduce_only_hits']:>8} "
              f"{r['liquidations']:>5} {r['breach_ticks']:>13} "
              f"{r['total_ticks']:>5} {r['final_M']:>9}")

    os.makedirs("results", exist_ok=True)
    with open("results/e4_conditional.json", "w") as f:
        json.dump(rows, f, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
