"""KuveraClient tests - every endpoint stubbed with httpx.MockTransport (no network)."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import urllib.parse

import httpx
import pytest
from loguru import logger

from src.kuvera_client import KuveraClient, TokenRedactionFilter
from src.logging_setup import loguru_redaction_filter
from tests.conftest import (
    ACCOUNT_RESPONSE,
    BASE,
    EQUITY_STATS_RESPONSE,
    FUND_SCHEMES_RESPONSE,
    HOLDINGS_RESPONSE,
    PERFORMANCE_RESPONSE,
    PORTFOLIOS_RESPONSE,
    make_jwt,
)

FUND_CODES = "|".join(HOLDINGS_RESPONSE)


def router(requests: list[httpx.Request], overrides: dict[str, httpx.Response] | None = None):
    routes = {
        "/api/v3/user/info.json": ACCOUNT_RESPONSE,
        "/api/users_service/v5/portfolio.json": PORTFOLIOS_RESPONSE,
        "/api/v3/portfolio/holdings.json": HOLDINGS_RESPONSE,
        "/api/v3/user/portfolio_performance.json": PERFORMANCE_RESPONSE,
        "/mf/api/v5/fund_investment_stats/INF879O01027.json": EQUITY_STATS_RESPONSE,
        "/api/v3/portfolio/switch/202.json": {"status": "ok"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if overrides and path in overrides:
            return overrides[path]
        if path.startswith("/mf/api/v5/fund_schemes/"):
            return httpx.Response(200, json=FUND_SCHEMES_RESPONSE)
        if path in routes:
            return httpx.Response(200, json=routes[path])
        return httpx.Response(404)

    return handler


def run(coro_fn, overrides: dict[str, httpx.Response] | None = None):
    requests: list[httpx.Request] = []

    async def go():
        transport = httpx.MockTransport(router(requests, overrides))
        async with httpx.AsyncClient(base_url=BASE, transport=transport) as http:
            return await coro_fn(KuveraClient(http))

    return asyncio.run(go()), requests


# ---------------------------------------------------------------------------
# Static helpers
# ---------------------------------------------------------------------------


class TestJwtHelpers:
    def test_validate_accepts_three_segments(self, token: str) -> None:
        assert KuveraClient.validate_jwt_format(token) is True

    @pytest.mark.parametrize(
        "bad",
        ["", "   ", "aaa.bbb", "aaa.bbb.ccc.ddd", " aaa.bbb.ccc", "aaa.bbb.ccc\r\nX: y", "a b.c.d"],
    )
    def test_validate_rejects_malformed(self, bad: str) -> None:
        assert KuveraClient.validate_jwt_format(bad) is False

    def test_decode_payload(self) -> None:
        assert KuveraClient.decode_jwt_payload(make_jwt({"sub": "7"})) == {"sub": "7"}

    def test_decode_payload_malformed_returns_none(self) -> None:
        assert KuveraClient.decode_jwt_payload("not-a-jwt") is None

    def test_email_from_token(self) -> None:
        assert KuveraClient.email_from_token(make_jwt({"email": " a@b.c "})) == "a@b.c"
        assert KuveraClient.email_from_token(make_jwt({"sub": "1"})) is None
        assert KuveraClient.email_from_token("garbage") is None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def test_headers_sent(token: str) -> None:
    _, requests = run(lambda c: c.get_account_info(token))
    headers = requests[0].headers
    assert headers["Authorization"] == f"Bearer {token}"
    assert headers["User-Agent"] == "cp-app/1.0"
    assert headers["Accept"] == "application/json"
    assert headers["Content-Type"] == "application/json"


def test_get_account_info(token: str) -> None:
    info, requests = run(lambda c: c.get_account_info(token))
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/api/v3/user/info.json"
    assert info["email"] == "investor@example.com"
    assert info["current_portfolio"]["id"] == 101
    assert info["primary_portfolio"]["email"] == "investor@example.com"


def test_get_account_info_http_error_returns_none(token: str) -> None:
    info, _ = run(
        lambda c: c.get_account_info(token),
        {"/api/v3/user/info.json": httpx.Response(401, json={"error": "expired"})},
    )
    assert info is None


def test_get_portfolios(token: str) -> None:
    portfolios, requests = run(lambda c: c.get_portfolios(token))
    assert requests[0].url.path == "/api/users_service/v5/portfolio.json"
    assert requests[0].url.params["v"] == "1.238.2"
    assert [p["portfolio_id"] for p in portfolios] == [101, 202]
    assert portfolios[0]["portfolio_name"] == "Self"
    assert portfolios[0]["account_status"] == "approved"
    assert portfolios[0]["applicants"][0]["name"] == "Investor"
    assert portfolios[0]["nominees"][0]["relationship"] == "Spouse"
    assert portfolios[1]["applicants"] == []


def test_get_fund_details_url_encodes_pipe_joined_codes(token: str) -> None:
    details, requests = run(lambda c: c.get_fund_details(list(HOLDINGS_RESPONSE), token))
    expected = "/mf/api/v5/fund_schemes/" + urllib.parse.quote(FUND_CODES, safe="") + ".json"
    assert requests[0].url.raw_path.decode() == expected
    ppfas = details["INF879O01027"]
    assert ppfas["nav"] == 80.0
    assert ppfas["nav_date"] == "2026-10-09"
    assert ppfas["fund_name"] == "PPFAS Mutual Fund"
    assert ppfas["returns"]["year_3"] == 21.2
    assert "ignored_field" not in ppfas


def test_get_holdings_two_step_flow(token: str) -> None:
    holdings, requests = run(lambda c: c.get_holdings(token))
    assert requests[0].url.path == "/api/v3/portfolio/holdings.json"
    assert requests[0].url.params["v"] == "1.238.2"
    assert requests[1].url.path.startswith("/mf/api/v5/fund_schemes/")
    assert len(requests) == 2

    assert len(holdings) == 3
    first, second, debt = holdings
    assert first["code"] == second["code"] == "INF879O01027"
    assert first["folio_number"] == "1111111/11"
    assert first["current_value"] == pytest.approx(100.0 * 80.0)
    assert second["current_value"] == pytest.approx(50.0 * 80.0)
    assert debt["current_value"] == pytest.approx(200.0 * 30.0)
    assert first["invested_value"] == 6000.0
    assert first["direct_plan"] is True and debt["direct_plan"] is False
    assert first["category"] == "Equity" and debt["category"] == "Debt"
    assert first["fund_details"]["name"].startswith("Parag Parikh")
    # Only type == "sip" entries survive.
    assert first["sips"] == [
        {
            "amount": 5000,
            "order_date": "2026-09-05",
            "frequency": "Monthly",
            "start_date": "2023-01-05",
        }
    ]
    assert "sips" not in second


def test_get_holdings_empty_skips_fund_details(token: str) -> None:
    holdings, requests = run(
        lambda c: c.get_holdings(token),
        {"/api/v3/portfolio/holdings.json": httpx.Response(200, json={})},
    )
    assert holdings == []
    assert len(requests) == 1


def test_get_holdings_fund_details_failure_gives_zero_value(token: str) -> None:
    holdings, requests = run(
        lambda c: c.get_holdings(token),
        {f"/mf/api/v5/fund_schemes/{FUND_CODES}.json": httpx.Response(500)},
    )
    assert len(requests) == 2
    assert len(holdings) == 3
    assert all(h["current_value"] == 0.0 for h in holdings)
    assert all(h["fund_details"] == {} for h in holdings)


def test_get_equity_distribution(token: str) -> None:
    rows, requests = run(lambda c: c.get_equity_distribution("INF879O01027", 10000.0, token))
    assert requests[0].url.path == "/mf/api/v5/fund_investment_stats/INF879O01027.json"
    assert len(rows) == 1  # Cash row filtered out
    assert rows[0]["company_name"] == "HDFC Bank Ltd"
    assert rows[0]["proportionate_amount"] == pytest.approx(800.0)


def test_get_portfolio_performance(token: str) -> None:
    perf, requests = run(lambda c: c.get_portfolio_performance(token))
    assert requests[0].url.path == "/api/v3/user/portfolio_performance.json"
    assert requests[0].url.params["v"] == "1.239.2"
    by_id = {p["portfolio_id"]: p for p in perf}
    assert set(by_id) == {101, 202}
    assert by_id[101]["current_xirr"] == 15.2
    assert by_id[101]["alltime_xirr"] == 14.1
    assert by_id[101]["invested"] == 14500.0


def test_get_portfolio_performance_non_success(token: str) -> None:
    perf, _ = run(
        lambda c: c.get_portfolio_performance(token),
        {
            "/api/v3/user/portfolio_performance.json": httpx.Response(
                200, json={"status": "error", "data": None}
            )
        },
    )
    assert perf == []


def test_switch_portfolio(token: str) -> None:
    result, requests = run(lambda c: c.switch_portfolio("202", token))
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/api/v3/portfolio/switch/202.json"
    assert requests[0].url.params["v"] == "1.239.1"
    assert result == {"success": True, "data": {"status": "ok"}}


def test_switch_portfolio_error(token: str) -> None:
    result, _ = run(
        lambda c: c.switch_portfolio("202", token),
        {"/api/v3/portfolio/switch/202.json": httpx.Response(403)},
    )
    assert result == {"success": False, "error": "HTTP 403"}


def test_timeout_returns_empty(token: str) -> None:
    async def go():
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        async with httpx.AsyncClient(base_url=BASE, transport=httpx.MockTransport(handler)) as h:
            return await KuveraClient(h).get_portfolios(token)

    assert asyncio.run(go()) == []


# ---------------------------------------------------------------------------
# Token redaction
# ---------------------------------------------------------------------------


class TestTokenRedaction:
    def _capture(self) -> tuple[logging.Logger, list[logging.LogRecord], logging.Handler]:
        records: list[logging.LogRecord] = []

        class Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        handler = Capture()
        handler.addFilter(TokenRedactionFilter())
        log = logging.getLogger("test_redaction")
        log.addHandler(handler)
        log.setLevel(logging.DEBUG)
        log.propagate = False
        return log, records, handler

    def test_scrubs_jwt_in_message_and_args(self, token: str) -> None:
        log, records, handler = self._capture()
        try:
            log.warning(f"inline {token}")
            log.warning("args %s", token)
            log.warning("dict %(t)s", {"t": token})
        finally:
            log.removeHandler(handler)
        assert len(records) == 3
        for record in records:
            message = record.getMessage()
            assert token not in message
            assert "[REDACTED]" in message

    def test_leaves_normal_messages_alone(self) -> None:
        log, records, handler = self._capture()
        try:
            log.info("Loaded %d holdings from %s", 3, "api.kuvera.in")
        finally:
            log.removeHandler(handler)
        assert records[0].getMessage() == "Loaded 3 holdings from api.kuvera.in"

    def test_loguru_sink_filter_scrubs_jwt(self, token: str) -> None:
        buffer = io.StringIO()
        sink_id = logger.add(buffer, filter=loguru_redaction_filter, format="{message}")
        try:
            logger.warning("token={}", token)
        finally:
            logger.remove(sink_id)
        assert token not in buffer.getvalue()
        assert "[REDACTED]" in buffer.getvalue()

    def test_client_never_logs_token_on_error(self, token: str) -> None:
        buffer = io.StringIO()
        sink_id = logger.add(buffer, format="{message}")
        try:
            run(
                lambda c: c.get_portfolios(token),
                {"/api/users_service/v5/portfolio.json": httpx.Response(500, text=token)},
            )
        finally:
            logger.remove(sink_id)
        output = buffer.getvalue()
        assert "HTTP 500" in output
        assert token not in output


def test_fixtures_are_json_serialisable() -> None:
    json.dumps([HOLDINGS_RESPONSE, FUND_SCHEMES_RESPONSE, PERFORMANCE_RESPONSE])
