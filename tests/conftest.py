from __future__ import annotations

import base64
import json

import pytest

from src.config import Settings

BASE = "https://api.kuvera.in"


def make_jwt(payload: dict | None = None) -> str:
    def enc(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return (
        f"{enc({'alg': 'HS256', 'typ': 'JWT'})}.{enc(payload or {'sub': '42'})}.c2lnbmF0dXJlLXNpZw"
    )


@pytest.fixture
def token() -> str:
    return make_jwt({"sub": "42", "email": "investor@example.com"})


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, openai_api_key=None, kuvera_api_base_url=BASE)


HOLDINGS_RESPONSE = {
    "INF879O01027": [
        {
            "folioNumber": "1111111/11",
            "units": 100.0,
            "direct": True,
            "lock_free_units": 100.0,
            "allottedAmount": 6000.0,
            "isSip": True,
            "kuvera_category": "Equity",
            "sips": [
                {
                    "amount": 5000,
                    "bse_placed_order_date": "2026-09-05",
                    "frequency": "Monthly",
                    "start_date": "2023-01-05",
                    "type": "sip",
                },
                {"amount": 999, "type": "stp"},
            ],
        },
        {
            "folioNumber": "2222222/22",
            "units": 50.0,
            "direct": True,
            "lock_free_units": 25.0,
            "allottedAmount": 3500.0,
            "isSip": False,
            "kuvera_category": "Equity",
        },
    ],
    "INF179K01VY8": [
        {
            "folioNumber": "3333333/33",
            "units": 200.0,
            "direct": False,
            "lock_free_units": 200.0,
            "allottedAmount": 5000.0,
            "isSip": False,
            "kuvera_category": "Debt",
        }
    ],
}

FUND_SCHEMES_RESPONSE = [
    {
        "code": "INF879O01027",
        "name": "Parag Parikh Flexi Cap Fund Direct Growth",
        "fund_name": "PPFAS Mutual Fund",
        "category": "Flexi Cap Fund",
        "aum": 90000.0,
        "expense_ratio": "0.63",
        "nav": {"nav": 80.0, "date": "2026-10-09"},
        "returns": {"year_1": 18.5, "year_3": 21.2, "year_5": 24.0, "date": "2026-10-09"},
        "volatility": 11.2,
        "ignored_field": "x",
    },
    {
        "code": "INF179K01VY8",
        "name": "HDFC Short Term Debt Fund Regular Growth",
        "fund_name": "HDFC Mutual Fund",
        "category": "Short Duration Fund",
        "aum": 15000.0,
        "expense_ratio": 0.75,
        "nav": {"nav": 30.0, "date": "2026-10-09"},
        "returns": {"year_1": 7.5, "year_3": 6.9},
        "volatility": 1.1,
    },
]

PORTFOLIOS_RESPONSE = [
    {
        "id": 101,
        "aof_status": "approved",
        "portfolio_name": "Self",
        "mode_of_investment": "single",
        "portfolio_code": "P101",
        "primary_applicant": {"id": 1, "name": "Investor", "gender": "F"},
        "nominees": [{"name": "Kin", "relationship": "Spouse"}],
    },
    {"id": 202, "aof_status": "approved", "portfolio_name": "Family", "portfolio_code": "P202"},
]

ACCOUNT_RESPONSE = {
    "id": 1,
    "name": "Investor",
    "email": "investor@example.com",
    "primary_portfolio_id": 101,
    "current_portfolio": {"id": 101, "name": "Self", "aof_status": "approved"},
    "primary_portfolio": {"id": 101, "name": "Self", "email": "investor@example.com"},
}

PERFORMANCE_RESPONSE = {
    "status": "success",
    "data": {
        "101": {
            "current_value": 18000.0,
            "current_gain": 3500.0,
            "current_gain_percent": 24.14,
            "one_day_gain": 120.0,
            "one_day_gain_percent": 0.67,
            "invested": 14500.0,
            "current_xirr": 15.2,
            "alltime_xirr": 14.1,
            "alltime_return": 4000.0,
            "alltime_abs_percentage": 27.6,
            "alltime_abs_return": 4000.0,
            "portfolio_type": "individual",
            "mutual_funds": {},
        },
        "202": {"current_value": 1000.0, "invested": 900.0, "current_xirr": 9.0},
    },
}

EQUITY_STATS_RESPONSE = {
    "INF879O01027": {
        "top_holdings": [
            {
                "company_name": "HDFC Bank Ltd",
                "percentage_to_aum": 8.0,
                "ticker": "HDFCBANK",
                "portfolio_date": "2026-09-30",
                "security_asset_class": "Equity",
            },
            {
                "company_name": "TREPS",
                "percentage_to_aum": 5.0,
                "security_asset_class": "Cash",
            },
        ]
    }
}
