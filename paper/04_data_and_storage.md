# 4. Data and storage design

## 4.1 Engine per store, chosen from the access pattern

| Store | Access pattern | Engine | Why not the obvious alternative |
|---|---|---|---|
| Order books | Top ticks at 100k/s; cancel by ID O(1) | In-memory price-level array, FIFO per level, index by order ID | A tree of queues costs a cache miss per hop; at 100 µs the misses are the budget |
| Scenario vectors and gross totals | Update per order, per (account, gateway) | Flat array, ≈ 64 MB resident | A revaluation per order is pointer-chasing at the top of book |
| Account positions and equity | Read per issuance; written on every fill | In-memory in the allocator shard, snapshotted | A row store serialises the hot accounts, which are the market makers |
| Log, journal and ledger entries | Sequential append at ≈ 21 MB/s (§2.7) | Append-only segments on NVMe | Nothing is updated in place |
| Balances | Fold of the journal, materialised | In-memory hash, snapshotted with the journal offset | Balances as source of truth is the running case's anti-pattern, Part 3 §7 |
| Historical balance-at-time-T | Rare, analytical | Columnar store fed by change data capture | Otherwise an analytical workload sits behind the trading lock |
| Scenario grid, add-on parameters, gateway weights, band and fee policy, credit version, authority bindings | Read on every derivation and replay | Versioned data on the log, activated at a sequence | As configuration they make replay non-deterministic |

The last row is specific to this design: those values look like configuration an
operator would tune, and are not, because a gateway derives its ceilings and its
refusals from them and a replay must reproduce both.

## 4.2 Accounting model

Double-entry, with conservation enforced at write time: every journal entry's
postings sum to zero per asset, so value is moved by code and never created.
Accounts are keyed `(ownerId, assetId, accountType)` over `USER_AVAILABLE`,
`USER_MARGIN_HOLD`, `USER_UNREALISED`, `EXCHANGE_FEE`, `INSURANCE_FUND`,
`EXCHANGE_HOT`, `EXCHANGE_COLD`, `SUSPENSE`, `EXTERNAL_SETTLEMENT`, `VENUE_BOOK` and
`FUNDING_CLEARING`.
`USER_MARGIN_HOLD` is the running case's `USER_HOLD` under cross-margin: there
the hold is per order and released when that order resolves, here it is the
account's single encumbrance and moves with the portfolio requirement.
Everything from `USER_UNREALISED` onward except the wallets and `SUSPENSE` is new
relative to a spot venue: a derivatives venue carries positions it marks every
tick, pays funding between clients, takes positions onto its own book in a
liquidation, and keeps a fund to absorb what an account cannot.

The ledger is the venue's own books. Every posting is a debit or a credit, and an
entry is valid only if its debits equal its credits in each asset. The venue's
wallets, `EXCHANGE_HOT` and `EXCHANGE_COLD`, are assets and normally carry a
debit balance. Every `USER_` account is money the venue owes a client, so it is
a liability with a normal credit balance, and the venue's own accounts,
`EXCHANGE_FEE`, `INSURANCE_FUND` and `VENUE_BOOK`, are credit-normal as well.
`SUSPENSE`, `EXTERNAL_SETTLEMENT` and `FUNDING_CLEARING` are transit accounts that
return to zero. A client's equity is therefore the combined credit balance of
`USER_AVAILABLE`, `USER_MARGIN_HOLD` and `USER_UNREALISED`, which matches the
simulator's collateral plus PnL at marks, less fees. Paying out a withdrawal
lowers a liability, which is a debit, and an asset, which is a credit, so one
entry balances; an earlier version called this "two debits" and split it in two,
which was a sign error rather than a fix.

Amounts are signed 64-bit integers in minimal units with 128-bit intermediates.
No floats: they break determinism, which is what makes replicas agree and replays
reproducible.

## 4.3 Leases, holds, and what actually follows

**A lease is an authorisation. A hold is a posting.** A lease never appears in
the ledger: it is capacity to create holds, issued by the allocator, consumed by
a gateway, ending with its term. A hold moves collateral from `USER_AVAILABLE` to
`USER_MARGIN_HOLD` when a position opens. Conflating the two is how an
authorisation becomes spendable.

An earlier version chained them into
`sum holds <= sum consumed leases <= sum issued leases <= collateral`. That is
withdrawn and does not hold: orders are checked against absolute worst-fill
envelopes rather than each consuming an additive quantity, so "consumed leases"
is not a sum, and a scenario requirement, a gross notional and an execution cost
are not commensurable and do not compose into a ledger amount.

What does hold is three facts that meet at the account: every posting sums to
zero per asset, enforced at write time; an account's equity is a cash-flow fold of
the authoritative log, rebuildable independently of any live component (a14); and
the admission condition of §2.2 keeps the requirement inside *that* equity after any
move the grid covers.

They do not compose into a proof that venue assets exceed liabilities: that needs
the ledger implemented, the insurance fund sized against moves outside the grid
(§5.4 argues a size from one stress run; it is not a calibration), and the venue
book's own risk bounded.

## 4.4 The order lifecycle as journal entries

Each row is one balanced entry, written Dr (debit) / Cr (credit).

| Event | Entry |
|---|---|
| Deposit confirmed | Dr `EXCHANGE_HOT` x / Cr `USER_AVAILABLE` x |
| Order admitted | None. Admission consumes a budget, which is not money |
| Fill | Dr `USER_AVAILABLE` / Cr `USER_MARGIN_HOLD` for the rise in requirement; fee Dr `USER_AVAILABLE` f / Cr `EXCHANGE_FEE` f |
| Mark-to-market | Dr loser's `USER_UNREALISED` u / Cr winner's `USER_UNREALISED` u; `VENUE_BOOK` takes the same entries for positions it holds |
| Position reduced | A gain moves from unrealised to available, Dr `USER_UNREALISED` / Cr `USER_AVAILABLE` (reversed for a loss), so it is never counted twice; released margin Dr `USER_MARGIN_HOLD` / Cr `USER_AVAILABLE` |
| Funding | Payer Dr `USER_AVAILABLE` p / Cr `FUNDING_CLEARING` p; receiver Dr `FUNDING_CLEARING` p / Cr `USER_AVAILABLE` p. The clearing account ends each interval at zero, so the venue is not a party |
| Liquidation basket | One entry per basket: unrealised PnL is realised into `USER_AVAILABLE` and the position passes to `VENUE_BOOK` at the basket price. A negative ending equity is covered Dr `INSURANCE_FUND` d / Cr `USER_AVAILABLE` d, bringing the account to zero |
| ADL | An opposing profitable position is closed at the bankruptcy price instead of the mark: Dr its `USER_UNREALISED` a / Cr the defaulted account's `USER_AVAILABLE` a, one entry per event |
| Withdrawal requested | Dr `USER_AVAILABLE` x / Cr `SUSPENSE` x, after the sequence in §6.2 |
| Withdrawal sent | Dr `EXTERNAL_SETTLEMENT` x / Cr `EXCHANGE_HOT` x |
| Withdrawal confirmed | Dr `SUSPENSE` x / Cr `EXTERNAL_SETTLEMENT` x. If the chain rejects the transfer, the sent entry is reversed |

The funding interval, the rate formula and the ADL ranking are venue policy and
are not specified here.

The order-admitted row is the one that matters: admission moves nothing, which is what
lets it run at gateway speed, and why the envelope bound has to be sound — it is
the only thing between an admitted order and an over-committed balance.

A withdrawal reduces equity, so capacity outstanding against the old figure has
to stop before funds leave: **fence, reconcile, re-issue against the reduced
equity, release**. Bumping the generation is not sufficient — a partitioned
gateway keeps admitting inside its term regardless. §6.2 gives the slower
alternative.

## 4.5 Write path, idempotency and reconciliation

Entry IDs are a hash of the trade sequence and leg index, so a replayed trade
deduplicates. Assert-then-apply is atomic over the accounts touched; the ledger
is a single-writer partition per asset class. A client-initiated entry, such as
a withdrawal, may not take `USER_AVAILABLE` below zero, and that is asserted at
write time. Entries the venue posts, such as marks, funding and liquidation, may
leave equity negative; that is the shortfall the liquidation entry covers from
the insurance fund. Reconciliation runs continuously: balances recomputed from the journal
against the materialised view, sequence-range checks for gaps and duplicates, hot
and cold mirrors against chain balances, and every account rebuilt from the log
against the live ledger — which catches divergence between §4.3's two folds.

The ledger module is specified here and not implemented in the simulator, which
models the envelope and account sides only: §4.3's three facts are established
for the account, and NFR row 11's venue-level statement is an argument, not a
checked property.
