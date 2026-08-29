# 3. Consistency map

## 3.1 Per flow

One row per data flow, with the model chosen and the reason it is survivable.
"Weakest the business can survive" is the test, not "strongest available".

| Flow | Model | Defence |
|---|---|---|
| Matching, per symbol | Single-writer total order | Price-time priority *is* a total order; anything weaker changes which client got the fill. Inherited from the running case |
| Credit consumption at a gateway | Local atomic, serialisable within the gateway | The quantity is one integer owned by one gateway. It never needs to be read by anyone else during the epoch, so there is nothing to coordinate |
| Schedule issuance and generation transition | Single authoritative allocator per account, linearisable | Two issuers for one account could double-issue capacity against the same collateral. This is the one place the design cannot weaken |
| Market state index | Bounded-stale, monotone within a lease | Staleness is bounded by the publisher's cadence, and the ratchet makes the value monotone at the reader, so lateness is conservative and earliness is impossible |
| Position feed into the allocator | Bounded-stale, eventually consistent | Lag makes the schedule stale in the safe direction: it reflects a smaller portfolio than exists, so the budget solved is smaller than the account could have supported. Lag in the unsafe direction is impossible because trades only add requirement |
| Audit journal | Durable append-only, linearisable per shard | Replay must reproduce the decision exactly (§5.2), which fails if entries can be reordered |
| Audit projections and dashboards | Eventually consistent | An operator reading a two-second-old capacity figure makes no decision that the system will not re-check |
| Ledger balances | Fold of the journal; strongly consistent for the fold, eventually consistent for materialised views | Running case, Part 3 §1 |
| Retry handling | At-least-once transport with end-to-end idempotency | Exactly-once is not a transport property. The matching shard applying a client order ID at most once is where "exactly once" is actually enforced |

The one row worth arguing rather than asserting is the position feed. A reviewer
will ask whether a stale position feed can under-state the requirement and let
too much through. It cannot, because the schedule condition of §2.4 subtracts
`R(P)` for the portfolio the allocator knows about, and any position the
allocator has not yet seen was admitted against a lease, so it is already
covered by the `2 * sum lambda` term. Lag costs capacity; it does not cost
safety.

## 3.2 CAP position of the matching core

Unchanged from the running case, and stated in one sentence:

> During a partition that costs the leader its majority, the matching core stops
> accepting orders rather than risk divergence.

Halting is embarrassing; double-executing trades is existential.

## 3.3 CAP position of the admission plane

This is the sentence that is specific to this design:

> During a partition between a gateway and the allocator, the gateway stops
> accepting risk-increasing orders and keeps accepting risk-reducing ones, for as
> long as its schedule remains valid and then unconditionally.

Two things follow, and they are the business-facing part of the answer.

**The admission plane chooses availability for one direction of traffic and
consistency for the other.** That is not a fudge of CAP; it is two different
operations with different invariants. Admitting a risk-increasing order requires
knowing the account's capacity, which requires the allocator. Accepting a cancel
or a closing order reduces the requirement monotonically, so it is safe under any
stale view — the invariant can only move in the direction that satisfies it.

**Why a venue should prefer this to halting.** A venue that blocks risk reduction
during stress manufactures the disorderly market it is trying to prevent: clients
who cannot close are pushed to hedge elsewhere or not at all, and the venue's own
liquidation engine ends up competing with them for the same liquidity. Refusing
new risk while accepting risk reduction is the narrower intervention, and it is
the one a supervisor is likelier to accept. This is our argument, not a rule we
are quoting; §6.4 says the same thing from the regulatory side.

## 3.4 Where the design refuses to weaken

Three places, each with what breaks if it is relaxed:

1. **One issuer per account.** Two allocators for one account can issue two
   schedules against the same collateral. No amount of downstream checking
   recovers from that, because each schedule is individually valid.
2. **Monotone generations.** If a gateway can act under a superseded generation,
   E2 shows the requirement reaching 10,047 against 10,000 of equity in a
   two-shard scripted case.
3. **Append-only, ordered audit.** Reordering the journal makes a decision
   unreproducible, which fails NFR row 10 and, per §6.4, is the thing a
   supervisor would actually ask for.

Everything else in the table above is chosen at the weakest model that survives.
