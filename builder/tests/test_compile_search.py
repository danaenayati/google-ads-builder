import pytest

from gads_builder import plans
from gads_builder.compile_search import compile_search_campaign

CID = "1234567890"


def compiled(ads_client, data):
    return compile_search_campaign(plans.plan_from_dict(data), ads_client, CID)


def op_at(ops, labels, label):
    return ops[labels.index(label)]


def which(op) -> str:
    return type(op).pb(op).WhichOneof("operation")


def test_order_labels_and_operation_kinds(ads_client, search_plan):
    ops, labels = compiled(ads_client, search_plan())
    assert labels == [
        "sitelink[0] Class Schedule", "sitelink[1] Membership Prices", "sitelink[2] Free Trial Class",
        "sitelink[3] Find a Gym",
        "callout[0] No Joining Fee", "callout[1] Cancel Any Time", "callout[2] Expert Coaches",
        "callout[3] Classes Every Day",
        "snippet[0] Types",
        "budget", "campaign",
        "location geoTargetConstants/1000001", "language languageConstants/1000",
        "campaign negative keyword[0] gym jobs",
        "ad group[0] Gym membership",
        "ad group[0] keyword[0] gym membership", "ad group[0] keyword[1] gym membership springfield",
        "ad group[0] keyword[2] monthly gym pass", "ad group[0] keyword[3] join a gym",
        "ad group[0] keyword[4] cheap gym membership",
        "ad group[0] negative keyword[0] free gym",
        "ad group[0] ad",
        "sitelink[0] link", "sitelink[1] link", "sitelink[2] link", "sitelink[3] link",
        "callout[0] link", "callout[1] link", "callout[2] link", "callout[3] link",
        "snippet[0] link",
    ]
    kinds = [which(op) for op in ops]
    assert kinds == ["asset_operation"] * 9 + ["campaign_budget_operation", "campaign_operation"] + [
        "campaign_criterion_operation"] * 3 + ["ad_group_operation"] + ["ad_group_criterion_operation"] * 6 + [
        "ad_group_ad_operation"] + ["campaign_asset_operation"] * 9
    assert all(getattr(type(op).pb(op), which(op)).WhichOneof("operation") == "create" for op in ops)


def test_the_campaign_is_paused_on_search_only(ads_client, search_plan):
    E = ads_client.enums
    ops, labels = compiled(ads_client, search_plan())
    c = op_at(ops, labels, "campaign").campaign_operation.create
    budget = op_at(ops, labels, "budget").campaign_budget_operation.create
    assert c.status == E.CampaignStatusEnum.PAUSED
    assert c.advertising_channel_type == E.AdvertisingChannelTypeEnum.SEARCH
    assert c.name == "Springfield Gyms - Search"
    ns = c.network_settings
    assert (ns.target_google_search, ns.target_search_network, ns.target_content_network,
            ns.target_partner_search_network) == (True, False, False, False)
    assert c.contains_eu_political_advertising == E.EuPoliticalAdvertisingStatusEnum.DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
    assert budget.name == "Springfield Gyms - Search budget" and budget.amount_micros == 80_000_000
    assert budget.explicitly_shared is False and budget.delivery_method == E.BudgetDeliveryMethodEnum.STANDARD


def test_the_network_choices_and_the_unshared_budget_are_set_explicitly(ads_client, search_plan):
    """Proto-plus reads an unset optional bool as False, so "False" alone doesn't prove it is sent."""
    ops, labels = compiled(ads_client, search_plan())
    c = op_at(ops, labels, "campaign").campaign_operation.create
    budget = op_at(ops, labels, "budget").campaign_budget_operation.create
    cpb = type(c).pb(c)
    assert cpb.HasField("network_settings")
    for name in ("target_google_search", "target_search_network", "target_content_network",
                 "target_partner_search_network"):
        assert cpb.network_settings.HasField(name), name
    assert type(budget).pb(budget).HasField("explicitly_shared")


def test_search_partners_is_the_only_network_choice(ads_client, search_plan):
    ops, labels = compiled(ads_client, search_plan(search_partners=True))
    ns = op_at(ops, labels, "campaign").campaign_operation.create.network_settings
    assert ns.target_search_network is True and ns.target_content_network is False


def _bidding(campaign) -> str:
    return type(campaign).pb(campaign).WhichOneof("campaign_bidding_strategy")


@pytest.mark.parametrize("over, strategy, fields", [
    ({}, "maximize_conversions", {}),
    ({"target_cpa": 12.5}, "maximize_conversions", {"maximize_conversions.target_cpa_micros": 12_500_000}),
    ({"bidding": "TARGET_CPA", "target_cpa": 12.5}, "target_cpa", {"target_cpa.target_cpa_micros": 12_500_000}),
    ({"bidding": "MAXIMIZE_CLICKS"}, "target_spend", {"target_spend.cpc_bid_ceiling_micros": 0}),
    ({"bidding": "MAXIMIZE_CLICKS", "max_cpc": 1.5}, "target_spend", {"target_spend.cpc_bid_ceiling_micros": 1_500_000}),
    ({"bidding": "TARGET_IMPRESSION_SHARE", "max_cpc": 2.0,
      "impression_share": {"location": "TOP_OF_PAGE", "percent": 65}},
     "target_impression_share", {"target_impression_share.location_fraction_micros": 650_000,
                                 "target_impression_share.cpc_bid_ceiling_micros": 2_000_000}),
    ({"bidding": "MANUAL_CPC", "max_cpc": 1.2}, "manual_cpc", {}),
])
def test_each_bidding_choice(ads_client, search_plan, over, strategy, fields):
    ops, labels = compiled(ads_client, search_plan(**over))
    c = op_at(ops, labels, "campaign").campaign_operation.create
    assert _bidding(c) == strategy
    for path, value in fields.items():
        part, name = path.split(".")
        assert getattr(getattr(c, part), name) == value, path
    if strategy == "target_impression_share":
        assert c.target_impression_share.location == ads_client.enums.TargetImpressionShareLocationEnum.TOP_OF_PAGE


def test_manual_cpc_sets_each_ad_group_bid(ads_client, search_plan):
    data = search_plan(bidding="MANUAL_CPC", max_cpc=1.2)
    extra = dict(data["ad_groups"][0], name="Yoga classes", cpc_bid=1.75,
                 keywords=[{"text": "yoga classes", "match": "PHRASE"}], negative_keywords=[])
    data["ad_groups"].append(extra)
    ops, labels = compiled(ads_client, data)
    assert op_at(ops, labels, "ad group[0] Gym membership").ad_group_operation.create.cpc_bid_micros == 1_200_000
    assert op_at(ops, labels, "ad group[1] Yoga classes").ad_group_operation.create.cpc_bid_micros == 1_750_000


def test_smart_bidding_sets_no_ad_group_bid(ads_client, search_plan):
    ops, labels = compiled(ads_client, search_plan())
    assert op_at(ops, labels, "ad group[0] Gym membership").ad_group_operation.create.cpc_bid_micros == 0


def test_everything_points_at_the_new_campaign_and_ad_groups(ads_client, search_plan):
    E = ads_client.enums
    ops, labels = compiled(ads_client, search_plan())
    budget = op_at(ops, labels, "budget").campaign_budget_operation.create
    c = op_at(ops, labels, "campaign").campaign_operation.create
    group = op_at(ops, labels, "ad group[0] Gym membership").ad_group_operation.create
    assets = [op.asset_operation.create.resource_name for op in ops if which(op) == "asset_operation"]
    temp = [*assets, budget.resource_name, c.resource_name, group.resource_name]
    assert len(set(temp)) == len(temp) and all(int(r.rsplit("/", 1)[1]) < 0 for r in temp)
    assert all(r.startswith(f"customers/{CID}/") for r in temp)
    assert c.campaign_budget == budget.resource_name and group.campaign == c.resource_name
    assert group.type_ == E.AdGroupTypeEnum.SEARCH_STANDARD and group.status == E.AdGroupStatusEnum.ENABLED
    for op in ops:
        kind = which(op)
        if kind == "campaign_criterion_operation":
            assert op.campaign_criterion_operation.create.campaign == c.resource_name
        if kind in ("ad_group_criterion_operation", "ad_group_ad_operation"):
            assert getattr(op, kind).create.ad_group == group.resource_name
    links = [op.campaign_asset_operation.create for op in ops if which(op) == "campaign_asset_operation"]
    assert [link.asset for link in links] == assets and {link.campaign for link in links} == {c.resource_name}
    assert [link.field_type for link in links] == [E.AssetFieldTypeEnum.SITELINK] * 4 + [
        E.AssetFieldTypeEnum.CALLOUT] * 4 + [E.AssetFieldTypeEnum.STRUCTURED_SNIPPET]


def test_keywords_and_negatives(ads_client, search_plan):
    E = ads_client.enums
    ops, labels = compiled(ads_client, search_plan())
    kw = op_at(ops, labels, "ad group[0] keyword[1] gym membership springfield").ad_group_criterion_operation.create
    assert (kw.keyword.text, kw.keyword.match_type, kw.negative) == (
        "gym membership springfield", E.KeywordMatchTypeEnum.EXACT, False)
    assert kw.status == E.AdGroupCriterionStatusEnum.ENABLED
    neg = op_at(ops, labels, "ad group[0] negative keyword[0] free gym").ad_group_criterion_operation.create
    assert (neg.keyword.text, neg.keyword.match_type, neg.negative) == ("free gym", E.KeywordMatchTypeEnum.EXACT, True)
    camp = op_at(ops, labels, "campaign negative keyword[0] gym jobs").campaign_criterion_operation.create
    assert (camp.keyword.text, camp.keyword.match_type, camp.negative) == ("gym jobs", E.KeywordMatchTypeEnum.PHRASE, True)
    loc = op_at(ops, labels, "location geoTargetConstants/1000001").campaign_criterion_operation.create
    assert loc.location.geo_target_constant == "geoTargetConstants/1000001" and loc.negative is False


def test_the_responsive_search_ad(ads_client, search_plan):
    data = search_plan()
    ops, labels = compiled(ads_client, data)
    ad_group_ad = op_at(ops, labels, "ad group[0] ad").ad_group_ad_operation.create
    rsa = ad_group_ad.ad.responsive_search_ad
    assert [h.text for h in rsa.headlines] == data["ad_groups"][0]["ad"]["headlines"]
    assert [d.text for d in rsa.descriptions] == data["ad_groups"][0]["ad"]["descriptions"]
    assert (rsa.path1, rsa.path2) == ("gym", "join")
    assert list(ad_group_ad.ad.final_urls) == ["https://www.example.com/gym"]
    assert ad_group_ad.status == ads_client.enums.AdGroupAdStatusEnum.ENABLED


def test_assets_carry_their_text_and_urls(ads_client, search_plan):
    data = search_plan()
    data["sitelinks"][3].update(desc1="", desc2="")
    ops, labels = compiled(ads_client, data)
    first = op_at(ops, labels, "sitelink[0] Class Schedule").asset_operation.create
    assert (first.sitelink_asset.link_text, first.sitelink_asset.description1, first.sitelink_asset.description2) == (
        "Class Schedule", "See every class this week", "Book a spot in seconds")
    assert list(first.final_urls) == ["https://www.example.com/classes"]
    bare = op_at(ops, labels, "sitelink[3] Find a Gym").asset_operation.create.sitelink_asset
    assert (bare.description1, bare.description2) == ("", "")
    assert op_at(ops, labels, "callout[2] Expert Coaches").asset_operation.create.callout_asset.callout_text == "Expert Coaches"
    snippet = op_at(ops, labels, "snippet[0] Types").asset_operation.create.structured_snippet_asset
    assert snippet.header == "Types" and list(snippet.values) == ["Yoga", "Spin", "Boxing", "Strength"]


def test_a_plan_without_assets_or_paths(ads_client, search_plan):
    data = search_plan(sitelinks=[], callouts=[], snippets=[], negative_keywords=[])
    data["ad_groups"][0]["ad"].update(path1="", path2="")
    ops, labels = compiled(ads_client, data)
    assert labels[:2] == ["budget", "campaign"] and not any("link" in label for label in labels)
    rsa = op_at(ops, labels, "ad group[0] ad").ad_group_ad_operation.create.ad.responsive_search_ad
    assert "path1" not in rsa and "path2" not in rsa


def test_compiling_twice_gives_the_same_bytes(ads_client, search_plan):
    from gads_builder import gads

    first, _ = compiled(ads_client, search_plan())
    second, _ = compiled(ads_client, search_plan())
    assert gads.request_digest(CID, first) == gads.request_digest(CID, second)


def test_location_option_and_ai_max_are_set_explicitly(ads_client, search_plan):
    """Only people in (or regularly in) the locations, and AI Max off: both sent, not left to defaults."""
    E = ads_client.enums
    ops, labels = compiled(ads_client, search_plan())
    c = op_at(ops, labels, "campaign").campaign_operation.create
    cpb = type(c).pb(c)
    assert cpb.HasField("geo_target_type_setting")
    assert c.geo_target_type_setting.positive_geo_target_type == E.PositiveGeoTargetTypeEnum.PRESENCE
    assert cpb.HasField("ai_max_setting") and cpb.ai_max_setting.HasField("enable_ai_max")
    assert c.ai_max_setting.enable_ai_max is False
