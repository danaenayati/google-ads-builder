"""Read an existing campaign so a new one can copy what already works."""

from __future__ import annotations

import re

from . import gads
from .plans_search import MAX_AD_GROUPS, SMART_BIDDING

AD_FIELDS = (
    "ad_group.name, ad_group_ad.status, ad_group_ad.ad.resource_name, ad_group_ad.ad.type, "
    "ad_group_ad.ad.app_ad.headlines, ad_group_ad.ad.app_ad.descriptions, "
    "ad_group_ad.ad.app_ad.images, ad_group_ad.ad.app_ad.youtube_videos, "
    "ad_group_ad.ad.app_engagement_ad.headlines, ad_group_ad.ad.app_engagement_ad.descriptions, "
    "ad_group_ad.ad.app_engagement_ad.images, ad_group_ad.ad.app_engagement_ad.videos, "
    "ad_group_ad.ad.final_app_urls, ad_group_ad.ad.responsive_search_ad.headlines, "
    "ad_group_ad.ad.responsive_search_ad.descriptions"
)


APP_AD_TYPES = ("APP_AD", "APP_ENGAGEMENT_AD")
RSA_FIELDS = (
    "ad_group.id, ad_group.name, ad_group_ad.status, ad_group_ad.ad.resource_name, ad_group_ad.ad.type, "
    "ad_group_ad.ad.responsive_search_ad.headlines, ad_group_ad.ad.responsive_search_ad.descriptions, "
    "ad_group_ad.ad.responsive_search_ad.path1, ad_group_ad.ad.responsive_search_ad.path2, ad_group_ad.ad.final_urls"
)
# Google's bidding strategy type -> the search_campaign plan's bidding
SEARCH_BIDDING = {
    "MAXIMIZE_CONVERSIONS": "MAXIMIZE_CONVERSIONS",
    "TARGET_CPA": "TARGET_CPA",
    "TARGET_SPEND": "MAXIMIZE_CLICKS",
    "TARGET_IMPRESSION_SHARE": "TARGET_IMPRESSION_SHARE",
    "MANUAL_CPC": "MANUAL_CPC",
}


def quote(text: str) -> str:
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def in_list(values: list[str]) -> str:
    return "(" + ", ".join(quote(v) for v in values) + ")"


def where_campaign(campaign: str) -> str:
    # ASCII digits only: Persian or Arabic-Indic digits are names, not IDs.
    return f"campaign.id = {campaign}" if re.fullmatch(r"[0-9]+", campaign) else f"campaign.name = {quote(campaign)}"


def ad_assets(row) -> dict:
    ad = row.ad_group_ad.ad
    kind = ad.type_.name
    if kind == "RESPONSIVE_SEARCH_AD":
        rsa = ad.responsive_search_ad
        return {"headlines": [a.text for a in rsa.headlines], "descriptions": [a.text for a in rsa.descriptions],
                "images": [], "videos": []}
    if kind == "APP_AD":
        info, videos = ad.app_ad, ad.app_ad.youtube_videos
    elif kind == "APP_ENGAGEMENT_AD":
        info, videos = ad.app_engagement_ad, ad.app_engagement_ad.videos
    else:
        raise ValueError(f"unsupported ad type {kind}: only APP_AD, APP_ENGAGEMENT_AD and RESPONSIVE_SEARCH_AD are handled")
    return {
        "headlines": [a.text for a in info.headlines],
        "descriptions": [a.text for a in info.descriptions],
        "images": [a.asset for a in info.images],
        "videos": [a.asset for a in videos],
    }


_AD_NAME = re.compile(r"customers/[0-9]+/ads/([0-9]+)")  # [0-9], not \d: int() also reads other digits


def _ad_id(ad_resource_name) -> int:
    """The ID of customers/<id>/ads/<id> as an int, so only ASCII digits reach a query."""
    m = _AD_NAME.fullmatch(ad_resource_name) if isinstance(ad_resource_name, str) else None
    if not m:
        raise LookupError(f"{ad_resource_name!r} is not an ad of the form customers/<id>/ads/<id>")
    return int(m.group(1))


def _pinned(row) -> bool:
    """A headline or description of the row's responsive search ad is pinned to a position."""
    rsa = row.ad_group_ad.ad.responsive_search_ad
    return any(text.pinned_field.name not in ("UNSPECIFIED", "UNKNOWN") for text in [*rsa.headlines, *rsa.descriptions])


def current_ad(client, customer_id: str, ad_resource_name: str) -> tuple[dict, bool]:
    """One read of an ad: its asset lists (as ad_assets gives them) and whether any of its responsive
    search ad text is pinned. An update replaces the text lists and would drop the pins, so pinned ads
    are changed in Google Ads. One query, so the two answers describe the same moment."""
    ad_id = _ad_id(ad_resource_name)
    rows = gads.search(client, customer_id, f"SELECT {AD_FIELDS} FROM ad_group_ad WHERE ad_group_ad.ad.id = {ad_id}")
    if not rows:
        raise LookupError(f"ad {ad_resource_name} not found")
    return ad_assets(rows[0]), any(_pinned(r) for r in rows)


def _name_lookups(customer_id: str) -> list[tuple[str, re.Pattern, tuple[str, ...]]]:
    """(GAQL resource, resource names it answers for, its name fields; the first is the one shown).
    ASCII digits only: \\d would also match Persian and Arabic-Indic digits."""
    cid = re.escape(customer_id)
    return [
        ("conversion_action", re.compile(rf"customers/{cid}/conversionActions/[0-9]+"), ("name",)),
        ("geo_target_constant", re.compile(r"geoTargetConstants/[0-9]+"), ("canonical_name", "name")),
        ("language_constant", re.compile(r"languageConstants/[0-9]+"), ("name",)),
        ("asset", re.compile(rf"customers/{cid}/assets/[0-9]+"), ("name",)),
        ("user_list", re.compile(rf"customers/{cid}/userLists/[0-9]+"), ("name",)),
        ("audience", re.compile(rf"customers/{cid}/audiences/[0-9]+"), ("name",)),
    ]


def google_names(client, customer_id: str, resources) -> dict[str, tuple[str, ...]]:
    """Google's own names for this account's conversion actions, assets, user lists and audiences, and for
    locations and languages: {resource name: names}, the first being the one to show (a location's canonical name).
    Anything else, and anything Google does not return, is left out (and never reaches the query)."""
    wanted = [r for r in dict.fromkeys(resources) if isinstance(r, str)]
    names: dict[str, tuple[str, ...]] = {}
    for resource, pattern, fields in _name_lookups(customer_id):
        values = [r for r in wanted if pattern.fullmatch(r)]
        if not values:
            continue
        select = ", ".join(f"{resource}.{f}" for f in ("resource_name", *fields))
        query = f"SELECT {select} FROM {resource} WHERE {resource}.resource_name IN {in_list(values)}"
        for row in gads.search(client, customer_id, query):
            obj = getattr(row, resource)
            names[obj.resource_name] = tuple(getattr(obj, f) for f in fields)
    return names


def build_template_plan(campaign: dict, locations: list[str], languages: list[str], ad: dict | None, names: dict) -> dict:
    """Pure: an app_campaign plan dict pre-filled from an existing campaign (edit before drafting)."""
    tcpa = campaign["target_cpa_micros"]
    return {
        "kind": "app_campaign",
        "name": f"{campaign['name']} (copy)",
        "template": campaign["name"],
        "subtype": campaign["subtype"],
        "app_store": campaign["app_store"],
        "app_id": campaign["app_id"],
        "goal": campaign["goal"],
        "target_cpa": tcpa / 1_000_000 if tcpa else None,
        "daily_budget": campaign["budget_micros"] / 1_000_000,
        "currency": campaign["currency"],
        "conversion_actions": list(campaign["conversion_actions"]),
        "locations": list(locations),
        "languages": list(languages),
        "headlines": list(ad["headlines"]) if ad else [],
        "descriptions": list(ad["descriptions"]) if ad else [],
        "images": [{"asset": a} for a in ad["images"]] if ad else [],
        "videos": [{"asset": a} for a in ad["videos"]] if ad else [],
        "final_app_urls": [dict(u) for u in ad.get("final_app_urls", [])] if ad else [],
        "brief": {"names": names},
    }


def describe_campaign(client, customer_id: str, campaign: str) -> dict:
    where = where_campaign(campaign)
    rows = gads.search(
        client,
        customer_id,
        "SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type, "
        "campaign.advertising_channel_sub_type, "
        "campaign.app_campaign_setting.app_id, campaign.app_campaign_setting.app_store, "
        "campaign.app_campaign_setting.bidding_strategy_goal_type, campaign.target_cpa.target_cpa_micros, "
        "campaign.selective_optimization.conversion_actions, campaign_budget.amount_micros, customer.currency_code "
        f"FROM campaign WHERE {where} AND campaign.status != 'REMOVED'",
    )
    if not rows:
        raise LookupError(f"no campaign matches {campaign!r}")
    if len(rows) > 1:
        raise LookupError(f"several campaigns are named {campaign!r}; use the campaign ID")
    r = rows[0]
    channel = r.campaign.advertising_channel_type.name
    if channel == "SEARCH":
        return describe_search_campaign(client, customer_id, r)
    if channel == "DEMAND_GEN":
        from .templates_demand_gen import describe_demand_gen_campaign  # here: that module imports this one

        return describe_demand_gen_campaign(client, customer_id, r)
    if channel == "PERFORMANCE_MAX":
        from .templates_pmax import describe_pmax_campaign  # here: that module imports this one

        return describe_pmax_campaign(client, customer_id, r)
    if channel != "MULTI_CHANNEL":
        raise ValueError(f"{r.campaign.name!r} is a {channel} campaign; only App (MULTI_CHANNEL), SEARCH, "
                         "DEMAND_GEN and PERFORMANCE_MAX campaigns can be described")
    by_id = f"campaign.id = {int(r.campaign.id)}"  # by name, a REMOVED namesake's rows would come along
    info = {
        "name": r.campaign.name,
        "subtype": r.campaign.advertising_channel_sub_type.name,
        "app_store": r.campaign.app_campaign_setting.app_store.name,
        "app_id": r.campaign.app_campaign_setting.app_id,
        "goal": r.campaign.app_campaign_setting.bidding_strategy_goal_type.name,
        "target_cpa_micros": r.campaign.target_cpa.target_cpa_micros,
        "conversion_actions": list(r.campaign.selective_optimization.conversion_actions),
        "budget_micros": r.campaign_budget.amount_micros,
        "currency": r.customer.currency_code,
    }
    criteria = gads.search(
        client,
        customer_id,
        "SELECT campaign_criterion.type, campaign_criterion.location.geo_target_constant, "
        "campaign_criterion.language.language_constant FROM campaign_criterion "
        f"WHERE {by_id} AND campaign_criterion.negative = FALSE "
        "AND campaign_criterion.type IN ('LOCATION', 'LANGUAGE')",
    )
    locations = [c.campaign_criterion.location.geo_target_constant for c in criteria if c.campaign_criterion.type_.name == "LOCATION"]
    languages = [c.campaign_criterion.language.language_constant for c in criteria if c.campaign_criterion.type_.name == "LANGUAGE"]
    ads = [
        {
            "ad": row.ad_group_ad.ad.resource_name,
            "ad_type": row.ad_group_ad.ad.type_.name,
            "ad_group": row.ad_group.name,
            "status": row.ad_group_ad.status.name,
            **ad_assets(row),
            "final_app_urls": [
                {"os_type": u.os_type.name, "url": u.url} for u in row.ad_group_ad.ad.final_app_urls
            ],
        }
        for row in gads.search(
            client, customer_id, f"SELECT {AD_FIELDS} FROM ad_group_ad WHERE {by_id} AND ad_group_ad.status != 'REMOVED'"
        )
        if row.ad_group_ad.ad.type_.name in APP_AD_TYPES
    ]
    found = google_names(client, customer_id, [*info["conversion_actions"], *locations, *languages])
    names = {resource: shown[0] for resource, shown in found.items()}
    first = next((a for a in ads if a["status"] == "ENABLED"), ads[0] if ads else None)
    return {
        "campaign": {"id": r.campaign.id, "name": r.campaign.name, "status": r.campaign.status.name, "subtype": info["subtype"]},
        "template_plan": build_template_plan(info, locations, languages, first, names),
        "ads": ads,
        "names": names,
    }


def _money(micros) -> float | None:
    return micros / 1_000_000 if micros else None


def plan_bidding(campaign, notes: list[str], supported: dict[str, str]) -> dict:
    """The plan's bidding fields from a Search, Demand Gen or Performance Max campaign's settings. `supported`
    maps Google's bidding strategy type to the plan's bidding; any other is noted and becomes MAXIMIZE_CONVERSIONS
    without targets. A plan kind gets `max_cpc` if it offers maximize clicks (TARGET_SPEND), `impression_share` if
    it offers impression-share bidding and `target_roas` if it offers maximize conversion value."""
    google = campaign.bidding_strategy_type.name
    bidding = supported.get(google)
    if bidding is None:
        notes.append(f"bidding {google} is not supported here; the template uses MAXIMIZE_CONVERSIONS")
        bidding = "MAXIMIZE_CONVERSIONS"
    if campaign.bidding_strategy:
        notes.append("the campaign uses a shared (portfolio) bid strategy; its targets are not copied")
    out = {"bidding": bidding, "target_cpa": None}
    if "TARGET_SPEND" in supported:
        out["max_cpc"] = None
    if "TARGET_IMPRESSION_SHARE" in supported:
        out["impression_share"] = None
    if "MAXIMIZE_CONVERSION_VALUE" in supported:
        out["target_roas"] = None
    if google not in supported:
        return out
    if google == "MAXIMIZE_CONVERSIONS":
        out["target_cpa"] = _money(campaign.maximize_conversions.target_cpa_micros)
    elif google == "TARGET_CPA":
        out["target_cpa"] = _money(campaign.target_cpa.target_cpa_micros)
    elif google == "TARGET_SPEND":
        out["max_cpc"] = _money(campaign.target_spend.cpc_bid_ceiling_micros)
    elif google == "MAXIMIZE_CONVERSION_VALUE":
        out["target_roas"] = campaign.maximize_conversion_value.target_roas or None
    elif google == "TARGET_IMPRESSION_SHARE":
        share = campaign.target_impression_share
        out["impression_share"] = {"location": share.location.name, "percent": share.location_fraction_micros / 10_000}
        out["max_cpc"] = _money(share.cpc_bid_ceiling_micros)
    return out


def _kinds(field_types: list[str]) -> str:
    """Asset field type names as words: ["SITELINK", "CALLOUT", "SITELINK"] -> "callout, sitelink"."""
    return ", ".join(sorted({name.lower().replace("_", " ") for name in field_types}))


def _listed(words: list[str]) -> str:
    """["a"] -> "a"; ["a", "b", "c"] -> "a, b and c"."""
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} and {words[-1]}"


def _bid_adjusted(criterion) -> bool:
    """True if a criterion carries a bid adjustment other than "no change" (an unset modifier or 1.0)."""
    pb = type(criterion).pb(criterion)
    return pb.HasField("bid_modifier") and pb.bid_modifier != 1.0


def _counted(type_names: list[str]) -> str:
    """Criterion type names as counted words: ["DEVICE", "AD_SCHEDULE", "DEVICE"] -> "1 ad schedule, 2 device"."""
    counts: dict[str, int] = {}
    for name in type_names:
        word = name.lower().replace("_", " ")
        counts[word] = counts.get(word, 0) + 1
    return ", ".join(f"{n} {word}" for word, n in sorted(counts.items()))


NOT_COPIED_TARGETING = "set it in Google Ads after creating the campaign"
_TRACKING = (("tracking_url_template", "tracking template"), ("final_url_suffix", "final URL suffix"),
             ("url_custom_parameters", "custom parameters"))


def _tracking_fields(resource: str) -> str:
    """The GAQL fields of a campaign's or ad group's tracking settings."""
    return ", ".join(f"{resource}.{field}" for field, _ in _TRACKING)


def _tracking(obj) -> list[str]:
    """The tracking settings a campaign or ad group has, in words. None of them is copied."""
    return [words for field, words in _TRACKING if getattr(obj, field)]


def _status(value) -> str:
    return value.name.lower()


def describe_search_campaign(client, customer_id: str, row) -> dict:
    """A Search campaign -> its settings, ads, and a ready-to-edit search_campaign plan. `row` is the
    campaign row describe_campaign read (id, name, status, budget, currency). Only what is ENABLED is
    copied (ad groups, keywords, campaign criteria and asset links), so the copy never switches on
    something that was paused; `notes` says what was left out and why."""
    by_id = f"campaign.id = {int(row.campaign.id)}"
    notes: list[str] = []
    settings = gads.search(
        client,
        customer_id,
        "SELECT campaign.bidding_strategy_type, campaign.bidding_strategy, "
        "campaign.maximize_conversions.target_cpa_micros, campaign.target_cpa.target_cpa_micros, "
        "campaign.target_spend.cpc_bid_ceiling_micros, campaign.target_impression_share.location, "
        "campaign.target_impression_share.location_fraction_micros, "
        "campaign.target_impression_share.cpc_bid_ceiling_micros, campaign.network_settings.target_search_network, "
        "campaign.network_settings.target_content_network, campaign.geo_target_type_setting.positive_geo_target_type, "
        f"campaign.ai_max_setting.enable_ai_max, {_tracking_fields('campaign')}, "
        f"campaign_budget.explicitly_shared FROM campaign WHERE {by_id}",
    )
    if len(settings) != 1:
        raise LookupError(f"expected one campaign with {by_id}, found {len(settings)}")
    campaign = settings[0].campaign
    bidding = plan_bidding(campaign, notes, SEARCH_BIDDING)
    if campaign.network_settings.target_content_network:
        notes.append("the campaign also shows on the Display Network; the template never does")
    option = campaign.geo_target_type_setting.positive_geo_target_type.name
    if option != "PRESENCE":
        notes.append(f"the campaign's location option is {option}; the copy uses people in or regularly in your "
                     "locations (PRESENCE)")
    if campaign.ai_max_setting.enable_ai_max:
        notes.append("AI Max is on in the campaign; the copy has AI Max off")
    tracking = _tracking(campaign)
    if tracking:
        notes.append(f"the campaign's {_listed(tracking)} {'are' if len(tracking) > 1 else 'is'} not copied; set "
                     f"{'them' if len(tracking) > 1 else 'it'} in Google Ads after creating the campaign")
    if settings[0].campaign_budget.explicitly_shared:
        notes.append("the campaign uses a shared budget; the copy gets a budget of its own")

    locations, languages, negatives, excluded, proximity, other = [], [], [], 0, 0, []
    for r in gads.search(  # every type, so what is not copied can be noted
        client,
        customer_id,
        "SELECT campaign.id, campaign_criterion.type, campaign_criterion.status, campaign_criterion.negative, "
        "campaign_criterion.bid_modifier, "
        "campaign_criterion.location.geo_target_constant, campaign_criterion.language.language_constant, "
        "campaign_criterion.keyword.text, campaign_criterion.keyword.match_type FROM campaign_criterion "
        f"WHERE {by_id} AND campaign_criterion.status != 'REMOVED'",
    ):
        cc = r.campaign_criterion
        kind = cc.type_.name
        if kind == "LOCATION" and cc.negative:
            excluded += 1
        elif kind == "PROXIMITY":
            proximity += 1
        elif kind == "DEVICE" and not _bid_adjusted(cc):
            continue  # Google lists every device on every campaign; only a changed bid is targeting
        elif kind not in ("LOCATION", "LANGUAGE") and not (kind == "KEYWORD" and cc.negative):
            other.append(kind)
        elif cc.status.name != "ENABLED":
            item = (f"location {cc.location.geo_target_constant}" if kind == "LOCATION"
                    else f"language {cc.language.language_constant}" if kind == "LANGUAGE"
                    else f"campaign negative keyword {cc.keyword.text!r}")
            notes.append(f"{_status(cc.status)} {item} not copied")
        elif kind == "LOCATION":
            locations.append(cc.location.geo_target_constant)
        elif kind == "LANGUAGE":
            languages.append(cc.language.language_constant)
        else:
            negatives.append({"text": cc.keyword.text, "match": cc.keyword.match_type.name})
    if excluded:
        notes.append(f"{excluded} excluded location(s) are not copied")
    if proximity:
        notes.append(f"{proximity} radius (proximity) target(s) are not copied; add the area as a location with "
                     "find_locations")
    if other:
        notes.append(f"campaign-level targeting is not copied: {_counted(other)}; {NOT_COPIED_TARGETING}")
    shared_lists = [r.shared_set.name for r in gads.search(
        client,
        customer_id,
        "SELECT campaign_shared_set.shared_set, shared_set.name FROM campaign_shared_set "
        f"WHERE {by_id} AND campaign_shared_set.status != 'REMOVED' AND shared_set.type = 'NEGATIVE_KEYWORDS'",
    )]
    if shared_lists:
        notes.append(f"{len(shared_lists)} shared negative keyword list(s) are not copied: "
                     f"{', '.join(map(repr, shared_lists))}")

    groups = []
    for g in gads.search(
        client,
        customer_id,
        "SELECT campaign.id, ad_group.id, ad_group.name, ad_group.status, ad_group.cpc_bid_micros, "
        f"{_tracking_fields('ad_group')} FROM ad_group WHERE {by_id} "
        "AND ad_group.status != 'REMOVED'",
    ):
        if g.ad_group.status.name != "ENABLED":
            notes.append(f"{_status(g.ad_group.status)} ad group {g.ad_group.name!r} not copied")
            continue
        groups.append(g)
        tracking = _tracking(g.ad_group)
        if tracking:
            notes.append(f"ad group {g.ad_group.name!r}: its {_listed(tracking)} "
                         f"{'are' if len(tracking) > 1 else 'is'} not copied")
    group_names = {g.ad_group.id: g.ad_group.name for g in groups}
    keywords: dict[int, dict[str, list]] = {}
    group_other = []
    for r in gads.search(  # every type, so what is not copied can be noted
        client,
        customer_id,
        "SELECT campaign.id, ad_group.id, ad_group.status, ad_group_criterion.type, ad_group_criterion.status, "
        "ad_group_criterion.negative, ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type "
        f"FROM ad_group_criterion WHERE {by_id} AND ad_group.status != 'REMOVED' "
        "AND ad_group_criterion.status != 'REMOVED'",
    ):
        crit = r.ad_group_criterion
        if r.ad_group.id not in group_names:
            continue  # an ad group that is not copied: its own note covers it
        if crit.type_.name != "KEYWORD":
            group_other.append(crit.type_.name)
            continue
        which = "negative keyword" if crit.negative else "keyword"
        if crit.status.name != "ENABLED":
            notes.append(f"{_status(crit.status)} {which} {crit.keyword.text!r} in ad group "
                         f"{group_names[r.ad_group.id]!r} not copied")
            continue
        found = keywords.setdefault(r.ad_group.id, {"keywords": [], "negative_keywords": []})
        found["negative_keywords" if crit.negative else "keywords"].append(
            {"text": crit.keyword.text, "match": crit.keyword.match_type.name})
    if group_other:
        notes.append(f"ad-group-level targeting is not copied: {_counted(group_other)}; {NOT_COPIED_TARGETING}")
    ads = [
        {
            "ad": r.ad_group_ad.ad.resource_name,
            "ad_type": "RESPONSIVE_SEARCH_AD",
            "ad_group": r.ad_group.name,
            "ad_group_id": r.ad_group.id,
            "status": r.ad_group_ad.status.name,
            **ad_assets(r),
            "final_urls": list(r.ad_group_ad.ad.final_urls),
            "path1": r.ad_group_ad.ad.responsive_search_ad.path1,
            "path2": r.ad_group_ad.ad.responsive_search_ad.path2,
            "pinned": _pinned(r),  # an asset_update would drop the pins, so it is refused for this ad
        }
        for r in gads.search(
            client,
            customer_id,
            f"SELECT {RSA_FIELDS} FROM ad_group_ad WHERE {by_id} AND ad_group.status != 'REMOVED' "
            "AND ad_group_ad.status != 'REMOVED' AND ad_group_ad.ad.type = 'RESPONSIVE_SEARCH_AD'",
        )
    ]
    if len(groups) > MAX_AD_GROUPS:
        notes.append(f"the campaign has {len(groups)} ad groups and a plan takes at most {MAX_AD_GROUPS}; "
                     "keep the best ones")
    template_groups = []
    for g in groups:
        gid, name = g.ad_group.id, g.ad_group.name
        mine = [a for a in ads if a["ad_group_id"] == gid]
        ad = next((a for a in mine if a["status"] == "ENABLED"), mine[0] if mine else None)
        if not any(a["status"] == "ENABLED" for a in mine):
            notes.append(f"ad group {name!r} has no enabled responsive search ad; "
                         + ("its ad is copied from a paused one" if ad else "write its ad"))
        if ad and not ad["final_urls"]:
            notes.append(f"ad group {name!r}: its ad has no final URL; add one")
        if ad and ad["pinned"]:
            notes.append(f"ad group {name!r}: the ad's pinned headlines or descriptions are copied without their pins")
        found = keywords.get(gid, {"keywords": [], "negative_keywords": []})
        template_groups.append({
            "name": g.ad_group.name,
            "keywords": found["keywords"],
            "negative_keywords": found["negative_keywords"],
            "cpc_bid": _money(g.ad_group.cpc_bid_micros) if bidding["bidding"] == "MANUAL_CPC" else None,
            "ad": {
                "headlines": list(ad["headlines"]) if ad else [],
                "descriptions": list(ad["descriptions"]) if ad else [],
                "final_url": ad["final_urls"][0] if ad and ad["final_urls"] else "",
                "path1": ad["path1"] if ad else "",
                "path2": ad["path2"] if ad else "",
            },
        })
    if bidding["bidding"] == "MANUAL_CPC" and template_groups:
        bidding["max_cpc"] = template_groups[0]["cpc_bid"]
    if bidding["bidding"] not in SMART_BIDDING:
        broad = sum(kw["match"] == "BROAD" for g in template_groups for kw in g["keywords"])
        if broad:
            notes.append(f"{broad} broad match keyword(s) need MAXIMIZE_CONVERSIONS or TARGET_CPA bidding; "
                         "change their match type or the bidding")

    sitelinks, callouts, snippets = [], [], []
    for r in gads.search(  # ADVERTISER: the person's own assets, not ones Google made automatically
        client,
        customer_id,
        "SELECT campaign.id, campaign_asset.field_type, campaign_asset.status, asset.resource_name, asset.final_urls, "
        "asset.sitelink_asset.link_text, asset.sitelink_asset.description1, asset.sitelink_asset.description2, "
        "asset.callout_asset.callout_text, asset.structured_snippet_asset.header, "
        "asset.structured_snippet_asset.values FROM campaign_asset "
        f"WHERE {by_id} AND campaign_asset.status != 'REMOVED' AND campaign_asset.source = 'ADVERTISER' "
        "AND campaign_asset.field_type IN ('SITELINK', 'CALLOUT', 'STRUCTURED_SNIPPET')",
    ):
        asset, kind = r.asset, r.campaign_asset.field_type.name
        if r.campaign_asset.status.name != "ENABLED":
            shown = {"SITELINK": asset.sitelink_asset.link_text, "CALLOUT": asset.callout_asset.callout_text,
                     "STRUCTURED_SNIPPET": asset.structured_snippet_asset.header}[kind]
            notes.append(f"{_status(r.campaign_asset.status)} {kind.lower().replace('_', ' ')} {shown!r} not copied")
        elif kind == "SITELINK":
            sitelinks.append({"text": asset.sitelink_asset.link_text,
                              "url": asset.final_urls[0] if asset.final_urls else "",
                              "desc1": asset.sitelink_asset.description1, "desc2": asset.sitelink_asset.description2})
        elif kind == "CALLOUT":
            callouts.append(asset.callout_asset.callout_text)
        elif kind == "STRUCTURED_SNIPPET":
            snippets.append({"header": asset.structured_snippet_asset.header,
                             "values": list(asset.structured_snippet_asset.values)})
    group_assets = [r.ad_group_asset.field_type.name for r in gads.search(
        client,
        customer_id,
        f"SELECT campaign.id, ad_group.status, ad_group_asset.field_type FROM ad_group_asset WHERE {by_id} AND ad_group.status != 'REMOVED' "
        "AND ad_group_asset.status != 'REMOVED' AND ad_group_asset.source = 'ADVERTISER'",
    )]
    if group_assets:
        notes.append(f"{len(group_assets)} ad-group-level asset(s) are not copied ({_kinds(group_assets)})")
    account_assets = [r.customer_asset.field_type.name for r in gads.search(
        client,
        customer_id,
        "SELECT customer_asset.field_type FROM customer_asset WHERE customer_asset.status != 'REMOVED' "
        "AND customer_asset.source = 'ADVERTISER'",
    )]
    if account_assets:
        notes.append(f"{len(account_assets)} account-level asset(s) are not copied ({_kinds(account_assets)}); "
                     "they stay on the account and can show with the new campaign")

    found_names = google_names(client, customer_id, [*locations, *languages])
    names = {resource: shown[0] for resource, shown in found_names.items()}
    template_plan = {
        "kind": "search_campaign",
        "name": f"{row.campaign.name} (copy)",
        "template": row.campaign.name,
        "daily_budget": row.campaign_budget.amount_micros / 1_000_000,
        "currency": row.customer.currency_code,
        **bidding,
        "search_partners": bool(campaign.network_settings.target_search_network),
        "locations": locations,
        "languages": languages,
        "negative_keywords": negatives,
        "ad_groups": template_groups,
        "sitelinks": sitelinks,
        "callouts": callouts,
        "snippets": snippets,
        "brief": {"names": names},
    }
    return {
        "campaign": {"id": row.campaign.id, "name": row.campaign.name, "status": row.campaign.status.name,
                     "channel": "SEARCH"},
        "template_plan": template_plan,
        "ads": ads,
        "names": names,
        "notes": notes,
    }


def list_campaigns(client, customer_id: str, name_contains: str = "", channel: str = "MULTI_CHANNEL") -> list[dict]:
    """Non-removed campaigns of one channel type, by name, so similar ones can be picked for insights."""
    if not re.fullmatch(r"[A-Z_]+", channel):
        raise ValueError("channel must look like MULTI_CHANNEL")
    # No name condition in GAQL: its LIKE is case-sensitive and treats [ ] _ % as pattern characters.
    # The name filter is a plain case-insensitive substring test in Python below.
    where = f"campaign.status != 'REMOVED' AND campaign.advertising_channel_type = {channel}"
    rows = gads.search(
        client,
        customer_id,
        "SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_sub_type, "
        "campaign.app_campaign_setting.bidding_strategy_goal_type, campaign.bidding_strategy_type, "
        "campaign_budget.amount_micros, campaign.target_cpa.target_cpa_micros, "
        f"campaign.maximize_conversions.target_cpa_micros FROM campaign WHERE {where}",
    )
    out = [
        {
            "id": r.campaign.id,
            "name": r.campaign.name,
            "status": r.campaign.status.name,
            "subtype": r.campaign.advertising_channel_sub_type.name,
            # an App campaign's goal; a Search, Demand Gen or Performance Max campaign's bidding strategy type
            # (TARGET_SPEND = maximize clicks)
            "goal": (r.campaign.bidding_strategy_type.name if channel in ("SEARCH", "DEMAND_GEN", "PERFORMANCE_MAX")
                     else r.campaign.app_campaign_setting.bidding_strategy_goal_type.name),
            "budget_micros": r.campaign_budget.amount_micros,
            "target_cpa_micros": (r.campaign.target_cpa.target_cpa_micros
                                  or r.campaign.maximize_conversions.target_cpa_micros),
        }
        for r in rows
        if name_contains.casefold() in r.campaign.name.casefold()
    ]
    return sorted(out, key=lambda c: c["name"])


def find_locations(client, names: list[str], country_code: str, locale: str = "en") -> list[dict]:
    return [
        {
            "resource": s.geo_target_constant.resource_name,
            "name": s.geo_target_constant.canonical_name,
            "type": s.geo_target_constant.target_type,
            "reach": s.reach,
            "searched": s.search_term,
        }
        for s in gads.suggest_locations(client, names, country_code, locale)
    ]
