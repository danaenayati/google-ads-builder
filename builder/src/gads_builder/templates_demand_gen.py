"""Read an existing Demand Gen campaign so a new one can copy it: its settings, ad groups, image and video
ads, and a ready-to-edit demand_gen_campaign plan. Only what is ENABLED is copied (ad groups and their
targeting), so the copy never switches on something that was paused; `notes` lists everything left out.

templates.describe_campaign hands every DEMAND_GEN campaign to this module."""

from __future__ import annotations

from . import gads
from .plans_demand_gen import CHANNELS, MAX_AD_GROUPS, MAX_ADS
from .templates import (
    NOT_COPIED_TARGETING, _bid_adjusted, _counted, _listed, _status, _tracking, _tracking_fields, google_names,
    plan_bidding,
)

# Google's bidding strategy type -> the demand_gen_campaign plan's bidding
DEMAND_GEN_BIDDING = {"MAXIMIZE_CONVERSIONS": "MAXIMIZE_CONVERSIONS", "TARGET_CPA": "TARGET_CPA",
                      "TARGET_SPEND": "MAXIMIZE_CLICKS"}
_CONTROLS = "ad_group.demand_gen_ad_group_settings.channel_controls"
GROUP_FIELDS = ", ".join([
    "campaign.id", "ad_group.id", "ad_group.name", "ad_group.status", "ad_group.optimized_targeting_enabled",
    f"{_CONTROLS}.channel_config", f"{_CONTROLS}.channel_strategy",
    *(f"{_CONTROLS}.selected_channels.{name}" for name in (*CHANNELS, "display", "maps")),
    _tracking_fields("ad_group"),
])
_IMAGE = "ad_group_ad.ad.demand_gen_multi_asset_ad"
_VIDEO = "ad_group_ad.ad.demand_gen_video_responsive_ad"
AD_FIELDS = ", ".join([
    "campaign.id", "ad_group.id", "ad_group.name", "ad_group.status", "ad_group_ad.status",
    "ad_group_ad.ad.resource_name", "ad_group_ad.ad.type", "ad_group_ad.ad.final_urls",
    "ad_group_ad.ad.final_mobile_urls", _tracking_fields("ad_group_ad.ad"),  # read only to note they aren't copied
    *(f"{_IMAGE}.{name}" for name in ("headlines", "descriptions", "business_name", "call_to_action_text",
                                      "marketing_images", "square_marketing_images", "portrait_marketing_images",
                                      "tall_portrait_marketing_images", "logo_images", "classic_display_images")),
    *(f"{_VIDEO}.{name}" for name in ("videos", "headlines", "long_headlines", "descriptions", "business_name",
                                      "logo_images", "call_to_actions", "companion_banners", "breadcrumb1",
                                      "breadcrumb2")),
])
_KEYS = {"LOCATION": "locations", "LANGUAGE": "languages", "USER_LIST": "audiences"}


def _settings_notes(campaign, budget, notes: list[str]) -> None:
    option = campaign.geo_target_type_setting.positive_geo_target_type.name
    if option != "PRESENCE":
        notes.append(f"the campaign's location option is {option}; the copy uses people in or regularly in your "
                     "locations (PRESENCE)")
    tracking = _tracking(campaign)
    if tracking:
        notes.append(f"the campaign's {_listed(tracking)} {'are' if len(tracking) > 1 else 'is'} not copied; set "
                     f"{'them' if len(tracking) > 1 else 'it'} in Google Ads after creating the campaign")
    if budget.explicitly_shared:
        notes.append("the campaign uses a shared budget; the copy gets a budget of its own")


def _goals(client, customer_id: str, by_id: str, notes: list[str]) -> list[dict]:
    """The campaign's biddable conversion goals; app goals stay out (Demand Gen here promotes websites)."""
    goals, app = [], 0
    for r in gads.search(client, customer_id, "SELECT campaign.id, campaign_conversion_goal.category, "
                                              "campaign_conversion_goal.origin, campaign_conversion_goal.biddable "
                                              f"FROM campaign_conversion_goal WHERE {by_id}"):
        goal = r.campaign_conversion_goal
        if not goal.biddable:
            continue
        if goal.origin.name == "APP":
            app += 1
        else:
            goals.append({"category": goal.category.name, "origin": goal.origin.name})
    if app:
        notes.append(f"{app} app conversion goal(s) are not copied: Demand Gen here promotes websites")
    if not goals:
        notes.append("the campaign bids on no website conversion goal; list conversion_goals")
    return goals


def _campaign_targeting(client, customer_id: str, by_id: str, notes: list[str]) -> dict[str, list[str]]:
    """The campaign's ENABLED locations and languages (used when it predates upgraded targeting); every other
    kind of campaign-level targeting is noted, not copied."""
    out: dict[str, list[str]] = {"locations": [], "languages": []}
    excluded, other = 0, []
    for r in gads.search(  # every type, so what is not copied can be noted
        client, customer_id,
        "SELECT campaign.id, campaign_criterion.type, campaign_criterion.status, campaign_criterion.negative, "
        "campaign_criterion.bid_modifier, campaign_criterion.location.geo_target_constant, "
        f"campaign_criterion.language.language_constant FROM campaign_criterion WHERE {by_id} "
        "AND campaign_criterion.status != 'REMOVED'",
    ):
        cc, kind = r.campaign_criterion, r.campaign_criterion.type_.name
        if kind == "DEVICE" and not _bid_adjusted(cc):
            continue  # Google lists every device on every campaign; only a changed bid is targeting
        if kind not in ("LOCATION", "LANGUAGE"):
            other.append(kind)
        elif cc.negative:
            excluded += 1
        elif cc.status.name != "ENABLED":
            value = cc.location.geo_target_constant if kind == "LOCATION" else cc.language.language_constant
            notes.append(f"{_status(cc.status)} {kind.lower()} {value} not copied")
        else:
            out[_KEYS[kind]].append(cc.location.geo_target_constant if kind == "LOCATION"
                                    else cc.language.language_constant)
    if excluded:
        notes.append(f"{excluded} excluded campaign location(s) or language(s) are not copied")
    if other:
        notes.append(f"campaign-level targeting is not copied: {_counted(other)}; {NOT_COPIED_TARGETING}")
    return out


def _group_targeting(client, customer_id: str, by_id: str, names: dict[int, str],
                     notes: list[str]) -> dict[int, dict[str, list[str]]]:
    """Each copied ad group's ENABLED locations, languages and user lists. Exclusions, paused criteria and
    other kinds of targeting are noted, not copied."""
    found: dict[int, dict[str, list[str]]] = {}
    excluded, other = 0, []
    for r in gads.search(
        client, customer_id,
        "SELECT campaign.id, ad_group.id, ad_group.status, ad_group_criterion.type, ad_group_criterion.status, "
        "ad_group_criterion.negative, ad_group_criterion.location.geo_target_constant, "
        "ad_group_criterion.language.language_constant, ad_group_criterion.user_list.user_list "
        f"FROM ad_group_criterion WHERE {by_id} AND ad_group.status != 'REMOVED' "
        "AND ad_group_criterion.status != 'REMOVED'",
    ):
        if r.ad_group.id not in names:
            continue  # an ad group that is not copied: its own note covers it
        crit, kind = r.ad_group_criterion, r.ad_group_criterion.type_.name
        if kind not in _KEYS:
            other.append(kind)
            continue
        value = {"LOCATION": crit.location.geo_target_constant, "LANGUAGE": crit.language.language_constant,
                 "USER_LIST": crit.user_list.user_list}[kind]
        if crit.negative:
            excluded += 1
        elif crit.status.name != "ENABLED":
            notes.append(f"{_status(crit.status)} {kind.lower().replace('_', ' ')} {value} in ad group "
                         f"{names[r.ad_group.id]!r} not copied")
        else:
            found.setdefault(r.ad_group.id, {key: [] for key in _KEYS.values()})[_KEYS[kind]].append(value)
    if excluded:
        notes.append(f"{excluded} excluded location(s), language(s) or list(s) are not copied")
    if other:
        notes.append(f"ad-group-level targeting is not copied: {_counted(other)}; {NOT_COPIED_TARGETING}")
    return found


def _channels(row, notes: list[str]) -> list[str]:
    controls = row.ad_group.demand_gen_ad_group_settings.channel_controls
    name = row.ad_group.name
    if controls.channel_config.name == "SELECTED_CHANNELS":
        chosen = controls.selected_channels
        off_plan = [c for c in ("display", "maps") if getattr(chosen, c)]
        if off_plan:
            notes.append(f"ad group {name!r} also runs on {' and '.join(off_plan)}; the copy doesn't")
        return [c for c in CHANNELS if getattr(chosen, c)]
    if controls.channel_strategy.name == "ALL_OWNED_AND_OPERATED_CHANNELS":
        notes.append(f"ad group {name!r} lets Google choose among its own channels; the copy names "
                     "YouTube, Discover and Gmail")
    else:
        notes.append(f"ad group {name!r} uses all channels, including third-party Display; the copy uses "
                     "YouTube, Discover and Gmail only")
    return list(CHANNELS)


def _texts(items) -> list[str]:
    return [item.text for item in items]


def _assets(items) -> list[dict]:
    return [{"asset": item.asset} for item in items]


def _plan_ad(row, notes: list[str]) -> dict | None:
    """One existing ad as a plan ad, or None (with a note) for a kind the plan can't hold."""
    ad, kind = row.ad_group_ad.ad, row.ad_group_ad.ad.type_.name
    where = f"ad {ad.resource_name}"
    if kind not in ("DEMAND_GEN_MULTI_ASSET_AD", "DEMAND_GEN_VIDEO_RESPONSIVE_AD"):
        notes.append(f"{where} is a {kind} and is not copied")
        return None
    final_url = ad.final_urls[0] if ad.final_urls else ""
    if not final_url:
        notes.append(f"{where} has no final URL; add one")
    if ad.final_mobile_urls:
        notes.append(f"{where}: its mobile final URLs are not copied")
    tracking = _tracking(ad)
    if tracking:
        many = len(tracking) > 1
        notes.append(f"{where}: its {_listed(tracking)} {'are' if many else 'is'} not copied; set "
                     f"{'them' if many else 'it'} in Google Ads after creating the campaign")
    if kind == "DEMAND_GEN_MULTI_ASSET_AD":
        info = ad.demand_gen_multi_asset_ad
        if info.tall_portrait_marketing_images:
            notes.append(f"{where}: {len(info.tall_portrait_marketing_images)} 9:16 image(s) are not copied")
        if info.classic_display_images:
            notes.append(f"{where}: {len(info.classic_display_images)} classic display image(s) are not copied")
        if info.call_to_action_text:
            notes.append(f"{where}: its call to action {info.call_to_action_text!r} is not copied; Google chooses one")
        return {
            "type": "image", "headlines": _texts(info.headlines), "descriptions": _texts(info.descriptions),
            "business_name": info.business_name, "final_url": final_url,
            "marketing_images": _assets(info.marketing_images),
            "square_marketing_images": _assets(info.square_marketing_images),
            "portrait_marketing_images": _assets(info.portrait_marketing_images),
            "logo_images": _assets(info.logo_images),
        }
    info = ad.demand_gen_video_responsive_ad
    if info.call_to_actions:
        notes.append(f"{where}: its call to action is not copied; set call_to_action")
    if info.companion_banners:
        notes.append(f"{where}: its companion banner is not copied")
    if info.breadcrumb1 or info.breadcrumb2:
        notes.append(f"{where}: its breadcrumbs are not copied")
    return {
        "type": "video", "videos": _assets(info.videos), "headlines": _texts(info.headlines),
        "long_headlines": _texts(info.long_headlines), "descriptions": _texts(info.descriptions),
        "business_name": info.business_name.text, "final_url": final_url, "logo_images": _assets(info.logo_images),
        "call_to_action": None,
    }


def _copied_groups(client, customer_id: str, by_id: str, notes: list[str]) -> list:
    """The ENABLED ad groups (the others are noted), each with its own notes."""
    groups = []
    for g in gads.search(client, customer_id, f"SELECT {GROUP_FIELDS} FROM ad_group WHERE {by_id} "
                                              "AND ad_group.status != 'REMOVED'"):
        name = g.ad_group.name
        if g.ad_group.status.name != "ENABLED":
            notes.append(f"{_status(g.ad_group.status)} ad group {name!r} not copied")
            continue
        groups.append(g)
        tracking = _tracking(g.ad_group)
        if tracking:
            notes.append(f"ad group {name!r}: its {_listed(tracking)} {'are' if len(tracking) > 1 else 'is'} not copied")
        if g.ad_group.optimized_targeting_enabled:
            notes.append(f"ad group {name!r} has optimized targeting on; the copy doesn't turn it on, but Google may; "
                         "check quality_report after creation")
    if len(groups) > MAX_AD_GROUPS:
        notes.append(f"the campaign has {len(groups)} enabled ad groups and a plan takes at most {MAX_AD_GROUPS}; "
                     "keep the best ones")
    return groups


def describe_demand_gen_campaign(client, customer_id: str, row) -> dict:
    """A Demand Gen campaign -> its settings, ads, and a ready-to-edit demand_gen_campaign plan. `row` is the
    campaign row describe_campaign read (id, name, status, budget, currency)."""
    by_id = f"campaign.id = {int(row.campaign.id)}"
    notes: list[str] = []
    settings = gads.search(
        client, customer_id,
        "SELECT campaign.id, campaign.bidding_strategy_type, campaign.bidding_strategy, "
        "campaign.maximize_conversions.target_cpa_micros, campaign.target_cpa.target_cpa_micros, "
        "campaign.target_spend.cpc_bid_ceiling_micros, campaign.demand_gen_campaign_settings.upgraded_targeting, "
        f"campaign.geo_target_type_setting.positive_geo_target_type, {_tracking_fields('campaign')}, "
        f"campaign_budget.explicitly_shared FROM campaign WHERE {by_id}",
    )
    if len(settings) != 1:
        raise LookupError(f"expected one campaign with {by_id}, found {len(settings)}")
    campaign = settings[0].campaign
    bidding = plan_bidding(campaign, notes, DEMAND_GEN_BIDDING)
    # Google refuses a target with maximize conversions, or a CPC ceiling with maximize clicks, on a new Demand Gen
    # campaign: the first becomes target CPA with that target, the second plain maximize clicks
    if bidding["bidding"] == "MAXIMIZE_CONVERSIONS" and bidding["target_cpa"]:
        bidding["bidding"] = "TARGET_CPA"
        notes.append(f"the campaign maximizes conversions with a target cost per conversion of "
                     f"{bidding['target_cpa']:g}; Demand Gen takes that as bidding TARGET_CPA")
    if bidding["bidding"] == "MAXIMIZE_CLICKS" and bidding["max_cpc"]:
        notes.append(f"the campaign's maximum CPC of {bidding['max_cpc']:g} is not copied; Demand Gen doesn't accept "
                     f"one with MAXIMIZE_CLICKS")
        bidding["max_cpc"] = None
    if not row.campaign_budget.amount_micros:
        notes.append("the campaign has a campaign total budget, not a daily one; set daily_budget")
    _settings_notes(campaign, settings[0].campaign_budget, notes)
    goals = _goals(client, customer_id, by_id, notes)
    campaign_targeting = _campaign_targeting(client, customer_id, by_id, notes)
    upgraded = campaign.demand_gen_campaign_settings.upgraded_targeting
    if not upgraded:
        notes.append("the campaign targets locations and languages at campaign level; the copy puts them on each "
                     "ad group (upgraded targeting)")
    else:
        at_campaign = (["LOCATION"] * len(campaign_targeting["locations"])
                       + ["LANGUAGE"] * len(campaign_targeting["languages"]))
        if at_campaign:
            notes.append(f"campaign-level targeting is not copied: {_counted(at_campaign)}; with upgraded targeting "
                         "the copy uses each ad group's own locations and languages, so add them there if they matter")
    groups = _copied_groups(client, customer_id, by_id, notes)
    names = {g.ad_group.id: g.ad_group.name for g in groups}
    targeting = _group_targeting(client, customer_id, by_id, names, notes)
    ad_rows = gads.search(client, customer_id, f"SELECT {AD_FIELDS} FROM ad_group_ad WHERE {by_id} "
                                               "AND ad_group.status != 'REMOVED' AND ad_group_ad.status != 'REMOVED'")
    ads = [{"ad": r.ad_group_ad.ad.resource_name, "ad_type": r.ad_group_ad.ad.type_.name, "ad_group": r.ad_group.name,
            "status": r.ad_group_ad.status.name} for r in ad_rows]
    template_groups = []
    for g in groups:
        gid, name = g.ad_group.id, g.ad_group.name
        mine = [r for r in ad_rows if r.ad_group.id == gid]
        enabled = [r for r in mine if r.ad_group_ad.status.name == "ENABLED"]
        if mine and not enabled:
            notes.append(f"ad group {name!r} has no enabled ad; its ads are copied from paused ones")
        plan_ads = [a for a in (_plan_ad(r, notes) for r in (enabled or mine)) if a is not None]
        if len(plan_ads) > MAX_ADS:
            notes.append(f"ad group {name!r} has {len(plan_ads)} ads and a plan takes at most {MAX_ADS}; keep the best")
        if not plan_ads:
            notes.append(f"ad group {name!r} has no image or video ad; write one")
        own = targeting.get(gid, {key: [] for key in _KEYS.values()})
        template_groups.append({
            "name": name,
            "locations": list(own["locations"] if upgraded else campaign_targeting["locations"]),
            "languages": list(own["languages"] if upgraded else campaign_targeting["languages"]),
            "channels": _channels(g, notes),
            "audiences": list(own["audiences"]),
            "ads": plan_ads,
        })
    used = [x for group in template_groups for key in ("locations", "languages", "audiences") for x in group[key]]
    labels = {resource: shown[0] for resource, shown in google_names(client, customer_id, used).items()}
    template_plan = {
        "kind": "demand_gen_campaign",
        "name": f"{row.campaign.name} (copy)",
        "template": row.campaign.name,
        "daily_budget": row.campaign_budget.amount_micros / 1_000_000,
        "currency": row.customer.currency_code,
        **bidding,
        "conversion_goals": goals,
        "ad_groups": template_groups,
        "brief": {"names": labels},
    }
    return {
        "campaign": {"id": row.campaign.id, "name": row.campaign.name, "status": row.campaign.status.name,
                     "channel": "DEMAND_GEN"},
        "template_plan": template_plan,
        "ads": ads,
        "names": labels,
        "notes": notes,
    }
