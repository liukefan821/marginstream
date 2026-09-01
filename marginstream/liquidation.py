"""Liquidation.

The admission mechanism keeps an account inside its equity for any move the
scenario grid covers. Liquidation is what happens when the move is larger than
that, which is the only way the account can end up with a requirement above its
equity. So a liquidation is by construction an event outside the model, and the
question is not whether it can be prevented but how much the delay costs.

Three things have to stop, and they stop in three different places:

    new admissions            at the ordering point, by fencing every live
                              lease. This does not need to reach the gateway.
    resting orders filling    only by a cancel acknowledged at the ordering
                              point. A fence does not stop a fill: an order
                              already on a book can still trade.
    the position moving       only by trading it out.

The first is immediate and cheap. The second takes an acknowledgement round
trip per order. The third takes as long as the position takes to unwind.

**Who is allowed to trade the account down.** Not an ordinary gateway. c9
showed that an order which lowers one gateway's requirement can raise the
account's, by removing a hedge held on another gateway, so a gateway cannot
decide on its own that an order reduces risk. The liquidator therefore holds
the merged account view and checks each of its orders against it: the order is
refused unless the merged loss numerator does not rise at any scenario in the
grid and the merged gross does not rise.

That check replaces a ceiling rather than adding to one. Nothing in the
capacity accounting bounds the liquidator, so it is inside the trusted
computing base on its own account. The alternative, putting it under an
ordinary lease, does not work: the account is in shortfall exactly when the
solve is infeasible and an ordinary lease would be issued in quarantine.

**How a basket reaches the book.** It does not. Ordinary matching here is
sharded by symbol, and a basket spanning several symbols cannot fill atomically
across several order books: a partial fill on one shard and none on another
leaves the account somewhere the check never approved. Rather than assume an
atomicity the matching path does not have, the unwind does not use the matching
path. A basket is an internal transfer, priced against the venue's own marks
inside the same band and fee cap an ordinary fill is held to, and committed at
the ordering point as a single log record. Either the record is there and the
whole basket happened, or it is not and none of it did.

That is a real venue mechanism with a real cost, and the cost is that the venue
is the counterparty to the transfer. Nothing here claims that sharded matching
supports atomic cross-symbol baskets, and nothing here routes a basket through
it.

What the liquidator is not bounded on is execution cost. Its own fills pay a
band and a fee, which lowers equity, and the debit envelope was sized for the
order set that existed before the trigger. The bound that does hold is
arithmetic rather than reserved: the liquidator only reduces, so the lots it
trades are at most the lots the account holds, and its cost is at most
`sum_s |q_s| * (band_s + fee_s)`. `Liquidation.debit_bound` computes it and E6
measures the realised figure against it.
"""


def merge(gateways, account):
    """Merge the exposure of one account across gateways.

    The per-scenario numerators add, because the loss under a fixed scenario is
    linear in positions. The per-symbol quantities have to be merged before the
    gross formula is applied: two gateways' worst-fill gross figures do not
    add, since a buy held at one and a sell held at the other net inside the
    account.
    """
    filled, buy, sell = {}, {}, {}
    fnum, pnum = None, None
    for gw in gateways:
        f, b, s = gw.exposure_parts(account)
        for d, src in ((filled, f), (buy, b), (sell, s)):
            for k, v in src.items():
                d[k] = d.get(k, 0) + v
        gf, gp = gw.scenario_parts(account)
        if fnum is None:
            fnum, pnum = list(gf), list(gp)
        else:
            for k in range(len(fnum)):
                fnum[k] += gf[k]
                pnum[k] += gp[k]
    if fnum is None:
        fnum, pnum = [], []
    return filled, buy, sell, fnum, pnum


def envelopes(risk, gateways, account, extra=None, extra_filled=None):
    """Merged worst-fill envelopes, and the per-scenario vector behind the
    first.

    `extra` is an order that would rest, as (symbol, qty): worst-fill counts
    its positive part at every scenario. `extra_filled` is a quantity that
    would be part of the position instead, as {symbol: dq}, which is what an
    order that crosses the book immediately produces. The two are not the same
    state and the difference is the whole reason the unwind crosses rather than
    rests.
    """
    filled, buy, sell, fnum, pnum = merge(gateways, account)
    if extra_filled:
        for sym, dq in extra_filled.items():
            filled[sym] = filled.get(sym, 0) + dq
    n = len(fnum) if fnum else len(risk.grid)
    vec = [0] * n
    for k in range(n):
        vec[k] = (fnum[k] if fnum else 0) + (pnum[k] if pnum else 0)
    if extra_filled:
        for k, f in enumerate(risk.grid):
            for sym, dq in extra_filled.items():
                vec[k] += risk.leg_num(sym, dq, f)
    if extra is not None:
        sym, qty = extra
        for k, f in enumerate(risk.grid):
            leg = risk.leg_num(sym, qty, f)
            if leg > 0:
                vec[k] += leg
        if qty > 0:
            buy[sym] = buy.get(sym, 0) + qty
        else:
            sell[sym] = sell.get(sym, 0) - qty

    worst = max(vec) if vec else 0
    r = 0 if worst <= 0 else risk.ceil_div(worst, risk.DEN)

    g = 0
    for sym in set(filled) | set(buy) | set(sell):
        mark = risk.mark_plus(sym)
        q = filled.get(sym, 0)
        g += mark * max(abs(q + buy.get(sym, 0)), abs(q - sell.get(sym, 0)))
    return r, g, vec


def requirement(risk, gateways, account):
    """The merged worst-fill requirement. The scenario term is the envelope
    over reachable fills; the add-on takes the reachable gross."""
    r, g, _vec = envelopes(risk, gateways, account)
    return r + risk.A_of_gross(g)


def shortfall(risk, gateways, account, ledger, marks=None):
    """Requirement less equity, floored at zero. The monitor's trigger."""
    return max(0, requirement(risk, gateways, account)
               - ledger.equity(marks))


class Liquidation:
    """One account's liquidation, driven a step at a time by the caller so the
    experiments can put faults between the steps."""

    def __init__(self, risk, sequencer, allocator, account, ledger,
                 gateways, liquidator):
        self.risk = risk
        self.sequencer = sequencer
        self.allocator = allocator
        self.account = account
        self.ledger = ledger
        self.gateways = list(gateways)          # ordinary ingress gateways
        self.liquidator = liquidator            # the gateway the unwind uses
        self.fenced = []
        self.seals = {}
        self.cancel_requested = 0
        self.cancel_acked = 0
        self.refused_after_fence = 0
        self.reduce_orders = 0
        self.reduce_refused = 0
        self.stalls = 0
        self.fill_no = 0
        self._order_no = 0
        self._basket_no = 0
        self.baskets = 0
        self.cancels_recorded_only = 0
        self.cancels_never_acknowledged = 0

    @property
    def liquidator_lease_id(self):
        lease = self.liquidator.lease.get(self.account)
        return None if lease is None else lease.lease_id

    # ---- the view every decision is taken against ------------------------

    def all_gateways(self):
        return self.gateways + [self.liquidator]

    def envelopes(self, extra=None, extra_filled=None):
        return envelopes(self.risk, self.all_gateways(), self.account, extra,
                         extra_filled)

    def requirement(self):
        return requirement(self.risk, self.all_gateways(), self.account)

    def merged_filled(self):
        filled, _b, _s, _f, _p = merge(self.all_gateways(), self.account)
        return {k: v for k, v in filled.items() if v}

    def debit_bound(self):
        """The most the unwind can cost.

        Taken over the worst-fill reachable position, not over what is filled
        now. Orders admitted before the trigger are still able to fill during
        the delay, and every lot they add is a lot the unwind has to trade back
        out. E6 measured the figure over the filled position first and the
        realised cost came in above it.

        The per-symbol quantity is the same one the gross envelope uses:
        `max(|f_s + B_s|, |f_s - S_s|)`.
        """
        filled, buy, sell, _fnum, _pnum = merge(self.all_gateways(),
                                                self.account)
        total = 0
        for sym in set(filled) | set(buy) | set(sell):
            q = filled.get(sym, 0)
            lots = max(abs(q + buy.get(sym, 0)), abs(q - sell.get(sym, 0)))
            total += lots * self.risk.debit_per_lot(sym)
        return total

    # ---- step one: fence -------------------------------------------------

    def fence_liquidator(self):
        """End the liquidator's own authority to commit baskets.

        This is authority like any other and the settlement will not run while
        it is live: a liquidator that can still commit is a holder that can
        still change the position the settlement is about to freeze.
        """
        lid = self.liquidator_lease_id
        if lid is None:
            return None
        seal = self.sequencer.fence(lid)
        self.seals[lid] = seal
        return seal

    def fence_all(self, deliver=True):
        """Fence every lease the allocator still has authority recorded for.

        The seal is taken here. Nothing is released by it: releasing exposure
        needs the terminal reconciliation, and the position is still there.
        `deliver` models whether the gateways are also told; the point of
        fencing at the ordering point is that the answer does not matter for
        safety.
        """
        auth = self.allocator.authority.get(self.account, {})
        for lease_id in sorted(auth):
            self.seals[lease_id] = self.sequencer.fence(lease_id)
            self.fenced.append(lease_id)
        if deliver:
            for gw in self.gateways:
                lease = gw.lease.get(self.account)
                if lease is not None and lease.lease_id in self.seals:
                    gw.lease.pop(self.account, None)
        return list(self.fenced)

    # ---- step two: cancel ------------------------------------------------

    def cancel_all(self, lose=None, lose_mode="never_acknowledged"):
        """Cancel every live order. `lose(order_id)` picks the ones that fail,
        and `lose_mode` says how, because the two ways are not the same fact.

        `notification_lost`   the ordering point recorded the cancel and the
                              news of it did not reach the gateway. The order
                              is cancelled: it is in the authoritative log, so
                              nothing can fill against it and a rebuild from
                              the log releases its reservation. Only the local
                              view is stale.

        `never_acknowledged`  the matching side never confirmed the cancel, so
                              there is no record of it anywhere. The order is
                              still live and can still fill. A settlement has
                              to keep its worst-fill reservation, and the fence
                              does not help: a fence stops new admissions and
                              does nothing to an order already resting.

        Collapsing these into one fault name would let a run that never
        released anything look identical to one that released everything.
        """
        from .execution import execute_cancel
        for gw in self.gateways:
            for oid in list(gw.live_orders(self.account)):
                self.cancel_requested += 1
                if lose is not None and lose(oid):
                    if lose_mode == "notification_lost":
                        # the ordering point still records it; the gateway is
                        # simply not told
                        if self.sequencer.record_cancel(oid)[0]:
                            self.cancels_recorded_only += 1
                    else:
                        self.cancels_never_acknowledged += 1
                    continue
                ok, _why = execute_cancel(self.sequencer, gw, self.account, oid)
                if ok:
                    self.cancel_acked += 1
        return self.cancel_requested, self.cancel_acked

    # ---- step three: unwind ----------------------------------------------

    def _next_order_id(self):
        self._order_no += 1
        return f"liq:{self.account}:{self._order_no}"

    def propose(self, fraction_num, fraction_den, lots_cap=None):
        """A proportional reduction across every open leg.

        Leg at a time does not work. On a hedged book, closing one leg while
        its offset stays put raises the account's requirement, which is c9 with
        the liquidator in the gateway's place. Scaling every leg by the same
        factor cannot: the loss under a scenario is linear in positions, so a
        position scaled by a factor below one has a scenario vector scaled by
        the same factor, and gross scales with it too.

        The rounding breaks the proportionality, so this is a proposal and not
        a guarantee. `check` is the guarantee.
        """
        basket = {}
        for sym, q in sorted(self.merged_filled().items()):
            take = abs(q) * fraction_num // fraction_den
            if take == 0:
                take = 1
            if lots_cap is not None:
                take = min(take, lots_cap)
            take = min(take, abs(q))
            if take:
                basket[sym] = -take if q > 0 else take
        return basket

    def check(self, basket):
        """Would this basket, filled in full, leave both merged envelopes where
        they are or lower?"""
        r0, g0, _v0 = self.envelopes()
        r1, g1, _v1 = self.envelopes(extra_filled=basket)
        return (r1 <= r0 and g1 <= g0), (r0, g0), (r1, g1)

    def commit(self, basket, after_commit=None):
        """Commit one checked basket as a single atomic transfer.

        There is no resting stage, so the state the check was made against and
        the state that results are the same state. An earlier version admitted
        the legs as orders and filled them, which left a window in which the
        merged worst-fill envelope was above where the check left it.
        """
        from .execution import execute_basket
        lid = self.liquidator_lease_id
        if lid is None:
            return None, "no_liquidator_lease"
        self._basket_no += 1
        basket_id = f"liqb:{self.account}:{self._basket_no}"
        legs, terms = [], []
        for sym in sorted(basket):
            qty = basket[sym]
            s = self.risk.symbols[sym]
            price = s.mark + s.band if qty > 0 else s.mark - s.band
            fee = s.fee_per_lot * abs(qty)
            legs.append((sym, qty, price, fee))
            terms.append((s.mark, s.band, s.fee_per_lot))
        seq = self.sequencer.last_seq.get(lid, 0) + 1
        ok, why = execute_basket(
            self.sequencer, self.liquidator, self.ledger, lid, seq, basket_id,
            self.account, (self.liquidator.id, self.liquidator.incarnation),
            tuple(legs), tuple(terms), after_commit=after_commit)
        if not ok:
            self.reduce_refused += 1
            return None, why
        self.baskets += 1
        return basket_id, tuple(legs)

    def unwind_step(self, fraction_num=1, fraction_den=4, lots_cap=None,
                    now=0, after_commit=None):
        """One atomic reduction: propose, check, commit.

        The fraction is halved on a failed check, down to a single lot, which
        is where a book that cannot be reduced without raising the requirement
        shows up as a stall rather than as a breach. `after_commit` is the hook
        E7 uses to destroy the process between the commit landing in the log
        and the fold happening locally.
        """
        num, den = fraction_num, fraction_den
        basket = None
        while True:
            candidate = self.propose(num, den, lots_cap)
            if not candidate:
                return None
            ok, _before, _after = self.check(candidate)
            if ok:
                basket = candidate
                break
            if all(abs(v) <= 1 for v in candidate.values()):
                self.stalls += 1
                return ("stalled", candidate)
            den *= 2

        basket_id, legs = self.commit(basket, after_commit=after_commit)
        if basket_id is None:
            return ("refused", legs)
        return ("committed", basket_id, legs)

    def flat(self):
        """No position left. Live orders are reported separately: an order the
        matching side never acknowledged a cancel for cannot be traded away by
        the liquidator, and calling that state flat would hide it."""
        return not self.merged_filled()

    def live_orders_remaining(self):
        return sum(len(gw.live_orders(self.account))
                   for gw in self.all_gateways())

    # ---- the part that releases capacity ---------------------------------

    def settle(self, fence_liquidator=True, if_credit_version=None):
        """Run the account-wide compaction.

        Issuance is suspended first, then the liquidator's own authority is
        ended, and only then is the barrier taken. Doing it in the other order
        would take a barrier over a set of holders that can still change.

        `fence_liquidator=False` is what the fault experiment uses to check
        that the settlement refuses while the liquidator can still commit.
        """
        cv = self.allocator.begin_settling(self.account)
        if fence_liquidator:
            self.fence_liquidator()
        return self.allocator.settle(
            self.account, self.sequencer, self.risk,
            if_credit_version=cv if if_credit_version is None
            else if_credit_version)

    def reconcile(self):
        """Terminal reconciliation for every fenced lease.

        Refused for a lease whose seal has not been delivered, which is the
        case the operational-failure experiment injects: the capacity stays
        occupied and no replacement gets it.
        """
        out = {}
        for lease_id in self.fenced:
            seal = self.seals.get(lease_id)
            if seal is None:
                out[lease_id] = (False, "no_seal")
                continue
            out[lease_id] = self.allocator.release(self.account, lease_id,
                                                   seal, self.sequencer,
                                                   self.risk)
        return out
