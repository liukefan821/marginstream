"""Recovery of a gateway's order state.

The gateway stopped being a stateless edge when it started holding an envelope.
What it holds is a deterministic fold of the ordering point's log, so the
acceptance test is not "the process restarted" but "the rebuilt state is the
state the log implies".
"""
import random
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


def build(seed=1, steps=120):
    rng = random.Random(seed)
    syms = [Symbol(f"S{i}", 0, 800 + 200 * i, 40 + 10 * i, 60 + 20 * i)
            for i in range(4)]
    risk = RiskModel(syms, addon_kappa=1, addon_scale=10 ** 6)
    seqr = Sequencer()
    alloc = Allocator(risk, ttl=10 ** 6, gross_per_risk=10 ** 4)
    gw = Gateway(0, risk, sequencer=seqr)
    leases, _ = alloc.issue(ACC, 10 ** 10, {0: 1}, now=0)
    gw.install_lease(leases[0])
    gen = alloc.current_generation(ACC)

    snapshot = None
    snapshot_at = steps // 3
    for i in range(steps):
        r = rng.random()
        if r < 0.55:
            ok, _ = gw.admit(ACC, rng.choice(syms).name,
                             rng.choice([-9, -3, -1, 1, 3, 9]), gen,
                             order_id=f"o{i}")
        elif r < 0.80:
            live = list(gw.live_orders(ACC).items())
            if live:
                oid, (_s, rem) = rng.choice(live)
                part = rem if rng.random() < 0.5 else (rem // 2 or rem)
                if part and gw.fill(ACC, oid, part)[0]:
                    seqr.record_fill(oid, part)
        else:
            live = list(gw.live_orders(ACC))
            if live:
                oid = rng.choice(live)
                if gw.cancel_ack(ACC, oid)[0]:
                    seqr.record_cancel(oid)
        if i == snapshot_at:
            snapshot = gw.snapshot()
    return risk, seqr, alloc, gw, snapshot, gen


def r1_snapshot_plus_replay_equals_full_rebuild():
    risk, seqr, _a, live, snap, _g = build()
    rec = Gateway(0, risk, sequencer=seqr)
    ok, why = rec.restore(snap, seqr)
    ref = Gateway.rebuild_from_log(0, risk, seqr)
    same_state = rec.state_digest(ACC) == ref.state_digest(ACC)
    same_aggs = rec.aggregate_digest(ACC) == ref.aggregate_digest(ACC)
    same_env = (rec.used_risk(ACC), rec.used_gross(ACC)) == \
               (ref.used_risk(ACC), ref.used_gross(ACC))
    matches_live = rec.state_digest(ACC) == live.state_digest(ACC)
    return _report(
        "r1 snapshot plus replay equals a full rebuild, and equals the live gateway",
        ok and same_state and same_aggs and same_env and matches_live,
        f"restore {ok}/{why}; state {rec.state_digest(ACC)} vs ref "
        f"{ref.state_digest(ACC)} vs live {live.state_digest(ACC)}; "
        f"aggregates equal {same_aggs}; envelopes equal {same_env}",
    )


def r2_replay_is_idempotent():
    risk, seqr, _a, _live, snap, _g = build(seed=2)
    rec = Gateway(0, risk, sequencer=seqr)
    rec.restore(snap, seqr)
    before = (rec.state_digest(ACC), rec.aggregate_digest(ACC))
    rec.recovering = True
    rec.replay(seqr.replay_from(snap["watermark"]))       # the same slice again
    rec.replay(seqr.replay_from(0))                       # and the whole log
    rec.recovering = False
    after = (rec.state_digest(ACC), rec.aggregate_digest(ACC))
    return _report(
        "r2 replaying the same events again changes nothing",
        before == after,
        f"{before} -> {after}",
    )


def r3_a_snapshot_from_another_holder_is_refused():
    risk, seqr, _a, _live, snap, _g = build(seed=3)
    other = Gateway(0, risk, incarnation=1, sequencer=seqr)
    ok, why = other.restore(snap, seqr)
    return _report(
        "r3 a snapshot cut for another incarnation is refused",
        (not ok) and why == "snapshot_for_another_holder" and other.recovering,
        f"restore {ok}/{why}; recovering={other.recovering}",
    )


def r4_a_snapshot_ahead_of_the_log_is_refused():
    risk, seqr, _a, _live, snap, _g = build(seed=4)
    ahead = dict(snap)
    ahead["watermark"] = seqr.position() + 5
    rec = Gateway(0, risk, sequencer=seqr)
    ok, why = rec.restore(ahead, seqr)
    return _report(
        "r4 a snapshot claiming a watermark past the log is refused",
        (not ok) and why == "snapshot_ahead_of_log" and rec.recovering,
        f"restore {ok}/{why}",
    )


def r5_a_recovering_gateway_admits_nothing():
    risk, seqr, alloc, _live, snap, gen = build(seed=5)
    rec = Gateway(0, risk, sequencer=seqr)
    rec.recovering = True
    leases, _ = alloc.issue(ACC, 10 ** 10, {0: 1}, now=1)
    rec.install_lease(leases[0])
    ok, why = rec.admit(ACC, "S0", 1, alloc.current_generation(ACC))
    return _report(
        "r5 a gateway that has not finished recovering admits nothing",
        (not ok) and why == "recovering",
        f"admit {ok}/{why}",
    )


def r6_decisions_after_recovery_match_a_gateway_that_never_crashed():
    risk, seqr, alloc, live, snap, gen = build(seed=6)
    rec = Gateway(0, risk, sequencer=seqr)
    rec.restore(snap, seqr)
    # give both the same lease and the same following orders
    leases, _ = alloc.issue(ACC, 10 ** 7, {0: 1}, now=1)
    live.install_lease(leases[0])
    rec.install_lease(leases[0])
    g = alloc.current_generation(ACC)

    rng = random.Random(99)
    same = True
    detail = ""
    for i in range(60):
        sym = rng.choice(list(risk.symbols))
        qty = rng.choice([-7, -2, 2, 7])
        a = live.admit(ACC, sym, qty, g, order_id=f"post{i}")
        b = rec.admit(ACC, sym, qty, g, order_id=f"post{i}")
        if a[0] != b[0]:
            same = False
            detail = f"step {i}: live {a}, recovered {b}"
            break
    return _report(
        "r6 admission decisions after recovery match a gateway that never crashed",
        same and live.state_digest(ACC) == rec.state_digest(ACC),
        detail or f"live {live.state_digest(ACC)} recovered {rec.state_digest(ACC)}",
    )


CASES = [r1_snapshot_plus_replay_equals_full_rebuild,
         r2_replay_is_idempotent,
         r3_a_snapshot_from_another_holder_is_refused,
         r4_a_snapshot_ahead_of_the_log_is_refused,
         r5_a_recovering_gateway_admits_nothing,
         r6_decisions_after_recovery_match_a_gateway_that_never_crashed]


def main():
    results = [c() for c in CASES]
    print(f"\n{sum(results)} of {len(results)} properties hold")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
