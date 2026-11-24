"""Fan Millionaire web server (Starlette). Run:  uvicorn app.main:app --port 8000"""
import asyncio
import contextlib
import inspect
import json
import logging
import re
import secrets
import time
from urllib.parse import urlparse

from sse_starlette import EventSourceResponse
from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import betting, config, content, db, feed, game, ledger, paystack, security, seed, settings, settlement, wallet

log = logging.getLogger("fm")
ROUTES = []


class ApiError(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status, self.msg = status, msg


# ------------------------------------------------------------------ plumbing
def client_ip(request):
    """Behind a reverse proxy set TRUST_PROXY=1: we then use the address the proxy appended (the LAST
    X-Forwarded-For entry; earlier entries can be forged by the client)."""
    if config.TRUST_PROXY:
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            return xff.split(",")[-1].strip()
    return request.client.host if request.client else "?"


def csrf(request):
    """State-changing calls must be JSON from our own origin (browsers can't forge that cross-site)."""
    origin = request.headers.get("origin")
    if origin and urlparse(origin).netloc != request.headers.get("host", ""):
        raise ApiError(403, "Cross-site request blocked")
    if not request.headers.get("content-type", "").startswith("application/json"):
        raise ApiError(415, "Send JSON")


def current_user(request):
    u = security.user_for_token(request.cookies.get("fm_session"))
    return u if u and u["status"] == "active" else None


def api(path, methods=("GET",), auth=None, check_csrf=True):
    """auth: None = optional login, 'user' = login required, 'admin' = admin only."""
    def deco(fn):
        async def handler(request):
            try:
                if check_csrf and request.method not in ("GET", "HEAD"):
                    csrf(request)
                user = current_user(request)
                if auth and not user:
                    raise ApiError(401, "Please log in")
                if auth == "admin" and user["role"] != "admin":
                    raise ApiError(403, "Admins only")
                res = fn(request, user)
                if inspect.isawaitable(res):
                    res = await res
                return res if isinstance(res, Response) else JSONResponse(res)
            except ApiError as e:
                return JSONResponse({"error": e.msg}, status_code=e.status)
            except (betting.StakeError, wallet.WalletError, game.GameError, settlement.SettleError) as e:
                return JSONResponse({"error": e.msg if hasattr(e, "msg") else str(e)},
                                    status_code=getattr(e, "status", 400))
            except Exception:
                log.exception("unhandled error on %s", request.url.path)
                return JSONResponse({"error": "Something went wrong. Please try again."}, status_code=500)
        ROUTES.append(Route(path, handler, methods=list(methods)))
        return fn
    return deco


async def body(request):
    try:
        d = await request.json()
    except Exception:
        raise ApiError(400, "Invalid request")
    if not isinstance(d, dict):
        raise ApiError(400, "Invalid request")
    return d


def as_int(v, name="value"):
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise ApiError(400, f"{name} must be a number")
    try:
        f = float(v)
    except ValueError:
        raise ApiError(400, f"{name} must be a number")
    if f != int(f):
        raise ApiError(400, f"{name} must be a whole number")
    return int(f)


def text(d, key, lo, hi, label=None):
    v = d.get(key)
    if not isinstance(v, str) or not lo <= len(v.strip()) <= hi:
        raise ApiError(400, f"{label or key} must be {lo}-{hi} characters")
    return v.strip()


def public_user(u):
    return {"id": u["id"], "name": u["name"], "email": u["email"], "phone": u["phone"], "role": u["role"],
            "balance_kobo": u["balance_kobo"], "bank_name": u["bank_name"], "account_number": u["account_number"],
            "account_name": u["account_name"], "has_bank": bool(u["recipient_code"]), "created_at": u["created_at"]}


def fresh(user):
    return dict(db.one("SELECT * FROM users WHERE id=?", (user["id"],)))


# ------------------------------------------------------------------ auth
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def set_cookie(resp, token):
    resp.set_cookie("fm_session", token, max_age=config.SESSION_DAYS * 86400, httponly=True,
                    samesite="lax", secure=config.COOKIE_SECURE, path="/")


@api("/api/signup", ["POST"])
async def signup(request, user):
    if security.rate_limited("signup:" + client_ip(request), 30, 600):
        raise ApiError(429, "Too many attempts. Try again later.")
    d = await body(request)
    name = text(d, "name", 2, 60, "Name")
    email = text(d, "email", 5, 120, "Email").lower()
    phone = re.sub(r"[\s\-+]", "", str(d.get("phone", "")))
    pw = d.get("password", "")
    if not EMAIL.match(email):
        raise ApiError(400, "Enter a valid email")
    if not (phone.isdigit() and 10 <= len(phone) <= 15):
        raise ApiError(400, "Enter a valid phone number")
    if not isinstance(pw, str) or len(pw) < 8:
        raise ApiError(400, "Password must be at least 8 characters")
    if d.get("age_confirmed") is not True:
        raise ApiError(400, "You must confirm that you are 18 or older")
    device = str(d.get("device_id", ""))[:64]
    device = device if re.fullmatch(r"[A-Za-z0-9\-]{8,64}", device) else None
    h, salt = security.hash_password(pw)
    try:
        uid = db.run("INSERT INTO users(email,phone,name,pw_hash,pw_salt,device_id,signup_ip,age_confirmed,created_at) "
                     "VALUES(?,?,?,?,?,?,?,?,?)", (email, phone, name, h, salt, device, client_ip(request), 1, int(time.time())))
    except Exception:
        raise ApiError(409, "That email or phone number is already registered")
    resp = JSONResponse({"ok": True})
    set_cookie(resp, security.new_session(uid))
    return resp


@api("/api/login", ["POST"])
async def login(request, user):
    d = await body(request)
    email = str(d.get("email", "")).strip().lower()
    if security.rate_limited(f"login:{client_ip(request)}:{email}", 8, 300):
        raise ApiError(429, "Too many attempts. Wait a few minutes.")
    r = db.one("SELECT * FROM users WHERE email=?", (email,))
    if not r or not security.check_password(str(d.get("password", "")), r["pw_hash"], r["pw_salt"]):
        raise ApiError(401, "Wrong email or password")
    if r["status"] != "active":
        raise ApiError(403, "This account is suspended")
    device = str(d.get("device_id", ""))
    if re.fullmatch(r"[A-Za-z0-9\-]{8,64}", device) and not r["device_id"]:
        db.run("UPDATE users SET device_id=? WHERE id=?", (device, r["id"]))
    resp = JSONResponse({"ok": True, "role": r["role"]})
    set_cookie(resp, security.new_session(r["id"]))
    return resp


@api("/api/logout", ["POST"])
def logout(request, user):
    security.end_session(request.cookies.get("fm_session"))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("fm_session", path="/")
    return resp


@api("/api/me")
def me(request, user):
    return {"user": public_user(fresh(user)) if user else None}


@api("/api/me", ["PUT"], auth="user")
async def update_me(request, user):
    d = await body(request)
    name = text(d, "name", 2, 60, "Name")
    phone = re.sub(r"[\s\-+]", "", str(d.get("phone", "")))
    if not (phone.isdigit() and 10 <= len(phone) <= 15):
        raise ApiError(400, "Enter a valid phone number")
    try:
        db.run("UPDATE users SET name=?, phone=? WHERE id=?", (name, phone, user["id"]))
    except Exception:
        raise ApiError(409, "That phone number is already registered")
    return {"ok": True}


@api("/api/me/history", auth="user")
def my_history(request, user):
    stakes = db.rows("SELECT s.match_id, m.home, m.away, m.status, m.outcome, s.side, SUM(s.amount_kobo) amount_kobo "
                     "FROM stakes s JOIN matches m ON m.id=s.match_id WHERE s.user_id=? GROUP BY s.match_id, s.side "
                     "ORDER BY s.match_id DESC LIMIT 50", (user["id"],))
    wins = db.rows("SELECT sl.match_id, m.home, m.away, sl.round_no, sl.amount_kobo, sl.answer_ms FROM slots sl "
                   "JOIN matches m ON m.id=sl.match_id WHERE sl.winner_user_id=? ORDER BY sl.id DESC LIMIT 50",
                   (user["id"],))
    return {"stakes": stakes, "wins": wins}


# ------------------------------------------------------------------ public content & matches
@api("/api/content")
def get_content(request, user):
    return content.get_all()


@api("/api/time")
def server_time(request, user):
    return {"ms": game.now_ms()}


def match_core(m):
    return {k: m[k] for k in ("id", "home", "away", "competition", "kickoff_at", "staking_closes_at", "status",
                              "home_score", "away_score", "outcome", "trivia_start_at")}


def my_state(mid, uid):
    st = db.one("SELECT side, SUM(amount_kobo) amt FROM stakes WHERE match_id=? AND user_id=? GROUP BY side", (mid, uid))
    if not st:
        return {"side": None, "staked_kobo": 0, "can_play": False}
    open_sides = {r["side"] for r in db.q("SELECT side FROM slots WHERE match_id=? AND status='open'", (mid,))}
    won = db.one("SELECT 1 FROM slots WHERE match_id=? AND winner_user_id=? AND status='won'", (mid, uid))
    return {"side": st["side"], "staked_kobo": st["amt"], "can_play": st["side"] in open_sides and not won}


def match_view(m, uid=None):
    d = match_core(m)
    now = int(time.time())
    d["staking_open"] = m["status"] == "open" and now < m["staking_closes_at"]
    d["pots"] = betting.pots(m["id"])
    g = db.one("SELECT mode, play_side, status, error FROM games WHERE match_id=?", (m["id"],))
    d["game"] = dict(g) if g else None
    d["winners"] = []
    d["unclaimed_total_kobo"] = 0
    if g:
        s = game.match_summary(m["id"])
        d["winners"], d["unclaimed_total_kobo"] = s["winners"], s["unclaimed_total"]
        d["slots_open"] = db.one("SELECT COUNT(*) n FROM slots WHERE match_id=? AND status='open'", (m["id"],))["n"]
        d["slot_amount_kobo"] = db.one("SELECT COALESCE(MAX(amount_kobo),0) a FROM slots WHERE match_id=?", (m["id"],))["a"]
    d["me"] = my_state(m["id"], uid) if uid else None
    return d


@api("/api/matches")
def list_matches(request, user):
    uid = user["id"] if user else None
    open_ = db.q("SELECT * FROM matches WHERE status='open' ORDER BY kickoff_at ASC LIMIT 40")
    done = db.q("SELECT * FROM matches WHERE status='finished' ORDER BY kickoff_at DESC LIMIT 20")
    return {"open": [match_view(m, uid) for m in open_], "finished": [match_view(m, uid) for m in done]}


def get_match(mid):
    m = db.one("SELECT * FROM matches WHERE id=?", (mid,))
    if not m:
        raise ApiError(404, "Match not found")
    return m


@api("/api/matches/{mid:int}")
def one_match(request, user):
    return match_view(get_match(request.path_params["mid"]), user["id"] if user else None)


@api("/api/matches/{mid:int}/stake", ["POST"], auth="user")
async def stake(request, user):
    mid = request.path_params["mid"]
    d = await body(request)
    if security.rate_limited(f"stake:{user['id']}", 20, 60):
        raise ApiError(429, "Slow down")
    pots = betting.place_stake(user, mid, d.get("side"), as_int(d.get("amount_kobo"), "amount"))
    game.hub.publish(mid, "pots", pots)
    return {"ok": True, "pots": pots, "balance_kobo": fresh(user)["balance_kobo"]}


# ------------------------------------------------------------------ live trivia
def snapshot(mid, user):
    g = game.ACTIVE.get(mid)
    return {"server_ms": game.now_ms(), "match": match_view(get_match(mid), user["id"] if user else None),
            "state": g.public_state() if g else None,
            "answered": bool(g and user and user["id"] in g.answers)}


@api("/api/matches/{mid:int}/stream")
async def stream(request, user):
    mid = request.path_params["mid"]
    get_match(mid)
    qu = game.hub.subscribe(mid)

    async def gen():
        try:
            yield {"event": "snapshot", "data": json.dumps(snapshot(mid, user))}
            sent, nxt = 0, time.time() + 0.2
            while True:
                try:
                    ev, data = await asyncio.wait_for(qu.get(), max(0.05, nxt - time.time()))
                    yield {"event": ev, "data": json.dumps(data)}
                except asyncio.TimeoutError:
                    if user:       # latency probe: client must echo the nonce straight back
                        yield {"event": "ping", "data": json.dumps({"nonce": game.new_ping(user["id"])})}
                    sent += 1
                    nxt = time.time() + (0.4 if sent < 4 else 4)
        finally:
            game.hub.unsubscribe(mid, qu)

    return EventSourceResponse(gen(), ping=20, headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"})


@api("/api/pong", ["POST"], auth="user")
async def pong(request, user):
    d = await body(request)
    game.handle_pong(user["id"], str(d.get("nonce", "")))
    return {"ok": True}


@api("/api/answer", ["POST"], auth="user")
async def answer(request, user):
    d = await body(request)
    if security.rate_limited(f"answer:{user['id']}", 30, 60):
        raise ApiError(429, "Slow down")
    return game.submit_answer(user["id"], as_int(d.get("match_id"), "match"), as_int(d.get("round_id"), "round"),
                              d.get("option") if isinstance(d.get("option"), int) else -1)


# ------------------------------------------------------------------ wallet
@api("/api/wallet", auth="user")
def wallet_info(request, user):
    u = fresh(user)
    return {"balance_kobo": u["balance_kobo"], "bank": public_user(u),
            "ledger": db.rows("SELECT type, amount_kobo, note, created_at FROM ledger WHERE user_id=? "
                              "ORDER BY id DESC LIMIT 40", (u["id"],)),
            "payments": db.rows("SELECT kind, reference, amount_kobo, status, created_at FROM payments "
                                "WHERE user_id=? ORDER BY id DESC LIMIT 20", (u["id"],)),
            "min_withdraw_kobo": settings.get("min_withdraw_kobo"), "mock": paystack.MOCK}


@api("/api/wallet/deposit", ["POST"], auth="user")
async def deposit(request, user):
    d = await body(request)
    return await asyncio.to_thread(wallet.start_deposit, fresh(user), as_int(d.get("amount_kobo"), "amount"),
                                   f"{config.APP_URL}/pay-return.html")


@api("/api/wallet/verify", ["POST"], auth="user")
async def verify(request, user):
    d = await body(request)
    return await asyncio.to_thread(wallet.verify_deposit, user, str(d.get("reference", "")))


@api("/api/mock/pay", ["POST"], auth="user")
async def mock_pay(request, user):
    """Test-mode checkout. Disabled whenever real Paystack is configured."""
    if not paystack.MOCK:
        raise ApiError(404, "Not found")
    d = await body(request)
    ref = str(d.get("reference", ""))
    p = db.one("SELECT * FROM payments WHERE reference=? AND user_id=? AND kind='deposit'", (ref, user["id"]))
    if not p:
        raise ApiError(404, "Unknown payment")
    return {"result": wallet.credit_deposit(ref, p["amount_kobo"])}


@api("/api/banks")
async def banks(request, user):
    return {"banks": await asyncio.to_thread(paystack.list_banks)}


@api("/api/wallet/bank", ["POST"], auth="user")
async def save_bank(request, user):
    d = await body(request)
    return await asyncio.to_thread(wallet.save_bank, user, str(d.get("bank_code", "")), str(d.get("bank_name", ""))[:80],
                                   str(d.get("account_number", "")))


@api("/api/wallet/withdraw", ["POST"], auth="user")
async def withdraw(request, user):
    d = await body(request)
    if security.rate_limited(f"wd:{user['id']}", 5, 3600):
        raise ApiError(429, "Too many withdrawal requests")
    return await asyncio.to_thread(wallet.request_withdrawal, fresh(user), as_int(d.get("amount_kobo"), "amount"))


@api("/api/paystack/webhook", ["POST"], check_csrf=False)
async def paystack_webhook(request, user):
    raw = await request.body()
    if not paystack.verify_signature(raw, request.headers.get("x-paystack-signature", "")):
        return JSONResponse({"error": "bad signature"}, status_code=401)
    try:
        event = json.loads(raw)
    except ValueError:
        return JSONResponse({"error": "bad json"}, status_code=400)
    await asyncio.to_thread(wallet.handle_webhook, event)
    return {"ok": True}


# ------------------------------------------------------------------ admin
def money_sum(sql, args=()):
    return db.one(sql, args)["t"] or 0


@api("/api/admin/dashboard", auth="admin")
def a_dashboard(request, user):
    return {
        "users": db.one("SELECT COUNT(*) n FROM users WHERE role='user'")["n"],
        "deposits_kobo": money_sum("SELECT SUM(amount_kobo) t FROM ledger WHERE type='deposit'"),
        "staked_kobo": money_sum("SELECT SUM(amount_kobo) t FROM stakes"),
        "prizes_paid_kobo": money_sum("SELECT SUM(amount_kobo) t FROM ledger WHERE type='prize'"),
        "platform_fee_kobo": money_sum("SELECT SUM(amount_kobo) t FROM ledger WHERE type='platform_fee'"),
        "unclaimed_kobo": money_sum("SELECT SUM(amount_kobo) t FROM ledger WHERE type='unclaimed'"),
        "platform_total_kobo": money_sum("SELECT SUM(amount_kobo) t FROM ledger WHERE user_id IS NULL"),
        "wallet_liability_kobo": money_sum("SELECT SUM(balance_kobo) t FROM users"),
        "pending_withdrawals": db.rows("SELECT COUNT(*) n, COALESCE(SUM(amount_kobo),0) t FROM payments "
                                       "WHERE kind='withdrawal' AND status IN ('pending_approval','needs_attention')")[0],
        "open_matches": db.one("SELECT COUNT(*) n FROM matches WHERE status='open'")["n"],
        "running_games": len(game.ACTIVE),
        "open_flags": db.one("SELECT COUNT(*) n FROM flags WHERE resolved=0")["n"],
        "mock_payments": paystack.MOCK,
    }


def parse_match_fields(d, existing=None):
    home = text(d, "home", 2, 40, "Home team")
    away = text(d, "away", 2, 40, "Away team")
    if home.lower() == away.lower():
        raise ApiError(400, "Teams must be different")
    kickoff = as_int(d.get("kickoff_at"), "kickoff")
    closes = as_int(d.get("staking_closes_at") or kickoff, "staking close")
    if closes > kickoff + 6 * 3600:
        raise ApiError(400, "Staking must close within 6 hours after kickoff")
    return home, away, str(d.get("competition", ""))[:60], kickoff, closes


@api("/api/admin/matches", auth="admin")
def a_matches(request, user):
    out = []
    for m in db.q("SELECT * FROM matches ORDER BY kickoff_at DESC LIMIT 200"):
        v = match_view(m)
        v["stakers"] = db.one("SELECT COUNT(DISTINCT user_id) n FROM stakes WHERE match_id=?", (m["id"],))["n"]
        out.append(v)
    return {"matches": out}


@api("/api/admin/matches", ["POST"], auth="admin")
async def a_create_match(request, user):
    home, away, comp, kickoff, closes = parse_match_fields(await body(request))
    mid = db.run("INSERT INTO matches(home,away,competition,kickoff_at,staking_closes_at,created_at) VALUES(?,?,?,?,?,?)",
                 (home, away, comp, kickoff, closes, int(time.time())))
    return {"id": mid}


@api("/api/admin/matches/{mid:int}", ["PUT"], auth="admin")
async def a_edit_match(request, user):
    mid = request.path_params["mid"]
    m = get_match(mid)
    if m["status"] != "open":
        raise ApiError(409, "Only open matches can be edited")
    home, away, comp, kickoff, closes = parse_match_fields(await body(request))
    if (home, away) != (m["home"], m["away"]) and db.one("SELECT 1 FROM stakes WHERE match_id=?", (mid,)):
        raise ApiError(409, "Team names can't change once fans have staked")
    db.run("UPDATE matches SET home=?, away=?, competition=?, kickoff_at=?, staking_closes_at=? WHERE id=?",
           (home, away, comp, kickoff, closes, mid))
    return {"ok": True}


@api("/api/admin/matches/{mid:int}/finish", ["POST"], auth="admin")
async def a_finish(request, user):
    d = await body(request)
    hs, as_ = as_int(d.get("home_score"), "home score"), as_int(d.get("away_score"), "away score")
    if not (0 <= hs <= 99 and 0 <= as_ <= 99):
        raise ApiError(400, "Scores must be between 0 and 99")
    mid = request.path_params["mid"]
    p = settlement.finish_match(mid, hs, as_)
    game.hub.publish(mid, "match_finished", {"outcome": settlement.outcome_of(hs, as_)})
    return {"ok": True, "mode": p["mode"], "platform_base_kobo": p["platform_base"]}


@api("/api/admin/matches/{mid:int}/cancel", ["POST"], auth="admin")
def a_cancel(request, user):
    settlement.cancel_match(request.path_params["mid"])
    return {"ok": True}


@api("/api/admin/matches/{mid:int}/trivia", ["POST"], auth="admin")
async def a_trivia(request, user):
    """action: start_now | schedule (needs start_at) | resume"""
    mid = request.path_params["mid"]
    d = await body(request)
    g = db.one("SELECT status FROM games WHERE match_id=?", (mid,))
    if not g:
        raise ApiError(409, "Finish the match first")
    action = d.get("action")
    if action == "schedule":
        if g["status"] != "pending":
            raise ApiError(409, "Trivia has already started or finished")
        db.run("UPDATE matches SET trivia_start_at=? WHERE id=?", (as_int(d.get("start_at"), "start time"), mid))
        return {"ok": True}
    if action in ("start_now", "resume"):
        db.run("UPDATE matches SET trivia_start_at=? WHERE id=?", (int(time.time()), mid))
        ok, msg = game.start_game(mid)
        if not ok:
            raise ApiError(409, msg)
        return {"ok": True}
    raise ApiError(400, "Unknown action")


@api("/api/admin/matches/{mid:int}", auth="admin")
def a_match_detail(request, user):
    mid = request.path_params["mid"]
    m = get_match(mid)
    return {
        "match": match_view(m),
        "stakers": db.rows("SELECT u.id, u.name, u.email, s.side, SUM(s.amount_kobo) amount_kobo FROM stakes s "
                           "JOIN users u ON u.id=s.user_id WHERE s.match_id=? GROUP BY u.id ORDER BY amount_kobo DESC", (mid,)),
        "slots": db.rows("SELECT sl.*, u.name winner_name, u.email winner_email FROM slots sl LEFT JOIN users u "
                         "ON u.id=sl.winner_user_id WHERE sl.match_id=? ORDER BY sl.round_no, sl.side", (mid,)),
        "rounds": db.rows("SELECT round_no, attempt, sides, question_id FROM rounds WHERE match_id=? ORDER BY id", (mid,)),
        "unclaimed": db.rows("SELECT * FROM unclaimed WHERE match_id=? ORDER BY id", (mid,)),
        "flags": db.rows("SELECT f.*, u.name FROM flags f LEFT JOIN users u ON u.id=f.user_id WHERE f.match_id=?", (mid,)),
    }


@api("/api/admin/unclaimed", auth="admin")
def a_unclaimed(request, user):
    return {
        "total_kobo": money_sum("SELECT SUM(amount_kobo) t FROM unclaimed"),
        "by_club": db.rows("SELECT club, COUNT(*) n, SUM(amount_kobo) total_kobo FROM unclaimed GROUP BY club "
                           "ORDER BY total_kobo DESC"),
        "by_reason": db.rows("SELECT reason, COUNT(*) n, SUM(amount_kobo) total_kobo FROM unclaimed GROUP BY reason"),
        "rows": db.rows("SELECT un.*, m.home, m.away, m.kickoff_at FROM unclaimed un JOIN matches m ON m.id=un.match_id "
                        "ORDER BY un.id DESC LIMIT 300"),
    }


@api("/api/admin/wins", auth="admin")
def a_wins(request, user):
    return {"wins": db.rows(
        "SELECT sl.id, sl.match_id, m.home, m.away, sl.round_no, sl.side, sl.amount_kobo, sl.answer_ms, sl.resolved_at, "
        "u.id user_id, u.name, u.email FROM slots sl JOIN matches m ON m.id=sl.match_id JOIN users u ON u.id=sl.winner_user_id "
        "WHERE sl.status='won' ORDER BY sl.id DESC LIMIT 300")}


@api("/api/admin/users", auth="admin")
def a_users(request, user):
    qs = request.query_params.get("q", "").strip()
    like = f"%{qs}%"
    rows = db.rows(
        "SELECT u.id, u.name, u.email, u.phone, u.role, u.status, u.balance_kobo, u.created_at, u.account_name, "
        "(SELECT COALESCE(SUM(amount_kobo),0) FROM stakes WHERE user_id=u.id) staked_kobo, "
        "(SELECT COALESCE(SUM(amount_kobo),0) FROM slots WHERE winner_user_id=u.id AND status='won') won_kobo, "
        "(SELECT COUNT(*) FROM flags WHERE user_id=u.id AND resolved=0) open_flags FROM users u "
        "WHERE (? = '' OR u.name LIKE ? OR u.email LIKE ? OR u.phone LIKE ?) ORDER BY u.id DESC LIMIT 200",
        (qs, like, like, like))
    return {"users": rows}


@api("/api/admin/users/{uid:int}", auth="admin")
def a_user(request, user):
    uid = request.path_params["uid"]
    u = db.one("SELECT * FROM users WHERE id=?", (uid,))
    if not u:
        raise ApiError(404, "User not found")
    prof = public_user(dict(u))
    prof.update({"status": u["status"], "signup_ip": u["signup_ip"], "device_id": u["device_id"],
                 "age_confirmed": bool(u["age_confirmed"])})
    return {
        "profile": prof,
        "stakes": db.rows("SELECT s.match_id, m.home, m.away, s.side, s.amount_kobo, s.created_at FROM stakes s "
                          "JOIN matches m ON m.id=s.match_id WHERE s.user_id=? ORDER BY s.id DESC LIMIT 100", (uid,)),
        "wins": db.rows("SELECT sl.match_id, m.home, m.away, sl.round_no, sl.amount_kobo, sl.answer_ms FROM slots sl "
                        "JOIN matches m ON m.id=sl.match_id WHERE sl.winner_user_id=? ORDER BY sl.id DESC", (uid,)),
        "payments": db.rows("SELECT kind, reference, amount_kobo, status, created_at FROM payments WHERE user_id=? "
                            "ORDER BY id DESC LIMIT 50", (uid,)),
        "ledger": db.rows("SELECT type, amount_kobo, note, created_at FROM ledger WHERE user_id=? ORDER BY id DESC LIMIT 100", (uid,)),
        "flags": db.rows("SELECT * FROM flags WHERE user_id=? ORDER BY id DESC", (uid,)),
    }


@api("/api/admin/users/{uid:int}/status", ["POST"], auth="admin")
async def a_user_status(request, user):
    uid = request.path_params["uid"]
    st = (await body(request)).get("status")
    if st not in ("active", "suspended"):
        raise ApiError(400, "Bad status")
    if uid == user["id"]:
        raise ApiError(409, "You can't suspend yourself")
    with db.tx() as c:
        c.execute("UPDATE users SET status=? WHERE id=?", (st, uid))
        if st == "suspended":
            c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    return {"ok": True}


@api("/api/admin/withdrawals", auth="admin")
def a_withdrawals(request, user):
    return {"withdrawals": db.rows(
        "SELECT p.id, p.reference, p.amount_kobo, p.status, p.detail, p.created_at, u.id user_id, u.name, u.email, "
        "u.bank_name, u.account_number, u.account_name FROM payments p JOIN users u ON u.id=p.user_id "
        "WHERE p.kind='withdrawal' ORDER BY (p.status='pending_approval') DESC, p.id DESC LIMIT 200")}


@api("/api/admin/withdrawals/{ref}", ["POST"], auth="admin")
async def a_withdrawal_action(request, user):
    ref = request.path_params["ref"]
    act = (await body(request)).get("action")
    p = db.one("SELECT status FROM payments WHERE reference=? AND kind='withdrawal'", (ref,))
    if not p:
        raise ApiError(404, "Not found")
    if act == "approve":
        if p["status"] != "pending_approval":
            raise ApiError(409, "Not awaiting approval")
        await asyncio.to_thread(wallet.send_transfer, ref)
    elif act == "decline":
        if p["status"] not in ("pending_approval", "needs_attention"):
            raise ApiError(409, "Can't decline in this state")
        wallet.decline_withdrawal(ref)
    else:
        raise ApiError(400, "Unknown action")
    return {"ok": True, "status": db.one("SELECT status FROM payments WHERE reference=?", (ref,))["status"]}


@api("/api/admin/payments", auth="admin")
def a_payments(request, user):
    return {"payments": db.rows("SELECT p.*, u.name, u.email FROM payments p JOIN users u ON u.id=p.user_id "
                                "ORDER BY p.id DESC LIMIT 200")}


@api("/api/admin/questions", auth="admin")
def a_questions(request, user):
    rows = db.rows("SELECT * FROM questions ORDER BY id DESC")
    for r in rows:
        r["options"] = json.loads(r["options"])
    return {"questions": rows}


def parse_question(d):
    qt = text(d, "text", 5, 300, "Question")
    opts = d.get("options")
    if not isinstance(opts, list) or not 2 <= len(opts) <= 4 or not all(isinstance(o, str) and o.strip() for o in opts):
        raise ApiError(400, "Give 2-4 answer options")
    ci = as_int(d.get("correct_index"), "correct answer")
    if not 0 <= ci < len(opts):
        raise ApiError(400, "Correct answer must be one of the options")
    return qt, json.dumps([o.strip()[:120] for o in opts]), ci


@api("/api/admin/questions", ["POST"], auth="admin")
async def a_q_add(request, user):
    d = await body(request)
    items = d["items"] if isinstance(d.get("items"), list) else [d]       # single or bulk import
    parsed = [parse_question(i) for i in items[:500]]
    with db.tx() as c:
        for qt, opts, ci in parsed:
            c.execute("INSERT INTO questions(text,options,correct_index) VALUES(?,?,?)", (qt, opts, ci))
    return {"added": len(parsed)}


@api("/api/admin/questions/{qid:int}", ["PUT"], auth="admin")
async def a_q_edit(request, user):
    d = await body(request)
    qt, opts, ci = parse_question(d)
    db.run("UPDATE questions SET text=?, options=?, correct_index=?, active=? WHERE id=?",
           (qt, opts, ci, 0 if d.get("active") is False else 1, request.path_params["qid"]))
    return {"ok": True}


@api("/api/admin/questions/{qid:int}", ["DELETE"], auth="admin")
def a_q_del(request, user):
    db.run("UPDATE questions SET active=0 WHERE id=?", (request.path_params["qid"],))   # soft delete keeps history
    return {"ok": True}


@api("/api/admin/content", auth="admin")
def a_content(request, user):
    return content.get_all()


@api("/api/admin/content", ["PUT"], auth="admin")
async def a_content_save(request, user):
    try:
        content.update(await body(request))
    except ValueError as e:
        raise ApiError(400, str(e))
    return {"ok": True}


@api("/api/admin/settings", auth="admin")
def a_settings(request, user):
    return settings.all()


@api("/api/admin/settings", ["PUT"], auth="admin")
async def a_settings_save(request, user):
    try:
        return settings.update(await body(request))
    except ValueError as e:
        raise ApiError(400, str(e))


@api("/api/admin/flags", auth="admin")
def a_flags(request, user):
    return {"flags": db.rows("SELECT f.*, u.name, u.email FROM flags f LEFT JOIN users u ON u.id=f.user_id "
                             "ORDER BY f.resolved, f.id DESC LIMIT 300")}


@api("/api/admin/flags/{fid:int}/resolve", ["POST"], auth="admin")
def a_flag_resolve(request, user):
    db.run("UPDATE flags SET resolved=1 WHERE id=?", (request.path_params["fid"],))
    return {"ok": True}



# ------------------------------------------------------------------ football results feed (admin)
@api("/api/admin/feed", auth="admin")
def a_feed(request, user):
    return feed.overview()


@api("/api/admin/feed/fixtures", ["POST"], auth="admin")
async def a_feed_fixtures(request, user):
    code = str((await body(request)).get("competition", ""))
    if code not in dict(feed.COMPETITIONS):
        raise ApiError(400, "Unknown competition")
    try:
        fx = await asyncio.to_thread(feed.provider().fixtures, code)
    except feed.FeedError as e:
        raise ApiError(502, str(e))
    have = {r["external_id"] for r in db.q("SELECT external_id FROM matches WHERE external_id IS NOT NULL")}
    return {"fixtures": [dict(f, added=f["external_id"] in have) for f in fx]}


@api("/api/admin/feed/add", ["POST"], auth="admin")
async def a_feed_add(request, user):
    try:
        return {"id": feed.add_fixture(await body(request))}
    except feed.FeedError as e:
        raise ApiError(409, str(e))


@api("/api/admin/feed/sync", ["POST"], auth="admin")
async def a_feed_sync(request, user):
    try:
        n = await asyncio.to_thread(feed.sync_results)
    except feed.FeedError as e:
        raise ApiError(502, str(e))
    for mid in feed.settle_due():
        game.hub.publish(mid, "match_finished", {})
    return {"checked": n}


@api("/api/admin/matches/{mid:int}/hold", ["POST"], auth="admin")
async def a_hold(request, user):
    hold = 1 if (await body(request)).get("hold") else 0
    db.run("UPDATE matches SET settle_hold=? WHERE id=? AND status='open'", (hold, request.path_params["mid"]))
    return {"ok": True, "hold": bool(hold)}


@api("/api/admin/matches/{mid:int}/settle-feed", ["POST"], auth="admin")
def a_settle_feed(request, user):
    mid = request.path_params["mid"]
    try:
        p = feed.settle_from_feed(mid)
    except feed.FeedError as e:
        raise ApiError(409, str(e))
    game.hub.publish(mid, "match_finished", {})
    return {"ok": True, "mode": p["mode"]}


async def feed_loop():
    """Every 15s: auto-settle anything due. Every 2 minutes: ask the feed for fresh scores."""
    last = 0
    while True:
        try:
            if time.time() - last >= 120:
                last = time.time()
                try:
                    await asyncio.to_thread(feed.sync_results)
                except feed.FeedError as e:
                    db.run("INSERT INTO kv(key,value) VALUES('feed_last_error',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(e),))
            for mid in feed.settle_due():
                game.hub.publish(mid, "match_finished", {})
        except Exception:
            log.exception("feed loop error")
        await asyncio.sleep(15)


# ------------------------------------------------------------------ startup
def bootstrap_admin():
    if db.one("SELECT 1 FROM users WHERE role='admin'"):
        return
    pw = config.ADMIN_PASSWORD or secrets.token_urlsafe(12)
    h, salt = security.hash_password(pw)
    db.run("INSERT INTO users(email,phone,name,pw_hash,pw_salt,role,age_confirmed,created_at) VALUES(?,?,?,?,?,?,?,?)",
           (config.ADMIN_EMAIL, "0000000000", "Admin", h, salt, "admin", 1, int(time.time())))
    if not config.ADMIN_PASSWORD:
        print(f"\n*** First run: admin login  {config.ADMIN_EMAIL}  /  {pw}  (change ADMIN_PASSWORD in env) ***\n", flush=True)


@contextlib.asynccontextmanager
async def lifespan(app):
    logging.basicConfig(level=logging.INFO)
    db.conn()
    seed.seed_questions_if_empty()
    bootstrap_admin()
    game.on_boot()
    task = asyncio.create_task(game.scheduler())
    ftask = asyncio.create_task(feed_loop())
    if paystack.MOCK:
        log.warning("Paystack MOCK mode: no real money moves. Set PAYSTACK_SECRET_KEY to go live.")
    if not config.FOOTBALL_API_KEY:
        log.warning("No FOOTBALL_DATA_API_KEY: the fixtures feed shows DEMO data.")
    yield
    task.cancel()
    ftask.cancel()


class SecurityHeaders:
    """Pure ASGI middleware (does not buffer the live SSE stream)."""
    CSP = ("default-src 'self'; img-src 'self' https: data:; style-src 'self' 'unsafe-inline'; "
           "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def wrapped(message):
            if message["type"] == "http.response.start":
                h = message.setdefault("headers", [])
                for k, v in ((b"content-security-policy", self.CSP.encode()), (b"x-content-type-options", b"nosniff"),
                             (b"referrer-policy", b"same-origin"), (b"x-frame-options", b"DENY")):
                    h.append((k, v))
            await send(message)
        await self.app(scope, receive, wrapped)


app = Starlette(routes=ROUTES + [Mount("/", StaticFiles(directory=str(config.BASE / "static"), html=True))],
                lifespan=lifespan)
app = SecurityHeaders(app)
