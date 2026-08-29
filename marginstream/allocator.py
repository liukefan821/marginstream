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


# ---------------------------------------------------------------------------
# Price-conditional leases
# ---------------------------------------------------------------------------

DECAY_DEN = 1000


class ConditionalLease:
    """A lease whose amount is a non-increasing function of the published
    market state index k, rather than a single number.

    A shard evaluates the curve against the market state it already receives on
    the market-data path, so the amount available falls as the market moves
    adversely without any message from the allocator.
    """

    __slots__ = ("account", "epoch", "generation", "shard", "curve")

    def __init__(self, account, epoch, generation, shard, curve):
        self.account = account
        self.epoch = epoch
        self.generation = generation
        self.shard = shard
        self.curve = tuple(curve)          # curve[k] = amount at market state k

    def at(self, k):
        if k < 0:
            k = 0
        if k >= len(self.curve):
            k = len(self.curve) - 1
        return self.curve[k]

    @property
    def amount(self):
        return self.curve[0]


class CurveAllocator(Allocator):
    """Issues ConditionalLease curves.

    The curve shape is fixed in advance (a non-increasing sequence of
    multipliers); the allocator solves for the single scale that makes the
    pointwise safety condition hold at every market state.

    At market state k the account's equity is its collateral less the loss the
    portfolio has already taken at k. Positions admitted during the epoch add
    their own loss at k, and that addition is bounded by the R budget they
    consumed, because R is a maximum over a scenario set that contains k. The
    condition therefore closes on itself with a factor of two:

        2 * sum_g curve_g[k] + A_reachable(k) <= collateral - loss(P, f_k)
    """

    def __init__(self, risk, shape, factor_of_state, drift_bps=0, residual=0):
        super().__init__(risk, drift_bps=drift_bps, residual=residual)
        self.shape = tuple(shape)                  # multipliers over DECAY_DEN
        self.factor_of_state = tuple(factor_of_state)

    def _lhs(self, scale, weights, k, gross_now, collateral, r_now=0):
        total_w = sum(weights.values()) or 1
        per_k = sum(
            (scale * w * self.shape[k]) // (total_w * DECAY_DEN)
            for w in weights.values()
        )
        addon = self.risk.A_of_gross(self._reachable_gross(gross_now, per_k))
        drift = (collateral * self.drift_bps + 9999) // 10000
        # r_now is the requirement the portfolio already carries; the two
        # factors of per_k cover the new positions' own requirement and the
        # loss those same positions take at state k
        return r_now + 2 * per_k + addon + drift + self.residual

    def solve_scale(self, positions, collateral, weights):
        gross_now = self.risk.gross(positions)
        r_now = self.risk.R(positions)

        def feasible(scale):
            for k in range(len(self.shape)):
                rhs = collateral - self.risk.loss(positions, self.factor_of_state[k])
                if self._lhs(scale, weights, k, gross_now, collateral, r_now) > rhs:
                    return False
            return True

        if not feasible(0):
            return 0
        hi, step = 0, max(1, collateral)
        while feasible(hi + step):
            hi += step
        lo, hi = hi, hi + step
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            if feasible(mid):
                lo = mid
            else:
                hi = mid
        return lo

    def issue_curve(self, account, positions, collateral, weights):
        gen = self.generation.get(account, 0) + 1
        self.generation[account] = gen
        scale = self.solve_scale(positions, collateral, weights)
        total_w = sum(weights.values()) or 1
        out = {}
        for g, w in weights.items():
            curve = [
                (scale * w * self.shape[k]) // (total_w * DECAY_DEN)
                for k in range(len(self.shape))
            ]
            out[g] = ConditionalLease(account, self.epoch, gen, g, curve)
        self.issued[(account, self.epoch, gen)] = {
            g: lz.amount for g, lz in out.items()
        }
        return out, scale
