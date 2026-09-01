# MarginStream

A deterministic simulator for cross-margin admission control on a symbol-sharded
derivatives venue. It is the evidence base for the whitepaper in `paper/`.

**The problem.** A unified cross-margin account has one margin requirement that
is global over the account and non-additive over contracts, and it must be
enforced *before* an order reaches a book — while the books themselves are
sharded by symbol and written concurrently.

**The mechanism.** The requirement splits as `M_k(P) = R(P) + A(G_k(P))`: a
worst-case loss over a finite scenario set, plus a convex add-on in gross
notional. `R` and gross are sub-additive across a partition and `A` is
super-additive, so the first two divide into per-gateway ceilings checked locally
and the add-on is evaluated once, centrally, on the summed gross. Each ingress
gateway holds **three** ceilings — scenario requirement, reachable gross, and
execution cost — and compares **absolute** worst-fill figures against them, never
the increment an order adds. The allocator issues them under

    2 * sum_g λ_g^R  +  A( sum_g λ_g^G )  +  sum_g λ_g^D   <=   E_0

where the factor of two is a closure, not a margin. There is no market state in
the condition. §2 of the paper has the derivation; ADR-2 records the
price-conditional schedule that an earlier version of this design used and that
is now withdrawn.

**Authority.** Every lease is registered at a single ordering point against an
account, a holder and an authority kind; the holder comes from the authenticated
session, never from the request. A term ends a holder's authority to admit and
never ends the exposure it created. Only a fence at the ordering point stops a
holder that is unreachable or dishonest.

## What is canonical, and what is not

| Path | Status |
|---|---|
| `marginstream/risk.py`, `account.py`, `sequencer.py`, `allocator2.py`, `gateway2.py`, `execution.py`, `liquidation.py` | **Canonical.** This is the mechanism the paper describes |
| `marginstream/allocator.py`, `gateway.py`, `shard.py`, `sim.py`, `invariants.py` | **Legacy.** Earlier interfaces, kept only because `experiments/superseded/` imports them. Nothing current imports them |
| `experiments/superseded/`, `results/superseded/` | **Superseded.** Produced by withdrawn mechanisms. The scripts there cannot run from that path and will raise `ImportError`; that is deliberate |

The `2` suffix is history and not a version number. Renaming would touch frozen
code, so the naming is documented rather than fixed.

## Running it

Eleven test files and seven experiments. **Run exactly these**; do not use a
glob, because a glob picks up anything left on disk by an earlier extraction.

    python3 --version    # 3.12.3 produced results/; see results/PROVENANCE.md

    for t in tests/test_account.py tests/test_algebra.py \
             tests/test_authority.py tests/test_counterexamples.py \
             tests/test_execution_debit.py tests/test_lifecycle_fuzz.py \
             tests/test_liquidation.py tests/test_recovery.py \
             tests/test_repricing.py tests/test_worst_fill.py \
             tests/test_worst_fill_exhaustive.py; do
      echo "### $t"; python3 "$t"; echo "exit=$?"
    done

    for e in experiments/e1_equity_safety.py \
             experiments/e2_naive_netting_negative.py \
             experiments/e3_hot_path_benchmark.py \
             experiments/e4_recovery.py \
             experiments/e5_flawed_equity_negative.py \
             experiments/e6_liquidation_delay.py \
             experiments/e7_operational_faults.py; do
      echo "### $e"; python3 "$e"; echo "exit=$?"
    done

Every file exits 0. Running the experiments rewrites `results/`; six of the seven
files are byte-identical on any machine because the simulator is integer-only and
seeded, and `results/e3_hot_path.json` is wall-clock timing and will differ.
Restore it with `git checkout -- results/` if you did not intend to re-record.

No dependencies beyond the standard library.

## What each piece is evidence for

| | |
|---|---|
| `test_algebra` | the two algebraic properties the decomposition rests on |
| `test_counterexamples` | 16 lifecycle counterexamples, c1–c16 |
| `test_worst_fill`, `test_worst_fill_exhaustive` | envelopes over order state; closed form against enumeration of all 2ⁿ fill subsets |
| `test_repricing` | where gross is measured, and the scope of the tightness claim |
| `test_authority` | the ordering point's lease binding, including the cross-account attack an external review demonstrated |
| `test_execution_debit` | price band, fee cap, fill identity and the cross-generation debit baseline |
| `test_account`, `test_recovery`, `test_liquidation`, `test_lifecycle_fuzz` | ledger identity, crash recovery, the liquidation and settlement path, and a fuzz over 10,060 admissions |
| E1 | worst-fill requirement against equity at every scenario, with a **binding** trial |
| E2, E5 | negative controls: netting live orders; ceilings solved against a misreported equity |
| E3 | incremental admission against a full scan computing identical envelopes |
| E4 | 3,642 injected crashes; snapshot plus replay against a rebuild from the whole log |
| E6, E7 | liquidation delay decomposed exactly, and eleven faults injected into that path |

## Determinism

All arithmetic is integer; divisions producing a requirement round up. Nothing
reads wall-clock time except E3, and randomness comes only from a seeded
`random.Random`. A given `(seed, config)` reproduces the same counters.

## Documents

    paper/01..09          the whitepaper body
    paper/A_..C_          appendices: decision history, target deployment, protocols
    paper/diagrams.md     Figures 1-4; Figure 5 is in Appendix B
    REPRODUCE.md          recorded output per round, and the claims withdrawn
    results/PROVENANCE.md machine, OS, Python, date and commit for the current results
    DELIVERY.md           what changed since the previous package, including deletions
