from __future__ import annotations

import csv
import re
from datetime import date, datetime
from pathlib import Path

from . import models
from .fmt import inr, mask_sensitive, parse_amount

DATE_FORMATS = (
    "%d/%b/%y",
    "%d/%b/%Y",
    "%d-%b-%y",
    "%d-%b-%Y",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%Y-%m-%d",
    "%d/%m/%y",
)

TRANSFER_KEYWORDS = [
    "self ",
    "own a/c",
    "own ac",
]

CC_PAYMENT_KEYWORDS = [
    "cc bill",
    "credit card payment",
    "cc payment",
    "payment recd",
    "payment received",
    "autodebit payment",
    "autopay",
]


def parse_date(raw: str) -> date | None:
    s = raw.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def import_budget_sheet(conn: sqlite3.Connection, path: str) -> dict:
    src = Path(path).expanduser()
    if not src.exists():
        raise FileNotFoundError(f"{src} not found")
    incomes = []
    projections = {}
    skipped = 0
    with open(src, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 3:
                continue
            d = parse_date(row[1]) if len(row) > 1 else None
            if d is None:
                continue
            ym = d.strftime("%Y-%m")
            note = (row[3].strip() if len(row) > 3 else "")
            has_projection = False
            if len(row) > 5 and row[5].strip():
                try:
                    savings = parse_amount(row[5])
                    sip_plan = (
                        parse_amount(row[6])
                        if len(row) > 6 and row[6].strip()
                        else None
                    )
                    invest_plan = (
                        parse_amount(row[7])
                        if len(row) > 7 and row[7].strip()
                        else None
                    )
                    proj_income = None
                    if row[2].strip():
                        try:
                            proj_income = parse_amount(row[2])
                        except ValueError:
                            proj_income = None
                    projections[ym] = {
                        "income": proj_income,
                        "savings_target": savings,
                        "sip_plan": sip_plan,
                        "invest_plan": invest_plan,
                    }
                    has_projection = True
                except ValueError:
                    pass
            try:
                amount = parse_amount(row[2])
            except ValueError:
                if not has_projection:
                    skipped += 1
                continue
            if amount <= 0:
                continue
            type_ = "salary"
            low = note.lower()
            if "bonus" in low:
                type_ = "bonus"
            elif "itr" in low or "refund" in low:
                type_ = "itr"
            if d > date.today():
                continue
            incomes.append((d.isoformat(), type_, amount, note))
    inserted = 0
    duplicates = 0
    for d, type_, amount, note in incomes:
        key = models.dedupe("budgetsheet", d, amount)
        if models.add_txn(
            conn, d, type_, amount,
            category="income", note=note, source="budgetsheet", key=key,
        ):
            inserted += 1
        else:
            duplicates += 1
    for ym, p in sorted(projections.items()):
        models.projection_upsert(
            conn, ym, p["income"], p["savings_target"],
            p["sip_plan"], p["invest_plan"],
        )
    return {
        "file": src.name,
        "incomes_found": len(incomes),
        "inserted": inserted,
        "duplicates_skipped": duplicates,
        "projection_months": sorted(projections),
        "skipped_rows": skipped,
    }


def detect_columns(headers: list[str]) -> dict[str, int | None]:
    normed = [h.strip().lower() for h in headers]
    mapping: dict[str, int | None] = {
        "date": None, "description": None,
        "debit": None, "credit": None,
        "amount": None, "signed": None,
    }
    date_keys = ["txn date", "transaction date", "value date", "date"]
    desc_keys = [
        "narration", "remarks", "description", "particulars",
        "details", "transaction details", "merchant",
    ]
    debit_keys = ["withdrawal", "debit", "paid out"]
    credit_keys = ["deposit", "credit", "paid in"]
    amount_bad = ("intl", "reward", "billing", "point", "balance")

    for i, h in enumerate(normed):
        if mapping["date"] is None and any(k == h or k in h for k in date_keys):
            mapping["date"] = i
    for i, h in enumerate(normed):
        if mapping["description"] is None and any(k in h for k in desc_keys):
            mapping["description"] = i
    for i, h in enumerate(normed):
        if mapping["debit"] is None and any(k in h for k in debit_keys):
            mapping["debit"] = i
    for i, h in enumerate(normed):
        if mapping["credit"] is None and any(k in h for k in credit_keys):
            mapping["credit"] = i
    for i, h in enumerate(normed):
        if "sign" in h:
            mapping["signed"] = i
    for i, h in enumerate(normed):
        if h == "amount":
            mapping["amount"] = i
            break
    if mapping["amount"] is None:
        for i, h in enumerate(normed):
            if (
                h.startswith("amount")
                and not any(b in h for b in amount_bad)
            ):
                mapping["amount"] = i
                break
    if mapping["amount"] is None:
        for i, h in enumerate(normed):
            if "amount" in h and not any(b in h for b in amount_bad):
                mapping["amount"] = i
                break
    return mapping


def categorize(conn: sqlite3.Connection, description: str) -> tuple[str, bool]:
    category = models.rule_match(conn, description)
    if category:
        return category, True
    return "uncategorized", False


def import_statement(
    conn: sqlite3.Connection,
    path: str,
    source_name: str,
    assume_yes: bool = False,
    invert_sign: bool = False,
) -> dict:
    src = Path(path).expanduser()
    if not src.exists():
        raise FileNotFoundError(f"{src} not found")

    with open(src, newline="", encoding="utf-8-sig") as f:
        all_rows = list(csv.reader(f))
    all_rows = [r for r in all_rows if any(c.strip() for c in r)]

    header_idx = None
    mapping = None
    for i, row in enumerate(all_rows[:25]):
        cand = detect_columns(row)
        if (
            cand["date"] is not None
            and cand["description"] is not None
            and any(cand[k] is not None for k in ("debit", "credit", "amount", "signed"))
        ):
            header_idx, mapping = i, cand
            break
    if header_idx is None:
        raise ValueError(
            "no header row found in first 25 lines - "
            "expected columns like Date / Details / Amount"
        )
    headers = all_rows[header_idx]
    rows = [r for r in all_rows[header_idx + 1:] if len(r) >= 2]

    from rich.console import Console
    from rich.table import Table

    console = Console()
    t = Table(title=f"Column mapping — {src.name}")
    t.add_column("Field")
    t.add_column("Detected column")
    for field in ("date", "description", "debit", "credit", "amount", "signed"):
        idx = mapping.get(field)
        label = headers[idx] if idx is not None and idx < len(headers) else "—"
        t.add_row(field, label)
    console.print(t)

    if invert_sign and mapping["signed"] is None and mapping["amount"] is not None:
        signed_col = next(
            (i for i, h in enumerate(headers)
             if "sign" in h.lower()), None
        )
        if signed_col is not None:
            mapping["signed"] = signed_col

    parsed = []
    bad_dates = 0
    for r in rows:
        if max(x for x in mapping.values() if x is not None) >= len(r):
            continue
        raw_date = r[mapping["date"]].strip()
        d = parse_date(raw_date)
        if d is None:
            if raw_date:
                bad_dates += 1
            continue
        desc = mask_sensitive(r[mapping["description"]].strip())
        debit = credit = 0.0
        try:
            if (
                mapping["debit"] is not None
                and mapping["debit"] < len(r)
                and r[mapping["debit"]].strip()
            ):
                debit = abs(parse_amount(r[mapping["debit"]]))
            if (
                mapping["credit"] is not None
                and mapping["credit"] < len(r)
                and r[mapping["credit"]].strip()
            ):
                credit = abs(parse_amount(r[mapping["credit"]]))
            if (
                debit == 0 and credit == 0
                and mapping["signed"] is not None
                and mapping["signed"] < len(r)
            ):
                val = parse_amount(r[mapping["signed"]])
                if invert_sign:
                    val = -val
                if val < 0:
                    debit, credit = -val, 0.0
                else:
                    credit, debit = val, 0.0
            if (
                debit == 0 and credit == 0
                and mapping["amount"] is not None
                and mapping["amount"] < len(r)
            ):
                val = parse_amount(r[mapping["amount"]])
                if invert_sign:
                    debit, credit = val, 0.0
                else:
                    credit, debit = val, 0.0
        except ValueError:
            continue
        if debit == 0 and credit == 0:
            continue
        low = desc.lower()
        is_transfer = any(k in low for k in TRANSFER_KEYWORDS) and credit == 0
        is_cc_payment = any(k in low for k in CC_PAYMENT_KEYWORDS)
        category, matched = categorize(conn, desc)
        if debit > 0:
            type_ = "expense"
            amount = debit
        else:
            type_ = "credit"
            amount = credit
        parsed.append(
            {
                "date": d.isoformat(),
                "type": type_,
                "category": category,
                "amount": amount,
                "note": desc,
                "is_transfer": is_transfer,
                "is_cc_payment": is_cc_payment,
                "matched": matched,
            }
        )

    auto_categorized = sum(1 for p in parsed if p["matched"])
    preview = Table(title=f"Preview — {len(parsed)} rows (newest first)")
    for col in ("date", "type", "category", "amount", "note"):
        preview.add_column(col)
    for p in sorted(parsed, key=lambda p: p["date"], reverse=True)[:12]:
        preview.add_row(
            p["date"], p["type"], p["category"], inr(p["amount"]),
            (p["note"][:38] + "…") if len(p["note"]) > 38 else p["note"],
        )
    console.print(preview)
    console.print(
        f"[cyan]{len(parsed)}[/] rows parsed · "
        f"[green]{auto_categorized}[/] auto-categorized · "
        f"[yellow]{bad_dates}[/] unparseable dates skipped"
    )
    if not assume_yes:
        from typer import confirm

        if not confirm("Import these rows?"):
            return {"aborted": True}

    inserted = duplicates = 0
    for p in parsed:
        key = models.dedupe(source_name, p["date"], p["amount"], p["note"])
        if models.add_txn(
            conn,
            p["date"], p["type"], p["amount"], category=p["category"],
            note=p["note"], source=source_name,
            is_transfer=p["is_transfer"], is_cc_payment=p["is_cc_payment"],
            key=key,
        ):
            inserted += 1
        else:
            duplicates += 1
    return {
        "file": src.name,
        "parsed": len(parsed),
        "inserted": inserted,
        "duplicates_skipped": duplicates,
    }


def export_csv(conn: sqlite3.Connection, out_dir: str) -> list[str]:
    out = Path(out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    written = []
    tables = {
        "transactions": "SELECT * FROM transactions ORDER BY date",
        "budgets": "SELECT * FROM budgets",
        "projections": "SELECT * FROM projections ORDER BY month",
        "sips": "SELECT * FROM sips",
        "sip_lots": "SELECT * FROM sip_lots ORDER BY date",
        "stock_txns": "SELECT * FROM stock_txns ORDER BY date",
        "balances": "SELECT * FROM balances",
    }
    for name, sql in tables.items():
        cur = conn.execute(sql)
        target = out / f"{name}.csv"
        cols = [d[0] for d in cur.description]
        with open(target, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(cols)
            w.writerows(cur.fetchall())
        written.append(str(target))
    return written
