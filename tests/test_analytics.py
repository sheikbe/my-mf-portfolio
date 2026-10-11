from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pyxirr import xirr

from src import analytics
from src.models import CashFlow, FundDetails, Holding, Portfolio, Sip
from src.providers.csv_provider import CSVPortfolioProvider

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "sample_holdings.csv"


def holding(code: str, category: str, invested: float, current: float, **kw) -> Holding:
    return Holding(
        code=code,
        category=category,
        invested_value=invested,
        current_value=current,
        fund_details=FundDetails(
            code=code,
            name=kw.pop("name", code),
            fund_name=kw.pop("amc", None),
            returns=kw.pop("returns", None),
        ),
        **kw,
    )


@pytest.fixture
def holdings() -> list[Holding]:
    return [
        holding(
            "A",
            "Equity",
            1000,
            1500,
            amc="AMC1",
            direct_plan=True,
            returns={"year_1": 12.0, "year_3": "15.5", "date": "2026-10-09"},
        ),
        holding("A", "Equity", 500, 500, amc="AMC1", direct_plan=True, folio_number="F2"),
        holding("B", "Debt", 1000, 1000, amc="AMC2"),
        holding("C", "Hybrid", 1000, 0, amc="AMC2", direct_plan=True),
        holding("D", "Solution Oriented", 0, 1000),
    ]


def test_totals_and_absolute_return(holdings: list[Holding]) -> None:
    assert analytics.total_invested(holdings) == 3500
    assert analytics.total_current_value(holdings) == 4000
    assert analytics.absolute_return_pct(3500, 4000) == pytest.approx(500 / 3500 * 100)
    assert analytics.absolute_return_pct(0, 100) is None
    assert analytics.absolute_return_pct(100, 80) == pytest.approx(-20.0)


def test_portfolio_aggregates(holdings: list[Holding]) -> None:
    p = Portfolio(source="csv", holdings=holdings)
    assert p.total_gain == 500
    assert p.absolute_return_pct == pytest.approx(500 / 3500 * 100)
    dumped = p.model_dump()
    assert dumped["total_current_value"] == 4000


def test_allocation_by_category(holdings: list[Holding]) -> None:
    slices = analytics.allocation_by_category(holdings)
    assert [s.label for s in slices] == ["Equity", "Debt", "Other"]  # zero-value Hybrid dropped
    by_label = {s.label: s for s in slices}
    assert by_label["Equity"].value == 2000
    assert by_label["Equity"].percent == pytest.approx(50.0)
    assert by_label["Other"].value == 1000
    assert sum(s.percent for s in slices) == pytest.approx(100.0)


def test_allocation_by_amc_and_plan(holdings: list[Holding]) -> None:
    amc = {s.label: s.value for s in analytics.allocation_by_amc(holdings)}
    assert amc == {"AMC1": 2000, "AMC2": 1000, "D": 1000}  # falls back to first word of name
    plan = analytics.allocation_by_plan(holdings)
    assert [(s.label, s.value) for s in plan] == [("Direct", 2000), ("Regular", 2000)]


def test_allocation_empty() -> None:
    assert analytics.allocation_by_category([]) == []


def test_sip_summary() -> None:
    hs = [
        holding(
            "A",
            "Equity",
            0,
            0,
            sip_running=True,
            sips=[Sip(amount=1000, frequency="Monthly"), Sip(amount=500, frequency="Weekly")],
        ),
        holding("B", "Equity", 0, 0, sip_running=True),
        holding("C", "Debt", 0, 0),
    ]
    summary = analytics.sip_summary(hs)
    assert summary.active_sip_count == 2
    assert summary.funds_with_sip == 2
    assert summary.monthly_sip_amount == pytest.approx(1000 + 500 * 52 / 12)


def test_fund_returns_merges_folios_and_uses_scheme_returns(holdings: list[Holding]) -> None:
    returns = {r.code: r for r in analytics.fund_returns(holdings)}
    a = returns["A"]
    assert a.invested_value == 1500
    assert a.current_value == 2000
    assert a.absolute_return_pct == pytest.approx(500 / 1500 * 100)
    assert a.returns == {"year_1": 12.0, "year_3": 15.5}  # non-numeric 'date' dropped
    assert returns["C"].absolute_return_pct == pytest.approx(-100.0)
    assert returns["D"].absolute_return_pct is None
    assert next(iter(returns)) == "A"  # sorted by current value desc


def test_compute_xirr_matches_pyxirr() -> None:
    flows = [
        CashFlow(date=date(2024, 1, 1), amount=-10000),
        CashFlow(date=date(2024, 7, 1), amount=-5000),
    ]
    result = analytics.compute_xirr(flows, 16500, date(2025, 1, 1))
    expected = xirr([date(2024, 1, 1), date(2024, 7, 1), date(2025, 1, 1)], [-10000, -5000, 16500])
    assert result == pytest.approx(expected * 100)


def test_compute_xirr_one_year_ten_percent() -> None:
    flows = [CashFlow(date=date(2023, 1, 1), amount=-1000)]
    assert analytics.compute_xirr(flows, 1100, date(2024, 1, 1)) == pytest.approx(10.0, abs=0.05)


def test_compute_xirr_insufficient_data() -> None:
    assert analytics.compute_xirr([], 1000, date(2024, 1, 1)) is None
    flows = [CashFlow(date=date(2023, 1, 1), amount=-1000)]
    assert analytics.compute_xirr(flows, 0, date(2024, 1, 1)) is None


def test_xirr_on_csv_fixture() -> None:
    portfolio = CSVPortfolioProvider(SAMPLE).get_portfolio()
    as_of = date(2026, 1, 1)
    expected = xirr(
        [cf.date for cf in portfolio.cashflows] + [as_of],
        [cf.amount for cf in portfolio.cashflows] + [portfolio.total_current_value],
    )
    assert analytics.compute_xirr(portfolio.cashflows, portfolio.total_current_value, as_of) == (
        pytest.approx(expected * 100)
    )
    # CSV portfolios report the computed XIRR; there is no Kuvera performance object.
    assert portfolio.performance is None
    assert portfolio.xirr == portfolio.computed_xirr


def test_dataframes(holdings: list[Holding]) -> None:
    p = Portfolio(source="csv", holdings=holdings)
    merged = analytics.holdings_dataframe(p)
    assert len(merged) == 4
    assert merged.loc[merged["Code"] == "A", "Folios"].item() == 2
    assert len(analytics.folios_dataframe(p)) == 5
