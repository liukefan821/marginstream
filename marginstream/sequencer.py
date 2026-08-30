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
        self.rejected = 0

    def submit(self, lease_id, admission_seq):
        """Return (accepted, reason)."""
        if lease_id in self.fenced:
            self.rejected += 1
            return False, "lease_fenced"
        expected = self.last_seq.get(lease_id, 0) + 1
        if admission_seq != expected:
            self.rejected += 1
            return False, "sequence_gap"
        self.last_seq[lease_id] = admission_seq
        return True, "ok"

    def fence(self, lease_id):
        """Stop accepting anything under this lease and return its seal.

        Fencing is idempotent: fencing a lease twice returns the same seal.
        """
        if lease_id not in self.fenced:
            self.fenced[lease_id] = Seal(lease_id,
                                         self.last_seq.get(lease_id, 0))
        return self.fenced[lease_id]

    def is_fenced(self, lease_id):
        return lease_id in self.fenced

    def seal_of(self, lease_id):
        return self.fenced.get(lease_id)
