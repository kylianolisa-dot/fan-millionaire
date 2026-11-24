"""SQLite storage. One shared connection, one lock, explicit transactions.

All money is stored as integer kobo (1 naira = 100 kobo). Never floats.
"""
import os
import sqlite3
import threading
from contextlib import contextmanager

from . import config

_lock = threading.RLock()
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, phone TEXT UNIQUE, name TEXT NOT NULL,
  pw_hash TEXT NOT NULL, pw_salt TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user',
  status TEXT NOT NULL DEFAULT 'active',
  balance_kobo INTEGER NOT NULL DEFAULT 0 CHECK(balance_kobo >= 0),
  device_id TEXT, signup_ip TEXT, age_confirmed INTEGER NOT NULL DEFAULT 0,
  bank_code TEXT, bank_name TEXT, account_number TEXT, account_name TEXT, recipient_code TEXT,
  created_at INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS sessions(
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL);

-- user_id NULL = the platform's own account
CREATE TABLE IF NOT EXISTS ledger(
  id INTEGER PRIMARY KEY, user_id INTEGER, type TEXT NOT NULL, amount_kobo INTEGER NOT NULL,
  ref TEXT UNIQUE NOT NULL, match_id INTEGER, note TEXT, created_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS ledger_user ON ledger(user_id);

CREATE TABLE IF NOT EXISTS matches(
  id INTEGER PRIMARY KEY, home TEXT NOT NULL, away TEXT NOT NULL, competition TEXT,
  kickoff_at INTEGER NOT NULL, staking_closes_at INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',            -- open | finished | cancelled
  home_score INTEGER, away_score INTEGER, outcome TEXT,   -- home | away | draw
  trivia_start_at INTEGER, created_at INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS stakes(
  id INTEGER PRIMARY KEY, match_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
  side TEXT NOT NULL CHECK(side IN ('home','away')),
  amount_kobo INTEGER NOT NULL CHECK(amount_kobo > 0), created_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS stakes_match ON stakes(match_id, side);

CREATE TABLE IF NOT EXISTS games(
  match_id INTEGER PRIMARY KEY, mode TEXT NOT NULL,        -- win | draw | none
  play_side TEXT, total_pot_kobo INTEGER NOT NULL, platform_base_kobo INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',                   -- pending | running | done | interrupted
  started_at INTEGER, finished_at INTEGER, error TEXT);

-- one row per prize slot (a round's prize for one winner)
CREATE TABLE IF NOT EXISTS slots(
  id INTEGER PRIMARY KEY, match_id INTEGER NOT NULL, round_no INTEGER NOT NULL,
  side TEXT NOT NULL, amount_kobo INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',                      -- open | won | unclaimed
  winner_user_id INTEGER, answer_ms INTEGER, round_id INTEGER, reason TEXT, resolved_at INTEGER);
CREATE INDEX IF NOT EXISTS slots_match ON slots(match_id);

CREATE TABLE IF NOT EXISTS questions(
  id INTEGER PRIMARY KEY, text TEXT NOT NULL, options TEXT NOT NULL, correct_index INTEGER NOT NULL,
  active INTEGER NOT NULL DEFAULT 1, times_used INTEGER NOT NULL DEFAULT 0);

CREATE TABLE IF NOT EXISTS rounds(
  id INTEGER PRIMARY KEY, match_id INTEGER NOT NULL, round_no INTEGER NOT NULL, attempt INTEGER NOT NULL,
  question_id INTEGER NOT NULL, sides TEXT NOT NULL, reveal_at_ms INTEGER, closed_at_ms INTEGER);

CREATE TABLE IF NOT EXISTS answers(
  id INTEGER PRIMARY KEY, round_id INTEGER NOT NULL, user_id INTEGER NOT NULL, side TEXT NOT NULL,
  option_index INTEGER NOT NULL, correct INTEGER NOT NULL, recv_ms INTEGER NOT NULL,
  raw_ms INTEGER NOT NULL, rtt_ms INTEGER NOT NULL, adjusted_ms INTEGER NOT NULL, flag TEXT,
  UNIQUE(round_id, user_id));

-- money the platform kept because nobody could/did claim it
CREATE TABLE IF NOT EXISTS unclaimed(
  id INTEGER PRIMARY KEY, match_id INTEGER NOT NULL, slot_id INTEGER UNIQUE NOT NULL, round_no INTEGER NOT NULL,
  side TEXT NOT NULL, club TEXT NOT NULL, amount_kobo INTEGER NOT NULL, reason TEXT NOT NULL, created_at INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS flags(
  id INTEGER PRIMARY KEY, user_id INTEGER, match_id INTEGER, kind TEXT NOT NULL, detail TEXT,
  resolved INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS payments(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, kind TEXT NOT NULL,      -- deposit | withdrawal
  reference TEXT UNIQUE NOT NULL, amount_kobo INTEGER NOT NULL,
  status TEXT NOT NULL, provider_ref TEXT, detail TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS content(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
"""

# columns added after the first release (applied to existing databases too)
MIGRATIONS = [
    ("matches", "external_id", "TEXT"), ("matches", "feed_status", "TEXT"), ("matches", "feed_home", "INTEGER"),
    ("matches", "feed_away", "INTEGER"), ("matches", "feed_ft_at", "INTEGER"), ("matches", "feed_checked_at", "INTEGER"),
    ("matches", "settle_hold", "INTEGER NOT NULL DEFAULT 0"),
]


def conn():
    global _conn
    with _lock:
        if _conn is None:
            os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
            c = sqlite3.connect(config.DB_PATH, check_same_thread=False, isolation_level=None)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA busy_timeout=5000")
            c.executescript(SCHEMA)
            for table, col, typ in MIGRATIONS:
                have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
                if col not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS matches_ext ON matches(external_id) WHERE external_id IS NOT NULL")
            _conn = c
        return _conn


def close():
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


@contextmanager
def tx():
    """Atomic write transaction. Everything inside commits together or not at all."""
    c = conn()
    with _lock:
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise


def q(sql, args=()):
    with _lock:
        return conn().execute(sql, args).fetchall()


def one(sql, args=()):
    with _lock:
        return conn().execute(sql, args).fetchone()


def run(sql, args=()):
    with tx() as c:
        cur = c.execute(sql, args)
        return cur.lastrowid


def rows(sql, args=()):
    return [dict(r) for r in q(sql, args)]
