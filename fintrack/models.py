from __future__ import annotations

import hashlib
import sqlite3
from datetime import date
from typing import Any

INCOME_TYPES = ("salary", "bonus", "itr", "credit")
LIVING_TYPES = ("expense", "debit")
INVEST_TYPES = ("saving", "sip", "invest")
ALL_TXN_TYPES = INCOME_TYPES + LIVING_TYPES + INVEST_TYPES


def _ym(month: str | None) -> str:
    if month:
        return month
    return date.today().strftime("%Y-%m")


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def dedupe(source: str, *parts: Any) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha256(f"{source}:{raw}".encode()).hexdigest()[:16]


def add_txn(
    conn: sqlite3.Connection,
    d: str,
    type_: str,
    amount: float,
    category: str = "",
    note: str = "",
    method: str = "",
    is_transfer: bool = False,
    is_cc_payment: bool = False,
    source: str = "manual",
    key: str | None = None,
) -> int | None:
    cur = conn.execute(
        """INSERT OR IGNORE INTO transactions
           (date, type, category, amount, note, method,
            is_transfer, is_cc_payment, source, dedupe_key)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            d,
            type_,
            category or "uncategorized",
            amount,
            note,
            method,
            int(is_transfer),
            int(is_cc_payment),
            source,
            key or dedupe(source, d, type_, amount, note),
        ),
    )
    conn.commit()
    return cur.lastrowid if cur.rowcount else None


def txn_get(conn: sqlite3.Connection, txn_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM transactions WHERE id=?", (txn_id,)
    ).fetchone()


def txn_update(
    conn: sqlite3.Connection,
    txn_id: int,
    type_: str | None = None,
    category: str | None = None,
) -> bool:
    if type_ or category:
        cur = conn.execute(
            "UPDATE transactions SET"
            " type=COALESCE(?, type), category=COALESCE(?, category)"
            " WHERE id=?",
            (type_, category, txn_id),
        )
        conn.commit()
        return bool(cur.rowcount)
    return False


def txn_delete(conn: sqlite3.Connection, txn_id: int) -> bool:
    cur = conn.execute("DELETE FROM transactions WHERE id=?", (txn_id,))
    conn.commit()
    return bool(cur.rowcount)


def list_txns(
    conn: sqlite3.Connection,
    month: str | None = None,
    type_: str | None = None,
    category: str | None = None,
    limit: int = 100,
) -> list[sqlite3.Row]:
    q = "SELECT * FROM transactions WHERE 1=1"
    args: list[Any] = []
    if month:
        q += " AND strftime('%Y-%m', date)=?"
        args.append(_ym(month))
    if type_:
        q += " AND type=?"
        args.append(type_)
    if category:
        q += " AND category=?"
        args.append(category)
    q += " ORDER BY date DESC, id DESC LIMIT ?"
    args.append(limit)
    return conn.execute(q, args).fetchall()


def distinct_categories(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT category FROM transactions "
        "UNION SELECT category FROM budgets ORDER BY 1"
    ).fetchall()
    return [r[0] for r in rows]


def month_summary(conn: sqlite3.Connection, month: str | None = None) -> dict:
    ym = _ym(month)

    def scalar(sql: str, args: tuple) -> float:
        row = conn.execute(sql, args).fetchone()
        return row[0] or 0.0

    income = scalar(
        "SELECT SUM(amount) FROM transactions "
        "WHERE strftime('%Y-%m', date)=? AND type IN "
        "('salary','bonus','itr','credit') AND is_cc_payment=0",
        (ym,),
    )
    living = scalar(
        "SELECT SUM(amount) FROM transactions "
        "WHERE strftime('%Y-%m', date)=? AND type IN ('expense','debit') "
        "AND is_transfer=0 AND is_cc_payment=0",
        (ym,),
    )
    invested = scalar(
        "SELECT SUM(amount) FROM transactions "
        "WHERE strftime('%Y-%m', date)=? AND type IN "
        "('saving','sip','invest')",
        (ym,),
    )
    saved = income - living
    rate = (saved / income * 100) if income else 0.0
    return {
        "month": ym,
        "income": income,
        "living": living,
        "invested": invested,
        "saved": saved,
        "rate": rate,
    }


def category_breakdown(
    conn: sqlite3.Connection, month: str | None = None
) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT category, SUM(amount) AS total, COUNT(*) AS n
           FROM transactions
           WHERE strftime('%Y-%m', date)=?
             AND type IN ('expense','debit')
             AND is_transfer=0 AND is_cc_payment=0
           GROUP BY category ORDER BY total DESC""",
        (_ym(month),),
    ).fetchall()


def monthly_series(conn: sqlite3.Connection, months: int = 12) -> list[dict]:
    out = []
    today = date.today()
    y, m = today.year, today.month
    for _ in range(months):
        ym = f"{y:04d}-{m:02d}"
        out.append(month_summary(conn, ym))
        m -= 1
        if m == 0:
            y -= 1
            m = 12
    return list(reversed(out))


def budget_set(conn: sqlite3.Connection, category: str, limit: float) -> None:
    conn.execute(
        "INSERT INTO budgets(category, monthly_limit) VALUES (?, ?) "
        "ON CONFLICT(category) DO UPDATE SET monthly_limit=excluded.monthly_limit",
        (category.lower(), limit),
    )
    conn.commit()


def budget_all(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM budgets ORDER BY category"
    ).fetchall()


def projection_upsert(
    conn: sqlite3.Connection,
    month: str,
    income: float | None,
    savings_target: float | None,
    sip_plan: float | None,
    invest_plan: float | None,
) -> None:
    conn.execute(
        "INSERT INTO projections(month, income, savings_target, sip_plan, invest_plan)"
        " VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(month) DO UPDATE SET"
        "   income=COALESCE(excluded.income, income),"
        "   savings_target=COALESCE(excluded.savings_target, savings_target),"
        "   sip_plan=COALESCE(excluded.sip_plan, sip_plan),"
        "   invest_plan=COALESCE(excluded.invest_plan, invest_plan)",
        (month, income, savings_target, sip_plan, invest_plan),
    )
    conn.commit()


def projection_get(
    conn: sqlite3.Connection, month: str | None = None
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM projections WHERE month=?", (_ym(month),)
    ).fetchone()


def rule_add(conn: sqlite3.Connection, keyword: str, category: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO rules(keyword, category) VALUES (?, ?)",
        (keyword.lower(), category.lower()),
    )
    conn.commit()


def rule_all(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM rules ORDER BY category, keyword"
    ).fetchall()


def rule_match(conn: sqlite3.Connection, description: str) -> str | None:
    desc = description.lower()
    row = conn.execute(
        "SELECT category FROM rules WHERE instr(?, keyword) > 0 "
        "ORDER BY length(keyword) DESC LIMIT 1",
        (desc,),
    ).fetchone()
    return row["category"] if row else None


def balance_set(
    conn: sqlite3.Connection,
    instrument: str,
    kind: str,
    amount: float,
    as_of: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO balances(instrument, kind, amount, as_of)"
        " VALUES (?, ?, ?, ?)"
        " ON CONFLICT(instrument) DO UPDATE SET"
        "   kind=excluded.kind, amount=excluded.amount,"
        "   as_of=COALESCE(excluded.as_of, balances.as_of)",
        (instrument.lower(), kind, amount, as_of),
    )
    conn.commit()


def balance_all(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM balances ORDER BY kind, instrument"
    ).fetchall()


def liquid_total(conn: sqlite3.Connection) -> float:
    row = conn.execute(
        "SELECT SUM(amount) FROM balances WHERE kind='bank'"
    ).fetchone()
    return row[0] or 0.0


def avg_burn(conn: sqlite3.Connection, months: int = 3) -> float:
    vals = [
        s["living"]
        for s in monthly_series(conn, months + 1)[:-1]
        if s["living"] > 0
    ]
    return sum(vals) / len(vals) if vals else 0.0


def sip_add(
    conn: sqlite3.Connection,
    name: str,
    monthly_amount: float,
    start_date: str | None,
    step_up_pct: float = 0.0,
    amfi_code: str = "",
) -> int:
    conn.execute(
        "INSERT INTO sips(name, amfi_code, monthly_amount, start_date,"
        " step_up_pct, active) VALUES (?, ?, ?, ?, ?, 1)"
        " ON CONFLICT(name) DO UPDATE SET monthly_amount=excluded.monthly_amount,"
        " step_up_pct=excluded.step_up_pct, amfi_code=excluded.amfi_code",
        (name, amfi_code, monthly_amount, start_date, step_up_pct),
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM sips WHERE name=?", (name,)
    ).fetchone()[0]


def sip_by_name(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    row = conn.execute(
        "SELECT * FROM sips WHERE name LIKE ? ORDER BY name LIMIT 1",
        (f"%{name}%",),
    ).fetchone()
    if not row:
        raise ValueError(f"no SIP matching {name!r}")
    return row


def sip_log(
    conn: sqlite3.Connection,
    sip_id: int,
    d: str,
    amount: float,
    nav: float | None,
    units: float | None = None,
) -> int | None:
    if units is None and nav:
        units = amount / nav
    lot_key = dedupe("siplot", sip_id, d, amount)
    cur = conn.execute(
        "INSERT OR IGNORE INTO sip_lots(sip_id, date, amount, nav, units)"
        " VALUES (?, ?, ?, ?, ?)",
        (sip_id, d, amount, nav, units),
    )
    if not cur.rowcount:
        return None
    add_txn(
        conn, d, "sip", amount, category="sip", note="SIP instalment",
        source="manual", key=dedupe("txn-siplot", sip_id, d, amount),
    )
    conn.commit()
    return cur.lastrowid


def sip_set_active(
    conn: sqlite3.Connection, sip_id: int, active: bool
) -> None:
    conn.execute(
        "UPDATE sips SET active=? WHERE id=?", (int(active), sip_id)
    )
    conn.commit()


def portfolio_sips(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT s.*, COALESCE(SUM(l.amount), 0) AS invested,
                  SUM(l.units) AS units,
                  MAX(l.nav) AS latest_nav
           FROM sips s LEFT JOIN sip_lots l ON l.sip_id = s.id
           GROUP BY s.id ORDER BY s.name"""
    ).fetchall()
    out = []
    for r in rows:
        units = r["units"]
        value = units * r["latest_nav"] if units and r["latest_nav"] else r["invested"]
        gain = value - r["invested"]
        out.append(
            {
                "name": r["name"],
                "active": bool(r["active"]),
                "monthly": r["monthly_amount"],
                "invested": r["invested"],
                "value": value,
                "gain": gain,
                "gain_pct": (gain / r["invested"] * 100) if r["invested"] else 0.0,
                "nav": r["latest_nav"],
                "as_of": conn.execute(
                    "SELECT MAX(date) FROM sip_lots WHERE sip_id=?",
                    (r["id"],),
                ).fetchone()[0],
            }
        )
    return out


def stock_record(
    conn: sqlite3.Connection,
    symbol: str,
    d: str,
    side: str,
    qty: float,
    price: float,
) -> int:
    held = stock_holdings(conn).get(symbol.upper())
    if side == "SELL" and (held is None or held["qty"] < qty - 1e-9):
        raise ValueError(
            f"cannot SELL {qty} of {symbol}: only "
            f"{held['qty'] if held else 0} held"
        )
    cur = conn.execute(
        "INSERT INTO stock_txns(symbol, date, side, qty, price)"
        " VALUES (?, ?, ?, ?, ?)",
        (symbol.upper(), d, side, qty, price),
    )
    type_ = "invest"
    note = f"{side} {qty} {symbol.upper()} @ {price}"
    key = dedupe("stocktxn", symbol, d, side, qty, price)
    amount = qty * price
    add_txn(
        conn, d, type_, amount, category="stocks", note=note,
        source="manual", key=key,
    )
    conn.commit()
    return cur.lastrowid


def stock_holdings(conn: sqlite3.Connection) -> dict[str, dict]:
    txns = conn.execute(
        "SELECT * FROM stock_txns ORDER BY date, id"
    ).fetchall()
    holdings: dict[str, dict] = {}
    realized: dict[str, float] = {}
    for t in txns:
        sym = t["symbol"]
        h = holdings.setdefault(sym, {"qty": 0.0, "avg": 0.0})
        realized.setdefault(sym, 0.0)
        if t["side"] == "BUY":
            total_cost = h["avg"] * h["qty"] + t["price"] * t["qty"]
            h["qty"] += t["qty"]
            h["avg"] = total_cost / h["qty"] if h["qty"] else 0.0
        else:
            sell_qty = min(t["qty"], h["qty"])
            realized[sym] += (t["price"] - h["avg"]) * sell_qty
            h["qty"] -= sell_qty
        if h["qty"] <= 1e-9:
            h["qty"] = max(h["qty"], 0.0)
    for sym, h in holdings.items():
        price_row = conn.execute(
            "SELECT price, updated_at FROM prices WHERE symbol=?", (sym,)
        ).fetchone()
        last_price = price_row["price"] if price_row else h["avg"]
        as_of = price_row["updated_at"] if price_row else None
        h["last_price"] = last_price
        h["priced_at"] = as_of
        h["value"] = h["qty"] * last_price
        h["cost"] = h["qty"] * h["avg"]
        h["unrealized"] = h["value"] - h["cost"]
        h["realized"] = realized.get(sym, 0.0)
    return holdings


def price_set(
    conn: sqlite3.Connection, symbol: str, price: float, as_of: str
) -> None:
    conn.execute(
        "INSERT INTO prices(symbol, price, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT(symbol) DO UPDATE SET"
        "   price=excluded.price, updated_at=excluded.updated_at",
        (symbol.upper(), price, as_of),
    )
    conn.commit()


def xirr_flows(conn: sqlite3.Connection) -> tuple[list[tuple[date, float]], float]:
    flows: list[tuple[date, float]] = []
    for r in conn.execute("SELECT date, amount FROM sip_lots"):
        flows.append((date.fromisoformat(r["date"]), -r["amount"]))
    for t in conn.execute("SELECT * FROM stock_txns"):
        amt = t["qty"] * t["price"]
        sign = -1.0 if t["side"] == "BUY" else 1.0
        flows.append((date.fromisoformat(t["date"]), sign * amt))
    terminal = 0.0
    for sym, h in stock_holdings(conn).items():
        terminal += h["value"]
    for p in portfolio_sips(conn):
        terminal += p["value"]
    if terminal > 0:
        flows.append((date.today(), terminal))
    return flows, terminal


def tracked_investment_value(conn: sqlite3.Connection) -> float:
    _, terminal = xirr_flows(conn)
    return terminal


def net_worth(conn: sqlite3.Connection) -> dict[str, float]:
    by_kind: dict[str, float] = {}
    for b in balance_all(conn):
        by_kind[b["kind"]] = by_kind.get(b["kind"], 0.0) + b["amount"]
    stocks_market = sum(h["value"] for h in stock_holdings(conn).values())
    sips_value = sum(p["value"] for p in portfolio_sips(conn))
    if "stock" in by_kind:
        stocks_market = 0.0
    if "sip" in by_kind:
        sips_value = 0.0
    total = (
        by_kind.get("bank", 0.0)
        + by_kind.get("rd", 0.0)
        + by_kind.get("mf", 0.0)
        + by_kind.get("sip", 0.0)
        + stocks_market
        + sips_value
    )
    return {
        "bank": by_kind.get("bank", 0.0),
        "rd": by_kind.get("rd", 0.0),
        "mf": by_kind.get("mf", 0.0),
        "sip_snapshot": by_kind.get("sip", 0.0),
        "stocks_market": stocks_market,
        "sips_tracked": sips_value,
        "total": total,
    }
