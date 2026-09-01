# 5. Failure and recovery

## 5.1 What has to survive a node loss

| State | Owner | Size at the scale of §1.1 | Rebuilt from |
|---|---|---|---|
| Order books | Matching shard | ≈ 160 MB per shard at 8 shards | Snapshot plus log tail |
| Per-account positions, equity, ceilings, generation | Allocator shard | ≈ 27 MB total | Snapshot plus log tail. **Not implemented** (§5.7) |
| Per-(account, gateway) scenario vectors and gross totals | Gateway | ≈ 64 MB total | Derived; not snapshotted |
| Balances | Ledger | ≈ 100 MB | Journal fold |

The scenario vectors are a cache of a pure function of the order state, so they
are rebuilt rather than restored: reconstructing 128 bytes from a handful of
orders is faster than reading it from disk, and snapshotting them would double
the snapshot and buy nothing.

## 5.2 What must be on the replicated log

§2.7 lists what an admission decision's replayability puts on the log and why the
ceilings are derived rather than logged. The requirement is stronger than the
running case's because three components must reconstruct the same facts without
reaching each other, and two consequences carry into the rest of this section:
**a snapshot bounds replay time and is never a correctness requirement**, and
**replay is idempotent by log position** rather than by order ID — which covers
admissions but not fills or cancels, and r2 caught that.

## 5.3 The idempotency chain

The full nine-step chain is Appendix C.1. Its shape:

- **the client** retries on the same client order ID; a timeout is an unknown
  outcome, not a failure;
- **the gateway** submits under `(lease_id, admission_seq)`, and the ordering
  point takes only the next number for that lease, so the recorded sequence is
  gap-free — which is what later lets a seal cover every admission;
- **the ordering point decides first.** Only a fill it accepted is folded into
  the gateway and the account; an earlier implementation called the gateway first
  and moved state the authority then refused;
- **a fill** is refused, writing nothing, on a reused identifier with different
  figures, an unknown or cancelled order, the wrong direction, an over-fill, a
  price outside the recorded band, or a fee above the cap;
- **a basket** is one record under one identifier, idempotent on retry;
- **the matching shard** applies a client order ID at most once.

Two costs stated rather than hidden. A retry routed through another gateway is a
new admission attempt there and may consume envelope, released at the next
issuance. And a cancel *request* releases nothing; only an acknowledgement
recorded at the ordering point does, which produces the two failures below.

## 5.4 Fencing, liquidation and settlement

An account cannot reach a requirement above its equity through any move the grid
covers; that is what the closure reserves for. **A liquidation is therefore an
event outside the model, the credit event has already happened by the time anyone
sees it, and the mechanism is limiting a loss rather than preventing a
violation.** What it limits is the draw on the insurance fund once the account's
own equity is gone.

### Three things stop, in three places

| What | Where | Cost |
|---|---|---|
| New admissions | a fence at the ordering point | one write; no gateway need be reachable |
| Resting orders filling | a cancel acknowledged at the ordering point | a round trip per order, and it can fail |
| The position moving | trading it out | as long as the unwind takes |

### The unwind is an internal transfer

Reducing one leg at a time does not work: on a hedged book, closing one leg while
its offset stays put raises the account's requirement — c9 with the liquidator in
the gateway's place. l3 measures the requirement going 0 → 2,000 on a single-leg
proposal and staying at 0 on a proportional basket.

That forces a basket, and matching is sharded by symbol, so **a basket spanning
several symbols cannot fill atomically across several books**; a partial fill on
one shard with none on another leaves the account somewhere the check never
approved. This design does not assume an atomicity the matching path lacks and
does not route baskets through it. The liquidator prices the whole basket against
the venue's own marks, inside the same band and fee cap an ordinary fill is held
to, and the ordering point commits it as one record: either the record is there
and the whole basket happened, or none of it did.

**The cost is that the venue is the counterparty.** Risk moves to the venue's own
book and, past the account's equity, to the insurance fund. Venue-side portfolio
risk and capital limits for that book are not modelled in this document.

### Two ways a cancel fails

| Failure | What the log holds | What the settlement must do |
|---|---|---|
| Acknowledgement recorded, notification to the gateway lost | the cancel | release the order; only the local view is stale |
| Matching side never confirmed | nothing | keep the worst-fill reservation and the execution-cost reserve |

Fencing does not help in the second case: it stops new admissions and does
nothing to a resting order. Both arms of E7 show one order live at the end; the
settlement figure is `(0, 0, 0)` for the recorded cancel and `(120, 2232, 9)` for
the unacknowledged one.

### Two kinds of release

**A seal releases one lease** — portable evidence a holder carries to the
allocator, refused unless the lease is fenced and the seal's terminal sequence
matches, with the figures taken from the log rather than from whoever asks.

**A barrier compacts the whole account.** Per-holder occupancy is summed and does
not net — necessary while any holder may still be acting, and badly wrong once
none is: after a liquidation each ingress lease reconciles to its own gross leg,
the offsetting legs sit under the liquidator, and an account holding nothing looks
fully occupied. The account-wide path uses no seal, because the allocator reads
the same log the seal was cut from, so an undelivered seal freezes nothing.

> A seal releases one lease. A globally fenced, ordered account barrier permits
> account-wide compaction.

Compaction requires six conditions, none supplied by the caller: issuance stopped
for the account; every lease it ever held fenced, including every incarnation and
the liquidator's own basket authority; a barrier watermark at which the recorded
sequence is gap-free and no admission was made under an unregistered lease; the
occupancy rebuilt from the log at that watermark; a credit-version
compare-and-set on the install; and issuance resumed only afterwards. Appendix
C.2 states them in full. E7 refuses `no_fence` with two ingress leases live and
`liquidator_authority_live` with the liquidator's lease live, and settles
`seal_undelivered`.

### What the delay costs

E6 decomposes the equity change exactly and asserts the identity on integers with
no tolerance:

    ending equity == trigger equity + drift - slippage - fees

Across the delay sweep execution cost runs 2,522 → 6,486 while drift runs
16,444 → 229,708. **The two things the mechanism controls — new admissions and
the execution cost of unwinding — are bounded. Market drift is not.** In E6 it
grows linearly with the delay, but that is a property of the constant-rate price
path used, not a result. The unwind's cost sits inside the bound taken over the
reachable position **when authority ends**, and outside any bound taken when the
shortfall was detected, because everything admitted during the detection delay is
inside the first and outside the second. In the unfenced arm no such moment
exists and the cost exceeds the bound by 1,562.

The required-buffer figures E6 reports are measurements of one configuration, one
seed and one price path. They are not a bound.

## 5.5 Recovery-time arithmetic

State is not one snapshot: the tiers of Appendix B fail independently and are
sized separately.

| Tier | Snapshot | Log tail over 5 min | Conclusion |
|---|---|---|---|
| Matching shard | ≈ 160 MB (one shard of eight) | orders and fills for that shard's symbols | Dominated by the snapshot read, then replay |
| Allocator shard | ≈ 27 MB total across sixteen shards | lease inputs, ≈ 8 MB/s | Small enough that replay dominates |
| Gateway | none — its state is derived (§5.1) | admissions, fills, cancels for its own lease | Rebuild is pure replay |

The one figure that can be checked from this document is the replay volume:

    5 min at ≈ 21 MB/s (§2.7) = 6.3 GB;
    at an assumed 10x live replay rate, the log tail takes about 30 s,
    excluding snapshot fetch and leader election.

Two caveats that the arithmetic does not carry. The 21 MB/s is a lower bound: it
counts order commands and lease inputs and excludes fills, cancels, fences,
baskets, framing, and any replication factor. And the 10× replay rate is
**assumed**, not measured — it is the first chaos experiment (§8.1).

**Warm failover is a design target, not a result.** Row 8's three seconds would
be election plus commit catch-up on the tail, and neither replication nor leader
election is implemented (§5.7), so there is no measurement to derive it from.
What *is* established is the property failover would need:
`tests/test_recovery.py` and E4's 3,642 injected crashes show snapshot plus
replay reproducing the state the whole log implies, with zero equivalence
failures, including 2,364 mid-partial-fill and 819 stale-snapshot cases.

During any such window gateways keep their ceilings and admit inside them; when
terms expire they admit nothing at all, and risk reduction is the liquidation
path's job (§3.3). A failover longer than the shortest outstanding term therefore
leaves accounts with no ingress.

## 5.6 Zero-downtime upgrade

Anything a decision is derived from is versioned data on the log, activated at a
sequence (§4.1), so a replay uses the values in force then. Upgrades are rolling
with the state machine version pinned per record, and an operator cannot tune a
derivation mid-session — a real operational loss, and the price of NFR row 10.

## 5.7 What is not covered here

**This is a deterministic simulator, not a deployment.** Everything measured runs
in one process against an in-memory ordering point. A replicated ordering point,
allocator high availability, leader election and real distributed deployment are
*designed* here and *not built* (Appendix B). The recovery results establish that
the fold from the log is deterministic and idempotent — the property replication
would need — and not that replication works.

**The allocator's own crash and failover are not implemented.** The gateway, the
account and the settlement path rebuild from the log and are tested doing so; the
allocator does not.

**The liquidation transfer moves risk onto the venue** (above), and venue-side
capital limits and insurance-fund sizing are outside this document.

Also out: the waterfall beyond the unwind — how much to reduce, in what order,
auto-deleveraging, who absorbs a shortfall the fund cannot — the replay rate of
§5.5, and cross-datacentre replication.
