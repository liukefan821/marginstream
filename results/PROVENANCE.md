# Provenance of the current results

Every file in this directory was produced by one run of one script, all seven on
the same machine in the same session. Reproducing them needs the same machine
for the timing figures and only the same code for everything else: the simulator
is integer-only and seeded, so the six non-timing files are byte-identical on any
machine running the same commit.

    commit    09b715d
    python    3.12.3
    os        Linux x86_64
    recorded  2026-09-01 19:49 UTC

| File | Script | What it records |
|---|---|---|
| `e1_equity.json` | `experiments/e1_equity_safety.py` | worst-fill requirement against equity at every scenario, plus a binding trial |
| `e2_naive_netting.json` | `experiments/e2_naive_netting_negative.py` | negative control: netting live orders instead of taking the worst fill |
| `e3_hot_path.json` | `experiments/e3_hot_path_benchmark.py` | incremental admission against a full scan computing identical envelopes |
| `e4_recovery.json` | `experiments/e4_recovery.py` | crash injection; snapshot plus replay against a rebuild from the whole log |
| `e5_flawed_equity.json` | `experiments/e5_flawed_equity_negative.py` | negative control: ceilings solved against a misreported equity |
| `e6_liquidation_delay.json` | `experiments/e6_liquidation_delay.py` | liquidation delay and the insurance-fund draw, decomposed exactly |
| `e7_operational_faults.json` | `experiments/e7_operational_faults.py` | eleven faults injected into the liquidation path |

**`e3_hot_path.json` is the only machine-dependent file here.** Its figures are
wall-clock nanoseconds from CPython on a shared machine and are not comparable
across hosts; `paper/01` §1.7 quotes only the ratios between rows of this file,
never the absolute figures. Runs on other hosts are recorded in `REPRODUCE.md`
under the machine that produced them.

Five older result files are in `superseded/` with the commit that produced each.
