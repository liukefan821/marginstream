# Executive summary

MarginStream is a futures venue, with 40 underlyings and up to 20× leverage, where
one deposit backs all of a client's positions, so a client long one contract and
short a correlated one pays margin only on the net risk. That capital efficiency
is the product, but it is hard to deliver: "margin must not exceed equity"
applies to the whole account, while order books are split by symbol and run in
parallel. Each order must be accepted or refused in microseconds, yet no single
book sees the whole account, and checking each book separately is unsafe — in
our simulator, an account with 2,000 of collateral reached a requirement of
201,000 (E2).

Our solution moves the account-level calculation off the order path. A margin
allocator gives each gateway a budget per account every 50–200 ms, and the
gateway checks orders against it locally. The budgets are sized so that, even if
all are used in full and the market makes the worst move our model covers, the
account can still meet its margin, while a single ordering point records every
accepted order and can revoke any gateway's authority at once. When things fail,
the system stops rather than guesses. If the allocator is unreachable, gateways
trade within their current budgets until they expire, then stop. If the market
moves beyond the model, the venue takes over the account's positions in atomic
basket transfers, and any shortfall falls to the insurance fund and, as a last
resort, auto-deleveraging. After a crash, state is rebuilt by replaying one
ordered log; 3,642 injected crashes all recovered exactly (E4).

This safety has two costs. First, budgets keep the account solvent after the
worst covered move without liquidation, which roughly doubles the margin held,
much as initial margin is about twice maintenance margin; a central checker with
the same guarantee would pay this too. Second, a hedge split across two gateways
cannot offset within either budget, which is the true cost of distribution. In
our tightest test (E1), budgets were 99% used and the requirement reached 49% of
equity, with no breach. We built and tested a deterministic simulator of
admission, recovery and liquidation, with 11 test suites and 9 experiments
including a flash crash (E9); replication, the production ledger, the
mark-price pipeline and the insurance-fund waterfall are designed but not built,
as §5.7 sets out.
