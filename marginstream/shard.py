"""Symbol shard.

Holds positions for its own symbols and one lease per account. Admission is a
local computation: price the order's marginal R against this shard's positions
only, then compare with the remaining lease. No cross-shard read.

`fencing` selects whether the shard rejects orders carrying a generation other
than the one its lease was issued under.
"""


class Shard:
    def __init__(self, shard_id, risk, fencing=True, ratchet=False):
        self.id = shard_id
        self.risk = risk
        self.fencing = fencing
        # when set, the curve is evaluated at the most adverse market state
        # observed since the lease was installed, rather than at the state
        # carried by the current message
        self.ratchet = ratchet
        self.worst_state = {}            # account -> most adverse state seen
        self.positions = {}              # account -> {symbol: lots}
        self.seen_generation = {}        # account -> highest generation observed
        self.lease = {}                  # account -> Lease
        self.spent = {}                  # account -> R consumed under that lease

    def install_lease(self, lease):
        self.lease[lease.account] = lease
        self.spent[lease.account] = 0
        prev = self.seen_generation.get(lease.account, 0)
        self.seen_generation[lease.account] = max(prev, lease.generation)
        self.worst_state[lease.account] = 0

    def local_positions(self, account):
        return self.positions.setdefault(account, {})

    def admit(self, account, symbol, qty, generation, market_state=0):
        """Return (accepted, cost, reason).

        market_state indexes the published market state. A scalar lease ignores
        it; a conditional lease evaluates its curve at that index."""
        lease = self.lease.get(account)
        if lease is None:
            return False, 0, "no_lease"
        if self.fencing:
            # a generation observed on any message is monotone evidence of the
            # allocator's position; a shard whose lease is below it is stale
            seen = max(self.seen_generation.get(account, 0), generation)
            self.seen_generation[account] = seen
            if lease.generation < seen:
                return False, 0, "shard_stale"
            if lease.generation != generation:
                return False, 0, "stale_generation"

        pos = self.local_positions(account)
        cost = self.risk.marginal_R(pos, symbol, qty)
        if cost < 0:
            cost = 0                      # risk-reducing orders cost nothing
        if self.ratchet:
            w = max(self.worst_state.get(account, 0), market_state)
            self.worst_state[account] = w
            effective_state = w
        else:
            effective_state = market_state
        available = (lease.at(effective_state) if hasattr(lease, "at")
                     else lease.amount)
        remaining = available - self.spent[account]
        if remaining < 0:
            # consumption already exceeds what this market state allows. The
            # shard cannot undo what it admitted; it stops admitting anything
            # that increases risk and reports the condition.
            return False, 0, "reduce_only"
        if cost > remaining:
            return False, cost, "lease_exhausted"

        self.spent[account] += cost
        pos[symbol] = pos.get(symbol, 0) + qty
        return True, cost, "ok"

    def total_spent(self, account):
        return self.spent.get(account, 0)
