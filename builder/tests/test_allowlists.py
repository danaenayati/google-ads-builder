"""The guard's field allowlists: every field a compiler sets is listed, nothing else is, and a request with any
other field set is refused, for every plan kind."""

from typing import NamedTuple

import pytest

from gads_builder import allowlists, plans, server
from gads_builder.compile_app import compile_app_campaign
from gads_builder.compile_assets import compile_asset_update, compile_image_upload
from gads_builder.compile_demand_gen import compile_demand_gen_campaign
from gads_builder.compile_pmax import compile_pmax_campaign
from gads_builder.compile_search import compile_search_campaign

CID = "1234567890"
GOALS = (("PURCHASE", "WEBSITE"), ("DOWNLOAD", "APP"))
LIST = "customers/1234567890/userLists/5"
AUDIENCE = "customers/1234567890/audiences/5"
BASE = {"headlines": ["Old one", "Old two"], "descriptions": ["Old description"],
        "images": ["customers/1234567890/assets/1"], "videos": []}


class Request(NamedTuple):
    plan: object
    ops: list
    cfg: object
    facts: dict


@pytest.fixture
def build(ads_client, cfg, search_cfg, app_plan, search_plan, demand_gen_plan, pmax_plan, make_png):
    """name -> a valid compiled request of that kind (a new one every call)."""
    landscape = str(make_png(1200, 628, "allow-landscape.png"))
    square = str(make_png(1200, 1200, "allow-square.png"))

    def demand_gen(**over):
        data = demand_gen_plan(**over)
        data["ad_groups"][0]["audiences"] = [LIST]
        data["ad_groups"][0]["ads"][1]["call_to_action"] = "SIGN_UP"
        return data

    def demand_gen_mixed():
        """Two ad groups; an existing landscape image; the second group's image ad reuses the square file and the
        logo; a video ad with an existing video and two new ones, and no call to action."""
        data = demand_gen()
        image_ad, video_ad = data["ad_groups"][0]["ads"]
        image_ad["marketing_images"] = [{"asset": f"customers/{CID}/assets/7"}, *image_ad["marketing_images"]]
        second_image = dict(image_ad, marketing_images=[{"file": landscape}], portrait_marketing_images=[],
                            headlines=["Yoga Every Day"], final_url="https://www.example.com/yoga")
        second_video = dict(video_ad, call_to_action=None, business_name="Pulse Yoga",
                            videos=[{"asset": f"customers/{CID}/assets/9"}, {"youtube_id": "zyxwvutsrqp"},
                                    {"youtube_id": "abcdefghijk"}])
        data["ad_groups"].append(dict(data["ad_groups"][0], name="Yoga fans", channels=["discover"], audiences=[],
                                      ads=[second_image, second_video]))
        return data

    def two_groups():
        """Two ad groups (the second's ad without paths), a sitelink without descriptions, two snippets."""
        data = search_plan()
        group = data["ad_groups"][0]
        second = dict(group, name="Yoga classes", negative_keywords=[],
                      keywords=[{"text": "yoga classes", "match": "PHRASE"}, {"text": "yoga springfield", "match": "EXACT"}],
                      ad=dict(group["ad"], final_url="https://www.example.com/yoga", path1="", path2=""))
        data["ad_groups"].append(second)
        data["sitelinks"].append({"text": "Yoga Timetable", "url": "https://www.example.com/yoga/times"})
        data["snippets"].append({"header": "Amenities", "values": ["Showers", "Lockers", "Parking"]})
        data["languages"] = ["languageConstants/1000", "languageConstants/1019"]
        return data

    def pmax(**over):
        data = pmax_plan(landscape_logos=[{"file": str(make_png(1200, 300, "allow-wide-logo.png"))}], **over)
        data["asset_groups"][0].update(audience_signal=AUDIENCE, call_to_action="SIGN_UP")
        return data

    def pmax_mixed():
        """Two asset groups; an existing logo, landscape image and video; the second group reuses the files, with no
        display path, call to action, audience, portrait image or search theme, and two YouTube IDs."""
        data = pmax(logos=[{"asset": f"customers/{CID}/assets/7"}])
        group = data["asset_groups"][0]
        group["marketing_images"] = [{"asset": f"customers/{CID}/assets/8"}, *group["marketing_images"]]
        data["asset_groups"].append(dict(
            group, name="Yoga fans", final_url="https://www.example.com/yoga", path1="", path2="", call_to_action=None,
            audience_signal=None, search_themes=[], portrait_marketing_images=[],
            headlines=["Yoga Every Day", *group["headlines"][1:]],
            videos=[{"asset": f"customers/{CID}/assets/9"}, {"youtube_id": "zyxwvutsrqp"}, {"youtube_id": "abcdefghijk"}]))
        return data

    def update(ad_type, **over):
        media = {} if ad_type == "RESPONSIVE_SEARCH_AD" else {
            "add_images": [{"file": square}], "add_videos": [{"youtube_id": "abcdefghijk"}]}
        return {"kind": "asset_update", "ad": f"customers/{CID}/ads/55", "ad_type": ad_type, "base": BASE,
                "add_headlines": ["New one"], "remove": ["Old two"], **media, **over}

    made = {
        "app": (app_plan(images=[{"file": landscape}]), compile_app_campaign, cfg),
        "app engagement": (app_plan(subtype="APP_CAMPAIGN_FOR_ENGAGEMENT", target_cpa=None,
                                    goal="OPTIMIZE_IN_APP_CONVERSIONS_WITHOUT_TARGET_CPA", images=[{"file": square}],
                                    final_app_urls=[{"os_type": "ANDROID", "url": "example://classes"}]),
                           compile_app_campaign, cfg),
        "app mixed": (app_plan(  # existing assets between new ones, two locations, a deep link on an App ad
            images=[{"asset": f"customers/{CID}/assets/7"}, {"file": landscape}, {"file": square}],
            videos=[{"asset": f"customers/{CID}/assets/9"}, {"youtube_id": "abcdefghijk"}],
            locations=["geoTargetConstants/1000001", "geoTargetConstants/1000002"],
            final_app_urls=[{"os_type": "ANDROID", "url": "example://classes"}]), compile_app_campaign, cfg),
        "search": (search_plan(), compile_search_campaign, search_cfg),
        "search two groups": (two_groups(), compile_search_campaign, search_cfg),
        "search with a target": (search_plan(target_cpa=5.0), compile_search_campaign, search_cfg),
        "search target CPA": (search_plan(bidding="TARGET_CPA", target_cpa=5.0), compile_search_campaign, search_cfg),
        "search clicks": (search_plan(bidding="MAXIMIZE_CLICKS", max_cpc=1.5), compile_search_campaign, search_cfg),
        "search impression share": (search_plan(bidding="TARGET_IMPRESSION_SHARE", max_cpc=1.5, impression_share={
            "location": "TOP_OF_PAGE", "percent": 50}), compile_search_campaign, search_cfg),
        "search manual": (search_plan(bidding="MANUAL_CPC", max_cpc=1.5), compile_search_campaign, search_cfg),
        "demand gen": (demand_gen(), compile_demand_gen_campaign, search_cfg),
        "demand gen target CPA": (demand_gen(bidding="TARGET_CPA", target_cpa=1.5), compile_demand_gen_campaign,
                                  search_cfg),
        "demand gen clicks": (demand_gen(bidding="MAXIMIZE_CLICKS"), compile_demand_gen_campaign, search_cfg),
        "demand gen mixed": (demand_gen_mixed(), compile_demand_gen_campaign, search_cfg),
        "pmax": (pmax(), compile_pmax_campaign, search_cfg),
        "pmax with a target": (pmax(target_cpa=5.0), compile_pmax_campaign, search_cfg),
        "pmax value": (pmax(bidding="MAXIMIZE_CONVERSION_VALUE"), compile_pmax_campaign, search_cfg),
        "pmax value with a target": (pmax(bidding="MAXIMIZE_CONVERSION_VALUE", target_roas=3.5),
                                     compile_pmax_campaign, search_cfg),
        "pmax mixed": (pmax_mixed(), compile_pmax_campaign, search_cfg),
        "upload": ({"kind": "image_upload", "files": [landscape, square]}, compile_image_upload, cfg),
        "update APP_AD": (update("APP_AD"), compile_asset_update, cfg),
        "update APP_ENGAGEMENT_AD": (update("APP_ENGAGEMENT_AD"), compile_asset_update, cfg),
        "update RESPONSIVE_SEARCH_AD": (update("RESPONSIVE_SEARCH_AD"), compile_asset_update, cfg),
        "update APP_AD mixed": (update(  # existing assets between new ones, an image removed, a description added
            "APP_AD", add_images=[{"asset": f"customers/{CID}/assets/8"}, {"file": square}, {"file": landscape}],
            add_videos=[{"asset": f"customers/{CID}/assets/9"}, {"youtube_id": "abcdefghijk"}],
            add_descriptions=["New description"], remove=["Old two", f"customers/{CID}/assets/1"]),
            compile_asset_update, cfg),
    }

    def make(name: str) -> Request:
        data, compiler, config = made[name]
        plan = plans.plan_from_dict(data)
        if compiler in (compile_demand_gen_campaign, compile_pmax_campaign):
            return Request(plan, compiler(plan, ads_client, CID, GOALS)[0], config, {"account_goals": GOALS})
        return Request(plan, compiler(plan, ads_client, CID)[0], config, {})

    make.names = list(made)
    return make


KINDS = ["app", "app engagement", "app mixed", "search", "search two groups", "search with a target", "search target CPA", "search clicks", "search impression share",
         "search manual", "demand gen", "demand gen target CPA", "demand gen clicks", "demand gen mixed",
         "pmax", "pmax with a target", "pmax value", "pmax value with a target", "pmax mixed",
         "upload", "update APP_AD", "update APP_ENGAGEMENT_AD", "update RESPONSIVE_SEARCH_AD", "update APP_AD mixed"]


def guard(ads_client, r: Request) -> list[str]:
    return server._guard(r.plan, r.ops, ads_client, r.cfg, r.facts)


def test_every_kind_is_covered(build):
    assert build.names == KINDS


@pytest.mark.parametrize("name", KINDS)
def test_an_untampered_request_passes(ads_client, build, name):
    r = build(name)
    assert guard(ads_client, r) == [] and allowlists.problems(r.plan, r.ops) == []


# ------------------------------------------------- the lists are exactly what the compilers set


def _listed(allowed, path: str = "") -> set[str]:
    """Every field path a list allows; a kind's own fields are under "[kind]"."""
    if isinstance(allowed, allowlists.Variants):
        return {p for kind, sub in allowed.lists.items() for p in _listed(sub, f"{path}[{kind}]")}
    out = set()
    for name, sub in (allowed or {}).items():
        out.add(path + name)
        out |= _listed(sub, f"{path}{name}.")
    return out


def _sent(message, allowed, path: str = "") -> set[str]:
    """Every field path a message sets, in the same form (the list names each message's kind)."""
    if isinstance(allowed, allowlists.Variants):
        kind = allowed.kind(message)
        return _sent(message, allowed.lists.get(kind), f"{path}[{kind}]")
    out = set()
    for field, value in message.ListFields():
        out.add(path + field.name)
        if field.message_type is not None:
            for item in value if field.is_repeated else [value]:
                out |= _sent(item, (allowed or {}).get(field.name), f"{path}{field.name}.")
    return out


def _sent_by_part(requests: list[Request], allowed: dict) -> tuple[dict, dict, dict]:
    """({part: field paths sent}, {part: update mask paths sent}, {part: actions used})."""
    fields: dict[str, set[str]] = {}
    masks: dict[str, set[str]] = {}
    actions: dict[str, set[str]] = {}
    for r in requests:
        for op in r.ops:
            pb = type(op).pb(op)
            which = pb.WhichOneof("operation")
            operation = getattr(pb, which)
            action = operation.WhichOneof("operation")
            actions.setdefault(which, set()).add(action)
            fields.setdefault(which, set()).update(_sent(getattr(operation, action), allowed.get(which)))
            if action == "update":
                masks.setdefault(which, set()).update(operation.update_mask.paths)
    return fields, masks, actions


@pytest.mark.parametrize("kind, names", [
    ("app_campaign", ["app", "app engagement", "app mixed"]),
    ("search_campaign", ["search", "search two groups", "search with a target", "search target CPA", "search clicks", "search impression share", "search manual"]),
    ("demand_gen_campaign", ["demand gen", "demand gen target CPA", "demand gen clicks", "demand gen mixed"]),
    ("pmax_campaign", ["pmax", "pmax with a target", "pmax value", "pmax value with a target", "pmax mixed"]),
    ("image_upload", ["upload"]),
    ("asset_update", ["update APP_AD", "update APP_AD mixed"]),
    ("asset_update", ["update APP_ENGAGEMENT_AD"]),
    ("asset_update", ["update RESPONSIVE_SEARCH_AD"]),
])
def test_each_list_is_exactly_what_its_compiler_sends(build, kind, names):
    requests = [build(name) for name in names]
    allowed, masks = allowlists.for_plan(requests[0].plan)
    assert requests[0].plan.kind == kind
    fields, sent_masks, actions = _sent_by_part(requests, allowed)
    assert set(fields) == set(allowed)
    for which, sub in allowed.items():
        assert fields[which] == _listed(sub), which
        assert actions[which] == {"update" if which in masks else "create"}, which
    assert sent_masks == {k: v for k, v in masks.items() if v}


# ------------------------------------------------------------------ tampered requests


def _creates(r: Request, which: str, pick: int = 0):
    """The raw protobuf create (or update) of the `pick`-th operation of one kind."""
    found = []
    for op in r.ops:
        pb = type(op).pb(op)
        if pb.WhichOneof("operation") == which:
            operation = getattr(pb, which)
            found.append(getattr(operation, operation.WhichOneof("operation")))
    return found[pick]


def _operation(r: Request, which: str, pick: int = 0):
    return [getattr(type(op).pb(op), which) for op in r.ops if type(op).pb(op).WhichOneof("operation") == which][pick]


def _asset_of_kind(r: Request, kind: str):
    return next(_creates(r, "asset_operation", i) for i in range(len(r.ops))
                if _creates(r, "asset_operation", i).WhichOneof("asset_data") == kind)


def _campaign(field, value):
    return lambda r, E: setattr(_creates(r, "campaign_operation"), field, value)


def _group(field, value):
    return lambda r, E: setattr(_creates(r, "ad_group_operation"), field, value)


def _ad(field, value):
    return lambda r, E: setattr(_creates(r, "ad_group_ad_operation").ad, field, value)


def _url_collection(r, E):
    _creates(r, "ad_group_ad_operation").ad.url_collections.add(url_collection_id="x").final_urls.append(
        "https://www.example.net/")


def _ad_parameter(r, E):
    _creates(r, "ad_group_ad_operation").ad.url_custom_parameters.add(key="season", value="x")


def _campaign_parameter(r, E):
    _creates(r, "campaign_operation").url_custom_parameters.add(key="season", value="x")


def _opted_in(r, E):
    setting = _creates(r, "campaign_operation").asset_automation_settings.add()
    setting.asset_automation_type = E.AssetAutomationTypeEnum.GENERATE_VERTICAL_YOUTUBE_VIDEOS
    setting.asset_automation_status = E.AssetAutomationStatusEnum.OPTED_IN


def _asset(kind, field, value):
    return lambda r, E: getattr(_asset_of_kind(r, kind), field).append(value) if field == "final_urls" else setattr(
        _asset_of_kind(r, kind), field, value)


def _policy_exemption(r, E):
    _operation(r, "ad_group_ad_operation").policy_validation_parameter.ignorable_policy_topics.append("TRADEMARKS")


def _keyword_exemption(r, E):
    _operation(r, "ad_group_criterion_operation").exempt_policy_violation_keys.add(policy_name="x", violating_text="y")


def _create_mask(r, E):
    _operation(r, "campaign_operation").update_mask.paths.append("name")


def _criterion(which, field, value):
    return lambda r, E: setattr(_creates(r, which), field, value)


def _pinned(info):
    def edit(r, E):
        headline = getattr(_creates(r, "ad_group_ad_operation").ad, info).headlines[0]
        headline.pinned_field = E.ServedAssetFieldTypeEnum.HEADLINE_1
    return edit


def _budget(field, value):
    return lambda r, E: setattr(_creates(r, "campaign_budget_operation"), field, value(E) if callable(value) else value)


def _unexpected(field, part):
    return allowlists.UNEXPECTED.format(field=field, part=part)


TRACKER = "https://tracker.example.net/{lpurl}"
CAMPAIGN_TAMPERS = [
    (_campaign("tracking_url_template", TRACKER), _unexpected("tracking_url_template", "campaign")),
    (_campaign("final_url_suffix", "utm_source=x"), _unexpected("final_url_suffix", "campaign")),
    (_campaign_parameter, _unexpected("url_custom_parameters", "campaign")),
    (_opted_in, _unexpected("asset_automation_settings", "campaign")),
    (lambda r, E: setattr(_creates(r, "campaign_operation").shopping_setting, "merchant_id", 5),
     _unexpected("shopping_setting", "campaign")),
    (lambda r, E: setattr(_creates(r, "campaign_operation").audience_setting, "use_audience_grouped", True),
     _unexpected("audience_setting", "campaign")),
    (_campaign("view_through_conversion_optimization_enabled", True),
     _unexpected("view_through_conversion_optimization_enabled", "campaign")),
    (_budget("period", lambda E: E.BudgetPeriodEnum.CUSTOM_PERIOD), _unexpected("period", "budget")),
    (_budget("total_amount_micros", 900_000_000), _unexpected("total_amount_micros", "budget")),
    (_policy_exemption, _unexpected("policy_validation_parameter", "the ad operation")),
    (_create_mask, _unexpected("update_mask", "the campaign operation")),
    (_ad_parameter, _unexpected("ad.url_custom_parameters", "ad")),
    (_url_collection, _unexpected("ad.url_collections", "ad")),
    (_ad("display_url", "example.com"), _unexpected("ad.display_url", "ad")),
    (lambda r, E: setattr(_creates(r, "ad_group_ad_operation"), "start_date_time", "2026-11-01 00:00:00"),
     _unexpected("start_date_time", "ad")),
    (_group("tracking_url_template", TRACKER), _unexpected("tracking_url_template", "ad group")),
    (_group("final_url_suffix", "utm_source=x"), _unexpected("final_url_suffix", "ad group")),
    (_group("target_cpa_micros", 1_000_000), _unexpected("target_cpa_micros", "ad group")),
]
APP_TAMPERS = CAMPAIGN_TAMPERS + [
    (_group("cpc_bid_micros", 1_000_000), _unexpected("cpc_bid_micros", "ad group")),
    (_ad("tracking_url_template", TRACKER), _unexpected("ad.tracking_url_template", "ad")),
    (lambda r, E: _creates(r, "ad_group_ad_operation").ad.final_urls.append("https://www.example.net/"),
     _unexpected("ad.final_urls", "ad")),
    (_ad("name", "Sneaky"), _unexpected("ad.name", "ad")),
    (lambda r, E: setattr(_creates(r, "ad_group_ad_operation").ad.app_ad.mandatory_ad_text, "text", "Hi"),
     _unexpected("ad.app_ad.mandatory_ad_text", "ad")),
    (_asset("image_asset", "final_urls", "https://www.example.net/"), _unexpected("final_urls", "asset")),
    (_criterion("campaign_criterion_operation", "bid_modifier", 1.5), _unexpected("bid_modifier", "campaign criterion")),
    (lambda r, E: setattr(_creates(r, "campaign_operation").geo_target_type_setting, "positive_geo_target_type",
                          E.PositiveGeoTargetTypeEnum.PRESENCE), _unexpected("geo_target_type_setting", "campaign")),
]
SEARCH_TAMPERS = CAMPAIGN_TAMPERS + [
    (_ad("tracking_url_template", TRACKER), _unexpected("ad.tracking_url_template", "ad")),
    (_ad("name", "Sneaky"), _unexpected("ad.name", "ad")),
    (_pinned("responsive_search_ad"), _unexpected("ad.responsive_search_ad.headlines.pinned_field", "ad")),
    (_asset("sitelink_asset", "tracking_url_template", TRACKER), _unexpected("tracking_url_template", "asset")),
    (lambda r, E: setattr(_asset_of_kind(r, "sitelink_asset").sitelink_asset, "start_date", "2026-11-01"),
     _unexpected("sitelink_asset.start_date", "asset")),
    (_criterion("campaign_criterion_operation", "bid_modifier", 1.5), _unexpected("bid_modifier", "campaign criterion")),
    (_criterion("ad_group_criterion_operation", "cpc_bid_micros", 9_000_000),
     _unexpected("cpc_bid_micros", "ad group criterion")),
    (lambda r, E: _creates(r, "ad_group_criterion_operation").final_urls.append("https://www.example.net/"),
     _unexpected("final_urls", "ad group criterion")),
    (_keyword_exemption, _unexpected("exempt_policy_violation_keys", "the ad group criterion operation")),
    (lambda r, E: setattr(_creates(r, "campaign_asset_operation"), "status", E.AssetLinkStatusEnum.PAUSED),
     _unexpected("status", "asset link")),
    (lambda r, E: setattr(_creates(r, "campaign_operation").geo_target_type_setting, "negative_geo_target_type",
                          E.NegativeGeoTargetTypeEnum.PRESENCE),
     _unexpected("geo_target_type_setting.negative_geo_target_type", "campaign")),
]
DEMAND_GEN_TAMPERS = CAMPAIGN_TAMPERS + [
    (_group("cpc_bid_micros", 1_000_000), _unexpected("cpc_bid_micros", "ad group")),
    (lambda r, E: setattr(_creates(r, "ad_group_operation").audience_setting, "use_audience_grouped", True),
     _unexpected("audience_setting", "ad group")),
    (_pinned("demand_gen_multi_asset_ad"), _unexpected("ad.demand_gen_multi_asset_ad.headlines.pinned_field", "ad")),
    (lambda r, E: setattr(_creates(r, "ad_group_ad_operation", 1).ad.demand_gen_video_responsive_ad.videos[0]
                          .ad_video_asset_info.ad_video_asset_inventory_preferences, "in_feed_preference", True),
     _unexpected("ad.demand_gen_video_responsive_ad.videos.ad_video_asset_info", "ad")),
    (_asset("image_asset", "final_urls", "https://www.example.net/"), _unexpected("final_urls", "asset")),
    (_asset("youtube_video_asset", "tracking_url_template", TRACKER), _unexpected("tracking_url_template", "asset")),
    (_asset("call_to_action_asset", "final_urls", "https://www.example.net/"), _unexpected("final_urls", "asset")),
    (_criterion("ad_group_criterion_operation", "bid_modifier", 1.5), _unexpected("bid_modifier", "ad group criterion")),
    (_criterion("ad_group_criterion_operation", "status", 3), _unexpected("status", "ad group criterion")),
    (lambda r, E: setattr(_creates(r, "ad_group_operation").demand_gen_ad_group_settings.channel_controls,
                          "channel_config", E.DemandGenChannelConfigEnum.SELECTED_CHANNELS),
     _unexpected("demand_gen_ad_group_settings.channel_controls.channel_config", "ad group")),
]
PMAX_TAMPERS = [
    (_campaign("tracking_url_template", TRACKER), _unexpected("tracking_url_template", "campaign")),
    (_campaign("final_url_suffix", "utm_source=x"), _unexpected("final_url_suffix", "campaign")),
    (_campaign_parameter, _unexpected("url_custom_parameters", "campaign")),
    (lambda r, E: setattr(_creates(r, "campaign_operation").shopping_setting, "merchant_id", 5),
     _unexpected("shopping_setting", "campaign")),
    (lambda r, E: setattr(_creates(r, "campaign_operation").brand_guidelines, "main_color", "#ff0000"),
     _unexpected("brand_guidelines", "campaign")),
    (lambda r, E: _creates(r, "campaign_operation").text_guidelines.term_exclusions.append("cheap"),
     _unexpected("text_guidelines", "campaign")),
    (lambda r, E: setattr(_creates(r, "campaign_operation").network_settings, "target_content_network", True),
     _unexpected("network_settings", "campaign")),
    (lambda r, E: setattr(_creates(r, "campaign_operation").maximize_conversions, "cpc_bid_ceiling_micros", 1),
     _unexpected("maximize_conversions.cpc_bid_ceiling_micros", "campaign")),
    (lambda r, E: _creates(r, "campaign_operation").asset_automation_settings[4].automated_video_crawl_setting
     .SetInParent(), _unexpected("asset_automation_settings.automated_video_crawl_setting", "campaign")),
    (_budget("period", lambda E: E.BudgetPeriodEnum.CUSTOM_PERIOD), _unexpected("period", "budget")),
    (_create_mask, _unexpected("update_mask", "the campaign operation")),
    (_asset("text_asset", "final_urls", "https://www.example.net/"), _unexpected("final_urls", "asset")),
    (_asset("image_asset", "tracking_url_template", TRACKER), _unexpected("tracking_url_template", "asset")),
    (_asset("call_to_action_asset", "final_urls", "https://www.example.net/"), _unexpected("final_urls", "asset")),
    (_criterion("campaign_criterion_operation", "bid_modifier", 1.5), _unexpected("bid_modifier", "campaign criterion")),
    (lambda r, E: setattr(_creates(r, "campaign_asset_operation"), "status", E.AssetLinkStatusEnum.PAUSED),
     _unexpected("status", "asset link")),
    (_criterion("asset_group_operation", "tracking_url_template", TRACKER),
     _unexpected("tracking_url_template", "asset group")),
    (_criterion("asset_group_operation", "final_url_suffix", "utm_source=x"),
     _unexpected("final_url_suffix", "asset group")),
    (lambda r, E: _creates(r, "asset_group_operation").final_mobile_urls.append("https://www.example.com/m"),
     _unexpected("final_mobile_urls", "asset group")),
    (lambda r, E: _creates(r, "asset_group_operation").url_custom_parameters.add(key="season", value="x"),
     _unexpected("url_custom_parameters", "asset group")),
    (lambda r, E: setattr(_creates(r, "asset_group_asset_operation"), "status", E.AssetLinkStatusEnum.PAUSED),
     _unexpected("status", "asset group link")),
    (lambda r, E: setattr(_creates(r, "asset_group_signal_operation"), "approval_status",
                          E.AssetGroupSignalApprovalStatusEnum.APPROVED),
     _unexpected("approval_status", "asset group signal")),
    (lambda r, E: _operation(r, "asset_group_signal_operation").exempt_policy_violation_keys.add(
        policy_name="x", violating_text="y"),
     _unexpected("exempt_policy_violation_keys", "the asset group signal operation")),
]
UPLOAD_TAMPERS = [
    (_asset("image_asset", "final_urls", "https://www.example.net/"), _unexpected("final_urls", "asset")),
    (_asset("image_asset", "tracking_url_template", TRACKER), _unexpected("tracking_url_template", "asset")),
    (lambda r, E: setattr(_asset_of_kind(r, "image_asset"), "type_", E.AssetTypeEnum.IMAGE),
     _unexpected("type_", "asset")),
]


def _update(field, value):
    return lambda r, E: getattr(_creates(r, "ad_operation"), field).append(value) if field == "final_urls" else (
        setattr(_creates(r, "ad_operation"), field, value))


UPDATE_TAMPERS = [
    (_update("final_urls", "https://www.example.net/"), _unexpected("final_urls", "ad update")),
    (_update("tracking_url_template", TRACKER), _unexpected("tracking_url_template", "ad update")),
    (_update("name", "Sneaky"), _unexpected("name", "ad update")),
    (lambda r, E: _operation(r, "ad_operation").policy_validation_parameter.ignorable_policy_topics.append("X"),
     _unexpected("policy_validation_parameter", "the ad update operation")),
]
APP_UPDATE_TAMPERS = UPDATE_TAMPERS + [
    (lambda r, E: setattr(_creates(r, "ad_operation").app_ad.mandatory_ad_text, "text", "Hi"),
     _unexpected("app_ad.mandatory_ad_text", "ad update")),
    (_asset("image_asset", "final_urls", "https://www.example.net/"), _unexpected("final_urls", "asset")),
]
RSA_UPDATE_TAMPERS = UPDATE_TAMPERS + [
    (lambda r, E: setattr(_creates(r, "ad_operation").responsive_search_ad, "path1", "deals"),
     _unexpected("responsive_search_ad.path1", "ad update")),
    (lambda r, E: setattr(_creates(r, "ad_operation").responsive_search_ad.headlines[0], "pinned_field",
                          E.ServedAssetFieldTypeEnum.HEADLINE_1),
     _unexpected("responsive_search_ad.headlines.pinned_field", "ad update")),
]

CASES = ([("app", *t) for t in APP_TAMPERS] + [("search", *t) for t in SEARCH_TAMPERS]
         + [("demand gen", *t) for t in DEMAND_GEN_TAMPERS] + [("pmax", *t) for t in PMAX_TAMPERS]
         + [("upload", *t) for t in UPLOAD_TAMPERS]
         + [("update APP_AD", *t) for t in APP_UPDATE_TAMPERS]
         + [("update RESPONSIVE_SEARCH_AD", *t) for t in RSA_UPDATE_TAMPERS])


@pytest.mark.parametrize("name, edit, expected", CASES)
def test_a_field_outside_the_list_is_refused(ads_client, build, name, edit, expected):
    r = build(name)
    edit(r, ads_client.enums)
    assert guard(ads_client, r) == [expected]


def test_an_unknown_update_mask_path_is_refused(ads_client, build):
    r = build("demand gen")
    plan = r.plan
    _operation(r, "campaign_conversion_goal_operation").update_mask.paths.append("category")
    assert allowlists.problems(plan, r.ops) == [_unexpected("update_mask path category", "conversion goal")]


def test_an_operation_the_plan_kind_never_sends_is_refused(ads_client, build):
    r = build("upload")
    op = ads_client.get_type("MutateOperation")
    op.campaign_operation.remove = f"customers/{CID}/campaigns/1"
    assert allowlists.problems(r.plan, [*r.ops, op]) == [
        _unexpected("campaign_operation remove", "the request")]



# MutateOperation kinds whose message has no "operation" oneof (API v26): asking it which action is set would raise
# ValueError, so the guard and the allowlist refuse them as not allowed instead of crashing.
NO_ACTION = ["customer_operation", "book_campaigns_operation", "quote_campaigns_operation",
             "smart_campaign_setting_operation"]


@pytest.mark.parametrize("name", ["app", "search", "demand gen", "pmax", "upload", "update APP_AD"])
@pytest.mark.parametrize("which", NO_ACTION)
def test_an_operation_without_an_action_oneof_is_refused_not_a_crash(ads_client, build, which, name):
    r = build(name)
    op = ads_client.get_type("MutateOperation")
    part = getattr(type(op).pb(op), which)
    assert "operation" not in part.DESCRIPTOR.oneofs_by_name
    part.SetInParent()
    ops = [op, *r.ops]
    assert allowlists.problems(r.plan, ops) == [_unexpected(f"{which} None", "the request")]
    assert server._guard(r.plan, ops, ads_client, r.cfg, r.facts) == [
        f"internal safety check: operation 0 ({which} None) is not allowed in a {r.plan.kind} plan"]


# Google refuses a target with maximize conversions and a CPC ceiling with maximize clicks on a new Demand Gen
# campaign (check-only run, API v26): the list holds both messages empty. The bidding check also refuses them, and
# runs first in the guard, so the list is asked directly.
@pytest.mark.parametrize("name, member, field, value", [
    ("demand gen", "maximize_conversions", "target_cpa_micros", 1_000_000),
    ("demand gen clicks", "target_spend", "cpc_bid_ceiling_micros", 800_000),
])
def test_demand_gen_bidding_messages_are_empty(ads_client, build, name, member, field, value):
    r = build(name)
    assert allowlists.problems(r.plan, r.ops) == []
    setattr(getattr(_creates(r, "campaign_operation"), member), field, value)
    assert allowlists.problems(r.plan, r.ops) == [_unexpected(f"{member}.{field}", "campaign")]
    assert guard(ads_client, r) == [server.BIDDING]


# ------------------------------------------------- each part only with its own action


@pytest.mark.parametrize("name, which", [
    ("app", "campaign_operation"), ("search", "campaign_budget_operation"), ("demand gen", "ad_group_operation"),
    ("upload", "asset_operation"), ("update APP_AD", "asset_operation"),
])
def test_an_update_of_a_part_the_plan_only_creates_is_refused(ads_client, build, name, which):
    """The allowlist ties each kind of operation to its one action, as the guard's first check does."""
    r = build(name)
    op = ads_client.get_type("MutateOperation")
    getattr(type(op).pb(op), which).update.resource_name = _creates(r, which).resource_name
    assert allowlists.problems(r.plan, [*r.ops, op]) == [_unexpected(f"{which} update", "the request")]


# ------------------------------------------------- unknown field numbers


def _unknown_field(number: int = 131071) -> bytes:
    """The wire bytes of one varint field (value 1) with a field number no Google Ads message has."""
    tag, out = number << 3, bytearray()
    while tag > 0x7F:
        out.append(tag & 0x7F | 0x80)
        tag >>= 7
    return bytes([*out, tag, 1])


@pytest.mark.parametrize("name, where, field, part", [
    ("app", lambda r: _creates(r, "campaign_operation"), "#131071", "campaign"),
    ("search", lambda r: _creates(r, "campaign_operation"), "#131071", "campaign"),
    ("app", lambda r: _creates(r, "campaign_budget_operation"), "#131071", "budget"),
    ("demand gen", lambda r: _creates(r, "campaign_budget_operation"), "#131071", "budget"),
    ("app", lambda r: _creates(r, "ad_group_ad_operation").ad, "ad.#131071", "ad"),
    ("search", lambda r: _creates(r, "ad_group_ad_operation").ad, "ad.#131071", "ad"),
    ("demand gen", lambda r: _creates(r, "ad_group_ad_operation").ad, "ad.#131071", "ad"),
    ("demand gen", lambda r: _creates(r, "ad_group_ad_operation").ad.demand_gen_multi_asset_ad.headlines[0],
     "ad.demand_gen_multi_asset_ad.headlines.#131071", "ad"),
    ("search", lambda r: _creates(r, "campaign_operation").network_settings, "network_settings.#131071", "campaign"),
    ("demand gen", lambda r: _creates(r, "campaign_operation").maximize_conversions,
     "maximize_conversions.#131071", "campaign"),
    ("update APP_AD", lambda r: _creates(r, "ad_operation"), "#131071", "ad update"),
    ("update RESPONSIVE_SEARCH_AD", lambda r: _creates(r, "ad_operation").responsive_search_ad,
     "responsive_search_ad.#131071", "ad update"),
    ("upload", lambda r: _creates(r, "asset_operation"), "#131071", "asset"),
    ("upload", lambda r: _operation(r, "asset_operation"), "#131071", "the asset operation"),
    ("demand gen", lambda r: _operation(r, "campaign_conversion_goal_operation").update_mask, "update_mask.#131071",
     "the conversion goal operation"),
    ("search", lambda r: type(r.ops[0]).pb(r.ops[0]), "#131071", "the request"),
    ("pmax", lambda r: _creates(r, "asset_group_operation"), "#131071", "asset group"),
    ("pmax", lambda r: _creates(r, "asset_group_asset_operation"), "#131071", "asset group link"),
    ("pmax", lambda r: _creates(r, "asset_group_signal_operation", 1).search_theme, "search_theme.#131071",
     "asset group signal"),
    ("pmax", lambda r: _creates(r, "campaign_operation").asset_automation_settings[0],
     "asset_automation_settings.#131071", "campaign"),
    ("pmax", lambda r: _operation(r, "asset_group_signal_operation"), "#131071", "the asset group signal operation"),
])
def test_an_unknown_field_number_is_refused(ads_client, build, name, where, field, part):
    """Bytes the installed library doesn't know are still sent; the allowlist can't name them, so it refuses them."""
    r = build(name)
    where(r).MergeFromString(_unknown_field())
    assert guard(ads_client, r) == [_unexpected(field, part)]


# ------------------------------------------------- per-kind lists


def _criteria(r: Request, which: str, negative: bool):
    return [c for c in (_creates(r, which, i) for i in range(sum(
        type(op).pb(op).WhichOneof("operation") == which for op in r.ops))) if c.negative is negative]


def _set_raw(get, field, value):
    return lambda r, E: setattr(get(r), field, value(E) if callable(value) else value)


KIND_TAMPERS = [
    # a call-to-action asset carries no name; the compiler names only images and videos
    ("demand gen", _asset("call_to_action_asset", "name", "Sneaky"), _unexpected("name", "asset")),
    # a callout or snippet has no final URL; only a sitelink does
    ("search", _asset("callout_asset", "final_urls", "https://www.example.com/"), _unexpected("final_urls", "asset")),
    ("search", _asset("structured_snippet_asset", "final_urls", "https://www.example.com/"),
     _unexpected("final_urls", "asset")),
    # a negative keyword has no status; a positive one is never sent as "not negative"
    ("search", _set_raw(lambda r: _criteria(r, "ad_group_criterion_operation", True)[0], "status",
                        lambda E: E.AdGroupCriterionStatusEnum.ENABLED), _unexpected("status", "ad group criterion")),
    ("search", _set_raw(lambda r: _criteria(r, "ad_group_criterion_operation", False)[0], "negative", False),
     _unexpected("negative", "ad group criterion")),
    ("search", _set_raw(lambda r: _creates(r, "campaign_criterion_operation"), "negative", False),
     _unexpected("negative", "campaign criterion")),
]


@pytest.mark.parametrize("name, edit, expected", KIND_TAMPERS)
def test_each_kind_of_asset_and_criterion_has_its_own_list(ads_client, build, name, edit, expected):
    r = build(name)
    edit(r, ads_client.enums)
    assert guard(ads_client, r) == [expected]


def _added(which, fill):
    """A request with one more create of `which`, filled by fill(create, request, enums)."""
    def edit(r, E, client):
        op = client.get_type("MutateOperation")
        fill(getattr(type(op).pb(op), which).create, r, E)
        r.ops.append(op)
    return edit


def _keyword_on(field, which):
    """A positive keyword criterion on the new campaign or ad group."""
    def fill(criterion, r, E):
        setattr(criterion, field, _creates(r, which).resource_name)
        criterion.keyword.text = "gym"
    return fill


@pytest.mark.parametrize("name, edit, expected", [
    ("upload", _added("asset_operation", lambda a, r, E: setattr(a.youtube_video_asset, "youtube_video_id", "x")),
     _unexpected("youtube_video_asset", "asset")),
    ("app", _added("asset_operation", lambda a, r, E: setattr(a.call_to_action_asset, "call_to_action",
                                                              E.CallToActionTypeEnum.SIGN_UP)),
     _unexpected("call_to_action_asset", "asset")),
    ("search", _added("asset_operation", lambda a, r, E: setattr(a.image_asset, "data", b"x")),
     _unexpected("image_asset", "asset")),
    ("search", _added("campaign_criterion_operation", _keyword_on("campaign", "campaign_operation")),
     _unexpected("keyword", "campaign criterion")),
    ("demand gen", _added("ad_group_criterion_operation", _keyword_on("ad_group", "ad_group_operation")),
     _unexpected("keyword", "ad group criterion")),
])
def test_a_kind_the_compiler_never_sends_is_refused_by_its_name(ads_client, build, name, edit, expected):
    r = build(name)
    edit(r, ads_client.enums, ads_client)
    assert allowlists.problems(r.plan, r.ops) == [expected]



@pytest.mark.parametrize("which, field_type, expected", [
    ("asset_group_asset_operation", "LOGO", _unexpected("LOGO", "asset group link")),
    ("asset_group_asset_operation", "SITELINK", _unexpected("SITELINK", "asset group link")),
    ("asset_group_asset_operation", "TALL_PORTRAIT_MARKETING_IMAGE",
     _unexpected("TALL_PORTRAIT_MARKETING_IMAGE", "asset group link")),
    ("campaign_asset_operation", "SITELINK", _unexpected("SITELINK", "asset link")),
    ("campaign_asset_operation", "HEADLINE", _unexpected("HEADLINE", "asset link")),
])
def test_a_pmax_link_of_another_field_type_is_refused_by_its_name(ads_client, build, which, field_type, expected):
    """The field allowlist alone, without the guard's counts: each kind of link is listed by its field type."""
    r = build("pmax")
    _creates(r, which).field_type = getattr(ads_client.enums.AssetFieldTypeEnum, field_type)
    assert allowlists.problems(r.plan, r.ops) == [expected]
    _creates(r, which).field_type = 999  # a number the library has no name for
    assert allowlists.problems(r.plan, r.ops) == [_unexpected("999", expected.rsplit(" on ", 1)[1])]
