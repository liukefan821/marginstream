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
