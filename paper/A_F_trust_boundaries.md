# Appendix F — Trust boundaries in full

The argument behind §6.1, moved here to keep the body to its page budget.

**The gateway is inside the trusted computing base.** An earlier version of this
document claimed its blast radius is bounded by the leases it holds. That is
withdrawn. Nothing downstream re-derives the envelopes: the ordering point checks
that an admission is the next number for a live, correctly bound lease and that a
fill matches the terms recorded at admission; it does not recompute the
worst-fill figures the gateway compared against its ceilings. **A gateway that
lies about its own envelope arithmetic is believed.**

What a compromised gateway cannot do is each pinned by a test. It cannot act
under a fenced lease or under a lease id the allocator never minted (t3, t4). It
cannot use another account's lease or another holder's, or submit without an
authenticated session (t1, t2). It cannot commit a liquidation basket under an
ingress lease (t5). It cannot fill outside the band or above the fee cap
recorded at admission, fill in the wrong direction, over-fill, or land the same
fill twice (d4 to d6). Nor can it hide an admission, because the ordering point
takes only the next sequence number for its lease.

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
equity, the scenario displacements and $G^{+}$, so a wrong value changes how much
capacity is solved for — weaker than an earlier draft claimed, since the
admission check reads no market state, but it is the exposure in §6.3 A1.
