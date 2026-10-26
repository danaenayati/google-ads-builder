"""scripts/make_settings.py: the parts that need no Google account. The file it writes must load as builder.toml."""

import importlib.util
import os
from pathlib import Path

import pytest

from gads_builder.config import load_config

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "make_settings.py"
spec = importlib.util.spec_from_file_location("make_settings", SCRIPT)
ms = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ms)

SETTINGS = {"customer_id": "123-456-7890", "account_name": "Pulse Rides", "currency": "EUR",
            "daily_budget_cap": 250.0, "country_code": "DE", "domains": ["pulse.example", "shop.pulse.example"],
            "apps": [("APPLE_APP_STORE", "1234567890"), ("GOOGLE_APP_STORE", "com.pulse.rider")]}


def test_the_written_settings_load_as_builder_toml(tmp_path):
    path = tmp_path / "builder.toml"
    path.write_text(ms.render(SETTINGS), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.customer_id == "1234567890" and cfg.currency == "EUR" and cfg.daily_budget_cap == 250
    assert cfg.country_code == "DE" and cfg.domains == ("pulse.example", "shop.pulse.example")
    assert [(a.store, a.app_id) for a in cfg.apps] == SETTINGS["apps"]
    assert "daily_budget_cap = 250 " in path.read_text(encoding="utf-8")


def test_an_account_without_app_campaigns_gets_a_placeholder_that_still_loads(tmp_path):
    path = tmp_path / "builder.toml"
    path.write_text(ms.render({**SETTINGS, "apps": [], "domains": [], "daily_budget_cap": 99.5}), encoding="utf-8")
    cfg = load_config(path)
    assert [(a.store, a.app_id) for a in cfg.apps] == [("GOOGLE_APP_STORE", "com.example.app")]
    assert cfg.domains == () and cfg.daily_budget_cap == 99.5
    assert "No App campaign found" in path.read_text(encoding="utf-8")


def test_names_with_quotes_and_other_scripts_are_escaped(tmp_path):
    path = tmp_path / "builder.toml"
    path.write_text(ms.render({**SETTINGS, "account_name": 'Café "Nord" \\ متجر'}), encoding="utf-8")
    assert load_config(path).customer_id == "1234567890"


@pytest.mark.parametrize("url, host", [
    ("https://www.pulse.example/ride?x=1", "pulse.example"),
    ("http://Shop.Pulse.Example./", "shop.pulse.example"),
    ("ftp://pulse.example", None), ("https://localhost", None), ("not a url", None), ("https://[::1]/", None),
])
def test_host_of(url, host):
    assert ms.host_of(url) == host


def test_domains_keep_parents_and_drop_their_subdomains():
    urls = ["https://shop.pulse.example/a", "https://www.pulse.example/", "https://pulse.example/b",
            "https://other.example/", "https://m.other.example/x", "android-app://com.pulse.rider"]
    assert ms.domains_from(urls) == ["other.example", "pulse.example"]
    assert ms.domains_from(["https://a.pulse.example", "https://b.pulse.example"]) == ["a.pulse.example",
                                                                                         "b.pulse.example"]


@pytest.mark.parametrize("budgets, cap", [([], 100.0), ([0], 100.0), ([8_078_400_000], 8100.0),
                                          ([300_000_000, 1_400_000_000], 1400.0), ([45_500_000], 46.0)])
def test_suggested_cap_rounds_the_largest_budget_up(budgets, cap):
    assert ms.suggested_cap(budgets) == cap


def test_country_for_zone_reads_the_system_table_when_there_is_one():
    found = ms.country_for_zone("Europe/Berlin")
    assert found in ("DE", None)  # None where the system has no zone table (Windows without tzdata)
    assert ms.country_for_zone("Nowhere/Nothing") is None


def test_login_customer_id_is_written_and_removed(tmp_path):
    yaml = tmp_path / "google-ads.yaml"
    yaml.write_text("client_id: x\nrefresh_token: y\nlogin_customer_id: 1111111111\nuse_proto_plus: True\n",
                    encoding="utf-8")
    ms.set_login_customer_id(yaml, "2222222222")
    text = yaml.read_text(encoding="utf-8")
    assert text.count("login_customer_id") == 1 and "login_customer_id: 2222222222" in text
    assert "refresh_token: y" in text
    ms.set_login_customer_id(yaml, None)
    assert "login_customer_id" not in yaml.read_text(encoding="utf-8")


@pytest.mark.skipif(os.name == "nt", reason="Windows keeps files private by the user folder, not by mode")
def test_settings_are_written_private(tmp_path):
    path = tmp_path / "sub" / "builder.toml"
    ms.write_private(path, "x = 1\n")
    assert path.stat().st_mode & 0o777 == 0o600


def test_shared_sites_are_never_suggested_as_your_website():
    urls = ["https://play.google.com/store/apps/details?id=x", "https://docs.google.com/forms/d/1",
            "https://abc123.typeform.com/to/x", "https://pulse.example/", "https://www.instagram.com/pulse"]
    assert ms.domains_from(urls, keep_shared=False) == ["pulse.example"]
    assert ms.is_shared("abc123.typeform.com") and not ms.is_shared("mytypeform.com")
    assert "docs.google.com" in ms.domains_from(urls)  # kept when asked for every host
