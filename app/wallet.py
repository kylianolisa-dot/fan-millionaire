"""Deposits and withdrawals on top of Paystack. Every state change is idempotent."""
import secrets
import time

from . import db, ledger, paystack, settings


class WalletError(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.msg, self.status = msg, status


def _now():
    return int(time.time())


# ------------------------------------------------------------------ deposits
def start_deposit(user, amount_kobo, callback_url):
    if amount_kobo < 10000:
        raise WalletError("Minimum deposit is ₦100")
    if amount_kobo > 500_000_000:
        raise WalletError("Maximum deposit is ₦5,000,000")
    ref = "fm_dep_" + secrets.token_hex(10)
    db.run("INSERT INTO payments(user_id,kind,reference,amount_kobo,status,created_at,updated_at) "
           "VALUES(?,?,?,?,?,?,?)", (user["id"], "deposit", ref, amount_kobo, "pending", _now(), _now()))
    try:
        data = paystack.initialize(user["email"], amount_kobo, ref, callback_url)
    except paystack.PaystackError as e:
        db.run("UPDATE payments SET status='failed', detail=?, updated_at=? WHERE reference=?",
               (str(e)[:200], _now(), ref))
        raise WalletError(f"Payment provider error: {e}", 502)
    return {"reference": ref, "authorization_url": data["authorization_url"]}


def credit_deposit(reference, paid_kobo, currency="NGN"):
    """Credit the wallet ONCE for a confirmed payment. Safe to call from verify, webhook and mock."""
    with db.tx() as c:
        p = c.execute("SELECT * FROM payments WHERE reference=? AND kind='deposit'", (reference,)).fetchone()
        if p is None:
            return None
        if p["status"] == "success":
            return "already"
        if currency != "NGN" or int(paid_kobo) != p["amount_kobo"]:
            c.execute("UPDATE payments SET status='mismatch', detail=?, updated_at=? WHERE id=?",
                      (f"paid {paid_kobo} {currency}, expected {p['amount_kobo']} NGN", _now(), p["id"]))
            return "mismatch"
        c.execute("UPDATE payments SET status='success', updated_at=? WHERE id=?", (_now(), p["id"]))
        try:
            ledger.post(c, p["user_id"], p["amount_kobo"], "deposit", f"dep-{reference}", None, "Paystack deposit")
        except ledger.Duplicate:
            pass
        return "credited"


def verify_deposit(user, reference):
    p = db.one("SELECT * FROM payments WHERE reference=? AND user_id=? AND kind='deposit'", (reference, user["id"]))
    if p is None:
        raise WalletError("Unknown payment", 404)
    if p["status"] == "success":
        return {"status": "success"}
    if paystack.MOCK:
        return {"status": p["status"]}
    try:
        d = paystack.verify(reference)
    except paystack.PaystackError as e:
        raise WalletError(f"Could not verify yet: {e}", 502)
    if d.get("status") == "success":
        credit_deposit(reference, d.get("amount", 0), d.get("currency", "NGN"))
        return {"status": "success"}
    return {"status": d.get("status", "pending")}


# ------------------------------------------------------------------ bank details
def save_bank(user, bank_code, bank_name, account_number):
    account_number = (account_number or "").strip()
    if not (account_number.isdigit() and len(account_number) == 10):
        raise WalletError("Account number must be 10 digits")
    try:
        acct = paystack.resolve_account(account_number, bank_code)
        rec = paystack.create_recipient(acct["account_name"], account_number, bank_code)
    except paystack.PaystackError as e:
        raise WalletError(f"Could not verify that account: {e}", 502)
    db.run("UPDATE users SET bank_code=?, bank_name=?, account_number=?, account_name=?, recipient_code=? "
           "WHERE id=?", (bank_code, bank_name, account_number, acct["account_name"], rec["recipient_code"], user["id"]))
    return {"account_name": acct["account_name"]}


# ------------------------------------------------------------------ withdrawals
def request_withdrawal(user, amount_kobo):
    cfg = settings.all()
    if not user["recipient_code"]:
        raise WalletError("Add and verify your bank account first")
    if amount_kobo < cfg["min_withdraw_kobo"]:
        raise WalletError(f"Minimum withdrawal is ₦{cfg['min_withdraw_kobo'] // 100:,}")
    flagged = db.one("SELECT COUNT(*) n FROM flags WHERE user_id=? AND resolved=0", (user["id"],))["n"] > 0
    needs_approval = bool(cfg["withdrawals_require_approval"]) or flagged
    ref = "wd_" + secrets.token_hex(12)
    try:
        with db.tx() as c:
            ledger.post(c, user["id"], -amount_kobo, "withdrawal", f"wd-{ref}", None, "Withdrawal requested")
            c.execute("INSERT INTO payments(user_id,kind,reference,amount_kobo,status,detail,created_at,updated_at) "
                      "VALUES(?,?,?,?,?,?,?,?)",
                      (user["id"], "withdrawal", ref, amount_kobo,
                       "pending_approval" if needs_approval else "processing",
                       "flagged account" if flagged else None, _now(), _now()))
    except ledger.InsufficientFunds:
        raise WalletError("Insufficient balance")
    if not needs_approval:
        send_transfer(ref)
    return {"reference": ref, "status": "pending_approval" if needs_approval else "processing"}


def _refund_withdrawal(ref, why, allow_success=False):
    with db.tx() as c:
        p = c.execute("SELECT * FROM payments WHERE reference=? AND kind='withdrawal'", (ref,)).fetchone()
        blocked = ("failed", "declined") if allow_success else ("failed", "declined", "success")
        if p is None or p["status"] in blocked:
            return
        c.execute("UPDATE payments SET status=?, detail=?, updated_at=? WHERE id=?",
                  ("declined" if why == "declined by admin" else "failed", why[:200], _now(), p["id"]))
        try:
            ledger.post(c, p["user_id"], p["amount_kobo"], "withdrawal_refund", f"wdrf-{ref}", None, why)
        except ledger.Duplicate:
            pass


def send_transfer(ref):
    """Move an approved withdrawal to Paystack. Blocking: call via asyncio.to_thread from async code."""
    with db.tx() as c:
        n = c.execute("UPDATE payments SET status='processing', updated_at=? WHERE reference=? AND kind='withdrawal' "
                      "AND status IN ('pending_approval','processing')", (_now(), ref)).rowcount
    if not n:
        return
    p = db.one("SELECT p.*, u.recipient_code FROM payments p JOIN users u ON u.id=p.user_id WHERE p.reference=?", (ref,))
    try:
        d = paystack.transfer(p["amount_kobo"], p["recipient_code"], ref, "Fan Millionaire winnings")
    except paystack.PaystackError as e:
        _refund_withdrawal(ref, f"Transfer failed: {e}")
        return
    st = d.get("status")
    if st == "success":
        db.run("UPDATE payments SET status='success', provider_ref=?, updated_at=? WHERE reference=?",
               (d.get("transfer_code"), _now(), ref))
    elif st == "otp":
        # Transfers need an OTP: do NOT refund (it may still complete). Admin must look at it.
        db.run("UPDATE payments SET status='needs_attention', provider_ref=?, detail=?, updated_at=? WHERE reference=?",
               (d.get("transfer_code"), "Paystack asked for an OTP. Disable OTP for transfers in the dashboard.",
                _now(), ref))
    else:
        db.run("UPDATE payments SET provider_ref=?, updated_at=? WHERE reference=?", (d.get("transfer_code"), _now(), ref))


def decline_withdrawal(ref):
    _refund_withdrawal(ref, "declined by admin")


# ------------------------------------------------------------------ webhook
def handle_webhook(event):
    """Process a (signature-verified) Paystack event. Idempotent."""
    kind, d = event.get("event"), event.get("data") or {}
    ref = d.get("reference")
    if not ref:
        return "ignored"
    if kind == "charge.success":
        return credit_deposit(ref, d.get("amount", 0), d.get("currency", "NGN")) or "unknown"
    if kind == "transfer.success":
        db.run("UPDATE payments SET status='success', updated_at=? WHERE reference=? AND kind='withdrawal' "
               "AND status IN ('processing','needs_attention')", (_now(), ref))
        return "ok"
    if kind in ("transfer.failed", "transfer.reversed"):
        _refund_withdrawal(ref, f"Paystack: {kind}", allow_success=(kind == "transfer.reversed"))
        return "ok"
    return "ignored"
