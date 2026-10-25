"""AppCampaignPlan -> one all-or-nothing list of MutateOperations with temporary IDs.

Safety rule 1 lives here: the campaign status is hard-coded to PAUSED."""

from __future__ import annotations

import hashlib
import itertools
from pathlib import Path

from . import plans
from .plans import APP_GOALS, AppCampaignPlan, ImageRef, VideoRef


def to_micros(amount: float) -> int:
    return int(round(amount * 1_000_000))


def text_assets(client, texts: list[str]) -> list:
    """AdTextAssets for an ad's headlines or descriptions (App ads and responsive search ads)."""
    out = []
    for text in texts:
        asset = client.get_type("AdTextAsset")
        asset.text = text
        out.append(asset)
    return out


def fill_app_ad_info(client, info, ad_type: str, headlines, descriptions, images, videos) -> None:
    """Fill AppAdInfo (APP_AD) or AppEngagementAdInfo (APP_ENGAGEMENT_AD)."""
    info.headlines.extend(text_assets(client, headlines))
    info.descriptions.extend(text_assets(client, descriptions))
    for name in images:
        asset = client.get_type("AdImageAsset")
        asset.asset = name
        info.images.append(asset)
    video_field = info.youtube_videos if ad_type == "APP_AD" else info.videos
    for name in videos:
        asset = client.get_type("AdVideoAsset")
        asset.asset = name
        video_field.append(asset)


def asset_ops(client, customer_id: str, ids, prefix: str, images: list[ImageRef], videos: list[VideoRef]):
    """Create-operations for new images/videos. Returns (ops, labels, image_names, video_names)."""
    ops, labels, image_names, video_names = [], [], [], []
    for i, ref in enumerate(images):
        if ref.asset:
            image_names.append(ref.asset)
            continue
        path = Path(ref.file).expanduser()
        name = f"customers/{customer_id}/assets/{next(ids)}"
        op = client.get_type("MutateOperation")
        asset = op.asset_operation.create
        asset.resource_name = name
        data = path.read_bytes()
        asset.name = plans.image_asset_name(prefix, path, hashlib.sha256(data).hexdigest())
        asset.image_asset.data = data
        ops.append(op)
        labels.append(f"image[{i}] {path.name}")
        image_names.append(name)
    for i, ref in enumerate(videos):
        if ref.asset:
            video_names.append(ref.asset)
            continue
        name = f"customers/{customer_id}/assets/{next(ids)}"
        op = client.get_type("MutateOperation")
        asset = op.asset_operation.create
        asset.resource_name = name
        asset.name = plans.video_asset_name(prefix, ref.youtube_id)
        asset.youtube_video_asset.youtube_video_id = ref.youtube_id
        ops.append(op)
        labels.append(f"video[{i}] {ref.youtube_id}")
        video_names.append(name)
    return ops, labels, image_names, video_names


def compile_app_campaign(plan: AppCampaignPlan, client, customer_id: str) -> tuple[list, list[str]]:
    E = client.enums
    ids = itertools.count(-1, -1)
    ops, labels, image_names, video_names = asset_ops(
        client, customer_id, ids, plans.asset_prefix(plan), plan.images, plan.videos
    )

    def add(op, label):
        ops.append(op)
        labels.append(label)

    budget_name = f"customers/{customer_id}/campaignBudgets/{next(ids)}"
    op = client.get_type("MutateOperation")
    budget = op.campaign_budget_operation.create
    budget.resource_name = budget_name
    budget.name = plans.budget_name(plan.name)
    budget.amount_micros = to_micros(plan.daily_budget)
    budget.delivery_method = E.BudgetDeliveryMethodEnum.STANDARD
    budget.explicitly_shared = False  # App campaigns cannot use shared budgets
    add(op, "budget")

    campaign_name = f"customers/{customer_id}/campaigns/{next(ids)}"
    op = client.get_type("MutateOperation")
    c = op.campaign_operation.create
    c.resource_name = campaign_name
    c.name = plan.name
    c.status = E.CampaignStatusEnum.PAUSED  # never anything else
    c.campaign_budget = budget_name
    c.advertising_channel_type = E.AdvertisingChannelTypeEnum.MULTI_CHANNEL
    c.advertising_channel_sub_type = getattr(E.AdvertisingChannelSubTypeEnum, plan.subtype)
    c.app_campaign_setting.app_id = plan.app_id
    c.app_campaign_setting.app_store = getattr(E.AppCampaignAppStoreEnum, plan.app_store)
    c.app_campaign_setting.bidding_strategy_goal_type = getattr(
        E.AppCampaignBiddingStrategyGoalTypeEnum, plan.goal
    )
    c.contains_eu_political_advertising = (
        E.EuPoliticalAdvertisingStatusEnum.DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
    )
    bidding, _ = APP_GOALS[plan.goal]
    if bidding == "TARGET_CPA":
        c.target_cpa.target_cpa_micros = to_micros(plan.target_cpa)
    else:
        type(c).pb(c).maximize_conversions.SetInParent()
    c.selective_optimization.conversion_actions.extend(plan.conversion_actions)
    add(op, "campaign")

    for location in plan.locations:
        op = client.get_type("MutateOperation")
        criterion = op.campaign_criterion_operation.create
        criterion.campaign = campaign_name
        criterion.location.geo_target_constant = location
        add(op, f"location {location}")
    for language in plan.languages:
        op = client.get_type("MutateOperation")
        criterion = op.campaign_criterion_operation.create
        criterion.campaign = campaign_name
        criterion.language.language_constant = language
        add(op, f"language {language}")

    ad_group_name = f"customers/{customer_id}/adGroups/{next(ids)}"
    op = client.get_type("MutateOperation")
    ad_group = op.ad_group_operation.create
    ad_group.resource_name = ad_group_name
    ad_group.name = plans.ad_group_name(plan.name)
    ad_group.campaign = campaign_name
    ad_group.status = E.AdGroupStatusEnum.ENABLED  # inside a paused campaign
    add(op, "ad group")

    op = client.get_type("MutateOperation")
    ad_group_ad = op.ad_group_ad_operation.create
    ad_group_ad.ad_group = ad_group_name
    ad_group_ad.status = E.AdGroupAdStatusEnum.ENABLED
    ad_type = "APP_AD" if plan.subtype == "APP_CAMPAIGN" else "APP_ENGAGEMENT_AD"
    info = ad_group_ad.ad.app_ad if ad_type == "APP_AD" else ad_group_ad.ad.app_engagement_ad
    fill_app_ad_info(client, info, ad_type, plan.headlines, plan.descriptions, image_names, video_names)
    for link in plan.final_app_urls:  # deep links into the app (required for engagement ads)
        app_url = client.get_type("FinalAppUrl")
        app_url.os_type = getattr(E.AppUrlOperatingSystemTypeEnum, link["os_type"])
        app_url.url = link["url"]
        ad_group_ad.ad.final_app_urls.append(app_url)
    add(op, "ad")
    return ops, labels
