"""creative_insights(mode="pmax"): Performance Max history, with gads.search answered from fake rows."""

import json
from pathlib import Path

import pytest

from gads_builder import insights, insights_pmax

CID = "1234567890"


class Account:
    """Answers gads.search like an account with one Performance Max campaign (ID 42). Records every query."""

    def __init__(self, ads_client):
        self.client, self.E = ads_client, ads_client.enums
        self.queries = []
        self.assets = [
            ("HEADLINE", 1, "Classes Every Day", "", "", 5000, 100, 10.0),
            ("HEADLINE", 2, "Join Pulse Fitness", "", "", 4000, 40, 1.0),
            ("HEADLINE", 3, "Thin data", "", "", 200, 10, 5.0),
            ("LONG_HEADLINE", 4, "Join a friendly Springfield gym", "", "", 3000, 30, 3.0),
            ("CALL_TO_ACTION_SELECTION", 5, "", "", "", 3000, 30, 3.0),
            ("SQUARE_MARKETING_IMAGE", 6, "", "https://img/6", "", 6000, 60, 6.0),
            ("YOUTUBE_VIDEO", 8, "", "", "abcdefghijk", 9000, 90, 9.0),
        ]
        # brand guidelines on: the business name and logos are the campaign's own assets
        self.campaign_assets = [
            ("BUSINESS_NAME", 11, "Pulse Fitness", "", 7000, 70, 7.0),
            ("LOGO", 12, "", "https://img/12", 7000, 35, 2.0),
            ("LANDSCAPE_LOGO", 7, "", "https://img/7", 6000, 60, 1.0),
        ]
        self.combinations = [[("HEADLINE", 1), ("SQUARE_MARKETING_IMAGE", 6)], [("YOU_TUBE_VIDEO", 8)]]  # served names
        self.categories = [("gym membership", 500, 20, 4.0), ("", 300, 10, 1.0), ("yoga classes", 400, 30, 4.0)]

    def row(self):
        return self.client.get_type("GoogleAdsRow")

    def asset_rows(self):
        rows = []
        for field, n, text, url, youtube, impressions, clicks, conversions in self.assets:
            r = self.row()
            r.campaign.name, r.asset_group.name = "Pulse PMax", "Members"
            r.asset_group_asset.field_type = getattr(self.E.AssetFieldTypeEnum, field)
            r.asset_group_asset.source = self.E.AssetSourceEnum.ADVERTISER
            r.asset.resource_name = f"customers/{CID}/assets/{n}"
            if text:  # asset_data is a oneof: set only the kind the asset is
                r.asset.text_asset.text = text
            elif url:
                r.asset.image_asset.full_size.url = url
            elif youtube:
                r.asset.youtube_video_asset.youtube_video_id = youtube
            else:
                r.asset.call_to_action_asset.call_to_action = self.E.CallToActionTypeEnum.SIGN_UP
            r.metrics.impressions, r.metrics.clicks, r.metrics.conversions = impressions, clicks, conversions
            r.metrics.cost_micros = 1_000_000
            rows.append(r)
        return rows

    def campaign_asset_rows(self):
        rows = []
        for field, n, text, url, impressions, clicks, conversions in self.campaign_assets:
            r = self.row()
            r.campaign.name = "Pulse PMax"
            r.campaign.advertising_channel_type = self.E.AdvertisingChannelTypeEnum.PERFORMANCE_MAX
            r.campaign_asset.field_type = getattr(self.E.AssetFieldTypeEnum, field)
            r.campaign_asset.source = self.E.AssetSourceEnum.ADVERTISER
            r.asset.resource_name = f"customers/{CID}/assets/{n}"
            if text:
                r.asset.text_asset.text = text
            else:
                r.asset.image_asset.full_size.url = url
            r.metrics.impressions, r.metrics.clicks, r.metrics.conversions = impressions, clicks, conversions
            r.metrics.cost_micros = 1_000_000
            rows.append(r)
        return rows

    def group_rows(self):
        r = self.row()
        r.campaign.name = "Pulse PMax"
        g = r.asset_group
        g.name, g.status = "Members", self.E.AssetGroupStatusEnum.ENABLED
        g.ad_strength = self.E.AdStrengthEnum.AVERAGE
        g.primary_status = self.E.AssetGroupPrimaryStatusEnum.NOT_ELIGIBLE
        g.primary_status_reasons.append(self.E.AssetGroupPrimaryStatusReasonEnum.CAMPAIGN_PAUSED)
        item = self.client.get_type("AdStrengthActionItem")
        item.action_item_type = self.E.AdStrengthActionItemTypeEnum.ADD_ASSET
        item.add_asset_details.asset_field_type = self.E.AssetFieldTypeEnum.YOUTUBE_VIDEO
        item.add_asset_details.asset_count = 1
        item.add_asset_details.video_aspect_ratio_requirement = (
            self.E.AssetCoverageVideoAspectRatioRequirementEnum.VERTICAL)
        g.asset_coverage.ad_strength_action_items.append(item)
        item = self.client.get_type("AdStrengthActionItem")
        item.add_asset_details.asset_field_type = self.E.AssetFieldTypeEnum.HEADLINE
        item.add_asset_details.asset_count = 4
        g.asset_coverage.ad_strength_action_items.append(item)
        return [r]

    def combination_rows(self):
        r = self.row()
        r.campaign.name, r.asset_group.name = "Pulse PMax", "Members"
        for combo in self.combinations:
            data = self.client.get_type("AssetGroupAssetCombinationData")
            for field, n in combo:
                used = self.client.get_type("AssetUsage")
                used.asset = f"customers/{CID}/assets/{n}"
                used.served_asset_field_type = getattr(self.E.ServedAssetFieldTypeEnum, field)
                data.asset_combination_served_assets.append(used)
            r.asset_group_top_combination_view.asset_group_top_combinations.append(data)
        return [r]

    def campaign_rows(self):
        r = self.row()
        r.campaign.id, r.campaign.name = 42, "Pulse PMax"
        r.campaign.advertising_channel_type = self.E.AdvertisingChannelTypeEnum.PERFORMANCE_MAX
        return [r]

    def category_rows(self):
        rows = []
        for label, impressions, clicks, conversions in self.categories:
            r = self.row()
            r.campaign_search_term_insight.category_label = label
            r.metrics.impressions, r.metrics.clicks, r.metrics.conversions = impressions, clicks, conversions
            rows.append(r)
        return rows

    def __call__(self, client, customer_id, query):
        assert customer_id == CID
        self.queries.append(query)
        resource = query.split(" FROM ", 1)[1].split(" ", 1)[0]
        return {"asset_group_asset": self.asset_rows, "asset_group": self.group_rows,
                "campaign_asset": self.campaign_asset_rows,
                "asset_group_top_combination_view": self.combination_rows, "campaign": self.campaign_rows,
                "campaign_search_term_insight": self.category_rows}[resource]()


@pytest.fixture
def account(ads_client, monkeypatch):
    fake = Account(ads_client)
    monkeypatch.setattr(insights.gads, "search", fake)
    return fake


def build(cfg, ads_client, campaigns=("Pulse PMax",), download=False):
    return insights.build_insights(ads_client, cfg, list(campaigns), 30, 1000, download, mode="pmax")


def test_assets_are_ranked_by_results_per_kind(cfg, ads_client, account):
    res = build(cfg, ads_client)
    assert res["mode"] == "pmax" and res["labels"] == insights_pmax.NO_LABELS
    assert [w["text"] for w in res["headlines"]["winners"]] == ["Classes Every Day", "Join Pulse Fitness"]
    assert res["headlines"]["not_enough_data"] == 1
    assert res["long_headlines"]["winners"][0]["text"] == "Join a friendly Springfield gym"
    assert res["calls_to_action"]["winners"][0]["text"] == "SIGN_UP"
    assert res["square_images"]["winners"][0]["image_url"] == "https://img/6"
    assert res["landscape_logos"]["winners"][0]["image_url"] == "https://img/7"
    assert res["business_names"]["winners"][0]["text"] == "Pulse Fitness"
    assert res["logos"]["winners"][0]["image_url"] == "https://img/12"
    assert res["videos"]["winners"][0]["youtube_url"] == "https://www.youtube.com/watch?v=abcdefghijk"
    assert all(w["label"] == "UNKNOWN" for kind in insights_pmax.PMAX_KINDS.values() for w in res[kind]["winners"])
    assert res["missing_campaigns"] == []


def test_the_asset_query_reads_only_the_advertisers_own_assets(cfg, ads_client, account):
    build(cfg, ads_client)
    query = next(q for q in account.queries if " FROM asset_group_asset " in q)
    start, end = insights.date_range(30)
    assert "campaign.name IN ('Pulse PMax')" in query and f"segments.date BETWEEN '{start}' AND '{end}'" in query
    assert query.endswith("AND asset_group_asset.source = 'ADVERTISER'") and "asset_group_asset.source, " in query
    assert ("asset_group_asset.field_type IN ('HEADLINE', 'LONG_HEADLINE', 'DESCRIPTION', 'BUSINESS_NAME', "
            "'CALL_TO_ACTION_SELECTION', 'MARKETING_IMAGE', 'SQUARE_MARKETING_IMAGE', 'PORTRAIT_MARKETING_IMAGE', "
            "'LOGO', 'LANDSCAPE_LOGO', 'YOUTUBE_VIDEO')") in query


def test_the_brand_assets_are_read_from_the_campaigns_own_assets(cfg, ads_client, account):
    """With brand guidelines on, the business name and logos are campaign assets: one campaign_asset query, by
    date range, of the named Performance Max campaigns' own brand assets."""
    build(cfg, ads_client)
    [query] = [q for q in account.queries if " FROM campaign_asset " in q]
    start, end = insights.date_range(30)
    assert "campaign.name IN ('Pulse PMax')" in query and f"segments.date BETWEEN '{start}' AND '{end}'" in query
    assert "AND campaign.advertising_channel_type = 'PERFORMANCE_MAX'" in query
    assert "campaign_asset.field_type IN ('BUSINESS_NAME', 'LOGO', 'LANDSCAPE_LOGO')" in query
    assert query.endswith("AND campaign_asset.source = 'ADVERTISER'")
    assert all(f in query.split(" FROM ", 1)[0] for f in ("metrics.impressions", "metrics.conversions",
                                                          "asset.text_asset.text", "asset.image_asset.full_size.url"))


def test_no_query_uses_a_named_date_range(cfg, ads_client, account):
    build(cfg, ads_client)
    assert not any("DURING" in q or "LAST_" in q for q in account.queries)


def test_asset_groups_show_ad_strength_and_what_to_add(cfg, ads_client, account):
    res = build(cfg, ads_client)
    assert res["asset_groups"] == [{
        "campaign": "Pulse PMax", "asset_group": "Members", "status": "ENABLED", "ad_strength": "AVERAGE",
        "primary_status": "NOT_ELIGIBLE", "primary_status_reasons": ["CAMPAIGN_PAUSED"],
        "add_assets": [{"field": "YOUTUBE_VIDEO", "count": 1, "video_aspect_ratio": "VERTICAL"},
                       {"field": "HEADLINE", "count": 4}],
    }]


def test_top_combinations_name_their_assets(cfg, ads_client, account):
    res = build(cfg, ads_client)
    assert res["top_combinations"] == [{"campaign": "Pulse PMax", "asset_group": "Members", "combinations": [
        [{"field": "HEADLINE", "asset": f"customers/{CID}/assets/1", "text": "Classes Every Day"},
         {"field": "SQUARE_MARKETING_IMAGE", "asset": f"customers/{CID}/assets/6", "image_url": "https://img/6"}],
        [{"field": "YOU_TUBE_VIDEO", "asset": f"customers/{CID}/assets/8", "youtube_id": "abcdefghijk"}],
    ]}]


def test_search_categories_are_read_one_campaign_at_a_time(cfg, ads_client, account):
    res = build(cfg, ads_client)
    assert res["search_categories"] == [{"campaign": "Pulse PMax", "categories": [
        {"category": "yoga classes", "impressions": 400, "clicks": 30, "conversions": 4.0},
        {"category": "gym membership", "impressions": 500, "clicks": 20, "conversions": 4.0},
        {"category": "(other searches)", "impressions": 300, "clicks": 10, "conversions": 1.0},
    ]}]
    campaign_query = next(q for q in account.queries if " FROM campaign " in q)
    assert "campaign.advertising_channel_type FROM campaign" in campaign_query
    assert campaign_query.endswith("AND campaign.advertising_channel_type = 'PERFORMANCE_MAX'")
    insight = next(q for q in account.queries if " FROM campaign_search_term_insight " in q)
    assert "WHERE campaign_search_term_insight.campaign_id = 42 AND segments.date BETWEEN" in insight


def test_every_query_selects_the_fields_it_filters_on(cfg, ads_client, account):
    build(cfg, ads_client)
    for query in account.queries:
        select, where = query.split(" FROM ", 1)
        for name in ("campaign.name", "asset_group.status", "asset_group_asset.source", "asset_group_asset.field_type",
                     "campaign_asset.source", "campaign_asset.field_type", "campaign.advertising_channel_type",
                     "campaign_search_term_insight.campaign_id"):
            if f"{name} " in where.split(" WHERE ", 1)[1]:
                assert name in select, (name, query)


def test_the_report_is_saved_and_images_are_downloaded(cfg, ads_client, account, monkeypatch):
    fetched = []
    monkeypatch.setattr(insights_pmax, "download_media",
                        lambda ranked, folder: fetched.append(sorted(k for k in ranked if ranked[k]["winners"])) or [])
    res = build(cfg, ads_client, download=True)
    saved = json.loads(Path(res["saved_to"]).read_text(encoding="utf-8"))
    assert saved["mode"] == "pmax" and saved["search_categories"] == res["search_categories"]
    assert Path(res["saved_to"]).parent.name.endswith("-pmax-pulse-pmax")
    assert "landscape_logos" in fetched[0] and "landscape_logos" in insights.IMAGE_KINDS


def test_missing_campaigns_and_limits(cfg, ads_client, account):
    assert build(cfg, ads_client, campaigns=("Pulse PMax", "Typo"))["missing_campaigns"] == ["Typo"]
    with pytest.raises(ValueError, match="at least one campaign"):
        build(cfg, ads_client, campaigns=(" ",))
    with pytest.raises(ValueError, match="at most 10 campaigns"):
        build(cfg, ads_client, campaigns=[f"C{i}" for i in range(11)])
