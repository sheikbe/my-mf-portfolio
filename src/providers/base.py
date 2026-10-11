from __future__ import annotations

from abc import ABC, abstractmethod

from src.models import Portfolio


class ProviderError(RuntimeError):
    """Raised when a provider cannot produce a portfolio (bad input, auth failure, etc.)."""


class PortfolioProvider(ABC):
    @abstractmethod
    def get_portfolio(self) -> Portfolio:
        """Load the user's portfolio."""
