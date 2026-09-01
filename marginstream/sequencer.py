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
        # a basket is one atomic account transfer, not a set of orders. it
        # occupies the same per-lease sequence space as an admission, so the
        # gap-free check at a barrier covers both without a second rule.
        self.basket_log = {}        # (lease_id, seq) -> payload
        self.basket_by_id = {}      # basket_id -> payload
        self.barriers = {}          # account -> watermark of the last barrier
        # Authority binding. A lease id on its own is a bearer token: knowing
        # one was enough to submit under it, for any account and claiming any
        # holder. The registry is what makes it a capability instead.
        self.lease_registry = {}    # lease_id -> (account, holder, kind)
        self.sessions = {}          # session token -> holder

    # ---- authority binding -----------------------------------------------

    def open_session(self, holder):
        """Stand-in for an authenticated connection.

        In a deployment the holder identity comes from the transport — a client
        certificate, a mutual-TLS peer name — and never from the request body.
        Here a component opens a session once and the ordering point resolves
        the holder from it, ignoring anything the request claims. What this
        models and tests is the *binding check*; the authentication itself is
        assumed and is not implemented.
        """
        token = f"sess:{len(self.sessions) + 1}"
        self.sessions[token] = tuple(holder)
        return token

    def register_lease(self, lease_id, account, holder, kind):
        """Bind a lease to the account, holder and authority kind it was
        issued for. The allocator is the only caller: it is the single issuer,
        so it is the only component that knows the binding.

        `kind` is `ingress` or `liquidation`. They are not interchangeable: an
        ingress lease may carry orders and not basket transfers, and a
        liquidation lease the other way round, because the liquidator's
        admissions are checked against the merged account rather than against a
        ceiling (§2.6).
        """
        prior = self.lease_registry.get(lease_id)
        binding = (account, tuple(holder), kind)
        if prior is not None and prior != binding:
            return False, "lease_id_rebound"
        self.lease_registry[lease_id] = binding
        return True, "registered"

    def _authorise(self, session, lease_id, account, kind):
        """Return None if this submission is authorised, or a refusal reason.

        Deliberately not checked here: the lease's term. The ordering point has
        no clock it can compare against an expiry that was set by another
        component, so an honest gateway is bounded by its own term and a
        Byzantine one is bounded only by the fence. §6.1 says so rather than
        claiming otherwise.
        """
        binding = self.lease_registry.get(lease_id)
        if binding is None:
            return "unknown_lease"
        bound_account, bound_holder, bound_kind = binding
        if bound_kind != kind:
            return "wrong_authority_kind"
        if account != bound_account:
            return "wrong_account"
        holder = self.sessions.get(session)
        if holder is None:
            return "unauthenticated"
        if holder != bound_holder:
            return "wrong_holder"
        return None

    def position(self):
        return len(self.events)

    def replay_from(self, watermark):
        """Events after `watermark`, each with its position, so a replaying
        component can tell a repeat from a new fact."""
        return list(enumerate(self.events))[watermark:]

    def submit(self, session, lease_id, admission_seq, order_id, account,
               symbol, qty, mark=None, band=0, fee_cap=0, policy=0):
        """Return (accepted, reason).

        The payload is recorded, so a reconciliation can be computed from this
        log rather than taken on trust from whoever reports it. A retry of an
        entry already accepted, carrying the same payload, succeeds again; the
        same number with a different payload is a conflict.
        """
        refusal = self._authorise(session, lease_id, account, "ingress")
        if refusal is not None:
            self.rejected += 1
            return False, refusal
        holder = self.sessions[session]

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

    def _occupancy(self, admissions, risk):
        """Worst-fill occupancy of a set of admissions, from the log.

        Filled quantities become positions; whatever an order has left, and has
        not been acknowledged as cancelled, is still able to fill.
        """
        filled = {}
        remaining = []
        for order_id, _acct, symbol, qty, _holder in admissions:
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
            mark = risk.mark_plus(symbol)
            fq = filled.get(symbol, 0)
            g += mark * max(abs(fq + buy.get(symbol, 0)),
                            abs(fq - sell.get(symbol, 0)))
        return (r, g)

    # ---- atomic baskets --------------------------------------------------

    def commit_basket(self, session, lease_id, seq, basket_id, account, legs,
                      terms):
        """Commit a whole basket as one event, or nothing.

        Ordinary matching in this design is sharded by symbol, so a basket
        spanning several symbols cannot fill atomically across several books.
        The liquidation path therefore does not go through matching at all: it
        is an internal transfer priced against the venue's own marks and
        written here as a single log record. Either the record is there and the
        whole basket happened, or it is not and none of it did. There is no
        state in which half a basket exists.

        `legs` is a tuple of `(symbol, qty, price, fee)`. `terms` carries the
        `(mark, band, fee_cap)` each leg is checked against, the same way
        `submit` carries them for an order. A retry under the same basket id
        with the same payload succeeds again and folds once; the same id with a
        different payload is a conflict.
        """
        refusal = self._authorise(session, lease_id, account, "liquidation")
        if refusal is not None:
            self.rejected += 1
            return False, refusal
        holder = self.sessions[session]

        payload = (account, tuple(legs), holder)
        prior = self.basket_by_id.get(basket_id)
        if prior is not None:
            if prior == payload:
                return True, "idempotent_retry"
            self.rejected += 1
            return False, "conflicting_basket_payload"
        if lease_id in self.fenced:
            self.rejected += 1
            return False, "lease_fenced"
        expected = self.last_seq.get(lease_id, 0) + 1
        if seq != expected:
            self.rejected += 1
            return False, "sequence_gap"
        for (symbol, qty, price, fee), (mark, band, cap) in zip(legs, terms):
            if qty == 0:
                return False, "zero_leg"
            if mark is not None and abs(price - mark) > band:
                return False, "outside_price_band"
            if fee > cap * abs(qty):
                return False, "fee_above_cap"

        self.last_seq[lease_id] = seq
        self.basket_log[(lease_id, seq)] = payload
        self.basket_by_id[basket_id] = payload
        self.events.append(("basket", lease_id, seq, basket_id, account,
                            holder, tuple(legs)))
        return True, "ok"

    # ---- barrier ---------------------------------------------------------

    def barrier(self, account, lease_ids):
        """Establish that no admission authority for this account survives.

        Returns `(ok, B, reason)`. `B` is the log position the settlement is
        computed at. Three things are checked, and all three are checked here
        rather than taken from a caller:

        - every lease the account was ever given is fenced. A lease that has
          admitted nothing is still authority and still has to be fenced.
        - the recorded sequence under each of those leases is gap-free, so the
          log holds every admission and every basket that lease produced.
        - no admission or basket for this account was recorded under a lease
          outside that set, which would be authority nobody is accounting for.

        `submit` and `commit_basket` already refuse anything but the next
        number, so the gap-free check should never fire. It is here because
        the settlement's whole claim is that the log is complete, and an
        assertion that never fires is cheap next to a claim that is assumed.
        """
        known = set(lease_ids)
        unfenced = sorted(l for l in known if l not in self.fenced)
        if unfenced:
            return False, None, f"authority_still_live:{unfenced}"

        for lease in sorted(known):
            last = self.last_seq.get(lease, 0)
            for i in range(1, last + 1):
                if ((lease, i) not in self.log
                        and (lease, i) not in self.basket_log):
                    return False, None, f"sequence_gap:lease={lease},seq={i}"

        for ev in self.events:
            if ev[0] == "admit" and ev[4] == account and ev[1] not in known:
                return False, None, f"unknown_authority:lease={ev[1]}"
            if ev[0] == "basket" and ev[4] == account and ev[1] not in known:
                return False, None, f"unknown_authority:lease={ev[1]}"

        b = len(self.events)
        self.events.append(("barrier", account, b))
        self.barriers[account] = b
        return True, b, "ok"

    # ---- reconciliation --------------------------------------------------

    def reconcile(self, lease_id, risk):
        """One lease's occupancy. This is what a seal releases."""
        return self._occupancy(self.admissions_of(lease_id), risk)

    def reconcile_account(self, account, risk, barrier=None):
        """The account's whole occupancy at a barrier, from the log alone.

        Returns `(worst_fill_risk, gross_reach, debit)`.

        - filled positions are admissions that filled, plus every leg of every
          committed basket;
        - an order counts as live unless the ordering point recorded a cancel
          for it. A cancel that was acknowledged here and whose notification
          was lost elsewhere is in the log and releases the order. A cancel
          that the matching side never confirmed is not in the log and the
          order keeps its reservation;
        - `debit` is the execution cost still ahead of the account: the price
          band plus the fee cap on whatever is still able to fill. Cost already
          executed is not included, because it is already inside the equity any
          new lease will be solved against.

        No gateway has to be reachable for this. That is the point: the holders
        whose exposure is being compacted are exactly the ones that may be gone.
        """
        end = len(self.events) if barrier is None else barrier
        owner, done, cancelled = {}, {}, set()
        filled = {}
        for ev in self.events[:end]:
            kind = ev[0]
            if kind == "admit":
                _k, _lease, _seq, oid, acct, sym, qty, _holder = ev
                if acct == account:
                    owner[oid] = (sym, qty)
            elif kind == "fill":
                oid, qty = ev[1], ev[2]
                if oid in owner:
                    done[oid] = done.get(oid, 0) + qty
            elif kind == "cancel":
                if ev[1] in owner:
                    cancelled.add(ev[1])
            elif kind == "basket":
                _k, _lease, _seq, _bid, acct, _holder, legs = ev
                if acct == account:
                    for sym, qty, _price, _fee in legs:
                        filled[sym] = filled.get(sym, 0) + qty

        remaining = []
        for oid, (sym, qty) in owner.items():
            d = done.get(oid, 0)
            if d:
                filled[sym] = filled.get(sym, 0) + d
            rest = qty - d
            if rest and oid not in cancelled:
                remaining.append((sym, rest))

        worst = None
        for f in risk.grid:
            v = risk.loss_num(filled, f)
            for sym, rest in remaining:
                leg = risk.leg_num(sym, rest, f)
                if leg > 0:
                    v += leg
            if worst is None or v > worst:
                worst = v
        r = 0 if worst is None or worst <= 0 else risk.ceil_div(worst, risk.DEN)

        buy, sell = {}, {}
        for sym, rest in remaining:
            if rest > 0:
                buy[sym] = buy.get(sym, 0) + rest
            else:
                sell[sym] = sell.get(sym, 0) - rest
        g = 0
        for sym in set(filled) | set(buy) | set(sell):
            mark = risk.mark_plus(sym)
            fq = filled.get(sym, 0)
            g += mark * max(abs(fq + buy.get(sym, 0)),
                            abs(fq - sell.get(sym, 0)))

        debit = sum(abs(rest) * risk.debit_per_lot(sym)
                    for sym, rest in remaining)
        return (r, g, debit)

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
            elif ev[0] == "basket":
                bid, legs = ev[3], ev[6]
                for i, (sym, qty, price, fee) in enumerate(legs):
                    acct.apply_fill(("basket", bid, i), sym, qty, price, fee)
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
