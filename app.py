"""Streamlit entry point: ``streamlit run app.py``."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from loguru import logger

from src import analytics
from src.config import get_settings
from src.llm import PortfolioAssistant
from src.logging_setup import configure_logging
from src.models import Portfolio
from src.providers import CSVPortfolioProvider, KuveraPortfolioProvider, ProviderError

SAMPLE_CSV = Path(__file__).parent / "samples" / "sample_holdings.csv"
TOKEN_JS = "localStorage.getItem('vue-authenticate.vueauth_token')"
SESSION_KEYS = ("kuvera_token", "portfolio", "portfolios", "selected_portfolio_id", "chat_history")

st.set_page_config(page_title="Kuvera Portfolio Analytics", page_icon="📈", layout="wide")
settings = get_settings()


@st.cache_resource
def _init_logging(level: str) -> bool:
    configure_logging(level)
    return True


_init_logging(settings.log_level)


def inr(value: float | None) -> str:
    return "-" if value is None else f"₹{value:,.0f}"


def pct(value: float | None) -> str:
    return "-" if value is None else f"{value:,.2f}%"


def clear_session() -> None:
    for key in SESSION_KEYS + ("token_input",):
        st.session_state.pop(key, None)


def set_portfolio(portfolio: Portfolio) -> None:
    st.session_state["portfolio"] = portfolio
    st.session_state["chat_history"] = []


def load_from_kuvera(token: str, portfolio_id: int | str | None = None) -> None:
    try:
        provider = KuveraPortfolioProvider(token, portfolio_id=portfolio_id, settings=settings)
        with st.spinner("Fetching your portfolio from Kuvera..."):
            if "portfolios" not in st.session_state:
                st.session_state["portfolios"] = provider.list_portfolios()
            portfolio = provider.get_portfolio()
    except ProviderError as exc:
        st.sidebar.error(str(exc))
        return
    st.session_state["kuvera_token"] = token
    st.session_state["selected_portfolio_id"] = portfolio.portfolio_id
    set_portfolio(portfolio)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Kuvera")
    token_loaded = "kuvera_token" in st.session_state
    if not token_loaded:
        with st.expander("How do I get my token?", expanded=False):
            st.markdown(
                "1. Log in to [kuvera.in](https://kuvera.in) in your browser.\n"
                "2. Open DevTools (F12 / Cmd+Opt+I) → **Console**.\n"
                "3. Run the line below and copy the value (without quotes)."
            )
            st.code(TOKEN_JS, language="javascript")
            st.caption(
                "The token is kept only in this browser session's memory and is sent only to "
                f"`{settings.kuvera_api_base_url}`. It expires when you log out of Kuvera."
            )
        with st.form("kuvera_form", clear_on_submit=True):
            token_input = st.text_input(
                "Kuvera JWT token", type="password", key="token_input", placeholder="eyJ..."
            )
            if st.form_submit_button("Load from Kuvera", type="primary"):
                load_from_kuvera(token_input.strip())
                if "kuvera_token" in st.session_state:
                    st.rerun()
    else:
        token = st.session_state["kuvera_token"]
        email = KuveraPortfolioProvider(token, settings=settings).email
        st.success(f"Connected{f' as {email}' if email else ''}")
        portfolios = st.session_state.get("portfolios") or []
        if len(portfolios) > 1:
            ids = [p.portfolio_id for p in portfolios]
            labels = {p.portfolio_id: p.label for p in portfolios}
            current = st.session_state.get("selected_portfolio_id")
            choice = st.selectbox(
                "Portfolio",
                ids,
                index=ids.index(current) if current in ids else 0,
                format_func=lambda pid: labels[pid],
                help="Switching changes the active portfolio on your Kuvera account.",
            )
            if str(choice) != str(current):
                load_from_kuvera(token, portfolio_id=choice)
                st.rerun()
        col_a, col_b = st.columns(2)
        if col_a.button("Refresh"):
            load_from_kuvera(token, st.session_state.get("selected_portfolio_id"))
            st.rerun()
        if col_b.button("Forget token"):
            clear_session()
            st.rerun()

    st.divider()
    st.header("Or upload a CSV")
    upload = st.file_uploader("Holdings CSV", type=["csv"], help="See README for the schema.")
    if upload is not None and st.button("Load CSV"):
        try:
            set_portfolio(CSVPortfolioProvider(upload.getvalue(), name=upload.name).get_portfolio())
            st.session_state.pop("kuvera_token", None)
            st.session_state.pop("portfolios", None)
            st.rerun()
        except ProviderError as exc:
            st.error(str(exc))
    if SAMPLE_CSV.exists():
        st.download_button(
            "Download sample CSV", SAMPLE_CSV.read_bytes(), "sample_holdings.csv", "text/csv"
        )
        if st.button("Try with sample data"):
            set_portfolio(CSVPortfolioProvider(SAMPLE_CSV, name="Sample data").get_portfolio())
            st.rerun()

# ---------------------------------------------------------------------------
# Main area
# ---------------------------------------------------------------------------

st.title("📈 Kuvera Portfolio Analytics")
portfolio: Portfolio | None = st.session_state.get("portfolio")

if portfolio is None:
    st.info(
        "Paste your Kuvera token in the sidebar to load your mutual fund portfolio, or upload a "
        "holdings CSV. Nothing is stored after you close this tab."
    )
    st.caption(
        "Unofficial tool. Kuvera integration uses undocumented api.kuvera.in endpoints and may "
        "break without notice. Not investment advice."
    )
    st.stop()

source_label = "Kuvera" if portfolio.source == "kuvera" else "CSV"
st.caption(
    f"Source: {source_label}"
    + (f" · {portfolio.portfolio_name}" if portfolio.portfolio_name else "")
    + f" · loaded {portfolio.as_of:%d %b %Y %H:%M}"
)

holdings = portfolio.holdings
overview, holdings_tab, performance_tab, ask_tab = st.tabs(
    ["Overview", "Holdings", "Performance", "Ask"]
)

with overview:
    perf = portfolio.performance
    c1, c2, c3, c4 = st.columns(4)
    current_value = perf.current_value if perf and perf.current_value else None
    invested = perf.invested if perf and perf.invested else None
    current_value = current_value or portfolio.total_current_value
    invested = invested or portfolio.total_invested
    gain_pct = (
        perf.current_gain_percent
        if perf and perf.current_gain_percent is not None
        else analytics.absolute_return_pct(invested, current_value)
    )
    c1.metric(
        "Current value",
        inr(current_value),
        delta=f"{inr(perf.one_day_gain)} today" if perf and perf.one_day_gain else None,
    )
    c2.metric("Invested", inr(invested))
    c3.metric("Gain", inr(current_value - invested), delta=pct(gain_pct))
    c4.metric("XIRR", pct(portfolio.xirr), help="Kuvera's current XIRR, or computed from CSV dates")

    if not holdings:
        st.warning("No holdings found in this portfolio.")
    else:
        left, right = st.columns(2)
        cat = pd.DataFrame([s.model_dump() for s in analytics.allocation_by_category(holdings)])
        left.plotly_chart(
            px.pie(cat, names="label", values="value", hole=0.45, title="By category"),
            use_container_width=True,
        )
        plan = pd.DataFrame([s.model_dump() for s in analytics.allocation_by_plan(holdings)])
        right.plotly_chart(
            px.pie(plan, names="label", values="value", hole=0.45, title="Direct vs regular"),
            use_container_width=True,
        )
        amc = pd.DataFrame([s.model_dump() for s in analytics.allocation_by_amc(holdings)])
        st.plotly_chart(
            px.bar(amc, x="value", y="label", orientation="h", text="percent", title="By AMC")
            .update_traces(texttemplate="%{text:.1f}%")
            .update_layout(yaxis={"categoryorder": "total ascending", "title": None}),
            use_container_width=True,
        )
        sips = analytics.sip_summary(holdings)
        s1, s2, s3 = st.columns(3)
        s1.metric("Active SIPs", sips.active_sip_count)
        s2.metric("Funds with SIP", sips.funds_with_sip)
        s3.metric("Monthly SIP (approx.)", inr(sips.monthly_sip_amount))

with holdings_tab:
    show_folios = st.toggle("Show per-folio detail", value=False)
    df = (
        analytics.folios_dataframe(portfolio)
        if show_folios
        else analytics.holdings_dataframe(portfolio)
    )
    if df.empty:
        st.info("No holdings.")
    else:
        df = df.sort_values("Current value", ascending=False)
        st.dataframe(
            df,
            hide_index=True,
            use_container_width=True,
            column_config={
                "Invested": st.column_config.NumberColumn(format="₹%.0f"),
                "Current value": st.column_config.NumberColumn(format="₹%.0f"),
                "Gain": st.column_config.NumberColumn(format="₹%.0f"),
                "Return %": st.column_config.NumberColumn(format="%.2f%%"),
            },
        )
        st.download_button(
            "Download as CSV", df.to_csv(index=False).encode(), "holdings.csv", "text/csv"
        )

with performance_tab:
    perf = portfolio.performance
    if perf:
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Current XIRR", pct(perf.current_xirr))
        p2.metric("All-time XIRR", pct(perf.alltime_xirr))
        p3.metric(
            "All-time return", inr(perf.alltime_return), delta=pct(perf.alltime_abs_percentage)
        )
        p4.metric("1-day gain", inr(perf.one_day_gain), delta=pct(perf.one_day_gain_percent))
    else:
        p1, p2 = st.columns(2)
        p1.metric("XIRR", pct(portfolio.xirr))
        p2.metric("Absolute return", pct(portfolio.absolute_return_pct))
        if portfolio.source == "csv" and portfolio.xirr is None:
            st.caption("Add an `investment_date` column to your CSV to compute XIRR.")

    returns = analytics.fund_returns(holdings)
    if returns:
        rows = []
        for r in returns:
            row = {
                "Fund": r.scheme_name,
                "Category": r.category,
                "Invested": round(r.invested_value, 2),
                "Current value": round(r.current_value, 2),
                "Gain": round(r.gain, 2),
                "Return %": None
                if r.absolute_return_pct is None
                else round(r.absolute_return_pct, 2),
                "Expense ratio": r.expense_ratio,
            }
            row.update({f"Scheme {k}": v for k, v in r.returns.items()})
            rows.append(row)
        returns_df = pd.DataFrame(rows)
        st.subheader("Per-fund returns")
        st.plotly_chart(
            px.bar(
                returns_df, x="Return %", y="Fund", color="Category", orientation="h"
            ).update_layout(yaxis={"categoryorder": "total ascending", "title": None}),
            use_container_width=True,
        )
        st.dataframe(returns_df, hide_index=True, use_container_width=True)
        st.caption("'Scheme …' columns are the fund's published trailing returns from Kuvera.")

    if portfolio.source == "kuvera" and "kuvera_token" in st.session_state:
        st.subheader("Equity look-through")
        equity = [m for m in portfolio.merged_holdings() if m.category == "Equity"]
        if not equity:
            st.caption("No equity funds in this portfolio.")
        else:
            choice = st.selectbox(
                "Fund", equity, format_func=lambda m: m.scheme_name, key="equity_fund"
            )
            if st.button("Show top equity holdings"):
                provider = KuveraPortfolioProvider(
                    st.session_state["kuvera_token"], settings=settings
                )
                with st.spinner("Fetching fund holdings..."):
                    exposure = provider.get_equity_distribution(choice.code, choice.current_value)
                if not exposure:
                    st.info("Kuvera returned no equity holdings for this fund.")
                else:
                    edf = pd.DataFrame([e.model_dump() for e in exposure])
                    edf = edf.sort_values("proportionate_amount", ascending=False)
                    st.plotly_chart(
                        px.bar(
                            edf.head(15),
                            x="proportionate_amount",
                            y="company_name",
                            orientation="h",
                            labels={
                                "proportionate_amount": "Your exposure (₹)",
                                "company_name": "",
                            },
                        ).update_layout(yaxis={"categoryorder": "total ascending"}),
                        use_container_width=True,
                    )
                    st.dataframe(edf, hide_index=True, use_container_width=True)

with ask_tab:
    assistant = PortfolioAssistant(portfolio, settings=settings)
    if not assistant.enabled:
        st.info("Set `OPENAI_API_KEY` (env, `.env`, or `st.secrets`) to enable the assistant.")
    else:
        history: list[dict[str, str]] = st.session_state.setdefault("chat_history", [])
        for message in history:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])
        question = st.chat_input("Ask about your portfolio, e.g. 'What is my equity allocation?'")
        if question:
            with st.chat_message("user"):
                st.markdown(question)
            with st.chat_message("assistant"), st.spinner("Thinking..."):
                answer = assistant.ask(question, history)
                st.markdown(answer)
            history += [
                {"role": "user", "content": question},
                {"role": "assistant", "content": answer},
            ]
            logger.debug("Answered chat question ({} turns)", len(history) // 2)
