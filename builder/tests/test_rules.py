import math

import pytest

from gads_builder import rules


def test_display_length_counts_wide_characters_twice():
    assert rules.display_length("abc") == 3
    assert rules.display_length("مرحبا") == 5
    assert rules.display_length("日本") == 4


def test_headline_rules():
    assert rules.check_text("Fast workouts", "h", 30, headline=True) == []
    assert any("'!'" in f.message for f in rules.check_text("Fast workouts!", "h", 30, headline=True))
    too_long = rules.check_text("x" * 31, "h", 30, headline=True)
    assert too_long[0].level == "error" and "31 characters" in too_long[0].message


def test_description_may_use_one_exclamation_but_not_repeats():
    assert rules.check_text("Save more today!", "d", 90, headline=False) == []
    assert any("repeated" in f.message for f in rules.check_text("Save!!", "d", 90, headline=False))


def test_phone_spaces_caps():
    assert any("phone" in f.message for f in rules.check_text("Call 0555 123 4567", "d", 90, headline=False))
    assert any("double spaces" in f.message for f in rules.check_text("Fast  workout", "h", 30, headline=True))
    caps = rules.check_text("SAVE big now", "h", 30, headline=True)
    assert caps and caps[0].level == "warning"


def test_empty_text_is_an_error():
    assert rules.check_text("   ", "h", 30, headline=True)[0].message == "is empty"


def test_duplicates_ignore_case_and_punctuation():
    found = rules.find_duplicates(["Fast workout", "fast workout.", "Other"], "headlines")
    assert [(f.field, f.message) for f in found] == [("headlines[1]", "duplicates headlines[0]")]


def test_classify_image(make_png):
    assert rules.classify_image(make_png(1200, 628))[0] == "landscape"
    assert rules.classify_image(make_png(1200, 1200))[0] == "square"
    assert rules.classify_image(make_png(1200, 1500))[0] == "portrait"
    ratio, found = rules.classify_image(make_png(1000, 700))
    assert ratio is None and found[0].level == "error"
    ratio, found = rules.classify_image(make_png(400, 209))
    assert ratio == "landscape" and found[0].level == "warning"


def test_missing_image_file(tmp_path):
    ratio, found = rules.classify_image(tmp_path / "missing.png")
    assert ratio is None and found[0].message == "file not found"


def test_budget_cap():
    assert rules.check_budget(300, 500, None, "USD") == []
    assert "above the cap" in rules.check_budget(600, 500, None, "USD")[0].message
    assert rules.check_budget(600, 500, 600, "USD")[0].level == "warning"
    assert rules.check_budget(600, 500, 601, "USD")[0].level == "error"
    assert "positive" in rules.check_budget(0, 500, None, "USD")[0].message


def _msgs(text, headline=False, limit=90):
    return [f.message for f in rules.check_text(text, "f", limit, headline=headline)]


@pytest.mark.parametrize("text", ["Call 0555 123 4567", "0555.123.4567", "+1 555 123 4567"])
def test_phone_numbers_are_flagged(text):
    assert rules.looks_like_phone(text)
    assert any("phone" in m for m in _msgs(text))


@pytest.mark.parametrize(
    "text",
    ["From 1 500 000 EUR", "Over 25 000 000 members", "Prices from 10000000 EUR",
     "Offer valid 2026-2027", "50%", "5,000", "24/7"],
)
def test_non_phone_numbers_pass(text):
    assert not rules.looks_like_phone(text)
    assert not any("phone" in m for m in _msgs(text))


def test_fullwidth_exclamation_and_repeated_arabic_punctuation():
    assert any("'!'" in m for m in _msgs("Fast workouts！", headline=True, limit=30))
    for text in ["چطوری؟؟", "سلام،، دنیا", "Hello！！", "Hello？？"]:
        assert any("repeated" in m for m in _msgs(text)), text


def test_invisible_marks_tabs_and_newlines():
    for text in ["Fast\u200f workouts", "Fast\u202a workouts", "Fast\u2066workouts", "Fast\tworkouts", "Fast\nworkouts", "Fast\rworkouts"]:
        assert any("invisible direction marks" in m for m in _msgs(text)), repr(text)
    assert _msgs("می\u200cروم") == []


def test_double_non_breaking_space():
    assert any("double spaces" in m for m in _msgs("Fast\u00a0\u00a0workout"))


def test_display_length_normalises():
    assert rules.display_length("e\u0301") == 1


def test_check_blocked():
    found = rules.check_blocked("Better than RIVAL gyms", "headlines[0]", ("rival", "other"))
    assert [(f.level, f.message) for f in found] == [("error", "contains the blocked word 'rival'")]
    assert rules.check_blocked("ＲＩＶＡＬ", "h", ("rival",))
    assert rules.check_blocked("Fine text", "h", ("rival",)) == []


def test_non_image_file(tmp_path):
    path = tmp_path / "fake.png"
    path.write_text("not an image", encoding="utf-8", newline="\n")
    ratio, found = rules.classify_image(path)
    assert ratio is None and [f.message for f in found] == ["is not a readable image"]


def test_gif_format_message_mentions_gif(tmp_path):
    from PIL import Image

    path = tmp_path / "a.bmp"
    Image.new("RGB", (600, 600)).save(path, format="BMP")
    _, found = rules.classify_image(path)
    assert any("use JPEG, PNG or GIF" in f.message for f in found)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, 0, -5, 10**400, -10**400, "80", True, None],
                         ids=["nan", "inf", "-inf", "0", "-5", "10**400", "-10**400", "text", "True", "None"])
def test_budget_must_be_positive_finite(bad):
    found = rules.check_budget(bad, 500, None, "USD")
    assert found[0].level == "error" and "must be a positive number" in found[0].message


def test_budget_three_times_cap_cannot_be_confirmed():
    found = rules.check_budget(1501, 500, 1501, "USD")
    assert found[0].level == "error" and "more than 3x the cap" in found[0].message
    assert "Raise daily_budget_cap in builder.toml" in found[0].message
    assert rules.check_budget(1500, 500, 1500, "USD")[0].level == "warning"


def test_confirmed_over_cap_is_a_warning():
    found = rules.check_budget(600, 500, 600, "USD")
    assert [(f.level, f.field) for f in found] == [("warning", "daily_budget")]
    assert found[0].message == "600.00 USD is ABOVE THE CAP of 500.00; confirm_budget_over_cap matches it. Approve only if you asked for this budget"
    assert rules.check_budget(600, 500, math.nan, "USD")[0].level == "error"


def test_blocked_words_match_whole_words_for_ascii():
    assert rules.check_blocked("Fast arrival times", "h", ("Rival",)) == []
    assert rules.check_blocked("Rival workouts", "h", ("Rival",))
    assert rules.check_blocked("outfit and refit", "h", ("Fit",)) == []
    assert rules.check_blocked("Pulsed on", "h", ("Pulse",)) == []
    assert rules.check_blocked("Try Pulse.", "h", ("Pulse",))
    assert rules.check_blocked("Try Rival-Box now", "h", ("rival box", "rival-box"))


def test_blocked_arabic_words_still_match_inside_prefixed_words():
    assert rules.check_blocked("والقهوة أفضل", "h", ("قهوة",))


@pytest.mark.parametrize("text", ["Call +1 555 123 456", "Call 0555 123 456"])
def test_prefixed_grouped_phone_numbers_are_flagged(text):
    assert rules.looks_like_phone(text)


@pytest.mark.parametrize(
    "text", ["Call 555 123 456", "1 500 000-2 000 000 EUR", "1 500 000 - 2 000 000 EUR"]
)
def test_grouped_numbers_and_price_ranges_are_not_phones(text):
    assert not rules.looks_like_phone(text)


@pytest.mark.parametrize("mark", ["\u061c", "\u200b", "\ufeff"])
def test_more_invisible_characters(mark):
    assert any("invisible" in m for m in _msgs(f"Fast{mark} workouts"))


BLANK_LETTERS = ["\u115f", "\u1160", "\u3164", "\uffa0", "\u2800"]  # Hangul fillers, the blank Braille pattern


@pytest.mark.parametrize("ch", BLANK_LETTERS)
def test_blank_looking_letters_count_as_invisible(ch):
    assert rules.has_invisible_characters(f"Pulse{ch}") and rules.has_invisible_characters(ch * 3)


def test_ordinary_letters_and_joiners_are_visible():
    for text in ("Pulse gym", "\ud3b4\uc2a4 \uccb4\uc721\uad00", "\u2801 braille", "\u0645\u06cc\u200c\u0631\u0648\u0645", "\U0001f469\u200d\U0001f4bb"):
        assert not rules.has_invisible_characters(text), ascii(text)


UNSAFE = ["\x00", "\x1b", "\t", "\n", "\x0b", "\x0c", "\r", "\x7f", "\x85", "\u2028", "\u2029",
          "\u202a", "\u202b", "\u202c", "\u202d", "\u202e", "\u2066", "\u2067", "\u2068", "\u2069"]


@pytest.mark.parametrize("ch", UNSAFE, ids=[f"U+{ord(c):04X}" for c in UNSAFE])
def test_unsafe_characters_are_found(ch):
    assert rules.has_unsafe_characters(f"Fast{ch}workouts")


@pytest.mark.parametrize("text", ["Fast workouts", "می\u200cروم", "Caf\u00e9", "日本", "", "a\u00a0b"])
def test_ordinary_text_and_zwnj_are_safe(text):
    assert not rules.has_unsafe_characters(text)


@pytest.mark.parametrize("ch", ["\u202e", "\u2028", "\u2029", "\x85", "\x00", "\x1b", "\x7f"])
def test_headlines_and_descriptions_reject_every_unsafe_character(ch):
    assert any("line breaks" in m for m in _msgs(f"Fast{ch}workouts", headline=True, limit=30)), repr(ch)
    assert any("line breaks" in m for m in _msgs(f"Fast{ch}workouts")), repr(ch)


def test_mentions_matches_whole_ascii_words_and_anywhere_in_other_scripts():
    assert rules.mentions("Pulse gym", "pulse") and rules.mentions("JOIN PULSE", "Pulse")
    assert not rules.mentions("Pulsed gym", "pulse") and not rules.mentions("Pulse gym", "  ")
    assert rules.mentions("بانبض", "نبض")  # a prefix attached to the word


def _repo_text_files():
    """Every file git tracks in the repo; without git, the usual text files under the repo root."""
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    try:
        listed = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, timeout=30, check=True)
        paths = [root / name for name in listed.stdout.decode("utf-8").split("\0") if name]
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
        paths = [path for pattern in ("*.md", "*.py", "*.json", "*.toml", "*.example") for path in root.rglob(pattern)
                 if not any(part.startswith(".") or part == "__pycache__" for part in path.relative_to(root).parts)]
    return root, sorted(set(paths))


def test_the_repo_holds_no_literal_bidi_controls():
    """A literal bidi control in any tracked file (code, docs, config) reorders what a reviewer reads;
    write them as \\u escapes."""
    root, paths = _repo_text_files()
    assert any(p.name == "rules.py" for p in paths) and any(p.name == "SECURITY.md" for p in paths)
    found = []
    for path in paths:
        try:
            text = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # not a text file
        found += [f"{path.relative_to(root)}:{n}" for n, line in enumerate(text.splitlines(), 1)
                  if any(ch in rules._BIDI_CONTROLS for ch in line)]
    assert found == []


def test_a_confirmation_too_large_for_a_float_is_no_confirmation():
    found = rules.check_budget(600, 500, 10**400, "USD")
    assert found[0].level == "error" and "must confirm this exact amount" in found[0].message
