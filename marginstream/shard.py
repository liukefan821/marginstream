"""Symbol shard.

Holds positions for its own symbols and one lease per account. Admission is a
local computation: price the order's marginal R against this shard's positions
only, then compare with the remaining lease. No cross-shard read.

`fencing` selects whether the shard rejects orders carrying a generation other
than the one its lease was issued under.
"""


class Shard:
    def __init__(self, shard_id, risk, fencing=True):
        self.id = shard_id
        self.risk = risk
        self.fencing = fencing
        self.positions = {}              # account -> {symbol: lots}
        self.seen_generation = {}        # account -> highest generation observed
        self.lease = {}                  # account -> Lease
        self.spent = {}                  # account -> R consumed under that lease

    def install_lease(self, lease):
        self.lease[lease.account] = lease
        self.spent[lease.account] = 0
        prev = self.seen_generation.get(lease.account, 0)
        self.seen_generation[lease.account] = max(prev, lease.generation)

    def local_positions(self, account):
        return self.positions.setdefault(account, {})

    def admit(self, account, symbol, qty, generation):
        """Return (accepted, cost, reason)."""
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
        remaining = lease.amount - self.spent[account]
        if cost > remaining:
            return False, cost, "lease_exhausted"

        self.spent[account] += cost
        pos[symbol] = pos.get(symbol, 0) + qty
        return True, cost, "ok"

    def total_spent(self, account):
        return self.spent.get(account, 0)
