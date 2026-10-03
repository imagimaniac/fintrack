from __future__ import annotations

import re


def _group_indian(n: int) -> str:
    s = str(abs(int(n)))
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts + [tail])


def inr(amount: float, decimals: bool = True) -> str:
    neg = amount < 0
    q = round(abs(float(amount)), 2)
    whole = int(q)
    paise = round((q - whole) * 100)
    if paise == 100:
        whole += 1
        paise = 0
    body = _group_indian(whole)
    if decimals and paise:
        body += f".{paise:02d}"
    prefix = "-₹" if neg else "₹"
    return f"{prefix}{body}"


def inr_compact(amount: float) -> str:
    a = abs(amount)
    sign = "-" if amount < 0 else ""
    if a >= 1_00_00_000:
        return f"{sign}₹{a / 1_00_00_000:.2f} Cr"
    if a >= 1_00_000:
        return f"{sign}₹{a / 1_00_000:.2f} L"
    if a >= 1_000:
        return f"{sign}₹{a / 1_000:.1f} K"
    return inr(amount)


def parse_amount(raw: str | float | int) -> float:
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    s = s.replace("₹", "").replace("Rs.", "").replace("Rs", "").replace("INR", "")
    s = s.replace(",", "").replace(" ", "")
    if not s or s in {"-", "NA"}:
        raise ValueError(f"unparsable amount: {raw!r}")
    mult = 1.0
    m = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)(L|l|CR|Cr|cr|K|k)", s)
    if m:
        s = m.group(1)
        unit = m.group(2).lower()
        mult = {"l": 1e5, "cr": 1e7, "k": 1e3}[unit]
    return float(s) * mult


_SENSITIVE = re.compile(r"\d{9,}")


def mask_sensitive(text: str) -> str:
    def repl(m: re.Match) -> str:
        digits = m.group(0)
        return "*" * (len(digits) - 4) + digits[-4:]

    return _SENSITIVE.sub(repl, text)


def bar(pct: float, width: int = 20, fill: str = "▓", empty: str = "░") -> str:
    pct = max(0.0, min(pct, 200.0))
    filled = int(round(min(pct, 100.0) / 100 * width))
    return fill * filled + empty * (width - filled)
