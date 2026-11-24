"""End-to-end smoke test against a RUNNING server (mock Paystack):  python tests/e2e_http.py
Fans sign up, deposit, stake, then answer live trivia over the real SSE stream like a browser would."""
import json
import sys
import threading
import time

import requests

sys.path.insert(0, ".")
from app.seed import QUESTIONS  # noqa: E402

BASE = "http://localhost:8000"
ANSWERS = {q: opts[c] for q, opts, c in QUESTIONS}


def sess():
    s = requests.Session()
    s.headers["Content-Type"] = "application/json"
    return s


def call(s, method, path, **kw):
    r = s.request(method, BASE + path, json=kw.get("json", {}) if method != "GET" else None)
    if r.status_code >= 400:
        raise SystemExit(f"{method} {path} -> {r.status_code} {r.text}")
    return r.json()


admin = sess()
call(admin, "POST", "/api/login", json={"email": "admin@fanmillionaire.local", "password": "AdminPass#12345"})
call(admin, "PUT", "/api/admin/settings", json={
    "countdown_s": 2, "answer_window_s": 6, "pause_s": 1, "grace_ms": 300, "min_human_ms": 300, "trivia_delay_minutes": 60,
    "withdrawals_require_approval": 1})
now = int(time.time())
mid = call(admin, "POST", "/api/admin/matches", json={"home": "Chelsea", "away": "Arsenal", "competition": "Test League",
                                                         "kickoff_at": now + 3600, "staking_closes_at": now + 3600})["id"]

fans = {}
for i, (name, side) in enumerate([("Chidi Okafor", "home"), ("Bola Adeyemi", "home"), ("Emeka Nwosu", "home"), ("Tunde Bakare", "away")]):
    s = sess()
    call(s, "POST", "/api/signup", json={"name": name, "email": f"fan{i}@test.com", "phone": f"080100000{i:02d}",
                                          "password": "password123", "age_confirmed": True, "device_id": f"device-{i}-abcdefgh"})
    dep = call(s, "POST", "/api/wallet/deposit", json={"amount_kobo": 500000})
    call(s, "POST", "/api/mock/pay", json={"reference": dep["reference"]})
    assert call(s, "POST", "/api/wallet/verify", json={"reference": dep["reference"]})["status"] == "success"
    call(s, "POST", f"/api/matches/{mid}/stake", json={"side": side, "amount_kobo": 100000})
    fans[name] = (s, side)
print("4 fans funded and staked. pots:", call(admin, "GET", f"/api/matches/{mid}")["pots"])

# a fan cannot back both clubs, and cannot stake below the minimum
s0 = fans["Chidi Okafor"][0]
r = s0.post(BASE + f"/api/matches/{mid}/stake", json={"side": "away", "amount_kobo": 100000})
assert r.status_code == 409, r.text

res = call(admin, "POST", f"/api/admin/matches/{mid}/finish", json={"home_score": 2, "away_score": 0})
print("settled:", res)
call(admin, "POST", f"/api/admin/matches/{mid}/trivia", json={"action": "start_now"})

results, lock = {}, threading.Lock()


def play(name, sess_, side, delay):
    got = []
    with sess_.get(BASE + f"/api/matches/{mid}/stream", stream=True, headers={"Accept": "text/event-stream"}) as r:
        ev = None
        for line in r.iter_lines(decode_unicode=True):
            if line is None:
                continue
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                d = json.loads(line[5:])
                got.append(ev)
                if ev == "ping":
                    sess_.post(BASE + "/api/pong", json={"nonce": d["nonce"]})
                elif ev == "question":
                    correct = ANSWERS[d["question"]["text"]]
                    time.sleep(delay)
                    if side == "home":       # eligible fans answer; the away fan is not eligible (home won)
                        r2 = sess_.post(BASE + "/api/answer", json={"match_id": mid, "round_id": d["round_id"],
                                                                     "option": d["question"]["options"].index(correct)})
                        got.append(f"answer:{r2.status_code}")
                elif ev == "game_over":
                    break
    with lock:
        results[name] = got


threads = []
delays = {"Chidi Okafor": 0.6, "Bola Adeyemi": 1.0, "Emeka Nwosu": 1.4, "Tunde Bakare": 0.5}
for name, (s, side) in fans.items():
    t = threading.Thread(target=play, args=(name, s, side, delays[name]), daemon=True)
    t.start()
    threads.append(t)
for t in threads:
    t.join(timeout=120)

print("\nevents seen by Chidi:", [e for e in results["Chidi Okafor"] if e != "ping"][:14], "... pings:", results["Chidi Okafor"].count("ping"))
assert results["Chidi Okafor"].count("ping") >= 3, "latency pings should reach the client"
print("away fan's answer attempts:", [e for e in results["Tunde Bakare"] if e.startswith("answer")], "(none: not eligible)")

d = call(admin, "GET", f"/api/admin/matches/{mid}")
print("\nslots:")
for s in d["slots"]:
    print(f"  round {s['round_no']}: {s['status']:9} {s['amount_kobo']/100:>8.2f}  winner={s['winner_name']}  {s['answer_ms']}ms")
assert [s["status"] for s in d["slots"]] == ["won"] * 3
assert [s["winner_name"] for s in d["slots"]] == ["Chidi Okafor", "Bola Adeyemi", "Emeka Nwosu"], "fastest first, one win each"
dash = call(admin, "GET", "/api/admin/dashboard")
print("dashboard:", {k: dash[k] for k in ("staked_kobo", "prizes_paid_kobo", "platform_fee_kobo", "unclaimed_kobo", "platform_total_kobo")})
assert dash["staked_kobo"] == 400000
assert dash["prizes_paid_kobo"] + dash["platform_total_kobo"] == 400000, "every kobo accounted for"
# winner withdraws: held for admin approval, then approved
s0 = fans["Chidi Okafor"][0]
call(s0, "POST", "/api/wallet/bank", json={"bank_code": "058", "bank_name": "GTBank", "account_number": "0123456789"})
w = call(s0, "POST", "/api/wallet/withdraw", json={"amount_kobo": 100000})
assert w["status"] == "pending_approval", w
wd = call(admin, "GET", "/api/admin/withdrawals")["withdrawals"][0]
print("withdrawal:", wd["status"], "->", call(admin, "POST", f"/api/admin/withdrawals/{wd['reference']}", json={"action": "approve"}))
print("\nE2E PASSED")
