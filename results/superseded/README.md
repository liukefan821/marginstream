# Superseded results

These five files were produced by interfaces the mechanism has moved off. The
scripts that produced them are in `experiments/superseded/`, which cannot be run
from that path — they import `marginstream` relative to `experiments/` and fail
with an `ImportError`. That is deliberate: run against today's code they would
not reproduce these numbers, they would produce vacuous ones. The
worst-fill sweep, for example, now admits nothing at all, because the allocator
it constructs has no ordering point to register its leases with, so every
submission is refused and the oracle passes on an empty sample.

**Nothing here is cited as current evidence anywhere in `paper/`.** They are kept
because `REPRODUCE.md` records their output as part of the design history.

| File | Produced by | At commit | Dated |
|---|---|---|---|
| `e1_safety.json` | `experiments/superseded/e1_safety.py` | `de485d0` | 2026-08-30 |
| `e1_worst_fill.json` | `experiments/superseded/e1_worst_fill_safety.py` | `16cd668` | 2026-08-31 |
| `e2_negative.json` | `experiments/superseded/e2_negative.py` | `de485d0` | 2026-08-30 |
| `e4_conditional.json` | `experiments/superseded/e4_conditional.py` | `18bd54c` | 2026-08-30 |
| `e5_adversarial.json` | `experiments/superseded/e5_adversarial.py` | `18bd54c` | 2026-08-30 |

`e4_conditional.json` and `e5_adversarial.json` are the figures ADR-2 withdraws.
`e1_worst_fill.json` predates the authority binding by five rounds.
