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
                 "expiry", "mode", "risk_curve", "gross_curve", "debit_curve",
                 "lease_id", "credit_version")

    def __init__(self, account, epoch, generation, gateway, incarnation,
                 expiry, mode, risk_curve, gross_curve, debit_curve=None,
                 lease_id=None, credit_version=0):
        self.account = account
        self.epoch = epoch
        self.generation = generation
        self.gateway = gateway
        self.incarnation = incarnation
        self.expiry = expiry
        self.mode = mode                    # "normal" or "quarantine"
        self.risk_curve = tuple(risk_curve)
        self.gross_curve = tuple(gross_curve)
        self.debit_curve = tuple(debit_curve if debit_curve is not None
                                 else (0,) * len(self.risk_curve))
        self.lease_id = lease_id
        self.credit_version = credit_version

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

    def debit_at(self, k):
        return self._at(self.debit_curve, k)

    @property
    def risk_amount(self):
        return self.risk_curve[0]

    @property
    def gross_amount(self):
        return self.gross_curve[0]

    @property
    def debit_amount(self):
        return self.debit_curve[0]


def _key(g):
    return g if isinstance(g, tuple) else (g, 0)


class Allocator:
    def __init__(self, risk, shape=(DECAY_DEN,), ttl=1, gross_per_risk=20,
                 residual=0, sequencer=None):
        """`gross_per_risk` fixes the ratio at which gross ceiling is issued
        alongside risk ceiling. The two resources are checked independently at
        the gateway, but they are not solved for independently: the solver
        moves along one ray through a two-dimensional feasible set. This is two
        independent checks against a fixed-ratio issuance policy, not a
        two-resource allocation."""
        self.risk = risk
        # the ordering point this allocator registers its leases with. It is
        # the single issuer, so it is the only component that knows which
        # account and which holder a lease was cut for.
        self.sequencer = sequencer
        self.shape = tuple(shape)
        self.ttl = ttl
        self.gross_per_risk = gross_per_risk
        ratio = risk.max_debit_ratio()
        # execution cost per unit of risk. a fill lands inside a price band and
        # pays a fee, and both reduce equity after the lease was solved, so the
        # solve has to reserve for them.
        self.debit_num, self.debit_den = ratio if ratio else (0, 1)
        self.residual = residual
        self.epoch = 0
        self.generation = {}
        self.issued = {}
        # account -> lease_id -> (holder, expiry, r_ceil, g_ceil)
        self.authority = {}
        self.committed = {}       # account -> holder -> (r_used, g_used)
        self.watermark = {}       # account -> holder -> highest report seq seen
        self.sealed = {}          # account -> lease_id -> measured usage
        self.sealed_sum = {}      # account -> holder -> summed sealed usage
        self.retired = {}         # account -> set of holders not to be issued to
        self.credit_version = {}  # account -> version, bumped on a credit change
        # lease ids minted for a liquidator. kept apart from `authority`
        # because they are not capacity; see issue_liquidation_lease.
        self.liquidation_leases = {}
        # every lease id ever minted for an account, ingress and liquidator.
        # the barrier is taken over this set, not over what is still in
        # `authority`: a lease that admitted nothing is still authority.
        self.all_leases = {}
        # accounts whose issuance is suspended while a settlement runs
        self.settling = set()
        # the barrier each installed settlement was computed at, and the
        # execution cost that settlement left unreserved
        self.settled_barrier = {}
        self.settled_debit = {}
        self._next_lease_id = 1

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

    def release(self, account, lease_id, seal, sequencer, risk=None):
        """Terminal reconciliation. The only path that lowers exposure.

        Three things have to be true, and a watermark that merely does not go
        backwards is none of them:

        - the lease has been fenced at the ordering point, so it can produce no
          further admissions. A clock comparison does not establish this: a
          partitioned gateway's clock may be behind the allocator's.
        - the seal is the one the ordering point issued for *this* lease, so a
          report about an earlier lease cannot release a later one.
        - the seal covers every admission the ordering point recorded, so the
          usage in the report is not missing any.
        """
        # the figures are computed from the ordering point's own log rather
        # than reported by whoever asks for the release. a correct seal paired
        # with an optimistic usage claim was previously accepted.
        if not sequencer.is_fenced(lease_id):
            return False, "not_fenced"
        truth = sequencer.seal_of(lease_id)
        if seal.lease_id != lease_id:
            return False, "seal_for_another_lease"
        if seal.terminal_seq != truth.terminal_seq:
            return False, "seal_does_not_cover_all_admissions"

        usage = sequencer.reconcile(lease_id, risk if risk is not None
                                    else self.risk)
        sealed = self.sealed.setdefault(account, {})
        if lease_id in sealed:
            # a replay of the same seal restates the same fact
            if sealed[lease_id] == usage:
                return True, "idempotent_replay"
            return False, "conflicting_replay"

        auth = self._auth(account)
        rec = auth.get(lease_id)
        if rec is None:
            return False, "unknown_lease"

        sealed[lease_id] = usage
        h = rec[0]
        # the measured figure covers this lease only. a holder may hold orders
        # admitted under several leases, so the sealed figures are summed;
        # unsealed leases keep contributing their ceilings through _bounds.
        ss = self.sealed_sum.setdefault(account, {})
        pr, pg = ss.get(h, (0, 0))
        ss[h] = (pr + usage[0], pg + usage[1])
        comm = self._comm(account)
        cr, cg = comm.get(h, (0, 0))
        comm[h] = (max(cr, ss[h][0]), max(cg, ss[h][1]))
        del auth[lease_id]
        return True, "released"

    def begin_settling(self, account):
        """Suspend issuance for an account and return the credit version the
        settlement will be installed against.

        Issuance has to stop first. Otherwise a lease minted after the barrier
        was taken is authority the settlement did not account for, and the
        compacted figure would be installed against a set of holders that has
        already changed.
        """
        self.settling.add(account)
        return self.credit_version.get(account, 0)

    def settle(self, account, sequencer, risk=None, if_credit_version=None):
        """Compact per-holder committed exposure into the account's own figure.

        Committed exposure is tracked per holder and the figures are summed.
        While any holder can still admit, that has to be an over-approximation:
        the allocator cannot see what an unreachable holder is doing, so a
        position one holder created cannot be assumed to offset a position
        another one created. After a liquidation the sum is badly wrong in the
        other direction: each lease reconciles to its own gross leg, the
        offsetting legs sit under the liquidator's lease, and an account that
        holds nothing still looks fully occupied.

        A seal is the portable evidence for releasing **one** lease: a holder
        carries it to the allocator. This is the account-wide path and it does
        not use one, because the allocator reads the same authoritative log the
        seal was cut from. A seal that never arrives therefore does not freeze
        the account's capacity. What the safety rests on is not the seal but
        the terminal fence the ordering point has already recorded.

        Every one of these has to hold, and none of them is supplied by the
        caller:

        1. the account is in `settling`, so no new lease can be issued;
        2. every lease ever minted for the account is fenced at the ordering
           point: every ingress lease, every incarnation, and the liquidator's
           basket authority. A liquidator that can still commit a basket is
           live authority like any other;
        3. a barrier watermark `B` is established, at which the recorded
           sequence under each of those leases is gap-free and no admission or
           basket for the account was recorded under any lease outside the set;
        4. `Sequencer.reconcile_account(B)` rebuilds, from the log alone, the
           filled positions, the orders with no authoritative cancel
           acknowledgement, the worst-fill risk, the repriced gross reach, and
           the execution cost still ahead of the account;
        5. the result is installed under a credit-version compare-and-set, so a
           settlement computed at an older barrier cannot overwrite a newer
           one;
        6. issuance resumes only after the install.

        No occupancy figure is accepted as an argument. An earlier version of
        this method took one, which is the interface defect the worst-fill
        round closed in `release`: a correct fence paired with an optimistic
        usage claim would have been accepted.
        """
        if account not in self.settling:
            return False, "not_settling"
        if (if_credit_version is not None
                and if_credit_version != self.credit_version.get(account, 0)):
            return False, "credit_version_moved"

        leases = self.all_leases.get(account, set())
        ok, barrier, why = sequencer.barrier(account, leases)
        if not ok:
            return False, why

        prior = self.settled_barrier.get(account)
        if prior is not None and barrier < prior:
            return False, f"stale_barrier:{barrier}<{prior}"

        r, g, d = sequencer.reconcile_account(
            account, risk if risk is not None else self.risk, barrier=barrier)

        auth = self._auth(account)
        for lid in list(auth):
            del auth[lid]
        holder = ("settled", 0)
        self.committed[account] = {holder: (r, g)}
        self.sealed_sum[account] = {holder: (r, g)}
        self.watermark[account] = {}
        self.settled_debit[account] = d
        self.settled_barrier[account] = barrier
        self.credit_version[account] = self.credit_version.get(account, 0) + 1
        self.settling.discard(account)
        return True, f"settled at barrier {barrier}: risk {r}, gross {g}, debit {d}"

    def retire(self, account, gateway):
        """Stop issuing to a holder.

        This does not take back a lease inside its term: a retired process may
        be partitioned rather than stopped, and its authority stands until the
        lease is fenced and sealed.
        """
        self.retired.setdefault(account, set()).add(_key(gateway))

    def activate(self, account, gateway):
        """Undo a retirement. A restarted process should come back as a new
        incarnation rather than through this."""
        self.retired.setdefault(account, set()).discard(_key(gateway))

    def bump_credit_version(self, account):
        v = self.credit_version.get(account, 0) + 1
        self.credit_version[account] = v
        return v

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

        # the largest ceiling still outstanding at each holder. a gateway
        # installs one lease per account, so two unsealed leases at one holder
        # are two candidates for the one it is actually using; different
        # incarnations are different holders and add up.
        held = {}
        for _lid, (h, _expiry, r_ceil, g_ceil) in auth.items():
            hr, hg = held.get(h, (0, 0))
            held[h] = (max(hr, r_ceil), max(hg, g_ceil))

        risk_total = gross_total = 0
        for h in set(held) | set(comm) | set(ceilings):
            new_r = ceilings.get(h, 0)
            new_g = new_r * self.gross_per_risk
            cr, cg = comm.get(h, (0, 0))
            hr, hg = held.get(h, (0, 0))
            risk_total += max(new_r, hr, cr)
            gross_total += max(new_g, hg, cg)
        return risk_total, gross_total

    def debit_of(self, risk_amount):
        if self.debit_num == 0:
            return 0
        return -((-risk_amount * self.debit_num) // self.debit_den)

    def _feasible(self, account, scale, weights, collateral, now):
        ceilings = self._ceilings(account, scale, weights)
        risk_total, gross_total = self._bounds(account, ceilings, now)
        den = self.risk.A_den()
        lhs = ((2 * risk_total + self.debit_of(risk_total) + self.residual
                + self.settled_debit.get(account, 0))
               * den + self.risk.A_num(gross_total))
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
        if account in self.settling:
            return {}, None
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
        retired = self.retired.get(account, set())
        cv = self.credit_version.get(account, 0)
        for g in weights:
            h = _key(g)
            if h in retired:
                continue
            top = base[h]
            floor_r = comm.get(h, (0, 0))[0]
            risk_curve = tuple(max((top * self.shape[k]) // denom, floor_r)
                               for k in range(len(self.shape)))
            gross_curve = tuple(r * self.gross_per_risk for r in risk_curve)
            debit_curve = tuple(self.debit_of(r) for r in risk_curve)
            lid = self._next_lease_id
            self._next_lease_id += 1
            self.all_leases.setdefault(account, set()).add(lid)
            if self.sequencer is not None:
                self.sequencer.register_lease(lid, account, h, "ingress")
            out[g] = Lease(account, self.epoch, gen, h[0], h[1],
                           now + self.ttl, mode, risk_curve, gross_curve,
                           debit_curve, lease_id=lid, credit_version=cv)

        auth = self._auth(account)
        for g, lz in out.items():
            auth[lz.lease_id] = (_key(g), lz.expiry, lz.risk_amount,
                                 lz.gross_amount)

        self.issued[(account, self.epoch, gen)] = {
            g: lz.risk_amount for g, lz in out.items()
        }
        return out, scale

    def issue_liquidation_lease(self, account, gateway, incarnation=0, now=0,
                                ttl=10 ** 9):
        """A lease for the liquidator.

        Its ceilings are not solved for, and it is deliberately kept out of
        `authority`, so `_bounds` does not count it. The justification is that
        the liquidator's orders are checked against the merged account and
        refused unless the merged scenario vector and the merged gross both
        fail to rise, so nothing it admits can raise the account's exposure.
        Everything the capacity accounting does is about bounding an increase.

        The price of that is stated rather than mitigated: the liquidator is a
        trusted component. A compromised one can trade the account subject only
        to the non-increase check, which permits arbitrary churn and therefore
        arbitrary execution cost.
        """
        cap = 1 << 62
        lid = self._next_lease_id
        self._next_lease_id += 1
        gen = self.generation.get(account, 0)
        lz = Lease(account, self.epoch, gen, gateway, incarnation,
                   now + ttl, "normal", (cap,), (cap,), (cap,),
                   lease_id=lid, credit_version=self.credit_version.get(account, 0))
        self.liquidation_leases.setdefault(account, []).append(lid)
        self.all_leases.setdefault(account, set()).add(lid)
        if self.sequencer is not None:
            self.sequencer.register_lease(lid, account, (gateway, incarnation),
                                          "liquidation")
        return lz

    def advance_epoch(self):
        self.epoch += 1

    def bump_generation(self, account):
        self.generation[account] = self.generation.get(account, 0) + 1
        return self.generation[account]

    def current_generation(self, account):
        return self.generation.get(account, 0)
