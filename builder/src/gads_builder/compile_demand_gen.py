"""DemandGenCampaignPlan -> one all-or-nothing list of MutateOperations with temporary IDs.

Safety rule 1 lives here too: the campaign status is hard-coded to PAUSED. Every other fixed setting is
sent explicitly rather than left to Google's defaults: upgraded targeting (location and language on each
ad group), the location option (people in or regularly in the locations), the EU political ads
declaration, a budget of its own, each ad group's channels (never third-party Display or Maps), no
demographic expansion, and every generated creative opted out on every ad. The account's conversion goals
are all overridden on the new campaign: the plan's goals on, every other goal off."""

from __future__ import annotations

import hashlib
import itertools
from pathlib import Path

from google.protobuf import field_mask_pb2

from . import plans
from .compile_app import text_assets, to_micros
from .plans_demand_gen import (
    CHANNELS, GENERATED_CREATIVES, IMAGE_LISTS, NEVER_CHANNELS, DemandGenCampaignPlan, ImageAd, VideoAd, _file_key,
    ad_names, calls_to_action, new_image_files, new_youtube_ids,
)


def _set_bidding(c, plan: DemandGenCampaignPlan) -> None:
    """Exactly one bidding strategy (a oneof in the Campaign message)."""
    pb = type(c).pb(c)
    if plan.bidding == "MAXIMIZE_CONVERSIONS":
        pb.maximize_conversions.SetInParent()  # Google refuses a target here for Demand Gen: TARGET_CPA has it
    elif plan.bidding == "TARGET_CPA":
        c.target_cpa.target_cpa_micros = to_micros(plan.target_cpa)
    elif plan.bidding == "MAXIMIZE_CLICKS":
        pb.target_spend.SetInParent()  # Google refuses a CPC ceiling here for Demand Gen
    else:
        raise ValueError(f"unknown bidding {plan.bidding!r}")


def campaign_temp_id(campaign_name: str) -> str:
    """customers/<cid>/campaigns/-7 -> -7"""
    return campaign_name.rsplit("/", 1)[-1]


def _image_assets(client, names: list[str]) -> list:
    out = []
    for name in names:
        asset = client.get_type("AdImageAsset")
        asset.asset = name
        out.append(asset)
    return out


def _video_assets(client, names: list[str]) -> list:
    out = []
    for name in names:
        asset = client.get_type("AdVideoAsset")
        asset.asset = name
        out.append(asset)
    return out


def compile_demand_gen_campaign(plan: DemandGenCampaignPlan, client, customer_id: str,
                                account_goals) -> tuple[list, list[str]]:
    """account_goals: the account's (category, origin) conversion goals, read live by check_plan."""
    E = client.enums
    ids = itertools.count(-1, -1)
    ops, labels = [], []

    def add(op, label):
        ops.append(op)
        labels.append(label)

    def temp(kind: str) -> str:
        return f"customers/{customer_id}/{kind}/{next(ids)}"

    # 1. New assets, each once: image files, YouTube videos, calls to action.
    image_names: dict[str, str] = {}  # resolved file -> new asset
    for n, file in enumerate(new_image_files(plan)):
        path = Path(file).expanduser()
        op = client.get_type("MutateOperation")
        asset = op.asset_operation.create
        asset.resource_name = temp("assets")
        data = path.read_bytes()
        asset.name = plans.image_asset_name(plan.name, path, hashlib.sha256(data).hexdigest())
        asset.image_asset.data = data
        image_names[_file_key(file)] = asset.resource_name
        add(op, f"image[{n}] {path.name}")
    video_names: dict[str, str] = {}  # YouTube ID -> new asset
    for n, youtube_id in enumerate(new_youtube_ids(plan)):
        op = client.get_type("MutateOperation")
        asset = op.asset_operation.create
        asset.resource_name = temp("assets")
        asset.name = plans.video_asset_name(plan.name, youtube_id)
        asset.youtube_video_asset.youtube_video_id = youtube_id
        video_names[youtube_id] = asset.resource_name
        add(op, f"video[{n}] {youtube_id}")
    cta_names: dict[str, str] = {}  # CallToActionTypeEnum name -> new asset
    for n, cta in enumerate(calls_to_action(plan)):
        op = client.get_type("MutateOperation")
        asset = op.asset_operation.create
        asset.resource_name = temp("assets")
        asset.call_to_action_asset.call_to_action = getattr(E.CallToActionTypeEnum, cta)
        cta_names[cta] = asset.resource_name
        add(op, f"call to action[{n}] {cta}")

    def images(refs) -> list[str]:
        return [ref.asset or image_names[_file_key(ref.file)] for ref in refs]

    # 2. Budget: the campaign's own.
    budget_name = temp("campaignBudgets")
    op = client.get_type("MutateOperation")
    budget = op.campaign_budget_operation.create
    budget.resource_name = budget_name
    budget.name = plans.budget_name(plan.name)
    budget.amount_micros = to_micros(plan.daily_budget)
    budget.delivery_method = E.BudgetDeliveryMethodEnum.STANDARD
    budget.explicitly_shared = False
    add(op, "budget")

    # 3. Campaign.
    campaign_name = temp("campaigns")
    op = client.get_type("MutateOperation")
    c = op.campaign_operation.create
    c.resource_name = campaign_name
    c.name = plan.name
    c.status = E.CampaignStatusEnum.PAUSED  # never anything else
    c.campaign_budget = budget_name
    c.advertising_channel_type = E.AdvertisingChannelTypeEnum.DEMAND_GEN
    c.demand_gen_campaign_settings.upgraded_targeting = True  # location and language on each ad group
    c.contains_eu_political_advertising = E.EuPoliticalAdvertisingStatusEnum.DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
    c.geo_target_type_setting.positive_geo_target_type = E.PositiveGeoTargetTypeEnum.PRESENCE  # not "interest in"
    _set_bidding(c, plan)
    add(op, "campaign")

    # 4. Conversion goals: every account goal is overridden on the new campaign, the plan's on, the rest off.
    wanted = {(g.category, g.origin) for g in plan.conversion_goals}
    for category, origin in account_goals:
        op = client.get_type("MutateOperation")
        goal = op.campaign_conversion_goal_operation.update
        goal.resource_name = (f"customers/{customer_id}/campaignConversionGoals/"
                              f"{campaign_temp_id(campaign_name)}~{category}~{origin}")
        goal.biddable = (category, origin) in wanted
        client.copy_from(op.campaign_conversion_goal_operation.update_mask, field_mask_pb2.FieldMask(paths=["biddable"]))
        add(op, f"conversion goal {category}~{origin}")

    # 5. Ad groups, each with its targeting and ads.
    names = ad_names(plan)
    for g, group in enumerate(plan.ad_groups):
        group_name = temp("adGroups")
        op = client.get_type("MutateOperation")
        ad_group = op.ad_group_operation.create
        ad_group.resource_name = group_name
        ad_group.name = group.name
        ad_group.campaign = campaign_name
        ad_group.status = E.AdGroupStatusEnum.ENABLED  # inside a paused campaign
        selected = ad_group.demand_gen_ad_group_settings.channel_controls.selected_channels
        for channel in (*CHANNELS, *NEVER_CHANNELS):
            setattr(selected, channel, channel in group.channels and channel in CHANNELS)
        ad_group.exclude_demographic_expansion = True  # optimized_targeting_enabled stays unset (False)
        add(op, f"ad group[{g}] {group.name}")
        for location in group.locations:
            op = client.get_type("MutateOperation")
            criterion = op.ad_group_criterion_operation.create
            criterion.ad_group = group_name
            criterion.location.geo_target_constant = location
            add(op, f"ad group[{g}] location {location}")
        for language in group.languages:
            op = client.get_type("MutateOperation")
            criterion = op.ad_group_criterion_operation.create
            criterion.ad_group = group_name
            criterion.language.language_constant = language
            add(op, f"ad group[{g}] language {language}")
        for audience in group.audiences:
            op = client.get_type("MutateOperation")
            criterion = op.ad_group_criterion_operation.create
            criterion.ad_group = group_name
            criterion.user_list.user_list = audience
            add(op, f"ad group[{g}] audience {audience}")
        for a, ad in enumerate(group.ads):
            op = client.get_type("MutateOperation")
            ad_group_ad = op.ad_group_ad_operation.create
            ad_group_ad.ad_group = group_name
            ad_group_ad.status = E.AdGroupAdStatusEnum.ENABLED
            if isinstance(ad, ImageAd):
                _image_ad(client, ad_group_ad.ad.demand_gen_multi_asset_ad, ad, images)
            else:
                _video_ad(client, ad_group_ad.ad.demand_gen_video_responsive_ad, ad, images, video_names, cta_names)
            ad_group_ad.ad.final_urls.append(ad.final_url)
            ad_group_ad.ad.name = names[g][a]  # Google requires it on Demand Gen ads
            for kind in GENERATED_CREATIVES[ad.type]:
                setting = client.get_type("AdGroupAdAssetAutomationSetting")
                setting.asset_automation_type = getattr(E.AssetAutomationTypeEnum, kind)
                setting.asset_automation_status = E.AssetAutomationStatusEnum.OPTED_OUT
                ad_group_ad.ad_group_ad_asset_automation_settings.append(setting)
            add(op, f"ad group[{g}] ad[{a}] {ad.type}")
    return ops, labels


def _image_ad(client, info, ad: ImageAd, images) -> None:
    info.headlines.extend(text_assets(client, ad.headlines))
    info.descriptions.extend(text_assets(client, ad.descriptions))
    info.business_name = ad.business_name
    for name, _, _ in IMAGE_LISTS:
        getattr(info, name).extend(_image_assets(client, images(getattr(ad, name))))


def _video_ad(client, info, ad: VideoAd, images, video_names: dict, cta_names: dict) -> None:
    info.videos.extend(_video_assets(client, [v.asset or video_names[v.youtube_id] for v in ad.videos]))
    info.headlines.extend(text_assets(client, ad.headlines))
    info.long_headlines.extend(text_assets(client, ad.long_headlines))
    info.descriptions.extend(text_assets(client, ad.descriptions))
    info.business_name.text = ad.business_name  # an AdTextAsset here, a plain string in the image ad
    info.logo_images.extend(_image_assets(client, images(ad.logo_images)))
    if ad.call_to_action:
        cta = client.get_type("AdCallToActionAsset")
        cta.asset = cta_names[ad.call_to_action]
        info.call_to_actions.append(cta)
