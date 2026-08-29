# MarginStream whitepaper — working skeleton

SC6118 Capstone, Group 1. Target: 20 pages plus appendices, PDF, due 23:59 on
2 October; defence 3 October.

Section numbering follows the brief. The "feeds" line under each section names
what already exists and what still has to be produced.

---

## Page budget

| § | Section | Pages |
|---|---|---|
| 1 | Business context and requirements | 2.0 |
| 2 | Architecture | 3.5 |
| 3 | Consistency map | 1.5 |
| 4 | Data and storage design | 2.5 |
| 5 | Failure and recovery | 3.0 |
| 6 | Security and threat model | 2.5 |
| 7 | Trade-offs and alternatives | 2.5 |
| 8 | Operations | 1.5 |
| 9 | AI-disclosure appendix | appendix |
| | Evidence appendix (E1, E2, E4, E5 tables) | appendix |

Sections 2 and 5 carry the weight. Section 1 must be short and numeric.

---

## §1 Business context and requirements

Contents: what MarginStream is (a cross-margin derivatives venue), why the
unified account breaks the additive pre-trade check that the running case
relies on, and the quantified NFR table below. Fermi work shown, not just
quoted.

Status: drafted in `paper/01_context_and_requirements.md`.
Committed scale: 40 underlyings, 120 contracts, 10^6 registered accounts,
10^5 with open positions, 10^4 changed per epoch, median 5 contracts per
account, leverage capped at 20x with the parameters set at 8-10x.
Open: the regulatory posture paragraph belongs in 3.3 and is not yet written
anywhere.

### NFR table

| Requirement | Target | Consequence for the design |
|---|---|---|
| Order throughput | 100k/s sustained, 1M/s burst | Admission is a local array operation; no allocator call on the order path |
| Admission-path latency | p50 < 20 µs, p99 < 200 µs, on top of matching | Per-account per-shard scenario vector kept resident; marginal R is O(\|S\|) |
| Matching latency | p50 < 100 µs, p99 < 1 ms | Unchanged from the running case; single-writer per symbol |
| Margin correctness | Requirement never exceeds equity at any market state reached | Pointwise lease condition; verified by E1 and E4 |
| Trigger latency | Reduce-only condition observable by a shard on the tick it occurs | Conditional lease read on the market-data path |
| Recompute cadence | Allocator epoch 50–200 ms | See derivation |
| Recompute throughput | ~4 × 10⁸ scenario-ops per epoch at 10⁴ changed accounts | Allocator sharded by account; scenario grid vectorised |
| Availability | 99.99% order entry; failover < 3 s | Raft, as in the running case |
| Degradation | Risk-reducing orders accepted in every state above HALT | Reserved capacity per shard |
| Auditability | Every admission decision replayable with the lease and state it saw | Append-only journal of (lease, generation, state, decision) |

Numbers in rows 1, 3, 8 are taken from the running case. Rows 2, 6, 7 are
derived below. Rows 4, 5, 9, 10 are properties of this design.

### Derivation of the epoch length and the curve granularity

Between two market states the lease curve is flat, so the residual exposure is
whatever the account's equity does inside one band.

1. Take an adverse index move of 3% as the range the curve must cover; this is
   the size of a fast move, not a tail event.
2. At 10× leverage a 1% adverse index move removes 10% of account equity, so
   equity moves 10× the index move.
3. Choose a residual tolerance: no more than 2% of equity may be uncovered
   inside a band. That fixes the band width at 0.2% of index.
4. Covering 3% at 0.2% granularity requires about 15 bands, so the curve needs
   K ≈ 16 states, not the 4 used in the current simulator.

The epoch is then set by how fast the allocator can refresh the whole curve,
not by how fast the market moves — the curve already absorbs movement inside
the epoch. The remaining reason to re-issue is position change, which the
allocator sees on the trade stream. 50–200 ms follows from the recompute cost
below rather than from a market argument.

### Derivation of the allocator's cost

- Scenario grid \|S\| = 16. Median symbols per account ≈ 5.
- One evaluation of R(P) ≈ \|S\| × symbols ≈ 80 multiply-adds.
- One feasibility check spans K = 16 states: ≈ 1.3 × 10³ ops.
- Bisection to a stable scale: ≈ 30 iterations, so ≈ 4 × 10⁴ ops per account.
- At 10⁴ accounts changed per epoch: ≈ 4 × 10⁸ ops per epoch.
- At a 100 ms epoch: ≈ 4 × 10⁹ ops/s.

That does not fit one core, and it does not have to. **Accounts are
independent in the allocator**: there is no invariant that spans two accounts,
so the allocator shards by account while matching shards by symbol. The two
partitionings are orthogonal, which is the point worth making in §2. What
remains venue-level is the insurance fund and auto-deleveraging, and those are
not on the admission path.

Consequences to state explicitly: incremental recompute for accounts whose
positions and relevant marks are unchanged; the scenario grid evaluated as a
vector rather than a loop.

### Derivation of the admission-path data structure

Marginal R must not be recomputed from positions on every order. Keep, per
account per shard, the running loss under each scenario. Then admitting an
order is: add the order's contribution to each of \|S\| entries, take the
maximum, compare with the curve value at the current state.

- ≈ 16 multiply-adds plus 16 comparisons, so tens of nanoseconds.
- Memory: \|S\| × 8 bytes = 128 B per account per shard. At 10⁵ accounts and 3
  shards, ≈ 38 MB resident.

This is the Session 2 argument applied to the margin path: the structure is
chosen for the access pattern, not for the API.

---

## §2 Architecture

Contents: component, data-flow and deployment views. Identify the single-writer
core per symbol and everything on the replicated log. State the two orthogonal
partitionings (matching by symbol, allocator by account) and why leases exist
at all — admission happens at N gateways upstream of one writer.

Feeds: proposal figure and topology text; `marginstream/` module boundaries.
Still needed: three production-quality diagrams; deployment view.

## §3 Consistency map

Contents: per flow, one row, each defended. Matching, admission, lease
issuance and generation transition, synchrony/market-state estimates, audit
journal, projections and dashboards, retry handling. CAP position of the core
in one sentence, and the separate CAP position of the admission plane — the
venue degrades to reduce-only rather than halting when a shard loses the
allocator.

Feeds: proposal text; shard and allocator semantics as implemented.

## §4 Data and storage design

Contents: engine choice per store justified by access pattern. Double-entry
ledger with the hold-versus-lease distinction spelled out: a lease is an
authorisation, a hold is a posting. The `Σ postings = 0` invariant and how the
lease bound keeps `Σ holds ≤ collateral`.

Feeds: running case Part 3 (cited, not re-derived).
Still needed: the ledger module; the account-type table for a margin venue.

## §5 Failure and recovery

Contents: failover; the idempotency chain end to end; epoch and generation
fencing including the rule that a shard which has observed a higher generation
must fail closed; recovery-time arithmetic; zero-downtime upgrade of a
deterministic state machine.

Status: drafted in `paper/05_failure_and_recovery.md`.
Findings that came out of writing it: leases must not be logged per shard
(32 MB/s against a 12.8 MB/s order stream); the log carries the schedule
inputs instead and each shard derives its own lease, which is 8 MB/s. The
schedule shape and the market-state banding become versioned parts of the
state machine, so they cannot be tuned at runtime.
Open: the replay-rate assumption (10x live) is unmeasured; the liquidation
waterfall is not designed.

## §6 Security and threat model

Contents: blast radius per trust boundary; who can move money and with what
ceremony; top-3 business-logic abuse cases.

The three abuse cases:
1. Market-state replay — closed by evaluating the curve at the worst observed
   state; E5 shows this costs nothing when the feed is honest.
2. Mark-price suppression — E5 shows the ratchet reduces but does not remove
   the exposure; the remainder belongs to the mark pipeline (multi-source,
   staleness detection), and the whitepaper says so rather than claiming
   coverage.
3. Cross-shard lease capture within an account — mitigation is the reserved
   risk-reducing channel, which exists in the design but is not yet measured.

Feeds: E5.
Still needed: the mark pipeline design; abuse case 3 as an experiment.

## §7 Trade-offs and alternatives

ADR-style, at least three alternatives with the reason each lost:
1. Account-level lock on the order path — rejected on the hot path argument.
2. Fixed per-shard sub-limits with no offset — rejected because it removes the
   product's value; the corollary quantifies what it would cost.
3. Optimistic admission with post-hoc repair — rejected against the
   never-negative rule.
4. Scalar lease per epoch — rejected on E4: 236 of 480 ticks above equity.

Also: what we deliberately did not build.

Feeds: E4; the corollary.

## §8 Operations

Contents: the first chaos experiment; top-5 alerts with business invariants
first; volatility-day playbook.

Candidate alerts: lease sum above budget; any shard consumption above its
lease; requirement above equity outside a liquidation window; market-state
staleness; reduce-only rate.

## §9 AI-disclosure appendix

Mandatory. Prompts that mattered, what was used, what was rejected and why.

Material already available: the decision not to abstract the margin model into
a general coherent-risk-measure formulation, and why; the two invariant errors
that the simulator surfaced (the mixing of market-driven and admission-driven
breach, and the fencing comparison that let a stale shard serve); the
over-conservative budget solve that drove budgets to zero. Section ownership
table.

---

## Order of writing

1. §1 with the table and the three derivations. Everything else refers back to
   these numbers.
2. §5 and §6, which are where the current evidence sits.
3. §2 and §3, which need the diagrams.
4. §7, §8, §4, §9.

## Open items that block a section

- §4 needs the ledger module before it can be written honestly.
- §6 abuse case 3 needs an experiment or an explicit statement that it is
  argued rather than measured.
- §2 needs the deployment view, which depends on the allocator sharding
  decision above being committed to.
