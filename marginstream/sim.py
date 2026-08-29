"""Simulation harness.

Drives a single account across several symbol shards. Everything is integer
arithmetic and a seeded PRNG, so a given (seed, config) reproduces byte for
byte. No wall-clock time is read.
"""

import random
from dataclasses import dataclass, field

from .risk import RiskModel, Symbol
from .allocator import Allocator
from .shard import Shard
from .invariants import Oracle, Violation


@dataclass
class Config:
    seed: int = 1
    n_symbols: int = 6
    n_shards: int = 3
    epochs: int = 12
    orders_per_epoch: int = 40
    equity: int = 120_000
    equity_drop_bps: int = 0        # applied at each epoch boundary
    drift_bps: int = 50
    residual: int = 1_000
    addon_kappa: int = 1
    addon_scale: int = 2_000_000
    fencing: bool = True
    stale_order_rate: int = 30      # percent of orders sent with an old generation
    lease_loss_rate: int = 25       # percent chance a shard misses an epoch's lease
    log: list = field(default_factory=list)


def build_symbols(cfg):
    syms = []
    for i in range(cfg.n_symbols):
        syms.append(Symbol(
            name=f"S{i}",
            shard=i % cfg.n_shards,
            mark=1_000 + 100 * i,
            scan=50 + 5 * i,
            beta=80 + 10 * (i % 4),
        ))
    return syms



def _liquidate(risk, shards, account, equity):
    """Scale the account's positions down across all shards until the
    requirement fits the equity. Scaling is applied to every shard by the same
    integer percentage so the reduction is deterministic and order-free."""
    for pct in (90, 75, 60, 45, 30, 20, 10, 5, 0):
        trial = {}
        for sh in shards.values():
            for sym, qty in sh.local_positions(account).items():
                trial[sym] = trial.get(sym, 0) + (qty * pct) // 100
        if risk.M(trial) <= equity:
            for sh in shards.values():
                pos = sh.local_positions(account)
                for sym in list(pos):
                    pos[sym] = (pos[sym] * pct) // 100
            return pct
    return 0


def run(cfg):
    rng = random.Random(cfg.seed)
    symbols = build_symbols(cfg)
    risk = RiskModel(symbols, cfg.addon_kappa, cfg.addon_scale)
    alloc = Allocator(risk, drift_bps=cfg.drift_bps, residual=cfg.residual)
    shards = {i: Shard(i, risk, fencing=cfg.fencing) for i in range(cfg.n_shards)}
    oracle = Oracle(risk, alloc, shards)

    by_shard = {}
    for s in symbols:
        by_shard.setdefault(s.shard, []).append(s.name)

    account = "A1"
    equity = cfg.equity
    stats = {
        "accepted": 0, "rejected": 0, "stale_rejected": 0,
        "lease_exhausted": 0, "lease_missed": 0, "liquidations": 0,
        "violations": [], "budgets": [],
    }

    held_generation = {}     # shard -> generation the shard last saw

    for epoch in range(cfg.epochs):
        alloc.epoch = epoch
        if epoch > 0 and cfg.equity_drop_bps:
            equity -= (equity * cfg.equity_drop_bps) // 10000

        # An equity fall can put the account below its requirement without any
        # order having been admitted. That state is handled by liquidation:
        # the generation is bumped, which voids every outstanding lease, and
        # positions are scaled down until the requirement fits the equity.
        merged = oracle.portfolio(account)
        if risk.M(merged) > equity:
            stats["liquidations"] += 1
            alloc.bump_generation(account)
            _liquidate(risk, shards, account, equity)
            merged = oracle.portfolio(account)

        weights = {g: 1 for g in shards}
        leases, budget = alloc.issue(account, merged, equity, weights)
        stats["budgets"].append(budget)

        try:
            oracle.check_lease_sum(account, epoch, alloc.current_generation(account), budget)
        except Violation as v:
            stats["violations"].append(str(v))

        # a share of shards do not receive the epoch's lease, standing in for a
        # partition between allocator and shard across the boundary. Such a
        # shard keeps the lease and the consumption counter it already had.
        for g, lease in leases.items():
            if epoch > 0 and rng.randrange(100) < cfg.lease_loss_rate:
                stats["lease_missed"] += 1
                continue
            shards[g].install_lease(lease)
            held_generation[g] = lease.generation

        gen_now = alloc.current_generation(account)

        for k in range(cfg.orders_per_epoch):
            g = rng.randrange(cfg.n_shards)
            sym = rng.choice(by_shard[g])
            # directional flow: the constraint only binds when positions accumulate
            qty = rng.choice([-1, 1, 2, 3, 3, 4])
            order_id = f"e{epoch}-{k}"

            # a share of orders arrive stamped with the previous generation,
            # standing in for a message that was in flight across the boundary
            if rng.randrange(100) < cfg.stale_order_rate and gen_now > 1:
                stamped = gen_now - 1
            else:
                stamped = gen_now

            ok, cost, reason = shards[g].admit(account, sym, qty, stamped)
            if ok:
                stats["accepted"] += 1
                try:
                    oracle.check_order_id(order_id)
                    oracle.check_all(account, equity)
                except Violation as v:
                    stats["violations"].append(str(v))
                    cfg.log.append(("violation", epoch, k, str(v)))
            else:
                stats["rejected"] += 1
                if reason == "stale_generation":
                    stats["stale_rejected"] += 1
                elif reason == "lease_exhausted":
                    stats["lease_exhausted"] += 1

        alloc.advance_epoch()

    merged = oracle.portfolio(account)
    stats["final_M"] = risk.M(merged)
    stats["final_R"] = risk.R(merged)
    stats["final_A"] = risk.A(merged)
    stats["final_equity"] = equity
    stats["subadditivity_gap"] = sum(
        risk.R(shards[g].local_positions(account)) for g in shards
    ) - risk.R(merged)
    return stats
