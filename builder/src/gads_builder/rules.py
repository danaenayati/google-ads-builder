"""Pure checks shared by every plan kind. No Google calls here; Google's own
check-only validation (check_plan) remains the final authority."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

ERROR, WARNING = "error", "warning"


@dataclass(frozen=True)
class Finding:
    level: str  # "error" | "warning"
    field: str  # e.g. "headlines[2]"
    message: str


def display_length(text: str) -> int:
    """Google counts wide (e.g. CJK) characters as 2; Arabic and Latin count as 1."""
    text = unicodedata.normalize("NFC", text)
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


_REPEATED_PUNCT = re.compile(r"([!?.,;:؟،！？])\1")
_PHONE_RUN = re.compile(r"\d(?:[ \u00a0\-().]*\d)*")
_GROUP = r"\d{1,3}(?:[ ,.\u00a0]\d{3})+"
_THOUSANDS = re.compile(rf"^{_GROUP}(?:\s*-\s*{_GROUP})*$")
# Ad text also rejects these invisible marks (on top of the unsafe characters below).
_INVISIBLE = re.compile("[\u061c\u200b\u200e\u200f\ufeff]")
_DOUBLE_SPACE = re.compile("[ \u00a0]{2}")
# Bidi embedding, override and isolate controls: they can reorder what the person reads.
_BIDI_CONTROLS = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
UNSAFE_MESSAGE = "contains line breaks, control characters or bidi controls"


_JOINERS = frozenset("\u200c\u200d")  # ZWNJ and ZWJ: Persian words and emoji need them
# Letters and symbols that draw nothing, so a name padded with them looks like another name: the Hangul
# fillers (choseong, jungseong, compatibility and halfwidth) and the blank Braille pattern.
BLANK_LETTERS = frozenset("\u115f\u1160\u3164\uffa0\u2800")


def has_invisible_characters(text: str) -> bool:
    """Characters nobody sees: the invisible marks above, the blank-looking letters, and every other
    format character (Unicode category Cf: zero-width spaces, word joiners, soft hyphens, direction
    marks), except ZWNJ and ZWJ."""
    return bool(_INVISIBLE.search(text)) or any(
        ch in BLANK_LETTERS or (unicodedata.category(ch) == "Cf" and ch not in _JOINERS) for ch in text)


def has_unsafe_characters(text: str) -> bool:
    """Control characters (Cc: tabs, line breaks, NEL U+0085, escape codes), line and paragraph
    separators (U+2028/U+2029) and bidi controls: anything that could start a fake line or reorder
    text in the summary or the approval prompt. ZWNJ (U+200C) is fine: Persian needs it."""
    return any(unicodedata.category(ch) in ("Cc", "Zl", "Zp") or ch in _BIDI_CONTROLS for ch in text)


def looks_like_phone(text: str) -> bool:
    """A run of at least 9 digits (spaces, - ( ) . allowed between them) that is
    not a thousands-grouped number (or a hyphenated range of them). Runs that
    follow a '+' or start with 0 are always phone-like."""
    for m in _PHONE_RUN.finditer(text):
        run = m.group(0)
        if sum(ch.isdigit() for ch in run) < 9:
            continue
        international = text[: m.start()].endswith("+")
        if not international and not run.startswith("0") and _THOUSANDS.match(run):
            continue
        return True
    return False


_SHOUTING = re.compile(r"\b[A-Z]{4,}\b")


def check_text(text: str, field: str, limit: int, *, headline: bool) -> list[Finding]:
    if not text.strip():
        return [Finding(ERROR, field, "is empty")]
    out: list[Finding] = []
    n = display_length(text)
    if n > limit:
        out.append(Finding(ERROR, field, f"is {n} characters; the limit is {limit}"))
    if headline and ("!" in text or "！" in text):
        out.append(Finding(ERROR, field, "headlines may not contain '!' (Google editorial rule)"))
    if _REPEATED_PUNCT.search(text):
        out.append(Finding(ERROR, field, "repeated punctuation is not allowed"))
    if looks_like_phone(text):
        out.append(Finding(ERROR, field, "phone numbers are not allowed in ad text"))
    if has_unsafe_characters(text) or _INVISIBLE.search(text):
        out.append(Finding(ERROR, field, "contains line breaks, control characters or invisible direction marks"))
    if text != text.strip() or _DOUBLE_SPACE.search(text):
        out.append(Finding(ERROR, field, "has leading/trailing or double spaces"))
    if _SHOUTING.search(text):
        out.append(Finding(WARNING, field, "ALL-CAPS words are usually rejected; use normal case"))
    return out


_ASCII_WORD = re.compile(r"[a-z0-9 \-]+")


def mentions(text: str, word: str) -> bool:
    """`word` is in `text`, ignoring case and Unicode form: as a whole word for plain ASCII words, and
    anywhere for other scripts (e.g. Arabic, where prefixes attach to words)."""
    needle = unicodedata.normalize("NFKC", word).casefold().strip()
    if not needle:
        return False
    haystack = unicodedata.normalize("NFKC", text).casefold()
    if _ASCII_WORD.fullmatch(needle):
        return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack) is not None
    return needle in haystack


def check_blocked(text: str, field: str, blocked_words: tuple[str, ...] | list[str]) -> list[Finding]:
    return [Finding(ERROR, field, f"contains the blocked word '{word}'") for word in blocked_words if mentions(text, word)]


def _norm(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKC", text).casefold() if ch.isalnum())


def find_duplicates(texts: list[str], field: str) -> list[Finding]:
    seen: dict[str, int] = {}
    out = []
    for i, text in enumerate(texts):
        key = _norm(text)
        if key in seen:
            out.append(Finding(ERROR, f"{field}[{i}]", f"duplicates {field}[{seen[key]}]"))
        else:
            seen[key] = i
    return out


# name -> (width/height, recommended minimum size)
RATIOS = {
    "landscape": (1.91, (600, 314)),
    "square": (1.0, (600, 600)),
    "portrait": (0.8, (480, 600)),
}
MAX_IMAGE_BYTES = 5_120 * 1024


def classify_image(path: Path) -> tuple[str | None, list[Finding]]:
    field = f"image {path.name}"
    if not path.is_file():
        return None, [Finding(ERROR, field, "file not found")]
    out: list[Finding] = []
    if path.stat().st_size > MAX_IMAGE_BYTES:
        out.append(Finding(ERROR, field, "is larger than 5 MB"))
    try:
        with Image.open(path) as im:
            w, h = im.size
            fmt = im.format
    except (OSError, Image.DecompressionBombError):
        return None, [*out, Finding(ERROR, field, "is not a readable image")]
    if fmt not in ("JPEG", "PNG", "GIF"):
        out.append(Finding(ERROR, field, f"format {fmt} is not accepted; use JPEG, PNG or GIF"))
    for name, (target, (min_w, min_h)) in RATIOS.items():
        if abs(w / h - target) / target <= 0.01:
            if w < min_w or h < min_h:
                out.append(Finding(WARNING, field, f"{w}x{h} is below the recommended {min_w}x{min_h}"))
            return name, out
    out.append(Finding(ERROR, field, f"{w}x{h} is not 1.91:1, 1:1 or 4:5"))
    return None, out


def as_float(value) -> float | None:
    """A plan's number as a float: None if it is not a number, infinity if it is too large for one.
    (math.isfinite and float() raise OverflowError on a huge int, such as 10**400.)"""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        return float(value)
    except OverflowError:
        return math.inf if value > 0 else -math.inf


def check_budget(amount: float, cap: float, confirm: float | None, currency: str) -> list[Finding]:
    f = "daily_budget"
    amount, confirm = as_float(amount), as_float(confirm)
    if amount is None or not math.isfinite(amount) or amount <= 0:
        return [Finding(ERROR, f, "must be a positive number")]
    if amount > 3 * cap:
        return [
            Finding(
                ERROR,
                f,
                f"{amount:,.2f} {currency} is more than 3x the cap of {cap:,.2f}; this cannot be "
                "confirmed here. Raise daily_budget_cap in builder.toml if it is really intended",
            )
        ]
    if amount > cap:
        if confirm is None or not math.isfinite(confirm) or confirm != amount:
            return [
                Finding(
                    ERROR,
                    f,
                    f"{amount:,.2f} {currency} is above the cap of {cap:,.2f}; "
                    "the person must confirm this exact amount (confirm_budget_over_cap)",
                )
            ]
        return [
            Finding(
                WARNING,
                f,
                f"{amount:,.2f} {currency} is ABOVE THE CAP of {cap:,.2f}; confirm_budget_over_cap matches it. Approve only if you asked for this budget",
            )
        ]
    return []
