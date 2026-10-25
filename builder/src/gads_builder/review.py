"""After creation (still paused): Google's Ad Strength, approval status and asset labels, and for a Demand
Gen or Performance Max campaign the settings Google may have chosen itself (optimized targeting has no "off" the
API can send; image extraction follows an account setting; Google may add assets of its own)."""

from __future__ import annotations

from . import gads
from .plans_demand_gen import CHANNELS, GENERATED_CREATIVES
from .plans_pmax import AUTOMATIONS
from .templates import where_campaign

_CONTROLS = "ad_group.demand_gen_ad_group_settings.channel_controls"
_GROUP_FIELDS = ", ".join([
    "campaign.id", "ad_group.name", "ad_group.status", "ad_group.optimized_targeting_enabled",
    "ad_group.exclude_demographic_expansion",
    f"{_CONTROLS}.channel_config", f"{_CONTROLS}.channel_strategy",
    *(f"{_CONTROLS}.selected_channels.{name}" for name in (*CHANNELS, "display", "maps")),
])
# Google's ad type -> the generated creatives the builder opts it out of (the same constants compile uses)
EXPECTED_OPT_OUTS = {"DEMAND_GEN_MULTI_ASSET_AD": GENERATED_CREATIVES["image"],
                     "DEMAND_GEN_VIDEO_RESPONSIVE_AD": GENERATED_CREATIVES["video"]}
_STRATEGY_WORDS = {"ALL_CHANNELS": "all channels, including third-party Display"}


def quality_report(client, customer_id: str, campaign: str) -> dict:
    where = where_campaign(campaign)
    ads = gads.search(
        client,
        customer_id,
        "SELECT campaign.name, campaign.status, ad_group_ad.ad.resource_name, ad_group_ad.ad.type, "
        "ad_group_ad.ad_strength, ad_group_ad.action_items, ad_group_ad.primary_status, "
        "ad_group_ad.primary_status_reasons, ad_group_ad.policy_summary.approval_status, "
        "ad_group_ad.policy_summary.policy_topic_entries "
        f"FROM ad_group_ad WHERE {where} AND campaign.status != 'REMOVED' AND ad_group_ad.status != 'REMOVED'",
    )
    assets = gads.search(
        client,
        customer_id,
        "SELECT ad_group_ad_asset_view.field_type, ad_group_ad_asset_view.performance_label, "
        "asset.resource_name, asset.text_asset.text FROM ad_group_ad_asset_view "
        f"WHERE {where} AND campaign.status != 'REMOVED' AND ad_group_ad_asset_view.enabled = TRUE",
    )
    campaigns = gads.search(
        client,
        customer_id,
        f"SELECT campaign.id, campaign.name, campaign.advertising_channel_type FROM campaign WHERE {where} "
        "AND campaign.status != 'REMOVED'",
    )
    demand_gen = [demand_gen_settings(client, customer_id, r.campaign.id, r.campaign.name) for r in campaigns
                  if r.campaign.advertising_channel_type.name == "DEMAND_GEN"]
    pmax = [pmax_settings(client, customer_id, r.campaign.id, r.campaign.name) for r in campaigns
            if r.campaign.advertising_channel_type.name == "PERFORMANCE_MAX"]
    return {
        **({"demand_gen": demand_gen} if demand_gen else {}),
        **({"pmax": pmax} if pmax else {}),
        "ads": [
            {
                "campaign": r.campaign.name,
                "campaign_status": r.campaign.status.name,
                "ad": r.ad_group_ad.ad.resource_name,
                "ad_type": r.ad_group_ad.ad.type_.name,
                "ad_strength": r.ad_group_ad.ad_strength.name,
                "approval": r.ad_group_ad.policy_summary.approval_status.name,
                "ad_strength_suggestions": list(r.ad_group_ad.action_items),
                "primary_status": r.ad_group_ad.primary_status.name,
                "primary_status_reasons": [x.name for x in r.ad_group_ad.primary_status_reasons],
                "policy_topics": [e.topic for e in r.ad_group_ad.policy_summary.policy_topic_entries],
            }
            for r in ads
        ],
        "assets": [
            {
                "field": r.ad_group_ad_asset_view.field_type.name,
                "label": r.ad_group_ad_asset_view.performance_label.name,
                "asset": r.asset.resource_name,
                "text": r.asset.text_asset.text,
            }
            for r in assets
        ],
    }


def demand_gen_settings(client, customer_id: str, campaign_id: int, name: str) -> dict:
    """What a Demand Gen campaign really has, read back after creation, and `to_review`: every setting that
    differs from what the builder sends (Google may turn optimized targeting on by default)."""
    by_id = f"campaign.id = {int(campaign_id)}"
    review: list[str] = []
    rows = gads.search(client, customer_id,
                       "SELECT campaign.id, campaign.demand_gen_campaign_settings.upgraded_targeting, "
                       f"campaign.geo_target_type_setting.positive_geo_target_type FROM campaign WHERE {by_id}")
    if len(rows) != 1:
        raise LookupError(f"expected one campaign with {by_id}, found {len(rows)}")
    row = rows[0]
    upgraded = row.campaign.demand_gen_campaign_settings.upgraded_targeting
    option = row.campaign.geo_target_type_setting.positive_geo_target_type.name
    if not upgraded:
        review.append("upgraded targeting is off: locations and languages are set on the campaign")
    if option != "PRESENCE":
        review.append(f"the location option is {option}, not people in or regularly in the locations (PRESENCE)")
    goals: dict[str, list[str]] = {"bid_on": [], "not_bid_on": []}
    for r in gads.search(client, customer_id,
                         "SELECT campaign.id, campaign_conversion_goal.category, campaign_conversion_goal.origin, "
                         f"campaign_conversion_goal.biddable FROM campaign_conversion_goal WHERE {by_id}"):
        g = r.campaign_conversion_goal
        label = f"{g.category.name} ({g.origin.name})"
        goals["bid_on" if g.biddable else "not_bid_on"].append(label)
        if g.biddable and g.origin.name == "APP":
            review.append(f"the campaign bids on the app conversion goal {label}; the builder never does: switch it "
                          "off for this campaign in Google Ads")
    if not goals["bid_on"]:
        review.append("the campaign bids on no conversion goal: switch the plan's goals on for this campaign in "
                      "Google Ads")
    groups = []
    for r in gads.search(client, customer_id,
                         f"SELECT {_GROUP_FIELDS} FROM ad_group WHERE {by_id} AND ad_group.status != 'REMOVED'"):
        g = r.ad_group
        controls = g.demand_gen_ad_group_settings.channel_controls
        if controls.channel_config.name == "SELECTED_CHANNELS":
            channels = [c for c in (*CHANNELS, "display", "maps") if getattr(controls.selected_channels, c)]
        else:
            channels = [controls.channel_strategy.name]
        groups.append({"name": g.name, "optimized_targeting": g.optimized_targeting_enabled,
                       "demographic_expansion_excluded": g.exclude_demographic_expansion, "channels": channels})
        if g.optimized_targeting_enabled:
            review.append(f"ad group {g.name!r} has optimized targeting on (Google's default; the API can't send "
                          "'off'): switch it off in Google Ads before you switch the campaign on")
        if not g.exclude_demographic_expansion:
            review.append(f"ad group {g.name!r} allows demographic expansion: exclude demographic expansion in "
                          "Google Ads")
        if not set(channels) <= set(CHANNELS):
            other = sorted(set(channels) - set(CHANNELS))
            review.append(f"ad group {g.name!r} runs on {', '.join(_STRATEGY_WORDS.get(c, c) for c in other)}")
    ads = []
    for r in gads.search(client, customer_id,
                         "SELECT campaign.id, ad_group_ad.status, ad_group_ad.ad.resource_name, ad_group_ad.ad.type, "
                         "ad_group_ad.ad_group_ad_asset_automation_settings "
                         f"FROM ad_group_ad WHERE {by_id} AND ad_group_ad.status != 'REMOVED'"):
        ad, ad_type = r.ad_group_ad.ad.resource_name, r.ad_group_ad.ad.type_.name
        generated = {s.asset_automation_type.name: s.asset_automation_status.name
                     for s in r.ad_group_ad.ad_group_ad_asset_automation_settings}
        ads.append({"ad": ad, "ad_type": ad_type, "generated_creatives": generated})
        review += _generated_problems(ad, EXPECTED_OPT_OUTS.get(ad_type, ()), generated)
    return {"campaign": name, "upgraded_targeting": upgraded, "location_option": option,
            "conversion_goals": goals, "ad_groups": groups, "ads": ads, "to_review": review}


def _generated_problems(ad: str, expected: tuple[str, ...], generated: dict[str, str]) -> list[str]:
    """Every generated creative Google reports as on, and every one the builder opts this ad type out of that is
    missing or not opted out (Google's default then applies)."""
    out = []
    not_out = sorted(k for k in expected if generated.get(k) not in ("OPTED_OUT", "OPTED_IN"))
    if not_out:
        out.append(f"ad {ad} is not opted out of {', '.join(not_out)}: Google's default applies; opt out in Google Ads")
    opted_in = sorted(k for k, v in generated.items() if v == "OPTED_IN")
    if opted_in:
        out.append(f"ad {ad} lets Google generate: {', '.join(opted_in)}")
    return out


_WEAK = ("POOR", "AVERAGE")  # Ad Strength that gets a to_review entry with Google's suggestions


def _goal_review(client, customer_id: str, by_id: str, review: list[str]) -> dict[str, list[str]]:
    """The campaign's conversion goals, bid on or not, and to_review entries for an app goal bid on, no goal bid on
    and a custom goal (the builder sets none)."""
    goals: dict[str, list[str]] = {"bid_on": [], "not_bid_on": []}
    for r in gads.search(client, customer_id,
                         "SELECT campaign.id, campaign_conversion_goal.category, campaign_conversion_goal.origin, "
                         f"campaign_conversion_goal.biddable FROM campaign_conversion_goal WHERE {by_id}"):
        g = r.campaign_conversion_goal
        label = f"{g.category.name} ({g.origin.name})"
        goals["bid_on" if g.biddable else "not_bid_on"].append(label)
        if g.biddable and g.origin.name == "APP":
            review.append(f"the campaign bids on the app conversion goal {label}; the builder never does: switch it "
                          "off for this campaign in Google Ads")
    if not goals["bid_on"]:
        review.append("the campaign bids on no conversion goal: switch the plan's goals on for this campaign in "
                      "Google Ads")
    if any(r.conversion_goal_campaign_config.custom_conversion_goal for r in gads.search(
            client, customer_id, "SELECT campaign.id, conversion_goal_campaign_config.custom_conversion_goal "
                                 f"FROM conversion_goal_campaign_config WHERE {by_id}")):
        review.append("the campaign bids on a custom conversion goal; the builder never sets one: check its goals in "
                      "Google Ads")
    return goals


def _counted(names: list[str]) -> str:
    counts: dict[str, int] = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    return ", ".join(f"{n} {name}" for name, n in sorted(counts.items()))


def pmax_settings(client, customer_id: str, campaign_id: int, name: str) -> dict:
    """What a Performance Max campaign really has, read back after creation, and `to_review`: every setting that
    differs from what the builder sends (brand guidelines, the asset automations it opts out of, the location
    option, the goals), image extraction (the account's setting), each asset group's final URLs and weak Ad
    Strength with Google's suggestions, and every asset Google added itself."""
    by_id = f"campaign.id = {int(campaign_id)}"
    review: list[str] = []
    rows = gads.search(client, customer_id,
                       "SELECT campaign.id, campaign.brand_guidelines_enabled, campaign.asset_automation_settings, "
                       f"campaign.geo_target_type_setting.positive_geo_target_type FROM campaign WHERE {by_id}")
    if len(rows) != 1:
        raise LookupError(f"expected one campaign with {by_id}, found {len(rows)}")
    campaign = rows[0].campaign
    brand_guidelines = campaign.brand_guidelines_enabled
    automation = {s.asset_automation_type.name: s.asset_automation_status.name
                  for s in campaign.asset_automation_settings}
    option = campaign.geo_target_type_setting.positive_geo_target_type.name
    if not brand_guidelines:
        review.append("brand guidelines are off: the builder creates the campaign with them on; check the business "
                      "name and logos in Google Ads")
    not_out = [kind for kind in AUTOMATIONS if automation.get(kind) != "OPTED_OUT"]
    if not_out:
        review.append(f"the campaign is not opted out of {', '.join(AUTOMATIONS[k] for k in not_out)} "
                      f"({', '.join(not_out)}): Google's default applies; opt out in the campaign's asset optimization "
                      "settings in Google Ads")
    if automation.get("GENERATE_IMAGE_EXTRACTION") != "OPTED_OUT":
        review.append("Google may take images from the landing page (image extraction follows the account's setting, "
                      "which only the Google Ads UI changes): switch it off there if you don't want it")
    if option != "PRESENCE":
        review.append(f"the location option is {option}, not people in or regularly in the locations (PRESENCE)")
    goals = _goal_review(client, customer_id, by_id, review)
    brand: dict[str, int] = {"BUSINESS_NAME": 0, "LOGO": 0, "LANDSCAPE_LOGO": 0}
    generated: list[str] = []
    for r in gads.search(client, customer_id,
                         "SELECT campaign.id, campaign_asset.field_type, campaign_asset.status, campaign_asset.source "
                         f"FROM campaign_asset WHERE {by_id} AND campaign_asset.status != 'REMOVED'"):
        kind = r.campaign_asset.field_type.name
        if r.campaign_asset.source.name == "AUTOMATICALLY_CREATED":
            generated.append(kind.lower().replace("_", " "))
        elif kind in brand and r.campaign_asset.status.name == "ENABLED":
            brand[kind] += 1
    if not brand["BUSINESS_NAME"] or not brand["LOGO"]:
        review.append("the campaign has no enabled business name or logo of its own: add them in Google Ads")
    if generated:
        review.append(f"Google added campaign assets of its own ({_counted(generated)}): remove them in Google Ads if "
                      "you don't want them")
    added: dict[str, list[str]] = {}
    for r in gads.search(client, customer_id,
                         "SELECT campaign.id, asset_group.name, asset_group.status, asset_group_asset.field_type, "
                         "asset_group_asset.status, asset_group_asset.source FROM asset_group_asset "
                         f"WHERE {by_id} AND asset_group.status != 'REMOVED' AND asset_group_asset.status != 'REMOVED' "
                         "AND asset_group_asset.source = 'AUTOMATICALLY_CREATED'"):
        added.setdefault(r.asset_group.name, []).append(r.asset_group_asset.field_type.name.lower().replace("_", " "))
    groups = []
    for r in gads.search(client, customer_id,
                         "SELECT campaign.id, asset_group.name, asset_group.status, asset_group.ad_strength, "
                         "asset_group.primary_status, asset_group.final_urls, asset_group.final_mobile_urls, "
                         "asset_group.asset_coverage.ad_strength_action_items FROM asset_group "
                         f"WHERE {by_id} AND asset_group.status != 'REMOVED'"):
        g = r.asset_group
        to_add = []
        for item in g.asset_coverage.ad_strength_action_items:
            details = item.add_asset_details
            shape = details.video_aspect_ratio_requirement.name
            to_add.append(f"{details.asset_count} {details.asset_field_type.name}"
                          + (f" ({shape})" if shape not in ("UNSPECIFIED", "UNKNOWN") else ""))
        groups.append({"name": g.name, "status": g.status.name, "ad_strength": g.ad_strength.name,
                       "primary_status": g.primary_status.name, "final_urls": list(g.final_urls),
                       "add_assets": to_add, "generated_assets": sorted(added.get(g.name, []))})
        if len(g.final_urls) != 1 or g.final_mobile_urls:
            review.append(f"asset group {g.name!r} has {len(g.final_urls)} final URL(s) and "
                          f"{len(g.final_mobile_urls)} mobile URL(s); the builder sends exactly one final URL")
        if g.ad_strength.name in _WEAK:
            hint = f": Google suggests adding {', '.join(to_add)}" if to_add else ""
            review.append(f"asset group {g.name!r} has {g.ad_strength.name} Ad Strength{hint} (a new asset_update "
                          "can't change asset groups yet: add them in Google Ads)")
        if g.name in added:
            review.append(f"asset group {g.name!r} has assets Google added itself ({_counted(added[g.name])}): remove "
                          "them in Google Ads if you don't want them")
    return {"campaign": name, "brand_guidelines": brand_guidelines, "asset_automation": automation,
            "location_option": option, "conversion_goals": goals,
            "brand": {"business_names": brand["BUSINESS_NAME"], "logos": brand["LOGO"],
                      "landscape_logos": brand["LANDSCAPE_LOGO"]},
            "asset_groups": groups, "to_review": review}
