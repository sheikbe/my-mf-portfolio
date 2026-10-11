from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from src.providers.base import ProviderError
from src.providers.csv_provider import CSVPortfolioProvider

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "sample_holdings.csv"


def test_parses_sample_csv() -> None:
    portfolio = CSVPortfolioProvider(SAMPLE).get_portfolio()
    assert portfolio.source == "csv"
    assert len(portfolio.holdings) == 5
    first = portfolio.holdings[0]
    assert first.code == "PPFCF-GR"
    assert first.folio_number == "1111111/11"
    assert first.units == 250.0
    assert first.current_value == pytest.approx(250 * 80)  # units x nav when current_value blank
    assert first.direct_plan is True
    assert first.category == "Equity"
    assert first.fund_details.amc == "PPFAS Mutual Fund"
    assert first.sips[0].amount == 5000
    # Explicit current_value wins and NAV is back-filled.
    balanced = portfolio.holdings[4]
    assert balanced.current_value == 21000
    assert balanced.fund_details.nav == pytest.approx(70.0)
    assert portfolio.holdings[3].direct_plan is False
    assert len(portfolio.cashflows) == 5
    assert portfolio.cashflows[0].date == date(2023, 1, 10)
    assert portfolio.cashflows[0].amount == -15000
    assert portfolio.xirr is not None


def test_merges_rows_with_same_code() -> None:
    merged = CSVPortfolioProvider(SAMPLE).get_portfolio().merged_holdings()
    assert len(merged) == 4
    ppfas = merged[0]
    assert ppfas.folio_count == 2
    assert ppfas.units == pytest.approx(350.0)
    assert ppfas.invested_value == pytest.approx(21000.0)


def test_minimal_columns_and_bytes_input() -> None:
    csv = b"Scheme Name,Units,Invested Value\nSome Fund,10,1000\n"
    portfolio = CSVPortfolioProvider(csv).get_portfolio()
    holding = portfolio.holdings[0]
    assert holding.code == "Some Fund"
    assert holding.current_value == 1000  # falls back to invested when no nav/current value
    assert holding.category == "Other"
    assert portfolio.cashflows == []
    assert portfolio.xirr is None


def test_amount_formatting_is_tolerated() -> None:
    csv = 'scheme_name,units,invested_value,current_value\nFund,"1,000.5","₹10,000","12,500"\n'
    holding = CSVPortfolioProvider(csv.encode()).get_portfolio().holdings[0]
    assert holding.units == pytest.approx(1000.5)
    assert holding.invested_value == 10000
    assert holding.current_value == 12500


def test_missing_required_column() -> None:
    with pytest.raises(ProviderError, match="invested_value"):
        CSVPortfolioProvider(b"scheme_name,units\nFund,1\n").get_portfolio()


def test_non_numeric_value() -> None:
    with pytest.raises(ProviderError, match="Row 2: 'units'"):
        CSVPortfolioProvider(b"scheme_name,units,invested_value\nFund,abc,1\n").get_portfolio()


def test_invalid_date() -> None:
    csv = b"scheme_name,units,invested_value,investment_date\nFund,1,100,not-a-date\n"
    with pytest.raises(ProviderError, match="investment_date"):
        CSVPortfolioProvider(csv).get_portfolio()


def test_empty_file() -> None:
    with pytest.raises(ProviderError):
        CSVPortfolioProvider(b"").get_portfolio()
