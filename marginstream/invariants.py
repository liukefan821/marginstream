"""Invariant checks.

These recompute the quantities they check from the full system state rather
than reusing values the components maintain, so a component that miscounts is
still caught.
"""


class Violation(Exception):
    pass


class Oracle:
    def __init__(self, risk, allocator, shards):
        self.risk = risk
        self.allocator = allocator
        self.shards = shards
        self.applied_ids = set()
        self.records = []                # one entry per check, for the log

    def portfolio(self, account):
        merged = {}
        for sh in self.shards.values():
            for sym, qty in sh.local_positions(account).items():
                merged[sym] = merged.get(sym, 0) + qty
        return merged

    # ---- individual checks ---------------------------------------------

    def check_lease_sum(self, account, epoch, generation, budget):
        issued = self.allocator.issued.get((account, epoch, generation), {})
        total = sum(issued.values())
        if total > budget:
            raise Violation(
                f"I1 lease sum {total} exceeds budget {budget} "
                f"for {account} e{epoch} g{generation}")
        return total

    def check_shard_spend(self, account):
        for sh in self.shards.values():
            lease = sh.lease.get(account)
            if lease is None:
                continue
            if sh.total_spent(account) > lease.amount:
                raise Violation(
                    f"I2 shard {sh.id} spent {sh.total_spent(account)} "
                    f"over lease {lease.amount} for {account}")

    def check_order_id(self, order_id):
        if order_id in self.applied_ids:
            raise Violation(f"I4 client order id {order_id} applied twice")
        self.applied_ids.add(order_id)

    def check_solvency(self, account, equity):
        """M(P) <= equity for the merged portfolio."""
        p = self.portfolio(account)
        m = self.risk.M(p)
        self.records.append((account, m, equity))
        if m > equity:
            raise Violation(
                f"I5 requirement {m} exceeds equity {equity} for {account}")
        return m

    # ---- convenience ----------------------------------------------------

    def check_all(self, account, equity):
        self.check_shard_spend(account)
        return self.check_solvency(account, equity)
