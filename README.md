# kuvera-portfolio-analytics

A Streamlit dashboard for analysing your mutual fund portfolio from [Kuvera](https://kuvera.in):
current value, invested amount, gains, XIRR, allocation (category / AMC / direct vs regular),
per-fund returns, SIP summary, equity look-through, and an optional OpenAI assistant that answers
questions about *your* holdings using tool calls grounded in the loaded data.

> **Disclaimer.** This is an unofficial, personal project and is not affiliated with or endorsed
> by Kuvera. The Kuvera integration calls **undocumented, unofficial `api.kuvera.in` endpoints**
> ported from [`sheikbe/kuvera-mcp`](https://github.com/sheikbe/kuvera-mcp). They can change or
> stop working without notice. Nothing here is investment advice.

| Layer | Tool | File |
|---|---|---|
| UI | Streamlit + Plotly | `app.py` |
| Kuvera API client (async, unofficial endpoints) | httpx | `src/kuvera_client.py` |
| Data providers (Kuvera / CSV fallback) | Pydantic | `src/providers/` |
| Domain models | Pydantic | `src/models.py` |
| Analytics (pure functions, XIRR) | pandas, pyxirr | `src/analytics.py` |
| Ask-your-portfolio assistant | OpenAI SDK (tool calling) | `src/llm.py` |
| Config | pydantic-settings, `.env`, `st.secrets` | `src/config.py` |
| Logging (with JWT redaction) | loguru | `src/logging_setup.py` |
| Tests | pytest, `httpx.MockTransport` | `tests/` |
| CI | GitHub Actions (ruff, pytest, Docker smoke test) | `.github/workflows/ci.yml` |
| Packaging | Docker | `Dockerfile` |

## Setup

Requires Python 3.11+.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # optional: add OPENAI_API_KEY to enable the "Ask" tab
```

### Configuration

Settings come from (first wins) environment variables → `.env` → Streamlit secrets
(`.streamlit/secrets.toml`, see `.streamlit/secrets.toml.example`) → defaults.

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | _(unset)_ | Enables the "Ask" tab. The app works fully without it. |
| `OPENAI_MODEL` | `gpt-4o-mini` | Chat model used by the assistant. |
| `KUVERA_API_BASE_URL` | `https://api.kuvera.in` | Kuvera API base URL. |
| `KUVERA_API_TIMEOUT` | `15.0` | Per-request timeout (seconds). |
| `LOG_LEVEL` | `INFO` | loguru level. |

The Kuvera token is **not** configuration. You paste it into the UI at runtime and it is kept
only in `st.session_state` for that browser session, never written to disk.

## Run locally

```bash
streamlit run app.py
```

Open http://localhost:8501, then either paste your Kuvera token in the sidebar, upload a CSV,
or click **Try with sample data**.

### Docker

```bash
docker build -t kuvera-portfolio-analytics .
docker run --rm -p 8501:8501 --env-file .env kuvera-portfolio-analytics
```

## Getting your Kuvera token

Kuvera has no public API or OAuth. Instead, the app uses the same **JWT session token** the
Kuvera web app uses (this is the auth model from `sheikbe/kuvera-mcp`):

1. Log in to https://kuvera.in in your browser.
2. Open DevTools (F12, or Cmd+Opt+I on macOS) and go to the **Console** tab.
3. Run:

   ```js
   localStorage.getItem('vue-authenticate.vueauth_token')
   ```

4. Copy the printed value **without the surrounding quotes** and paste it into the
   **Kuvera JWT token** field in the sidebar.

Notes:

- Every API call sends it as `Authorization: Bearer <token>` along with
  `User-Agent: cp-app/1.0`, `Accept: application/json`, `Content-Type: application/json`.
- The app only checks that the token is shaped like a JWT (three base64url segments) and decodes
  its claims to show your email. It does **not** verify the signature; Kuvera does that.
- The token expires when you log out of Kuvera or after Kuvera's session timeout. If loading
  fails, fetch a fresh token.
- Treat the token like a password: it grants full access to your Kuvera account. It is never
  logged. A `TokenRedactionFilter` (stdlib logging) and a loguru sink filter replace anything
  JWT-shaped with `[REDACTED]`.
- If you have several Kuvera portfolios (e.g. family accounts), a selector appears. Selecting one
  calls Kuvera's `switch_portfolio` endpoint, which **changes the active portfolio on your Kuvera
  account**, just as switching in the Kuvera app does.

### Kuvera endpoints used

| Method | Endpoint | Used for |
|---|---|---|
| `get_account_info` | `GET /api/v3/user/info.json` | Current portfolio id |
| `get_portfolios` | `GET /api/users_service/v5/portfolio.json?v=1.238.2` | Portfolio selector |
| `get_holdings` | `GET /api/v3/portfolio/holdings.json?v=1.238.2`, then `get_fund_details` | Holdings; `current_value = units × nav` |
| `get_fund_details` | `GET /mf/api/v5/fund_schemes/{codes}.json` | NAV, category, expense ratio, returns |
| `get_portfolio_performance` | `GET /api/v3/user/portfolio_performance.json?v=1.239.2` | XIRR, gains |
| `get_equity_distribution` | `GET /mf/api/v5/fund_investment_stats/{code}.json` | Equity look-through |
| `switch_portfolio` | `POST /api/v3/portfolio/switch/{id}.json?v=1.239.1` | Switching portfolios |

For Kuvera data, the XIRR shown is Kuvera's own precomputed `current_xirr` (all-time XIRR is on
the Performance tab), not recomputed locally.

## What you get

- **Overview**: current value, invested, gain, XIRR, one-day change, allocation by category,
  AMC and direct vs regular plans, and a SIP summary.
- **Holdings**: sortable table with folios of the same fund merged into one row. Toggle
  *Show per-folio detail* to see each folio. Downloadable as CSV.
- **Performance**: Kuvera XIRR and gains, per-fund absolute returns, each scheme's published
  trailing returns (`fund_details.returns`), and on-demand equity look-through (top stocks in a
  fund and your proportionate ₹ exposure).
- **Ask**: chat backed by OpenAI tool calling. The tools `get_portfolio_summary`,
  `get_holding_details`, `get_allocation`, and `get_fund_performance` return structured data from
  the loaded portfolio, so answers about your holdings come from real data. The model may add
  general fund knowledge. The tab is disabled if `OPENAI_API_KEY` is unset. Tool output (your
  holdings) is sent to OpenAI when you use it.

## CSV fallback (no token needed)

Upload a CSV in the sidebar. Header names are case-insensitive and spaces become underscores,
so `Scheme Name` works the same as `scheme_name`. Each row is one folio or one purchase lot. Rows
with the same `scheme_code` (or `scheme_name` if there's no code) are merged for display.

| Column | Required | Description |
|---|---|---|
| `scheme_name` | yes | Fund/scheme name |
| `units` | yes | Units held |
| `invested_value` | yes | Amount invested (₹). `₹` and `,` are allowed |
| `scheme_code` | no | Scheme code/ISIN; used to merge rows (defaults to `scheme_name`) |
| `amc` | no | AMC name (for the AMC allocation) |
| `category` | no | `Equity`, `Debt`, `Hybrid`, anything else is `Other` |
| `folio_number` | no | Folio number |
| `nav` | no | Current NAV; `current_value = units × nav` if `current_value` is blank |
| `current_value` | no | Current value (₹); takes priority over `units × nav` |
| `plan` | no | `Direct` or `Regular` (default Regular) |
| `investment_date` | no | Purchase date (`YYYY-MM-DD`). Needed for XIRR |
| `sip_amount` | no | SIP instalment amount; marks the row as an active SIP |
| `sip_frequency` | no | `Monthly` (default), `Weekly`, `Daily`, `Quarterly`, ... |

If neither `nav` nor `current_value` is given, the current value falls back to `invested_value`.

**XIRR for CSV data:** each row with an `investment_date` becomes a dated outflow of
`-invested_value`. Today's total current value is the final inflow, and
[`pyxirr`](https://github.com/Anexen/pyxirr) solves for XIRR. For an accurate XIRR, add one row per
purchase lot/SIP instalment. With no dates, XIRR shows as "-".

See [`samples/sample_holdings.csv`](samples/sample_holdings.csv) (also downloadable from the
sidebar):

```csv
scheme_code,scheme_name,amc,category,folio_number,units,nav,current_value,invested_value,plan,investment_date,sip_amount,sip_frequency
PPFCF-GR,Parag Parikh Flexi Cap Fund Direct Growth,PPFAS Mutual Fund,Equity,1111111/11,250.000,80.00,,15000,Direct,2023-01-10,5000,Monthly
```

## Development

```bash
ruff check . && ruff format --check .
pytest -v
```

Every network call is mocked in the tests. Nothing contacts Kuvera or OpenAI:

- `tests/test_kuvera_client.py`: each endpoint stubbed with `httpx.MockTransport`. Covers the
  two-step holdings flow, `current_value = units × nav`, SIP filtering, URL encoding, error
  handling, and JWT redaction in logs.
- `tests/test_kuvera_provider.py`: a mocked `KuveraClient` mapped to `Portfolio`, covering
  multi-folio merge, portfolio switching, and expired-token handling.
- `tests/test_analytics.py`: allocations, absolute return, SIP summary, per-fund returns, XIRR
  on the CSV fixture.
- `tests/test_csv_provider.py`: CSV parsing and validation.
- `tests/test_llm.py`: tool outputs and the tool-calling loop with a fake OpenAI client.

### Project layout

```
app.py                     Streamlit entry point
src/config.py              Settings (env / .env / st.secrets)
src/kuvera_client.py       Async Kuvera client (ported from sheikbe/kuvera-mcp)
src/models.py              Pydantic models + folio merging
src/analytics.py           Pure analytics functions + XIRR
src/llm.py                 OpenAI tool-calling assistant
src/logging_setup.py       loguru/stdlib logging with JWT redaction
src/providers/base.py      PortfolioProvider ABC
src/providers/kuvera_provider.py   Kuvera -> Portfolio (async + asyncio.run sync wrapper)
src/providers/csv_provider.py      CSV -> Portfolio
samples/sample_holdings.csv        Example CSV
tests/                     pytest suite
```

### Notes on the ported client

`src/kuvera_client.py` is ported from `sheikbe/kuvera-mcp/kuvera_client.py` with the same
endpoints, parsing, `TokenRedactionFilter`, and `_request` error handling. The only changes are
lint fixes (an unused `math` import removed, imports sorted) and `ruff format` line wrapping.
`KuveraClient` takes an injected `httpx.AsyncClient`. The base URL and timeout for it come from
`KUVERA_API_BASE_URL` and `KUVERA_API_TIMEOUT`.

The AMC label uses Kuvera's `fund_name` field, falling back to the first word of the scheme name.
