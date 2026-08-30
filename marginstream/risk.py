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
    def __init__(self, symbols, addon_kappa, addon_scale, grid=FACTOR_GRID):
        # symbols: iterable of Symbol. addon_kappa / addon_scale parameterise A.
        # grid: the scenario set; its width is what the admission-path cost
        # scales with.
        self.grid = tuple(grid)
        self.symbols = {s.name: s for s in symbols}
        self.order = tuple(sorted(self.symbols))       # fixed iteration order
        self.addon_kappa = addon_kappa
        self.addon_scale = addon_scale

    # ---- scenario term -------------------------------------------------

    DEN = FACTOR_DEN * BETA_DEN

    def loss_num(self, positions, f):
        """Exact numerator of the loss under the scenario with factor value f.

        Linear in positions, which is what lets a worst-case fill subset be
        computed per scenario rather than by enumeration.
        """
        num = 0
        for name, qty in positions.items():
            if qty == 0:
                continue
            sym = self.symbols[name]
            num += -qty * sym.beta * f * sym.scan
        return num

    def leg_num(self, name, qty, f):
        """Numerator contributed by one signed quantity of one symbol."""
        sym = self.symbols[name]
        return -qty * sym.beta * f * sym.scan

    @staticmethod
    def ceil_div(num, den):
        return -((-num) // den)

    def loss(self, positions, f):
        return self.ceil_div(self.loss_num(positions, f), self.DEN)

    def R(self, positions):
        """Worst loss over the grid, floored at zero.

        Taken on numerators and divided once. Ceiling division is monotone, so
        max_f ceil(n_f/d) equals ceil(max_f n_f / d) and the two forms agree.
        """
        worst_num = None
        for f in self.grid:
            n = self.loss_num(positions, f)
            if worst_num is None or n > worst_num:
                worst_num = n
        if worst_num is None or worst_num <= 0:
            return 0
        return self.ceil_div(worst_num, self.DEN)

    # ---- add-on term ---------------------------------------------------

    def gross(self, positions):
        return sum(abs(q) * self.symbols[n].mark for n, q in positions.items())

    def A_num(self, gross):
        """Add-on numerator, exact. Convex, zero at zero, and super-additive
        without qualification because no division happens here."""
        return self.addon_kappa * gross * gross

    def A_den(self):
        return self.addon_scale

    def A_of_gross(self, gross):
        """Add-on in the same units as R. Rounds up, and is only ever computed
        once, centrally, on the account's total gross. Rounded values are never
        summed, because summing them is what breaks super-additivity."""
        return (self.A_num(gross) + self.addon_scale - 1) // self.addon_scale

    def A(self, positions):
        return self.A_of_gross(self.gross(positions))

    # ---- full requirement ----------------------------------------------

    def M(self, positions):
        return self.R(positions) + self.A(positions)

    # ---- helpers used by shards ----------------------------------------

    def R_after(self, positions, name, qty):
        """R of the position set that results from adding qty lots of `name`.

        The admission rule compares this absolute value against a lease. An
        earlier version compared the increment instead, which does not bound
        the account's requirement: a position flipped from short to long leaves
        the local requirement unchanged while the account's requirement moves
        to its maximum. See tests/test_counterexamples.py, c1."""
        after = dict(positions)
        after[name] = after.get(name, 0) + qty
        return self.R(after)

    def gross_after(self, positions, name, qty):
        after = dict(positions)
        after[name] = after.get(name, 0) + qty
        return self.gross(after)

    def marginal_R(self, positions, name, qty):
        """Retained for reporting. Not used by the admission rule."""
        return self.R_after(positions, name, qty) - self.R(positions)

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
