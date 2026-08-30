"""Ingress admission gateway.

Holds a lease per account and admits orders locally. The lease carries two
resources, because one does not bound the other:

  risk   an upper bound on R of the position set this gateway has admitted
  gross  an upper bound on the gross notional of that set

The risk resource alone is not enough. An order that reduces this gateway's
requirement can still raise gross notional, and the add-on term is a function
of gross; see tests/test_counterexamples.py, c2.

The admission rule compares the *absolute* value each resource would take after
the order against the lease, not the increment. Charging increments does not
bound anything, because a position flipped from short to long leaves the
requirement unchanged while the account's requirement moves; see c1.

The partition used for the risk decomposition is the gateway, not the symbol.
Lemma 1 holds for any partition of the position set, and the account's total
position is the sum of what the gateways admitted, so
`sum_g R(admitted_g) >= R(total)` regardless of how symbols are spread across
matching shards.
"""


class Gateway:
    def __init__(self, gateway_id, risk, fencing=True, ratchet=False):
        self.id = gateway_id
        self.risk = risk
        self.fencing = fencing
        self.ratchet = ratchet
        self.admitted = {}               # account -> {symbol: lots}
        self.lease = {}                  # account -> Lease
        self.seen_generation = {}
        self.worst_state = {}

    # ---- lease handling -------------------------------------------------

    def install_lease(self, lease):
        self.lease[lease.account] = lease
        prev = self.seen_generation.get(lease.account, 0)
        self.seen_generation[lease.account] = max(prev, lease.generation)
        self.worst_state[lease.account] = 0

    def local_positions(self, account):
        return self.admitted.setdefault(account, {})

    # ---- admission ------------------------------------------------------

    def admit(self, account, symbol, qty, generation, market_state=0, now=0):
        """Return (accepted, reason).

        `now` is logical time. A lease that has passed its expiry is refused
        without any message from the allocator, which is what makes a
        generation bump take effect at a partitioned gateway.
        """
        lease = self.lease.get(account)
        if lease is None:
            return False, "no_lease"

        if now >= lease.expiry:
            return False, "lease_expired"

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

        pos = self.local_positions(account)
        risk_after = self.risk.R_after(pos, symbol, qty)
        gross_after = self.risk.gross_after(pos, symbol, qty)

        if risk_after > lease.risk_at(state):
            return False, "risk_envelope"
        if gross_after > lease.gross_at(state):
            return False, "gross_envelope"

        pos[symbol] = pos.get(symbol, 0) + qty
        return True, "ok"

    def observe_market_state(self, account, state, now=0):
        """Evaluate the account against the current market state without an
        order being present.

        Returns one of: no_lease, lease_expired, within_envelope, reduce_only.
        A market-state tick calls this for every account the gateway holds, so
        the condition is reported when the state moves rather than when the
        next order happens to arrive.
        """
        lease = self.lease.get(account)
        if lease is None:
            return "no_lease"
        if now >= lease.expiry:
            return "lease_expired"

        if self.ratchet:
            state = max(self.worst_state.get(account, 0), state)
            self.worst_state[account] = state

        pos = self.local_positions(account)
        if (self.risk.R(pos) > lease.risk_at(state)
                or self.risk.gross(pos) > lease.gross_at(state)):
            return "reduce_only"
        return "within_envelope"

    # ---- reporting ------------------------------------------------------

    def used_risk(self, account):
        return self.risk.R(self.local_positions(account))

    def used_gross(self, account):
        return self.risk.gross(self.local_positions(account))
