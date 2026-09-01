"""E6: a liquidation and insurance-loss experiment.

This is not a test that the pre-trade invariant held. At the moment the run
starts measuring, the account is already 738 short of its requirement: the
market has moved further than the scenario grid covers, which is the only way
that state is reachable. The credit event has happened. What is measured is how
much of it the venue ends up wearing, which is the draw on an insurance fund
once the account's own equity is gone.

So the mechanism is not preventing a violation here. It is limiting a loss that
has already started, and the question is which parts of that loss it can bound
and which it cannot.

Three things move equity over that interval and they are separated exactly
rather than estimated. Between two observations,

    dE = sum_s d(cash_s) + sum_s d(q_s * mark_s) - d(fees)

A fill of `dq` at price `p` while the mark is `m` contributes `dq * (m - p)`,
which is the negative of its slippage, and its fee. A mark move with the
position held contributes `sum_s q_s * d(mark_s)`. So

    ending equity == trigger equity + drift - slippage - fees

with drift signed as it enters equity, so an adverse market move is negative.
The run asserts that identity on integers with no tolerance. If the three
components did not account for the whole change, the decomposition below would
be a plot rather than a measurement.

The two arms differ in one thing: whether the ordering point fences the account's
leases at the trigger. In the unfenced arm the gateways keep their leases and
keep admitting for the rest of their term, which is what a venue relying on a
revocation message has when the message does not arrive.
"""
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from marginstream.risk import RiskModel, Symbol
from marginstream.allocator2 import Allocator
from marginstream.gateway2 import Gateway
from marginstream.sequencer import Sequencer
from marginstream.account import Account
from marginstream.execution import execute_fill
from marginstream import liquidation as L

ACC = "X"
GRID_DEN = 1000          # a drift rate is in thousandths of the widest step
MAX_TICKS = 4000


class Run:
    """One liquidation, driven a tick at a time with the decomposition kept."""

    def __init__(self, seed, collateral=400_000, gross_per_risk=8,
                 rate=40, fill_prob=0.35, admit_per_tick=2):
        self.rng = random.Random(seed)
        self.rate = rate
        self.fill_prob = fill_prob
        self.admit_per_tick = admit_per_tick
        syms = [Symbol("A", 0, 1000, 200, 100, 5, 2),
                Symbol("B", 0, 1500, 150, 80, 7, 2)]
        self.risk = RiskModel(syms, addon_kappa=1, addon_scale=10 ** 7)
        self.seqr = Sequencer()
        self.alloc = Allocator(self.risk, sequencer=self.seqr, ttl=10 ** 9,
                               gross_per_risk=gross_per_risk)
        self.gws = [Gateway(i, self.risk, sequencer=self.seqr)
                    for i in range(2)]
        self.acct = Account(self.risk, collateral)
        leases, _ = self.alloc.issue(ACC, self.acct.equity(), {0: 1, 1: 1},
                                     now=0)
        for g in self.gws:
            g.install_lease(leases[g.id])
        self.gen = self.alloc.current_generation(ACC)
        self.liq_gw = Gateway(99, self.risk, sequencer=self.seqr, fencing=False)
        self.liq_gw.install_lease(
            self.alloc.issue_liquidation_lease(ACC, 99))
        self.liq = L.Liquidation(self.risk, self.seqr, self.alloc, ACC,
                                 self.acct, self.gws, self.liq_gw)

        self.drift = 0
        self.slip_resting = 0
        self.slip_unwind = 0
        self.fee_resting = 0
        self.fee_unwind = 0
        self.admitted_after_trigger = 0
        self.fills_after_trigger = 0
        self.order_no = 0
        self.fill_no = 0
        # orders the matching side is unresponsive for: it neither acknowledges
        # a cancel nor fills them. E7 uses this to hold an order in the state
        # the settlement has to reserve for.
        self.no_fill = set()
        self.ticks = 0

    # ---- book ------------------------------------------------------------

    def load(self, caps=(None, 20), leave_resting=0):
        """Build the position, per gateway.

        The default is asymmetric on purpose. Gateway 0 carries the position
        that will go into shortfall. Gateway 1 carries very little, so its
        lease still has room in it, and it is never reissued to during the
        march below. That is the shape that makes fencing worth anything: a
        holder that is out of contact keeps the ceiling it was given when
        equity was high, and no message from the allocator reaches it.
        """
        n = 0
        for g in self.gws:
            cap = caps[g.id] if g.id < len(caps) else None
            taken = 0
            for sym in ("A", "B"):
                while cap is None or taken < cap:
                    self.order_no += 1
                    ok, _why = g.admit(ACC, sym, -1, self.gen,
                                       order_id=f"o{g.id}:{self.order_no}")
                    if not ok:
                        break
                    taken += 1
                    n += 1
        for g in self.gws:
            live = list(g.live_orders(ACC).items())
            keep = live[len(live) - leave_resting:] if leave_resting else []
            keep_ids = {oid for oid, _ in keep}
            for oid, (sym, rem) in [x for x in live if x[0] not in keep_ids]:
                s = self.risk.symbols[sym]
                self.fill_no += 1
                execute_fill(self.seqr, g, self.acct, f"f{self.fill_no}", oid,
                             ACC, sym, rem, s.mark - s.band,
                             s.fee_per_lot * abs(rem))
        return n

    # ---- the three components -------------------------------------------

    def record_fill(self, sym, dq, price, fee, unwind):
        mark = self.risk.symbols[sym].mark
        slip = dq * (price - mark)
        if unwind:
            self.slip_unwind += slip
            self.fee_unwind += fee
        else:
            self.slip_resting += slip
            self.fee_resting += fee

    def move_market(self):
        pos = self.acct.positions()
        before = self.acct.marks()
        self.risk.reprice(self.risk.displaced_marks(
            max(self.risk.grid) * self.rate, GRID_DEN))
        after = self.acct.marks()
        for g in self.gws + [self.liq_gw]:
            g.reprice()
        self.drift += sum(q * (after[s] - before[s]) for s, q in pos.items())

    # ---- one tick --------------------------------------------------------

    def tick(self, admitting=False, filling=True):
        """Resting orders fill, gateways admit if they still may, market moves.

        The fill lands at the worst price the order's own terms allow. Those
        terms were recorded when it was admitted, so under drift the band is
        anchored at a mark the market has left.
        """
        self.ticks += 1
        if filling:
            for g in self.gws:
                for oid, (sym, rem) in list(g.live_orders(ACC).items()):
                    if oid in self.no_fill:
                        continue
                    if self.rng.random() >= self.fill_prob:
                        continue
                    terms = self.seqr.terms.get(oid)
                    if terms is None:
                        continue
                    _s, _q, admitted_mark, band, cap, _p = terms
                    price = (admitted_mark - band if rem < 0
                             else admitted_mark + band)
                    fee = cap * abs(rem)
                    self.fill_no += 1
                    ok, _why = execute_fill(self.seqr, g, self.acct,
                                            f"f{self.fill_no}", oid, ACC, sym,
                                            rem, price, fee)
                    if ok:
                        self.record_fill(sym, rem, price, fee, unwind=False)
                        self.fills_after_trigger += 1
        if admitting:
            # a gateway out of contact carries on with the generation it last
            # saw, which is the one its own lease was cut for.
            for g in self.gws:
                lease = g.lease.get(ACC)
                if lease is None:
                    continue
                for _ in range(self.admit_per_tick):
                    self.order_no += 1
                    ok, _why = g.admit(ACC, self.rng.choice(("A", "B")), -1,
                                       lease.generation,
                                       order_id=f"o{g.id}:{self.order_no}")
                    if ok:
                        self.admitted_after_trigger += 1
        self.move_market()

    # ---- the identity ----------------------------------------------------

    def check_identity(self, start_equity):
        end = self.acct.equity()
        predicted = (start_equity
                     - (self.slip_resting + self.slip_unwind)
                     - (self.fee_resting + self.fee_unwind)
                     + self.drift)
        return end, predicted


def one(delay, ack, cap, arm, seed=0, rate=40, fill_prob=0.35,
        load_caps=(None, 20), admit_per_tick=2, reissue_every=8, keep_live=6):
    r = Run(seed, rate=rate, fill_prob=fill_prob,
            admit_per_tick=admit_per_tick)
    r.load(caps=load_caps, leave_resting=0)

    # Run the market until the account is in shortfall. Nothing is measured
    # here; this is how the account gets outside the model. The book is kept
    # alive while it happens: a few orders are admitted and the older ones
    # cancelled, so the orders live at the trigger were admitted recently.
    #
    # That matters because the ordering point enforces an order's price band
    # against the mark recorded when it was admitted. An order left resting
    # across the whole march can fill hundreds of ticks away from the market,
    # and the first version of this experiment measured that instead of the
    # delay: its resting-fill component was five times the drift.
    t = 0
    ages = {oid: 0 for g in r.gws for oid in g.live_orders(ACC)}
    live_gw = r.gws[0]
    while (L.shortfall(r.risk, r.liq.all_gateways(), ACC, r.acct) == 0
           and t < MAX_TICKS):
        from marginstream.execution import execute_cancel
        for oid in list(live_gw.live_orders(ACC)):
            if t - ages.get(oid, t) > 3:
                if execute_cancel(r.seqr, live_gw, ACC, oid)[0]:
                    ages.pop(oid, None)
        while len(live_gw.live_orders(ACC)) < keep_live:
            r.order_no += 1
            oid = f"o{live_gw.id}:{r.order_no}"
            ok, _why = live_gw.admit(ACC, r.rng.choice(("A", "B")), -1, r.gen,
                                     order_id=oid)
            if not ok:
                break
            ages[oid] = t
        # only the gateway that is still in contact is reissued to. gateway 1
        # keeps the lease it was given at tick 0.
        if reissue_every and t % reissue_every == 0 and t:
            r.alloc.observe_usage(ACC, {g.id: (g.used_risk(ACC),
                                               g.used_gross(ACC))
                                        for g in r.gws}, seq=t)
            r.alloc.bump_generation(ACC)
            fresh, _sc = r.alloc.issue(ACC, r.acct.equity(), {0: 1, 1: 1},
                                       now=t)
            if live_gw.id in fresh:
                live_gw.install_lease(fresh[live_gw.id])
            r.gen = r.alloc.current_generation(ACC)
        r.move_market()
        t += 1
    if t >= MAX_TICKS:
        return None

    trigger = {
        "tick": t,
        "equity": r.acct.equity(),
        "requirement": r.liq.requirement(),
        "shortfall": L.shortfall(r.risk, r.liq.all_gateways(), ACC, r.acct),
        "reserved_debit": sum(g.used_debit(ACC) for g in r.gws),
        "debit_bound": r.liq.debit_bound(),
        "live_orders": sum(len(g.live_orders(ACC)) for g in r.gws),
        "headroom": {g.id: g.lease[ACC].risk_amount - g.used_risk(ACC)
                     for g in r.gws if ACC in g.lease},
    }
    start_equity = r.acct.equity()
    r.drift = r.slip_resting = r.slip_unwind = 0
    r.fee_resting = r.fee_unwind = 0

    # detection latency: nobody has decided anything yet
    for _ in range(delay):
        r.tick(admitting=True)

    if arm == "fenced":
        r.liq.fence_all(deliver=False)
    # The bound on what the unwind can cost is fixed by the reachable position
    # at the moment authority ends, not at the moment the shortfall is seen.
    # Everything admitted during the detection delay is inside the first and
    # outside the second, which is why the two are recorded separately.
    bound_at_fence = r.liq.debit_bound()
    # the cancel round trip
    for _ in range(ack):
        r.tick(admitting=True)
    r.liq.cancel_all()

    steps = 0
    while not r.liq.flat() and steps < 400:
        out = r.liq.unwind_step(1, 4, lots_cap=cap)
        if out is None or out[0] != "committed":
            break
        _kind, _basket_id, legs = out
        for sym, qty, price, fee in legs:
            r.record_fill(sym, qty, price, fee, unwind=True)
        r.tick(admitting=True, filling=True)
        steps += 1

    end, predicted = r.check_identity(start_equity)
    return {
        "arm": arm, "delay": delay, "ack": ack, "cap": cap, "rate": rate,
        "trigger": trigger,
        "ticks_to_flat": r.ticks, "unwind_steps": steps, "flat": r.liq.flat(),
        "drift": r.drift,
        "slip_resting": r.slip_resting, "slip_unwind": r.slip_unwind,
        "fee_resting": r.fee_resting, "fee_unwind": r.fee_unwind,
        "admitted_after_trigger": r.admitted_after_trigger,
        "fills_after_trigger": r.fills_after_trigger,
        "equity_end": end, "identity_ok": end == predicted,
        "identity_gap": end - predicted,
        "insurance_draw": max(0, -end),
        "buffer_required": trigger["equity"] - end,
        "live_orders_at_end": r.liq.live_orders_remaining(),
        "execution_cost": (r.slip_resting + r.slip_unwind
                           + r.fee_resting + r.fee_unwind),
        "debit_bound_at_fence": bound_at_fence,
        "unwind_cost_over_trigger_bound": (r.slip_unwind + r.fee_unwind
                                           - trigger["debit_bound"]),
        "unwind_cost_over_fence_bound": (r.slip_unwind + r.fee_unwind
                                         - bound_at_fence),
    }


def _row(r, label):
    return (f"{label:>11} {r['delay']:>6} {r['drift']:>10} "
            f"{-r['slip_resting']:>10} {-r['slip_unwind']:>9} "
            f"{-(r['fee_resting'] + r['fee_unwind']):>7} "
            f"{r['admitted_after_trigger']:>9} {r['equity_end']:>11} "
            f"{r['insurance_draw']:>8} {'ok' if r['identity_ok'] else 'NO':>3}")


HEAD = (f"{'':>11} {'delay':>6} {'drift':>10} {'slip rest':>10} "
        f"{'slip unw':>9} {'fees':>7} {'admitted':>9} {'end equity':>11} "
        f"{'draw':>8} {'id':>3}")


def main():
    rows = []
    failures = []

    # --- part A: the delay sweep, with the account fenced at the trigger ---
    part_a = []
    for delay in (0, 1, 2, 4, 8, 16, 32, 64):
        row = one(delay, ack=1, cap=40, arm="fenced", seed=1, load_caps=(None, 20))
        if row is None:
            failures.append(("A", delay, "never reached shortfall"))
            continue
        part_a.append(row)
        rows.append(row)

    base = part_a[0]["trigger"]
    print(f"trigger: tick {base['tick']}, equity {base['equity']}, "
          f"requirement {base['requirement']}, shortfall {base['shortfall']}, "
          f"{base['live_orders']} orders still live")
    print(f"drift rate {part_a[0]['rate']}/{GRID_DEN} of the widest scenario "
          f"step per tick; cancel round trip 1 tick; unwind "
          f"{part_a[0]['cap']} lots per tick")
    print("\nPart A - detection delay, account fenced at the trigger")
    print(HEAD)
    for r in part_a:
        print(_row(r, "fenced"))

    # --- part B: the same delays without a fence --------------------------
    part_b = []
    for delay in (0, 1, 2, 4, 8, 16, 32, 64):
        row = one(delay, ack=1, cap=40, arm="unfenced", seed=1, load_caps=(None, 20))
        if row is None:
            failures.append(("B", delay, "never reached shortfall"))
            continue
        part_b.append(row)
        rows.append(row)
    print("\nPart B - the same runs with the leases left live")
    print(HEAD)
    for r in part_b:
        print(_row(r, "unfenced"))

    # --- part C: the drift rate --------------------------------------------
    print("\nPart C - the drift component against the rate, at a fixed delay "
          "of 8 ticks")
    print(f"{'rate':>11} {'drift':>10} {'execution':>10} {'end equity':>11} "
          f"{'draw':>8}")
    part_c = []
    for rate in (10, 20, 40, 80, 160):
        row = one(8, ack=1, cap=40, arm="fenced", seed=1, rate=rate,
                  load_caps=(None, 20))
        if row is None:
            failures.append(("C", rate, "never reached shortfall"))
            continue
        part_c.append(row)
        rows.append(row)
        print(f"{rate:>11} {row['drift']:>10} {-row['execution_cost']:>10} "
              f"{row['equity_end']:>11} {row['insurance_draw']:>8}")

    for r in rows:
        if not r["identity_ok"]:
            failures.append((r["arm"], r["delay"], r["rate"],
                             f"identity off by {r['identity_gap']}"))
        if not r["flat"] and r["arm"] == "fenced":
            failures.append((r["arm"], r["delay"], r["rate"], "not flat"))

    print("\ncolumns are signed as they enter equity: drift is what the "
          "market took, the others are\nwhat execution cost. 'draw' is the "
          "draw on an insurance fund, which is what is left\nafter the "
          "account's own equity is gone; the account itself has lost the whole "
          "of\ndrift, slippage and fees in every row.")

    print("\nrequired buffer, measured: equity at the trigger less equity at "
          "the end.\nThese are figures from this configuration and this seed, "
          "not a bound.")
    print(f"{'':>11} {'delay':>6} {'buffer':>10} {'identity':>30}")
    for r in part_a:
        t = r["trigger"]["equity"]
        print(f"{'fenced':>11} {r['delay']:>6} {r['buffer_required']:>10} "
              f"{t:>7} {r['drift']:>+9} "
              f"{-(r['slip_resting'] + r['slip_unwind']):>+7} "
              f"{-(r['fee_resting'] + r['fee_unwind']):>+5} "
              f"= {r['equity_end']}")
    fen = part_a
    print(f"\nover part A: drift runs from {fen[0]['drift']} to "
          f"{fen[-1]['drift']}; execution cost from "
          f"{-fen[0]['execution_cost']} to {-fen[-1]['execution_cost']}")
    print("\nunwind cost against the bound over the reachable position, "
          "worst of each set (negative is inside):")
    for arm in ("fenced", "unfenced"):
        sel = [r for r in rows if r["arm"] == arm]
        print(f"  {arm:>9}: measured at the trigger "
              f"{max(r['unwind_cost_over_trigger_bound'] for r in sel):>7}, "
              f"measured when authority ends "
              f"{max(r['unwind_cost_over_fence_bound'] for r in sel):>7}")
    unfinished = [r["delay"] for r in part_b if not r["flat"]]
    print(f"unfenced runs that never reached flat: {unfinished}")
    print(f"orders admitted after the trigger: fenced "
          f"{sum(r['admitted_after_trigger'] for r in part_a)}, unfenced "
          f"{sum(r['admitted_after_trigger'] for r in part_b)}")

    if failures:
        print("\nFAIL")
        for f in failures:
            print(f"  {f}")
        return 1
    print("\nthe decomposition accounts for the whole equity change in every "
          "run")
    os.makedirs("results", exist_ok=True)
    with open("results/e6_liquidation_delay.json", "w") as fh:
        json.dump(rows, fh, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
