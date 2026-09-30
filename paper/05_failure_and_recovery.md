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
  price outside the recorded band, or a fee above the cap; **a basket** is one
  record under one identifier, idempotent on retry; **the matching shard**
  applies a client order ID at most once.

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
book and, past the account's equity, to the insurance fund. What limits that is
below, under the waterfall.

### Two ways a cancel fails

| Failure | What the log holds | What the settlement must do |
|---|---|---|
| Acknowledgement recorded, notification to the gateway lost | the cancel | release the order; only the local view is stale |
| Matching side never confirmed | nothing | keep the worst-fill reservation and the execution-cost reserve |

Fencing does not help in the second case: it stops new admissions and does
nothing to a resting order. Both arms of E7 show one order live at the end; the
settlement figure is `(0, 0, 0)` for the recorded cancel and `(120, 2232, 9)` for
the unacknowledged one.

### Releasing capacity afterwards

**A seal releases one lease; a globally fenced, ordered account barrier releases
the whole account.** Neither takes a holder's word or a clock: both read the log.
The barrier has six preconditions, none supplied by the caller (Appendix C.2);
E7 refuses it while any lease — including the liquidator's — is live.

### What the delay costs

E6 splits the equity change exactly — ending equity = trigger equity + drift −
slippage − fees, asserted on integers with no tolerance. Across its delay sweep
execution cost runs 2,522 → 6,486 while market drift runs 16,444 → 229,708. **What
the mechanism controls — new admissions and the cost of unwinding — is bounded;
market drift is not.** Without a fence the unwind's cost exceeds its bound by
1,562. E6's figures are one configuration, one seed, one price path.

### Below the unwind: venue book, insurance fund, ADL

Designed, not built. Each layer is a decision with a price.

| Layer | Decision | What it costs |
|---|---|---|
| Venue book | The venue's own book is margined like an account, against capital the venue allocates to it. It unwinds through ordinary matching, one symbol at a time, with a cap on its share of each book's volume | Slow exits in a thin market. A transfer that would take the book over its limit is not taken; that part goes straight to the next layer |
| Insurance fund | Covers an account's negative ending equity, from the E6 identity. Sized to cover the combined shortfall of the largest accounts under stress moves beyond the scenario set, plus a floor it may not be drawn below | Capital held idle. How many accounts and which stress moves are policy; we give the method and one run, not a calibration |
| ADL | When a draw would take the fund below its floor, the rest is taken from opposing profitable positions at the bankruptcy price, one record per event | Profitable clients lose part of a position they did not choose to close. It is the last resort because it is the only layer that touches clients who did nothing wrong |

### A flash crash (E9)

Session 3's flash-crash tabletop gives the shape — a fall past anything the risk
model covers, a symbol breaker, a halt and an auction reopen, liquidation under
stress. It does not give magnitudes, so E9 picks its own and runs the same
distance, 3.4 widest scenario steps, twice: as a **gap** over three ticks and as
a **slide** over ten. Five long accounts with 950,000 of collateral between them,
a fund of 100,000 and a floor of 20,000. Two reopen prices are run, because the
value of a halt depends on where the auction clears and the run cannot know that.

| Crash | Breaker, auction recovers 60% | Breaker, auction at the low | No breaker |
|---|---|---|---|
| Gap, 3 ticks | no liquidation, no draw | draw 214,848; fund to floor; **ADL 134,848** | draw 214,848; ADL 134,848 |
| Slide, 10 ticks | no liquidation, no draw | draw 214,856; ADL 134,856 | liquidation keeps up; **no draw** |

Three readings. Against a **gap**, liquidation cannot help: prices jump past the
point where equity runs out between two chances to act, and only a recovering
auction saves the fund. Against a **slide**, liquidation alone keeps up — and a
breaker that halts early and reopens at the low makes it *worse*, because a halt
stops the liquidator too. So **a breaker is a bet on the reopen price**, and its
threshold should sit where gradual liquidation stops keeping up, not at the first
sign of a move. And in this configuration a fund of about 235,000 — a quarter of
the collateral — would have avoided ADL in every arm; that is one run's figure,
not a sizing rule. The E6 identity holds in every liquidated account.

What E9 does not model: market-data conflation, reconnect storms and gateway
overload during the crash, which are load problems in front of the design rather
than inside it.

## 5.5 Recovery-time arithmetic

State is not one snapshot: the tiers of Figure 4 fail independently and are
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

Two caveats the arithmetic does not carry: 21 MB/s is a lower bound, counting
order commands and lease inputs but not fills, cancels, fences, baskets, framing
or any replication factor; and the 10× replay rate is **assumed**, not measured
(§8.1).

**Warm failover is a design target, not a result.** Neither replication nor
leader election is implemented (§5.7). What *is* established is the property
failover needs: E4's 3,642 injected crashes show snapshot plus replay reproducing
the state the whole log implies, zero equivalence failures, including 2,364
mid-partial-fill and 819 stale-snapshot cases.

**When an allocator shard fails over, outstanding leases and terms** (designed,
not built):

1. Leases already issued stay valid until their term ends. Gateways keep
   admitting inside them; nothing about a failover makes an issued budget unsafe.
2. The exposure those leases created stays charged to its holders. The new leader
   rebuilds occupancy per holder from the log, as the barrier does, and never from
   what a gateway reports.
3. Before issuing anything, the new leader commits an **epoch fence** at the
   ordering point: registrations from an older epoch are refused. A deposed leader
   that is still running cannot mint a lease, so there is still one issuer per
   account. Admissions under already-registered leases are not refused.
4. It then issues a new generation for every account on the shard.

The arithmetic, per account on the failed shard:

    no-ingress window  ≈  election + catch-up + one issuance − remaining term
    cold rebuild: 5 min of lease inputs at an assumed 10x replay ≈ 30 s
    hot follower applying the log continuously: catch-up ≈ replication lag

Row 8's three seconds is reachable only with hot followers; a cold rebuild
misses it by an order of magnitude. And because a term (50–200 ms) is shorter
than any failover, **the accounts on that shard — one in sixteen — lose order
entry for up to the failover time.** Avoiding that means terms longer than a
failover, which makes a credit cut under partition take seconds instead of
milliseconds. We keep the short term and accept the gap: stopping order entry
for 3 s on a sixteenth of accounts is cheaper than letting a cut credit line keep
trading for 3 s.

## 5.6 Zero-downtime upgrade

Anything a decision is derived from is versioned data on the log, activated at a
sequence (§4.1), so a replay uses the values in force then. Upgrades are rolling
with the state machine version pinned per record, and an operator cannot tune a
derivation mid-session — a real operational loss, and the price of NFR row 10.

## 5.7 What is not covered here

**This is a deterministic simulator, not a deployment.** Everything measured runs
in one process against an in-memory ordering point. A replicated ordering point,
allocator high availability, leader election and real distributed deployment are
*designed* and *not built* (Figure 4); the recovery results establish the
property replication would need, not that replication works. The allocator alone
cannot rebuild itself from the log, and that is not implemented either.

The waterfall below the unwind, the allocator failover protocol and the
mark-price pipeline are designed and not built; E9 exercises the waterfall's
arithmetic in simulation. The replay rate of §5.5 is assumed, and cross-datacentre
replication is not covered.
