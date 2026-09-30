# 7. Trade-offs and alternatives

Each decision with the alternatives considered and the reason each lost. Where a
number decided it, the number is given. The reversals are summarised here and
recorded in full in Appendix A.

## ADR-1 — How the account-level invariant is enforced pre-trade

**Decision.** Divide the account's capacity into per-gateway ceilings checked
locally against absolute figures.

| Alternative | Why it lost |
|---|---|
| Lock the account for the check | The only *exactly* correct option — full offset, no conservatism — and it puts the running case's fee-account hot-row problem (Part 3 §4) on every order. Market-maker accounts are the hottest objects in the venue |
| Fixed per-gateway sub-limits, no offset | Safe, trivial, and it deletes the product. The value forgone is exactly the sub-additivity gap $\sum_g R(P_g)-R(P)$ taken to its maximum |
| Admit optimistically, repair after | Violates the rule that a balance never goes negative. A venue proving assets ≥ liabilities at any instant cannot have a window where the proof is pending |

**Cost.** A gateway gets no credit for offsets held elsewhere, so the account is
charged the sub-additivity gap. §6.3 A3 records the routing distortion that
creates.

## ADR-2 — Flat ceiling for the term, or a schedule over market states

**Decision.** A flat ceiling. **This reverses an earlier decision**, and the reason is short: a lease cannot
remove a position it has already admitted, so capacity that shrinks with the
market restricts the *next* admission and does nothing about the exposure already
created. A flat ceiling at the solved level is equally safe and admits at least
as many orders as any decaying one with the same start. The figures that argued
otherwise came from a mechanism that charged the increment rather than the
absolute envelope (c1), and are withdrawn with it. Appendix A.1 has the full
reversal; Appendix A.2 keeps the abuse cases the schedule created.

**What survives.** A gateway evaluating a shrinking curve can notice locally, on
a market-state tick with no order present, that its consumption is higher than
the venue would like. That is a trigger, not a capacity mechanism, and its value
is unmeasured.

## ADR-3 — Where size is measured

**Decision.** Reserve size at the highest price the scenario set reaches, not at
today's price. **Alternative lost** on m1: 296 lots admitted, 382,143 over equity
after the move, against 249 lots and 5,842 inside. **Cost:** about 16% of
capacity. With one factor the reserve is tight to a minor unit per lot; with
several factors it stays safe but is loose (m4b: 24,000 against 20,000; E8: up to
8%).

## ADR-4 — What goes on the replicated log

**Decision.** The lease inputs, one record per changed account per issuance; each
gateway derives its own budgets. **Alternative lost** on bandwidth: logging the
budgets costs ≈ 32 MB/s against an order stream of ≈ 12.8 MB/s; the inputs are ≈
8 MB/s. **Cost:** everything in the derivation is versioned data (§4.1) and
cannot be tuned mid-session.

## ADR-5 — How the allocator is partitioned

**Decision.** By account, sixteen shards. **Alternative lost:** by symbol, like
matching, would need several shards to combine partial views of one account —
the coordination the design exists to remove.

## ADR-6 — What ends a holder's authority

**Decision.** A fence at the ordering point, with the term as the fallback, and
each lease bound to an account, a holder and a kind.

| Alternative | Why it lost |
|---|---|
| Compare the order's generation with the lease's | a stale gateway and a stale order agree with each other |
| The allocator's clock against the expiry | a partitioned gateway's clock may be behind (c11) |
| The holder reports it has stopped | the defect that kept recurring until release read the log itself |
| The lease id as the authority | a bearer token: any account, any holder (t1) |

**Cost.** The ordering point has no clock, so it does not enforce terms: an
honest gateway is bounded by its term, a compromised one only by the fence
(§6.1).

## What the counterexamples forced

Every row is a failing test written first, then a design change. Detail is in
Appendix A; the tests are in `tests/`.

| Counterexample | What broke | Design change it forced |
|---|---|---|
| c1 | charging an order its increment: a leg flipped from short to long left the increment unchanged while the requirement hit its maximum | gateways check absolute worst-fill figures |
| c9 | a gateway accepting "risk-reducing" orders during a partition: closing one leg raised the account's requirement because the hedge sat on another gateway | a cut-off gateway admits nothing; only the liquidator, with the merged account, reduces risk |
| m1 | size reserved at today's price: 296 lots, ending 382,143 over equity after the move | size reserved at the highest price in the scenario set: 249 lots, inside by 5,842 |
| l3 | unwinding one leg at a time: requirement 0 → 2,000 | proportional basket, committed as one record, venue as counterparty |
| c8, c11, c12 | exposure released on expiry, on a clock, or on the holder's own report | only a seal or an account-wide barrier releases exposure |
| t1 | a valid lease id under another account returned success | the ordering point binds each lease to account, holder and kind |
| r2 | replay de-duplicated by order id applied fills twice | replay is idempotent by log position |
| d7 | capacity shrank every term | the cost budget carries cost already incurred but not yet in equity |
| E5 Part B | reading the factor of two as a 64% tolerance for misreported equity | the two is a closure; at the binding point a breach tracks an overstatement one for one |
| ADR-2 | a budget that shrinks as the market moves, claimed as a safety mechanism | a flat budget per term; the schedule survives only as a local trigger |

## What we deliberately did not build

- **The waterfall, the mark-price pipeline and allocator failover** are designed
  (§5.4, §2.5, §5.5) and not built. E9 runs the waterfall's arithmetic.
- **A client-facing reduce-only path.** §3.3 says why, and what it costs.
- **Replication of the ordering point.** Figure 4 draws the target deployment and
  labels it unbuilt.
- **Anything that identifies the agent behind an order.** The mechanism is
  capacity control, not surveillance.
