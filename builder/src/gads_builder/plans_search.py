"""Search campaign plans: the model, strict parsing, every local rule and the summary the person reads.

plans.py hands every "search_campaign" plan to this module."""

from __future__ import annotations

import dataclasses
import math
import unicodedata
from dataclasses import dataclass, field

from . import rules
from .config import Config, suffix_like_domains
from .plans import (
    DESCRIPTION_LIMIT, HEADLINE_LIMIT, MAX_FREE_TEXT, MAX_NAME, RSA_DESCRIPTIONS, RSA_HEADLINES, PlanError,
    _brief_findings, _cell, _currency, _is_num, _named, _one_line, _rsa_count, _targeting, _text_section, _texts,
)
from .rules import ERROR, WARNING, Finding

BIDDING = ("MAXIMIZE_CONVERSIONS", "TARGET_CPA", "MAXIMIZE_CLICKS", "TARGET_IMPRESSION_SHARE", "MANUAL_CPC")
SMART_BIDDING = ("MAXIMIZE_CONVERSIONS", "TARGET_CPA")  # the only ones broad match may use
MAX_CPC_BIDDING = ("MAXIMIZE_CLICKS", "TARGET_IMPRESSION_SHARE", "MANUAL_CPC")  # the ones that use max_cpc
IMPRESSION_SHARE_LOCATIONS = {
    "ANYWHERE_ON_PAGE": "anywhere on the page",
    "TOP_OF_PAGE": "top of the page",
    "ABSOLUTE_TOP_OF_PAGE": "absolute top of the page",
}
MATCH_TYPES = ("EXACT", "PHRASE", "BROAD")
SNIPPET_HEADERS = (
    "Amenities", "Brands", "Courses", "Degree programs", "Destinations", "Featured hotels", "Insurance coverage",
    "Models", "Neighborhoods", "Service catalog", "Shows", "Styles", "Types",
)  # Google's English headers; a translated one is a warning (see _translated_header)
SNIPPET_HEADER = 25  # characters of a header outside the English list
TRANSLATED_HEADER = ("not one of Google's English headers; Google's check will confirm it is a valid "
                     "translated header")
MAX_AD_GROUPS = 20
MAX_KEYWORDS = 100  # per ad group
MAX_CAMPAIGN_NEGATIVES, MAX_AD_GROUP_NEGATIVES = 500, 200
MAX_LOCATIONS, MAX_LANGUAGES = 200, 30
KEYWORD_CHARS, KEYWORD_WORDS = 80, 10
KEYWORD_FORBIDDEN = frozenset('!@%,*=;<>[]{}^~|\\"')
_MATCH_MARKS = frozenset('"[]')  # how the Google Ads UI writes phrase and exact match
PATH_LIMIT = 15
SITELINK_TEXT, SITELINK_DESCRIPTION, MAX_SITELINKS = 25, 35, 20
CALLOUT_LIMIT, MAX_CALLOUTS = 25, 20
SNIPPET_VALUE, SNIPPET_VALUES, MAX_SNIPPETS = 25, (3, 10), 2
MAX_URL = 2048


@dataclass
class Keyword:
    text: str
    match: str  # EXACT | PHRASE | BROAD


@dataclass
class SearchAd:
    headlines: list[str]
    descriptions: list[str]
    final_url: str
    path1: str = ""
    path2: str = ""


@dataclass
class SearchAdGroup:
    name: str
    keywords: list[Keyword]
    ad: SearchAd
    negative_keywords: list[Keyword] = field(default_factory=list)
    cpc_bid: float | None = None  # MANUAL_CPC only; otherwise the campaign's max_cpc


@dataclass
class Sitelink:
    text: str
    url: str
    desc1: str = ""
    desc2: str = ""


@dataclass
class Snippet:
    header: str
    values: list[str]


@dataclass
class ImpressionShare:
    location: str  # ANYWHERE_ON_PAGE | TOP_OF_PAGE | ABSOLUTE_TOP_OF_PAGE
    percent: float  # 1-100


@dataclass
class SearchCampaignPlan:
    name: str
    daily_budget: float
    currency: str
    bidding: str
    locations: list[str]
    languages: list[str]
    ad_groups: list[SearchAdGroup]
    target_cpa: float | None = None
    max_cpc: float | None = None
    impression_share: ImpressionShare | None = None
    search_partners: bool = False  # Google Search is always on; the Display Network always off
    negative_keywords: list[Keyword] = field(default_factory=list)
    sitelinks: list[Sitelink] = field(default_factory=list)
    callouts: list[str] = field(default_factory=list)
    snippets: list[Snippet] = field(default_factory=list)
    confirm_budget_over_cap: float | None = None
    template: str | None = None
    brief: dict = field(default_factory=dict)  # creative brief, "sources", "names"; informational
    kind: str = "search_campaign"


# ------------------------------------------------------------------- parsing
# Strict: unknown fields and wrong types are refused, so nothing in the plan is silently ignored.


def _str(value, where: str) -> str:
    if not isinstance(value, str):
        raise PlanError(f"{where} must be text")
    return value


def _text(value, where: str) -> str:
    """Ad and keyword text, NFC-normalised like the App plan's texts."""
    return unicodedata.normalize("NFC", _str(value, where))


def _float(value, where: str) -> float:
    """Every amount becomes a float here, so a huge int (such as 10**400) never reaches the checks.
    NaN and infinity stay floats; the rules report them."""
    try:
        return float(value)
    except OverflowError:
        raise PlanError(f"{where} is too large") from None


def _num(value, where: str) -> float:
    if not _is_num(value):
        raise PlanError(f"{where} must be a number")
    return _float(value, where)


def _opt_num(value, where: str) -> float | None:
    if value is not None and not _is_num(value):
        raise PlanError(f"{where} must be a number or null")
    return None if value is None else _float(value, where)


def _bool(value, where: str) -> bool:
    if not isinstance(value, bool):
        raise PlanError(f"{where} must be true or false")
    return value


def _dict(value, where: str) -> dict:
    if not isinstance(value, dict):
        raise PlanError(f"{where} must be an object")
    return value


def _optional(check):
    return lambda value, where: None if value is None else check(value, where)


def _list_of(check):
    def parse(value, where: str) -> list:
        if not isinstance(value, list):
            raise PlanError(f"{where} must be a list")
        return [check(item, f"{where}[{i}]") for i, item in enumerate(value)]

    return parse


def _object(cls, spec: dict):
    required = [f.name for f in dataclasses.fields(cls)
                if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING]

    def parse(value, where: str):
        if not isinstance(value, dict):
            raise PlanError(f"{where} must be an object with {', '.join(spec)}")
        extra = set(value) - set(spec)
        if extra:
            raise PlanError(f"{where or 'the plan'} has unknown field(s): {', '.join(sorted(map(str, extra)))}")
        missing = [name for name in required if name not in value]
        if missing:
            s = "s" if len(missing) > 1 else ""
            raise PlanError(f"{where or 'the plan'}: missing field{s} {', '.join(map(repr, missing))}")
        fields = {key: spec[key](item, f"{where}.{key}" if where else key) for key, item in value.items()}
        return cls(**fields)

    return parse


_KEYWORD = _object(Keyword, {"text": _text, "match": _str})
_AD = _object(SearchAd, {"headlines": _list_of(_text), "descriptions": _list_of(_text), "final_url": _str,
                         "path1": _text, "path2": _text})
_AD_GROUP = _object(SearchAdGroup, {"name": _str, "keywords": _list_of(_KEYWORD), "ad": _AD,
                                    "negative_keywords": _list_of(_KEYWORD), "cpc_bid": _opt_num})
_PLAN = _object(SearchCampaignPlan, {
    "kind": _str, "name": _str, "daily_budget": _num, "currency": _str, "bidding": _str,
    "locations": _list_of(_str), "languages": _list_of(_str), "ad_groups": _list_of(_AD_GROUP),
    "target_cpa": _opt_num, "max_cpc": _opt_num,
    "impression_share": _optional(_object(ImpressionShare, {"location": _str, "percent": _num})),
    "search_partners": _bool, "negative_keywords": _list_of(_KEYWORD),
    "sitelinks": _list_of(_object(Sitelink, {"text": _text, "url": _str, "desc1": _text, "desc2": _text})),
    "callouts": _list_of(_text),
    "snippets": _list_of(_object(Snippet, {"header": _str, "values": _list_of(_text)})),
    "confirm_budget_over_cap": _opt_num, "template": _optional(_str), "brief": _dict,
})


def search_plan_from_dict(data: dict) -> SearchCampaignPlan:
    return _PLAN(data, "")


# ---------------------------------------------------------------- validation


def _key(text: str) -> str:
    """Compare texts ignoring case, Unicode form and spacing."""
    return " ".join(unicodedata.normalize("NFC", text).casefold().split())


def _positive(value) -> bool:
    value = rules.as_float(value)
    return value is not None and math.isfinite(value) and value > 0


def _cents(value: float, f: str) -> list[Finding]:
    """A positive amount the person typed: at least 0.01 and in whole cents, so nothing is rounded
    silently on the way to micros."""
    if value < 0.01:
        return [Finding(ERROR, f, "must be at least 0.01")]
    if round(value, 2) != value:
        return [Finding(ERROR, f, "must be in whole cents (at most 2 decimals)")]
    return []


def validate_search(p: SearchCampaignPlan, cfg: Config) -> list[Finding]:
    out: list[Finding] = []
    if not p.name.strip():
        out.append(Finding(ERROR, "name", "is empty"))
    out += _one_line(p.name, "name", MAX_NAME, name=True)
    out += _one_line(p.template or "", "template", MAX_FREE_TEXT)
    out += _brief_findings(p.brief)
    out += _currency(p.currency, cfg)
    out += rules.check_budget(p.daily_budget, cfg.daily_budget_cap, p.confirm_budget_over_cap, cfg.currency)
    if _positive(p.daily_budget):
        out += _cents(p.daily_budget, "daily_budget")
    out += _bidding(p, cfg)
    out += _targeting(p.locations, p.languages)
    for name, items, limit in (("locations", p.locations, MAX_LOCATIONS), ("languages", p.languages, MAX_LANGUAGES)):
        if len(items) > limit:
            out.append(Finding(ERROR, name, f"{len(items)} given; the maximum is {limit}"))
    positives = [(f"ad_groups[{g}].keywords[{k}]", kw)
                 for g, group in enumerate(p.ad_groups) for k, kw in enumerate(group.keywords)]
    out += _negatives(p.negative_keywords, "negative_keywords", positives, MAX_CAMPAIGN_NEGATIVES)
    out += _ad_groups(p, cfg)
    out += _brand_mix(p, cfg)
    out += _sitelinks(p.sitelinks, cfg)
    out += _callouts(p.callouts, cfg)
    out += _snippets(p.snippets, cfg)
    out += [Finding(WARNING, "domains", f"builder.toml allows {d!r}, which looks like a public suffix: every site "
                                        "under it would be allowed") for d in suffix_like_domains(cfg.domains)]
    return out


def _bid(value: float, f: str, p: SearchCampaignPlan) -> list[Finding]:
    """A max CPC or ad group bid: whole cents, and never more than the whole day's budget."""
    out = _cents(value, f)
    if _positive(p.daily_budget) and value > p.daily_budget:
        out.append(Finding(ERROR, f, "is higher than the daily budget"))
    return out


def _bidding(p: SearchCampaignPlan, cfg: Config) -> list[Finding]:
    out: list[Finding] = []
    err = lambda f, m: out.append(Finding(ERROR, f, m))  # noqa: E731
    if p.bidding not in BIDDING:
        err("bidding", f"must be one of {', '.join(BIDDING)}")
        return out
    if p.target_cpa is not None:
        if p.bidding not in SMART_BIDDING:
            err("target_cpa", "is only used with MAXIMIZE_CONVERSIONS or TARGET_CPA bidding")
        elif not _positive(p.target_cpa):
            err("target_cpa", "must be a positive number")
        else:
            out += _cents(p.target_cpa, "target_cpa")
            cap = cfg.daily_budget_cap
            if p.target_cpa > 3 * cap:
                err("target_cpa", f"{p.target_cpa:,.2f} is more than 3x the daily budget cap of {cap:,.2f}")
            elif _positive(p.daily_budget) and p.target_cpa > p.daily_budget:
                out.append(Finding(WARNING, "target_cpa",
                                   "is higher than the daily budget; the budget may not cover a single conversion"))
    elif p.bidding == "TARGET_CPA":
        err("target_cpa", "is required for TARGET_CPA bidding")
    if p.max_cpc is not None:
        if p.bidding not in MAX_CPC_BIDDING:
            err("max_cpc", "is only used with MAXIMIZE_CLICKS, TARGET_IMPRESSION_SHARE or MANUAL_CPC bidding")
        elif not _positive(p.max_cpc):
            err("max_cpc", "must be a positive number")
        else:
            out += _bid(p.max_cpc, "max_cpc", p)
    elif p.bidding == "MANUAL_CPC":
        err("max_cpc", "is required for MANUAL_CPC bidding: it is the default ad group bid")
    elif p.bidding == "TARGET_IMPRESSION_SHARE":
        err("max_cpc", "is required for TARGET_IMPRESSION_SHARE bidding: it is the bid ceiling")
    share = p.impression_share
    if p.bidding == "TARGET_IMPRESSION_SHARE":
        if share is None:
            err("impression_share", "is required for TARGET_IMPRESSION_SHARE bidding: {location, percent}")
        else:
            if share.location not in IMPRESSION_SHARE_LOCATIONS:
                err("impression_share.location", f"must be one of {', '.join(IMPRESSION_SHARE_LOCATIONS)}")
            if not (math.isfinite(share.percent) and 1 <= share.percent <= 100):
                err("impression_share.percent", "must be from 1 to 100")
    elif share is not None:
        err("impression_share", "is only used with TARGET_IMPRESSION_SHARE bidding")
    for i, group in enumerate(p.ad_groups):
        if group.cpc_bid is None:
            continue
        if p.bidding != "MANUAL_CPC":
            err(f"ad_groups[{i}].cpc_bid", "is only used with MANUAL_CPC bidding")
        elif not _positive(group.cpc_bid):
            err(f"ad_groups[{i}].cpc_bid", "must be a positive number")
        else:
            out += _bid(group.cpc_bid, f"ad_groups[{i}].cpc_bid", p)
    return out


def _keyword(kw: Keyword, f: str) -> list[Finding]:
    out: list[Finding] = []
    if kw.match not in MATCH_TYPES:
        out.append(Finding(ERROR, f"{f}.match", "must be EXACT, PHRASE or BROAD"))
    text = kw.text
    if not text.strip():
        return out + [Finding(ERROR, f"{f}.text", "is empty")]
    if rules.has_unsafe_characters(text):
        out.append(Finding(ERROR, f"{f}.text", rules.UNSAFE_MESSAGE))
    elif rules.has_invisible_characters(text):
        out.append(Finding(ERROR, f"{f}.text", "contains invisible characters"))
    if text.startswith(("+", "-")):
        out.append(Finding(ERROR, f"{f}.text", "may not start with + or -"))
    if text != text.strip() or "  " in text:
        out.append(Finding(ERROR, f"{f}.text", "has leading/trailing or double spaces"))
    if len(text) > KEYWORD_CHARS:
        out.append(Finding(ERROR, f"{f}.text", f"is {len(text)} characters; the limit is {KEYWORD_CHARS}"))
    words = len(text.split())
    if words > KEYWORD_WORDS:
        out.append(Finding(ERROR, f"{f}.text", f"has {words} words; the limit is {KEYWORD_WORDS}"))
    bad = sorted({ch for ch in text if ch in KEYWORD_FORBIDDEN})
    if bad:
        hint = "; set the match type instead of quotes or brackets" if _MATCH_MARKS & set(bad) else ""
        out.append(Finding(ERROR, f"{f}.text", f"may not contain {' '.join(bad)}{hint}"))
    return out


def _keywords(keywords: list[Keyword], f: str, bidding: str) -> list[Finding]:
    out: list[Finding] = []
    n = len(keywords)
    if n == 0:
        out.append(Finding(ERROR, f, "add at least one keyword"))
    elif n > MAX_KEYWORDS:
        out.append(Finding(ERROR, f, f"{n} given; the maximum is {MAX_KEYWORDS}"))
    elif n < 5:
        out.append(Finding(WARNING, f, f"only {n}; aim for 5 to 20 tightly related keywords"))
    elif n > 20:
        out.append(Finding(WARNING, f, f"{n} keywords; more than 20 usually means several intents: split the ad group"))
    seen: dict[tuple[str, str], int] = {}
    for i, kw in enumerate(keywords):
        out += _keyword(kw, f"{f}[{i}]")
        if kw.match == "BROAD" and bidding not in SMART_BIDDING:
            out.append(Finding(ERROR, f"{f}[{i}].match", "broad match needs MAXIMIZE_CONVERSIONS or TARGET_CPA bidding"))
        key = (_key(kw.text), kw.match)
        if key in seen:
            out.append(Finding(ERROR, f"{f}[{i}]", f"duplicates {f}[{seen[key]}]"))
        else:
            seen[key] = i
    return out


def _words(text: str) -> list[str]:
    """A keyword's words as the negative-keyword check compares them: NFKC, case-folded, split on
    whitespace, like rules.mentions."""
    return unicodedata.normalize("NFKC", text).casefold().split()


def _blocks(match: str, negative: list[str], words: list[str]) -> bool:
    """A negative keyword (its match type and words) blocks a search for exactly these words:
    EXACT the same words, PHRASE its words together and in order, BROAD all of its words."""
    if not negative:
        return False
    if match == "EXACT":
        return negative == words
    if match == "PHRASE":
        n = len(negative)
        return any(words[i:i + n] == negative for i in range(len(words) - n + 1))
    if match == "BROAD":
        return set(negative) <= set(words)
    return False


def _negatives(negatives: list[Keyword], f: str, positives: list[tuple[str, Keyword]], limit: int) -> list[Finding]:
    """Negative keywords: at most `limit`, each a valid keyword, none blocking a positive keyword it
    applies to (positives are (where, keyword) pairs)."""
    out: list[Finding] = []
    if len(negatives) > limit:
        out.append(Finding(ERROR, f, f"{len(negatives)} given; the maximum is {limit}"))
    targets = [(where, kw, _words(kw.text)) for where, kw in positives]
    targets = [(where, kw, words, frozenset(words)) for where, kw, words in targets]
    seen: dict[tuple[str, str], int] = {}
    for i, kw in enumerate(negatives):
        out += _keyword(kw, f"{f}[{i}]")
        negative = _words(kw.text)
        needed = frozenset(negative)  # every match type needs all of the negative's words: a quick first test
        out += [Finding(ERROR, f"{f}[{i}]", f"{kw.text!r} ({kw.match}) blocks the keyword {pos.text!r} ({where})")
                for where, pos, words, present in targets if needed <= present and _blocks(kw.match, negative, words)]
        key = (_key(kw.text), kw.match)
        if key in seen:
            out.append(Finding(ERROR, f"{f}[{i}]", f"duplicates {f}[{seen[key]}]"))
        else:
            seen[key] = i
    return out


def _url(url: str, f: str, cfg: Config) -> list[Finding]:
    if not url:
        return [Finding(ERROR, f, "add an https URL on one of your domains")]
    out = _one_line(url, f, MAX_URL)
    if not cfg.allows_url(url):
        allowed = ", ".join(cfg.domains) or "none yet: add domains to builder.toml"
        out.append(Finding(ERROR, f, f"must be an https URL on an allowed domain ({allowed})"))
    return out


def _rsa_text_count(f: str, n: int, limits: tuple[int, int]) -> list[Finding]:
    """An error outside the limits (plans._rsa_count); a warning below the most, since a full ad gets
    the best Ad Strength."""
    high = limits[1]
    errors = _rsa_count(f, n, limits, shown="{n} given")
    if errors:
        return errors
    if n < high:
        return [Finding(WARNING, f, f"only {n} of {high}; fill all {high} for the best Ad Strength")]
    return []


def _ad(ad: SearchAd, f: str, cfg: Config) -> list[Finding]:
    out: list[Finding] = []
    out += _rsa_text_count(f"{f}.headlines", len(ad.headlines), RSA_HEADLINES)
    out += _texts(ad.headlines, f"{f}.headlines", HEADLINE_LIMIT, headline=True, blocked=cfg.blocked_words)
    out += rules.find_duplicates(ad.headlines, f"{f}.headlines")
    out += _rsa_text_count(f"{f}.descriptions", len(ad.descriptions), RSA_DESCRIPTIONS)
    out += _texts(ad.descriptions, f"{f}.descriptions", DESCRIPTION_LIMIT, headline=False, blocked=cfg.blocked_words)
    out += rules.find_duplicates(ad.descriptions, f"{f}.descriptions")
    for name in ("path1", "path2"):
        value = getattr(ad, name)
        if value:
            out += rules.check_text(value, f"{f}.{name}", PATH_LIMIT, headline=True)
            out += rules.check_blocked(value, f"{f}.{name}", cfg.blocked_words)
    if ad.path2 and not ad.path1:
        out.append(Finding(ERROR, f"{f}.path2", "needs path1"))
    out += _url(ad.final_url, f"{f}.final_url", cfg)
    return out


def _relevance(group: SearchAdGroup, f: str) -> list[Finding]:
    """Ad relevance (part of Quality Score): the ad group's main keyword is in 2 to 3 headlines."""
    if not group.keywords or not group.ad.headlines:
        return []
    main = group.keywords[0].text
    if not main.strip():
        return []
    n = sum(rules.mentions(h, main) for h in group.ad.headlines)  # whole words: "spin" is not in "Spinach"
    if 2 <= n <= 3:
        return []
    return [Finding(WARNING, f"{f}.ad.headlines",
                    f"the main keyword '{main}' is in {n} of the headlines; put it in 2 to 3 for ad relevance")]


def _ad_groups(p: SearchCampaignPlan, cfg: Config) -> list[Finding]:
    out: list[Finding] = []
    n = len(p.ad_groups)
    if not 1 <= n <= MAX_AD_GROUPS:
        out.append(Finding(ERROR, "ad_groups", f"{n} given; use 1 to {MAX_AD_GROUPS}"))
    seen: dict[str, int] = {}
    for i, group in enumerate(p.ad_groups):
        f = f"ad_groups[{i}]"
        if not group.name.strip():
            out.append(Finding(ERROR, f"{f}.name", "is empty"))
        out += _one_line(group.name, f"{f}.name", MAX_NAME, name=True)
        key = _key(group.name)
        if key in seen:
            out.append(Finding(ERROR, f"{f}.name", f"duplicates ad_groups[{seen[key]}].name"))
        else:
            seen[key] = i
        out += _keywords(group.keywords, f"{f}.keywords", p.bidding)
        own = [(f"{f}.keywords[{k}]", kw) for k, kw in enumerate(group.keywords)]
        out += _negatives(group.negative_keywords, f"{f}.negative_keywords", own, MAX_AD_GROUP_NEGATIVES)
        out += _ad(group.ad, f"{f}.ad", cfg)
        out += _relevance(group, f)
    return out


def _brand_mix(p: SearchCampaignPlan, cfg: Config) -> list[Finding]:
    if not cfg.brand_words:
        return []
    brand = sum(
        any(rules.mentions(kw.text, word) for kw in group.keywords for word in cfg.brand_words)
        for group in p.ad_groups
    )
    if 0 < brand < len(p.ad_groups):
        return [Finding(WARNING, "ad_groups",
                        f"{brand} brand and {len(p.ad_groups) - brand} non-brand ad groups; "
                        "keep brand keywords in a campaign of their own")]
    return []


def _short_texts(texts: list[str], f: str, limit: int, cfg: Config) -> list[Finding]:
    out = _texts(texts, f, limit, headline=True, blocked=cfg.blocked_words)
    return out + rules.find_duplicates(texts, f)


def _sitelinks(links: list[Sitelink], cfg: Config) -> list[Finding]:
    out: list[Finding] = []
    if len(links) > MAX_SITELINKS:
        out.append(Finding(ERROR, "sitelinks", f"{len(links)} given; the maximum is {MAX_SITELINKS}"))
    elif len(links) < 4:
        out.append(Finding(WARNING, "sitelinks", f"only {len(links)}; add at least 4 for expected CTR"))
    pages: dict[str, int] = {}
    for i, link in enumerate(links):
        f = f"sitelinks[{i}]"
        if link.url in pages:
            out.append(Finding(WARNING, f"{f}.url", f"is the same page as sitelinks[{pages[link.url]}]; "
                                                    "give each sitelink a page of its own"))
        elif link.url:
            pages[link.url] = i
        out += rules.check_text(link.text, f"{f}.text", SITELINK_TEXT, headline=True)
        out += rules.check_blocked(link.text, f"{f}.text", cfg.blocked_words)
        if bool(link.desc1) != bool(link.desc2):
            out.append(Finding(ERROR, f, "give both desc1 and desc2, or neither"))
        for name in ("desc1", "desc2"):
            value = getattr(link, name)
            if value:
                out += rules.check_text(value, f"{f}.{name}", SITELINK_DESCRIPTION, headline=False)
                out += rules.check_blocked(value, f"{f}.{name}", cfg.blocked_words)
        out += _url(link.url, f"{f}.url", cfg)
    return out + rules.find_duplicates([link.text for link in links], "sitelinks")


def _callouts(callouts: list[str], cfg: Config) -> list[Finding]:
    out: list[Finding] = []
    if len(callouts) > MAX_CALLOUTS:
        out.append(Finding(ERROR, "callouts", f"{len(callouts)} given; the maximum is {MAX_CALLOUTS}"))
    elif len(callouts) < 4:
        out.append(Finding(WARNING, "callouts", f"only {len(callouts)}; add at least 4 for expected CTR"))
    return out + _short_texts(callouts, "callouts", CALLOUT_LIMIT, cfg)


def _translated_header(header: str, f: str) -> list[Finding]:
    """A header outside Google's English list. Google also accepts its translated headers (such as the
    Arabic for Services), so a safe one-line header is only a warning: check_plan asks Google. The summary
    still shows it quoted (see _choice)."""
    if not header.strip():
        return [Finding(ERROR, f, "is empty")]
    return _one_line(header, f, SNIPPET_HEADER) or [Finding(WARNING, f, TRANSLATED_HEADER)]


def _snippets(snippets: list[Snippet], cfg: Config) -> list[Finding]:
    out: list[Finding] = []
    if len(snippets) > MAX_SNIPPETS:
        out.append(Finding(ERROR, "snippets", f"{len(snippets)} given; the maximum is {MAX_SNIPPETS}"))
    elif not snippets:
        out.append(Finding(WARNING, "snippets", "none; add 1 or 2 structured snippets for expected CTR"))
    low, high = SNIPPET_VALUES
    for i, snippet in enumerate(snippets):
        f = f"snippets[{i}]"
        if snippet.header not in SNIPPET_HEADERS:
            out += _translated_header(snippet.header, f"{f}.header")
        if not low <= len(snippet.values) <= high:
            out.append(Finding(ERROR, f"{f}.values", f"{len(snippet.values)} given; use {low} to {high}"))
        out += _short_texts(snippet.values, f"{f}.values", SNIPPET_VALUE, cfg)
    return out


# ------------------------------------------------------------------- summary


def _money(value, currency: str) -> str:
    return "-" if value is None else f"{value:,.2f} {currency}"


def _choice(value: str, choices) -> str:
    """A choice as the summary shows it: plainly if it is one of `choices`, otherwise quoted with repr(),
    so an invalid value never starts a line of its own."""
    return value if value in choices else repr(value)


def bidding_label(p: SearchCampaignPlan) -> str:
    money = lambda v: _money(v, p.currency)  # noqa: E731
    if p.bidding == "MAXIMIZE_CONVERSIONS":
        return "Maximize conversions" + (f", target cost per conversion {money(p.target_cpa)}" if p.target_cpa else "")
    if p.bidding == "TARGET_CPA":
        return f"Target cost per conversion {money(p.target_cpa)}"
    if p.bidding == "MAXIMIZE_CLICKS":
        return "Maximize clicks" + (f", max CPC {money(p.max_cpc)}" if p.max_cpc else "")
    if p.bidding == "TARGET_IMPRESSION_SHARE" and p.impression_share is not None:
        share = p.impression_share
        where = IMPRESSION_SHARE_LOCATIONS.get(share.location, repr(share.location))
        return f"Target impression share {share.percent:g}% {where}, max CPC {money(p.max_cpc)}"
    if p.bidding == "MANUAL_CPC":
        return f"Manual CPC, default bid {money(p.max_cpc)}"
    return _choice(p.bidding, BIDDING)


def _keyword_section(title: str, keywords: list[Keyword], level: str = "###") -> list[str]:
    lines = [f"{level} {title} ({len(keywords)})"]
    lines += [f"- {kw.text} · {_choice(kw.match, MATCH_TYPES)}" for kw in keywords] or ["- none"]
    return lines + [""]


def summary_body(p: SearchCampaignPlan, head: list[str], cfg: Config) -> list[str]:
    """Everything above the approval arguments. Deterministic, like the rest of the summary."""
    names = p.brief.get("names", {})
    names = names if isinstance(names, dict) else {}
    sources = p.brief.get("sources", {})
    sources = sources if isinstance(sources, dict) else {}
    show = lambda xs: ", ".join(_named(x, names) for x in xs) or "-"  # noqa: E731
    networks = "Google Search and search partners" if p.search_partners else "Google Search only"
    lines = [
        f"# {p.name}",
        "",
        *head,
        "**Will be created PAUSED.** Nothing changes until the person approves.",
        "",
        "| Setting | Value |",
        "|---|---|",
        f"| Account | {_cell(cfg.customer_id)} |",
        "| Type | Search campaign |",
        f"| Bidding | {_cell(bidding_label(p))} |",
        f"| Networks | {_cell(networks)}; never the Display Network |",
        f"| Daily budget | {_cell(_money(p.daily_budget, p.currency))} |",
        f"| Locations | {_cell(show(p.locations))} |",
        f"| Languages | {_cell(show(p.languages))} |",
        "| Location option | people in or regularly in your locations |",
        "| AI Max | off |",
        f"| Ad groups | {len(p.ad_groups)} |",
        f"| Copied from | {_cell(p.template or '-')} |",
        "",
    ]
    for i, group in enumerate(p.ad_groups, 1):
        path = "/".join(x for x in (group.ad.path1, group.ad.path2) if x)
        lines += [f"## Ad group {i}: {group.name}", "", f"- Final URL: {group.ad.final_url}",
                  f"- Display path: /{path}" if path else "- Display path: none"]
        if p.bidding == "MANUAL_CPC":
            bid = group.cpc_bid if group.cpc_bid is not None else p.max_cpc
            lines.append(f"- Max CPC: {_money(bid, p.currency)}")
        lines.append("")
        lines += _keyword_section("Keywords", group.keywords)
        lines += _keyword_section("Negative keywords", group.negative_keywords)
        lines += _text_section("Headlines", group.ad.headlines, HEADLINE_LIMIT, sources, level="###")
        lines += _text_section("Descriptions", group.ad.descriptions, DESCRIPTION_LIMIT, sources, level="###")
    lines += _keyword_section("Campaign negative keywords", p.negative_keywords, level="##")
    lines.append(f"## Sitelinks ({len(p.sitelinks)})")
    for i, link in enumerate(p.sitelinks, 1):
        lines.append(f"{i}. {link.text} · {rules.display_length(link.text)}/{SITELINK_TEXT} → {link.url}")
        lines += [f"   - {d} · {rules.display_length(d)}/{SITELINK_DESCRIPTION}" for d in (link.desc1, link.desc2) if d]
    if not p.sitelinks:
        lines.append("- none")
    lines.append("")
    lines += _text_section("Callouts", p.callouts, CALLOUT_LIMIT, {})
    lines += [f"## Structured snippets ({len(p.snippets)})",
              *([f"- {_choice(s.header, SNIPPET_HEADERS)}: {', '.join(s.values)}" for s in p.snippets] or ["- none"]),
              ""]
    return lines
