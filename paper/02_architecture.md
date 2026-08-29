# 2. Architecture

## 2.1 Topology

Figure 1 gives the component view. Orders reach the venue through N ingress
admission gateways and are forwarded to a matching core partitioned by symbol.
Matching keeps a single-writer total order per symbol and no order crosses a
matching shard; that is the running case's arrangement and this design does not
touch it.

What is added is a second authority. The **margin allocator** runs off the order
path, computes each account's capacity, and hands each gateway a locally
checkable share of it. A gateway decides in constant time using only what it
already holds; it makes no call to the allocator and reads no other gateway's
state.

The two authorities are partitioned on different keys:

| | Partition key | Why |
|---|---|---|
| Matching | Symbol | Price-time priority is a total order per book, so a book must have one writer |
| Allocator | Account | Margin is an account-level quantity, and no invariant spans two accounts |

The partitionings are orthogonal, which is the structural point of the design.
An account's positions are spread across matching shards; a matching shard holds
positions for many accounts. Neither side needs the other's partition to be
correct, and the only venue-level state is the insurance fund and
auto-deleveraging, neither of which is on the admission path.

The single-writer core, in the sense the brief asks for, is therefore plural:
one writer per symbol for the book, one writer per account for the capacity
schedule. Everything else is a derived view.

## 2.2 Why a lease exists at all

If admission happened inside the matching shard, a plain counter would do: one
decision point, one number. Admission happens at N gateways upstream of that
shard, so either every order pays a network round trip to a shared counter, or
the account's capacity is divided into shares that each gateway can check
locally.

**The value of a lease is upstream early shedding — stopping a burst before it is
queued at the single-writer shard — not replacing a counter inside that shard.**
If the deployment collapses to a single ingress gateway, the mechanism reduces
to a local counter, and we would say so rather than defend the complexity.

## 2.3 What can be divided, and what cannot

Write the account's requirement as a scenario term plus an add-on term:

    M(P) = R(P) + A(P)
    R(P) = max over s in S of  sum over g of  loss_s(P_g)
    A(P) = phi(|P|),  phi convex,  phi(0) = 0

`R` is the worst loss across a fixed scenario set, and the loss under any single
scenario is linear in positions. `A` is a concentration and liquidity add-on,
convex in gross notional. `|P|` is gross notional, which is additive across
shards.

**Lemma 1 — R is sub-additive across shards.**
`R(P) = max_s sum_g loss_s(P_g) <= sum_g max_s loss_s(P_g) = sum_g R(P_g)`,
because a single scenario cannot beat the per-shard worst cases taken
separately.

**Lemma 2 — A is super-additive across shards.**
A convex function through the origin satisfies `phi(x + y) >= phi(x) + phi(y)`
for non-negative arguments; with `|P|` additive, `A(P) >= sum_g A(P_g)`.

The decomposition rule follows rather than being chosen: **the sub-additive part
can be split into per-shard leases, the super-additive part provably cannot and
is reserved centrally.** The algebra of the requirement determines what may be
pushed to the edge.

Both lemmas are checked numerically over sampled portfolios in
`tests/test_algebra.py`; over 2,000 samples the largest observed gap was 27,929
minor units for `R` and 142,052 for `A`, in the directions the lemmas predict.

## 2.4 The capacity schedule

A lease cannot undo an admission it has already granted. A single amount fixed
for an epoch therefore leaves a window in which equity has fallen and no gateway
can tell — E4 measures that window at 236 of 480 ticks.

Capacity is issued instead as a **schedule**: a non-increasing function of the
published market state index `k`, which the gateway evaluates against the state
it already receives on the market-data path. Capacity contracts as the market
moves adversely with no message from the allocator, and the point at which a
gateway must stop admitting risk-increasing orders becomes locally computable
and consistent across gateways without coordination.

The allocator solves, once per epoch per account, for the largest schedule
satisfying at every state `k`:

    R(P) + 2 * sum over g of lambda_g(k) + A(k)  <=  Collateral - loss(P, k)

Each term: `R(P)` is what the existing portfolio already requires; `A(k)` is the
add-on reserved for the largest portfolio the schedule can reach;
`loss(P, k)` is the mark-to-market the existing portfolio has taken at state `k`.

The factor of two is not a safety margin. Positions admitted during the epoch
contribute both their own requirement and the loss they take at state `k`, and
the second is bounded by the first, because `R` is a maximum over a scenario set
that contains `k`. The condition closes on itself, and the coefficient is the
price of that closure.

**Theorem.** If every gateway admits only when the order's locally-priced
marginal requirement fits `lambda_g(k)` at the state it observes, and the
allocator satisfies the condition above at issuance, then the account's
requirement does not exceed its equity at any state reached during the epoch.
Proof: chain Lemma 1, the admission rule, and the budget constraint.

**Corollary.** The offset value given up by decomposition is exactly
`sum_g R(P_g) - R(P)`, the sub-additivity gap. Attainable capital efficiency is
bounded by the sub-additive share of the requirement — a measurable quantity
rather than an assertion. §7 uses it to price the alternative that forbids
offset entirely.

Granularity: §1.5 derives `K ≈ 16` states from a 3% index span, 10× leverage and
a 2%-of-equity residual tolerance. The schedule is read by array index, so
raising `K` costs memory and not latency.

## 2.5 Fencing and generations

Every schedule carries `(account, epoch, generation)`. The allocator is the
single issuer, so generation transition has one serialisation point.

Two rules, both of which a first implementation got wrong and the simulator
corrected (§5.4):

1. A gateway that has observed a generation higher than the one its schedule was
   issued under refuses to serve. Comparing the order's generation against the
   schedule's is not enough: a stale gateway and a stale order agree with each
   other.
2. Entering liquidation bumps the generation first, voiding every outstanding
   schedule, and reduces positions second.

Credits are an accounting and admission unit, not money and not a risk measure:

    charge(order) = marginal R of the order, priced against this shard's
                    positions only, with no credit for offsets elsewhere

This is deliberately conservative and its cost is the corollary above.

## 2.6 Components

**Ingress admission gateway.** Holds schedules, evaluates them at the ratcheted
market state, prices the order's marginal `R` against per-scenario running loss
vectors (§1.7), decrements, forwards or refuses. Stateless with respect to other
gateways.

**Margin allocator.** Sharded by account. Consumes the trade stream and marks,
solves the schedule condition, emits schedule inputs to the log. Sixteen shards
at the scale of §1.1.

**Market-data publisher.** Publishes marks and the derived state index. This
path now carries authority (§6.1), so it is multi-source with a trimmed
statistic and staleness detection on each source. Named here; not designed in
this document.

**Matching core.** Unchanged from the running case. Applies a given client order
ID at most once, which is the final idempotency barrier.

**Ledger.** Double-entry, append-only. A lease is an authorisation and never
appears in the ledger; a hold is a posting. §4.

**Audit plane.** Journals, per admission decision, the schedule inputs the
gateway derived from, the generation, the state index it read, and the outcome.

## 2.7 What is on the replicated log

Orders and cancels, as in the running case. Plus, from §5.2: the schedule inputs
per account per epoch — scale, weights and shape identifier, roughly 80 bytes,
about 8 MB/s at the scale of §1.1 — and the market-state band crossings.

Not on the log: the per-shard leases themselves, which would cost 32 MB/s
against a 12.8 MB/s order stream to carry a value each shard can derive.
Not on the log: the per-scenario loss vectors, which are a cache of a pure
function of positions.

## 2.8 What this architecture does not do

It does not identify who is behind an order, separate informed from uninformed
flow, or attempt to price liquidity. It does not make the matching core elastic;
the core is partitioned, not scalable on demand, and that is inherited
deliberately. And it does not remove the need for a liquidation waterfall — it
provides the trigger and the fencing around it, and leaves the waterfall itself
to a design this document does not contain.
