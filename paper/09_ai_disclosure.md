# 9. AI-disclosure appendix

## 9.1 How AI was used

An AI assistant was used throughout as a co-author for design and
implementation: proposing the mechanism, writing the simulator, deriving the
Fermi estimates, and drafting these sections. Every experiment in the evidence
appendix was executed rather than described, and the outputs quoted in
`REPRODUCE.md` are the outputs those runs produced, on Python 3.13.5 locally and
3.12.3 in the assistant's environment, byte-identical because the simulator is
integer-only and seeded.

The material below is the part the rubric asks for: what was rejected, and why.

## 9.2 Prompts that mattered

Three shaped the work more than the rest.

**"Verify the citation before building on it."** The assistant's first
description of the safety argument cited a specific paper for the sub-additivity
property from memory. Requiring verification before it went into any document
surfaced that the *citation* was right and the assistant's *characterisation of
the risk* was wrong in an important way — see 9.3, item 1.

**"The comments should say what the code does, not what it is supposed to
prove."** Applied to the simulator and the experiment scripts. The effect is
visible in `experiments/e2_negative.py`, which describes the configuration it
runs and prints the counters it recorded, and does not tell the reader what the
numbers mean.

**"Does the constraint actually bind?"** Asked of the first E1 run, which
reported zero invariant violations. It did so because the budget was so far above
what the order flow consumed that the mechanism never engaged. A zero that comes
from a slack constraint says nothing. The configuration was re-tuned until lease
exhaustion appeared in the counters, and only then was the zero meaningful.

## 9.3 Suggestions rejected, and why

**1. That tiered maintenance margin breaks the decomposition.**
The assistant proposed this as the design's leading risk. Verification showed it
is not: margin tiers are per contract, and each contract lives on one shard, so
tiering is additive across shards and the local check is exact rather than
conservative. The property that actually breaks decomposition is the portfolio
level concentration and liquidity add-on, which is super-additive. Accepting the
first framing would have put a false risk in the paper and hidden the real one.

**2. Abstracting the requirement into a general coherent-risk-measure
formulation.**
Proposed as a way to make the treatment look more rigorous. Rejected: the
argument in §2.3 rests on exchanging a maximum with a sum, and burying that step
inside a general functional makes the paper harder to defend, not stronger. Four
lines of concrete algebra that any member can reproduce at the board beat a page
of notation nobody in the team can be questioned on.

**3. Checking `M(P) <= equity` as a single invariant.**
The first invariant oracle did this and reported 137 violations under a falling
equity path. The violations were real arithmetic and the wrong conclusion: an
equity fall puts existing positions above the requirement with no order having
been admitted, which is a liquidation event and not an admission failure. The
check was split, and a liquidation path added. Had this been discovered after the
paper was drafted rather than during implementation, §5 would have been written
around a mechanism that does not do what it claims.

**4. Fencing by comparing the order's generation with the lease's.**
The obvious rule, implemented first, and wrong: a stale shard and a stale order
agree with each other. Found by the scripted case in E2. The corrected rule is in
§2.5 and §5.4.

**5. Re-running liquidation on every tick that reported the condition.**
Compounded the reduction and drove positions to zero. The condition persists
until the consumption counters are reset, so liquidation is followed by a
generation bump and a re-issue.

**6. Sizing the whole project around a scalar lease.**
The mechanism in the first version of the proposal used a single amount per
epoch. E4 shows it above equity 236 ticks out of 480. The schedule of §2.4 was
added in response to a measurement, not to a preference.

## 9.4 What the numbers in this paper are, and are not

- E1, E2, E4 and E5 were run; the tables are their output.
- The simulator uses a four-state schedule where §1.5 derives sixteen, so the
  measured exposures are upper bounds rather than estimates.
- The ledger of §4 is designed and not implemented; the solvency chain of §4.3 is
  an argument in this document, not a property the simulator checks.
- The replay rate of §5.5 is assumed, not measured. It is the first item in §8.
- §6.3 A3 is argued, not measured.

## 9.5 Section ownership

| § | Section | Owner |
|---|---|---|
| 1 | Business context and requirements | |
| 2 | Architecture | |
| 3 | Consistency map | |
| 4 | Data and storage design | |
| 5 | Failure and recovery | |
| 6 | Security and threat model | |
| 7 | Trade-offs and alternatives | |
| 8 | Operations | |
| 9 | This appendix | |

Module ownership in the accompanying repository:

| Module | Owner |
|---|---|
| Sharded matching stub and replicated log | |
| Margin allocator, risk decomposition, conditional leases, fencing | |
| Ledger, hold-versus-lease separation, idempotency chain | |
| Fault injection, liquidation, evaluation harness | |

*Names to be filled in before submission. Every member presents their own
sections at the defence, and the panel may ask any member any question.*
