"""Margin allocator.

Runs off the order path. Solves, per account, for lease ceilings that keep the
account's requirement inside its equity at every scenario, and issues them to
ingress gateways.

With the absolute-envelope admission rule in `gateway.py`:

    R(P')      <= sum_h risk_h          (Lemma 1 plus the admission rule)
    loss(P',k) <= R(P')                 (R is a max over a set containing k)
    A(P')      <= A(sum_h gross_h)      (A is increasing in gross)

so `M(P') <= Collateral - loss(P', k)` follows from

    2 * sum_h risk_h + A(sum_h gross_h) <= Collateral

The factor of two is a closure, not a margin: positions admitted during the
term contribute both their own requirement and the loss they take at the
realised scenario, and the second is bounded by the first.

There is no market state in that condition. A schedule that shrinks with the
market does not make the mechanism safe, because a lease cannot remove a
position it has already admitted. A flat lease at the level solved for is
equally safe and admits at least as many orders as any decaying one. The
schedule is a local trigger and an operational tightening, not a capacity
mechanism.

The bookkeeping follows one rule:

    a lease term ends a holder's authority to admit; it never ends the
    exposure that holder already created.

Two quantities are therefore tracked per holder and only one of them expires.

A holder is `(gateway_id, incarnation)`. Identity alone is not enough: a
process that restarts and reuses its gateway id is a different holder, and both
may be live at once.
"""

DECAY_DEN = 1000


class Lease:
    __slots__ = ("account", "epoch", "generation", "gateway", "incarnation",
                 "expiry", "mode", "risk_curve", "gross_curve")

    def __init__(self, account, epoch, generation, gateway, incarnation,
                 expiry, mode, risk_curve, gross_curve):
        self.account = account
        self.epoch = epoch
        self.generation = generation
        self.gateway = gateway
        self.incarnation = incarnation
        self.expiry = expiry
        self.mode = mode                    # "normal" or "quarantine"
        self.risk_curve = tuple(risk_curve)
        self.gross_curve = tuple(gross_curve)

    @staticmethod
    def _at(curve, k):
        if k < 0:
            k = 0
        if k >= len(curve):
            k = len(curve) - 1
        return curve[k]

    def risk_at(self, k):
        return self._at(self.risk_curve, k)

    def gross_at(self, k):
        return self._at(self.gross_curve, k)

    @property
    def risk_amount(self):
        return self.risk_curve[0]

    @property
    def gross_amount(self):
        return self.gross_curve[0]


def _key(g):
    return g if isinstance(g, tuple) else (g, 0)


class Allocator:
    def __init__(self, risk, shape=(DECAY_DEN,), ttl=1, gross_per_risk=20,
                 residual=0):
        """`gross_per_risk` fixes the ratio at which gross ceiling is issued
        alongside risk ceiling. The two resources are checked independently at
        the gateway, but they are not solved for independently: the solver
        moves along one ray through a two-dimensional feasible set. This is two
        independent checks against a fixed-ratio issuance policy, not a
        two-resource allocation."""
        self.risk = risk
        self.shape = tuple(shape)
        self.ttl = ttl
        self.gross_per_risk = gross_per_risk
        self.residual = residual
        self.epoch = 0
        self.generation = {}
        self.issued = {}
        self.authority = {}       # account -> holder -> (expiry, r_ceil, g_ceil)
        self.committed = {}       # account -> holder -> (r_used, g_used)
        self.watermark = {}       # account -> holder -> highest report seq seen
        self.released = {}        # account -> set of terminally reconciled holders
        self.retired = {}         # account -> set of holders no longer issued to

    # ---- state -----------------------------------------------------------

    def _auth(self, account):
        return self.authority.setdefault(account, {})

    def _comm(self, account):
        return self.committed.setdefault(account, {})

    def committed_of(self, account, gateway):
        return self._comm(account).get(_key(gateway), (0, 0))

    def observe_usage(self, account, usage, seq=None):
        """Record what each holder's admitted set occupies.

        `seq` is the holder's admission high-water mark that this report
        covers. A report carrying a watermark no higher than one already
        applied is dropped: a late snapshot must not lower what a newer one
        established. Without a watermark the report can only raise the figure.
        """
        comm = self._comm(account)
        wm = self.watermark.setdefault(account, {})
        for g, (r, gr) in usage.items():
            h = _key(g)
            if seq is not None:
                if seq <= wm.get(h, -1):
                    continue
                wm[h] = seq
            pr, pg = comm.get(h, (0, 0))
            comm[h] = (max(pr, r), max(pg, gr))

    def release(self, account, gateway, seq, usage, now):
        """Terminal reconciliation.

        The only path that lowers a holder's exposure, and it is refused unless
        the holder can no longer admit: its term must have run out. The report
        must also carry a watermark at least as high as anything already
        applied, so that a late snapshot cannot undo a newer one.
        """
        h = _key(gateway)
        rec = self._auth(account).get(h)
        if rec and rec[0] > now:
            return False, "still_within_term"
        wm = self.watermark.setdefault(account, {})
        if seq < wm.get(h, -1):
            return False, "stale_watermark"
        wm[h] = seq
        self._comm(account)[h] = usage
        self.released.setdefault(account, set()).add(h)
        return True, "released"

    def retire(self, account, gateway):
        """Stop issuing to a holder.

        This does not take back a lease that is still inside its term: a
        retired process may be partitioned rather than stopped, and its
        authority stands until the term ends or a terminal reconciliation
        arrives.
        """
        self.retired.setdefault(account, set()).add(_key(gateway))

    # ---- safety condition ------------------------------------------------

    def _ceilings(self, account, scale, weights):
        comm = self._comm(account)
        total_w = sum(weights.values()) or 1
        out = {}
        for g, w in weights.items():
            h = _key(g)
            cr, cg = comm.get(h, (0, 0))
            share = (scale * w * self.shape[0]) // (total_w * DECAY_DEN)
            need_for_gross = (-(-cg // self.gross_per_risk)
                              if self.gross_per_risk else 0)
            out[h] = max(share, cr, need_for_gross)
        return out

    def _bounds(self, account, ceilings, now):
        """Per holder, the most it could still spend once this generation is
        out, together with the exposure it already holds.

        A holder that receives its new lease spends the new ceiling; one that
        does not keeps the old until the term ends; and neither case makes its
        existing positions disappear.
        """
        auth = self._auth(account)
        comm = self._comm(account)
        released = self.released.get(account, set())
        risk_total = gross_total = 0
        for h in set(auth) | set(comm) | set(ceilings):
            new_r = ceilings.get(h, 0)
            new_g = new_r * self.gross_per_risk
            cr, cg = comm.get(h, (0, 0))
            rec = auth.get(h)
            if h in released:
                # the holder has been proved unable to admit further and its
                # final usage is known exactly
                held_r, held_g = 0, 0
            elif rec:
                # a term that has run out without a terminal reconciliation
                # leaves the allocator unable to say what was spent, so the
                # whole ceiling stays occupied. expiry ends permission; only
                # terminal, ordered reconciliation releases exposure.
                held_r, held_g = rec[1], rec[2]
            else:
                held_r, held_g = 0, 0
            risk_total += max(new_r, held_r, cr)
            gross_total += max(new_g, held_g, cg)
        return risk_total, gross_total

    def _feasible(self, account, scale, weights, collateral, now):
        ceilings = self._ceilings(account, scale, weights)
        risk_total, gross_total = self._bounds(account, ceilings, now)
        den = self.risk.A_den()
        lhs = ((2 * risk_total + self.residual) * den
               + self.risk.A_num(gross_total))
        return lhs <= collateral * den

    def solve_scale(self, account, collateral, weights, now):
        if not self._feasible(account, 0, weights, collateral, now):
            return None
        hi, step = 0, max(1, collateral)
        while self._feasible(account, hi + step, weights, collateral, now):
            hi += step
        lo, hi = hi, hi + step
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            if self._feasible(account, mid, weights, collateral, now):
                lo = mid
            else:
                hi = mid
        return lo

    # ---- issuance ---------------------------------------------------------

    def issue(self, account, collateral, weights, floors=None,
              gross_floors=None, now=0):
        """Issue a generation of leases.

        `floors` and `gross_floors` report what each holder's admitted set
        occupies; they fold into the persistent committed exposure.

        When the solve is infeasible the leases are issued in quarantine mode
        and a quarantined gateway admits nothing. Local risk reduction is not a
        safe fallback: an order that lowers one gateway's requirement can raise
        the account's, by removing a hedge held on another gateway. Reducing
        risk from that state needs a check against the whole account, which a
        gateway cannot perform on its own.
        """
        if floors or gross_floors:
            f = floors or {}
            gf = gross_floors or {}
            self.observe_usage(account, {
                g: (f.get(g, 0), gf.get(g, 0)) for g in set(f) | set(gf)
            })

        gen = self.generation.get(account, 0) + 1
        self.generation[account] = gen

        scale = self.solve_scale(account, collateral, weights, now)
        mode = "quarantine" if scale is None else "normal"
        base = self._ceilings(account, 0 if scale is None else scale, weights)

        out = {}
        denom = self.shape[0] or 1
        comm = self._comm(account)
        for g in weights:
            h = _key(g)
            top = base[h]
            floor_r = comm.get(h, (0, 0))[0]
            risk_curve = tuple(max((top * self.shape[k]) // denom, floor_r)
                               for k in range(len(self.shape)))
            gross_curve = tuple(r * self.gross_per_risk for r in risk_curve)
            out[g] = Lease(account, self.epoch, gen, h[0], h[1],
                           now + self.ttl, mode, risk_curve, gross_curve)

        auth = self._auth(account)
        rel = self.released.setdefault(account, set())
        for g, lz in out.items():
            h = _key(g)
            # a holder that was terminally released and is now leased again is
            # live once more; leaving it marked released would drop its new
            # ceiling from the accounting
            rel.discard(h)
            prev = auth.get(h)
            if prev and prev[0] > now:
                auth[h] = (max(prev[0], lz.expiry),
                           max(prev[1], lz.risk_amount),
                           max(prev[2], lz.gross_amount))
            else:
                auth[h] = (lz.expiry, lz.risk_amount, lz.gross_amount)

        self.issued[(account, self.epoch, gen)] = {
            g: lz.risk_amount for g, lz in out.items()
        }
        return out, scale

    def advance_epoch(self):
        self.epoch += 1

    def bump_generation(self, account):
        self.generation[account] = self.generation.get(account, 0) + 1
        return self.generation[account]

    def current_generation(self, account):
        return self.generation.get(account, 0)
