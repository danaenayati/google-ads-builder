from gads_builder import plans, templates


def test_quote_and_where():
    assert templates.quote("It's") == "'It\\'s'"
    assert templates.where_campaign("12345") == "campaign.id = 12345"
    assert templates.where_campaign("City Launch") == "campaign.name = 'City Launch'"


def test_build_template_plan_is_a_valid_plan_shape(cfg):
    campaign = {
        "name": "City Launch",
        "subtype": "APP_CAMPAIGN",
        "app_store": "GOOGLE_APP_STORE",
        "app_id": "com.example.app",
        "goal": "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST",
        "target_cpa_micros": 14_000_000,
        "conversion_actions": ["customers/1234567890/conversionActions/111"],
        "budget_micros": 450_000_000,
        "currency": "USD",
    }
    ad = {
        "headlines": ["One", "Two"],
        "descriptions": ["Desc"],
        "images": ["customers/1234567890/assets/1"],
        "videos": ["customers/1234567890/assets/2"],
    }
    data = templates.build_template_plan(
        campaign, ["geoTargetConstants/1"], ["languageConstants/1000"], ad, {"geoTargetConstants/1": "City"}
    )
    assert data["name"] == "City Launch (copy)" and data["template"] == "City Launch"
    assert data["target_cpa"] == 14.0 and data["daily_budget"] == 450.0
    assert data["images"] == [{"asset": "customers/1234567890/assets/1"}]
    plan = plans.plan_from_dict(data)  # parses without error
    assert plan.brief["names"]["geoTargetConstants/1"] == "City"


def test_build_template_plan_without_target_cpa():
    campaign = {
        "name": "X", "subtype": "APP_CAMPAIGN", "app_store": "GOOGLE_APP_STORE", "app_id": "a",
        "goal": "OPTIMIZE_IN_APP_CONVERSIONS_WITHOUT_TARGET_CPA", "target_cpa_micros": 0,
        "conversion_actions": [], "budget_micros": 1_000_000, "currency": "USD",
    }
    data = templates.build_template_plan(campaign, [], [], None, {})
    assert data["target_cpa"] is None and data["headlines"] == [] and data["videos"] == []


# ------------------------------------------------------------ GoogleAdsRow-based tests

import pytest  # noqa: E402


def test_persian_digits_are_a_name_not_an_id():
    assert templates.where_campaign("۱۲۳") == "campaign.name = '۱۲۳'"
    assert templates.where_campaign("123") == "campaign.id = 123"


def _text(ads_client, text):
    t = ads_client.get_type("AdTextAsset")
    t.text = text
    return t


def _asset(ads_client, kind, asset):
    a = ads_client.get_type(kind)
    a.asset = asset
    return a


def _ad_row(ads_client, ad_type):
    r = ads_client.get_type("GoogleAdsRow")
    ad = r.ad_group_ad.ad
    ad.resource_name = "customers/1234567890/ads/5"
    if ad_type == "APP_AD":
        ad.type_ = ads_client.enums.AdTypeEnum.APP_AD
        ad.app_ad.headlines.append(_text(ads_client, "H1"))
        ad.app_ad.descriptions.append(_text(ads_client, "D1"))
        ad.app_ad.images.append(_asset(ads_client, "AdImageAsset", "customers/1234567890/assets/1"))
        ad.app_ad.youtube_videos.append(_asset(ads_client, "AdVideoAsset", "customers/1234567890/assets/2"))
    elif ad_type == "APP_ENGAGEMENT_AD":
        ad.type_ = ads_client.enums.AdTypeEnum.APP_ENGAGEMENT_AD
        ad.app_engagement_ad.headlines.append(_text(ads_client, "H2"))
        ad.app_engagement_ad.descriptions.append(_text(ads_client, "D2"))
        ad.app_engagement_ad.images.append(_asset(ads_client, "AdImageAsset", "customers/1234567890/assets/3"))
        ad.app_engagement_ad.videos.append(_asset(ads_client, "AdVideoAsset", "customers/1234567890/assets/4"))
    else:
        ad.type_ = ads_client.enums.AdTypeEnum.TEXT_AD
    return r


def test_ad_assets_for_both_app_ad_types(ads_client):
    assert templates.ad_assets(_ad_row(ads_client, "APP_AD")) == {
        "headlines": ["H1"], "descriptions": ["D1"],
        "images": ["customers/1234567890/assets/1"], "videos": ["customers/1234567890/assets/2"]}
    assert templates.ad_assets(_ad_row(ads_client, "APP_ENGAGEMENT_AD")) == {
        "headlines": ["H2"], "descriptions": ["D2"],
        "images": ["customers/1234567890/assets/3"], "videos": ["customers/1234567890/assets/4"]}


def test_ad_assets_refuses_other_types(ads_client, monkeypatch):
    with pytest.raises(ValueError, match="TEXT_AD"):
        templates.ad_assets(_ad_row(ads_client, "TEXT_AD"))
    monkeypatch.setattr(templates.gads, "search", lambda *a, **k: [_ad_row(ads_client, "TEXT_AD")])
    with pytest.raises(ValueError):
        templates.current_ad(ads_client, "1234567890", "customers/1234567890/ads/5")


def _campaign_row(ads_client, name, cid=1, cpa=0):
    r = ads_client.get_type("GoogleAdsRow")
    r.campaign.id = cid
    r.campaign.name = name
    r.campaign.status = ads_client.enums.CampaignStatusEnum.PAUSED
    r.campaign.advertising_channel_type = ads_client.enums.AdvertisingChannelTypeEnum.MULTI_CHANNEL
    r.campaign.advertising_channel_sub_type = ads_client.enums.AdvertisingChannelSubTypeEnum.APP_CAMPAIGN
    r.campaign.app_campaign_setting.bidding_strategy_goal_type = (
        ads_client.enums.AppCampaignBiddingStrategyGoalTypeEnum.OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST)
    r.campaign_budget.amount_micros = 5_000_000
    r.campaign.target_cpa.target_cpa_micros = cpa
    return r


def test_list_campaigns_builds_query_and_maps(ads_client, monkeypatch):
    seen = {}

    def fake(client, customer_id, query):
        seen["q"] = query
        return [_campaign_row(ads_client, "Zed 10%", 2, 3_000_000), _campaign_row(ads_client, "Alpha 10%", 1)]

    monkeypatch.setattr(templates.gads, "search", fake)
    out = templates.list_campaigns(ads_client, "1234567890", "10%")
    assert [c["name"] for c in out] == ["Alpha 10%", "Zed 10%"]
    assert out[1] == {"id": 2, "name": "Zed 10%", "status": "PAUSED", "subtype": "APP_CAMPAIGN",
                      "goal": "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST",
                      "budget_micros": 5_000_000, "target_cpa_micros": 3_000_000}
    q = seen["q"]
    assert "campaign.status != 'REMOVED'" in q and "campaign.advertising_channel_type = MULTI_CHANNEL" in q
    assert "campaign.name" not in q.split("FROM campaign", 1)[1]
    assert "campaign.target_cpa.target_cpa_micros" in q


def test_list_campaigns_validates_channel_and_sends_no_name_condition(ads_client, monkeypatch):
    seen = {}
    monkeypatch.setattr(templates.gads, "search", lambda c, i, q: seen.setdefault("q", q) and [])
    templates.list_campaigns(ads_client, "1234567890", "it's")
    assert "LIKE" not in seen["q"] and "it" not in seen["q"].split("FROM campaign", 1)[1]
    with pytest.raises(ValueError):
        templates.list_campaigns(ads_client, "1234567890", "", "x' OR 1=1")


@pytest.mark.parametrize("needle, name", [
    ("city", "Springfield CITY launch"), ("CITY", "springfield city launch"),
    ("[a]", "x [A] y"), ("a_b", "A_B"), ("100%", "Promo 100% off"), ("]", "end]"),
])
def test_list_campaigns_matches_case_insensitively_with_special_characters(ads_client, monkeypatch, needle, name):
    rows = [_campaign_row(ads_client, name, 1), _campaign_row(ads_client, "Unrelated", 2)]
    monkeypatch.setattr(templates.gads, "search", lambda c, i, q: rows)
    assert [c["name"] for c in templates.list_campaigns(ads_client, "1234567890", needle)] == [name]


def test_list_campaigns_underscore_and_percent_are_not_wildcards(ads_client, monkeypatch):
    rows = [_campaign_row(ads_client, "AxB", 1), _campaign_row(ads_client, "Anything", 2)]
    monkeypatch.setattr(templates.gads, "search", lambda c, i, q: rows)
    assert templates.list_campaigns(ads_client, "1234567890", "A_B") == []
    assert templates.list_campaigns(ads_client, "1234567890", "A%") == []


def test_describe_campaign_rejects_ambiguous_name(ads_client, monkeypatch):
    rows = [_campaign_row(ads_client, "Dup", 1), _campaign_row(ads_client, "Dup", 2)]
    monkeypatch.setattr(templates.gads, "search", lambda *a, **k: rows)
    with pytest.raises(LookupError, match="campaign ID"):
        templates.describe_campaign(ads_client, "1234567890", "Dup")


def test_describe_campaign_lists_only_app_ads_and_skips_removed(ads_client, monkeypatch):
    calls = []

    def fake(client, customer_id, query):
        calls.append(query)
        if "FROM campaign WHERE" in query:
            return [_campaign_row(ads_client, "One", 1)]
        if "FROM campaign_criterion" in query:
            return []
        if "FROM ad_group_ad" in query:
            a, b = _ad_row(ads_client, "APP_AD"), _ad_row(ads_client, "TEXT_AD")
            a.ad_group_ad.status = ads_client.enums.AdGroupAdStatusEnum.ENABLED
            return [b, a]
        return []

    monkeypatch.setattr(templates.gads, "search", fake)
    out = templates.describe_campaign(ads_client, "1234567890", "One")
    assert [x["ad_type"] for x in out["ads"]] == ["APP_AD"]
    assert "campaign.status != 'REMOVED'" in calls[0]


def test_template_plan_copies_deep_links():
    campaign = {
        "name": "Eng", "subtype": "APP_CAMPAIGN_FOR_ENGAGEMENT", "app_store": "GOOGLE_APP_STORE", "app_id": "a",
        "goal": "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST", "target_cpa_micros": 5_000_000,
        "conversion_actions": [], "budget_micros": 1_000_000, "currency": "USD",
    }
    ad = {"headlines": ["H"], "descriptions": ["D"], "images": [], "videos": [],
          "final_app_urls": [{"os_type": "ANDROID", "url": "example://open/.*"}]}
    data = templates.build_template_plan(campaign, [], [], ad, {})
    assert data["final_app_urls"] == [{"os_type": "ANDROID", "url": "example://open/.*"}]


def _named_row(ads_client, resource, resource_name, **fields):
    r = ads_client.get_type("GoogleAdsRow")
    obj = getattr(r, resource)
    obj.resource_name = resource_name
    for key, value in fields.items():
        setattr(obj, key, value)
    return r


def test_google_names_for_every_kind(ads_client, monkeypatch):
    queries = []
    rows = {
        "conversion_action": [_named_row(ads_client, "conversion_action", "customers/1234567890/conversionActions/1", name="Install")],
        "geo_target_constant": [_named_row(ads_client, "geo_target_constant", "geoTargetConstants/2", name="Springfield",
                                           canonical_name="Springfield,Test Region")],
        "language_constant": [_named_row(ads_client, "language_constant", "languageConstants/1000", name="English")],
        "asset": [_named_row(ads_client, "asset", "customers/1234567890/assets/4", name="Hero")],
        "user_list": [_named_row(ads_client, "user_list", "customers/1234567890/userLists/6", name="Past members")],
        "audience": [_named_row(ads_client, "audience", "customers/1234567890/audiences/7", name="Members")],
    }

    def fake(client, customer_id, query):
        queries.append(query)
        return rows[query.split(" FROM ", 1)[1].split(" ", 1)[0]]

    monkeypatch.setattr(templates.gads, "search", fake)
    found = templates.google_names(ads_client, "1234567890", [
        "customers/1234567890/conversionActions/1", "geoTargetConstants/2", "languageConstants/1000",
        "customers/1234567890/assets/4", "customers/1234567890/assets/4", "customers/9999999999/assets/5",
        "geoTargetConstants/2' OR 1=1", "nonsense", "customers/1234567890/userLists/6", "customers/9999999999/userLists/6",
        "customers/1234567890/audiences/7", "customers/9999999999/audiences/7",
    ])
    assert found == {
        "customers/1234567890/conversionActions/1": ("Install",),
        "geoTargetConstants/2": ("Springfield,Test Region", "Springfield"),
        "languageConstants/1000": ("English",),
        "customers/1234567890/assets/4": ("Hero",),
        "customers/1234567890/userLists/6": ("Past members",),
        "customers/1234567890/audiences/7": ("Members",),
    }
    assert len(queries) == 6
    assert "SELECT asset.resource_name, asset.name FROM asset WHERE asset.resource_name IN " \
           "('customers/1234567890/assets/4')" in queries
    assert all("9999999999" not in q and "OR 1=1" not in q and "nonsense" not in q for q in queries)
    assert "SELECT user_list.resource_name, user_list.name FROM user_list WHERE user_list.resource_name IN " \
           "('customers/1234567890/userLists/6')" in queries
    assert "SELECT audience.resource_name, audience.name FROM audience WHERE audience.resource_name IN " \
           "('customers/1234567890/audiences/7')" in queries
    assert templates.google_names(ads_client, "1234567890", []) == {} and len(queries) == 6


def test_google_names_ignores_ids_with_non_ascii_digits(ads_client, monkeypatch):
    def search(client, customer_id, query):
        raise AssertionError(f"queried: {query}")

    monkeypatch.setattr(templates.gads, "search", search)
    assert templates.google_names(ads_client, "1234567890", [
        "geoTargetConstants/\u0661\u0662", "languageConstants/\u06f1\u06f0\u06f0\u06f0",
        "customers/1234567890/conversionActions/\u0661", "customers/1234567890/assets/\u0661",
    ]) == {}


def test_describe_campaign_reads_criteria_and_ads_by_campaign_id(ads_client, monkeypatch):
    # A REMOVED campaign with the same name must not leak its targeting or ads into the template.
    calls = []

    def fake(client, customer_id, query):
        calls.append(query)
        return [_campaign_row(ads_client, "Same Name", 42)] if " FROM campaign WHERE " in query else []

    monkeypatch.setattr(templates.gads, "search", fake)
    templates.describe_campaign(ads_client, "1234567890", "Same Name")
    assert "campaign.name = 'Same Name'" in calls[0]
    assert " FROM campaign_criterion " in calls[1] and " FROM ad_group_ad " in calls[2]
    for query in calls[1:3]:
        where = query.split(" WHERE ", 1)[1]
        assert where.startswith("campaign.id = 42 AND ") and "campaign.name" not in where, query


def test_find_locations_goes_through_the_google_ads_layer(ads_client, monkeypatch):
    suggestion = ads_client.get_type("GeoTargetConstantSuggestion")
    suggestion.geo_target_constant.resource_name = "geoTargetConstants/1"
    suggestion.geo_target_constant.canonical_name = "Springfield,Test Region"
    suggestion.geo_target_constant.target_type = "City"
    suggestion.reach = 1000
    suggestion.search_term = "Springfield"
    seen = {}
    monkeypatch.setattr(templates.gads, "suggest_locations",
                        lambda client, names, country, locale: seen.update(args=(names, country, locale)) or [suggestion])
    assert templates.find_locations(ads_client, ["Springfield"], "XX") == [{
        "resource": "geoTargetConstants/1", "name": "Springfield,Test Region", "type": "City",
        "reach": 1000, "searched": "Springfield"}]
    assert seen["args"] == (["Springfield"], "XX", "en")


def _search_row(ads_client, name, cid, bidding, **micros):
    r = _campaign_row(ads_client, name, cid)
    r.campaign.advertising_channel_type = ads_client.enums.AdvertisingChannelTypeEnum.SEARCH
    r.campaign.advertising_channel_sub_type = ads_client.enums.AdvertisingChannelSubTypeEnum.UNSPECIFIED
    r.campaign.app_campaign_setting.bidding_strategy_goal_type = (
        ads_client.enums.AppCampaignBiddingStrategyGoalTypeEnum.UNSPECIFIED)
    r.campaign.bidding_strategy_type = getattr(ads_client.enums.BiddingStrategyTypeEnum, bidding)
    for path, value in micros.items():
        part, field = path.split("__")
        setattr(getattr(r.campaign, part), field, value)
    return r


def test_list_campaigns_for_search_shows_the_bidding_and_either_target_cpa(ads_client, monkeypatch):
    seen = {}

    def fake(client, customer_id, query):
        seen["q"] = query
        return [
            _search_row(ads_client, "Search A", 1, "MAXIMIZE_CONVERSIONS", maximize_conversions__target_cpa_micros=9_500_000),
            _search_row(ads_client, "Search B", 2, "TARGET_CPA", target_cpa__target_cpa_micros=12_000_000),
            _search_row(ads_client, "Search C", 3, "TARGET_SPEND"),
        ]

    monkeypatch.setattr(templates.gads, "search", fake)
    out = templates.list_campaigns(ads_client, "1234567890", "Search", "SEARCH")
    assert [(c["goal"], c["target_cpa_micros"]) for c in out] == [
        ("MAXIMIZE_CONVERSIONS", 9_500_000), ("TARGET_CPA", 12_000_000), ("TARGET_SPEND", 0)]
    assert "campaign.advertising_channel_type = SEARCH" in seen["q"]
    assert "campaign.bidding_strategy_type" in seen["q"] and "campaign.maximize_conversions.target_cpa_micros" in seen["q"]


def test_list_campaigns_for_demand_gen_shows_the_bidding_and_either_target_cpa(ads_client, monkeypatch):
    seen = {}

    def fake(client, customer_id, query):
        seen["q"] = query
        rows = [
            _search_row(ads_client, "Pulse DG A", 1, "MAXIMIZE_CONVERSIONS", maximize_conversions__target_cpa_micros=2_500_000),
            _search_row(ads_client, "Pulse DG B", 2, "TARGET_CPA", target_cpa__target_cpa_micros=3_000_000),
            _search_row(ads_client, "Pulse DG C", 3, "TARGET_SPEND"),
        ]
        for r in rows:
            r.campaign.advertising_channel_type = ads_client.enums.AdvertisingChannelTypeEnum.DEMAND_GEN
        return rows

    monkeypatch.setattr(templates.gads, "search", fake)
    out = templates.list_campaigns(ads_client, "1234567890", "Pulse DG", "DEMAND_GEN")
    assert [(c["goal"], c["target_cpa_micros"]) for c in out] == [
        ("MAXIMIZE_CONVERSIONS", 2_500_000), ("TARGET_CPA", 3_000_000), ("TARGET_SPEND", 0)]
    assert "campaign.advertising_channel_type = DEMAND_GEN" in seen["q"]


def test_the_list_campaigns_tool_mentions_demand_gen():
    from gads_builder import server

    assert "Demand Gen campaigns DEMAND_GEN" in " ".join(server.list_campaigns.__doc__.split())


def test_list_campaigns_for_app_falls_back_to_the_maximize_conversions_target(ads_client, monkeypatch):
    row = _campaign_row(ads_client, "App", 1)
    row.campaign.maximize_conversions.target_cpa_micros = 4_000_000
    monkeypatch.setattr(templates.gads, "search", lambda c, i, q: [row])
    [app] = templates.list_campaigns(ads_client, "1234567890")
    assert app["goal"] == "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST" and app["target_cpa_micros"] == 4_000_000
