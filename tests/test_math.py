from __future__ import annotations

import os
import sqlite3
from datetime import date
from pathlib import Path

import pytest

os.environ.setdefault("FINTRACK_DB", "/tmp/fintrack_pytest.db")

from fintrack import db, importer, math_tools, models  # noqa: E402
from fintrack.fmt import inr, inr_compact, mask_sensitive, parse_amount  # noqa: E402


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("FINTRACK_DB", str(tmp_path / "t.db"))
    c = db.connect()
    yield c
    c.close()


def test_inr_grouping():
    assert inr(100000) == "₹1,00,000"
    assert inr(6799985) == "₹67,99,985"
    assert inr(450) == "₹450"
    assert inr(-123456) == "-₹1,23,456"
    assert inr(1234.5) == "₹1,234.50"


def test_compact():
    assert inr_compact(13_020_000) == "₹1.30 Cr"
    assert inr_compact(100_000) == "₹1.00 L"
    assert inr_compact(-40_000) == "-₹40.0 K"


def test_parse_amount():
    assert parse_amount("50,000") == 50000.0
    assert parse_amount("Rs 8,667") == 8667.0
    assert parse_amount("1.5L") == 150_000.0
    assert parse_amount("10.00L") == 1_000_000.0
    assert parse_amount("2Cr") == 20_000_000.0
    assert parse_amount("₹450") == 450.0
    with pytest.raises(ValueError):
        parse_amount("-")


def test_mask():
    assert mask_sensitive("acct 1234567890123 done") == "acct *********0123 done"
    assert "1234567890123" not in mask_sensitive("UPI 999888777655 ref")


def test_xirr_exact_year():
    r = math_tools.xirr([(date(2021, 1, 1), -10_000), (date(2022, 1, 1), 11_000)])
    assert r is not None
    assert abs(r - 0.10) < 1e-6


def test_xirr_half_year():
    flows = [(date(2020, 1, 1), -5000), (date(2020, 7, 1), 6000)]
    expected = 1.2 ** (365 / 182) - 1
    r = math_tools.xirr(flows)
    assert abs(r - expected) < 1e-4


def test_xirr_same_sign_returns_none():
    assert math_tools.xirr([(date(2020, 1, 1), 100), (date(2020, 6, 1), 200)]) is None


def test_sip_fv_matches_closed_form():
    fv, contributed = math_tools.sip_future_value(10_000, 12, 1)
    i = 1.12 ** (1 / 12) - 1
    closed = 10_000 * ((1 + i) ** 12 - 1) / i * (1 + i)
    assert abs(fv - closed) < 0.01
    assert abs(contributed - 120_000) < 1e-9


def test_stepup_grows_fv():
    flat, _ = math_tools.sip_future_value(7500, 12, 10)
    stepped, _ = math_tools.sip_future_value(7500, 12, 10, step_up_pct=10)
    assert stepped > flat * 1.3


def test_budget_sheet_import(conn, tmp_path):
    csv = tmp_path / "bs.csv"
    today = date.today()
    future = date(today.year + 1, 1, 1)
    rows = [
        ",,,,,,,",
        ',INCOME TD,"Rs 53",,,,',
        ',1/Apr/25,"Rs 50,000",,,,,',
        ',1/May/25,"Rs 60,000",Bonus Added,,,',
        f',1/{future.strftime("%b")}/{future.strftime("%y")},"Rs 70,000",,,"Rs 50,000",7500,40000',
    ]
    csv.write_text("\n".join(rows))
    res = importer.import_budget_sheet(conn, str(csv))
    assert res["inserted"] == 2
    types = {r["type"] for r in models.list_txns(conn)}
    assert types == {"salary", "bonus"}
    proj = models.projection_get(conn, future.strftime("%Y-%m"))
    assert proj["savings_target"] == 50_000
    assert proj["sip_plan"] == 7_500


def test_month_summary_and_dedupe(conn):
    key = models.dedupe("budgetsheet", "2026-08-01", 100_000)
    models.add_txn(
        conn, "2026-08-01", "salary", 100_000, category="income", key=key
    )
    dup = models.add_txn(
        conn, "2026-08-01", "salary", 100_000, category="income", key=key
    )
    count = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE dedupe_key=?", (key,)
    ).fetchone()[0]
    assert count == 1 and dup is None
    models.add_txn(conn, "2026-08-05", "expense", 30_000, category="rent")
    s = models.month_summary(conn, "2026-08")
    assert s["income"] == 100_000 and s["living"] == 30_000
    assert s["saved"] == 70_000 and abs(s["rate"] - 70) < 1e-9


def test_transfer_not_living(conn):
    models.add_txn(conn, "2026-08-02", "debit", 5_000, is_transfer=True)
    s = models.month_summary(conn, "2026-08")
    assert s["living"] == 0


def test_stock_avg_cost(conn):
    models.stock_record(conn, "TCS", "2026-01-05", "BUY", 2, 3000)
    models.stock_record(conn, "TCS", "2026-02-05", "BUY", 2, 4000)
    h = models.stock_holdings(conn)["TCS"]
    assert h["qty"] == 4 and h["avg"] == 3500
    models.price_set(conn, "TCS", 5000, "2026-03-01")
    h = models.stock_holdings(conn)["TCS"]
    assert h["value"] == 20_000 and h["unrealized"] == 6_000


def test_statement_columns(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text(
        "Txn Date,Remarks,Withdrawal Amt.,Deposit Amt.\n"
        "01/08/2026,SWIGGY ORDER,450,\n"
        "02/08/2026,SALARY AUG,,100000\n"
        "03/08/2026,CC BILL PAYMENT 1234567890123456,9000,\n"
    )
    headers = p.read_text().splitlines()[0].split(",")
    m = importer.detect_columns(headers)
    assert m["date"] == 0 and m["description"] == 1
    assert m["debit"] == 2 and m["credit"] == 3


def test_runway():
    assert math_tools.months_of_runway(600_000, 50_000) == 12
    assert math_tools.months_of_runway(600_000, 0) is None
