# 6. Security and threat model

## 6.1 Trust boundaries and the trusted computing base

| Boundary | If the far side is fully compromised |
|---|---|
| Client ↔ gateway | The account's own ceilings. Ceilings are per account, so one client cannot consume another's |
| Gateway ↔ ordering point | **Inside the trusted computing base.** See below |
| Ordering point ↔ everything | Total. Every safety claim rests on it |
| Liquidator ↔ ordering point | The account it is liquidating, in full |
| Market data ↔ allocator | How much capacity is solved for. Not the admission check itself |
| Allocator ↔ gateway | An account's whole capacity |
| Chain ↔ custody | Venue assets. Unchanged from the running case |
| Operator ↔ ledger | Nothing; no write path. Detection is reconciliation, not prevention |

The gateway and the liquidator both sit inside the trusted computing base.
Nothing downstream recomputes a gateway's envelope arithmetic, so a gateway that
lies about it is believed. Tests show that it still cannot act under a fenced or
unregistered lease, use another account's or another holder's lease, commit a
liquidation basket, or record a fill outside the terms set at admission. It is,
however, bounded by neither its budgets nor its term, because the ordering point
has no clock with which to enforce a term, and only a fence at the ordering
point stops it. The mechanism therefore limits which accounts a compromised
gateway can touch but not how much damage it can do. Closing that gap would put
the margin calculation back on the single-writer path, which is what §2.2
exists to avoid. The liquidator is checked against the whole account rather
than a budget, so it cannot raise the account's risk but can churn it, and the
barrier refuses to settle while it is live. Appendix F gives the full argument
and the test behind each claim.

## 6.2 Who can move money

Three ways, none of them the margin path: a trade, journalled by clearing; a
deposit or withdrawal, frozen then reviewed then gated then signed once per
withdrawal ID; and a liquidation or insurance-fund draw, journalled like any
other posting. No component writes a balance. The ceremony scales with the
amount: withdrawals above a threshold need two-person approval, every operator
action is itself a journalled, dual-approved command, and cold-wallet signing is
m-of-n on HSMs, so no single operator and no compromised gateway can move funds.

A withdrawal reduces equity, so outstanding capacity against the old figure must
stop before funds leave. Two orderings work and differ in latency, not safety:
fence, reconcile, re-issue, release — immediate, at the cost of a fence round
trip; or wait for the terms to end, then re-issue and release — free, binds
within the term. Releasing first is not available.

## 6.3 Business-logic abuse cases

### A1 — Overstating equity

Every ceiling is solved against $E_0$, and nothing downstream re-derives it. An
account reporting more equity than it has buys a ceiling it cannot carry.

E5 measures it. An account that forgets a realised loss reports 92,000 where it
has 42,000, is issued 46,000 instead of 21,000, admits 230 orders instead of 105,
and ends 4,000 above equity. At the binding point the breach tracks the
overstatement approximately one for one, subject to lot rounding: overstating by
10,000 produces a breach of 10,000, while 1,000 produces 800, because the ceiling
cannot move until the overstatement buys a whole lot of requirement.
**The factor of two is a closure, not a margin against a misreported account**;
an earlier draft that read it as a 64% tolerance was reading unused workload
slack.

The exposure is to anything that moves $E_0$: a suppressed mark, a lost fee, a
realised loss not yet folded in. The defences are §4.3's cash-flow identity, the
account being an independently rebuildable fold of the log, and multi-sourced
marks. None is a proof; a compromised equity path is a compromised mechanism.

### A2 — Routing to concentrate an account's flow

A gateway gets no credit for offsets held on other gateways, so a client who can
steer hedged legs onto one gateway — by picking it, or retrying until it lands
there — gets more usable capacity than one who cannot. A **fairness** problem,
not a solvency one: the requirement stays bounded. Account affinity would narrow
it; not built.

### A3 — Stalling a liquidation

Orders whose cancels the matching side never acknowledges keep the account
fenced indefinitely: the settlement reserves for them (l13). The venue's exposure
is bounded by that reservation, but the account cannot be closed out. Detection
is §8.2's fenced-but-not-settled alert; there is no mitigation. Fee-cap gaming
through many small fills is bounded by the per-lot cap (d4).

## 6.4 Regulatory posture

A supervisor gets **reproducibility of what was admitted**: every admission is on
the log with its lease, holder and terms, and the values it was derived from are
versioned there (§4.1), so a three-month-old decision can be recomputed. Not yet
for a **refusal**: a gateway's refusal reaches no log (NFR row 10). And not a
guarantee that clients can always exit (§3.3) — a supervisor asking should be
told no.

## 6.5 Detection

Six signals, all paged on and listed in §8.2: equity divergence between what the
allocator solved against and what a rebuild from the log implies; ceilings
summing above the solve; a gateway's figures above its ceilings; a lease fenced
but not settled beyond a bound; mark divergence across sources; and any
authority-binding refusal, which should be zero. The gap this list cannot close
is §6.1's: nothing detects a gateway that lies about arithmetic nobody
re-derives, until the account's own figures move.
