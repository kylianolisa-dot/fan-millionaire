# Fan Millionaire

Fans stake on their club's pot. When the match ends, the winning club's fans play 3 timed trivia rounds for the pot
(in a draw, fans of both clubs play). Black / gold / white web app, real Paystack payments, admin panel.

## Run it

```bash
pip install -r requirements.txt
ADMIN_PASSWORD='choose-a-strong-one' python -m uvicorn app.main:app --port 8000
```
Open http://localhost:8000 (fans) and http://localhost:8000/admin.html (admin: `admin@fanmillionaire.local`).
With no Paystack key it runs in **test mode** (fake checkout, no real money). If you omit `ADMIN_PASSWORD`, a random
one is printed once on first start.

Tests: `python -m pytest tests -q` (37 tests: payout rules, ledger, game scenarios, webhooks, results feed).
End-to-end against a running server: `python tests/e2e_http.py`.

## The rules (all editable in Admin → Settings)

| | Straight win | Draw |
|---|---|---|
| Who plays | Fans who staked on the winning club (if it has no stakers, the losing club's stakers play their own pot) | Fans who staked on either club |
| Winners per round | 1 (fastest correct answer) | 1 per club |
| Prize per round | 15% of the total pot | 15%, shared: 7.5% each |
| Platform keeps | 55% + unclaimed prizes | 55% + unclaimed prizes |

* Must stake to play. One club per match per fan. One win per fan per pot.
* Nobody correct in a round: round repeats with a new question, up to 3 retakes, then the prize goes to the platform.
* Draw, only one club has a correct answer: that winner is locked in; only the other club retakes.
* A club with nobody left to play: its prize goes to the platform (recorded in Admin → Unclaimed funds with club and reason).
* Cancelled match: every stake refunded.

**Fairness:** the server clock decides. Each player's network round-trip is measured by the server over the live
stream and subtracted (capped, using the minimum of recent samples so it can't be gamed). **Anti-cheat:** one locked
answer per round, eligibility enforced server-side, answers faster than a human can read are disqualified and flagged,
shared-device accounts are flagged, flagged fans' withdrawals need manual approval.

## Football results feed (Admin → Fixtures & results)

Pick a competition, tap **Add match** on the fixtures you want. The server checks the feed every 2 minutes; when a match
reaches full time it appears in "Finished matches waiting to settle" with the feed's score.

* **Auto-settle is OFF by default.** Turn it on in that screen (or Settings). It waits `auto_settle_delay_minutes` (10) after
  full time, restarts the wait if the feed changes the score, and never settles a held, postponed, suspended, cancelled or
  awarded match. Settling locks the pots and cannot be undone, so settle by hand for the first few weeks.
* Postponed / cancelled matches raise a red alert with **Cancel and refund** (refunds are never automatic).
* The score used is the result after **90 minutes** (`regularTime` when a cup tie went to extra time).
* Provider: [football-data.org](https://www.football-data.org) v4. Set `FOOTBALL_DATA_API_KEY` (free key available). With no key the
  screen runs on **demo data** so you can try it safely. Check which competitions your plan includes; a 403 means "not in your plan".
  Nigerian league (NPFL) and AFCON coverage is not confirmed: check your provider before relying on it, or add those matches by hand
  in Matches & pots (that always works).

## Going live with Paystack

1. Get **live** keys from the Paystack dashboard. Set `PAYSTACK_SECRET_KEY=sk_live_...` (never commit it).
2. In Paystack → Settings → API: set the **webhook URL** to `https://YOUR-DOMAIN/api/paystack/webhook`.
3. Paystack → Settings → Preferences: **disable "Require OTP for transfers"**, otherwise payouts stop at `needs_attention`.
   Fund your Paystack balance (payouts come from it).
4. Set `APP_URL=https://YOUR-DOMAIN`, `COOKIE_SECURE=1`, and `TRUST_PROXY=1` if behind nginx/Cloudflare. Serve over HTTPS.
5. Do a ₦100 test deposit and a small withdrawal with **test keys first**, then switch to live keys.
6. Keep "Admin must approve every withdrawal" ON until you trust the system.
7. **Licence first.** Pooled stakes paid out on a match result are very likely gambling/lottery under Nigerian law, and
   Paystack will likely ask for your licence when you verify your business. Get legal advice before real money moves.

## Known limits (be aware before launch)

* Live updates use Server-Sent Events + normal requests (no WebSocket library was installable in the build sandbox). Same behaviour for this game.
* Run **one** server process (live game state is in memory). SQLite is fine for a launch; move to Postgres to scale.
* Live feed calls to football-data.org could not be tested from the build sandbox (no internet): parsing is tested against the documented format, and the flow with a fake provider. Starter trivia questions are samples: review them.
* Not included yet: email/phone verification, password reset, KYC/BVN checks, email/SMS notifications.
* Live Paystack calls could not be tested from the build sandbox (no internet). Webhook handling, signatures and refunds are unit-tested.
