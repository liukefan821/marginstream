# 4. Data and storage design

## 4.1 Engine per store, chosen from the access pattern

| Store | Access pattern | Engine | Why not the obvious alternative |
|---|---|---|---|
| Order books | Read and mutate the top few ticks at 100k/s; cancel by ID must be O(1) | In-memory: contiguous price-level array keyed by `(price - base) / tick`, FIFO list per level, open-addressing index by order ID | A tree of queues costs a cache miss per hop; at a 100 µs budget the misses are the budget. Running case, Part 2 §2 |
| Per-scenario loss vectors | Read and update 16 int64 per order, per (account, shard) | In-memory flat array, 128 B per pair, ≈ 64 MB resident | A portfolio revaluation call per order is the margin-path equivalent of pointer-chasing at the top of book |
| Account positions and collateral | Read once per epoch per account by the allocator; written on every fill | In-memory in the allocator shard, snapshotted | A row store would serialise the hot accounts, which are exactly the market makers |
| Replicated log | Sequential append at ≈ 21 MB/s, read only on replay | Append-only segments on NVMe | Nothing here is ever updated in place, which is the LSM-friendly case |
| Journal and ledger entries | Sequential append; range scans on replay and reconciliation | Append-only segments, retained for years | Same argument |
| Balances | Fold of the journal, materialised, read on every risk check | In-memory hash, snapshotted with the journal offset | Balances as the source of truth is the anti-pattern in the running case, Part 3 §7 |
| Historical queries — balance of account at time T | Rare, analytical, wide scans | Columnar store fed from the journal by change data capture | Serving these from the hot path would put an analytical workload behind the same lock as trading |
| Schedule shape tables and state bandings | Read on every derivation and on every replay | Versioned data on the log itself (§5.6) | Configuration would make replay non-deterministic, since a replayed decision would use today's table |

The last row is the one specific to this design and is easy to get wrong. The
shape table looks like configuration — it is a small array of multipliers an
operator would want to tune — and it is not, because a shard derives its lease
from it and a replay must reproduce that lease.

## 4.2 Accounting model

Double-entry, with the conservation invariant enforced at write time: every
journal entry's postings sum to zero per asset, so value is moved by code and
never created.

Account types for a margin venue:

    Account := (ownerId, assetId, accountType)

    USER_AVAILABLE     collateral the client can withdraw or use
    USER_MARGIN_HOLD   collateral encumbered by open positions
    USER_UNREALISED    mark-to-market on open positions, per settlement cycle
    EXCHANGE_FEE       fee revenue
    INSURANCE_FUND     venue-level buffer for liquidation shortfall
    EXCHANGE_HOT       on-chain hot wallet mirror
    EXCHANGE_COLD      cold storage mirror
    SUSPENSE           in-flight deposits and withdrawals

Amounts are signed 64-bit integers in minimal units, with 128-bit intermediates.
No floats anywhere, for the reason the running case gives: they break
determinism, and determinism is what makes replicas agree and replays
reproducible.

## 4.3 The distinction the whole design rests on

**A lease is an authorisation. A hold is a posting.**

A lease never appears in the ledger. It is capacity to create holds, issued by
the allocator, consumed by a gateway, and expiring with its epoch. A hold is a
double-entry posting that moves collateral from `USER_AVAILABLE` to
`USER_MARGIN_HOLD` when a position is opened.

The relationship between them is the solvency argument:

    sum of holds  <=  sum of consumed leases  <=  sum of issued leases  <=  collateral

The first inequality holds because a hold is only created for an admitted order
and the admitted order consumed at least its own requirement. The second is
arithmetic. The third is the schedule condition of §2.4. So
`sum of user holds <= sum of collateral` holds by construction, not by
reconciliation — which is the property §6.4 offers a supervisor.

Conflating the two words is how an authorisation becomes spendable, so the
document keeps them separate throughout and the code keeps them in separate
modules.

## 4.4 The order lifecycle as journal entries

| Event | Postings |
|---|---|
| Order admitted | None. Admission consumes a lease, which is not money |
| Position opened by a fill | `USER_AVAILABLE -x` / `USER_MARGIN_HOLD +x` for the initial requirement; fee posted to `EXCHANGE_FEE` |
| Mark-to-market settlement | `USER_UNREALISED` adjusted against the counterparty side; sums to zero across the two sides plus fee |
| Position reduced | `USER_MARGIN_HOLD -x` / `USER_AVAILABLE +x` for the released requirement |
| Liquidation | Same as a reduction, plus any shortfall drawn from `INSURANCE_FUND` |
| Withdrawal requested | `USER_AVAILABLE -x` / `SUSPENSE +x`, after the generation bump of §6.2 |
| Withdrawal confirmed | `SUSPENSE -x` / `EXCHANGE_HOT -x` |

The row worth noticing is the first. Admission moves nothing. That is what
allows admission to run at gateway speed without touching the ledger, and it is
also why the lease bound has to be sound: it is the only thing standing between
an admitted order and an over-committed balance.

## 4.5 Write path and idempotency

Entry IDs are derived deterministically from the upstream event — a hash of the
trade sequence and the leg index — so a replayed trade produces the same entry
ID and is deduplicated. Assert-then-apply is atomic with respect to the account
set touched, and the ledger is itself a single-writer partition per asset class,
the same pattern as the matching engine.

Negative balance is a write-time assertion, not a downstream check.

## 4.6 Reconciliation

Continuous, because software has bugs and operators have UPDATE statements:

1. Recompute balances from the journal nightly, diff against the materialised
   view, page on any non-zero difference.
2. Every trade sequence has exactly the expected number of ledger entries; gaps
   and duplicates are detected by sequence-range checks.
3. `EXCHANGE_HOT` and `EXCHANGE_COLD` mirrors against actual chain balances per
   asset.
4. Sum of holds against sum of consumed leases (§4.3). This one is specific to
   this design and it is the check that would catch a lease-accounting bug
   before it reached the balance sheet.

## 4.7 What is designed here and not built

The ledger module is specified in this section and is not implemented in the
accompanying simulator, which models the lease side only. The solvency chain of
§4.3 is therefore an argument in this document rather than a property the
simulator checks, and the evidence appendix does not claim otherwise.
