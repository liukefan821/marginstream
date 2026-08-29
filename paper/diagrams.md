# Diagrams

Mermaid source. GitHub renders these inline; for the PDF, paste a block into
mermaid.live and export SVG (see `paper/DIAGRAM_EXPORT.md`).

---

## Figure 1 — Component view

Two authorities and two orthogonal partitionings. Matching is partitioned by
symbol because price-time priority is a total order per book. The allocator is
partitioned by account because margin is an account-level quantity. Nothing on
the order path crosses either partition.

```mermaid
flowchart LR
  subgraph CL[Clients]
    C1[Client A]
    C2[Client B]
  end

  subgraph GW[Ingress admission gateways]
    G1[Gateway 1<br/>local check<br/>holds leases]
    G2[Gateway N<br/>local check<br/>holds leases]
  end

  subgraph CORE[Matching core - partitioned by symbol]
    M1[Shard 1<br/>single writer<br/>symbols 1..15]
    M2[Shard 8<br/>single writer<br/>symbols 106..120]
  end

  subgraph ALLOC[Margin allocator - partitioned by account]
    A1[Allocator shard 1<br/>accounts 1..N/16]
    A2[Allocator shard 16<br/>accounts 15N/16..N]
  end

  MD[Market data<br/>publishes state index k]
  LG[(Replicated log<br/>orders, schedule inputs,<br/>state transitions)]
  LD[Ledger<br/>double entry]

  C1 --> G1
  C2 --> G2
  G1 --> M1
  G1 --> M2
  G2 --> M1
  G2 --> M2
  M1 --> LD
  M2 --> LD
  M1 -. trades .-> A1
  M2 -. trades .-> A2
  MD -. state k .-> G1
  MD -. state k .-> G2
  MD -. marks .-> A1
  MD -. marks .-> A2
  A1 -. schedule inputs .-> LG
  A2 -. schedule inputs .-> LG
  LG -. derive lease .-> G1
  LG -. derive lease .-> G2
  M1 --> LG
  M2 --> LG
```

Solid edges are the order path. Dashed edges are asynchronous and carry no
per-order latency.

---

## Figure 2 — Data flow for one order

The four inputs to an admission decision, and where each comes from. Two of
them — the lease and the market state — are the reason §5.2 puts extra records
on the log.

```mermaid
flowchart TD
  O[Order arrives<br/>client order ID, symbol, qty] --> F1{Lease present<br/>for this account?}
  F1 -- no --> R1[Refuse: no lease]
  F1 -- yes --> F2{Lease generation<br/>below highest seen?}
  F2 -- yes --> R2[Refuse: shard stale<br/>fail closed]
  F2 -- no --> S1[Read market state k<br/>from market-data path]
  S1 --> S2[Ratchet: k = max k seen<br/>since lease installed]
  S2 --> S3[Update per-scenario<br/>loss vector, 16 adds]
  S3 --> S4[Marginal requirement<br/>= max over scenarios]
  S4 --> F3{Fits schedule at k<br/>minus already spent?}
  F3 -- no, remainder negative --> R3[Refuse: reduce-only<br/>report condition]
  F3 -- no, cost too large --> R4[Refuse: capacity exhausted]
  F3 -- yes --> A1[Decrement, forward to<br/>matching shard]
  A1 --> A2[Matching applies client<br/>order ID at most once]
  A2 --> A3[Journal: lease, generation,<br/>state, decision]
```

Steps S3 and S4 are the whole per-order cost of the margin check: sixteen
multiply-adds and sixteen comparisons, independent of how many contracts the
account holds.

---

## Figure 3 — Deployment view

```mermaid
flowchart TB
  subgraph AZ[Availability zone]
    subgraph EDGE[Edge tier - stateless, scales horizontally]
      GWX[Ingress gateways<br/>N instances]
    end

    subgraph COREZ[Core tier - pinned cores, no GC on hot path]
      MS1[Matching shard leader 1..8]
      MSR[Matching shard followers<br/>2 per shard]
    end

    subgraph ALLOCZ[Allocator tier - 16 shards by account]
      AL1[Allocator leader 1..16]
      ALR[Allocator followers<br/>2 per shard]
    end

    subgraph DATA[Durability]
      RL[(Raft log<br/>12.8 MB/s orders<br/>8 MB/s schedule inputs)]
      SN[(Snapshots<br/>every 5 min<br/>187 MB)]
    end

    MDP[Market data publisher<br/>multi-source, trimmed]
  end

  GWX --> MS1
  MS1 --- MSR
  AL1 --- ALR
  MS1 --> RL
  AL1 --> RL
  RL --> SN
  MDP -. state k .-> GWX
  MDP -. marks .-> AL1
  MS1 -. trades .-> AL1
```

Sizing from §1.6 and §5.5: 16 allocator shards at 2.5 x 10^8 scenario operations
per second each; snapshot 187 MB; cold rebuild inside 60 s at an assumed 10x
replay rate.

---

## Figure 4 — Failure paths and the degradation ladder

Every transition is labelled with what causes it and what the venue still
accepts in that state.

```mermaid
stateDiagram-v2
    [*] --> NORMAL

    NORMAL --> TIGHTEN: market state advances<br/>schedule shrinks, no message
    TIGHTEN --> NORMAL: next issuance at a<br/>calmer state

    TIGHTEN --> REDUCE_ONLY: consumption above<br/>schedule at current state
    NORMAL --> REDUCE_ONLY: allocator unreachable<br/>and lease expired
    NORMAL --> REDUCE_ONLY: shard observes a higher<br/>generation than its lease

    REDUCE_ONLY --> NORMAL: liquidation done,<br/>generation bumped, re-issued

    REDUCE_ONLY --> HALT: symbol circuit breaker
    HALT --> AUCTION_REOPEN: breaker window elapsed
    AUCTION_REOPEN --> NORMAL: auction matched,<br/>schedules re-issued

    note right of REDUCE_ONLY
      risk-reducing orders and
      cancels accepted throughout
    end note

    note right of HALT
      cancels accepted;
      no new orders
    end note
```

The three edges into REDUCE_ONLY are the three ways this design can lose its
authority: the market moved inside an epoch, the allocator became unreachable,
or the shard discovered it was stale. All three fail closed for new risk and
open for risk reduction, which is the CAP position §3 defends.

E4 measures the first edge. A scalar lease has no such edge and spends 236 of
480 ticks with the requirement above equity; the schedule spends none. E2
measures the third: with the generation rule disabled, a stale shard spends an
allowance that has been replaced and the requirement reaches 10,047 against
10,000 of equity.
