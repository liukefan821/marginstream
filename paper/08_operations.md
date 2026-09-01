# 8. Operations

## 8.1 The first chaos experiment

**Measure the replay rate**, because §5.5 assumes it at ten times live and
nothing else in the recovery arithmetic is assumed rather than derived.

Procedure: take a snapshot and the following five minutes of log from a
production-shaped load generator, cold-start a node, and time the rebuild split
into snapshot read, aggregate reconstruction, and log replay. Compare the state
hash of the rebuilt node against the live one — the same comparison E4 makes
3,642 times against a gateway rebuilt from the whole log.

Two outcomes and what each changes. At or above 10× live, the five-minute
snapshot cadence stands. At 2×, the cadence tightens to about one minute and
nothing else moves, because a snapshot bounds replay time and is not a
correctness requirement.

**The second experiment**: partition one gateway from the ordering point during
peak and hold it past its term. What should be observable is the gateway
admitting inside its ceilings until the term ends and then admitting nothing at
all — including orders that look locally like risk reduction — and no order
reaching a book under a fenced or expired lease. This is the CAP position §3.3
defends, including the part it gives up, and it is the one a panel is likeliest
to ask whether we have run.

**The third**: fence an account's leases without telling any gateway, and confirm
the ordering point refuses the submissions that follow. E7 does this in
simulation and counts 50 refusals; in a deployment it is the check that the
fence is a real serialisation point and not a convention.

## 8.2 Alerts, business invariants first

Five, in the order they would page:

1. **Sum of issued ceilings above what the condition solved for.** An arithmetic
   impossibility if the allocator is correct, so firing means the allocator is
   wrong. Page immediately.
2. **A gateway's worst-fill figures above the ceilings it holds.** Same class.
3. **Requirement above equity outside a liquidation window.** The invariant the
   whole design exists to hold. Page immediately.
4. **A lease fenced but not settled for longer than a bound.** The account's
   capacity stays occupied until the barrier runs, so this is the alert that
   catches a stuck liquidation before it looks like a capacity shortage.
5. **Mark staleness or divergence across sources.** Marks set equity, and an
   overstated equity buys a ceiling the account cannot carry (§6.3 A1). This is
   the one that catches the feed failing rather than the market moving.

Alerts 1 to 3 are the invariants the simulator checks after every admitted order,
promoted to production. That is deliberate: the same predicate that gates a test
run gates the live system, so a failure in production is expressible as a failing
test case.

Below these sit the ordinary ones — latency percentiles, queue depths, replica
lag — which are diagnostics rather than invariants and do not page on their own.

## 8.3 Volatility-day playbook

**Before the session.** Pre-scale gateways; the matching core is partitioned, not
elastic. Confirm every mark source is live and independent, because alert 5 is
only useful if they are. Decide the lease term for the session: it is the
tightening latency the venue is accepting under partition (§1.5), and it is the
one number that trades availability against how fast a credit decision binds.

**During.** There is nothing to tune. Capacity does not contract with the market
by itself — the schedule that used to do that is withdrawn (ADR-2) — so a
tightening happens by re-issuing against lower equity, which binds at the next
issuance for reachable gateways and within the term for unreachable ones. If it
has to bind faster than that, the instrument is fencing the affected leases at
the ordering point, which is immediate and does not need any gateway to be
reachable.

**Liquidation.** Venue-initiated and not client-initiated (§3.3). The operator
decisions are when to trigger, how aggressively to unwind, and whether to accept
a stall — the unwind halves its basket fraction on a failed check and stops at
one lot rather than pushing through a reduction that would raise the account's
requirement. A stall means the book cannot be reduced without raising the
requirement, which is a signal to widen the basket rather than to force it.

**Halt and reopen.** A symbol halt leaves cancels accepted and no new orders. On
reopen, ceilings are re-issued against post-auction marks before order entry
resumes, because the auction can move equity by more than the grid covers.

**After.** Reconcile holds against consumed ceilings (§4.6 check 4), and confirm
every account that entered liquidation reached a settlement barrier. An account
fenced but never settled is the accounting state that silently costs capacity.

## 8.4 What operations cannot do in this design

- **Grant capacity to one account by hand.** It comes from the condition of §2.4
  or it does not come.
- **Recall a lease from an unreachable gateway.** Fencing at the ordering point
  stops what the lease can still do; it does not remove what it already did.
- **Release an account's occupancy without a barrier.** Not by clock, not by a
  report from the holder, not by an operator judgement that the holder is gone.
- **Write to the ledger.** Corrections are journalled, dual-approved commands.

The second and third are the ones a venue would push back on hardest, because
both look like an operator being denied a manual override during an incident.
The answer is that both overrides are exactly the ones that were tried in
earlier versions of this mechanism and produced counterexamples c8, c11 and c12:
an operator who can declare a holder finished is an operator who can hand its
capacity to a replacement while it is still trading.
