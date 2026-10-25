"""Demand Gen campaign plans: the model, strict parsing, every local rule and the summary the person reads.

plans.py hands every "demand_gen_campaign" plan to this module. Website destinations only: Demand Gen in
API v26 has no app setting, so app goals stay in App campaigns."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from . import rules
from .config import Config, suffix_like_domains
from .plans import (
    DESCRIPTION_LIMIT, INVISIBLE_MESSAGE, MAX_FREE_TEXT, MAX_NAME, ImageRef, PlanError, VideoRef, _brief_findings,
    _cell, _check_ref_list, _currency, _images, _named, _one_line, _targeting, _text_section, _texts, _videos,
    ad_name, budget_name, image_asset_name, shown_file, video_asset_name,
)
from .plans_search import (
    MAX_LANGUAGES, MAX_LOCATIONS, _cents, _choice, _dict, _key, _list_of, _money, _num, _object, _opt_num, _optional,
    _positive, _str, _text, _url,
)
from .rules import ERROR, WARNING, Finding

BIDDING = ("MAXIMIZE_CONVERSIONS", "TARGET_CPA", "MAXIMIZE_CLICKS")
# The v26 names of AdGroup.DemandGenAdGroupSettings.DemandGenChannelControls.DemandGenSelectedChannels' fields
# (there is no channel enum), and what the summary calls them.
CHANNELS = {
    "youtube_in_stream": "YouTube in-stream",
    "youtube_in_feed": "YouTube in-feed",
    "youtube_shorts": "YouTube Shorts",
    "discover": "Discover",
    "gmail": "Gmail",
}
NEVER_CHANNELS = {
    "display": "third-party Display is never used: Demand Gen stays on Google's own channels",
    "maps": "Maps is not offered by the builder",
}
# v26 ConversionActionCategoryEnum and ConversionOriginEnum names, without UNSPECIFIED and UNKNOWN
CONVERSION_CATEGORIES = (
    "DEFAULT", "PAGE_VIEW", "PURCHASE", "SIGNUP", "DOWNLOAD", "ADD_TO_CART", "BEGIN_CHECKOUT", "SUBSCRIBE_PAID",
    "PHONE_CALL_LEAD", "IMPORTED_LEAD", "SUBMIT_LEAD_FORM", "BOOK_APPOINTMENT", "REQUEST_QUOTE", "GET_DIRECTIONS",
    "OUTBOUND_CLICK", "CONTACT", "ENGAGEMENT", "STORE_VISIT", "STORE_SALE", "QUALIFIED_LEAD", "CONVERTED_LEAD",
    "YOUTUBE_FOLLOW_ON_VIEWS", "MESSAGE_LEAD", "IN_APP_AD_REVENUE",
)
CONVERSION_ORIGINS = ("WEBSITE", "GOOGLE_HOSTED", "APP", "CALL_FROM_ADS", "STORE", "YOUTUBE_HOSTED", "LOCAL_SERVICES_ADS")
# v26 CallToActionTypeEnum names, without UNSPECIFIED and UNKNOWN (video ads only)
CALLS_TO_ACTION = (
    "LEARN_MORE", "GET_QUOTE", "APPLY_NOW", "SIGN_UP", "CONTACT_US", "SUBSCRIBE", "DOWNLOAD", "BOOK_NOW", "SHOP_NOW",
    "BUY_NOW", "DONATE_NOW", "ORDER_NOW", "PLAY_NOW", "SEE_MORE", "START_NOW", "VISIT_SITE", "WATCH_NOW",
)
# Generated creatives each ad opts out of (AdGroupAd.ad_group_ad_asset_automation_settings, AssetAutomationTypeEnum)
GENERATED_CREATIVES = {
    "image": ("GENERATE_DESIGN_VERSIONS_FOR_IMAGES", "GENERATE_VIDEOS_FROM_OTHER_ASSETS",
              "GENERATE_ANIMATED_IMAGES_FROM_OTHER_ASSETS"),
    "video": ("GENERATE_VERTICAL_YOUTUBE_VIDEOS", "GENERATE_SHORTER_YOUTUBE_VIDEOS", "GENERATE_LANDING_PAGE_TEXT",
              "GENERATE_LANDING_PAGE_PREVIEW"),
}
HEADLINE_LIMIT = 40  # Google Ads Help; the v26 proto says 30 for the image ad's headlines (a warning, see _image_ad)
IMAGE_AD_API_HEADLINE = 30
LONG_HEADLINE_LIMIT, BUSINESS_NAME_LIMIT = 90, 25
TEXTS = (1, 5)  # (fewest, most) headlines, long headlines and descriptions per ad
MAX_IMAGES, LOGOS, VIDEOS = 20, (1, 5), (1, 5)  # images: landscape + square + portrait in one image ad
MAX_AD_GROUPS, MAX_ADS = 20, 10  # the builder's own limits
MAX_AUDIENCES = 50  # user lists per ad group (the builder's own limit)
MIN_SIZE = {"landscape": (600, 314), "square": (300, 300), "portrait": (480, 600), "logo": (128, 128)}
IMAGE_LISTS = (  # (plan field, shape, what the summary calls it)
    ("marketing_images", "landscape", "Landscape images (1.91:1)"),
    ("square_marketing_images", "square", "Square images (1:1)"),
    ("portrait_marketing_images", "portrait", "Portrait images (4:5)"),
    ("logo_images", "logo", "Logos (1:1)"),
)
_USER_LIST = re.compile(r"customers/([0-9]{10})/userLists/[0-9]+")


@dataclass
class ConversionGoal:
    category: str  # ConversionActionCategoryEnum name, such as PURCHASE
    origin: str  # ConversionOriginEnum name, such as WEBSITE


@dataclass
class ImageAd:
    """A Demand Gen multi-asset ad (DemandGenMultiAssetAdInfo)."""
    headlines: list[str]
    descriptions: list[str]
    business_name: str
    final_url: str
    logo_images: list[ImageRef]
    marketing_images: list[ImageRef] = field(default_factory=list)  # landscape 1.91:1
    square_marketing_images: list[ImageRef] = field(default_factory=list)
    portrait_marketing_images: list[ImageRef] = field(default_factory=list)
    type: str = "image"


@dataclass
class VideoAd:
    """A Demand Gen video responsive ad (DemandGenVideoResponsiveAdInfo): YouTube videos."""
    videos: list[VideoRef]
    headlines: list[str]
    long_headlines: list[str]
    descriptions: list[str]
    business_name: str
    final_url: str
    logo_images: list[ImageRef]
    call_to_action: str | None = None  # CallToActionTypeEnum name; None lets Google choose
    type: str = "video"


@dataclass
class DemandGenAdGroup:
    name: str
    locations: list[str]
    languages: list[str]
    channels: list[str]
    ads: list[ImageAd | VideoAd]
    audiences: list[str] = field(default_factory=list)  # existing user lists: customers/<id>/userLists/<id>


@dataclass
class DemandGenCampaignPlan:
    name: str
    daily_budget: float
    currency: str
    bidding: str
    conversion_goals: list[ConversionGoal]
    ad_groups: list[DemandGenAdGroup]
    target_cpa: float | None = None
    max_cpc: float | None = None
    confirm_budget_over_cap: float | None = None
    template: str | None = None
    brief: dict = field(default_factory=dict)  # creative brief, "sources", "names"; informational
    kind: str = "demand_gen_campaign"


# ------------------------------------------------------------------- parsing
# Strict, like plans_search: unknown fields and wrong types are refused.


def _image_refs(value, where: str) -> list[ImageRef]:
    return _check_ref_list(where, value, ImageRef, ("file", "asset"))


def _video_refs(value, where: str) -> list[VideoRef]:
    return _check_ref_list(where, value, VideoRef, ("asset", "youtube_id"))


_TEXTS = _list_of(_text)
_IMAGE_AD = _object(ImageAd, {
    "type": _str, "headlines": _TEXTS, "descriptions": _TEXTS, "business_name": _text, "final_url": _str,
    "logo_images": _image_refs, "marketing_images": _image_refs, "square_marketing_images": _image_refs,
    "portrait_marketing_images": _image_refs,
})
_VIDEO_AD = _object(VideoAd, {
    "type": _str, "videos": _video_refs, "headlines": _TEXTS, "long_headlines": _TEXTS, "descriptions": _TEXTS,
    "business_name": _text, "final_url": _str, "logo_images": _image_refs, "call_to_action": _optional(_str),
})


def _ad(value, where: str) -> ImageAd | VideoAd:
    """An ad is an image ad or a video ad; its "type" says which."""
    kind = value.get("type") if isinstance(value, dict) else None
    if kind == "image":
        return _IMAGE_AD(value, where)
    if kind == "video":
        return _VIDEO_AD(value, where)
    raise PlanError(f"{where}.type must be \"image\" or \"video\"")


_AD_GROUP = _object(DemandGenAdGroup, {
    "name": _str, "locations": _list_of(_str), "languages": _list_of(_str), "channels": _list_of(_str),
    "ads": _list_of(_ad), "audiences": _list_of(_str),
})
_PLAN = _object(DemandGenCampaignPlan, {
    "kind": _str, "name": _str, "daily_budget": _num, "currency": _str, "bidding": _str,
    "conversion_goals": _list_of(_object(ConversionGoal, {"category": _str, "origin": _str})),
    "ad_groups": _list_of(_AD_GROUP), "target_cpa": _opt_num, "max_cpc": _opt_num,
    "confirm_budget_over_cap": _opt_num, "template": _optional(_str), "brief": _dict,
})


def demand_gen_plan_from_dict(data: dict) -> DemandGenCampaignPlan:
    return _PLAN(data, "")


# --------------------------------------------------------- what gets created


def _file_key(file: str) -> str:
    return str(Path(file).expanduser().resolve())


def image_refs(plan: DemandGenCampaignPlan) -> list[ImageRef]:
    """Every image reference in the plan, in order: ad groups, ads, then IMAGE_LISTS."""
    return [ref for group in plan.ad_groups for ad in group.ads for name, _, _ in IMAGE_LISTS
            for ref in getattr(ad, name, [])]


def new_image_files(plan: DemandGenCampaignPlan) -> list[str]:
    """The new image files, each once (by resolved path), in the order they first appear. One file used in
    several ads, such as a logo, is uploaded once."""
    seen: dict[str, str] = {}
    for ref in image_refs(plan):
        if ref.file and not ref.asset:
            seen.setdefault(_file_key(ref.file), ref.file)
    return list(seen.values())


def new_youtube_ids(plan: DemandGenCampaignPlan) -> list[str]:
    """YouTube video IDs to create video assets for, each once, in order."""
    ids = [v.youtube_id for group in plan.ad_groups for ad in group.ads if ad.type == "video"
           for v in ad.videos if v.youtube_id and not v.asset]
    return list(dict.fromkeys(ids))


def calls_to_action(plan: DemandGenCampaignPlan) -> list[str]:
    """The call-to-action assets to create, each once, in order."""
    found = [ad.call_to_action for group in plan.ad_groups for ad in group.ads
             if ad.type == "video" and ad.call_to_action]
    return list(dict.fromkeys(found))


def image_files(plan: DemandGenCampaignPlan) -> list[Path]:
    return [Path(f).expanduser() for f in new_image_files(plan)]


def ad_names(plan: DemandGenCampaignPlan) -> list[list[str]]:
    """Each ad group's ad names, in order. Image ads and video ads are numbered separately within their ad
    group: "<campaign> - <ad group> - image 1", "... - video 1"."""
    out = []
    for group in plan.ad_groups:
        counted: dict[str, int] = {}
        names = []
        for ad in group.ads:
            counted[ad.type] = counted.get(ad.type, 0) + 1
            names.append(ad_name(plan.name, group.name, ad.type, counted[ad.type]))
        out.append(names)
    return out


def derived_names(plan: DemandGenCampaignPlan) -> list[tuple[str, str, str]]:
    """(plan field, what, name) for every name the compiler makes; ad group names are in the plan."""
    out = [("name", "budget", budget_name(plan.name))]
    out += [("ad_groups", "asset", image_asset_name(plan.name, Path(f).expanduser(), "0" * 8))
            for f in new_image_files(plan)]
    out += [("ad_groups", "asset", video_asset_name(plan.name, y)) for y in new_youtube_ids(plan)]
    out += [(f"ad_groups[{g}].ads[{a}]", "ad", name)
            for g, names in enumerate(ad_names(plan)) for a, name in enumerate(names)]
    return out


# ---------------------------------------------------------------- validation


def validate_demand_gen(p: DemandGenCampaignPlan, cfg: Config) -> list[Finding]:
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
    out += _ad_groups(p, cfg)
    out += [Finding(WARNING, "domains", f"builder.toml allows {d!r}, which looks like a public suffix: every site "
                                        "under it would be allowed") for d in suffix_like_domains(cfg.domains)]
    return out


def _bidding(p: DemandGenCampaignPlan, cfg: Config) -> list[Finding]:
    out: list[Finding] = []
    err = lambda f, m: out.append(Finding(ERROR, f, m))  # noqa: E731
    if p.bidding not in BIDDING:
        err("bidding", f"must be one of {', '.join(BIDDING)}")
        return out
    if p.target_cpa is not None:
        if p.bidding == "MAXIMIZE_CONVERSIONS":
            err("target_cpa", "Demand Gen doesn't accept a target cost per conversion with MAXIMIZE_CONVERSIONS; "
                              "use bidding TARGET_CPA")
        elif p.bidding == "MAXIMIZE_CLICKS":
            err("target_cpa", "is only used with TARGET_CPA bidding")
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
            elif _positive(p.daily_budget) and p.daily_budget < 15 * p.target_cpa:
                out.append(Finding(WARNING, "daily_budget", "Google recommends a Demand Gen daily budget of at "
                                                            "least 15 times the target CPA"))
    elif p.bidding == "TARGET_CPA":
        err("target_cpa", "is required for TARGET_CPA bidding")
    if p.max_cpc is not None:
        if p.bidding == "MAXIMIZE_CLICKS":
            err("max_cpc", "Demand Gen doesn't accept a maximum CPC with MAXIMIZE_CLICKS; leave max_cpc out")
        else:
            err("max_cpc", "is not used by Demand Gen bidding; leave it out")
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
            out.append(Finding(ERROR, f"{f}.origin", "app goals stay in App campaigns; Demand Gen here promotes "
                                                     "websites"))
        elif goal.origin not in CONVERSION_ORIGINS:
            out.append(Finding(ERROR, f"{f}.origin", f"must be one of {', '.join(CONVERSION_ORIGINS)}"))
        pair = (goal.category, goal.origin)
        if pair in seen:
            out.append(Finding(ERROR, f, f"duplicates conversion_goals[{seen[pair]}]"))
        else:
            seen[pair] = i
    return out


def _prefixed(prefix: str, findings: list[Finding]) -> list[Finding]:
    return [Finding(x.level, f"{prefix}.{x.field}", x.message) for x in findings]


def _ad_groups(p: DemandGenCampaignPlan, cfg: Config) -> list[Finding]:
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
        out += _prefixed(f, _targeting(group.locations, group.languages))  # no location: an error
        for name, items, limit in (("locations", group.locations, MAX_LOCATIONS),
                                   ("languages", group.languages, MAX_LANGUAGES)):
            if len(items) > limit:
                out.append(Finding(ERROR, f"{f}.{name}", f"{len(items)} given; the maximum is {limit}"))
        out += _channels(group.channels, f"{f}.channels")
        out += _audiences(group.audiences, f"{f}.audiences", cfg)
        if not 1 <= len(group.ads) <= MAX_ADS:
            out.append(Finding(ERROR, f"{f}.ads", f"{len(group.ads)} given; use 1 to {MAX_ADS}"))
        for a, ad in enumerate(group.ads):
            where = f"{f}.ads[{a}]"
            out += _image_ad(ad, where, cfg) if ad.type == "image" else _video_ad(ad, where, cfg)
    return out


def _channels(channels: list[str], f: str) -> list[Finding]:
    out: list[Finding] = []
    if not channels:
        out.append(Finding(ERROR, f, f"choose at least one of {', '.join(CHANNELS)}"))
    seen: dict[str, int] = {}
    for i, name in enumerate(channels):
        if name in NEVER_CHANNELS:
            out.append(Finding(ERROR, f"{f}[{i}]", NEVER_CHANNELS[name]))
        elif name not in CHANNELS:
            out.append(Finding(ERROR, f"{f}[{i}]", f"must be one of {', '.join(CHANNELS)}"))
        if name in seen:
            out.append(Finding(ERROR, f"{f}[{i}]", f"duplicates {f}[{seen[name]}]"))
        else:
            seen[name] = i
    return out


def _audiences(audiences: list[str], f: str, cfg: Config) -> list[Finding]:
    out: list[Finding] = []
    if len(audiences) > MAX_AUDIENCES:
        out.append(Finding(ERROR, f, f"{len(audiences)} given; the maximum is {MAX_AUDIENCES}"))
    seen: dict[str, int] = {}
    for i, name in enumerate(audiences):
        m = _USER_LIST.fullmatch(name)
        if not m or m.group(1) != cfg.customer_id:
            out.append(Finding(ERROR, f"{f}[{i}]", "must be a user list of this account (customers/<id>/userLists/<id>)"))
        if name in seen:
            out.append(Finding(ERROR, f"{f}[{i}]", f"duplicates {f}[{seen[name]}]"))
        else:
            seen[name] = i
    return out


def _count(f: str, n: int, limits: tuple[int, int], *, want_full: bool = True) -> list[Finding]:
    low, high = limits
    if not low <= n <= high:
        return [Finding(ERROR, f, f"{n} given; use {low} to {high}")]
    if want_full and n < high:
        return [Finding(WARNING, f, f"only {n} of {high}; fill all {high} for the best Ad Strength")]
    return []


def _ad_texts(texts: list[str], f: str, limit: int, cfg: Config, *, headline: bool) -> list[Finding]:
    out = _count(f, len(texts), TEXTS)
    out += _texts(texts, f, limit, headline=headline, blocked=cfg.blocked_words)
    return out + rules.find_duplicates(texts, f)


def _business_name(name: str, f: str, cfg: Config) -> list[Finding]:
    """Ad text rules, and like a campaign name no invisible or blank-looking characters: the business name is
    the brand people see, so a padded one can't pass for another."""
    out = rules.check_text(name, f, BUSINESS_NAME_LIMIT, headline=True)
    if rules.has_invisible_characters(name):
        out.append(Finding(ERROR, f, INVISIBLE_MESSAGE))
    return out + rules.check_blocked(name, f, cfg.blocked_words)


def _pixels(path: Path) -> tuple[int, int] | None:
    try:
        with Image.open(path) as im:
            return im.size
    except (OSError, Image.DecompressionBombError):
        return None


def _image_list(refs: list[ImageRef], f: str, shape: str, cfg: Config) -> list[Finding]:
    """plans._images' checks for one of an ad's image lists, plus the shape the list takes and Google's
    smallest size for it. A logo's size is checked here, not against the marketing image's recommendation."""
    out, _ = _images(refs, cfg, f)
    if shape == "logo":
        out = [x for x in out if not (x.level == WARNING and "below the recommended" in x.message)]
    want = "square" if shape == "logo" else shape
    min_w, min_h = MIN_SIZE[shape]
    for i, ref in enumerate(refs):
        if ref.asset or not ref.file or rules.has_unsafe_characters(ref.file):
            continue
        path = Path(ref.file).expanduser()
        if not path.is_absolute() or not path.is_file():
            continue  # _images reported it
        ratio, _ = rules.classify_image(path)
        size = _pixels(path)
        if ratio is None or size is None:
            continue  # _images reported it
        if ratio != want:
            out.append(Finding(ERROR, f"{f}[{i}]", f"is a {ratio} image; this list takes {want} images"))
        elif size[0] < min_w or size[1] < min_h:
            out.append(Finding(ERROR, f"{f}[{i}]", f"is {size[0]}x{size[1]}; Google's minimum here is {min_w}x{min_h}"))
    return out


def _image_ad(ad: ImageAd, f: str, cfg: Config) -> list[Finding]:
    out = _ad_texts(ad.headlines, f"{f}.headlines", HEADLINE_LIMIT, cfg, headline=True)
    long = [i for i, h in enumerate(ad.headlines) if rules.display_length(h) > IMAGE_AD_API_HEADLINE]
    out += [Finding(WARNING, f"{f}.headlines[{i}]", f"is over {IMAGE_AD_API_HEADLINE} characters: Google Ads Help "
                    f"allows {HEADLINE_LIMIT}, the API documentation says {IMAGE_AD_API_HEADLINE}; check_plan will tell")
            for i in long]
    if ad.headlines and len(long) == len(ad.headlines):
        out.append(Finding(WARNING, f"{f}.headlines", f"add one of {IMAGE_AD_API_HEADLINE} characters or fewer, or "
                                                      "Ad Strength shows Incomplete"))
    out += _ad_texts(ad.descriptions, f"{f}.descriptions", DESCRIPTION_LIMIT, cfg, headline=False)
    out += _business_name(ad.business_name, f"{f}.business_name", cfg)
    out += _url(ad.final_url, f"{f}.final_url", cfg)
    for name, shape, _ in IMAGE_LISTS:
        out += _image_list(getattr(ad, name), f"{f}.{name}", shape, cfg)
    out += _count(f"{f}.logo_images", len(ad.logo_images), LOGOS, want_full=False)
    images = len(ad.marketing_images) + len(ad.square_marketing_images) + len(ad.portrait_marketing_images)
    if not ad.marketing_images and not ad.square_marketing_images:
        out.append(Finding(ERROR, f"{f}.marketing_images", "add a landscape or a square image (Google needs one)"))
    elif images > MAX_IMAGES:
        out.append(Finding(ERROR, f, f"{images} landscape, square and portrait images; the maximum is {MAX_IMAGES}"))
    missing = [label for name, label in (("marketing_images", "landscape"), ("square_marketing_images", "square"),
                                         ("portrait_marketing_images", "portrait")) if not getattr(ad, name)]
    if missing and len(missing) < 3:
        out.append(Finding(WARNING, f, f"no {', '.join(missing)} images; cover all three shapes"))
    return out


def _video_ad(ad: VideoAd, f: str, cfg: Config) -> list[Finding]:
    out = _count(f"{f}.videos", len(ad.videos), VIDEOS, want_full=False)
    out += _videos(ad.videos, cfg, f"{f}.videos")
    out += _ad_texts(ad.headlines, f"{f}.headlines", HEADLINE_LIMIT, cfg, headline=True)
    out += _ad_texts(ad.long_headlines, f"{f}.long_headlines", LONG_HEADLINE_LIMIT, cfg, headline=True)
    out += _ad_texts(ad.descriptions, f"{f}.descriptions", DESCRIPTION_LIMIT, cfg, headline=False)
    out += _business_name(ad.business_name, f"{f}.business_name", cfg)
    out += _url(ad.final_url, f"{f}.final_url", cfg)
    out += _image_list(ad.logo_images, f"{f}.logo_images", "logo", cfg)
    out += _count(f"{f}.logo_images", len(ad.logo_images), LOGOS, want_full=False)
    if ad.call_to_action is not None and ad.call_to_action not in CALLS_TO_ACTION:
        out.append(Finding(ERROR, f"{f}.call_to_action", f"must be one of {', '.join(CALLS_TO_ACTION)}, or null "
                                                         "to let Google choose"))
    return out


# ------------------------------------------------------------------- summary


def bidding_label(p: DemandGenCampaignPlan) -> str:
    money = lambda v: _money(v, p.currency)  # noqa: E731
    if p.bidding == "MAXIMIZE_CONVERSIONS":
        return "Maximize conversions"
    if p.bidding == "TARGET_CPA":
        return f"Target cost per conversion {money(p.target_cpa)}"
    if p.bidding == "MAXIMIZE_CLICKS":
        return "Maximize clicks"
    return _choice(p.bidding, BIDDING)


def goal_label(goal: ConversionGoal) -> str:
    return f"{_choice(goal.category, CONVERSION_CATEGORIES)} ({_choice(goal.origin, CONVERSION_ORIGINS)})"


def channel_labels(channels: list[str]) -> str:
    return ", ".join(CHANNELS.get(c, repr(c)) for c in channels) or "none"


def _media(title: str, items: list[str]) -> list[str]:
    return [f"#### {title} ({len(items)})", *([f"- {x}" for x in items] or ["- none"]), ""]


def _image_label(ref: ImageRef, names: dict) -> str:
    if ref.asset:
        return f"existing asset {_named(ref.asset, names)}"
    ratio = rules.classify_image(Path(ref.file).expanduser())[0] or "unknown ratio"
    return f"{shown_file(ref.file)} (new, {ratio})"


GENERATED_LABEL = ("off: text on images, videos and animations made from images, vertical and shorter "
                   "video cuts, landing-page text and preview")


def summary_body(p: DemandGenCampaignPlan, head: list[str], cfg: Config) -> list[str]:
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
        "| Type | Demand Gen campaign (website) |",
        f"| Bidding | {_cell(bidding_label(p))} |",
        f"| Daily budget | {_cell(_money(p.daily_budget, p.currency))} (check_plan compares it with Google's "
        "Demand Gen minimum) |",
        f"| Conversion goals bid on | {_cell(', '.join(goal_label(g) for g in p.conversion_goals) or '-')} |",
        "| Other conversion goals of the account | not bid on |",
        "| Targeting | location and language on each ad group (upgraded targeting) |",
        "| Location option | people in or regularly in your locations |",
        "| Third-party Display | never |",
        "| Optimized targeting | not turned on; demographic expansion excluded (quality_report reads both back) |",
        f"| Generated creatives | {GENERATED_LABEL} |",
        f"| Ad groups | {len(p.ad_groups)} |",
        f"| Copied from | {_cell(p.template or '-')} |",
        "",
    ]
    for i, (group, group_ad_names) in enumerate(zip(p.ad_groups, ad_names(p)), 1):
        lines += [
            f"## Ad group {i}: {group.name}",
            "",
            f"- Channels: {channel_labels(group.channels)}",
            f"- Locations: {show(group.locations)}",
            f"- Languages: {show(group.languages)}",
            f"- Audiences: {show(group.audiences) if group.audiences else 'none'}",
            "",
        ]
        for a, (ad, name) in enumerate(zip(group.ads, group_ad_names), 1):
            lines += _ad_lines(ad, a, name, sources, names)
    return lines


def _ad_lines(ad: ImageAd | VideoAd, n: int, name: str, sources: dict, names: dict) -> list[str]:
    kind = "image ad" if ad.type == "image" else "video ad"
    lines = [f"### Ad {n}: {kind}", "", f"- Ad name: {name}", f"- Final URL: {ad.final_url}",
             f"- Business name: {ad.business_name} · {rules.display_length(ad.business_name)}/{BUSINESS_NAME_LIMIT}"]
    if ad.type == "video":
        cta = _choice(ad.call_to_action, CALLS_TO_ACTION) if ad.call_to_action else "chosen by Google"
        lines.append(f"- Call to action: {cta}")
    lines.append("")
    if ad.type == "video":
        lines += _media("Videos", [_named(v.asset, names) if v.asset else f"YouTube {v.youtube_id}" for v in ad.videos])
    lines += _text_section("Headlines", ad.headlines, HEADLINE_LIMIT, sources, level="####")
    if ad.type == "video":
        lines += _text_section("Long headlines", ad.long_headlines, LONG_HEADLINE_LIMIT, sources, level="####")
    lines += _text_section("Descriptions", ad.descriptions, DESCRIPTION_LIMIT, sources, level="####")
    for name, _, title in IMAGE_LISTS:
        if ad.type == "image" or name == "logo_images":
            lines += _media(title, [_image_label(ref, names) for ref in getattr(ad, name)])
    return lines


def window_counts(p: DemandGenCampaignPlan) -> dict:
    """What the approval window counts."""
    ads = [ad for group in p.ad_groups for ad in group.ads]
    refs = image_refs(p)
    return {
        "image_ads": sum(ad.type == "image" for ad in ads),
        "video_ads": sum(ad.type == "video" for ad in ads),
        "new_images": len(new_image_files(p)),
        "existing_images": len({r.asset for r in refs if r.asset}),
        "videos": len({v.asset or v.youtube_id for ad in ads if ad.type == "video" for v in ad.videos}),
    }
