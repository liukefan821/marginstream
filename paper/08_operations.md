# 8. Operations

## 8.1 The first chaos experiments

**Measure the replay rate**, because §5.5 assumes it at ten times live and
nothing else in that arithmetic is assumed. Cold-start a node from a snapshot
plus five minutes of log, time the rebuild, and compare the state hash against
the live node — the comparison E4 makes 3,642 times. A slower rate only
shortens the snapshot cadence, since a snapshot bounds replay time, not
correctness.

**Second: partition a gateway from the allocator while it stays connected to the
ordering point**, and hold it past its term. This is the failure §3.3 is about.
What should be observable is the gateway admitting inside its ceilings until the
term ends and then admitting nothing at all, closing orders included.

**Third: fence an account's leases without telling any gateway** and confirm the
ordering point refuses what follows. E7 counts 50 refusals in simulation; in a
deployment this checks that the fence is a real serialisation point.

## 8.2 Alerts, business invariants first

1. **Sum of issued ceilings above what the condition solved for.** An arithmetic
   impossibility if the allocator is correct, so firing means it is wrong.
2. **A gateway's worst-fill figures above the ceilings it holds.** Same class.
3. **Requirement above equity outside a liquidation window.** The invariant the
   design exists to hold.
4. **Equity divergence** between what the allocator solved against and what a
   rebuild from the log implies, and **mark divergence** across sources. §6.3 A1
   is the reason both page rather than sit on a dashboard.
5. **A lease fenced but not settled beyond a bound**, and any **authority-binding
   refusal**. The first is a stuck liquidation holding capacity; the second
   should be zero in normal operation and otherwise means a component is
   submitting under a lease that is not its own.

Alerts 1 to 3 are the invariants the simulator checks after every admitted order,
promoted to production: the same predicate gates a test run and the live system,
so a production failure is expressible as a failing test case. Latency
percentiles, queue depths and replica lag are diagnostics and do not page.

## 8.3 Volatility-day playbook

**Before.** Pre-scale gateways; the matching core is partitioned, not elastic.
Confirm every mark source is live and independent. Decide the term for the
session: it is the tightening latency the venue accepts under partition (§1.5),
trading availability against how fast a credit decision binds.

**During.** Nothing to tune. A tightening is a re-issue against lower equity,
binding at the next issuance for reachable gateways and within the term for
unreachable ones. To bind faster, fence the affected leases.

**Liquidation** is venue-initiated. The operator decides when to trigger, how
aggressively to unwind, and whether to accept a stall: the unwind halves its
basket fraction on a failed check and stops at one lot rather than forcing a
reduction that would raise the requirement.

**Halt and reopen.** Cancels accepted, no new orders. On reopen, ceilings are
re-issued against post-auction marks before order entry resumes.

**After.** Reconcile per §4.5, and confirm every account that entered liquidation
reached a barrier: fenced but never settled is the state that silently costs
capacity.

## 8.4 What operations cannot do

Operations cannot grant capacity by hand, recall what an unreachable gateway has
already admitted, release an account's occupancy without a barrier, or write to
the ledger, where corrections are journalled, dual-approved commands. The middle
two were tried as manual overrides and produced c8, c11 and c12: an operator who
could declare a holder finished handed its capacity to a replacement while the
holder was still trading.
