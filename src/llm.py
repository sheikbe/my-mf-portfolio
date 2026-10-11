"""OpenAI tool-calling assistant grounded in the loaded ``Portfolio``.

Questions about the user's own portfolio are answered by calling the tools below, which return
structured data derived from the ``Portfolio`` - the model never has to guess holdings. The
model may still add general (non-personal) fund knowledge. Everything no-ops gracefully when
``OPENAI_API_KEY`` is not configured.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from src import analytics
from src.config import Settings, get_settings
from src.models import Portfolio

MAX_TOOL_ROUNDS = 5
DISABLED_MESSAGE = (
    "The assistant is disabled because OPENAI_API_KEY is not set. Add it to your environment, "
    "`.env`, or `.streamlit/secrets.toml` and restart the app."
)

SYSTEM_PROMPT = """You are a helpful mutual fund portfolio analyst for an Indian investor.
Amounts are in INR. Always call the provided tools to answer questions about the user's own
portfolio (values, holdings, allocation, returns, SIPs) and base those answers only on tool
output - never invent holdings or numbers. XIRR and return values are already in percent.
You may add general, educational context about funds or categories, clearly separated from the
user's data. You are not a registered investment adviser; do not give personalised buy/sell
recommendations. Keep answers concise and use tables or bullet points where helpful."""

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_portfolio_summary",
            "description": "Totals for the user's portfolio: invested, current value, gain, "
            "absolute return %, XIRR, Kuvera performance metrics, fund count and SIP summary.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_holding_details",
            "description": "Details for holdings whose scheme name or code matches the query "
            "(case-insensitive substring). Folios of the same fund are merged; per-folio detail "
            "is included. Use an empty query to list every holding.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Fund name or code"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_allocation",
            "description": "Allocation of current value by asset category, AMC, plan type "
            "(direct vs regular) or individual fund.",
            "parameters": {
                "type": "object",
                "properties": {
                    "by": {"type": "string", "enum": ["category", "amc", "plan", "fund"]},
                },
                "required": ["by"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fund_performance",
            "description": "Per-fund gain, absolute return % and the scheme's published "
            "trailing returns (from Kuvera). Optionally filter by fund name/code substring.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "additionalProperties": False,
            },
        },
    },
]


def _matches(query: str, *fields: str | None) -> bool:
    q = query.strip().lower()
    return not q or any(q in (f or "").lower() for f in fields)


def run_tool(name: str, arguments: dict[str, Any], portfolio: Portfolio) -> Any:
    """Execute a tool against the portfolio and return JSON-serialisable data."""
    holdings = portfolio.holdings
    if name == "get_portfolio_summary":
        return {
            "source": portfolio.source,
            "portfolio_name": portfolio.portfolio_name,
            "as_of": portfolio.as_of.isoformat(timespec="seconds"),
            "fund_count": len(portfolio.merged_holdings()),
            "folio_count": len(holdings),
            "total_invested": portfolio.total_invested,
            "total_current_value": portfolio.total_current_value,
            "total_gain": portfolio.total_gain,
            "absolute_return_pct": portfolio.absolute_return_pct,
            "xirr_pct": portfolio.xirr,
            "kuvera_performance": (
                portfolio.performance.model_dump(exclude={"mutual_funds"}, exclude_none=True)
                if portfolio.performance
                else None
            ),
            "sip_summary": analytics.sip_summary(holdings).model_dump(),
        }
    if name == "get_holding_details":
        query = str(arguments.get("query", ""))
        return [
            m.model_dump(mode="json")
            for m in portfolio.merged_holdings()
            if _matches(query, m.scheme_name, m.code, m.amc)
        ]
    if name == "get_allocation":
        by = arguments.get("by", "category")
        fn = {
            "category": analytics.allocation_by_category,
            "amc": analytics.allocation_by_amc,
            "plan": analytics.allocation_by_plan,
            "fund": analytics.allocation_by_fund,
        }.get(by)
        if fn is None:
            return {"error": f"Unknown allocation dimension: {by}"}
        return [s.model_dump() for s in fn(holdings)]
    if name == "get_fund_performance":
        query = str(arguments.get("query") or "")
        return [
            r.model_dump()
            for r in analytics.fund_returns(holdings)
            if _matches(query, r.scheme_name, r.code)
        ]
    return {"error": f"Unknown tool: {name}"}


class PortfolioAssistant:
    def __init__(
        self,
        portfolio: Portfolio,
        *,
        settings: Settings | None = None,
        client: Any | None = None,
    ) -> None:
        self.portfolio = portfolio
        self.settings = settings or get_settings()
        self._client = client
        if self._client is None and self.settings.openai_enabled:
            from openai import OpenAI

            key = self.settings.openai_api_key.get_secret_value()  # type: ignore[union-attr]
            self._client = OpenAI(api_key=key)

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def ask(self, question: str, history: list[dict[str, str]] | None = None) -> str:
        """Answer *question*, letting the model call portfolio tools as needed."""
        if not self.enabled:
            return DISABLED_MESSAGE
        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages += [{"role": m["role"], "content": m["content"]} for m in history or []]
        messages.append({"role": "user", "content": question})

        try:
            for _ in range(MAX_TOOL_ROUNDS):
                response = self._client.chat.completions.create(  # type: ignore[union-attr]
                    model=self.settings.openai_model, messages=messages, tools=TOOLS
                )
                message = response.choices[0].message
                if not message.tool_calls:
                    return message.content or ""
                messages.append(
                    {
                        "role": "assistant",
                        "content": message.content or "",
                        "tool_calls": [
                            {
                                "id": call.id,
                                "type": "function",
                                "function": {
                                    "name": call.function.name,
                                    "arguments": call.function.arguments,
                                },
                            }
                            for call in message.tool_calls
                        ],
                    }
                )
                for call in message.tool_calls:
                    try:
                        args = json.loads(call.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    logger.debug("LLM tool call: {}", call.function.name)
                    result = run_tool(call.function.name, args, self.portfolio)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": json.dumps(result, default=str),
                        }
                    )
        except Exception as exc:  # noqa: BLE001 - surface any SDK/network error to the UI
            logger.warning("OpenAI request failed: {}", type(exc).__name__)
            return f"Sorry, the assistant request failed ({type(exc).__name__})."
        return "Sorry, I couldn't finish answering within the tool-call limit."
