import hashlib
from pathlib import Path

import pytest

from gads_builder import plans, plans_pmax
from gads_builder.compile_pmax import compile_pmax_campaign

CID = "1234567890"
GOALS = (("PURCHASE", "WEBSITE"), ("SUBMIT_LEAD_FORM", "WEBSITE"), ("DOWNLOAD", "APP"))


def compiled(ads_client, data, goals=GOALS):
    return compile_pmax_campaign(plans.plan_from_dict(data), ads_client, CID, goals)


def op_at(ops, labels, label):
    return ops[labels.index(label)]


def which(op) -> str:
    return type(op).pb(op).WhichOneof("operation")


def raw(message):
    return type(message).pb(message)


def creates(ops, field):
    return [getattr(op, field).create for op in ops if which(op) == field]


def test_order_and_operation_kinds(ads_client, pmax_plan):
    ops, labels = compiled(ads_client, pmax_plan())
    kinds = [which(op) for op in ops]
    assert kinds == (["asset_operation"] * 31 + ["campaign_budget_operation", "campaign_operation"]
                     + ["campaign_asset_operation"] * 2 + ["campaign_criterion_operation"] * 2
                     + ["campaign_conversion_goal_operation"] * 3 + ["asset_group_operation"]
                     + ["asset_group_asset_operation"] * 29 + ["asset_group_signal_operation"] * 2)
    assert labels[:2] == ["text[0] Pulse Fitness", "text[1] Classes Every Day"]
    assert labels[26:31] == ["image[0] pm-logo.png", "image[1] pm-landscape.png", "image[2] pm-square.png",
                             "image[3] pm-portrait.png", "video[0] abcdefghijk"]
    assert labels[31:41] == ["budget", "campaign", "business name", "logo[0]", "location geoTargetConstants/1000001",
                             "language languageConstants/1000", "conversion goal PURCHASE~WEBSITE",
                             "conversion goal SUBMIT_LEAD_FORM~WEBSITE", "conversion goal DOWNLOAD~APP",
                             "asset group[0] Springfield members"]
    assert labels[41] == "asset group[0] headline[0]" and labels[-3] == "asset group[0] video[0]"
    assert labels[-2:] == ["asset group[0] search theme[0] gym membership springfield",
                           "asset group[0] search theme[1] yoga classes near me"]
    actions = [getattr(raw(op), which(op)).WhichOneof("operation") for op in ops]
    assert set(actions[:37]) == {"create"} and actions[37:40] == ["update"] * 3 and set(actions[40:]) == {"create"}


def test_every_asset_create_comes_before_every_asset_group_link(ads_client, pmax_plan):
    data = pmax_plan()
    data["asset_groups"].append(dict(data["asset_groups"][0], name="Yoga fans", call_to_action="SIGN_UP",
                                     headlines=["Yoga Every Day", *data["asset_groups"][0]["headlines"][1:]]))
    ops, _ = compiled(ads_client, data)
    kinds = [which(op) for op in ops]
    last_asset = max(i for i, k in enumerate(kinds) if k == "asset_operation")
    first_link = min(i for i, k in enumerate(kinds) if k == "asset_group_asset_operation")
    last_group = max(i for i, k in enumerate(kinds) if k == "asset_group_operation")
    assert last_asset < kinds.index("campaign_budget_operation") < last_group < first_link
    assert kinds.count("asset_group_operation") == 2


def test_temporary_ids_are_new_and_negative(ads_client, pmax_plan):
    ops, labels = compiled(ads_client, pmax_plan())
    assert op_at(ops, labels, "text[0] Pulse Fitness").asset_operation.create.resource_name == f"customers/{CID}/assets/-1"
    assert op_at(ops, labels, "budget").campaign_budget_operation.create.resource_name == \
        f"customers/{CID}/campaignBudgets/-32"
    assert op_at(ops, labels, "campaign").campaign_operation.create.resource_name == f"customers/{CID}/campaigns/-33"
    assert op_at(ops, labels, "asset group[0] Springfield members").asset_group_operation.create.resource_name == \
        f"customers/{CID}/assetGroups/-34"


def test_the_campaign_is_a_paused_pmax_campaign_with_every_setting_sent(ads_client, pmax_plan):
    E = ads_client.enums
    ops, labels = compiled(ads_client, pmax_plan())
    c = op_at(ops, labels, "campaign").campaign_operation.create
    budget = op_at(ops, labels, "budget").campaign_budget_operation.create
    cpb = raw(c)
    assert c.status == E.CampaignStatusEnum.PAUSED and c.name == "Pulse Springfield - Performance Max"
    assert c.advertising_channel_type == E.AdvertisingChannelTypeEnum.PERFORMANCE_MAX
    assert c.advertising_channel_sub_type == E.AdvertisingChannelSubTypeEnum.UNSPECIFIED
    assert c.campaign_budget == budget.resource_name
    assert cpb.HasField("brand_guidelines_enabled") and cpb.brand_guidelines_enabled is True
    assert c.contains_eu_political_advertising == E.EuPoliticalAdvertisingStatusEnum.DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
    assert c.geo_target_type_setting.positive_geo_target_type == E.PositiveGeoTargetTypeEnum.PRESENCE
    for name in ("network_settings", "shopping_setting", "text_guidelines", "brand_guidelines", "pmax_campaign_settings",
                 "selective_optimization", "listing_type", "tracking_url_template", "final_url_suffix"):
        assert not cpb.HasField(name), name
    assert c.bidding_strategy == "" and list(c.url_custom_parameters) == []
    assert budget.amount_micros == 50_000_000 and budget.name == "Pulse Springfield - Performance Max budget"
    assert raw(budget).HasField("explicitly_shared") and budget.explicitly_shared is False
    assert budget.delivery_method == E.BudgetDeliveryMethodEnum.STANDARD


def test_every_asset_automation_is_opted_out_explicitly(ads_client, pmax_plan):
    E = ads_client.enums
    ops, labels = compiled(ads_client, pmax_plan())
    settings = raw(op_at(ops, labels, "campaign").campaign_operation.create).asset_automation_settings
    assert all(s.HasField("asset_automation_type") and s.HasField("asset_automation_status") for s in settings)
    assert [E.AssetAutomationTypeEnum(s.asset_automation_type).name for s in settings] == [
        "FINAL_URL_EXPANSION_TEXT_ASSET_AUTOMATION", "TEXT_ASSET_AUTOMATION", "GENERATE_ENHANCED_YOUTUBE_VIDEOS",
        "GENERATE_IMAGE_ENHANCEMENT", "AUTOMATED_VIDEO_CRAWL"]
    assert {s.asset_automation_status for s in settings} == {E.AssetAutomationStatusEnum.OPTED_OUT}
    assert not any(s.HasField("automated_video_crawl_setting") for s in settings)


def _bidding(campaign) -> str:
    return raw(campaign).WhichOneof("campaign_bidding_strategy")


@pytest.mark.parametrize("over, strategy, fields", [
    ({}, "maximize_conversions", {"maximize_conversions.target_cpa_micros": 0}),
    ({"target_cpa": 2.5}, "maximize_conversions", {"maximize_conversions.target_cpa_micros": 2_500_000}),
    ({"bidding": "MAXIMIZE_CONVERSION_VALUE"}, "maximize_conversion_value", {"maximize_conversion_value.target_roas": 0}),
    ({"bidding": "MAXIMIZE_CONVERSION_VALUE", "target_roas": 3.5}, "maximize_conversion_value",
     {"maximize_conversion_value.target_roas": 3.5}),
])
def test_bidding(ads_client, pmax_plan, over, strategy, fields):
    ops, labels = compiled(ads_client, pmax_plan(**over))
    c = op_at(ops, labels, "campaign").campaign_operation.create
    assert _bidding(c) == strategy
    for path, value in fields.items():
        part, name = path.split(".")
        assert getattr(getattr(c, part), name) == value
    assert {f.name for f, _ in getattr(raw(c), strategy).ListFields()} <= {"target_cpa_micros", "target_roas"}


def test_unknown_bidding_is_refused(ads_client, pmax_plan):
    plan = plans.plan_from_dict(pmax_plan())
    plan.bidding = "TARGET_ROAS"
    with pytest.raises(ValueError, match="unknown bidding"):
        compile_pmax_campaign(plan, ads_client, CID, GOALS)


def test_the_brand_is_linked_to_the_campaign(ads_client, pmax_plan, make_png):
    wide = str(make_png(1200, 300, "pm-wide-logo.png"))
    ops, labels = compiled(ads_client, pmax_plan(landscape_logos=[{"file": wide},
                                                                  {"asset": "customers/1234567890/assets/9"}]))
    E = ads_client.enums
    campaign = op_at(ops, labels, "campaign").campaign_operation.create.resource_name
    links = creates(ops, "campaign_asset_operation")
    assert [E.AssetFieldTypeEnum(link.field_type).name for link in links] == [
        "BUSINESS_NAME", "LOGO", "LANDSCAPE_LOGO", "LANDSCAPE_LOGO"]
    assert all(link.campaign == campaign for link in links)
    name = op_at(ops, labels, "text[0] Pulse Fitness").asset_operation.create
    logo = op_at(ops, labels, "image[0] pm-logo.png").asset_operation.create
    wide_asset = op_at(ops, labels, "image[1] pm-wide-logo.png").asset_operation.create
    assert [link.asset for link in links] == [name.resource_name, logo.resource_name, wide_asset.resource_name,
                                              "customers/1234567890/assets/9"]
    assert name.text_asset.text == "Pulse Fitness" and not raw(name).HasField("name")
    assert labels.index("business name") == labels.index("campaign") + 1  # right after the campaign


def test_targeting_is_on_the_campaign(ads_client, pmax_plan):
    ops, labels = compiled(ads_client, pmax_plan())
    campaign = op_at(ops, labels, "campaign").campaign_operation.create.resource_name
    location = op_at(ops, labels, "location geoTargetConstants/1000001").campaign_criterion_operation.create
    language = op_at(ops, labels, "language languageConstants/1000").campaign_criterion_operation.create
    assert location.location.geo_target_constant == "geoTargetConstants/1000001" and location.campaign == campaign
    assert language.language.language_constant == "languageConstants/1000" and language.campaign == campaign
    assert not any(raw(c).HasField("negative") for c in (location, language))


def test_every_account_goal_is_overridden_on_the_new_campaign(ads_client, pmax_plan):
    ops, _ = compiled(ads_client, pmax_plan())
    goals = [op.campaign_conversion_goal_operation for op in ops if which(op) == "campaign_conversion_goal_operation"]
    assert [(g.update.resource_name, g.update.biddable) for g in goals] == [
        (f"customers/{CID}/campaignConversionGoals/-33~PURCHASE~WEBSITE", True),
        (f"customers/{CID}/campaignConversionGoals/-33~SUBMIT_LEAD_FORM~WEBSITE", False),
        (f"customers/{CID}/campaignConversionGoals/-33~DOWNLOAD~APP", False),
    ]
    assert all(list(g.update_mask.paths) == ["biddable"] for g in goals)
    assert all({f.name for f, _ in raw(g.update).ListFields()} <= {"resource_name", "biddable"} for g in goals)


def test_the_asset_group(ads_client, pmax_plan):
    ops, labels = compiled(ads_client, pmax_plan())
    group = op_at(ops, labels, "asset group[0] Springfield members").asset_group_operation.create
    assert group.name == "Springfield members" and list(group.final_urls) == ["https://www.example.com/join"]
    assert group.campaign == op_at(ops, labels, "campaign").campaign_operation.create.resource_name
    assert group.status == ads_client.enums.AssetGroupStatusEnum.ENABLED
    assert (group.path1, group.path2) == ("gym", "join")
    assert {f.name for f, _ in raw(group).ListFields()} == {"resource_name", "name", "campaign", "final_urls", "status",
                                                            "path1", "path2"}


def test_no_display_path_sends_no_path(ads_client, pmax_plan):
    data = pmax_plan()
    data["asset_groups"][0].update(path1="", path2="")
    ops, labels = compiled(ads_client, data)
    group = op_at(ops, labels, "asset group[0] Springfield members").asset_group_operation.create
    assert "path1" not in {f.name for f, _ in raw(group).ListFields()}


def test_the_asset_groups_links(ads_client, pmax_plan):
    E = ads_client.enums
    data = pmax_plan()
    data["asset_groups"][0].update(call_to_action="SIGN_UP", videos=[{"youtube_id": "abcdefghijk"},
                                                                    {"asset": "customers/1234567890/assets/8"}])
    ops, labels = compiled(ads_client, data)
    group = op_at(ops, labels, "asset group[0] Springfield members").asset_group_operation.create.resource_name
    links = creates(ops, "asset_group_asset_operation")
    assert all(link.asset_group == group for link in links)
    counts = {}
    for link in links:
        name = E.AssetFieldTypeEnum(link.field_type).name
        counts[name] = counts.get(name, 0) + 1
    assert counts == {"HEADLINE": 15, "LONG_HEADLINE": 5, "DESCRIPTION": 5, "MARKETING_IMAGE": 1,
                      "SQUARE_MARKETING_IMAGE": 1, "PORTRAIT_MARKETING_IMAGE": 1, "YOUTUBE_VIDEO": 2,
                      "CALL_TO_ACTION_SELECTION": 1}
    text = {op_at(ops, labels, label).asset_operation.create.text_asset.text:
            op_at(ops, labels, label).asset_operation.create.resource_name for label in labels if label.startswith("text[")}
    headline = op_at(ops, labels, "asset group[0] headline[0]").asset_group_asset_operation.create
    assert headline.asset == text["Classes Every Day"]
    video = op_at(ops, labels, "video[0] abcdefghijk").asset_operation.create
    assert video.youtube_video_asset.youtube_video_id == "abcdefghijk"
    assert video.name == "Pulse Springfield - Performance Max | YouTube abcdefghijk"
    assert op_at(ops, labels, "asset group[0] video[0]").asset_group_asset_operation.create.asset == video.resource_name
    assert op_at(ops, labels, "asset group[0] video[1]").asset_group_asset_operation.create.asset == \
        "customers/1234567890/assets/8"
    cta = op_at(ops, labels, "call to action[0] SIGN_UP").asset_operation.create
    assert cta.call_to_action_asset.call_to_action == E.CallToActionTypeEnum.SIGN_UP
    assert op_at(ops, labels, "asset group[0] call to action").asset_group_asset_operation.create.asset == \
        cta.resource_name
    assert all({f.name for f, _ in raw(link).ListFields()} == {"asset_group", "asset", "field_type"} for link in links)


def test_the_signals(ads_client, pmax_plan):
    data = pmax_plan()
    data["asset_groups"][0]["audience_signal"] = "customers/1234567890/audiences/5"
    ops, labels = compiled(ads_client, data)
    group = op_at(ops, labels, "asset group[0] Springfield members").asset_group_operation.create.resource_name
    signals = creates(ops, "asset_group_signal_operation")
    assert [raw(s).WhichOneof("signal") for s in signals] == ["audience", "search_theme", "search_theme"]
    assert signals[0].audience.audience == "customers/1234567890/audiences/5"
    assert [s.search_theme.text for s in signals[1:]] == ["gym membership springfield", "yoga classes near me"]
    assert all(s.asset_group == group for s in signals)


def test_image_assets_carry_the_file_bytes_and_a_name_with_their_digest(ads_client, pmax_plan):
    data = pmax_plan()
    ops, labels = compiled(ads_client, data)
    asset = op_at(ops, labels, "image[0] pm-logo.png").asset_operation.create
    file = Path(data["logos"][0]["file"])
    digest = hashlib.sha256(file.read_bytes()).hexdigest()
    assert asset.image_asset.data == file.read_bytes()
    assert asset.name == f"Pulse Springfield - Performance Max | pm-logo.png | {digest[:8]}"


def test_two_asset_groups_share_new_assets(ads_client, pmax_plan):
    data = pmax_plan()
    second = dict(data["asset_groups"][0], name="Yoga fans", final_url="https://www.example.com/yoga",
                  headlines=["Yoga Every Day", *data["asset_groups"][0]["headlines"][1:]], search_themes=[])
    data["asset_groups"].append(second)
    ops, labels = compiled(ads_client, data)
    assert sum(which(op) == "asset_operation" for op in ops) == 32  # one new headline; the rest are shared
    groups = creates(ops, "asset_group_operation")
    assert [g.resource_name for g in groups] == [f"customers/{CID}/assetGroups/-35", f"customers/{CID}/assetGroups/-36"]
    assert list(groups[1].final_urls) == ["https://www.example.com/yoga"]
    links = creates(ops, "asset_group_asset_operation")
    assert sum(link.asset_group == groups[1].resource_name for link in links) == 29
    assert len(plans_pmax.text_assets(plans.plan_from_dict(data))) == 27


def test_no_account_goals_means_no_goal_updates(ads_client, pmax_plan):
    ops, _ = compiled(ads_client, pmax_plan(), goals=())
    assert "campaign_conversion_goal_operation" not in [which(op) for op in ops]
