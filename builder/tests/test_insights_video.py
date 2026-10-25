import json
from pathlib import Path

import pytest

from gads_builder import insights, insights_video

CID = "1234567890"


class History:
    """Answers gads.search with fake video history from both views. Records the queries."""

    def __init__(self, ads_client):
        self.client, self.E = ads_client, ads_client.enums
        self.queries = []

    def _row(self, campaign, impressions, views, clicks, conversions, cost):
        r = self.client.get_type("GoogleAdsRow")
        r.campaign.name = campaign
        m = r.metrics
        m.impressions, m.video_trueview_views, m.clicks, m.conversions, m.cost_micros = (
            impressions, views, clicks, conversions, cost)
        return r

    def video_view(self):
        rows = []
        for campaign, vid, title, imp, views, clicks, conv in [
            ("Pulse YouTube A", "aaaaaaaaaaa", "Morning class", 100_000, 25_000, 900, 40.0),
            ("Pulse YouTube B", "aaaaaaaaaaa", "Morning class", 50_000, 10_000, 300, 20.0),
            ("Pulse YouTube A", "bbbbbbbbbbb", "Coach story", 80_000, 8_000, 200, 4.0),
            ("Pulse YouTube A", "ccccccccccc", "Teaser", 500, 100, 5, 1.0),  # too few impressions
        ]:
            r = self._row(campaign, imp, views, clicks, conv, imp * 10)
            r.campaign.advertising_channel_type = self.E.AdvertisingChannelTypeEnum.VIDEO
            r.video.id, r.video.title = vid, title
            rows.append(r)
        return rows

    def asset_view(self):
        rows = []
        for campaign, channel, vid, imp, views, clicks, conv in [
            ("Pulse App Springfield", "MULTI_CHANNEL", "aaaaaaaaaaa", 200_000, 500, 4000, 300.0),
            ("Pulse App Springfield", "MULTI_CHANNEL", "ddddddddddd", 150_000, 100, 1500, 30.0),
            ("Pulse DG Springfield", "DEMAND_GEN", "bbbbbbbbbbb", 40_000, 9_000, 600, 12.0),
            ("Pulse App Springfield", "MULTI_CHANNEL", "", 9_000, 0, 10, 0.0),  # an asset row with no video ID
            ("Pulse App Springfield", "MULTI_CHANNEL", "../../evil", 90_000, 0, 10, 50.0),  # not a YouTube ID
            ("Pulse DG Springfield", "DEMAND_GEN", "abcdefghij/", 90_000, 0, 10, 50.0),  # 11 characters, but not one
        ]:
            r = self._row(campaign, imp, views, clicks, conv, imp * 5)
            r.campaign.advertising_channel_type = getattr(self.E.AdvertisingChannelTypeEnum, channel)
            r.asset.youtube_video_asset.youtube_video_id = vid
            r.asset.youtube_video_asset.youtube_video_title = "From the asset"
            rows.append(r)
        return rows

    def __call__(self, client, customer_id, query):
        assert customer_id == CID
        self.queries.append(query)
        return self.video_view() if " FROM video " in query else self.asset_view()


@pytest.fixture
def history(ads_client, monkeypatch):
    fake = History(ads_client)
    monkeypatch.setattr(insights_video.gads, "search", fake)
    return fake


def build(ads_client, cfg, **kw):
    return insights_video.build_video_insights(ads_client, cfg, kw.pop("campaigns", None), download=False, **kw)


def test_videos_are_ranked_within_each_campaign_type(ads_client, cfg, history):
    res = build(ads_client, cfg)
    video = res["video_campaigns"]
    assert [v["youtube_id"] for v in video["winners"]] == ["aaaaaaaaaaa"]  # at most about half of the usable ones
    assert [v["youtube_id"] for v in video["losers"]] == ["bbbbbbbbbbb"]
    assert video["not_enough_data"] == 1
    top = video["winners"][0]
    assert top == {
        "youtube_id": "aaaaaaaaaaa", "title": "Morning class", "campaigns": ["Pulse YouTube A", "Pulse YouTube B"],
        "impressions": 150_000, "views": 35_000, "clicks": 1200, "view_rate": 0.2333, "ctr": 0.008,
        "conversions": 60.0, "conv_per_1k": 0.4, "cost": 1.5, "currency": "USD",
        "youtube_url": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
        "thumbnail_url": "https://i.ytimg.com/vi/aaaaaaaaaaa/hqdefault.jpg",
    }
    assert [v["youtube_id"] for v in res["app_campaigns"]["winners"]] == ["aaaaaaaaaaa"]
    assert [v["youtube_id"] for v in res["app_campaigns"]["losers"]] == ["ddddddddddd"]
    assert res["app_campaigns"]["winners"][0]["title"] == "From the asset"
    assert [v["youtube_id"] for v in res["demand_gen_campaigns"]["winners"]] == ["bbbbbbbbbbb"]
    assert res["demand_gen_campaigns"]["losers"] == []
    assert res["rank_by"] == "conv_per_1k" and res["campaigns"] == []
    assert res["scope"] == "all VIDEO, App and Demand Gen campaigns"


def test_only_real_youtube_ids_are_kept(ads_client, cfg, history):
    res = build(ads_client, cfg)
    seen = [v["youtube_id"] for kind in ("video_campaigns", "app_campaigns", "demand_gen_campaigns")
            for group in ("winners", "losers") for v in res[kind][group]]
    assert sorted(seen) == ["aaaaaaaaaaa", "aaaaaaaaaaa", "bbbbbbbbbbb", "bbbbbbbbbbb", "ddddddddddd"]
    assert res["app_campaigns"]["not_enough_data"] == 0 and res["demand_gen_campaigns"]["not_enough_data"] == 0


@pytest.mark.parametrize("usable, winners", [(1, 1), (2, 1), (3, 2), (5, 3), (30, 10)])
def test_winners_are_at_most_about_half_of_the_usable_videos(usable, winners):
    videos = [{"youtube_id": f"v{i:010d}", "title": "", "campaigns": ["C"], "impressions": 10_000, "views": 0,
               "clicks": 10, "conversions": float(i), "cost_micros": 0} for i in range(usable)]
    ranked = insights_video.rank(videos, 1000, "conv_per_1k", "EUR")
    assert len(ranked["winners"]) == winners
    assert len(ranked["losers"]) == min(5, usable - winners)
    assert all(v["currency"] == "EUR" for v in ranked["winners"] + ranked["losers"])


def test_rank_by_another_measure(ads_client, cfg, history):
    res = build(ads_client, cfg, rank_by="view_rate")
    assert [v["youtube_id"] for v in res["video_campaigns"]["winners"] + res["video_campaigns"]["losers"]] == [
        "aaaaaaaaaaa", "bbbbbbbbbbb"]
    res = build(ads_client, cfg, rank_by="ctr")
    assert [v["youtube_id"] for v in res["app_campaigns"]["winners"] + res["app_campaigns"]["losers"]] == [
        "aaaaaaaaaaa", "ddddddddddd"]
    with pytest.raises(ValueError, match="rank_by must be one of conv_per_1k, view_rate, ctr, views"):
        build(ads_client, cfg, rank_by="cost")


def test_losers_are_worst_first_and_never_winners(ads_client):
    videos = [{"youtube_id": f"v{i:010d}", "title": "", "campaigns": ["C"], "impressions": 10_000, "views": i * 100,
               "clicks": 10, "conversions": float(i), "cost_micros": 0} for i in range(1, 8)]
    ranked = insights_video.rank(videos, 1000, "conv_per_1k", "EUR", top=3, bottom=2)
    assert [v["youtube_id"] for v in ranked["winners"]] == ["v0000000007", "v0000000006", "v0000000005"]
    assert [v["youtube_id"] for v in ranked["losers"]] == ["v0000000001", "v0000000002"]


def test_the_queries(ads_client, cfg, history):
    build(ads_client, cfg, campaigns=["Pulse YouTube A", "  "], days=30)
    video, assets = history.queries
    start, end = insights.date_range(30)
    assert video.startswith("SELECT campaign.name, campaign.advertising_channel_type, video.id, video.title, ")
    assert "FROM video WHERE campaign.advertising_channel_type = 'VIDEO' AND campaign.name IN ('Pulse YouTube A')" in video
    assert f"segments.date BETWEEN '{start}' AND '{end}'" in video and "metrics.video_trueview_views" in video
    assert assets.startswith("SELECT campaign.name, campaign.advertising_channel_type, "
                             "ad_group_ad_asset_view.field_type, ")  # Google wants a filtered field selected
    assert ("FROM ad_group_ad_asset_view WHERE ad_group_ad_asset_view.field_type = 'YOUTUBE_VIDEO' "
            "AND campaign.advertising_channel_type IN ('MULTI_CHANNEL', 'DEMAND_GEN') "
            "AND campaign.name IN ('Pulse YouTube A')") in assets
    assert "SELECT campaign.name, campaign.advertising_channel_type, " in assets


def test_missing_campaigns_days_and_the_minimum(ads_client, cfg, history):
    res = build(ads_client, cfg, campaigns=["Pulse YouTube A", "Typo"], days=5000, min_impressions=10)
    assert res["missing_campaigns"] == ["Typo"] and res["days"] == 365 and res["min_impressions"] == 1000
    assert res["campaigns"] == ["Pulse YouTube A", "Typo"] and res["scope"] == "the named campaigns"


def test_the_report_is_saved(ads_client, cfg, history):
    res = build(ads_client, cfg)
    saved = json.loads(Path(res["saved_to"]).read_text(encoding="utf-8"))
    assert saved["video_campaigns"] == res["video_campaigns"] and saved["downloaded"] == []
    assert Path(res["saved_to"]).name == "video-insights.json"


def test_thumbnails_are_downloaded_once_per_video(ads_client, cfg, history, monkeypatch):
    fetched = []

    def fake_download(url, dest):
        fetched.append(url)
        dest.write_bytes(b"jpeg")
        return dest

    monkeypatch.setattr(insights_video, "_download", fake_download)
    res = insights_video.build_video_insights(ads_client, cfg, None, download=True)
    assert sorted(fetched) == sorted({f"https://i.ytimg.com/vi/{v}/hqdefault.jpg"
                                      for v in ("aaaaaaaaaaa", "bbbbbbbbbbb", "ddddddddddd")})
    assert len(res["downloaded"]) == 3
    assert res["app_campaigns"]["winners"][0]["local_file"] == res["video_campaigns"]["winners"][0]["local_file"]
