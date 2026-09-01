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
| 2 | Admission-path latency | p50 < 20 µs, p99 < 200 µs, above matching | Per-account per-gateway scenario vector kept resident; the check is O(\|S\|) in the grid and O(1) in live orders. Argued, not measured: E3 supports the scaling, not the target |
| 3 | Matching latency | p50 < 100 µs, p99 < 1 ms in-engine | Unchanged; single writer per symbol |
| 4 | Margin correctness | Requirement never exceeds equity after any move the scenario grid covers | The closure of §2.4, with the factor of two shown tight |
| 5 | Tightening latency | A tightening binds within the lease term under partition, immediately when the ordering point is reachable | Fence at the ordering point; §1.5 |
| 6 | Allocator cadence | Issuance every 50–200 ms | §1.5, §1.6 |
| 7 | Allocator throughput | ≈ 3 × 10⁷ scenario operations per issuance | Allocator sharded by account; scenario grid evaluated as a vector |
| 8 | Availability | 99.99% for order entry; failover < 3 s | Raft, as in the running case |
| 9 | Degradation | The venue can bound its own loss in every state above HALT. Client-initiated risk reduction is **not** preserved under partition | Venue-initiated liquidation (§5.4); §3.3 states what is given up |
| 10 | Auditability | Every admission decision replayable from the log together with the ceilings and figures it compared | Ordering point's log plus the gateway's own journal (§2.6) |
| 11 | Solvency | Σ user liabilities ≤ Σ venue assets, checkable continuously | Ledger holds bounded by issued leases (§4) |

Rows 1, 3 and 8 are inherited from the running case. Rows 2, 6 and 7 are derived
in §1.5–1.7. Rows 4, 5, 9, 10 and 11 are properties this design has to
establish, and §3, §5 and the evidence appendix are where they are established.

## 1.5 What sets the lease term

A lease is a fixed ceiling that a gateway may spend for the length of its term.
The term is the only number in the mechanism that has to be chosen, and it is
chosen from two constraints, not one.

**The recompute cost puts a floor under it.** §1.6 works out that re-solving the
whole book costs about 4 × 10⁸ operations per issuance, which is an ordinary
workload for sixteen shards at a 100 ms cadence and is not one at 1 ms.

**The enforcement latency puts a ceiling on it.** A gateway that cannot be
reached keeps admitting inside its ceiling until the term ends, and no message
from the allocator changes that — which is what the term is for. So:

> The lease term is the worst-case enforcement latency of a **tightening**
> decision **under partition**.

Raising a limit takes effect at the next issuance; lowering one does too when the
allocator can reach the gateways. It is only the unreachable case the term
bounds, and there it bounds everything: a collateral cut, a credit downgrade, a
withdrawal. A venue that needs a tightening to bind within X must set the term
below X, and 50–200 ms is chosen because it sits comfortably under any
supervisory or operational deadline a venue is likely to face while still
leaving the recompute affordable.

There is one path that does not wait for the term: fencing the lease at the
ordering point (§2.5). It is immediate and needs no gateway to be reachable, and
it is what liquidation uses. The term is what bounds *routine* tightening, where
fencing every affected lease would be a heavy instrument.

**Withdrawn.** An earlier version of this section derived K ≈ 16 market-state
bands from a 3% index span, 10× leverage and a 2%-of-equity tolerance, for a
capacity schedule that contracted as the market moved. The schedule provides no
safety and no capacity benefit under the corrected condition and has been
withdrawn (ADR-2); the derivation goes with it. The scenario grid is a separate
object and is unaffected.

## 1.6 What the allocator has to compute, and how often

Cost of one account's issuance:

- One evaluation of the scenario term is \|S\| × contracts held ≈ 16 × 5 ≈ 80
  multiply-adds for a median account.
- One feasibility check evaluates the condition of §2.4 once: the same order of
  magnitude, ≈ 10² operations.
- Solving for the scale by bisection to integer precision over a range of 10⁹
  minor units takes ≈ 30 iterations: ≈ 3 × 10³ operations per account.
- At 10⁴ accounts changed per issuance: ≈ 3 × 10⁷ operations per issuance.
- At a 100 ms cadence: ≈ 3 × 10⁸ operations per second.

That fits on a handful of cores, and it does not need to fit on one. **The
allocator has no invariant that spans two accounts.** Matching is partitioned by
symbol because price-time priority is a total order per book; the allocator is
partitioned by account because margin is an account-level quantity. The two
partitionings are orthogonal, and only the insurance fund and auto-deleveraging
are venue-level — neither of which sits on the admission path.

Dropping the schedule takes a factor of K out of this arithmetic, which is why
these figures are an order of magnitude below the earlier draft's. The sixteen
shards of §2.6 are sized for headroom, not for this number.

Recompute is incremental: an account whose positions have not changed and whose
marks have not moved keeps its ceilings.

## 1.7 What the admission path may do per order

The admission decision must not recompute the requirement from positions. Each
gateway keeps, per account, the running loss numerator under each of the \|S\|
scenarios plus the worst-fill gross and debit totals, and updates them on each
order state change. Admitting an order is then:

1. add the order's contribution to each of the \|S\| entries and take the
   maximum — one pass over the grid;
2. update one symbol's gross contribution — constant work;
3. compare three integers against three ceilings.

No dependence on how many orders the account has live. Memory is \|S\| × 8 bytes
for the scenario vector plus a small per-symbol tally, ≈ 128 B per
(account, gateway) pair; at a median of 5 gateways touched that is ≈ 5 × 10⁵
pairs and ≈ 64 MB resident across the venue.

**This is measured rather than argued.** E3 times both the incremental path and a
full scan that computes the identical envelopes, after checking they agree on 400
random books. On the recording machine, incremental admission is flat in the
order count — 7,434 ns at 50 live orders and 7,672 at 500 on a 7-scenario grid —
while the full scan grows by a factor of 7.5 over the same range and the
incremental path grows by 1.4 when the grid widens by 2.3. That is O(\|S\|)
against O(orders × \|S\|), shown by the scaling.

The absolute figures are CPython on a shared machine and are three orders of
magnitude away from what a compiled implementation would need to hit a
microsecond budget. **They support the scaling claim and not a latency target**,
and an independent run on different hardware reproduced the scaling with the
absolute figures 20% lower (`REPRODUCE.md`). A latency budget for this path
remains argued, not measured.

## 1.8 What these numbers commit us to

- A scenario-based requirement with a fixed grid, because the admission-path
  cost in §1.7 depends on \|S\| being small and constant. A requirement that
  needs a full revaluation per order is incompatible with row 2.
- Three ceilings per gateway per account, all checked against absolute figures
  rather than increments, because an incremental charge does not bound the
  account's requirement (§2.4).
- An allocator partitioned by account, sized at ≈ 16 shards for the scale in
  §1.1.
- A lease term chosen from recompute cost below and tightening latency above,
  with fencing at the ordering point as the path that does not wait for it.

Each of these is revisited in §7 with the alternative that was considered and
the reason it lost.
