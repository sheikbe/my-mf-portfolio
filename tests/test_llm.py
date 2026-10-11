"""LLM wrapper tests with a fake OpenAI client (no network)."""

from __future__ import annotations

import json
from types import SimpleNamespace

from src.config import Settings
from src.llm import DISABLED_MESSAGE, TOOLS, PortfolioAssistant, run_tool
from src.models import FundDetails, Holding, Portfolio, PortfolioPerformance


def portfolio() -> Portfolio:
    details = FundDetails(
        code="A", name="Alpha Flexi Cap", fund_name="Alpha MF", returns={"year_1": 10}
    )
    return Portfolio(
        source="kuvera",
        holdings=[
            Holding(
                code="A",
                folio_number="1",
                category="Equity",
                invested_value=100,
                current_value=150,
                fund_details=details,
            ),
            Holding(
                code="A",
                folio_number="2",
                category="Equity",
                invested_value=100,
                current_value=100,
                fund_details=details,
            ),
            Holding(
                code="B",
                category="Debt",
                invested_value=200,
                current_value=210,
                fund_details=FundDetails(code="B", name="Beta Liquid"),
            ),
        ],
        performance=PortfolioPerformance(current_xirr=12.5, invested=400, current_value=460),
    )


def test_tools_schema_names() -> None:
    names = {t["function"]["name"] for t in TOOLS}
    assert names == {
        "get_portfolio_summary",
        "get_holding_details",
        "get_allocation",
        "get_fund_performance",
    }


def test_run_tool_outputs() -> None:
    p = portfolio()
    summary = run_tool("get_portfolio_summary", {}, p)
    assert summary["total_current_value"] == 460
    assert summary["xirr_pct"] == 12.5
    assert summary["fund_count"] == 2
    details = run_tool("get_holding_details", {"query": "alpha"}, p)
    assert len(details) == 1 and details[0]["folio_count"] == 2
    allocation = run_tool("get_allocation", {"by": "category"}, p)
    assert {a["label"] for a in allocation} == {"Equity", "Debt"}
    perf = run_tool("get_fund_performance", {"query": "B"}, p)
    assert [r["code"] for r in perf] == ["B"]
    assert "error" in run_tool("nope", {}, p)
    json.dumps([summary, details, allocation, perf], default=str)


def test_disabled_without_api_key() -> None:
    assistant = PortfolioAssistant(
        portfolio(), settings=Settings(_env_file=None, openai_api_key=None)
    )
    assert assistant.enabled is False
    assert assistant.ask("hi") == DISABLED_MESSAGE


class FakeCompletions:
    def __init__(self, responses: list) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def _response(content=None, tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_tool_calling_loop_grounds_answer() -> None:
    call = SimpleNamespace(
        id="call_1", function=SimpleNamespace(name="get_portfolio_summary", arguments="{}")
    )
    completions = FakeCompletions([_response(tool_calls=[call]), _response(content="Worth ₹460")])
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    assistant = PortfolioAssistant(portfolio(), settings=Settings(_env_file=None), client=client)

    answer = assistant.ask("What is my portfolio worth?", [{"role": "user", "content": "hello"}])

    assert answer == "Worth ₹460"
    second = completions.calls[1]["messages"]
    assert second[-2]["tool_calls"][0]["function"]["name"] == "get_portfolio_summary"
    tool_msg = second[-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "call_1"
    assert json.loads(tool_msg["content"])["total_current_value"] == 460
    assert completions.calls[0]["tools"] == TOOLS


def test_sdk_error_is_reported_gracefully() -> None:
    class Boom:
        def create(self, **kwargs):
            raise RuntimeError("network down")

    client = SimpleNamespace(chat=SimpleNamespace(completions=Boom()))
    assistant = PortfolioAssistant(portfolio(), settings=Settings(_env_file=None), client=client)
    assert "RuntimeError" in assistant.ask("hi")
