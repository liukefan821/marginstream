# 2. Architecture

## 2.1 Topology

Figure 1 gives the component view. Orders reach the venue through N ingress
admission gateways, pass through a single **ordering point**, and are forwarded
to a matching core partitioned by symbol. Matching keeps a single-writer total
order per symbol and no order crosses a shard; that is the running case's
arrangement and this design does not touch it.

What is added is a second authority. The **margin allocator** runs off the order
path, computes each account's capacity, and hands each gateway a locally
checkable share of it. A gateway decides in constant time from what it already
holds; it calls no allocator and reads no other gateway's state.

| | Partition key | Why |
|---|---|---|
| Matching | Symbol | Price-time priority is a total order per book, so a book must have one writer |
| Allocator | Account | Margin is an account-level quantity, and no invariant spans two accounts |

The partitionings are orthogonal, which is the structural point: an account's
positions spread across matching shards, a shard holds many accounts, and neither
needs the other's partition to be correct. The single-writer core is plural — one
writer per symbol for the book, one per account for the capacity.

## 2.2 Why a lease exists at all

Admission happens at N gateways upstream of the shard, so either every order pays
a round trip to a shared counter or capacity is divided into locally checked
shares. **The value of a lease is upstream early shedding** — stopping a burst
before it queues at the single-writer shard — not replacing a counter inside it.
Collapsed to one gateway the mechanism reduces to a local counter, and we would
say so rather than defend the complexity.

## 2.3 What can be divided, and what cannot

Write the account's requirement as a scenario term plus an add-on. Two gross
figures are needed and they are not the same object:

    R(P)    = max over k in S of  loss_k(P)
    G_k(P)  = sum_s |q_s| * mark_s(k)          gross at the marks of scenario k
    G+(P)   = sum_s |q_s| * max_j mark_s(j)    gross at the highest mark in S
    M_k(P)  = R(P) + A(G_k(P))                 what the account owes at k
    A       = phi, convex, non-decreasing, phi(0) = 0

`G_k` is what the requirement is computed from; `G+` is what a lease reserves
against, because the marks move during a term (§2.4). `G_k(P) <= G+(P)` for every
`k` by construction. The code keeps them apart as `gross` and `gross_reach`.

`R` is the worst loss across a fixed scenario set `S`, and the loss under any
single scenario is linear in positions. `A` is a concentration and liquidity
add-on.

**Model boundary.** The algebra below holds for any finite `S`. The set used in
every correctness experiment here is **seven points on a single factor with
non-negative loadings**; E3 additionally times a 16-point grid. Nothing here shows
that a single-factor grid is adequate for 40 underlyings — basis and
idiosyncratic risk would need more factors or a wider set, and the evidence for
*that* choice is not in this document. What is shown is that the decomposition,
the closure and the lifecycle are correct for whatever finite `S` is picked.

**The partition is by gateway, not by symbol.** Two gateways can hold opposite
positions in the *same* symbol, and those net inside the account, so gross is not
additive across the partition.

**Lemma 1 — R is sub-additive.**
`R(P) = max_k sum_g loss_k(P_g) <= sum_g max_k loss_k(P_g) = sum_g R(P_g)`: a
single scenario cannot beat the per-gateway worst cases taken separately.

**Lemma 2 — gross is sub-additive.** Per symbol,
`|sum_g q_{g,s}| <= sum_g |q_{g,s}|` by the triangle inequality; multiplying by a
positive mark and summing gives `G_k(sum_g P_g) <= sum_g G_k(P_g)` for every `k`,
and the same for `G+`, with equality only when every gateway holds the same sign
in every symbol.

**Lemma 3 — A does not decompose.** A convex function through the origin is
super-additive on non-negative arguments, so `sum_g A(G_g) <= A(sum_g G_g)`.
Per-gateway add-on allowances added up under-state the whole, however the
positions are split.

The decomposition rule follows rather than being chosen:

> The sub-additive parts divide into per-gateway ceilings checked locally. The
> add-on does not; it is evaluated once, centrally, on the summed gross, and `A`
> being non-decreasing is what makes that an upper bound.

Chained, for the realised scenario `k`:

    G_k(P')  <=  G+(P')  <=  sum_g G+(P'_g)  <=  sum_g λ_g^G

the first step by construction, the second by Lemma 2, the third by the admission
rule. `A` non-decreasing then carries it to
`A(G_k(P')) <= A(sum_g λ_g^G)`. Nothing in that chain needs gross to be additive.
Lemmas 1 and 3 are checked over 2,000 sampled portfolios in
`tests/test_algebra.py`, in the predicted directions.

## 2.4 What a lease grants

A lease cannot undo an admission it has already granted. Everything here follows
from that.

### Three envelopes

| Envelope | What it bounds | Why separate |
|---|---|---|
| `λ_g^R` | worst-fill scenario requirement of everything the gateway holds | sub-additive, so it divides |
| `λ_g^G` | worst-fill `G+` the gateway can reach | an order can lower `R` while raising gross |
| `λ_g^D` | execution cost the gateway can still incur, plus cost already incurred that the equity the current lease was solved against does not yet reflect | its effect on equity is covered by neither the scenario requirement nor the gross add-on |

The three are not three allocations: `λ^G` and `λ^D` are issued at fixed ratios
to `λ^R`, so the solver searches one scalar along a ray through a
three-dimensional feasible set. Dropping the second half of `λ^D` is what made
capacity decay every term in an earlier implementation (d7).

### What a gateway holds is orders, not positions

Two resting orders of opposite sign net to nothing, and if only one fills the
account carries the other side. The envelopes are taken over the worst subset of
fills that could still occur, which needs no enumeration because the loss under a
fixed scenario is linear:

    E_k   = loss_k(filled) + sum_i max(0, loss_k(order_i))
    R_wf  = max(0, ceil(max_k E_k / DEN))
    G_wf  = sum_s max_j mark_s(j) * max(|filled_s + buy_s|, |filled_s - sell_s|)

Both closed forms agree with enumeration of all 2^n fill subsets on 4,000 random
books (`test_worst_fill_exhaustive`). Admission compares these **absolute**
figures against the ceilings, not the increment an order adds: a leg flipped from
short to long leaves the increment unchanged while the account's requirement moves
to its maximum (c1).

### Where gross is measured

A lease reserves against `G+`, not against the gross standing when the solve ran.
A short position's adverse scenario raises the mark, raises gross and raises the
add-on, while a figure measured at the issuance mark does not move. Reserving at
the issuance mark admits 296 lots in the worked case of m1 and finishes 382,143
above equity; reserving at `G+` admits 249 and finishes 5,842 inside. ADR-3 gives
the cost and the scope of the tightness claim.

### The condition and its closure

    2 * sum_g λ_g^R  +  A( sum_g λ_g^G )  +  sum_g λ_g^D   <=   E_0

with `E_0 = Collateral + sum_s (cash_s + q_s * mark_s) - fees`, mark-to-market
equity rather than collateral. There is no market state in it.

Write `P'` for the position the term ends with, `D` for the execution cost it
incurred, `k` for the realised scenario. Three bounds come from the admission
rule and the fourth because `R` is a maximum over a set containing `k`:

    R(P')   <= sum_g λ_g^R      G_k(P') <= sum_g λ_g^G
    D       <= sum_g λ_g^D      loss_k(P') <= R(P')

Adding requirement, cost and realised loss:

    M_k(P') + D + loss_k(P')  <=  2 sum_g λ_g^R + A(sum_g λ_g^G) + sum_g λ_g^D  <=  E_0

and since `Equity_after(k) = E_0 - D - loss_k(P')`,

    M_k(P')  <=  E_0 - D - loss_k(P')  =  Equity_after(k)

**Subtracting `D` is why the third resource exists.** A conclusion of
`M <= E_0 - loss` would leave execution cost out of the arithmetic while still
listing `λ^D` as a ceiling.

The factor of two is a closure, not a margin, and it is tight. Set `A = D = 0`
and take `R(P') = λ^R` with the realised scenario attaining the maximum, so
`loss_k = R`. Then `M_k = λ^R` and `Equity_after = E_0 - λ^R`, equal precisely
when `E_0 = 2λ^R`. With `c < 2` the solve issues `λ^R = E_0/c`, and the same
position ends with `M_k = E_0/c > E_0 - E_0/c`. The coefficient cannot be reduced.

### What the closure costs

Utilisation is capped near half of equity before the add-on reserve. In E1's
binding trial — every order filled at the worst price and fee the policy allows —
the risk and debit envelopes reach 99% and the requirement is 49% of equity, with
no breach. The offset decomposition gives up is separately §2.3's sub-additivity
gap, which §7 uses to price forbidding offset entirely.

## 2.5 Ending authority

Every lease carries `(account, epoch, generation, lease_id)` and is registered at
the ordering point against the account, the holder and the authority kind
(Appendix C.3). A holder is `(gateway_id, incarnation)`: a process that restarts
and reuses its identity is a different holder, and both may be live at once.

Two quantities are tracked per holder and only one expires. **A term ends
authority to admit; it never ends the exposure already created.** A clock
comparison does not prove a partitioned holder has stopped — its clock may be
behind. A **fence at the ordering point** does, because nothing reaches a book
except through it, and it need not reach the gateway: E7 fences without telling
any gateway, the ordering point turns away 50 submissions, and the run is
otherwise identical.

A fence is not terminal for exposure either: a resting order can still fill.
Removing its reservation needs a cancel acknowledged at the ordering point;
lowering a holder's recorded exposure needs a terminal reconciliation with that
lease's seal, or an account-wide barrier. §5.4 is the full lifecycle.

When the solve is infeasible, leases are issued in **quarantine** and the gateway
admits nothing — including orders that look locally like risk reduction, because
an order lowering one gateway's requirement can raise the account's by removing a
hedge held elsewhere (c9).

## 2.6 Components

| Component | Holds | Decides |
|---|---|---|
| Ingress gateway | three ceilings per account; worst-fill running totals over the grid | admit or refuse, by comparing absolute figures against ceilings. Its state is a deterministic fold of the log, so a snapshot bounds replay time rather than being a correctness requirement |
| Ordering point | the log; the lease registry; sessions; fences and seals | that an admission is the next number for a live, correctly bound lease; that a fill matches its recorded terms; that a basket commits as one record. Writes nothing when it refuses |
| Margin allocator | committed exposure per holder; generations; credit versions | the condition of §2.4, once per account per issuance. Sharded by account, ≈ 16 shards |
| Liquidator | the merged account view | which basket to transfer, checked against the merged account rather than a ceiling — the check c9 shows a gateway cannot make. Inside the trusted computing base on its own account (§6.1) |
| Market-data publisher | marks | nothing on the admission path, but marks set equity, the scenario displacements and `G+`, so this path fixes how much capacity is solved for. Named here, not designed |
| Matching core | books | unchanged; applies a client order ID at most once |
| Ledger | postings | double-entry, append-only. A lease never appears in it (§4.3) |

## 2.7 What is on the replicated log

Every admission with its lease, sequence number, holder and terms; every fill
with price and fee; every cancel acknowledgement; every fence with its seal;
every liquidation basket as one record; every settlement barrier; and the lease
inputs per account per issuance.

Three components reconstruct the same facts from it without reaching each other:
a recovering gateway rebuilds its order state, the ledger rebuilds the account,
and the allocator computes occupancy for a holder that may be gone.

Not on the log: the per-gateway ceilings, which cost ≈ 32 MB/s against an ≈ 12.8
MB/s order stream to carry a value each gateway derives from ≈ 8 MB/s of inputs
(ADR-4 has the arithmetic). Also not on the log: the scenario vectors and running
gross, caches of pure functions of the order state.

## 2.8 What this architecture does not do

It does not identify who is behind an order, price liquidity, or make the
matching core elastic. And it does not remove the need for a liquidation
waterfall: it provides the trigger, the fencing and the unwind, and leaves who
absorbs a shortfall to a design this document does not contain.
