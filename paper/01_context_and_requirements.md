# 1. Business context and requirements

## 1.1 The venue

MarginStream is a derivatives venue offering linear perpetual and dated futures
on 40 underlyings, 120 contracts in total, to retail and institutional clients
in APAC. Clients post collateral in a small set of assets, and positions across
all 120 contracts are margined against a single account balance rather than
contract by contract. Leverage is capped at 20×; the parameters below assume a
median active account at 8×.

The commercial reason to offer a unified account is capital efficiency: a client
who is long one contract and short a correlated one should not have to fund both
legs separately. That is the product. Any design that quietly removes the offset
has removed the reason the venue exists, so the cost of every conservative step
taken below is stated as a number rather than absorbed silently.

Scale we design to: 10⁶ registered accounts, 10⁵ with open positions at any
time, 10⁴ whose positions or relevant marks change within one allocator epoch.
A median account holds positions in 5 contracts; the tail holds 50.

## 1.2 The requirement that does not decompose

The running case can place pre-trade risk before the sequencer because the check
is per-account and per-asset: freezing funds for one order says nothing about
any other book, so the check runs per connection and scales horizontally
(OrderStream, Part 5 §1). Matching is then sharded by symbol with a single
writer per shard and no order crossing a shard (Part 2 §3).

A unified cross-margin account removes the first property while leaving the
second in place. A fill on one contract changes the margin requirement of
positions in other contracts held by the same account, and those positions sit
on other shards that are being written concurrently. The invariant

> the account's margin requirement must not exceed its equity

is global over the account and non-additive over contracts, yet it must be
enforced before the order reaches the book, inside the same latency budget.

Three obvious resolutions and why each fails:

- **Lock the account for the duration of the check.** Market-maker accounts are
  the hottest objects in the venue; serialising them on the order path
  reintroduces exactly the hot-row problem the running case rejects for the fee
  account (Part 3 §4).
- **Give each shard a fixed sub-limit and forbid offset.** Correct, and it
  deletes the product. §7 quantifies what it costs.
- **Admit optimistically and repair afterwards.** Violates the rule that a
  balance never goes negative, which is not negotiable for a venue that must
  prove assets ≥ liabilities at any instant.

The design in §2 keeps matching exactly as the running case has it and moves the
difficulty into a second authority — a margin allocator that runs off the order
path and hands each shard a locally checkable share of the account's capacity.

## 1.3 Scope

In scope: the margin authority, the admission path, the degradation ladder, the
liquidation trigger, and the audit trail for admission decisions.

Taken as given from the running case and cited rather than re-derived: the
matching engine and its data structures, the sequencer and the Raft-replicated
log, determinism as a design invariant, and double-entry accounting.

Out of scope and stated as such: order routing, market making, the fiat rails,
and the wallet infrastructure. We do not build a matching engine, train any
model, or attempt to distinguish informed from uninformed flow.

## 1.4 Non-functional requirements

| # | Requirement | Target | Consequence for the design |
|---|---|---|---|
| 1 | Order throughput | 100k/s sustained, 1M/s burst | Admission is a local array operation; no allocator call on the order path |
| 2 | Admission-path latency | p50 < 20 µs, p99 < 200 µs, above matching | Per-account per-shard scenario vector kept resident; marginal requirement is O(\|S\|) |
| 3 | Matching latency | p50 < 100 µs, p99 < 1 ms in-engine | Unchanged; single writer per symbol |
| 4 | Margin correctness | Requirement never exceeds equity at any market state reached within an epoch | Pointwise lease condition (§2.4) |
| 5 | Trigger latency | A shard observes the reduce-only condition on the tick it occurs | Lease read from the market-data path, not from the allocator |
| 6 | Allocator cadence | Epoch 50–200 ms | §1.6 |
| 7 | Allocator throughput | ≈ 4 × 10⁸ scenario operations per epoch | Allocator sharded by account; scenario grid evaluated as a vector |
| 8 | Availability | 99.99% for order entry; failover < 3 s | Raft, as in the running case |
| 9 | Degradation | Risk-reducing orders accepted in every state above HALT | Reserved per-shard capacity |
| 10 | Auditability | Every admission decision replayable together with the lease and market state it saw | Append-only journal of (account, lease, generation, state, decision) |
| 11 | Solvency | Σ user liabilities ≤ Σ venue assets, checkable continuously | Ledger holds bounded by issued leases (§4) |

Rows 1, 3 and 8 are inherited from the running case. Rows 2, 6 and 7 are derived
in §1.5–1.7. Rows 4, 5, 9, 10 and 11 are properties this design has to
establish, and §3, §5 and the evidence appendix are where they are established.

## 1.5 How fine the capacity schedule has to be

The mechanism in §2 issues capacity as a schedule over published market states
rather than as a single number. Between two adjacent states the schedule is
flat, so whatever the account's equity does inside one band is uncovered.

Working from a tolerance rather than from a guess:

1. The band the schedule must span: a 3% adverse move in the index. This is the
   size of a fast move on a normal bad day, not a tail event; tail events are
   handled by HALT and auction reopen, not by capacity shrinkage.
2. Equity moves with leverage. At 10× a 1% index move removes 10% of equity, so
   equity moves ten times the index.
3. Set the tolerance: at most 2% of equity uncovered inside a band. That fixes
   the band at 0.2% of index.
4. Spanning 3% at 0.2% per band gives 15 bands, so K ≈ 16 states.

Sixteen states is also the width of a standard scenario grid, which is
convenient but not the reason: the number falls out of the tolerance in step 3.
Raising leverage to 20× halves the band and doubles K to 32; that is the lever
to pull if the venue lists higher leverage, and it costs memory, not latency,
because the schedule is read by index.

The simulator currently uses K = 4. Every number reported in the evidence
appendix is therefore at a coarser granularity than the design calls for, which
makes the measured exposures upper bounds rather than estimates.

## 1.6 What the allocator has to compute, and how often

Cost of one account's schedule:

- One evaluation of the scenario term is \|S\| × contracts held ≈ 16 × 5 ≈ 80
  multiply-adds for a median account.
- One feasibility check evaluates all K states: 16 × 80 ≈ 1.3 × 10³ operations.
- Solving for the schedule scale by bisection to integer precision over a range
  of 10⁹ minor units takes ≈ 30 iterations: ≈ 4 × 10⁴ operations per account.
- At 10⁴ accounts changed per epoch: ≈ 4 × 10⁸ operations per epoch.
- At a 100 ms epoch: ≈ 4 × 10⁹ operations per second.

That does not fit on one core, and it does not need to. **The allocator has no
invariant that spans two accounts.** Matching is partitioned by symbol because
price-time priority is a total order per book; the allocator is partitioned by
account because margin is an account-level quantity. The two partitionings are
orthogonal, and only the insurance fund and auto-deleveraging are venue-level —
neither of which sits on the admission path. Sixteen allocator shards at 2.5 ×
10⁸ operations per second each is an ordinary workload.

Two consequences to carry into §2. Recompute is incremental: an account whose
positions are unchanged and whose contracts have not moved outside their current
band keeps its schedule. And the scenario grid is evaluated as a vector across
scenarios, not as a loop over positions, because the inner loop is the same
16-wide operation for every account.

Note what sets the epoch. It is not how fast the market moves — the schedule
already absorbs movement inside the epoch, which is the point of §2.4. It is how
fast positions change enough that the schedule issued at the start of the epoch
no longer reflects the portfolio. 50–200 ms follows from the recompute cost
above and from the trade-stream lag, not from a market argument. This is a
reversal of the usual reasoning and it is worth stating plainly, because a
reviewer will expect the epoch to be justified by volatility.

## 1.7 What the admission path may do per order

The admission decision must not recompute the requirement from positions. For
each (account, shard) pair with open positions, the shard keeps the running loss
under each of the \|S\| scenarios. Admitting an order is then:

1. add the order's contribution to each of the \|S\| entries — 16 multiply-adds;
2. take the maximum — 16 comparisons;
3. read the schedule at the current state index — one array access;
4. compare and decrement — two operations.

Tens of nanoseconds, and no dependence on how many contracts the account holds.
Memory is \|S\| × 8 bytes = 128 B per (account, shard) pair; at a median of 5
contracts an account touches at most 5 shards, so ≈ 5 × 10⁵ pairs and ≈ 64 MB
resident across the venue.

This is the Session 2 argument moved to the margin path: the structure follows
from the access pattern. A general portfolio-valuation call per order would be
the margin-path equivalent of a pointer-chasing tree at the top of book.

## 1.8 What these numbers commit us to

- A scenario-based requirement with a fixed grid, because the admission-path
  cost in §1.7 depends on \|S\| being small and constant. A requirement that
  needs a full revaluation per order is incompatible with row 2.
- A schedule with K ≈ 16 states, and memory rather than latency as the cost of
  raising leverage.
- An allocator partitioned by account, sized at ≈ 16 shards for the scale in
  §1.1.
- An epoch chosen from recompute cost and trade-stream lag, with the market-move
  argument discharged by the schedule instead.

Each of these is revisited in §7 with the alternative that was considered and
the reason it lost.
