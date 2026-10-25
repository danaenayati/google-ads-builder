import os
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


@pytest.mark.parametrize("found, expected", [({"codex": "/usr/local/bin/codex"}, "/usr/local/bin/codex"), ({}, None)])
def test_codex_on_linux_is_found_on_path(tmp_path, monkeypatch, found, expected):
    monkeypatch.setattr(config.sys, "platform", "linux")
    monkeypatch.setattr(config.shutil, "which", found.get)
    cfg = load_config(write(tmp_path, GOOD))
    assert cfg.codex_path == (None if expected is None else Path(expected))


WIN_CWD = "C:\\Users\\YOU\\project"  # the folder the server runs in: perhaps a repo Claude Code opened
NPM_BIN = ("C:\\Users\\YOU\\AppData\\Roaming\\npm\\node_modules\\@openai\\codex\\node_modules\\@openai\\"
           "codex-win32-{arch}\\vendor\\{triple}\\bin\\codex.exe")
NPM_X64 = NPM_BIN.format(arch="x64", triple="x86_64-pc-windows-msvc")
NPM_ARM64 = NPM_BIN.format(arch="arm64", triple="aarch64-pc-windows-msvc")


@pytest.fixture
def win(monkeypatch):
    monkeypatch.setattr(config.sys, "platform", "win32")
    monkeypatch.setattr(config, "_current_folder", lambda: WIN_CWD)
    monkeypatch.setattr(config, "_is_file", lambda path: False)  # no npm Codex unless a test says so
    monkeypatch.setenv("APPDATA", "C:\\Users\\YOU\\AppData\\Roaming")
    monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath", raising=False)


def test_windows_looks_up_only_codex_exe_and_never_in_the_current_folder(tmp_path, win, monkeypatch):
    asked = []

    def which(name, *args, **kwargs):
        asked.append((name, os.environ.get("NoDefaultCurrentDirectoryInExePath")))
        return "C:\\Tools\\codex.exe"

    monkeypatch.setattr(config.shutil, "which", which)
    assert str(load_config(write(tmp_path, GOOD)).codex_path) == "C:\\Tools\\codex.exe"
    assert asked == [("codex.exe", "1")]  # Windows' own rule: don't search the current folder first
    assert "NoDefaultCurrentDirectoryInExePath" not in os.environ  # put back as it was


def test_windows_keeps_an_existing_no_current_folder_setting(tmp_path, win, monkeypatch):
    monkeypatch.setenv("NoDefaultCurrentDirectoryInExePath", "yes")
    monkeypatch.setattr(config.shutil, "which", lambda name, *a, **k: "C:\\Tools\\codex.exe")
    load_config(write(tmp_path, GOOD))
    assert os.environ["NoDefaultCurrentDirectoryInExePath"] == "yes"


@pytest.mark.parametrize("found", [
    ".\\codex.exe", "codex.exe", "bin\\codex.exe",  # relative: the current folder, or a relative PATH entry
    "C:\\Users\\YOU\\project\\codex.exe", "c:\\users\\you\\PROJECT\\Codex.EXE", "C:/Users/YOU/project/codex.exe",
    "C:\\Users\\YOU\\project\\.\\codex.exe", "C:\\Users\\YOU\\other\\..\\project\\codex.exe",
    "C:\\Users\\YOU\\project\\codex.exe. .",  # Windows drops the trailing dots and spaces: still the current folder
    "C:\\Tools\\codex.cmd", "C:\\Tools\\codex.bat", "C:\\Tools\\codex", "C:\\Tools\\codex.ps1", "C:\\Tools\\.exe",
    "C:\\Tools\\codex.exe:evil", "C:\\Tools\\codex.exe::$DATA", "C:\\Tools\\x:codex.exe",  # NTFS streams
    "C:codex.exe", "\\Tools\\codex.exe",  # not a full path
    "\\\\server\\share\\codex.exe", "\\\\?\\C:\\Tools\\codex.exe", "\\\\.\\C:\\Tools\\codex.exe",  # network and device paths
    "",
])
def test_windows_refuses_a_codex_that_could_be_planted_or_is_not_an_exe(tmp_path, win, monkeypatch, found):
    monkeypatch.setattr(config.shutil, "which", lambda name, *a, **k: found)
    assert load_config(write(tmp_path, GOOD)).codex_path is None
    assert config.windows_codex(found, outside=WIN_CWD) is None


@pytest.mark.parametrize("found, expected", [
    ("C:\\Tools\\codex.exe", "C:\\Tools\\codex.exe"),
    ("C:/Tools/codex.exe", "C:\\Tools\\codex.exe"),
    ("C:\\Tools\\codex.exe. . ", "C:\\Tools\\codex.exe"),  # as Windows reads it
    ("D:\\Apps\\CODEX.EXE", "D:\\Apps\\CODEX.EXE"),
    ("C:\\Users\\YOU\\project\\tools\\codex.exe", "C:\\Users\\YOU\\project\\tools\\codex.exe"),  # an absolute PATH entry
])
def test_windows_accepts_codex_exe_by_its_full_path(tmp_path, win, monkeypatch, found, expected):
    monkeypatch.setattr(config.shutil, "which", lambda name, *a, **k: found)
    assert str(load_config(write(tmp_path, GOOD)).codex_path) == expected


@pytest.mark.parametrize("installed", [NPM_X64, NPM_ARM64])
def test_windows_finds_codex_exe_where_npm_installs_it(tmp_path, win, monkeypatch, installed):
    monkeypatch.setattr(config.shutil, "which", lambda name, *a, **k: None)  # npm puts only codex.cmd on PATH
    monkeypatch.setattr(config, "_is_file", lambda path: path == installed)
    assert str(load_config(write(tmp_path, GOOD)).codex_path) == installed


@pytest.mark.parametrize("appdata", [None, "", "AppData\\Roaming", "\\\\server\\profiles\\YOU\\AppData\\Roaming"])
def test_windows_without_a_usable_appdata_folder_does_not_guess(tmp_path, win, monkeypatch, appdata):
    if appdata is None:
        monkeypatch.delenv("APPDATA")
    else:
        monkeypatch.setenv("APPDATA", appdata)
    monkeypatch.setattr(config.shutil, "which", lambda name, *a, **k: None)
    monkeypatch.setattr(config, "_is_file", lambda path: True)
    assert load_config(write(tmp_path, GOOD)).codex_path is None


def test_windows_codex_normalises_what_windows_would_run():
    assert config.windows_codex("C:\\Tools\\sub\\..\\codex.exe . .") == "C:\\Tools\\codex.exe"
    assert config.windows_codex("C:\\Users\\YOU\\project\\codex.exe") == "C:\\Users\\YOU\\project\\codex.exe"  # no folder to avoid
    assert config.windows_codex(None) is None and config.windows_codex(5) is None


def test_codex_path_in_the_config_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(config.shutil, "which", lambda name: pytest.fail("the config names it"))
    cfg = load_config(write(tmp_path, _with('codex_path = "~/bin/codex"')))
    assert cfg.codex_path == Path.home() / "bin" / "codex"
    for bad in ('""', "5"):
        with pytest.raises(ConfigError, match="codex_path"):
            load_config(write(tmp_path, _with(f"codex_path = {bad}")))
