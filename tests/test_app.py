"""Headless smoke test of the Streamlit UI (no network: uses the bundled sample CSV)."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> AppTest:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    at = AppTest.from_file(str(APP_PATH), default_timeout=30)
    at.run()
    return at


def test_landing_page_renders(app: AppTest) -> None:
    assert not app.exception
    assert "Kuvera Portfolio Analytics" in app.title[0].value
    assert any("Paste your Kuvera token" in i.value for i in app.info)


def test_sample_data_renders_all_tabs(app: AppTest) -> None:
    next(b for b in app.button if b.label == "Try with sample data").click().run()
    assert not app.exception
    assert [t.label for t in app.tabs] == ["Overview", "Holdings", "Performance", "Ask"]
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Current value"] == "₹139,000"
    assert metrics["Invested"] == "₹118,000"
    assert metrics["XIRR"].endswith("%")
    assert len(app.dataframe) >= 2
    assert any("OPENAI_API_KEY" in i.value for i in app.info)


def test_malformed_token_shows_error(app: AppTest) -> None:
    app.text_input(key="token_input").set_value("not-a-jwt")
    next(b for b in app.button if b.label == "Load from Kuvera").click().run()
    assert not app.exception
    assert any("doesn't look like a Kuvera token" in e.value for e in app.error)
    assert "kuvera_token" not in app.session_state
