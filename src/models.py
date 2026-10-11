"""Pydantic models mirroring the shapes returned by ``KuveraClient`` plus derived views."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

Category = Literal["Equity", "Debt", "Hybrid", "Other"]
CATEGORIES: tuple[Category, ...] = ("Equity", "Debt", "Hybrid", "Other")
PortfolioSource = Literal["kuvera", "csv"]


def normalize_category(value: Any) -> Category:
    """Map Kuvera's ``kuvera_category`` (or a CSV value) onto Equity/Debt/Hybrid/Other."""
    if isinstance(value, str):
        text = value.strip().lower()
        for category in ("Equity", "Debt", "Hybrid"):
            if text.startswith(category.lower()):
                return category  # type: ignore[return-value]
    return "Other"


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class FundDetails(_Model):
    aum: float | None = None
    category: str | None = None
    code: str | None = None
    expense_ratio: float | None = None
    name: str | None = None
    fund_name: str | None = None
    nav: float | None = None
    nav_date: str | None = None
    returns: dict[str, Any] | None = None
    volatility: float | None = None

    @field_validator("aum", "expense_ratio", "nav", "volatility", mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        return None if value in ("", "NA", "N/A", "-") else value

    @property
    def amc(self) -> str | None:
        """Best-effort AMC label: Kuvera's ``fund_name``, else the first word of the scheme name."""
        if self.fund_name:
            return self.fund_name
        if self.name:
            return self.name.split()[0]
        return None

    def numeric_returns(self) -> dict[str, float]:
        """``returns`` with non-numeric entries (e.g. dates) dropped and values cast to float."""
        out: dict[str, float] = {}
        for key, value in (self.returns or {}).items():
            if isinstance(value, bool):
                continue
            try:
                out[key] = float(value)
            except (TypeError, ValueError):
                continue
        return out


class Sip(_Model):
    amount: float | None = None
    order_date: str | None = None
    frequency: str | None = None
    start_date: str | None = None


class Holding(_Model):
    """One folio of one fund, exactly as produced by ``KuveraClient.get_holdings``."""

    code: str
    folio_number: str = "Unknown"
    units: float = 0.0
    direct_plan: bool = False
    lock_free_units: float = 0.0
    invested_value: float = 0.0
    current_value: float = 0.0
    fund_details: FundDetails = Field(default_factory=FundDetails)
    sip_running: bool = False
    category: Category = "Other"
    sips: list[Sip] | None = None

    @field_validator("category", mode="before")
    @classmethod
    def _normalize_category(cls, value: Any) -> Category:
        return normalize_category(value)

    @field_validator("units", "lock_free_units", "invested_value", "current_value", mode="before")
    @classmethod
    def _none_to_zero(cls, value: Any) -> Any:
        return 0.0 if value is None else value

    @field_validator("folio_number", mode="before")
    @classmethod
    def _folio_to_str(cls, value: Any) -> str:
        return "Unknown" if value in (None, "") else str(value)

    @field_validator("fund_details", mode="before")
    @classmethod
    def _empty_details(cls, value: Any) -> Any:
        return {} if value is None else value

    @property
    def scheme_name(self) -> str:
        return self.fund_details.name or self.code

    @property
    def gain(self) -> float:
        return self.current_value - self.invested_value


class MergedHolding(_Model):
    """All folios of a single fund code collapsed into one logical position."""

    code: str
    scheme_name: str
    amc: str | None = None
    category: Category = "Other"
    direct_plan: bool = False
    units: float = 0.0
    invested_value: float = 0.0
    current_value: float = 0.0
    sip_running: bool = False
    sips: list[Sip] = Field(default_factory=list)
    fund_details: FundDetails = Field(default_factory=FundDetails)
    folios: list[Holding] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def folio_count(self) -> int:
        return len(self.folios)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def gain(self) -> float:
        return self.current_value - self.invested_value

    @computed_field  # type: ignore[prop-decorator]
    @property
    def absolute_return_pct(self) -> float | None:
        if not self.invested_value:
            return None
        return self.gain / self.invested_value * 100


def merge_holdings(holdings: list[Holding]) -> list[MergedHolding]:
    """Merge folios sharing a fund code, preserving first-seen order and per-folio detail."""
    merged: dict[str, MergedHolding] = {}
    for h in holdings:
        m = merged.get(h.code)
        if m is None:
            m = MergedHolding(
                code=h.code,
                scheme_name=h.scheme_name,
                amc=h.fund_details.amc,
                category=h.category,
                direct_plan=h.direct_plan,
                fund_details=h.fund_details,
            )
            merged[h.code] = m
        m.units += h.units
        m.invested_value += h.invested_value
        m.current_value += h.current_value
        m.sip_running = m.sip_running or h.sip_running
        m.sips.extend(h.sips or [])
        m.folios.append(h)
    return list(merged.values())


class PortfolioPerformance(_Model):
    portfolio_id: int | None = None
    current_value: float | None = None
    current_gain: float | None = None
    current_gain_percent: float | None = None
    one_day_gain: float | None = None
    one_day_gain_percent: float | None = None
    invested: float | None = None
    current_xirr: float | None = None
    alltime_xirr: float | None = None
    alltime_return: float | None = None
    alltime_abs_percentage: float | None = None
    alltime_abs_return: float | None = None
    portfolio_type: str | None = None
    mutual_funds: Any = None


class PortfolioSummary(_Model):
    """Lightweight entry from ``KuveraClient.get_portfolios`` used for the portfolio selector."""

    portfolio_id: int | str
    portfolio_name: str | None = None
    portfolio_code: str | None = None
    account_status: str | None = None
    mode_of_investment: str | None = None

    @property
    def label(self) -> str:
        return self.portfolio_name or self.portfolio_code or str(self.portfolio_id)


class CashFlow(_Model):
    """Dated cashflow (negative = investment). Only populated for the CSV path."""

    date: date
    amount: float


class EquityExposure(_Model):
    portfolio_date: str | None = None
    company_name: str | None = None
    percentage_to_aum: float | None = None
    ticker: str | None = None
    proportionate_amount: float = 0.0


class Portfolio(_Model):
    source: PortfolioSource
    holdings: list[Holding] = Field(default_factory=list)
    performance: PortfolioPerformance | None = None
    portfolio_id: int | str | None = None
    portfolio_name: str | None = None
    cashflows: list[CashFlow] = Field(default_factory=list)
    computed_xirr: float | None = Field(
        default=None, description="XIRR in percent computed from cashflows (CSV path only)."
    )
    as_of: datetime = Field(default_factory=datetime.now)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_invested(self) -> float:
        return sum(h.invested_value for h in self.holdings)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_current_value(self) -> float:
        return sum(h.current_value for h in self.holdings)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_gain(self) -> float:
        return self.total_current_value - self.total_invested

    @computed_field  # type: ignore[prop-decorator]
    @property
    def absolute_return_pct(self) -> float | None:
        if not self.total_invested:
            return None
        return self.total_gain / self.total_invested * 100

    @computed_field  # type: ignore[prop-decorator]
    @property
    def xirr(self) -> float | None:
        """Kuvera's precomputed ``current_xirr`` when available, else the CSV-derived XIRR."""
        if self.performance and self.performance.current_xirr is not None:
            return self.performance.current_xirr
        return self.computed_xirr

    def merged_holdings(self) -> list[MergedHolding]:
        return merge_holdings(self.holdings)
