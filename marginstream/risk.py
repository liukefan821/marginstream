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
    mark: int              # price per lot, minor units
    scan: int              # adverse move per lot covered by the scenario grid
    beta: int              # factor loading, in units of 1/BETA_DEN
    band: int = 0          # furthest a fill may land from the mark, per lot
    fee_per_lot: int = 0   # most that may be charged per lot


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

    # ---- marks -----------------------------------------------------------

    def max_move(self, name):
        """The furthest this symbol's mark can travel inside the scenario set.

        `dp(f) = beta * f * scan / DEN`, so the largest displacement over the
        grid is at the widest factor value. Rounded up.
        """
        sym = self.symbols[name]
        widest = max(abs(f) for f in self.grid)
        return self.ceil_div(abs(sym.beta) * widest * sym.scan, self.DEN)

    def mark_plus(self, name):
        """The highest mark this symbol reaches at any scenario in the grid.

        This is what an *envelope* is measured at. A lease is solved once and
        then admits orders for a term over which the marks move, so the gross
        notional it has to reserve for is the largest the position can reach
        inside the grid, not the figure standing when the solve ran. Measuring
        the reserve at the current mark is what tests/test_repricing.py m1
        breaks.

        The *requirement* is a different object and is not measured here: it is
        what the account owes at the marks in force, so it uses `gross`. The
        two meet in the safety argument, where the requirement after a move is
        bounded by the reserve taken before it.

        This is the per-symbol maximum, and it is an upper bound on the
        per-scenario maximum `max_f sum_s |q_s| (mark_s + dp_s(f))` in every
        case. How loose it is depends on the factor model and is not a general
        property:

        - With one factor and non-negative loadings, which is the model
          configured everywhere in this repository, every symbol reaches its
          highest mark at the same f, so the two coincide up to the rounding:
          `max_move` rounds up while the displacement rounds down, leaving at
          most one minor unit per lot. m4a measures that.
        - With signed loadings, or with several factors, different symbols
          reach their highest mark at different scenarios and the per-symbol
          sum can exceed any single scenario's gross by an arbitrary margin.
          m4b constructs that case and measures it, so the limitation is
          established rather than noted.

        The bound is still safe in both cases. It is only the tightness claim
        that is scoped to the single-factor model.
        """
        return self.symbols[name].mark + self.max_move(name)

    def reprice(self, marks):
        """Move the marks. Anything holding a cached gross figure has to
        recompute it; `Gateway.reprice` is that path."""
        import dataclasses
        for name, mark in marks.items():
            self.symbols[name] = dataclasses.replace(self.symbols[name],
                                                     mark=mark)

    def displaced_marks(self, f, den=1):
        """The marks at factor value `f / den`. `den` lets a caller ask for a
        move part-way to a grid point, or past the widest one."""
        out = {}
        for name, sym in self.symbols.items():
            dp = (sym.beta * f * sym.scan) // (self.DEN * den)
            out[name] = sym.mark + dp
        return out

    # ---- gross -----------------------------------------------------------

    def gross(self, positions):
        """Gross notional at the marks in force. This is the argument the
        add-on takes when the requirement is computed."""
        return sum(abs(q) * self.symbols[n].mark for n, q in positions.items())

    def gross_reach(self, positions):
        """Gross notional at the highest marks the grid allows. This is the
        argument the add-on takes when capacity is reserved."""
        return sum(abs(q) * self.mark_plus(n) for n, q in positions.items())

    def gross_at_marks(self, positions, marks):
        """Gross at a given set of marks."""
        return sum(abs(q) * marks[n] for n, q in positions.items())

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

    def debit_per_lot(self, name):
        """The most one lot of this symbol can cost beyond the mark: the price
        band it may fill inside, plus the fee cap."""
        sym = self.symbols[name]
        return sym.band + sym.fee_per_lot

    def max_debit_ratio(self):
        """(num, den) bounding execution cost per unit of scenario risk, taken
        over the symbols. Used to size the third envelope from the first."""
        best_num, best_den = 0, 1
        for name in self.order:
            r = self.R({name: 1})
            d = self.debit_per_lot(name)
            if r <= 0:
                if d > 0:
                    return None            # cost with no risk cannot be sized
                continue
            if d * best_den > best_num * r:
                best_num, best_den = d, r
        return best_num, best_den

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
