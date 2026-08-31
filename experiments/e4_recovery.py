"""E4: crash, snapshot, replay, takeover.

A gateway is driven through admissions, partial fills, fills and cancel
acknowledgements. At randomly chosen points the process is destroyed and
rebuilt, from a snapshot when one exists and from the whole log when it does
not. After every recovery the rebuilt state is compared against a gateway built
from the entire log, and the run continues on the rebuilt object.

The crash points are recorded by the window they fall in rather than by a
counter of how many times a crash was called:

    before_any_snapshot      no snapshot exists yet, so the whole log is replayed
    after_snapshot           a snapshot exists and events follow it
    with_no_events_since     a snapshot exists and nothing has happened since
    mid_partial_fill         between two partial fills of one order
    stale_snapshot_offered   an older snapshot is offered instead of the newest
    other_incarnation        a snapshot cut by a different incarnation is offered
"""
import random
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from marginstream.risk import RiskModel, Symbol
from marginstream.allocator2 import Allocator
from marginstream.gateway2 import Gateway
from marginstream.sequencer import Sequencer

ACC = "X"
TRIALS = 200
STEPS = 150


def fresh_counts():
    return {
        "crashes": 0, "recoveries": 0,
        "before_any_snapshot": 0, "after_snapshot": 0,
        "with_no_events_since": 0, "mid_partial_fill": 0,
        "stale_snapshot_offered": 0, "other_incarnation_refused": 0,
        "events_replayed": 0, "events_skipped": 0,
        "equivalence_checks": 0, "equivalence_failures": 0,
        "admissions": 0, "fills": 0, "cancels": 0,
    }


def one_trial(seed, c):
    rng = random.Random(seed)
    syms = [Symbol(f"S{i}", 0, 700 + 150 * i, 30 + 12 * i, 50 + 25 * i)
            for i in range(4)]
    risk = RiskModel(syms, addon_kappa=1, addon_scale=10 ** 6)
    seqr = Sequencer()
    alloc = Allocator(risk, ttl=10 ** 6, gross_per_risk=10 ** 4)
    gw = Gateway(0, risk, sequencer=seqr)
    leases, _ = alloc.issue(ACC, 10 ** 10, {0: 1}, now=0)
    gw.install_lease(leases[0])
    gen = alloc.current_generation(ACC)

    snaps = []
    pending_partial = None

    for i in range(STEPS):
        r = rng.random()
        if r < 0.45:
            ok, _ = gw.admit(ACC, rng.choice(syms).name,
                             rng.choice([-9, -3, -1, 1, 3, 9]), gen,
                             order_id=f"{seed}:{i}")
            if ok:
                c["admissions"] += 1
        elif r < 0.68:
            live = list(gw.live_orders(ACC).items())
            if live:
                oid, (_s, rem) = rng.choice(live)
                part = rem // 2 or rem
                if part and gw.fill(ACC, oid, part)[0]:
                    seqr.record_fill(oid, part, risk.symbols[_s].mark, 0)
                    c["fills"] += 1
                    pending_partial = oid if abs(part) < abs(rem) else None
        elif r < 0.80:
            live = list(gw.live_orders(ACC))
            if live:
                oid = rng.choice(live)
                if gw.cancel_ack(ACC, oid)[0]:
                    seqr.record_cancel(oid)
                    c["cancels"] += 1
        elif r < 0.88:
            snaps.append(gw.snapshot())
        else:
            # crash
            c["crashes"] += 1
            if not snaps:
                c["before_any_snapshot"] += 1
                use = None
            elif snaps[-1]["watermark"] == seqr.position():
                c["with_no_events_since"] += 1
                use = snaps[-1]
            elif len(snaps) > 1 and rng.random() < 0.3:
                c["stale_snapshot_offered"] += 1
                use = snaps[0]
            else:
                c["after_snapshot"] += 1
                use = snaps[-1]
            if pending_partial is not None:
                c["mid_partial_fill"] += 1

            if rng.random() < 0.15 and use is not None:
                wrong = dict(use)
                wrong["holder"] = (0, 99)
                probe = Gateway(0, risk, sequencer=seqr)
                ok, _why = probe.restore(wrong, seqr)
                if not ok:
                    c["other_incarnation_refused"] += 1

            rebuilt = Gateway(0, risk, sequencer=seqr)
            if use is None:
                rebuilt.recovering = True
                a, s = rebuilt.replay(seqr.replay_from(0))
                rebuilt.recovering = False
            else:
                okr, msg = rebuilt.restore(use, seqr)
                a = int(msg.split()[1]) if okr else 0
                s = 0
            c["events_replayed"] += a
            c["events_skipped"] += s
            c["recoveries"] += 1

            ref = Gateway.rebuild_from_log(0, risk, seqr)
            c["equivalence_checks"] += 1
            if (rebuilt.state_digest(ACC) != ref.state_digest(ACC)
                    or rebuilt.aggregate_digest(ACC) != ref.aggregate_digest(ACC)
                    or rebuilt.used_risk(ACC) != ref.used_risk(ACC)
                    or rebuilt.used_gross(ACC) != ref.used_gross(ACC)):
                c["equivalence_failures"] += 1
                return (seed, i, rebuilt.state_digest(ACC),
                        ref.state_digest(ACC))
            rebuilt.install_lease(leases[0])
            gw = rebuilt
    return None


def main():
    c = fresh_counts()
    failures = []
    for seed in range(TRIALS):
        f = one_trial(seed, c)
        if f:
            failures.append(f)
    print(f"trials: {TRIALS}, steps per trial: {STEPS}")
    print("windows and counters: " + ", ".join(f"{k}={v}" for k, v in c.items()))
    print("check: rebuilt state, aggregates and both envelopes against a "
          "gateway built from the whole log")
    if failures:
        print(f"FAIL: {len(failures)} recoveries disagreed")
        for f in failures[:3]:
            print(f"  seed {f[0]} step {f[1]}: {f[2]} vs {f[3]}")
        return 1
    print("every recovery reproduced the state the log implies")
    os.makedirs("results", exist_ok=True)
    with open("results/e4_recovery.json", "w") as fh:
        json.dump(c, fh, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
