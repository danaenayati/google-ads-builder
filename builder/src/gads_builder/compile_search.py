"""SearchCampaignPlan -> one all-or-nothing list of MutateOperations with temporary IDs.

Safety rule 1 lives here too: the campaign status is hard-coded to PAUSED, and the Display Network
is hard-coded off. So are the location option (only people in, or regularly in, the locations) and AI
Max, sent explicitly rather than left to Google's defaults."""

from __future__ import annotations

import itertools

from . import plans
from .compile_app import text_assets, to_micros
from .plans_search import Keyword, SearchCampaignPlan


def _keyword_criterion(criterion, kw: Keyword, client, *, negative: bool) -> None:
    """Fills a CampaignCriterion or AdGroupCriterion with one keyword. A positive one is ENABLED."""
    if negative:
        criterion.negative = True
    else:
        criterion.status = client.enums.AdGroupCriterionStatusEnum.ENABLED
    criterion.keyword.text = kw.text
    criterion.keyword.match_type = getattr(client.enums.KeywordMatchTypeEnum, kw.match)


def _set_bidding(c, plan: SearchCampaignPlan, client) -> None:
    """Exactly one bidding strategy (a oneof in the Campaign message)."""
    pb = type(c).pb(c)
    if plan.bidding == "MAXIMIZE_CONVERSIONS":
        pb.maximize_conversions.SetInParent()
        if plan.target_cpa:
            c.maximize_conversions.target_cpa_micros = to_micros(plan.target_cpa)
    elif plan.bidding == "TARGET_CPA":
        c.target_cpa.target_cpa_micros = to_micros(plan.target_cpa)
    elif plan.bidding == "MAXIMIZE_CLICKS":
        pb.target_spend.SetInParent()
        if plan.max_cpc:
            c.target_spend.cpc_bid_ceiling_micros = to_micros(plan.max_cpc)
    elif plan.bidding == "TARGET_IMPRESSION_SHARE":
        share = c.target_impression_share
        share.location = getattr(client.enums.TargetImpressionShareLocationEnum, plan.impression_share.location)
        share.location_fraction_micros = int(round(plan.impression_share.percent * 10_000))  # 1% = 10,000
        share.cpc_bid_ceiling_micros = to_micros(plan.max_cpc)
    elif plan.bidding == "MANUAL_CPC":
        pb.manual_cpc.SetInParent()
    else:
        raise ValueError(f"unknown bidding {plan.bidding!r}")


def compile_search_campaign(plan: SearchCampaignPlan, client, customer_id: str) -> tuple[list, list[str]]:
    E = client.enums
    ids = itertools.count(-1, -1)
    ops, labels = [], []
    links = []  # (new asset, AssetFieldTypeEnum name, label) to attach to the campaign at the end

    def add(op, label):
        ops.append(op)
        labels.append(label)

    def temp(kind: str) -> str:
        return f"customers/{customer_id}/{kind}/{next(ids)}"

    # 1. Sitelink, callout and structured snippet assets.
    for i, link in enumerate(plan.sitelinks):
        op = client.get_type("MutateOperation")
        asset = op.asset_operation.create
        asset.resource_name = temp("assets")
        asset.sitelink_asset.link_text = link.text
        if link.desc1:
            asset.sitelink_asset.description1 = link.desc1
            asset.sitelink_asset.description2 = link.desc2
        asset.final_urls.append(link.url)
        add(op, f"sitelink[{i}] {link.text}")
        links.append((asset.resource_name, "SITELINK", f"sitelink[{i}] link"))
    for i, text in enumerate(plan.callouts):
        op = client.get_type("MutateOperation")
        asset = op.asset_operation.create
        asset.resource_name = temp("assets")
        asset.callout_asset.callout_text = text
        add(op, f"callout[{i}] {text}")
        links.append((asset.resource_name, "CALLOUT", f"callout[{i}] link"))
    for i, snippet in enumerate(plan.snippets):
        op = client.get_type("MutateOperation")
        asset = op.asset_operation.create
        asset.resource_name = temp("assets")
        asset.structured_snippet_asset.header = snippet.header
        asset.structured_snippet_asset.values.extend(snippet.values)
        add(op, f"snippet[{i}] {snippet.header}")
        links.append((asset.resource_name, "STRUCTURED_SNIPPET", f"snippet[{i}] link"))

    # 2. Budget.
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
    c.advertising_channel_type = E.AdvertisingChannelTypeEnum.SEARCH
    c.network_settings.target_google_search = True
    c.network_settings.target_search_network = plan.search_partners is True
    c.network_settings.target_content_network = False  # the Display Network: always off
    c.network_settings.target_partner_search_network = False
    c.contains_eu_political_advertising = E.EuPoliticalAdvertisingStatusEnum.DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
    c.geo_target_type_setting.positive_geo_target_type = E.PositiveGeoTargetTypeEnum.PRESENCE  # not "interest in"
    c.ai_max_setting.enable_ai_max = False  # AI Max widens matching and rewrites ads: always off here
    _set_bidding(c, plan, client)
    add(op, "campaign")

    # 4. Locations and languages. 5. Campaign negative keywords.
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
    for i, kw in enumerate(plan.negative_keywords):
        op = client.get_type("MutateOperation")
        criterion = op.campaign_criterion_operation.create
        criterion.campaign = campaign_name
        _keyword_criterion(criterion, kw, client, negative=True)
        add(op, f"campaign negative keyword[{i}] {kw.text}")

    # 6. Ad groups, each with its keywords, negative keywords and responsive search ad.
    for g, group in enumerate(plan.ad_groups):
        group_name = temp("adGroups")
        op = client.get_type("MutateOperation")
        ad_group = op.ad_group_operation.create
        ad_group.resource_name = group_name
        ad_group.name = group.name
        ad_group.campaign = campaign_name
        ad_group.status = E.AdGroupStatusEnum.ENABLED  # inside a paused campaign
        ad_group.type_ = E.AdGroupTypeEnum.SEARCH_STANDARD
        if plan.bidding == "MANUAL_CPC":
            ad_group.cpc_bid_micros = to_micros(group.cpc_bid if group.cpc_bid is not None else plan.max_cpc)
        add(op, f"ad group[{g}] {group.name}")
        for k, kw in enumerate(group.keywords):
            op = client.get_type("MutateOperation")
            criterion = op.ad_group_criterion_operation.create
            criterion.ad_group = group_name
            _keyword_criterion(criterion, kw, client, negative=False)
            add(op, f"ad group[{g}] keyword[{k}] {kw.text}")
        for k, kw in enumerate(group.negative_keywords):
            op = client.get_type("MutateOperation")
            criterion = op.ad_group_criterion_operation.create
            criterion.ad_group = group_name
            _keyword_criterion(criterion, kw, client, negative=True)
            add(op, f"ad group[{g}] negative keyword[{k}] {kw.text}")
        op = client.get_type("MutateOperation")
        ad_group_ad = op.ad_group_ad_operation.create
        ad_group_ad.ad_group = group_name
        ad_group_ad.status = E.AdGroupAdStatusEnum.ENABLED
        rsa = ad_group_ad.ad.responsive_search_ad
        rsa.headlines.extend(text_assets(client, group.ad.headlines))
        rsa.descriptions.extend(text_assets(client, group.ad.descriptions))
        if group.ad.path1:
            rsa.path1 = group.ad.path1
        if group.ad.path2:
            rsa.path2 = group.ad.path2
        ad_group_ad.ad.final_urls.append(group.ad.final_url)
        add(op, f"ad group[{g}] ad")

    # 7. Attach the new assets to the new campaign.
    for asset_name, field_type, label in links:
        op = client.get_type("MutateOperation")
        link = op.campaign_asset_operation.create
        link.campaign = campaign_name
        link.asset = asset_name
        link.field_type = getattr(E.AssetFieldTypeEnum, field_type)
        add(op, label)
    return ops, labels
