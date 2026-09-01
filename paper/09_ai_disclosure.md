# 9. AI-disclosure appendix

## 9.1 How AI was used

An AI assistant was used throughout as a co-author: proposing mechanism, writing
the simulator, deriving the Fermi estimates, and drafting these sections. Every
experiment was executed rather than described, and the outputs quoted in
`REPRODUCE.md` are the outputs those runs produced — Python 3.12.3 on Linux in
the assistant's environment and 3.13.5 on macOS locally, identical except for
E3's wall-clock figures, because the simulator is integer-only and seeded.

The material below is what the rubric asks for: what was rejected, and why.

## 9.2 Prompts that mattered

**"Verify before building on it."** Applied to citations and to the assistant's
own characterisation of the design's risks. It surfaced that the risk the
assistant named first was not the real one (9.3, item 1).

**"Comments say what the code does, not what it is supposed to prove."** Applied
to the simulator and every experiment script, which describe the configuration
they run and print the counters they recorded.

**"Does the constraint actually bind?"** Asked of the first E1 run, which
reported zero violations because the budget was far above what the flow consumed.
A zero from a slack constraint says nothing. E1 now carries a **binding trial**
where the risk and debit envelopes reach 99% and the requirement is 49% of
equity; only that zero is meaningful.

**"Attack your own interface."** External hostile review was run each round
against the code rather than the prose. It produced the authority-binding
failure (9.3, item 6), among others.

## 9.3 Suggestions and claims rejected, and why

1. **That tiered maintenance margin breaks the decomposition.** Proposed as the
   leading risk. It is not: tiers are per contract and additive. What actually
   breaks decomposition is the portfolio-level add-on, which is super-additive.
2. **Abstracting the requirement into a general coherent-risk-measure
   formulation.** Rejected: §2.3 rests on exchanging a maximum with a sum, and
   burying that step inside a general functional makes the paper harder to
   defend, not stronger.
3. **Charging an order its marginal requirement.** A leg flipped from short to
   long leaves the increment unchanged while the account's requirement moves to
   its maximum (c1). Replaced by absolute worst-fill envelopes.
4. **A price-conditional schedule as a safety and capacity mechanism.** Withdrawn
   with its evidence (ADR-2). A lease cannot remove a position it already
   admitted.
5. **Letting a gateway accept locally risk-reducing orders under partition.**
   Withdrawn: c9. Risk reduction is an account-level operation.
6. **Treating a lease id as authority.** Review demonstrated a valid lease id
   submitted under a different account returning success. Replaced by the
   registry and session binding of Appendix C.3.
7. **Reading the factor of two as a 64% tolerance for a misreported account.**
   Measured in a non-binding configuration. At the binding point the breach
   tracks the overstatement one for one (E5 Part B).
8. **Expiry releasing exposure**, and **a holder's own report releasing it.**
   Both replaced by seals and the account barrier (c8, c11, c12).

## 9.4 What the numbers are, and are not

- **Current evidence:** E1–E7, and eleven test files — 16 lifecycle
  counterexamples, 6 worst-fill cases plus an exhaustive closed-form comparison
  over 4,000 books, 6 recovery cases, 14 liquidation cases, 6 repricing cases, 6
  authority cases, 7 execution-cost cases, 14 ledger cases, and a lifecycle fuzz
  over 10,060 admissions.
- E3's absolute latencies support a **scaling** claim, not NFR row 2's target.
- E6's required-buffer figures are one configuration, one seed, one price path.
- The ledger of §4 is designed and not implemented; §4.3 establishes three facts
  about the account, not venue-level solvency.
- The replay rate of §5.5 is assumed, not measured (§8.1).
- §6.3 A3 is argued, not measured.
- Replication and allocator failover are designed and not built (Appendix B).
- Superseded results — E4's schedule comparison, E5's suppression sweep — are in
  `experiments/superseded/` and are cited nowhere as current.

## 9.5 Section ownership

**TODO — names to be filled in before submission.** Every member presents their
own sections at the defence and the panel may ask any member any question.

| § | Section | Owner |
|---|---|---|
| 1 | Business context and requirements | TODO |
| 2 | Architecture | TODO |
| 3 | Consistency map | TODO |
| 4 | Data and storage design | TODO |
| 5 | Failure and recovery | TODO |
| 6 | Security and threat model | TODO |
| 7 | Trade-offs and alternatives | TODO |
| 8 | Operations | TODO |
| 9 | This appendix, and appendices A–C | TODO |

| Module | Owner |
|---|---|
| Risk decomposition, allocator, envelope solve | TODO |
| Ordering point, authority binding, fencing and seals | TODO |
| Gateway, worst-fill envelopes, recovery | TODO |
| Ledger, liquidation, settlement barrier, evaluation harness | TODO |
