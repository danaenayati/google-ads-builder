"""Plan files: the single source of truth for every change the builder makes."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Union

from . import rules
from .config import Config
from .rules import ERROR, WARNING, Finding

if TYPE_CHECKING:  # plans_search, plans_demand_gen and plans_pmax import this module
    from .plans_demand_gen import DemandGenCampaignPlan
    from .plans_pmax import PmaxCampaignPlan
    from .plans_search import SearchCampaignPlan

APP_SUBTYPES = ("APP_CAMPAIGN", "APP_CAMPAIGN_FOR_ENGAGEMENT")
# goal -> (bidding strategy, needs target_cpa)
APP_GOALS = {
    "OPTIMIZE_INSTALLS_TARGET_INSTALL_COST": ("TARGET_CPA", True),
    "OPTIMIZE_INSTALLS_WITHOUT_TARGET_INSTALL_COST": ("MAXIMIZE_CONVERSIONS", False),
    "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST": ("TARGET_CPA", True),
    "OPTIMIZE_IN_APP_CONVERSIONS_WITHOUT_TARGET_CPA": ("MAXIMIZE_CONVERSIONS", False),
}
MAX_TEXTS, HEADLINE_LIMIT, DESCRIPTION_LIMIT, MAX_MEDIA = 5, 30, 90, 20
RSA_HEADLINES, RSA_DESCRIPTIONS = (3, 15), (2, 4)  # responsive search ads: (fewest, most)
UPDATE_AD_TYPES = ("APP_AD", "APP_ENGAGEMENT_AD", "RESPONSIVE_SEARCH_AD")
MAX_PLAN_IMAGE_BYTES = 20 * 1024 * 1024  # new image bytes sent in one request

# Always used with fullmatch: "$" with match would accept a trailing newline. [0-9], not \d, which also
# matches Persian and Arabic-Indic digits.
_CONV = re.compile(r"customers/([0-9]{10})/conversionActions/[0-9]+")
_GEO = re.compile(r"geoTargetConstants/[0-9]+")
_LANG = re.compile(r"languageConstants/[0-9]+")
_ASSET = re.compile(r"customers/([0-9]{10})/assets/[0-9]+")
_AD = re.compile(r"customers/([0-9]{10})/ads/[0-9]+")
_YOUTUBE = re.compile(r"[A-Za-z0-9_-]{11}")
MAX_NAME = 255  # campaign, budget, ad group and asset names
MAX_MICROS = 2**63 - 1  # Google's amounts are int64 micros
_CURRENCY = re.compile(r"[A-Z]{3}")
MAX_FREE_TEXT = MAX_NAME  # template and brief.names labels are Google names, so they can be this long


class PlanError(ValueError):
    pass


@dataclass
class ImageRef:
    file: str | None = None
    asset: str | None = None


@dataclass
class VideoRef:
    asset: str | None = None
    youtube_id: str | None = None


@dataclass
class AppCampaignPlan:
    name: str
    subtype: str
    app_store: str
    app_id: str
    goal: str
    daily_budget: float
    currency: str
    conversion_actions: list[str]
    locations: list[str]
    languages: list[str]
    headlines: list[str]
    descriptions: list[str]
    images: list[ImageRef] = field(default_factory=list)
    videos: list[VideoRef] = field(default_factory=list)
    target_cpa: float | None = None
    confirm_budget_over_cap: float | None = None
    template: str | None = None
    brief: dict = field(default_factory=dict)  # creative brief, "sources", "names"; informational
    # Deep links into the app: [{"os_type": "ANDROID"|"IOS", "url": "..."}]. Required for engagement ads.
    final_app_urls: list[dict] = field(default_factory=list)
    kind: str = "app_campaign"


@dataclass
class ImageUploadPlan:
    files: list[str]
    kind: str = "image_upload"


@dataclass
class AssetUpdatePlan:
    ad: str  # customers/<cid>/ads/<id>
    ad_type: str  # APP_AD | APP_ENGAGEMENT_AD | RESPONSIVE_SEARCH_AD
    base: dict  # snapshot when drafted: headlines, descriptions, images, videos
    add_headlines: list[str] = field(default_factory=list)
    add_descriptions: list[str] = field(default_factory=list)
    add_images: list[ImageRef] = field(default_factory=list)
    add_videos: list[VideoRef] = field(default_factory=list)
    remove: list[str] = field(default_factory=list)  # texts or asset resource names
    kind: str = "asset_update"


Plan = Union[AppCampaignPlan, ImageUploadPlan, AssetUpdatePlan, "SearchCampaignPlan", "DemandGenCampaignPlan",
             "PmaxCampaignPlan"]
_KINDS = {"app_campaign": AppCampaignPlan, "image_upload": ImageUploadPlan, "asset_update": AssetUpdatePlan}


# ------------------------------------------------------------------- names
# The compilers name what they create with these; validate checks the same names against MAX_NAME.


def budget_name(campaign_name: str) -> str:
    return f"{campaign_name} budget"


def ad_group_name(campaign_name: str) -> str:
    return f"{campaign_name} - ad group 1"


def image_asset_name(prefix: str, path: Path, digest: str) -> str:
    return f"{prefix} | {path.name} | {digest[:8]}"


def video_asset_name(prefix: str, youtube_id: str) -> str:
    return f"{prefix} | YouTube {youtube_id}"


def ad_name(campaign_name: str, ad_group_name: str, ad_type: str, n: int) -> str:
    """A Demand Gen ad's name, which Google requires: "<campaign> - <ad group> - image 1" (or "video 1")."""
    return f"{campaign_name} - {ad_group_name} - {ad_type} {n}"


def asset_prefix(plan: Plan) -> str:
    """New assets are named after the campaign, the upload, or the ad (so a file differs per ad)."""
    if isinstance(plan, AppCampaignPlan):
        return plan.name
    if isinstance(plan, ImageUploadPlan):
        return "upload"
    return f"update ad {plan.ad.rsplit('/', 1)[-1]}"


def derived_names(plan: Plan) -> list[tuple[str, str, str]]:
    """(plan field, what, name) for every name the compiler makes. An image's digest is a placeholder of
    the same length, so no file is read."""
    if plan.kind == "search_campaign":  # its ad groups are named in the plan and checked there
        return [("name", "budget", budget_name(plan.name))]
    if plan.kind == "demand_gen_campaign":
        from .plans_demand_gen import derived_names as demand_gen_names

        return demand_gen_names(plan)
    if plan.kind == "pmax_campaign":
        from .plans_pmax import derived_names as pmax_names

        return pmax_names(plan)
    prefix = asset_prefix(plan)
    out = []
    if isinstance(plan, AppCampaignPlan):
        out += [("name", "budget", budget_name(plan.name)), ("name", "ad group", ad_group_name(plan.name))]
        media = [("images", plan.images), ("videos", plan.videos)]
    elif isinstance(plan, ImageUploadPlan):
        media = [("files", [ImageRef(file=f) for f in plan.files])]
    else:
        media = [("add_images", plan.add_images), ("add_videos", plan.add_videos)]
    for field_, refs in media:
        for i, ref in enumerate(refs):
            if ref.asset:
                continue  # an existing asset keeps its name
            if isinstance(ref, ImageRef) and ref.file:
                out.append((f"{field_}[{i}]", "asset", image_asset_name(prefix, Path(ref.file).expanduser(), "0" * 8)))
            elif isinstance(ref, VideoRef) and ref.youtube_id:
                out.append((f"{field_}[{i}]", "asset", video_asset_name(prefix, ref.youtube_id)))
    return out


_SCHEMA = {
    "app_campaign": {
        "name": "str", "subtype": "str", "app_store": "str", "app_id": "str", "goal": "str",
        "daily_budget": "num", "currency": "str", "conversion_actions": "strs", "locations": "strs",
        "languages": "strs", "headlines": "strs", "descriptions": "strs", "images": "images",
        "videos": "videos", "target_cpa": "optnum", "confirm_budget_over_cap": "optnum",
        "template": "optstr", "brief": "dict", "final_app_urls": "appurls", "kind": "str",
    },
    "image_upload": {"files": "strs", "kind": "str"},
    "asset_update": {
        "ad": "str", "ad_type": "str", "base": "dict", "add_headlines": "strs",
        "add_descriptions": "strs", "add_images": "images", "add_videos": "videos",
        "remove": "strs", "kind": "str",
    },
}
_NUM = (int, float)
_NFC_FIELDS = ("headlines", "descriptions", "add_headlines", "add_descriptions", "remove")


def _is_num(v) -> bool:
    return isinstance(v, _NUM) and not isinstance(v, bool)


def _fits_float(name: str, value) -> None:
    """A number too large for a float (such as 10**400) would crash the checks: refuse it here."""
    try:
        float(value)
    except OverflowError:
        raise PlanError(f"{name} is too large") from None


def _opt_str(v) -> bool:
    return v is None or isinstance(v, str)


def _check_ref_list(name: str, value, cls, fields: tuple[str, ...]) -> list:
    if not isinstance(value, list):
        raise PlanError(f"{name} must be a list")
    refs = []
    for i, item in enumerate(value):
        if not isinstance(item, dict):
            raise PlanError(f"{name}[{i}] must be an object with {' / '.join(fields)}")
        extra = set(item) - set(fields)
        if extra:
            raise PlanError(f"{name}[{i}] has unknown field(s): {', '.join(sorted(extra))}")
        for key in fields:
            if not _opt_str(item.get(key)):
                raise PlanError(f"{name}[{i}].{key} must be text")
        refs.append(cls(**item))
    return refs


def _check_app_urls(name: str, value) -> list[dict]:
    if not isinstance(value, list):
        raise PlanError(f"{name} must be a list of {{os_type, url}} objects")
    urls = []
    for i, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != {"os_type", "url"}:
            raise PlanError(f"{name}[{i}] must be an object with exactly os_type and url")
        if not isinstance(item["os_type"], str) or not isinstance(item["url"], str):
            raise PlanError(f"{name}[{i}].os_type and .url must be text")
        urls.append({"os_type": item["os_type"].upper(), "url": item["url"]})
    return urls


def plan_from_dict(data: dict) -> Plan:
    if not isinstance(data, dict):
        raise PlanError("a plan must be an object")
    kind = data.get("kind")
    if kind == "search_campaign":
        from .plans_search import search_plan_from_dict  # here, not at the top: plans_search imports this module

        return search_plan_from_dict(data)
    if kind == "demand_gen_campaign":
        from .plans_demand_gen import demand_gen_plan_from_dict

        return demand_gen_plan_from_dict(data)
    if kind == "pmax_campaign":
        from .plans_pmax import pmax_plan_from_dict

        return pmax_plan_from_dict(data)
    cls = _KINDS.get(kind) if isinstance(kind, str) else None
    if cls is None:
        kinds = ", ".join([*_KINDS, "search_campaign", "demand_gen_campaign", "pmax_campaign"])
        raise PlanError(f"unknown plan kind {data.get('kind')!r}; use one of {kinds}")
    data = dict(data)
    schema = _SCHEMA[data["kind"]]
    for name, value in data.items():
        code = schema.get(name)
        if code is None:
            continue  # unknown names are rejected by the constructor below
        if code == "str" and not isinstance(value, str):
            raise PlanError(f"{name} must be text")
        if code == "num" and not _is_num(value):
            raise PlanError(f"{name} must be a number")
        if code == "optnum" and value is not None and not _is_num(value):
            raise PlanError(f"{name} must be a number or null")
        if code in ("num", "optnum") and value is not None:
            _fits_float(name, value)  # checked, not converted: an App plan's fingerprint stays as it was
        if code == "optstr" and not _opt_str(value):
            raise PlanError(f"{name} must be text or null")
        if code == "dict" and not isinstance(value, dict):
            raise PlanError(f"{name} must be an object")
        if code == "strs" and not (isinstance(value, list) and all(isinstance(x, str) for x in value)):
            raise PlanError(f"{name} must be a list of text values")
        if code == "images":
            data[name] = _check_ref_list(name, value, ImageRef, ("file", "asset"))
        if code == "videos":
            data[name] = _check_ref_list(name, value, VideoRef, ("asset", "youtube_id"))
        if code == "appurls":
            data[name] = _check_app_urls(name, value)
        if name in _NFC_FIELDS and code == "strs":
            data[name] = [unicodedata.normalize("NFC", x) for x in value]
    try:
        return cls(**data)
    except TypeError as e:
        raise PlanError(str(e)) from None


def plan_to_dict(plan: Plan) -> dict:
    return asdict(plan)


def _image_files(plan: Plan) -> list[Path]:
    if plan.kind == "search_campaign":
        return []
    if plan.kind == "demand_gen_campaign":
        from .plans_demand_gen import image_files

        return image_files(plan)
    if plan.kind == "pmax_campaign":
        from .plans_pmax import image_files as pmax_image_files

        return pmax_image_files(plan)
    if isinstance(plan, ImageUploadPlan):
        return [Path(f).expanduser() for f in plan.files]
    refs = plan.images if isinstance(plan, AppCampaignPlan) else plan.add_images
    return [Path(r.file).expanduser() for r in refs if r.file]


def fingerprint(plan: Plan) -> str:
    """Covers every field AND the bytes of every image file the plan would upload."""
    h = hashlib.sha256(json.dumps(plan_to_dict(plan), sort_keys=True, ensure_ascii=False).encode())
    for path in _image_files(plan):
        if path.is_file():
            data = path.read_bytes()
            h.update(len(data).to_bytes(8, "big") + hashlib.sha256(data).digest())
        else:
            h.update(b"missing:" + str(path).encode())
    return h.hexdigest()


def slugify(text: str, fallback: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()[:60].strip("-")
    return slug or fallback


def _plan_slug(plan: Plan) -> str:
    if isinstance(plan, AppCampaignPlan):
        return slugify(plan.name, "app-campaign")
    if plan.kind == "search_campaign":
        return slugify(plan.name, "search-campaign")
    if plan.kind == "demand_gen_campaign":
        return slugify(plan.name, "demand-gen-campaign")
    if plan.kind == "pmax_campaign":
        return slugify(plan.name, "pmax-campaign")
    if isinstance(plan, ImageUploadPlan):
        return "image-upload"
    return slugify(f"update-ad-{plan.ad.rsplit('/', 1)[-1]}", "update-ad")


def write_atomic(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:  # "\n" on every system, never "\r\n"
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def save_plan(plan: Plan, cfg: Config, findings: list[Finding]) -> Path:
    summary = render_summary(plan, findings, cfg)  # before any folder exists: a failure leaves nothing behind
    base = Path(cfg.plans_dir).expanduser()
    base.mkdir(parents=True, exist_ok=True)
    stem = f"{datetime.now().strftime('%Y-%m-%d-%H%M%S')}-{_plan_slug(plan)}"
    folder, n = base / stem, 1
    while True:
        try:
            folder.mkdir()
            break
        except FileExistsError:
            n += 1
            folder = base / f"{stem}-{n}"
    path = folder / "plan.json"
    write_atomic(path, json.dumps(plan_to_dict(plan), indent=2, ensure_ascii=False) + "\n")
    write_atomic(folder / "summary.md", summary)
    return path


def load_plan(path: Path) -> Plan:
    return plan_from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def approval_args(plan: Plan, cfg: Config) -> dict:
    """What the change tool must be given besides customer_id and fingerprint, so the person sees it
    in the approval prompt. (An asset update also needs the ad's live campaign_name and
    campaign_status, read by check_plan.)"""
    if isinstance(plan, AppCampaignPlan) or plan.kind in ("search_campaign", "demand_gen_campaign", "pmax_campaign"):
        return {"campaign_name": plan.name, "daily_budget": float(plan.daily_budget),
                "over_cap": bool(plan.daily_budget > cfg.daily_budget_cap)}
    if isinstance(plan, ImageUploadPlan):
        return {"image_count": len(plan.files)}
    return {"added": _added_lines(plan), "removed": _removed_lines(plan)}


def shown_file(file: str) -> str:
    """A new image as the person sees it: the full path that is read and sent, so two files with the
    same name never look alike, and never like an asset's resource name."""
    return str(Path(file).expanduser())


def _added_lines(plan: AssetUpdatePlan) -> list[str]:
    return [
        *(f"headline: {t}" for t in plan.add_headlines),
        *(f"description: {t}" for t in plan.add_descriptions),
        *(f"image: {shown_file(r.file) if r.file else r.asset}" for r in plan.add_images),
        *(f"video: {r.youtube_id or r.asset}" for r in plan.add_videos),
    ]


def _removed_lines(plan: AssetUpdatePlan) -> list[str]:
    base = plan.base if isinstance(plan.base, dict) else {}
    kinds = (("headlines", "headline"), ("descriptions", "description"), ("images", "image"), ("videos", "video"))

    def line(item: str) -> str:
        kind = next((k for key, k in kinds if isinstance(base.get(key), list) and item in base[key]), "not on the ad")
        return f"{kind}: {item}"

    return [line(item) for item in plan.remove]


def merged_assets(plan: AssetUpdatePlan) -> dict:
    """The ad's text and existing-asset lists after the update (new uploads are added at compile time)."""
    removed = set(plan.remove)

    def keep(key):
        items = plan.base.get(key, [])
        return [x for x in items if x not in removed] if isinstance(items, list) else []

    return {
        "headlines": keep("headlines") + plan.add_headlines,
        "descriptions": keep("descriptions") + plan.add_descriptions,
        "images": keep("images"),
        "videos": keep("videos"),
    }


# ---------------------------------------------------------------- validation


def validate(plan: Plan, cfg: Config) -> list[Finding]:
    if isinstance(plan, AppCampaignPlan):
        out = _validate_app(plan, cfg)
    elif isinstance(plan, ImageUploadPlan):
        out = _validate_upload(plan)
    elif plan.kind == "search_campaign":
        from .plans_search import validate_search

        out = validate_search(plan, cfg)
    elif plan.kind == "demand_gen_campaign":
        from .plans_demand_gen import validate_demand_gen

        out = validate_demand_gen(plan, cfg)
    elif plan.kind == "pmax_campaign":
        from .plans_pmax import validate_pmax

        out = validate_pmax(plan, cfg)
    else:
        out = _validate_update(plan, cfg)
    return out + _upload_size(plan) + _name_lengths(plan)


def _name_lengths(plan: Plan) -> list[Finding]:
    return [
        Finding(ERROR, field_, f"makes the {what} name {len(name)} characters; the limit is {MAX_NAME}")
        for field_, what, name in derived_names(plan)
        if len(name) > MAX_NAME
    ]


INVISIBLE_MESSAGE = "contains invisible characters"


def _one_line(value, field_: str, limit: int | None = None, *, name: bool = False) -> list[Finding]:
    """Plan text the summary or the approval prompt shows: nothing that could start a fake line or
    reorder text, and at most `limit` characters. A `name` (of a campaign or an ad group) also has no
    invisible characters or blank-looking letters, so it can't pass for another name."""
    text = value if isinstance(value, str) else str(value)
    out = [Finding(ERROR, field_, rules.UNSAFE_MESSAGE)] if rules.has_unsafe_characters(text) else []
    if name and rules.has_invisible_characters(text):
        out.append(Finding(ERROR, field_, INVISIBLE_MESSAGE))
    if limit is not None and len(text) > limit:
        out.append(Finding(ERROR, field_, f"is {len(text)} characters; the limit is {limit}"))
    return out


_IMAGE_FIELD = {"app_campaign": "images", "image_upload": "files", "asset_update": "add_images",
                "demand_gen_campaign": "ad_groups", "pmax_campaign": "asset_groups"}


def _upload_size(plan: Plan) -> list[Finding]:
    total = 0
    for path in _image_files(plan):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            pass
    if total <= MAX_PLAN_IMAGE_BYTES:
        return []
    mb = math.ceil(total / (1024 * 1024) * 10) / 10  # rounded up: never shows "20.0" for an over-cap plan
    return [Finding(ERROR, _IMAGE_FIELD[plan.kind], f"new images total {mb:.1f} MB; keep each plan under 20 MB")]


def _count(field_: str, n: int, max_n: int, *, want_full: bool) -> list[Finding]:
    if n == 0:
        return [Finding(ERROR, field_, "add at least one")]
    if n > max_n:
        return [Finding(ERROR, field_, f"{n} given; the maximum is {max_n}")]
    if want_full and n < max_n:
        return [Finding(WARNING, field_, f"only {n} of {max_n}; fill all {max_n} for the best Ad Strength")]
    return []


def _texts(
    texts: list[str], field_: str, limit: int, *, headline: bool, blocked: tuple[str, ...] = ()
) -> list[Finding]:
    out = []
    for i, text in enumerate(texts):
        out += rules.check_text(text, f"{field_}[{i}]", limit, headline=headline)
        out += rules.check_blocked(text, f"{field_}[{i}]", blocked)
    return out


_ABS_MSG = "use an absolute path (or ~/...)"


def _images(
    refs: list[ImageRef], cfg: Config, field_: str, on_ad: set[str] | None = None
) -> tuple[list[Finding], set[str]]:
    out, ratios = [], set()
    seen: dict[str, int] = {}
    for i, ref in enumerate(refs):
        f = f"{field_}[{i}]"
        key = None
        if bool(ref.file) == bool(ref.asset):
            out.append(Finding(ERROR, f, "give exactly one of file or asset"))
        elif ref.asset:
            key = ref.asset
            m = _ASSET.fullmatch(ref.asset)
            if not m or m.group(1) != cfg.customer_id:
                out.append(Finding(ERROR, f, "must be an asset of this account"))
            if on_ad is not None and ref.asset in on_ad:
                out.append(Finding(ERROR, f, "is already on the ad"))
        else:
            path = Path(ref.file).expanduser()
            if rules.has_unsafe_characters(ref.file):
                out.append(Finding(ERROR, f, rules.UNSAFE_MESSAGE))  # not read: findings would repeat its name
            elif not path.is_absolute():
                out.append(Finding(ERROR, f, _ABS_MSG))
            else:
                key = str(path.resolve())
                ratio, found = rules.classify_image(path)
                out += found
                if ratio:
                    ratios.add(ratio)
        if key is not None:
            if key in seen:
                out.append(Finding(ERROR, f, f"duplicate of {field_}[{seen[key]}]"))
            else:
                seen[key] = i
    return out, ratios


def _videos(refs: list[VideoRef], cfg: Config, field_: str, on_ad: set[str] | None = None) -> list[Finding]:
    out = []
    seen: dict[str, int] = {}
    for i, ref in enumerate(refs):
        f = f"{field_}[{i}]"
        key = ref.asset or ref.youtube_id
        if bool(ref.asset) == bool(ref.youtube_id):
            out.append(Finding(ERROR, f, "give exactly one of asset or youtube_id"))
            continue
        if ref.asset:
            m = _ASSET.fullmatch(ref.asset)
            if not m or m.group(1) != cfg.customer_id:
                out.append(Finding(ERROR, f, "must be an asset of this account"))
        elif not _YOUTUBE.fullmatch(ref.youtube_id):
            out.append(Finding(ERROR, f, "is not a YouTube video ID (11 characters)"))
        if on_ad is not None and key in on_ad:
            out.append(Finding(ERROR, f, "is already on the ad"))
        if key in seen:
            out.append(Finding(ERROR, f, f"duplicate of {field_}[{seen[key]}]"))
        else:
            seen[key] = i
    return out


def _brief_findings(brief: dict) -> list[Finding]:
    """sources and names must be objects of one-line values (the summary shows them); a plan without
    sources gets a warning."""
    out = []
    if not isinstance(brief.get("sources", {}), dict) or not isinstance(brief.get("names", {}), dict):
        out.append(Finding(WARNING, "brief", "sources/names must be objects; ignored"))
    for part in ("sources", "names"):
        values = brief.get(part)
        for key, value in values.items() if isinstance(values, dict) else ():
            out += _one_line(value, f"brief.{part}[{key!r}]", MAX_FREE_TEXT)  # repr: the key stays one line
    if not brief.get("sources"):
        out.append(Finding(WARNING, "brief", "no sources; mark each headline and description as proven or new"))
    return out


def _currency(currency: str, cfg: Config) -> list[Finding]:
    """Exactly Google's code for the account's currency: three capital letters, never another case."""
    if not _CURRENCY.fullmatch(currency):
        return [Finding(ERROR, "currency", f"must be a 3-letter currency code in capitals, like {cfg.currency}")]
    if currency != cfg.currency:
        return [Finding(ERROR, "currency", f"the account uses {cfg.currency}")]
    return []


def _targeting(locations: list[str], languages: list[str]) -> list[Finding]:
    out = []
    if not locations:
        out.append(Finding(ERROR, "locations", "add at least one location"))
    for i, x in enumerate(locations):
        if not _GEO.fullmatch(x):
            out.append(Finding(ERROR, f"locations[{i}]", "must look like geoTargetConstants/<id> (use find_locations)"))
    if not languages:  # valid in Google Ads: no language criterion = all languages
        out.append(Finding(WARNING, "languages", "no language targeting: ads can show to users of all languages"))
    for i, x in enumerate(languages):
        if not _LANG.fullmatch(x):
            out.append(Finding(ERROR, f"languages[{i}]", "must look like languageConstants/<id>"))
    return out


def _validate_app(p: AppCampaignPlan, cfg: Config) -> list[Finding]:
    out: list[Finding] = []
    err = lambda f, m: out.append(Finding(ERROR, f, m))  # noqa: E731
    warn = lambda f, m: out.append(Finding(WARNING, f, m))  # noqa: E731

    if not p.name.strip():
        err("name", "is empty")
    out += _one_line(p.name, "name", MAX_NAME, name=True)
    out += _one_line(p.template or "", "template", MAX_FREE_TEXT)
    out += _brief_findings(p.brief)
    if p.subtype not in APP_SUBTYPES:
        err("subtype", f"must be one of {', '.join(APP_SUBTYPES)}")
    if p.goal not in APP_GOALS:
        err("goal", f"must be one of {', '.join(APP_GOALS)}")
    else:
        _, needs_cpa = APP_GOALS[p.goal]
        if p.subtype == "APP_CAMPAIGN_FOR_ENGAGEMENT" and "INSTALL" in p.goal:
            err("goal", "engagement campaigns optimize in-app conversions, not installs")
        if p.target_cpa is not None and not math.isfinite(p.target_cpa):
            err("target_cpa", "must be a finite number")
        elif p.target_cpa is not None and abs(p.target_cpa) * 1_000_000 > MAX_MICROS:
            err("target_cpa", "is too large")
        elif needs_cpa and not (p.target_cpa and p.target_cpa > 0):
            err("target_cpa", "is required and must be positive for this goal")
        if not needs_cpa and p.target_cpa:
            warn("target_cpa", "is ignored with 'maximize conversions' bidding")
        if "IN_APP" in p.goal and not p.conversion_actions:
            err("conversion_actions", "list the in-app conversion actions to optimize for")
        if (
            p.target_cpa is not None
            and math.isfinite(p.target_cpa)
            and math.isfinite(p.daily_budget)
            and p.target_cpa > p.daily_budget
        ):
            warn("target_cpa", "is higher than the daily budget; the budget may not cover a single conversion")
    if not cfg.allows_app(p.app_store, p.app_id):
        err("app_id", "this app is not on the allow-list in builder.toml")
    out += _currency(p.currency, cfg)
    out += rules.check_budget(p.daily_budget, cfg.daily_budget_cap, p.confirm_budget_over_cap, cfg.currency)
    for i, ca in enumerate(p.conversion_actions):
        m = _CONV.fullmatch(ca)
        if not m or m.group(1) != cfg.customer_id:
            err(f"conversion_actions[{i}]", "must be a conversion action of this account")
    out += _targeting(p.locations, p.languages)
    store_os = {"GOOGLE_APP_STORE": "ANDROID", "APPLE_APP_STORE": "IOS"}.get(p.app_store)
    app = cfg.app(p.app_store, p.app_id)
    prefixes = app.deep_link_prefixes if app else ()
    if p.subtype == "APP_CAMPAIGN_FOR_ENGAGEMENT" and not p.final_app_urls:
        err("final_app_urls", "engagement ads need a deep link into the app; copy final_app_urls from describe_campaign")
    for i, u in enumerate(p.final_app_urls):
        if u["os_type"] not in ("ANDROID", "IOS"):
            err(f"final_app_urls[{i}]", "os_type must be ANDROID or IOS")
        elif store_os and u["os_type"] != store_os:
            err(f"final_app_urls[{i}]", f"os_type {u['os_type']} does not match the app store {p.app_store}")
        if not u["url"].strip() or any(ch.isspace() for ch in u["url"]):
            err(f"final_app_urls[{i}]", "url must be a deep link without spaces")
        elif prefixes and not u["url"].startswith(prefixes):
            err(f"final_app_urls[{i}]", f"must start with an allowed deep-link prefix for this app ({', '.join(prefixes)})")
        out += _one_line(u["url"], f"final_app_urls[{i}]")
    if p.final_app_urls and not prefixes:
        warn("final_app_urls", "no deep-link allowlist configured for this app")

    out += _count("headlines", len(p.headlines), MAX_TEXTS, want_full=True)
    out += _texts(p.headlines, "headlines", HEADLINE_LIMIT, headline=True, blocked=cfg.blocked_words)
    out += rules.find_duplicates(p.headlines, "headlines")
    out += _count("descriptions", len(p.descriptions), MAX_TEXTS, want_full=True)
    out += _texts(p.descriptions, "descriptions", DESCRIPTION_LIMIT, headline=False, blocked=cfg.blocked_words)
    out += rules.find_duplicates(p.descriptions, "descriptions")

    if len(p.images) > MAX_MEDIA:
        err("images", f"{len(p.images)} given; the maximum is {MAX_MEDIA}")
    found, ratios = _images(p.images, cfg, "images")
    out += found
    if not p.images:
        warn("images", "no images; add at least one landscape, square and portrait image")
    elif not any(r.asset for r in p.images):
        missing = {"landscape", "square", "portrait"} - ratios
        if missing:
            warn("images", f"missing ratios: {', '.join(sorted(missing))}")
    if len(p.videos) > MAX_MEDIA:
        err("videos", f"{len(p.videos)} given; the maximum is {MAX_MEDIA}")
    out += _videos(p.videos, cfg, "videos")
    if not p.videos:
        warn("videos", "no videos; reuse your best YouTube videos (Google makes weaker ones if none are given)")
    return out


def _validate_upload(p: ImageUploadPlan) -> list[Finding]:
    out = []
    if not p.files:
        out.append(Finding(ERROR, "files", "add at least one image"))
    if len(p.files) > MAX_MEDIA:
        out.append(Finding(ERROR, "files", f"upload at most {MAX_MEDIA} images per plan"))
    seen: dict[str, int] = {}
    for i, f in enumerate(p.files):
        path = Path(f).expanduser()
        if rules.has_unsafe_characters(f):
            out.append(Finding(ERROR, f"files[{i}]", rules.UNSAFE_MESSAGE))  # not read: findings would repeat its name
            continue
        if not path.is_absolute():
            out.append(Finding(ERROR, f"files[{i}]", _ABS_MSG))
            continue
        key = str(path.resolve())
        if key in seen:
            out.append(Finding(ERROR, f"files[{i}]", f"duplicate of files[{seen[key]}]"))
        else:
            seen[key] = i
        out += rules.classify_image(path)[1]
    return out


_BASE_KEYS = ("headlines", "descriptions", "images", "videos")


def _rsa_count(field_: str, n: int, limits: tuple[int, int], shown: str = "the ad would have {n}") -> list[Finding]:
    """An error unless a responsive search ad's n headlines (or descriptions) are within limits. `shown`
    says what n is; plans_search uses it for a new ad."""
    low, high = limits
    if low <= n <= high:
        return []
    return [Finding(ERROR, field_, f"{shown.format(n=n)}; a responsive search ad needs {low} to {high}")]


def _validate_update(p: AssetUpdatePlan, cfg: Config) -> list[Finding]:
    out: list[Finding] = []
    m = _AD.fullmatch(p.ad)
    if not m or m.group(1) != cfg.customer_id:
        out.append(Finding(ERROR, "ad", "must be an ad of this account (customers/<id>/ads/<id>)"))
    if p.ad_type not in UPDATE_AD_TYPES:
        out.append(Finding(ERROR, "ad_type", "must be APP_AD, APP_ENGAGEMENT_AD or RESPONSIVE_SEARCH_AD"))
    base = p.base
    for key in _BASE_KEYS:  # the summary shows the ad after the change, and the removed items
        items = base.get(key) if isinstance(base, dict) else None
        for i, item in enumerate(items if isinstance(items, list) else []):
            out += _one_line(item, f"base.{key}[{i}]")
    for i, item in enumerate(p.remove):
        out += _one_line(item, f"remove[{i}]")
    base_ok = (
        isinstance(base, dict)
        and set(base) == set(_BASE_KEYS)
        and all(isinstance(base[k], list) and all(isinstance(x, str) for x in base[k]) for k in _BASE_KEYS)
    )
    if not base_ok:
        out.append(
            Finding(
                ERROR,
                "base",
                "must list headlines, descriptions, images and videos (copy them from describe_campaign)",
            )
        )
        out += _texts(p.add_headlines, "add_headlines", HEADLINE_LIMIT, headline=True, blocked=cfg.blocked_words)
        out += _texts(p.add_descriptions, "add_descriptions", DESCRIPTION_LIMIT, headline=False, blocked=cfg.blocked_words)
        return out
    on_ad = {x for key in _BASE_KEYS for x in base[key]}
    for i, item in enumerate(p.remove):
        if item not in on_ad:
            out.append(Finding(ERROR, f"remove[{i}]", "is not on the ad"))
        elif item in base["headlines"] and item in base["descriptions"]:
            out.append(
                Finding(
                    ERROR,
                    f"remove[{i}]",
                    "appears in both headlines and descriptions; remove it by editing one list in a new plan",
                )
            )
    if not (p.add_headlines or p.add_descriptions or p.add_images or p.add_videos or p.remove):
        out.append(Finding(ERROR, "plan", "nothing to change"))

    merged = merged_assets(p)
    rsa = p.ad_type == "RESPONSIVE_SEARCH_AD"
    if rsa:
        out += _rsa_count("headlines", len(merged["headlines"]), RSA_HEADLINES)
        out += _rsa_count("descriptions", len(merged["descriptions"]), RSA_DESCRIPTIONS)
        no_media = "a responsive search ad has no images or videos"
        out += [Finding(ERROR, field_, no_media)
                for field_, present in (("add_images", p.add_images), ("add_videos", p.add_videos),
                                        ("base", base["images"] or base["videos"])) if present]
    else:
        out += _count("headlines", len(merged["headlines"]), MAX_TEXTS, want_full=False)
        out += _count("descriptions", len(merged["descriptions"]), MAX_TEXTS, want_full=False)
    out += _texts(p.add_headlines, "add_headlines", HEADLINE_LIMIT, headline=True, blocked=cfg.blocked_words)
    out += rules.find_duplicates(merged["headlines"], "headlines")
    out += _texts(p.add_descriptions, "add_descriptions", DESCRIPTION_LIMIT, headline=False, blocked=cfg.blocked_words)
    out += rules.find_duplicates(merged["descriptions"], "descriptions")
    if len(merged["images"]) + len(p.add_images) > MAX_MEDIA:
        out.append(Finding(ERROR, "images", f"the ad would have more than {MAX_MEDIA} images"))
    out += _images(p.add_images, cfg, "add_images", on_ad=set(base["images"]))[0]
    if len(merged["videos"]) + len(p.add_videos) > MAX_MEDIA:
        out.append(Finding(ERROR, "videos", f"the ad would have more than {MAX_MEDIA} videos"))
    out += _videos(p.add_videos, cfg, "add_videos", on_ad=set(base["videos"]))
    return out


# ------------------------------------------------------------------- summary


def _text_section(title: str, texts: list[str], limit: int, sources: dict, level: str = "##") -> list[str]:
    lines = [f"{level} {title} ({len(texts)})"]
    for i, t in enumerate(texts, 1):
        tag = f" · {sources[t]}" if t in sources else ""
        lines.append(f"{i}. {t} · {rules.display_length(t)}/{limit}{tag}")
    return lines + [""]


def _media_section(title: str, items: list[str]) -> list[str]:
    return [f"## {title} ({len(items)})", *([f"- {x}" for x in items] or ["- none"]), ""]


def _image_label(ref: ImageRef, names: dict | None = None) -> str:
    if ref.asset:
        return f"existing asset {_named(ref.asset, names or {})}"
    ratio = rules.classify_image(Path(ref.file).expanduser())[0] or "unknown ratio"
    return f"{shown_file(ref.file)} (new, {ratio})"


GOAL_LABELS = {
    "OPTIMIZE_INSTALLS_TARGET_INSTALL_COST": "Installs, target cost per install",
    "OPTIMIZE_INSTALLS_WITHOUT_TARGET_INSTALL_COST": "Installs, as many as possible within the budget",
    "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST": "In-app conversions, target cost per conversion",
    "OPTIMIZE_IN_APP_CONVERSIONS_WITHOUT_TARGET_CPA": "In-app conversions, as many as possible within the budget",
}


def _named(item: str, names: dict) -> str:
    name = names.get(item)
    return f"{name} ({item})" if name else item


def _cell(value) -> str:
    return str(value).replace("|", "\\|")


def _arg_lines(key: str, value) -> list[str]:
    if isinstance(value, list):
        return [f"- {key}:", *(f"  - {x}" for x in value)] if value else [f"- {key}: none"]
    return [f"- {key}: {json.dumps(value) if isinstance(value, bool) else value}"]


def _approval_section(plan: Plan, short_fingerprint: str, cfg: Config) -> list[str]:
    lines = ["## Approval arguments", f"- customer_id: {cfg.customer_id}", f"- fingerprint: {short_fingerprint}"]
    if isinstance(plan, AssetUpdatePlan):
        lines.append("- campaign_name: shown by check_plan (the ad's campaign, read live from the account)")
        lines.append("- campaign_status: shown by check_plan (that campaign's live status)")
    for key, value in approval_args(plan, cfg).items():
        lines += _arg_lines(key, value)
    return lines + [""]


def render_summary(plan: Plan, findings: list[Finding], cfg: Config) -> str:
    """Deterministic: check_plan and the change tools render it again and compare it with summary.md."""
    lines: list[str] = []
    short = fingerprint(plan)[:12]
    head = [f"**Plan fingerprint: {short}**", ""]
    for f in findings:
        if f.field == "daily_budget" and f.level == WARNING:
            head += [f"**⚠ {f.message}**", ""]
    if isinstance(plan, AppCampaignPlan):
        names = plan.brief.get("names", {})
        names = names if isinstance(names, dict) else {}
        kind = "installs (new users)" if plan.subtype == "APP_CAMPAIGN" else "engagement (existing users)"
        bidding, _ = APP_GOALS.get(plan.goal, ("?", False))
        bid = (
            f"Target cost per conversion {plan.target_cpa:,.2f} {plan.currency}"
            if bidding == "TARGET_CPA" and plan.target_cpa
            else "Maximize conversions"
        )
        show = lambda xs: ", ".join(_named(x, names) for x in xs) or "-"  # noqa: E731
        deep_links = ", ".join(f"{u['url']} ({u['os_type']})" for u in plan.final_app_urls) or "-"
        lines += [
            f"# {plan.name}",
            "",
            *head,
            "**Will be created PAUSED.** Nothing changes until the person approves.",
            "",
            "| Setting | Value |",
            "|---|---|",
            f"| Account | {_cell(cfg.customer_id)} |",
            f"| Type | {_cell('App campaign: ' + kind)} |",
            f"| App | {_cell(plan.app_store)} `{_cell(plan.app_id)}` |",
            f"| Goal | {_cell(GOAL_LABELS.get(plan.goal, plan.goal))} |",
            f"| Bidding | {_cell(bid)} |",
            f"| Daily budget | {_cell(f'{plan.daily_budget:,.2f} {plan.currency}')} |",
            f"| Conversion goals | {_cell(show(plan.conversion_actions))} |",
            f"| Locations | {_cell(show(plan.locations))} |",
            f"| Languages | {_cell(show(plan.languages))} |",
            f"| App deep links | {_cell(deep_links)} |",
            f"| Copied from | {_cell(plan.template or '-')} |",
            "",
        ]
        sources = plan.brief.get("sources", {})
        sources = sources if isinstance(sources, dict) else {}
        lines += _text_section("Headlines", plan.headlines, HEADLINE_LIMIT, sources)
        lines += _text_section("Descriptions", plan.descriptions, DESCRIPTION_LIMIT, sources)
        lines += _media_section("Images", [_image_label(i, names) for i in plan.images])
        lines += _media_section("Videos", [_named(v.asset, names) if v.asset else f"YouTube {v.youtube_id}" for v in plan.videos])
    elif isinstance(plan, ImageUploadPlan):
        lines += ["# Image upload", "", *head, f"Account: {cfg.customer_id}", "",
                  "Uploads to the asset library only; not attached to any ad.", ""]
        lines += _media_section("Images", [_image_label(ImageRef(file=f)) for f in plan.files])
    elif plan.kind == "search_campaign":
        from .plans_search import summary_body

        lines += summary_body(plan, head, cfg)
    elif plan.kind == "demand_gen_campaign":
        from .plans_demand_gen import summary_body as demand_gen_body

        lines += demand_gen_body(plan, head, cfg)
    elif plan.kind == "pmax_campaign":
        from .plans_pmax import summary_body as pmax_body

        lines += pmax_body(plan, head, cfg)
    else:
        merged = merged_assets(plan)
        lines += [
            f"# Asset update for ad `{plan.ad}`",
            "",
            *head,
            f"Account: {cfg.customer_id}",
            "",
            "**If the ad's campaign is running, this change takes effect immediately.**",
            "",
        ]
        lines += _text_section("Headlines after the change", merged["headlines"], HEADLINE_LIMIT, {})
        lines += _text_section("Descriptions after the change", merged["descriptions"], DESCRIPTION_LIMIT, {})
        lines += _media_section("Images added", [_image_label(i) for i in plan.add_images])
        lines += _media_section("Videos added", [v.asset or f"YouTube {v.youtube_id}" for v in plan.add_videos])
        lines += _media_section("Removed", list(plan.remove))
    lines += _approval_section(plan, short, cfg)
    errs = [f for f in findings if f.level == ERROR]
    warns = [f for f in findings if f.level == WARNING]
    if errs:
        lines += ["## Problems to fix", *[f"- **{f.field}**: {f.message}" for f in errs], ""]
    if warns:
        lines += ["## Warnings", *[f"- {f.field}: {f.message}" for f in warns], ""]
    return "\n".join(lines)
