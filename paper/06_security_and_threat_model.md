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

**The gateway is inside the trusted computing base.** An earlier version of this
document claimed its blast radius is bounded by the leases it holds. That is
withdrawn. Nothing downstream re-derives the envelopes: the ordering point checks
that an admission is the next number for a live, correctly bound lease and that a
fill matches the terms recorded at admission; it does not recompute the
worst-fill figures the gateway compared against its ceilings. **A gateway that
lies about its own envelope arithmetic is believed.**

What a compromised gateway cannot do — each pinned by a test:

- act under a fenced lease, or under a lease id the allocator never minted (t3,
  t4);
- use another account's lease, or another holder's, or submit with no
  authenticated session (t1, t2);
- commit a liquidation basket under an ingress lease (t5);
- fill outside the band or above the fee cap recorded at admission, fill in the
  wrong direction, over-fill, or land the same fill twice (d4–d6);
- hide an admission, because the ordering point takes only the next sequence
  number for its lease.

What it can do, stated without softening: **a compromised gateway is bounded by
neither its ceilings nor its own term.** It can submit arbitrary quantity for
every account whose lease it holds, and it will not stop at its expiry, because
the ordering point does not enforce terms — it has no clock it can compare
against an expiry set elsewhere (ADR-6). A term bounds an *honest* gateway that
has lost contact. The only thing that stops a dishonest one is a fence
committing at the ordering point.

So the mechanism bounds the **scope** of the damage — which accounts, which
authority kind, which holder — and does not bound its **magnitude**. The exposure
window is detection latency plus fence-commit latency, and nothing in this
document measures either. Closing this would mean re-deriving
the envelopes at the ordering point, which puts the per-order margin computation
back on the single-writer path — the thing §2.2 exists to avoid. That trade is
not made here; it is the residual.

**The liquidator is inside it too, on its own account.** Its orders are checked
against the merged account rather than a ceiling (c9), so no ceiling bounds it.
The non-increase test bounds the *risk* it creates — neither merged envelope may
rise — but permits unlimited churn, and churn costs execution. Its authority ends
at the ordering point like any other's, and the barrier refuses to run while it
is live (t5, l11): containment of duration, not of authority.

**The market-data path** carries no authority in the running case. Here marks set
equity, the scenario displacements and `G+`, so a wrong value changes how much
capacity is solved for — weaker than an earlier draft claimed, since the
admission check reads no market state, but it is the exposure in §6.3 A1.

## 6.2 Who can move money

Three ways, none of them the margin path: a trade, journalled by clearing; a
deposit or withdrawal, frozen then reviewed then gated then signed once per
withdrawal ID; and a liquidation or insurance-fund draw, journalled like any
other posting. No component writes a balance.

A withdrawal reduces equity, so outstanding capacity against the old figure must
stop before funds leave. Two orderings work and differ in latency, not safety:
fence, reconcile, re-issue, release — immediate, at the cost of a fence round
trip; or wait for the terms to end, then re-issue and release — free, binds
within the term. Releasing first is not available.

## 6.3 Business-logic abuse cases

### A1 — Overstating equity

Every ceiling is solved against `E_0`, and nothing downstream re-derives it. An
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

The exposure is to anything that moves `E_0`: a suppressed mark, a lost fee, a
realised loss not yet folded in. The defences are §4.3's cash-flow identity, the
account being an independently rebuildable fold of the log, and multi-sourced
marks. None is a proof; a compromised equity path is a compromised mechanism.

### A2 — A compromised gateway

Covered in §6.1; it is a trust-boundary question, not a business-logic one.

### A3 — Routing to concentrate an account's flow

A gateway prices nothing globally: it compares absolute figures against its own
ceilings and gets no credit for offsets held elsewhere, so the account is charged
the sub-additivity gap of §2.3. Concentrating onto fewer gateways the positions
that *offset each other*, or whose losses peak in *different* scenarios, shrinks
that gap and buys real capacity — not by concentrating market risk, but by making
the decomposition less conservative.

This is a **capacity-fairness** problem, not a solvency one: the requirement is
still bounded, but a client who can influence routing — by picking a gateway, or
retrying until it lands where it wants — gets more usable capacity than one who
cannot.

Mitigations, none implemented: account affinity and controlled failover keep
offsetting legs together and so shrink the gap; usage-aware weights do not shrink
it at all, they only reduce stranded capacity on gateways an account is not
using.

### Two lesser cases, recorded and not developed

Fee-cap gaming through many small fills, bounded by the per-lot cap (d4); and
stalling a liquidation by leaving orders whose cancels are never acknowledged,
which the settlement keeps reserved rather than releases (l13).

## 6.4 Regulatory posture

What this design offers a supervisor is **reproducibility of what was admitted**:
every admitted order is on the ordering point's log with the lease, the holder
and the terms it was held to, and the values the decision was derived from are
versioned on that log (§4.1), so an admission from three months ago can be
recomputed rather than described.

It does not yet offer the same for a **refusal**. A gateway that refuses an order
on its own envelope arithmetic sends nothing to the ordering point, so there is
no record to replay. NFR row 10 is written as a target for that reason, and the
gateway refusal journal that would close it is designed and not built.

What it does not offer is a guarantee that clients can always exit. The venue can
act on an account it can no longer margin (§5.4), but a client whose gateway's
term has ended cannot close through it and there is no client-facing close-only
path (§3.3). A supervisor asking whether clients can always exit should be told
no; §7 records that the wider claim was considered and not built.

## 6.5 Detection

Six signals, all paged on and listed in §8.2: equity divergence between what the
allocator solved against and what a rebuild from the log implies; ceilings
summing above the solve; a gateway's figures above its ceilings; a lease fenced
but not settled beyond a bound; mark divergence across sources; and any
authority-binding refusal, which should be zero and otherwise means a component
is submitting under a lease that is not its own.

The gap this list cannot close is §6.1's: nothing here detects a gateway that
lies about arithmetic nobody re-derives, until the account's own figures move.
