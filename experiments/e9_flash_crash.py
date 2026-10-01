"""E9: a flash crash run through the liquidation path, with a circuit breaker,
an auction reopen, and the waterfall below the account.

The path follows the Session 3 timeline (slide 29): a fall past the edge of the
scenario grid, a symbol breaker, and a reopen by auction. The breaker trips at
half the widest grid step, which is 10% on symbol A (widest step 200 on a mark
of 1000) and matches the slide's band; the slide's 60 s halt duration is not
modelled. Session 3's headline move of 4% lies inside the grid, so the fall here
is larger: 3.4 widest steps. Units are E6's: a rate of 1000 moves the marks by the widest
scenario step in one tick.

Each account is long. Both gateways are loaded to their ceilings and filled, except that gateway 1 gives back 20 lots of room, so the crash is the
adverse direction. For every account and every arm the run records the equity
at the end, the draw on the insurance fund (the part of the loss the account's
own equity did not cover), what the venue's own book holds after the transfer,
and how many orders were admitted after the trigger. The E6 identity

    ending equity == trigger equity + drift - slippage - fees

is asserted on integers for every run.

Arms:
  breaker      the breaker halts matching and admissions when the move from
               the reference mark passes half the grid; liquidation waits for
               the reopen and runs at the auction mark
  no_breaker   no halt; liquidation runs through the crash

Reopen assumptions, both run, because the value of a halt depends on where the
auction clears and this run cannot know that:
  recover      the auction clears 60% of the way back from the low
  at_low       the auction clears at the low
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from e6_liquidation_delay import Run, GRID_DEN   # noqa: E402
from marginstream.execution import execute_fill  # noqa: E402
from marginstream import liquidation as L        # noqa: E402

ACC = "X"
SHAPES = {
    "gap": [-1400, -1400, -600],   # 3.4 widest grid steps in three ticks
    "slide": [-340] * 10,           # the same distance in ten ticks
}
CALM_BEFORE = 3
BREAKER_FRACTION = 500         # halt once the move passes half a grid step
HALT_TICKS = 10
DETECT_DELAY = 1


class LongRun(Run):
    def load_long(self, room=20):
        from marginstream.execution import execute_cancel
        for g in self.gws:
            for sym in ("A", "B"):
                while True:
                    self.order_no += 1
                    ok, _ = g.admit(ACC, sym, 1, self.gen,
                                    order_id=f"o{g.id}:{self.order_no}")
                    if not ok:
                        break
        # gateway 1 gives back `room` lots, so an unfenced lease there still
        # has capacity to admit with after the trigger
        g1 = self.gws[1]
        for oid in list(g1.live_orders(ACC))[-room:]:
            execute_cancel(self.seqr, g1, ACC, oid)
        for g in self.gws:
            for oid, (sym, rem) in list(g.live_orders(ACC).items()):
                s = self.risk.symbols[sym]
                self.fill_no += 1
                execute_fill(self.seqr, g, self.acct, f"f{self.fill_no}", oid,
                             ACC, sym, rem, s.mark + s.band,
                             s.fee_per_lot * abs(rem))

    def admit_some(self):
        n = 0
        for g in self.gws:
            lease = g.lease.get(ACC)
            if lease is None:
                continue
            for _ in range(self.admit_per_tick):
                self.order_no += 1
                ok, _ = g.admit(ACC, "A", 1, lease.generation,
                                order_id=f"o{g.id}:{self.order_no}")
                n += ok
        self.admitted_after_trigger += n

    def move(self, rate):
        self.rate = rate
        self.move_market()


def run_account(collateral, arm, reopen, crash):
    r = LongRun(seed=7, collateral=collateral, admit_per_tick=2)
    r.load_long()
    ref = dict(r.acct.marks())
    widest = {s: r.risk.max_move(s) for s in ref}
    path = [0] * CALM_BEFORE + list(crash) + [0] * 4
    st = {"trig": None, "start": None, "venue_lots": 0}

    def check_trigger():
        if (st["trig"] is None and
                L.shortfall(r.risk, r.liq.all_gateways(), ACC, r.acct) > 0):
            st["trig"] = True
            st["start"] = r.acct.equity()
            r.drift = r.slip_resting = r.slip_unwind = 0
            r.fee_resting = r.fee_unwind = 0
            r.admitted_after_trigger = 0
            st["wait"] = DETECT_DELAY

    def act():
        # after the trigger: detection delay, then fence and cancel, then one
        # unwind step per tick. unfenced leases keep admitting throughout.
        if st["trig"] is None:
            return
        if arm == "no_fence" or st["wait"] > 0:
            r.admit_some()
        if st["wait"] > 0:
            st["wait"] -= 1
            if st["wait"] == 0:
                if arm != "no_fence":
                    r.liq.fence_all(deliver=False)
                r.liq.cancel_all()
            return
        if not r.liq.flat():
            out = r.liq.unwind_step(1, 2, lots_cap=None)
            if out is not None and out[0] == "committed":
                for sym, qty, price, fee in out[2]:
                    r.record_fill(sym, qty, price, fee, unwind=True)
                    st["venue_lots"] += abs(qty)

    i = 0
    halted = False
    while i < len(path):
        r.move(path[i])
        i += 1
        check_trigger()
        act()
        if arm != "no_breaker":
            moved = max(abs(r.acct.marks()[s] - ref[s]) * GRID_DEN // widest[s]
                        for s in ref)
            if moved >= BREAKER_FRACTION:
                halted = True
                break
    if halted:
        # the halt: nothing trades. the rest of the fall happens off-venue and
        # the auction sets the reopening mark.
        before = dict(r.acct.marks())
        for rate in path[i:]:
            r.move(rate)
        low = dict(r.acct.marks())
        if reopen == "recover":
            target = {s: low[s] + (ref[s] - low[s]) * 6 // 10 for s in ref}
            r.risk.reprice(target)
            for g in r.gws + [r.liq_gw]:
                g.reprice()
            pos = r.acct.positions()
            r.drift += sum(q * (target[s] - low[s]) for s, q in pos.items())
        check_trigger()
    for _ in range(200):
        if st["trig"] is None or (r.liq.flat() and st.get("wait", 0) == 0):
            break
        act()
    if st["trig"] is None:
        return {"collateral": collateral, "arm": arm, "reopen": reopen,
                "liquidated": False, "equity_end": r.acct.equity(), "draw": 0,
                "venue_book_lots": 0, "admitted_after": 0, "identity_ok": True}
    end, predicted = r.check_identity(st["start"])
    return {"collateral": collateral, "arm": arm, "reopen": reopen,
            "liquidated": True, "flat": r.liq.flat(),
            "equity_at_trigger": st["start"], "equity_end": end,
            "drift": r.drift, "execution": -(r.slip_resting + r.slip_unwind
                                             + r.fee_resting + r.fee_unwind),
            "draw": max(0, -end), "venue_book_lots": st["venue_lots"],
            "admitted_after": r.admitted_after_trigger,
            "identity_ok": end == predicted}


def main():
    population = [60_000, 90_000, 150_000, 250_000, 400_000]
    fund = 100_000
    floor = 20_000
    rows = []
    fails = []
    print(f"breaker at {BREAKER_FRACTION / GRID_DEN:.2f} of a grid step; "
          f"detection delay {DETECT_DELAY} tick; unwind half the position per "
          f"tick")
    print(f"insurance fund {fund}, floor {floor}; five long accounts, "
          f"collateral {population}\n")
    print(f"{'shape':>6} {'arm':>10} {'reopen':>8} {'liquidated':>10} "
          f"{'draw':>8} {'venue lots':>10} {'fund left':>10} {'ADL':>7} "
          f"{'fund for no ADL':>16}")
    for shape, crash in SHAPES.items():
        for arm, reopen in (("breaker", "recover"), ("breaker", "at_low"),
                            ("no_breaker", "-")):
            res = [run_account(c, arm, reopen, crash) for c in population]
            for x in res:
                if not x["identity_ok"]:
                    fails.append((shape, arm, reopen, x["collateral"]))
            draw = sum(x["draw"] for x in res)
            usable = fund - floor
            adl = max(0, draw - usable)
            left = fund - min(draw, usable)
            rows.append({"shape": shape, "crash": crash, "arm": arm,
                         "reopen": reopen, "accounts": res, "draw": draw,
                         "adl": adl, "fund_left": left,
                         "fund_needed_no_adl": draw + floor})
            print(f"{shape:>6} {arm:>10} {reopen:>8} "
                  f"{sum(x['liquidated'] for x in res):>10} {draw:>8} "
                  f"{sum(x['venue_book_lots'] for x in res):>10} {left:>10} "
                  f"{adl:>7} {draw + floor:>16}")
    print("\ndraw: loss beyond the accounts' own equity, summed. fund left: "
          "after the draw, never below the floor.\nADL: what the fund could "
          "not cover above its floor, to be allocated to opposing positions.")
    if fails:
        print("\nFAIL", fails)
        return 1
    os.makedirs("results", exist_ok=True)
    with open("results/e9_flash_crash.json", "w") as fh:
        json.dump(rows, fh, indent=2)
    print("\nthe E6 identity holds in every liquidated account")
    return 0


if __name__ == "__main__":
    sys.exit(main())
