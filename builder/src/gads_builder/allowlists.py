"""The exact fields each compiler sets, for every plan kind and every kind of change. The guard refuses a
request with any other field set, at every depth listed here, so a field no other check reads (a tracking
template, an opted-in automation, a bid modifier, a budget period, a policy exemption) can't ride along. A field
number the installed library doesn't know (an unknown field, which protobuf keeps and sends) is refused too, at
every depth, and so is any action on an operation but the one its compiler uses (create, or update).

An allowlist maps each allowed field to None (a plain value) or to the allowlist of the message it holds (for
a repeated message, of each item). A message listed with None or {} must be empty, as `manual_cpc` is. Where
kinds of one message differ (a sitelink, callout or snippet asset; a location, language or negative keyword
criterion; a Performance Max asset link's field type or signal), a Variants holds one list per kind. The lists are
exactly what compile_app, compile_search, compile_demand_gen, compile_pmax and compile_assets send;
tests/test_allowlists.py compares them with compiled requests of every kind."""

from __future__ import annotations

from typing import Any, Callable, NamedTuple

from google.protobuf import unknown_fields

from .plans_demand_gen import CHANNELS

UNEXPECTED = "internal safety check: unexpected field {field} on {part}"


def _fields(*names: str) -> dict:
    """Plain fields (values, not messages)."""
    return dict.fromkeys(names)


class Variants(NamedTuple):
    """A message whose allowed fields depend on its kind: kind(message) names it (read on the raw protobuf), and
    lists holds each kind's allowlist. A kind not listed is refused by its name."""
    kind: Callable[[Any], str | None]
    lists: dict[str, dict]


def _asset_kind(asset) -> str | None:
    return asset.WhichOneof("asset_data")


def _signal_kind(signal) -> str | None:
    """An asset group signal's kind: "audience" or "search_theme"."""
    return signal.WhichOneof("signal")


def _field_type_kind(link) -> str | None:
    """An asset link's field type (AssetFieldTypeEnum name, or its number if the library has no name for it)."""
    value = link.DESCRIPTOR.fields_by_name["field_type"].enum_type.values_by_number.get(link.field_type)
    return value.name if value else str(link.field_type)


def _criterion_kind(criterion) -> str | None:
    """A criterion's kind: "location", "keyword", ..., or "negative keyword" for a negative one."""
    kind = criterion.WhichOneof("criterion")
    return f"negative {kind}" if kind and criterion.negative else kind


TEXT = _fields("text")  # AdTextAsset: never pinned
ASSET = _fields("asset")  # AdImageAsset, AdVideoAsset, AdCallToActionAsset
BUDGET = _fields("resource_name", "name", "amount_micros", "delivery_method", "explicitly_shared")
_NEW_IMAGE = {"resource_name": None, "name": None, "image_asset": _fields("data")}
_NEW_VIDEO = {"resource_name": None, "name": None, "youtube_video_asset": _fields("youtube_video_id")}
NEW_MEDIA = Variants(_asset_kind, {"image_asset": _NEW_IMAGE, "youtube_video_asset": _NEW_VIDEO})
_LOCATION = {"location": _fields("geo_target_constant")}
_LANGUAGE = {"language": _fields("language_constant")}
_KEYWORD = {"keyword": _fields("text", "match_type")}
_NEW_CAMPAIGN = _fields("resource_name", "name", "status", "campaign_budget", "advertising_channel_type",
                        "contains_eu_political_advertising")
_APP_AD = {"headlines": TEXT, "descriptions": TEXT, "images": ASSET, "youtube_videos": ASSET}
_APP_ENGAGEMENT_AD = {"headlines": TEXT, "descriptions": TEXT, "images": ASSET, "videos": ASSET}
_RSA = {"headlines": TEXT, "descriptions": TEXT}

APP = {
    "asset_operation": NEW_MEDIA,
    "campaign_budget_operation": BUDGET,
    "campaign_operation": {
        **_NEW_CAMPAIGN,
        "advertising_channel_sub_type": None,
        "app_campaign_setting": _fields("app_id", "app_store", "bidding_strategy_goal_type"),
        "selective_optimization": _fields("conversion_actions"),
        "target_cpa": _fields("target_cpa_micros"),
        "maximize_conversions": {},  # an App goal without a target
    },
    "campaign_criterion_operation": Variants(_criterion_kind, {
        "location": {"campaign": None, **_LOCATION}, "language": {"campaign": None, **_LANGUAGE}}),
    "ad_group_operation": _fields("resource_name", "name", "campaign", "status"),
    "ad_group_ad_operation": {
        "ad_group": None, "status": None,
        "ad": {"app_ad": _APP_AD, "app_engagement_ad": _APP_ENGAGEMENT_AD, "final_app_urls": _fields("os_type", "url")},
    },
}

SEARCH = {
    "asset_operation": Variants(_asset_kind, {
        "sitelink_asset": {"resource_name": None, "final_urls": None,
                           "sitelink_asset": _fields("link_text", "description1", "description2")},
        "callout_asset": {"resource_name": None, "callout_asset": _fields("callout_text")},
        "structured_snippet_asset": {"resource_name": None, "structured_snippet_asset": _fields("header", "values")},
    }),
    "campaign_budget_operation": BUDGET,
    "campaign_operation": {
        **_NEW_CAMPAIGN,
        "network_settings": _fields("target_google_search", "target_search_network", "target_content_network",
                                    "target_partner_search_network"),
        "geo_target_type_setting": _fields("positive_geo_target_type"),
        "ai_max_setting": _fields("enable_ai_max"),
        "maximize_conversions": _fields("target_cpa_micros"),
        "target_cpa": _fields("target_cpa_micros"),
        "target_spend": _fields("cpc_bid_ceiling_micros"),
        "target_impression_share": _fields("location", "location_fraction_micros", "cpc_bid_ceiling_micros"),
        "manual_cpc": {},
    },
    "campaign_criterion_operation": Variants(_criterion_kind, {
        "location": {"campaign": None, **_LOCATION}, "language": {"campaign": None, **_LANGUAGE},
        "negative keyword": {"campaign": None, "negative": None, **_KEYWORD}}),
    "ad_group_operation": _fields("resource_name", "name", "campaign", "status", "type_", "cpc_bid_micros"),
    "ad_group_criterion_operation": Variants(_criterion_kind, {
        "keyword": {"ad_group": None, "status": None, **_KEYWORD},
        "negative keyword": {"ad_group": None, "negative": None, **_KEYWORD}}),
    "ad_group_ad_operation": {
        "ad_group": None, "status": None,
        "ad": {"final_urls": None, "responsive_search_ad": {**_RSA, "path1": None, "path2": None}},
    },
    "campaign_asset_operation": _fields("campaign", "asset", "field_type"),
}

DEMAND_GEN = {
    "asset_operation": Variants(_asset_kind, {
        "image_asset": _NEW_IMAGE, "youtube_video_asset": _NEW_VIDEO,
        "call_to_action_asset": {"resource_name": None, "call_to_action_asset": _fields("call_to_action")}}),
    "campaign_budget_operation": BUDGET,
    "campaign_operation": {
        **_NEW_CAMPAIGN,
        "demand_gen_campaign_settings": _fields("upgraded_targeting"),
        "geo_target_type_setting": _fields("positive_geo_target_type"),
        "maximize_conversions": {},  # Google refuses a target here for Demand Gen
        "target_cpa": _fields("target_cpa_micros"),
        "target_spend": {},  # nor a CPC ceiling
    },
    "campaign_conversion_goal_operation": _fields("resource_name", "biddable"),
    "ad_group_operation": {
        **_fields("resource_name", "name", "campaign", "status", "exclude_demographic_expansion"),
        "demand_gen_ad_group_settings": {"channel_controls": {"selected_channels": _fields(*CHANNELS)}},
    },
    "ad_group_criterion_operation": Variants(_criterion_kind, {
        "location": {"ad_group": None, **_LOCATION}, "language": {"ad_group": None, **_LANGUAGE},
        "user_list": {"ad_group": None, "user_list": _fields("user_list")}}),
    "ad_group_ad_operation": {
        "ad_group": None, "status": None,
        "ad": {
            "final_urls": None, "name": None,
            "demand_gen_multi_asset_ad": {
                "headlines": TEXT, "descriptions": TEXT, "business_name": None, "marketing_images": ASSET,
                "square_marketing_images": ASSET, "portrait_marketing_images": ASSET, "logo_images": ASSET,
            },
            "demand_gen_video_responsive_ad": {
                "videos": ASSET, "headlines": TEXT, "long_headlines": TEXT, "descriptions": TEXT,
                "business_name": TEXT, "logo_images": ASSET, "call_to_actions": ASSET,
            },
        },
        "ad_group_ad_asset_automation_settings": _fields("asset_automation_type", "asset_automation_status"),
    },
}

PMAX = {
    "asset_operation": Variants(_asset_kind, {
        "text_asset": {"resource_name": None, "text_asset": _fields("text")},
        "image_asset": _NEW_IMAGE, "youtube_video_asset": _NEW_VIDEO,
        "call_to_action_asset": {"resource_name": None, "call_to_action_asset": _fields("call_to_action")}}),
    "campaign_budget_operation": BUDGET,
    "campaign_operation": {
        **_NEW_CAMPAIGN,
        "brand_guidelines_enabled": None,
        "asset_automation_settings": _fields("asset_automation_type", "asset_automation_status"),
        "geo_target_type_setting": _fields("positive_geo_target_type"),
        "maximize_conversions": _fields("target_cpa_micros"),
        "maximize_conversion_value": _fields("target_roas"),
    },
    "campaign_asset_operation": Variants(_field_type_kind, dict.fromkeys(  # the brand, with brand guidelines on
        ("BUSINESS_NAME", "LOGO", "LANDSCAPE_LOGO"), _fields("campaign", "asset", "field_type"))),
    "campaign_criterion_operation": Variants(_criterion_kind, {
        "location": {"campaign": None, **_LOCATION}, "language": {"campaign": None, **_LANGUAGE}}),
    "campaign_conversion_goal_operation": _fields("resource_name", "biddable"),
    "asset_group_operation": _fields("resource_name", "name", "campaign", "final_urls", "status", "path1", "path2"),
    "asset_group_asset_operation": Variants(_field_type_kind, dict.fromkeys(
        ("HEADLINE", "LONG_HEADLINE", "DESCRIPTION", "MARKETING_IMAGE", "SQUARE_MARKETING_IMAGE",
         "PORTRAIT_MARKETING_IMAGE", "YOUTUBE_VIDEO", "CALL_TO_ACTION_SELECTION"),
        _fields("asset_group", "asset", "field_type"))),
    "asset_group_signal_operation": Variants(_signal_kind, {
        "audience": {"asset_group": None, "audience": _fields("audience")},
        "search_theme": {"asset_group": None, "search_theme": _fields("text")}}),
}

IMAGE_UPLOAD = {"asset_operation": Variants(_asset_kind, {"image_asset": _NEW_IMAGE})}

# asset_update: the ad type -> its info field and the only asset lists an update may set (and name in its mask)
AD_UPDATES = {
    "APP_AD": ("app_ad", _APP_AD),
    "APP_ENGAGEMENT_AD": ("app_engagement_ad", _APP_ENGAGEMENT_AD),
    "RESPONSIVE_SEARCH_AD": ("responsive_search_ad", _RSA),
}

_BY_KIND = {"app_campaign": APP, "search_campaign": SEARCH, "demand_gen_campaign": DEMAND_GEN, "pmax_campaign": PMAX,
            "image_upload": IMAGE_UPLOAD}
# The operations a plan kind updates, with the update mask paths each may name. Every other operation in its
# list is a create, and only a create.
_UPDATES = {"demand_gen_campaign": {"campaign_conversion_goal_operation": {"biddable"}},
            "pmax_campaign": {"campaign_conversion_goal_operation": {"biddable"}}}

# MutateOperation field -> what the refusal calls it
PARTS = {
    "asset_operation": "asset", "campaign_budget_operation": "budget", "campaign_operation": "campaign",
    "campaign_criterion_operation": "campaign criterion", "ad_group_operation": "ad group",
    "ad_group_criterion_operation": "ad group criterion", "ad_group_ad_operation": "ad",
    "campaign_asset_operation": "asset link", "campaign_conversion_goal_operation": "conversion goal",
    "ad_operation": "ad update", "asset_group_operation": "asset group",
    "asset_group_asset_operation": "asset group link", "asset_group_signal_operation": "asset group signal",
}


def for_plan(plan) -> tuple[dict[str, dict | Variants], dict[str, set[str]]]:
    """({MutateOperation field: the allowlist of what it creates or updates}, {field: the update mask paths
    it may name}) for one plan. A field in the second is updated; every other field in the first is created."""
    if plan.kind == "asset_update":
        field, info = AD_UPDATES.get(plan.ad_type, ("", {}))
        ad = {"resource_name": None, **({field: info} if field else {})}
        media = {} if plan.ad_type == "RESPONSIVE_SEARCH_AD" else {"asset_operation": NEW_MEDIA}  # no new assets
        return ({**media, "ad_operation": ad}, {"ad_operation": {f"{field}.{name}" for name in info}})
    return _BY_KIND.get(plan.kind, {}), _UPDATES.get(plan.kind, {})


def _unknown(message, path: str) -> str | None:
    """The first unknown field number on the raw protobuf `message` (not its sub-messages), as path#number."""
    for item in unknown_fields.UnknownFieldSet(message):
        return f"{path}#{item.field_number}"
    return None


def unexpected(message, allowed: dict | Variants | None, path: str = "") -> str | None:
    """The first field set on the raw protobuf `message` that `allowed` doesn't list, as a dotted path (an unknown
    field number as #number, a kind not listed by its name), or None if every field set is listed (recursively)."""
    found = _unknown(message, path)
    if found:
        return found
    if isinstance(allowed, Variants):
        kind = allowed.kind(message)
        if kind is not None and kind not in allowed.lists:
            return path + kind
        allowed = allowed.lists.get(kind)  # no kind at all: any field set is refused
    allowed = allowed or {}
    for field, value in message.ListFields():
        name = path + field.name
        if field.name not in allowed:
            return name
        if field.message_type is not None:
            for item in value if field.is_repeated else [value]:
                found = unexpected(item, allowed[field.name], name + ".")
                if found:
                    return found
    return None


def action(operation) -> str | None:
    """The action set on one MutateOperation member (raw protobuf), such as "create" or "update"; None if none is
    set or its message has no "operation" oneof (customer_operation, book_campaigns_operation and two more in v26,
    where asking would raise ValueError). The callers refuse None as not allowed."""
    if operation is None or "operation" not in operation.DESCRIPTOR.oneofs_by_name:
        return None
    return operation.WhichOneof("operation")


def problems(plan, ops) -> list[str]:
    """[] if every operation is its part's one action and sets only the fields its compiler sends for this plan;
    otherwise why not."""
    allowed, masks = for_plan(plan)
    for op in ops:
        pb = type(op).pb(op)
        found = _unknown(pb, "")
        if found:
            return [UNEXPECTED.format(field=found, part="the request")]
        which = pb.WhichOneof("operation")
        operation = getattr(pb, which) if which else None
        act = action(operation)
        part = PARTS.get(which, which or "operation")
        if which not in allowed or act != ("update" if which in masks else "create"):
            return [UNEXPECTED.format(field=f"{which} {act}", part="the request")]
        outer = {"create"} if act == "create" else {"update", "update_mask"}
        has_mask = "update_mask" in operation.DESCRIPTOR.fields_by_name  # an asset group signal has none
        found = _unknown(operation, "") or (_unknown(operation.update_mask, "update_mask.")
                                            if has_mask and operation.HasField("update_mask") else None)
        if found:
            return [UNEXPECTED.format(field=found, part=f"the {part} operation")]
        for field, _ in operation.ListFields():
            if field.name not in outer:
                return [UNEXPECTED.format(field=field.name, part=f"the {part} operation")]
        found = unexpected(getattr(operation, act), allowed[which])
        if found:
            return [UNEXPECTED.format(field=found, part=part)]
        if act == "update":
            extra = sorted(set(operation.update_mask.paths) - masks.get(which, set()))
            if extra:
                return [UNEXPECTED.format(field=f"update_mask path {extra[0]}", part=part)]
    return []
