# Appendix C — Protocols in full

The main body states what each protocol guarantees and cites the test that pins
it. This appendix carries the step-by-step form, for a reader checking the
guarantee against the mechanism rather than reading the argument.

## C.1 The idempotency chain, in full

End to end, for one client order. Every link is a named identifier and a
decision point that has been tested rather than argued.

1. **The client** assigns a client order ID and retries with the same ID on
   timeout. A timeout is an unknown outcome, not a failure.
2. **The gateway** checks its three envelopes and, if they hold, submits to the
   ordering point under `(lease_id, admission_seq)` where `admission_seq` is the
   next number it has used under that lease.
3. **The ordering point** accepts that pair only if it is the next number for
   the lease, and only if the lease is not fenced. A retry carrying the same
   payload under the same pair succeeds again and records once; the same pair
   with a different payload is a conflict and records nothing. The gap-free
   sequence is what later lets a seal claim to cover every admission the lease
   produced.
4. **A fill** is submitted under a `fill_id` against an `order_id`. The ordering
   point refuses it — writing nothing and moving nothing — if the identifier has
   been used with different figures, the order is unknown or cancelled, the
   direction disagrees, the cumulative quantity would exceed what was admitted,
   the price falls outside the band recorded at admission, or the fee exceeds the
   cap for that quantity. A retry with the same figures lands once
   (`tests/test_execution_debit.py`, d4 to d6).
5. **The order of operations is fixed**: the ordering point decides first, and
   only a fill it accepted is folded into the gateway and the account. An earlier
   implementation called the gateway first, which moved state the authority then
   refused.
6. **A liquidation basket** is committed under a `basket_id` as a single record.
   A retry with the same payload is idempotent; the same identifier with
   different figures is refused and moves nothing (`tests/test_liquidation.py`,
   l7). A process that dies between the commit landing in the log and the
   transfer being folded in locally rebuilds from the log, and `applied_baskets`
   plus the ledger's fill keys make the reapplication land once (l14).
7. **Replay is idempotent by log position.** An earlier version relied on the
   order ID for that, which covers admissions and not fills or cancels, and
   replaying the same slice twice applied the fills twice
   (`tests/test_recovery.py`, r2).
8. **The matching shard** applies a given client order ID at most once, so no
   duplicate book action occurs regardless of how many times the order was
   admitted upstream.
9. **Clearing** derives ledger entry IDs deterministically from the trade
   sequence, so a replayed trade produces the same entry ID and is deduplicated
   (running case, Part 3 §4).

Step 2 is where this design differs from the running case, and it is a cost we
state rather than hide: a retry routed through another gateway is a new admission
attempt at that gateway and may conservatively consume envelope there, though it
cannot produce a duplicate book action. That consumption is released at the next
issuance.

One asymmetry is deliberate and matters later. A cancel *request* releases
nothing; only an acknowledgement recorded at the ordering point does, because
until then the order can still fill. That produces two different failures which
are not the same fact and are tested separately (§5.4).

Exactly-once is, as in the running case, end-to-end idempotency layered on
at-least-once delivery. Nothing in the margin path changes that.

## C.2 The settlement barrier, in full

A seal is portable evidence for releasing **one** lease: the ordering point
issues it when a lease is fenced, naming the last admission it recorded, and a
holder carries it to the allocator. The account-wide compaction does not use one,
because the allocator reads the same log the seal was cut from.

> A seal releases one lease. A globally fenced, ordered account barrier permits
> account-wide compaction.

`Allocator.settle` requires all of the following, and none of them is supplied by
the caller:

1. the account is in `settling`, so no new lease can be issued for it. Doing this
   first is what stops a lease minted after the barrier from being authority the
   settlement did not account for;
2. every lease ever minted for the account is fenced at the ordering point —
   every ingress lease, every incarnation, and the liquidator's own basket
   authority. `all_leases` is the set the barrier is taken over, not
   `authority`, because a lease that admitted nothing is still authority;
3. a barrier watermark `B` at which the recorded sequence under each of those
   leases is gap-free and no admission or basket for the account was recorded
   under a lease outside the set. `submit` and `commit_basket` already refuse
   anything but the next number, so this check should never fire; it is here
   because the settlement's whole claim is that the log is complete, and an
   assertion that never fires is cheap next to a claim that is assumed;
4. the aggregate rebuilt from the log at `B`: filled positions, orders with no
   authoritative cancel acknowledgement, worst-fill risk, repriced gross reach,
   and the execution cost still ahead of the account;
5. installation under a credit-version compare-and-set, so a settlement computed
   at an older barrier cannot overwrite a newer one;
6. issuance resumes only after the install.

E7 exercises the refusals as well as the successes: `no_fence` is refused with
two ingress leases live, and `liquidator_authority_live` is refused with the
liquidator's lease live even though every ingress lease is fenced and the
account is flat.

## C.3 Authority binding at the ordering point

The ordering point holds `lease_id -> (account, holder, kind)`, registered by the
allocator at issuance because the allocator is the single issuer and therefore
the only component that knows the binding. The holder on a submission is resolved
from the authenticated session and never read from the request body. A submission
is refused as `unknown_lease`, `wrong_account`, `wrong_holder`,
`wrong_authority_kind` or `unauthenticated` before any sequence check runs.

`kind` is `ingress` or `liquidation` and they are not interchangeable: an ingress
holder that could commit a basket would be admitting with no ceiling at all,
since the liquidator's admissions are checked against the merged account instead.

Deliberately not checked here: the lease's term. The ordering point has no clock
it can compare against an expiry set by another component, so an honest gateway
is bounded by its own term and a Byzantine one only by the fence (§6.1, ADR-6).

`tests/test_authority.py` pins six cases, including the one an external reviewer
demonstrated against the previous interface: a valid lease id submitted under a
different account, which used to return `(True, "ok")`.
