"""Margin allocator.

Runs off the order path. Once per epoch it recomputes, for each account, the
budget that may be distributed to shards as leases, and issues leases carrying
(account, epoch, generation, shard).

The budget solve is the part that has to be conservative in a specific way.
Shards spend against R only, so the allocator must reserve, up front, enough
for the add-on term A of any portfolio the budget could reach, plus a drift
term for equity movement inside the epoch. Because A is increasing in gross
notional and the reachable gross notional is increasing in the budget, the
constraint is monotone in B and is solved by bisection.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Lease:
    account: str
    epoch: int
    generation: int
    shard: int
    amount: int


class Allocator:
    def __init__(self, risk, drift_bps=0, residual=0):
        self.risk = risk
        self.drift_bps = drift_bps       # equity drift allowance, basis points
        self.residual = residual         # flat model-error allowance
        self.epoch = 0
        self.generation = {}             # account -> current generation
        self.issued = {}                 # (account, epoch, gen) -> {shard: amount}

    # ---- budget --------------------------------------------------------

    def _reachable_gross(self, gross_now, budget):
        """Upper bound on gross notional after spending `budget` of R."""
        mm = self.risk.min_margin_rate_num()
        if mm is None:
            return gross_now
        r_per_lot, mark_per_lot = mm
        # budget / (r_per_lot / mark_per_lot) = budget * mark_per_lot / r_per_lot
        return gross_now + (budget * mark_per_lot) // r_per_lot

    def headroom(self, gross_now, budget, equity):
        a = self.risk.A_of_gross(self._reachable_gross(gross_now, budget))
        drift = (equity * self.drift_bps + 9999) // 10000
        return a + drift + self.residual

    def solve_budget(self, positions, equity):
        """Largest B with B + headroom(B) <= equity, found by bisection."""
        gross_now = self.risk.gross(positions)
        r_now = self.risk.R(positions)

        def feasible(b):
            return b + self.headroom(gross_now, b, equity) <= equity

        if not feasible(0):
            return 0
        lo, hi = 0, max(1, equity)
        while not feasible(hi):
            hi //= 2
            if hi == 0:
                return 0
        # hi is feasible; grow it until it is not, then bisect
        step = max(1, equity)
        while feasible(hi + step):
            hi += step
        lo = hi
        hi = hi + step
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            if feasible(mid):
                lo = mid
            else:
                hi = mid
        # already-consumed R stays inside the budget
        return max(0, lo - r_now)

    # ---- lease issuance -------------------------------------------------

    def split(self, budget, weights):
        """Split budget across shards. Floor division leaves the remainder
        unissued, which keeps the sum below the budget rather than above it."""
        total = sum(weights.values())
        if total <= 0:
            return {g: 0 for g in weights}
        return {g: (budget * w) // total for g, w in weights.items()}

    def issue(self, account, positions, equity, weights):
        gen = self.generation.get(account, 0) + 1
        self.generation[account] = gen
        budget = self.solve_budget(positions, equity)
        amounts = self.split(budget, weights)
        self.issued[(account, self.epoch, gen)] = dict(amounts)
        return {
            g: Lease(account, self.epoch, gen, g, amt)
            for g, amt in amounts.items()
        }, budget

    def advance_epoch(self):
        self.epoch += 1

    def bump_generation(self, account):
        """Invalidate every outstanding lease for an account."""
        self.generation[account] = self.generation.get(account, 0) + 1
        return self.generation[account]

    def current_generation(self, account):
        return self.generation.get(account, 0)
