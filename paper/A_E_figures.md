# Appendix E — Liquidation and settlement flow

The step-by-step flow behind §5.4, moved here from §2.9 to keep the body to
its page budget. E7 injects a fault at each of its three stages.

## E.1 Figure 5 — Liquidation and settlement

The path from a shortfall to capacity being returned, in three stages. E7 injects
a fault at each.

```mermaid
flowchart TD
  T[Monitor sees<br/>requirement &gt; equity] --> D[Detection delay<br/>nobody has decided yet]
  D --> FE[Fence every ingress lease<br/>at the ordering point]
  FE --> N1{Delivered to<br/>the gateways?}
  N1 -- no --> N2[Irrelevant to safety:<br/>the ordering point refuses]
  N1 -- yes --> N2
  N2 --> CA[Cancel every live order]
  CA --> K1{Acknowledged at<br/>the ordering point?}
  K1 -- recorded,<br/>notice lost --> K2[Order released<br/>local view is stale]
  K1 -- never<br/>confirmed --> K3[Order stays live<br/>keeps its reservation]
  K2 --> U([to the unwind])
  K3 --> U
```

```mermaid
flowchart TD
  U([from the cancel phase]) --> P[Propose a proportional basket,<br/>check both merged envelopes<br/>do not rise]
  P --> UC{Check passes?}
  UC -- no --> UH[Halve the fraction, down to<br/>one lot, then stall]
  UC -- yes --> CB[Commit basket as ONE record<br/>internal transfer, venue is<br/>the counterparty]
  CB --> CR{Crash before the<br/>local fold?}
  CR -- yes --> CRR[Rebuild from the log<br/>basket ID lands it once]
  CR -- no --> FL{Position flat?}
  CRR --> FL
  FL -- no --> P
  FL -- yes --> S([to the settlement])
```

```mermaid
flowchart TD
  S([from the unwind]) --> FQ[Fence the liquidator's<br/>own basket authority]
  FQ --> ST[Stop issuance,<br/>take barrier B]
  ST --> B1{Every lease fenced<br/>and B gap-free?}
  B1 -- no --> B2[Refuse:<br/>authority still live]
  B1 -- yes --> B3[Rebuild occupancy from the<br/>log at B: risk, gross reach,<br/>unabsorbed debit]
  B3 --> B4[Install under credit-version<br/>CAS, then resume issuance]
```

The cancel check has two labelled exits because they are two different facts
(§5.4): a cancel recorded at the ordering point releases the order, one the
matching side never confirmed does not. The barrier refuses on `no_fence` and on
a live liquidator, and E7 exercises both refusals.


