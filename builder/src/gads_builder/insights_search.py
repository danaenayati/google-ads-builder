"""Search history: each keyword's Quality Score, Google's labels for responsive search ad headlines and
descriptions, and the search terms people typed (new keyword ideas and wasted spend). Changes nothing
in Google Ads; the report is saved in a new local folder."""

from __future__ import annotations

import json

from . import gads
from .insights import _new_folder, aggregate, date_range, rank
from .landing import _norm
from .plans import slugify
from .templates import in_list

MIN_IMPRESSIONS = 100  # search volumes are small: learn from assets and keywords with at least this many
TOP = 50  # longest list returned
TAKEN = {"ADDED", "EXCLUDED", "ADDED_EXCLUDED"}  # search term statuses: already a keyword or a negative
METRICS = ("impressions", "clicks", "conversions", "cost_micros")
MAX_ERROR = 500  # characters of Google's refusal kept in the report


def _metrics(r) -> dict:
    return {"impressions": r.metrics.impressions, "clicks": r.metrics.clicks,
            "conversions": r.metrics.conversions, "cost_micros": r.metrics.cost_micros}


def _summed(rows: list[dict], keys: tuple[str, ...]) -> list[dict]:
    merged: dict[tuple, dict] = {}
    for r in rows:
        k = tuple(r[x] for x in keys)
        if k in merged:
            for m in METRICS:
                merged[k][m] += r[m]
        else:
            merged[k] = dict(r)
    return list(merged.values())


def _results(r: dict) -> dict:
    """Metrics as a person reads them: CTR, conversions and cost in the account currency."""
    out = {k: v for k, v in r.items() if k != "cost_micros"}
    out.update(ctr=round(r["clicks"] / r["impressions"], 4) if r["impressions"] else 0.0,
               conversions=round(r["conversions"], 2), cost=round(r["cost_micros"] / 1_000_000, 2))
    return out


def keyword_rows(client, customer_id: str, campaigns: list[str], start: str, end: str) -> list[dict]:
    query = (
        "SELECT campaign.name, ad_group.name, ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type, "
        "ad_group_criterion.quality_info.quality_score, ad_group_criterion.quality_info.search_predicted_ctr, "
        "ad_group_criterion.quality_info.creative_quality_score, "
        "ad_group_criterion.quality_info.post_click_quality_score, metrics.impressions, metrics.clicks, "
        "metrics.conversions, metrics.cost_micros FROM keyword_view "
        f"WHERE campaign.name IN {in_list(campaigns)} AND segments.date BETWEEN '{start}' AND '{end}'"
    )
    out = []
    for r in gads.search(client, customer_id, query):
        crit = r.ad_group_criterion
        quality = crit.quality_info
        out.append({
            "campaign": r.campaign.name,
            "ad_group": r.ad_group.name,
            "keyword": crit.keyword.text,
            "match": crit.keyword.match_type.name,
            "quality_score": quality.quality_score or None,  # 0: Google has no score yet
            "expected_ctr": quality.search_predicted_ctr.name,
            "ad_relevance": quality.creative_quality_score.name,
            "landing_page": quality.post_click_quality_score.name,
            **_metrics(r),
        })
    return _summed(out, ("campaign", "ad_group", "keyword", "match"))


def asset_rows(client, customer_id: str, campaigns: list[str], start: str, end: str) -> tuple[list[dict], str | None]:
    """Headline and description rows of responsive search ads, shaped like insights.fetch_rows. Returns
    (rows, refusal): if Google refuses metrics for this view, the rows carry labels only and `refusal` is
    Google's answer (None when the rows have metrics). A network failure is raised, never taken for a
    refusal."""
    fields = ("SELECT campaign.name, ad_group_ad_asset_view.field_type, ad_group_ad_asset_view.performance_label, "
              "asset.resource_name, asset.text_asset.text")
    where = (f" FROM ad_group_ad_asset_view WHERE campaign.name IN {in_list(campaigns)} "
             "AND ad_group_ad_asset_view.field_type IN ('HEADLINE', 'DESCRIPTION') "
             "AND ad_group_ad.ad.type = 'RESPONSIVE_SEARCH_AD'")
    try:
        rows = gads.search(client, customer_id, fields + ", metrics.impressions, metrics.clicks, metrics.conversions, "
                           "metrics.cost_micros" + where + f" AND segments.date BETWEEN '{start}' AND '{end}'")
        refusal = None
    except gads.GoogleAdsError as e:
        if gads.not_a_refusal(e):  # the daily quota or the network: not a refusal of the metrics
            raise
        refusal = str(e)[:MAX_ERROR] or "Google refused metrics for this view"
        rows = gads.search(client, customer_id, fields + where)
    out = [
        {
            "campaign": r.campaign.name,
            "field": r.ad_group_ad_asset_view.field_type.name,
            "label": r.ad_group_ad_asset_view.performance_label.name,
            "asset": r.asset.resource_name,
            "text": r.asset.text_asset.text,
            "url": "",
            "youtube": "",
            **(_metrics(r) if refusal is None else dict.fromkeys(METRICS, 0)),
        }
        for r in rows
    ]
    return out, refusal


def term_rows(client, customer_id: str, campaigns: list[str], start: str, end: str) -> list[dict]:
    query = (
        "SELECT campaign.name, ad_group.name, search_term_view.search_term, search_term_view.status, "
        "metrics.impressions, metrics.clicks, metrics.conversions, metrics.cost_micros FROM search_term_view "
        f"WHERE campaign.name IN {in_list(campaigns)} AND segments.date BETWEEN '{start}' AND '{end}'"
    )
    return [
        {"campaign": r.campaign.name, "ad_group": r.ad_group.name, "term": r.search_term_view.search_term,
         "status": r.search_term_view.status.name, **_metrics(r)}
        for r in gads.search(client, customer_id, query)
    ]


def term_lists(terms: list[dict], keywords: list[dict]) -> dict:
    """Search terms that are neither keywords nor negatives yet: the ones that converted (keyword ideas,
    most conversions first) and the ones that cost money without a conversion (negative keyword ideas,
    most expensive first)."""
    merged: dict[str, dict] = {}
    for t in terms:
        m = merged.setdefault(_norm(t["term"]), {"term": t["term"], "ad_groups": [], "statuses": set(),
                                                **dict.fromkeys(METRICS, 0)})
        for k in METRICS:
            m[k] += t[k]
        m["statuses"].add(t["status"])
        if t["ad_group"] not in m["ad_groups"]:
            m["ad_groups"].append(t["ad_group"])
    known = {_norm(k["keyword"]) for k in keywords}
    open_terms = [m for key, m in merged.items() if not (m["statuses"] & TAKEN) and key not in known]

    def shown(m):
        return _results({k: v for k, v in m.items() if k != "statuses"})

    converting = sorted((m for m in open_terms if m["conversions"] > 0),
                        key=lambda m: (-m["conversions"], -m["clicks"], m["term"]))
    wasted = sorted((m for m in open_terms if m["cost_micros"] > 0 and m["conversions"] == 0),
                    key=lambda m: (-m["cost_micros"], m["term"]))
    return {"keyword_ideas": [shown(m) for m in converting[:TOP]],
            "negative_keyword_ideas": [shown(m) for m in wasted[:TOP]]}


def build_search_insights(client, cfg, campaigns: list[str], days: int = 90,
                          min_impressions: int = MIN_IMPRESSIONS) -> dict:
    campaigns = [c for c in campaigns if isinstance(c, str) and c.strip()]
    if not campaigns:
        raise ValueError("give at least one campaign name")
    days = max(1, min(365, int(days)))
    min_impressions = max(MIN_IMPRESSIONS, int(min_impressions))
    start, end = date_range(days)
    keywords = keyword_rows(client, cfg.customer_id, campaigns, start, end)
    assets, refusal = asset_rows(client, cfg.customer_id, campaigns, start, end)
    terms = term_rows(client, cfg.customer_id, campaigns, start, end)
    ranked = rank(aggregate(assets), min_impressions, labels_only=refusal is not None)
    by_cost = sorted(keywords, key=lambda k: (-k["cost_micros"], k["keyword"]))
    weak = [k for k in by_cost if k["quality_score"] is not None and k["quality_score"] <= 5
            and k["impressions"] >= min_impressions]
    seen = {r["campaign"] for r in [*keywords, *assets, *terms]}
    result = {
        "campaigns": campaigns,
        "missing_campaigns": [c for c in campaigns if c not in seen],
        "days": days,
        "min_impressions": min_impressions,
        "keywords": [_results(k) for k in by_cost[:TOP]],
        "keywords_total": len(keywords),
        "low_quality_score": [_results(k) for k in weak[:TOP]],
        "asset_metrics": refusal is None,
        "asset_metrics_error": refusal,
        "headlines": ranked["headlines"],
        "descriptions": ranked["descriptions"],
        **term_lists(terms, keywords),
    }
    folder = _new_folder(cfg.insights_dir, slugify("search-" + "-".join(campaigns), "search-insights"))
    path = folder / "search-insights.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    result["saved_to"] = str(path)
    return result
