# FinTrack

100% offline personal finance manager for the terminal. INR-native.
No network code exists anywhere in this project — nothing you record ever
leaves your machine.

## Security model

- **Zero networking**: no `requests`, `urllib`, `socket`, no API keys, ever.
- **Never asks** for bank logins, account numbers, PAN, phone or email.
- Database at `data/finance.db` is created with `chmod 600`.
- Statement imports auto-mask any digit-run of 9+ characters (`****1234`)
  so account numbers never persist in notes.
- Backups are clean snapshots written with `chmod 600`
  (`ft backup`, keeps last 10).

## Install

```bash
pip install -r requirements.txt
```

Run it as a module: `python -m fintrack`. For daily ergonomics, alias it in
your shell, e.g. `ft(){ python -m fintrack "$@"; }` — then `ft dashboard` is
the full picture and `ft <subcommand>` handles the rest.

## First-time setup (example values)

```bash
ft import budget-sheet "budgetsheet.csv"            # salary history + spending plan
ft balance set bank1 -k bank 10.00L
ft balance set bank2 -k bank 2.00L
ft balance set mf    -k mf   5.00L
ft balance set sip   -k sip  1.00L
```

Balances accept lakh notation (`10.00L`). Refresh them monthly — the
dashboard shows how stale each number is.

## Daily use

```bash
ft add expense 450 -c food -n "lunch" -m upi
ft add expense 1.5L -c rent            # lakh notation works everywhere
ft add debit 5000 --transfer           # self-transfer: excluded from spend
ft add salary 100000
ft list --month 2026-08
ft budget set food 8000 && ft budget show
ft rule add swiggy food                # teach auto-categorization
```

## Statements (CSV only)

Download CSVs from net-banking yourself; FinTrack parses them locally.

```bash
ft import statement "CreditCardStatement (1).CSV" -s cc1
ft txn retype 123 expense              # fix anything mis-flagged
ft txn cat 124 health
```

Rows are deduped by content hash, so re-importing an overlapping file is safe.

## Investing

```bash
ft sip add "Axis Bluechip" -a 7500 --start 2026-01-01 --stepup 10
ft sip log Axis 7500 --nav 52.30       # monthly, units auto-computed
ft stock buy RELIANCE 5 2450
ft price set RELIANCE 2680             # offline price marks
ft xirr                                # money-weighted return
ft forecast --stepup 10                # corpus at 3/5/10/15/25 yr
ft dashboard                           # the whole picture
```

## Methodology (matches your own budget sheet)

- Income = salary + bonus + ITR + other credits.
- Living expense excludes transfers and CC bill payments.
- Savings target is set per month (income − living expense − SIP −
  investments); Plan-vs-Actual tracks it every month.
- Savings-rate baseline is user-configurable (`ft meta savings_rate_baseline`).

## Math notes

- XIRR: Newton-Raphson with bisection fallback over dated cashflows;
  terminal value = tracked SIP units × last NAV + stocks qty × last mark.
- Forecast: beginning-of-month contributions compounded monthly at the
  assumed annual rate, step-up applied after each 12 months.
- Runway: liquid bank balances ÷ avg living burn of last 3 months.

## Housekeeping

```bash
ft export                              # CSV dump -> imports/export/
ft backup                              # snapshot -> data/backups/
pytest tests/                          # math + parser regression suite
```

The original budget-sheet CSV is only ever read, never modified.