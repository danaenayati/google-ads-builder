import hashlib

import pytest

from gads_builder import plans
from gads_builder.compile_app import compile_app_campaign, to_micros

CID = "1234567890"


def by_label(ops, labels, label):
    return ops[labels.index(label)]


def test_ad_info_field_names_exist(ads_client):
    # Guards the two formats verified on the live account: APP_AD and APP_ENGAGEMENT_AD.
    assert len(ads_client.get_type("AppAdInfo").youtube_videos) == 0
    assert len(ads_client.get_type("AppEngagementAdInfo").videos) == 0


def test_order_and_references(ads_client, app_plan, make_png):
    img = make_png(1200, 628, "land.png")
    plan = plans.plan_from_dict(app_plan(images=[{"file": str(img)}]))
    ops, labels = compile_app_campaign(plan, ads_client, CID)
    assert labels == [
        "image[0] land.png",
        "video[0] abcdefghijk",
        "budget",
        "campaign",
        "location geoTargetConstants/1000001",
        "language languageConstants/1000",
        "language languageConstants/1019",
        "ad group",
        "ad",
    ]
    image = by_label(ops, labels, "image[0] land.png").asset_operation.create
    budget = by_label(ops, labels, "budget").campaign_budget_operation.create
    campaign = by_label(ops, labels, "campaign").campaign_operation.create
    ad_group = by_label(ops, labels, "ad group").ad_group_operation.create
    ad = by_label(ops, labels, "ad").ad_group_ad_operation.create
    temp_ids = [image.resource_name, budget.resource_name, campaign.resource_name, ad_group.resource_name]
    assert all(int(r.rsplit("/", 1)[1]) < 0 for r in temp_ids) and len(set(temp_ids)) == 4
    assert campaign.campaign_budget == budget.resource_name
    assert ad_group.campaign == campaign.resource_name
    assert ad.ad_group == ad_group.resource_name
    assert by_label(ops, labels, "location geoTargetConstants/1000001").campaign_criterion_operation.create.campaign == campaign.resource_name
    assert image.image_asset.data == img.read_bytes()
    digest = hashlib.sha256(img.read_bytes()).hexdigest()[:8]
    assert image.name == f"Test City Launch | land.png | {digest}"
    video = ops[labels.index("video[0] abcdefghijk")].asset_operation.create
    assert video.name == "Test City Launch | YouTube abcdefghijk"
    assert [a.asset for a in ad.ad.app_ad.images] == [image.resource_name]


def test_campaign_settings_are_safe(ads_client, app_plan):
    E = ads_client.enums
    ops, labels = compile_app_campaign(plans.plan_from_dict(app_plan()), ads_client, CID)
    campaign = by_label(ops, labels, "campaign").campaign_operation.create
    budget = by_label(ops, labels, "budget").campaign_budget_operation.create
    assert campaign.status == E.CampaignStatusEnum.PAUSED
    assert campaign.advertising_channel_type == E.AdvertisingChannelTypeEnum.MULTI_CHANNEL
    assert campaign.advertising_channel_sub_type == E.AdvertisingChannelSubTypeEnum.APP_CAMPAIGN
    assert campaign.app_campaign_setting.app_id == "com.example.app"
    assert campaign.contains_eu_political_advertising == (
        E.EuPoliticalAdvertisingStatusEnum.DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
    )
    assert campaign.target_cpa.target_cpa_micros == 12_500_000
    assert list(campaign.selective_optimization.conversion_actions) == ["customers/1234567890/conversionActions/111"]
    assert budget.amount_micros == 300_000_000 and budget.explicitly_shared is False


def test_maximize_conversions_goal(ads_client, app_plan):
    plan = plans.plan_from_dict(app_plan(goal="OPTIMIZE_IN_APP_CONVERSIONS_WITHOUT_TARGET_CPA", target_cpa=None))
    ops, labels = compile_app_campaign(plan, ads_client, CID)
    campaign = by_label(ops, labels, "campaign").campaign_operation.create
    assert "maximize_conversions" in campaign
    assert "target_cpa" not in campaign


def test_engagement_uses_engagement_ad(ads_client, app_plan):
    plan = plans.plan_from_dict(
        app_plan(subtype="APP_CAMPAIGN_FOR_ENGAGEMENT", videos=[{"asset": "customers/1234567890/assets/9"}])
    )
    ops, labels = compile_app_campaign(plan, ads_client, CID)
    ad = by_label(ops, labels, "ad").ad_group_ad_operation.create.ad
    assert [h.text for h in ad.app_engagement_ad.headlines][0] == "Fast workouts"
    assert [v.asset for v in ad.app_engagement_ad.videos] == ["customers/1234567890/assets/9"]
    assert "app_ad" not in ad


def test_to_micros():
    assert to_micros(12.345678) == 12_345_678


GOALS = [
    ("APP_CAMPAIGN", "OPTIMIZE_INSTALLS_TARGET_INSTALL_COST", True),
    ("APP_CAMPAIGN", "OPTIMIZE_INSTALLS_WITHOUT_TARGET_INSTALL_COST", False),
    ("APP_CAMPAIGN", "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST", True),
    ("APP_CAMPAIGN", "OPTIMIZE_IN_APP_CONVERSIONS_WITHOUT_TARGET_CPA", False),
    ("APP_CAMPAIGN_FOR_ENGAGEMENT", "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST", True),
    ("APP_CAMPAIGN_FOR_ENGAGEMENT", "OPTIMIZE_IN_APP_CONVERSIONS_WITHOUT_TARGET_CPA", False),
]


@pytest.mark.parametrize("subtype,goal,targeted", GOALS)
def test_every_goal_compiles_paused_with_the_right_bidding(ads_client, app_plan, subtype, goal, targeted):
    E = ads_client.enums
    plan = plans.plan_from_dict(app_plan(subtype=subtype, goal=goal, target_cpa=7.25 if targeted else None))
    ops, labels = compile_app_campaign(plan, ads_client, CID)
    campaign = by_label(ops, labels, "campaign").campaign_operation.create
    assert campaign.status == E.CampaignStatusEnum.PAUSED
    assert campaign.app_campaign_setting.bidding_strategy_goal_type == getattr(
        E.AppCampaignBiddingStrategyGoalTypeEnum, goal
    )
    assert campaign.advertising_channel_sub_type == getattr(E.AdvertisingChannelSubTypeEnum, subtype)
    if targeted:
        assert "target_cpa" in campaign
        assert campaign.target_cpa.target_cpa_micros == 7_250_000
        assert "maximize_conversions" not in campaign
    else:
        assert "maximize_conversions" in campaign
        assert "target_cpa" not in campaign


def test_new_youtube_asset_temp_id_is_the_one_the_ad_references(ads_client, app_plan):
    plan = plans.plan_from_dict(app_plan(videos=[{"youtube_id": "abcdefghijk"}]))
    ops, labels = compile_app_campaign(plan, ads_client, CID)
    video = ops[labels.index("video[0] abcdefghijk")].asset_operation.create
    ad = by_label(ops, labels, "ad").ad_group_ad_operation.create.ad
    assert video.youtube_video_asset.youtube_video_id == "abcdefghijk"
    assert int(video.resource_name.rsplit("/", 1)[1]) < 0
    assert [v.asset for v in ad.app_ad.youtube_videos] == [video.resource_name]


def test_existing_image_assets_are_referenced_unchanged(ads_client, app_plan):
    refs = ["customers/1234567890/assets/41", "customers/1234567890/assets/42"]
    plan = plans.plan_from_dict(app_plan(images=[{"asset": r} for r in refs]))
    ops, labels = compile_app_campaign(plan, ads_client, CID)
    assert not any(label.startswith("image") for label in labels)
    ad = by_label(ops, labels, "ad").ad_group_ad_operation.create.ad
    assert [i.asset for i in ad.app_ad.images] == refs


def test_engagement_deep_link_is_sent(ads_client, app_plan):
    plan = plans.plan_from_dict(app_plan(
        subtype="APP_CAMPAIGN_FOR_ENGAGEMENT", final_app_urls=[{"os_type": "ANDROID", "url": "example://open/.*"}]))
    ops, labels = compile_app_campaign(plan, ads_client, CID)
    ad = by_label(ops, labels, "ad").ad_group_ad_operation.create.ad
    assert [(u.os_type, u.url) for u in ad.final_app_urls] == [
        (ads_client.enums.AppUrlOperatingSystemTypeEnum.ANDROID, "example://open/.*")]


def test_install_ad_without_deep_link_sends_none(ads_client, app_plan):
    ops, labels = compile_app_campaign(plans.plan_from_dict(app_plan()), ads_client, CID)
    assert len(by_label(ops, labels, "ad").ad_group_ad_operation.create.ad.final_app_urls) == 0


def test_app_campaigns_set_no_location_option_or_ai_max(ads_client, app_plan):
    ops, labels = compile_app_campaign(plans.plan_from_dict(app_plan()), ads_client, "1234567890")
    c = ops[labels.index("campaign")].campaign_operation.create
    cpb = type(c).pb(c)
    assert not cpb.HasField("geo_target_type_setting") and not cpb.HasField("ai_max_setting")
