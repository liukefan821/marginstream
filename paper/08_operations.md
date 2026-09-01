# 8. Operations

## 8.1 The first chaos experiments

**Measure the replay rate**, because §5.5 assumes it at ten times live and
nothing else in that arithmetic is assumed. Cold-start a node from a snapshot
plus five minutes of log, time the rebuild, and compare the state hash against
the live node — the comparison E4 makes 3,642 times. At or above 10× the
five-minute snapshot cadence stands; at 2× it tightens to about a minute and
nothing else moves, because a snapshot bounds replay time and is not a
correctness requirement.

**Second: partition a gateway from the allocator while it stays connected to the
ordering point**, and hold it past its term. This is the failure §3.3 is about.
What should be observable is the gateway admitting inside its ceilings until the
term ends and then admitting nothing at all, closing orders included.
Partitioning a gateway from the *ordering point* tests nothing about margin: no
order reaches a book at all.

**Third: fence an account's leases without telling any gateway** and confirm the
ordering point refuses what follows. E7 does this in simulation and counts 50
refusals; in a deployment it is the check that the fence is a real serialisation
point and not a convention.

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
session: it is the tightening latency the venue is accepting under partition
(§1.5), and it is the one number trading availability against how fast a credit
decision binds.

**During.** There is nothing to tune. A tightening happens by re-issuing against
lower equity, which binds at the next issuance for reachable gateways and within
the term for unreachable ones. If it must bind faster, the instrument is fencing
the affected leases, which is immediate and needs no gateway to be reachable.

**Liquidation** is venue-initiated. The operator decides when to trigger, how
aggressively to unwind, and whether to accept a stall: the unwind halves its
basket fraction on a failed check and stops at one lot rather than forcing a
reduction that would raise the requirement. A stall is a signal to widen the
basket, not to push.

**Halt and reopen.** Cancels accepted, no new orders. On reopen, ceilings are
re-issued against post-auction marks before order entry resumes, because the
auction can move equity by more than the grid covers.

**After.** Reconcile per §4.6, and confirm every account that entered liquidation
reached a barrier. Fenced but never settled is the state that silently costs
capacity.

## 8.4 What operations cannot do

- **Grant capacity to one account by hand.** It comes from the condition of §2.4
  or not at all.
- **Recall a lease from an unreachable gateway.** Fencing stops what it can still
  do; it does not remove what it already did.
- **Release an account's occupancy without a barrier** — not by clock, not by a
  holder's report, not by an operator's judgement that the holder is gone.
- **Write to the ledger.** Corrections are journalled, dual-approved commands.

The middle two look like an operator denied a manual override during an incident.
Both overrides were tried in earlier versions and produced c8, c11 and c12: an
operator who can declare a holder finished is an operator who can hand its
capacity to a replacement while it is still trading.
