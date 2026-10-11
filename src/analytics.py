"""Pure, side-effect-free portfolio analytics."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import date, datetime

import pandas as pd
from pydantic import BaseModel

from src.models import CATEGORIES, CashFlow, Holding, MergedHolding, Portfolio, merge_holdings

# Approximate number of instalments per month for each SIP frequency.
SIP_MONTHLY_FACTOR: dict[str, float] = {
    "daily": 21.0,
    "weekly": 52 / 12,
    "fortnightly": 26 / 12,
    "monthly": 1.0,
    "quarterly": 1 / 3,
    "half-yearly": 1 / 6,
    "annually": 1 / 12,
    "yearly": 1 / 12,
}


class AllocationSlice(BaseModel):
    label: str
    value: float
    percent: float


class SipLine(BaseModel):
    code: str
    scheme_name: str
    amount: float
    frequency: str | None
    monthly_equivalent: float
    start_date: str | None = None


class SipSummary(BaseModel):
    active_sip_count: int
    funds_with_sip: int
    monthly_sip_amount: float
    sips: list[SipLine]


class FundReturn(BaseModel):
    code: str
    scheme_name: str
    category: str
    invested_value: float
    current_value: float
    gain: float
    absolute_return_pct: float | None
    expense_ratio: float | None = None
    returns: dict[str, float] = {}


# ---------------------------------------------------------------------------
# Totals
# ---------------------------------------------------------------------------


def total_invested(holdings: Iterable[Holding | MergedHolding]) -> float:
    return sum(h.invested_value for h in holdings)


def total_current_value(holdings: Iterable[Holding | MergedHolding]) -> float:
    return sum(h.current_value for h in holdings)


def absolute_return_pct(invested: float, current: float) -> float | None:
    """Simple (non-annualised) return in percent; None when nothing was invested."""
    if not invested:
        return None
    return (current - invested) / invested * 100


# ---------------------------------------------------------------------------
# Allocation
# ---------------------------------------------------------------------------


def _allocation(
    holdings: Iterable[Holding], key: Callable[[Holding], str], order: Iterable[str] = ()
) -> list[AllocationSlice]:
    sums: dict[str, float] = defaultdict(float)
    for h in holdings:
        sums[key(h)] += h.current_value
    buckets = {k: v for k, v in sums.items() if v}
    total = sum(buckets.values())
    labels = [label for label in order if label in buckets]
    labels += sorted((k for k in buckets if k not in labels), key=lambda k: -buckets[k])
    return [
        AllocationSlice(
            label=label,
            value=buckets[label],
            percent=(buckets[label] / total * 100) if total else 0.0,
        )
        for label in labels
    ]


def allocation_by_category(holdings: Iterable[Holding]) -> list[AllocationSlice]:
    return _allocation(holdings, lambda h: h.category, order=CATEGORIES)


def allocation_by_amc(holdings: Iterable[Holding]) -> list[AllocationSlice]:
    return _allocation(holdings, lambda h: h.fund_details.amc or "Unknown")


def allocation_by_plan(holdings: Iterable[Holding]) -> list[AllocationSlice]:
    return _allocation(
        holdings, lambda h: "Direct" if h.direct_plan else "Regular", order=("Direct", "Regular")
    )


def allocation_by_fund(holdings: Iterable[Holding]) -> list[AllocationSlice]:
    return _allocation(holdings, lambda h: h.scheme_name)


# ---------------------------------------------------------------------------
# SIPs and per-fund returns
# ---------------------------------------------------------------------------


def sip_summary(holdings: Iterable[Holding]) -> SipSummary:
    lines: list[SipLine] = []
    funds: set[str] = set()
    for h in holdings:
        for sip in h.sips or []:
            amount = sip.amount or 0.0
            factor = SIP_MONTHLY_FACTOR.get((sip.frequency or "monthly").strip().lower(), 1.0)
            lines.append(
                SipLine(
                    code=h.code,
                    scheme_name=h.scheme_name,
                    amount=amount,
                    frequency=sip.frequency,
                    monthly_equivalent=amount * factor,
                    start_date=sip.start_date,
                )
            )
            funds.add(h.code)
        if h.sip_running and not h.sips:
            funds.add(h.code)
    return SipSummary(
        active_sip_count=len(lines),
        funds_with_sip=len(funds),
        monthly_sip_amount=sum(line.monthly_equivalent for line in lines),
        sips=lines,
    )


def fund_returns(holdings: list[Holding]) -> list[FundReturn]:
    """Per-fund (folios merged) gains plus the scheme's published returns from Kuvera."""
    return [
        FundReturn(
            code=m.code,
            scheme_name=m.scheme_name,
            category=m.category,
            invested_value=m.invested_value,
            current_value=m.current_value,
            gain=m.gain,
            absolute_return_pct=m.absolute_return_pct,
            expense_ratio=m.fund_details.expense_ratio,
            returns=m.fund_details.numeric_returns(),
        )
        for m in sorted(merge_holdings(holdings), key=lambda m: -m.current_value)
    ]


# ---------------------------------------------------------------------------
# XIRR (CSV path only - Kuvera supplies its own precomputed XIRR)
# ---------------------------------------------------------------------------


def compute_xirr(
    cashflows: Iterable[CashFlow], terminal_value: float, as_of: date | datetime | None = None
) -> float | None:
    """Annualised XIRR in percent from dated investments plus today's value as the final inflow.

    Returns None when there is not enough data (no dated cashflows / no sign change) or the
    solver fails to converge.
    """
    from pyxirr import InvalidPaymentsError, xirr

    flows = list(cashflows)
    if not flows or terminal_value <= 0:
        return None
    end = as_of or date.today()
    if isinstance(end, datetime):
        end = end.date()
    dates = [cf.date for cf in flows] + [end]
    amounts = [cf.amount for cf in flows] + [terminal_value]
    try:
        rate = xirr(dates, amounts)
    except (InvalidPaymentsError, ValueError):
        return None
    if rate is None:
        return None
    return float(rate) * 100


# ---------------------------------------------------------------------------
# Tabular views for the UI
# ---------------------------------------------------------------------------


def holdings_dataframe(portfolio: Portfolio) -> pd.DataFrame:
    """One row per fund (folios merged)."""
    rows = [
        {
            "Fund": m.scheme_name,
            "Code": m.code,
            "AMC": m.amc,
            "Category": m.category,
            "Plan": "Direct" if m.direct_plan else "Regular",
            "Folios": m.folio_count,
            "Units": round(m.units, 3),
            "NAV": m.fund_details.nav,
            "Invested": round(m.invested_value, 2),
            "Current value": round(m.current_value, 2),
            "Gain": round(m.gain, 2),
            "Return %": None if m.absolute_return_pct is None else round(m.absolute_return_pct, 2),
            "SIP": m.sip_running,
        }
        for m in portfolio.merged_holdings()
    ]
    return pd.DataFrame(rows)


def folios_dataframe(portfolio: Portfolio) -> pd.DataFrame:
    """One row per folio (no merging)."""
    rows = [
        {
            "Fund": h.scheme_name,
            "Code": h.code,
            "Folio": h.folio_number,
            "Units": round(h.units, 3),
            "Lock-free units": round(h.lock_free_units, 3),
            "Invested": round(h.invested_value, 2),
            "Current value": round(h.current_value, 2),
            "Gain": round(h.gain, 2),
            "SIP": h.sip_running,
        }
        for h in portfolio.holdings
    ]
    return pd.DataFrame(rows)
