"""E1: a random order-state machine.

Admission, partial fill, full fill, cancel request, cancel acknowledgement,
fencing and terminal reconciliation, driven at random against one account
across several leases. After every step the oracle recomputes the account's
requirement from filled positions plus the worst subset of live orders that
could still fill, and compares it with equity at every scenario.
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

ACC = "X"
TRIALS = 400
STEPS = 200


def account_state(gws):
    filled, orders = {}, []
    for gw in gws.values():
        for sym, q in gw.filled_positions(ACC).items():
            filled[sym] = filled.get(sym, 0) + q
        for _oid, (sym, rem) in gw.live_orders(ACC).items():
            orders.append((sym, rem))
    return filled, orders


def worst_gross(risk, filled, orders):
    buy, sell = {}, {}
    for sym, rem in orders:
        if rem > 0:
            buy[sym] = buy.get(sym, 0) + rem
        else:
            sell[sym] = sell.get(sym, 0) - rem
    total = 0
    for sym in set(filled) | set(buy) | set(sell):
        mark = risk.symbols[sym].mark
        f = filled.get(sym, 0)
        total += mark * max(abs(f + buy.get(sym, 0)),
                            abs(f - sell.get(sym, 0)))
    return total


def worst_gaps(risk, filled, orders, collateral):
    """Requirement under the worst fill subset, against equity at each
    scenario.

    Both terms of the requirement use the worst subset. Taking the add-on from
    the filled position alone understates it, because unfilled orders can still
    raise the gross notional the account reaches.
    """
    worst = None
    for f in risk.grid:
        v = risk.loss_num(filled, f)
        for sym, rem in orders:
            leg = risk.leg_num(sym, rem, f)
            if leg > 0:
                v += leg
        if worst is None or v > worst:
            worst = v
    r = 0 if worst is None or worst <= 0 else risk.ceil_div(worst, risk.DEN)
    m = r + risk.A_of_gross(worst_gross(risk, filled, orders))
    return [m - (collateral - risk.loss(filled, f)) for f in risk.grid]


def one_trial(seed, counts):
    rng = random.Random(seed)
    n_sym = rng.randrange(2, 7)
    syms = [Symbol(f"S{i}", 0, rng.randrange(500, 3000),
                   rng.randrange(20, 200), rng.randrange(30, 160))
            for i in range(n_sym)]
    risk = RiskModel(syms, addon_kappa=rng.randrange(1, 3),
                     addon_scale=10 ** 7)
    collateral = rng.randrange(50_000, 2_000_000)

    seqr = Sequencer()
    alloc = Allocator(risk, ttl=rng.randrange(20, 200),
                      gross_per_risk=rng.randrange(5, 40))
    holders = [(0, 0), (1, 0)]
    gws = {h: Gateway(h[0], risk, incarnation=h[1], sequencer=seqr)
           for h in holders}
    now = 0
    oid = 0

    leases, _ = alloc.issue(ACC, collateral, {h: 1 for h in holders}, now=now)
    for h, lz in leases.items():
        gws[h].install_lease(lz)
    gen = alloc.current_generation(ACC)

    for _ in range(STEPS):
        now += 1
        act = rng.random()
        h = rng.choice(holders)
        gw = gws[h]

        if act < 0.45:
            oid += 1
            sym = rng.choice(syms).name
            qty = rng.choice([-30, -9, -3, -1, 1, 3, 9, 30])
            ok, _ = gw.admit(ACC, sym, qty, gen, now=now, order_id=f"o{oid}")
            counts["admitted" if ok else "refused"] += 1
        elif act < 0.70:
            live = list(gw.live_orders(ACC).items())
            if live:
                o, (sym, rem) = rng.choice(live)
                part = rem if rng.random() < 0.5 else (
                    rem // 2 if abs(rem) > 1 else rem)
                if part:
                    ok, _ = gw.fill(ACC, o, part)
                    if ok:
                        seqr.record_fill(o, part)
                        counts["filled"] += 1
        elif act < 0.80:
            live = list(gw.live_orders(ACC))
            if live:
                o = rng.choice(live)
                gw.cancel_request(ACC, o)
                counts["cancel_requested"] += 1
        elif act < 0.90:
            live = list(gw.live_orders(ACC))
            if live:
                o = rng.choice(live)
                ok, _ = gw.cancel_ack(ACC, o)
                if ok:
                    seqr.record_cancel(o)
                    counts["cancel_acked"] += 1
        else:
            lz = gw.lease.get(ACC)
            if lz is not None and now >= lz.expiry:
                seal = seqr.fence(lz.lease_id)
                ok, _ = alloc.release(ACC, lz.lease_id, seal, seqr)
                counts["released" if ok else "release_refused"] += 1
            alloc.bump_generation(ACC)
            new, _ = alloc.issue(ACC, collateral,
                                 {hh: 1 for hh in holders}, now=now)
            for hh, lz2 in new.items():
                gws[hh].install_lease(lz2)
            gen = alloc.current_generation(ACC)
            counts["reissued"] += 1

        filled, orders = account_state(gws)
        gaps = worst_gaps(risk, filled, orders, collateral)
        if max(gaps) > 0:
            return (seed, gaps, collateral, filled, orders)
    return None


def main():
    counts = {"admitted": 0, "refused": 0, "filled": 0, "cancel_requested": 0,
              "cancel_acked": 0, "released": 0, "release_refused": 0,
              "reissued": 0}
    failures = []
    for seed in range(TRIALS):
        f = one_trial(seed, counts)
        if f:
            failures.append(f)
    print(f"trials: {TRIALS}, steps per trial: {STEPS}")
    print("actions: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    print("oracle: worst-fill requirement against equity at every scenario")
    if failures:
        print(f"FAIL: {len(failures)} trials")
        for f in failures[:2]:
            print(f"  seed {f[0]}: gaps {f[1]} collateral {f[2]}")
            print(f"    filled {f[3]} orders {f[4]}")
        return 1
    print("no state reached a requirement above equity")
    os.makedirs("results", exist_ok=True)
    with open("results/e1_worst_fill.json", "w") as fh:
        json.dump(counts, fh, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
