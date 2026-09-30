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

## 2.2 The idea in one page

**Why the requirement cannot be checked one book at a time.** A client long
BTC-PERP and short ETH-PERP is hedged: the account owes less than the two legs
would owe separately. That offset is the product. But the two legs sit on
different matching shards, written concurrently, and an order arrives at one of
them. No single place sees the whole account at the moment the order has to be
accepted or refused, and "requirement ≤ equity" is a statement about the whole
account. Checking each book on its own lets an account build more than it can
pay for: E2's naive control reaches a requirement of 201,000 on 2,000 of
collateral.

**Why not ask a central service on every order.** Admission happens at N
gateways in front of the shards. Either every order pays a round trip to one
shared counter per account, or each gateway is handed a share it can check
locally. **The value of the share is upstream early shedding** — stopping a
burst before it queues at a single-writer shard. With one gateway the design
collapses to a local counter, and we would say so rather than defend it.

**How the requirement is split.** The requirement has two parts.

- **The scenario part** is the worst loss the account takes over a fixed set of
  market moves. It *can* be split: the worst case of the whole account is never
  more than the worst cases of its parts added up, because one market move
  cannot be the worst for every part at once. So each gateway gets its own
  risk budget and checks it locally.
- **The concentration add-on** grows faster than size (it is convex). Pieces of
  it add up to *less* than the whole, so splitting it would under-charge. It is
  never split: it is reserved once, centrally, on the total size all gateways
  together may reach.

**What the closure buys.** The allocator issues budgets so that

    2 × (risk budgets) + add-on(total size budget) + (cost budgets) ≤ equity

The **two** is exact, not a safety buffer. A filled position does two things to
the account: it *owes* its requirement, up to the risk budget, and if the bad
market move happens it *loses* money, also up to the risk budget, because the
scenario part is by definition the worst loss. Both have to fit inside equity.
With equity 100 and a risk budget of 50: fill to 50, the worst move loses 50,
equity is 50 and the requirement is 50 — exactly equal. With a factor of 1.5 the
budget is 66.7, the same move leaves equity at 33.3 against a requirement of
66.7, and the account is in breach.

The result: **the requirement stays inside equity after any market move the
scenario set covers, with no central call per order.** The price: only about
half of equity is usable. In E1's binding trial every order fills at the worst
price and fee the policy allows; the budgets reach 99% and the requirement is
49% of equity, with no breach. Appendix D carries the algebra.

**Model risk, stated.** Every correctness experiment in §2–§5 uses a simple
scenario set: seven points on one factor. The split and the closure hold for
*any* finite scenario set; E8 re-runs E1's oracle on seventeen scenarios with two
factors, loadings of both signs and idiosyncratic moves, and finds no breach in
600 trials, including 300 driven to 99% of the risk budget, while a control that
drops the factor of two breaches in 6 of 300. What is not shown is that any
particular set is adequate for 40 underlyings — that is a calibration question.
**When the set is wrong, the architecture does three things:** a move outside it
is handled by liquidation and the insurance fund (§5.4), which is what E9's
flash crash exercises; the set is versioned data on the log (§4.1), so it can be
widened between sessions and a replay still reproduces old decisions; and a
wider set costs capacity, not latency, because admission is one pass over the
set (E3: 7 → 16 scenarios, +34%).

## 2.3 What a lease grants

A lease is three budgets per account per gateway, for a term of 50–200 ms. **A
lease cannot undo an admission it has already granted.** Everything below
follows from that.

| Budget | Bounds | Why it is separate |
|---|---|---|
| Risk | worst-case scenario loss of everything the gateway holds | the part that can be split |
| Size (gross) | the largest total size the gateway's orders can reach | an order can lower risk while raising size, and the add-on is charged on size |
| Cost | fees and slippage the gateway's orders can still incur | it lowers equity, and neither of the others covers it |

The three are issued at fixed ratios, so the allocator searches one number per
account, not three.

**What a gateway checks is orders, not positions.** Two resting orders of
opposite sign net to nothing, but if only one fills the account carries the
other side. So each budget is checked against the worst subset of fills that
could still happen. Because loss under one market move is linear, that needs no
enumeration; the closed form matches brute force over every possible subset of fills on
4,000 random books.

**Absolute figures, not the increment an order adds.** An order that flips a leg
from short to long leaves the increment unchanged while the account's
requirement jumps to its maximum (c1). Admission compares the whole worst-case
figure against the budget.

**Size is reserved at the highest price the scenario set reaches.** The add-on
depends on size, size depends on price, and prices move during a term. In the
worked case m1, reserving at today's price admits 296 lots and ends 382,143 above
equity once the market reaches the edge of the set; reserving at the highest
price admits 249 lots and ends 5,842 inside. The cost is 47 lots, about 16% of
capacity (ADR-3).

**The equity the budgets are solved against is mark-to-market equity**, not
collateral: collateral plus cash and position value at current marks, less fees.
That is why the mark-price pipeline (§2.5) sets how much capacity exists.

## 2.4 Ending authority

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

## 2.5 Mark prices

Marks carry no authority on the admission path — the check reads no market
state — but they set mark-to-market equity, where the scenario set is centred,
and the highest price size is reserved at. So **a wrong mark changes how much
capacity is solved for**, and a mark held too high is §6.3 A1 by another route:
E5 measures a breach that tracks an equity overstatement one for one.

| Decision | What we get | What we pay |
|---|---|---|
| Several independent index sources per underlying, each checked for staleness on its own | one frozen or manipulated feed cannot set the mark | more feeds to operate and reconcile |
| The mark is a trimmed statistic across the live sources, with a bound on how far it may move per publish | a single outlier print is dropped | a genuine fast move is followed with a lag of a few publishes |
| Marks are versioned data on the log, activated at a sequence (§4.1) | every admission and liquidation replays against the mark it saw | a correction is a new record, not an edit |
| Fewer than a quorum of live sources: the allocator stops issuing and existing leases run out their term | capacity is never solved against a mark nobody can confirm | order entry for affected accounts stops within one term |
| Divergence across sources pages (§8.2, alert 4) | an operator sees the attack or the outage | — |

The number of sources, the trim and the per-publish bound are operational
parameters chosen per underlying; none of them is derived here. The pipeline is
designed and not built: the simulator takes marks as given.

## 2.6 Components

| Component | Holds | Decides |
|---|---|---|
| Ingress gateway | three ceilings per account; worst-fill running totals over the grid | admit or refuse, by comparing absolute figures against ceilings. Its state is a deterministic fold of the log, so a snapshot bounds replay time rather than being a correctness requirement |
| Ordering point | the log; the lease registry; sessions; fences and seals | that an admission is the next number for a live, correctly bound lease; that a fill matches its recorded terms; that a basket commits as one record. Writes nothing when it refuses |
| Margin allocator | committed exposure per holder; generations; credit versions | the condition of §2.2, once per account per issuance. Sharded by account, ≈ 16 shards |
| Liquidator | the merged account view | which basket to transfer, checked against the merged account rather than a ceiling — the check c9 shows a gateway cannot make. Inside the trusted computing base on its own account (§6.1) |
| Mark-price pipeline | marks, per source and published | nothing on the admission path, but marks set equity and the reserve, so this path fixes how much capacity is solved for (§2.5) |
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

**Batching.** Replicating a batch of commands amortises the round trip (Part 4
§3). Two rules keep it compatible with this design: the ordering point numbers a
batch at entry and refuses the whole batch on any gap, so a seal never covers a
hole; and a fence takes effect between batches, never inside one. Neither is
implemented; the simulator commits one admission at a time.

## 2.8 What this architecture does not do

It does not identify who is behind an order, price liquidity, or make the
matching core elastic. The liquidation waterfall below the unwind — the venue
book's limits, the insurance fund and ADL — is designed in §5.4 and not built.
