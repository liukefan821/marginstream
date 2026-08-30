"""Margin allocator.

Runs off the order path. Once per epoch it solves, per account, for the largest
lease envelope that keeps the account's requirement inside its collateral at
every market state, and issues that envelope to the ingress gateways.

The condition, with the absolute-envelope admission rule of `gateway.py`:

    R(P')      <= sum_g risk_g          (Lemma 1 plus the admission rule)
    loss(P',k) <= R(P')                 (R is a max over a set containing k)
    A(P')      <= A(sum_g gross_g)      (A is increasing in gross)

and the requirement to hold is `M(P') <= Collateral - loss(P', k)`, so

    2 * sum_g risk_g + A(sum_g gross_g) <= Collateral

The factor of two is a closure, not a margin: positions admitted during the
epoch contribute both their own requirement and the loss they take at the
realised state, and the second is bounded by the first.

The condition has no k in it. That is deliberate and it is the honest
statement: a schedule that shrinks with the market does not make the mechanism
safe, because a lease cannot reduce a position it has already admitted. What a
shrinking schedule buys is a local trigger and capacity in calm states; safety
comes from the envelope above holding at issuance.

Everything is exact integer arithmetic. The add-on is compared through its
numerator and denominator rather than a rounded value, because rounded add-on
values are not super-additive at small arguments; see
tests/test_counterexamples.py, c3.
"""

from dataclasses import dataclass

DECAY_DEN = 1000


@dataclass(frozen=True)
class Lease:
    account: str
    epoch: int
    generation: int
    gateway: int
    expiry: int                      # logical time at which this lease dies
    risk_curve: tuple                # risk envelope per market state
    gross_curve: tuple               # gross envelope per market state

    def _at(self, curve, k):
        if k < 0:
            k = 0
        if k >= len(curve):
            k = len(curve) - 1
        return curve[k]

    def risk_at(self, k):
        return self._at(self.risk_curve, k)

    def gross_at(self, k):
        return self._at(self.gross_curve, k)

    @property
    def risk_amount(self):
        return self.risk_curve[0]

    @property
    def gross_amount(self):
        return self.gross_curve[0]


class Allocator:
    def __init__(self, risk, shape=(DECAY_DEN,), ttl=1, gross_per_risk=20,
                 residual=0):
        """shape            non-increasing multipliers over market states
        ttl              logical lifetime of an issued lease
        gross_per_risk   how much gross envelope is issued per unit of risk
                         envelope; the lever that decides how much hedging
                         headroom an account gets
        """
        self.risk = risk
        self.shape = tuple(shape)
        self.ttl = ttl
        self.gross_per_risk = gross_per_risk
        self.residual = residual
        self.epoch = 0
        self.generation = {}
        self.issued = {}

    # ---- safety condition ------------------------------------------------

    def _feasible(self, scale, weights, collateral):
        """2 * sum_g risk_g + A(sum_g gross_g) <= collateral, exactly."""
        total_w = sum(weights.values()) or 1
        risk_total = sum(
            (scale * w * self.shape[0]) // (total_w * DECAY_DEN)
            for w in weights.values()
        )
        gross_total = risk_total * self.gross_per_risk
        den = self.risk.A_den()
        lhs = (2 * risk_total + self.residual) * den + self.risk.A_num(gross_total)
        return lhs <= collateral * den

    def solve_scale(self, collateral, weights):
        if not self._feasible(0, weights, collateral):
            return 0
        hi, step = 0, max(1, collateral)
        while self._feasible(hi + step, weights, collateral):
            hi += step
        lo, hi = hi, hi + step
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            if self._feasible(mid, weights, collateral):
                lo = mid
            else:
                hi = mid
        return lo

    # ---- issuance ---------------------------------------------------------

    def issue(self, account, collateral, weights, now=0):
        gen = self.generation.get(account, 0) + 1
        self.generation[account] = gen
        scale = self.solve_scale(collateral, weights)
        total_w = sum(weights.values()) or 1

        out = {}
        for g, w in weights.items():
            risk_curve = tuple(
                (scale * w * self.shape[k]) // (total_w * DECAY_DEN)
                for k in range(len(self.shape))
            )
            gross_curve = tuple(r * self.gross_per_risk for r in risk_curve)
            out[g] = Lease(account, self.epoch, gen, g, now + self.ttl,
                           risk_curve, gross_curve)

        self.issued[(account, self.epoch, gen)] = {
            g: lz.risk_amount for g, lz in out.items()
        }
        return out, scale

    def advance_epoch(self):
        self.epoch += 1

    def bump_generation(self, account):
        self.generation[account] = self.generation.get(account, 0) + 1
        return self.generation[account]

    def current_generation(self, account):
        return self.generation.get(account, 0)
