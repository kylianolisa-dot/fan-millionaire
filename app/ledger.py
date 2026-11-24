"""Double-entry-style wallet ledger. Every naira movement is a row; balances are cached on users.

`ref` is unique, so posting the same reference twice is rejected. That is what makes
payments, webhooks and payouts idempotent (safe to receive twice).
"""
import sqlite3
import time


class InsufficientFunds(Exception):
    pass


class Duplicate(Exception):
    pass


def post(c, user_id, amount_kobo, type_, ref, match_id=None, note=""):
    """Record a movement inside the caller's transaction. user_id=None is the platform."""
    try:
        c.execute(
            "INSERT INTO ledger(user_id,type,amount_kobo,ref,match_id,note,created_at) VALUES(?,?,?,?,?,?,?)",
            (user_id, type_, int(amount_kobo), ref, match_id, note, int(time.time())),
        )
    except sqlite3.IntegrityError:
        raise Duplicate(ref)
    if user_id is not None:
        try:
            c.execute("UPDATE users SET balance_kobo = balance_kobo + ? WHERE id=?", (int(amount_kobo), user_id))
        except sqlite3.IntegrityError:
            raise InsufficientFunds()


def balance(c, user_id):
    r = c.execute("SELECT COALESCE(SUM(amount_kobo),0) s FROM ledger WHERE user_id=?", (user_id,)).fetchone()
    return r["s"]
