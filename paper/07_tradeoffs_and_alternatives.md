# 7. Trade-offs and alternatives

Each decision is recorded with the alternatives considered and the reason each
lost. Where a number decided it, the number is given.

## ADR-1 — How the account-level invariant is enforced pre-trade

**Decision.** Divide the account's capacity into per-gateway shares that are
checked locally.

**Alternative A — lock the account for the duration of the check.**
Rejected on the hot path. Market-maker accounts are the hottest objects in the
venue, so this is the fee-account hot-row problem of the running case (Part 3
§4) transplanted onto every order. It is also the only alternative that is
*exactly* correct — it gives full offset with no conservatism — which is worth
saying, because the design here trades exactness for locality and should not
pretend the trade is free.

**Alternative B — fixed per-shard sub-limits with no offset.**
Safe, trivial to implement, and it deletes the product. A client long one
contract and short a correlated one funds both legs separately, which is the
thing a unified account exists to avoid. The corollary in §2.3 prices this: the
value forgone is exactly the sub-additivity gap `sum_g R(P_g) - R(P)`, so the
cost of Alternative B is that gap taken to its maximum rather than minimised.

**Alternative C — admit optimistically, repair afterwards.**
Rejected against the rule that a balance never goes negative. A venue that must
prove assets ≥ liabilities at any instant cannot have a window in which the
proof is pending.

**What the decision costs.** A gateway compares absolute figures against its own
ceilings and gets no credit for offsets held elsewhere, so the account is charged
more than its true risk by exactly the sub-additivity gap of §2.3. §6.3 A3
records the incentive distortion this creates.

## ADR-2 — Scalar lease per term, or a schedule over market states

**Decision.** A flat ceiling for the term. The schedule is retained only as a
local operational trigger and carries no safety or capacity claim.

**This ADR reverses an earlier decision, and the reversal is the interesting
part.** The first version of this document issued capacity as a schedule
contracting with a published market state index, and argued from E4 that a
scalar lease spent 236 of 480 ticks with the requirement above equity while the
schedule spent none. The mechanism that produced those numbers was wrong in a way
the experiment could not see: it charged the *increment* an order added rather
than the absolute envelope, so a leg flipped from short to long looked free. Once
admission compares absolute envelopes, the safety condition is

    2 * sum_g λ_g^R + A( sum_g λ_g^G ) + sum_g λ_g^D <= E_0

and there is no market state in it at all.

**Why a schedule cannot buy safety.** A lease cannot remove a position it has
already admitted. Capacity that shrinks as the market moves restricts what a
gateway may admit *next*; it does nothing about what the gateway admitted at the
start of the term, which is where the exposure is. A flat ceiling at the level
the condition solves for is equally safe and admits at least as many orders as
any decaying schedule with the same starting point.

**What it keeps.** A gateway evaluating a shrinking curve at the state it already
receives can notice, on a market-state tick and with no order present, that its
consumption is above where the venue would like it to be, and report that
locally. That is a useful trigger and it is not a capacity mechanism. Its value —
whatever it saves in tail exposure and forced reduction — is unmeasured, and
accepted-order count is not the metric for it.

**Withdrawn along with the decision:** E4's 236-of-480 figure and E5's
suppression comparison. Both were produced by the superseded interfaces and both
now live in `experiments/superseded/`. They are not cited as current results
anywhere in this document.

**What the reversal costs.** The market-data path still matters, because the
marks set equity and `mark_plus`, but it no longer carries authority *inside the
admission check*. §6.1 states the weaker claim.

## ADR-3 — Where gross notional is measured

**Decision.** Two figures, not one. The requirement uses gross at the marks in
force; the reserve uses gross at `mark_plus`, the highest mark each symbol
reaches at any scenario in the grid.

**Alternative — one figure at the current mark.** This is what the code did until
repricing was introduced, and it is unsafe. `A` is a function of gross, gross
depends on the mark, and a lease is solved once and then admits for a term over
which the mark moves. A short position's adverse scenario raises the mark, raises
gross and raises the add-on, while a figure measured at the issuance mark does
not move.

The arithmetic, from `tests/test_repricing.py` m1 — one symbol, 200 of scenario
requirement and 7 of execution cost per lot, `E_0` of 1,000,000:

| Reserve measured at | Lots admitted | Requirement after the move | Equity after the move | Over by |
|---|---|---|---|---|
| the issuance mark, 1000 | 296 | 1,320,871 | 938,728 | 382,143 |
| `mark_plus`, 1200 | 249 | 942,615 | 948,457 | inside by 5,842 |

**What the decision costs.** 47 lots of the 296, about sixteen per cent of
capacity in that configuration. That is the price of making both terms of the
requirement maxima over the same scenario set, which is what the earlier
formulation only did for `R`.

**Scope of the tightness claim.** `mark_plus` takes the worst mark per symbol
independently and is an upper bound on the per-scenario maximum in every case. It
is *tight* only in the model configured here, which has one factor and
non-negative loadings, so every symbol reaches its highest mark at the same
scenario; there the gap is the rounding, at most one minor unit per lot (m4a).
With signed loadings the symbols peak at different scenarios and the gap is
unbounded by that figure: m4b constructs two symbols with opposite loadings where
the per-symbol bound is 24,000 against a best single scenario of 20,000. The
bound stays safe; the tightness statement does not generalise and is not written
as though it does.

**The superseded ADR-3.** The earlier version of this ADR chose state-contingent
sizing over worst-state sizing and cited a 3.8× throughput gain from E4. That
number is withdrawn with ADR-2. Under the corrected condition the reserve *is*
sized for the worst state the grid contains, and the throughput question is
answered by the utilisation figures of §2.4 rather than by a curve shape.

## ADR-4 — What goes on the replicated log

**Decision.** The lease inputs — scale and weights — one record per changed
account per issuance. Each gateway derives its own three ceilings.

**Alternative — log the per-gateway ceilings directly.**
Rejected on bandwidth: ≈ 5 × 10⁵ records per second at ≈ 64 bytes is 32 MB/s,
against an order command stream of 12.8 MB/s. Logging the inputs instead is
≈ 8 MB/s. Two and a half times the order traffic to carry a derived value did
not survive the arithmetic.

**Rule extracted.** Log the inputs a derived value is computed from, not the
derived value, unless the derivation is not deterministic. This is the same
argument the running case makes for balances being a fold of the journal.

**What the decision costs.** Everything in the derivation becomes part of the
state machine. ADR-2's schedule shape was the main thing that made that costly;
with the schedule withdrawn, what remains versioned is the scenario grid, the
add-on parameters, the price bands and the fee caps.

## ADR-5 — How the allocator is partitioned

**Decision.** By account, sixteen shards.

**Alternative — one allocator instance.** Rejected on §1.6: ≈ 4 × 10⁹ scenario
operations per second at a 100 ms epoch does not fit a core.

**Alternative — partition by symbol, matching the matching core.** Rejected
because margin is an account-level quantity; a symbol-partitioned allocator
would have to combine partial views to produce one account's ceilings, which
reintroduces the coordination the design exists to avoid.

The accepted answer works because there is no invariant spanning two accounts,
which is the property that makes the two partitionings orthogonal.

## ADR-6 — What ends a holder's authority

**Decision.** A fence at the ordering point, with the lease term as the fallback
for holders the ordering point is not being asked about.

**Alternative A — compare the order's generation with the lease's and refuse on
mismatch.** The first implementation, and wrong: a stale gateway and a stale
order agree with each other, so the gateway keeps spending an allowance that has
been replaced. Corrected to "a gateway that has seen a higher generation refuses
to serve", which is necessary and still not sufficient.

**Alternative B — the allocator compares its own clock against the lease
expiry.** Rejected on c11 and the fifth review round. A partitioned gateway's
clock may be behind, so the allocator concluding that a term has ended concludes
nothing about whether the holder has stopped. A fence does establish it, because
nothing reaches a book except through the ordering point.

**Alternative C — the holder reports that it has stopped.** Rejected because it
is the interface defect that kept recurring: a correct seal paired with an
optimistic usage claim was accepted until `release` was changed to compute the
figure from the log itself, and the same mistake reappeared in the first version
of the account barrier.

**What the decision costs.** The ordering point has no clock it can compare
against an expiry set elsewhere, so it does not enforce terms. An honest gateway
is bounded by its own term; a Byzantine one is bounded only by the fence. §6.1
states that rather than claiming the term is enforced.

## What we deliberately did not build

- **A liquidation waterfall.** Partial liquidation, insurance-fund draw and
  auto-deleveraging are named and not designed. The design provides the trigger
  and the fencing around it; who absorbs a shortfall is a separate document.
- **The mark-price pipeline.** Named in §2.6 and required by §6.3 A1, not
  designed. Marks set equity, and every ceiling is solved against equity, so we
  would not claim the venue's capacity control is sound without it.
- **A client-facing reduce-only path.** §3.3 explains why the design does not
  have one and what that costs during a partition.
- **Replication of the ordering point, and allocator failover.** Appendix B
  draws the target deployment and labels it unbuilt.
- **A matching engine.** Taken from the running case and cited.
- **Anything that identifies the agent behind an order,** or tries to separate
  harmful from legitimate synchrony. The mechanism is capacity control, not
  classification.
- **Cross-datacentre replication.** Single availability zone throughout.

## The one that would change the design if it turned out false

The replay-rate assumption of §5.5 — that replay runs at roughly ten times live.
If it is two times, the snapshot cadence tightens from five minutes to about one
and nothing else moves. That is a small enough consequence that we did not
measure it before writing, and measuring it is the first item in §8.

The assumption that would matter more is the scenario grid staying small and
fixed. Every latency number in §1.7 depends on `|S| = 16`. A requirement that
needs a full portfolio revaluation per order is incompatible with NFR row 2, and
the design has no fallback for that case; it would need a different admission
path, not a tuned one.
