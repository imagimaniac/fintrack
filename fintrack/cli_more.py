from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime
from pathlib import Path

import typer
from rich.panel import Panel
from rich.table import Table

from . import db, dashboard, importer, math_tools, models
from .cli import (
    app,
    console,
    resolve_date,
    resolve_month,
)
from .fmt import inr, inr_compact, parse_amount

sip_app = typer.Typer(no_args_is_help=True)
stock_app = typer.Typer(no_args_is_help=True)
price_app = typer.Typer(no_args_is_help=True)
balance_app = typer.Typer(no_args_is_help=True)
import_app = typer.Typer(no_args_is_help=True)

app.add_typer(sip_app, name="sip")
app.add_typer(stock_app, name="stock")
app.add_typer(price_app, name="price")
app.add_typer(balance_app, name="balance")
app.add_typer(import_app, name="import")


@app.command("dashboard")
def dashboard_cmd(month: str = typer.Option(None, "--month", "-m")):
    """Full wealth dashboard (Plan-vs-Actual, trends, portfolio, forecast)."""
    conn = db.connect()
    dashboard.render(conn, resolve_month(month))
    conn.close()


@app.command()
def xirr():
    """Money-weighted return across all tracked investments."""
    conn = db.connect()
    flows, terminal = models.xirr_flows(conn)
    r = math_tools.xirr(flows)
    t = Table(box=None)
    t.add_column("metric")
    t.add_column("value", justify="right")
    invested = sum(-a for _, a in flows if a < 0)
    recovered = sum(a for _, a in flows if a > 0) - terminal
    t.add_row("cashflow entries", str(len(flows)))
    t.add_row("invested so far", inr(invested))
    t.add_row("withdrawn/recovered", inr(recovered))
    t.add_row("current tracked value", inr(terminal))
    t.add_row(
        "XIRR",
        f"[bold magenta]{r * 100:+.2f}% p.a.[/]"
        if r is not None else "[dim]need buys+sells or NAV marks[/]",
    )
    console.print(Panel(t, title="XIRR", expand=False))
    conn.close()


@app.command()
def forecast(
    amount: str = typer.Option(None, "--amount", "-a", help="monthly invest"),
    rate: float = typer.Option(None, "--rate", help="annual return %"),
    stepup: float = typer.Option(0.0, "--stepup", help="annual increment %"),
    years: str = typer.Option("3,5,10,15,25", "--years", "-y"),
):
    """Project corpus at horizons (defaults from your SIPs)."""
    conn = db.connect()
    if rate is None:
        rate = float(models.get_meta(conn, "default_return_pct") or 12)
    if amount is None:
        active = sum(
            p["monthly"] for p in models.portfolio_sips(conn) if p["active"]
        )
        extra = float(models.get_meta(conn, "extra_monthly_invest") or 0)
        monthly = active + extra
        if monthly <= 0:
            raise typer.BadParameter(
                "no active SIPs found - pass --amount 7500"
            )
    else:
        monthly = parse_amount(amount)
    t = Table(
        title=(
            f"{inr(monthly)}/mo @ {rate:.0f}% p.a."
            + (f" step-up {stepup}%" if stepup else "")
        ),
        box=None,
    )
    for col, just in (
        ("horizon", "left"), ("you invest", "right"),
        ("corpus", "right"), ("growth", "right"), ("multiple", "right"),
    ):
        t.add_column(col, justify=just)
    for y in [float(x) for x in years.split(",")]:
        fv, contributed = math_tools.sip_future_value(monthly, rate, y, stepup)
        t.add_row(
            f"{y:g} yr",
            inr_compact(contributed),
            f"[bold cyan]{inr_compact(fv)}[/]",
            f"[green]+{inr_compact(fv - contributed)}[/]",
            f"{fv / contributed:.2f}x" if contributed else "-",
        )
    console.print(t)
    conn.close()


@sip_app.command("add")
def sip_add(
    name: str,
    amount: str = typer.Option(..., "--amount", "-a", metavar="AMOUNT"),
    start: str = typer.Option(None, "--start", "-s"),
    stepup: float = typer.Option(0.0, "--stepup"),
):
    """Register or update a SIP."""
    conn = db.connect()
    models.sip_add(
        conn,
        name,
        parse_amount(amount),
        resolve_date(start) if start else None,
        stepup,
    )
    console.print(f"[green]OK[/] SIP '{name}' {inr(parse_amount(amount))}/mo"
                  + (f", step-up {stepup:g}%" if stepup else ""))
    conn.close()


@sip_app.command("log")
def sip_log(
    name: str,
    amount: str = typer.Argument(None, metavar="AMOUNT"),
    nav: float = typer.Option(None, "--nav"),
    units: float = typer.Option(None, "--units"),
    d: str = typer.Option(None, "--date", "-d"),
):
    """Log one instalment (units auto-computed when --nav given)."""
    conn = db.connect()
    s = models.sip_by_name(conn, name)
    amt = parse_amount(amount) if amount else float(
        typer.prompt("Instalment amount", default=s["monthly_amount"])
    )
    iso = resolve_date(d)
    lot_id = models.sip_log(conn, s["id"], iso, amt, nav, units)
    if lot_id is None:
        console.print("[yellow]duplicate instalment - skipped[/]")
    else:
        u_txt = f", {amt / nav:.4f} units @ nav {nav}" if nav else ""
        console.print(f"[green]OK[/] {inr(amt)} logged for '{s['name']}'{u_txt}")
    conn.close()


@sip_app.command("list")
def sip_list():
    conn = db.connect()
    ports = models.portfolio_sips(conn)
    if not ports:
        console.print("[dim]none yet - fintrack sip add 'Axis Bluechip' -a 7500[/]")
        raise typer.Exit()
    t = Table(box=None)
    for col, just in (
        ("name", "left"), ("on", "center"), ("monthly", "right"),
        ("invested", "right"), ("units", "right"), ("value*", "right"),
        ("P&L", "right"), ("freshness", "right"),
    ):
        t.add_column(col, justify=just)
    total_inv = total_val = 0.0
    for p in ports:
        color = "green" if p["gain"] >= 0 else "red"
        total_inv += p["invested"]
        total_val += p["value"]
        t.add_row(
            p["name"],
            "[green]Y[/]" if p["active"] else "[dim]paused[/]",
            inr(p["monthly"]),
            inr_compact(p["invested"]),
            f"{p['units']:g}" if p.get("units") else "-",
            inr_compact(p["value"]),
            f"[{color}]{inr_compact(p['gain'])} ({p['gain_pct']:+.1f}%)[/]",
            dashboard._staleness(p["as_of"]),
        )
    console.print(t)
    console.print("*value = units x last NAV you entered; refresh via sip log --nav")
    console.print(
        f"total invested {inr(total_inv)} · current {inr(total_val)} · "
        f"P&L [{'green' if total_val >= total_inv else 'red'}]"
        f"{inr(total_val - total_inv)}[/]"
    )
    conn.close()


@sip_app.command("pause")
def sip_pause(name: str):
    conn = db.connect()
    s = models.sip_by_name(conn, name)
    models.sip_set_active(conn, s["id"], False)
    console.print(f"paused '{s['name']}'")
    conn.close()


@sip_app.command("resume")
def sip_resume(name: str):
    conn = db.connect()
    s = models.sip_by_name(conn, name)
    models.sip_set_active(conn, s["id"], True)
    console.print(f"[green]resumed '{s['name']}'[/]")
    conn.close()


@stock_app.command("buy")
def stock_buy(symbol: str, qty: float, price: float,
              d: str = typer.Option(None, "--date", "-d")):
    conn = db.connect()
    try:
        models.stock_record(conn, symbol, resolve_date(d), "BUY", qty, price)
    except ValueError as e:
        raise typer.BadParameter(str(e))
    console.print(f"[green]OK[/] BUY {qty:g} {symbol.upper()} @ ₹{price:g}")
    conn.close()


@stock_app.command("sell")
def stock_sell(symbol: str, qty: float, price: float,
               d: str = typer.Option(None, "--date", "-d")):
    conn = db.connect()
    try:
        models.stock_record(conn, symbol, resolve_date(d), "SELL", qty, price)
    except ValueError as e:
        raise typer.BadParameter(str(e))
    console.print(f"[green]OK[/] SELL {qty:g} {symbol.upper()} @ ₹{price:g}")
    conn.close()


@stock_app.command("list")
def stock_list():
    conn = db.connect()
    holdings = models.stock_holdings(conn)
    rows = [(k, v) for k, v in sorted(holdings.items())
            if v["qty"] > 1e-9 or abs(v["realized"]) > 1e-9]
    if not rows:
        console.print("[dim]no stock transactions yet[/]")
        raise typer.Exit()
    t = Table(box=None)
    for col, just in (
        ("symbol", "left"), ("qty", "right"), ("avg", "right"),
        ("last", "right"), ("value", "right"), ("unrealized", "right"),
        ("realized", "right"), ("priced", "right"),
    ):
        t.add_column(col, justify=just)
    for sym, h in rows:
        color = "green" if h["unrealized"] >= 0 else "red"
        t.add_row(
            sym, f"{h['qty']:g}", inr(h["avg"]),
            inr(h["last_price"]) if h["qty"] > 1e-9 else "-",
            inr_compact(h["value"]) if h["qty"] > 1e-9 else "-",
            f"[{color}]{inr_compact(h['unrealized'])}[/]"
            if h["qty"] > 1e-9 else "-",
            inr_compact(h["realized"]),
            dashboard._staleness(h["priced_at"]) if h["qty"] > 1e-9 else "-",
        )
    console.print(t)
    console.print("[dim]offline mode: mark prices via fintrack price set SYMBOL P[/]")
    conn.close()


@price_app.command("set")
def price_set(symbol: str, price: float,
              d: str = typer.Option(None, "--date", "-d")):
    """Manually mark a symbol's latest known price."""
    conn = db.connect()
    iso = resolve_date(d)
    models.price_set(conn, symbol, price, iso)
    console.print(f"[green]OK[/] {symbol.upper()} = ₹{price:g} ({iso})")
    conn.close()


@balance_app.command("set")
def balance_set(
    instrument: str,
    amount: str = typer.Argument(..., metavar="AMOUNT"),
    kind: str = typer.Option(..., "--kind", "-k",
                             help="bank | mf | sip | stock | rd"),
    d: str = typer.Option(None, "--as-of", "-d"),
):
    """Snapshot an instrument value (10.00L style accepted)."""
    kind = kind.lower()
    if kind not in ("bank", "mf", "sip", "stock", "rd"):
        raise typer.BadParameter("kind must be bank|mf|sip|stock|rd")
    conn = db.connect()
    amt = parse_amount(amount)
    models.balance_set(conn, instrument, kind, amt,
                       resolve_date(d) if d else date.today().isoformat())
    console.print(f"[green]OK[/] {instrument} ({kind}) = {inr(amt)}")
    conn.close()


@balance_app.command("list")
def balance_list():
    conn = db.connect()
    rows = models.balance_all(conn)
    if not rows:
        console.print("[dim]none yet - fintrack balance set bank1 -k bank 10.00L[/]")
        raise typer.Exit()
    t = Table(box=None)
    for col, just in (
        ("instrument", "left"), ("kind", "left"),
        ("value", "right"), ("as of", "right"),
    ):
        t.add_column(col, justify=just)
    for b in rows:
        t.add_row(b["instrument"], b["kind"], inr(b["amount"]),
                  dashboard._staleness(b["as_of"]))
    console.print(t)
    nw = models.net_worth(conn)
    console.print(f"net worth [bold cyan]{inr(nw['total'])}[/]")
    conn.close()


@import_app.command("budget-sheet")
def import_budget_sheet(path: str):
    """Import YOUR budget sheet CSV: income history + projections."""
    conn = db.connect()
    res = importer.import_budget_sheet(conn, path)
    _print_import_result(res)
    conn.close()


@import_app.command("statement")
def import_statement(
    path: str,
    source: str = typer.Option(None, "--source", "-s",
                               help="label e.g. bank1/cc1"),
    yes: bool = typer.Option(False, "--yes", "-y"),
    invert_sign: bool = typer.Option(
        False, "--invert-sign",
        help="flip debit/credit interpretation if preview looks reversed",
    ),
):
    """Import bank/CC statement CSV (masked, deduped, categorized)."""
    conn = db.connect()
    src = source or Path(path).stem.lower()[:24]
    try:
        res = importer.import_statement(
            conn, path, src, assume_yes=yes, invert_sign=invert_sign
        )
    except ValueError as e:
        raise typer.BadParameter(str(e))
    if res.get("aborted"):
        console.print("[yellow]aborted[/]")
        raise typer.Exit()
    _print_import_result(res)
    conn.close()


def _print_import_result(res: dict) -> None:
    t = Table(box=None, title=res.get("file", ""))
    t.add_column("metric")
    t.add_column("value", justify="right")
    for key, label in (
        ("incomes_found", "income rows found"),
        ("parsed", "rows parsed"),
        ("inserted", "inserted"),
        ("duplicates_skipped", "duplicates skipped"),
        ("skipped_rows", "other rows skipped"),
    ):
        if key in res:
            t.add_row(label, str(res[key]))
    if "projection_months" in res:
        t.add_row("projection months", ", ".join(res["projection_months"]))
    console.print(t)


@import_app.command("export")
def export(out_dir: str = typer.Option("imports/export", "--out", "-o")):
    """Dump every table to CSV (Excel-friendly)."""
    conn = db.connect()
    written = importer.export_csv(conn, out_dir)
    for w in written:
        console.print(f"[green]wrote[/] {w}")
    conn.close()


@app.command()
def backup():
    """Clean snapshot of finance.db into data/backups (chmod 600)."""
    conn = db.connect()
    bdir = db.db_path().parent / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    target = bdir / f"finance-{datetime.now():%Y%m%d-%H%M%S}.db"
    conn.execute("VACUUM INTO ?", (str(target),))
    os.chmod(target, 0o600)
    backups = sorted(bdir.glob("finance-*.db"))
    for old in backups[:-10]:
        old.unlink()
    size_kb = target.stat().st_size / 1024
    console.print(
        f"[green]OK[/] {target.name} ({size_kb:.0f} KB) · "
        f"{len(backups[-10:])} backups kept"
    )
    conn.close()


@app.command()
def meta(key: str, value: str = typer.Argument(None)):
    """Read/write settings (savings_rate_baseline, default_return_pct...)."""
    conn = db.connect()
    if value is None:
        v = models.get_meta(conn, key)
        console.print(f"{key} = {v}" if v is not None
                      else f"[yellow]{key} not set[/]")
    else:
        models.set_meta(conn, key, value)
        console.print(f"[green]OK[/] {key} = {value}")
    conn.close()
