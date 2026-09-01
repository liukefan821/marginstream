"""Authority binding at the ordering point.

A lease id used to be a bearer token. Knowing one was enough to submit under
it: `Sequencer.submit` checked the sequence number and the fence and nothing
else, so a component could name any account and claim any holder and be
believed. An external review demonstrated it directly — a valid lease id
belonging to one account, submitted with a different account and a fabricated
holder, returned `(True, "ok")` — which falsified what §6.1 claimed about a
compromised gateway's blast radius.

t1 is that attack, and it now fails. The rest are the neighbouring cases.

What these tests establish is the *binding check*. They do not establish
authentication: `open_session` stands in for a transport that has already
identified the peer, and in a deployment that identity comes from the
connection and never from the request body. §6.1 states the residual.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from marginstream.risk import RiskModel, Symbol
from marginstream.allocator2 import Allocator
from marginstream.gateway2 import Gateway
from marginstream.sequencer import Sequencer

VICTIM = "victim"
OTHER = "other-account"


def _report(name, ok, detail):
    print(f"[{'pass' if ok else 'FAIL'}] {name}")
    if not ok:
        print(f"        {detail}")
    return ok


def _venue():
    syms = [Symbol("A", 0, 1000, 200, 100, 5, 2)]
    risk = RiskModel(syms, addon_kappa=1, addon_scale=10 ** 7)
    seqr = Sequencer()
    alloc = Allocator(risk, sequencer=seqr, ttl=10 ** 6, gross_per_risk=8)
    gw = Gateway(0, risk, sequencer=seqr)
    leases, _ = alloc.issue(VICTIM, 400_000, {0: 1}, now=0)
    gw.install_lease(leases[0])
    return risk, seqr, alloc, gw, leases[0]


def t1_a_valid_lease_id_does_not_carry_another_account():
    """The reviewer's case. A component holding a lease id issued for `victim`
    submits under it naming a different account."""
    risk, seqr, alloc, gw, lease = _venue()
    honest, why_honest = seqr.submit(gw.session, lease.lease_id, 1, "o1",
                                     VICTIM, "A", -1, mark=1000, band=5,
                                     fee_cap=2)
    stolen, why_stolen = seqr.submit(gw.session, lease.lease_id, 2, "o2",
                                     OTHER, "A", -1, mark=1000, band=5,
                                     fee_cap=2)
    recorded = [e for e in seqr.events
                if e[0] == "admit" and e[4] == OTHER]
    return _report(
        "t1 a valid lease id does not carry another account",
        honest and (not stolen) and why_stolen == "wrong_account"
        and not recorded,
        f"under its own account: {honest} ({why_honest}); under another: "
        f"{stolen} ({why_stolen}); admissions recorded for {OTHER}: "
        f"{len(recorded)}")


def t2_a_lease_cannot_be_used_from_another_holders_session():
    """The holder is resolved from the session, not from the request. A second
    process authenticated as a different holder cannot spend the first's
    lease, even with the correct lease id and account."""
    risk, seqr, alloc, gw, lease = _venue()
    impostor = seqr.open_session((999, 999))
    ok, why = seqr.submit(impostor, lease.lease_id, 1, "o1", VICTIM, "A", -1,
                          mark=1000, band=5, fee_cap=2)
    unauth, why_unauth = seqr.submit(None, lease.lease_id, 1, "o1", VICTIM,
                                     "A", -1, mark=1000, band=5, fee_cap=2)
    return _report(
        "t2 a lease cannot be used from another holder's session",
        (not ok) and why == "wrong_holder"
        and (not unauth) and why_unauth == "unauthenticated",
        f"from holder (999,999): {ok} ({why}); with no session: {unauth} "
        f"({why_unauth})")


def t3_a_fenced_lease_refuses_from_its_own_holder():
    """The fence is what stops a holder the term cannot: the ordering point has
    no clock it can compare against a lease expiry set elsewhere, so an honest
    gateway is bounded by its own term and this is what bounds a dishonest
    one."""
    risk, seqr, alloc, gw, lease = _venue()
    before, _w1 = seqr.submit(gw.session, lease.lease_id, 1, "o1", VICTIM, "A",
                              -1, mark=1000, band=5, fee_cap=2)
    seal = seqr.fence(lease.lease_id)
    after, why = seqr.submit(gw.session, lease.lease_id, 2, "o2", VICTIM, "A",
                             -1, mark=1000, band=5, fee_cap=2)
    return _report(
        "t3 a fenced lease refuses even from its own holder's session",
        before and (not after) and why == "lease_fenced"
        and seal.terminal_seq == 1,
        f"before the fence: {before}; after: {after} ({why}); seal covers "
        f"{seal.terminal_seq} admission(s)")


def t4_an_unregistered_lease_id_is_not_authority():
    """A lease id the allocator never minted is not a capability. Guessing or
    incrementing one buys nothing."""
    risk, seqr, alloc, gw, lease = _venue()
    fabricated = lease.lease_id + 10 ** 6
    ok, why = seqr.submit(gw.session, fabricated, 1, "o1", VICTIM, "A", -1,
                          mark=1000, band=5, fee_cap=2)
    return _report(
        "t4 an unregistered lease id is not authority",
        (not ok) and why == "unknown_lease",
        f"lease {fabricated}: {ok} ({why})")


def t5_the_two_authority_kinds_are_not_interchangeable():
    """An ingress lease may carry orders and not basket transfers; a
    liquidation lease the other way round. The liquidator's admissions are
    checked against the merged account rather than against a ceiling, so an
    ingress holder that could commit a basket would be admitting without any
    ceiling at all."""
    risk, seqr, alloc, gw, lease = _venue()
    liq_gw = Gateway(99, risk, sequencer=seqr, fencing=False)
    liq_lease = alloc.issue_liquidation_lease(VICTIM, 99)
    liq_gw.install_lease(liq_lease)

    legs = (("A", 1, 1005, 2),)
    terms = ((1000, 5, 2),)
    basket_on_ingress, why_b = seqr.commit_basket(
        gw.session, lease.lease_id, 1, "b1", VICTIM, legs, terms)
    order_on_liquidation, why_o = seqr.submit(
        liq_gw.session, liq_lease.lease_id, 1, "o1", VICTIM, "A", -1,
        mark=1000, band=5, fee_cap=2)
    proper, why_p = seqr.commit_basket(
        liq_gw.session, liq_lease.lease_id, 1, "b2", VICTIM, legs, terms)

    return _report(
        "t5 the two authority kinds are not interchangeable",
        (not basket_on_ingress) and why_b == "wrong_authority_kind"
        and (not order_on_liquidation) and why_o == "wrong_authority_kind"
        and proper,
        f"basket under an ingress lease: {basket_on_ingress} ({why_b}); order "
        f"under a liquidation lease: {order_on_liquidation} ({why_o}); basket "
        f"under its own lease: {proper} ({why_p})")


def t6_a_lease_id_cannot_be_rebound():
    """Registration is write-once for a given binding. Re-registering the same
    id against a different account or holder is refused, so a component that
    can reach the registry cannot quietly move an existing lease."""
    risk, seqr, alloc, gw, lease = _venue()
    same, why_same = seqr.register_lease(lease.lease_id, VICTIM, (0, 0),
                                         "ingress")
    moved, why_moved = seqr.register_lease(lease.lease_id, OTHER, (0, 0),
                                           "ingress")
    still_bound = seqr.lease_registry[lease.lease_id]
    return _report(
        "t6 a lease id cannot be rebound to another account or holder",
        same and (not moved) and why_moved == "lease_id_rebound"
        and still_bound[0] == VICTIM,
        f"re-registering the same binding: {same} ({why_same}); a different "
        f"one: {moved} ({why_moved}); the binding is still {still_bound}")


CASES = [t1_a_valid_lease_id_does_not_carry_another_account,
         t2_a_lease_cannot_be_used_from_another_holders_session,
         t3_a_fenced_lease_refuses_from_its_own_holder,
         t4_an_unregistered_lease_id_is_not_authority,
         t5_the_two_authority_kinds_are_not_interchangeable,
         t6_a_lease_id_cannot_be_rebound]


def main():
    results = [c() for c in CASES]
    print(f"\n{sum(results)} of {len(results)} properties hold")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
