# Superseded experiments

These target interfaces the mechanism has moved off. They are kept because
`REPRODUCE.md` records their output as part of the history of the design, not
because they still run: `e1_worst_fill_safety.py` calls `Sequencer.record_fill`
with the signature it had before the execution-policy round, and the others use
the pre-worst-fill `Shard` and `Allocator`.

Nothing here is claimed as a current result. The current set is `e1` through
`e7` in the parent directory.
