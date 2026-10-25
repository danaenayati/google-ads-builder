"""Learn from history: asset-level results -> winners, losers, and images to look at."""

from __future__ import annotations

import io
import json
import re
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from PIL import Image

from . import gads
from .plans import slugify
from .templates import in_list

KINDS = {"HEADLINE": "headlines", "DESCRIPTION": "descriptions", "MARKETING_IMAGE": "images", "YOUTUBE_VIDEO": "videos"}
# A Demand Gen ad's asset field types (v26 AssetFieldTypeEnum) -> the report's lists
DEMAND_GEN_KINDS = {
    "HEADLINE": "headlines", "LONG_HEADLINE": "long_headlines", "DESCRIPTION": "descriptions",
    "BUSINESS_NAME": "business_names", "CALL_TO_ACTION": "calls_to_action", "CALL_TO_ACTION_SELECTION": "calls_to_action",
    "MARKETING_IMAGE": "landscape_images", "SQUARE_MARKETING_IMAGE": "square_images",
    "PORTRAIT_MARKETING_IMAGE": "portrait_images", "TALL_PORTRAIT_MARKETING_IMAGE": "tall_portrait_images",
    "LOGO": "logos", "YOUTUBE_VIDEO": "videos",
}
MODES = {"app": KINDS, "demand_gen": DEMAND_GEN_KINDS}  # creative_insights' mode -> the field types it reads
IMAGE_KINDS = ("images", "landscape_images", "square_images", "portrait_images", "tall_portrait_images", "logos",
               "landscape_logos")
_CALLS_TO_ACTION = ("CALL_TO_ACTION", "CALL_TO_ACTION_SELECTION")
LABEL_TIER = {"BEST": 3, "GOOD": 2, "LOW": 0}  # everything else (LEARNING, PENDING, UNKNOWN) = 1
MIN_IMPRESSIONS = 1000  # learn only from assets with at least this much data (or a BEST/LOW label)


@dataclass
class AssetStat:
    asset: str
    kind: str
    text: str = ""
    image_url: str = ""
    youtube_id: str = ""
    impressions: int = 0
    clicks: int = 0
    conversions: float = 0.0
    cost_micros: int = 0
    label: str = "UNKNOWN"
    campaigns: list[str] = field(default_factory=list)
    _label_impressions: int = 0

    @property
    def ctr(self) -> float:
        return self.clicks / self.impressions if self.impressions else 0.0

    @property
    def conv_rate(self) -> float:
        return self.conversions / self.clicks if self.clicks else 0.0

    @property
    def conv_per_1k(self) -> float:
        return 1000 * self.conversions / self.impressions if self.impressions else 0.0

    def enough_data(self, min_impressions: int) -> bool:
        return self.impressions >= min_impressions or self.label in ("BEST", "LOW")

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("_label_impressions")
        d.update(ctr=round(self.ctr, 5), conv_per_1k=round(self.conv_per_1k, 3), conv_rate=round(self.conv_rate, 5))
        if self.youtube_id:
            d["youtube_url"] = f"https://www.youtube.com/watch?v={self.youtube_id}"
        return d


def aggregate(rows: list[dict], kinds: dict = KINDS) -> list[AssetStat]:
    stats: dict[tuple[str, str], AssetStat] = {}
    for r in rows:
        kind = kinds.get(r["field"])
        if not kind:
            continue
        s = stats.setdefault((r["asset"], kind), AssetStat(r["asset"], kind, r["text"], r["url"], r["youtube"]))
        s.impressions += r["impressions"]
        s.clicks += r["clicks"]
        s.conversions += r["conversions"]
        s.cost_micros += r["cost_micros"]
        if r["impressions"] >= s._label_impressions:
            s.label, s._label_impressions = r["label"], r["impressions"]
        if r["campaign"] not in s.campaigns:
            s.campaigns.append(r["campaign"])
    return list(stats.values())


def rank(stats: list[AssetStat], min_impressions: int, top: int = 10, bottom: int = 5, *,
         labels_only: bool = False, kinds: dict = KINDS) -> dict:
    """Best first by Google's label, then conversions per 1,000 impressions, then CTR.
    A LOW-labelled asset is never a winner, a BEST or GOOD one never a loser; losers are listed worst first.
    labels_only: there are no results, only Google's labels. Then only BEST and GOOD can win and only LOW
    can lose; LEARNING, PENDING, UNKNOWN and any other label count as not enough data."""
    key = lambda s: (LABEL_TIER.get(s.label, 1), s.conv_per_1k, s.ctr)  # noqa: E731
    out = {}
    for kind in dict.fromkeys(kinds.values()):
        group = [s for s in stats if s.kind == kind]
        usable = [s for s in group if (s.label in LABEL_TIER if labels_only else s.enough_data(min_impressions))]
        ranked = sorted(usable, key=key, reverse=True)
        winners = [s for s in ranked if s.label != "LOW"][:top]
        losers = [s for s in reversed(ranked) if s not in winners and s.label not in ("BEST", "GOOD")][:bottom]
        out[kind] = {
            "winners": [s.to_dict() for s in winners],
            "losers": [s.to_dict() for s in losers],
            "not_enough_data": len(group) - len(ranked),
        }
    return out


def date_range(days: int) -> tuple[str, str]:
    """The last `days` full days, ending yesterday, as GAQL dates (start, end)."""
    end = date.today() - timedelta(days=1)
    return (end - timedelta(days=days - 1)).isoformat(), end.isoformat()


def fetch_rows(client, customer_id: str, campaigns: list[str], days: int,
               field_types: tuple[str, ...] = tuple(KINDS)) -> list[dict]:
    """Asset rows of the named campaigns for the given field types (AssetFieldTypeEnum names). A call to
    action's text is its CallToActionTypeEnum name."""
    if not field_types or not all(re.fullmatch(r"[A-Z_]+", f) for f in field_types):
        raise ValueError("field_types must be AssetFieldTypeEnum names such as HEADLINE")
    start, end = date_range(days)
    with_cta = any(f in _CALLS_TO_ACTION for f in field_types)
    query = (
        "SELECT campaign.name, ad_group_ad_asset_view.field_type, ad_group_ad_asset_view.performance_label, "
        "asset.resource_name, asset.text_asset.text, asset.image_asset.full_size.url, "
        "asset.youtube_video_asset.youtube_video_id, "
        + ("asset.call_to_action_asset.call_to_action, " if with_cta else "")
        + "metrics.impressions, metrics.clicks, "
        "metrics.conversions, metrics.cost_micros FROM ad_group_ad_asset_view "
        f"WHERE campaign.name IN {in_list(campaigns)} "
        f"AND segments.date BETWEEN '{start}' AND '{end}' "
        f"AND ad_group_ad_asset_view.field_type IN ({', '.join(repr(f) for f in field_types)})"
    )
    return [
        {
            "campaign": r.campaign.name,
            "field": r.ad_group_ad_asset_view.field_type.name,
            "label": r.ad_group_ad_asset_view.performance_label.name,
            "asset": r.asset.resource_name,
            "text": (r.asset.call_to_action_asset.call_to_action.name
                     if r.ad_group_ad_asset_view.field_type.name in _CALLS_TO_ACTION else r.asset.text_asset.text),
            "url": r.asset.image_asset.full_size.url,
            "youtube": r.asset.youtube_video_asset.youtube_video_id,
            "impressions": r.metrics.impressions,
            "clicks": r.metrics.clicks,
            "conversions": r.metrics.conversions,
            "cost_micros": r.metrics.cost_micros,
        }
        for r in gads.search(client, customer_id, query)
    ]


MAX_SIDE = 1600


def _download(url: str, dest: Path) -> Path | None:
    """Fetch, verify it really is an image, and save it as an RGB JPEG (longest side <= 1600).
    Any failure returns None so one bad file never stops the report."""
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = resp.read()
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            im = im.convert("RGB")
            im.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
            im.save(dest, "JPEG", quality=88)
        return dest
    except Exception:
        dest.unlink(missing_ok=True)
        return None


def download_media(ranked: dict, folder: Path) -> list[str]:
    """Top/bottom images and video thumbnails, named so their rank is obvious when viewed."""
    folder.mkdir(parents=True, exist_ok=True)
    saved = []
    for kind in [k for k in (*IMAGE_KINDS, "videos") if k in ranked]:
        for group in ("winners", "losers"):
            for i, item in enumerate(ranked[kind][group][: 6 if group == "winners" else 4], 1):
                url = item["image_url"] or (
                    f"https://i.ytimg.com/vi/{item['youtube_id']}/hqdefault.jpg" if item["youtube_id"] else ""
                )
                if not url:
                    continue
                asset_id = item["asset"].rsplit("/", 1)[-1]
                path = _download(url, folder / f"{kind}-{group}-{i:02d}-{asset_id}.jpg")
                if path:
                    item["local_file"] = str(path)
                    saved.append(str(path))
    return saved


def _new_folder(root: Path, slug: str) -> Path:
    base = Path(root) / f"{datetime.now().strftime('%Y-%m-%d-%H%M%S')}-{slug}"
    folder, n = base, 2
    while True:
        try:
            folder.mkdir(parents=True)
            return folder
        except FileExistsError:
            folder, n = base.with_name(f"{base.name}-{n}"), n + 1


def build_insights(client, cfg, campaigns: list[str], days: int, min_impressions: int, download: bool,
                   mode: str = "app") -> dict:
    """mode: "app" (headlines, descriptions, images, videos), "demand_gen" (the image and video ads' asset
    kinds, not carousel cards) or "pmax" (Performance Max asset groups, see insights_pmax)."""
    if mode == "pmax":
        from .insights_pmax import build_pmax_insights  # here: that module imports this one

        return build_pmax_insights(client, cfg, campaigns, days, min_impressions, download)
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join([*MODES, 'pmax'])}")
    kinds = MODES[mode]
    campaigns = [c for c in campaigns if isinstance(c, str) and c.strip()]
    if not campaigns:
        raise ValueError("give at least one campaign name")
    days = max(1, min(365, int(days)))
    min_impressions = max(MIN_IMPRESSIONS, int(min_impressions))
    rows = fetch_rows(client, cfg.customer_id, campaigns, days, tuple(kinds))
    ranked = rank(aggregate(rows, kinds), min_impressions, kinds=kinds)
    seen = {r["campaign"] for r in rows}
    missing = [c for c in campaigns if c not in seen]
    folder = _new_folder(cfg.insights_dir, slugify("-".join(campaigns), "insights"))
    downloaded = download_media(ranked, folder) if download else []
    result = {"campaigns": campaigns, "mode": mode, "missing_campaigns": missing, "days": days,
              "min_impressions": min_impressions, **ranked, "downloaded": downloaded}
    (folder / "insights.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
                                          newline="\n")
    result["saved_to"] = str(folder / "insights.json")
    return result
