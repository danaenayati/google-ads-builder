"""Performance Max history for creative_insights(mode="pmax"): each asset's results in its asset groups (and the
campaign's own business name and logos), the asset groups' Ad Strength and Google's suggestions, the asset
combinations that served most, and the search categories people used. API v26 has no Best/Good/Low labels for
asset group assets, so assets are ranked by their results only. Changes nothing in Google Ads; the report is saved
in a new local folder."""

from __future__ import annotations

import json

from . import gads
from .insights import MIN_IMPRESSIONS, _new_folder, aggregate, date_range, download_media, rank
from .plans import slugify
from .templates import in_list

# An asset group's asset field types (v26 AssetFieldTypeEnum) -> the report's lists
PMAX_KINDS = {
    "HEADLINE": "headlines", "LONG_HEADLINE": "long_headlines", "DESCRIPTION": "descriptions",
    "BUSINESS_NAME": "business_names", "CALL_TO_ACTION_SELECTION": "calls_to_action",
    "MARKETING_IMAGE": "landscape_images", "SQUARE_MARKETING_IMAGE": "square_images",
    "PORTRAIT_MARKETING_IMAGE": "portrait_images", "LOGO": "logos", "LANDSCAPE_LOGO": "landscape_logos",
    "YOUTUBE_VIDEO": "videos",
}
# With brand guidelines on (every Performance Max campaign the builder makes), these are campaign assets
# (campaign_asset); without, asset group assets. Both are read.
BRAND_KINDS = ("BUSINESS_NAME", "LOGO", "LANDSCAPE_LOGO")
NO_LABELS = ("Performance Max has no Best/Good/Low labels in the API (v26): assets are ranked by conversions per "
             "1,000 impressions, then CTR, from the advertiser's own assets only")
TOP_COMBINATIONS, TOP_CATEGORIES, MAX_CAMPAIGNS = 10, 20, 10


def fetch_rows(client, customer_id: str, campaigns: list[str], start: str, end: str) -> list[dict]:
    """The advertiser's own asset group assets of the named campaigns with their results, shaped like
    insights.fetch_rows (label UNKNOWN: there are none). A call to action's text is its CallToActionTypeEnum
    name."""
    query = (
        "SELECT campaign.name, asset_group.name, asset_group_asset.field_type, asset_group_asset.source, "
        "asset.resource_name, asset.text_asset.text, asset.image_asset.full_size.url, "
        "asset.youtube_video_asset.youtube_video_id, asset.call_to_action_asset.call_to_action, metrics.impressions, "
        "metrics.clicks, metrics.conversions, metrics.cost_micros FROM asset_group_asset "
        f"WHERE campaign.name IN {in_list(campaigns)} AND segments.date BETWEEN '{start}' AND '{end}' "
        f"AND asset_group_asset.field_type IN ({', '.join(repr(f) for f in PMAX_KINDS)}) "
        "AND asset_group_asset.source = 'ADVERTISER'"
    )
    out = []
    for r in gads.search(client, customer_id, query):
        field = r.asset_group_asset.field_type.name
        out.append({
            "campaign": r.campaign.name,
            "field": field,
            "label": "UNKNOWN",
            "asset": r.asset.resource_name,
            "text": (r.asset.call_to_action_asset.call_to_action.name if field == "CALL_TO_ACTION_SELECTION"
                     else r.asset.text_asset.text),
            "url": r.asset.image_asset.full_size.url,
            "youtube": r.asset.youtube_video_asset.youtube_video_id,
            "impressions": r.metrics.impressions,
            "clicks": r.metrics.clicks,
            "conversions": r.metrics.conversions,
            "cost_micros": r.metrics.cost_micros,
        })
    return out


def fetch_brand_rows(client, customer_id: str, campaigns: list[str], start: str, end: str) -> list[dict]:
    """The advertiser's own business name and logos linked to the named Performance Max campaigns (brand
    guidelines), with their results, shaped like fetch_rows."""
    query = (
        "SELECT campaign.name, campaign.advertising_channel_type, campaign_asset.field_type, campaign_asset.source, "
        "asset.resource_name, asset.text_asset.text, asset.image_asset.full_size.url, metrics.impressions, "
        "metrics.clicks, metrics.conversions, metrics.cost_micros FROM campaign_asset "
        f"WHERE campaign.name IN {in_list(campaigns)} AND campaign.advertising_channel_type = 'PERFORMANCE_MAX' "
        f"AND segments.date BETWEEN '{start}' AND '{end}' "
        f"AND campaign_asset.field_type IN ({', '.join(repr(f) for f in BRAND_KINDS)}) "
        "AND campaign_asset.source = 'ADVERTISER'"
    )
    return [{
        "campaign": r.campaign.name,
        "field": r.campaign_asset.field_type.name,
        "label": "UNKNOWN",
        "asset": r.asset.resource_name,
        "text": r.asset.text_asset.text,
        "url": r.asset.image_asset.full_size.url,
        "youtube": "",
        "impressions": r.metrics.impressions,
        "clicks": r.metrics.clicks,
        "conversions": r.metrics.conversions,
        "cost_micros": r.metrics.cost_micros,
    } for r in gads.search(client, customer_id, query)]


def asset_groups(client, customer_id: str, campaigns: list[str]) -> list[dict]:
    """Each asset group's Ad Strength, status and Google's suggestions (which assets to add)."""
    rows = gads.search(
        client, customer_id,
        "SELECT campaign.name, asset_group.name, asset_group.status, asset_group.ad_strength, "
        "asset_group.primary_status, asset_group.primary_status_reasons, asset_group.asset_coverage.ad_strength_action_items "
        f"FROM asset_group WHERE campaign.name IN {in_list(campaigns)} AND asset_group.status != 'REMOVED'",
    )
    out = []
    for r in rows:
        g = r.asset_group
        add = []
        for item in g.asset_coverage.ad_strength_action_items:
            details = item.add_asset_details
            entry = {"field": details.asset_field_type.name, "count": details.asset_count}
            if details.video_aspect_ratio_requirement.name not in ("UNSPECIFIED", "UNKNOWN"):
                entry["video_aspect_ratio"] = details.video_aspect_ratio_requirement.name
            add.append(entry)
        out.append({"campaign": r.campaign.name, "asset_group": g.name, "status": g.status.name,
                    "ad_strength": g.ad_strength.name, "primary_status": g.primary_status.name,
                    "primary_status_reasons": [x.name for x in g.primary_status_reasons], "add_assets": add})
    return out


def top_combinations(client, customer_id: str, campaigns: list[str], start: str, end: str, known: dict) -> list[dict]:
    """The asset combinations that served most, per asset group. `known`: asset -> what the report shows of it."""
    rows = gads.search(
        client, customer_id,
        "SELECT campaign.name, asset_group.name, asset_group_top_combination_view.asset_group_top_combinations "
        f"FROM asset_group_top_combination_view WHERE campaign.name IN {in_list(campaigns)} "
        f"AND segments.date BETWEEN '{start}' AND '{end}'",
    )
    groups: dict[tuple[str, str], list] = {}
    for r in rows:
        combos = groups.setdefault((r.campaign.name, r.asset_group.name), [])
        for combo in r.asset_group_top_combination_view.asset_group_top_combinations:
            if len(combos) < TOP_COMBINATIONS:
                combos.append([{"field": used.served_asset_field_type.name, "asset": used.asset,
                                **known.get(used.asset, {})} for used in combo.asset_combination_served_assets])
    return [{"campaign": c, "asset_group": g, "combinations": combos} for (c, g), combos in groups.items()]


def search_categories(client, customer_id: str, campaigns: list[str], start: str, end: str) -> list[dict]:
    """The search categories people used, per Performance Max campaign (Google answers one campaign per query)."""
    found = gads.search(
        client, customer_id,
        "SELECT campaign.id, campaign.name, campaign.advertising_channel_type FROM campaign "
        f"WHERE campaign.name IN {in_list(campaigns)} AND campaign.advertising_channel_type = 'PERFORMANCE_MAX'",
    )
    out = []
    for c in found:
        rows = gads.search(
            client, customer_id,
            "SELECT campaign_search_term_insight.campaign_id, campaign_search_term_insight.category_label, "
            "metrics.impressions, metrics.clicks, metrics.conversions FROM campaign_search_term_insight "
            f"WHERE campaign_search_term_insight.campaign_id = {int(c.campaign.id)} "
            f"AND segments.date BETWEEN '{start}' AND '{end}'",
        )
        categories = [{"category": r.campaign_search_term_insight.category_label or "(other searches)",
                       "impressions": r.metrics.impressions, "clicks": r.metrics.clicks,
                       "conversions": round(r.metrics.conversions, 2)} for r in rows]
        categories.sort(key=lambda x: (-x["conversions"], -x["clicks"], x["category"]))
        out.append({"campaign": c.campaign.name, "categories": categories[:TOP_CATEGORIES]})
    return out


def _known(rows: list[dict]) -> dict:
    """asset -> its text, image URL or YouTube ID, for the top combinations."""
    known = {}
    for r in rows:
        shown = {k: r[key] for k, key in (("text", "text"), ("image_url", "url"), ("youtube_id", "youtube")) if r[key]}
        known.setdefault(r["asset"], shown)
    return known


def build_pmax_insights(client, cfg, campaigns: list[str], days: int, min_impressions: int, download: bool) -> dict:
    campaigns = [c for c in campaigns if isinstance(c, str) and c.strip()]
    if not campaigns:
        raise ValueError("give at least one campaign name")
    if len(campaigns) > MAX_CAMPAIGNS:
        raise ValueError(f"give at most {MAX_CAMPAIGNS} campaigns")
    days = max(1, min(365, int(days)))
    min_impressions = max(MIN_IMPRESSIONS, int(min_impressions))
    start, end = date_range(days)
    rows = (fetch_rows(client, cfg.customer_id, campaigns, start, end)
            + fetch_brand_rows(client, cfg.customer_id, campaigns, start, end))
    ranked = rank(aggregate(rows, PMAX_KINDS), min_impressions, kinds=PMAX_KINDS)
    groups = asset_groups(client, cfg.customer_id, campaigns)
    seen = {r["campaign"] for r in rows} | {g["campaign"] for g in groups}
    result = {
        "campaigns": campaigns, "mode": "pmax", "missing_campaigns": [c for c in campaigns if c not in seen],
        "days": days, "min_impressions": min_impressions, "labels": NO_LABELS, **ranked, "asset_groups": groups,
        "top_combinations": top_combinations(client, cfg.customer_id, campaigns, start, end, _known(rows)),
        "search_categories": search_categories(client, cfg.customer_id, campaigns, start, end),
    }
    folder = _new_folder(cfg.insights_dir, slugify("pmax-" + "-".join(campaigns), "pmax-insights"))
    result["downloaded"] = download_media(ranked, folder) if download else []
    path = folder / "insights.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    result["saved_to"] = str(path)
    return result
