import json
from pathlib import Path

import grpc
import pytest

from gads_builder import gads, insights, insights_search, landing

CID = "1234567890"


class History:
    """Answers gads.search with fake Search history. Records the queries."""

    def __init__(self, ads_client, *, asset_metrics=True):
        self.client, self.E = ads_client, ads_client.enums
        self.asset_metrics = asset_metrics  # True, False (Google refuses them) or an exception to raise
        self.queries = []

    def _row(self, campaign="Springfield Gyms", group="Gym membership", impressions=0, clicks=0, conversions=0.0,
             cost=0):
        r = self.client.get_type("GoogleAdsRow")
        r.campaign.name, r.ad_group.name = campaign, group
        r.metrics.impressions, r.metrics.clicks = impressions, clicks
        r.metrics.conversions, r.metrics.cost_micros = conversions, cost
        return r

    def keywords(self):
        rows = []
        for text, match, score, impressions, clicks, conversions, cost in [
            ("gym membership", "PHRASE", 8, 2000, 120, 9.0, 90_000_000),
            ("cheap gym", "BROAD", 4, 900, 40, 0.0, 60_000_000),
            ("join a gym", "EXACT", 0, 30, 2, 0.0, 1_000_000),
        ]:
            r = self._row(impressions=impressions, clicks=clicks, conversions=conversions, cost=cost)
            crit = r.ad_group_criterion
            crit.keyword.text = text
            crit.keyword.match_type = getattr(self.E.KeywordMatchTypeEnum, match)
            crit.quality_info.quality_score = score
            crit.quality_info.search_predicted_ctr = self.E.QualityScoreBucketEnum.ABOVE_AVERAGE
            crit.quality_info.creative_quality_score = self.E.QualityScoreBucketEnum.AVERAGE
            crit.quality_info.post_click_quality_score = self.E.QualityScoreBucketEnum.BELOW_AVERAGE
            rows.append(r)
        return rows

    def assets(self, with_metrics):
        rows = []
        for n, (field, label, text, impressions, clicks) in enumerate([
            ("HEADLINE", "BEST", "Gym Membership Deals", 3000, 150),
            ("HEADLINE", "LOW", "Fitness for All", 2500, 20),
            ("HEADLINE", "GOOD", "Join Today", 50, 1),
            ("HEADLINE", "LEARNING", "Train Smarter", 40, 3),
            ("HEADLINE", "PENDING", "New Classes Weekly", 0, 0),
            ("DESCRIPTION", "GOOD", "Classes every day.", 3000, 90),
        ], 1):
            r = self._row(impressions=impressions if with_metrics else 0, clicks=clicks if with_metrics else 0)
            r.ad_group_ad_asset_view.field_type = getattr(self.E.AssetFieldTypeEnum, field)
            r.ad_group_ad_asset_view.performance_label = getattr(self.E.AssetPerformanceLabelEnum, label)
            r.asset.resource_name = f"customers/{CID}/assets/{n}"
            r.asset.text_asset.text = text
            rows.append(r)
        return rows

    def terms(self):
        rows = []
        for term, group, status, clicks, conversions, cost in [
            ("gym with pool springfield", "Gym membership", "NONE", 30, 4.0, 20_000_000),
            ("gym with pool springfield", "Yoga classes", "NONE", 10, 1.0, 5_000_000),  # same term, two ad groups
            ("24 hour gym", "Gym membership", "NONE", 12, 2.0, 9_000_000),
            ("gym jobs", "Gym membership", "NONE", 25, 0.0, 30_000_000),
            ("gym equipment for sale", "Gym membership", "NONE", 8, 0.0, 7_000_000),
            ("Gym Membership", "Gym membership", "NONE", 50, 5.0, 40_000_000),  # already a keyword (any case)
            ("pulse gym", "Gym membership", "ADDED", 5, 3.0, 2_000_000),
            ("free gym", "Gym membership", "EXCLUDED", 3, 0.0, 1_000_000),
            ("gym tour", "Gym membership", "NONE", 0, 0.0, 0),  # no cost, no conversion: in neither list
        ]:
            r = self._row(group=group, impressions=clicks * 10, clicks=clicks, conversions=conversions, cost=cost)
            r.search_term_view.search_term = term
            r.search_term_view.status = getattr(self.E.SearchTermTargetingStatusEnum, status)
            rows.append(r)
        return rows

    def __call__(self, client, customer_id, query):
        assert customer_id == CID
        self.queries.append(query)
        if " FROM keyword_view " in query:
            return self.keywords()
        if " FROM search_term_view " in query:
            return self.terms()
        if " FROM ad_group_ad_asset_view " in query:
            if "metrics." in query and isinstance(self.asset_metrics, BaseException):
                raise self.asset_metrics
            if "metrics." in query and not self.asset_metrics:
                raise gads.GoogleAdsError(["metrics are not available for this view"])
            return self.assets("metrics." in query)
        raise AssertionError(f"unexpected query {query}")


@pytest.fixture
def history(ads_client, monkeypatch):
    fake = History(ads_client)
    monkeypatch.setattr(insights_search.gads, "search", fake)
    return fake


def build(ads_client, cfg, campaigns=("Springfield Gyms",), days=90, min_impressions=100):
    return insights_search.build_search_insights(ads_client, cfg, list(campaigns), days, min_impressions)


def test_keywords_with_quality_score_and_its_parts(ads_client, cfg, history):
    res = build(ads_client, cfg)
    first = res["keywords"][0]
    assert first == {
        "campaign": "Springfield Gyms", "ad_group": "Gym membership", "keyword": "gym membership", "match": "PHRASE",
        "quality_score": 8, "expected_ctr": "ABOVE_AVERAGE", "ad_relevance": "AVERAGE",
        "landing_page": "BELOW_AVERAGE", "impressions": 2000, "clicks": 120, "conversions": 9.0, "ctr": 0.06,
        "cost": 90.0,
    }
    assert [k["keyword"] for k in res["keywords"]] == ["gym membership", "cheap gym", "join a gym"]  # by cost
    assert res["keywords"][2]["quality_score"] is None  # Google has no score yet
    assert [k["keyword"] for k in res["low_quality_score"]] == ["cheap gym"] and res["keywords_total"] == 3


def test_rsa_headlines_and_descriptions_are_ranked(ads_client, cfg, history):
    res = build(ads_client, cfg)
    assert res["asset_metrics"] is True
    assert [w["text"] for w in res["headlines"]["winners"]] == ["Gym Membership Deals"]
    assert [w["text"] for w in res["headlines"]["losers"]] == ["Fitness for All"]
    assert res["headlines"]["not_enough_data"] == 3  # "Join Today", LEARNING and PENDING: under 100 impressions
    assert res["asset_metrics_error"] is None
    assert [w["text"] for w in res["descriptions"]["winners"]] == ["Classes every day."]
    query = next(q for q in history.queries if " FROM ad_group_ad_asset_view " in q)
    assert "ad_group_ad.ad.type = 'RESPONSIVE_SEARCH_AD'" in query
    assert "field_type IN ('HEADLINE', 'DESCRIPTION')" in query and "segments.date BETWEEN" in query


def test_labels_only_when_google_refuses_asset_metrics(ads_client, cfg, history):
    history.asset_metrics = False
    res = build(ads_client, cfg)
    assert res["asset_metrics"] is False
    asset_queries = [q for q in history.queries if " FROM ad_group_ad_asset_view " in q]
    assert len(asset_queries) == 2 and "metrics." not in asset_queries[1] and "segments.date" not in asset_queries[1]
    assert [w["text"] for w in res["headlines"]["winners"]] == ["Gym Membership Deals", "Join Today"]
    assert [w["text"] for w in res["headlines"]["losers"]] == ["Fitness for All"]
    assert res["headlines"]["not_enough_data"] == 2  # LEARNING and PENDING: neither winners nor losers
    assert res["headlines"]["winners"][0]["impressions"] == 0
    assert res["asset_metrics_error"] == "metrics are not available for this view"


def _unreachable_with_context():
    try:
        raise grpc.RpcError("Stream removed")
    except grpc.RpcError as e:
        return gads._unreachable(e)  # as gads.search raises it: `from None`, the RpcError as its context


@pytest.mark.parametrize("failure", [_unreachable_with_context, lambda: gads._unreachable(TimeoutError("timed out"))])
def test_a_network_failure_is_not_taken_for_a_refusal(ads_client, cfg, history, failure):
    history.asset_metrics = failure()
    with pytest.raises(gads.GoogleAdsError, match="Couldn't reach Google Ads"):
        build(ads_client, cfg)
    assert len([q for q in history.queries if " FROM ad_group_ad_asset_view " in q]) == 1  # no labels-only retry


def test_the_daily_quota_is_not_taken_for_a_refusal(ads_client, cfg, history):
    from test_gads import QuotaRpcError

    history.asset_metrics = gads._unreachable(QuotaRpcError("Too many requests. Retry in 600 seconds."))
    with pytest.raises(gads.GoogleAdsError, match="daily Google Ads API quota is used up"):
        build(ads_client, cfg)
    assert len([q for q in history.queries if " FROM ad_group_ad_asset_view " in q]) == 1  # no labels-only retry


def test_a_long_refusal_is_cut(ads_client, cfg, history):
    history.asset_metrics = gads.GoogleAdsError(["x" * 400, "y" * 400])
    res = build(ads_client, cfg)
    assert res["asset_metrics"] is False and len(res["asset_metrics_error"]) == 500


def test_search_terms_become_keyword_and_negative_ideas(ads_client, cfg, history):
    res = build(ads_client, cfg)
    assert [t["term"] for t in res["keyword_ideas"]] == ["gym with pool springfield", "24 hour gym"]
    pool = res["keyword_ideas"][0]
    assert pool["conversions"] == 5.0 and pool["clicks"] == 40 and pool["cost"] == 25.0
    assert pool["ad_groups"] == ["Gym membership", "Yoga classes"]
    assert [t["term"] for t in res["negative_keyword_ideas"]] == ["gym jobs", "gym equipment for sale"]
    assert res["negative_keyword_ideas"][0]["cost"] == 30.0


def test_every_query_names_the_campaigns_and_the_dates(ads_client, cfg, history):
    build(ads_client, cfg, campaigns=("Springfield Gyms", "It's Gyms"), days=30)
    assert len(history.queries) == 3
    start, end = insights.date_range(30)
    for q in history.queries:
        assert "campaign.name IN ('Springfield Gyms', 'It\\'s Gyms')" in q
        assert f"segments.date BETWEEN '{start}' AND '{end}'" in q


def test_the_report_is_saved_in_a_new_folder(ads_client, cfg, history):
    a, b = build(ads_client, cfg), build(ads_client, cfg)
    assert a["saved_to"] != b["saved_to"]
    saved = Path(a["saved_to"])
    assert saved.name == "search-insights.json" and saved.parent.parent == cfg.insights_dir
    assert saved.parent.name.endswith("-search-springfield-gyms")
    assert json.loads(saved.read_text(encoding="utf-8"))["keyword_ideas"] == a["keyword_ideas"]


def test_inputs_are_clamped_and_checked(ads_client, cfg, history):
    assert build(ads_client, cfg, min_impressions=5)["min_impressions"] == 100
    assert build(ads_client, cfg, min_impressions=500)["min_impressions"] == 500
    assert build(ads_client, cfg, days=0)["days"] == 1 and build(ads_client, cfg, days=9999)["days"] == 365
    assert build(ads_client, cfg, campaigns=("Springfield Gyms", "Typo"))["missing_campaigns"] == ["Typo"]
    with pytest.raises(ValueError):
        build(ads_client, cfg, campaigns=(" ",))


def test_search_terms_compare_arabic_script_variants_as_one():
    def term(text, cost, conversions=0.0):
        return {"campaign": "Springfield Gyms", "ad_group": "Gym membership", "term": text, "status": "NONE",
                "impressions": 100, "clicks": 10, "conversions": conversions, "cost_micros": cost}

    terms = [term("عضويت باشگاه", 5_000_000, 1.0),  # Arabic yeh: already the keyword below
             term("كلاس يوگا", 2_000_000), term("کلاس‌یوگا", 3_000_000)]  # one term, two spellings
    lists = insights_search.term_lists(terms, [{"keyword": "عضویت باشگاه"}])
    assert lists["keyword_ideas"] == []
    [wasted] = lists["negative_keyword_ideas"]
    assert wasted["term"] == "كلاس يوگا" and wasted["cost"] == 5.0 and wasted["clicks"] == 20


def test_one_normalizer_for_terms_and_pages():
    assert insights_search._norm is landing._norm and not hasattr(insights_search, "_key")
