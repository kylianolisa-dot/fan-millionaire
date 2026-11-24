"""Results feed tests. No network: a fake provider stands in for football-data.org."""
import time
import unittest

from app import betting, db, feed, settings, settlement
from tests.test_core import fresh_db, mk_user


class FakeProvider:
    name, demo = "fake", False

    def __init__(self):
        self.data = {}

    def by_ids(self, ids):
        return [self.data[i] for i in ids if i in self.data]


def fx(ext="100", home="Chelsea", away="Arsenal", ko=None):
    return {"external_id": ext, "home": home, "away": away, "competition": "Premier League",
            "kickoff_at": ko or int(time.time()) + 3600}


def feed_row(ext, status, h=None, a=None):
    return {"external_id": ext, "status": status, "home_score": h, "away_score": a, "home": "x", "away": "y",
            "competition": "", "kickoff_at": 0}


class ParseTests(unittest.TestCase):
    def test_regular_finish_uses_full_time(self):
        m = {"id": 7, "utcDate": "2026-11-29T17:30:00Z", "status": "FINISHED", "homeTeam": {"shortName": "Chelsea"},
             "awayTeam": {"name": "Arsenal FC"}, "competition": {"name": "Premier League"},
             "score": {"duration": "REGULAR", "fullTime": {"home": 2, "away": 1}}}
        p = feed.parse_match(m)
        self.assertEqual((p["home"], p["away"], p["home_score"], p["away_score"], p["status"]), ("Chelsea", "Arsenal FC", 2, 1, "finished"))

    def test_extra_time_uses_the_90_minute_score(self):
        m = {"id": 8, "utcDate": "2026-11-29T17:30:00Z", "status": "FINISHED", "homeTeam": {"name": "A"}, "awayTeam": {"name": "B"},
             "score": {"duration": "EXTRA_TIME", "fullTime": {"home": 3, "away": 2}, "regularTime": {"home": 2, "away": 2}}}
        p = feed.parse_match(m)
        self.assertEqual((p["home_score"], p["away_score"]), (2, 2))          # a draw after 90 minutes

    def test_missing_score_and_status_names(self):
        self.assertIsNone(feed.ninety_minute_score({"score": {"fullTime": {"home": None, "away": None}}}))
        self.assertEqual([feed.norm_status(s) for s in ("TIMED", "IN_PLAY", "POSTPONED", "AWARDED", "???")],
                         ["scheduled", "live", "postponed", "awarded", "unknown"])


class FeedFlowTests(unittest.TestCase):
    def setUp(self):
        fresh_db()
        self.p = FakeProvider()
        self.t0 = int(time.time())

    def match(self, ext="100", stake=True):
        mid = feed.add_fixture(fx(ext))
        if stake:
            a, b = mk_user("a" + ext, 1_000_000), mk_user("b" + ext, 1_000_000)
            betting.place_stake(a, mid, "home", 100_000)
            betting.place_stake(b, mid, "away", 100_000)
        db.run("UPDATE matches SET kickoff_at=? WHERE id=?", (self.t0 - 7200, mid))       # kicked off 2h ago
        return mid

    def status(self, mid):
        return db.one("SELECT status FROM matches WHERE id=?", (mid,))["status"]

    def test_import_and_duplicate_import(self):
        mid = feed.add_fixture(fx("1"))
        self.assertEqual(db.one("SELECT staking_closes_at, kickoff_at FROM matches WHERE id=?", (mid,))["staking_closes_at"],
                         db.one("SELECT kickoff_at FROM matches WHERE id=?", (mid,))["kickoff_at"])
        with self.assertRaises(feed.FeedError):
            feed.add_fixture(fx("1"))
        with self.assertRaises(feed.FeedError):
            feed.add_fixture(fx("2", ko=self.t0 - 60))                                    # already kicked off
        with self.assertRaises(feed.FeedError):
            feed.add_fixture(fx("3", home="Chelsea", away="chelsea"))

    def test_auto_settle_is_off_by_default(self):
        mid = self.match()
        self.p.data["100"] = feed_row("100", "finished", 2, 1)
        feed.sync_results(self.p, self.t0)
        self.assertEqual(feed.settle_due(self.t0 + 3600), [])
        self.assertEqual(self.status(mid), "open")

    def test_waits_for_delay_then_settles_with_feed_score(self):
        mid = self.match()
        settings.update({"auto_settle": 1, "auto_settle_delay_minutes": 10})
        self.p.data["100"] = feed_row("100", "finished", 2, 1)
        feed.sync_results(self.p, self.t0)
        self.assertEqual(feed.settle_due(self.t0 + 9 * 60), [])                            # too early
        self.assertEqual(feed.settle_due(self.t0 + 10 * 60), [mid])
        m = db.one("SELECT * FROM matches WHERE id=?", (mid,))
        self.assertEqual((m["status"], m["outcome"], m["home_score"], m["away_score"]), ("finished", "home", 2, 1))
        self.assertEqual(feed.settle_due(self.t0 + 3600), [])                              # never settles twice

    def test_score_change_restarts_the_wait(self):
        mid = self.match()
        settings.update({"auto_settle": 1, "auto_settle_delay_minutes": 10})
        self.p.data["100"] = feed_row("100", "finished", 1, 1)
        feed.sync_results(self.p, self.t0)
        self.p.data["100"] = feed_row("100", "finished", 2, 1)                             # feed corrected the score
        feed.sync_results(self.p, self.t0 + 8 * 60)
        self.assertEqual(feed.settle_due(self.t0 + 12 * 60), [])                           # 12 min since first report: not yet
        self.assertEqual(feed.settle_due(self.t0 + 18 * 60), [mid])
        self.assertEqual(db.one("SELECT outcome FROM matches WHERE id=?", (mid,))["outcome"], "home")

    def test_hold_blocks_auto_settle(self):
        mid = self.match()
        settings.update({"auto_settle": 1, "auto_settle_delay_minutes": 0})
        db.run("UPDATE matches SET settle_hold=1 WHERE id=?", (mid,))
        self.p.data["100"] = feed_row("100", "finished", 0, 0)
        feed.sync_results(self.p, self.t0)
        self.assertEqual(feed.settle_due(self.t0 + 60), [])
        db.run("UPDATE matches SET settle_hold=0 WHERE id=?", (mid,))
        self.assertEqual(feed.settle_due(self.t0 + 60), [mid])

    def test_postponed_and_awarded_never_auto_settle_and_raise_alerts(self):
        m1, m2 = self.match("100"), self.match("200")
        settings.update({"auto_settle": 1, "auto_settle_delay_minutes": 0})
        self.p.data["100"] = feed_row("100", "postponed")
        self.p.data["200"] = feed_row("200", "awarded", 3, 0)
        feed.sync_results(self.p, self.t0)
        self.assertEqual(feed.settle_due(self.t0 + 3600), [])
        ov = feed.overview(self.t0)
        self.assertEqual({a["status"] for a in ov["alerts"]}, {"postponed", "awarded"})
        self.assertEqual(ov["alerts"][0]["pot_kobo"], 200_000)
        settlement.cancel_match(m1)                                                         # admin refunds the postponed one
        self.assertEqual(self.status(m1), "cancelled")

    def test_match_that_leaves_finished_state_loses_its_timer(self):
        mid = self.match()
        settings.update({"auto_settle": 1, "auto_settle_delay_minutes": 5})
        self.p.data["100"] = feed_row("100", "finished", 1, 0)
        feed.sync_results(self.p, self.t0)
        self.p.data["100"] = feed_row("100", "live")                                        # feed reverts (reopened/error)
        feed.sync_results(self.p, self.t0 + 60)
        self.assertEqual(feed.settle_due(self.t0 + 3600), [])
        self.assertEqual(self.status(mid), "open")

    def test_settle_now_needs_a_feed_score(self):
        mid = self.match()
        with self.assertRaises(feed.FeedError):
            feed.settle_from_feed(mid)
        self.p.data["100"] = feed_row("100", "finished", 1, 3)
        feed.sync_results(self.p, self.t0)
        feed.settle_from_feed(mid)
        self.assertEqual(db.one("SELECT outcome FROM matches WHERE id=?", (mid,))["outcome"], "away")

    def test_overview_pending_shows_countdown_only_when_auto_on(self):
        mid = self.match()
        self.p.data["100"] = feed_row("100", "finished", 2, 2)
        feed.sync_results(self.p, self.t0)
        self.assertIsNone(feed.overview(self.t0)["pending"][0]["settle_at"])
        settings.update({"auto_settle": 1, "auto_settle_delay_minutes": 10})
        pend = feed.overview(self.t0)["pending"][0]
        self.assertEqual((pend["outcome"], pend["settle_at"]), ("draw", self.t0 + 600))


if __name__ == "__main__":
    unittest.main()
