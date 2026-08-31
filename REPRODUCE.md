# Reproduction

Environment used for the recorded output below:

    Python 3.12.3, standard library only, Linux x86_64

## Commands and recorded output

### tests/test_algebra.py

    $ python3 tests/test_algebra.py
    trials per test: 2000
    R: sum of parts minus whole, largest observed = 27929
    A: whole minus sum of parts, largest observed = 142052
    admission bound held on every sample

Seeds are fixed inside the file (11/12, 21/22, 31/32).

### experiments/e1_safety.py

Seeds 1-8, `fencing=True`, `equity_drop_bps=1800`. Exit code 0 when no
violation is recorded, 1 otherwise. Writes `results/e1_safety.json`.

    seed    acc    rej  stale    exh  viol          M     equity      gap
       1     95    385     75    162     0      11712      13527        1
       2    118    362     96    157     0      10456      13527        0
       3     99    381     97    160     0      10578      13527        0
       4    106    374    122    190     0      12249      13527        1
       5    107    373    108    144     0      11665      13527        0
       6    104    376     97    170     0      11161      13527        0
       7     96    384     82     97     0      11639      13527        0
       8    110    370     92    182     0      10451      13527        1

    seeds=8  violations recorded = 0

Columns: accepted, rejected, rejected for stale generation, rejected for lease
exhaustion, violations, final requirement, final equity, and the observed
sub-additivity gap.

### experiments/e2_negative.py

Part A is deterministic. Writes `results/e2_negative.json`.

    fencing=on
      epoch 0  equity=20000  budget=17082  lease0=8541  lease1=8541  gen=1
      epoch 1  equity=10000  budget=9160   lease0=4580  shard 1 not updated  gen=2
      accepted=45  refused=75  M=4703  equity=10000  shard0_spent=4500  shard1_spent=0
      violation: none recorded

    fencing=off
      epoch 0  equity=20000  budget=17082  lease0=8541  lease1=8541  gen=1
      epoch 1  equity=10000  budget=9160   lease0=4580  shard 1 not updated  gen=2
      accepted=92  refused=2  M=10047  equity=10000  shard0_spent=4500  shard1_spent=4700
      violation: I5 requirement 10047 exceeds equity 10000 for A1

## Failure modes seen while building this

- An earlier version checked `M(P) <= equity` without a liquidation path. An
  equity fall put existing positions above the requirement with no order having
  been admitted, and the check fired. Liquidation at the epoch boundary was
  added, and the check now separates the two cases.
- An earlier version of `Shard.admit` compared the order's generation with the
  lease's generation only. A shard holding a superseded lease therefore served
  orders that carried the same superseded generation. The shard now tracks the
  highest generation it has observed and refuses to serve when its lease is
  below it.
- With a large add-on coefficient the budget solve returned zero from the
  second epoch onward and the run became degenerate. `addon_scale` is the
  parameter that controls this.

### experiments/e4_conditional.py

Eight epochs of sixty ticks. The published market state index advances from 0
to 3 within each epoch. Writes `results/e4_conditional.json`.

              mode   acc  ro_hits   liq  breach_ticks    of   final_M
            scalar    36        0     0           236   480    169325
        curve:flat    29        0     0             0   480    121127
        curve:mild    58        6     6             0   480    129039
       curve:steep   111       17    17             0   480    114027

Columns: orders accepted, ticks on which a shard reported the reduce-only
condition, liquidations run, ticks spent with the requirement above equity, the
total tick count, and the final requirement.

## Further failure modes seen while building this

- The first version of the conditional lease omitted the requirement the
  portfolio already carried from the left-hand side of the pointwise condition.
  The flat curve then recorded more breach ticks than the scalar lease.
- Re-running liquidation on every tick that reported the condition compounded
  the reduction and drove positions to zero. Liquidation is now followed by a
  generation bump and a re-issue, which resets the consumption counters.

### experiments/e5_adversarial.py

Same market path as E4. In the suppressed runs the state index handed to the
shards is held at 0 between ticks 20 and 50 while the invariant checker uses the
true state. Writes `results/e5_adversarial.json`.

          mode  suppressed   acc   liq  breach_ticks    of   final_M
      no_curve       False    36     0           236   480    169325
      no_curve        True    36     0           236   480    169325
         naive       False    58     6             0   480    129039
         naive        True    62     6            12   480    129611
       ratchet       False    58     6             0   480    129039
       ratchet        True    49     4             5   480    130690

## Mechanism revision after external review (2026-08-30)

`review/2026-08-30_external_hostile_review.md` produced five counterexamples
against the implementation. All five were reproduced against the code before
any change was made, and are pinned in `tests/test_counterexamples.py`.

What changed:

- **Admission compares absolute envelopes, not increments.** Charging the
  increase in a gateway's requirement does not bound the account's requirement:
  a leg flipped from short to long leaves the local value unchanged while the
  account's value moves to its maximum. `Gateway.admit` now compares
  `R(admitted set + order)` against the lease.
- **The lease carries two resources.** An order can reduce a gateway's
  requirement while raising gross notional, and the add-on term is a function
  of gross. Risk envelope and gross envelope are issued and checked separately.
- **The add-on is compared through exact integers.** Ceiling-rounded add-on
  values are not super-additive at small arguments; the safety condition now
  multiplies through by the denominator instead of rounding.
- **Leases expire.** A generation bump alone does not reach a partitioned
  gateway. Leases carry a term and a gateway past its term refuses without any
  message.
- **The risk decomposition is partitioned by gateway, not by symbol.** Lemma 1
  holds for any partition, and the gateway is where admission happens.

What did not survive: the claim that a shrinking schedule makes the mechanism
safe. It does not, because a lease cannot reduce a position it has already
admitted. The safety condition is

    2 * sum_g risk_g + A(sum_g gross_g) <= collateral

which contains no market state. The schedule remains a local trigger and an operational tightening. It is not a
capacity mechanism: a flat lease at the level solved for is equally safe and
admits at least as many orders as any decaying one. c5 tests the trigger rather
than a guarantee.

    $ python3 tests/test_counterexamples.py
    [pass] c1 the envelope bounds the global requirement
    [pass] c2 gross notional stays inside its own envelope
    [pass] c3 add-on is super-additive in the units the condition uses
    [pass] c4 an undelivered revocation takes effect by expiry
    [pass] c5 the gateway detects the condition locally on the tick
    5 of 5 properties hold

    $ python3 tests/test_envelope_fuzz.py
    trials: 400, orders per trial: 300
    no trial produced a requirement above collateral

`experiments/e1`, `e2`, `e4` and `e5` still target the previous allocator and
gateway interfaces and have not been re-run against the revised mechanism.
Their recorded output above describes the implementation as it was before this
revision.

## Second review round (2026-08-30)

The same reviewer attacked the revised interfaces and found two further
counterexamples, both reproduced before any change:

- **c6, capacity reissued over a live lease.** A term bounds how long a
  partitioned gateway keeps serving; it does not entitle the allocator to hand
  that capacity to anyone else meanwhile. The allocator now tracks outstanding
  leases per gateway and budgets, at every gateway with an unexpired lease, for
  the larger of the old and new ceilings. A replacement brought up inside the
  old term receives nothing until the term ends.
- **c7, weight migration below existing usage.** Lowering a gateway's ceiling
  does not remove the positions it already admitted, so a ceiling is floored at
  what that gateway occupies, in both resources. When the floors alone do not
  fit the global condition the account receives ceilings equal to its floors,
  which is reduce-only.

Two further corrections came out of the same round:

- `c5` now calls `Gateway.observe_market_state`, so it tests that the condition
  is reported on a market-state tick with no order present, rather than that
  the next order to arrive is refused.
- The fuzz was a single-issuance check against collateral. It is replaced by
  `tests/test_lifecycle_fuzz.py`, which runs several generations per trial with
  lease delivery loss, weight changes, gateway replacement and clock advance
  past lease terms, and whose oracle compares the requirement against equity at
  every scenario in the grid rather than against collateral.

The upgraded fuzz found three breaches in 300 trials on the first run. The
cause was that the allocator overwrote its record of an outstanding lease at
each issuance, so a gateway still holding an older and larger lease stopped
being counted; and that the ceiling floors covered the risk resource only, not
gross notional. Both are fixed.

    $ python3 tests/test_counterexamples.py
    7 of 7 properties hold

    $ python3 tests/test_lifecycle_fuzz.py
    trials: 300, generations per trial: 6, admissions: 7498
    oracle: M(P) <= collateral - loss(P, k) for every k in the grid
    no trial exceeded equity at any scenario

    extended run: trials 1500, admissions 35642, breaches 0

Still open, and not claimed closed:

- A gateway tracks the orders it admitted, not the positions that filled. Two
  resting orders that net to zero are treated as zero risk; if only one fills
  the account carries the other side. The envelope has to be taken over the
  worst fill subset, which for a scenario-linear loss is the sum of positive
  parts per scenario.
- A gateway is now a stateful authority. Its crash, rebuild, replication and
  failover are not designed.
- If matching does not re-check the lease, a fully compromised gateway can
  ignore it. Section 6.1 claims the blast radius is bounded by the lease; that
  claim has to be replaced with an admission that the gateway is inside the
  trusted computing base.
- The schedule provides no capacity benefit under the corrected condition: a
  flat lease at the level solved for is equally safe and admits at least as
  many orders. Its remaining value is stress tightening, the local trigger, and
  whatever it saves in tail exposure and forced reduction, none of which is yet
  measured. Accepted-order count is not the metric for it.

## Third review round (2026-08-30)

Three further counterexamples, all reproduced against the code before any
change. The rule they share is one line:

> a lease term ends a holder's authority to admit; it never ends the exposure
> that holder already created.

- **c8, expiry released exposure.** A replacement brought up after the old term
  ended was granted the capacity the old holder's positions still occupied.
  The previous round's c6 missed it because its oracle compared against
  collateral rather than against equity at each scenario, and the breach sat
  exactly on that boundary.
- **c9, an infeasible solve was not reduce-only.** Issuing ordinary envelopes
  at the floor let a gateway close its own leg, which lowered that gateway's
  requirement and raised the account's by removing a hedge held elsewhere.
  Local risk reduction is not global risk reduction.
- **c10, one identity, two live processes.** Outstanding capacity was keyed by
  gateway id and collapsed by `max`, so a restarted process reusing its id
  could spend a full ceiling alongside its predecessor.

What changed:

- Holders are `(gateway_id, incarnation)`. Capacity is summed across
  incarnations rather than collapsed by identity.
- Two quantities per holder, only one of which expires: **authority**, which
  the term ends, and **committed exposure**, which only an authoritative
  position reconciliation reduces. The solve covers every holder that appears
  in either, including retired ones.
- An infeasible solve issues leases in **quarantine** mode and the gateway
  admits nothing. Reducing risk from that state needs a check against the whole
  account.
- The per-scenario equity oracle is used in c6, c7, c8, c9 and c10. c4 tests
  term behaviour only and does not use it.

The lifecycle fuzz now retires holders, restarts processes under the same id,
cuts collateral, and reports which branches it actually exercised.

    $ python3 tests/test_counterexamples.py
    10 of 10 properties hold

    $ python3 tests/test_lifecycle_fuzz.py
    trials: 300, generations per trial: 6, admissions: 5489
    branches exercised: join=340, retire=209, retire_with_positions=131,
      same_id_restart=359, collateral_cut=422, quarantine=27, lease_lost=1235
    oracle: M(P) <= collateral - loss(P, k) for every k in the grid
    no trial exceeded equity at any scenario

    extended run: trials 1500, admissions 25870, breaches 0
    branches: join=1657, retire=1143, retire_with_positions=675,
      same_id_restart=1762, collateral_cut=2155, quarantine=192,
      lease_lost=6306

Two claims corrected rather than defended:

- `gross_per_risk` fixes the ratio between the two ceilings, so the solver
  moves along one ray through a two-dimensional feasible set. This is two
  independent checks against a fixed-ratio issuance policy, not a two-resource
  allocation, and the code says so.
- The cross-generation lease lifecycle is evidence of rigour, not a
  contribution. Expiry, incarnation, reissue and persistent occupancy are
  standard lease machinery. The contribution remains the absolute scenario
  envelope, the gross constraint that the add-on term forces, and admission
  partitioned by gateway.

Still open: a gateway tracks the orders it admitted, not the positions that
filled; a gateway is a stateful authority whose crash and failover are not
designed; and section 6.1's claim that a compromised gateway's blast radius is
bounded by its lease has to be replaced by an admission that the gateway is
inside the trusted computing base.

## Fourth review round (2026-08-30)

The rule from the previous round was half of one:

> Expiry ends permission; only terminal, ordered reconciliation releases
> exposure.

Three counterexamples, all reproduced before any change:

- **c11, an expired term with no usage report.** The previous round's c8 fed
  the allocator the old holder's exact final usage before the replacement was
  issued. A partitioned or crashed holder is precisely the case where that
  report does not arrive. With it removed, capacity was reissued and the breach
  came back. A term that ends without a terminal reconciliation now keeps its
  whole ceiling occupied.
- **c12, a stale reconciliation.** Reports now carry the holder's admission
  watermark and one carrying an older watermark than a report already applied
  is dropped.
- **c13, retirement revoked a live lease.** `retire` deleted the holder's
  authority, so a retired but partitioned process stopped being counted while
  it was still inside its term. Retirement now only stops future issuance.

`release(holder, seq, usage, now)` is the only path that lowers exposure. It is
refused while the holder is still inside its term, and refused if its watermark
is behind one already applied.

A separate defect surfaced while testing: a holder that had been terminally
released and was then leased again stayed marked as released, so its new
ceiling was dropped from the accounting.

### The fuzz was measuring the wrong thing

Branch counters recorded that a retirement happened, not that it happened while
the retired holder's term was still running. With terms drawn from 2 to 19 and
40 to 60 orders per generation, an old term always ended before the next
generation began, so the overlap those counters implied had never occurred.
Terms are now drawn so a good share of them outlive a generation, and the
counters record the condition rather than the event.

The oracle was also too loose. A collateral cut can put an existing portfolio
above its equity with no order having been admitted; that is a credit event for
the liquidation path. The oracle now measures the shortfall before and after
every admission and fails on an increase, which is the property the mechanism
actually claims.

    $ python3 tests/test_counterexamples.py
    13 of 13 properties hold

    $ python3 tests/test_lifecycle_fuzz.py
    trials: 300, generations per trial: 6, admissions: 13322
    conditions reached: retire_with_live_authority=48,
      restart_with_live_predecessor=112, lost_usage_report=1812,
      expired_unreconciled_term=1583,
      old_holder_admitted_on_its_own_generation=8509,
      stale_reconciliation_rejected=404, terminal_release_accepted=1602,
      preexisting_breach_after_collateral_cut=27, quarantine=337,
      lease_lost=1011, increase_inside_a_pre_cut_term=1
    no admission increased the shortfall

    extended run: trials 1500, admissions 67129, failures 0

### A limit that is documented rather than fixed

A collateral reduction cannot bind faster than the terms already outstanding. A
gateway holding a lease issued before the cut keeps admitting inside that
ceiling, and no message from the allocator can stop it: that is what the term
is for. The fuzz separates these cases and counts them
(`increase_inside_a_pre_cut_term`) rather than treating them as failures.

The consequence for the design is that **the lease term is the latency of every
credit decision**, not only of revocation. A venue that needs a collateral cut
to bind within X must set the term below X, and §1.6's argument that the term
follows from recompute cost is therefore incomplete.

### Still open

- A gateway tracks the orders it admitted, not the positions that filled. The
  worst-fill envelope needs both a scenario form,
  `max_k [ loss_k(filled) + sum_i max(0, loss_k(remaining_i)) ]`, and a gross
  form, `sum_s mark_s * max(|p_s + B_s|, |p_s - S_s|)`. Implementing only the
  first would reopen c2.
- One holder incarnation is assumed to be a single-writer failure domain and
  not clonable. Two processes restored onto the same incarnation each spend a
  full ceiling. `Gateway.install_lease` now refuses a lease cut for a different
  identity or incarnation, which is a check and not a proof.
- A gateway is a stateful authority whose crash, rebuild and failover are not
  designed.
- Section 6.1's claim that a compromised gateway's blast radius is bounded by
  its lease has to be replaced by an admission that the gateway is inside the
  trusted computing base.

## Fifth review round (2026-08-30)

The previous round named its rule "terminal, ordered reconciliation" and
implemented only the ordering half. Three defects followed from that, and one
of the tests written to guard it did not guard anything.

- **c12 was vacuous.** It called `observe_usage` twice and checked that a
  smaller figure did not overwrite a larger one. `observe_usage` takes a
  maximum, so the test passed with the watermark check deleted. It never called
  `release`, which is the function whose behaviour it claimed to pin.
- **A watermark that does not go backwards is not coverage.** With every usage
  report lost the allocator's watermark stays at its initial value, so a report
  claiming zero usage was accepted as terminal and the ceiling was handed to a
  replacement.
- **Reports were keyed by holder, not by lease**, so a terminal report about
  one term released a later term at the same holder.
- **A clock comparison does not prove a holder has stopped.** The allocator
  compared its own `now` against the lease expiry; a partitioned gateway whose
  clock is behind keeps admitting.

What changed:

- Every issuance carries a unique `lease_id`, and authority is keyed by it.
- A gateway numbers its admissions under a lease from one upward and submits
  each to the ordering point, which accepts only the next number for that
  lease. The recorded sequence is therefore gap-free.
- `marginstream/sequencer.py` holds the fence. Fencing a lease stops anything
  carrying it from reaching a book, whatever any gateway believes the time to
  be, and returns a seal naming the last admission it recorded.
- `release(account, lease_id, seal, usage, sequencer)` is refused unless the
  lease is fenced, the seal is the one that ordering point issued for that
  lease, and its terminal sequence matches. A replay with identical figures is
  idempotent; one with different figures is a conflict.
- `retire` is enforced in `issue` rather than being a convention between the
  allocator and its caller, and `activate` undoes it.

Three counterexamples were added for the seal, and c12 was rewritten to
exercise `release` with no usage reports at all.

### The fuzz oracle was collapsing the property it claimed to test

It compared the largest shortfall across scenarios before and after each
admission. A portfolio whose shortfall vector rotates while its maximum stays
put passes that test, and the printed line claimed the check ran "at any
scenario". The oracle now compares the vector entry by entry.

Branch counters were also counting attempts rather than outcomes, and the
predicate for "is this holder's authority still live" was left keyed by holder
after authority moved to lease ids, which silently zeroed two of them. Both are
fixed, and the counters now separate submitted from accepted.

    $ python3 tests/test_counterexamples.py
    16 of 16 properties hold

    $ python3 tests/test_lifecycle_fuzz.py
    trials: 300, generations per trial: 6, admissions: 13253
    conditions reached: retire_with_live_authority=57,
      restart_with_live_predecessor=112, lost_usage_report=1808,
      expired_unreconciled_term=2458, old_generation_attempted=8365,
      old_generation_accepted=77, stale_report_submitted=626,
      stale_report_rejected=626, terminal_release_submitted=2501,
      terminal_release_accepted=2265, terminal_release_refused=236,
      preexisting_breach_after_collateral_cut=25, quarantine=392,
      lease_lost=966, increase_inside_a_pre_cut_term=1
    no admission worsened any scenario

    extended run: trials 1500, admissions 63691, failures 0

### A sentence corrected

The previous round recorded that "the lease term is the latency of every credit
decision". That is too broad. Raising a limit takes effect at the next
issuance; lowering one does too when the allocator can reach the gateways. The
accurate statement is:

> the lease term is the worst-case enforcement latency of a **tightening**
> decision **under partition**

and a withdrawal can be settled after the outstanding terms end rather than
requiring collateral to be lowered first.

### Still open

- Worst-fill. A gateway tracks the orders it admitted, not the positions that
  filled, and a fenced lease is still not terminal for exposure: resting orders
  admitted under it can fill afterwards. The envelope needs both
  `max_k [ loss_k(filled) + sum_i max(0, loss_k(remaining_i)) ]` and
  `sum_s mark_s * max(|p_s + B_s|, |p_s - S_s|)`, and a cancel must release its
  reservation only on an ordered confirmation.
- One holder incarnation is assumed to be a single-writer failure domain and
  not clonable.
- A gateway is a stateful authority whose crash, rebuild and failover are not
  designed.
- Section 6.1's blast-radius claim still has to be replaced by an admission
  that the gateway is inside the trusted computing base.

## Worst-fill round (2026-08-30)

The previous rounds were about who may admit and for how long. This one is
about what a gateway is actually holding. It holds orders, not positions: two
live orders of opposite sign net to nothing, and if only one fills the account
carries the other side.

### Envelopes

Because the loss under a fixed scenario is linear in positions, the worst fill
subset does not have to be enumerated:

    E_k   = loss_k(filled) + sum_i max(0, loss_k(order_i))
    R_wf  = max(0, ceil(max_k E_k / DEN))
    G_wf  = sum_s mark_s * max(|filled_s + buy_s|, |filled_s - sell_s|)

Both are checked. Only the first would reopen the case where an order lowers
the requirement while raising reachable gross notional.

Four running totals are maintained per account per gateway and updated on each
order state change, so admission costs one pass over the scenario grid however
many orders are live.

State transitions are one-directional: a cancel request releases nothing, and
only an acknowledgement that has come back through the ordering point removes
a reservation.

### The interface the reviewer asked to close

`release` no longer takes a usage figure from its caller. The ordering point
records each admission's payload and each fill and cancel acknowledgement, and
`Sequencer.reconcile` replays that log to produce the figure. A correct seal
paired with an optimistic usage claim used to be accepted.

A defect found while making that change: the per-lease figure was overwriting
the holder's total, so a holder with orders admitted under two leases lost one
of them. Sealed figures are now summed per holder.

### Evidence

    $ python3 tests/test_counterexamples.py
    16 of 16 properties hold

    $ python3 tests/test_worst_fill.py
    6 of 6 properties hold

    $ python3 tests/test_worst_fill_exhaustive.py
    trials: 4000, orders per trial: 0..8, subsets enumerated per trial: up to 256
    closed form equals enumeration on every trial, for both envelopes

    $ python3 tests/test_lifecycle_fuzz.py
    no admission worsened any scenario

    $ python3 experiments/e1_worst_fill_safety.py
    trials: 400, steps per trial: 200
    actions: admitted=35228, refused=713, filled=18921, cancel_requested=7593,
      cancel_acked=7588, released=63, release_refused=0, reissued=8011
    no state reached a requirement above equity

  The oracle takes both terms of the requirement over the worst fill subset. An
  earlier version took the add-on from the filled position alone, which
  understates it whenever an unfilled order can raise the gross notional the
  account reaches, and would have made a clean run a false negative.

    $ python3 experiments/e2_naive_netting_negative.py
            mode   ceiling  admitted  requirement  collateral  shortfall
      worst-fill      1000         2         1000        2000          0
         netting      1000       402       201000        2000     400000

    $ python3 experiments/e3_hot_path_benchmark.py
    python 3.12.3 on Linux x86_64; 4000 repetitions per figure
     grid  orders          mode  admit p50  admit p95
        7      50   incremental     4865.0      10977
        7      50     full scan    59143.0     106103
        7     500   incremental     4698.0       6015
        7     500     full scan   464749.5     671080
       16      50   incremental     6343.0       9322
       16      50     full scan   119582.5     149858
       16     500   incremental     6667.0       9564
       16     500     full scan   995371.0    1054377
    figures in nanoseconds

Reading E3: both modes compute the same envelopes and were checked to agree on
400 random books before being timed, so the comparison is of cost. Incremental
admission does not move with the number of live orders (4,698 ns at 500 orders
against 4,865 at 50) and grows by a factor of 1.36 when the grid widens by 2.3.
The full scan grows by 7.9 when the order count grows by 10, and by 2.0 when
the grid widens by 2.3. That is O(|S|) against O(orders x |S|), shown by the
scaling rather than by a single ratio.

The book is held at a fixed size during measurement: the order admitted in each
repetition is removed outside the timed window. An earlier version let the book
grow while it was being timed, which made the order-count column meaningless.

The absolute figures are CPython on a shared machine and are three orders of
magnitude away from what section 1.7 assumes for a compiled implementation.
They support the scaling argument, not the latency target.

E2 is the control that makes E1's clean run mean something: with netting
instead of worst-fill, the same script admits 402 orders against a ceiling of
1,000, and once the buys fill the requirement is 201,000 against 2,000 of
collateral.

### Still open

- Snapshot and replay recovery for the gateway's order state.
- Cost basis, realised profit and loss, and fees.
- The old E1/E2/E4/E5 remain results from earlier interfaces and are not
  claimed as current. The schedule ablation measured by accepted-order count
  has lost its argument and should be replaced rather than extended.
- Section 6.1's blast-radius claim still needs replacing with an admission that
  the gateway is inside the trusted computing base.

## Recovery round (2026-08-31)

### What a gateway actually has to persist

Taking the fields one at a time, the answer is that none of them has to be
persisted for correctness.

| Field | Classification |
|---|---|
| `filled` positions | a fold of the ordering point's fill records |
| `orders` and their remaining quantities | admissions less fills less acknowledged cancels |
| `filled_num`, `pos_part_num` per scenario | pure functions of the two above; cache |
| `buy_rem`, `sell_rem` per symbol | pure functions of the orders; cache |
| `gross_wf` | pure function; cache |
| `admission_seq` per lease | the ordering point's own last sequence for that lease |
| the installed lease | not restored. a recovered gateway waits to be handed one |
| ratchet `worst_state` | not restored. it is re-established from the next state tick |

So the gateway's safety-critical state is a deterministic fold of the log, the
same relationship the running case has between balances and the journal. A
snapshot is a way of bounding replay time, not a correctness requirement, and
that is why `Gateway.rebuild_from_log` exists as the reference the recovered
state is compared against.

### The protocol

A snapshot records only the authoritative fields plus the log position it was
cut at, and the aggregates are rebuilt on load, so a snapshot cannot disagree
with itself. `restore` loads it and replays the log after that watermark.
Refused, with the gateway left quarantined: a snapshot cut by a different
holder or incarnation, and one claiming a position the ordering point has not
reached. A gateway that has not finished recovering admits nothing; it does not
fall back on a local judgment that an order reduces risk, because c9 already
showed that judgment is unsound across gateways.

Replay is idempotent by log position. An earlier version relied on the order id
for that, which covers admissions and not fills or cancels, and r2 caught it:
replaying the same slice twice applied the fills twice.

### Evidence

    $ python3 tests/test_recovery.py
    6 of 6 properties hold

    $ python3 experiments/e4_recovery.py
    trials: 200, steps per trial: 150
    windows and counters: crashes=3642, recoveries=3642, before_any_snapshot=322,
      after_snapshot=2177, with_no_events_since=324, mid_partial_fill=2364,
      stale_snapshot_offered=819, other_incarnation_refused=516,
      events_replayed=74436, events_skipped=0, equivalence_checks=3642,
      equivalence_failures=0, admissions=13512, fills=6641, cancels=3519
    every recovery reproduced the state the log implies

The counters name the window a crash fell in rather than counting how often a
crash was called: 322 recoveries had no snapshot to work from and replayed the
whole log, 2,364 fell between two partial fills of one order, 819 were offered
an older snapshot than the newest available, and 516 were offered one cut by a
different incarnation and refused it. Each of the 3,642 recoveries was compared
against a gateway rebuilt from the entire log on filled positions, live orders,
both per-scenario aggregate families and both envelopes.

A defect found by that comparison: a per-symbol tally left at zero rather than
removed made two structurally different dictionaries hold the same quantities,
so 174 of the first run's recoveries were reported as disagreements when the
state was in fact equal. Tallies are now dropped when they reach zero.

### Regression across the frozen layers

    test_counterexamples   16 of 16
    test_worst_fill         6 of 6
    test_worst_fill_exhaustive  closed form equals enumeration
    test_algebra            admission bound held on every sample
    test_lifecycle_fuzz     no admission worsened any scenario
    e1_worst_fill_safety    no state reached a requirement above equity
    e2_naive_netting        netting reaches 201,000 against 2,000 of collateral

### Still open

- Cost basis, realised profit and loss, fees, and equity as
  `Collateral + realised + unrealised(k) - fees`. Until that exists the safety
  condition is stated against collateral rather than against equity as a venue
  would compute it.
- The allocator's own snapshot and failover. This round covers the gateway.
- Section 6.1's blast-radius claim still needs replacing with an admission that
  the gateway is inside the trusted computing base.

## Account equity round (2026-08-31)

### Connecting the ledger to the scenario model

The grid is a set of price displacements, not a set of settlement prices:
`leg_num(s, q, f) = -q * beta_s * f * scan_s`, so scenario `f` moves symbol `s`
by `dp_s(f) = beta_s * f * scan_s / DEN` and `loss(P, f) = -sum_s q_s dp_s(f)`.
Therefore

    E0    = collateral + sum_s ( cash_s + q_s * mark_s ) - fees
    E(f)  = collateral + sum_s ( cash_s + q_s * (mark_s + dp_s(f)) ) - fees
          = E0 - loss(P, f)

which is the form the oracle already used, with `E0` where `collateral` stood.
The safety condition carries over unchanged: the requirement is bounded by the
sum of the ceilings, the loss at any scenario is bounded by the same sum
because `R` is a maximum over a set containing that scenario, so

    2 * sum_h risk_h + A(sum_h gross_h) <= E0

is still sufficient. `loss` rounds a loss up, so scenario equity is understated
rather than overstated.

### Two ledgers, one of which decides

The safety path uses an exact cash-flow identity with no division in it:
`cash -= dq * p` and `q += dq` per fill, and `total_pnl(marks) = sum_s (cash_s +
q_s * mark_s)`. That covers opening, adding, partial and full closes and a fill
that crosses through zero, with no average cost and no rounding decision.

A first-in first-out lot list produces the realised and unrealised split a
statement needs. It is required to satisfy `realised + unrealised(marks) ==
cash + q * mark`, which is checked over 500 random fill sequences and on a
sequence of primes where any division would leave a remainder. Fees accumulate
separately and are subtracted once.

    $ python3 tests/test_account.py
    13 of 13 properties hold

### E1 against real equity

    $ python3 experiments/e1_equity_safety.py
    trials: 300, steps per trial: 160
    actions: admitted=4296, refused=15807, fills=4612, cancels=1842, reissues=7656
    account: realised=119995, unrealised=-40927, fees=3366
    max requirement 41427, min equity 203714, min headroom 201427,
      peak requirement as a share of equity 5%, violations 0
    no state reached a requirement above equity

The constraint is active: 15,807 admissions were refused against 4,296
accepted. The peak requirement is nevertheless a small share of equity, which
is structural rather than accidental. The factor of two caps utilisation at
about half of equity before the add-on reserve is taken, so the requirement
cannot approach equity by construction. That is a cost of the closure and it is
quantified in E5 rather than left as an impression.

### E5, an account that misreports equity

    $ python3 experiments/e5_flawed_equity_negative.py

    Part A - scripted
                    mode   reported     actual   ceiling  admitted  requirement  worst equity  shortfall
                   exact      42000      42000     21000       105        21000         42000          0
        ignores_realised      92000      42000     46000       230        46000         42000       4000
            ignores_fees      50000      42000     25000       125        25000         42000          0

    Part B - how large an overstatement has to be before it bites
        fee hidden   reported     actual  requirement  worst equity  shortfall
              8000      50000      42000        25000         42000          0
             16000      50000      34000        25000         34000          0
             24000      50000      26000        25000         26000          0
             32000      50000      18000        25000         18000       7000
             40000      50000      10000        25000         10000      15000
             48000      50000       2000        25000          2000      23000
             56000      50000      -6000        25000         -6000      31000

    Part C - the random run of E1 with each account feeding issuance
                    mode  trials  trials with a breach  largest overstatement  smallest equity
                   exact     120                     0                      0           205903
        ignores_realised     120                     0                   3139           205903
            ignores_fees     120                     0                     45           205903

An account that forgets a realised loss reports 92,000 where it has 42,000, is
issued a ceiling of 46,000 instead of 21,000, admits 230 orders instead of 105,
and ends above equity by 4,000. That is the control that makes E1's clean run
mean something.

Part B measures the tolerance rather than assuming one: the closure absorbs an
overstatement until it reaches 32,000 of a reported 50,000, which is where the
ceiling it buys exceeds what equity can carry. Part C shows that the random
configuration never produces an overstatement near that size, so it does not
breach; that is a measurement of the margin, not evidence that a misreported
account is safe.

### Still open

- Liquidation latency and the operational failure experiments.
- The allocator's own snapshot and failover; this round and the last cover the
  gateway and the account.
- Section 6.1's blast-radius claim still needs replacing with an admission that
  the gateway is inside the trusted computing base.

## Execution cost round (2026-08-31)

### The hole

A lease is solved against the equity the account has when it is issued. Two
things reduce that equity afterwards and neither moves a position, so neither
appears in the risk or the gross envelope: a fill lands at a price away from
the mark, and a fee is charged.

The counterexample puts the ceiling exactly at the binding point. With a
ceiling of 50,000 the gateway admits 250 lots whose requirement is 50,000, and
equity at the worst scenario is 50,000, so the account sits exactly on the
line. A fee of one per lot moves equity to 49,750 and the account is past it by
250; a fill one tick away from the mark does the same.

The earlier E1 did not find this because its peak requirement was five per cent
of equity. A constraint that is not binding hides every error of this shape,
which is the third time in this project that a clean run turned out to come
from slack rather than from correctness.

### The third envelope

    2 * sum_h risk_h + A(sum_h gross_h) + sum_h debit_h  <=  E0

`debit_h` bounds the execution cost a holder can still incur: for each order,
the quantity times the symbol's price band plus its fee cap. The band and the
cap are venue policy, which is what makes the bound finite; without a price
band there is no bound and no ceiling can be issued. A cancel acknowledgement
releases the reservation because nothing was executed; a fill keeps it, since
the cost has now been paid and equity has fallen by at most that much.

The envelope is not free, and the size of what it costs is visible:

    execution policy      ceiling  admitted  requirement  worst equity  breach
    no cost                 50000       250        50000         50000       0
    fee of 1 per lot        49875       249        49800         49951       0
    one tick of slippage    49875       249        49800         49951       0
    fee of 3 and 2 ticks    49382       246        49200         49570       0

    $ python3 tests/test_execution_debit.py
    3 of 3 properties hold

### The account is now a fold of the log

The ordering point records the price and the fee on every fill, so
`Sequencer.rebuild_account` can produce the account from the log alone. The
previous round's snapshot and restore test only showed that the object
serialises; a14 compares an account rebuilt from the log against the live one.

    $ python3 tests/test_account.py
    14 of 14 properties hold

### A claim withdrawn

The previous round reported that the closure "absorbs an overstatement until it
reaches 32,000 of a reported 50,000", and read that as a tolerance of about 64
per cent. That was wrong. The number came from a configuration whose ceiling
was not binding; it recorded where a coarse scan first crossed, not a property.
At the binding point the breach tracks the overstatement:

    overstated by   ceiling  admitted  requirement  worst equity   breach
                0     50000       250        50000         50000        0
                2     50001       250        50000         50000        0
              100     50050       250        50000         50000        0
             1000     50500       252        50400         49600      800
            10000     55000       275        55000         45000    10000

Small values show nothing only because one lot is 200 of requirement, so an
overstatement has to buy a whole lot before the ceiling can move. **The factor
of two is a closure, not a margin against a misreported account.**

### Regression

    test_algebra                admission bound held on every sample
    test_counterexamples        16 of 16
    test_worst_fill              6 of 6
    test_worst_fill_exhaustive  closed form equals enumeration
    test_lifecycle_fuzz         no admission worsened any scenario
    test_recovery                6 of 6
    test_account                14 of 14
    test_execution_debit         3 of 3
    e1_equity_safety            no state reached a requirement above equity
    e4_recovery                 every recovery reproduced the state the log implies

### Still open

- E1's random configuration still runs well below the binding point. Until it
  is driven to the ceiling its clean result is weaker evidence than the
  directed cases.
- Liquidation latency and the operational failure experiments.
- The allocator's own snapshot and failover.
- Section 6.1's blast-radius claim.
