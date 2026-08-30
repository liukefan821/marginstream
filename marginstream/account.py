"""Account state: positions, cash, fees, and equity.

Two ledgers are kept, and only one of them is allowed to decide anything.

**The safety ledger** is an exact integer cash-flow identity with no division in
it. Every fill of signed quantity `dq` at price `p` does

    cash  -= dq * p
    qty   += dq

and the total profit and loss at a set of marks is

    total_pnl(marks) = sum over symbols of ( cash_s + qty_s * mark_s )

That covers opening, adding, partial closes, full closes and a fill that
crosses through zero into the opposite direction, without an average cost and
without a rounding decision anywhere.

**The reporting ledger** is a first-in first-out lot list, which is what a
statement needs in order to separate realised from unrealised. It is required
to satisfy

    realised + unrealised(marks) == cash + qty * mark, per symbol

and that is tested against random fill sequences rather than assumed. Fees are
accumulated on their own and subtracted once.

Equity is

    E0    = collateral + total_pnl(marks) - fees

and the scenario grid is a set of price displacements from the current marks,
so equity under scenario f is

    E(f)  = E0 - loss(positions, f)

Because `loss` rounds a loss up, scenario equity is understated rather than
overstated, which is the direction safety needs.

`mode` exists for the negative control in the experiments: an account that
forgets realised losses, or forgets fees, reports a higher equity than it has.
"""


class Account:
    def __init__(self, risk, collateral, mode="exact"):
        self.risk = risk
        self.collateral = collateral
        self.mode = mode                  # exact | ignores_realised | ignores_fees
        self.qty = {}                     # symbol -> lots held
        self.cash = {}                    # symbol -> signed minor units
        self.fees = 0
        self.realised = {}                # symbol -> realised profit and loss
        self.lots = {}                    # symbol -> list of [qty, price]
        self.applied = set()              # fill keys already folded in

    # ---- fills ------------------------------------------------------------

    def apply_fill(self, key, symbol, dq, price, fee=0):
        """Fold one fill in. `key` makes a repeat of the same fill a no-op, so
        a replayed log produces the same account."""
        if key in self.applied:
            return False, "already_applied"
        if dq == 0:
            return False, "zero_quantity"
        self.applied.add(key)

        # safety ledger
        self.cash[symbol] = self.cash.get(symbol, 0) - dq * price
        q = self.qty.get(symbol, 0) + dq
        if q:
            self.qty[symbol] = q
        else:
            self.qty.pop(symbol, None)
        self.fees += fee

        # reporting ledger
        lots = self.lots.setdefault(symbol, [])
        rest = dq
        realised = 0
        while rest and lots and (lots[0][0] > 0) != (rest > 0):
            lot_qty, lot_price = lots[0]
            take = min(abs(lot_qty), abs(rest))
            if lot_qty > 0:
                realised += take * (price - lot_price)   # a long lot closed
            else:
                realised += take * (lot_price - price)   # a short lot closed
            if abs(lot_qty) == take:
                lots.pop(0)
            else:
                lots[0][0] = lot_qty - (take if lot_qty > 0 else -take)
            rest -= (take if rest > 0 else -take)
        if rest:
            lots.append([rest, price])
        if not lots:
            self.lots.pop(symbol, None)
        if realised:
            self.realised[symbol] = self.realised.get(symbol, 0) + realised
        return True, "applied"

    def charge_fee(self, key, amount):
        if key in self.applied:
            return False, "already_applied"
        self.applied.add(key)
        self.fees += amount
        return True, "charged"

    # ---- valuation --------------------------------------------------------

    def marks(self):
        return {name: sym.mark for name, sym in self.risk.symbols.items()}

    def positions(self):
        return dict(self.qty)

    def total_pnl(self, marks=None):
        marks = marks or self.marks()
        total = 0
        for symbol in set(self.cash) | set(self.qty):
            total += self.cash.get(symbol, 0)
            total += self.qty.get(symbol, 0) * marks.get(symbol, 0)
        return total

    def realised_pnl(self):
        return sum(self.realised.values())

    def unrealised_pnl(self, marks=None):
        marks = marks or self.marks()
        total = 0
        for symbol, lots in self.lots.items():
            mark = marks.get(symbol, 0)
            for lot_qty, lot_price in lots:
                total += lot_qty * (mark - lot_price)
        return total

    def equity(self, marks=None):
        """Mark-to-market equity now.

        The two flawed modes are what the negative experiment uses; they are
        never reachable from the exact path.
        """
        marks = marks or self.marks()
        if self.mode == "ignores_realised":
            return self.collateral + self.unrealised_pnl(marks) - self.fees
        if self.mode == "ignores_fees":
            return self.collateral + self.total_pnl(marks)
        return self.collateral + self.total_pnl(marks) - self.fees

    def equity_at(self, f, marks=None):
        """Equity under scenario f. The grid displaces the marks, so this is
        equity now less the loss the position takes at that displacement."""
        return self.equity(marks) - self.risk.loss(self.positions(), f)

    # ---- persistence ------------------------------------------------------

    def snapshot(self):
        return {
            "collateral": self.collateral,
            "qty": dict(self.qty),
            "cash": dict(self.cash),
            "fees": self.fees,
            "realised": dict(self.realised),
            "lots": {s: [list(l) for l in v] for s, v in self.lots.items()},
            "applied": sorted(self.applied),
        }

    def restore(self, blob):
        self.collateral = blob["collateral"]
        self.qty = dict(blob["qty"])
        self.cash = dict(blob["cash"])
        self.fees = blob["fees"]
        self.realised = dict(blob["realised"])
        self.lots = {s: [list(l) for l in v] for s, v in blob["lots"].items()}
        self.applied = set(tuple(k) if isinstance(k, list) else k
                           for k in blob["applied"])

    def digest(self):
        import hashlib
        canon = repr((sorted(self.qty.items()), sorted(self.cash.items()),
                      self.fees, sorted(self.realised.items()),
                      sorted((s, [tuple(l) for l in v])
                             for s, v in self.lots.items()))).encode()
        return hashlib.sha256(canon).hexdigest()[:16]
