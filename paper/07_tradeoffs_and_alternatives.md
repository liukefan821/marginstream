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

**What the decision costs.** Local pricing gives no credit for offsets on other
shards, so every charge is conservative and the account is charged more than its
true risk. §6.3 A3 records the incentive distortion this creates.

## ADR-2 — Scalar lease per epoch, or a schedule over market states

**Decision.** A schedule, non-increasing in the published market state.

**Alternative — a single amount fixed for the epoch.**
Rejected on E4. Over eight epochs of sixty ticks with the market state advancing
inside each epoch, a scalar lease spent **236 of 480 ticks with the requirement
above equity**, and no gateway could detect the condition locally. The schedule
spent none.

**What the decision costs.** The market-data path now carries authority it did
not carry before (§6.1), which opens A1 and A2 in §6.3. A1 is closed by the
ratchet at no cost on an honest feed. A2 is only partially mitigated: under
suppression the ratchet reduces exposure from 12 ticks to 5 and costs 9 admitted
orders. The residual is pushed to the mark pipeline, which this document names
and does not design.

Also: the schedule shape and the state banding become versioned parts of the
state machine (§5.6), so an operator cannot tune them during a volatile session.
That is a real operational loss. The alternative — runtime-tunable shapes — makes
replay non-deterministic and was rejected for that reason alone.

## ADR-3 — Sizing capacity for the worst state, or as a function of state

**Decision.** As a function of state.

**Alternative — one conservative amount sized for the worst state the schedule
must survive.** This is the flat curve in E4, and it is safe: zero ticks above
equity. It admitted **29 orders**. The steepest state-contingent schedule
admitted **111** at the same zero-breach outcome, a factor of 3.8.

The general form of the finding: capacity granted as a function of state
recovers throughput that worst-case sizing gives away, because most of the time
the market is not in the worst state.

## ADR-4 — What goes on the replicated log

**Decision.** The schedule inputs — scale, weights, shape identifier — one
record per changed account per epoch. Each shard derives its own lease.

**Alternative — log the per-shard leases directly.**
Rejected on bandwidth: ≈ 5 × 10⁵ records per second at ≈ 64 bytes is 32 MB/s,
against an order command stream of 12.8 MB/s. Logging the inputs instead is
≈ 8 MB/s. Two and a half times the order traffic to carry a derived value did
not survive the arithmetic.

**Rule extracted.** Log the inputs a derived value is computed from, not the
derived value, unless the derivation is not deterministic. This is the same
argument the running case makes for balances being a fold of the journal.

**What the decision costs.** Everything in the derivation becomes part of the
state machine, which is where ADR-2's operational loss comes from.

## ADR-5 — How the allocator is partitioned

**Decision.** By account, sixteen shards.

**Alternative — one allocator instance.** Rejected on §1.6: ≈ 4 × 10⁹ scenario
operations per second at a 100 ms epoch does not fit a core.

**Alternative — partition by symbol, matching the matching core.** Rejected
because margin is an account-level quantity; a symbol-partitioned allocator
would have to combine partial views to produce one account's schedule, which
reintroduces the coordination the design exists to avoid.

The accepted answer works because there is no invariant spanning two accounts,
which is the property that makes the two partitionings orthogonal.

## ADR-6 — Fencing rule

**Decision.** A gateway that has observed a generation higher than the one its
schedule was issued under refuses to serve.

**Alternative — compare the order's generation with the schedule's and refuse on
mismatch.** This was the first implementation and it is wrong: a stale gateway
and a stale order agree with each other, so the gateway keeps spending an
allowance that has been replaced. E2's scripted case shows the requirement
reaching 10,047 against 10,000 of equity. The error and its correction are in
`REPRODUCE.md`.

## What we deliberately did not build

- **A liquidation waterfall.** Partial liquidation, insurance-fund draw and
  auto-deleveraging are named and not designed. The design provides the trigger
  and the fencing around it; who absorbs a shortfall is a separate document.
- **The mark-price pipeline.** Named in §2.6, required by §6.3 A2, not designed.
  We would not claim the venue's capacity control is sound without it.
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
