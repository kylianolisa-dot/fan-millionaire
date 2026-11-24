"""Paystack webhook + wallet safety tests (no network needed)."""
import hashlib
import hmac
import json
import unittest

from app import config, db, ledger, paystack, wallet
from tests.test_core import fresh_db, mk_user


def sign(body: bytes, secret="sk_test_secret"):
    return hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()


class WebhookTests(unittest.TestCase):
    def setUp(self):
        fresh_db()
        self._old = config.PAYSTACK_SECRET
        config.PAYSTACK_SECRET = "sk_test_secret"

    def tearDown(self):
        config.PAYSTACK_SECRET = self._old

    def pending_deposit(self, uid, amount=500000, ref="fm_dep_abc"):
        db.run("INSERT INTO payments(user_id,kind,reference,amount_kobo,status,created_at,updated_at) "
               "VALUES(?,?,?,?,?,1,1)", (uid, "deposit", ref, amount, "pending"))
        return ref

    def bal(self, uid):
        return db.one("SELECT balance_kobo b FROM users WHERE id=?", (uid,))["b"]

    def test_signature_checked(self):
        body = b'{"event":"charge.success"}'
        self.assertTrue(paystack.verify_signature(body, sign(body)))
        self.assertFalse(paystack.verify_signature(body, sign(body, "wrong")))
        self.assertFalse(paystack.verify_signature(body, ""))
        self.assertFalse(paystack.verify_signature(body + b" ", sign(body)))     # tampered body

    def test_deposit_credited_once_even_if_webhook_repeats(self):
        u = mk_user("a")
        ref = self.pending_deposit(u["id"])
        ev = {"event": "charge.success", "data": {"reference": ref, "amount": 500000, "currency": "NGN"}}
        self.assertEqual(wallet.handle_webhook(ev), "credited")
        self.assertEqual(wallet.handle_webhook(ev), "already")
        self.assertEqual(self.bal(u["id"]), 500000)

    def test_wrong_amount_or_currency_is_not_credited(self):
        u = mk_user("a")
        ref = self.pending_deposit(u["id"])
        bad = {"event": "charge.success", "data": {"reference": ref, "amount": 100, "currency": "NGN"}}
        self.assertEqual(wallet.handle_webhook(bad), "mismatch")
        self.assertEqual(self.bal(u["id"]), 0)

    def test_unknown_reference_ignored(self):
        self.assertIsNone(wallet.credit_deposit("nope", 100))

    def _withdrawal(self, uid):
        with db.tx() as c:
            ledger.post(c, uid, 300000, "deposit", "seed-dep")
        db.run("UPDATE users SET recipient_code='RCP_x' WHERE id=?", (uid,))
        user = dict(db.one("SELECT * FROM users WHERE id=?", (uid,)))
        return wallet.request_withdrawal(user, 200000)

    def test_withdrawal_is_held_for_approval_and_funds_leave_wallet(self):
        u = mk_user("a")
        w = self._withdrawal(u["id"])
        self.assertEqual(w["status"], "pending_approval")
        self.assertEqual(self.bal(u["id"]), 100000)

    def test_decline_refunds_exactly_once(self):
        u = mk_user("a")
        ref = self._withdrawal(u["id"])["reference"]
        wallet.decline_withdrawal(ref)
        wallet.decline_withdrawal(ref)
        self.assertEqual(self.bal(u["id"]), 300000)

    def test_transfer_failed_webhook_refunds_once_and_reversal_after_success_too(self):
        u = mk_user("a")
        ref = self._withdrawal(u["id"])["reference"]
        wallet.send_transfer(ref)                                                    # mock transfer succeeds
        self.assertEqual(db.one("SELECT status FROM payments WHERE reference=?", (ref,))["status"], "success")
        ev = {"event": "transfer.reversed", "data": {"reference": ref}}
        wallet.handle_webhook(ev)
        wallet.handle_webhook(ev)
        self.assertEqual(self.bal(u["id"]), 300000)                                  # refunded once, not twice

    def test_cannot_overdraw_or_withdraw_without_bank(self):
        u = mk_user("a")
        user = dict(db.one("SELECT * FROM users WHERE id=?", (u["id"],)))
        with self.assertRaises(wallet.WalletError):
            wallet.request_withdrawal(user, 200000)                                  # no bank added
        db.run("UPDATE users SET recipient_code='RCP_x' WHERE id=?", (u["id"],))
        user = dict(db.one("SELECT * FROM users WHERE id=?", (u["id"],)))
        with self.assertRaises(wallet.WalletError):
            wallet.request_withdrawal(user, 200000)                                  # no money
        self.assertEqual(self.bal(u["id"]), 0)


if __name__ == "__main__":
    unittest.main()
