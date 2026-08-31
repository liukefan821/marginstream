"""The ordering point.

Every admitted order passes through here before it reaches a book. Two jobs
that matter to the margin path:

**Gap-free admission sequence per lease.** A gateway numbers its admissions
under a lease from one upward. The sequencer accepts a submission only if it is
the next number for that lease, so the sequence it records is complete: there
is no admission it has not seen.

**Terminal fencing.** A lease can be fenced here. After that, an order carrying
it is refused no matter what the gateway believes about its own clock. This is
what makes a term terminal. Comparing the allocator's clock against a lease
expiry does not prove that a partitioned gateway has stopped: its clock may be
behind. A fence at the ordering point does prove it, because nothing reaches a
book except through this component.

The seal returned by `fence` is what a terminal reconciliation has to carry.
"""


class Seal:
    """Evidence that a lease can produce no further admissions, and how many it
    produced."""

    __slots__ = ("lease_id", "terminal_seq")

    def __init__(self, lease_id, terminal_seq):
        self.lease_id = lease_id
        self.terminal_seq = terminal_seq

    def __repr__(self):
        return f"Seal(lease={self.lease_id}, terminal_seq={self.terminal_seq})"


class Sequencer:
    def __init__(self):
        self.last_seq = {}          # lease_id -> last admission seq accepted
        self.fenced = {}            # lease_id -> Seal
        self.log = {}               # (lease_id, seq) -> payload
        self.fills = {}             # order_id -> filled quantity
        self.cancelled = set()      # order_ids acknowledged as cancelled
        self.rejected = 0
        # the same facts in arrival order, which is what a recovering component
        # replays. `position` is the watermark a snapshot records.
        self.events = []
        self.terms = {}             # order_id -> (symbol, qty, mark, band, cap, policy)
        self.fill_log = {}          # fill_id -> payload, for idempotent retry
        self.filled_qty = {}        # order_id -> quantity filled so far

    def position(self):
        return len(self.events)

    def replay_from(self, watermark):
        """Events after `watermark`, each with its position, so a replaying
        component can tell a repeat from a new fact."""
        return list(enumerate(self.events))[watermark:]

    def submit(self, lease_id, admission_seq, order_id, account, symbol, qty,
               holder=None, mark=None, band=0, fee_cap=0, policy=0):
        """Return (accepted, reason).

        The payload is recorded, so a reconciliation can be computed from this
        log rather than taken on trust from whoever reports it. A retry of an
        entry already accepted, carrying the same payload, succeeds again; the
        same number with a different payload is a conflict.
        """
        key = (lease_id, admission_seq)
        payload = (order_id, account, symbol, qty, holder)
        terms = (symbol, qty, mark, band, fee_cap, policy)
        if key in self.log:
            if self.log[key] == payload:
                return True, "idempotent_retry"
            self.rejected += 1
            return False, "conflicting_payload"
        if lease_id in self.fenced:
            self.rejected += 1
            return False, "lease_fenced"
        expected = self.last_seq.get(lease_id, 0) + 1
        if admission_seq != expected:
            self.rejected += 1
            return False, "sequence_gap"
        self.last_seq[lease_id] = admission_seq
        self.log[key] = payload
        # the parameters a fill under this order will be checked against.
        # keeping them here is what lets the ordering point enforce policy
        # rather than trust whoever reports the fill.
        self.terms[order_id] = terms
        self.events.append(("admit", lease_id, admission_seq, order_id,
                            account, symbol, qty, holder))
        return True, "ok"

    def record_fill(self, fill_id, order_id, qty, price, fee=0):
        """An authoritative fill, checked against the terms the order was
        admitted under.

        Nothing is written and no state moves unless every check passes, so a
        rejected fill leaves the ordering point, the gateway and the account
        exactly as they were. Returns (accepted, reason).
        """
        payload = (order_id, qty, price, fee)
        prior = self.fill_log.get(fill_id)
        if prior is not None:
            if prior == payload:
                return True, "idempotent_retry"
            return False, "conflicting_fill_payload"

        terms = self.terms.get(order_id)
        if terms is None:
            return False, "unknown_order"
        symbol, admitted_qty, mark, band, fee_cap, _policy = terms
        if order_id in self.cancelled:
            return False, "order_cancelled"
        if qty == 0 or (qty > 0) != (admitted_qty > 0):
            return False, "wrong_direction"
        done = self.filled_qty.get(order_id, 0)
        if abs(done + qty) > abs(admitted_qty):
            return False, "overfill"
        if mark is not None and band is not None and abs(price - mark) > band:
            return False, "outside_price_band"
        if fee > fee_cap * abs(qty):
            return False, "fee_above_cap"

        self.fill_log[fill_id] = payload
        self.filled_qty[order_id] = done + qty
        self.fills[order_id] = self.fills.get(order_id, 0) + qty
        self.events.append(("fill", order_id, qty, price, fee, fill_id))
        return True, "ok"

    def record_cancel(self, order_id):
        if order_id in self.cancelled:
            return False, "already_cancelled"
        self.cancelled.add(order_id)
        self.events.append(("cancel", order_id))
        return True, "ok"

    def reconcile(self, lease_id, risk):
        """Replay the log for one lease and return its worst-fill occupancy.

        Filled quantities become positions; whatever an order has left, and has
        not been acknowledged as cancelled, is still able to fill.
        """
        filled = {}
        remaining = []
        for order_id, _acct, symbol, qty, _holder in self.admissions_of(lease_id):
            done = self.fills.get(order_id, 0)
            if done:
                filled[symbol] = filled.get(symbol, 0) + done
            rest = qty - done
            if rest and order_id not in self.cancelled:
                remaining.append((symbol, rest))

        worst = None
        for f in risk.grid:
            v = risk.loss_num(filled, f)
            for symbol, rest in remaining:
                leg = risk.leg_num(symbol, rest, f)
                if leg > 0:
                    v += leg
            if worst is None or v > worst:
                worst = v
        r = 0 if worst is None or worst <= 0 else risk.ceil_div(worst, risk.DEN)

        buy, sell = {}, {}
        for symbol, rest in remaining:
            if rest > 0:
                buy[symbol] = buy.get(symbol, 0) + rest
            else:
                sell[symbol] = sell.get(symbol, 0) - rest
        g = 0
        for symbol in set(filled) | set(buy) | set(sell):
            mark = risk.symbols[symbol].mark
            fq = filled.get(symbol, 0)
            g += mark * max(abs(fq + buy.get(symbol, 0)),
                            abs(fq - sell.get(symbol, 0)))
        return (r, g)

    def rebuild_account(self, risk, collateral, mode="exact"):
        """Fold the log into an account. This is the reference a recovered
        account is compared against."""
        from .account import Account
        acct = Account(risk, collateral, mode=mode)
        symbol_of = {}
        n = 0
        for ev in self.events:
            if ev[0] == "admit":
                symbol_of[ev[3]] = ev[5]
            elif ev[0] == "fill":
                oid, qty, price, fee = ev[1], ev[2], ev[3], ev[4]
                if price is None:
                    continue
                n += 1
                acct.apply_fill(("log", n), symbol_of.get(oid), qty, price, fee)
        return acct

    def admissions_of(self, lease_id):
        """Every payload recorded under a lease, in order."""
        out = []
        n = self.last_seq.get(lease_id, 0)
        for i in range(1, n + 1):
            entry = self.log.get((lease_id, i))
            if entry is not None:
                out.append(entry)
        return out

    def fence(self, lease_id):
        """Stop accepting anything under this lease and return its seal.

        Fencing is idempotent: fencing a lease twice returns the same seal.
        """
        if lease_id not in self.fenced:
            self.fenced[lease_id] = Seal(lease_id,
                                         self.last_seq.get(lease_id, 0))
            self.events.append(("fence", lease_id,
                                self.fenced[lease_id].terminal_seq))
        return self.fenced[lease_id]

    def is_fenced(self, lease_id):
        return lease_id in self.fenced

    def seal_of(self, lease_id):
        return self.fenced.get(lease_id)
