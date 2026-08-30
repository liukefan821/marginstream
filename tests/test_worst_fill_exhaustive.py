"""The worst-fill formulas against brute force.

Both envelopes claim to be the maximum over every subset of live orders that
could still fill. That claim is checked here by enumerating all 2^n subsets and
comparing, rather than by argument.

    risk   max over subsets S of R(filled + sum of S)
           against  max(0, ceil(max_k [loss_k(filled)
                                       + sum_i max(0, loss_k(order_i))] / DEN))

    gross  max over subsets S of gross(filled + sum of S)
           against  sum_s mark_s * max(|f_s + buy_s|, |f_s - sell_s|)

Integers throughout, seeds fixed. Failures print the portfolio that produced
them.
"""
import itertools
import random
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from marginstream.risk import RiskModel, Symbol, FACTOR_GRID

TRIALS = 4000
MAX_ORDERS = 8


def closed_form_risk(risk, filled, orders):
    worst = None
    for f in FACTOR_GRID:
        v = risk.loss_num(filled, f)
        for sym, qty in orders:
            leg = risk.leg_num(sym, qty, f)
            if leg > 0:
                v += leg
        if worst is None or v > worst:
            worst = v
    if worst is None or worst <= 0:
        return 0
    return risk.ceil_div(worst, risk.DEN)


def closed_form_gross(risk, filled, orders):
    buy, sell = {}, {}
    for sym, qty in orders:
        if qty > 0:
            buy[sym] = buy.get(sym, 0) + qty
        else:
            sell[sym] = sell.get(sym, 0) - qty
    total = 0
    for sym in set(filled) | set(buy) | set(sell):
        mark = risk.symbols[sym].mark
        f = filled.get(sym, 0)
        total += mark * max(abs(f + buy.get(sym, 0)),
                            abs(f - sell.get(sym, 0)))
    return total


def brute_force(risk, filled, orders):
    worst_r = 0
    worst_g = 0
    n = len(orders)
    for mask in range(1 << n):
        pos = dict(filled)
        for i in range(n):
            if mask & (1 << i):
                sym, qty = orders[i]
                pos[sym] = pos.get(sym, 0) + qty
        r = risk.R(pos)
        g = risk.gross(pos)
        if r > worst_r:
            worst_r = r
        if g > worst_g:
            worst_g = g
    return worst_r, worst_g


def one_trial(seed):
    rng = random.Random(seed)
    n_sym = rng.randrange(1, 5)
    syms = [
        Symbol(f"S{i}", 0, rng.randrange(100, 3000),
               rng.randrange(10, 200), rng.randrange(20, 180))
        for i in range(n_sym)
    ]
    risk = RiskModel(syms, addon_kappa=0, addon_scale=1)

    filled = {}
    for sym in syms:
        q = rng.randrange(-30, 31)
        if q:
            filled[sym.name] = q

    n_orders = rng.randrange(0, MAX_ORDERS + 1)
    orders = []
    for _ in range(n_orders):
        sym = rng.choice(syms).name
        qty = rng.choice([-25, -11, -3, -1, 1, 3, 11, 25])
        orders.append((sym, qty))

    bf_r, bf_g = brute_force(risk, filled, orders)
    cf_r = closed_form_risk(risk, filled, orders)
    cf_g = closed_form_gross(risk, filled, orders)

    if bf_r != cf_r or bf_g != cf_g:
        return (seed, filled, orders, (bf_r, cf_r), (bf_g, cf_g))
    return None


def main():
    mismatches = []
    for seed in range(TRIALS):
        m = one_trial(seed)
        if m:
            mismatches.append(m)
    print(f"trials: {TRIALS}, orders per trial: 0..{MAX_ORDERS}, "
          f"subsets enumerated per trial: up to {1 << MAX_ORDERS}")
    if mismatches:
        print(f"FAIL: {len(mismatches)} trials disagreed")
        for m in mismatches[:3]:
            print(f"  seed {m[0]}: filled {m[1]} orders {m[2]}")
            print(f"    risk brute/closed {m[3]}  gross brute/closed {m[4]}")
        return 1
    print("closed form equals enumeration on every trial, for both envelopes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
