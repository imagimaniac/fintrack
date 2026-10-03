from __future__ import annotations

import sqlite3
from datetime import date

from rich.box import ROUNDED
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import math_tools, models
from .fmt import bar, inr, inr_compact

W = 108


def _verdict(pct_used: float) -> tuple[str, str]:
    if pct_used <= 85:
        return "[green]OK[/]", "green"
    if pct_used <= 100:
        return "[yellow]NEAR[/]", "yellow"
    return "[red]OVER[/]", "red"


def _days_since(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        d = date.fromisoformat(str(iso)[:10])
    except ValueError:
        return None
    return (date.today() - d).days


def _staleness(iso: str | None) -> str:
    days = _days_since(iso)
    if days is None:
        return "[dim]never set[/]"
    if days == 0:
        return "[green]today[/]"
    if days <= 35:
        return f"[green]{days}d ago[/]"
    return f"[red]{days}d ago[/]"


def header_panel(month: str) -> Panel:
    text = Text()
    text.append("FinTrack ", style="bold cyan")
    text.append("· 100% offline personal finance · ", style="dim")
    y, m = month.split("-")
    names = [
        "January", "February", "March", "April", "May", "June", "July",
        "August", "September", "October", "November", "December",
    ]
    text.append(f"{names[int(m) - 1]} {y}", style="bold white")
    return Panel(text, width=W, box=ROUNDED, border_style="cyan")


def plan_panel(conn: sqlite3.Connection, month: str) -> Panel | None:
    proj = models.projection_get(conn, month)
    if not proj:
        return None
    s = models.month_summary(conn, month)
    t = Table(width=W - 4, box=None, pad_edge=False)
    for col, just in (
        ("Metric", "left"), ("Planned", "right"), ("Actual", "right"),
        ("Delta", "right"), ("Status", "center"),
    ):
        t.add_column(col, justify=just)
    rows = [
        ("Income", proj["income"], s["income"], True),
        ("Savings", proj["savings_target"], s["saved"], True),
        (
            "Investments",
            (proj["sip_plan"] or 0) + (proj["invest_plan"] or 0) or None,
            s["invested"],
            True,
        ),
    ]
    for label, planned, actual, judge in rows:
        p_txt = inr(planned) if planned else "—"
        a_txt = inr(actual) if actual is not None else "—"
        delta = "—"
        mark = "·"
        if planned and actual is not None:
            diff = actual - planned
            delta = f"{'+' if diff >= 0 else ''}{inr_compact(diff)}"
            if judge:
                ratio = actual / planned * 100 if planned else 0
                if label == "Income":
                    mark = "✓" if ratio >= 99 else ("⚠" if ratio >= 90 else "✗")
                else:
                    mark = "✓" if ratio >= 95 else ("⚠" if ratio >= 80 else "✗")
                delta = f"[{'green' if mark == '✓' else 'yellow' if mark == '⚠' else 'red'}]{delta}[/]"
        t.add_row(label, p_txt, a_txt, delta, mark)
    return Panel(t, title="Plan vs Actual", box=ROUNDED,
                 border_style="magenta")


def summary_panel(conn: sqlite3.Connection, month: str) -> Panel:
    s = models.month_summary(conn, month)
    baseline = float(models.get_meta(conn, "savings_rate_baseline") or 62)

    cards = Table(width=W - 4, box=None, pad_edge=False, show_header=False)
    cards.add_column(justify="left", ratio=1)
    cards.add_column(justify="left", ratio=1)
    cards.add_column(justify="left", ratio=1)
    cards.add_column(justify="left", ratio=1)

    def cell(label: str, value: str, color: str) -> Text:
        txt = Text()
        txt.append(f"{label}\n", style="dim")
        txt.append(value, style=f"bold {color}")
        return txt

    rate_color = (
        "green" if s["rate"] >= baseline
        else "yellow" if s["rate"] >= baseline * 0.8 else "red"
    )
    cards.add_row(
        cell("INCOME", inr(s["income"]), "green"),
        cell("LIVING EXPENSE", inr(s["living"]), "red"),
        cell("NET SAVED", inr(s["saved"]), "cyan"),
        cell("INVESTED", inr(s["invested"]), "magenta"),
    )

    gauge = Table.grid(padding=(0, 1))
    gauge.add_row(
        Text("Savings rate", style="dim"),
        Text(bar(min(s["rate"], 100), 30), style=rate_color),
        Text(f"{s['rate']:.1f}%", style=f"bold {rate_color}"),
        Text(f"(baseline {baseline:.0f}%)", style="dim"),
    )
    return Panel(Group(cards, gauge), title="This Month", box=ROUNDED,
                 border_style="blue")


def category_panel(conn: sqlite3.Connection, month: str) -> Panel | None:
    rows = models.category_breakdown(conn, month)
    budgets = {b["category"]: b["monthly_limit"] for b in models.budget_all(conn)}
    if not rows:
        return None
    total = sum(r["total"] for r in rows)
    t = Table(width=W - 4, box=None, pad_edge=False)
    t.add_column("Category", ratio=2)
    t.add_column("Amount", justify="right")
    t.add_column("Share", width=6, justify="right")
    t.add_column("", ratio=2)
    t.add_column("Budget", justify="right")
    for r in rows:
        share = r["total"] / total * 100 if total else 0
        limit = budgets.get(r["category"])
        if limit:
            used = r["total"] / limit * 100
            status, color = _verdict(used)
            budget_cell = (
                f"{inr(limit)} [bold {color}]({used:.0f}%){status}[/]"
            )
        else:
            budget_cell = "[dim]—[/]"
        t.add_row(
            r["category"], inr(r["total"]), f"{share:.0f}%",
            bar(share, 18), budget_cell,
        )
    foot = Table.grid(padding=(0, 1))
    foot.add_row(Text("Total spend", style="bold"),
                 Text(inr(total), style="bold red"))
    return Panel(Group(t, foot), title="Where Money Went", box=ROUNDED,
                 border_style="red")


def trend_panel(conn: sqlite3.Connection, months: int = 12) -> Panel:
    series = models.monthly_series(conn, months)
    peak = max((max(s["income"], s["living"]) for s in series), default=0) or 1
    t = Table(width=W - 4, box=None, pad_edge=False)
    t.add_column("Month")
    t.add_column("Income", justify="right")
    t.add_column("", ratio=3)
    t.add_column("Expense", justify="right")
    t.add_column("", ratio=3)
    t.add_column("Rate", justify="right")
    for s in series:
        inc_w = int(s["income"] / peak * 14)
        exp_w = int(s["living"] / peak * 14)
        rate_style = (
            "green" if s["rate"] >= 50
            else "yellow" if s["rate"] >= 30 else "red"
        )
        t.add_row(
            s["month"],
            inr_compact(s["income"]) if s["income"] else "—",
            Text("▇" * inc_w, style="green"),
            inr_compact(s["living"]) if s["living"] else "—",
            Text("▇" * exp_w, style="red"),
            Text(f"{s['rate']:.0f}%", style=rate_style) if s["income"] else "—",
        )
    return Panel(t, title=f"Last {months} Months", box=ROUNDED,
                 border_style="green")


def portfolio_panel(conn: sqlite3.Connection) -> Panel:
    parts: list[Table | Text] = []

    balances = models.balance_all(conn)
    if balances:
        bt = Table(box=None, pad_edge=False, width=W - 4)
        bt.add_column("Instrument", ratio=2)
        bt.add_column("Kind")
        bt.add_column("Value", justify="right")
        bt.add_column("As of", justify="right")
        for b in balances:
            bt.add_row(
                b["instrument"], b["kind"], inr(b["amount"]),
                _staleness(b["as_of"]),
            )
        parts.append(bt)

    sips = models.portfolio_sips(conn)
    stocks = {k: v for k, v in models.stock_holdings(conn).items() if v["qty"] > 0}

    st = Table(box=None, pad_edge=False, width=W - 4)
    st.add_column("Holding", ratio=2)
    st.add_column("Invested", justify="right")
    st.add_column("Value", justify="right")
    st.add_column("P&L", justify="right")
    st.add_column("Freshness", justify="right")

    def pnl(v: float, pct: bool = True) -> str:
        color = "green" if v >= 0 else "red"
        return f"[{color}]{inr_compact(v)}[/]"

    for p in sips:
        gain = f"{p['gain_pct']:+.1f}%" if p["invested"] else "—"
        color = "green" if p["gain"] >= 0 else "red"
        st.add_row(
            f"SIP · {p['name']}",
            inr_compact(p["invested"]),
            inr_compact(p["value"]),
            f"[{color}]{inr_compact(p['gain'])} ({gain})[/]",
            _staleness(p["as_of"]),
        )
    for sym, h in sorted(stocks.items()):
        color = "green" if h["unrealized"] >= 0 else "red"
        pct = (h["unrealized"] / h["cost"] * 100) if h["cost"] else 0
        st.add_row(
            f"STK · {sym} ({h['qty']:g})",
            inr_compact(h["cost"]),
            inr_compact(h["value"]),
            f"[{color}]{inr_compact(h['unrealized'])} ({pct:+.1f}%)[/]",
            _staleness(h["priced_at"]),
        )
    if sips or stocks:
        parts.append(st)

    nw = models.net_worth(conn)
    flows, _terminal = models.xirr_flows(conn)
    r = math_tools.xirr(flows)
    xirr_txt = (
        f"{r * 100:+.1f}%" if r is not None
        else "add SIP logs/buys to compute"
    )
    foot = Table.grid(padding=(0, 2))
    foot.add_row(
        Text("Net worth", style="bold"),
        Text(inr(nw["total"]), style="bold cyan"),
        Text("XIRR", style="bold"),
        Text(xirr_txt, style="bold magenta"),
    )
    if not parts:
        parts.append(Text("No instruments recorded yet.", style="dim"))
    return Panel(Group(*parts, foot), title="Portfolio", box=ROUNDED,
                 border_style="magenta")


def forecast_panel(conn: sqlite3.Connection) -> Panel:
    rate = float(models.get_meta(conn, "default_return_pct") or 12)
    active_monthly = sum(
        p["monthly"] for p in models.portfolio_sips(conn) if p["active"]
    )
    extra = float(models.get_meta(conn, "extra_monthly_invest") or 0)
    monthly = active_monthly + extra
    t = Table(width=W - 4, box=None, pad_edge=False)
    t.add_column("Horizon", justify="left")
    t.add_column("Contributed", justify="right")
    t.add_column(f"Corpus @{rate:.0f}% p.a.", justify="right")
    t.add_column("Growth", justify="right")
    if monthly <= 0:
        body: Table | Text = Text(
            f"No active SIP/monthly investment set — run "
            f"'fintrack sip add' or set --amount on 'fintrack forecast'.",
            style="dim",
        )
    else:
        for yrs in (3, 5, 10, 15, 25):
            fv, contributed = math_tools.sip_future_value(
                monthly, rate, yrs
            )
            growth = fv - contributed
            t.add_row(
                f"{yrs:>2} yr", inr_compact(contributed),
                Text(inr_compact(fv), style="bold cyan"),
                Text(f"+{inr_compact(growth)}", style="green"),
            )
        body = t
    liquid = models.liquid_total(conn)
    burn = models.avg_burn(conn)
    runway = math_tools.months_of_runway(liquid, burn)
    rw = (
        f"{runway:.1f} months"
        if runway is not None else "n/a (no expense data)"
    )
    rw_style = "green" if (runway or 0) >= 6 else "yellow" if (runway or 0) >= 3 else "red"
    foot = Table.grid(padding=(0, 2))
    foot.add_row(
        Text("Liquid", style="bold"), inr(liquid),
        Text("Avg burn/mo", style="bold"), inr(burn),
        Text("Runway", style="bold"), Text(rw, style=f"bold {rw_style}"),
    )
    return Panel(
        Group(body, Text(), foot), title="Forecast & Runway",
        box=ROUNDED, border_style="yellow",
    )


def render_report(conn: sqlite3.Connection, month: str) -> None:
    console = Console(width=W + 12)
    console.print(summary_panel(conn, month))
    cp = category_panel(conn, month)
    if cp:
        console.print(cp)
    else:
        console.print("[dim]no expenses recorded that month[/]")


def cheatsheet_panel() -> Panel:
    rows = [
        ("ADD SPEND", 'money add expense 450 -c food -n "swiggy"'),
        ("SALARY / BONUS", "money add salary 100000 · money add bonus 50000"),
        ("SIP DAY", "money sip log Axis 7500 --nav 52.30"),
        ("STOCKS", "money stock buy RELIANCE 5 2450"),
        ("MARK PRICE", "money price set RELIANCE 2680"),
        ("BALANCES", "money balance set bank1 -k bank 10.00L"),
        ("IMPORT CSV", "drop file in imports/ →"),
        ("", "money import statement imports/f.csv -s bankname"),
        ("MONTHLY PLAN",
         'money import budget-sheet "budgetsheet.csv"'),
        ("LOOK CLOSER", "money list · money report · money xirr"),
        ("", "money forecast --stepup 10"),
        ("FIX MISTAKES", "money txn retype ID expense · money txn cat ID food"),
        ("SAFETY", "money backup · money export"),
        ("DETAILS", "money <any-command> --help"),
    ]
    t = Table(width=W - 4, box=None, pad_edge=False)
    t.add_column("WHEN", style="bold dim", no_wrap=True)
    t.add_column("DO THIS", style="dim")
    for when, cmd in rows:
        t.add_row(when, cmd)
    return Panel(t, title="Cheat Sheet — you never have to remember these",
                 box=ROUNDED, border_style="dim white")


def render(conn: sqlite3.Connection, month: str | None = None) -> None:
    m = month or date.today().strftime("%Y-%m")
    console = Console(width=W + 12)
    console.print(header_panel(m))
    pp = plan_panel(conn, m)
    if pp:
        console.print(pp)
    console.print(summary_panel(conn, m))
    cp = category_panel(conn, m)
    if cp:
        console.print(cp)
    console.print(trend_panel(conn))
    console.print(portfolio_panel(conn))
    console.print(forecast_panel(conn))
    console.print(cheatsheet_panel())
