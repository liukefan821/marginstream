# MarginStream

A simulator for cross-margin admission control on a symbol-sharded venue.

The margin requirement for an account is `M(P) = R(P) + A(P)`, where `R` is the
worst loss over a fixed scenario set and `A` is a convex add-on in gross
notional. `R` is sub-additive across shards and `A` is super-additive, so a
budget can be split into per-shard leases only for the `R` part; the `A` part is
reserved centrally. Shards admit orders against their lease with a local
computation and no cross-shard read.

## Layout

    marginstream/risk.py         scenario term R, add-on term A, marginal cost
    marginstream/allocator.py    budget solve by bisection, lease issuance, generations
    marginstream/shard.py        local admission, fencing
    marginstream/invariants.py   independent recomputation of the checked quantities
    marginstream/sim.py          seeded harness: epochs, lease delivery loss, liquidation
    experiments/e1_safety.py     seeded sweep with fencing enabled
    experiments/e2_negative.py   scripted case and sweep with fencing disabled
    tests/test_algebra.py        sampled checks of the two algebraic properties

## Running

    python3 tests/test_algebra.py
    python3 experiments/e1_safety.py
    python3 experiments/e2_negative.py

No dependencies beyond the standard library. Python 3.12.3 was used.

## Determinism

All arithmetic is integer. Divisions that produce a requirement round up. The
harness reads no wall-clock time and draws only from a seeded `random.Random`.
A given `(seed, Config)` reproduces the same counters.

## What is not in here yet

- E3, the capital-efficiency sweep over epoch length and correlation structure.
- Intra-epoch equity movement: the drift allowance `delta` is reserved in the
  budget but the harness only moves equity at epoch boundaries, so that
  allowance is not currently exercised.
- The ledger module (hold and lease are separate concepts; only the lease side
  is implemented).
- Matching. Shards hold positions; they do not match orders against a book.
