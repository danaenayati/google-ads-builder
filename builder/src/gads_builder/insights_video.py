"""Which YouTube videos work: views, view rate, CTR and conversions per 1,000 impressions for the videos of
VIDEO, App and Demand Gen campaigns, ranked within each campaign type (their numbers aren't comparable).
Changes nothing in Google Ads; the report is saved in a new local folder."""

from __future__ import annotations

import json
from pathlib import Path

from . import gads
from .insights import MIN_IMPRESSIONS, _download, _new_folder, date_range
from .plans import _YOUTUBE, slugify
from .templates import in_list

# campaign type -> the report's list. VIDEO campaigns are read FROM video; App (MULTI_CHANNEL) and Demand Gen
# campaigns FROM ad_group_ad_asset_view, whose YOUTUBE_VIDEO rows FROM video doesn't return.
CHANNELS = {"VIDEO": "video_campaigns", "MULTI_CHANNEL": "app_campaigns", "DEMAND_GEN": "demand_gen_campaigns"}
RANK_BY = ("conv_per_1k", "view_rate", "ctr", "views")
TOP, BOTTOM = 10, 5
METRICS = ("impressions", "views", "clicks", "conversions", "cost_micros")
THUMBNAIL = "https://i.ytimg.com/vi/{}/hqdefault.jpg"


def _metrics(r) -> dict:
    return {"impressions": r.metrics.impressions, "views": r.metrics.video_trueview_views, "clicks": r.metrics.clicks,
            "conversions": r.metrics.conversions, "cost_micros": r.metrics.cost_micros}


def video_rows(client, customer_id: str, campaigns: list[str], start: str, end: str) -> list[dict]:
    """One row per video and campaign, from both views. `campaigns` empty: every campaign. Google refuses a
    filter on a field that isn't also selected, so both queries select what they filter on. Only real YouTube
    IDs (11 letters, digits, - or _) are kept: they name the thumbnail URLs and files."""
    named = f" AND campaign.name IN {in_list(campaigns)}" if campaigns else ""
    when = f" AND segments.date BETWEEN '{start}' AND '{end}'"
    rows = [
        {"channel": "VIDEO", "campaign": r.campaign.name, "youtube_id": r.video.id, "title": r.video.title,
         **_metrics(r)}
        for r in gads.search(
            client, customer_id,
            "SELECT campaign.name, campaign.advertising_channel_type, video.id, video.title, metrics.impressions, "
            "metrics.video_trueview_views, metrics.clicks, metrics.conversions, metrics.cost_micros FROM video "
            "WHERE campaign.advertising_channel_type = 'VIDEO'" + named + when)
    ]
    rows += [
        {"channel": r.campaign.advertising_channel_type.name, "campaign": r.campaign.name,
         "youtube_id": r.asset.youtube_video_asset.youtube_video_id,
         "title": r.asset.youtube_video_asset.youtube_video_title, **_metrics(r)}
        for r in gads.search(
            client, customer_id,
            "SELECT campaign.name, campaign.advertising_channel_type, ad_group_ad_asset_view.field_type, "
            "asset.youtube_video_asset.youtube_video_id, "
            "asset.youtube_video_asset.youtube_video_title, metrics.impressions, metrics.video_trueview_views, "
            "metrics.clicks, metrics.conversions, metrics.cost_micros FROM ad_group_ad_asset_view "
            "WHERE ad_group_ad_asset_view.field_type = 'YOUTUBE_VIDEO' "
            "AND campaign.advertising_channel_type IN ('MULTI_CHANNEL', 'DEMAND_GEN')" + named + when)
    ]
    return [r for r in rows if _YOUTUBE.fullmatch(r["youtube_id"] or "")]


def aggregate(rows: list[dict]) -> dict[str, list[dict]]:
    """{campaign type: one entry per YouTube video, its metrics summed over campaigns}."""
    out: dict[str, dict[str, dict]] = {channel: {} for channel in CHANNELS}
    for r in rows:
        if r["channel"] not in out:
            continue
        v = out[r["channel"]].setdefault(r["youtube_id"], {"youtube_id": r["youtube_id"], "title": "", "campaigns": [],
                                                           **dict.fromkeys(METRICS, 0)})
        for m in METRICS:
            v[m] += r[m]
        v["title"] = v["title"] or r["title"]
        if r["campaign"] not in v["campaigns"]:
            v["campaigns"].append(r["campaign"])
    return {channel: list(videos.values()) for channel, videos in out.items()}


def _shown(v: dict, currency: str) -> dict:
    """A video as the report shows it: rates, cost in the account currency (named), links."""
    n = v["impressions"]
    out = {k: v[k] for k in ("youtube_id", "title", "campaigns", "impressions", "views", "clicks")}
    out.update(
        view_rate=round(v["views"] / n, 4) if n else 0.0,
        ctr=round(v["clicks"] / n, 4) if n else 0.0,
        conversions=round(v["conversions"], 2),
        conv_per_1k=round(1000 * v["conversions"] / n, 3) if n else 0.0,
        cost=round(v["cost_micros"] / 1_000_000, 2),
        currency=currency,
        youtube_url=f"https://www.youtube.com/watch?v={v['youtube_id']}",
        thumbnail_url=THUMBNAIL.format(v["youtube_id"]),
    )
    return out


def rank(videos: list[dict], min_impressions: int, rank_by: str, currency: str, top: int = TOP,
         bottom: int = BOTTOM) -> dict:
    """Best first by `rank_by`, then the other measures; only videos with at least min_impressions count.
    Winners are at most about half of those (rounded up), so a short list still has losers; losers are listed
    worst first and are never winners."""
    shown = [_shown(v, currency) for v in videos]
    usable = [v for v in shown if v["impressions"] >= min_impressions]
    order = (rank_by, *(k for k in RANK_BY if k != rank_by), "impressions")
    ranked = sorted(usable, key=lambda v: tuple(v[k] for k in order), reverse=True)
    winners = ranked[:min(top, (len(ranked) + 1) // 2)]
    losers = [v for v in reversed(ranked) if v not in winners][:bottom]
    return {"winners": winners, "losers": losers, "not_enough_data": len(shown) - len(usable)}


def download_thumbnails(report: dict, folder: Path) -> list[str]:
    """The top 6 and bottom 4 of each list, named by type, rank and YouTube ID; each video once."""
    folder.mkdir(parents=True, exist_ok=True)
    saved, done = [], {}
    for name in CHANNELS.values():
        for group in ("winners", "losers"):
            for i, item in enumerate(report[name][group][: 6 if group == "winners" else 4], 1):
                youtube_id = item["youtube_id"]
                if youtube_id not in done:
                    done[youtube_id] = _download(item["thumbnail_url"],
                                                 folder / f"{name}-{group}-{i:02d}-{youtube_id}.jpg")
                    if done[youtube_id]:
                        saved.append(str(done[youtube_id]))
                if done[youtube_id]:
                    item["local_file"] = str(done[youtube_id])
    return saved


def build_video_insights(client, cfg, campaigns: list[str] | None, days: int = 90,
                         min_impressions: int = MIN_IMPRESSIONS, rank_by: str = "conv_per_1k",
                         download: bool = True) -> dict:
    if rank_by not in RANK_BY:
        raise ValueError(f"rank_by must be one of {', '.join(RANK_BY)}")
    campaigns = [c for c in (campaigns or []) if isinstance(c, str) and c.strip()]
    days = max(1, min(365, int(days)))
    min_impressions = max(MIN_IMPRESSIONS, int(min_impressions))
    start, end = date_range(days)
    rows = video_rows(client, cfg.customer_id, campaigns, start, end)
    by_channel = aggregate(rows)
    seen = {r["campaign"] for r in rows}
    result = {
        "scope": "the named campaigns" if campaigns else "all VIDEO, App and Demand Gen campaigns",
        "campaigns": campaigns,
        "missing_campaigns": [c for c in campaigns if c not in seen],
        "days": days,
        "min_impressions": min_impressions,
        "rank_by": rank_by,
        **{CHANNELS[channel]: rank(videos, min_impressions, rank_by, cfg.currency)
           for channel, videos in by_channel.items()},
    }
    folder = _new_folder(cfg.insights_dir, slugify("videos-" + "-".join(campaigns), "video-insights"))
    result["downloaded"] = download_thumbnails(result, folder) if download else []
    path = folder / "video-insights.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    result["saved_to"] = str(path)
    return result
