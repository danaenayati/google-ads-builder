import hashlib
from pathlib import Path

import pytest

from gads_builder import plans
from gads_builder.compile_demand_gen import compile_demand_gen_campaign

CID = "1234567890"
GOALS = (("PURCHASE", "WEBSITE"), ("SUBMIT_LEAD_FORM", "WEBSITE"), ("DOWNLOAD", "APP"))


def compiled(ads_client, data, goals=GOALS):
    return compile_demand_gen_campaign(plans.plan_from_dict(data), ads_client, CID, goals)


def op_at(ops, labels, label):
    return ops[labels.index(label)]


def which(op) -> str:
    return type(op).pb(op).WhichOneof("operation")


def raw(message):
    return type(message).pb(message)


def test_order_labels_and_operation_kinds(ads_client, demand_gen_plan):
    ops, labels = compiled(ads_client, demand_gen_plan())
    assert labels == [
        "image[0] dg-landscape.png", "image[1] dg-square.png", "image[2] dg-portrait.png", "image[3] dg-logo.png",
        "video[0] abcdefghijk",
        "budget", "campaign",
        "conversion goal PURCHASE~WEBSITE", "conversion goal SUBMIT_LEAD_FORM~WEBSITE", "conversion goal DOWNLOAD~APP",
        "ad group[0] Springfield members",
        "ad group[0] location geoTargetConstants/1000001", "ad group[0] language languageConstants/1000",
        "ad group[0] ad[0] image", "ad group[0] ad[1] video",
    ]
    assert [which(op) for op in ops] == ["asset_operation"] * 5 + [
        "campaign_budget_operation", "campaign_operation"] + ["campaign_conversion_goal_operation"] * 3 + [
        "ad_group_operation"] + ["ad_group_criterion_operation"] * 2 + ["ad_group_ad_operation"] * 2
    actions = [getattr(raw(op), which(op)).WhichOneof("operation") for op in ops]
    assert actions == ["create"] * 7 + ["update"] * 3 + ["create"] * 5


def test_temporary_ids_are_new_and_negative(ads_client, demand_gen_plan):
    ops, labels = compiled(ads_client, demand_gen_plan())
    assert op_at(ops, labels, "image[0] dg-landscape.png").asset_operation.create.resource_name == \
        f"customers/{CID}/assets/-1"
    assert op_at(ops, labels, "budget").campaign_budget_operation.create.resource_name == \
        f"customers/{CID}/campaignBudgets/-6"
    assert op_at(ops, labels, "campaign").campaign_operation.create.resource_name == f"customers/{CID}/campaigns/-7"
    assert op_at(ops, labels, "ad group[0] Springfield members").ad_group_operation.create.resource_name == \
        f"customers/{CID}/adGroups/-8"


def test_the_campaign_is_a_paused_demand_gen_campaign_with_every_setting_sent(ads_client, demand_gen_plan):
    E = ads_client.enums
    ops, labels = compiled(ads_client, demand_gen_plan())
    c = op_at(ops, labels, "campaign").campaign_operation.create
    budget = op_at(ops, labels, "budget").campaign_budget_operation.create
    assert c.status == E.CampaignStatusEnum.PAUSED and c.name == "Pulse Springfield - Demand Gen"
    assert c.advertising_channel_type == E.AdvertisingChannelTypeEnum.DEMAND_GEN
    assert c.advertising_channel_sub_type == E.AdvertisingChannelSubTypeEnum.UNSPECIFIED
    assert c.campaign_budget == budget.resource_name
    cpb = raw(c)
    assert cpb.demand_gen_campaign_settings.HasField("upgraded_targeting")
    assert cpb.demand_gen_campaign_settings.upgraded_targeting is True
    assert c.contains_eu_political_advertising == E.EuPoliticalAdvertisingStatusEnum.DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
    assert c.geo_target_type_setting.positive_geo_target_type == E.PositiveGeoTargetTypeEnum.PRESENCE
    assert not cpb.HasField("network_settings") and not cpb.HasField("selective_optimization")
    assert not cpb.HasField("view_through_conversion_optimization_enabled") and c.bidding_strategy == ""
    assert budget.amount_micros == 30_000_000 and budget.name == "Pulse Springfield - Demand Gen budget"
    assert raw(budget).HasField("explicitly_shared") and budget.explicitly_shared is False
    assert budget.delivery_method == E.BudgetDeliveryMethodEnum.STANDARD


def _bidding(campaign) -> str:
    return raw(campaign).WhichOneof("campaign_bidding_strategy")


@pytest.mark.parametrize("over, strategy, fields", [
    ({}, "maximize_conversions", {}),
    ({"target_cpa": 2.5}, "maximize_conversions", {}),  # validation refuses it; the compiler never sends it
    ({"bidding": "TARGET_CPA", "target_cpa": 2.0}, "target_cpa", {"target_cpa.target_cpa_micros": 2_000_000}),
    ({"bidding": "MAXIMIZE_CLICKS"}, "target_spend", {}),
    ({"bidding": "MAXIMIZE_CLICKS", "max_cpc": 0.8}, "target_spend", {}),  # same: no ceiling
])
def test_bidding(ads_client, demand_gen_plan, over, strategy, fields):
    ops, labels = compiled(ads_client, demand_gen_plan(**over))
    c = op_at(ops, labels, "campaign").campaign_operation.create
    assert _bidding(c) == strategy
    for path, value in fields.items():
        part, name = path.split(".")
        assert getattr(getattr(c, part), name) == value


def test_unknown_bidding_is_refused(ads_client, demand_gen_plan):
    plan = plans.plan_from_dict(demand_gen_plan())
    plan.bidding = "MANUAL_CPV"
    with pytest.raises(ValueError, match="unknown bidding"):
        compile_demand_gen_campaign(plan, ads_client, CID, GOALS)


def test_every_account_goal_is_overridden_on_the_new_campaign(ads_client, demand_gen_plan):
    ops, labels = compiled(ads_client, demand_gen_plan())
    goals = [op.campaign_conversion_goal_operation for op in ops if which(op) == "campaign_conversion_goal_operation"]
    assert [(g.update.resource_name, g.update.biddable) for g in goals] == [
        (f"customers/{CID}/campaignConversionGoals/-7~PURCHASE~WEBSITE", True),
        (f"customers/{CID}/campaignConversionGoals/-7~SUBMIT_LEAD_FORM~WEBSITE", False),
        (f"customers/{CID}/campaignConversionGoals/-7~DOWNLOAD~APP", False),
    ]
    assert all(list(g.update_mask.paths) == ["biddable"] for g in goals)  # False is sent through the mask
    assert all({f.name for f, _ in raw(g.update).ListFields()} <= {"resource_name", "biddable"} for g in goals)


def test_no_account_goals_means_no_goal_updates(ads_client, demand_gen_plan):
    ops, _ = compiled(ads_client, demand_gen_plan(), goals=())
    assert "campaign_conversion_goal_operation" not in [which(op) for op in ops]


def test_the_ad_group_has_its_channels_and_no_expansion(ads_client, demand_gen_plan):
    data = demand_gen_plan()
    data["ad_groups"][0]["channels"] = ["youtube_shorts", "gmail"]
    ops, labels = compiled(ads_client, data)
    group = op_at(ops, labels, "ad group[0] Springfield members").ad_group_operation.create
    gpb = raw(group)
    controls = gpb.demand_gen_ad_group_settings.channel_controls
    assert controls.WhichOneof("channel_configuration") == "selected_channels"
    chosen = controls.selected_channels
    assert {f.name for f in chosen.DESCRIPTOR.fields if getattr(chosen, f.name)} == {"youtube_shorts", "gmail"}
    assert chosen.display is False and chosen.maps is False
    assert group.exclude_demographic_expansion is True and group.optimized_targeting_enabled is False
    assert group.type_ == ads_client.enums.AdGroupTypeEnum.UNSPECIFIED
    assert group.status == ads_client.enums.AdGroupStatusEnum.ENABLED
    assert group.campaign == op_at(ops, labels, "campaign").campaign_operation.create.resource_name


def test_targeting_and_audiences_are_on_the_ad_group(ads_client, demand_gen_plan):
    data = demand_gen_plan()
    data["ad_groups"][0]["audiences"] = ["customers/1234567890/userLists/5"]
    ops, labels = compiled(ads_client, data)
    group = op_at(ops, labels, "ad group[0] Springfield members").ad_group_operation.create.resource_name
    location = op_at(ops, labels, "ad group[0] location geoTargetConstants/1000001").ad_group_criterion_operation.create
    language = op_at(ops, labels, "ad group[0] language languageConstants/1000").ad_group_criterion_operation.create
    audience = op_at(ops, labels, "ad group[0] audience customers/1234567890/userLists/5").ad_group_criterion_operation.create
    assert location.location.geo_target_constant == "geoTargetConstants/1000001" and location.ad_group == group
    assert language.language.language_constant == "languageConstants/1000" and language.ad_group == group
    assert audience.user_list.user_list == "customers/1234567890/userLists/5" and audience.ad_group == group
    assert not any(raw(c).HasField("negative") for c in (location, language, audience))


def test_the_image_ad(ads_client, demand_gen_plan):
    data = demand_gen_plan()
    data["ad_groups"][0]["ads"][0]["marketing_images"].append({"asset": "customers/1234567890/assets/7"})
    ops, labels = compiled(ads_client, data)
    created = op_at(ops, labels, "ad group[0] ad[0] image").ad_group_ad_operation.create
    info = created.ad.demand_gen_multi_asset_ad
    assert [h.text for h in info.headlines][0] == "Classes Every Day" and len(info.descriptions) == 5
    assert info.business_name == "Pulse Fitness" and raw(info).HasField("business_name")
    assert not raw(info).HasField("call_to_action_text")
    new = {label: op_at(ops, labels, label).asset_operation.create.resource_name
           for label in labels if label.startswith("image[")}
    assert [a.asset for a in info.marketing_images] == [new["image[0] dg-landscape.png"], "customers/1234567890/assets/7"]
    assert [a.asset for a in info.square_marketing_images] == [new["image[1] dg-square.png"]]
    assert [a.asset for a in info.portrait_marketing_images] == [new["image[2] dg-portrait.png"]]
    assert [a.asset for a in info.logo_images] == [new["image[3] dg-logo.png"]]
    assert list(created.ad.final_urls) == ["https://www.example.com/join"]
    assert created.status == ads_client.enums.AdGroupAdStatusEnum.ENABLED


def test_the_video_ad(ads_client, demand_gen_plan):
    data = demand_gen_plan()
    data["ad_groups"][0]["ads"][1].update(call_to_action="SIGN_UP",
                                          videos=[{"youtube_id": "abcdefghijk"}, {"asset": "customers/1234567890/assets/8"}])
    ops, labels = compiled(ads_client, data)
    created = op_at(ops, labels, "ad group[0] ad[1] video").ad_group_ad_operation.create
    info = created.ad.demand_gen_video_responsive_ad
    video = op_at(ops, labels, "video[0] abcdefghijk").asset_operation.create
    cta = op_at(ops, labels, "call to action[0] SIGN_UP").asset_operation.create
    assert video.youtube_video_asset.youtube_video_id == "abcdefghijk"
    assert video.name == "Pulse Springfield - Demand Gen | YouTube abcdefghijk"
    assert cta.call_to_action_asset.call_to_action == ads_client.enums.CallToActionTypeEnum.SIGN_UP
    assert [v.asset for v in info.videos] == [video.resource_name, "customers/1234567890/assets/8"]
    assert [a.asset for a in info.call_to_actions] == [cta.resource_name]
    assert info.business_name.text == "Pulse Fitness"
    assert len(info.headlines) == len(info.long_headlines) == len(info.descriptions) == 5
    logo = op_at(ops, labels, "image[3] dg-logo.png").asset_operation.create.resource_name
    assert [a.asset for a in info.logo_images] == [logo]  # the same file as the image ad's logo: one asset
    assert list(created.ad.final_urls) == ["https://www.example.com/classes"]


@pytest.mark.parametrize("index, kinds", [
    (0, ["GENERATE_DESIGN_VERSIONS_FOR_IMAGES", "GENERATE_VIDEOS_FROM_OTHER_ASSETS",
         "GENERATE_ANIMATED_IMAGES_FROM_OTHER_ASSETS"]),
    (1, ["GENERATE_VERTICAL_YOUTUBE_VIDEOS", "GENERATE_SHORTER_YOUTUBE_VIDEOS", "GENERATE_LANDING_PAGE_TEXT",
         "GENERATE_LANDING_PAGE_PREVIEW"]),
])
def test_every_generated_creative_is_opted_out_explicitly(ads_client, demand_gen_plan, index, kinds):
    ops, labels = compiled(ads_client, demand_gen_plan())
    created = op_at(ops, labels, f"ad group[0] ad[{index}] {'image' if index == 0 else 'video'}").ad_group_ad_operation.create
    settings = raw(created).ad_group_ad_asset_automation_settings
    assert all(s.HasField("asset_automation_type") and s.HasField("asset_automation_status") for s in settings)
    assert [ads_client.enums.AssetAutomationTypeEnum(s.asset_automation_type).name for s in settings] == kinds
    assert {s.asset_automation_status for s in settings} == {ads_client.enums.AssetAutomationStatusEnum.OPTED_OUT}


def test_image_assets_carry_the_file_bytes_and_a_name_with_their_digest(ads_client, demand_gen_plan):
    data = demand_gen_plan()
    ops, labels = compiled(ads_client, data)
    asset = op_at(ops, labels, "image[3] dg-logo.png").asset_operation.create
    file = Path(data["ad_groups"][0]["ads"][0]["logo_images"][0]["file"])
    digest = hashlib.sha256(file.read_bytes()).hexdigest()
    assert asset.image_asset.data == file.read_bytes()
    assert asset.name == f"Pulse Springfield - Demand Gen | dg-logo.png | {digest[:8]}"


def test_two_ad_groups_share_new_assets_and_keep_their_own_targeting(ads_client, demand_gen_plan):
    data = demand_gen_plan()
    second = dict(data["ad_groups"][0], name="Yoga fans", locations=["geoTargetConstants/1000002"], languages=[],
                  channels=["discover"])
    data["ad_groups"].append(second)
    ops, labels = compiled(ads_client, data)
    assert sum(which(op) == "asset_operation" for op in ops) == 5  # the same files and video: no new assets
    assert labels[-4:] == ["ad group[1] Yoga fans", "ad group[1] location geoTargetConstants/1000002",
                           "ad group[1] ad[0] image", "ad group[1] ad[1] video"]
    group = op_at(ops, labels, "ad group[1] Yoga fans").ad_group_operation.create
    assert group.resource_name == f"customers/{CID}/adGroups/-9"
    ad = op_at(ops, labels, "ad group[1] ad[0] image").ad_group_ad_operation.create
    assert ad.ad_group == group.resource_name


def test_every_ad_has_its_name(ads_client, demand_gen_plan):
    """Google requires a Demand Gen ad's name (refused live without it): campaign, ad group, kind and number."""
    data = demand_gen_plan()
    data["ad_groups"][0]["ads"].append(data["ad_groups"][0]["ads"][0])
    ops, labels = compiled(ads_client, data)
    ads = [op.ad_group_ad_operation.create.ad for op in ops if which(op) == "ad_group_ad_operation"]
    assert [ad.name for ad in ads] == [
        "Pulse Springfield - Demand Gen - Springfield members - image 1",
        "Pulse Springfield - Demand Gen - Springfield members - video 1",
        "Pulse Springfield - Demand Gen - Springfield members - image 2",
    ]
    assert all(raw(ad).HasField("name") for ad in ads)
