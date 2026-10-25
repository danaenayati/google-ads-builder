"""Private, per-company settings. The file lives outside the repo (default
~/.config/google-ads-mcp/builder.toml, or $GADS_BUILDER_CONFIG)."""

from __future__ import annotations

import math
import os
import re
import shutil
import sys
import tomllib
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_PATH = Path.home() / ".config" / "google-ads-mcp" / "builder.toml"
DEFAULT_CODEX = "/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex"  # the ChatGPT app's, on macOS


def default_codex() -> str | None:
    """Where Codex is when builder.toml doesn't say: the ChatGPT app's on macOS; elsewhere `codex` on PATH
    (on Windows a real codex.exe first, then npm's codex.cmd). None if it isn't installed."""
    if sys.platform == "darwin":
        return DEFAULT_CODEX
    if sys.platform == "win32":
        return shutil.which("codex.exe") or shutil.which("codex.cmd") or shutil.which("codex")
    return shutil.which("codex")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class AppRef:
    store: str  # GOOGLE_APP_STORE | APPLE_APP_STORE
    app_id: str
    deep_link_prefixes: tuple[str, ...] = ()  # final_app_urls must start with one of these, when given


@dataclass(frozen=True)
class Config:
    customer_id: str
    currency: str
    daily_budget_cap: float
    apps: tuple[AppRef, ...]
    country_code: str
    credentials: Path
    plans_dir: Path
    insights_dir: Path
    images_dir: Path
    codex_path: Path | None  # None: Codex isn't installed (generate_image says so)
    codex_model: str
    codex_images_dir: Path
    check_max_age_hours: float = 24.0
    blocked_words: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()  # final URLs must be https on one of these hosts or their subdomains
    brand_words: tuple[str, ...] = ()  # a keyword with one of these is a brand keyword

    def app(self, store: str, app_id: str) -> AppRef | None:
        return next((a for a in self.apps if a.store == store and a.app_id == app_id), None)

    def allows_app(self, store: str, app_id: str) -> bool:
        return self.app(store, app_id) is not None

    def allows_url(self, url: str) -> bool:
        return url_allowed(url, self.domains)


_HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+")
# Whitespace, control, invisible and bidi characters, and the backslash (a browser reads it as "/", so
# "https://evil.example\\@example.com" would go to evil.example while urlsplit sees example.com).
_BAD_URL_CHAR = re.compile(r"[\s\\\x00-\x1f\x7f-\x9f\u061c\u200b-\u200f\u2028-\u202e\u2066-\u2069\ufeff]")


def url_allowed(url: str, domains: tuple[str, ...]) -> bool:
    """https only, no user name, password or port (not even an empty one), no invisible format
    characters or odd spaces, and the host is one of `domains` or a subdomain of one. Fails closed on
    anything it can't parse."""
    if not isinstance(url, str) or _BAD_URL_CHAR.search(url):
        return False
    if any(unicodedata.category(ch) in ("Cf", "Zs") for ch in url):
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    if parts.scheme != "https" or "@" in parts.netloc or ":" in parts.netloc or port is not None:
        return False
    if not url.startswith("https://"):
        return False
    host = parts.hostname or ""
    return bool(_HOST.fullmatch(host)) and any(host == d or host.endswith("." + d) for d in domains)


# A second-level public suffix such as com.xx or co.xx: anyone can register a site under it.
_SUFFIX_LIKE = re.compile(r"(?:ac|biz|co|com|edu|gen|gov|info|ltd|net|nom|org|plc)\.[a-z]{2}")


def suffix_like_domains(domains) -> list[str]:
    """Configured domains that look like a bare public suffix (no dot, or com.xx, co.xx and the like).
    Allowing one would allow every site under it. A warning, not an error: the list is a heuristic."""
    return [d for d in domains if "." not in d or _SUFFIX_LIKE.fullmatch(d)]


def _path(value: str) -> Path:
    return Path(os.path.expanduser(str(value)))


def _codex_path(value) -> Path | None:
    if value is None:
        found = default_codex()
        return _path(found) if found else None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("codex_path must be the full path of the codex program")
    return _path(value)


def _words(value, name: str = "blocked_words") -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(w, str) for w in value):
        raise ConfigError(f"{name} must be a list of strings")
    return tuple(value)


def _domains(value) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(d, str) and _HOST.fullmatch(d.lower()) for d in value):
        raise ConfigError("domains must be a list of host names such as example.com (no https://, no path)")
    return tuple(d.lower() for d in value)


def _brand_words(value) -> tuple[str, ...]:
    words = _words(value, "brand_words")
    if not all(w.strip() for w in words):
        raise ConfigError("brand_words must not contain empty words")
    return tuple(w.strip() for w in words)


def _app(raw) -> AppRef:
    if not isinstance(raw, dict):
        raise ConfigError("each [[apps]] entry needs store and id")
    prefixes = raw.get("deep_link_prefixes", [])
    if not isinstance(prefixes, list) or not all(
        isinstance(p, str) and "://" in p and not any(ch.isspace() for ch in p) for p in prefixes
    ):
        raise ConfigError("deep_link_prefixes must be a list of non-empty strings containing ://")
    return AppRef(raw["store"], str(raw["id"]), tuple(prefixes))


def load_config(path: Path | None = None) -> Config:
    path = Path(path or os.environ.get("GADS_BUILDER_CONFIG", DEFAULT_PATH))
    if not path.is_file():
        raise ConfigError(f"Builder config not found: {path}")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        cfg = Config(
            customer_id="".join(ch for ch in str(raw["customer_id"]) if ch in "0123456789"),  # ASCII only
            currency=str(raw["currency"]).upper(),
            daily_budget_cap=float(raw["daily_budget_cap"]),
            apps=tuple(_app(a) for a in raw["apps"]),
            country_code=str(raw["country_code"]).upper(),
            credentials=_path(raw.get("credentials", "~/.config/google-ads-mcp/google-ads.yaml")),
            plans_dir=_path(raw["plans_dir"]),
            insights_dir=_path(raw["insights_dir"]),
            images_dir=_path(raw["images_dir"]),
            codex_path=_codex_path(raw.get("codex_path")),
            codex_model=str(raw.get("codex_model", "gpt-6-luna")),
            codex_images_dir=_path(raw.get("codex_images_dir", "~/.codex/generated_images")),
            check_max_age_hours=float(raw.get("check_max_age_hours", 24)),
            blocked_words=_words(raw.get("blocked_words", [])),
            domains=_domains(raw.get("domains", [])),
            brand_words=_brand_words(raw.get("brand_words", [])),
        )
    except KeyError as missing:
        raise ConfigError(f"Builder config {path} is missing {missing}") from None
    except (tomllib.TOMLDecodeError, ValueError, TypeError) as e:
        raise ConfigError(f"Builder config {path} is not valid: {e}") from None
    if len(cfg.customer_id) != 10:
        raise ConfigError("customer_id must have 10 digits")
    if not math.isfinite(cfg.daily_budget_cap) or cfg.daily_budget_cap <= 0:
        raise ConfigError("daily_budget_cap must be a finite positive number")
    if not re.fullmatch(r"[A-Z]{3}", cfg.currency):
        raise ConfigError("currency must be a three-letter code such as USD")
    if not re.fullmatch(r"[A-Z]{2}", cfg.country_code):
        raise ConfigError("country_code must be a two-letter code such as US")
    if not math.isfinite(cfg.check_max_age_hours) or not 0 < cfg.check_max_age_hours <= 168:
        raise ConfigError("check_max_age_hours must be above 0 and at most 168")
    if not cfg.apps:
        raise ConfigError("apps must list at least one allowed app")
    return cfg
