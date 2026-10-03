from __future__ import annotations

from datetime import date


def xirr(flows: list[tuple[date, float]]) -> float | None:
    if len(flows) < 2:
        return None
    flows = sorted(flows, key=lambda f: f[0])
    signs = {1 if cf > 0 else -1 for _, cf in flows}
    if len(signs) < 2:
        return None
    d0 = flows[0][0]
    ts = [(d - d0).days / 365.0 for d, _ in flows]
    cfs = [cf for _, cf in flows]

    def npv(r: float) -> float:
        return sum(cf * (1.0 + r) ** (-t) for cf, t in zip(cfs, ts))

    def d_npv(r: float) -> float:
        return sum(
            cf * (-t) * (1.0 + r) ** (-t - 1.0) for cf, t in zip(cfs, ts)
        )

    for guess in (0.1, 0.5, 0.01, -0.5, 2.0):
        r = guess
        try:
            for _ in range(100):
                f = npv(r)
                df = d_npv(r)
                if df == 0 or f != f:
                    break
                step = f / df
                nr = r - step
                if nr <= -1.0:
                    nr = (r - 1.0) / 2.0
                if abs(nr - r) < 1e-10:
                    return nr
                r = nr
            if abs(npv(r)) < 1e-6:
                return r
        except (OverflowError, ZeroDivisionError, ValueError):
            continue

    lo, hi = -0.999999, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2.0
        f_mid = npv(mid)
        if abs(f_mid) < 1e-10 or (hi - lo) < 1e-12:
            return mid
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2.0


def sip_future_value(
    monthly: float,
    annual_rate_pct: float,
    years: float,
    step_up_pct: float = 0.0,
) -> tuple[float, float]:
    i = (1.0 + annual_rate_pct / 100.0) ** (1.0 / 12.0) - 1.0
    balance = 0.0
    contributed = 0.0
    m = monthly
    months = int(round(years * 12))
    for month in range(months):
        balance = (balance + m) * (1.0 + i)
        contributed += m
        if (month + 1) % 12 == 0:
            m *= 1.0 + step_up_pct / 100.0
    return balance, contributed


def lump_sum_future_value(
    principal: float, annual_rate_pct: float, years: float
) -> float:
    return principal * (1.0 + annual_rate_pct / 100.0) ** years


def months_of_runway(liquid: float, avg_monthly_burn: float) -> float | None:
    if avg_monthly_burn <= 0:
        return None
    return liquid / avg_monthly_burn
