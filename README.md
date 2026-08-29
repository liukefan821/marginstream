# MarginStream

A simulator for cross-margin admission control on a symbol-sharded venue.

The margin requirement for an account is `M(P) = R(P) + A(P)`, where `R` is the
worst loss over a fixed scenario set and `A` is a convex add-on in gross
notional. `R` is sub-additive across shards and `A` is super-additive, so a
budget can be split into per-shard leases only for the `R` part; the `A` part is
reserved centrally. Shards admit orders against their lease with a local
computation and no cross-shard read.

A lease can be a single amount, or a non-increasing function of the published
market state. The second form is evaluated by the shard against the state it
already receives on the market-data path, so the amount available falls as the
market moves adversely with no message from the allocator. A lease cannot undo
an admission it has already granted; what the curve provides is a locally
computable point at which a shard stops admitting risk-increasing orders and
reports the condition.

Reading the state on the market-data path puts a requirement on that path which
it did not previously carry. A shard may be configured to evaluate its curve at
the most adverse state it has observed since the lease was installed rather than
at the state on the current message, which removes any gain from replaying an
older and more favourable state.

## Layout

    marginstream/risk.py         scenario term R, add-on term A, marginal cost
    marginstream/allocator.py    budget solve by bisection, lease issuance, generations
    marginstream/shard.py        local admission, fencing
    marginstream/invariants.py   independent recomputation of the checked quantities
    marginstream/sim.py          seeded harness: epochs, lease delivery loss, liquidation
    experiments/e1_safety.py     seeded sweep with fencing enabled
    experiments/e2_negative.py   scripted case and sweep with fencing disabled
    experiments/e4_conditional.py scalar lease against price-conditional lease
    experiments/e5_adversarial.py  market-state suppression against the curve
    tests/test_algebra.py        sampled checks of the two algebraic properties

## Running

    python3 tests/test_algebra.py
    python3 experiments/e1_safety.py
    python3 experiments/e2_negative.py
    python3 experiments/e4_conditional.py
    python3 experiments/e5_adversarial.py

No dependencies beyond the standard library. Python 3.12.3 was used.

## Determinism

All arithmetic is integer. Divisions that produce a requirement round up. The
harness reads no wall-clock time and draws only from a seeded `random.Random`.
A given `(seed, Config)` reproduces the same counters.

## What is not in here yet

- E3, the capital-efficiency sweep over correlation structure.
- A mark-price pipeline. E5 shows that evaluating the curve at the worst
  observed state removes the gain from replaying an older state but does not
  address a feed that reports a rise late; that belongs to the pipeline.
- Adversarial lease capture: an account spraying orders across shards to take
  lease before other flow can.
- A liquidation waterfall. Reduction is a proportional scale-down; there is no
  partial liquidation, insurance fund or auto-deleveraging.
- The ledger module (hold and lease are separate concepts; only the lease side
  is implemented).
- Matching. Shards hold positions; they do not match orders against a book.
