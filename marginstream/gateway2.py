"""Ingress admission gateway.

What a gateway holds for an account is not a position. It is a set of orders it
admitted, some of which have filled and some of which are still live on a book.
Netting the two together understates the risk: two resting orders of opposite
sign net to nothing, and if only one of them fills the account carries the other
side.

The envelope is therefore taken over the worst subset of fills that could still
occur. Because the loss under a fixed scenario is linear in positions, that
worst subset does not have to be enumerated:

    E_k   = loss_k(filled) + sum_i max(0, loss_k(order_i))
    R_wf  = max(0, ceil(max_k E_k / DEN))

and gross notional separates by symbol, so

    G_wf  = sum_s mark_s * max(|filled_s + buy_s|, |filled_s - sell_s|)

where buy_s and sell_s are the remaining unfilled quantities on each side.
Both are checked; checking only the first reopens the case where an order
reduces the requirement while raising gross notional.

The four running totals below are updated per order state change rather than
recomputed, so admission costs one pass over the scenario grid regardless of
how many orders are live.

State transitions are conservative in one direction: a cancel *request*
releases nothing. Only a cancel acknowledgement that has come back through the
ordering point removes an order's reservation, because until then the order can
still fill.
"""



class _AccountState:
    __slots__ = ("filled", "orders", "filled_num", "pos_part_num",
                 "buy_rem", "sell_rem", "gross_wf", "debit_reserved",
                 "debit_incurred_total", "debit_baseline")

    def __init__(self, n_scen):
        self.filled = {}                 # symbol -> filled lots
        self.orders = {}                 # order_id -> (symbol, remaining, vec)
        self.filled_num = [0] * n_scen   # loss numerator of filled, per scenario
        self.pos_part_num = [0] * n_scen # sum of positive order parts
        self.buy_rem = {}                # symbol -> unfilled buy lots
        self.sell_rem = {}               # symbol -> unfilled sell lots (positive)
        self.gross_wf = 0                # running worst-fill gross
        # execution cost still ahead of this account at this gateway
        self.debit_reserved = 0
        # bound on cost already executed, monotone
        self.debit_incurred_total = 0
        # what had been executed when the current lease was solved. anything at
        # or before this is already inside the equity the lease was issued
        # against and must not be reserved for twice.
        self.debit_baseline = 0


class Gateway:
    def __init__(self, gateway_id, risk, fencing=True, ratchet=False,
                 incarnation=0, sequencer=None, worst_fill=True,
                 incremental=True):
        self.id = gateway_id
        self.incarnation = incarnation
        self.risk = risk
        self.fencing = fencing
        self.ratchet = ratchet
        self.sequencer = sequencer
        # when false the gateway nets orders into a position, which is the
        # behaviour the negative experiment uses
        self.worst_fill = worst_fill
        # when false the same envelopes are recomputed from the whole order set
        # on every call. the answer is identical; only the cost differs, which
        # is what makes it a fair performance baseline.
        self.incremental = incremental
        self.grid = risk.grid
        self.n_scen = len(risk.grid)
        self.state = {}
        self.lease = {}
        self.seen_generation = {}
        self.worst_state = {}
        self.admission_seq = {}
        # a recovering gateway admits nothing until its state has been rebuilt
        self.recovering = False
        # highest log position folded in, so a repeated slice is absorbed
        self.log_high_water = 0

    # ---- state helpers ---------------------------------------------------

    def _st(self, account):
        st = self.state.get(account)
        if st is None:
            st = _AccountState(self.n_scen)
            self.state[account] = st
        return st

    @staticmethod
    def _bump(tally, sym, delta):
        """Adjust a per-symbol tally and drop it when it reaches zero, so two
        equal states have equal structure."""
        v = tally.get(sym, 0) + delta
        if v:
            tally[sym] = v
        else:
            tally.pop(sym, None)

    def _symbol_gross(self, st, sym):
        mark = self.risk.symbols[sym].mark
        f = st.filled.get(sym, 0)
        b = st.buy_rem.get(sym, 0)
        s = st.sell_rem.get(sym, 0)
        return mark * max(abs(f + b), abs(f - s))

    def _order_vector(self, symbol, qty):
        return [self.risk.leg_num(symbol, qty, f) for f in self.grid]

    def _risk_wf_fullscan(self, st, extra_sym=None, extra_qty=0):
        worst = None
        for f in self.grid:
            v = self.risk.loss_num(st.filled, f)
            for _oid, (sym, rem, _v) in st.orders.items():
                leg = self.risk.leg_num(sym, rem, f)
                if leg > 0:
                    v += leg
            if extra_sym is not None:
                leg = self.risk.leg_num(extra_sym, extra_qty, f)
                if leg > 0:
                    v += leg
            if worst is None or v > worst:
                worst = v
        if worst is None or worst <= 0:
            return 0
        return self.risk.ceil_div(worst, self.risk.DEN)

    def _gross_wf_fullscan(self, st, extra_sym=None, extra_qty=0):
        buy, sell = {}, {}
        for _oid, (sym, rem, _v) in st.orders.items():
            if rem > 0:
                buy[sym] = buy.get(sym, 0) + rem
            else:
                sell[sym] = sell.get(sym, 0) - rem
        if extra_sym is not None:
            if extra_qty > 0:
                buy[extra_sym] = buy.get(extra_sym, 0) + extra_qty
            else:
                sell[extra_sym] = sell.get(extra_sym, 0) - extra_qty
        total = 0
        for sym in set(st.filled) | set(buy) | set(sell):
            mark = self.risk.symbols[sym].mark
            f = st.filled.get(sym, 0)
            total += mark * max(abs(f + buy.get(sym, 0)),
                                abs(f - sell.get(sym, 0)))
        return total

    def _risk_wf(self, st, extra_vec=None):
        worst = None
        for k in range(self.n_scen):
            v = st.filled_num[k] + st.pos_part_num[k]
            if extra_vec is not None and extra_vec[k] > 0:
                v += extra_vec[k]
            if worst is None or v > worst:
                worst = v
        if worst is None or worst <= 0:
            return 0
        return self.risk.ceil_div(worst, self.risk.DEN)

    # ---- lease -----------------------------------------------------------

    def install_lease(self, lease):
        if (getattr(lease, "gateway", self.id) != self.id
                or getattr(lease, "incarnation", self.incarnation)
                != self.incarnation):
            raise ValueError(
                f"lease for ({lease.gateway},{lease.incarnation}) installed at "
                f"({self.id},{self.incarnation})")
        self.lease[lease.account] = lease
        # the equity this lease was solved against already reflects every fill
        # folded in so far, so those costs are not reserved for again
        st = self._st(lease.account)
        st.debit_baseline = st.debit_incurred_total
        self.admission_seq.setdefault(getattr(lease, "lease_id", None), 0)
        prev = self.seen_generation.get(lease.account, 0)
        self.seen_generation[lease.account] = max(prev, lease.generation)
        self.worst_state[lease.account] = 0

    # ---- admission -------------------------------------------------------

    def admit(self, account, symbol, qty, generation, market_state=0, now=0,
              order_id=None):
        if self.recovering:
            return False, "recovering"
        lease = self.lease.get(account)
        if lease is None:
            return False, "no_lease"
        if now >= lease.expiry:
            return False, "lease_expired"
        if getattr(lease, "mode", "normal") == "quarantine":
            return False, "quarantine"

        if self.fencing:
            seen = max(self.seen_generation.get(account, 0), generation)
            self.seen_generation[account] = seen
            if lease.generation < seen:
                return False, "gateway_stale"
            if lease.generation != generation:
                return False, "stale_generation"

        if self.ratchet:
            w = max(self.worst_state.get(account, 0), market_state)
            self.worst_state[account] = w
            state = w
        else:
            state = market_state

        st = self._st(account)
        vec = self._order_vector(symbol, qty)

        if self.worst_fill and not self.incremental:
            risk_after = self._risk_wf_fullscan(st, symbol, qty)
            gross_after = self._gross_wf_fullscan(st, symbol, qty)
        elif self.worst_fill:
            risk_after = self._risk_wf(st, vec)
            before_sym = self._symbol_gross(st, symbol)
            self._bump(st.buy_rem if qty > 0 else st.sell_rem, symbol,
                       qty if qty > 0 else -qty)
            gross_after = st.gross_wf - before_sym + self._symbol_gross(st, symbol)
            # undo the tentative update; it is redone below only if admitted
            self._bump(st.buy_rem if qty > 0 else st.sell_rem, symbol,
                       -qty if qty > 0 else qty)
        else:
            net = dict(st.filled)
            for _oid, (s2, r2, _v) in st.orders.items():
                net[s2] = net.get(s2, 0) + r2
            net[symbol] = net.get(symbol, 0) + qty
            risk_after = self.risk.R(net)
            gross_after = self.risk.gross(net)

        debit_after = (st.debit_reserved
                       + (st.debit_incurred_total - st.debit_baseline)
                       + abs(qty) * self.risk.debit_per_lot(symbol))

        if risk_after > lease.risk_at(state):
            return False, "risk_envelope"
        if gross_after > lease.gross_at(state):
            return False, "gross_envelope"
        if debit_after > lease.debit_at(state):
            return False, "debit_envelope"

        lid = getattr(lease, "lease_id", None)
        if self.sequencer is not None and lid is not None:
            nxt = self.admission_seq.get(lid, 0) + 1
            oid = order_id if order_id is not None else f"{lid}:{nxt}"
            sym = self.risk.symbols[symbol]
            ok, why = self.sequencer.submit(
                lid, nxt, oid, account, symbol, qty,
                holder=(self.id, self.incarnation), mark=sym.mark,
                band=sym.band, fee_cap=sym.fee_per_lot)
            if not ok:
                return False, why
            self.admission_seq[lid] = nxt
            order_id = oid
        elif order_id is None:
            order_id = f"{self.id}:{self.incarnation}:{len(st.orders)}"

        # commit
        st.orders[order_id] = (symbol, qty, vec)
        for k in range(self.n_scen):
            if vec[k] > 0:
                st.pos_part_num[k] += vec[k]
        before_sym = self._symbol_gross(st, symbol)
        self._bump(st.buy_rem if qty > 0 else st.sell_rem, symbol,
                   qty if qty > 0 else -qty)
        st.gross_wf += self._symbol_gross(st, symbol) - before_sym
        st.debit_reserved += abs(qty) * self.risk.debit_per_lot(symbol)
        return True, "ok"

    # ---- order lifecycle -------------------------------------------------

    def fill(self, account, order_id, qty):
        """Move `qty` lots of a live order into the filled position. Signed the
        same way as the order."""
        st = self._st(account)
        rec = st.orders.get(order_id)
        if rec is None:
            return False, "unknown_order"
        symbol, remaining, _vec = rec
        if qty == 0 or (qty > 0) != (remaining > 0) or abs(qty) > abs(remaining):
            return False, "bad_fill"

        before_sym = self._symbol_gross(st, symbol)
        part = self._order_vector(symbol, qty)
        rest = remaining - qty

        # the filled part leaves the reservation and enters the position
        old_vec = self._order_vector(symbol, remaining)
        new_vec = self._order_vector(symbol, rest)
        for k in range(self.n_scen):
            if old_vec[k] > 0:
                st.pos_part_num[k] -= old_vec[k]
            if new_vec[k] > 0:
                st.pos_part_num[k] += new_vec[k]
            st.filled_num[k] += part[k]

        # the reservation becomes an executed cost; it stays counted until a
        # lease is issued against an equity that already reflects it
        d = abs(qty) * self.risk.debit_per_lot(symbol)
        st.debit_reserved -= d
        st.debit_incurred_total += d
        self._bump(st.filled, symbol, qty)
        self._bump(st.buy_rem if remaining > 0 else st.sell_rem, symbol,
                   -qty if remaining > 0 else qty)

        if rest == 0:
            del st.orders[order_id]
        else:
            st.orders[order_id] = (symbol, rest, new_vec)
        st.gross_wf += self._symbol_gross(st, symbol) - before_sym
        return True, "filled"

    def cancel_request(self, account, order_id):
        """A request releases nothing: the order can still fill until the
        ordering point says otherwise."""
        return False, "awaiting_acknowledgement"

    def cancel_ack(self, account, order_id):
        st = self._st(account)
        rec = st.orders.pop(order_id, None)
        if rec is None:
            return False, "unknown_order"
        symbol, remaining, vec = rec
        before_sym = self._symbol_gross(st, symbol)
        for k in range(self.n_scen):
            if vec[k] > 0:
                st.pos_part_num[k] -= vec[k]
        self._bump(st.buy_rem if remaining > 0 else st.sell_rem, symbol,
                   -remaining if remaining > 0 else remaining)
        st.gross_wf += self._symbol_gross(st, symbol) - before_sym
        # nothing was executed, so the cost this order reserved is released
        st.debit_reserved -= abs(remaining) * self.risk.debit_per_lot(symbol)
        return True, "cancelled"

    # ---- reporting -------------------------------------------------------

    def observe_market_state(self, account, state, now=0):
        lease = self.lease.get(account)
        if lease is None:
            return "no_lease"
        if now >= lease.expiry:
            return "lease_expired"
        if getattr(lease, "mode", "normal") == "quarantine":
            return "quarantine"
        if self.ratchet:
            state = max(self.worst_state.get(account, 0), state)
            self.worst_state[account] = state
        if (self.used_risk(account) > lease.risk_at(state)
                or self.used_gross(account) > lease.gross_at(state)):
            return "reduce_only"
        return "within_envelope"

    def local_positions(self, account):
        """Net of filled positions and live orders, for merging an account's
        holdings across gateways."""
        st = self._st(account)
        out = dict(st.filled)
        for _oid, (sym, rem, _v) in st.orders.items():
            out[sym] = out.get(sym, 0) + rem
        return out

    def filled_positions(self, account):
        return dict(self._st(account).filled)

    def live_orders(self, account):
        return {oid: (sym, rem)
                for oid, (sym, rem, _v) in self._st(account).orders.items()}

    def used_risk(self, account):
        st = self._st(account)
        if not self.worst_fill:
            return self.risk.R(self.local_positions(account))
        return self._risk_wf(st)

    def used_debit(self, account):
        st = self._st(account)
        return st.debit_reserved + (st.debit_incurred_total - st.debit_baseline)

    def used_gross(self, account):
        st = self._st(account)
        if not self.worst_fill:
            return self.risk.gross(self.local_positions(account))
        return st.gross_wf

    # ---- snapshot and recovery -------------------------------------------

    def state_digest(self, account):
        """A digest over the state that recovery has to reproduce.

        Only the authoritative fields go in. The per-scenario aggregates are a
        pure function of them, so including them would hide a rebuild that got
        the aggregates right by accident and the orders wrong.
        """
        import hashlib
        st = self._st(account)
        canon = repr((
            sorted(st.filled.items()),
            sorted((oid, sym, rem) for oid, (sym, rem, _v) in st.orders.items()),
        )).encode()
        return hashlib.sha256(canon).hexdigest()[:16]

    def aggregate_digest(self, account):
        import hashlib
        st = self._st(account)
        canon = repr((list(st.filled_num), list(st.pos_part_num),
                      sorted(st.buy_rem.items()), sorted(st.sell_rem.items()),
                      st.gross_wf)).encode()
        return hashlib.sha256(canon).hexdigest()[:16]

    def snapshot(self):
        """A point-in-time image plus the log watermark it corresponds to.

        Only the authoritative fields are written. Everything else is rebuilt
        on load, which is why a snapshot cannot disagree with itself.
        """
        wm = self.sequencer.position() if self.sequencer else 0
        return {
            "holder": (self.id, self.incarnation),
            "watermark": wm,
            "admission_seq": dict(self.admission_seq),
            "accounts": {
                acct: {
                    "filled": dict(st.filled),
                    "orders": {oid: (sym, rem)
                               for oid, (sym, rem, _v) in st.orders.items()},
                    "debit_reserved": st.debit_reserved,
                    "debit_incurred_total": st.debit_incurred_total,
                    "debit_baseline": st.debit_baseline,
                }
                for acct, st in self.state.items()
            },
        }

    def _apply_admit(self, account, order_id, symbol, qty):
        st = self._st(account)
        if order_id in st.orders:
            return                                   # idempotent
        vec = self._order_vector(symbol, qty)
        st.orders[order_id] = (symbol, qty, vec)
        for k in range(self.n_scen):
            if vec[k] > 0:
                st.pos_part_num[k] += vec[k]
        st.debit_reserved += abs(qty) * self.risk.debit_per_lot(symbol)
        before = self._symbol_gross(st, symbol)
        self._bump(st.buy_rem if qty > 0 else st.sell_rem, symbol,
                   qty if qty > 0 else -qty)
        st.gross_wf += self._symbol_gross(st, symbol) - before

    def restore(self, snapshot, sequencer, expected_holder=None):
        """Load a snapshot and replay the log after its watermark.

        Refused, with the gateway left in quarantine, when the snapshot was cut
        for another holder or claims a watermark the ordering point has not
        reached. Recovery is the same fold the live path performs, so a
        recovered gateway is byte-identical to one rebuilt from the whole log.
        """
        self.recovering = True
        holder = tuple(snapshot.get("holder", (self.id, self.incarnation)))
        want = expected_holder or (self.id, self.incarnation)
        if holder != tuple(want):
            return False, "snapshot_for_another_holder"
        wm = snapshot.get("watermark", 0)
        if wm > sequencer.position():
            return False, "snapshot_ahead_of_log"

        self.sequencer = sequencer
        self.state = {}
        self.log_high_water = wm
        self.admission_seq = dict(snapshot.get("admission_seq", {}))
        for acct, blob in snapshot.get("accounts", {}).items():
            st = self._st(acct)
            for oid, (sym, rem) in blob.get("orders", {}).items():
                self._apply_admit(acct, oid, sym, rem)
            st.debit_reserved = blob.get("debit_reserved", st.debit_reserved)
            st.debit_incurred_total = blob.get("debit_incurred_total",
                                               st.debit_incurred_total)
            st.debit_baseline = blob.get("debit_baseline", st.debit_baseline)
            for sym, q in blob.get("filled", {}).items():
                if q:
                    self._bump(st.filled, sym, q)
                    part = self._order_vector(sym, q)
                    for k in range(self.n_scen):
                        st.filled_num[k] += part[k]
            st.gross_wf = self._gross_wf_fullscan(st)

        applied, skipped = self.replay(sequencer.replay_from(wm))
        self.recovering = False
        return True, f"replayed {applied} events, {skipped} not ours"

    def replay(self, events):
        """Apply an ordered slice of the log. Events for another holder are
        skipped; repeats are absorbed."""
        applied = skipped = 0
        mine = (self.id, self.incarnation)
        for pos, ev in events:
            if pos < self.log_high_water:
                skipped += 1
                continue
            self.log_high_water = pos + 1
            kind = ev[0]
            if kind == "admit":
                _k, lease_id, seq, oid, acct, sym, qty, holder = ev
                if holder is not None and tuple(holder) != mine:
                    skipped += 1
                    continue
                self._apply_admit(acct, oid, sym, qty)
                prev = self.admission_seq.get(lease_id, 0)
                self.admission_seq[lease_id] = max(prev, seq)
                applied += 1
            elif kind == "fill":
                oid, qty = ev[1], ev[2]
                for acct in list(self.state):
                    if oid in self.state[acct].orders:
                        self.fill(acct, oid, qty)
                        applied += 1
                        break
                else:
                    skipped += 1
            elif kind == "cancel":
                _k, oid = ev
                for acct in list(self.state):
                    if oid in self.state[acct].orders:
                        self.cancel_ack(acct, oid)
                        applied += 1
                        break
                else:
                    skipped += 1
            else:
                skipped += 1
        return applied, skipped

    @classmethod
    def rebuild_from_log(cls, gateway_id, risk, sequencer, incarnation=0):
        """A gateway rebuilt from the whole log with no snapshot. The reference
        the recovered state is compared against."""
        gw = cls(gateway_id, risk, incarnation=incarnation, sequencer=sequencer)
        gw.recovering = True
        gw.log_high_water = 0
        gw.replay(sequencer.replay_from(0))
        gw.recovering = False
        return gw
