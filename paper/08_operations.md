# 8. Operations

## 8.1 The first chaos experiment

**Measure the replay rate**, because §5.5 assumes it at ten times live and
nothing else in the recovery arithmetic is assumed rather than derived.

Procedure: take a snapshot and the following five minutes of log from a
production-shaped load generator, cold-start a node, and time the rebuild
split into snapshot read, scenario-vector reconstruction, and log replay.
Compare the state hash of the rebuilt node against the live one.

Two outcomes and what each changes. At or above 10× live, the five-minute
snapshot cadence stands. At 2×, the cadence tightens to about one minute and
nothing else in the design moves. Either way the experiment also exercises the
state-hash comparison, which is the mechanism that detects replica divergence.

**The second experiment**, once the first has a number: partition one gateway
from the allocator during peak and hold it there past lease expiry. What should
be observable is the gateway entering reduce-only on its own, cancels and
closing orders continuing to be accepted throughout, and no order admitted
against an expired schedule. This is the failure path §5.5 describes and the
CAP position §3.3 defends; it is the one a panel is likeliest to ask whether we
have actually run.

## 8.2 Alerts, business invariants first

Five, in the order they would page:

1. **Sum of issued schedules above the budget solved.** An arithmetic
   impossibility if the allocator is correct, so firing means the allocator is
   wrong. Page immediately.
2. **A gateway's consumption above the schedule it holds.** Same class.
3. **Requirement above equity outside a liquidation window.** The invariant the
   whole design exists to hold. Page immediately.
4. **Market-state staleness on any gateway beyond a bound.** New in this design,
   because the market-data path now carries authority (§6.1). A gateway acting
   on a stale state is acting on stale capacity.
5. **Reduce-only rate rising across accounts without a corresponding market
   move.** The signature of mark suppression (§6.3 A2): the published state
   failed to advance, so the reduction it should have caused arrives late and
   all at once.

Alerts 1 to 3 are the invariants the simulator checks after every admitted order,
promoted to production. That is deliberate: the same predicate that gates a test
run gates the live system, so a failure in production is expressible as a failing
test case.

Below these sit the ordinary ones — latency percentiles, queue depths, replica
lag — which are diagnostics rather than invariants and do not page on their own.

## 8.3 Volatility-day playbook

**Before the session.** Pre-scale gateways; the matching core is partitioned, not
elastic, and does not scale on demand. Re-derive the schedule shape for the day's
expected range and, if it needs to change, activate the new shape identifier at a
log sequence before the open — it cannot be changed during the session (§5.6).
Confirm every mark source is live and independent; alert 4 is only useful if the
sources are.

**During.** Expect the schedule to do the work: capacity contracts as the state
index advances with no operator action and no allocator message. The operator's
job is to watch alert 5, because that is the one that catches the feed failing
rather than the market moving.

If the insurance fund is drawn beyond a threshold, the lever is the shape
identifier for the next session, not a runtime change.

**Halt and reopen.** A symbol halt leaves cancels accepted and no new orders. On
reopen, schedules are re-issued against post-auction marks before order entry
resumes, because the auction can move equity by more than a schedule band.

**After.** Reconcile holds against consumed leases (§4.6 check 4). A day with
heavy reduce-only activity is the most likely day for a lease-accounting bug to
have manifested, and it is the cheapest time to find one.

## 8.4 What operations cannot do in this design

Stated plainly, because it is the operational cost of ADR-2 and ADR-4:

- The schedule shape and the market-state banding cannot be tuned during a
  session. They are versioned data on the log, activated at a sequence.
- An operator has no write path to the ledger. Corrections are journalled,
  dual-approved commands, not statements.
- Capacity cannot be granted to a single account by hand. It comes from the
  schedule condition or it does not come.

The first of these is the one a venue would push back on hardest. The answer is
in ADR-2: a runtime-tunable shape makes replay non-deterministic, and replay is
what §6.4 offers a supervisor.
