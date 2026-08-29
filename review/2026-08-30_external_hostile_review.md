# Hostile review — MarginStream capstone whitepaper

## Verdict

I reran `tests/test_algebra.py` and E1, E2, E4, and E5. Their recorded outputs reproduce. The problem is not fabricated measurements; it is that the experiments do not exercise several premises needed by the safety theorem.

The current draft is **not defensible as a safe pre-trade margin architecture**. I find six fatal issues. The core idea remains salvageable, but only if the paper changes from “a state-contingent lease proves continuous solvency” to a more conservative design with persistent reservations, two leased resources, causal issuance, and a schedule that is explicitly a trigger unless liquidation latency is included in the proof.

No external citations are supplied. The archive contains Markdown and Mermaid source but no assembled PDF or exported figures, so final pagination and rendered-diagram quality could not be assessed.

## Fatal findings

### F1 — The factor-of-two theorem is false for the admission charge actually used

**Files/sections:** `paper/02_architecture.md` §2.4–2.5; `marginstream/shard.py` `Shard.admit`; `tests/test_algebra.py` `test_admission_bound`.

**What is wrong.** The coefficient 2 is conditionally correct and tight, but the theorem silently needs a quantity `B` satisfying all three of these facts:

1. `R(P + Δ) <= R(P) + B`;
2. `loss(Δ, k) <= B` for every covered state `k`;
3. the reserved add-on upper-bounds `A(P + Δ)`.

Under those assumptions,

`M(P + Δ) <= R(P) + B + A*`, while

`equity(P + Δ, k) >= Collateral - loss(P, k) - B`.

Therefore `R(P) + 2B + A* <= Collateral - loss(P,k)` is sufficient. It is tight in this abstract form: with `P = 0`, `A* = 0`, `Collateral = 2B`, and a new position whose requirement and loss at `k` are both `B`, equality holds. Any coefficient below 2 admits a case with requirement above equity.

MarginStream does not lease that `B`. It leases the sum of clipped **local marginal** changes in `R`. That does not bound the change in global `R`, because an order can remove a cross-shard hedge without increasing either shard's standalone worst loss.

Deterministic counterexample against the supplied code:

- Two identical symbols on two shards; initial positions are `+10` and `-10`.
- Initial global `R = 0`, loss at the adverse state is `0`, `A = 0`, and collateral is `1,000`.
- The solved total lease is `500`, so the published condition `0 + 2*500 <= 1,000` holds.
- An order of `+20` on the second shard flips its position from `-10` to `+10`. Local `R` is `1,000` both before and after, so `marginal_R = 0`; `Shard.admit` accepts it for zero credit.
- Final global `R = 2,000`, state loss is `2,000`, and equity is `-1,000`.

No larger coefficient repairs a zero-cost order. The missing inequality is between local marginal charges and the global portfolio change; Lemma 1 does not provide it. `test_admission_bound` only sets each share equal to the absolute `R` of a shard and rechecks sub-additivity. It never tests the marginal charging rule used by `Shard.admit`.

**What would fix it.** Replace the theorem and charge model. A reproducible safe baseline is to reserve the **absolute post-order per-risk-shard envelope**, so the allocator budgets `sum_g R(P_g + openOrders_g)` rather than `R(P)` plus clipped local increments. If cross-shard offset is to be retained, represent it as an explicit central offset credit tied to both legs and to a generation; removing either leg revokes/consumes that credit. Also define reduce-only as “quantity cannot cross the current filled position through zero,” not “local marginal R is non-positive.”

**Page trade:** replace the current theorem, proof sentence, and corollary in §2.4–2.5. Do not add prose around them.

### F2 — A decreasing schedule cannot retroactively cap capacity already spent

**Files/sections:** `paper/02_architecture.md` §2.4; `paper/01_context_and_requirements.md` NFR 4–5; `experiments/e4_conditional.py`; `marginstream/shard.py` lines implementing `remaining = lease.at(k) - spent`.

**What is wrong.** The pointwise solve uses `lambda(k)` as though total positions admitted during the epoch were bounded by `lambda(k)`. They are not. They may have consumed the larger `lambda(j)` at an earlier, more favourable state `j`. When the curve shrinks, the gateway can stop future admissions, but it cannot make prior exposure satisfy the smaller bound.

Deterministic counterexample against the supplied code:

- One symbol, `A = 0`, initial position `+1`, `R(P) = 100`, collateral `300`.
- State 0 is favourable and state 1 adverse. A valid non-increasing shape solves to `lambda(0) = 150`, `lambda(1) = 49`.
- At state 0, another `+1` costs `100` and is accepted.
- When state 1 arrives, requirement is `200` and equity is `100`: the invariant has already failed.
- Only a later call to `admit` returns `reduce_only`, because spent `100` exceeds the new allowance `49`.

This remains a breach even if a market-data callback evaluates the curve on every tick: detection and liquidation occur after the old position exists at the new state. To claim continuous safety, the proof must include a pre-trigger buffer and a measured upper bound on state-publication plus liquidation completion, or reserve against the maximum outstanding exposure at every future state. The latter largely collapses to worst-state sizing.

E4 obtains zero breach ticks for the steep curves by performing an instantaneous, cross-shard proportional liquidation inside the same simulator tick. It then compares 111 admissions plus 17 ideal liquidations with 29 admissions and zero liquidations. That is not a clean capital-efficiency comparison.

**What would fix it.** Choose one honest claim:

- **Trigger design:** state that the curve gives a local reduce-only/liquidation trigger, not a continuous-solvency proof; measure breach depth and duration under a non-zero liquidation latency.
- **Safety design:** make every earlier admission safe through the next trigger boundary, including publication and liquidation latency, and solve with outstanding capacity rather than current `lambda(k)`.

**Page trade:** replace §2.4's theorem and ADR-2/ADR-3's interpretation of E4. The schedule explanation can stay, but the “zero-breach guarantee” cannot.

### F3 — Capacity is reusable across epochs while the risk it authorised survives

**Files/sections:** `paper/03_consistency_map.md` §3.1; `paper/05_failure_and_recovery.md` §5.3–5.5; `marginstream/allocator.py` issuance; `marginstream/shard.py` lease installation; the absence of open-order state throughout the simulator.

**What is wrong.** The claim that a stale position feed is only costly has the direction backwards. A smaller portfolio normally produces **more** headroom, not less. An unseen fill was covered by an old lease, but a new issuance resets consumption and does not subtract the old, still-unseen risk.

Deterministic counterexample against the supplied code:

- One symbol, collateral `1,000`, a single adverse state, and the allocator's position view held at empty.
- Epoch 0 issues `500`; `+5` is admitted. Actual `R = 500`, equity `500`: safe at equality.
- The fill remains absent from the allocator view. Epoch 1 issues another `500` and resets spend.
- Another `+5` is admitted. Actual `R = 1,000`, equity `0`: violation.

The same temporal hole exists for resting orders. An order can consume lease in epoch 0, remain unfilled on the book, have its credit “recovered” at the epoch boundary, and then fill after new epoch-1 capacity has been spent. Unique client-order IDs prevent duplicate application; they do not prevent two distinct, previously authorised orders from filling against the same collateral.

`Lease` contains an epoch but no expiry time, and `Shard.admit` does not check epoch expiry. Old and new generations can therefore overlap unless every old holder learns the new generation first, which an asynchronous partition does not guarantee.

**What would fix it.** New issuance must use a causally complete watermark. The allocator may issue generation `g+1` only after it has incorporated all fills/cancels and all persistent open-order reservations through log offset `L`, or it must carry the unacknowledged reservations from `g` into the `g+1` budget. Resting-order reservations persist until fill, cancel, rejection, or forced expiry; they do not reset because an allocator epoch changed. If orders are intentionally epoch-scoped, the matching core must auto-cancel them before the reservation is released.

**Page trade:** replace the existing §3.1 defence and the retry-credit paragraph in §5.3 with the watermark/reservation lifecycle. This is not an addition.

### F4 — The central add-on reserve underestimates portfolios reachable with a given R budget

**Files/sections:** `paper/02_architecture.md` §2.3–2.4; `marginstream/allocator.py` `_reachable_gross`; `marginstream/risk.py` `min_margin_rate_num`.

**What is wrong.** `_reachable_gross` assumes every increase in gross notional consumes at least the minimum standalone `R` rate. A hedge can increase gross while decreasing local `R`, so the assumption is false even within one shard.

Deterministic counterexample against the supplied code:

- Two identical symbols on one shard, collateral `30,000`, quadratic add-on scale `1,000,000`.
- The curve solver issues `10,000` of R capacity. `_reachable_gross` says the largest reachable gross is `100,000`, so it reserves `A = 10,000` and the constraint binds: `2*10,000 + 10,000 = 30,000`.
- Buy `+100` of the first symbol: cost `10,000`. Sell `-100` of the second: local `R` falls to zero, so cost is `0`.
- Actual gross is `200,000`; actual `A = 40,000`; final `M = 40,000 > 30,000` equity. Both orders are accepted.

This is an invariant breach caused solely by the add-on bound.

**What would fix it.** Lease two additive resources: scenario-risk capacity and **positive gross-notional capacity**. Every order consumes `max(0, gross_after - gross_before)` from the latter even when it reduces `R`. Reserve the central add-on at `A(gross_now + total_gross_lease)`. Alternatively use a proven conservative gross envelope, but the current minimum-R-rate argument is not one.

**Page trade:** replace `_reachable_gross`'s explanation in §2.3–2.4 with the two-resource lease. The add-on paragraph need not grow.

### F5 — The proposed distributed authority is not the system the simulator implements, and the fencing repair is incomplete

**Files/sections:** `paper/02_architecture.md` §2.1–2.2 and §2.6; `paper/diagrams.md` Figures 1–2; `paper/05_failure_and_recovery.md` §5.3–5.4; `paper/06_security_and_threat_model.md` §6.1; `marginstream/shard.py`; E2.

**What is wrong.** The draft alternates among three incompatible owners:

- the ingress gateway holds, prices, and spends the lease;
- the matching shard holds the scenario vector and “admits or refuses”;
- §6.1 says the matching shard checks the lease so a compromised gateway cannot overspend.

The code collapses admission and the symbol shard into one `Shard`. It does not model N gateways upstream of a symbol writer. If leases are per symbol shard, multiple gateways can double-spend them. If leases are per gateway, the matching shard cannot bound a compromised gateway without maintaining a second authoritative per-gateway counter. The actual distributable unit is at least `(account, gateway)` and possibly `(account, gateway, matching-shard)`; none of the memory or log arithmetic prices that matrix.

E2 also does not establish revocation. Its “fencing on” run sends the stale shard an order stamped with the **new** generation, so the order itself tells the shard that it is stale. With a stale lease at generation 1 and a stale order stamped generation 1, the current fenced code accepts. I reproduced exactly that result after the allocator had advanced to generation 2. A partition can hide the new generation from both participants; “highest generation observed” cannot revoke information that was never observed.

**What would fix it.** Choose one authoritative topology and redraw all four figures around it. The simplest defensible choices are:

- pin an account to one replicated ingress risk owner; or
- issue signed subleases per `(account, gateway, matching-shard)`, with the matching shard authoritatively checking that gateway's sublease and counter.

Then specify non-overlap: expiry on a trusted monotonic time/log boundary, acknowledgements before reissue, or accounting for every unexpired old lease in the new budget. A generation number alone is not revocation.

**Page trade:** replace §2.1–2.2 and ADR-6; redraw rather than add diagrams. Recompute §1.7 and §5.2 after the owner is fixed.

### F6 — E4 and E5 use retroactive P&L and loss-erasing liquidation

**Files/sections:** `experiments/e4_conditional.py` and `experiments/e5_adversarial.py`, especially `equity_at`; `paper/02_architecture.md` §2.4; evidence claims in §§6–7.

**What is wrong.** `equity_at` computes `Collateral - loss(current_positions, state)` for every current position, regardless of when it was opened. A position admitted at state 3 is treated as if it had existed since state 0 and had already suffered the whole move. When a position is reduced or liquidated, the associated loss simply disappears from `loss(current_positions, state)`; it is not realised into cash. This is not mark-to-market accounting.

Consequently E4/E5's breach ticks, liquidation triggers, and final requirements are deterministic but do not measure the mechanism described for a venue. The factor-of-two story also relies on this retroactive loss treatment for new positions.

**What would fix it.** Track fill price/cost basis, cash and realised P&L, current mark, partial fills, open-order reservations, and fees. An order filled at the current mark initially changes position and requirement but not equity except fees; later mark changes alter equity. Liquidation realises P&L rather than deleting it. Rerun E4/E5 with non-zero publication and liquidation latency.

**Page trade:** replace the existing E4/E5 tables and their interpretation after rerunning; no extra body pages are needed.

## Major findings

### M1 — The 16-state derivation is arithmetically tidy but not a cross-margin derivation

**Files/sections:** `paper/01_context_and_requirements.md` §1.5; `paper/02_architecture.md` §2.4; `marginstream/risk.py` factor model.

**What is wrong.** `3% / 0.2% = 15` bands and therefore 16 endpoints is correct. The premise “10x leverage means a 1% index move removes 10% of equity” is only true for a one-directional portfolio whose net index delta/equity is 10. Gross leverage does not determine equity sensitivity for a hedged, 40-underlying cross-margin portfolio. Long and short accounts also have opposite adverse directions.

A single scalar, globally monotone `k` cannot describe arbitrary 40-dimensional mark moves unless the venue explicitly adopts the one-factor beta model in the simulator. If it does, that restriction must be in scope and basis/idiosyncratic moves need a residual bound. If it does not, `k` must be derived per account from a vector of published marks, and K is not obtained from one index span.

The assertion that K=4 measurements are upper bounds for K=16 is also unproved. Finer schedules can admit more orders and can increase exposure under suppression; there is no monotone coupling in E4/E5.

**What would fix it.** Replace §1.5 with a derivation from worst account delta/beta permitted by the risk limits, or explicitly freeze a one-factor venue model. Define the mapping from a continuous mark vector to a state and prove `loss(Δ,k) <= R(Δ)` for every point inside a band, not merely for named grid points.

**Page trade:** replace §1.5's four-step chain; do not append another derivation.

### M2 — `M = R + A` is a chosen simplified venue model, not yet a demonstrated real-venue decomposition

**Files/sections:** `paper/02_architecture.md` §2.3; `paper/09_ai_disclosure.md` §9.3 item 1; `tests/test_algebra.py`.

**What is wrong.** For exact real arithmetic, the stated `R` is sub-additive and `phi(gross)` is super-additive when `phi` is convex through zero. That proves properties of the selected model; it does not show that all material terms in a production futures margin rulebook have one of those two forms.

A concrete omitted “neither” term is a directional liquidity/close-out impact for a signed risk bucket, `L(q) = c*q^2`. For two same-direction shard exposures, `L(1+1)=4c > 2c` (super-additive). For opposite exposures, `L(1-1)=0 < 2c` (sub-additive). It is neither uniformly across shards. A gross-based `A` can upper-bound it, but then it is a deliberately conservative envelope and the capital-efficiency loss must be measured.

Other terms must be placed explicitly rather than assumed away: open-order exposure, collateral haircuts and FX moves on the equity side, funding/fees during an epoch, delivery/basis charges, floors, caps, and conditional spread credits. Some may be additive for this symbol mapping; that has to be shown term by term.

The integer implementation also breaks Lemma 2 at rounding boundaries. With scale `1,000,000`, `A(1)=1`, `A(1)+A(1)=2`, but `A(2)=1`. The sampled test misses this. Round the exact central total once or add an explicit rounding reserve.

**What would fix it.** Replace the sampled-gap paragraph in §2.3 with a small component table: exact term, side of the balance sheet, algebraic class, edge treatment, and conservative fallback. Keep sampled implementation tests in the appendix.

### M3 — A3's algebraic direction is right only after F1 is fixed; the proposed mitigations do not mitigate the distortion

**Files/sections:** `paper/06_security_and_threat_model.md` §6.3 A3; §2.4 corollary.

**What is wrong.** If the safe design actually charges `sum_g R(P_g)`, merging two risk partitions weakly lowers that sum, so a coarser/fewer-shard placement weakly lowers the sub-additivity gap. That mathematical direction is correct.

The current code does not charge the absolute shard sum, so its distortion is not bounded by the stated gap; F1 shows it can become unsafe. Even after correction, a trader cannot generally choose a shard directly—contracts are statically mapped—so the business claim should be “shard-placement bias among economically substitutable contracts,” not a universal reward for concentration.

Usage-based weights reduce stranded lease capacity. They do not reduce the sub-additivity gap. Reporting the gap is monitoring, not mitigation.

**What would fix it.** Measure equivalent hedge portfolios under alternative symbol-to-risk-shard mappings. A real mitigation is to make risk groups stable and correlation-aware rather than identical to physical matching shards, or to issue centrally controlled cross-shard offset credits. If neither is built, explicitly accept a quantified placement tax.

**Page trade:** replace the two lesser abuse cases in §6.3 with the A3 experiment and move the lesser cases to the appendix.

### M4 — The Fermi arithmetic contains several correct multiplications but does not support the sizing conclusions

**Files/sections:** §§1.5, 1.6, 1.7, 5.2 and 5.5.

| Section and estimate | Arithmetic verdict | Design consequence |
|---|---|---|
| §1.5: 3% span / 0.2% band = 15 bands, ~16 states | Correct, conditional on the invalid 10x net-sensitivity premise | Does not derive K for a hedged multi-asset account |
| §1.5: 20x halves the band and gives ~32 states | Numerically reasonable: 30 bands plus endpoints, rounded to ~32 | Still conditional on net beta equalling gross leverage |
| §1.6: 16 scenarios x 5 contracts = 80 multiply-adds | Correct for a median five-contract account and a 16-scenario model | The code uses seven factor scenarios; tail accounts hold 50 and may dominate updates |
| §1.6: 16 states x 80 = ~1,280 per feasibility check | Multiplication correct, operation model wrong | `R(P)` and per-state existing losses can be precomputed; a bisection step need not recompute a full 16x5 scenario evaluation for every state |
| §1.6: 30 bisections over 1e9 | Correct: `log2(1e9)` is about 30 | Does not itself set an epoch |
| §1.6: 40k/account, 400m/epoch, 4bn/s, 250m/s on each of 16 shards | Internally consistent from the 1,280 premise | The premise overcounts reusable work, while median-based sizing undercounts the hot tail. “Ordinary workload” and 50–200 ms need a benchmark and utilisation target |
| §1.7: 16 adds + 16 comparisons + 3 scalar operations | Correct as a nominal cached-vector kernel | “Tens of nanoseconds” is unmeasured; the supplied code recomputes `R` over positions twice and does not implement this kernel |
| §1.7: 128 B per pair; 500k pairs; 64 MB | `16*8=128` and `500k*128=64 MB` are correct raw payload arithmetic | A median cannot be multiplied into a fleet total; use mean unique shards. The owner is unresolved, and gateway replication/object overhead is omitted |
| §5.2: 50k lease records/epoch, 500k/s, 32 MB/s | Correct raw arithmetic from 10k accounts, 5 records and 64 B | Record framing, replication and the actual gateway-by-shard unit are omitted |
| §5.2: 100k input records/s x 80 B = 8 MB/s; saving 24 MB/s | Correct raw arithmetic | Does not include admission-attempt journal traffic or exact per-gateway state observations |
| §5.2: band crossings are rare | No arithmetic supplied | Bound crossings under oscillation and a 1M/s burst before calling them negligible |
| §5.5: 160 MB + 27 MB at 2 GB/s ~= 0.1 s | Division correct | Table says 160 MB **per matching shard** and 27 MB **total**, then adds them as though colocated. A full eight-shard node is 1.28 GB before allocator state |
| §5.5: 500k pairs x 80 = 40m operations | Multiplication correct | Pair count and work per pair use inconsistent “median” assumptions; sub-second is unmeasured |
| §5.5: 1m commands/s x 50 s = 50m commands = 500 s of a 100k/s stream | Correct for orders alone | The log already has ~100k schedule records/s too. A five-minute tail is ~60m records before the audit journal; at a fixed 1m/s it consumes the full 60 s budget |
| §5.5: at 2x replay, snapshot about every minute | Conservative but plausible if “2x” means two times the **total** live log rate | Define cold restart: a node “with nothing” must fetch bytes over the network, while the arithmetic assumes local NVMe and no network |

**What would fix it.** Replace the prose estimates with one compact worksheet using mean and p99 account sizes, total logical record rate, physical replication rate, a chosen maximum utilisation, and measured scalar/SIMD throughput. The derived shard count should be the output, not an input selected to make the rate ordinary.

**Page trade:** this table can replace the current §1.6 derivation and §5.5 bullets at roughly the same length.

### M5 — The evidence suite does not test the paper's claimed mechanism

**Files/sections:** `experiments/e1_safety.py`, E2, E4, E5, `tests/test_algebra.py`, `REPRODUCE.md`.

**What is wrong.** E1 uses the scalar `Allocator`, not `CurveAllocator`, and has no intra-epoch market states. Its final sub-additivity gaps are only 0 or 1 in all eight seeds, so it does not stress the cross-shard offset property at the centre of the paper. Lease exhaustion shows a counter bound; it does not validate the theorem.

E2's random sweep with fencing disabled records zero violations for all eight seeds; only the scripted case fails, and the JSON file stores the zero-violation sweep rather than the scripted negative control. E2 also does not test stale-lease/stale-order agreement after the claimed repair.

E4's “3.8x” compares configurations with radically different liquidation counts and final inventory. E5's “zero cost on an honest feed” follows because the honest path is monotone inside every epoch; real current prices recover. If the published state is defined as worst-since-epoch-start, say that explicitly—then replay protection is a property of the publisher, not the reader ratchet.

**What would fix it.** Add deterministic property cases for F1–F4, multi-factor long/short portfolios, state oscillation, non-zero feed/liquidation latency, and persistent open orders. Report maintained risk/notional, liquidation volume and shortfall, not only accepted-order count.

**Page trade:** replace E1's seed table and the current E4 accepted-order comparison; put full seed output in the evidence appendix.

### M6 — The audit replay claim cannot reconstruct the decision a gateway actually made

**Files/sections:** `paper/02_architecture.md` §2.6–2.7; `paper/05_failure_and_recovery.md` §5.2; Figure 2.

**What is wrong.** Refused orders never enter the matching command stream, yet NFR 10 promises every admission decision. A globally logged market-state crossing also does not prove which bounded-stale state a particular gateway had received before its local decision. Replay needs the gateway's local sequence, observed state/version, lease identity, spend-before value, order payload and result. The diagram only journals after the matching path and does not connect refusal branches to the journal.

The missing admission-attempt stream is at least order-rate scale and changes the 8/21 MB/s log arithmetic. If it is a separate audit journal rather than the consensus log, its durability, ordering and tamper evidence must be specified.

**What would fix it.** Log one compact decision record at the gateway before acknowledgement, with `(gateway, local_seq, order_id, lease_id, state_version, spend_before, charge, result)`, including refusals. Define how these records are durably ordered and joined to accepted matching commands.

**Page trade:** replace §5.2's general “log inputs, not derived values” conclusion with the exact replay record and revised bandwidth.

### M7 — The ledger section does not prove either account margin safety or venue solvency

**Files/sections:** `paper/04_data_and_storage.md` §4.3–4.4; `paper/01_context_and_requirements.md` NFR 11; §6.4.

**What is wrong.** “Holds <= consumed leases <= issued leases <= collateral” compares quantities that have not been defined as the same accounting unit. A risk charge is not necessarily a posted initial-margin hold, the central `A` reserve is not consumed at the edge, partial fills break the one-order/one-hold assumption, and market moves change requirement without consuming another lease.

Even if that chain held, `sum holds <= sum collateral` is not `sum user liabilities <= sum venue assets`. Customer available balances, holds, realised/unrealised P&L and withdrawal suspense are liabilities; custody/bank assets are a separate reconciliation.

The withdrawal-confirmation row posts `SUSPENSE -x / EXCHANGE_HOT -x`, which does not sum to zero under the section's own “postings sum to zero” convention.

**What would fix it.** Separate four invariants: per-account margin, ledger conservation, customer-liability reconciliation, and custody asset coverage. Give each its own equation and data source. Correct the withdrawal signs or define account normal balances explicitly.

**Page trade:** replace §4.3's current three-inequality proof and NFR 11's claimed consequence.

### M8 — Recovery arithmetic mixes node scopes and assumes away the network in a “node with nothing” rebuild

**Files/sections:** `paper/05_failure_and_recovery.md` §5.1 and §5.5; Figure 3.

**What is wrong.** The state table labels books per shard and allocator state total, while the 187 MB snapshot adds one full book shard to all allocator state. The deployment does not say such components are colocated. A truly empty node must receive a snapshot/log over the network; a local NVMe read applies to a process restart with retained disk, not a cold node.

Warm allocator failover also has no executed evidence that the follower issues the identical schedule without overlapping the former leader's leases. §8 proposes experiments, but the high band asks for applied consensus and recovery, not a future procedure.

**What would fix it.** Define three recovery targets—process restart with disk, replica replacement, and leader failover—and calculate each from the state actually hosted there. Run the allocator-leader kill and record election time, generation/curve hash equality, and overlapping capacity.

**Page trade:** replace §5.5 rather than add a second recovery subsection.

### M9 — Several high-band rubric descriptors are absent or only proposed

**Files/sections:** whole paper, especially §§8–9 and diagrams.

| High-band descriptor | Current status | The sentence/evidence that would have to exist and does not | What it should displace |
|---|---|---|---|
| Optimised flow including batching | Vectorisation is named; batching is absent | After measurement: “Allocator workers batch B accounts by shape/scenario version; at p99 account size the batch sustains X accounts/s at Y% utilisation.” | Replace “250m operations/s is ordinary” in §1.6 |
| Deterministic execution plus consensus applied | Integer determinism is shown; allocator failover is planned only | “Killing the allocator leader at log offset L elected a follower in X ms; both derived the same curve hash and no old/new capacity overlapped.” | Replace the hypothetical first part of §8.1 after running it |
| Production-ready diagrams including failure paths | Mermaid source exists, but no exported figures/PDF; authority labels conflict | The diagrams must identify the sole spend authority, quorum/failure boundary, revocation/expiry path, and stale-stale path. This is a drawing correction, not a prose sentence. | Redraw Figures 1–4; no page increase |
| Comprehensive error handling | A few business failures are covered; overload, log-full, corrupt snapshot, out-of-order fill, integer overflow, partial fill/cancel and poison-account handling are absent | A compact matrix of “fault -> detector -> immediate safe state -> recovery gate -> owner.” | Replace inherited order-book/storage exposition in §4.1 and repeated scope lists |
| Performance-tuning insight | Contiguous arrays and vectorisation are argued, not benchmarked | “With account state resident/missing L1/L2/LLC, admission p50/p99 is X/Y; batching and NUMA pinning change it by Z.” | Replace the unmeasured “tens of nanoseconds” sentence |
| Deep distributed ownership | Section and module owner cells are blank; no secondary owners or cross-examination evidence | “Each section has a primary and secondary owner; on [date] every member reproduced one experiment and passed a five-question cross-examination outside their section.” | Fill/replace §9.5; appendix space |
| Genuine internal debate | ADRs record alternatives and AI rejections, but no team dissent/decision trail | One ADR row needs owner, date, dissenting member, evidence that changed the decision, and final sign-off. | Put in the appendix; shorten repetitive ADR prose |
| War-room readiness | §8.3 is a market-day narrative, not an incident command plan | “On alert 3, X is incident commander, Y owns risk, Z owns client/regulatory communication; halt authority is __; recovery requires invariants __ for __ minutes.” | Replace §8.3's generic before/during/after prose |
| Low-level capability tied to market strategy | Capital efficiency is asserted; no commercial sensitivity connects E4 to liquidity, spreads, maker retention or fee revenue | With explicit assumptions: “A reduction of the placement tax from X to Y changes admitted maker notional by Z and expected fee/spread KPI by W.” | Replace repeated product-value prose in §1.1 and ADR-1 |
| Actionable regulatory posture and long-term scalability | Evidence types are named; no control owner/cadence, scaling trigger or reshard plan | “Control C is owned by R, produced every D, retained for T; at allocator utilisation U or accounts/epoch Q, accounts are rebalanced by procedure P without dual issuance.” | Replace the last generic regulatory paragraph and duplicate “not built” lists |

CAP in business terms and AI disclosure with rejected suggestions are already among the strongest rubric areas. They do not need more prose.

## Minor findings

### m1 — Assumption labels drift between 8x and 10x

**Files/sections:** §1.1 says median active account 8x; §1.5 derives at 10x; NFRs then read as venue commitments.

**Fix:** say 8x is the business median and 10x is the sizing percentile/stress assumption, or use one. Replace the existing two numbers; add nothing.

### m2 — The implementation and document use different scenario counts and different hot-path algorithms

**Files/sections:** §1.7 and `marginstream/risk.py`/`shard.py`.

**What is wrong.** The paper sizes `|S| = 16`; code uses a seven-point factor grid. The paper claims cached-vector O(|S|) updates; code calls `R` before and after and loops over all local positions. The simulator is valid as a functional toy but not evidence for the latency or memory layout.

**Fix:** label those parts “designed, not implemented,” as honestly as the ledger is labelled, or implement the cached vector before citing it as evidence. Replace the latency claim.

### m3 — E2's structured result omits the only violating run

**Files/sections:** `experiments/e2_negative.py`; `results/e2_negative.json`; `REPRODUCE.md`.

**Fix:** store Part A's fenced/unfenced scripted records and violation in JSON. Keep Part B separately so a machine reader does not see eight zero-violation rows under the name “negative” and miss the real negative control.

### m4 — Several operational claims are detectors, not diagnoses

**Files/sections:** §6.5 and §8.2.

**What is wrong.** A rise in reduce-only rate without the external “true” market is not a unique signature of mark suppression; it can result from allocator lag, a bad shape, correlated client flow or a generation rollout. Likewise “market-state staleness” requires an independent time/source reference.

**Fix:** call these anomaly alerts and list the first disambiguating checks. Replace the claim that alert 5 detects A2.

### m5 — Constructor invariants are not enforced

**Files/sections:** `CurveAllocator.__init__` and `ConditionalLease.at`.

**What is wrong.** Shape monotonicity, non-negativity, factor/state length equality and a non-empty curve are assumed. A malformed versioned shape can increase capacity in an adverse state or crash replay.

**Fix:** validate at activation and reject the log command deterministically. This belongs in the error matrix, not a new subsection.

## Five hostile panel questions the draft currently cannot answer

1. **“Show the inequality that turns clipped local marginal-R charges into a bound on global `R(P+Δ)-R(P)`. What happens when `(+10,-10)` becomes `(+10,+10)` and the flipped shard's local R is unchanged?”**

2. **“If I spend 100 when the curve allows 150, then the market moves to a state where the curve allows 49 and no new order arrives, what prevents requirement 200 against equity 100 before liquidation?”**

3. **“A fill is absent from the allocator at the next epoch, and an old resting order is still executable. Exactly which watermark or persistent reservation prevents the same collateral being leased again?”**

4. **“Who is the authoritative spender: gateway or matching shard? If both the gateway and shard have generation 1 during a partition, how do they learn generation 2 before accepting a generation-1 order?”**

5. **“How does one monotone state index represent adverse moves for both long and short portfolios across 40 underlyings, and where is the proof that every continuous state inside a band is bounded by the scenario maximum?”**

## Recommended repair order

1. Freeze prose work. Write executable counterexamples for F1–F4 and make them fail the current implementation.
2. Decide the actual authority topology and the lifecycle of an open-order reservation.
3. Rewrite the safety invariant using absolute risk-shard envelopes plus a gross-notional lease and a causal issuance watermark.
4. Decide whether the curve is a trigger or part of a latency-bounded safety proof.
5. Repair P&L accounting, then rerun E1–E5 with multi-factor, long/short, oscillating and delayed cases.
6. Only then redo the arithmetic, diagrams, rubric evidence and defence script.

Polishing the current document before these steps would preserve claims the supplied implementation can deterministically falsify.
