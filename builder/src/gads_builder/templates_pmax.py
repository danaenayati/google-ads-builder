"""Read an existing Performance Max campaign so a new one can copy it: its settings, brand assets, asset groups
and their assets and signals, and a ready-to-edit pmax_campaign plan. Only what is ENABLED and the
advertiser's own is copied (asset groups, their assets, targeting), so the copy never switches on something
that was paused; `notes` lists everything left out (such as brand colors and font, text guidelines, extra or
unusable audience signals, and disapproved or surplus search themes).

templates.describe_campaign hands every PERFORMANCE_MAX campaign to this module."""

from __future__ import annotations

import re

from . import gads
from .plans_pmax import AUTOMATIONS, MAX_ALL_LOGOS, MAX_ASSET_GROUPS, SEARCH_THEMES
from .templates import (
    _counted, _kinds, _listed, _status, _tracking, _tracking_fields, google_names, in_list, plan_bidding,
)
from .templates_demand_gen import _campaign_targeting

# Google's bidding strategy type -> the pmax_campaign plan's bidding
PMAX_BIDDING = {"MAXIMIZE_CONVERSIONS": "MAXIMIZE_CONVERSIONS", "MAXIMIZE_CONVERSION_VALUE": "MAXIMIZE_CONVERSION_VALUE"}
# asset group field types (AssetFieldTypeEnum) -> the plan's list
GROUP_LISTS = {"HEADLINE": "headlines", "LONG_HEADLINE": "long_headlines", "DESCRIPTION": "descriptions",
               "MARKETING_IMAGE": "marketing_images", "SQUARE_MARKETING_IMAGE": "square_marketing_images",
               "PORTRAIT_MARKETING_IMAGE": "portrait_marketing_images", "YOUTUBE_VIDEO": "videos"}
BRAND = {"BUSINESS_NAME": "business_name", "LOGO": "logos", "LANDSCAPE_LOGO": "landscape_logos"}
_TEXT_LISTS = ("headlines", "long_headlines", "descriptions")
SET_LATER = "is not copied: set it in Google Ads after creating the campaign"
# Campaign.brand_guidelines fields (v26) -> what a note calls them
BRAND_STYLE = {"main_color": "brand main color", "accent_color": "brand accent color",
               "predefined_font_family": "brand font"}
# Campaign.text_guidelines fields (v26) -> what a note calls one of them
TEXT_GUIDELINES = {"term_exclusions": "term exclusion", "messaging_restrictions": "messaging restriction"}
# not yet confirmed live, so they have a query of their own: if Google refuses one, the rest is still described
GUIDELINE_FIELDS = (*(f"campaign.brand_guidelines.{name}" for name in BRAND_STYLE),
                    *(f"campaign.text_guidelines.{name}" for name in TEXT_GUIDELINES))
MAX_ANSWER = 500  # characters of Google's refusal kept in a note


def _settings_notes(campaign, budget, notes: list[str]) -> None:
    if not campaign.brand_guidelines_enabled:
        notes.append("brand guidelines are off in the campaign; the copy turns them on and links the business name "
                     "and logos to the campaign")
    opted_out = {s.asset_automation_type.name for s in campaign.asset_automation_settings
                 if s.asset_automation_status.name == "OPTED_OUT"}
    left_on = [label for kind, label in AUTOMATIONS.items() if kind not in opted_out]
    if left_on:
        notes.append(f"the campaign doesn't opt out of {_listed(left_on)}; the copy opts out of all of them")
    if any(s.asset_automation_type.name == "GENERATE_IMAGE_EXTRACTION" and s.asset_automation_status.name == "OPTED_IN"
           for s in campaign.asset_automation_settings):
        notes.append("the campaign takes images from the landing page; the copy follows the account's setting, which "
                     "only the Google Ads UI changes")
    option = campaign.geo_target_type_setting.positive_geo_target_type.name
    if option != "PRESENCE":
        notes.append(f"the campaign's location option is {option}; the copy uses people in or regularly in your "
                     "locations (PRESENCE)")
    tracking = _tracking(campaign)
    if tracking:
        notes.append(f"the campaign's {_listed(tracking)} {'are' if len(tracking) > 1 else 'is'} not copied; set "
                     f"{'them' if len(tracking) > 1 else 'it'} in Google Ads after creating the campaign")
    if campaign.shopping_setting.merchant_id:
        notes.append("the campaign uses a Merchant Center feed (products and listing groups); the copy has none")
    if budget.explicitly_shared:
        notes.append("the campaign uses a shared budget; the copy gets a budget of its own")


def _guidelines(client, customer_id: str, by_id: str, notes: list[str]) -> None:
    """A note for each brand style and text guideline the campaign sets (none is copied). If Google refuses the
    query, one note says so instead; the daily quota or a network failure is raised."""
    try:
        rows = gads.search(client, customer_id, f"SELECT campaign.id, {', '.join(GUIDELINE_FIELDS)} "
                                                f"FROM campaign WHERE {by_id}")
    except gads.GoogleAdsError as e:
        if gads.not_a_refusal(e):
            raise
        answer = str(e)[:MAX_ANSWER] or "no reason given"
        notes.append(f"couldn't read the campaign's brand or text guidelines (Google's answer: {answer}); check them "
                     "in Google Ads: they are not copied")
        return
    for campaign in (r.campaign for r in rows):  # one row: describe_pmax_campaign checked the campaign is there
        for name, label in BRAND_STYLE.items():
            value = getattr(campaign.brand_guidelines, name)
            if value:
                notes.append(f"the campaign's {label} ({value}) {SET_LATER}")
        for name, label in TEXT_GUIDELINES.items():
            count = len(getattr(campaign.text_guidelines, name))
            if count:
                notes.append(f"the campaign's {count} {label}(s) (text guidelines) are not copied: set them in Google "
                             "Ads after creating the campaign (they apply to generated text, which the copy turns off)")


def _goals(client, customer_id: str, by_id: str, notes: list[str]) -> list[dict]:
    """The campaign's biddable conversion goals; app goals stay out (Performance Max here promotes websites)."""
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
        notes.append(f"{app} app conversion goal(s) are not copied: Performance Max here promotes websites")
    custom = [r for r in gads.search(client, customer_id, "SELECT campaign.id, conversion_goal_campaign_config"
                                                          ".custom_conversion_goal FROM conversion_goal_campaign_config "
                                                          f"WHERE {by_id}")
              if r.conversion_goal_campaign_config.custom_conversion_goal]
    if custom:
        notes.append("the campaign bids on a custom conversion goal, which a plan can't hold; list conversion_goals")
    elif not goals:
        notes.append("the campaign bids on no website conversion goal; list conversion_goals")
    return goals


def _brand(client, customer_id: str, by_id: str, notes: list[str]) -> dict:
    """The campaign's ENABLED business name and logos (brand guidelines); its other campaign assets are noted."""
    brand: dict = {"business_name": "", "logos": [], "landscape_logos": []}
    other, generated = [], 0
    for r in gads.search(client, customer_id,
                         "SELECT campaign.id, campaign_asset.field_type, campaign_asset.status, campaign_asset.source, "
                         "asset.resource_name, asset.text_asset.text FROM campaign_asset "
                         f"WHERE {by_id} AND campaign_asset.status != 'REMOVED'"):
        link, kind = r.campaign_asset, r.campaign_asset.field_type.name
        if link.source.name != "ADVERTISER":
            generated += 1
        elif kind not in BRAND:
            other.append(kind)
        elif link.status.name != "ENABLED":
            notes.append(f"{_status(link.status)} campaign {kind.lower().replace('_', ' ')} {r.asset.resource_name} "
                         "not copied")
        elif kind == "BUSINESS_NAME":
            brand["business_name"] = brand["business_name"] or r.asset.text_asset.text
        else:
            brand[BRAND[kind]].append({"asset": r.asset.resource_name})
    if other:
        notes.append(f"{len(other)} campaign-level asset(s) are not copied ({_kinds(other)}); add them in Google Ads "
                     "after creating the campaign")
    if generated:
        notes.append(f"{generated} campaign-level asset(s) Google created automatically are not copied")
    return brand


def _copied_groups(client, customer_id: str, by_id: str, notes: list[str]) -> list:
    """The ENABLED asset groups (the others are noted), each with its own notes."""
    groups = []
    for g in gads.search(client, customer_id,
                         "SELECT campaign.id, asset_group.id, asset_group.name, asset_group.status, "
                         "asset_group.final_urls, asset_group.final_mobile_urls, asset_group.path1, asset_group.path2, "
                         f"{_tracking_fields('asset_group')} FROM asset_group WHERE {by_id} "
                         "AND asset_group.status != 'REMOVED'"):
        group = g.asset_group
        if group.status.name != "ENABLED":
            notes.append(f"{_status(group.status)} asset group {group.name!r} not copied")
            continue
        groups.append(group)
        if len(group.final_urls) > 1 or group.final_mobile_urls:
            notes.append(f"asset group {group.name!r}: only its first final URL is copied")
        tracking = _tracking(group)
        if tracking:
            notes.append(f"asset group {group.name!r}: its {_listed(tracking)} "
                         f"{'are' if len(tracking) > 1 else 'is'} not copied")
    if len(groups) > MAX_ASSET_GROUPS:
        notes.append(f"the campaign has {len(groups)} enabled asset groups and a plan takes at most "
                     f"{MAX_ASSET_GROUPS}; keep the best ones")
    return groups


def _empty_group(group) -> dict:
    return {
        "name": group.name, "final_url": group.final_urls[0] if group.final_urls else "",
        "path1": group.path1, "path2": group.path2, **{name: [] for name in GROUP_LISTS.values()},
        "call_to_action": None, "audience_signal": None, "search_themes": [],
    }


def _group_assets(client, customer_id: str, by_id: str, plan_groups: dict[int, dict], brand: dict,
                  names: dict[int, str], notes: list[str]) -> None:
    """Each copied asset group's ENABLED own assets. Paused, generated and other kinds are noted. A business name
    or logo linked to an asset group (a campaign without brand guidelines) moves to the campaign, up to
    MAX_ALL_LOGOS logos in all (the first ones in Google's order; the rest are noted)."""
    paused: dict[int, list[str]] = {}
    generated: dict[int, int] = {}
    other: dict[int, list[str]] = {}
    moved = False
    left_out: list[str] = []  # logos beyond MAX_ALL_LOGOS
    for r in gads.search(client, customer_id,
                         "SELECT campaign.id, asset_group.id, asset_group.status, asset_group_asset.field_type, "
                         "asset_group_asset.status, asset_group_asset.source, asset.resource_name, asset.text_asset.text, "
                         "asset.call_to_action_asset.call_to_action FROM asset_group_asset "
                         f"WHERE {by_id} AND asset_group.status != 'REMOVED' AND asset_group_asset.status != 'REMOVED'"):
        gid = r.asset_group.id
        if gid not in plan_groups:
            continue  # an asset group that is not copied: its own note covers it
        link, kind, asset = r.asset_group_asset, r.asset_group_asset.field_type.name, r.asset
        if link.source.name != "ADVERTISER":
            generated[gid] = generated.get(gid, 0) + 1
        elif link.status.name != "ENABLED":
            paused.setdefault(gid, []).append(kind)
        elif kind in GROUP_LISTS:
            key = GROUP_LISTS[kind]
            plan_groups[gid][key].append(asset.text_asset.text if key in _TEXT_LISTS else {"asset": asset.resource_name})
        elif kind == "CALL_TO_ACTION_SELECTION":
            plan_groups[gid]["call_to_action"] = asset.call_to_action_asset.call_to_action.name
        elif kind in BRAND:
            moved = True
            if kind == "BUSINESS_NAME":
                brand["business_name"] = brand["business_name"] or asset.text_asset.text
            elif {"asset": asset.resource_name} in brand[BRAND[kind]] or asset.resource_name in left_out:
                continue  # in several asset groups: counted once
            elif len(brand["logos"]) + len(brand["landscape_logos"]) < MAX_ALL_LOGOS:
                brand[BRAND[kind]].append({"asset": asset.resource_name})
            else:
                left_out.append(asset.resource_name)
        else:
            other.setdefault(gid, []).append(kind)
    if moved:
        notes.append("the business name and logos of the asset groups are copied to the campaign (brand guidelines)")
    if left_out:
        found = len(brand["logos"]) + len(brand["landscape_logos"]) + len(left_out)
        notes.append(f"the campaign and its asset groups have {found} logos (square and landscape together) and a "
                     f"plan takes at most {MAX_ALL_LOGOS}; only the first {MAX_ALL_LOGOS} are copied, not "
                     f"{_listed(left_out)}")
    for gid, kinds in paused.items():
        notes.append(f"asset group {names[gid]!r}: {_counted(kinds)} paused asset(s) not copied")
    for gid, n in generated.items():
        notes.append(f"asset group {names[gid]!r}: {n} asset(s) Google created automatically are not copied")
    for gid, kinds in other.items():
        notes.append(f"asset group {names[gid]!r}: {_counted(kinds)} asset(s) are not copied")


def audience_scopes(client, customer_id: str, audiences) -> dict[str, tuple[str, str]]:
    """{audience resource name: (scope, status)} for this account's audiences that Google returns. Only an
    ENABLED audience with CUSTOMER scope can be an asset group's signal in another campaign."""
    pattern = re.compile(rf"customers/{re.escape(customer_id)}/audiences/[0-9]+")  # [0-9]: ASCII digits only
    wanted = [a for a in dict.fromkeys(audiences) if isinstance(a, str) and pattern.fullmatch(a)]
    if not wanted:
        return {}
    rows = gads.search(client, customer_id, "SELECT audience.resource_name, audience.scope, audience.status "
                                            f"FROM audience WHERE audience.resource_name IN {in_list(wanted)}")
    return {r.audience.resource_name: (r.audience.scope.name, r.audience.status.name) for r in rows}


CHOOSE_AUDIENCE = "; choose an account-wide audience (customers/<id>/audiences/<id>) or none"


def _audience_reason(scope_status: tuple[str, str] | None) -> str:
    """Why an asset group's audience signal is not copied, from the audience's (scope, status); None when Google
    didn't return the audience. A removed or missing audience is said so, whatever its scope."""
    if scope_status is None:
        return "was not found in the account and is not copied"
    scope, status = scope_status
    if status == "REMOVED":
        return "is removed and is not copied"
    if scope == "ASSET_GROUP":
        return "was made for that asset group and can't be reused"
    return f"(scope {scope}, status {status}) is not copied"


def _signals(client, customer_id: str, by_id: str, plan_groups: dict[int, dict], names: dict[int, str],
             notes: list[str]) -> None:
    """Search themes are copied, at most SEARCH_THEMES per asset group and none Google disapproved. An audience
    signal is copied only if it is an ENABLED account-wide audience, and only the first such one (a plan takes one):
    one made for its own asset group (ASSET_GROUP scope) can't be reused. Every signal left out is noted."""
    audiences: dict[int, list[str]] = {}
    themes: dict[int, list[str]] = {}
    disapproved: dict[int, list[str]] = {}
    for r in gads.search(client, customer_id,
                         "SELECT campaign.id, asset_group.id, asset_group.status, asset_group_signal.approval_status, "
                         "asset_group_signal.audience.audience, asset_group_signal.search_theme.text "
                         f"FROM asset_group_signal WHERE {by_id} AND asset_group.status != 'REMOVED'"):
        gid = r.asset_group.id
        if gid not in plan_groups:
            continue
        signal = r.asset_group_signal
        if signal.search_theme.text:
            bucket = disapproved if signal.approval_status.name == "DISAPPROVED" else themes
            bucket.setdefault(gid, []).append(signal.search_theme.text)
        elif signal.audience.audience:
            audiences.setdefault(gid, []).append(signal.audience.audience)
    for gid, found in themes.items():
        plan_groups[gid]["search_themes"] = found[:SEARCH_THEMES]
        if len(found) > SEARCH_THEMES:
            notes.append(f"asset group {names[gid]!r} has {len(found)} search themes and a plan takes at most "
                         f"{SEARCH_THEMES}; only the first {SEARCH_THEMES} are copied")
    for gid, found in disapproved.items():
        notes.append(f"asset group {names[gid]!r}: {len(found)} disapproved search theme(s) not copied: "
                     f"{_listed([repr(t) for t in found])}")
    scopes = audience_scopes(client, customer_id, [a for found in audiences.values() for a in found])
    for gid, found in audiences.items():
        usable = [a for a in found if scopes.get(a) == ("CUSTOMER", "ENABLED")]
        if usable:
            plan_groups[gid]["audience_signal"] = usable[0]
        if len(usable) > 1:
            notes.append(f"asset group {names[gid]!r}: a plan takes one audience signal; {len(usable) - 1} more "
                         f"account-wide audience(s) are not copied: {_listed(usable[1:])}")
        for audience in found:
            if audience not in usable:
                notes.append(f"asset group {names[gid]!r}: its audience signal {audience} "
                             f"{_audience_reason(scopes.get(audience))}{'' if usable else CHOOSE_AUDIENCE}")


def describe_pmax_campaign(client, customer_id: str, row) -> dict:
    """A Performance Max campaign -> its settings, asset groups, and a ready-to-edit pmax_campaign plan. `row` is
    the campaign row describe_campaign read (id, name, status, budget, currency)."""
    by_id = f"campaign.id = {int(row.campaign.id)}"
    notes: list[str] = []
    settings = gads.search(
        client, customer_id,
        "SELECT campaign.id, campaign.bidding_strategy_type, campaign.bidding_strategy, "
        "campaign.maximize_conversions.target_cpa_micros, campaign.maximize_conversion_value.target_roas, "
        "campaign.brand_guidelines_enabled, campaign.asset_automation_settings, "
        f"campaign.geo_target_type_setting.positive_geo_target_type, {_tracking_fields('campaign')}, "
        f"campaign.shopping_setting.merchant_id, campaign_budget.explicitly_shared FROM campaign WHERE {by_id}",
    )
    if len(settings) != 1:
        raise LookupError(f"expected one campaign with {by_id}, found {len(settings)}")
    campaign = settings[0].campaign
    bidding = plan_bidding(campaign, notes, PMAX_BIDDING)
    if not row.campaign_budget.amount_micros:
        notes.append("the campaign has a campaign total budget, not a daily one; set daily_budget")
    _settings_notes(campaign, settings[0].campaign_budget, notes)
    _guidelines(client, customer_id, by_id, notes)
    goals = _goals(client, customer_id, by_id, notes)
    targeting = _campaign_targeting(client, customer_id, by_id, notes)
    brand = _brand(client, customer_id, by_id, notes)
    groups = _copied_groups(client, customer_id, by_id, notes)
    names = {g.id: g.name for g in groups}
    plan_groups = {g.id: _empty_group(g) for g in groups}
    _group_assets(client, customer_id, by_id, plan_groups, brand, names, notes)
    _signals(client, customer_id, by_id, plan_groups, names, notes)
    for gid, group in plan_groups.items():
        if not group["videos"]:
            notes.append(f"asset group {names[gid]!r} has no video of its own; add one of your YouTube videos")
        if not group["final_url"]:
            notes.append(f"asset group {names[gid]!r} has no final URL; add one")
    if not brand["business_name"] or not brand["logos"]:
        notes.append("the campaign has no business name or logo to copy; add them")
    used = [*targeting["locations"], *targeting["languages"],
            *(g["audience_signal"] for g in plan_groups.values() if g["audience_signal"])]
    labels = {resource: shown[0] for resource, shown in google_names(client, customer_id, used).items()}
    template_plan = {
        "kind": "pmax_campaign",
        "name": f"{row.campaign.name} (copy)",
        "template": row.campaign.name,
        "daily_budget": row.campaign_budget.amount_micros / 1_000_000,
        "currency": row.customer.currency_code,
        **bidding,
        "conversion_goals": goals,
        "locations": targeting["locations"],
        "languages": targeting["languages"],
        **brand,
        "asset_groups": list(plan_groups.values()),
        "brief": {"names": labels},
    }
    return {
        "campaign": {"id": row.campaign.id, "name": row.campaign.name, "status": row.campaign.status.name,
                     "channel": "PERFORMANCE_MAX"},
        "template_plan": template_plan,
        "asset_groups": [{"id": g.id, "name": g.name, "status": g.status.name} for g in groups],
        "names": labels,
        "notes": notes,
    }
