"""Football fixtures + results feed.

football-data.org v4 is the real provider (set FOOTBALL_DATA_API_KEY). Without a key a DEMO provider with fake
fixtures is used so the whole screen can be tried safely.

Safety rules, because settling locks real money:
  * Auto-settle is OFF by default and waits `auto_settle_delay_minutes` after the feed reports full time.
  * If the feed changes the score during that wait, the wait restarts.
  * Any match can be put on hold; postponed / suspended / cancelled / awarded matches are NEVER auto-settled.
  * The score used is the result after 90 minutes (the feed's `regularTime` when a cup tie went to extra time).
"""
import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone

import requests

from . import config, db, settings, settlement

log = logging.getLogger("fm.feed")

COMPETITIONS = [("PL", "Premier League"), ("PD", "La Liga"), ("SA", "Serie A"), ("BL1", "Bundesliga"),
                ("FL1", "Ligue 1"), ("CL", "Champions League"), ("ELC", "Championship"),
                ("DED", "Eredivisie"), ("PPL", "Primeira Liga")]
API = "https://api.football-data.org/v4"


class FeedError(Exception):
    pass


def norm_status(s):
    if s in ("SCHEDULED", "TIMED"):
        return "scheduled"
    if s in ("IN_PLAY", "PAUSED", "EXTRA_TIME", "PENALTY_SHOOTOUT"):
        return "live"
    if s == "FINISHED":
        return "finished"
    if s in ("POSTPONED", "SUSPENDED", "CANCELLED"):
        return s.lower()
    if s == "AWARDED":
        return "awarded"
    return "unknown"


def ninety_minute_score(m):
    """(home, away) after 90 minutes, or None. For ties that went to extra time the feed gives regularTime."""
    sc = m.get("score") or {}
    use = sc.get("regularTime") if sc.get("duration") in ("EXTRA_TIME", "PENALTY_SHOOTOUT") and sc.get("regularTime") \
        else sc.get("fullTime")
    if not use or use.get("home") is None or use.get("away") is None:
        return None
    return int(use["home"]), int(use["away"])


def parse_match(m):
    """football-data.org match object -> our flat dict."""
    ko = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00")).timestamp()
    sc = ninety_minute_score(m)
    ht, at = m.get("homeTeam") or {}, m.get("awayTeam") or {}
    return {"external_id": str(m["id"]), "home": (ht.get("shortName") or ht.get("name") or "Home")[:40],
            "away": (at.get("shortName") or at.get("name") or "Away")[:40],
            "competition": ((m.get("competition") or {}).get("name") or "")[:60], "kickoff_at": int(ko),
            "status": norm_status(m.get("status")), "home_score": sc[0] if sc else None,
            "away_score": sc[1] if sc else None}


# ------------------------------------------------------------------ providers
class FootballDataOrg:
    name = "football-data.org"
    demo = False

    def _get(self, path, params):
        try:
            r = requests.get(API + path, params=params, headers={"X-Auth-Token": config.FOOTBALL_API_KEY}, timeout=20)
        except requests.RequestException as e:
            raise FeedError(f"Could not reach the football feed: {e}")
        if r.status_code == 429:
            raise FeedError("The feed's rate limit was hit. It will retry shortly.")
        if r.status_code in (401, 403):
            raise FeedError("The feed refused the request: check the API key, or that this competition is in your plan.")
        if r.status_code >= 400:
            raise FeedError(f"Feed error {r.status_code}")
        return r.json()

    def fixtures(self, code, days=14):
        today = datetime.now(timezone.utc).date()
        data = self._get(f"/competitions/{code}/matches",
                         {"dateFrom": today.isoformat(), "dateTo": (today + timedelta(days=days)).isoformat()})
        return [x for x in map(parse_match, data.get("matches", [])) if x["status"] == "scheduled"]

    def by_ids(self, ids):
        out = []
        for i in range(0, len(ids), 40):
            data = self._get("/matches", {"ids": ",".join(ids[i:i + 40])})
            out += [parse_match(m) for m in data.get("matches", [])]
        return out


class DemoProvider:
    """Fake fixtures/results for trying the screen. Scores are derived from the match id so they are repeatable."""
    name = "Demo data"
    demo = True
    overrides = {}                                  # external_id -> {"status":..., "home_score":..., "away_score":...}
    TEAMS = {"PL": ["Chelsea", "Arsenal", "Liverpool", "Man City", "Tottenham", "Man United", "Newcastle", "Everton"],
             "PD": ["Real Madrid", "Barcelona", "Atlético", "Sevilla", "Valencia", "Villarreal"],
             "CL": ["Bayern", "PSG", "Inter", "Real Madrid", "Arsenal", "Dortmund"]}

    def fixtures(self, code, days=14):
        teams = self.TEAMS.get(code) or ["Team A", "Team B", "Team C", "Team D"]
        base = int(time.time()) // 3600 * 3600
        out = []
        for i in range(0, len(teams) - 1, 2):
            ext = f"demo-{code}-{i}"
            out.append({"external_id": ext, "home": teams[i], "away": teams[i + 1], "competition": dict(COMPETITIONS).get(code, code),
                        "kickoff_at": base + (i + 1) * 3 * 3600, "status": "scheduled", "home_score": None, "away_score": None})
        return out

    def by_ids(self, ids):
        out = []
        for i in ids:
            r = db.one("SELECT * FROM matches WHERE external_id=?", (i,))
            if not r:
                continue
            ov = self.overrides.get(i)
            if ov:
                out.append({"external_id": i, "home": r["home"], "away": r["away"], "competition": r["competition"],
                            "kickoff_at": r["kickoff_at"], "status": ov["status"], "home_score": ov.get("home_score"),
                            "away_score": ov.get("away_score")})
            else:
                done = time.time() > r["kickoff_at"] + 110 * 60
                h = int(hashlib.md5(i.encode()).hexdigest(), 16)
                out.append({"external_id": i, "home": r["home"], "away": r["away"], "competition": r["competition"],
                            "kickoff_at": r["kickoff_at"], "status": "finished" if done else "scheduled",
                            "home_score": h % 4 if done else None, "away_score": (h // 7) % 3 if done else None})
        return out


def provider():
    return FootballDataOrg() if config.FOOTBALL_API_KEY else DemoProvider()


# ------------------------------------------------------------------ importing fixtures
def add_fixture(fx):
    """Create a match from a feed fixture. Staking closes at kickoff."""
    try:
        ko = int(fx["kickoff_at"])
        home, away = str(fx["home"]).strip()[:40], str(fx["away"]).strip()[:40]
        ext = str(fx["external_id"])[:40]
    except (KeyError, ValueError, TypeError):
        raise FeedError("Bad fixture")
    if not home or not away or home.lower() == away.lower():
        raise FeedError("Bad fixture")
    if ko < time.time():
        raise FeedError("That match has already kicked off")
    if db.one("SELECT 1 FROM matches WHERE external_id=?", (ext,)):
        raise FeedError("That match is already added")
    return db.run("INSERT INTO matches(home,away,competition,kickoff_at,staking_closes_at,external_id,feed_status,created_at) "
                  "VALUES(?,?,?,?,?,?,?,?)", (home, away, str(fx.get("competition", ""))[:60], ko, ko, ext, "scheduled", int(time.time())))


# ------------------------------------------------------------------ syncing results
def _kv_set(key, value):
    db.run("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def kv_get(key, default=None):
    r = db.one("SELECT value FROM kv WHERE key=?", (key,))
    return r["value"] if r else default


def sync_results(prov=None, now=None):
    """Ask the feed about open matches that are near/after kickoff and store what it says."""
    prov = prov or provider()
    now = int(now or time.time())
    rows = db.rows("SELECT id, external_id FROM matches WHERE status='open' AND external_id IS NOT NULL "
                   "AND kickoff_at <= ?", (now + 36 * 3600,))
    if not rows:
        _kv_set("feed_last_sync", now)
        return 0
    got = {m["external_id"]: m for m in prov.by_ids([r["external_id"] for r in rows])}
    n = 0
    with db.tx() as c:
        for r in rows:
            f = got.get(r["external_id"])
            if not f:
                continue
            cur = c.execute("SELECT feed_status, feed_home, feed_away, feed_ft_at FROM matches WHERE id=?", (r["id"],)).fetchone()
            if f["status"] == "finished" and f["home_score"] is not None and f["away_score"] is not None:
                same = (cur["feed_status"], cur["feed_home"], cur["feed_away"]) == ("finished", f["home_score"], f["away_score"])
                ft_at = cur["feed_ft_at"] if same and cur["feed_ft_at"] else now      # score changed -> wait restarts
                c.execute("UPDATE matches SET feed_status='finished', feed_home=?, feed_away=?, feed_ft_at=?, feed_checked_at=? "
                          "WHERE id=?", (f["home_score"], f["away_score"], ft_at, now, r["id"]))
            else:
                c.execute("UPDATE matches SET feed_status=?, feed_home=NULL, feed_away=NULL, feed_ft_at=NULL, feed_checked_at=? "
                          "WHERE id=?", (f["status"], now, r["id"]))
            n += 1
    _kv_set("feed_last_sync", now)
    _kv_set("feed_last_error", "")
    return n


def settle_due(now=None):
    """Auto-settle matches whose full-time score has been stable for the configured delay. Returns settled match ids."""
    cfg = settings.all()
    if not cfg["auto_settle"]:
        return []
    now = int(now or time.time())
    due = db.rows("SELECT id, feed_home, feed_away FROM matches WHERE status='open' AND feed_status='finished' "
                  "AND settle_hold=0 AND feed_ft_at IS NOT NULL AND feed_home IS NOT NULL AND feed_away IS NOT NULL "
                  "AND feed_ft_at + ? <= ?", (cfg["auto_settle_delay_minutes"] * 60, now))
    done = []
    for m in due:
        try:
            settlement.finish_match(m["id"], m["feed_home"], m["feed_away"])
            done.append(m["id"])
            log.info("auto-settled match %s %s-%s", m["id"], m["feed_home"], m["feed_away"])
        except settlement.SettleError:
            pass
    return done


def settle_from_feed(match_id):
    """Admin pressed 'Settle now': use the feed's full-time score immediately."""
    m = db.one("SELECT * FROM matches WHERE id=?", (match_id,))
    if not m or m["feed_status"] != "finished" or m["feed_home"] is None:
        raise FeedError("The feed has not reported a final score for this match")
    return settlement.finish_match(match_id, m["feed_home"], m["feed_away"])


def overview(now=None):
    """Everything the admin screen needs."""
    now = int(now or time.time())
    cfg = settings.all()
    prov = provider()
    pend, alerts = [], []
    for m in db.rows("SELECT * FROM matches WHERE status='open' AND feed_status IN "
                     "('finished','postponed','suspended','cancelled','awarded') ORDER BY kickoff_at"):
        pot = db.one("SELECT COALESCE(SUM(amount_kobo),0) t, COUNT(DISTINCT user_id) n FROM stakes WHERE match_id=?", (m["id"],))
        base = {"id": m["id"], "home": m["home"], "away": m["away"], "pot_kobo": pot["t"], "fans": pot["n"]}
        if m["feed_status"] == "finished" and m["feed_home"] is not None:
            outcome = settlement.outcome_of(m["feed_home"], m["feed_away"])
            settle_at = (m["feed_ft_at"] + cfg["auto_settle_delay_minutes"] * 60) if (cfg["auto_settle"] and not m["settle_hold"] and m["feed_ft_at"]) else None
            side_fans = db.one("SELECT COUNT(DISTINCT user_id) n FROM stakes WHERE match_id=? AND side=?",
                               (m["id"], outcome if outcome != "draw" else "home"))["n"] if outcome != "draw" else None
            pend.append(dict(base, feed_home=m["feed_home"], feed_away=m["feed_away"], outcome=outcome, hold=bool(m["settle_hold"]),
                             settle_at=settle_at, winners_fans=side_fans))
        else:
            alerts.append(dict(base, status=m["feed_status"]))
    return {"provider": prov.name, "demo": prov.demo, "last_sync": int(kv_get("feed_last_sync", 0) or 0),
            "last_error": kv_get("feed_last_error", "") or "", "auto_settle": bool(cfg["auto_settle"]),
            "delay_minutes": cfg["auto_settle_delay_minutes"], "competitions": [{"code": c, "name": n} for c, n in COMPETITIONS],
            "pending": pend, "alerts": alerts, "now": now}
