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
                 "buy_rem", "sell_rem", "gross_wf")

    def __init__(self, n_scen):
        self.filled = {}                 # symbol -> filled lots
        self.orders = {}                 # order_id -> (symbol, remaining, vec)
        self.filled_num = [0] * n_scen   # loss numerator of filled, per scenario
        self.pos_part_num = [0] * n_scen # sum of positive order parts
        self.buy_rem = {}                # symbol -> unfilled buy lots
        self.sell_rem = {}               # symbol -> unfilled sell lots (positive)
        self.gross_wf = 0                # running worst-fill gross


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

    # ---- state helpers ---------------------------------------------------

    def _st(self, account):
        st = self.state.get(account)
        if st is None:
            st = _AccountState(self.n_scen)
            self.state[account] = st
        return st

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
        self.admission_seq.setdefault(getattr(lease, "lease_id", None), 0)
        prev = self.seen_generation.get(lease.account, 0)
        self.seen_generation[lease.account] = max(prev, lease.generation)
        self.worst_state[lease.account] = 0

    # ---- admission -------------------------------------------------------

    def admit(self, account, symbol, qty, generation, market_state=0, now=0,
              order_id=None):
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
            if qty > 0:
                st.buy_rem[symbol] = st.buy_rem.get(symbol, 0) + qty
            else:
                st.sell_rem[symbol] = st.sell_rem.get(symbol, 0) - qty
            gross_after = st.gross_wf - before_sym + self._symbol_gross(st, symbol)
            # undo the tentative update; it is redone below only if admitted
            if qty > 0:
                st.buy_rem[symbol] -= qty
            else:
                st.sell_rem[symbol] += qty
        else:
            net = dict(st.filled)
            for _oid, (s2, r2, _v) in st.orders.items():
                net[s2] = net.get(s2, 0) + r2
            net[symbol] = net.get(symbol, 0) + qty
            risk_after = self.risk.R(net)
            gross_after = self.risk.gross(net)

        if risk_after > lease.risk_at(state):
            return False, "risk_envelope"
        if gross_after > lease.gross_at(state):
            return False, "gross_envelope"

        lid = getattr(lease, "lease_id", None)
        if self.sequencer is not None and lid is not None:
            nxt = self.admission_seq.get(lid, 0) + 1
            oid = order_id if order_id is not None else f"{lid}:{nxt}"
            ok, why = self.sequencer.submit(
                lid, nxt, oid, account, symbol, qty)
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
        if qty > 0:
            st.buy_rem[symbol] = st.buy_rem.get(symbol, 0) + qty
        else:
            st.sell_rem[symbol] = st.sell_rem.get(symbol, 0) - qty
        st.gross_wf += self._symbol_gross(st, symbol) - before_sym
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

        st.filled[symbol] = st.filled.get(symbol, 0) + qty
        if remaining > 0:
            st.buy_rem[symbol] = st.buy_rem.get(symbol, 0) - qty
        else:
            st.sell_rem[symbol] = st.sell_rem.get(symbol, 0) + qty

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
        if remaining > 0:
            st.buy_rem[symbol] = st.buy_rem.get(symbol, 0) - remaining
        else:
            st.sell_rem[symbol] = st.sell_rem.get(symbol, 0) + remaining
        st.gross_wf += self._symbol_gross(st, symbol) - before_sym
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

    def used_gross(self, account):
        st = self._st(account)
        if not self.worst_fill:
            return self.risk.gross(self.local_positions(account))
        return st.gross_wf
