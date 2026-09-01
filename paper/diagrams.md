# Diagrams

Mermaid source. GitHub renders these inline; for the PDF, paste a block into
mermaid.live and export SVG (see `paper/DIAGRAM_EXPORT.md`).

---

## Figure 1 — Component view

Two authorities partitioned on different keys, and one ordering point that both
of them and every recovery path read from. Matching is partitioned by symbol
because price-time priority is a total order per book. The allocator is
partitioned by account because margin is an account-level quantity. Nothing on
the order path crosses either partition.

The liquidator is drawn apart from the gateways on purpose: it is the only holder
whose orders are checked against the merged account rather than against a
ceiling, and its transfers do not touch the order books.

```mermaid
flowchart LR
  subgraph CL[Clients]
    C1[Client A]
    C2[Client B]
  end

  subgraph GW[Ingress admission gateways]
    G1[Gateway 1<br/>three envelopes<br/>worst-fill totals]
    G2[Gateway N<br/>three envelopes<br/>worst-fill totals]
  end

  OP[Ordering point<br/>gap-free seq per lease<br/>band, fee cap, fill identity<br/>fence, seal, barrier]

  subgraph CORE[Matching core - partitioned by symbol]
    M1[Shard 1<br/>single writer<br/>symbols 1..15]
    M2[Shard 8<br/>single writer<br/>symbols 106..120]
  end

  subgraph ALLOC[Margin allocator - partitioned by account]
    A1[Allocator shard 1<br/>solves the condition<br/>issues ceilings]
    A2[Allocator shard 16]
  end

  LQ[Liquidator<br/>merged account view<br/>atomic basket transfer]
  MD[Market data<br/>publishes marks]
  LG[(Authoritative log<br/>admissions, fills, cancels,<br/>fences, baskets, barriers,<br/>lease inputs)]
  LD[Ledger<br/>double entry]

  C1 --> G1
  C2 --> G2
  G1 -- "session, lease_id, seq" --> OP
  G2 -- "session, lease_id, seq" --> OP
  LQ -- "session, liquidation lease" --> OP
  OP --> M1
  OP --> M2
  OP --> LG
  M1 --> LD
  M2 --> LD
  LQ -. transfer, no book .-> LD
  LG -. rebuild order state .-> G1
  LG -. rebuild order state .-> G2
  LG -. occupancy at a barrier .-> A1
  MD -. marks .-> A1
  MD -. marks .-> A2
  A1 -. lease inputs .-> LG
  A2 -. lease inputs .-> LG
  LG -. derive ceilings .-> G1
  LG -. derive ceilings .-> G2
  A1 == "register lease_id to<br/>account, holder, kind" ==> OP
  A1 -. fence .-> OP
```

Solid edges are the order path; dashed edges are asynchronous and carry no
per-order latency. The allocator never reads a gateway: every figure it acts on
comes from the log.

The thick edge is the one this design would be unsound without. A `lease_id` on
its own is a bearer token; the ordering point only knows which account, which
holder and which authority kind a lease belongs to because the allocator — the
single issuer, and therefore the only component that knows — registers the
binding. The holder on each submission is resolved from the authenticated
session, never read from the request body (§6.1, Appendix C.3).

---

## Figure 2 — Data flow for one order

Three envelope checks, all against absolute figures rather than the increment the
order adds, and then a submission the ordering point can refuse for reasons no
gateway is consulted about.

```mermaid
flowchart TD
  O[Order arrives<br/>client order ID, symbol, qty] --> F0{Gateway finished<br/>recovering?}
  F0 -- no --> R0[Refuse: recovering<br/>admits nothing]
  F0 -- yes --> F1{Ceilings present<br/>for this account?}
  F1 -- no --> R1[Refuse: no lease]
  F1 -- yes --> F2{Term still running,<br/>mode not quarantine?}
  F2 -- no --> R2[Refuse: expired<br/>or quarantined]
  F2 -- yes --> F3{Lease generation<br/>below highest seen?}
  F3 -- yes --> R3[Refuse: gateway stale<br/>fail closed]
  F3 -- no --> S1[Update worst-fill totals<br/>one pass over the grid]
  S1 --> C1{R_wf after<br/>&lt;= risk ceiling?}
  C1 -- no --> R4[Refuse: risk envelope]
  C1 -- yes --> C2{G_wf after<br/>&lt;= gross ceiling?}
  C2 -- no --> R5[Refuse: gross envelope]
  C2 -- yes --> C3{debit after<br/>&lt;= debit ceiling?}
  C3 -- no --> R6[Refuse: debit envelope]
  C3 -- yes --> SUB[Submit to ordering point<br/>session, lease_id, next seq]
  SUB --> B1{Lease registered?}
  B1 -- no --> R7[Refuse: unknown_lease]
  B1 -- yes --> B2{Session resolves to the bound<br/>holder? account and<br/>authority kind match?}
  B2 -- no --> R8[Refuse: wrong_holder,<br/>wrong_account,<br/>wrong_authority_kind<br/>or unauthenticated]
  B2 -- yes --> F4{Lease fenced,<br/>or sequence gap?}
  F4 -- yes --> R9[Refuse: nothing recorded,<br/>no state moves]
  F4 -- no --> A1[Recorded with its terms:<br/>mark, band, fee cap]
  A1 --> A2[Commit locally, forward<br/>to the matching shard]
```

The whole per-order margin cost is the one pass at S1: the running totals are
updated per order state change rather than recomputed, so admission costs one
pass over the scenario grid however many orders are live. E3 measures that as
flat in the order count and linear in the grid width.

The boxes after `SUB` are the ordering point's, and they are the only checks here
a compromised gateway cannot influence: they use the registered binding and the
authenticated session rather than anything the request claims. Nothing downstream
re-derives S1 or the three envelope comparisons, which is what §6.1 means by the
gateway being inside the trusted computing base.

---

## Figure 3 — Liquidation and settlement

The path from a shortfall to capacity being returned. Three of the boxes are
where a fault matters, and E7 injects one at each.

```mermaid
flowchart TD
  T[Monitor sees<br/>requirement &gt; equity] --> D[Detection delay<br/>nobody has decided yet]
  D --> FE[Fence every ingress lease<br/>at the ordering point]
  FE --> N1{Delivered to<br/>the gateways?}
  N1 -- no --> N2[Irrelevant to safety:<br/>the ordering point refuses]
  N1 -- yes --> N2
  N2 --> CA[Cancel every live order]
  CA --> K1{Acknowledged at<br/>the ordering point?}
  K1 -- recorded, notice lost --> K2[Order is released<br/>local view is stale]
  K1 -- never confirmed --> K3[Order stays live<br/>keeps its reservation]
  K2 --> U
  K3 --> U
  U[Unwind: propose a proportional<br/>basket, check both merged<br/>envelopes do not rise]
  U --> UC{Check passes?}
  UC -- no --> UH[Halve the fraction,<br/>down to one lot, then stall]
  UC -- yes --> CB[Commit basket as ONE record<br/>internal transfer, venue is<br/>the counterparty]
  CB --> CR{Crash before the<br/>local fold?}
  CR -- yes --> CRR[Rebuild from the log<br/>basket ID lands it once]
  CR -- no --> FL
  CRR --> FL
  FL{Position flat?}
  FL -- no --> U
  FL -- yes --> FQ[Fence the liquidator's<br/>own basket authority]
  FQ --> ST[Stop issuance,<br/>take barrier B]
  ST --> B1{Every lease fenced<br/>and B gap-free?}
  B1 -- no --> B2[Refuse: authority still live]
  B1 -- yes --> B3[Rebuild occupancy from<br/>the log at B: risk, gross<br/>reach, unabsorbed debit]
  B3 --> B4[Install under credit-version<br/>CAS, then resume issuance]
```

The two cancel outcomes are the two different facts of §5.4, and they are why the
box has two labelled exits rather than one. The barrier refuses on `no_fence` and
on a live liquidator; E7 exercises both refusals.

---

## Figure 4 — Failure paths and the degradation ladder

Every transition is labelled with what causes it and what the venue still accepts
in that state. The state a previous version of this figure called REDUCE_ONLY has
been removed: a gateway does not accept locally-judged risk-reducing orders,
because c9 shows that judgement is unsound across gateways.

```mermaid
stateDiagram-v2
    [*] --> NORMAL

    NORMAL --> TERM_EXPIRED: term ends with no<br/>new issuance
    TERM_EXPIRED --> NORMAL: allocator reachable,<br/>ceilings re-issued

    NORMAL --> QUARANTINE: solve infeasible<br/>at issuance
    NORMAL --> STALE: gateway sees a higher<br/>generation than its own
    QUARANTINE --> NORMAL: equity recovers,<br/>solve feasible again

    NORMAL --> FENCED: shortfall observed,<br/>leases fenced at the<br/>ordering point
    TERM_EXPIRED --> FENCED
    QUARANTINE --> FENCED
    STALE --> FENCED

    FENCED --> UNWINDING: cancel phase complete<br/>unacknowledged orders keep<br/>their reservation
    UNWINDING --> SETTLING: position flat,<br/>liquidator fenced
    SETTLING --> NORMAL: barrier taken, occupancy<br/>rebuilt, issuance resumed
    SETTLING --> SETTLING: refused while any<br/>authority is live

    NORMAL --> HALT: symbol circuit breaker
    HALT --> AUCTION_REOPEN: breaker window elapsed
    AUCTION_REOPEN --> NORMAL: auction matched,<br/>ceilings re-issued

    note right of QUARANTINE
      admits nothing, including
      orders that look locally
      like risk reduction
    end note

    note right of UNWINDING
      resting orders can still
      fill; a fence does not
      cancel them
    end note
```

The four edges into FENCED are the four ways this design loses its authority, and
all four fail closed for new risk. Risk reduction does not happen at the gateway
in any of them; it happens on the liquidation path, which is the CAP position §3.3
defends and the correction that removed REDUCE_ONLY.

E6 measures what the FENCED-to-SETTLING path costs as a function of the detection
delay, and E7 injects a fault at each labelled box.
