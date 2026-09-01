# 1. Business context and requirements

## 1.1 The venue

MarginStream is a derivatives venue offering linear perpetual and dated futures
on 40 underlyings, 120 contracts, to APAC retail and institutional clients. All
120 are margined against a single account balance rather than contract by
contract. Leverage is capped at 20×; parameters below assume a median active
account at 8×.

The commercial reason for a unified account is capital efficiency: a client long
one contract and short a correlated one should not fund both legs separately.
Any design that quietly removes the offset removes the reason the venue exists,
so the cost of every conservative step below is stated as a number.

Scale: 10⁶ registered accounts, 10⁵ with open positions, 10⁴ whose positions or
marks change between two issuances. A median account holds 5 contracts; the tail
holds 50.

## 1.2 The requirement that does not decompose

The running case places pre-trade risk before the sequencer because the check is
per-account and per-asset: freezing funds for one order says nothing about any
other book, so it runs per connection and scales horizontally (Part 5 §1).
Matching is sharded by symbol, single writer per shard (Part 2 §3).

A unified cross-margin account removes the first property and leaves the second.
A fill on one contract changes the requirement of positions in other contracts
held by the same account, sitting on shards being written concurrently. The
invariant

> the account's margin requirement must not exceed its equity

is global over the account and non-additive over contracts, yet it must be
enforced before the order reaches the book, inside the same latency budget.

Locking the account, giving each holder a fixed sub-limit with no offset, and
admitting optimistically are the three obvious resolutions; ADR-1 records why
each fails.

§2 keeps matching exactly as the running case has it and moves the difficulty
into a second authority: a margin allocator that runs off the order path and
hands **each ingress gateway** a locally checkable share of the account's
capacity. Matching shards hold no lease and make no margin decision.

## 1.3 Scope

In scope: the margin authority, the admission path, the degradation ladder, the
liquidation and settlement path, and the audit trail for admission decisions.

Taken as given from the running case and cited rather than re-derived: the
matching engine, the replicated log, determinism, and double-entry accounting.
Out of scope: order routing, market making, fiat rails, wallet infrastructure.

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
| 10 | Auditability | **Target:** every admission *and refusal* replayable with the figures it compared. **Today:** admissions only. A refusal never reaches the ordering point, and §2.6's gateway refusal journal is not built |
| 11 | Solvency | **Target:** Σ user liabilities ≤ Σ venue assets, continuously checkable. **Today:** three facts about one account (§4.3). The ledger is unimplemented and the fund unsized, so the venue-level claim is an argument |

Rows 1, 3 and 8 are inherited; 2, 6 and 7 derived in §1.5–1.7; 4, 5 and 9
established in §2, §3 and §5. Rows 10 and 11 are targets, marked as such; §9.4
lists everything designed and not built.

## 1.5 What sets the lease term

A lease is a fixed ceiling a gateway may spend for the length of its term. The
term is the mechanism's principal operational trade-off parameter — the scenario
grid, the add-on parameters, the price bands, the fee caps and the gateway
weights are all chosen too — and it is the one bounded from both sides at once.

**Recompute cost is the floor.** §1.6 works out ≈ 3 × 10⁷ operations per issuance
for the whole book, which is an ordinary workload at a 100 ms cadence and is not
one at 1 ms.

**Tightening latency is the ceiling.** A gateway that cannot be reached keeps
admitting inside its ceiling until the term ends, and no allocator message
changes that — which is what the term is for.

> The lease term is the worst-case enforcement latency of a **tightening**
> decision **under partition**.

Raising a limit takes effect at the next issuance, and so does lowering one when
the allocator can reach the gateways; the term bounds only the unreachable case,
where it bounds everything — collateral cut, downgrade, withdrawal. 50–200 ms is
a **design target chosen against an assumed tightening SLO**, not a figure derived
from any supervisory deadline.

One path does not wait: fencing the lease at the ordering point (§2.5), which is
immediate and needs no gateway to be reachable. The term bounds *routine*
tightening, where fencing every affected lease is a heavy instrument.

An earlier version issued capacity as a schedule contracting with a published
market state. It is withdrawn: see ADR-2 and Appendix A.

## 1.6 What the allocator has to compute, and how often

Per account per issuance: one evaluation of the scenario term at |S| × contracts
≈ 16 × 5 ≈ 80 multiply-adds, one feasibility check of §2.4's condition at the same
order, and a bisection for the scale over 10⁹ minor units at ≈ 30 iterations —
≈ 3 × 10³ operations in all. At 10⁴ accounts changed per issuance that is
**≈ 3 × 10⁷ per issuance** and, at a 100 ms cadence, **≈ 3 × 10⁸ per second**.
Sixteen allocator shards (§2.6) carry ≈ 2 × 10⁷ each: headroom, not a fit.

No invariant spans two accounts, which is what makes the sharding trivial, and
recompute is incremental: unchanged positions and marks keep their ceilings.

## 1.7 What the admission path may do per order

The admission decision must not recompute the requirement from positions. Each
gateway keeps, per account, the running loss numerator under each of the |S|
scenarios plus the worst-fill gross and debit totals, updated on each order state
change. Admitting is one pass over the grid, one symbol's gross update and three
integer comparisons — independent of how many orders are live.

Memory is ≈ 128 B per (account, gateway) pair; at 10⁵ active accounts touching 5
gateways each, ≈ 5 × 10⁵ pairs and ≈ 64 MB resident.

**Measured, not argued.** E3 times the incremental path against a full scan
computing identical envelopes, after checking the two agree on 400 random books.
From the recorded run in `results/e3_hot_path.json`: increasing live orders 10×,
from 50 to 500, changed the incremental median by 1.1%, while the full scan over
the same range grew 6.7×; widening the grid from 7 scenarios to 16 raised the
incremental median by 34%. That is O(|S|) against O(orders × |S|).

**The absolute figures are deliberately not quoted here.** They are wall-clock
nanoseconds from CPython on a shared machine, they differ by a third between the
hosts this has run on, and they are three orders of magnitude from what a
compiled implementation would need. Only the ratios above are evidence, and they
support the scaling claim rather than row 2's latency target, which remains
argued. `results/PROVENANCE.md` records the machine, and `REPRODUCE.md` lists
other hosts under the host that produced them.

## 1.8 What these numbers commit us to

- Three ceilings per gateway per account, checked against absolute figures rather
  than increments, because an incremental charge does not bound the account's
  requirement (§2.4).
- An allocator partitioned by account, ≈ 16 shards at this scale.
- A lease term chosen from recompute cost below and tightening latency above,
  with fencing at the ordering point as the path that does not wait for it.

Each is revisited in §7 with the alternative that lost.
