# 6. Security and threat model

## 6.1 Trust boundaries and the trusted computing base

| Boundary | If the far side is fully compromised |
|---|---|
| Client ↔ ingress gateway | The account's own ceilings for the current term. Ceilings are per account, so one compromised client cannot consume another's |
| Ingress gateway ↔ ordering point | **The gateway is inside the trusted computing base.** See below |
| Ordering point ↔ everything | Total. Every safety claim in this document rests on it |
| Liquidator ↔ ordering point | The account it is liquidating, in full. Nothing in the capacity accounting bounds it |
| Market data ↔ allocator | How much capacity is solved for, for every account. Not the admission check itself |
| Allocator ↔ gateway | An account's whole capacity. The allocator is the authority for margin |
| Chain ↔ custody | Venue assets. Unchanged from the running case |
| Operator ↔ ledger | Nothing; there is no write path. Detection is reconciliation, not prevention |

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

What it can do: admit orders its ceilings do not cover, for the accounts it
serves, until its term ends or it is fenced. The bound on that damage is the term
length and the fence latency, not the ceiling. Closing it would mean re-deriving
the envelopes at the ordering point, which puts the per-order margin computation
back on the single-writer path — the thing §2.2 exists to avoid. That trade is
not made here; it is the residual.

**The liquidator is inside it too, on its own account.** Its orders are checked
against the merged account rather than a ceiling (c9), so no ceiling bounds it.
The non-increase test bounds the *risk* it can create — neither merged envelope
may rise — but permits unlimited churn, and churn costs execution. Its authority
ends like any other holder's, at the ordering point, and the barrier refuses to
run while it is live (t5, l11). That is containment of duration, not of
authority.

**The market-data path.** In the running case it carries no authority. Here the
marks set equity, the scenario displacements and `mark_plus`, so a wrong value
changes how much capacity is solved for. That is weaker than an earlier draft
claimed — the admission check contains no market state — but it is the exposure
in §6.3 A1.

## 6.2 Who can move money

Three ways, none of them the margin path: a trade, journalled by clearing; a
deposit or withdrawal, frozen then reviewed then gated then signed once per
withdrawal ID; and a liquidation or insurance-fund draw, journalled like any
other posting. No component writes a balance directly.

A withdrawal reduces equity, so outstanding capacity against the old figure must
stop before funds leave. Two orderings work and differ in latency, not safety:
**fence, reconcile, re-issue against the reduced equity, release** — immediate,
at the cost of a fence round trip; or **wait for the outstanding terms to end,
then re-issue and release** — free, binds within the term. Releasing first is not
available: it leaves gateways spending against equity that has left the venue.

## 6.3 Business-logic abuse cases

### A1 — Overstating equity

Every ceiling is solved against `E_0`, and nothing downstream re-derives it. An
account reporting more equity than it has buys a ceiling it cannot carry.

E5 measures it. An account that forgets a realised loss reports 92,000 where it
has 42,000, is issued 46,000 instead of 21,000, admits 230 orders instead of 105,
and ends 4,000 above equity. At the binding point the breach tracks the
overstatement one for one: overstating by 10,000 produces a breach of 10,000.
**The factor of two is a closure, not a margin against a misreported account**;
an earlier draft that read it as a 64% tolerance was reading unused workload
slack.

The exposure is to anything that moves `E_0`: a suppressed mark, a lost fee, a
realised loss the ledger has not folded in. The defences are the exact cash-flow
identity of §4.3, the account being a fold of the log so it can be rebuilt
independently, and multi-sourced marks. None is a proof; a compromised equity
path is a compromised mechanism.

### A2 — A compromised gateway

Covered in §6.1; it is a trust-boundary question, not a business-logic one.

### A3 — Routing to concentrate an account's flow

A gateway prices nothing globally: it compares absolute figures against its own
ceilings and gets no credit for offsets held elsewhere, so the account is charged
the sub-additivity gap of §2.3. Concentrating one account's *correlated* orders
onto fewer gateways shrinks that gap and buys real capacity — not by
concentrating market risk, but by making the decomposition less conservative.

This is a **capacity-fairness** problem rather than a solvency one: the account's
requirement is still bounded, but a client who can influence routing gets more
usable capacity than one who cannot. It matters exactly when the client can pick
a gateway, or can shift its flow by retrying until it lands where it wants.

Mitigations, none implemented: account affinity, so an account's flow lands on
the same gateway set unless the venue moves it; controlled failover rather than
client-driven retry; and usage-aware weights, so the allocator gives more of the
ceiling to the gateway an account actually uses. The withdrawn reduce-only
channel is not among them.

### Two lesser cases, recorded and not developed

Fee-cap gaming through many small fills, bounded by the per-lot cap the ordering
point enforces (d4); and deliberately stalling a liquidation by leaving orders
the matching side never acknowledges cancels for, which the settlement keeps
reserved rather than releases (l13).

## 6.4 Regulatory posture

What this design offers a supervisor is **reproducibility**: every admission and
every refusal is a function of the log, and the values it was derived from are
versioned on that log (§4.1). A decision from three months ago can be recomputed
rather than described.

What it does not offer is a guarantee that clients can always exit. The venue can
act on an account it can no longer margin (§5.4); a client whose gateway's term
has ended cannot close through it, and there is no client-facing close-only path
(§3.3). A supervisor asking whether clients can always exit should be told no.
That is the narrower and defensible claim, and §7 records that building the wider
one was considered and not done.

## 6.5 Detection

The signals specific to this threat model, all of which §8.2 pages on:
divergence between the equity the allocator solved against and the equity a
rebuild from the log implies; ceilings summing above what the condition solved
for; a gateway's own figures above the ceilings it holds; a lease fenced but not
settled beyond a bound; mark divergence across sources; and a non-zero rate of
authority-binding refusals, which in normal operation should be zero and
otherwise means a component is submitting under a lease that is not its own.
