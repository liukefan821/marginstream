# 1. Business context and requirements

## 1.1 The venue

MarginStream is a derivatives venue offering linear perpetual and dated futures on
40 underlyings, 120 contracts, to APAC retail and institutional clients, all
margined against a single account balance rather than contract by contract.
Leverage is capped at 20×; parameters below assume a median active account at 8×.
The commercial reason for a unified account is capital efficiency — a client long
one contract and short a correlated one should not fund both legs separately — so
any design that quietly removes the offset removes the reason the venue exists,
and the cost of every conservative step below is stated as a number.

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

**Where this sits relative to the nearest candidate system.** MarginStream is
candidate 3 — a real-time risk and liquidation engine for a margin venue — with
a harder core. Candidate 3 assumes the account-level check has already been
serialised somewhere; here it cannot be, because the books it depends on are
sharded by symbol and written concurrently. Candidate 3's obligations still
apply and are met here: the mark-price pipeline (§2.5), the liquidation
waterfall from unwind through the venue book and insurance fund to ADL (§5.4),
and a flash crash run as a test vector (E9, §5.4).

## 1.3 Scope

Designed here: the venue's requirements, the margin authority and admission
path, the consistency map, the derivatives accounting model, the degradation
ladder, recovery, the liquidation waterfall, the threat model, operations and
the target deployment. Out of scope: order routing, market making, fiat rails,
wallet infrastructure.

Four things are taken from the running case and cited rather than re-derived.
What matters is where each sits and what the margin authority does to it:

| Cited from the running case | Where it sits here (Figure 1) | How the margin authority interacts with it |
|---|---|---|
| Matching engine, single writer per symbol (Part 2 §3) | behind the ordering point, eight shards | none on the order path: a shard holds no lease and makes no margin decision. Liquidation does not use it; baskets are internal transfers (§5.4) |
| Replicated log (Part 4 §2–3) | the ordering point's log | the allocator writes lease inputs to it and reads occupancy from it; gateways derive their budgets from it; fences, seals and barriers are records on it |
| Determinism (Part 2 §3) | every component's state is a fold of the log | budgets are derived deterministically from logged inputs, so they are not logged themselves (ADR-4); all arithmetic is integer |
| Double-entry ledger (Part 3 §1) | downstream of fills and baskets | a lease never appears in it; `USER_MARGIN_HOLD` replaces per-order `USER_HOLD`; funding, the insurance fund and ADL are new postings (§4.4) |

## 1.4 Non-functional requirements

| # | Requirement | Target | Consequence for the design |
|---|---|---|---|
| 1 | Order throughput | 100k/s sustained, 1M/s burst | Admission is a local array operation; no allocator call on the order path |
| 2 | Admission-path latency | p50 < 20 µs, p99 < 200 µs, above matching | Per-account per-gateway scenario vector kept resident. Argued, not measured: E3 supports the scaling, not the target |
| 3 | Matching latency | p50 < 100 µs, p99 < 1 ms in-engine | Unchanged; single writer per symbol |
| 4 | Margin correctness | Requirement never exceeds equity after any move the scenario grid covers | The closure of §2.2, with the factor of two shown tight |
| 5 | Tightening latency | Binds within the lease term under partition, immediately when the ordering point is reachable | Fence at the ordering point; §1.5 |
| 6 | Allocator cadence | Issuance every 50–200 ms | §1.5, §1.6 |
| 7 | Allocator throughput | ≈ 3 × 10⁷ scenario operations per issuance | Sharded by account; grid evaluated as a vector |
| 8 | Availability | 99.99% for order entry; failover < 3 s | Replicated log, as in the running case |
| 9 | Degradation | The venue can bound its own loss in every state above HALT. Client-initiated risk reduction is **not** preserved under partition | Venue-initiated liquidation (§5.4); §3.3 states what is given up |
| 10 | Auditability | **Target:** every admission *and refusal* replayable with the figures it compared. **Today:** admissions only. A refusal never reaches the ordering point, and §2.6's gateway refusal journal is not built |
| 11 | Solvency | **Target:** Σ user liabilities ≤ Σ venue assets, continuously checkable. **Today:** three facts about one account (§4.3). The ledger is unimplemented and the fund's size is argued from one stress run (§5.4), so the venue-level claim is an argument |

Rows 1, 3 and 8 are inherited; 2, 6 and 7 derived in §1.5–1.7; 4, 5 and 9
established in §2, §3 and §5. Rows 10 and 11 are targets, marked as such; §9.4
lists everything designed and not built.

## 1.5 What sets the lease term

The term is the main operational trade-off, and it is bounded from both sides.
**Recompute cost is the floor:** ≈ 3 × 10⁷ operations per issuance (§1.6) is
ordinary at a 100 ms cadence and not at 1 ms. **Tightening latency is the
ceiling:** a gateway nobody can reach keeps spending its budget until the term
ends.

> The lease term is the worst-case delay before a credit cut binds on a gateway
> the allocator cannot reach.

Raising or lowering a limit on a reachable gateway takes effect at the next
issuance; a fence at the ordering point (§2.4) binds immediately on any gateway,
reachable or not, and is kept for the heavy cases. 50–200 ms is a design target
against an assumed tightening SLO, not a regulatory figure.

## 1.6 What the allocator has to compute, and how often

Each account needs about 3 × 10³ operations per issuance: one pass over the
scenario set, one check of §2.2's condition and a bisection for the budget size.
With 10⁴ accounts changing per issuance, that is **≈ 3 × 10⁷ operations per
issuance**, or ≈ 3 × 10⁸ per second at a 100 ms cadence, which sixteen
allocator shards (§2.6) carry with headroom. No invariant spans two accounts, so
sharding by account is trivial, and unchanged accounts keep their budgets.

## 1.7 What the admission path may do per order

Admission never recomputes the requirement from positions. Each gateway keeps
running worst-case totals per account, about 128 bytes each, or ≈ 64 MB for
5 × 10⁵ account and gateway pairs, and admitting is one pass over the scenario
set and three integer comparisons. E3 confirms the scaling: ten times more live
orders changed the incremental check by 1.1% while a full scan grew 6.7×, and
widening the set from 7 to 16 scenarios cost 34%. These are CPython ratios, so
NFR row 2's absolute target remains argued.
