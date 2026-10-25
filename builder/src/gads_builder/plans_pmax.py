"""Performance Max campaign plans (website goals): the model, strict parsing, every local rule and the summary
the person reads.

plans.py hands every "pmax_campaign" plan to this module. Website destinations only: app goals stay in App
campaigns. Brand guidelines are on, so the business name and the logos belong to the campaign, not to an
asset group (API v26)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from . import rules
from .config import Config, suffix_like_domains
from .plans import (
    DESCRIPTION_LIMIT, HEADLINE_LIMIT, MAX_FREE_TEXT, MAX_NAME, ImageRef, VideoRef, _brief_findings, _cell,
    _check_ref_list, _currency, _images, _named, _one_line, _targeting, _text_section, _texts, _videos,
    budget_name, image_asset_name, shown_file, video_asset_name,
)
from .plans_demand_gen import (
    CALLS_TO_ACTION, CONVERSION_CATEGORIES, CONVERSION_ORIGINS, ConversionGoal, _business_name, _image_list, goal_label,
)
from .plans_search import (
    MAX_LANGUAGES, MAX_LOCATIONS, PATH_LIMIT, _cents, _choice, _dict, _key, _list_of, _money, _num, _object,
    _opt_num, _optional, _positive, _str, _text, _url,
)
from .rules import ERROR, WARNING, Finding

BIDDING = ("MAXIMIZE_CONVERSIONS", "MAXIMIZE_CONVERSION_VALUE")  # the only two PMax takes (API v26)
# Campaign.asset_automation_settings the compiler opts out of (AssetAutomationTypeEnum), and what the summary
# calls them. GENERATE_IMAGE_EXTRACTION follows the account's setting and only the Google Ads UI changes it.
AUTOMATIONS = {
    "FINAL_URL_EXPANSION_TEXT_ASSET_AUTOMATION": "final URL expansion",
    "TEXT_ASSET_AUTOMATION": "generated headlines and descriptions",
    "GENERATE_ENHANCED_YOUTUBE_VIDEOS": "enhanced (vertical and shorter) videos",
    "GENERATE_IMAGE_ENHANCEMENT": "image cropping and enhancement",
    "AUTOMATED_VIDEO_CRAWL": "videos added from your landing page, social profiles and YouTube channel",
}
GENERATED_LABEL = "off: " + ", ".join(AUTOMATIONS.values())
LONG_HEADLINE_LIMIT, BUSINESS_NAME_LIMIT, SHORT_DESCRIPTION = 90, 25, 60
HEADLINES, LONG_HEADLINES, DESCRIPTIONS = (3, 15), (1, 5), (2, 5)  # (fewest, most) per asset group
LANDSCAPE, SQUARE, PORTRAIT = (1, 20), (1, 20), (0, 20)
VIDEOS = (1, 15)  # at least one: with none, Google makes videos from the images and text
LOGOS, LANDSCAPE_LOGOS = (1, 5), (0, 5)
# Google's Performance Max limits across lists (Google Ads Help: Performance Max asset specs): 20 images per asset
# group, the three shapes together, and 5 logos, square and landscape together (on the campaign: brand guidelines)
MAX_GROUP_IMAGES, MAX_ALL_LOGOS = 20, 5
MAX_ASSET_GROUPS = 5  # the builder's own limit (Google allows 100)
ASSET_GROUP_NAME = 128  # Google's limit for an asset group's name
SEARCH_THEMES, SEARCH_THEME_CHARS, SEARCH_THEME_WORDS = 25, 80, 10
LANDSCAPE_LOGO_RATIO, LANDSCAPE_LOGO_MIN = 4.0, (512, 128)
IMAGE_LISTS = (  # (plan field, shape, what the summary calls it) of an asset group
    ("marketing_images", "landscape", "Landscape images (1.91:1)"),
    ("square_marketing_images", "square", "Square images (1:1)"),
    ("portrait_marketing_images", "portrait", "Portrait images (4:5)"),
)
_AUDIENCE = re.compile(r"customers/([0-9]{10})/audiences/[0-9]+")


@dataclass
class PmaxAssetGroup:
    """One asset group: its own final URL, text, images, videos and signals."""
    name: str
    final_url: str
    headlines: list[str]
    long_headlines: list[str]
    descriptions: list[str]
    marketing_images: list[ImageRef]  # landscape 1.91:1
    square_marketing_images: list[ImageRef]
    videos: list[VideoRef]
    portrait_marketing_images: list[ImageRef] = field(default_factory=list)
    path1: str = ""
    path2: str = ""
    call_to_action: str | None = None  # CallToActionTypeEnum name; None lets Google choose
    audience_signal: str | None = None  # an existing account-wide audience: customers/<id>/audiences/<id>
    search_themes: list[str] = field(default_factory=list)


@dataclass
class PmaxCampaignPlan:
    name: str
    daily_budget: float
    currency: str
    bidding: str
    conversion_goals: list[ConversionGoal]
    locations: list[str]
    languages: list[str]
    business_name: str
    logos: list[ImageRef]
    asset_groups: list[PmaxAssetGroup]
    landscape_logos: list[ImageRef] = field(default_factory=list)
    target_cpa: float | None = None  # MAXIMIZE_CONVERSIONS only
    target_roas: float | None = None  # MAXIMIZE_CONVERSION_VALUE only; 3.5 means 350%
    confirm_budget_over_cap: float | None = None
    template: str | None = None
    brief: dict = field(default_factory=dict)  # creative brief, "sources", "names"; informational
    kind: str = "pmax_campaign"


# ------------------------------------------------------------------- parsing
# Strict, like plans_search: unknown fields and wrong types are refused.


def _image_refs(value, where: str) -> list[ImageRef]:
    return _check_ref_list(where, value, ImageRef, ("file", "asset"))


def _video_refs(value, where: str) -> list[VideoRef]:
    return _check_ref_list(where, value, VideoRef, ("asset", "youtube_id"))


_TEXTS = _list_of(_text)
_ASSET_GROUP = _object(PmaxAssetGroup, {
    "name": _str, "final_url": _str, "headlines": _TEXTS, "long_headlines": _TEXTS, "descriptions": _TEXTS,
    "marketing_images": _image_refs, "square_marketing_images": _image_refs, "videos": _video_refs,
    "portrait_marketing_images": _image_refs, "path1": _text, "path2": _text, "call_to_action": _optional(_str),
    "audience_signal": _optional(_str), "search_themes": _TEXTS,
})
_PLAN = _object(PmaxCampaignPlan, {
    "kind": _str, "name": _str, "daily_budget": _num, "currency": _str, "bidding": _str,
    "conversion_goals": _list_of(_object(ConversionGoal, {"category": _str, "origin": _str})),
    "locations": _list_of(_str), "languages": _list_of(_str), "business_name": _text, "logos": _image_refs,
    "asset_groups": _list_of(_ASSET_GROUP), "landscape_logos": _image_refs, "target_cpa": _opt_num,
    "target_roas": _opt_num, "confirm_budget_over_cap": _opt_num, "template": _optional(_str), "brief": _dict,
})


def pmax_plan_from_dict(data: dict) -> PmaxCampaignPlan:
    return _PLAN(data, "")


# --------------------------------------------------------- what gets created


def _file_key(file: str) -> str:
    return str(Path(file).expanduser().resolve())


def image_refs(plan: PmaxCampaignPlan) -> list[ImageRef]:
    """Every image reference in the plan, in order: the logos, the landscape logos, then each asset group's
    IMAGE_LISTS."""
    return [*plan.logos, *plan.landscape_logos,
            *(ref for group in plan.asset_groups for name, _, _ in IMAGE_LISTS for ref in getattr(group, name))]


def new_image_files(plan: PmaxCampaignPlan) -> list[str]:
    """The new image files, each once (by resolved path), in the order they first appear."""
    seen: dict[str, str] = {}
    for ref in image_refs(plan):
        if ref.file and not ref.asset:
            seen.setdefault(_file_key(ref.file), ref.file)
    return list(seen.values())


def new_youtube_ids(plan: PmaxCampaignPlan) -> list[str]:
    """YouTube video IDs to create video assets for, each once, in order."""
    ids = [v.youtube_id for group in plan.asset_groups for v in group.videos if v.youtube_id and not v.asset]
    return list(dict.fromkeys(ids))


def calls_to_action(plan: PmaxCampaignPlan) -> list[str]:
    """The call-to-action assets to create, each once, in order."""
    return list(dict.fromkeys(g.call_to_action for g in plan.asset_groups if g.call_to_action))


def text_assets(plan: PmaxCampaignPlan) -> list[str]:
    """The text assets to create, each distinct text once, in order: the business name, then each asset
    group's headlines, long headlines and descriptions. PMax links text as Asset objects, not inline text."""
    texts = [plan.business_name]
    for group in plan.asset_groups:
        texts += [*group.headlines, *group.long_headlines, *group.descriptions]
    return list(dict.fromkeys(texts))


def image_files(plan: PmaxCampaignPlan) -> list[Path]:
    return [Path(f).expanduser() for f in new_image_files(plan)]


def derived_names(plan: PmaxCampaignPlan) -> list[tuple[str, str, str]]:
    """(plan field, what, name) for every name the compiler makes; asset group names are in the plan."""
    out = [("name", "budget", budget_name(plan.name))]
    out += [("asset_groups", "asset", image_asset_name(plan.name, Path(f).expanduser(), "0" * 8))
            for f in new_image_files(plan)]
    out += [("asset_groups", "asset", video_asset_name(plan.name, y)) for y in new_youtube_ids(plan)]
    return out


# ---------------------------------------------------------------- validation


def validate_pmax(p: PmaxCampaignPlan, cfg: Config) -> list[Finding]:
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
    out += _goals(p.conversion_goals)
    out += _targeting(p.locations, p.languages)  # no location: an error
    for name, items, limit in (("locations", p.locations, MAX_LOCATIONS), ("languages", p.languages, MAX_LANGUAGES)):
        if len(items) > limit:
            out.append(Finding(ERROR, name, f"{len(items)} given; the maximum is {limit}"))
    out += _brand(p, cfg)
    out += _asset_groups(p, cfg)
    out += [Finding(WARNING, "domains", f"builder.toml allows {d!r}, which looks like a public suffix: every site "
                                        "under it would be allowed") for d in suffix_like_domains(cfg.domains)]
    return out


def _bidding(p: PmaxCampaignPlan, cfg: Config) -> list[Finding]:
    out: list[Finding] = []
    err = lambda f, m: out.append(Finding(ERROR, f, m))  # noqa: E731
    if p.bidding not in BIDDING:
        err("bidding", f"must be one of {', '.join(BIDDING)}")
        return out
    if p.target_cpa is not None:
        if p.bidding != "MAXIMIZE_CONVERSIONS":
            err("target_cpa", "is only used with MAXIMIZE_CONVERSIONS bidding")
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
    if p.target_roas is not None:
        if p.bidding != "MAXIMIZE_CONVERSION_VALUE":
            err("target_roas", "is only used with MAXIMIZE_CONVERSION_VALUE bidding")
        elif not (_positive(p.target_roas) and 0.01 <= p.target_roas <= 1000):
            err("target_roas", "must be from 0.01 to 1000 (3.5 means a 350% return on ad spend)")
        elif round(p.target_roas, 2) != p.target_roas:
            err("target_roas", "must be a whole percent (at most 2 decimals, such as 3.5 for 350%)")
    return out


def _goals(goals: list[ConversionGoal]) -> list[Finding]:
    out: list[Finding] = []
    if not goals:
        out.append(Finding(ERROR, "conversion_goals", "list the goals to bid on, such as "
                                                      "{\"category\": \"PURCHASE\", \"origin\": \"WEBSITE\"}"))
    seen: dict[tuple[str, str], int] = {}
    for i, goal in enumerate(goals):
        f = f"conversion_goals[{i}]"
        if goal.category not in CONVERSION_CATEGORIES:
            out.append(Finding(ERROR, f"{f}.category", "must be a conversion category such as PURCHASE, "
                                                       "SUBMIT_LEAD_FORM or SIGNUP"))
        if goal.origin == "APP":
            out.append(Finding(ERROR, f"{f}.origin", "app goals stay in App campaigns; Performance Max here "
                                                     "promotes websites"))
        elif goal.origin not in CONVERSION_ORIGINS:
            out.append(Finding(ERROR, f"{f}.origin", f"must be one of {', '.join(CONVERSION_ORIGINS)}"))
        pair = (goal.category, goal.origin)
        if pair in seen:
            out.append(Finding(ERROR, f, f"duplicates conversion_goals[{seen[pair]}]"))
        else:
            seen[pair] = i
    return out


def _count(f: str, n: int, limits: tuple[int, int], *, want_full: bool = False) -> list[Finding]:
    low, high = limits
    if not low <= n <= high:
        return [Finding(ERROR, f, f"{n} given; use {low} to {high}")]
    if want_full and n < high:
        return [Finding(WARNING, f, f"only {n} of {high}; fill all {high} for the best Ad Strength")]
    return []


def _brand(p: PmaxCampaignPlan, cfg: Config) -> list[Finding]:
    """The campaign's business name (the rules of a Demand Gen ad's) and logos (brand guidelines are on, so they
    belong to the campaign)."""
    out = _business_name(p.business_name, "business_name", cfg)
    out += _count("logos", len(p.logos), LOGOS)
    out += _image_list(p.logos, "logos", "logo", cfg)
    out += _count("landscape_logos", len(p.landscape_logos), LANDSCAPE_LOGOS)
    out += _landscape_logos(p.landscape_logos, cfg)
    total = len(p.logos) + len(p.landscape_logos)
    if total > MAX_ALL_LOGOS and len(p.logos) <= LOGOS[1] and len(p.landscape_logos) <= LANDSCAPE_LOGOS[1]:
        out.append(Finding(ERROR, "logos", f"{total} logos in all (logos and landscape_logos); Google's limit is "
                                           f"{MAX_ALL_LOGOS} per campaign"))
    return out


def _pixels(path: Path) -> tuple[int, int] | None:
    try:
        with Image.open(path) as im:
            return im.size
    except (OSError, Image.DecompressionBombError):
        return None


def _landscape_logos(refs: list[ImageRef], cfg: Config) -> list[Finding]:
    """4:1 logos. rules.classify_image knows only 1.91:1, 1:1 and 4:5, so its ratio error is replaced here
    by the 4:1 check and Google's smallest size."""
    out = [x for x in _images(refs, cfg, "landscape_logos")[0] if "is not 1.91:1, 1:1 or 4:5" not in x.message
           and "below the recommended" not in x.message]
    min_w, min_h = LANDSCAPE_LOGO_MIN
    for i, ref in enumerate(refs):
        if ref.asset or not ref.file or rules.has_unsafe_characters(ref.file):
            continue
        path = Path(ref.file).expanduser()
        size = _pixels(path) if path.is_absolute() and path.is_file() else None
        if size is None:
            continue  # _images reported it
        w, h = size
        if abs(w / h - LANDSCAPE_LOGO_RATIO) / LANDSCAPE_LOGO_RATIO > 0.01:
            out.append(Finding(ERROR, f"landscape_logos[{i}]", f"is {w}x{h}; a landscape logo is 4:1"))
        elif w < min_w or h < min_h:
            out.append(Finding(ERROR, f"landscape_logos[{i}]", f"is {w}x{h}; Google's minimum here is {min_w}x{min_h}"))
    return out


def _prefixed(prefix: str, findings: list[Finding]) -> list[Finding]:
    return [Finding(x.level, f"{prefix}.{x.field}", x.message) for x in findings]


def _asset_groups(p: PmaxCampaignPlan, cfg: Config) -> list[Finding]:
    out: list[Finding] = []
    n = len(p.asset_groups)
    if not 1 <= n <= MAX_ASSET_GROUPS:
        out.append(Finding(ERROR, "asset_groups", f"{n} given; use 1 to {MAX_ASSET_GROUPS}"))
    seen: dict[str, int] = {}
    for i, group in enumerate(p.asset_groups):
        f = f"asset_groups[{i}]"
        if not group.name.strip():
            out.append(Finding(ERROR, f"{f}.name", "is empty"))
        out += _one_line(group.name, f"{f}.name", ASSET_GROUP_NAME, name=True)
        key = _key(group.name)
        if key in seen:
            out.append(Finding(ERROR, f"{f}.name", f"duplicates asset_groups[{seen[key]}].name"))
        else:
            seen[key] = i
        out += _asset_group(group, f, cfg)
    return out


def _group_texts(texts: list[str], f: str, limits: tuple[int, int], limit: int, cfg: Config, *,
                 headline: bool) -> list[Finding]:
    out = _count(f, len(texts), limits, want_full=True)
    out += _texts(texts, f, limit, headline=headline, blocked=cfg.blocked_words)
    return out + rules.find_duplicates(texts, f)


def _asset_group(g: PmaxAssetGroup, f: str, cfg: Config) -> list[Finding]:
    out = _url(g.final_url, f"{f}.final_url", cfg)
    for name in ("path1", "path2"):
        value = getattr(g, name)
        if value:
            out += rules.check_text(value, f"{f}.{name}", PATH_LIMIT, headline=True)
            out += rules.check_blocked(value, f"{f}.{name}", cfg.blocked_words)
    if g.path2 and not g.path1:
        out.append(Finding(ERROR, f"{f}.path2", "needs path1"))
    out += _group_texts(g.headlines, f"{f}.headlines", HEADLINES, HEADLINE_LIMIT, cfg, headline=True)
    out += _group_texts(g.long_headlines, f"{f}.long_headlines", LONG_HEADLINES, LONG_HEADLINE_LIMIT, cfg,
                        headline=True)
    out += _group_texts(g.descriptions, f"{f}.descriptions", DESCRIPTIONS, DESCRIPTION_LIMIT, cfg, headline=False)
    if g.descriptions and not any(rules.display_length(d) <= SHORT_DESCRIPTION for d in g.descriptions):
        out.append(Finding(ERROR, f"{f}.descriptions", f"add one of {SHORT_DESCRIPTION} characters or fewer "
                                                       "(Google needs a short description)"))
    for (name, shape, _), limits in zip(IMAGE_LISTS, (LANDSCAPE, SQUARE, PORTRAIT)):
        refs = getattr(g, name)
        out += _count(f"{f}.{name}", len(refs), limits)
        out += _image_list(refs, f"{f}.{name}", shape, cfg)
    counts = [len(getattr(g, name)) for name, _, _ in IMAGE_LISTS]
    if sum(counts) > MAX_GROUP_IMAGES and all(n <= high for n, (_, high) in zip(counts, (LANDSCAPE, SQUARE, PORTRAIT))):
        out.append(Finding(ERROR, f, f"has {sum(counts)} images in all (landscape, square and portrait); Google's "
                                     f"limit is {MAX_GROUP_IMAGES} per asset group"))
    if not g.portrait_marketing_images:
        out.append(Finding(WARNING, f"{f}.portrait_marketing_images", "no portrait images; cover all three shapes"))
    if not g.videos:
        out.append(Finding(ERROR, f"{f}.videos", "add at least one of your YouTube videos: without one, Google "
                                                 "makes videos from the images and text"))
    else:
        out += _count(f"{f}.videos", len(g.videos), VIDEOS)
    out += _videos(g.videos, cfg, f"{f}.videos")
    if g.call_to_action is not None and g.call_to_action not in CALLS_TO_ACTION:
        out.append(Finding(ERROR, f"{f}.call_to_action", f"must be one of {', '.join(CALLS_TO_ACTION)}, or null "
                                                         "to let Google choose"))
    if g.audience_signal is not None:
        m = _AUDIENCE.fullmatch(g.audience_signal)
        if not m or m.group(1) != cfg.customer_id:
            out.append(Finding(ERROR, f"{f}.audience_signal", "must be an audience of this account "
                                                              "(customers/<id>/audiences/<id>)"))
    out += _search_themes(g.search_themes, f"{f}.search_themes")
    return out


def _search_themes(themes: list[str], f: str) -> list[Finding]:
    """Keyword-like signals: like keywords, they are never shown, so a competitor's name may be one."""
    out: list[Finding] = []
    if len(themes) > SEARCH_THEMES:
        out.append(Finding(ERROR, f, f"{len(themes)} given; the maximum is {SEARCH_THEMES}"))
    seen: dict[str, int] = {}
    for i, theme in enumerate(themes):
        where = f"{f}[{i}]"
        if not theme.strip():
            out.append(Finding(ERROR, where, "is empty"))
            continue
        if rules.has_unsafe_characters(theme):
            out.append(Finding(ERROR, where, rules.UNSAFE_MESSAGE))
        elif rules.has_invisible_characters(theme):
            out.append(Finding(ERROR, where, "contains invisible characters"))
        if theme != theme.strip() or "  " in theme:
            out.append(Finding(ERROR, where, "has leading/trailing or double spaces"))
        if len(theme) > SEARCH_THEME_CHARS:
            out.append(Finding(ERROR, where, f"is {len(theme)} characters; the limit is {SEARCH_THEME_CHARS}"))
        words = len(theme.split())
        if words > SEARCH_THEME_WORDS:
            out.append(Finding(ERROR, where, f"has {words} words; the limit is {SEARCH_THEME_WORDS}"))
        key = _key(theme)
        if key in seen:
            out.append(Finding(ERROR, where, f"duplicates {f}[{seen[key]}]"))
        else:
            seen[key] = i
    return out


# ------------------------------------------------------------------- summary


def bidding_label(p: PmaxCampaignPlan) -> str:
    if p.bidding == "MAXIMIZE_CONVERSIONS":
        cpa = f", target cost per conversion {_money(p.target_cpa, p.currency)}" if p.target_cpa else ""
        return "Maximize conversions" + cpa
    if p.bidding == "MAXIMIZE_CONVERSION_VALUE":
        return "Maximize conversion value" + (f", target return on ad spend {p.target_roas * 100:g}%"
                                              if p.target_roas else "")
    return _choice(p.bidding, BIDDING)


def _media(title: str, items: list[str], level: str = "###") -> list[str]:
    return [f"{level} {title} ({len(items)})", *([f"- {x}" for x in items] or ["- none"]), ""]


def _image_label(ref: ImageRef, names: dict) -> str:
    if ref.asset:
        return f"existing asset {_named(ref.asset, names)}"
    path = Path(ref.file).expanduser()
    ratio = rules.classify_image(path)[0]
    if ratio is None:
        size = _pixels(path) if path.is_file() else None
        ratio = "4:1" if size and abs(size[0] / size[1] - LANDSCAPE_LOGO_RATIO) / LANDSCAPE_LOGO_RATIO <= 0.01 else None
    return f"{shown_file(ref.file)} (new, {ratio or 'unknown ratio'})"


def summary_body(p: PmaxCampaignPlan, head: list[str], cfg: Config) -> list[str]:
    """Everything above the approval arguments. Deterministic, like the rest of the summary."""
    names = p.brief.get("names", {})
    names = names if isinstance(names, dict) else {}
    sources = p.brief.get("sources", {})
    sources = sources if isinstance(sources, dict) else {}
    show = lambda xs: ", ".join(_named(x, names) for x in xs) or "-"  # noqa: E731
    lines = [
        f"# {p.name}",
        "",
        *head,
        "**Will be created PAUSED.** Nothing changes until the person approves.",
        "",
        "| Setting | Value |",
        "|---|---|",
        f"| Account | {_cell(cfg.customer_id)} |",
        "| Type | Performance Max campaign (website) |",
        f"| Bidding | {_cell(bidding_label(p))} |",
        f"| Daily budget | {_cell(_money(p.daily_budget, p.currency))} |",
        f"| Conversion goals bid on | {_cell(', '.join(goal_label(g) for g in p.conversion_goals) or '-')} |",
        "| Other conversion goals of the account | not bid on |",
        f"| Locations | {_cell(show(p.locations))} |",
        f"| Languages | {_cell(show(p.languages))} |",
        "| Location option | people in or regularly in your locations |",
        "| Final URLs | only each asset group's own final URL (final URL expansion off) |",
        f"| Generated assets | {GENERATED_LABEL} |",
        "| Videos made from images | not expected: every asset group has its own YouTube video (quality_report "
        "checks it after creation) |",
        "| Images taken from the landing page | the account's setting (only the Google Ads UI changes it; "
        "quality_report reads it back) |",
        "| Brand guidelines | on: the business name and logos belong to the campaign |",
        f"| Asset groups | {len(p.asset_groups)} |",
        f"| Copied from | {_cell(p.template or '-')} |",
        "",
        "## Brand",
        "",
        f"- Business name: {p.business_name} · {rules.display_length(p.business_name)}/{BUSINESS_NAME_LIMIT}",
        "",
    ]
    lines += _media("Logos (1:1)", [_image_label(r, names) for r in p.logos])
    lines += _media("Landscape logos (4:1)", [_image_label(r, names) for r in p.landscape_logos])
    for i, group in enumerate(p.asset_groups, 1):
        lines += _group_lines(group, i, sources, names)
    return lines


def _group_lines(g: PmaxAssetGroup, n: int, sources: dict, names: dict) -> list[str]:
    path = "/".join(x for x in (g.path1, g.path2) if x)
    cta = _choice(g.call_to_action, CALLS_TO_ACTION) if g.call_to_action else "chosen by Google"
    audience = _named(g.audience_signal, names) if g.audience_signal else "none"
    lines = [f"## Asset group {n}: {g.name}", "", f"- Final URL: {g.final_url}",
             f"- Display path: /{path}" if path else "- Display path: none", f"- Call to action: {cta}",
             f"- Audience signal: {audience}", ""]
    lines += _text_section("Headlines", g.headlines, HEADLINE_LIMIT, sources, level="###")
    lines += _text_section("Long headlines", g.long_headlines, LONG_HEADLINE_LIMIT, sources, level="###")
    lines += _text_section("Descriptions", g.descriptions, DESCRIPTION_LIMIT, sources, level="###")
    for name, _, title in IMAGE_LISTS:
        lines += _media(title, [_image_label(ref, names) for ref in getattr(g, name)])
    lines += _media("Videos", [_named(v.asset, names) if v.asset else f"YouTube {v.youtube_id}" for v in g.videos])
    lines += _media("Search themes", list(g.search_themes))
    return lines


def window_counts(p: PmaxCampaignPlan) -> dict:
    """What the approval window counts."""
    groups = p.asset_groups
    return {
        "asset_groups": len(groups),
        "headlines": sum(len(g.headlines) for g in groups),
        "long_headlines": sum(len(g.long_headlines) for g in groups),
        "descriptions": sum(len(g.descriptions) for g in groups),
        "new_images": len(new_image_files(p)),
        "existing_images": len({r.asset for r in image_refs(p) if r.asset}),
        "videos": len({v.asset or v.youtube_id for g in groups for v in g.videos}),
        "search_themes": sum(len(g.search_themes) for g in groups),
        "audience_signals": sum(g.audience_signal is not None for g in groups),
    }
