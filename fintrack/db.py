from __future__ import annotations

import os
import sqlite3
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    type TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'uncategorized',
    amount REAL NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    method TEXT NOT NULL DEFAULT '',
    is_transfer INTEGER NOT NULL DEFAULT 0,
    is_cc_payment INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'manual',
    dedupe_key TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_txns_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_txns_type ON transactions(type);
CREATE INDEX IF NOT EXISTS idx_txns_cat ON transactions(category);

CREATE TABLE IF NOT EXISTS budgets (
    category TEXT PRIMARY KEY,
    monthly_limit REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS projections (
    month TEXT PRIMARY KEY,
    income REAL,
    savings_target REAL,
    sip_plan REAL,
    invest_plan REAL
);

CREATE TABLE IF NOT EXISTS sips (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    amfi_code TEXT NOT NULL DEFAULT '',
    monthly_amount REAL NOT NULL DEFAULT 0,
    start_date TEXT,
    step_up_pct REAL NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS sip_lots (
    id INTEGER PRIMARY KEY,
    sip_id INTEGER NOT NULL REFERENCES sips(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    amount REAL NOT NULL,
    nav REAL,
    units REAL
);

CREATE TABLE IF NOT EXISTS stock_txns (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
    qty REAL NOT NULL,
    price REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stock_sym ON stock_txns(symbol, date);

CREATE TABLE IF NOT EXISTS balances (
    instrument TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('bank','mf','sip','stock','rd')),
    amount REAL NOT NULL,
    as_of TEXT
);

CREATE TABLE IF NOT EXISTS prices (
    symbol TEXT PRIMARY KEY,
    price REAL NOT NULL,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS rules (
    keyword TEXT PRIMARY KEY,
    category TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

DEFAULT_META = {
    "savings_rate_baseline": "62",
    "default_return_pct": "12",
}

DEFAULT_RULES = [
    ("swiggy", "food"),
    ("zomato", "food"),
    ("dominos", "food"),
    ("pizza", "food"),
    ("uber", "travel"),
    ("ola", "travel"),
    ("rapido", "travel"),
    ("irctc", "travel"),
    ("indigo", "travel"),
    ("amazon", "shopping"),
    ("flipkart", "shopping"),
    ("myntra", "shopping"),
    ("meesho", "shopping"),
    ("bigbasket", "groceries"),
    ("blinkit", "groceries"),
    ("zepto", "groceries"),
    ("dmart", "groceries"),
    ("rent", "rent"),
    ("electricity", "utilities"),
    ("bescom", "utilities"),
    ("gas bill", "utilities"),
    ("jio", "mobile"),
    ("airtel", "mobile"),
    ("bsnl", "mobile"),
    ("netflix", "entertainment"),
    ("hotstar", "entertainment"),
    ("spotify", "entertainment"),
    ("bookmyshow", "entertainment"),
    ("apollo", "health"),
    ("pharmeasy", "health"),
    ("1mg", "health"),
    ("hospital", "health"),
    ("emi", "emi"),
    ("loan", "emi"),
    ("insurance", "insurance"),
    ("lic", "insurance"),
    ("salary", "income"),
]


def db_path() -> Path:
    env = os.environ.get("FINTRACK_DB")
    if env:
        return Path(env).expanduser()
    return PROJECT_DIR / "data" / "finance.db"


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    for k, v in DEFAULT_META.items():
        conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)", (k, v)
        )
    for keyword, category in DEFAULT_RULES:
        conn.execute(
            "INSERT OR IGNORE INTO rules(keyword, category) VALUES (?, ?)",
            (keyword, category),
        )
    conn.commit()
    os.chmod(path, 0o600)
    return conn
