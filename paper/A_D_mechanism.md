# Appendix D — The mechanism, formally

§2.2–§2.3 state the mechanism in words. This appendix carries the definitions,
the three lemmas, the worst-fill closed forms and the closure argument with its
tightness, for a reader checking the claim rather than following the design. The
tests named here are the evidence; the prose is the argument.

## D.1 What can be divided, and what cannot

Write the account's requirement as a scenario term plus an add-on. Two gross
figures are needed and they are not the same object:

$$
R(P)=\max_{k\in S}\operatorname{loss}_k(P),
\qquad
G_k(P)=\sum_s |q_s|\,m_s(k),
\qquad
G^{+}(P)=\sum_s |q_s|\max_{j\in S} m_s(j)
$$

$$
M_k(P)=R(P)+A\bigl(G_k(P)\bigr),
\qquad A \text{ convex, non-decreasing, } A(0)=0
$$

$G_k$ is what the requirement is computed from; $G^{+}$ is what a lease reserves
against, because the marks move during a term (D.2). $G_k(P)\le G^{+}(P)$ for every
`k` by construction. The code keeps them apart as `gross` and `gross_reach`.

$R$ is the worst loss across a fixed scenario set $S$, and the loss under any
single scenario is linear in positions. `A` is a concentration and liquidity
add-on.

**Model boundary.** The algebra below holds for any finite $S$. The set used in
every correctness experiment here is **seven points on a single factor with
non-negative loadings**; E3 additionally times a 16-point grid. Nothing here shows
that a single-factor grid is adequate for 40 underlyings — basis and
idiosyncratic risk would need more factors or a wider set, and the evidence for
*that* choice is not in this document. What is shown is that the decomposition,
the closure and the lifecycle are correct for whatever finite $S$ is picked.

**The partition is by gateway, not by symbol.** Two gateways can hold opposite
positions in the *same* symbol, and those net inside the account, so gross is not
additive across the partition.

**Lemma 1 — R is sub-additive.**
$R(P)=\max_k\sum_g \operatorname{loss}_k(P_g)\le\sum_g\max_k \operatorname{loss}_k(P_g)=\sum_g R(P_g)$: a
single scenario cannot beat the per-gateway worst cases taken separately.

**Lemma 2 — gross is sub-additive.** Per symbol,
$\bigl|\sum_g q_{g,s}\bigr|\le\sum_g |q_{g,s}|$ by the triangle inequality; multiplying by a
positive mark and summing gives $G_k\bigl(\sum_g P_g\bigr)\le\sum_g G_k(P_g)$ for every `k`,
and the same for $G^{+}$, with equality only when every gateway holds the same sign
in every symbol.

**Lemma 3 — A does not decompose.** A convex function through the origin is
super-additive on non-negative arguments, so $\sum_g A(G_g)\le A\bigl(\sum_g G_g\bigr)$.
Per-gateway add-on allowances added up under-state the whole, however the
positions are split.

The decomposition rule follows rather than being chosen:

> The sub-additive parts divide into per-gateway ceilings checked locally. The
> add-on does not; it is evaluated once, centrally, on the summed gross, and `A`
> being non-decreasing is what makes that an upper bound.

Chained, for the realised scenario `k`:

$$
G_k(P')\;\le\;G^{+}(P')\;\le\;\sum_g G^{+}(P'_g)\;\le\;\sum_g \lambda_g^{G}
$$

the first step by construction, the second by Lemma 2, the third by the admission
rule. $A$ non-decreasing then carries it to
$A(G_k(P'))\le A(\sum_g \lambda_g^{G})$. Nothing in that chain needs gross to be additive.
Lemmas 1 and 3 are checked over 2,000 sampled portfolios in
`tests/test_algebra.py`, in the predicted directions.

## D.2 The envelopes and the closure

A lease cannot undo an admission it has already granted. Everything here follows
from that.

### Three envelopes

| Envelope | What it bounds | Why separate |
|---|---|---|
| $\lambda_g^{R}$ | worst-fill scenario requirement of everything the gateway holds | sub-additive, so it divides |
| $\lambda_g^{G}$ | worst-fill $G^{+}$ the gateway can reach | an order can lower `R` while raising gross |
| $\lambda_g^{D}$ | execution cost the gateway can still incur, plus cost already incurred that the equity the current lease was solved against does not yet reflect | its effect on equity is covered by neither the scenario requirement nor the gross add-on |

The three are not three allocations: $\lambda^{G}$ and $\lambda^{D}$ are issued at fixed ratios
to $\lambda^{R}$, so the solver searches one scalar along a ray through a
three-dimensional feasible set. Dropping the second half of $\lambda^{D}$ is what made
capacity decay every term in an earlier implementation (d7).

### What a gateway holds is orders, not positions

Two resting orders of opposite sign net to nothing, and if only one fills the
account carries the other side. The envelopes are taken over the worst subset of
fills that could still occur, which needs no enumeration because the loss under a
fixed scenario is linear:

$$
E_k=\operatorname{loss}_k(f)+\sum_i \max\bigl(0,\operatorname{loss}_k(o_i)\bigr),
\qquad
R_{\mathrm{wf}}=\max\Bigl(0,\Bigl\lceil \tfrac{1}{D}\max_k E_k \Bigr\rceil\Bigr)
$$

$$
G_{\mathrm{wf}}=\sum_s \Bigl(\max_{j} m_s(j)\Bigr)\,
\max\bigl(|f_s+b_s|,\;|f_s-s_s|\bigr)
$$

with $f$ the filled position, $o_i$ the live orders, and $b_s,s_s$ the unfilled
quantity on each side.

Both closed forms agree with enumeration of all 2^n fill subsets on 4,000 random
books (`test_worst_fill_exhaustive`). Admission compares these **absolute**
figures against the ceilings, not the increment an order adds: a leg flipped from
short to long leaves the increment unchanged while the account's requirement moves
to its maximum (c1).

### Where gross is measured

A lease reserves against $G^{+}$, not against the gross standing when the solve ran.
A short position's adverse scenario raises the mark, raises gross and raises the
add-on, while a figure measured at the issuance mark does not move. Reserving at
the issuance mark admits 296 lots in the worked case of m1 and finishes 382,143
above equity; reserving at $G^{+}$ admits 249 and finishes 5,842 inside. ADR-3 gives
the cost and the scope of the tightness claim.

### The condition and its closure

$$
2\sum_g \lambda_g^{R}\;+\;A\Bigl(\sum_g \lambda_g^{G}\Bigr)\;+\;\sum_g \lambda_g^{D}
\;\;\le\;\; E_0
$$

with $E_0=\text{Collateral}+\sum_s (c_s+q_s m_s)-\text{fees}$, mark-to-market
equity rather than collateral. There is no market state in it.

Write $P'$ for the position the term ends with, `D` for the execution cost it
incurred, `k` for the realised scenario. Three bounds come from the admission
rule and the fourth because `R` is a maximum over a set containing `k`:

$$
R(P')\le\sum_g \lambda_g^{R},
\quad
G_k(P')\le\sum_g \lambda_g^{G},
\quad
D\le\sum_g \lambda_g^{D},
\quad
\operatorname{loss}_k(P')\le R(P')
$$

Adding requirement, cost and realised loss:

$$
M_k(P')+D+\operatorname{loss}_k(P')
\;\le\;
2\sum_g \lambda_g^{R}+A\Bigl(\sum_g \lambda_g^{G}\Bigr)+\sum_g \lambda_g^{D}
\;\le\; E_0
$$

and since `Equity_after(k) = E_0 - D - loss_k(P')`,

$$
M_k(P')\;\le\;E_0-D-\operatorname{loss}_k(P')\;=\;\mathrm{Equity}_{\text{after}}(k)
$$

**Subtracting `D` is why the third resource exists.** A conclusion of
$M\le E_0-\text{loss}$ would leave execution cost out of the arithmetic while still
listing $\lambda^{D}$ as a ceiling.

The factor of two is a closure, not a margin, and it is tight. Set `A = D = 0`
and take $R(P')=\lambda^R$ with the realised scenario attaining the maximum, so
$\operatorname{loss}_k=R$. Then $M_k=\lambda^R$ and $\mathrm{Equity}_{\text{after}}=E_0-\lambda^R$, equal precisely
when $E_0=2\lambda^R$. With $c<2$ the solve issues $\lambda^R=E_0/c$, and the same
position ends with $M_k=E_0/c>E_0-E_0/c$. The coefficient cannot be reduced.

### What the closure costs

Utilisation is capped near half of equity before the add-on reserve. In E1's
binding trial — every order filled at the worst price and fee the policy allows —
the risk and debit envelopes reach 99% and the requirement is 49% of equity, with
no breach. The offset decomposition gives up is separately D.1's sub-additivity
gap (§7). The multi-factor case is E8.

