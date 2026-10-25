import dataclasses
from pathlib import Path

import pytest

from gads_builder import plans, plans_pmax as pm
from gads_builder.plans_pmax import PmaxAssetGroup, PmaxCampaignPlan


def errors(findings):
    return [f"{f.field}: {f.message}" for f in findings if f.level == "error"]


def warnings(findings):
    return [f"{f.field}: {f.message}" for f in findings if f.level == "warning"]


def check(cfg, data):
    return plans.validate(plans.plan_from_dict(data), cfg)


def group(p):
    return p["asset_groups"][0]


def summary(cfg, data):
    plan = plans.plan_from_dict(data)
    return plans.render_summary(plan, plans.validate(plan, cfg), cfg)


def test_the_fixture_plan_is_clean(search_cfg, pmax_plan):
    findings = check(search_cfg, pmax_plan())
    assert errors(findings) == [] and warnings(findings) == []


def test_the_video_crawl_label_names_all_three_sources():
    assert pm.AUTOMATIONS["AUTOMATED_VIDEO_CRAWL"] == ("videos added from your landing page, social profiles and "
                                                      "YouTube channel")
    assert pm.AUTOMATIONS["AUTOMATED_VIDEO_CRAWL"] in pm.GENERATED_LABEL


def test_plan_from_dict_builds_a_pmax_plan(pmax_plan):
    plan = plans.plan_from_dict(pmax_plan())
    assert isinstance(plan, PmaxCampaignPlan) and plan.kind == "pmax_campaign"
    g = plan.asset_groups[0]
    assert isinstance(g, PmaxAssetGroup) and g.videos[0].youtube_id == "abcdefghijk"
    assert g.call_to_action is None and g.audience_signal is None and plan.landscape_logos == []
    assert plan.conversion_goals[0].category == "PURCHASE" and plan.target_roas is None
    assert plans.plan_from_dict(plans.plan_to_dict(plan)) == plan


def test_save_and_load_roundtrip(search_cfg, pmax_plan):
    plan = plans.plan_from_dict(pmax_plan())
    path = plans.save_plan(plan, search_cfg, plans.validate(plan, search_cfg))
    assert path.parent.name.endswith("-pulse-springfield-performance-max")
    assert plans.load_plan(path) == plan


@pytest.mark.parametrize("edit, message", [
    (lambda p: p.update(url_expansion=True), "the plan has unknown field(s): url_expansion"),
    (lambda p: group(p).update(final_urls=["https://www.example.com"]),
     "asset_groups[0] has unknown field(s): final_urls"),
    (lambda p: group(p).update(audience_signals=["customers/1234567890/audiences/5"]),
     "asset_groups[0] has unknown field(s): audience_signals"),
    (lambda p: group(p).pop("videos"), "asset_groups[0]: missing field 'videos'"),
    (lambda p: p.pop("business_name"), "the plan: missing field 'business_name'"),
    (lambda p: p.pop("conversion_goals"), "the plan: missing field 'conversion_goals'"),
    (lambda p: p.update(logos="logo.png"), "logos must be a list"),
    (lambda p: group(p).update(videos=[{"url": "x"}]), "asset_groups[0].videos[0] has unknown field(s): url"),
    (lambda p: group(p).update(search_themes="gym"), "asset_groups[0].search_themes must be a list"),
    (lambda p: p.update(target_roas="3.5"), "target_roas must be a number or null"),
    (lambda p: p.update(daily_budget="50"), "daily_budget must be a number"),
])
def test_parsing_is_strict(pmax_plan, edit, message):
    data = pmax_plan()
    edit(data)
    with pytest.raises(plans.PlanError) as e:
        plans.plan_from_dict(data)
    assert str(e.value) == message


def test_the_constants_match_googles_enums(ads_client):
    automation = {m.name for m in ads_client.enums.AssetAutomationTypeEnum}
    assert set(pm.AUTOMATIONS) <= automation and "GENERATE_IMAGE_EXTRACTION" not in pm.AUTOMATIONS
    campaign = ads_client.get_type("Campaign")
    fields = {f.name for f in type(campaign).pb(campaign).DESCRIPTOR.fields}
    assert {"maximize_conversions", "maximize_conversion_value", "brand_guidelines_enabled",
            "asset_automation_settings"} <= fields and "url_expansion_opt_out" not in fields
    group_fields = {f.name for f in type(ads_client.get_type("AssetGroup")).pb(ads_client.get_type("AssetGroup"))
                    .DESCRIPTOR.fields}
    assert {"name", "final_urls", "path1", "path2", "status", "campaign"} <= group_fields


# ------------------------------------------------------------------- campaign rules


@pytest.mark.parametrize("over, expected", [
    ({"bidding": "TARGET_CPA"}, ["bidding: must be one of MAXIMIZE_CONVERSIONS, MAXIMIZE_CONVERSION_VALUE"]),
    ({"bidding": "MAXIMIZE_CONVERSION_VALUE", "target_cpa": 5.0},
     ["target_cpa: is only used with MAXIMIZE_CONVERSIONS bidding"]),
    ({"target_roas": 3.5}, ["target_roas: is only used with MAXIMIZE_CONVERSION_VALUE bidding"]),
    ({"bidding": "MAXIMIZE_CONVERSION_VALUE", "target_roas": 0.0},
     ["target_roas: must be from 0.01 to 1000 (3.5 means a 350% return on ad spend)"]),
    ({"bidding": "MAXIMIZE_CONVERSION_VALUE", "target_roas": 1000.5},
     ["target_roas: must be from 0.01 to 1000 (3.5 means a 350% return on ad spend)"]),
    ({"bidding": "MAXIMIZE_CONVERSION_VALUE", "target_roas": 3.555},
     ["target_roas: must be a whole percent (at most 2 decimals, such as 3.5 for 350%)"]),
    ({"target_cpa": -1.0}, ["target_cpa: must be a positive number"]),
    ({"target_cpa": 2.555}, ["target_cpa: must be in whole cents (at most 2 decimals)"]),
    ({"target_cpa": 1600.0}, ["target_cpa: 1,600.00 is more than 3x the daily budget cap of 500.00"]),
    ({"daily_budget": 50.005}, ["daily_budget: must be in whole cents (at most 2 decimals)"]),
    ({"currency": "USD"}, ["currency: the account uses EUR"]),
    ({"daily_budget": 600.0},
     ["daily_budget: 600.00 EUR is above the cap of 500.00; the person must confirm this exact amount "
      "(confirm_budget_over_cap)"]),
    ({"name": " "}, ["name: is empty"]),
    ({"name": "Pulse\nBudget: 1"}, ["name: contains line breaks, control characters or bidi controls"]),
    ({"name": "Pulse\u3164Springfield"}, ["name: contains invisible characters"]),
    ({"locations": []}, ["locations: add at least one location"]),
    ({"locations": ["Springfield"]}, ["locations[0]: must look like geoTargetConstants/<id> (use find_locations)"]),
    ({"languages": ["English"]}, ["languages[0]: must look like languageConstants/<id>"]),
])
def test_campaign_errors(search_cfg, pmax_plan, over, expected):
    assert errors(check(search_cfg, pmax_plan(**over))) == expected


@pytest.mark.parametrize("over, expected", [
    ({"target_cpa": 60.0}, ["target_cpa: is higher than the daily budget; the budget may not cover a single conversion"]),
    ({"target_cpa": 20.0}, []),
    ({"bidding": "MAXIMIZE_CONVERSION_VALUE"}, []),
    ({"bidding": "MAXIMIZE_CONVERSION_VALUE", "target_roas": 3.5}, []),
    ({"languages": []}, ["languages: no language targeting: ads can show to users of all languages"]),
])
def test_bidding_and_targeting_warnings(search_cfg, pmax_plan, over, expected):
    findings = check(search_cfg, pmax_plan(**over))
    assert errors(findings) == [] and warnings(findings) == expected


@pytest.mark.parametrize("goals, expected", [
    ([], ['conversion_goals: list the goals to bid on, such as {"category": "PURCHASE", "origin": "WEBSITE"}']),
    ([{"category": "DOWNLOAD", "origin": "APP"}],
     ["conversion_goals[0].origin: app goals stay in App campaigns; Performance Max here promotes websites"]),
    ([{"category": "PURCHASES", "origin": "WEBSITE"}],
     ["conversion_goals[0].category: must be a conversion category such as PURCHASE, SUBMIT_LEAD_FORM or SIGNUP"]),
    ([{"category": "PURCHASE", "origin": "web"}],
     ["conversion_goals[0].origin: must be one of WEBSITE, GOOGLE_HOSTED, APP, CALL_FROM_ADS, STORE, "
      "YOUTUBE_HOSTED, LOCAL_SERVICES_ADS"]),
    ([{"category": "PURCHASE", "origin": "WEBSITE"}, {"category": "PURCHASE", "origin": "WEBSITE"}],
     ["conversion_goals[1]: duplicates conversion_goals[0]"]),
    ([{"category": "PURCHASE", "origin": "WEBSITE"}, {"category": "SUBMIT_LEAD_FORM", "origin": "WEBSITE"}], []),
])
def test_conversion_goals(search_cfg, pmax_plan, goals, expected):
    assert errors(check(search_cfg, pmax_plan(conversion_goals=goals))) == expected


# ------------------------------------------------------------------- the brand


@pytest.mark.parametrize("over, expected", [
    ({"business_name": "Pulse Fitness Springfield Gyms"}, ["business_name: is 30 characters; the limit is 25"]),
    ({"business_name": ""}, ["business_name: is empty"]),
    ({"business_name": "Pulse!"}, ["business_name: headlines may not contain '!' (Google editorial rule)"]),
    ({"business_name": "Pulse\u3164Fitness"}, ["business_name: contains invisible characters"]),
    ({"logos": []}, ["logos: 0 given; use 1 to 5"]),
    ({"logos": [{"asset": "customers/9999999999/assets/1"}]}, ["logos[0]: must be an asset of this account"]),
    ({"logos": [{"asset": f"customers/1234567890/assets/{i}"} for i in range(6)]}, ["logos: 6 given; use 1 to 5"]),
])
def test_brand_errors(search_cfg, pmax_plan, over, expected):
    assert errors(check(search_cfg, pmax_plan(**over))) == expected


def _existing(first: int, n: int) -> list[dict]:
    return [{"asset": f"customers/1234567890/assets/{i}"} for i in range(first, first + n)]


LOGO_LIMIT = "logos: {n} logos in all (logos and landscape_logos); Google's limit is 5 per campaign"


@pytest.mark.parametrize("logos, landscape, expected", [
    (5, 0, []), (4, 1, []), (1, 4, []),
    (5, 1, [LOGO_LIMIT.format(n=6)]), (3, 3, [LOGO_LIMIT.format(n=6)]), (1, 5, [LOGO_LIMIT.format(n=6)]),
    (6, 0, ["logos: 6 given; use 1 to 5"]),  # its own list's limit says it already
])
def test_at_most_five_logos_in_all(search_cfg, pmax_plan, logos, landscape, expected):
    """Google's Performance Max limit: 5 logos per campaign (brand guidelines), square and landscape together."""
    data = pmax_plan(logos=_existing(100, logos), landscape_logos=_existing(200, landscape))
    assert errors(check(search_cfg, data)) == expected


IMAGE_LIMIT = ("asset_groups[0]: has {n} images in all (landscape, square and portrait); Google's limit is 20 per "
               "asset group")


@pytest.mark.parametrize("landscape, square, portrait, expected", [
    (10, 5, 5, []), (18, 1, 1, []), (1, 19, 0, []),
    (10, 6, 5, [IMAGE_LIMIT.format(n=21)]), (19, 1, 1, [IMAGE_LIMIT.format(n=21)]), (20, 1, 0, [IMAGE_LIMIT.format(n=21)]),
    (7, 7, 7, [IMAGE_LIMIT.format(n=21)]),
    (21, 1, 1, ["asset_groups[0].marketing_images: 21 given; use 1 to 20"]),  # its own list's limit says it already
])
def test_at_most_twenty_images_in_all_per_asset_group(search_cfg, pmax_plan, landscape, square, portrait, expected):
    """Google's Performance Max limit: 20 images per asset group, the three shapes together."""
    data = pmax_plan()
    group(data).update(marketing_images=_existing(100, landscape), square_marketing_images=_existing(200, square),
                       portrait_marketing_images=_existing(300, portrait))
    assert errors(check(search_cfg, data)) == expected


def test_logos_take_their_own_shapes_and_googles_minimum_sizes(search_cfg, pmax_plan, make_png):
    data = pmax_plan(logos=[{"file": str(make_png(100, 100, "tiny-logo.png"))}],
                     landscape_logos=[{"file": str(make_png(1200, 300, "wide-logo.png"))},
                                      {"file": str(make_png(400, 100, "small-wide-logo.png"))},
                                      {"file": str(make_png(1200, 628, "not-a-logo.png"))}])
    assert errors(check(search_cfg, data)) == [
        "logos[0]: is 100x100; Google's minimum here is 128x128",
        "landscape_logos[1]: is 400x100; Google's minimum here is 512x128",
        "landscape_logos[2]: is 1200x628; a landscape logo is 4:1",
    ]


def test_a_landscape_logo_and_an_existing_logo_are_fine(search_cfg, pmax_plan, make_png):
    data = pmax_plan(landscape_logos=[{"file": str(make_png(1200, 300, "wide-logo.png"))},
                                      {"asset": "customers/1234567890/assets/9"}])
    findings = check(search_cfg, data)
    assert errors(findings) == [] and warnings(findings) == []
    assert "(new, 4:1)" in summary(search_cfg, data)


# ------------------------------------------------------------------- asset groups


def _group(**over):
    def edit(p):
        group(p).update(over)
    return edit


HEADLINES = ["Classes Every Day", "Join Pulse Fitness", "Train With Expert Coaches"]


@pytest.mark.parametrize("edit, expected", [
    (_group(name=""), ["asset_groups[0].name: is empty"]),
    (_group(name="x" * 129), ["asset_groups[0].name: is 129 characters; the limit is 128"]),
    (_group(name="Springfield\u2800members"), ["asset_groups[0].name: contains invisible characters"]),
    (_group(final_url="https://www.example.net/join"),
     ["asset_groups[0].final_url: must be an https URL on an allowed domain (example.com)"]),
    (_group(final_url=""), ["asset_groups[0].final_url: add an https URL on one of your domains"]),
    (_group(path1="", path2="join"), ["asset_groups[0].path2: needs path1"]),
    (_group(path1="springfield-gyms"), ["asset_groups[0].path1: is 16 characters; the limit is 15"]),
    (_group(headlines=HEADLINES[:2]), ["asset_groups[0].headlines: 2 given; use 3 to 15"]),
    (_group(headlines=[f"Headline {i}" for i in range(16)]), ["asset_groups[0].headlines: 16 given; use 3 to 15"]),
    (_group(headlines=HEADLINES[:2] + ["x" * 31]), ["asset_groups[0].headlines[2]: is 31 characters; the limit is 30"]),
    (_group(headlines=HEADLINES[:2] + ["Classes every day"]),
     ["asset_groups[0].headlines[2]: duplicates asset_groups[0].headlines[0]"]),
    (_group(long_headlines=[]), ["asset_groups[0].long_headlines: 0 given; use 1 to 5"]),
    (_group(long_headlines=["x" * 91]), ["asset_groups[0].long_headlines[0]: is 91 characters; the limit is 90"]),
    (_group(long_headlines=["Join today!"]),
     ["asset_groups[0].long_headlines[0]: headlines may not contain '!' (Google editorial rule)"]),
    (_group(descriptions=["Free parking at every club across Springfield."]),
     ["asset_groups[0].descriptions: 1 given; use 2 to 5"]),
    (_group(descriptions=["x" * 61, "y" * 70]),
     ["asset_groups[0].descriptions: add one of 60 characters or fewer (Google needs a short description)"]),
    (_group(marketing_images=[]), ["asset_groups[0].marketing_images: 0 given; use 1 to 20"]),
    (_group(square_marketing_images=[]), ["asset_groups[0].square_marketing_images: 0 given; use 1 to 20"]),
    (_group(square_marketing_images=[{"asset": "customers/9999999999/assets/1"}]),
     ["asset_groups[0].square_marketing_images[0]: must be an asset of this account"]),
    (_group(videos=[]), ["asset_groups[0].videos: add at least one of your YouTube videos: without one, Google "
                         "makes videos from the images and text"]),
    (_group(videos=[{"youtube_id": f"abcdefghi{i:02d}"} for i in range(16)]),
     ["asset_groups[0].videos: 16 given; use 1 to 15"]),
    (_group(videos=[{"youtube_id": "https://youtu.be/abcdefghijk"}]),
     ["asset_groups[0].videos[0]: is not a YouTube video ID (11 characters)"]),
    (_group(call_to_action="Learn more"),
     ["asset_groups[0].call_to_action: must be one of LEARN_MORE, GET_QUOTE, APPLY_NOW, SIGN_UP, CONTACT_US, "
      "SUBSCRIBE, DOWNLOAD, BOOK_NOW, SHOP_NOW, BUY_NOW, DONATE_NOW, ORDER_NOW, PLAY_NOW, SEE_MORE, START_NOW, "
      "VISIT_SITE, WATCH_NOW, or null to let Google choose"]),
    (_group(audience_signal="customers/1234567890/userLists/5"),
     ["asset_groups[0].audience_signal: must be an audience of this account (customers/<id>/audiences/<id>)"]),
    (_group(audience_signal="customers/9999999999/audiences/5"),
     ["asset_groups[0].audience_signal: must be an audience of this account (customers/<id>/audiences/<id>)"]),
    (lambda p: p.update(asset_groups=[]), ["asset_groups: 0 given; use 1 to 5"]),
])
def test_asset_group_errors(search_cfg, pmax_plan, edit, expected):
    data = pmax_plan()
    edit(data)
    assert errors(check(search_cfg, data)) == expected


@pytest.mark.parametrize("themes, expected", [
    (["gym"] * 1 + [f"theme {i}" for i in range(25)], ["asset_groups[0].search_themes: 26 given; the maximum is 25"]),
    (["x" * 81], ["asset_groups[0].search_themes[0]: is 81 characters; the limit is 80"]),
    (["a b c d e f g h i j k"], ["asset_groups[0].search_themes[0]: has 11 words; the limit is 10"]),
    (["gym", "GYM"], ["asset_groups[0].search_themes[1]: duplicates asset_groups[0].search_themes[0]"]),
    ([" gym"], ["asset_groups[0].search_themes[0]: has leading/trailing or double spaces"]),
    (["gym\nBudget: 1"], ["asset_groups[0].search_themes[0]: contains line breaks, control characters or bidi controls"]),
    (["gym\u200bclasses"], ["asset_groups[0].search_themes[0]: contains invisible characters"]),
    ([""], ["asset_groups[0].search_themes[0]: is empty"]),
    (["Rival gym membership"], []),  # like a keyword, a search theme is never shown: a competitor's name is allowed
])
def test_search_themes(search_cfg, pmax_plan, themes, expected):
    cfg = dataclasses.replace(search_cfg, blocked_words=("Rival",))
    assert errors(check(cfg, pmax_plan(asset_groups=[dict(group(pmax_plan()), search_themes=themes)]))) == expected


def test_fewer_texts_and_no_portrait_images_are_warnings(search_cfg, pmax_plan):
    data = pmax_plan()
    group(data).update(headlines=HEADLINES, descriptions=group(data)["descriptions"][3:],
                       portrait_marketing_images=[])
    findings = check(search_cfg, data)
    assert errors(findings) == []
    assert warnings(findings) == [
        "asset_groups[0].headlines: only 3 of 15; fill all 15 for the best Ad Strength",
        "asset_groups[0].descriptions: only 2 of 5; fill all 5 for the best Ad Strength",
        "asset_groups[0].portrait_marketing_images: no portrait images; cover all three shapes",
    ]


def test_each_image_list_takes_its_own_shape_and_googles_minimum_size(search_cfg, pmax_plan, make_png):
    data = pmax_plan()
    g = group(data)
    g["marketing_images"] = [{"file": g["square_marketing_images"][0]["file"]}]
    g["square_marketing_images"] = [{"file": str(make_png(250, 250, "tiny-square.png"))}]
    g["portrait_marketing_images"] = [{"file": str(make_png(400, 500, "small-portrait.png"))}]
    assert errors(check(search_cfg, data)) == [
        "asset_groups[0].marketing_images[0]: is a square image; this list takes landscape images",
        "asset_groups[0].square_marketing_images[0]: is 250x250; Google's minimum here is 300x300",
        "asset_groups[0].portrait_marketing_images[0]: is 400x500; Google's minimum here is 480x600",
    ]


def test_asset_group_names_are_unique_and_five_at_most(search_cfg, pmax_plan):
    data = pmax_plan()
    data["asset_groups"].append(dict(group(data), name="springfield  MEMBERS"))
    assert errors(check(search_cfg, data)) == ["asset_groups[1].name: duplicates asset_groups[0].name"]
    data["asset_groups"] = [dict(group(data), name=f"Group {i}") for i in range(6)]
    assert errors(check(search_cfg, data)) == ["asset_groups: 6 given; use 1 to 5"]


def test_a_call_to_action_an_audience_and_existing_assets(search_cfg, pmax_plan):
    data = pmax_plan()
    group(data).update(call_to_action="SIGN_UP", audience_signal="customers/1234567890/audiences/5",
                       videos=[{"asset": "customers/1234567890/assets/8"}],
                       marketing_images=[{"asset": "customers/1234567890/assets/7"}])
    findings = check(search_cfg, data)
    assert errors(findings) == [] and warnings(findings) == []


def test_blocked_words_are_checked_in_every_text(search_cfg, pmax_plan):
    cfg = dataclasses.replace(search_cfg, blocked_words=("Rival",))
    data = pmax_plan(business_name="Rival")
    group(data)["descriptions"][0] = "Better than Rival, with classes every day."
    group(data)["long_headlines"][0] = "Leave Rival for Pulse"
    group(data)["path2"] = "rival"
    assert errors(check(cfg, data)) == [
        "business_name: contains the blocked word 'Rival'",
        "asset_groups[0].path2: contains the blocked word 'Rival'",
        "asset_groups[0].long_headlines[0]: contains the blocked word 'Rival'",
        "asset_groups[0].descriptions[0]: contains the blocked word 'Rival'",
    ]


# ------------------------------------------------------------------- what gets created


def test_each_file_video_text_and_call_to_action_is_created_once(pmax_plan):
    data = pmax_plan()
    second = dict(group(data), name="Yoga fans", final_url="https://www.example.com/yoga", call_to_action="SIGN_UP",
                  headlines=["Yoga Every Day", *group(data)["headlines"][1:]])
    data["asset_groups"].append(second)
    group(data)["call_to_action"] = "SIGN_UP"
    plan = plans.plan_from_dict(data)
    assert [Path(f).name for f in pm.new_image_files(plan)] == [
        "pm-logo.png", "pm-landscape.png", "pm-square.png", "pm-portrait.png"]
    assert pm.new_youtube_ids(plan) == ["abcdefghijk"] and pm.calls_to_action(plan) == ["SIGN_UP"]
    texts = pm.text_assets(plan)
    assert texts[0] == "Pulse Fitness" and texts[-1] == "Yoga Every Day" and len(texts) == 1 + 25 + 1
    assert [p.name for p in plans._image_files(plan)] == ["pm-logo.png", "pm-landscape.png", "pm-square.png",
                                                          "pm-portrait.png"]


def test_derived_names_cover_the_budget_and_every_new_image_and_video(pmax_plan):
    plan = plans.plan_from_dict(pmax_plan())
    assert [(f, what) for f, what, _ in plans.derived_names(plan)] == [("name", "budget")] + [("asset_groups", "asset")] * 5
    assert plans.derived_names(plan)[-1][2] == "Pulse Springfield - Performance Max | YouTube abcdefghijk"


def test_a_long_name_makes_asset_names_too_long(search_cfg, pmax_plan):
    assert errors(check(search_cfg, pmax_plan(name="P" * 240))) == [
        f"asset_groups: makes the asset name {n} characters; the limit is 255" for n in (265, 270, 267, 269, 262)]


def test_the_fingerprint_covers_the_image_bytes(pmax_plan, make_png):
    data = pmax_plan()
    before = plans.fingerprint(plans.plan_from_dict(data))
    make_png(1200, 1200, "pm-logo.png", color=(1, 2, 3))  # same path, new bytes
    assert plans.fingerprint(plans.plan_from_dict(data)) != before


def test_new_images_over_20_mb_are_refused(search_cfg, pmax_plan, monkeypatch):
    monkeypatch.setattr(plans, "MAX_PLAN_IMAGE_BYTES", 1000)
    assert any(e.startswith("asset_groups: new images total") for e in errors(check(search_cfg, pmax_plan())))


def test_approval_arguments_are_a_new_campaigns(search_cfg, pmax_plan):
    plan = plans.plan_from_dict(pmax_plan(daily_budget=600.0, confirm_budget_over_cap=600.0))
    assert plans.approval_args(plan, search_cfg) == {"campaign_name": "Pulse Springfield - Performance Max",
                                                     "daily_budget": 600.0, "over_cap": True}


def test_window_counts(pmax_plan):
    data = pmax_plan()
    group(data)["marketing_images"].append({"asset": "customers/1234567890/assets/7"})
    group(data)["audience_signal"] = "customers/1234567890/audiences/5"
    assert pm.window_counts(plans.plan_from_dict(data)) == {
        "asset_groups": 1, "headlines": 15, "long_headlines": 5, "descriptions": 5, "new_images": 4,
        "existing_images": 1, "videos": 1, "search_themes": 2, "audience_signals": 1}


# ------------------------------------------------------------------- summary


def test_the_summary_shows_every_setting_and_asset(search_cfg, pmax_plan):
    data = pmax_plan(conversion_goals=[{"category": "PURCHASE", "origin": "WEBSITE"},
                                       {"category": "SUBMIT_LEAD_FORM", "origin": "WEBSITE"}])
    text = summary(search_cfg, data)
    for line in [
        "# Pulse Springfield - Performance Max",
        "**Will be created PAUSED.** Nothing changes until the person approves.",
        "| Type | Performance Max campaign (website) |",
        "| Bidding | Maximize conversions |",
        "| Daily budget | 50.00 EUR |",
        "| Conversion goals bid on | PURCHASE (WEBSITE), SUBMIT_LEAD_FORM (WEBSITE) |",
        "| Other conversion goals of the account | not bid on |",
        "| Locations | geoTargetConstants/1000001 |",
        "| Location option | people in or regularly in your locations |",
        "| Final URLs | only each asset group's own final URL (final URL expansion off) |",
        f"| Generated assets | {pm.GENERATED_LABEL} |",
        "| Videos made from images | not expected: every asset group has its own YouTube video (quality_report "
        "checks it after creation) |",
        "| Brand guidelines | on: the business name and logos belong to the campaign |",
        "## Brand",
        "- Business name: Pulse Fitness · 13/25",
        "### Logos (1:1) (1)",
        "### Landscape logos (4:1) (0)",
        "## Asset group 1: Springfield members",
        "- Final URL: https://www.example.com/join",
        "- Display path: /gym/join",
        "- Call to action: chosen by Google",
        "- Audience signal: none",
        "### Headlines (15)",
        "1. Classes Every Day · 17/30 · proven",
        "2. Join Pulse Fitness · 18/30 · new",
        "### Long headlines (5)",
        "### Descriptions (5)",
        "5. Free parking at every club across Springfield. · 46/90",
        "### Landscape images (1.91:1) (1)",
        "### Portrait images (4:5) (1)",
        "### Videos (1)",
        "- YouTube abcdefghijk",
        "### Search themes (2)",
        "- gym membership springfield",
        "## Approval arguments",
        "- campaign_name: Pulse Springfield - Performance Max",
        "- over_cap: false",
    ]:
        assert line in text.split("\n"), line
    assert text == summary(search_cfg, data)


def test_the_summary_names_resources_and_quotes_unknown_choices(search_cfg, pmax_plan):
    data = pmax_plan(conversion_goals=[{"category": "PURCHASE\n| Daily budget | 1", "origin": "WEBSITE"}],
                     brief={"sources": {}, "names": {"customers/1234567890/audiences/5": "Past members",
                                                     "geoTargetConstants/1000001": "Springfield"}})
    group(data).update(audience_signal="customers/1234567890/audiences/5", call_to_action="SIGN_UP")
    lines = summary(search_cfg, data).split("\n")
    assert "| Conversion goals bid on | 'PURCHASE\\n\\| Daily budget \\| 1' (WEBSITE) |" in lines
    assert "| Locations | Springfield (geoTargetConstants/1000001) |" in lines
    assert "- Audience signal: Past members (customers/1234567890/audiences/5)" in lines
    assert "- Call to action: SIGN_UP" in lines


@pytest.mark.parametrize("over, label", [
    ({}, "Maximize conversions"),
    ({"target_cpa": 2.5}, "Maximize conversions, target cost per conversion 2.50 EUR"),
    ({"bidding": "MAXIMIZE_CONVERSION_VALUE"}, "Maximize conversion value"),
    ({"bidding": "MAXIMIZE_CONVERSION_VALUE", "target_roas": 3.5}, "Maximize conversion value, target return on ad "
                                                                    "spend 350%"),
    ({"bidding": "TARGET_ROAS"}, "'TARGET_ROAS'"),
])
def test_bidding_label(pmax_plan, over, label):
    assert pm.bidding_label(plans.plan_from_dict(pmax_plan(**over))) == label
