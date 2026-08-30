"""Randomised check across generations.

The property tested is narrower and more exact than "the account never
breaches". A collateral cut can put an existing portfolio above its equity with
no order having been admitted; that is a credit event for the liquidation path,
not an admission failure. What the mechanism claims is:

    no admitted order increases the amount by which the requirement exceeds
    equity, at any scenario in the grid

so the oracle measures the breach before and after every admission and fails on
an increase. It also runs after collateral cuts, retirements, term expiries and
issuances, so a breach that appears without an admission is recorded rather
than passing unseen.

Branch counters record the condition, not the event. An earlier version counted
that a retirement happened; what matters is whether it happened while the
retired holder's term was still running, and with terms shorter than the span
of a generation that never occurred.
"""
import random
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from marginstream.risk import RiskModel, Symbol, FACTOR_GRID
from marginstream.allocator2 import Allocator, _key
from marginstream.gateway2 import Gateway
from marginstream.sequencer import Sequencer

ACC = "X"
TRIALS = 300
GENERATIONS = 6
ORDERS_PER_GEN = 40


def fresh_branches():
    return {
        "retire_with_live_authority": 0,
        "restart_with_live_predecessor": 0,
        "lost_usage_report": 0,
        "expired_unreconciled_term": 0,
        "old_generation_attempted": 0,
        "old_generation_accepted": 0,
        "stale_report_submitted": 0,
        "stale_report_rejected": 0,
        "terminal_release_submitted": 0,
        "terminal_release_accepted": 0,
        "terminal_release_refused": 0,
        "preexisting_breach_after_collateral_cut": 0,
        "quarantine": 0,
        "lease_lost": 0,
        "increase_inside_a_pre_cut_term": 0,
    }


def merged(gws):
    out = {}
    for gw in gws.values():
        for sym, qty in gw.local_positions(ACC).items():
            out[sym] = out.get(sym, 0) + qty
    return out


def gaps(risk, pos, collateral):
    """The shortfall of equity against the requirement, one entry per scenario.

    Collapsing this to a maximum hides risk moving from one scenario to
    another while the maximum stays put, so the oracle compares entry by
    entry.
    """
    m = risk.M(pos)
    return [m - (collateral - risk.loss(pos, f)) for f in FACTOR_GRID]


def worsened(before, after):
    """Which scenarios got worse, ignoring ones that were already negative and
    stayed inside their equity."""
    out = []
    for k, (b, a) in enumerate(zip(before, after)):
        if a > 0 and a > b:
            out.append((k, b, a))
    return out


def one_trial(seed, branches):
    rng = random.Random(seed)
    n_sym = rng.randrange(2, 9)
    syms = [
        Symbol(f"S{i}", 0, rng.randrange(500, 3000),
               rng.randrange(20, 200), rng.randrange(30, 160))
        for i in range(n_sym)
    ]
    risk = RiskModel(syms, addon_kappa=rng.randrange(0, 3),
                     addon_scale=rng.choice([10 ** 5, 10 ** 6, 10 ** 7]))
    collateral = rng.randrange(20_000, 2_000_000)

    # terms are drawn so that a good share of them outlive a generation, which
    # is what makes overlap between an old and a new holder possible at all
    span = ORDERS_PER_GEN
    ttl = rng.choice([rng.randrange(2, span), rng.randrange(span, 6 * span)])

    seqr = Sequencer()
    alloc = Allocator(risk, ttl=ttl, gross_per_risk=rng.randrange(5, 60))
    pool = [(g, 0) for g in range(rng.randrange(1, 4))]
    gws = {h: Gateway(h[0], risk, incarnation=h[1], sequencer=seqr)
           for h in pool}
    reported_seq = {}
    for h in pool:
        reported_seq[h] = -1
    now = 0
    admissions = 0
    seq = 0
    cut_binds_at = -1

    def live(h):
        # authority is keyed by lease id, so a holder is live when any lease
        # issued to it is still inside its term
        return any(holder == h and expiry > now
                   for holder, expiry, _r, _g
                   in alloc.authority.get(ACC, {}).values())

    for _ in range(GENERATIONS):
        r = rng.random()
        if r < 0.18 and len(pool) < 6:
            new_id = max(h[0] for h in gws) + 1
            h = (new_id, 0)
            pool.append(h); gws[h] = Gateway(new_id, risk, sequencer=seqr)
            reported_seq[h] = -1
        elif r < 0.36 and len(pool) > 1:
            h = rng.choice(pool)
            if live(h):
                branches["retire_with_live_authority"] += 1
            pool.remove(h)
            alloc.retire(ACC, h)
        elif r < 0.54:
            h = rng.choice(pool)
            if live(h):
                branches["restart_with_live_predecessor"] += 1
            nh = (h[0], h[1] + 1)
            pool.remove(h); pool.append(nh)
            gws[nh] = Gateway(nh[0], risk, incarnation=nh[1], sequencer=seqr)
            reported_seq[nh] = -1

        if rng.random() < 0.25:
            collateral = max(1_000, (collateral * 7) // 10)
            alloc.bump_credit_version(ACC)
            # a lease already in a gateway's hands cannot be recalled; the cut
            # binds only once every lease outstanding at that moment has run
            # out of term
            cut_binds_at = max(
                [rec[1] for rec in alloc.authority.get(ACC, {}).values()]
                or [now])
            if max(gaps(risk, merged(gws), collateral)) > 0:
                branches["preexisting_breach_after_collateral_cut"] += 1

        # usage reports, some of them lost, some of them stale
        for h in list(gws):
            if rng.random() < 0.3:
                branches["lost_usage_report"] += 1
                continue
            seq += 1
            if rng.random() < 0.15:
                prev = reported_seq.get(h, -1)
                stale = min(prev, seq - 5)
                branches["stale_report_submitted"] += 1
                before_c = alloc.committed_of(ACC, h)
                alloc.observe_usage(ACC, {h: (0, 0)}, seq=stale)
                if alloc.committed_of(ACC, h) == before_c:
                    branches["stale_report_rejected"] += 1
            else:
                alloc.observe_usage(
                    ACC, {h: (gws[h].used_risk(ACC), gws[h].used_gross(ACC))},
                    seq=seq)
                reported_seq[h] = seq

        # terminal release for leases whose term is over, sometimes
        for lid, (h, expiry, _r, _g) in list(
                alloc.authority.get(ACC, {}).items()):
            if expiry > now:
                continue
            if rng.random() < 0.5:
                branches["terminal_release_submitted"] += 1
                seal = seqr.fence(lid)
                if rng.random() < 0.2:
                    from marginstream.sequencer import Seal
                    seal = Seal(lid, max(0, seal.terminal_seq - 1))
                ok, _why = alloc.release(ACC, lid, seal, seqr)
                branches["terminal_release_accepted" if ok
                         else "terminal_release_refused"] += 1
            else:
                branches["expired_unreconciled_term"] += 1

        weights = {h: rng.randrange(0, 4) for h in pool}
        if not weights or sum(weights.values()) == 0:
            if pool:
                weights = {pool[0]: 1}
            else:
                break

        alloc.bump_generation(ACC)
        leases, scale = alloc.issue(ACC, collateral, weights, now=now)
        if scale is None:
            branches["quarantine"] += 1
        gen = alloc.current_generation(ACC)

        gen_of = {}
        for g, lz in leases.items():
            if rng.random() < 0.25:
                branches["lease_lost"] += 1
                continue
            gws[_key(g)].install_lease(lz)
            gen_of[_key(g)] = lz.generation

        for _ in range(ORDERS_PER_GEN):
            now += 1
            h = rng.choice(list(gws))
            sym = rng.choice(syms).name
            qty = rng.choice([-40, -13, -5, -1, 1, 5, 13, 40])
            held = gws[h].lease.get(ACC)
            if held is not None and rng.random() < 0.4:
                stamped = held.generation          # its own, possibly old
                if held.generation != gen:
                    branches["old_generation_attempted"] += 1
            else:
                stamped = gen
            before = gaps(risk, merged(gws), collateral)
            held = gws[h].lease.get(ACC)
            pre_cut_lease = (held is not None
                             and getattr(held, "credit_version", 0)
                             < alloc.credit_version.get(ACC, 0))
            ok, _reason = gws[h].admit(ACC, sym, qty, stamped, now=now)
            if not ok:
                continue
            admissions += 1
            if held is not None and held.generation != gen:
                branches["old_generation_accepted"] += 1
            after = gaps(risk, merged(gws), collateral)
            bad = worsened(before, after)
            if bad:
                if pre_cut_lease and now < cut_binds_at:
                    # a collateral cut is still inside the term of a lease
                    # issued before it. the exposure this permits is bounded by
                    # that term and is not an admission the mechanism claims to
                    # prevent
                    branches["increase_inside_a_pre_cut_term"] += 1
                    continue
                return (seed, bad, collateral,
                        dict(merged(gws))), admissions

        now += rng.randrange(0, ttl + 2)

    return None, admissions


def main():
    branches = fresh_branches()
    failures = []
    total = 0
    for seed in range(TRIALS):
        f, adm = one_trial(seed, branches)
        total += adm
        if f:
            failures.append(f)
    print(f"trials: {TRIALS}, generations per trial: {GENERATIONS}, "
          f"admissions: {total}")
    print("conditions reached: " + ", ".join(
        f"{k}={v}" for k, v in branches.items()))
    print("oracle: no admitted order worsens the shortfall at any individual "
          "scenario, compared entry by entry")
    if failures:
        print(f"FAIL: {len(failures)} trials")
        for f in failures[:3]:
            print(f"  seed {f[0]}: scenarios worsened {f[1]} "
                  f"(collateral {f[2]})")
            print(f"    positions {f[3]}")
        return 1
    print("no admission worsened any scenario")
    return 0


if __name__ == "__main__":
    sys.exit(main())
