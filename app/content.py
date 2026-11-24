"""Site copy that the admin can edit without touching code. Rendered as plain text in the browser (no HTML)."""
import time

from . import db

DEFAULTS = {
    "site_name": "Fan Millionaire",
    "tagline": "Back your club. Prove your football brain. Win the pot.",
    "banner": "",
    "hero_title": "Your club. Your pot. Your moment.",
    "hero_text": "Put your money behind your team. When the final whistle blows, the winning fans take both pots "
                 "and the fastest football minds win the prizes.",
    "hero_image_url": "",
    "how_title": "How Fan Millionaire works",
    "how_text": "1. Pick a match and stake on your club. Every fan stake goes into that club's pot.\n"
                "2. When the match ends, the winning club's pot takes the losing club's pot too.\n"
                "3. Fans of the winning club play 3 trivia rounds. The fastest correct answer in each round "
                "wins a prize from the pot.\n"
                "4. In a draw, fans of both clubs play. Each round, the fastest correct fan from each club wins.\n"
                "5. You must have staked on a match to play its trivia, and you can win only once per pot.",
    "rules_title": "Rules",
    "rules_text": "Straight win: one winner per round, each wins 15% of the total pot.\n"
                  "Draw: one winner per club per round, each wins 7.5% of the total pot.\n"
                  "If nobody answers a round correctly, the round is repeated with a new question (up to 3 times).\n"
                  "The platform keeps the rest of the pot and any prize that cannot be claimed.\n"
                  "Answer times are measured by our server and adjusted for network delay so everyone gets a fair race.",
    "faq_title": "FAQ",
    "faq_text": "Can I stake on both clubs? No. You back one club per match.\n"
                "What if my club has no other fans? You still play for the pot that exists.\n"
                "How do I get paid? Winnings go to your wallet and you can withdraw to your Nigerian bank account.",
    "terms_title": "Terms & responsible play",
    "terms_text": "You must be 18 or older. Only stake money you can afford to lose. "
                  "Replace this text with your final terms and conditions.",
    "footer_text": "© Fan Millionaire. 18+ only. Play responsibly.",
}


def get_all():
    out = dict(DEFAULTS)
    for r in db.q("SELECT key,value FROM content"):
        if r["key"] in DEFAULTS:
            out[r["key"]] = r["value"]
    return out


def update(values):
    for k, v in values.items():
        if k not in DEFAULTS:
            raise ValueError(f"Unknown content key: {k}")
        if not isinstance(v, str) or len(v) > 8000:
            raise ValueError(f"{k} must be text under 8000 characters")
    with db.tx() as c:
        for k, v in values.items():
            c.execute("INSERT INTO content(key,value,updated_at) VALUES(?,?,?) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                      (k, v, int(time.time())))
