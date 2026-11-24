"""Live trivia engine.

Fairness (the server is the only clock that matters)
  * The question is hidden until the server's reveal time; early answers are refused.
  * Every answer is timestamped by the SERVER on arrival. Phone clocks are never trusted.
  * LATENCY FAIRNESS: each connected player is pinged over the live stream. The server measures the
    round-trip time itself (client replies are matched to a server nonce) and keeps the MINIMUM of the
    last few samples. A player's answer time is `arrival - reveal - minRTT` (capped), i.e. their thinking
    time, so a player on a slower network is not beaten by someone with a faster connection.
    Using the minimum means a cheater cannot gain extra credit by delaying their own replies.
  * After the first correct answer we wait a short grace period before closing, so players whose
    packets are still in flight (within the cap) are still ranked correctly.

Anti-cheat
  * One answer per player per attempt, locked on first submit (no guess-spamming 4 options).
  * Only eligible players (staked on the right side, haven't already won this pot) can answer.
  * Answers faster than a human can read the question are silently disqualified and flagged.
  * Shared-device accounts are flagged at staking time (see main.py) and any withdrawal by a flagged user
    needs manual admin approval.
"""
import asyncio
import json
import logging
import random
import secrets
import time
from collections import deque

from . import db, ledger, settings, settlement

log = logging.getLogger("fm.game")
ROUNDS = settlement.ROUNDS


def now_ms():
    return int(time.time() * 1000)


class GameError(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status, self.msg = status, msg


# ---------------------------------------------------------------- live event hub
class Hub:
    def __init__(self):
        self.subs = {}

    def subscribe(self, match_id):
        qu = asyncio.Queue(maxsize=500)
        self.subs.setdefault(match_id, set()).add(qu)
        return qu

    def unsubscribe(self, match_id, qu):
        s = self.subs.get(match_id)
        if s:
            s.discard(qu)
            if not s:
                self.subs.pop(match_id, None)

    def publish(self, match_id, event, data):
        for qu in list(self.subs.get(match_id, ())):
            try:
                qu.put_nowait((event, data))
            except asyncio.QueueFull:
                pass  # a stuck client must never block the game


hub = Hub()
ACTIVE = {}          # match_id -> Game

# ---------------------------------------------------------------- latency measurement
RTT = {}             # user_id -> deque of server-measured round-trip ms
PINGS = {}           # nonce -> (user_id, sent_ms)


def new_ping(uid):
    nonce = secrets.token_hex(6)
    PINGS[nonce] = (uid, now_ms())
    if len(PINGS) > 5000:                      # drop stale entries
        cutoff = now_ms() - 30000
        for k in [k for k, v in PINGS.items() if v[1] < cutoff]:
            PINGS.pop(k, None)
    return nonce


def handle_pong(uid, nonce):
    item = PINGS.pop(nonce, None)
    if not item or item[0] != uid:
        return
    rtt = now_ms() - item[1]
    if 0 <= rtt <= 10000:
        RTT.setdefault(uid, deque(maxlen=8)).append(rtt)


def latency_comp(uid, cap):
    samples = RTT.get(uid)
    return min(min(samples), cap) if samples else 0


def public_name(name):
    parts = (name or "Fan").split()
    return parts[0] + (f" {parts[-1][0]}." if len(parts) > 1 else "")


# ---------------------------------------------------------------- game object
class Game:
    def __init__(self, match_id):
        self.mid = match_id
        self.phase = "starting"
        self.round_no = 0
        self.attempt = 0
        self.round_id = None
        self.sides = []
        self.prizes = []
        self.question = None
        self.correct = None
        self.reveal_ms = 0
        self.ends_ms = 0
        self.eligible = {}       # uid -> side
        self.answers = {}        # uid -> dict
        self.close_event = asyncio.Event()
        self.close_scheduled = False
        self.last_result = None
        self.cfg = settings.all()
        self.task = None

    def public_state(self):
        st = {"phase": self.phase, "round_no": self.round_no, "attempt": self.attempt,
              "round_id": self.round_id, "sides": self.sides, "prizes": self.prizes,
              "reveal_ms": self.reveal_ms, "ends_ms": self.ends_ms,
              "eligible_count": len(self.eligible), "last_result": self.last_result}
        if self.phase in ("open", "closing") and self.question:
            st["question"] = self.question
        return st


def eligible_users(match_id, side):
    won = {r["winner_user_id"] for r in db.q(
        "SELECT winner_user_id FROM slots WHERE match_id=? AND status='won'", (match_id,))}
    return {r["user_id"] for r in db.q(
        "SELECT DISTINCT s.user_id FROM stakes s JOIN users u ON u.id=s.user_id "
        "WHERE s.match_id=? AND s.side=? AND u.status='active'", (match_id, side))} - won


def pick_question(match_id):
    qs = db.rows("SELECT * FROM questions WHERE active=1")
    if not qs:
        raise RuntimeError("No active trivia questions")
    used = {r["question_id"] for r in db.q("SELECT question_id FROM rounds WHERE match_id=?", (match_id,))}
    q = random.choice([x for x in qs if x["id"] not in used] or qs)
    db.run("UPDATE questions SET times_used=times_used+1 WHERE id=?", (q["id"],))
    opts = json.loads(q["options"])
    order = random.sample(range(len(opts)), len(opts))      # shuffle so position can't be memorised
    return {"id": q["id"], "text": q["text"], "options": [opts[i] for i in order],
            "correct": order.index(q["correct_index"])}


# ---------------------------------------------------------------- answering
def submit_answer(uid, match_id, round_id, option):
    """Called from the HTTP handler. Synchronous (no awaits) so it is atomic on the event loop."""
    recv = now_ms()
    g = ACTIVE.get(match_id)
    if not g or g.phase != "open" or g.round_id != round_id:
        raise GameError(409, "This round is not open for answers")
    if recv < g.reveal_ms:
        raise GameError(409, "Question not revealed yet")
    if uid not in g.eligible:
        raise GameError(403, "You are not eligible to answer this round")
    if uid in g.answers:
        raise GameError(409, "You already answered")
    if not isinstance(option, int) or not 0 <= option < len(g.question["options"]):
        raise GameError(400, "Invalid option")
    cap = g.cfg["latency_cap_ms"]
    rtt = latency_comp(uid, cap)
    raw = recv - g.reveal_ms
    adj = max(0, raw - rtt)
    flag = "too_fast" if adj < g.cfg["min_human_ms"] else None
    g.answers[uid] = {"uid": uid, "side": g.eligible[uid], "option": option, "correct": option == g.correct,
                      "recv": recv, "raw": raw, "rtt": rtt, "adj": adj, "flag": flag}
    # close early: everyone answered, or every open side already has a valid correct answer
    if len(g.answers) >= len(g.eligible):
        g.close_event.set()
    elif not g.close_scheduled:
        good = {a["side"] for a in g.answers.values() if a["correct"] and not a["flag"]}
        if set(g.sides) <= good:
            g.close_scheduled = True
            asyncio.get_running_loop().call_later(g.cfg["grace_ms"] / 1000, g.close_event.set)
    return {"ok": True}


# ---------------------------------------------------------------- the round loop
async def _sleep_until(ms):
    d = ms / 1000 - time.time()
    if d > 0:
        await asyncio.sleep(d)


def _result_payload(g, slots, winners_by_slot, forfeited):
    ws = []
    for s in slots:
        w = winners_by_slot.get(s["id"])
        if w:
            ws.append({"side": s["side"], "amount": s["amount_kobo"], "name": w["name"], "ms": w["adj"]})
    return {"round_no": g.round_no, "attempt": g.attempt, "correct_index": g.correct, "winners": ws,
            "unfilled": [s["side"] for s in slots if s["id"] not in winners_by_slot], "forfeited": forfeited}


async def _attempt(g, rn, attempt, slots):
    cfg = g.cfg = settings.all()
    mid = g.mid
    q = pick_question(mid)
    g.round_no, g.attempt = rn, attempt
    g.sides = [s["side"] for s in slots]
    g.prizes = [{"side": s["side"], "amount": s["amount_kobo"]} for s in slots]
    g.eligible = {}
    for side in g.sides:
        for uid in eligible_users(mid, side):
            g.eligible[uid] = side
    g.answers, g.close_event, g.close_scheduled = {}, asyncio.Event(), False
    g.question = {"text": q["text"], "options": q["options"]}
    g.correct = q["correct"]
    g.reveal_ms = now_ms() + cfg["countdown_s"] * 1000
    g.ends_ms = g.reveal_ms + cfg["answer_window_s"] * 1000
    g.last_result = None
    with db.tx() as c:
        g.round_id = c.execute(
            "INSERT INTO rounds(match_id,round_no,attempt,question_id,sides,reveal_at_ms) VALUES(?,?,?,?,?,?)",
            (mid, rn, attempt, q["id"], ",".join(g.sides), g.reveal_ms)).lastrowid
    g.phase = "countdown"
    hub.publish(mid, "intro", {"round_no": rn, "attempt": attempt, "round_id": g.round_id, "sides": g.sides,
                               "prizes": g.prizes, "eligible_count": len(g.eligible),
                               "reveal_ms": g.reveal_ms, "ends_ms": g.ends_ms, "server_ms": now_ms()})
    await _sleep_until(g.reveal_ms)
    g.phase = "open"
    hub.publish(mid, "question", {"round_id": g.round_id, "round_no": rn, "attempt": attempt,
                                  "question": g.question, "reveal_ms": g.reveal_ms, "ends_ms": g.ends_ms,
                                  "sides": g.sides, "prizes": g.prizes, "server_ms": now_ms()})
    try:
        await asyncio.wait_for(g.close_event.wait(), timeout=max(0.1, g.ends_ms / 1000 - time.time()))
    except asyncio.TimeoutError:
        pass
    g.phase = "closing"                       # from here no new answers are accepted

    answers = list(g.answers.values())
    winners_by_slot = {}
    names = {}
    with db.tx() as c:
        c.execute("UPDATE rounds SET closed_at_ms=? WHERE id=?", (now_ms(), g.round_id))
        for a in answers:
            c.execute("INSERT INTO answers(round_id,user_id,side,option_index,correct,recv_ms,raw_ms,rtt_ms,"
                      "adjusted_ms,flag) VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (g.round_id, a["uid"], a["side"], a["option"], int(a["correct"]), a["recv"], a["raw"],
                       a["rtt"], a["adj"], a["flag"]))
            if a["flag"]:
                c.execute("INSERT INTO flags(user_id,match_id,kind,detail,created_at) VALUES(?,?,?,?,?)",
                          (a["uid"], mid, a["flag"],
                           f"round {rn} attempt {attempt}: answered in {a['adj']}ms after latency adjustment",
                           int(time.time())))
        for s in slots:
            cands = sorted((a for a in answers if a["side"] == s["side"] and a["correct"] and not a["flag"]),
                           key=lambda a: (a["adj"], a["recv"], a["uid"]))
            if not cands:
                continue
            w = cands[0]
            changed = c.execute(
                "UPDATE slots SET status='won', winner_user_id=?, answer_ms=?, round_id=?, resolved_at=? "
                "WHERE id=? AND status='open'", (w["uid"], w["adj"], g.round_id, int(time.time()), s["id"])).rowcount
            if changed:
                ledger.post(c, w["uid"], s["amount_kobo"], "prize", f"prize-{s['id']}", mid,
                            f"Round {rn} prize")
                u = c.execute("SELECT name FROM users WHERE id=?", (w["uid"],)).fetchone()
                winners_by_slot[s["id"]] = {"adj": w["adj"], "name": public_name(u["name"])}
    g.last_result = _result_payload(g, slots, winners_by_slot, [])
    g.phase = "result"
    hub.publish(mid, "round_result", g.last_result)


def _forfeit(mid, slot_ids, reason):
    if not slot_ids:
        return
    with db.tx() as c:
        for sid in slot_ids:
            settlement.forfeit_slot(c, sid, reason)


async def _run(g):
    mid = g.mid
    hub.publish(mid, "game_start", {"server_ms": now_ms()})
    await asyncio.sleep(1)
    for rn in range(1, ROUNDS + 1):
        attempt = 0
        while True:
            cfg = settings.all()
            open_slots = db.rows("SELECT * FROM slots WHERE match_id=? AND round_no=? AND status='open'", (mid, rn))
            if not open_slots:
                break
            playable, empty = [], []
            for s in open_slots:
                (playable if eligible_users(mid, s["side"]) else empty).append(s)
            _forfeit(mid, [s["id"] for s in empty], "no_eligible_players")
            if not playable:
                hub.publish(mid, "round_skipped", {"round_no": rn, "reason": "no_eligible_players"})
                break
            if attempt > cfg["max_retakes"]:
                _forfeit(mid, [s["id"] for s in playable], "no_correct_answer")
                hub.publish(mid, "round_skipped", {"round_no": rn, "reason": "no_correct_answer"})
                break
            await _attempt(g, rn, attempt, playable)
            attempt += 1
            await asyncio.sleep(cfg["pause_s"])
    with db.tx() as c:
        c.execute("UPDATE games SET status='done', finished_at=?, error=NULL WHERE match_id=?",
                  (int(time.time()), mid))
    g.phase = "done"
    hub.publish(mid, "game_over", match_summary(mid))


def match_summary(mid):
    wins = db.rows("SELECT s.round_no, s.side, s.amount_kobo, s.answer_ms, u.name FROM slots s "
                   "JOIN users u ON u.id=s.winner_user_id WHERE s.match_id=? AND s.status='won' "
                   "ORDER BY s.round_no, s.side", (mid,))
    for w in wins:
        w["name"] = public_name(w["name"])
    kept = db.one("SELECT COALESCE(SUM(amount_kobo),0) t, COUNT(*) n FROM unclaimed WHERE match_id=?", (mid,))
    return {"winners": wins, "unclaimed_total": kept["t"], "unclaimed_count": kept["n"]}


async def _guarded(g):
    try:
        await _run(g)
    except asyncio.CancelledError:
        raise
    except Exception as e:                         # never leave a game half-running silently
        log.exception("game %s crashed", g.mid)
        db.run("UPDATE games SET status='interrupted', error=? WHERE match_id=?", (str(e)[:300], g.mid))
        hub.publish(g.mid, "game_error", {"message": "Trivia was interrupted. An admin will restart it shortly."})
    finally:
        ACTIVE.pop(g.mid, None)


def start_game(match_id):
    """Start (or resume after interruption) the trivia for a settled match. Returns (ok, message)."""
    if match_id in ACTIVE:
        return False, "Game already running"
    if db.one("SELECT COUNT(*) n FROM questions WHERE active=1")["n"] < ROUNDS:
        db.run("UPDATE games SET status='interrupted', error=? WHERE match_id=? AND status IN ('pending','interrupted')",
               ("Add at least 3 active trivia questions, then start again", match_id))
        return False, "Add at least 3 active trivia questions first"
    with db.tx() as c:
        n = c.execute("UPDATE games SET status='running', started_at=COALESCE(started_at,?), error=NULL "
                      "WHERE match_id=? AND status IN ('pending','interrupted')", (int(time.time()), match_id)).rowcount
    if not n:
        return False, "This match has no game waiting to start"
    g = Game(match_id)
    ACTIVE[match_id] = g
    g.task = asyncio.get_running_loop().create_task(_guarded(g))
    return True, "Started"


async def scheduler():
    """Starts each game when its scheduled time arrives."""
    while True:
        try:
            due = db.q("SELECT g.match_id FROM games g JOIN matches m ON m.id=g.match_id "
                       "WHERE g.status='pending' AND m.trivia_start_at IS NOT NULL AND m.trivia_start_at<=?",
                       (int(time.time()),))
            for r in due:
                start_game(r["match_id"])
        except Exception:
            log.exception("scheduler error")
        await asyncio.sleep(1)


def on_boot():
    db.run("UPDATE games SET status='interrupted', error='Server restarted during trivia' WHERE status='running'")
