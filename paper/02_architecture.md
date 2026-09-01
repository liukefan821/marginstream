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
capacity. Everything else is a derived view.

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

## 2.4 What a lease grants

A lease cannot undo an admission it has already granted. Everything in this
section follows from that one sentence.

### The three envelopes

`M(P) = R(P) + A(P)` splits into a sub-additive part and a super-additive part
(§2.3), so `R` can be divided into per-gateway shares and `A` cannot. A third
quantity has to be reserved alongside them: a fill lands somewhere inside a price
band and pays a fee, and both reduce equity after the lease was solved without
moving any position, so neither appears in `R` or `A`. A gateway therefore holds
three ceilings and checks all three independently:

| Envelope | What it bounds | Why it is separate |
|---|---|---|
| `λ_g^R` | the worst-fill scenario requirement of everything the gateway holds | sub-additive, so it divides |
| `λ_g^G` | the worst-fill gross notional the gateway can reach | an order can lower `R` while raising gross, and `A` is a function of gross |
| `λ_g^D` | the execution cost still ahead of the gateway: quantity times price band plus fee cap | bounded only because the band and the cap are venue policy; without a band there is no bound and no ceiling can be issued |

The gross ceiling is not a second allocation. `λ_g^G` is issued at a fixed ratio
to `λ_g^R`, so the solver moves along one ray through a two-dimensional feasible
set. Two independent checks against a fixed-ratio issuance policy is what this
is, and calling it a two-resource allocator would overstate it.

### What a gateway holds is orders, not positions

Two resting orders of opposite sign net to nothing, and if only one of them fills
the account carries the other side. The envelopes are therefore taken over the
worst subset of fills that could still occur, which does not have to be
enumerated, because the loss under a fixed scenario is linear in positions:

    E_k   = loss_k(filled) + sum_i max(0, loss_k(order_i))
    R_wf  = max(0, ceil(max_k E_k / DEN))
    G_wf  = sum_s mark_plus(s) * max(|filled_s + buy_s|, |filled_s - sell_s|)

`tests/test_worst_fill_exhaustive.py` compares both closed forms against
enumeration of all 2^n fill subsets over 4,000 random books; they agree on every
trial. Admission compares these **absolute** figures against the ceilings, not
the increment an order adds: a leg flipped from short to long leaves the local
increment unchanged while the account's requirement moves to its maximum
(`tests/test_counterexamples.py`, c1).

### Where gross is measured

`mark_plus(s)` is the highest mark symbol `s` reaches at any scenario in the
grid, not the mark standing when the solve ran. A lease is solved once and then
admits orders for a term over which the market moves; a short position's adverse
scenario raises the mark, raises gross, and raises the add-on, while a figure
measured at the issuance mark does not move. Reserving at the issuance mark
admits 296 lots in the worked case of `tests/test_repricing.py` m1 and finishes
382,143 above equity once the market reaches the edge of the grid; reserving at
`mark_plus` admits 249 and finishes 5,842 inside. The requirement the account
owes is still measured at the marks in force — these are two different objects
and the code keeps them apart as `gross` and `gross_reach`.

### The condition

The allocator solves, per account per issuance, for the largest ceilings
satisfying

    2 * sum_g λ_g^R  +  A( sum_g λ_g^G )  +  sum_g λ_g^D   <=   E_0

with `E_0 = Collateral + sum_s (cash_s + q_s * mark_s) - fees`, the account's
mark-to-market equity, not its collateral.

The factor of two is a closure, not a safety margin. Positions admitted during
the term contribute both their own requirement and the loss they take at the
realised scenario, and the second is bounded by the first because `R` is a
maximum over a scenario set containing that scenario. Chaining Lemma 1, the
admission rule and the condition gives `M(P') <= E(k)` at every scenario `k` in
the grid, where `E(k) = E_0 - loss(P', k)`.

**There is no market state in the condition.** An earlier version of this
document issued capacity as a schedule contracting with a published state index
and claimed that made the mechanism safe. It does not, because a lease cannot
remove a position it has already admitted. A flat lease at the level solved for
is equally safe and admits at least as many orders as any decaying one. What the
schedule remains is a local operational trigger — a gateway can notice on a
market-state tick that its consumption is above where it would like to be — and
that is worth having and is not a capacity mechanism. ADR-2 records the
withdrawal.

### What the closure costs

The condition caps utilisation at roughly half of equity before the add-on
reserve is taken, so the requirement cannot approach equity by construction. In
the binding trial of E1 — every order filled at the worst price and fee the
policy allows — the risk and debit envelopes reach 99 per cent and the
requirement is 49 per cent of equity, with no breach. That is the price of the
closure, and it is a measurement rather than an impression.

The offset given up by decomposition is separately `sum_g R(P_g) - R(P)`, the
sub-additivity gap of §2.3. §7 uses it to price the alternative that forbids
offset entirely.

## 2.5 Ending authority

Every lease carries `(account, epoch, generation, lease_id)`. The allocator is
the single issuer per account, so generation transition has one serialisation
point. Generations alone are not enough to end authority, and the reason is the
one that shapes the rest of the design.

**A term ends a holder's authority to admit. It never ends the exposure that
holder already created.** A gateway that is partitioned keeps admitting inside
its ceiling until its term expires, and its positions do not disappear when the
term does. Two quantities are therefore tracked per holder and only one of them
expires: authority, which the term ends, and committed exposure, which only an
authoritative reconciliation reduces. A holder is `(gateway_id, incarnation)`,
because a process that restarts and reuses its identity is a different holder and
both may be live at once.

**A clock comparison does not prove a holder has stopped.** The allocator
comparing its own time against a lease expiry says nothing about a partitioned
gateway whose clock is behind. What does prove it is a fence at the **ordering
point**: nothing reaches a book except through that component, so a fenced lease
can produce no further admission whatever any gateway believes. The fence does
not have to reach the gateway to work, and E7 measures that — with the fence
taken and no gateway told, the ordering point turns away 50 submissions and the
run is otherwise identical to the base.

A fence is not terminal for exposure either. An order already resting can still
fill after its lease is fenced; the fence stops new admissions and nothing else.
Removing an order's reservation needs a cancel acknowledged at the ordering
point, and lowering a holder's recorded exposure needs a terminal reconciliation
carrying that lease's seal.

When the solve is infeasible the leases are issued in **quarantine** and a
quarantined gateway admits nothing at all — including orders that look, locally,
like risk reduction. An order that lowers one gateway's requirement can raise the
account's by removing a hedge held on another gateway (c9), so a gateway cannot
make that judgement on its own. §5.4 and §3.3 carry the consequences.

The consequence for credit decisions is worth stating in one line, because §1.6
derives the term from recompute cost and that derivation is incomplete: **the
lease term is the worst-case enforcement latency of a tightening decision under
partition.** Raising a limit takes effect at the next issuance, and lowering one
does too when the allocator can reach the gateways. A venue that needs a
collateral cut to bind within X must set the term below X.

## 2.6 Components

**Ingress admission gateway.** Holds three ceilings per account, maintains the
worst-fill running totals over the scenario grid, compares the absolute figures
against the ceilings, forwards or refuses. Stateless with respect to other
gateways. Its state is a deterministic fold of the ordering point's log, so a
snapshot bounds replay time rather than being a correctness requirement (§5.5).

**Ordering point.** The single place every admitted order passes through before
it reaches a book. It numbers admissions per lease and accepts only the next
number, so the sequence it records is gap-free; it enforces the price band, the
fee cap, fill direction, over-fill and fill identity on every fill, writing
nothing and moving nothing when it refuses; it holds the fence; and it commits
liquidation baskets as single records. Every figure the allocator acts on is
computed from its log rather than reported to it.

**Margin allocator.** Sharded by account. Consumes the trade stream and marks,
solves the condition of §2.4, emits lease inputs to the log. Sixteen shards at
the scale of §1.1.

**Liquidator.** One per account under liquidation, and the only holder whose
orders are checked against the merged account rather than against a ceiling —
which is the check c9 shows a gateway cannot perform on its own. It is therefore
inside the trusted computing base on its own account: nothing in the capacity
accounting bounds it, and a compromised one can churn the account subject only to
the non-increase test. §5.4 and §6.1 carry that.

**Market-data publisher.** Publishes marks. The marks set equity, the scenario
displacements and `mark_plus`, so this path determines how much capacity the
allocator solves for even though it does not appear in the admission check.
Multi-source with a trimmed statistic and staleness detection on each source.
Named here; not designed in this document.

**Matching core.** Unchanged from the running case. Applies a given client order
ID at most once, which is the final idempotency barrier.

**Ledger.** Double-entry, append-only. A lease is an authorisation and never
appears in the ledger; a hold is a posting. §4.

**Audit plane.** Journals, per admission decision, the ceilings the gateway
derived from, the lease id and generation, the three envelope figures it compared,
and the outcome. The ordering point's log already holds the admission itself; this
is the gateway's side of the same decision, and it is what makes a refusal
reviewable rather than only an acceptance.

## 2.7 What is on the replicated log

Every admission, with the lease and sequence number it was made under, the
holder, and the terms it will be held to; every fill, with its price and fee;
every cancel acknowledgement; every fence, with the seal naming the last
admission it covers; every liquidation basket, as one record; and every
settlement barrier. Plus the lease inputs per account per issuance — scale,
weights and shape identifier, roughly 80 bytes, about 8 MB/s at the scale of
§1.1.

This is more than the running case puts on its log, and the reason is that three
different components have to be able to reconstruct the same facts without
reaching each other: a recovering gateway rebuilds its order state, the ledger
rebuilds the account, and the allocator computes occupancy for a holder that may
be gone. All three are folds of this one log.

Not on the log: the per-gateway ceilings, which would cost 32 MB/s against a
12.8 MB/s order stream to carry a value each gateway can derive. Not on the log:
the per-scenario loss vectors and the running gross figure, which are caches of
pure functions of the order state.

## 2.8 What this architecture does not do

It does not identify who is behind an order, separate informed from uninformed
flow, or attempt to price liquidity. It does not make the matching core elastic;
the core is partitioned, not scalable on demand, and that is inherited
deliberately. And it does not remove the need for a liquidation waterfall — it
provides the trigger and the fencing around it, and leaves the waterfall itself
to a design this document does not contain.
