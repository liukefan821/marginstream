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

Every link is a named identifier and a decision point that has been tested. The
full nine-step chain is in Appendix C; the shape is:

- **the client** retries on the same client order ID, because a timeout is an
  unknown outcome and not a failure;
- **the gateway** submits under `(lease_id, admission_seq)`, and the ordering
  point takes only the next number for that lease, so the recorded sequence is
  gap-free — which is what later lets a seal claim to cover every admission;
- **the ordering point decides first.** Only a fill it accepted is folded into
  the gateway and the account. An earlier implementation called the gateway
  first and moved state the authority then refused;
- **a fill** is refused, writing nothing and moving nothing, on a reused
  identifier with different figures, an unknown or cancelled order, the wrong
  direction, an over-fill, a price outside the band recorded at admission, or a
  fee above the cap;
- **a basket** is one record under one identifier, idempotent on retry and
  refused on a conflicting payload;
- **replay is idempotent by log position**, not by order ID — which covers
  admissions and not fills or cancels, and r2 caught that;
- **the matching shard** applies a client order ID at most once.

Two costs stated rather than hidden. A retry routed through another gateway is a
new admission attempt there and may consume envelope, released at the next
issuance. And a cancel *request* releases nothing; only an acknowledgement
recorded at the ordering point does, which produces the two failures §5.4 keeps
apart.

## 5.4 Fencing, liquidation and settlement

An account cannot reach a requirement above its equity through any move the
scenario grid covers; that is what the closure of §2.4 reserves for. So a
liquidation is by construction an event outside the model, the credit event has
already happened by the time anyone can see it, and the mechanism is not
preventing a violation. It is limiting a loss, and what it limits is the draw on
the insurance fund once the account's own equity is gone.

### Three things have to stop, in three different places

| What | Where it stops | What it costs |
|---|---|---|
| New admissions | a fence at the ordering point | one write; no gateway has to be reachable |
| Resting orders filling | a cancel acknowledged at the ordering point | one round trip per order, and it can fail (below) |
| The position moving | trading it out | as long as the unwind takes |

Only the first is immediate, and only the first works under partition. E7's
`fence_undelivered` arm takes the fence and tells no gateway: the ordering point
turns away 50 submissions and the run is otherwise identical to the base.

### The unwind is an internal transfer, not orders on the books

Reducing one leg at a time does not work. On a hedged book, closing one leg while
its offset stays put raises the account's requirement — c9 again, with the
liquidator in the gateway's place. `tests/test_liquidation.py` l3 measures the
requirement going from 0 to 2,000 on a single-leg proposal and staying at 0 on a
proportional basket.

That forces a basket, and a basket forces an architecture decision, because
matching here is sharded by symbol. **A basket spanning several symbols cannot
fill atomically across several order books**, and a partial fill on one shard
with none on another leaves the account somewhere the check never approved. This
design does not assume an atomicity the matching path does not have and does not
route baskets through it. Instead the liquidator prices the whole basket against
the venue's own marks, inside the same band and fee cap an ordinary fill is held
to, and the ordering point commits it as one record. Either the record is there
and the whole basket happened, or it is not and none of it did.

**The cost is that the venue is the counterparty to that transfer.** Risk moves
from the account to the venue's own book and, past the account's equity, to the
insurance fund. Venue-side portfolio risk and capital limits for that book are
not modelled anywhere in this document. Nothing here claims sharded matching
supports atomic cross-symbol baskets.

### Two ways a cancel fails, which are not the same fact

| Failure | What the log holds | What the settlement must do |
|---|---|---|
| The acknowledgement was recorded and the notification to the gateway was lost | the cancel | release the order. Nothing can fill against it; only the local view is stale |
| The matching side never confirmed | nothing | keep the order's worst-fill reservation and its execution-cost reserve. It is still able to fill |

Fencing does not help in the second case: it stops new admissions and does
nothing to an order already resting. l12 and l13 pin them separately and E7 runs
them as separate faults, because the difference is not in what the gateways show
— both show one order live — but in what the log holds. The settlement figure is
`(0, 0, 0)` for the recorded cancel and `(120, 2232, 9)` for the unacknowledged
one.

### Two kinds of release

**A seal releases one lease.** It is portable evidence: the ordering point issues
it when a lease is fenced, naming the last admission it recorded, and a holder
carries it to the allocator. A release is refused unless the lease is fenced, the
seal is the one issued for that lease, and its terminal sequence matches. The
figures come from the ordering point's log, not from whoever asks for the
release; an earlier version accepted a correct seal paired with an optimistic
usage claim.

**A barrier compacts the whole account.** Per-holder occupancy is summed and does
not net, which is necessary while any holder may still be acting and badly wrong
once none is: after a liquidation each ingress lease reconciles to its own gross
leg, the offsetting legs sit under the liquidator, and an account holding nothing
still looks fully occupied. The account-wide path does not use a seal, because
the allocator reads the same log the seal was cut from, so an undelivered seal
does not freeze an account's capacity. What it rests on is the terminal fence the
ordering point has already recorded.

The rule, stated so the two are not confused:

> A seal releases one lease. A globally fenced, ordered account barrier permits
> account-wide compaction.

Compaction requires six conditions, none of them supplied by the caller:
issuance stopped for the account; every lease it ever held fenced, including
every incarnation and the liquidator's own basket authority; a barrier watermark
at which the recorded sequence is gap-free and no admission was made under an
unregistered lease; the occupancy rebuilt from the log at that watermark; a
credit-version compare-and-set on the install; and issuance resumed only after
it. Appendix C states them in full.

E7 exercises the refusals as well as the successes: `no_fence` is refused with
two ingress leases live, and `liquidator_authority_live` is refused with the
liquidator's lease live even though every ingress lease is fenced and the account
is flat.

### What the mechanism bounds during the delay, and what it does not

E6 decomposes the equity change exactly and asserts the identity on integers with
no tolerance:

    ending equity == trigger equity + drift - slippage - fees

Across the delay sweep, execution cost runs from 2,522 to 6,486 while drift runs
from 16,444 to 229,708. **The two things the mechanism controls — new admissions,
and the execution cost of unwinding what is there — are bounded. Market drift is
not.** In E6 it grows linearly with the delay, but that is a property of the
constant-rate price path the experiment uses, not a result: on any other path the
statement is only that nothing in the mechanism bounds it. The unwind's cost sits inside the bound
taken over the reachable position at the moment authority ends, and outside any
bound taken at the moment the shortfall was detected, because everything admitted
during the detection delay is inside the first and outside the second. In the
unfenced arm there is no moment at which authority ends and the cost exceeds the
bound by 1,562.

The required-buffer figures E6 reports are measurements of one configuration and
one seed along one price path. They are not a bound, not an upper bound, and not
a probabilistic guarantee, and §7 does not use them as one.

## 5.5 Recovery-time arithmetic

Two different events with two different budgets.

**Warm failover** — a leader is lost and a follower takes over. Followers apply
the same log and hold the same books, so there is no rebuild; the cost is the
election plus the commit catch-up on the tail. The budget is row 8: under 3
seconds. The allocator fails over the same way, and because the log carries the lease
inputs rather than the leases, a new allocator leader recomputes identical
ceilings from the same records. That is the design; the allocator's own snapshot
and failover are not implemented, and §5.7 says so.

Behaviour during the window matters more than its length. Gateways keep the
ceilings they hold and continue to admit within them; when the terms expire and
no new generation arrives, every gateway stops admitting entirely. It does not
fall back on admitting orders that look locally like risk reduction, because c9
shows that judgement is unsound across gateways. Risk reduction during that
window is the liquidation path's job (§3.3), which is the property §3 defends in
business terms — and it means the failover budget and the term length interact:
a failover longer than the shortest outstanding term leaves accounts with no
ingress at all until issuance resumes.

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

Three boundaries, stated here rather than left for a reviewer to find.

**This is a deterministic simulator, not a deployment.** Everything measured in
this document runs in one process against an in-memory ordering point. A
replicated ordering point, allocator high availability, leader election and a
real distributed deployment are *designed* in this document and *not built*. The
recovery results are about the fold from the log being deterministic and
idempotent, which is the property replication would need, and they are not
evidence that the replication works.

**The allocator's own crash and failover are not implemented.** The gateway, the
account and the settlement path all rebuild from the log and are tested doing so.
The allocator does not, and a settlement installed under a credit-version
compare-and-set is a mechanism for ordering competing installs, not a substitute
for that missing work.

**The liquidation transfer moves risk onto the venue.** The basket is an internal
transfer with the venue as counterparty (§5.4), so the venue's own book absorbs
what the account cannot. Venue-side portfolio risk, capital limits on that book,
and the insurance fund's sizing are outside this document entirely.

- The liquidation waterfall beyond the unwind itself. §5.4 covers fencing,
  cancellation, the basket transfer and the settlement barrier. How much to
  reduce and in what order, auto-deleveraging, and who absorbs a shortfall the
  insurance fund cannot are a separate design and are not claimed here.
- The replay-rate assumption of §5.5 is unmeasured.
- Cross-datacentre replication is out of scope; everything above assumes a
  single availability zone with the geo case handled the same way the running
  case handles it.
