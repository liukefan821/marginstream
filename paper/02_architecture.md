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

    M(P) = R(P) + A(G(P))
    R(P) = max over k in S of  loss_k(P)
    G(P) = sum over symbols s of  |q_s| * mark_plus(s)
    A     = phi, convex, non-decreasing, phi(0) = 0

`R` is the worst loss across a fixed scenario set, and the loss under any single
scenario is linear in positions. `G` is gross notional, measured at the highest
mark the grid reaches (§2.4). `A` is a concentration and liquidity add-on,
convex and non-decreasing in gross.

**The partition is by gateway, not by symbol.** That matters for what follows,
and an earlier version of this section got it wrong: it partitioned by matching
shard and treated gross as additive across the partition. It is not. Two
gateways can hold opposite positions in the *same* symbol, and those net inside
the account.

**Lemma 1 — R is sub-additive across any partition.**
`R(P) = max_k sum_g loss_k(P_g) <= sum_g max_k loss_k(P_g) = sum_g R(P_g)`,
because a single scenario cannot beat the per-gateway worst cases taken
separately.

**Lemma 2 — G is sub-additive across any partition.**
Per symbol, `|sum_g q_{g,s}| <= sum_g |q_{g,s}|` by the triangle inequality;
multiplying by `mark_plus(s) > 0` and summing over symbols gives

    G( sum_g P_g )  <=  sum_g G(P_g)

with equality only when every gateway holds the same sign in every symbol. So a
per-gateway gross ceiling bounds the account's gross from above, which is the
direction safety needs, and it gives that bound up whenever gateways hold
offsetting legs in one symbol.

**Lemma 3 — A does not decompose.**
A convex function through the origin is super-additive on non-negative
arguments, so `sum_g A(G_g) <= A(sum_g G_g)`. Handing each gateway its own
add-on allowance and adding the allowances up therefore under-states the whole,
however the positions are split.

The decomposition rule follows from the three rather than being chosen:

> The sub-additive parts can be divided into per-gateway ceilings and checked
> locally. The add-on cannot be divided; it is evaluated once, centrally, on the
> summed gross, and `A` being non-decreasing is what makes that central
> evaluation an upper bound on the account's true add-on.

Chained: `A(G(P')) <= A(sum_g G(P'_g)) <= A(sum_g λ_g^G)`, the first step by
Lemma 2 with `A` non-decreasing, the second by the admission rule. Nothing in
that chain needs gross to be additive.

Lemmas 1 and 3 are checked numerically over sampled portfolios in
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
| `λ_g^D` | the execution cost this gateway can still incur, plus whatever it has already incurred that the equity figure the current lease was solved against does not yet reflect | bounded only because the band and the cap are venue policy; without a band there is no bound and no ceiling can be issued |

The three ceilings are not three allocations. `λ_g^G` and `λ_g^D` are issued at
fixed ratios to `λ_g^R`, so the solver searches a single scalar and moves along
one ray through a three-dimensional feasible set. Three independent checks
against a fixed-ratio issuance policy is what this is, and calling it a
multi-resource allocator would overstate it.

`λ_g^D` covers two things and not one. A gateway's live orders will cost band
plus fee when they fill, and that is ahead of it. A gateway's *filled* orders
already cost it, and until a lease is issued against an equity figure that
reflects those fills, the cost is behind the ceiling and still has to be
reserved. Dropping the second half is what made capacity decay every term in an
earlier implementation (`tests/test_execution_debit.py`, d7).

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

**The closure.** Write `P'` for the position the account ends the term with, `D`
for the execution cost the term actually incurred, and `k` for the scenario the
market realises. Four bounds hold, the first three by the admission rule and the
fourth because `R` is a maximum over a set containing `k`:

    R(P')      <=  sum_g λ_g^R
    G(P')      <=  sum_g λ_g^G        (Lemma 2, then A non-decreasing)
    D          <=  sum_g λ_g^D
    loss_k(P') <=  R(P')

Adding the requirement, the cost and the realised loss together:

    M_k(P') + D + loss_k(P')
        <=  2 * sum_g λ_g^R  +  A( sum_g λ_g^G )  +  sum_g λ_g^D
        <=  E_0

and the equity the account actually has after the term is
`Equity_after(k) = E_0 - D - loss_k(P')`, so

    M_k(P')  <=  E_0 - D - loss_k(P')  =  Equity_after(k)

**Subtracting `D` is the whole reason the third resource exists.** An earlier
version of this section concluded `M <= E_0 - loss` and left execution cost out
of the arithmetic while still listing `λ^D` as a ceiling, which made the third
envelope look like belt-and-braces rather than a term in the inequality.

The factor of two is a closure, not a safety margin, and it is tight. Set
`A = D = 0` and take a position whose requirement fills the ceiling exactly,
`R(P') = λ^R`, with the realised scenario being the one that attains the
maximum, so `loss_k(P') = R(P')`. Then `M_k = λ^R` and
`Equity_after(k) = E_0 - λ^R`, and the two are equal precisely when
`E_0 = 2λ^R`. With any coefficient `c < 2` the solve issues `λ^R = E_0 / c`,
which is larger, and the same position ends with
`M_k = E_0/c > E_0 - E_0/c = Equity_after(k)`. The coefficient cannot be reduced
without giving up the guarantee.

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
