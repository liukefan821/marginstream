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
