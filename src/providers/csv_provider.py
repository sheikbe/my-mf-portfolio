"""Fallback provider: parse a holdings CSV exported/maintained by the user (no token needed).

Schema (header names are case-insensitive; see README for details):

required: scheme_name, units, invested_value
optional: scheme_code, amc, category, folio_number, nav, current_value, plan,
          investment_date, sip_amount, sip_frequency

Each row is a folio or a purchase lot. Rows sharing a scheme_code (or scheme_name) are merged
for display. When ``investment_date`` is present, each row becomes a dated outflow used to
compute XIRR with pyxirr.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import IO, Any

import pandas as pd
from loguru import logger

from src.analytics import compute_xirr
from src.models import CashFlow, FundDetails, Holding, Portfolio, Sip
from src.providers.base import PortfolioProvider, ProviderError

REQUIRED_COLUMNS = ("scheme_name", "units", "invested_value")
OPTIONAL_COLUMNS = (
    "scheme_code",
    "amc",
    "category",
    "folio_number",
    "nav",
    "current_value",
    "plan",
    "investment_date",
    "sip_amount",
    "sip_frequency",
)
CsvSource = str | Path | bytes | IO[bytes] | IO[str]


def _clean(value: Any) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _to_float(value: Any, column: str, row: int) -> float | None:
    value = _clean(value)
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").replace("₹", ""))
    except ValueError as exc:
        raise ProviderError(f"Row {row}: '{column}' must be a number, got {value!r}") from exc


class CSVPortfolioProvider(PortfolioProvider):
    def __init__(self, source: CsvSource, *, name: str | None = "CSV upload") -> None:
        self._source = source
        self._name = name

    def _read(self) -> pd.DataFrame:
        source = self._source
        if isinstance(source, bytes):
            source = io.BytesIO(source)
        try:
            df = pd.read_csv(source, dtype=str, keep_default_na=False)
        except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError) as exc:
            raise ProviderError(f"Could not parse CSV: {exc}") from exc
        df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
        missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ProviderError(f"CSV is missing required column(s): {', '.join(missing)}")
        return df

    def get_portfolio(self) -> Portfolio:
        df = self._read()
        holdings: list[Holding] = []
        cashflows: list[CashFlow] = []
        for idx, raw in enumerate(df.to_dict(orient="records"), start=2):
            row = {k: _clean(v) for k, v in raw.items()}
            name = row.get("scheme_name")
            if not name:
                raise ProviderError(f"Row {idx}: 'scheme_name' is required")
            units = _to_float(row.get("units"), "units", idx) or 0.0
            invested = _to_float(row.get("invested_value"), "invested_value", idx) or 0.0
            nav = _to_float(row.get("nav"), "nav", idx)
            current = _to_float(row.get("current_value"), "current_value", idx)
            if current is None:
                current = units * nav if nav is not None else invested
            elif nav is None and units:
                nav = current / units
            code = row.get("scheme_code") or name
            plan = (row.get("plan") or "").lower()
            sip_amount = _to_float(row.get("sip_amount"), "sip_amount", idx)
            sips = (
                [Sip(amount=sip_amount, frequency=row.get("sip_frequency") or "Monthly")]
                if sip_amount
                else None
            )
            holdings.append(
                Holding(
                    code=code,
                    folio_number=row.get("folio_number") or "Unknown",
                    units=units,
                    lock_free_units=units,
                    direct_plan=plan.startswith("direct"),
                    invested_value=invested,
                    current_value=current,
                    category=row.get("category"),
                    sip_running=bool(sips),
                    sips=sips,
                    fund_details=FundDetails(
                        code=code, name=name, fund_name=row.get("amc"), nav=nav
                    ),
                )
            )
            if row.get("investment_date") and invested:
                try:
                    when = pd.to_datetime(row["investment_date"], dayfirst=False).date()
                except (ValueError, TypeError) as exc:
                    raise ProviderError(
                        f"Row {idx}: invalid investment_date {row['investment_date']!r}"
                    ) from exc
                cashflows.append(CashFlow(date=when, amount=-invested))

        portfolio = Portfolio(
            source="csv", holdings=holdings, cashflows=cashflows, portfolio_name=self._name
        )
        portfolio.computed_xirr = compute_xirr(
            cashflows, portfolio.total_current_value, portfolio.as_of
        )
        logger.info(
            "Parsed CSV portfolio: {} rows, {} dated cashflows", len(holdings), len(cashflows)
        )
        return portfolio
