"""Reads the real account and uses Google's check-only mode. An autouse guard makes any
real change fail the test before it reaches Google. Run them yourself, outside Claude Code's command
sandbox, on a network that reaches Google's APIs (in PowerShell, set each variable first, such as
$env:GADS_LIVE = "1"):

GADS_LIVE=1 GADS_TEMPLATE_CAMPAIGN='<install campaign>' GADS_ENGAGEMENT_TEMPLATE='<engagement campaign>' \
  GADS_SEARCH_TEMPLATE='<search campaign>' uv run pytest -m live -v

Each template is optional: a test whose template isn't set is skipped. The Search tests need `domains`
in builder.toml to cover the template's final URLs.

The Demand Gen tests build a plan from these, and are skipped unless the first four are set:
  GADS_DG_FINAL_URL     a page on the allowed `domains`
  GADS_DG_YOUTUBE_ID    one of the account's YouTube videos (11 characters)
  GADS_DG_GOAL          a conversion goal of the account as CATEGORY~ORIGIN, such as PURCHASE~WEBSITE
  GADS_DG_LOCATION      geoTargetConstants/<id>
  GADS_DG_LANGUAGE      optional, languageConstants/<id> (default languageConstants/1000)
  GADS_DG_IMAGE_ASSET   optional, an existing 1.91:1 image asset (customers/<id>/assets/<id>), added to the image ad
  GADS_DG_USER_LIST     optional, customers/<id>/userLists/<id>, for the audience test
  GADS_DG_TEMPLATE      optional, a Demand Gen campaign to describe

The Performance Max tests build a plan the same way, and are skipped unless the first four are set:
  GADS_PMAX_FINAL_URL   a page on the allowed `domains`
  GADS_PMAX_YOUTUBE_ID  one of the account's YouTube videos (11 characters), 10 seconds or longer
  GADS_PMAX_GOAL        a website conversion goal of the account as CATEGORY~ORIGIN, such as PURCHASE~WEBSITE
  GADS_PMAX_LOCATION    geoTargetConstants/<id>
  GADS_PMAX_LANGUAGE    optional, languageConstants/<id> (default languageConstants/1000)
  GADS_PMAX_AUDIENCE    optional, an ENABLED account-wide audience (customers/<id>/audiences/<id>), for the signal test
  GADS_PMAX_TEMPLATE    optional, a Performance Max campaign to describe, copy, read the history of and read back
The Performance Max tests are few on purpose (a live run uses a large share of the daily API quota); run them alone
with -k pmax.
"""

import copy
import dataclasses
import functools
import math
import os
import time
from pathlib import Path

import pytest

from gads_builder import gads, server, templates
from gads_builder.config import load_config

pytestmark = pytest.mark.live
if os.environ.get("GADS_LIVE") != "1":
    pytest.skip("live tests need GADS_LIVE=1", allow_module_level=True)

INSTALL = os.environ.get("GADS_TEMPLATE_CAMPAIGN")
ENGAGEMENT = os.environ.get("GADS_ENGAGEMENT_TEMPLATE")
SEARCH = os.environ.get("GADS_SEARCH_TEMPLATE")
needs_install = pytest.mark.skipif(not INSTALL, reason="set GADS_TEMPLATE_CAMPAIGN")
needs_search = pytest.mark.skipif(not SEARCH, reason="set GADS_SEARCH_TEMPLATE")
DG = {name: os.environ.get(f"GADS_DG_{name}") for name in (
    "FINAL_URL", "YOUTUBE_ID", "GOAL", "LOCATION", "LANGUAGE", "IMAGE_ASSET", "USER_LIST", "TEMPLATE")}
needs_dg = pytest.mark.skipif(not all(DG[k] for k in ("FINAL_URL", "YOUTUBE_ID", "GOAL", "LOCATION")),
                              reason="set GADS_DG_FINAL_URL, GADS_DG_YOUTUBE_ID, GADS_DG_GOAL and GADS_DG_LOCATION")
PM = {name: os.environ.get(f"GADS_PMAX_{name}") for name in (
    "FINAL_URL", "YOUTUBE_ID", "GOAL", "LOCATION", "LANGUAGE", "AUDIENCE", "TEMPLATE")}
needs_pmax = pytest.mark.skipif(
    not all(PM[k] for k in ("FINAL_URL", "YOUTUBE_ID", "GOAL", "LOCATION")),
    reason="set GADS_PMAX_FINAL_URL, GADS_PMAX_YOUTUBE_ID, GADS_PMAX_GOAL and GADS_PMAX_LOCATION")
needs_pmax_template = pytest.mark.skipif(not PM["TEMPLATE"], reason="set GADS_PMAX_TEMPLATE")


@pytest.fixture(scope="module")
def live():
    cfg = load_config()
    return cfg, gads.client_from(str(cfg.credentials))


@pytest.fixture(autouse=True)
def check_only(monkeypatch, live, tmp_path):
    real_mutate = gads.mutate

    def guarded(client, cid, ops, labels, *, validate_only):
        assert validate_only is True, "live tests must never change the account"
        return real_mutate(client, cid, ops, labels, validate_only=True)

    monkeypatch.setattr(gads, "mutate", guarded)
    cfg, _ = live
    test_cfg = dataclasses.replace(cfg, plans_dir=tmp_path / "plans", insights_dir=tmp_path / "insights")
    monkeypatch.setattr(server, "load_config", lambda: test_cfg)


def _no_campaign_named(live, name):
    cfg, client = live
    rows = gads.search(client, cfg.customer_id, f"SELECT campaign.id FROM campaign WHERE campaign.name = {templates.quote(name)}")
    assert rows == [], "a campaign was created; that must never happen in tests"


def _check_only_copy(template, make_png, live, image_name):
    cfg, client = live
    tp = server.describe_campaign(template)["template_plan"]
    tp["name"] = f"ZZ check-only test {int(time.time())}"
    tp["daily_budget"] = min(tp["daily_budget"], cfg.daily_budget_cap)
    tp["images"] = tp["images"][:19] + [{"file": str(make_png(1200, 628, image_name))}]
    tp["brief"]["sources"] = {t: "proven" for t in tp["headlines"] + tp["descriptions"]}  # copied as-is
    # Label an existing image with Google's own asset.name, read the way check_plan reads it, so the
    # live name check covers assets too. Assets with an empty name get no label (see the playbook).
    assets = [i["asset"] for i in tp["images"] if i.get("asset")]
    google = templates.google_names(client, cfg.customer_id, assets)
    labelled = next((a for a in assets if a in google and google[a][0]), None)
    if labelled:
        tp["brief"]["names"][labelled] = google[labelled][0]
    out = server.draft_plan(tp)
    assert out["errors"] == [], out["errors"]
    assert not any(w.startswith("brief") for w in out["warnings"]), out["warnings"]
    if labelled:
        assert f"{google[labelled][0]} ({labelled})" in out["summary"]
    result = server.check_plan(out["plan_path"])
    assert result["passed"] is True, result
    assert result["approval"]["customer_id"] == cfg.customer_id and result["approval"]["over_cap"] is False
    _no_campaign_named(live, tp["name"])


@needs_install
def test_describe_install_template():
    tp = server.describe_campaign(INSTALL)["template_plan"]
    assert tp["subtype"] == "APP_CAMPAIGN" and tp["headlines"] and tp["locations"] and tp["languages"]


@needs_install
def test_check_only_install_copy(make_png, live):
    _check_only_copy(INSTALL, make_png, live, "live-landscape.png")


@pytest.mark.skipif(not ENGAGEMENT, reason="set GADS_ENGAGEMENT_TEMPLATE")
def test_check_only_engagement_copy(make_png, live):
    _check_only_copy(ENGAGEMENT, make_png, live, "live-landscape-eng.png")


@needs_install
def test_check_only_asset_update(make_png, live):
    cfg, client = live
    ad = next(a for a in server.describe_campaign(INSTALL)["ads"] if a["status"] == "ENABLED")
    base = {k: ad[k] for k in ("headlines", "descriptions", "images", "videos")}
    square = make_png(1200, 1200, "live-square.png")
    plan = {
        "kind": "asset_update", "ad": ad["ad"], "ad_type": ad["ad_type"], "base": base,
        "add_images": [{"file": str(square)}],
        "remove": base["images"][:1] if len(base["images"]) >= 20 else [],
    }
    out = server.draft_plan(plan)
    assert out["errors"] == [], out["errors"]
    result = server.check_plan(out["plan_path"])
    assert result["passed"] is True, result
    assert result["approval"]["added"] == [f"image: {square}"]
    assert result["approval"]["campaign_status"] in ("ENABLED", "PAUSED")
    assert templates.current_ad(client, cfg.customer_id, ad["ad"])[0] == base


def test_check_only_image_upload(make_png):
    out = server.draft_plan({"kind": "image_upload", "files": [str(make_png(1200, 1500, "live-portrait.png"))]})
    assert server.check_plan(out["plan_path"])["passed"] is True


@needs_install
def test_creative_insights_downloads_media():
    res = server.creative_insights([INSTALL], days=30, min_impressions=1000, download_images=True)
    assert res["headlines"]["winners"] or res["descriptions"]["winners"] or res["videos"]["winners"]
    assert res["downloaded"], "expected at least one image or video thumbnail"


@needs_install
def test_find_locations_round_trip():
    described = server.describe_campaign(INSTALL)
    location = described["template_plan"]["locations"][0]
    city = described["names"][location].split(",")[0]
    assert location in [r["resource"] for r in server.find_locations([city])]


@needs_install
def test_quality_report_reads_ad_strength():
    report = server.quality_report(INSTALL)
    assert report["ads"] and report["ads"][0]["ad_strength"]


# ------------------------------------------------------------------- Search (phase 2)


@needs_search
def test_check_only_search_copy(live):
    cfg, _ = live
    described = server.describe_campaign(SEARCH)
    tp = described["template_plan"]
    assert tp["kind"] == "search_campaign" and described["campaign"]["channel"] == "SEARCH"
    tp["name"] = f"ZZ check-only search test {int(time.time())}"
    tp["daily_budget"] = min(tp["daily_budget"], cfg.daily_budget_cap)
    tp["ad_groups"] = [g for g in tp["ad_groups"] if g["keywords"] and g["ad"]["headlines"]][:3]
    assert tp["ad_groups"], "the template has no ad group with keywords and an ad"
    texts = [t for g in tp["ad_groups"] for t in g["ad"]["headlines"] + g["ad"]["descriptions"]]
    tp["brief"]["sources"] = {t: "proven" for t in texts}  # copied as-is
    out = server.draft_plan(tp)
    assert out["errors"] == [], out["errors"]
    result = server.check_plan(out["plan_path"])
    assert result["passed"] is True, result
    assert result["approval"]["campaign_name"] == tp["name"] and result["approval"]["over_cap"] is False
    _no_campaign_named(live, tp["name"])


@needs_search
def test_search_insights_on_the_template():
    res = server.search_insights([SEARCH], days=30, min_impressions=100)
    assert res["missing_campaigns"] == [], res["missing_campaigns"]
    assert res["keywords"] and isinstance(res["asset_metrics"], bool)
    assert Path(res["saved_to"]).is_file()


@needs_search
def test_landing_pages_of_the_template():
    tp = server.describe_campaign(SEARCH)["template_plan"]
    urls = sorted({g["ad"]["final_url"] for g in tp["ad_groups"] if g["ad"]["final_url"]})[:5]
    keywords = [g["keywords"][0]["text"] for g in tp["ad_groups"] if g["keywords"]]
    results = server.check_landing_pages(urls, keywords)
    assert results and all("error" not in r for r in results), results
    assert all(r["status"] == 200 for r in results), results


@needs_search
def test_check_only_rsa_update(live):
    cfg, client = live
    ads = server.describe_campaign(SEARCH)["ads"]
    ad = next((a for a in ads if a["status"] == "ENABLED" and not templates.current_ad(client, cfg.customer_id, a["ad"])[1]),
              None)
    if ad is None:
        pytest.skip("the Search template has no enabled responsive search ad without pinned text")
    base = {k: ad[k] for k in ("headlines", "descriptions", "images", "videos")}
    plan = {"kind": "asset_update", "ad": ad["ad"], "ad_type": "RESPONSIVE_SEARCH_AD", "base": base}
    if len(base["headlines"]) < 15:
        plan["add_headlines"] = ["Check Only Test Headline"]
    else:
        plan["remove"] = base["headlines"][-1:]
    out = server.draft_plan(plan)
    assert out["errors"] == [], out["errors"]
    result = server.check_plan(out["plan_path"])
    assert result["passed"] is True, result
    assert result["approval"]["campaign_status"] in ("ENABLED", "PAUSED")
    assert templates.current_ad(client, cfg.customer_id, ad["ad"])[0] == base


# ------------------------------------------------------------------- Demand Gen (phase 3)


def _dg_plan(make_png, live, **over) -> dict:
    """A small Demand Gen plan around the GADS_DG_* settings: one ad group on all five channels, an image ad
    with new test images and a video ad with the YouTube video, at Google's minimum daily budget."""
    cfg, client = live
    minimum = gads.demand_gen_minimum_micros(client, cfg.customer_id, cfg.currency) / 1_000_000
    budget = math.ceil(minimum * 100) / 100
    if budget > cfg.daily_budget_cap:
        pytest.skip(f"Google's Demand Gen minimum ({budget}) is above daily_budget_cap")
    category, origin = DG["GOAL"].split("~")
    logo = {"file": str(make_png(1200, 1200, "live-dg-logo.png", color=(20, 20, 20)))}
    headlines = ["Check Only Test One", "Check Only Test Two", "Check Only Test Three", "Check Only Test Four",
                 "Check Only Test Five"]
    descriptions = [f"Check-only description number {n}, never created." for n in ("one", "two", "three", "four", "five")]
    landscape = [{"file": str(make_png(1200, 628, "live-dg-landscape.png"))}]
    if DG["IMAGE_ASSET"]:
        landscape.append({"asset": DG["IMAGE_ASSET"]})
    plan = {
        "kind": "demand_gen_campaign",
        "name": f"ZZ check-only demand gen test {int(time.time())}",
        "daily_budget": budget,
        "currency": cfg.currency,
        "bidding": "MAXIMIZE_CONVERSIONS",
        "conversion_goals": [{"category": category, "origin": origin}],
        "ad_groups": [{
            "name": "Check-only ad group",
            "locations": [DG["LOCATION"]],
            "languages": [DG["LANGUAGE"] or "languageConstants/1000"],
            "channels": ["youtube_in_stream", "youtube_in_feed", "youtube_shorts", "discover", "gmail"],
            "ads": [
                {"type": "image", "headlines": headlines, "descriptions": descriptions,
                 "business_name": "Check Only Test", "final_url": DG["FINAL_URL"], "marketing_images": landscape,
                 "square_marketing_images": [{"file": str(make_png(1200, 1200, "live-dg-square.png"))}],
                 "portrait_marketing_images": [{"file": str(make_png(960, 1200, "live-dg-portrait.png"))}],
                 "logo_images": [logo]},
                {"type": "video", "videos": [{"youtube_id": DG["YOUTUBE_ID"]}], "headlines": headlines,
                 "long_headlines": [f"{h}, a longer check-only headline" for h in headlines],
                 "descriptions": descriptions, "business_name": "Check Only Test", "final_url": DG["FINAL_URL"],
                 "logo_images": [logo]},
            ],
        }],
        "brief": {"sources": {h: "new" for h in headlines}},
    }
    plan.update(over)
    return plan


def _dg_check(plan: dict, live) -> dict:
    out = server.draft_plan(plan)
    assert out["errors"] == [], out["errors"]
    result = server.check_plan(out["plan_path"])
    _no_campaign_named(live, plan["name"])
    return result


@needs_dg
def test_demand_gen_live_reads(live):
    cfg, client = live
    goals = gads.customer_goals(client, cfg.customer_id)
    assert tuple(DG["GOAL"].split("~")) in goals, goals
    assert gads.demand_gen_minimum_micros(client, cfg.customer_id, cfg.currency) > 0


@needs_dg
def test_check_only_demand_gen_plan(make_png, live):
    """Settles, on the first run: conversion goal updates with the temporary campaign ID in the same request,
    every goal of the account updatable on a Demand Gen campaign (including app goals), the location option
    PRESENCE, exclude_demographic_expansion, selected channels, every generated-creative opt-out on both ad
    types, a YouTube video asset with a temporary ID, a budget exactly at Google's minimum, and the ad name on
    both ad types (Google refused a video ad without one)."""
    cfg, _ = live
    plan = _dg_plan(make_png, live)
    result = _dg_check(plan, live)
    assert result["passed"] is True, result
    assert result["approval"] == {"customer_id": cfg.customer_id, "fingerprint": result["fingerprint"],
                                  "campaign_name": plan["name"], "daily_budget": plan["daily_budget"],
                                  "over_cap": False}


@needs_dg
@pytest.mark.parametrize("bidding", ["TARGET_CPA", "MAXIMIZE_CLICKS", "MAXIMIZE_CONVERSIONS"])
def test_check_only_demand_gen_bidding(make_png, live, bidding):
    """Google (API v26) accepts these three for a NEW Demand Gen campaign: TARGET_CPA with a target, maximize
    clicks without a max CPC and maximize conversions without a target. It refuses (not allowed for the given
    context) maximize conversions WITH a target (maximize_conversions.target_cpa_micros) and maximize clicks WITH a
    max CPC (target_spend.cpc_bid_ceiling_micros), so the plan rules refuse those two."""
    plan = _dg_plan(make_png, live, bidding=bidding)
    if bidding == "TARGET_CPA":
        plan["target_cpa"] = max(0.01, math.floor(plan["daily_budget"] * 10) / 100)  # a tenth of the budget, in cents
    assert _dg_check(plan, live)["passed"] is True


@needs_dg
def test_check_only_demand_gen_image_headline_over_30(make_png, live):
    """Settles the image ad's headline limit: Help says 40, the v26 proto says 30. If Google refuses this
    35-character headline, lower the image ad's limit in plans_demand_gen (HEADLINE_LIMIT stays 40 for video)."""
    plan = _dg_plan(make_png, live)
    plan["ad_groups"][0]["ads"][0]["headlines"][4] = "Check Only Test Headline Of 35 Char"
    assert len(plan["ad_groups"][0]["ads"][0]["headlines"][4]) == 35
    assert _dg_check(plan, live)["passed"] is True


@needs_dg
def test_check_only_demand_gen_call_to_action(make_png, live):
    """Settles whether a CALL_TO_ACTION asset with a temporary ID works in the same request."""
    plan = _dg_plan(make_png, live)
    plan["ad_groups"][0]["ads"][1]["call_to_action"] = "LEARN_MORE"
    assert _dg_check(plan, live)["passed"] is True


@needs_dg
@pytest.mark.skipif(not DG["USER_LIST"], reason="set GADS_DG_USER_LIST")
def test_check_only_demand_gen_audience(make_png, live):
    """Settles whether a new Demand Gen ad group takes a plain user_list criterion (use_audience_grouped unset)."""
    plan = _dg_plan(make_png, live)
    plan["ad_groups"][0]["audiences"] = [DG["USER_LIST"]]
    assert _dg_check(plan, live)["passed"] is True


@pytest.mark.skipif(not DG["TEMPLATE"], reason="set GADS_DG_TEMPLATE")
def test_describe_demand_gen_template():
    """Also settles that the ad fields read only to note them are selectable: classic display images, companion
    banners, breadcrumbs, and the ad's tracking template, final URL suffix, custom parameters and mobile URLs."""
    described = server.describe_campaign(DG["TEMPLATE"])
    assert described["campaign"]["channel"] == "DEMAND_GEN"
    tp = described["template_plan"]
    assert tp["kind"] == "demand_gen_campaign" and tp["ad_groups"], described["notes"]
    assert isinstance(described["notes"], list)


def test_video_insights_on_recent_data():
    res = server.video_insights(days=30, download_thumbnails=False)
    lists = ("video_campaigns", "app_campaigns", "demand_gen_campaigns")
    assert all(set(res[k]) == {"winners", "losers", "not_enough_data"} for k in lists)
    assert any(res[k]["winners"] or res[k]["not_enough_data"] for k in lists), res
    assert Path(res["saved_to"]).is_file()


@needs_install
def test_creative_insights_demand_gen_mode_is_a_valid_query():
    res = server.creative_insights([INSTALL], days=30, download_images=False, mode="demand_gen")
    assert res["mode"] == "demand_gen" and "long_headlines" in res and "logos" in res


# ------------------------------------------------------------------- Performance Max (phase 4)
# Few tests, each settling several open points, because a live run uses a large share of the daily API quota. Run
# them alone with -k pmax. One describe_campaign read of GADS_PMAX_TEMPLATE serves every template test.


def _pmax_group(make_png, name: str, **over) -> dict:
    """One asset group around the GADS_PMAX_* settings, with new test images: 15 headlines, 5 long headlines,
    5 descriptions (one of 60 characters or fewer), the YouTube video and a search theme."""
    group = {
        "name": name,
        "final_url": PM["FINAL_URL"],
        "headlines": [f"Check Only Test {n:02d}" for n in range(1, 16)],
        "long_headlines": [f"Check-only long headline number {n}, never created" for n in ("one", "two", "three",
                                                                                           "four", "five")],
        "descriptions": ["Check-only short description.",
                         *(f"Check-only description number {n}, never created." for n in ("two", "three", "four",
                                                                                         "five"))],
        "marketing_images": [{"file": str(make_png(1200, 628, "live-pm-landscape.png"))}],
        "square_marketing_images": [{"file": str(make_png(1200, 1200, "live-pm-square.png"))}],
        "portrait_marketing_images": [{"file": str(make_png(960, 1200, "live-pm-portrait.png"))}],
        "videos": [{"youtube_id": PM["YOUTUBE_ID"]}],
        "search_themes": ["check only test theme"],
    }
    group.update(over)
    return group


_PMAX_BUDGET: dict = {}


def _pmax_plan(make_png, live, **over) -> dict:
    """A small Performance Max plan around the GADS_PMAX_* settings, with a new 1:1 logo and a new 4:1 landscape
    logo on the campaign. The budget is Google's Demand Gen minimum for the currency (Performance Max documents
    none): small, and accepted by Google for a new campaign."""
    cfg, client = live
    if "budget" not in _PMAX_BUDGET:  # read once per run
        minimum = gads.demand_gen_minimum_micros(client, cfg.customer_id, cfg.currency) / 1_000_000
        _PMAX_BUDGET["budget"] = math.ceil(minimum * 100) / 100
    budget = _PMAX_BUDGET["budget"]
    if budget > cfg.daily_budget_cap:
        pytest.skip(f"the test budget ({budget}) is above daily_budget_cap")
    category, origin = PM["GOAL"].split("~")
    plan = {
        "kind": "pmax_campaign",
        "name": f"ZZ check-only pmax test {int(time.time())}",
        "daily_budget": budget,
        "currency": cfg.currency,
        "bidding": "MAXIMIZE_CONVERSIONS",
        "conversion_goals": [{"category": category, "origin": origin}],
        "locations": [PM["LOCATION"]],
        "languages": [PM["LANGUAGE"] or "languageConstants/1000"],
        "business_name": "Check Only Test",
        "logos": [{"file": str(make_png(1200, 1200, "live-pm-logo.png", color=(20, 20, 20)))}],
        "landscape_logos": [{"file": str(make_png(1200, 300, "live-pm-wide-logo.png", color=(20, 20, 20)))}],
        "asset_groups": [_pmax_group(make_png, "Check-only asset group")],
        "brief": {"sources": {"Check Only Test 01": "new"}},
    }
    plan.update(over)
    return plan


def _pmax_check(plan: dict, live) -> dict:
    out = server.draft_plan(plan)
    assert out["errors"] == [], out["errors"]
    result = server.check_plan(out["plan_path"])
    _no_campaign_named(live, plan["name"])
    return result


@needs_pmax
def test_check_only_pmax_plan(make_png, live):
    """One request settles, on the first run: brand guidelines on with the business name and logos linked to the
    campaign (text and image assets with temporary IDs), a new 4:1 landscape logo, every asset automation opt-out at
    create (AUTOMATED_VIDEO_CRAWL, off by default, included), PRESENCE, the conversion goal updates on a temporary
    Performance Max campaign, two asset groups (every asset group before every link, sharing assets), a YouTube video
    asset by ID, a call to action, search themes and, with GADS_PMAX_AUDIENCE, an account-wide audience signal.
    One headline is the business name (a common real case): the compiler makes one text asset for both, so the same
    call settles whether Google takes one text asset linked as the campaign's BUSINESS_NAME and an asset group
    HEADLINE. Google reports every error in the request at once, each naming its part."""
    cfg, _ = live
    plan = _pmax_plan(make_png, live)
    plan["asset_groups"][0]["headlines"][0] = plan["business_name"]  # one asset, two field types
    second = _pmax_group(make_png, "Check-only asset group two", call_to_action="LEARN_MORE",
                         headlines=[f"Check Only Group Two {n:02d}" for n in range(1, 16)])
    if PM["AUDIENCE"]:
        second["audience_signal"] = PM["AUDIENCE"]
    plan["asset_groups"].append(second)
    result = _pmax_check(plan, live)
    assert result["passed"] is True, result
    assert result["approval"] == {"customer_id": cfg.customer_id, "fingerprint": result["fingerprint"],
                                  "campaign_name": plan["name"], "daily_budget": plan["daily_budget"],
                                  "over_cap": False}


@needs_pmax
@pytest.mark.parametrize("bidding, target, value", [("MAXIMIZE_CONVERSIONS", "target_cpa", None),
                                                   ("MAXIMIZE_CONVERSION_VALUE", "target_roas", 3.5)])
def test_check_only_pmax_target(make_png, live, bidding, target, value):
    """Settles whether a new Performance Max campaign takes a target: Google documents
    maximize_conversions.target_cpa_micros as Performance Max's target CPA, but refused that field on a new Demand
    Gen campaign. If Google refuses it, apply the fallback in the plan (no target for Performance Max)."""
    plan = _pmax_plan(make_png, live, bidding=bidding)
    plan[target] = value or max(0.01, math.floor(plan["daily_budget"] * 10) / 100)  # a tenth of the budget
    result = _pmax_check(plan, live)
    assert result["passed"] is True, result


@functools.lru_cache(maxsize=1)
def _pmax_template() -> dict:
    """GADS_PMAX_TEMPLATE described once per run: every template test reads this copy."""
    return server.describe_campaign(PM["TEMPLATE"])


@needs_pmax_template
def test_check_only_pmax_template_copy(make_png, live):
    """describe_campaign on a real Performance Max campaign, then its template_plan in check-only mode: its enabled
    asset groups (at most 5) with their own assets. A gap the template can't fill (no website goal, an asset group
    without a video, a final URL off the allowed domains) is filled from GADS_PMAX_* when set."""
    cfg, _ = live
    described = copy.deepcopy(_pmax_template())
    assert described["campaign"]["channel"] == "PERFORMANCE_MAX" and isinstance(described["notes"], list)
    tp = described["template_plan"]
    assert tp["kind"] == "pmax_campaign"
    tp["name"] = f"ZZ check-only pmax copy {int(time.time())}"
    tp["daily_budget"] = min(tp["daily_budget"] or cfg.daily_budget_cap, cfg.daily_budget_cap)
    if not tp["conversion_goals"] and PM["GOAL"]:
        category, origin = PM["GOAL"].split("~")
        tp["conversion_goals"] = [{"category": category, "origin": origin}]
    tp["asset_groups"] = tp["asset_groups"][:5]
    for group in tp["asset_groups"]:
        if not group["videos"] and PM["YOUTUBE_ID"]:
            group["videos"] = [{"youtube_id": PM["YOUTUBE_ID"]}]
        if not cfg.allows_url(group["final_url"]) and PM["FINAL_URL"]:
            group["final_url"] = PM["FINAL_URL"]
    tp["brief"]["sources"] = {t: "proven" for g in tp["asset_groups"] for t in g["headlines"]}
    out = server.draft_plan(tp)
    assert out["errors"] == [], (out["errors"], described["notes"])
    result = server.check_plan(out["plan_path"])
    _no_campaign_named(live, tp["name"])
    assert result["passed"] is True, result


@needs_pmax_template
def test_pmax_history_and_read_back_on_the_template():
    """creative_insights(mode="pmax") and quality_report's Performance Max part on the template: every query is
    accepted (a paused campaign may have no recent rows)."""
    res = server.creative_insights([PM["TEMPLATE"]], days=90, download_images=False, mode="pmax")
    assert res["mode"] == "pmax" and "long_headlines" in res and isinstance(res["search_categories"], list)
    assert res["missing_campaigns"] == [] and Path(res["saved_to"]).is_file()
    report = server.quality_report(PM["TEMPLATE"])
    assert len(report["pmax"]) == 1 and isinstance(report["pmax"][0]["to_review"], list)
