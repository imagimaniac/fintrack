from __future__ import annotations

from datetime import date, timedelta

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import db, dashboard, importer, models
from .fmt import inr, parse_amount

app = typer.Typer(
    help="FinTrack - 100% offline personal finance manager.",
)
add_app = typer.Typer(no_args_is_help=True)
budget_app = typer.Typer(no_args_is_help=True)
rule_app = typer.Typer(no_args_is_help=True)
txn_app = typer.Typer(no_args_is_help=True)

app.add_typer(add_app, name="add")
app.add_typer(budget_app, name="budget")
app.add_typer(rule_app, name="rule")
app.add_typer(txn_app, name="txn")

console = Console()


@app.callback(invoke_without_command=True)
def _root(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        conn = db.connect()
        dashboard.render(conn)
        conn.close()
        raise typer.Exit()


def resolve_date(raw: str | None) -> str:
    if not raw or raw.lower() in ("today", "t", "now"):
        return date.today().isoformat()
    if raw.lower() == "yesterday":
        return (date.today() - timedelta(days=1)).isoformat()
    d = importer.parse_date(raw)
    if d is None:
        raise typer.BadParameter(f"unparseable date: {raw!r}")
    return d.isoformat()


def resolve_month(raw: str | None) -> str:
    if not raw or raw.lower() == "this":
        return date.today().strftime("%Y-%m")
    try:
        date.fromisoformat(raw + "-01")
    except ValueError as e:
        raise typer.BadParameter(f"month must be YYYY-MM: {raw!r}") from e
    return raw


def prompt_category(default: str = "") -> str:
    raw = typer.prompt("Category", default=default or "uncategorized")
    return raw.strip().lower()


def _add(type_: str, amount_raw, category, note, method, d):
    conn = db.connect()
    if amount_raw is None:
        amount_raw = typer.prompt("Amount")
    amount = parse_amount(amount_raw)
    if category is None:
        category = "income" if type_ in models.INCOME_TYPES else prompt_category()
    txn_id = models.add_txn(
        conn,
        resolve_date(d),
        type_,
        amount,
        category=category.lower(),
        note=note or "",
        method=method or "",
        source="manual",
    )
    if txn_id is None:
        console.print("[yellow]duplicate - nothing added[/]")
    else:
        console.print(f"[green]OK[/] {type_} {inr(amount)} recorded (id {txn_id})")
    conn.close()


@app.command()
def init():
    """Create the database and show where it lives."""
    conn = db.connect()
    console.print(
        Panel(
            f"[green]ready[/] · {db.db_path()}\n"
            "permissions locked 600 · zero network code",
            title="FinTrack initialised",
            expand=False,
        )
    )
    conn.close()


def _register_income(name: str, help_: str):
    @add_app.command(name, help=help_)
    def _cmd(
        amount=typer.Argument(None, metavar="AMOUNT"),
        note: str = typer.Option("", "--note", "-n"),
        d: str = typer.Option(None, "--date", "-d"),
    ):
        _add(name, amount, "income", note, "", d)


_register_income("salary", "Record salary income.")
_register_income("bonus", "Record bonus income.")
_register_income("itr", "Record income-tax refund.")


@add_app.command("expense")
def add_expense(
    amount=typer.Argument(None, metavar="AMOUNT"),
    category: str = typer.Option(None, "--cat", "-c"),
    note: str = typer.Option("", "--note", "-n"),
    method: str = typer.Option("", "--method", "-m"),
    d: str = typer.Option(None, "--date", "-d"),
):
    """Record a living expense (accepts 1.5L style amounts)."""
    _add("expense", amount, category, note, method, d)


@add_app.command("credit")
def add_credit(
    amount=typer.Argument(None, metavar="AMOUNT"),
    category: str = typer.Option(None, "--cat", "-c"),
    note: str = typer.Option("", "--note", "-n"),
    d: str = typer.Option(None, "--date", "-d"),
):
    """Record any other inflow."""
    _add("credit", amount, category or "income", note, "", d)


@add_app.command("debit")
def add_debit(
    amount=typer.Argument(None, metavar="AMOUNT"),
    category: str = typer.Option(None, "--cat", "-c"),
    note: str = typer.Option("", "--note", "-n"),
    transfer: bool = typer.Option(False, "--transfer", help="not a real expense"),
    d: str = typer.Option(None, "--date", "-d"),
):
    """Record a debit; --transfer marks self-moves excluded from spend."""
    conn = db.connect()
    if amount is None:
        amount = parse_amount(typer.prompt("Amount"))
    if category is None:
        category = prompt_category()
    txn_id = models.add_txn(
        conn,
        resolve_date(d),
        "debit",
        amount,
        category=category,
        note=note,
        source="manual",
        is_transfer=transfer,
    )
    tag = "[yellow]transfer[/]" if transfer else "[green]OK[/] debit"
    console.print(f"{tag} {inr(amount)} recorded (id {txn_id})")
    conn.close()


@add_app.command("saving")
def add_saving(
    amount=typer.Argument(None, metavar="AMOUNT"),
    note: str = typer.Option("", "--note", "-n"),
    d: str = typer.Option(None, "--date", "-d"),
):
    """Money moved into savings (allocation, not expense)."""
    _add("saving", amount, "savings", note, "", d)


@add_app.command("invest")
def add_invest(
    amount=typer.Argument(None, metavar="AMOUNT"),
    note: str = typer.Option("", "--note", "-n"),
    d: str = typer.Option(None, "--date", "-d"),
):
    """RD/FD/other investment outflow (allocation, not expense)."""
    _add("invest", amount, "invest", note, "", d)


@app.command("list")
def list_txns_cmd(
    month: str = typer.Option(None, "--month", "-m"),
    type_: str = typer.Option(None, "--type", "-t"),
    category: str = typer.Option(None, "--cat", "-c"),
    limit: int = typer.Option(40, "--limit", "-l"),
):
    """List transactions with totals (T=transfer, C=cc-payment)."""
    conn = db.connect()
    rows = models.list_txns(conn, resolve_month(month), type_, category, limit)
    t = Table(title=f"{len(rows)} transactions", box=None)
    for col, just in (
        ("id", "right"), ("date", "left"), ("type", "left"),
        ("category", "left"), ("amount", "right"), ("note", "left"),
        ("flags", "center"),
    ):
        t.add_column(col, justify=just)
    total = 0.0
    for r in rows:
        total += r["amount"]
        flags = ("T" if r["is_transfer"] else "") + ("C" if r["is_cc_payment"] else "")
        t.add_row(
            str(r["id"]),
            r["date"],
            r["type"],
            r["category"],
            inr(r["amount"]),
            (r["note"][:34] + "...") if len(r["note"]) > 34 else r["note"],
            flags or "-",
        )
    console.print(t)
    console.print(f"shown total [bold]{inr(total)}[/]")
    conn.close()


@app.command()
def report(month: str = typer.Option(None, "--month")):
    """Monthly summary + where money went."""
    from . import dashboard

    m = resolve_month(month)
    conn = db.connect()
    dashboard.render_report(conn, m)
    conn.close()


@budget_app.command("set")
def budget_set(category: str, limit: str = typer.Argument(..., metavar="LIMIT")):
    """Set monthly limit (8000 / 1.2L both fine)."""
    conn = db.connect()
    limit_f = parse_amount(limit)
    models.budget_set(conn, category, limit_f)
    console.print(f"[green]OK[/] budget {category}: {inr(limit_f)}/mo")
    conn.close()


@budget_app.command("show")
def budget_show():
    """Budgets vs current-month usage."""
    conn = db.connect()
    budgets = models.budget_all(conn)
    if not budgets:
        console.print("[dim]none yet - fintrack budget set food 8000[/]")
        raise typer.Exit()
    spend = {r["category"]: r["total"] for r in models.category_breakdown(conn)}
    t = Table(box=None)
    for col, just in (
        ("category", "left"), ("limit", "right"), ("spent", "right"),
        ("used", "right"), ("", "center"),
    ):
        t.add_column(col, justify=just)
    for b in budgets:
        used = spend.get(b["category"], 0.0)
        pct = used / b["monthly_limit"] * 100
        mark, color = (
            ("OK", "green") if pct <= 85
            else ("NEAR", "yellow") if pct <= 100 else ("OVER", "red")
        )
        t.add_row(
            b["category"], inr(b["monthly_limit"]), inr(used),
            f"{pct:.0f}%", f"[{color}]{mark}[/]",
        )
    console.print(t)
    conn.close()


@rule_app.command("add")
def rule_add(keyword: str, category: str):
    """Keyword found in descriptions maps to category."""
    conn = db.connect()
    models.rule_add(conn, keyword, category)
    console.print(f"[green]OK[/] '{keyword}' -> {category}")
    conn.close()


@rule_app.command("list")
def rule_list():
    conn = db.connect()
    rules = models.rule_all(conn)
    t = Table(box=None, title=f"{len(rules)} rules")
    t.add_column("keyword")
    t.add_column("category")
    for r in rules:
        t.add_row(r["keyword"], r["category"])
    console.print(t)
    conn.close()


@txn_app.command("retype")
def txn_retype(txn_id: int, type_: str):
    """Fix a transaction's type after import."""
    conn = db.connect()
    ok = models.txn_update(conn, txn_id, type_=type_.lower())
    console.print("[green]OK[/]" if ok else "[red]nothing changed[/]")
    conn.close()


@txn_app.command("cat")
def txn_cat(txn_id: int, category: str):
    """Fix a transaction's category."""
    conn = db.connect()
    ok = models.txn_update(conn, txn_id, category=category.lower())
    console.print("[green]OK[/]" if ok else "[red]nothing changed[/]")
    conn.close()


@txn_app.command("rm")
def txn_rm(txn_id: int):
    """Delete a transaction."""
    conn = db.connect()
    (console.print("[green]deleted[/]") if models.txn_delete(conn, txn_id)
     else console.print("[red]id not found[/]"))
    conn.close()
