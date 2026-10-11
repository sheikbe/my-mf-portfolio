"""KuveraPortfolioProvider tests with a mocked KuveraClient (no network)."""

from __future__ import annotations

import copy
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.config import Settings
from src.kuvera_client import KuveraClient
from src.providers.kuvera_provider import (
    InvalidTokenError,
    KuveraPortfolioProvider,
    KuveraUnavailableError,
    run_sync,
)
from tests.conftest import (
    ACCOUNT_RESPONSE,
    BASE,
    FUND_SCHEMES_RESPONSE,
    PERFORMANCE_RESPONSE,
)

PPFAS = {
    "code": "INF879O01027",
    "name": "Parag Parikh Flexi Cap Fund Direct Growth",
    "fund_name": "PPFAS Mutual Fund",
    "nav": 80.0,
    "returns": {"year_1": 18.5},
}
HDFC = {"code": "INF179K01VY8", "name": "HDFC Short Term Debt Fund", "nav": 30.0}

RAW_HOLDINGS = [
    {
        "code": "INF879O01027",
        "folio_number": "1111111/11",
        "units": 100.0,
        "direct_plan": True,
        "lock_free_units": 100.0,
        "invested_value": 6000.0,
        "current_value": 8000.0,
        "fund_details": PPFAS,
        "sip_running": True,
        "category": "Equity",
        "sips": [{"amount": 5000, "frequency": "Monthly", "start_date": "2023-01-05"}],
    },
    {
        "code": "INF879O01027",
        "folio_number": "2222222/22",
        "units": 50.0,
        "direct_plan": True,
        "lock_free_units": 25.0,
        "invested_value": 3500.0,
        "current_value": 4000.0,
        "fund_details": PPFAS,
        "sip_running": False,
        "category": "Equity",
    },
    {
        "code": "INF179K01VY8",
        "folio_number": "3333333/33",
        "units": 200.0,
        "direct_plan": False,
        "lock_free_units": 200.0,
        "invested_value": 5000.0,
        "current_value": 6000.0,
        "fund_details": HDFC,
        "sip_running": False,
        "category": "debt",
    },
]
RAW_PORTFOLIOS = [
    {"portfolio_id": 101, "portfolio_name": "Self", "portfolio_code": "P101"},
    {"portfolio_id": 202, "portfolio_name": "Family", "portfolio_code": "P202"},
]
RAW_PERFORMANCE = [
    {
        "portfolio_id": 101,
        "current_value": 18000.0,
        "invested": 14500.0,
        "current_xirr": 15.2,
        "alltime_xirr": 14.1,
        "current_gain_percent": 24.14,
    },
    {"portfolio_id": 202, "current_value": 1000.0, "invested": 900.0, "current_xirr": 9.0},
]


def fake_client(**overrides) -> MagicMock:
    client = MagicMock(spec=KuveraClient)
    client.get_account_info = AsyncMock(return_value=copy.deepcopy(ACCOUNT_RESPONSE))
    client.get_holdings = AsyncMock(return_value=copy.deepcopy(RAW_HOLDINGS))
    client.get_portfolios = AsyncMock(return_value=copy.deepcopy(RAW_PORTFOLIOS))
    client.get_portfolio_performance = AsyncMock(return_value=copy.deepcopy(RAW_PERFORMANCE))
    client.switch_portfolio = AsyncMock(return_value={"success": True, "data": {}})
    client.get_equity_distribution = AsyncMock(
        return_value=[
            {"company_name": "HDFC Bank", "percentage_to_aum": 8.0, "proportionate_amount": 800.0}
        ]
    )
    for name, value in overrides.items():
        setattr(client, name, AsyncMock(return_value=value))
    return client


def make_provider(token: str, settings: Settings, client: MagicMock, **kwargs):
    seen: list[httpx.AsyncClient] = []

    def factory(http: httpx.AsyncClient) -> MagicMock:
        seen.append(http)
        return client

    provider = KuveraPortfolioProvider(token, settings=settings, client_factory=factory, **kwargs)
    return provider, seen


def test_rejects_malformed_token(settings: Settings) -> None:
    with pytest.raises(InvalidTokenError):
        KuveraPortfolioProvider("not-a-jwt", settings=settings)


def test_repr_hides_token(token: str, settings: Settings) -> None:
    provider = KuveraPortfolioProvider(token, settings=settings)
    assert token not in repr(provider)
    assert provider.email == "investor@example.com"


def test_builds_httpx_client_from_settings(token: str) -> None:
    settings = Settings(
        _env_file=None, kuvera_api_base_url="https://kuvera.test", kuvera_api_timeout=3.5
    )
    provider, seen = make_provider(token, settings, fake_client())
    provider.get_portfolio()
    assert str(seen[0].base_url) == "https://kuvera.test"
    assert seen[0].timeout.read == 3.5


def test_maps_holdings_and_performance(token: str, settings: Settings) -> None:
    client = fake_client()
    provider, _ = make_provider(token, settings, client)
    portfolio = provider.get_portfolio()

    assert portfolio.source == "kuvera"
    assert portfolio.portfolio_id == 101
    assert portfolio.portfolio_name == "Self"
    assert len(portfolio.holdings) == 3
    assert portfolio.holdings[2].category == "Debt"
    assert portfolio.holdings[0].fund_details.amc == "PPFAS Mutual Fund"
    assert portfolio.holdings[0].sips[0].amount == 5000
    assert portfolio.total_invested == pytest.approx(14500.0)
    assert portfolio.total_current_value == pytest.approx(18000.0)
    # Kuvera's precomputed XIRR is preferred.
    assert portfolio.performance.portfolio_id == 101
    assert portfolio.xirr == 15.2
    client.switch_portfolio.assert_not_awaited()
    for method in (client.get_holdings, client.get_portfolios, client.get_portfolio_performance):
        method.assert_awaited_once_with(token)


def test_multi_folio_merge(token: str, settings: Settings) -> None:
    provider, _ = make_provider(token, settings, fake_client())
    merged = {m.code: m for m in provider.get_portfolio().merged_holdings()}

    assert list(merged) == ["INF879O01027", "INF179K01VY8"]
    ppfas = merged["INF879O01027"]
    assert ppfas.folio_count == 2
    assert [f.folio_number for f in ppfas.folios] == ["1111111/11", "2222222/22"]
    assert ppfas.units == pytest.approx(150.0)
    assert ppfas.invested_value == pytest.approx(9500.0)
    assert ppfas.current_value == pytest.approx(12000.0)
    assert ppfas.sip_running is True
    assert len(ppfas.sips) == 1
    assert ppfas.absolute_return_pct == pytest.approx(2500 / 9500 * 100)
    assert merged["INF179K01VY8"].folio_count == 1


def test_switches_when_other_portfolio_selected(token: str, settings: Settings) -> None:
    client = fake_client()
    provider, _ = make_provider(token, settings, client, portfolio_id=202)
    portfolio = provider.get_portfolio()
    client.switch_portfolio.assert_awaited_once_with("202", token)
    assert portfolio.portfolio_id == 202
    assert portfolio.portfolio_name == "Family"
    assert portfolio.performance.current_xirr == 9.0


def test_switch_failure_raises(token: str, settings: Settings) -> None:
    client = fake_client(switch_portfolio={"success": False, "error": "HTTP 403"})
    provider, _ = make_provider(token, settings, client, portfolio_id=202)
    with pytest.raises(KuveraUnavailableError, match="HTTP 403"):
        provider.get_portfolio()
    client.get_holdings.assert_not_awaited()


def test_no_data_raises_unavailable(token: str, settings: Settings) -> None:
    client = fake_client(
        get_account_info=None, get_holdings=[], get_portfolios=[], get_portfolio_performance=[]
    )
    provider, _ = make_provider(token, settings, client)
    with pytest.raises(KuveraUnavailableError, match="expired"):
        provider.get_portfolio()


def test_list_portfolios(token: str, settings: Settings) -> None:
    provider, _ = make_provider(token, settings, fake_client())
    portfolios = provider.list_portfolios()
    assert [p.label for p in portfolios] == ["Self", "Family"]


def test_equity_distribution(token: str, settings: Settings) -> None:
    client = fake_client()
    provider, _ = make_provider(token, settings, client)
    rows = provider.get_equity_distribution("INF879O01027", 10000.0)
    client.get_equity_distribution.assert_awaited_once_with("INF879O01027", 10000.0, token)
    assert rows[0].company_name == "HDFC Bank"
    assert rows[0].proportionate_amount == 800.0


def test_end_to_end_with_real_client_and_mock_transport(token: str, settings: Settings) -> None:
    """Real KuveraClient over httpx.MockTransport -> Portfolio, covering the full mapping."""
    from tests.conftest import HOLDINGS_RESPONSE, PORTFOLIOS_RESPONSE

    routes = {
        "/api/v3/user/info.json": ACCOUNT_RESPONSE,
        "/api/users_service/v5/portfolio.json": PORTFOLIOS_RESPONSE,
        "/api/v3/portfolio/holdings.json": HOLDINGS_RESPONSE,
        "/api/v3/user/portfolio_performance.json": PERFORMANCE_RESPONSE,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/mf/api/v5/fund_schemes/"):
            return httpx.Response(200, json=FUND_SCHEMES_RESPONSE)
        return httpx.Response(200, json=routes[request.url.path])

    def factory(http: httpx.AsyncClient) -> KuveraClient:
        mocked = httpx.AsyncClient(base_url=BASE, transport=httpx.MockTransport(handler))
        return KuveraClient(mocked)

    portfolio = KuveraPortfolioProvider(
        token, settings=settings, client_factory=factory
    ).get_portfolio()
    assert portfolio.total_current_value == pytest.approx(100 * 80 + 50 * 80 + 200 * 30)
    assert portfolio.holdings[0].fund_details.expense_ratio == pytest.approx(0.63)
    assert portfolio.xirr == 15.2
    assert len(portfolio.merged_holdings()) == 2


def test_run_sync_inside_running_loop() -> None:
    import asyncio

    async def inner() -> int:
        return 7

    async def outer() -> int:
        return run_sync(inner())

    assert asyncio.run(outer()) == 7
