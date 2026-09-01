# 1. Business context and requirements

## 1.1 The venue

MarginStream is a derivatives venue offering linear perpetual and dated futures
on 40 underlyings, 120 contracts, to APAC retail and institutional clients.
Positions across all 120 contracts are margined against a single account balance
rather than contract by contract. Leverage is capped at 20×; parameters below
assume a median active account at 8×.

The commercial reason to offer a unified account is capital efficiency: a client
long one contract and short a correlated one should not have to fund both legs
separately. That is the product. Any design that quietly removes the offset has
removed the reason the venue exists, so the cost of every conservative step taken
below is stated as a number rather than absorbed silently.

Scale: 10⁶ registered accounts, 10⁵ with open positions, 10⁴ whose positions or
marks change between two issuances. A median account holds 5 contracts; the tail
holds 50.

## 1.2 The requirement that does not decompose

The running case can place pre-trade risk before the sequencer because the check
is per-account and per-asset: freezing funds for one order says nothing about any
other book, so the check runs per connection and scales horizontally
(OrderStream, Part 5 §1). Matching is then sharded by symbol with a single writer
per shard and no order crossing a shard (Part 2 §3).

A unified cross-margin account removes the first property while leaving the
second in place. A fill on one contract changes the margin requirement of
positions in other contracts held by the same account, and those positions sit on
other shards being written concurrently. The invariant

> the account's margin requirement must not exceed its equity

is global over the account and non-additive over contracts, yet it must be
enforced before the order reaches the book, inside the same latency budget.

Locking the account, giving each holder a fixed sub-limit with no offset, and
admitting optimistically are the three obvious resolutions; ADR-1 records why
each fails.

The design in §2 keeps matching exactly as the running case has it and moves the
difficulty into a second authority: a margin allocator that runs off the order
path and hands **each ingress gateway** a locally checkable share of the
account's capacity. Matching shards hold no lease and make no margin decision.

## 1.3 Scope

In scope: the margin authority, the admission path, the degradation ladder, the
liquidation and settlement path, and the audit trail for admission decisions.

Taken as given from the running case and cited rather than re-derived: the
matching engine, the replicated log, determinism as a design invariant, and
double-entry accounting. Out of scope: order routing, market making, fiat rails
and wallet infrastructure. We do not build a matching engine or attempt to
distinguish informed from uninformed flow.

## 1.4 Non-functional requirements

| # | Requirement | Target | Consequence for the design |
|---|---|---|---|
| 1 | Order throughput | 100k/s sustained, 1M/s burst | Admission is a local array operation; no allocator call on the order path |
| 2 | Admission-path latency | p50 < 20 µs, p99 < 200 µs, above matching | Per-account per-gateway scenario vector kept resident. Argued, not measured: E3 supports the scaling, not the target |
| 3 | Matching latency | p50 < 100 µs, p99 < 1 ms in-engine | Unchanged; single writer per symbol |
| 4 | Margin correctness | Requirement never exceeds equity after any move the scenario grid covers | The closure of §2.4, with the factor of two shown tight |
| 5 | Tightening latency | Binds within the lease term under partition, immediately when the ordering point is reachable | Fence at the ordering point; §1.5 |
| 6 | Allocator cadence | Issuance every 50–200 ms | §1.5, §1.6 |
| 7 | Allocator throughput | ≈ 3 × 10⁷ scenario operations per issuance | Sharded by account; grid evaluated as a vector |
| 8 | Availability | 99.99% for order entry; failover < 3 s | Replicated log, as in the running case |
| 9 | Degradation | The venue can bound its own loss in every state above HALT. Client-initiated risk reduction is **not** preserved under partition | Venue-initiated liquidation (§5.4); §3.3 states what is given up |
| 10 | Auditability | Every admission decision replayable from the log with the ceilings and figures it compared | Ordering point's log plus the gateway's journal (§2.6) |
| 11 | Solvency | Σ user liabilities ≤ Σ venue assets, checkable continuously | §4.3 |

Rows 1, 3 and 8 are inherited. Rows 2, 6 and 7 are derived in §1.5–1.7. The rest
are established in §2, §3 and §5.

## 1.5 What sets the lease term

A lease is a fixed ceiling a gateway may spend for the length of its term. The
term is the only free number in the mechanism, and it is bounded from both sides.

**Recompute cost is the floor.** §1.6 works out ≈ 3 × 10⁷ operations per issuance
for the whole book, which is an ordinary workload at a 100 ms cadence and is not
one at 1 ms.

**Tightening latency is the ceiling.** A gateway that cannot be reached keeps
admitting inside its ceiling until the term ends, and no allocator message
changes that — which is what the term is for.

> The lease term is the worst-case enforcement latency of a **tightening**
> decision **under partition**.

Raising a limit takes effect at the next issuance, and so does lowering one when
the allocator can reach the gateways. The term bounds only the unreachable case,
and there it bounds everything: a collateral cut, a downgrade, a withdrawal.
50–200 ms is a **design target chosen against an assumed tightening SLO**, not a
figure derived from any supervisory deadline; a venue with a specific obligation
must set the term below it.

One path does not wait: fencing the lease at the ordering point (§2.5), which is
immediate and needs no gateway to be reachable. The term bounds *routine*
tightening, where fencing every affected lease is a heavy instrument.

An earlier version issued capacity as a schedule contracting with a published
market state. It is withdrawn: see ADR-2 and Appendix A.

## 1.6 What the allocator has to compute, and how often

Per account, per issuance:

- one evaluation of the scenario term: |S| × contracts held ≈ 16 × 5 ≈ 80
  multiply-adds for a median account;
- one feasibility check of the condition of §2.4: the same order, ≈ 10²;
- solving for the scale by bisection to integer precision over 10⁹ minor units,
  ≈ 30 iterations: ≈ 3 × 10³ operations.

At 10⁴ accounts changed per issuance that is **≈ 3 × 10⁷ operations per
issuance**, and at a 100 ms cadence **≈ 3 × 10⁸ per second**. Sixteen allocator
shards (§2.6) carry ≈ 2 × 10⁷ each, which is headroom rather than a fit.

The allocator has no invariant spanning two accounts, which is what makes the
sharding trivial. Recompute is incremental: an account whose positions and marks
have not changed keeps its ceilings.

## 1.7 What the admission path may do per order

The admission decision must not recompute the requirement from positions. Each
gateway keeps, per account, the running loss numerator under each of the |S|
scenarios plus the worst-fill gross and debit totals, updated on each order state
change. Admitting an order is one pass over the grid, one symbol's gross update,
and three integer comparisons — no dependence on how many orders are live.

Memory is |S| × 8 bytes plus a small per-symbol tally, ≈ 128 B per
(account, gateway) pair; at 10⁵ active accounts touching 5 gateways each,
≈ 5 × 10⁵ pairs and ≈ 64 MB resident.

**Measured, not argued.** E3 times the incremental path against a full scan
computing identical envelopes, after checking they agree on 400 random books.
Incremental admission is flat in the order count — 7,434 ns at 50 live orders and
7,672 at 500 on a 7-scenario grid — while the full scan grows 7.5× over the same
range and the incremental path grows 1.4× when the grid widens 2.3×. That is
O(|S|) against O(orders × |S|), shown by the scaling.

The absolute figures are CPython on a shared machine. **They support the scaling
claim and not row 2's latency target**, which remains argued.

## 1.8 What these numbers commit us to

- Three ceilings per gateway per account, checked against absolute figures rather
  than increments, because an incremental charge does not bound the account's
  requirement (§2.4).
- An allocator partitioned by account, ≈ 16 shards at this scale.
- A lease term chosen from recompute cost below and tightening latency above,
  with fencing at the ordering point as the path that does not wait for it.

Each is revisited in §7 with the alternative that lost.
