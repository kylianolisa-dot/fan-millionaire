"""Core money + game tests. Run: python -m pytest tests -q"""
import asyncio
import os
import tempfile
import time
import unittest

from app import betting, config, db, game, ledger, seed, settings, settlement


def fresh_db():
    db.close()
    config.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
    db.conn()
    seed.seed_questions_if_empty()


def mk_user(name, balance=0, device=None):
    uid = db.run("INSERT INTO users(email,phone,name,pw_hash,pw_salt,device_id,created_at) VALUES(?,?,?,?,?,?,?)",
                 (f"{name}@x.com", name, name, "h", "s", device, int(time.time())))
    if balance:
        with db.tx() as c:
            ledger.post(c, uid, balance, "deposit", f"dep-{uid}")
    return {"id": uid, "email": f"{name}@x.com"}


def mk_match(home="Chelsea", away="Arsenal"):
    now = int(time.time())
    return db.run("INSERT INTO matches(home,away,kickoff_at,staking_closes_at,created_at) VALUES(?,?,?,?,?)",
                  (home, away, now + 3600, now + 3600, now))


def check_conservation(test, match_id):
    """Every kobo staked is exactly accounted for: prizes + platform fee + unclaimed (+ refunds)."""
    staked = db.one("SELECT COALESCE(SUM(amount_kobo),0) t FROM stakes WHERE match_id=?", (match_id,))["t"]
    out = db.one("SELECT COALESCE(SUM(amount_kobo),0) t FROM ledger WHERE match_id=? AND type IN "
                 "('prize','platform_fee','unclaimed')", (match_id,))["t"]
    open_left = db.one("SELECT COALESCE(SUM(amount_kobo),0) t FROM slots WHERE match_id=? AND status='open'",
                       (match_id,))["t"]
    test.assertEqual(staked, out + open_left)
    # cached balances match the ledger for every user
    for u in db.q("SELECT id, balance_kobo FROM users"):
        s = db.one("SELECT COALESCE(SUM(amount_kobo),0) t FROM ledger WHERE user_id=?", (u["id"],))["t"]
        test.assertEqual(u["balance_kobo"], s)


CFG = settings.DEFAULTS


class PlanTests(unittest.TestCase):
    def test_straight_win_both_pots_go_to_winners_side(self):
        p = settlement.plan(600_000, 400_000, 6, 4, "home", CFG)
        self.assertEqual(p["mode"], "win")
        self.assertEqual(p["play_side"], "home")
        self.assertEqual([s["amount"] for s in p["slots"]], [150_000] * 3)      # 15% of 1,000,000 each
        self.assertEqual(p["platform_base"], 550_000)

    def test_draw_two_winners_split_15_percent(self):
        p = settlement.plan(600_000, 400_000, 6, 4, "draw", CFG)
        self.assertEqual(p["mode"], "draw")
        self.assertEqual(len(p["slots"]), 6)
        self.assertTrue(all(s["amount"] == 75_000 for s in p["slots"]))          # 7.5% each
        self.assertEqual(p["platform_base"], 550_000)

    def test_winning_team_has_no_stakers_losing_stakers_play(self):
        p = settlement.plan(0, 500_000, 0, 5, "home", CFG)
        self.assertEqual(p["play_side"], "away")
        self.assertEqual(p["slots"][0]["amount"], 75_000)

    def test_draw_one_sided_other_slot_forced_unclaimed(self):
        p = settlement.plan(500_000, 0, 5, 0, "draw", CFG)
        self.assertEqual(len(p["slots"]), 6)
        self.assertEqual([s["forced"] for s in p["slots"] if s["side"] == "away"], ["no_stakers_on_side"] * 3)
        cfg = dict(CFG, draw_one_sided_full_prize=1)
        p2 = settlement.plan(500_000, 0, 5, 0, "draw", cfg)
        self.assertEqual([s["amount"] for s in p2["slots"]], [75_000] * 3)

    def test_nobody_staked(self):
        self.assertEqual(settlement.plan(0, 0, 0, 0, "draw", CFG)["mode"], "none")

    def test_rounding_dust_stays_with_platform(self):
        p = settlement.plan(333, 334, 1, 1, "home", CFG)
        paid = sum(s["amount"] for s in p["slots"])
        self.assertEqual(paid + p["platform_base"], 667)


class StakeAndSettleTests(unittest.TestCase):
    def setUp(self):
        fresh_db()

    def test_cannot_back_both_sides_or_overspend(self):
        a = mk_user("a", 100_000)
        m = mk_match()
        betting.place_stake(a, m, "home", 20_000)
        with self.assertRaises(betting.StakeError):
            betting.place_stake(a, m, "away", 20_000)
        with self.assertRaises(betting.StakeError):
            betting.place_stake(a, m, "home", 10_000_000)        # more than balance / max
        betting.place_stake(a, m, "home", 20_000)                  # topping up same side is fine
        self.assertEqual(betting.pots(m)["home"], 40_000)

    def test_shared_device_is_flagged(self):
        a, b = mk_user("a", 100_000, "dev1"), mk_user("b", 100_000, "dev1")
        m = mk_match()
        betting.place_stake(a, m, "home", 10_000)
        betting.place_stake(b, m, "away", 10_000)
        self.assertEqual(db.one("SELECT COUNT(*) n FROM flags WHERE kind='shared_device'")["n"], 2)

    def test_finish_win_books_platform_fee(self):
        a, b = mk_user("a", 1_000_000), mk_user("b", 1_000_000)
        m = mk_match()
        betting.place_stake(a, m, "home", 600_000)
        betting.place_stake(b, m, "away", 400_000)
        settlement.finish_match(m, 2, 1)
        self.assertEqual(db.one("SELECT COALESCE(SUM(amount_kobo),0) t FROM ledger WHERE type='platform_fee'")["t"], 550_000)
        check_conservation(self, m)

    def test_cancel_refunds_everyone(self):
        a = mk_user("a", 100_000)
        m = mk_match()
        betting.place_stake(a, m, "home", 50_000)
        settlement.cancel_match(m)
        self.assertEqual(db.one("SELECT balance_kobo b FROM users WHERE id=?", (a["id"],))["b"], 100_000)

    def test_draw_one_sided_forfeits_empty_side_immediately(self):
        a = mk_user("a", 1_000_000)
        m = mk_match()
        betting.place_stake(a, m, "home", 100_000)
        settlement.finish_match(m, 1, 1)
        un = db.rows("SELECT * FROM unclaimed WHERE match_id=?", (m,))
        self.assertEqual(len(un), 3)
        self.assertEqual({u["club"] for u in un}, {"Arsenal"})
        check_conservation(self, m)


# ---------------------------------------------------------------- full game simulations
async def bot(match_id, uid, plan):
    """plan: callable(round_info)->(option_kind, delay_s). option_kind: 'right'|'wrong'|None"""
    seen = set()
    while True:
        g = game.ACTIVE.get(match_id)
        if g is None:
            return
        if g.phase == "open" and g.round_id not in seen and uid in g.eligible:
            seen.add(g.round_id)
            kind, delay = plan(g)
            if kind:
                await asyncio.sleep(delay)
                opt = g.correct if kind == "right" else (g.correct + 1) % 4
                try:
                    game.submit_answer(uid, match_id, g.round_id, opt)
                except game.GameError:
                    pass
        await asyncio.sleep(0.01)


def fast_settings(**kw):
    base = dict(countdown_s=0, answer_window_s=1, pause_s=0, grace_ms=50, min_human_ms=0, trivia_delay_minutes=0)
    base.update(kw)
    settings.update(base)


class GameTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fresh_db()
        fast_settings()

    async def run_game(self, m, bots):
        ok, msg = game.start_game(m)
        self.assertTrue(ok, msg)
        tasks = [asyncio.create_task(bot(m, uid, plan)) for uid, plan in bots]
        await asyncio.wait_for(game.ACTIVE[m].task, timeout=60)
        for t in tasks:
            t.cancel()

    async def test_straight_win_fastest_correct_wins_once_each(self):
        a, b, c_, d = (mk_user(n, 1_000_000) for n in "abcd")
        m = mk_match()
        betting.place_stake(a, m, "home", 100_000)
        betting.place_stake(b, m, "home", 100_000)
        betting.place_stake(c_, m, "home", 100_000)
        betting.place_stake(d, m, "away", 200_000)
        settlement.finish_match(m, 3, 0)
        # a is fastest, then b, then c. Winners must be 3 DIFFERENT people (one win per pot).
        bots = [(a["id"], lambda g: ("right", 0.05)), (b["id"], lambda g: ("right", 0.15)),
                (c_["id"], lambda g: ("right", 0.25)), (d["id"], lambda g: ("right", 0.01))]
        await self.run_game(m, bots)
        won = db.rows("SELECT * FROM slots WHERE match_id=? ORDER BY round_no", (m,))
        self.assertEqual([s["status"] for s in won], ["won"] * 3)
        self.assertEqual([s["winner_user_id"] for s in won], [a["id"], b["id"], c_["id"]])
        self.assertEqual(won[0]["amount_kobo"], 75_000)        # 15% of the 500,000 total pot
        self.assertEqual(db.one("SELECT status FROM games WHERE match_id=?", (m,))["status"], "done")
        check_conservation(self, m)

    async def test_losing_side_cannot_play_in_win(self):
        a, d = mk_user("a", 1_000_000), mk_user("d", 1_000_000)
        m = mk_match()
        betting.place_stake(a, m, "home", 100_000)
        betting.place_stake(d, m, "away", 100_000)
        settlement.finish_match(m, 1, 0)
        await self.run_game(m, [(d["id"], lambda g: ("right", 0.0)), (a["id"], lambda g: ("right", 0.05))])
        w = [s["winner_user_id"] for s in db.rows("SELECT * FROM slots WHERE match_id=? AND status='won'", (m,))]
        self.assertEqual(w, [a["id"]])                      # only one eligible fan: wins round 1; rest unclaimed
        un = db.rows("SELECT * FROM unclaimed WHERE match_id=?", (m,))
        self.assertEqual({u["reason"] for u in un}, {"no_eligible_players"})
        self.assertEqual(len(un), 2)
        check_conservation(self, m)

    async def test_nobody_correct_retakes_then_platform_keeps(self):
        a = mk_user("a", 1_000_000)
        m = mk_match()
        betting.place_stake(a, m, "home", 100_000)
        settlement.finish_match(m, 1, 0)
        fast_settings(max_retakes=1, answer_window_s=1)
        await self.run_game(m, [(a["id"], lambda g: ("wrong", 0.0))])
        rounds = db.one("SELECT COUNT(*) n FROM rounds WHERE match_id=?", (m,))["n"]
        self.assertEqual(rounds, 3 * 2)                      # each round: 1 attempt + 1 retake
        un = db.rows("SELECT * FROM unclaimed WHERE match_id=?", (m,))
        self.assertEqual(len(un), 3)
        self.assertEqual({u["reason"] for u in un}, {"no_correct_answer"})
        check_conservation(self, m)

    async def test_draw_one_winner_per_team_and_locked_when_other_side_retakes(self):
        hs = [mk_user(f"h{i}", 1_000_000) for i in range(3)]
        as_ = [mk_user(f"a{i}", 1_000_000) for i in range(3)]
        m = mk_match()
        for u in hs:
            betting.place_stake(u, m, "home", 100_000)
        for u in as_:
            betting.place_stake(u, m, "away", 100_000)
        settlement.finish_match(m, 2, 2)

        def home_plan(g):
            return ("right", 0.05)

        def away_plan(g):   # round 2: away side gets it wrong the first time -> retake for away only
            return ("wrong", 0.0) if (g.round_no == 2 and g.attempt == 0) else ("right", 0.1)

        bots = [(u["id"], home_plan) for u in hs] + [(u["id"], away_plan) for u in as_]
        await self.run_game(m, bots)
        slots = db.rows("SELECT * FROM slots WHERE match_id=? ORDER BY round_no, side", (m,))
        self.assertEqual(len(slots), 6)
        self.assertEqual({s["status"] for s in slots}, {"won"})
        self.assertTrue(all(s["amount_kobo"] == 45_000 for s in slots))           # 7.5% of the 600,000 pot
        r2 = db.rows("SELECT attempt, sides FROM rounds WHERE match_id=? AND round_no=2 ORDER BY attempt", (m,))
        self.assertEqual([r["sides"] for r in r2], ["home,away", "away"])         # home winner stayed locked
        self.assertEqual(len({s["winner_user_id"] for s in slots}), 6)            # six different winners
        check_conservation(self, m)

    async def test_too_fast_answers_are_disqualified_and_flagged(self):
        a, b = mk_user("a", 1_000_000), mk_user("b", 1_000_000)
        m = mk_match()
        betting.place_stake(a, m, "home", 100_000)
        betting.place_stake(b, m, "home", 100_000)
        settlement.finish_match(m, 1, 0)
        fast_settings(min_human_ms=300, max_retakes=0)
        # a answers instantly (bot), b answers after a human-like delay
        await self.run_game(m, [(a["id"], lambda g: ("right", 0.0)), (b["id"], lambda g: ("right", 0.4))])
        w = db.rows("SELECT * FROM slots WHERE match_id=? AND status='won'", (m,))
        self.assertTrue(all(s["winner_user_id"] == b["id"] for s in w))
        self.assertGreaterEqual(db.one("SELECT COUNT(*) n FROM flags WHERE kind='too_fast' AND user_id=?",
                                       (a["id"],))["n"], 1)

    async def test_latency_compensation_levels_the_field(self):
        # b has 400ms network delay (measured), a has none: b answers 300ms "later" in raw time but is faster.
        game.RTT[1_000_001] = game.deque([400], maxlen=8)
        game.RTT[1_000_002] = game.deque([0], maxlen=8)
        self.assertEqual(game.latency_comp(1_000_001, 500), 400)
        self.assertEqual(game.latency_comp(1_000_001, 300), 300)     # capped
        self.assertEqual(game.latency_comp(999, 500), 0)             # unmeasured -> no credit


if __name__ == "__main__":
    unittest.main()
