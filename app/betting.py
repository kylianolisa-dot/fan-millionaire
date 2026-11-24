"""Staking: put money into a club's pot."""
import secrets
import time

from . import db, ledger, settings


class StakeError(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.msg, self.status = msg, status


def place_stake(user, match_id, side, amount_kobo):
    if side not in ("home", "away"):
        raise StakeError("Pick a club to back")
    if not isinstance(amount_kobo, int) or amount_kobo <= 0:
        raise StakeError("Enter a valid amount")
    cfg = settings.all()
    now = int(time.time())
    with db.tx() as c:
        m = c.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
        if m is None:
            raise StakeError("Match not found", 404)
        if m["status"] != "open" or now >= m["staking_closes_at"]:
            raise StakeError("Staking is closed for this match", 409)
        u = c.execute("SELECT status, device_id FROM users WHERE id=?", (user["id"],)).fetchone()
        if u["status"] != "active":
            raise StakeError("Your account is suspended", 403)
        mine = c.execute("SELECT side, SUM(amount_kobo) amt FROM stakes WHERE match_id=? AND user_id=? GROUP BY side",
                         (match_id, user["id"])).fetchall()
        if any(r["side"] != side for r in mine):
            raise StakeError("You already backed the other club in this match", 409)
        existing = sum(r["amt"] for r in mine)
        if amount_kobo < cfg["min_stake_kobo"]:
            raise StakeError(f"Minimum stake is ₦{cfg['min_stake_kobo'] // 100:,}")
        if existing + amount_kobo > cfg["max_stake_kobo"]:
            raise StakeError(f"Maximum total stake per match is ₦{cfg['max_stake_kobo'] // 100:,}")
        try:
            ledger.post(c, user["id"], -amount_kobo, "stake", f"stake-{secrets.token_hex(8)}", match_id,
                        f"Stake on {m['home'] if side == 'home' else m['away']}")
        except ledger.InsufficientFunds:
            raise StakeError("Insufficient wallet balance. Deposit first.", 402)
        c.execute("INSERT INTO stakes(match_id,user_id,side,amount_kobo,created_at) VALUES(?,?,?,?,?)",
                  (match_id, user["id"], side, amount_kobo, now))
        if u["device_id"]:       # anti-cheat: several accounts on one device in the same pot
            others = c.execute(
                "SELECT DISTINCT s.user_id FROM stakes s JOIN users x ON x.id=s.user_id "
                "WHERE s.match_id=? AND s.user_id!=? AND x.device_id=?", (match_id, user["id"], u["device_id"])).fetchall()
            for o in others:
                lo, hi = sorted((user["id"], o["user_id"]))
                dup = c.execute("SELECT 1 FROM flags WHERE match_id=? AND kind='shared_device' AND detail=?",
                                (match_id, f"users {lo} and {hi}")).fetchone()
                if not dup:
                    for uid in (lo, hi):
                        c.execute("INSERT INTO flags(user_id,match_id,kind,detail,created_at) VALUES(?,?,?,?,?)",
                                  (uid, match_id, "shared_device", f"users {lo} and {hi}", now))
    return pots(match_id)


def pots(match_id):
    out = {"home": 0, "away": 0, "home_fans": 0, "away_fans": 0}
    for r in db.q("SELECT side, SUM(amount_kobo) amt, COUNT(DISTINCT user_id) n FROM stakes WHERE match_id=? "
                  "GROUP BY side", (match_id,)):
        out[r["side"]] = r["amt"]
        out[r["side"] + "_fans"] = r["n"]
    out["total"] = out["home"] + out["away"]
    return out
