# 5. Failure and recovery

## 5.1 What has to survive a node loss

Three kinds of state, with different recovery paths:

| State | Owner | Size at the scale of §1.1 | Rebuilt from |
|---|---|---|---|
| Order books | Matching shard | ≈ 160 MB per shard at 8 shards | Snapshot plus log tail |
| Per-account positions, collateral, schedule, generation | Allocator shard | ≈ 27 MB total | Snapshot plus log tail |
| Per-(account, shard) scenario vectors | Matching shard | ≈ 64 MB total | Derived; not snapshotted |
| Balances | Ledger | ≈ 100 MB | Journal fold |

The scenario vectors of §1.7 are a cache of a pure function of positions, so
they are rebuilt rather than restored. That is a deliberate choice: snapshotting
them would double the snapshot and buy nothing, because reconstructing 128 bytes
from five positions is faster than reading it from disk.

## 5.2 What must be on the replicated log

An admission decision is a function of four inputs: the order, the shard's local
positions, the lease the shard held, and the market state it read. Row 10 of the
NFR table requires that any decision be replayable. Replay reproduces the first
two from the log by construction. The other two do not follow unless they are on
the log as well.

This is a stronger requirement than the running case has. There, everything that
affects matching arrives as a command through the sequencer. Here the allocator
and the market-data path both feed the admission decision, so both must be
sequenced into the same log, or replay produces a different answer from the one
the venue gave a client.

Writing the leases to the log directly is the obvious move, and the bandwidth
arithmetic rules it out:

- 10⁴ accounts change per epoch; a median account touches 5 shards, so ≈ 5 × 10⁴
  per-shard lease records per epoch.
- At a 100 ms epoch that is 5 × 10⁵ records per second.
- At ≈ 64 bytes per record, ≈ 32 MB/s.

The order command stream at 100k/s and 128 bytes is 12.8 MB/s (running case,
Part 1 §5). Logging leases per shard would put two and a half times the order
traffic on the log to carry a derived quantity.

What goes on the log instead is the input the schedule is computed from: per
account and epoch, the schedule scale, the shard weights, and the shape
identifier. One record of ≈ 80 bytes per changed account:

- 10⁴ records per epoch, 10⁵ per second, ≈ 8 MB/s.

Each shard then derives its own lease from (scale, weights, shape) by the same
integer arithmetic it used live. Replay reproduces the leases because the
derivation is deterministic, which is the same argument the running case makes
for balances being a fold of the journal rather than a stored value (Part 3 §1).

The market state index is logged as one record per contract per band crossing,
which at the granularity of §1.5 is rare compared with the order stream.

The general rule this yields, and the one worth carrying into §7: **log the
inputs a derived value is computed from, not the derived value**, unless the
derivation is not deterministic. Applying it here removes 24 MB/s from the log.

## 5.3 The idempotency chain

End to end, for one client order:

1. The client assigns a client order ID and retries with the same ID on timeout.
   A timeout is an unknown outcome, not a failure.
2. The ingress gateway stamps the order with the generation of the lease it
   holds and forwards it.
3. The shard admits or refuses. A refusal is final for that (order ID,
   generation) pair; a retry through a different gateway is a new admission
   attempt, and it may consume an additional ingress credit.
4. The matching shard applies a given client order ID at most once, so no
   duplicate book action occurs regardless of how many times the order was
   admitted upstream.
5. Clearing derives ledger entry IDs deterministically from the trade sequence,
   so a replayed trade produces the same entry ID and is deduplicated (running
   case, Part 3 §4).

Step 3 is where this design differs from the running case, and it is a cost we
state rather than hide: a retry routed through another gateway may conservatively
consume an additional ingress credit, but it cannot produce a duplicate book
action. The credit is recovered at the next epoch. The retry-amplification factor
is a measured quantity in the evidence appendix, not an assumption.

Exactly-once is, as in the running case, end-to-end idempotency layered on
at-least-once delivery. Nothing in the margin path changes that.

## 5.4 Fencing

Two rules, both of which we got wrong in a first implementation and corrected
after the simulator produced counterexamples. Both corrections are recorded in
`REPRODUCE.md` and reproduced here because they are the substance of this
section.

**Rule 1 — a shard that has seen a higher generation must fail closed.** The
first implementation compared the generation carried by the order with the
generation of the lease the shard held, and refused on mismatch. That looks
sufficient and is not: a shard still holding a superseded lease accepts an order
that carries the same superseded generation, because the two agree. The shard
therefore keeps spending an allowance the allocator has already replaced. The
corrected rule is that the generation observed on any message is monotone
evidence of the allocator's position, so a shard whose lease is below the highest
generation it has seen refuses to serve at all.

The scripted case in E2 makes the difference concrete. Two shards, both leased at
epoch 0; equity halves at epoch 1 and only shard 0 receives the new lease. With
the rule in force, shard 1 refuses everything and the requirement stays at 4,703
against 10,000 of equity. Without it, shard 1 spends the 4,700 it still holds on
top of shard 0's 4,500 and the requirement reaches 10,047 against 10,000 — a
detected breach. The zero-violation result of E1 is only meaningful because this
case shows the checker fires when it should.

**Rule 2 — liquidation voids leases before it reduces positions.** Entering
liquidation bumps the account's generation, which invalidates every outstanding
lease by Rule 1, and only then are positions reduced. Reversing the order leaves
a window in which a shard is still spending against an allowance computed for a
portfolio that is being changed underneath it.

A related error, also recorded: re-running liquidation on every tick that
reported the condition compounded the reduction and drove positions to zero.
Liquidation is followed by a generation bump and a re-issue, which resets the
consumption counters, so the condition clears instead of repeating.

## 5.5 Recovery-time arithmetic

Two different events with two different budgets.

**Warm failover** — a leader is lost and a follower takes over. Followers apply
the same log and hold the same books, so there is no rebuild; the cost is the
election plus the commit catch-up on the tail. The budget is row 8: under 3
seconds. The allocator fails over the same way, and because the log now carries
the schedule inputs rather than the schedules, a new allocator leader recomputes
identical schedules from the same records.

Behaviour during the window matters more than its length. Shards keep the leases
they hold and continue to admit within them; when the leases expire and no new
generation arrives, the fail-closed rule of §5.4 puts every shard into
reduce-only. The venue therefore keeps accepting risk-reducing orders throughout
an allocator failover, which is the property §3 defends in business terms.

**Cold rebuild** — a node restarts with nothing. Budget from the running case:
under 60 seconds.

- Snapshot read: 160 MB of books plus 27 MB of allocator state from NVMe at
  ≈ 2 GB/s ≈ 0.1 s.
- Scenario vectors rebuilt from positions: 5 × 10⁵ pairs at ≈ 80 multiply-adds
  each ≈ 4 × 10⁷ operations, well under a second.
- Log tail replay: this dominates. Replay runs without network or
  acknowledgement, so it is faster than live; we assume 10× and mark it as an
  assumption to be measured rather than a result. At 10⁶ commands per second and
  a 50-second replay budget, 5 × 10⁷ commands, which is 500 seconds of log at the
  sustained rate of 100k/s.

A snapshot every 5 minutes therefore leaves the cold rebuild inside 60 seconds
with room. Snapshots are taken on a follower so the leader never stalls, and the
snapshot records the log offset it corresponds to, so replay starts at a known
point.

The assumption to check first is the replay rate. If replay turns out to run at
2× live rather than 10×, the snapshot cadence tightens from 5 minutes to about a
minute; nothing else in the design changes. Measuring it is the first item in
§8.

## 5.6 Zero-downtime upgrade

The running case gives the rules for rolling a deterministic state machine
(Part 4 §6): never change the meaning of an existing command type, upgrade
followers before the leader, switch behaviour on a log-embedded activation
sequence rather than on wall-clock time, and keep the ability to replay the same
log under both versions and diff the state hashes.

Two additions specific to this design, both consequences of §5.2.

**The schedule derivation is part of the state machine.** Because the log carries
the schedule inputs and each shard derives its own lease, a change to the shape
table, to the state banding, or to the rounding in the derivation changes the
leases a replay produces. Any such change is a new shape identifier, activated at
a log sequence, with the old identifier retained for replay of older log
segments. The shape table is versioned data, not configuration.

**The market state banding is part of the state machine for the same reason.**
Re-banding the market state changes which state an old order was admitted under.
Bands are versioned and activated at a sequence.

The consequence to state plainly: the schedule shape and the banding cannot be
tuned by an operator at runtime. A venue would want to tighten them during a
volatile session; here that is a log-sequenced change, not a dial. Whether that
is the right trade is argued in §7.

## 5.7 What is not covered here

- The liquidation waterfall — partial liquidation, insurance fund, and
  auto-deleveraging — is in §5.4 only as far as the generation bump. The
  mechanism that decides how much to reduce and who absorbs the shortfall is a
  separate design and is not claimed here.
- The replay-rate assumption of §5.5 is unmeasured.
- Cross-datacentre replication is out of scope; everything above assumes a
  single availability zone with the geo case handled the same way the running
  case handles it.
