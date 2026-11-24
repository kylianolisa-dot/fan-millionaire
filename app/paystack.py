"""Paystack client (https://paystack.com/docs/api).

LIVE mode talks to api.paystack.co with your secret key. MOCK mode (no key set) fakes every call so the
whole app can be exercised without internet or a Paystack account. Amounts are kobo, same as Paystack.
"""
import hashlib
import hmac
import secrets

import requests

from . import config

API = "https://api.paystack.co"
MOCK = config.PAYSTACK_MODE != "live"


class PaystackError(Exception):
    pass


def _call(method, path, **kw):
    try:
        r = requests.request(method, API + path, timeout=20,
                             headers={"Authorization": f"Bearer {config.PAYSTACK_SECRET}",
                                      "Content-Type": "application/json"}, **kw)
        body = r.json()
    except (requests.RequestException, ValueError) as e:
        raise PaystackError(f"Could not reach Paystack: {e}")
    if r.status_code >= 400 or not body.get("status"):
        raise PaystackError(body.get("message") or f"Paystack error {r.status_code}")
    return body["data"]


def verify_signature(raw_body: bytes, signature: str) -> bool:
    """Webhook authenticity: HMAC-SHA512 of the raw body with the secret key."""
    if not config.PAYSTACK_SECRET or not signature:
        return False
    expected = hmac.new(config.PAYSTACK_SECRET.encode(), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature)


def initialize(email, amount_kobo, reference, callback_url):
    if MOCK:
        return {"authorization_url": f"{config.APP_URL}/mock-pay.html?reference={reference}", "reference": reference}
    return _call("POST", "/transaction/initialize",
                 json={"email": email, "amount": int(amount_kobo), "reference": reference,
                       "currency": "NGN", "callback_url": callback_url})


def verify(reference):
    """Returns Paystack's transaction data (status, amount, currency, ...)."""
    return _call("GET", f"/transaction/verify/{reference}")


def list_banks():
    if MOCK:
        return [{"name": "Access Bank", "code": "044"}, {"name": "GTBank", "code": "058"},
                {"name": "First Bank", "code": "011"}, {"name": "UBA", "code": "033"},
                {"name": "Zenith Bank", "code": "057"}]
    return [{"name": b["name"], "code": b["code"]} for b in
            _call("GET", "/bank", params={"country": "nigeria", "perPage": 200, "currency": "NGN"})]


def resolve_account(account_number, bank_code):
    if MOCK:
        return {"account_name": "TEST ACCOUNT HOLDER", "account_number": account_number}
    return _call("GET", "/bank/resolve", params={"account_number": account_number, "bank_code": bank_code})


def create_recipient(name, account_number, bank_code):
    if MOCK:
        return {"recipient_code": "RCP_mock_" + secrets.token_hex(4)}
    return _call("POST", "/transferrecipient",
                 json={"type": "nuban", "name": name, "account_number": account_number,
                       "bank_code": bank_code, "currency": "NGN"})


def transfer(amount_kobo, recipient_code, reference, reason):
    """Pay out to a bank account. Final result arrives via webhook (transfer.success / failed / reversed)."""
    if MOCK:
        return {"status": "success", "transfer_code": "TRF_mock_" + secrets.token_hex(4)}
    return _call("POST", "/transfer",
                 json={"source": "balance", "amount": int(amount_kobo), "recipient": recipient_code,
                       "reference": reference, "reason": reason})
