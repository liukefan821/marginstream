"""Applying a fill in the right order.

The ordering point decides first. Only a fill it has accepted is folded into
the gateway and the account, so a fill it rejects leaves every component
untouched. Calling the gateway first and the ordering point second, which is
what the experiments used to do, moves state that the authority then refuses.
"""


def execute_fill(sequencer, gateway, account, fill_id, order_id, account_name,
                 symbol, qty, price, fee=0, ledger_key=None):
    """Return (accepted, reason). Nothing moves unless the ordering point
    accepts."""
    ok, why = sequencer.record_fill(fill_id, order_id, qty, price, fee)
    if not ok:
        return False, why
    if why == "idempotent_retry":
        return True, why
    gateway.fill(account_name, order_id, qty)
    if account is not None:
        account.apply_fill(ledger_key or ("fill", fill_id), symbol, qty,
                           price, fee)
    return True, "ok"


def execute_cancel(sequencer, gateway, account_name, order_id):
    ok, why = sequencer.record_cancel(order_id)
    if not ok:
        return False, why
    gateway.cancel_ack(account_name, order_id)
    return True, "ok"


def execute_basket(sequencer, gateway, account, lease_id, seq, basket_id,
                   account_name, legs, terms, after_commit=None):
    """Commit a basket and then fold it. Same order as a fill: the ordering
    point decides, and only what it committed moves.

    `after_commit` is the hook the fault experiment uses to destroy the process
    in the window between the commit landing in the log and the fold happening
    locally. Recovery from the log reapplies it, and `applied_baskets` plus the
    ledger keys make the reapplication land once.
    """
    ok, why = sequencer.commit_basket(gateway.session, lease_id, seq, basket_id,
                                      account_name, legs, terms)
    if not ok:
        return False, why
    if after_commit is not None:
        after_commit()
    gateway.apply_basket(account_name, basket_id, legs)
    if account is not None:
        for i, (symbol, qty, price, fee) in enumerate(legs):
            account.apply_fill(("basket", basket_id, i), symbol, qty, price,
                               fee)
    return True, why
