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

which contains no market state. The schedule remains a local trigger and a
capacity lever, and c5 tests the trigger rather than a guarantee.

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
