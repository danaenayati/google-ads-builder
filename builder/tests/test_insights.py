import io
from datetime import date, timedelta
from pathlib import Path

import pytest
from PIL import Image

from gads_builder import insights


def row(asset, kind="HEADLINE", label="GOOD", impressions=1000, clicks=10, conversions=1.0, text="t", campaign="A"):
    return {
        "campaign": campaign, "field": kind, "label": label, "asset": asset, "text": text,
        "url": "", "youtube": "", "impressions": impressions, "clicks": clicks,
        "conversions": conversions, "cost_micros": 1_000_000,
    }


def test_aggregate_sums_and_keeps_label_of_biggest_row():
    stats = insights.aggregate([
        row("a1", label="LOW", impressions=100, clicks=1, conversions=0, campaign="A"),
        row("a1", label="BEST", impressions=900, clicks=9, conversions=3, campaign="B"),
    ])
    (s,) = stats
    assert (s.impressions, s.clicks, s.conversions, s.label) == (1000, 10, 3, "BEST")
    assert s.campaigns == ["A", "B"]
    assert round(s.ctr, 4) == 0.01 and s.conv_per_1k == 3.0


def test_rank_orders_by_label_then_conversions_then_ctr():
    stats = insights.aggregate([
        row("good-high", label="GOOD", conversions=9),
        row("best-low", label="BEST", conversions=1),
        row("low", label="LOW", conversions=50),
        row("thin", label="GOOD", impressions=10, conversions=5),
    ])
    ranked = insights.rank(stats, min_impressions=1000)["headlines"]
    assert [s["asset"] for s in ranked["winners"]][:2] == ["best-low", "good-high"]
    assert ranked["losers"][0]["asset"] == "low"
    assert ranked["not_enough_data"] == 1


def test_low_label_counts_even_with_few_impressions():
    stats = insights.aggregate([row("x", label="LOW", impressions=5)])
    assert insights.rank(stats, min_impressions=1000)["headlines"]["losers"][0]["asset"] == "x"


# ------------------------------------------------------------ fetch_rows


def _gads_row(ads_client, **kw):
    r = ads_client.get_type("GoogleAdsRow")
    r.campaign.name = kw.get("campaign", "Camp A")
    view = r.ad_group_ad_asset_view
    view.field_type = ads_client.enums.AssetFieldTypeEnum.HEADLINE
    view.performance_label = ads_client.enums.AssetPerformanceLabelEnum.BEST
    r.asset.resource_name = "customers/1234567890/assets/77"
    r.asset.text_asset.text = "Fast workouts"
    r.metrics.impressions = 5000
    r.metrics.clicks = 50
    r.metrics.conversions = 5.5
    r.metrics.cost_micros = 2_000_000
    return r


def test_fetch_rows_maps_google_rows(ads_client, monkeypatch):
    seen = {}

    def fake_search(client, customer_id, query):
        seen["query"] = query
        return [_gads_row(ads_client)]

    monkeypatch.setattr(insights.gads, "search", fake_search)
    (r,) = insights.fetch_rows(ads_client, "1234567890", ["Camp A"], 30)
    assert r["campaign"] == "Camp A" and r["field"] == "HEADLINE" and r["label"] == "BEST"
    assert r["asset"].endswith("/77") and r["text"] == "Fast workouts"
    assert (r["impressions"], r["clicks"], r["conversions"], r["cost_micros"]) == (5000, 50, 5.5, 2_000_000)
    assert "campaign.name IN ('Camp A')" in seen["query"]
    start, end = insights.date_range(30)
    assert f"segments.date BETWEEN '{start}' AND '{end}'" in seen["query"]


# ------------------------------------------------------------ downloads


class _Resp:
    def __init__(self, data):
        self.data = data

    def read(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _png_bytes(w, h, mode="RGBA"):
    buf = io.BytesIO()
    Image.new(mode, (w, h), (10, 20, 30, 128) if mode == "RGBA" else (10, 20, 30)).save(buf, "PNG")
    return buf.getvalue()


def test_download_converts_png_to_rgb_jpeg_and_downsizes(tmp_path, monkeypatch):
    monkeypatch.setattr(insights.urllib.request, "urlopen", lambda url, timeout=0: _Resp(_png_bytes(3200, 1600)))
    out = insights._download("https://x/y", tmp_path / "a.jpg")
    with Image.open(out) as im:
        assert im.format == "JPEG" and im.mode == "RGB" and max(im.size) == 1600


def test_download_skips_html_and_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(insights.urllib.request, "urlopen", lambda url, timeout=0: _Resp(b"<html>nope</html>"))
    assert insights._download("https://x/y", tmp_path / "a.jpg") is None
    assert not (tmp_path / "a.jpg").exists()

    def boom(url, timeout=0):
        raise ValueError("weird")

    monkeypatch.setattr(insights.urllib.request, "urlopen", boom)
    assert insights._download("https://x/y", tmp_path / "b.jpg") is None


def test_one_bad_download_does_not_stop_the_rest(tmp_path, monkeypatch):
    def fake(url, timeout=0):
        if "bad" in url:
            raise OSError("down")
        return _Resp(_png_bytes(100, 100))

    monkeypatch.setattr(insights.urllib.request, "urlopen", fake)
    item = lambda n, u: {"asset": f"customers/1/assets/{n}", "image_url": u, "youtube_id": ""}  # noqa: E731
    empty = {"winners": [], "losers": []}
    ranked = {"images": {"winners": [item(1, "https://bad/1"), item(2, "https://ok/2")], "losers": []}, "videos": empty}
    saved = insights.download_media(ranked, tmp_path / "f")
    assert len(saved) == 1 and saved[0].endswith("-2.jpg")


# ------------------------------------------------------------ build_insights


def _build(cfg, ads_client, monkeypatch, rows, campaigns=("Camp A",), days=90):
    monkeypatch.setattr(insights.gads, "search", lambda *a, **k: rows)
    return insights.build_insights(ads_client, cfg, list(campaigns), days, 1000, False)


def test_missing_campaigns_are_reported(cfg, ads_client, monkeypatch):
    res = _build(cfg, ads_client, monkeypatch, [_gads_row(ads_client)], campaigns=("Camp A", "Camp typo"))
    assert res["missing_campaigns"] == ["Camp typo"]
    assert res["headlines"]["winners"][0]["conv_rate"] == 0.11


def test_needs_a_campaign(cfg, ads_client, monkeypatch):
    with pytest.raises(ValueError):
        _build(cfg, ads_client, monkeypatch, [], campaigns=())
    with pytest.raises(ValueError):
        _build(cfg, ads_client, monkeypatch, [], campaigns=("  ",))


def test_days_are_clamped(cfg, ads_client, monkeypatch):
    assert _build(cfg, ads_client, monkeypatch, [], days=0)["days"] == 1
    assert _build(cfg, ads_client, monkeypatch, [], days=5000)["days"] == 365


def test_every_run_gets_its_own_folder(cfg, ads_client, monkeypatch):
    a = _build(cfg, ads_client, monkeypatch, [])["saved_to"]
    b = _build(cfg, ads_client, monkeypatch, [])["saved_to"]
    assert a != b and Path(a).exists() and Path(b).exists()


def test_min_impressions_is_at_least_1000(cfg, ads_client, monkeypatch):
    rows = [_gads_row(ads_client)]
    rows[0].metrics.impressions = 500
    rows[0].ad_group_ad_asset_view.performance_label = ads_client.enums.AssetPerformanceLabelEnum.GOOD
    monkeypatch.setattr(insights.gads, "search", lambda *a, **k: rows)
    res = insights.build_insights(ads_client, cfg, ["Camp A"], 30, 10, False)
    assert res["min_impressions"] == 1000
    assert res["headlines"]["winners"] == [] and res["headlines"]["not_enough_data"] == 1
    assert insights.build_insights(ads_client, cfg, ["Camp A"], 30, 5000, False)["min_impressions"] == 5000


def test_best_and_good_assets_are_never_losers():
    stats = insights.aggregate([
        row("best", label="BEST", conversions=1),
        row("good-1", label="GOOD", conversions=9),
        row("good-2", label="GOOD", conversions=5),
        row("good-3", label="GOOD", conversions=2),
        row("low", label="LOW", conversions=50),
        row("learning", label="LEARNING", conversions=0),
    ])
    ranked = insights.rank(stats, min_impressions=1000, top=2, bottom=5)["headlines"]
    assert [s["asset"] for s in ranked["winners"]] == ["best", "good-1"]
    assert [s["asset"] for s in ranked["losers"]] == ["low", "learning"]


def test_labels_only_ranking_trusts_only_best_good_and_low():
    """Without results (Google refused the metrics), a label is all there is: LEARNING, PENDING, UNKNOWN
    and UNSPECIFIED say nothing about quality, so they are neither winners nor losers."""
    stats = insights.aggregate([
        row("learning", label="LEARNING", impressions=0, clicks=0, conversions=0),
        row("good", label="GOOD", impressions=0, clicks=0, conversions=0),
        row("pending", label="PENDING", impressions=0, clicks=0, conversions=0),
        row("low", label="LOW", impressions=0, clicks=0, conversions=0),
        row("unknown", label="UNKNOWN", impressions=0, clicks=0, conversions=0),
        row("best", label="BEST", impressions=0, clicks=0, conversions=0),
        row("unspecified", label="UNSPECIFIED", impressions=0, clicks=0, conversions=0),
    ])
    ranked = insights.rank(stats, min_impressions=0, labels_only=True)["headlines"]
    assert [s["asset"] for s in ranked["winners"]] == ["best", "good"]
    assert [s["asset"] for s in ranked["losers"]] == ["low"]
    assert ranked["not_enough_data"] == 4


def test_date_range_is_the_last_full_days():
    yesterday = date.today() - timedelta(days=1)
    assert insights.date_range(1) == (yesterday.isoformat(), yesterday.isoformat())
    start, end = insights.date_range(30)
    assert end == yesterday.isoformat() and date.fromisoformat(end) - date.fromisoformat(start) == timedelta(days=29)


# ------------------------------------------------------------ Demand Gen mode


def _dg_row(ads_client, field, asset, **kw):
    r = _gads_row(ads_client)
    r.ad_group_ad_asset_view.field_type = getattr(ads_client.enums.AssetFieldTypeEnum, field)
    r.asset.resource_name = f"customers/1234567890/assets/{asset}"
    r.asset.text_asset.text = kw.get("text", "")
    if "url" in kw:
        r.asset.image_asset.full_size.url = kw["url"]
    if "cta" in kw:
        r.asset.call_to_action_asset.call_to_action = getattr(ads_client.enums.CallToActionTypeEnum, kw["cta"])
    return r


def test_the_app_query_reads_the_four_app_kinds(ads_client, monkeypatch):
    seen = {}
    monkeypatch.setattr(insights.gads, "search", lambda client, cid, query: seen.update(query=query) or [])
    insights.fetch_rows(ads_client, "1234567890", ["Camp A"], 30)
    assert seen["query"].endswith(
        "AND ad_group_ad_asset_view.field_type IN ('HEADLINE', 'DESCRIPTION', 'MARKETING_IMAGE', 'YOUTUBE_VIDEO')")
    assert "call_to_action" not in seen["query"]


def test_the_demand_gen_mode_reads_every_demand_gen_kind(cfg, ads_client, monkeypatch):
    queries = []
    rows = [_dg_row(ads_client, "LONG_HEADLINE", 1, text="Join a friendly gym"),
            _dg_row(ads_client, "PORTRAIT_MARKETING_IMAGE", 2, url="https://img/2"),
            _dg_row(ads_client, "LOGO", 3, url="https://img/3"),
            _dg_row(ads_client, "CALL_TO_ACTION", 4, cta="SIGN_UP"),
            _dg_row(ads_client, "BUSINESS_NAME", 5, text="Pulse Fitness"),
            _dg_row(ads_client, "TALL_PORTRAIT_MARKETING_IMAGE", 6, url="https://img/6")]
    monkeypatch.setattr(insights.gads, "search", lambda client, cid, query: queries.append(query) or rows)
    res = insights.build_insights(ads_client, cfg, ["Camp A"], 30, 1000, False, mode="demand_gen")
    assert res["mode"] == "demand_gen"
    assert ("AND ad_group_ad_asset_view.field_type IN ('HEADLINE', 'LONG_HEADLINE', 'DESCRIPTION', 'BUSINESS_NAME', "
            "'CALL_TO_ACTION', 'CALL_TO_ACTION_SELECTION', 'MARKETING_IMAGE', 'SQUARE_MARKETING_IMAGE', "
            "'PORTRAIT_MARKETING_IMAGE', 'TALL_PORTRAIT_MARKETING_IMAGE', 'LOGO', 'YOUTUBE_VIDEO')") in queries[0]
    assert "asset.call_to_action_asset.call_to_action, " in queries[0]
    for kind in ("headlines", "long_headlines", "descriptions", "business_names", "calls_to_action", "landscape_images",
                 "square_images", "portrait_images", "tall_portrait_images", "logos", "videos"):
        assert set(res[kind]) == {"winners", "losers", "not_enough_data"}, kind
    assert res["long_headlines"]["winners"][0]["text"] == "Join a friendly gym"
    assert res["calls_to_action"]["winners"][0]["text"] == "SIGN_UP"
    assert res["logos"]["winners"][0]["image_url"] == "https://img/3"
    assert "images" not in res


def test_the_demand_gen_mode_downloads_every_image_kind(tmp_path, monkeypatch):
    fetched = []

    def fake(url, timeout=0):
        fetched.append(url)
        return _Resp(_png_bytes(100, 100))

    monkeypatch.setattr(insights.urllib.request, "urlopen", fake)
    item = lambda n, u: {"asset": f"customers/1/assets/{n}", "image_url": u, "youtube_id": ""}  # noqa: E731
    empty = {"winners": [], "losers": []}
    ranked = {"square_images": {"winners": [item(1, "https://x/1")], "losers": []},
              "logos": {"winners": [item(2, "https://x/2")], "losers": []}, "videos": empty, "headlines": empty}
    saved = insights.download_media(ranked, tmp_path / "f")
    assert [Path(p).name for p in saved] == ["square_images-winners-01-1.jpg", "logos-winners-01-2.jpg"]


def test_an_unknown_mode_or_field_type_is_refused(cfg, ads_client, monkeypatch):
    monkeypatch.setattr(insights.gads, "search", lambda *a: [])
    with pytest.raises(ValueError, match="mode must be one of app, demand_gen, pmax"):
        insights.build_insights(ads_client, cfg, ["Camp A"], 30, 1000, False, mode="shopping")
    with pytest.raises(ValueError, match="AssetFieldTypeEnum names"):
        insights.fetch_rows(ads_client, "1234567890", ["Camp A"], 30, ("HEADLINE') OR (1=1",))
