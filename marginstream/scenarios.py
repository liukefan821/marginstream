"""A risk model whose scenario set is an explicit table rather than one factor.

`RiskModel` builds its scenarios from a single factor grid, so every symbol
moves in proportion to one number. This subclass takes the scenario set as a
list of per-symbol price-shock numerators, which allows several factors,
loadings of either sign, and idiosyncratic moves. Everything downstream reads
the scenario set through `loss_num`, `leg_num`, `max_move` and
`displaced_marks`, so those four are what is overridden.

A scenario k displaces symbol s by shock[k][s] / DEN per lot. Loss is linear in
positions, as in the base model.
"""
from .risk import RiskModel


class ScenarioRiskModel(RiskModel):
    def __init__(self, symbols, shocks, addon_kappa, addon_scale):
        # shocks: list of dicts {symbol name: numerator over DEN}
        self.shocks = [dict(s) for s in shocks]
        super().__init__(symbols, addon_kappa, addon_scale,
                         grid=tuple(range(len(self.shocks))))

    def loss_num(self, positions, f):
        shock = self.shocks[f]
        num = 0
        for name, qty in positions.items():
            if qty:
                num += -qty * shock.get(name, 0)
        return num

    def leg_num(self, name, qty, f):
        return -qty * self.shocks[f].get(name, 0)

    def max_move(self, name):
        widest = max(abs(s.get(name, 0)) for s in self.shocks)
        return self.ceil_div(widest, self.DEN)

    def displaced_marks(self, f, den=1):
        out = {}
        for name, sym in self.symbols.items():
            out[name] = sym.mark + self.shocks[f].get(name, 0) // (self.DEN * den)
        return out

    def max_scenario_gross(self, positions):
        """Largest gross over the scenarios actually in the set. `gross_reach`
        takes each symbol at its own highest mark, which can exceed this when
        symbols peak in different scenarios."""
        best = 0
        for k in self.grid:
            marks = self.displaced_marks(k)
            g = sum(abs(q) * marks[n] for n, q in positions.items())
            best = max(best, g)
        return best


def two_factor_table(symbols, beta1, beta2, idio, levels=(-3, 0, 3)):
    """Joint moves of two factors at `levels`, plus one up and one down
    idiosyncratic move per symbol. Loadings are in units of 1/BETA_DEN and the
    factor levels in units of 1/FACTOR_DEN of each symbol's scan range, the
    same units the single-factor model uses."""
    shocks = []
    for f1 in levels:
        for f2 in levels:
            shocks.append({s.name: (b1 * f1 + b2 * f2) * s.scan
                           for s, b1, b2 in zip(symbols, beta1, beta2)})
    widest = max(abs(x) for x in levels)
    for s, d in zip(symbols, idio):
        for sign in (1, -1):
            shocks.append({s.name: sign * d * widest * s.scan})
    return shocks
