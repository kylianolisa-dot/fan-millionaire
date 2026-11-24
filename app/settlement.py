"""Turns a finished match into a prize plan.

Agreed rules
  Straight win : 3 rounds, 1 winner per round (first correct answer), each wins 15% of the TOTAL pot.
                 Only fans who staked on the winning team play. If the winning team has no stakers,
                 the losing team's stakers play for their own pot instead.
  Draw         : 3 rounds, 1 winner per team per round, the round's 15% shared => 7.5% each.
  Platform     : keeps the remainder (55% by default) plus any prize nobody could claim.
All amounts are integer kobo; rounding dust stays with the platform.
"""
import time

from . import db, ledger, settings

ROUNDS = 3


def outcome_of(home_score, away_score):
    return "home" if home_score > away_score else "away" if away_score > home_score else "draw"


def other(side):
    return "away" if side == "home" else "home"


def plan(home_pot, away_pot, home_n, away_n, outcome, cfg):
    """Pure function: returns {mode, play_side, total, platform_base, slots:[{round_no, side, amount, forced}]}"""
    total = home_pot + away_pot
    n = {"home": home_n, "away": away_n}
    if total <= 0 or home_n + away_n == 0:
        return {"mode": "none", "play_side": None, "total": total, "platform_base": total, "slots": []}

    slots = []
    if outcome in ("home", "away"):
        mode = "win"
        play = outcome if n[outcome] > 0 else other(outcome)   # winning side empty -> losing stakers play
        amount = total * cfg["win_round_bp"] // 10000
        for r in range(1, ROUNDS + 1):
            slots.append({"round_no": r, "side": play, "amount": amount, "forced": None})
    else:
        mode, play = "draw", None
        half = total * (cfg["draw_round_bp"] // 2) // 10000
        full = total * cfg["draw_round_bp"] // 10000
        staked = [s for s in ("home", "away") if n[s] > 0]
        for r in range(1, ROUNDS + 1):
            if len(staked) == 2:
                slots.append({"round_no": r, "side": "home", "amount": half, "forced": None})
                slots.append({"round_no": r, "side": "away", "amount": half, "forced": None})
            elif cfg["draw_one_sided_full_prize"]:
                slots.append({"round_no": r, "side": staked[0], "amount": full, "forced": None})
            else:
                slots.append({"round_no": r, "side": staked[0], "amount": half, "forced": None})
                slots.append({"round_no": r, "side": other(staked[0]), "amount": half,
                              "forced": "no_stakers_on_side"})
    platform_base = total - sum(s["amount"] for s in slots)
    return {"mode": mode, "play_side": play, "total": total, "platform_base": platform_base, "slots": slots}


def pot_stats(c, match_id):
    out = {"home": 0, "away": 0, "home_n": 0, "away_n": 0}
    for r in c.execute("SELECT side, SUM(amount_kobo) amt, COUNT(DISTINCT user_id) n FROM stakes "
                       "WHERE match_id=? GROUP BY side", (match_id,)):
        out[r["side"]] = r["amt"]
        out[r["side"] + "_n"] = r["n"]
    return out


def forfeit_slot(c, slot_id, reason):
    """Prize nobody claimed: it goes to the platform and is recorded in the unclaimed-funds register."""
    s = c.execute("SELECT * FROM slots WHERE id=?", (slot_id,)).fetchone()
    if s is None or s["status"] != "open":
        return
    m = c.execute("SELECT home, away FROM matches WHERE id=?", (s["match_id"],)).fetchone()
    club = m["home"] if s["side"] == "home" else m["away"]
    now = int(time.time())
    c.execute("UPDATE slots SET status='unclaimed', reason=?, resolved_at=? WHERE id=?", (reason, now, slot_id))
    if s["amount_kobo"] > 0:
        ledger.post(c, None, s["amount_kobo"], "unclaimed", f"unclaimed-{slot_id}", s["match_id"], reason)
    c.execute("INSERT INTO unclaimed(match_id,slot_id,round_no,side,club,amount_kobo,reason,created_at) "
              "VALUES(?,?,?,?,?,?,?,?)",
              (s["match_id"], slot_id, s["round_no"], s["side"], club, s["amount_kobo"], reason, now))


class SettleError(Exception):
    pass


def finish_match(match_id, home_score, away_score):
    """Record the final score, freeze the pots, build the prize plan. Returns the plan."""
    cfg = settings.all()
    with db.tx() as c:
        m = c.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
        if m is None:
            raise SettleError("Match not found")
        if m["status"] != "open":
            raise SettleError(f"Match is already {m['status']}")
        outcome = outcome_of(home_score, away_score)
        ps = pot_stats(c, match_id)
        p = plan(ps["home"], ps["away"], ps["home_n"], ps["away_n"], outcome, cfg)
        now = int(time.time())
        start_at = now + cfg["trivia_delay_minutes"] * 60 if p["mode"] != "none" else None
        c.execute("UPDATE matches SET status='finished', home_score=?, away_score=?, outcome=?, trivia_start_at=? "
                  "WHERE id=?", (home_score, away_score, outcome, start_at, match_id))
        c.execute("INSERT INTO games(match_id,mode,play_side,total_pot_kobo,platform_base_kobo,status,finished_at) "
                  "VALUES(?,?,?,?,?,?,?)",
                  (match_id, p["mode"], p["play_side"], p["total"], p["platform_base"],
                   "done" if p["mode"] == "none" else "pending", now if p["mode"] == "none" else None))
        if p["mode"] == "none":
            return p
        if p["platform_base"] > 0:
            ledger.post(c, None, p["platform_base"], "platform_fee", f"fee-{match_id}", match_id,
                        "Platform share of pot")
        for s in p["slots"]:
            cur = c.execute("INSERT INTO slots(match_id,round_no,side,amount_kobo) VALUES(?,?,?,?)",
                            (match_id, s["round_no"], s["side"], s["amount"]))
            if s["forced"]:
                forfeit_slot(c, cur.lastrowid, s["forced"])
        _maybe_close_empty_game(c, match_id)
        return p


def _maybe_close_empty_game(c, match_id):
    """If every slot was already forfeited at settlement there is nothing to play."""
    left = c.execute("SELECT COUNT(*) n FROM slots WHERE match_id=? AND status='open'", (match_id,)).fetchone()["n"]
    if left == 0:
        c.execute("UPDATE games SET status='done', finished_at=? WHERE match_id=?", (int(time.time()), match_id))


def cancel_match(match_id):
    """Postponed/abandoned match: every stake is refunded in full."""
    with db.tx() as c:
        m = c.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
        if m is None:
            raise SettleError("Match not found")
        if m["status"] != "open":
            raise SettleError(f"Match is already {m['status']}")
        for s in c.execute("SELECT * FROM stakes WHERE match_id=?", (match_id,)).fetchall():
            ledger.post(c, s["user_id"], s["amount_kobo"], "refund", f"refund-stake-{s['id']}", match_id,
                        "Match cancelled")
        c.execute("UPDATE matches SET status='cancelled' WHERE id=?", (match_id,))
