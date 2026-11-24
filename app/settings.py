"""Admin-editable settings (all integers). Defaults reflect the agreed Fan Millionaire rules."""
from . import db

DEFAULTS = {
    # payouts, in basis points of the TOTAL pot (100 bp = 1%)
    "win_round_bp": 1500,        # straight win: 15% to the single winner of each round
    "draw_round_bp": 1500,       # draw: 15% per round, shared by the two team winners (7.5% each)
    "draw_one_sided_full_prize": 0,  # 1 = in a draw with only one team staked, that winner gets the full 15%
    # game flow
    "max_retakes": 3,            # extra attempts for a round before its prize goes to the platform
    "countdown_s": 5,            # "get ready" countdown before each question
    "answer_window_s": 15,       # seconds players have to answer
    "pause_s": 7,                # pause after a round result
    "trivia_delay_minutes": 10,  # default delay between final whistle and trivia start
    # latency fairness / anti-cheat
    "grace_ms": 600,             # wait after the first correct answer so slower-network players are still counted
    "latency_cap_ms": 500,       # max network delay we compensate for
    "min_human_ms": 800,         # answers faster than this (after compensation) are disqualified + flagged
    # money limits (kobo)
    "min_stake_kobo": 10000,         # N100
    "max_stake_kobo": 50000000,      # N500,000 per user per match
    "min_withdraw_kobo": 100000,     # N1,000
    "withdrawals_require_approval": 1,
    # results feed: lock pots automatically once the feed reports full time (OFF until you trust the feed)
    "auto_settle": 0,
    "auto_settle_delay_minutes": 10,
}

_BOUNDS = {
    "win_round_bp": (0, 3333), "draw_round_bp": (0, 3333), "draw_one_sided_full_prize": (0, 1),
    "max_retakes": (0, 10), "countdown_s": (0, 60), "answer_window_s": (1, 120), "pause_s": (0, 60),
    "trivia_delay_minutes": (0, 1440), "grace_ms": (0, 5000), "latency_cap_ms": (0, 3000),
    "min_human_ms": (0, 5000), "min_stake_kobo": (100, 10**10), "max_stake_kobo": (100, 10**12),
    "min_withdraw_kobo": (100, 10**12), "withdrawals_require_approval": (0, 1),
    "auto_settle": (0, 1), "auto_settle_delay_minutes": (0, 240),
}


def all():
    out = dict(DEFAULTS)
    for r in db.q("SELECT key,value FROM settings"):
        if r["key"] in DEFAULTS:
            try:
                out[r["key"]] = int(r["value"])
            except ValueError:
                pass
    return out


def get(key):
    return all()[key]


def update(values):
    """Validate and save. Raises ValueError with a readable message."""
    cur = all()
    new = dict(cur)
    for k, v in values.items():
        if k not in DEFAULTS:
            raise ValueError(f"Unknown setting: {k}")
        try:
            v = int(v)
        except (TypeError, ValueError):
            raise ValueError(f"{k} must be a whole number")
        lo, hi = _BOUNDS[k]
        if not lo <= v <= hi:
            raise ValueError(f"{k} must be between {lo} and {hi}")
        new[k] = v
    if new["min_stake_kobo"] > new["max_stake_kobo"]:
        raise ValueError("Minimum stake cannot be above maximum stake")
    # 3 rounds must never pay out more than the pot
    if new["win_round_bp"] * 3 > 10000 or new["draw_round_bp"] * 3 > 10000:
        raise ValueError("Round payouts would exceed 100% of the pot")
    with db.tx() as c:
        for k in values:
            c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (k, str(new[k])))
    return new
