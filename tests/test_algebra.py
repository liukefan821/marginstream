"""Numerical checks of the two algebraic properties the admission rule uses.

test_R_subadditive samples portfolios, splits them by shard, and compares
R of the whole against the sum of R of the parts.

test_A_superadditive does the same for the add-on term.

Run with:  python3 -m pytest tests/ -q      (or python3 tests/test_algebra.py)
"""
import random
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from marginstream.risk import RiskModel, Symbol

N_SYMBOLS = 8
N_SHARDS = 4
TRIALS = 2000


def make_model(seed):
    rng = random.Random(seed)
    syms = [
        Symbol(name=f"S{i}", shard=i % N_SHARDS,
               mark=rng.randrange(500, 3000),
               scan=rng.randrange(20, 200),
               beta=rng.randrange(30, 160))
        for i in range(N_SYMBOLS)
    ]
    return RiskModel(syms, addon_kappa=1, addon_scale=1_000_000), syms


def split_by_shard(positions, syms):
    shard_of = {s.name: s.shard for s in syms}
    parts = {}
    for name, qty in positions.items():
        parts.setdefault(shard_of[name], {})[name] = qty
    return parts


def test_R_subadditive():
    risk, syms = make_model(11)
    rng = random.Random(12)
    worst_slack = None
    for _ in range(TRIALS):
        pos = {s.name: rng.randrange(-40, 41) for s in syms}
        parts = split_by_shard(pos, syms)
        whole = risk.R(pos)
        summed = sum(risk.R(p) for p in parts.values())
        assert whole <= summed, (whole, summed, pos)
        slack = summed - whole
        if worst_slack is None or slack > worst_slack:
            worst_slack = slack
    return worst_slack


def test_A_superadditive():
    risk, syms = make_model(21)
    rng = random.Random(22)
    worst_slack = None
    for _ in range(TRIALS):
        pos = {s.name: rng.randrange(-40, 41) for s in syms}
        parts = split_by_shard(pos, syms)
        whole = risk.A(pos)
        summed = sum(risk.A(p) for p in parts.values())
        assert whole >= summed, (whole, summed, pos)
        slack = whole - summed
        if worst_slack is None or slack > worst_slack:
            worst_slack = slack
    return worst_slack


def test_admission_bound():
    """If every part's R fits its share of a budget, the whole portfolio's R
    does not exceed the budget."""
    risk, syms = make_model(31)
    rng = random.Random(32)
    for _ in range(TRIALS):
        pos = {s.name: rng.randrange(-40, 41) for s in syms}
        parts = split_by_shard(pos, syms)
        shares = {g: risk.R(p) for g, p in parts.items()}
        budget = sum(shares.values())
        assert risk.R(pos) <= budget
    return True


if __name__ == "__main__":
    a = test_R_subadditive()
    b = test_A_superadditive()
    test_admission_bound()
    print(f"trials per test: {TRIALS}")
    print(f"R: sum of parts minus whole, largest observed = {a}")
    print(f"A: whole minus sum of parts, largest observed = {b}")
    print("admission bound held on every sample")
