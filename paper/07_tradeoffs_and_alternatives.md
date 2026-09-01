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
| Fixed per-gateway sub-limits, no offset | Safe, trivial, and it deletes the product. The value forgone is exactly the sub-additivity gap `sum_g R(P_g) - R(P)` taken to its maximum |
| Admit optimistically, repair after | Violates the rule that a balance never goes negative. A venue proving assets ≥ liabilities at any instant cannot have a window where the proof is pending |

**Cost.** A gateway gets no credit for offsets held elsewhere, so the account is
charged the sub-additivity gap. §6.3 A3 records the routing distortion that
creates.

## ADR-2 — Flat ceiling for the term, or a schedule over market states

**Decision.** A flat ceiling. **This reverses an earlier decision.**

The first version issued capacity as a schedule contracting with a published
market state index and argued from E4 that a scalar lease spent 236 of 480 ticks
above equity. The mechanism producing those numbers charged the *increment* an
order added rather than the absolute envelope, so a leg flipped from short to
long looked free (c1). Under the corrected condition there is no market state at
all.

**Why a schedule cannot buy safety.** A lease cannot remove a position it has
already admitted. Capacity that shrinks restricts the *next* admission, not the
exposure already created; a flat ceiling at the solved level is equally safe and
admits at least as many orders as any decaying one with the same start.

**What survives.** A gateway evaluating a shrinking curve can notice locally, on
a market-state tick with no order present, that its consumption is higher than
the venue would like, and report it. That is a trigger, not a capacity mechanism,
and its value is unmeasured.

**Withdrawn with it:** E4's figure and E5's suppression comparison, both produced
by superseded interfaces. Appendix A.2 keeps the record.

## ADR-3 — Where gross notional is measured

**Decision.** Two figures: the requirement uses gross at the marks in force, the
reserve uses gross at `mark_plus`.

**Alternative — one figure at the current mark.** Unsafe. `A` is a function of
gross, gross depends on the mark, and a lease admits for a term over which the
mark moves.

| Reserve measured at | Lots admitted | Requirement after the move | Equity after | Over by |
|---|---|---|---|---|
| the issuance mark, 1000 | 296 | 1,320,871 | 938,728 | 382,143 |
| `mark_plus`, 1200 | 249 | 942,615 | 948,457 | inside by 5,842 |

**Cost.** 47 lots of 296, about 16% of capacity in that configuration — the price
of making both terms maxima over the same scenario set.

**Scope.** `mark_plus` is an upper bound on the per-scenario maximum in every
case. It is *tight* only with one factor and non-negative loadings, where the gap
is the rounding, at most one minor unit per lot (m4a). With signed loadings the
symbols peak at different scenarios: m4b gives 24,000 against a best single
scenario of 20,000. The bound stays safe; the tightness claim does not
generalise.

## ADR-4 — What goes on the replicated log

**Decision.** The lease inputs — scale and weights — one record per changed
account per issuance. Each gateway derives its own three ceilings.

**Alternative — log the per-gateway ceilings.** Rejected on bandwidth: 10⁴
accounts × 5 gateways × 64 B ≈ 32 MB/s at a 100 ms cadence, against an order
stream of ≈ 12.8 MB/s. The inputs are ≈ 8 MB/s. Two and a half times the order
traffic to carry a derived value did not survive the arithmetic.

**Rule extracted.** Log the inputs a derived value comes from, not the derived
value, unless the derivation is not deterministic — the same argument the running
case makes for balances being a fold of the journal.

**Cost.** Everything in the derivation becomes versioned state-machine data
(§4.1), so it cannot be tuned mid-session.

## ADR-5 — How the allocator is partitioned

**Decision.** By account, sixteen shards.

**Alternative — by symbol, matching the core.** Rejected because margin is an
account-level quantity: shards would have to combine partial views to produce one
account's ceilings, which reintroduces the coordination the design exists to
remove. The two partitionings are orthogonal and neither needs the other to be
correct (§2.1).

## ADR-6 — What ends a holder's authority

**Decision.** A fence at the ordering point, with the lease term as the fallback
for holders nobody is asking about, and a registry binding each lease to an
account, a holder and an authority kind.

| Alternative | Why it lost |
|---|---|
| Compare the order's generation with the lease's | A stale gateway and a stale order agree with each other, so the gateway keeps spending a replaced allowance. Corrected to "a gateway that has seen a higher generation refuses to serve", which is necessary and not sufficient |
| The allocator compares its clock against the expiry | A partitioned gateway's clock may be behind, so concluding the term ended concludes nothing about whether the holder stopped (c11) |
| The holder reports that it has stopped | The interface defect that kept recurring: a correct seal paired with an optimistic usage claim was accepted until `release` computed the figure from the log itself, and the same mistake reappeared in the first account barrier |
| Treat the lease id as the authority | It was a bearer token: any account, any claimed holder, until an external review demonstrated it (t1) |

**Cost.** The ordering point has no clock it can compare against an expiry set
elsewhere, so it does not enforce terms. An honest gateway is bounded by its own
term; a Byzantine one only by the fence. §6.1 states that rather than claiming
otherwise.

## What we deliberately did not build

- **A liquidation waterfall.** The trigger, the fencing and the unwind are here;
  who absorbs a shortfall is a separate document.
- **The mark-price pipeline.** Marks set equity and every ceiling is solved
  against equity, so we would not claim the capacity control is sound without it
  (§6.3 A1).
- **A client-facing reduce-only path.** §3.3 says why, and what it costs.
- **Replication of the ordering point, and allocator failover.** Appendix B draws
  the target deployment and labels it unbuilt.
- **Anything that identifies the agent behind an order.** The mechanism is
  capacity control, not surveillance.
