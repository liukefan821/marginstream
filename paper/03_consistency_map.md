# 3. Consistency map

## 3.1 Per flow

One row per data flow, with the model chosen and the reason it is survivable.
"Weakest the business can survive" is the test, not "strongest available".

| Flow | Model | Defence |
|---|---|---|
| Matching, per symbol | Single-writer total order | Price-time priority *is* a total order; anything weaker changes which client got the fill. Inherited from the running case |
| Envelope consumption at a gateway | Local atomic, serialisable within the gateway | Three integers and a per-scenario vector owned by one gateway. Nobody else reads them during the term, so there is nothing to coordinate |
| Admission sequence per lease | Gap-free total order at the ordering point | The ordering point accepts only the next number for a lease. Everything downstream — the seal, the barrier, the settlement figure — rests on the log holding every admission that lease produced, and a gap would make that claim false |
| Fill terms | Checked at the ordering point against the terms recorded at admission | A fill reported by a component that could be wrong or compromised is not evidence. Price band, fee cap, direction, over-fill and fill identity are all decided there, and a refused fill writes nothing and moves nothing |
| Lease issuance and generation transition | Single authoritative allocator per account, linearisable | Two issuers for one account could double-issue capacity against the same collateral. This is the one place the design cannot weaken |
| End of authority | Fence at the ordering point, linearisable | A clock comparison is not evidence that a partitioned holder has stopped; a fence is, because nothing reaches a book except through that component. The fence does not need to be delivered to the holder |
| Marks into the allocator | Bounded-stale | Marks set equity, the scenario displacements and `mark_plus`. Staleness costs capacity in both directions and does not cost safety inside the grid, because gross is reserved at the highest mark the grid reaches rather than at the mark the solve saw |
| Position feed into the allocator | Bounded-stale, eventually consistent | A fill the allocator has not seen was admitted under a lease and is still covered by that lease's absolute worst-fill, gross and debit ceilings. Until a terminal ordered reconciliation, the allocator neither releases that occupancy nor issues it to anyone else |
| Holder occupancy | Over-approximated per holder while any holder is live; compacted from the log once none is | Per-holder figures are summed and do not net, which is necessary while an unreachable holder may still be acting. Once every lease for the account is fenced, the log is the whole truth and §5.4 replaces the sum with it |
| Audit journal | Durable append-only, linearisable per shard | Replay must reproduce the decision exactly (§5.2), which fails if entries can be reordered |
| Audit projections and dashboards | Eventually consistent | An operator reading a two-second-old capacity figure makes no decision the system will not re-check |
| Ledger balances | Fold of the journal; strongly consistent for the fold, eventually consistent for materialised views | Running case, Part 3 §1 |
| Retry handling | At-least-once transport with end-to-end idempotency | Exactly-once is not a transport property. §5.3 gives the chain |

Two rows are worth arguing rather than asserting.

**The position feed.** A reviewer will ask whether a stale position feed can
under-state the requirement and let too much through. The answer is not that lag
produces a smaller ceiling — that is neither the reason nor reliably true. The
reason is that the ceilings are absolute rather than incremental and the
accounting is monotone: a fill the allocator has not seen was admitted under some
lease, is inside that lease's worst-fill, gross and debit ceilings by
construction, and stays charged to that holder until a terminal ordered
reconciliation replaces the ceiling with a measured figure. The allocator never
treats an unseen fill as absent capacity, because it never releases a holder's
occupancy on the strength of not having heard from it.

**Holder occupancy.** The over-approximation is not conservatism for its own
sake. While a holder can still admit, the allocator cannot see what it is doing,
so a position one holder created cannot be assumed to offset a position another
created. The cost of that is visible after a liquidation: each lease reconciles
to its own gross leg, the offsetting legs sit under the liquidator, and an
account holding nothing still looks fully occupied. That is what the settlement
barrier of §5.4 exists to clear, and it is why the barrier requires *every*
authority to be terminal rather than most of them.

## 3.2 CAP position of the matching core

Unchanged from the running case, and stated in one sentence:

> During a partition that costs the leader its majority, the matching core stops
> accepting orders rather than risk divergence.

Halting is embarrassing; double-executing trades is existential.

## 3.3 CAP position of the admission plane

An earlier version of this document said that during a partition a gateway keeps
accepting risk-reducing orders. That is withdrawn. It is not safe, and the
counterexample is in the test suite as c9: an order that lowers one gateway's
requirement can raise the account's, by closing a leg whose hedge is held on
another gateway. A gateway does not have the account's portfolio and cannot tell
the two apart.

The sentence that is specific to this design is therefore narrower and less
comfortable:

> During a partition between a gateway and the allocator, the gateway keeps
> admitting inside the ceilings it already holds until its term ends, and then
> admits nothing. It does not fall back on a local judgement that an order
> reduces risk. Risk reduction for the account is performed by the liquidator,
> which holds the merged view, and whose own authority is ended at the ordering
> point rather than by a clock.

Three things follow.

**Availability during a partition is bought before the partition, not during
it.** The gateway can keep serving because it was handed a ceiling that was
solved to be safe on its own. When the term runs out, availability ends. That is
the honest form of the trade: the term length *is* the availability budget, and
it is the same number as the enforcement latency of a tightening (§2.5).

**Risk reduction is an account-level operation, so it needs an account-level
authority.** Putting it at the gateway is the thing that does not work. Putting
it in the liquidator does, at the price of a component that is not bounded by the
capacity accounting at all (§6.1). We would rather name that price than keep a
sentence that reads better and is false.

**What this does and does not preserve.** It has to be said precisely, because
the comfortable version is false. When a term ends with no new issuance, *all*
client order flow through that gateway stops, closing orders included. The
liquidator is not a client-facing reduce-only API and this document does not
contain one: it is a venue-initiated internal transfer that the venue decides to
run. So what the design preserves during a partition is **the venue's ability to
bound its own loss**, not the client's ability to close a position.

A client-facing reduce-only path is possible and is not built. It would need the
same account-level check the liquidator performs, which means a central
component the gateway can reach; if that component is reachable the account is
not partitioned in the way that matters, and if it is not, only venue-initiated
liquidation remains. §6.4 argues from the regulatory side that a venue should
want such a path; this design does not provide one.

## 3.4 Where the design refuses to weaken

Three places, each with what breaks if it is relaxed:

1. **One issuer per account.** Two allocators for one account can issue two
   sets of ceilings against the same equity. No amount of downstream checking
   recovers from that, because each set is individually valid.
2. **Authority ends at the ordering point, not on a clock.** If a fenced lease
   can still produce an admission, nothing downstream is recoverable: the seal
   names an admission count that is already wrong and the settlement figure is
   computed over an incomplete log. E7's `no_fence` arm is the measured version
   — 7,790 drawn on the insurance fund against 0 in the base run, 75 transfers
   instead of 24, and a settlement that cannot run at all.
3. **Append-only, ordered audit.** Reordering the journal makes a decision
   unreproducible, which fails NFR row 10 and, per §6.4, is the thing a
   supervisor would actually ask for.

Everything else in the table above is chosen at the weakest model that survives.
