# Appendix A — Decision history

This appendix holds what the main body no longer claims. It is here because a
design document that quietly deletes its reversals is less trustworthy than one
that records them, and because §9 has to disclose what was rejected as well as
what was adopted.

Nothing in this appendix is current evidence. Where a figure appears, it was
produced by an interface the mechanism has moved off, and the experiment that
produced it now lives in `experiments/superseded/`.

## A.1 What was withdrawn, and what replaced it

| Withdrawn claim | Why it failed | What replaced it |
|---|---|---|
| A capacity schedule contracting with a published market state makes the mechanism safe | A lease cannot remove a position it has already admitted. Capacity that shrinks restricts the *next* admission, not the exposure already created | A flat ceiling for the term. The schedule survives only as a local operational trigger, with no capacity claim (ADR-2) |
| A state-contingent schedule recovers 3.8× the throughput of worst-case sizing | Measured on the incremental-charge mechanism, which did not bound the account's requirement at all | Utilisation is reported directly: the closure caps it near half of equity, and E1's binding trial reaches 99% of two envelopes at 49% of equity |
| Charging an order its marginal requirement is sufficient | A leg flipped from short to long leaves the local increment unchanged while the account's requirement moves to its maximum (c1) | Admission compares absolute worst-fill envelopes |
| Gross notional is additive across the partition | Two gateways can hold opposite legs in the same symbol; they net inside the account | Gross is sub-additive by the triangle inequality, and the add-on is evaluated once on the summed gross (§2.3) |
| Expiry releases the exposure a holder created | A term ends authority. The positions stay | Authority and committed exposure are tracked separately; only a terminal reconciliation or an account barrier lowers the second (§5.4) |
| A gateway may keep accepting risk-reducing orders during a partition | An order that lowers one gateway's requirement can raise the account's by removing a hedge held elsewhere (c9) | A gateway in quarantine admits nothing. Risk reduction is an account-level operation (§3.3) |
| A compromised gateway's blast radius is bounded by its leases | Nothing downstream re-derives the envelopes the gateway compared against | The gateway is inside the trusted computing base, and §6.1 says what it can and cannot do |
| A lease id identifies authority | It was a bearer token: any account, any claimed holder | Registered bindings and session-resolved holders at the ordering point (`tests/test_authority.py`) |
| The closure absorbs an equity overstatement of about 64% | Measured in a configuration where the ceiling was not binding | At the binding point the breach tracks the overstatement one for one (E5 Part B) |
| The lease term is the latency of every credit decision | Raising a limit takes effect at the next issuance | It is the enforcement latency of a *tightening* under partition (§1.5) |

## A.2 The abuse cases the schedule created

Retained verbatim because they are the record of what the market-data
authority argument was, and because §6.1's weaker claim only makes sense against
them. Under the current condition the admission check reads no market state, so
A1 has no target on the admission path; A2 survives in the weakened form now
recorded as §6.3 A1.

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

## A.3 The counterexample ledger

Sixteen lifecycle counterexamples (`tests/test_counterexamples.py`, c1–c16), six
worst-fill cases (w1–w6), six recovery cases (r1–r6), fourteen liquidation cases
(l1–l14), six repricing cases (m1–m5 with m4 split), six authority cases
(t1–t6), seven execution-cost cases (d1–d7) and fourteen ledger cases (a1–a14).
Each was written after a failure, most of them found by external review, and
each is named in the section of the main body that depends on it. The pattern
worth extracting is that **every one of them was found by a check that compared
against something recomputed independently**, never by a check that trusted a
figure a component reported about itself.

The three that changed the mechanism rather than fixing an implementation are
c1 (absolute envelopes), c9 (local risk reduction is not global) and m1 (gross
measured at one mark). The rest are lifecycle discipline.
