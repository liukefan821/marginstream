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
        # account -> {gateway: (expiry, risk_ceiling, gross_ceiling)}
        # a lease stays live at its gateway until the gateway is handed a
        # replacement or the term runs out, whichever comes first, and the
        # allocator cannot know which happened
        self.outstanding = {}

    # ---- safety condition ------------------------------------------------

    def _ceilings(self, scale, weights, floors, gross_floors=None):
        """Per-gateway ceilings at market state 0, never below the floor that
        gateway's existing admitted set already occupies. Lowering a ceiling
        below current usage does not remove the positions, so the ceiling has
        to accommodate them or the account has to stop taking new risk."""
        gross_floors = gross_floors or {}
        total_w = sum(weights.values()) or 1
        out = {}
        for g, w in weights.items():
            share = (scale * w * self.shape[0]) // (total_w * DECAY_DEN)
            # the ceiling has to cover both resources the gateway already
            # occupies; a gross floor is expressed in risk units through the
            # issuance ratio
            gfloor = gross_floors.get(g, 0)
            need_for_gross = -(-gfloor // self.gross_per_risk) if self.gross_per_risk else 0
            out[g] = max(share, floors.get(g, 0), need_for_gross)
        return out

    def _live_after(self, account, new_ceilings, now):
        """What can be spent once the new leases are out.

        A gateway that receives its replacement spends the new ceiling. A
        gateway that does not receive it keeps spending the old one until the
        term expires. The allocator cannot distinguish the two cases, so it
        budgets for the larger of them at every gateway with an unexpired
        lease.
        """
        live = dict(new_ceilings)
        for g, (expiry, r_ceil, _gr) in self.outstanding.get(account, {}).items():
            if expiry > now:
                live[g] = max(live.get(g, 0), r_ceil)
        return live

    def _feasible(self, account, scale, weights, floors, collateral, now,
                  gross_floors=None):
        ceilings = self._ceilings(scale, weights, floors, gross_floors)
        live = self._live_after(account, ceilings, now)
        risk_total = sum(live.values())
        gross_total = risk_total * self.gross_per_risk
        den = self.risk.A_den()
        lhs = (2 * risk_total + self.residual) * den + self.risk.A_num(gross_total)
        return lhs <= collateral * den

    def solve_scale(self, account, collateral, weights, floors, now,
                    gross_floors=None):
        if not self._feasible(account, 0, weights, floors, collateral, now,
                              gross_floors):
            return None                 # not even the floors fit: reduce-only
        hi, step = 0, max(1, collateral)
        while self._feasible(account, hi + step, weights, floors, collateral,
                             now, gross_floors):
            hi += step
        lo, hi = hi, hi + step
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            if self._feasible(account, mid, weights, floors, collateral, now,
                              gross_floors):
                lo = mid
            else:
                hi = mid
        return lo

    # ---- issuance ---------------------------------------------------------

    def issue(self, account, collateral, weights, floors=None, now=0,
              gross_floors=None):
        """Issue a generation of leases.

        `floors` maps gateway -> the requirement its admitted set already
        occupies. A ceiling is never issued below the floor.

        Returns (leases, scale). `scale` is None when the account cannot be
        given new capacity at all, in which case every ceiling equals the
        floor and the account is effectively reduce-only.
        """
        floors = floors or {}
        gen = self.generation.get(account, 0) + 1
        self.generation[account] = gen

        gross_floors = gross_floors or {}
        scale = self.solve_scale(account, collateral, weights, floors, now,
                                 gross_floors)
        base = self._ceilings(0 if scale is None else scale, weights, floors,
                              gross_floors)

        out = {}
        for g in weights:
            top = base[g]
            denom = self.shape[0] or 1
            risk_curve = tuple((top * self.shape[k]) // denom
                               for k in range(len(self.shape)))
            # a ceiling never drops below the floor, at any state
            risk_curve = tuple(max(v, floors.get(g, 0)) for v in risk_curve)
            gross_curve = tuple(r * self.gross_per_risk for r in risk_curve)
            out[g] = Lease(account, self.epoch, gen, g, now + self.ttl,
                           risk_curve, gross_curve)

        # A gateway that never received an earlier replacement is still
        # holding whichever lease did reach it, so the record kept here is the
        # largest live ceiling rather than the most recent one. Overwriting
        # with the latest loses the older, possibly larger, live lease.
        book = self.outstanding.setdefault(account, {})
        for g, lz in out.items():
            prev = book.get(g)
            if prev and prev[0] > now:
                book[g] = (max(prev[0], lz.expiry),
                           max(prev[1], lz.risk_amount),
                           max(prev[2], lz.gross_amount))
            else:
                book[g] = (lz.expiry, lz.risk_amount, lz.gross_amount)

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
