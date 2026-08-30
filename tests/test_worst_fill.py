"""Deterministic cases for the order-state envelopes.

Each is a situation where netting orders into a position gives an answer the
account cannot rely on.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from marginstream.risk import RiskModel, Symbol
from marginstream.allocator2 import Allocator
from marginstream.gateway2 import Gateway
from marginstream.sequencer import Sequencer

ACC = "X"


def _report(name, ok, detail):
    print(f"[{'pass' if ok else 'FAIL'}] {name}")
    if not ok:
        print(f"        {detail}")
    return ok


def w1_opposite_resting_orders_are_not_netted():
    """Two live orders of opposite sign net to nothing. Only one of them fills.
    """
    syms = [Symbol("A", 0, 1000, 100, 100)]
    risk = RiskModel(syms, addon_kappa=0, addon_scale=1)
    seqr = Sequencer()
    alloc = Allocator(risk, ttl=100)
    gw = Gateway(0, risk, sequencer=seqr)
    leases, _ = alloc.issue(ACC, 100_000, {0: 1}, now=0)
    gw.install_lease(leases[0])
    gen = alloc.current_generation(ACC)

    gw.admit(ACC, "A", 10, gen, order_id="o1")
    gw.admit(ACC, "A", -10, gen, order_id="o2")
    envelope = gw.used_risk(ACC)
    netted = risk.R({"A": 0})

    gw.fill(ACC, "o1", 10)                 # only the buy fills
    realised = risk.R(gw.filled_positions(ACC))

    return _report(
        "w1 opposite live orders are not netted away",
        envelope >= realised and netted == 0 and envelope > 0,
        f"netted view {netted}, envelope {envelope}, realised after one fill "
        f"{realised}",
    )


def w2_a_hedge_order_can_raise_reachable_gross():
    """An order that lowers the scenario requirement raises the gross notional
    the account can reach, and the gross envelope has to see it."""
    syms = [Symbol("A", 0, 1000, 100, 100), Symbol("B", 0, 1000, 100, 100)]
    risk = RiskModel(syms, addon_kappa=1, addon_scale=1_000_000)
    seqr = Sequencer()
    alloc = Allocator(risk, ttl=100, gross_per_risk=20)
    gw = Gateway(0, risk, sequencer=seqr)
    leases, _ = alloc.issue(ACC, 200_000, {0: 1}, now=0)
    gw.install_lease(leases[0])
    gen = alloc.current_generation(ACC)

    gw.admit(ACC, "A", 20, gen, order_id="o1")
    r1, g1 = gw.used_risk(ACC), gw.used_gross(ACC)
    ok, why = gw.admit(ACC, "B", -20, gen, order_id="o2")
    r2, g2 = gw.used_risk(ACC), gw.used_gross(ACC)

    return _report(
        "w2 the gross envelope sees a hedge that raises reachable notional",
        (not ok) or g2 > g1,
        f"admitted={ok} ({why}); risk {r1}->{r2}, gross {g1}->{g2}",
    )


def w3_a_fill_does_not_raise_the_envelope():
    """Turning a reservation into a position moves risk from the order side to
    the filled side and must not increase either envelope."""
    syms = [Symbol("A", 0, 1000, 100, 100), Symbol("B", 0, 900, 80, 120)]
    risk = RiskModel(syms, addon_kappa=0, addon_scale=1)
    seqr = Sequencer()
    alloc = Allocator(risk, ttl=100)
    gw = Gateway(0, risk, sequencer=seqr)
    leases, _ = alloc.issue(ACC, 500_000, {0: 1}, now=0)
    gw.install_lease(leases[0])
    gen = alloc.current_generation(ACC)

    gw.admit(ACC, "A", 12, gen, order_id="o1")
    gw.admit(ACC, "B", -7, gen, order_id="o2")
    r0, g0 = gw.used_risk(ACC), gw.used_gross(ACC)
    gw.fill(ACC, "o1", 5)                      # partial
    r1, g1 = gw.used_risk(ACC), gw.used_gross(ACC)
    gw.fill(ACC, "o1", 7)                      # the rest
    r2, g2 = gw.used_risk(ACC), gw.used_gross(ACC)

    return _report(
        "w3 filling a reservation does not raise either envelope",
        r1 <= r0 and r2 <= r0 and g1 <= g0 and g2 <= g0,
        f"risk {r0} -> {r1} -> {r2}; gross {g0} -> {g1} -> {g2}",
    )


def w4_cancel_releases_only_on_acknowledgement():
    syms = [Symbol("A", 0, 1000, 100, 100)]
    risk = RiskModel(syms, addon_kappa=0, addon_scale=1)
    seqr = Sequencer()
    alloc = Allocator(risk, ttl=100)
    gw = Gateway(0, risk, sequencer=seqr)
    leases, _ = alloc.issue(ACC, 100_000, {0: 1}, now=0)
    gw.install_lease(leases[0])
    gen = alloc.current_generation(ACC)

    gw.admit(ACC, "A", 9, gen, order_id="o1")
    before = gw.used_risk(ACC)
    gw.cancel_request(ACC, "o1")
    after_request = gw.used_risk(ACC)
    gw.cancel_ack(ACC, "o1")
    after_ack = gw.used_risk(ACC)

    return _report(
        "w4 a cancel request releases nothing, the acknowledgement does",
        after_request == before and after_ack < before,
        f"{before} -> after request {after_request} -> after ack {after_ack}",
    )


def w5_retry_is_idempotent_conflict_is_refused():
    seqr = Sequencer()
    ok1 = seqr.submit(7, 1, "o1", ACC, "A", 5)
    ok2 = seqr.submit(7, 1, "o1", ACC, "A", 5)      # same payload
    ok3 = seqr.submit(7, 1, "o1", ACC, "A", 6)      # same slot, different order
    ok4 = seqr.submit(7, 3, "o3", ACC, "A", 5)      # gap
    seqr.fence(7)
    ok5 = seqr.submit(7, 2, "o2", ACC, "A", 5)      # after the fence

    return _report(
        "w5 the ordering point is idempotent on retry and strict otherwise",
        ok1[0] and ok2[0] and (not ok3[0]) and (not ok4[0]) and (not ok5[0]),
        f"first {ok1}, retry {ok2}, conflict {ok3}, gap {ok4}, fenced {ok5}",
    )


def w6_fencing_does_not_remove_resting_orders():
    """A fenced lease admits nothing new. Orders it already admitted are still
    on the book and can still fill, so the exposure is not terminal."""
    syms = [Symbol("A", 0, 1000, 100, 100)]
    risk = RiskModel(syms, addon_kappa=0, addon_scale=1)
    seqr = Sequencer()
    alloc = Allocator(risk, ttl=100)
    gw = Gateway(0, risk, sequencer=seqr)
    leases, _ = alloc.issue(ACC, 100_000, {0: 1}, now=0)
    gw.install_lease(leases[0])
    gen = alloc.current_generation(ACC)
    gw.admit(ACC, "A", 8, gen, order_id="o1")

    seqr.fence(leases[0].lease_id)
    blocked, why = gw.admit(ACC, "A", 1, gen, order_id="o2")
    still_live = len(gw.live_orders(ACC))
    gw.fill(ACC, "o1", 8)
    after = risk.R(gw.filled_positions(ACC))

    return _report(
        "w6 a fence stops admission and leaves resting orders able to fill",
        (not blocked) and still_live == 1 and after > 0,
        f"post-fence admission {blocked} ({why}); live orders {still_live}; "
        f"requirement after the fill {after}",
    )


CASES = [w1_opposite_resting_orders_are_not_netted,
         w2_a_hedge_order_can_raise_reachable_gross,
         w3_a_fill_does_not_raise_the_envelope,
         w4_cancel_releases_only_on_acknowledgement,
         w5_retry_is_idempotent_conflict_is_refused,
         w6_fencing_does_not_remove_resting_orders]


def main():
    results = [c() for c in CASES]
    print(f"\n{sum(results)} of {len(results)} properties hold")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
