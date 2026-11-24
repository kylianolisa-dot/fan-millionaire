"""Passwords, sessions, rate limiting."""
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque

from . import config, db


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32)
    return h.hex(), salt


def check_password(password, pw_hash, salt):
    h, _ = hash_password(password, salt)
    return hmac.compare_digest(h, pw_hash)


def _th(token):
    return hashlib.sha256(token.encode()).hexdigest()


def new_session(user_id):
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    db.run("INSERT INTO sessions(token_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)",
           (_th(token), user_id, now, now + config.SESSION_DAYS * 86400))
    return token


def user_for_token(token):
    if not token:
        return None
    r = db.one("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id "
               "WHERE s.token_hash=? AND s.expires_at>?", (_th(token), int(time.time())))
    return dict(r) if r else None


def end_session(token):
    if token:
        db.run("DELETE FROM sessions WHERE token_hash=?", (_th(token),))


_hits = defaultdict(deque)


def rate_limited(key, limit, per_seconds):
    """True if `key` has exceeded `limit` calls in the last `per_seconds`."""
    now = time.time()
    dq = _hits[key]
    while dq and dq[0] < now - per_seconds:
        dq.popleft()
    if len(dq) >= limit:
        return True
    dq.append(now)
    return False
