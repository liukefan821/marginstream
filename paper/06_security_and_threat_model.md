# 6. Security and threat model

## 6.1 Trust boundaries and the trusted computing base

| Boundary | What crosses it | If the far side is fully compromised |
|---|---|---|
| Client ↔ ingress gateway | Orders, cancels, API credentials | The account's own ceilings for the current term. Ceilings are per account, so one compromised client cannot consume another's |
| Ingress gateway ↔ ordering point | Admissions under `(lease_id, seq)` | **The gateway is inside the trusted computing base.** See below |
| Ordering point ↔ everything | Every admission, fill, cancel, fence, basket and barrier | Total. This is the component every safety claim in the document rests on |
| Liquidator ↔ ordering point | Basket transfers | The account it is liquidating, in full. Nothing in the capacity accounting bounds it |
| Market-data publisher ↔ allocator | Marks | How much capacity is solved for, for every account. Not the admission check itself |
| Allocator ↔ gateway | Lease inputs and generation | An account's whole capacity. The allocator is the authority for margin and a compromise of it is a compromise of the invariant |
| Chain ↔ custody | Deposits, withdrawals | Venue assets. Unchanged from the running case |
| Operator ↔ ledger | Nothing. There is no write path | An operator with database access can corrupt state; detection is reconciliation, not prevention |

### The gateway is inside the trusted computing base

An earlier version of this document claimed that a compromised gateway's blast
radius is bounded by the leases it holds, because the shard would check the lease
itself. That claim is withdrawn. Nothing downstream re-derives the envelopes: the
ordering point checks that an admission is the next number for a live lease and
that a fill matches the terms recorded at admission, and it does not recompute
the worst-fill figures the gateway compared against its ceilings. **A gateway
that lies about its own envelope arithmetic is believed.**

What a compromised gateway therefore cannot do:

- act under a fenced lease, or after its term, because the ordering point refuses
  the submission and no gateway is consulted about that;
- produce a gap in its own admission sequence, because the ordering point accepts
  only the next number, so an admission it hides is one the log would then reject;
- fill outside the price band or above the fee cap it was admitted under, or fill
  in the wrong direction, or over-fill, or land the same fill twice;
- spend another account's capacity, or another holder's.

What it can do: admit orders that its ceilings do not cover, for the accounts it
serves, until its term ends or it is fenced. The bound on that damage is the term
length and the fence latency, not the ceiling.

Closing this would mean re-deriving the worst-fill envelopes at the ordering
point, which puts the per-order margin computation back on the single-writer
path — the thing §2.2 exists to avoid. That is a real trade and it is not made
here. It is named as the residual.

### The liquidator is inside it too, on its own account

The liquidator's orders are checked against the merged account rather than
against a ceiling, because c9 shows a gateway cannot make that judgement on its
own. So no ceiling bounds it. The non-increase test does bound the *risk* it can
create — neither merged envelope may rise — but it permits unlimited churn, and
churn costs execution. Its authority ends the same way every other holder's does,
at the ordering point, and the settlement barrier of §5.4 refuses to run while it
is live. That is containment of duration, not of authority.

### The market-data path

In the running case market data is a fan-out carrying no authority: a wrong value
produces a wrong screen, not a wrong balance. Here the marks set equity, the
scenario displacements and `mark_plus`, so a wrong value changes how much
capacity the allocator solves for. That is weaker than the earlier draft claimed
— the admission check itself contains no market state, because the schedule was
withdrawn (§2.4, ADR-2) — but it is not nothing, and §6.3 A2 measures the
suppression case.

## 6.2 Who can move money, and with what ceremony

Money moves in exactly three ways, and none of them is the margin path:

1. **A trade.** The matching shard emits a trade; clearing turns it into
   double-entry postings. No component writes a balance directly.
2. **A deposit or withdrawal.** Withdrawal freezes first, then risk review,
   then a manual gate above a threshold, then one signed transaction per
   withdrawal ID, ever.
3. **A liquidation or an insurance-fund draw.** Journalled like any other
   posting, with the generation bump that preceded it in the same log.

A lease is not money. It is an authorisation to consume a share of a risk
budget, and it never appears in the ledger. The ledger records holds; the lease
bounds how many holds can be created before the next issuance. Keeping the two
words separate throughout the document is deliberate, because conflating them is
how an authorisation becomes spendable.

Withdrawals interact with margin in one direction that has to be explicit: a
withdrawal reduces equity, so the capacity outstanding against the old figure has
to stop before funds leave. Bumping the generation is not enough, because a
partitioned gateway keeps admitting inside its term regardless. Two orderings
work and they differ in latency, not in safety:

1. fence the outstanding leases at the ordering point, reconcile, re-issue
   against the reduced equity, then release. Binds immediately and costs a fence
   round trip on the account.
2. wait for the outstanding terms to end, then re-issue and release. Binds within
   the term and costs nothing.

The choice is a product decision about withdrawal latency. What is not available
is releasing first: that leaves gateways spending against equity that has left
the venue, and it is the same shape as the pre-cut term the lifecycle fuzz
counts and does not treat as a failure.

## 6.3 Business-logic abuse cases

**Scope note.** A1 and A2 below were written against the price-conditional
schedule, which ADR-2 withdraws. Under the current condition the admission check
reads no market state at all, so A1 — replaying a stale, more favourable state to
obtain capacity — no longer has a target on the admission path, and the ratchet
that closed it is no longer load-bearing. A2 survives in weakened form: a
suppressed mark still overstates equity, and an overstated equity still buys a
ceiling the account cannot carry, which is the shape E5's misreported-equity
control measures directly. The E4 and E5 figures quoted in A1 and A2 come from
superseded experiments and are retained below as the record of what was argued,
not as current evidence. Rewriting these two cases against the misreported-equity
control is outstanding work, not something this section claims to have done.

### A1 — Replaying an older market state

The shard evaluates its schedule at the market state it reads. An account able
to make a shard read a stale, more favourable state obtains capacity the current
state does not permit.

Mitigation: the shard evaluates its schedule at the most adverse state it has
observed since the lease was installed, not at the state on the current message.
A replayed lower state changes nothing.

Cost of the mitigation when the feed is honest: none. In E5 the ratcheted and
unratcheted configurations are identical on an honest feed — 58 orders admitted,
zero ticks with the requirement above equity. The defence only engages when the
state goes backwards, which on an honest feed it does not.

### A2 — Holding the published mark below the market

An account able to suppress the published state, rather than replay an old one,
is a different attack: the state never goes backwards, it simply fails to go
forwards. The ratchet cannot see this, because there is nothing to ratchet
against.

E5 measures it. The published state is held at 0 between ticks 20 and 50 while
the checker uses the true state:

| Configuration | Suppressed | Admitted | Ticks above equity, of 480 |
|---|---|---|---|
| Scalar lease | no | 36 | 236 |
| Scalar lease | yes | 36 | 236 |
| Schedule, current state | no | 58 | 0 |
| Schedule, current state | yes | 62 | 12 |
| Schedule, ratcheted | no | 58 | 0 |
| Schedule, ratcheted | yes | 49 | 5 |

Three readings. The scalar lease is indifferent to the attack because it never
reads the state — it is also above equity 236 ticks out of 480 for unrelated
reasons (§7). The schedule closes that, and in doing so opens A2: suppression
buys 4 extra admissions and 12 ticks of exposure. The ratchet reduces the
exposure to 5 ticks and costs 9 admissions under attack.

**This is a partial mitigation and we do not present it as more than that.** The
residual belongs to the mark pipeline, not to the lease: multiple independent
sources, a trimmed statistic across them, staleness detection on each source,
and a bound on how far the published state may lag the sources. That pipeline is
named in §2 and not designed in this document, which is a scope decision rather
than an oversight.

### A3 — Concentrating to escape the conservative split

This one is not an attack on safety and is more interesting for it.

A shard prices an order's marginal requirement against its own positions only,
with no credit for offsetting positions on other shards. The sum of the shard
charges therefore exceeds the true portfolio requirement by the sub-additivity
gap. An account that concentrates its positions on fewer shards has a smaller
gap, so it is charged less and obtains more effective capacity than an account
holding the same risk spread across shards.

The mechanism therefore rewards concentration and penalises cross-shard hedging,
which is backwards from a risk standpoint. Nothing unsafe happens — every
charge is conservative — but the incentive points the wrong way.

Two mitigations, neither of which removes it:

- Shard weights follow the account's actual usage rather than being uniform, so
  an account that hedges across shards is not additionally penalised by having
  its budget parked where it does not trade.
- The distortion is bounded above by the sub-additivity gap, which the corollary
  in §2 makes a measurable quantity rather than an unknown. Reporting it per
  account is how a venue would decide whether the distortion is material.

Not measured yet, and stated as such. The experiment is a portfolio held first
concentrated and then hedged across shards, with the admitted notional compared.

### Two lesser cases, recorded and not developed

**Lease starvation between keys of one account.** Where several API keys share an
account, one key can exhaust a shard's lease with cheap orders and block another.
Bounded to that account; the reserved risk-reducing channel means it cannot block
a close-out. Per-key sub-allocation is the fix and is not designed here.

**Epoch-boundary timing.** Consumption resets at issuance, so an account can time
flow to arrive just after a boundary. This changes when capacity is used, not how
much, because the schedule is sized against collateral rather than against a
rate.

## 6.4 Regulatory posture

We do not claim compliance with any named regime and do not cite specific rules.
What follows is the evidence the design produces, which is what a supervisor
would ask for.

**Reconstruction of any decision.** §5.2 puts the schedule inputs and the market
state on the replicated log, so a supervisor asking why a particular order was
refused on a particular day gets the lease the shard held, the state it read, and
the arithmetic, replayed rather than reconstructed from memory. Admission
decisions are as auditable as trades, which in a venue with an authority outside
the matching path they otherwise would not be.

**Solvency.** Holds are bounded by issued leases and leases are bounded by
collateral, so `Σ user holds ≤ Σ collateral` holds by construction rather than by
reconciliation. Reconciliation still runs nightly, because the argument is only
as good as the code.

**Retention.** The journal is append-only and retained for years, as in the
running case. The schedule shape table and the state banding are versioned data
on the same log, so a replay years later uses the parameters that were live then,
not the current ones.

**Orderly degradation.** The venue degrades to reduce-only rather than halting.
The argument we would make to a supervisor is that a venue which blocks risk
reduction during stress manufactures the disorderly market it is trying to
prevent: clients unable to close are forced to hedge elsewhere or not at all, and
the venue's own liquidation engine is competing with them for the same liquidity.
Refusing new risk while accepting risk reduction is the narrower intervention.
This is our argument, not a rule we are quoting.

**Where a supervisor would push.** The mark pipeline of §6.3, because A2 shows
the venue's capacity control is only as sound as its price feed. We would expect
to have to evidence the independence of the sources.

## 6.5 Detection

Reconciliation is a security control here, not only an accounting one. The
checks that would page:

1. Sum of issued leases above the budget the allocator solved for.
2. Any shard's consumption above the lease it holds.
3. Requirement above equity outside a liquidation window.
4. Market-state staleness on any shard beyond a bound.
5. Reduce-only rate across accounts rising without a corresponding market move —
   the signature of A2, since suppression eventually produces the reduction it
   delayed.

The first three are the invariants the simulator checks on every admitted order,
promoted to production alerts. The fourth is new in this design because the
market-data path now carries authority. The fifth is the only one that detects
A2 rather than preventing it.
