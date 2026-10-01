# 3. Consistency map

## 3.1 Per flow

One row per flow, with the model chosen and why it is survivable. The test is
"weakest the business can survive", not "strongest available".

| Flow | Model | Defence |
|---|---|---|
| Matching, per symbol | Single-writer total order | Price-time priority *is* a total order; anything weaker changes which client got the fill |
| Envelope consumption at a gateway | Local atomic within the gateway | Three integers and a vector owned by one gateway; nobody else reads them during the term |
| Admission sequence per lease | Gap-free total order at the ordering point | It accepts only the next number for a lease. The seal, the barrier and the settlement figure all rest on the log holding every admission that lease produced |
| Authority binding | Registry at the ordering point, holder from the authenticated session | A lease id alone is a bearer token: without the binding, knowing one is enough to submit for any account (Appendix C.3) |
| Fill terms | Checked at the ordering point against terms recorded at admission | A fill reported by a component that could be compromised is not evidence. Band, fee cap, direction, over-fill and identity are decided there; a refused fill writes nothing |
| Lease issuance and generation | Single allocator per account, linearisable | Two issuers could double-issue against the same equity. The one place the design cannot weaken |
| End of authority | Fence at the ordering point, linearisable | A clock comparison is not evidence a partitioned holder has stopped; a fence is. It need not be delivered to the holder |
| Marks into the allocator | Bounded-stale | Staleness costs capacity in both directions and, inside the grid, not safety — for two reasons that are both needed: the add-on is reserved against $G^{+}$, the highest gross the grid reaches, and `R` already covers the equity the account loses at any scenario in it |
| Position feed into the allocator | Bounded-stale, eventually consistent | A fill the allocator has not seen was admitted under a lease and is inside that lease's absolute ceilings. Until a terminal ordered reconciliation it stays charged to that holder |
| Holder occupancy | Over-approximated per holder while any is live; compacted from the log once none is | Per-holder figures are summed and do not net, which is necessary while an unreachable holder may still be acting (§5.4) |
| Audit journal | Durable append-only, linearisable per shard | Replay must reproduce the decision exactly, which fails if entries reorder |
| Dashboards and capacity displays | Don't-care: eventually consistent, may lag | No decision depends on them; anything a client acts on is re-checked at admission |
| Balances that authorise money, such as a withdrawal | Strong: read only once the ledger's fold has reached the account's latest fill | Paying out against a stale balance could release money the account no longer has (§6.2) |
| Retry handling | At-least-once delivery with end-to-end idempotency | Exactly-once is not a transport property (§5.3) |

The row worth arguing is the position feed. A stale feed cannot under-state the
requirement, and the reason is **not** that lag produces a smaller ceiling —
neither the mechanism nor reliably true. It is that the ceilings are absolute
rather than incremental and the accounting is monotone: the allocator never
releases a holder's occupancy on the strength of not having heard from it.

## 3.2 CAP position of the matching core

Unchanged from the running case: during a partition that costs the leader its
majority, the matching core stops accepting orders rather than risk divergence.
Halting is embarrassing; double-executing trades is existential.

## 3.3 CAP position of the admission plane

The relevant partition is **between a gateway and the allocator, with the
ordering point still reachable**. A gateway cut off from the ordering point
cannot reach a book at all and is not serving in any sense.

> During a gateway–allocator partition, the gateway keeps admitting inside the
> ceilings it already holds until its term ends, and then admits nothing. It does
> not fall back on a local judgement that an order reduces risk.

It cannot tell a risk-reducing order from one that closes a leg whose hedge sits
on another gateway (c9), so it does not try.

**Availability is bought before the partition.** The gateway serves because its
budget was solved to be safe on its own; the term length *is* the availability
budget, and the same number as the tightening latency of §1.5. **When the term
ends, all order flow through that gateway stops, closing orders included.** What
survives a partition is the venue's ability to bound its own loss, through the
liquidator — not the client's ability to exit. A client-facing reduce-only path
would need the account-level check the liquidator makes, so a central component
the gateway can reach; it is not built.

## 3.4 Where the design refuses to weaken

1. **One issuer per account.** Two allocators can issue two sets of ceilings
   against the same equity; each is individually valid, so no downstream check
   recovers.
2. **Authority ends at the ordering point, not on a clock.** If a fenced lease
   can still admit, the seal names a count that is already wrong and every figure
   computed from the log is computed over an incomplete one. E7's `no_fence` arm
   draws 7,790 on the insurance fund against 0 in the base run, takes 75
   transfers instead of 24, and cannot settle at all.
3. **Append-only, ordered audit.** Reordering makes an admitted decision
   unreproducible, which is the whole of what §6.4 offers a supervisor.

Everything else is chosen at the weakest model that survives.
