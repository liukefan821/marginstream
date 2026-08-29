"""Margin requirement model.

The requirement for a portfolio is M(P) = R(P) + A(P).

R is a scenario term: the worst loss over a fixed scenario set, where the loss
under any single scenario is linear in positions.

A is an add-on term: a convex function of gross notional with A(0) = 0.

All quantities are integers in minor units. Divisions round away from zero on
the conservative side so that a requirement is never understated by rounding.
"""

from dataclasses import dataclass

# Factor grid used to build the scenario set. Values are numerators over
# FACTOR_DEN, so a scenario shocks each symbol by (f / FACTOR_DEN) of its
# scan range, scaled by the symbol's factor loading.
FACTOR_GRID = (-3, -2, -1, 0, 1, 2, 3)
FACTOR_DEN = 3
BETA_DEN = 100


@dataclass(frozen=True)
class Symbol:
    name: str
    shard: int
    mark: int        # price per lot, minor units
    scan: int        # adverse move per lot covered by the scenario grid
    beta: int        # factor loading, in units of 1/BETA_DEN


class RiskModel:
    def __init__(self, symbols, addon_kappa, addon_scale):
        # symbols: iterable of Symbol. addon_kappa / addon_scale parameterise A.
        self.symbols = {s.name: s for s in symbols}
        self.order = tuple(sorted(self.symbols))       # fixed iteration order
        self.addon_kappa = addon_kappa
        self.addon_scale = addon_scale

    # ---- scenario term -------------------------------------------------

    def loss(self, positions, f):
        """Loss of `positions` under the scenario with factor value f.

        positions maps symbol name -> signed lots. Long positions lose when the
        factor is negative. The sum is exact; the division to undo the factor
        and beta denominators is applied once, rounding up.
        """
        num = 0
        for name, qty in positions.items():
            if qty == 0:
                continue
            s = self.symbols[name]
            num += -qty * s.beta * f * s.scan
        den = FACTOR_DEN * BETA_DEN
        # ceiling division, valid for negative numerators too
        return -((-num) // den)

    def R(self, positions):
        worst = 0
        for f in FACTOR_GRID:
            v = self.loss(positions, f)
            if v > worst:
                worst = v
        return worst

    # ---- add-on term ---------------------------------------------------

    def gross(self, positions):
        return sum(abs(q) * self.symbols[n].mark for n, q in positions.items())

    def A_of_gross(self, gross):
        # quadratic in gross notional: convex, zero at zero
        return (self.addon_kappa * gross * gross + self.addon_scale - 1) // self.addon_scale

    def A(self, positions):
        return self.A_of_gross(self.gross(positions))

    # ---- full requirement ----------------------------------------------

    def M(self, positions):
        return self.R(positions) + self.A(positions)

    # ---- helpers used by shards ----------------------------------------

    def marginal_R(self, positions, name, qty):
        """Increase in R caused by adding qty lots of `name` to `positions`."""
        before = self.R(positions)
        after = dict(positions)
        after[name] = after.get(name, 0) + qty
        return self.R(after) - before

    def min_margin_rate_num(self):
        """Numerator of the smallest R-per-notional ratio across symbols.

        Returned as (num, den) so callers can bound how much additional gross
        notional a given amount of R budget can buy.
        """
        best = None
        for name in self.order:
            s = self.symbols[name]
            # R of one long lot, over its notional
            r = self.R({name: 1})
            if r <= 0:
                continue
            if best is None or r * best[1] < best[0] * s.mark:
                best = (r, s.mark)
        return best
