# 4. Data and storage design

## 4.1 Engine per store, chosen from the access pattern

| Store | Access pattern | Engine | Why not the obvious alternative |
|---|---|---|---|
| Order books | Mutate the top ticks at 100k/s; cancel by ID O(1) | In-memory price-level array, FIFO per level, index by order ID | A tree of queues costs a cache miss per hop; at 100 µs the misses are the budget |
| Scenario vectors and gross totals | Update per order, per (account, gateway) | Flat array, ≈ 128 B per pair, ≈ 64 MB resident | A revaluation per order is pointer-chasing at the top of book |
| Account positions and equity | Read per issuance; written on every fill | In-memory in the allocator shard, snapshotted | A row store serialises the hot accounts, which are the market makers |
| Replicated log; journal and ledger entries | Sequential append at ≈ 21 MB/s (§2.7); read on replay | Append-only segments on NVMe | Nothing is updated in place |
| Balances | Fold of the journal, materialised | In-memory hash, snapshotted with the journal offset | Balances as source of truth is the anti-pattern in the running case, Part 3 §7 |
| Historical balance-at-time-T | Rare, analytical, wide scans | Columnar store fed by change data capture | This would put an analytical workload behind the trading lock |
| Scenario grid, add-on parameters, gateway weights, band and fee policy, credit version, authority bindings | Read on every derivation and every replay | Versioned data on the log, activated at a sequence | As configuration they make replay non-deterministic: a replayed decision would use today's values |

The last row is the one specific to this design. Those values look like
configuration an operator would want to tune, and they are not, because a gateway
derives its ceilings and its refusals from them and a replay must reproduce both.

## 4.2 Accounting model

Double-entry, with conservation enforced at write time: every journal entry's
postings sum to zero per asset, so value is moved by code and never created.
Accounts are keyed `(ownerId, assetId, accountType)` over `USER_AVAILABLE`,
`USER_MARGIN_HOLD`, `USER_UNREALISED`, `EXCHANGE_FEE`, `INSURANCE_FUND`,
`EXCHANGE_HOT`, `EXCHANGE_COLD` and `SUSPENSE`.

Amounts are signed 64-bit integers in minimal units with 128-bit intermediates.
No floats: they break determinism, which is what makes replicas agree and replays
reproducible.

## 4.3 Leases, holds, and what actually follows

**A lease is an authorisation. A hold is a posting.** A lease never appears in
the ledger: it is capacity to create holds, issued by the allocator, consumed by
a gateway, ending with its term. A hold is a posting moving collateral from
`USER_AVAILABLE` to `USER_MARGIN_HOLD` when a position opens. Conflating the two
words is how an authorisation becomes spendable, so the document and the code
keep them apart.

An earlier version chained them into
`sum holds <= sum consumed leases <= sum issued leases <= collateral`. That is
withdrawn and does not hold: orders are checked against absolute worst-fill
envelopes rather than each consuming an additive quantity, so "consumed leases"
is not a sum, and a scenario requirement, a gross notional and an execution cost
are not commensurable and do not compose into a ledger amount.

What does hold is three facts that meet at the account:

1. **Every posting sums to zero per asset**, enforced at write time, so the
   journal cannot create value.
2. **An account's equity is a cash-flow fold of the authoritative log** —
   collateral plus cash and mark-to-market per symbol, less fees — and is
   rebuildable from it independently of any live component (a14).
3. **The admission theorem of §2.4** guarantees that the account's requirement
   does not exceed *that* equity after any move the scenario grid covers.

They do not compose into a proof that venue assets exceed liabilities: that would
additionally need the ledger implemented, the insurance fund sized, and moves
outside the grid accounted for.

## 4.4 The order lifecycle as journal entries

| Event | Postings |
|---|---|
| Order admitted | None. Admission consumes envelope, which is not money |
| Position opened by a fill | `USER_AVAILABLE -x` / `USER_MARGIN_HOLD +x`; fee to `EXCHANGE_FEE` |
| Mark-to-market | `USER_UNREALISED` adjusted against the counterparty; sums to zero across both plus fee |
| Position reduced | `USER_MARGIN_HOLD -x` / `USER_AVAILABLE +x` |
| Liquidation basket | Same as a reduction. The transfer's counterparty is the venue (§5.4), and any shortfall draws `INSURANCE_FUND` |
| Withdrawal requested | `USER_AVAILABLE -x` / `SUSPENSE +x`, after the sequence in §6.2 |
| Withdrawal confirmed | `SUSPENSE -x` / `EXCHANGE_HOT -x` |

The first row is the one that matters: admission moves nothing, which is what
lets it run at gateway speed without touching the ledger, and why the envelope
bound has to be sound — it is the only thing between an admitted order and an
over-committed balance.

A withdrawal reduces equity, so capacity outstanding against the old figure has
to stop before funds leave: **fence, reconcile, re-issue against the reduced
equity, release**. Bumping the generation is not sufficient, because a
partitioned gateway keeps admitting inside its term regardless. §6.2 gives the
slower alternative that waits for the term instead.

## 4.5 Write path, idempotency and reconciliation

Entry IDs are derived from the upstream event — a hash of the trade sequence and
leg index — so a replayed trade produces the same ID and deduplicates.
Assert-then-apply is atomic over the accounts touched; the ledger is a
single-writer partition per asset class; negative balance is a write-time
assertion, not a downstream check.

Reconciliation runs continuously: balances recomputed from the journal against
the materialised view; sequence-range checks for gaps and duplicates per trade
sequence; hot and cold mirrors against actual chain balances; and every account
rebuilt from the log against the live ledger, which is the check that catches
divergence between the two folds of §4.3.

## 4.6 What is designed here and not built

The ledger module is specified here and not implemented in the simulator, which
models the envelope and account sides only. The three facts of §4.3 are
established for the account; the venue-level solvency statement is an argument in
this document and not a property anything checks.
