"""PmaxCampaignPlan -> one all-or-nothing list of MutateOperations with temporary IDs.

Safety rule 1 lives here too: the campaign status is hard-coded to PAUSED. Every other fixed setting is
sent explicitly rather than left to Google's defaults: brand guidelines on (the business name and logos
linked to the campaign in the same request), every asset automation the API lets a new campaign opt out
of (final URL expansion, generated text, enhanced videos, image enhancement, automated video crawl), the
location option (people in or regularly in the locations), the EU political ads declaration and a budget
of its own. The account's conversion goals are all overridden on the new campaign: the plan's goals on,
every other goal off. There are no listing groups (no Merchant Center feed).

Order, as Google asks for Performance Max: every new asset first, then the budget and the campaign, the
campaign's brand assets, criteria and goal updates, then every asset group, then every asset group's asset
links (all asset creates come before all asset group links), then the signals."""

from __future__ import annotations

import hashlib
import itertools
from pathlib import Path

from google.protobuf import field_mask_pb2

from . import plans
from .compile_app import to_micros
from .compile_demand_gen import campaign_temp_id
from .plans_pmax import (
    AUTOMATIONS, IMAGE_LISTS, PmaxCampaignPlan, _file_key, calls_to_action, new_image_files, new_youtube_ids,
    text_assets,
)

# an asset group's text lists -> AssetFieldTypeEnum name
TEXT_FIELDS = (("headlines", "HEADLINE"), ("long_headlines", "LONG_HEADLINE"), ("descriptions", "DESCRIPTION"))
# an asset group's image lists -> AssetFieldTypeEnum name
IMAGE_FIELDS = {"marketing_images": "MARKETING_IMAGE", "square_marketing_images": "SQUARE_MARKETING_IMAGE",
                "portrait_marketing_images": "PORTRAIT_MARKETING_IMAGE"}


def _set_bidding(c, plan: PmaxCampaignPlan) -> None:
    """Exactly one bidding strategy (a oneof in the Campaign message). Performance Max takes only these two:
    a target CPA is maximize_conversions.target_cpa_micros, a target ROAS maximize_conversion_value.target_roas."""
    pb = type(c).pb(c)
    if plan.bidding == "MAXIMIZE_CONVERSIONS":
        pb.maximize_conversions.SetInParent()
        if plan.target_cpa:
            c.maximize_conversions.target_cpa_micros = to_micros(plan.target_cpa)
    elif plan.bidding == "MAXIMIZE_CONVERSION_VALUE":
        pb.maximize_conversion_value.SetInParent()
        if plan.target_roas:
            c.maximize_conversion_value.target_roas = plan.target_roas
    else:
        raise ValueError(f"unknown bidding {plan.bidding!r}")


def compile_pmax_campaign(plan: PmaxCampaignPlan, client, customer_id: str, account_goals) -> tuple[list, list[str]]:
    """account_goals: the account's (category, origin) conversion goals, read live by check_plan."""
    E = client.enums
    ids = itertools.count(-1, -1)
    ops, labels = [], []

    def add(op, label):
        ops.append(op)
        labels.append(label)

    def temp(kind: str) -> str:
        return f"customers/{customer_id}/{kind}/{next(ids)}"

    # 1. New assets, each once: texts, image files, YouTube videos, calls to action.
    text_names: dict[str, str] = {}  # text -> new asset
    for n, text in enumerate(text_assets(plan)):
        op = client.get_type("MutateOperation")
        asset = op.asset_operation.create
        asset.resource_name = temp("assets")
        asset.text_asset.text = text
        text_names[text] = asset.resource_name
        add(op, f"text[{n}] {text}")
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

    def image(ref) -> str:
        return ref.asset or image_names[_file_key(ref.file)]

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
    c.advertising_channel_type = E.AdvertisingChannelTypeEnum.PERFORMANCE_MAX
    c.brand_guidelines_enabled = True  # the business name and logos are linked to the campaign below
    for kind in AUTOMATIONS:
        setting = type(c).AssetAutomationSetting()
        setting.asset_automation_type = getattr(E.AssetAutomationTypeEnum, kind)
        setting.asset_automation_status = E.AssetAutomationStatusEnum.OPTED_OUT
        c.asset_automation_settings.append(setting)
    c.contains_eu_political_advertising = E.EuPoliticalAdvertisingStatusEnum.DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
    c.geo_target_type_setting.positive_geo_target_type = E.PositiveGeoTargetTypeEnum.PRESENCE  # not "interest in"
    _set_bidding(c, plan)
    add(op, "campaign")

    # 4. The brand: business name and logos on the campaign (brand guidelines), in this same request.
    brand = [("BUSINESS_NAME", text_names[plan.business_name], "business name")]
    brand += [("LOGO", image(ref), f"logo[{i}]") for i, ref in enumerate(plan.logos)]
    brand += [("LANDSCAPE_LOGO", image(ref), f"landscape logo[{i}]") for i, ref in enumerate(plan.landscape_logos)]
    for field_type, asset_name, label in brand:
        op = client.get_type("MutateOperation")
        link = op.campaign_asset_operation.create
        link.campaign = campaign_name
        link.asset = asset_name
        link.field_type = getattr(E.AssetFieldTypeEnum, field_type)
        add(op, label)

    # 5. Locations and languages, on the campaign.
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

    # 6. Conversion goals: every account goal is overridden on the new campaign, the plan's on, the rest off.
    wanted = {(g.category, g.origin) for g in plan.conversion_goals}
    for category, origin in account_goals:
        op = client.get_type("MutateOperation")
        goal = op.campaign_conversion_goal_operation.update
        goal.resource_name = (f"customers/{customer_id}/campaignConversionGoals/"
                              f"{campaign_temp_id(campaign_name)}~{category}~{origin}")
        goal.biddable = (category, origin) in wanted
        client.copy_from(op.campaign_conversion_goal_operation.update_mask, field_mask_pb2.FieldMask(paths=["biddable"]))
        add(op, f"conversion goal {category}~{origin}")

    # 7. Every asset group: ENABLED inside the paused campaign, with its one final URL.
    group_names = []
    for g, group in enumerate(plan.asset_groups):
        op = client.get_type("MutateOperation")
        asset_group = op.asset_group_operation.create
        asset_group.resource_name = temp("assetGroups")
        asset_group.name = group.name
        asset_group.campaign = campaign_name
        asset_group.final_urls.append(group.final_url)
        asset_group.status = E.AssetGroupStatusEnum.ENABLED
        if group.path1:
            asset_group.path1 = group.path1
        if group.path2:
            asset_group.path2 = group.path2
        group_names.append(asset_group.resource_name)
        add(op, f"asset group[{g}] {group.name}")

    # 8. Every asset group's asset links, after every asset create.
    for g, group in enumerate(plan.asset_groups):
        links = [(field_type, text_names[text], f"{name[:-1].replace('_', ' ')}[{i}]")
                 for name, field_type in TEXT_FIELDS for i, text in enumerate(getattr(group, name))]
        links += [(IMAGE_FIELDS[name], image(ref), f"{name[:-1].replace('_', ' ')}[{i}]")
                  for name, _, _ in IMAGE_LISTS for i, ref in enumerate(getattr(group, name))]
        links += [("YOUTUBE_VIDEO", v.asset or video_names[v.youtube_id], f"video[{i}]")
                  for i, v in enumerate(group.videos)]
        if group.call_to_action:
            links.append(("CALL_TO_ACTION_SELECTION", cta_names[group.call_to_action], "call to action"))
        for field_type, asset_name, label in links:
            op = client.get_type("MutateOperation")
            link = op.asset_group_asset_operation.create
            link.asset_group = group_names[g]
            link.asset = asset_name
            link.field_type = getattr(E.AssetFieldTypeEnum, field_type)
            add(op, f"asset group[{g}] {label}")

    # 9. Signals: the audience and the search themes.
    for g, group in enumerate(plan.asset_groups):
        if group.audience_signal:
            op = client.get_type("MutateOperation")
            signal = op.asset_group_signal_operation.create
            signal.asset_group = group_names[g]
            signal.audience.audience = group.audience_signal
            add(op, f"asset group[{g}] audience {group.audience_signal}")
        for i, theme in enumerate(group.search_themes):
            op = client.get_type("MutateOperation")
            signal = op.asset_group_signal_operation.create
            signal.asset_group = group_names[g]
            signal.search_theme.text = theme
            add(op, f"asset group[{g}] search theme[{i}] {theme}")
    return ops, labels
