from datetime import timedelta
from pathlib import Path

from streamlit.testing.v1 import AppTest

from frc.models import utcnow
from frc.store import Store


def setup(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LOCAL_DEV", "1")
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def test_all_pages_render_without_api_key(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=15).run()
    assert not app.exception
    for page in ("Donations", "Recipients", "Public directory", "Copilot", "Audit"):
        app.sidebar.radio[0].set_value(page).run()
        assert not app.exception


def test_donation_form_persists(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=15).run()
    app.sidebar.radio[0].set_value("Donations").run()
    fields = {v.label: v for v in app.text_input}
    fields["Name"].set_value("Coordinator-entered restaurant")
    fields["Address"].set_value("Confirmed sample address")
    fields["Available from (ISO datetime with timezone; blank = unknown)"].set_value((utcnow()+timedelta(hours=1)).isoformat())
    fields["Pickup deadline (ISO datetime with timezone; blank = unknown)"].set_value((utcnow()+timedelta(hours=4)).isoformat())
    app.text_area[0].set_value("Prepared meals")
    next(b for b in app.button if b.label == "Save donation").click().run()
    assert not app.exception
    assert Store(tmp_path / "food_rescue_v1.db").list("donation")[0][0].name == "Coordinator-entered restaurant"


def test_login_required_and_valid_password(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    monkeypatch.setenv("LOCAL_DEV", "0")
    monkeypatch.setenv("APP_PASSWORD", "test-only-long-password-123")
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=15).run()
    assert not app.sidebar.radio
    app.text_input[0].set_value("wrong")
    app.button[0].click().run()
    assert not app.sidebar.radio
    app.text_input[0].set_value("test-only-long-password-123")
    app.button[0].click().run()
    assert not app.exception
    assert app.sidebar.radio[0].value == "Overview"


def test_render_never_allows_local_auth_bypass(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("APP_PASSWORD", "")
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=15).run()
    assert not app.sidebar.radio
    assert any("APP_PASSWORD" in e.value for e in app.error)
