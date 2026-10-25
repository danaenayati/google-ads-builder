from pathlib import Path

import pytest

from gads_builder import config
from gads_builder.config import AppRef, ConfigError, load_config, suffix_like_domains, url_allowed

GOOD = """customer_id = "123-456-7890"
currency = "usd"
daily_budget_cap = 500
country_code = "xx"
plans_dir = "~/plans"
insights_dir = "~/insights"
images_dir = "~/images"

[[apps]]
store = "GOOGLE_APP_STORE"
id = "com.example.app"
"""


def write(tmp_path, text):
    path = tmp_path / "builder.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_config_normalises_values(tmp_path):
    cfg = load_config(write(tmp_path, GOOD))
    assert cfg.customer_id == "1234567890"
    assert cfg.currency == "USD"
    assert cfg.country_code == "XX"
    assert cfg.plans_dir == Path.home() / "plans"
    assert cfg.allows_app("GOOGLE_APP_STORE", "com.example.app")
    assert not cfg.allows_app("APPLE_APP_STORE", "com.example.app")
    assert cfg.codex_images_dir == Path.home() / ".codex" / "generated_images"
    assert cfg.check_max_age_hours == 24.0


def test_missing_key_is_named(tmp_path):
    with pytest.raises(ConfigError, match="daily_budget_cap"):
        load_config(write(tmp_path, GOOD.replace("daily_budget_cap = 500\n", "")))


def test_customer_id_must_have_ten_digits(tmp_path):
    with pytest.raises(ConfigError, match="10 digits"):
        load_config(write(tmp_path, GOOD.replace("123-456-7890", "12345")))


def test_customer_id_needs_ascii_digits(tmp_path):
    with pytest.raises(ConfigError, match="10 digits"):
        load_config(write(tmp_path, GOOD.replace("123-456-7890", "\u0661\u0662\u0663-\u0664\u0665\u0666-\u0667\u0668\u0669\u0660")))


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")


def test_app_ref_equality():
    assert AppRef("GOOGLE_APP_STORE", "a") == AppRef("GOOGLE_APP_STORE", "a")


@pytest.mark.parametrize(
    "old, new, match",
    [
        ("daily_budget_cap = 500", "daily_budget_cap = nan", "daily_budget_cap"),
        ("daily_budget_cap = 500", "daily_budget_cap = inf", "daily_budget_cap"),
        ('currency = "usd"', 'currency = "dollars"', "currency"),
        ('country_code = "xx"', 'country_code = "xxx"', "country_code"),
        ("daily_budget_cap = 500", "daily_budget_cap = 500\ncheck_max_age_hours = 0", "check_max_age_hours"),
        ("daily_budget_cap = 500", "daily_budget_cap = 500\ncheck_max_age_hours = 1000", "check_max_age_hours"),
        ("daily_budget_cap = 500", 'daily_budget_cap = "lots"', "not valid"),
    ],
)
def test_bad_values_are_config_errors(tmp_path, old, new, match):
    with pytest.raises(ConfigError, match=match):
        load_config(write(tmp_path, GOOD.replace(old, new)))


def test_invalid_toml_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, "this is = = not toml"))


def test_blocked_words(tmp_path):
    assert load_config(write(tmp_path, GOOD)).blocked_words == ()
    text = GOOD.replace("daily_budget_cap = 500", 'daily_budget_cap = 500\nblocked_words = ["Rival", "Other"]')
    assert load_config(write(tmp_path, text)).blocked_words == ("Rival", "Other")
    with pytest.raises(ConfigError, match="blocked_words"):
        load_config(write(tmp_path, GOOD.replace("daily_budget_cap = 500", "daily_budget_cap = 500\nblocked_words = 5")))


def test_deep_link_prefixes(tmp_path):
    assert load_config(write(tmp_path, GOOD)).apps[0].deep_link_prefixes == ()
    text = GOOD + 'deep_link_prefixes = ["example://open/", "example://deals/"]\n'
    cfg = load_config(write(tmp_path, text))
    assert cfg.apps[0].deep_link_prefixes == ("example://open/", "example://deals/")
    assert cfg.allows_app("GOOGLE_APP_STORE", "com.example.app")
    assert cfg.app("GOOGLE_APP_STORE", "com.example.app").deep_link_prefixes == ("example://open/", "example://deals/")
    assert cfg.app("APPLE_APP_STORE", "com.example.app") is None


@pytest.mark.parametrize("value", ['"example://"', '[""]', '["example"]', "[1]", '["example:// x"]'])
def test_bad_deep_link_prefixes_are_config_errors(tmp_path, value):
    with pytest.raises(ConfigError, match="deep_link_prefixes"):
        load_config(write(tmp_path, GOOD + f"deep_link_prefixes = {value}\n"))


def _with(line: str) -> str:
    return GOOD.replace("daily_budget_cap = 500", f"daily_budget_cap = 500\n{line}")


def test_domains_and_brand_words(tmp_path):
    cfg = load_config(write(tmp_path, GOOD))
    assert cfg.domains == () and cfg.brand_words == ()
    assert not cfg.allows_url("https://example.com/")  # no domains configured: no URL is allowed
    cfg = load_config(write(tmp_path, _with('domains = ["Example.com", "shop.example.org"]\nbrand_words = ["Pulse"]')))
    assert cfg.domains == ("example.com", "shop.example.org") and cfg.brand_words == ("Pulse",)
    assert cfg.allows_url("https://www.example.com/gym") and cfg.allows_url("https://shop.example.org/")
    assert not cfg.allows_url("https://example.org/")


def test_brand_words_are_stored_stripped(tmp_path):
    cfg = load_config(write(tmp_path, _with('brand_words = ["  Pulse ", "Pulse Fitness\t"]')))
    assert cfg.brand_words == ("Pulse", "Pulse Fitness")


@pytest.mark.parametrize("value", ['"example.com"', '["https://example.com"]', '["example.com/gym"]', '["localhost"]',
                                   '[""]', "[1]", '["exa mple.com"]', '["example.com."]', '["*.example.com"]'])
def test_bad_domains_are_config_errors(tmp_path, value):
    with pytest.raises(ConfigError, match="domains"):
        load_config(write(tmp_path, _with(f"domains = {value}")))


@pytest.mark.parametrize("value", ['"Pulse"', '[""]', '["  "]', "[1]"])
def test_bad_brand_words_are_config_errors(tmp_path, value):
    with pytest.raises(ConfigError, match="brand_words"):
        load_config(write(tmp_path, _with(f"brand_words = {value}")))


@pytest.mark.parametrize("url, allowed", [
    ("https://example.com", True),
    ("https://www.example.com/gym?ref=ad#top", True),
    ("https://WWW.EXAMPLE.COM/gym", True),
    ("https://example.com/\u0628\u0627\u0634\u06af\u0627\u0647", True),  # a non-ASCII path is fine
    ("http://example.com/", False),
    ("HTTPS://example.com/", False),
    ("https://notexample.com/", False),
    ("https://example.com.evil.test/", False),
    ("https://user@example.com/", False),
    ("https://evil.test\\@example.com/", False),
    ("https://evil.test#@example.com", False),
    ("https://example.com:443/", False),
    ("https://example.com:x/", False),
    ("https://example.com/two words", False),
    ("https://example.com/\u202e", False),
    ("https://example.com/\u200b", False),
    ("https://example.com/\n", False),
    ("https://example.com./", False),
    ("https://ex\u0430mple.com/", False),  # Cyrillic a
    ("https:///example.com", False),
    ("https://example.com:/", False),  # an empty port is still a port
    ("https://example.com:/gym", False),
    ("https://www.example.com/pri\u00adces", False),  # soft hyphen (Cf)
    ("https://www.example.com/gym\u2060", False),  # word joiner (Cf)
    ("https://www.example.com/gym\u180e", False),  # Mongolian vowel separator (Cf)
    ("https://www.example.com/\U000e0041", False),  # tag character (Cf)
    ("https://www.example.com/\u0600", False),  # Arabic number sign (Cf)
    ("https://www.example.com/a\u2009b", False),  # thin space (Zs)
    ("https://www.example.com/a\u3000b", False),  # ideographic space (Zs)
    ("", False),
    (None, False),
])
def test_allowed_urls(url, allowed):
    assert url_allowed(url, ("example.com",)) is allowed


def test_domains_that_look_like_a_public_suffix():
    domains = ("example.com", "com.xx", "co.xx", "shop.co.xx", "org.xx", "localhost", "example.co")
    assert suffix_like_domains(domains) == ["com.xx", "co.xx", "org.xx", "localhost"]


# ------------------------------------------------------------------ where Codex is


def test_codex_defaults_to_the_chatgpt_app_on_macos(tmp_path, monkeypatch):
    monkeypatch.setattr(config.sys, "platform", "darwin")
    monkeypatch.setattr(config.shutil, "which", lambda name: pytest.fail("macOS uses the ChatGPT app's Codex"))
    assert load_config(write(tmp_path, GOOD)).codex_path == Path(config.DEFAULT_CODEX)


@pytest.mark.parametrize("platform, found, expected", [
    ("win32", {"codex.exe": "C:/tools/codex.exe", "codex.cmd": "C:/npm/codex.cmd"}, "C:/tools/codex.exe"),
    ("win32", {"codex.cmd": "C:/npm/codex.cmd", "codex": "C:/npm/codex"}, "C:/npm/codex.cmd"),
    ("win32", {}, None),
    ("linux", {"codex": "/usr/local/bin/codex"}, "/usr/local/bin/codex"),
    ("linux", {}, None),
])
def test_codex_elsewhere_is_found_on_path(tmp_path, monkeypatch, platform, found, expected):
    monkeypatch.setattr(config.sys, "platform", platform)
    monkeypatch.setattr(config.shutil, "which", found.get)
    cfg = load_config(write(tmp_path, GOOD))
    assert cfg.codex_path == (None if expected is None else Path(expected))


def test_codex_path_in_the_config_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(config.shutil, "which", lambda name: pytest.fail("the config names it"))
    cfg = load_config(write(tmp_path, _with('codex_path = "~/bin/codex"')))
    assert cfg.codex_path == Path.home() / "bin" / "codex"
    for bad in ('""', "5"):
        with pytest.raises(ConfigError, match="codex_path"):
            load_config(write(tmp_path, _with(f"codex_path = {bad}")))
