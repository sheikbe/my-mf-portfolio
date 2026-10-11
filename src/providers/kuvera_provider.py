"""Portfolio provider backed by the (unofficial) Kuvera API via ``KuveraClient``."""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any, TypeVar

import httpx
from loguru import logger

from src.config import Settings, get_settings
from src.kuvera_client import KuveraClient
from src.models import (
    EquityExposure,
    Holding,
    Portfolio,
    PortfolioPerformance,
    PortfolioSummary,
)
from src.providers.base import PortfolioProvider, ProviderError

T = TypeVar("T")
ClientFactory = Callable[[httpx.AsyncClient], Any]


class InvalidTokenError(ProviderError):
    """The supplied string is not structurally a JWT."""


class KuveraUnavailableError(ProviderError):
    """Kuvera returned nothing usable: token expired/revoked or the API is unreachable."""


def run_sync(coro: Coroutine[Any, Any, T]) -> T:
    """Run *coro* to completion from sync code (Streamlit), even if a loop is already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _select_performance(
    entries: list[dict[str, Any]], portfolio_id: int | str | None
) -> PortfolioPerformance | None:
    if not entries:
        return None
    if portfolio_id is not None:
        for entry in entries:
            if str(entry.get("portfolio_id")) == str(portfolio_id):
                return PortfolioPerformance.model_validate(entry)
        logger.warning("No performance entry for the selected portfolio")
        return None
    return PortfolioPerformance.model_validate(entries[0])


class KuveraPortfolioProvider(PortfolioProvider):
    """Loads holdings, portfolios and performance for the token's *current* Kuvera portfolio.

    The token is held only on this instance and is never logged.
    """

    def __init__(
        self,
        token: str,
        *,
        portfolio_id: int | str | None = None,
        settings: Settings | None = None,
        client_factory: ClientFactory = KuveraClient,
    ) -> None:
        token = (token or "").strip()
        if not KuveraClient.validate_jwt_format(token):
            raise InvalidTokenError(
                "That doesn't look like a Kuvera token (expected a 3-part JWT). "
                "Copy the value of localStorage.getItem('vue-authenticate.vueauth_token')."
            )
        self._token = token
        self._portfolio_id = portfolio_id
        self._settings = settings or get_settings()
        self._client_factory = client_factory

    def __repr__(self) -> str:
        return f"{type(self).__name__}(portfolio_id={self._portfolio_id!r}, token=[REDACTED])"

    @property
    def email(self) -> str | None:
        return KuveraClient.email_from_token(self._token)

    @asynccontextmanager
    async def _client(self) -> AsyncIterator[Any]:
        async with httpx.AsyncClient(
            base_url=self._settings.kuvera_api_base_url,
            timeout=self._settings.kuvera_api_timeout,
        ) as http:
            yield self._client_factory(http)

    # ------------------------------------------------------------------
    # Async API
    # ------------------------------------------------------------------

    async def alist_portfolios(self) -> list[PortfolioSummary]:
        async with self._client() as client:
            raw = await client.get_portfolios(self._token)
        return [PortfolioSummary.model_validate(p) for p in raw if p.get("portfolio_id")]

    async def aget_portfolio(self) -> Portfolio:
        async with self._client() as client:
            account = await client.get_account_info(self._token)
            current_id = ((account or {}).get("current_portfolio") or {}).get("id")
            target_id = self._portfolio_id if self._portfolio_id is not None else current_id

            if (
                target_id is not None
                and current_id is not None
                and str(target_id) != str(current_id)
            ):
                result = await client.switch_portfolio(str(target_id), self._token)
                if not result.get("success"):
                    raise KuveraUnavailableError(
                        f"Could not switch Kuvera portfolio ({result.get('error', 'unknown')})."
                    )
                logger.info("Switched Kuvera portfolio")

            raw_holdings, raw_portfolios, raw_performance = await asyncio.gather(
                client.get_holdings(self._token),
                client.get_portfolios(self._token),
                client.get_portfolio_performance(self._token),
            )

        if account is None and not raw_holdings and not raw_performance:
            raise KuveraUnavailableError(
                "Kuvera returned no data. Your token may have expired - log in to Kuvera again "
                "and paste a fresh token."
            )

        name = next(
            (
                p.get("portfolio_name")
                for p in raw_portfolios
                if target_id is not None and str(p.get("portfolio_id")) == str(target_id)
            ),
            None,
        )
        portfolio = Portfolio(
            source="kuvera",
            holdings=[Holding.model_validate(h) for h in raw_holdings],
            performance=_select_performance(raw_performance, target_id),
            portfolio_id=target_id,
            portfolio_name=name,
        )
        logger.info(
            "Loaded Kuvera portfolio: {} folios across {} funds",
            len(portfolio.holdings),
            len(portfolio.merged_holdings()),
        )
        return portfolio

    async def aget_equity_distribution(
        self, fund_code: str, current_value: float
    ) -> list[EquityExposure]:
        async with self._client() as client:
            raw = await client.get_equity_distribution(fund_code, current_value, self._token)
        return [EquityExposure.model_validate(r) for r in raw]

    # ------------------------------------------------------------------
    # Sync wrappers for Streamlit
    # ------------------------------------------------------------------

    def get_portfolio(self) -> Portfolio:
        return run_sync(self.aget_portfolio())

    def list_portfolios(self) -> list[PortfolioSummary]:
        return run_sync(self.alist_portfolios())

    def get_equity_distribution(self, fund_code: str, current_value: float) -> list[EquityExposure]:
        return run_sync(self.aget_equity_distribution(fund_code, current_value))
