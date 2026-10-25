import dataclasses
from pathlib import Path

import pytest

from gads_builder import plans, plans_demand_gen as dg
from gads_builder.plans_demand_gen import DemandGenCampaignPlan, ImageAd, VideoAd


def errors(findings):
    return [f"{f.field}: {f.message}" for f in findings if f.level == "error"]


def warnings(findings):
    return [f"{f.field}: {f.message}" for f in findings if f.level == "warning"]


def check(cfg, data):
    return plans.validate(plans.plan_from_dict(data), cfg)


def image_ad(p):
    return p["ad_groups"][0]["ads"][0]


def video_ad(p):
    return p["ad_groups"][0]["ads"][1]


def summary(cfg, data):
    plan = plans.plan_from_dict(data)
    return plans.render_summary(plan, plans.validate(plan, cfg), cfg)


def test_the_fixture_plan_is_clean(search_cfg, demand_gen_plan):
    findings = check(search_cfg, demand_gen_plan())
    assert errors(findings) == [] and warnings(findings) == []


def test_plan_from_dict_builds_a_demand_gen_plan(demand_gen_plan):
    plan = plans.plan_from_dict(demand_gen_plan())
    assert isinstance(plan, DemandGenCampaignPlan) and plan.kind == "demand_gen_campaign"
    group = plan.ad_groups[0]
    assert isinstance(group.ads[0], ImageAd) and isinstance(group.ads[1], VideoAd)
    assert group.ads[1].videos[0].youtube_id == "abcdefghijk" and group.ads[1].call_to_action is None
    assert plan.conversion_goals[0].category == "PURCHASE" and group.audiences == []
    assert plans.plan_from_dict(plans.plan_to_dict(plan)) == plan


def test_save_and_load_roundtrip(search_cfg, demand_gen_plan):
    plan = plans.plan_from_dict(demand_gen_plan())
    path = plans.save_plan(plan, search_cfg, plans.validate(plan, search_cfg))
    assert path.parent.name.endswith("-pulse-springfield-demand-gen")
    assert plans.load_plan(path) == plan


@pytest.mark.parametrize("edit, message", [
    (lambda p: p.update(display_network=True), "the plan has unknown field(s): display_network"),
    (lambda p: image_ad(p).update(call_to_action="LEARN_MORE"),
     "ad_groups[0].ads[0] has unknown field(s): call_to_action"),
    (lambda p: image_ad(p).pop("type"), 'ad_groups[0].ads[0].type must be "image" or "video"'),
    (lambda p: video_ad(p).update(type="carousel"), 'ad_groups[0].ads[1].type must be "image" or "video"'),
    (lambda p: p["ad_groups"][0].pop("channels"), "ad_groups[0]: missing field 'channels'"),
    (lambda p: p.pop("conversion_goals"), "the plan: missing field 'conversion_goals'"),
    (lambda p: image_ad(p).update(logo_images="logo.png"), "ad_groups[0].ads[0].logo_images must be a list"),
    (lambda p: video_ad(p).update(videos=[{"url": "x"}]), "ad_groups[0].ads[1].videos[0] has unknown field(s): url"),
    (lambda p: p["conversion_goals"].append({"category": "PURCHASE"}), "conversion_goals[1]: missing field 'origin'"),
    (lambda p: p.update(daily_budget="30"), "daily_budget must be a number"),
])
def test_parsing_is_strict(demand_gen_plan, edit, message):
    data = demand_gen_plan()
    edit(data)
    with pytest.raises(plans.PlanError) as e:
        plans.plan_from_dict(data)
    assert str(e.value) == message


def test_the_constants_match_googles_enums(ads_client):
    E = ads_client.enums

    def names(enum):
        return tuple(m.name for m in enum if m.name not in ("UNSPECIFIED", "UNKNOWN"))

    assert dg.CONVERSION_CATEGORIES == names(E.ConversionActionCategoryEnum)
    assert dg.CONVERSION_ORIGINS == names(E.ConversionOriginEnum)
    assert dg.CALLS_TO_ACTION == names(E.CallToActionTypeEnum)
    automation = set(names(E.AssetAutomationTypeEnum))
    assert all(set(types) <= automation for types in dg.GENERATED_CREATIVES.values())
    group = ads_client.get_type("AdGroup")
    channels = type(group).pb(group).demand_gen_ad_group_settings.channel_controls.selected_channels
    channel_fields = {f.name for f in channels.DESCRIPTOR.fields}
    assert set(dg.CHANNELS) | set(dg.NEVER_CHANNELS) == channel_fields


# ------------------------------------------------------------------- campaign rules


@pytest.mark.parametrize("over, expected", [
    ({"bidding": "TARGET_ROAS"}, ["bidding: must be one of MAXIMIZE_CONVERSIONS, TARGET_CPA, MAXIMIZE_CLICKS"]),
    ({"bidding": "TARGET_CPA"}, ["target_cpa: is required for TARGET_CPA bidding"]),
    ({"bidding": "MAXIMIZE_CLICKS", "target_cpa": 5.0}, ["target_cpa: is only used with TARGET_CPA bidding"]),
    ({"target_cpa": 2.5},
     ["target_cpa: Demand Gen doesn't accept a target cost per conversion with MAXIMIZE_CONVERSIONS; "
      "use bidding TARGET_CPA"]),
    ({"bidding": "MAXIMIZE_CLICKS", "max_cpc": 0.8},
     ["max_cpc: Demand Gen doesn't accept a maximum CPC with MAXIMIZE_CLICKS; leave max_cpc out"]),
    ({"max_cpc": 1.0}, ["max_cpc: is not used by Demand Gen bidding; leave it out"]),
    ({"bidding": "TARGET_CPA", "target_cpa": -1.0}, ["target_cpa: must be a positive number"]),
    ({"bidding": "TARGET_CPA", "target_cpa": 1600.0}, ["target_cpa: 1,600.00 is more than 3x the daily budget cap of 500.00"]),
    ({"daily_budget": 30.005}, ["daily_budget: must be in whole cents (at most 2 decimals)"]),
    ({"currency": "USD"}, ["currency: the account uses EUR"]),
    ({"daily_budget": 600.0},
     ["daily_budget: 600.00 EUR is above the cap of 500.00; the person must confirm this exact amount "
      "(confirm_budget_over_cap)"]),
    ({"name": " "}, ["name: is empty"]),
    ({"name": "Pulse\nBudget: 1"}, ["name: contains line breaks, control characters or bidi controls"]),
    ({"name": "Pulse\u3164Springfield"}, ["name: contains invisible characters"]),
])
def test_campaign_errors(search_cfg, demand_gen_plan, over, expected):
    assert errors(check(search_cfg, demand_gen_plan(**over))) == expected


@pytest.mark.parametrize("over, expected", [
    ({"bidding": "TARGET_CPA", "target_cpa": 40.0},
     ["target_cpa: is higher than the daily budget; the budget may not cover a single conversion"]),
    ({"bidding": "TARGET_CPA", "target_cpa": 5.0},
     ["daily_budget: Google recommends a Demand Gen daily budget of at least 15 times the target CPA"]),
    ({"bidding": "TARGET_CPA", "target_cpa": 2.0}, []),
    ({"bidding": "MAXIMIZE_CLICKS"}, []),
])
def test_bidding_warnings(search_cfg, demand_gen_plan, over, expected):
    findings = check(search_cfg, demand_gen_plan(**over))
    assert errors(findings) == [] and warnings(findings) == expected


@pytest.mark.parametrize("goals, expected", [
    ([], ['conversion_goals: list the goals to bid on, such as {"category": "PURCHASE", "origin": "WEBSITE"}']),
    ([{"category": "DOWNLOAD", "origin": "APP"}],
     ["conversion_goals[0].origin: app goals stay in App campaigns; Demand Gen here promotes websites"]),
    ([{"category": "PURCHASES", "origin": "WEBSITE"}],
     ["conversion_goals[0].category: must be a conversion category such as PURCHASE, SUBMIT_LEAD_FORM or SIGNUP"]),
    ([{"category": "PURCHASE", "origin": "web"}],
     ["conversion_goals[0].origin: must be one of WEBSITE, GOOGLE_HOSTED, APP, CALL_FROM_ADS, STORE, "
      "YOUTUBE_HOSTED, LOCAL_SERVICES_ADS"]),
    ([{"category": "PURCHASE", "origin": "WEBSITE"}, {"category": "PURCHASE", "origin": "WEBSITE"}],
     ["conversion_goals[1]: duplicates conversion_goals[0]"]),
    ([{"category": "PURCHASE", "origin": "WEBSITE"}, {"category": "SUBMIT_LEAD_FORM", "origin": "WEBSITE"}], []),
])
def test_conversion_goals(search_cfg, demand_gen_plan, goals, expected):
    assert errors(check(search_cfg, demand_gen_plan(conversion_goals=goals))) == expected


# ------------------------------------------------------------------- ad group rules


def _group(**over):
    def edit(p):
        p["ad_groups"][0].update(over)
    return edit


@pytest.mark.parametrize("edit, expected", [
    (_group(channels=["display"]),
     ["ad_groups[0].channels[0]: third-party Display is never used: Demand Gen stays on Google's own channels"]),
    (_group(channels=["discover", "maps"]), ["ad_groups[0].channels[1]: Maps is not offered by the builder"]),
    (_group(channels=["YOUTUBE_INSTREAM"]),
     ["ad_groups[0].channels[0]: must be one of youtube_in_stream, youtube_in_feed, youtube_shorts, discover, gmail"]),
    (_group(channels=[]),
     ["ad_groups[0].channels: choose at least one of youtube_in_stream, youtube_in_feed, youtube_shorts, discover, "
      "gmail"]),
    (_group(channels=["gmail", "gmail"]), ["ad_groups[0].channels[1]: duplicates ad_groups[0].channels[0]"]),
    (_group(locations=[]), ["ad_groups[0].locations: add at least one location"]),
    (_group(locations=["Springfield"]),
     ["ad_groups[0].locations[0]: must look like geoTargetConstants/<id> (use find_locations)"]),
    (_group(audiences=["customers/9999999999/userLists/5"]),
     ["ad_groups[0].audiences[0]: must be a user list of this account (customers/<id>/userLists/<id>)"]),
    (_group(audiences=["customers/1234567890/userLists/5", "customers/1234567890/userLists/5"]),
     ["ad_groups[0].audiences[1]: duplicates ad_groups[0].audiences[0]"]),
    (_group(audiences=[f"customers/1234567890/userLists/{n}" for n in range(51)]),
     ["ad_groups[0].audiences: 51 given; the maximum is 50"]),
    (_group(ads=[]), ["ad_groups[0].ads: 0 given; use 1 to 10"]),
    (_group(name=""), ["ad_groups[0].name: is empty"]),
    (_group(name="Springfield\u2800members"), ["ad_groups[0].name: contains invisible characters"]),
    (lambda p: p.update(ad_groups=[]), ["ad_groups: 0 given; use 1 to 20"]),
])
def test_ad_group_errors(search_cfg, demand_gen_plan, edit, expected):
    data = demand_gen_plan()
    edit(data)
    assert errors(check(search_cfg, data)) == expected


def test_ad_group_names_are_unique_and_languages_are_optional(search_cfg, demand_gen_plan):
    data = demand_gen_plan()
    data["ad_groups"].append(dict(data["ad_groups"][0], name="springfield  MEMBERS", languages=[]))
    findings = check(search_cfg, data)
    assert errors(findings) == ["ad_groups[1].name: duplicates ad_groups[0].name"]
    assert warnings(findings) == ["ad_groups[1].languages: no language targeting: ads can show to users of all languages"]


def test_too_many_ads_in_an_ad_group(search_cfg, demand_gen_plan):
    data = demand_gen_plan()
    data["ad_groups"][0]["ads"] = [video_ad(data)] * 11
    assert errors(check(search_cfg, data)) == ["ad_groups[0].ads: 11 given; use 1 to 10"]


# ------------------------------------------------------------------- the image ad


def _ad(index, **over):
    def edit(p):
        p["ad_groups"][0]["ads"][index].update(over)
    return edit


HEADLINE_35 = "Train With Expert Coaches Every Day"  # 35 characters


@pytest.mark.parametrize("edit, expected", [
    (_ad(0, headlines=[]), ["ad_groups[0].ads[0].headlines: 0 given; use 1 to 5"]),
    (_ad(0, headlines=["Join Today"] * 1 + ["A", "B", "C", "D", "E"]), ["ad_groups[0].ads[0].headlines: 6 given; use 1 to 5"]),
    (_ad(0, headlines=["Classes Every Day", "x" * 41]),
     ["ad_groups[0].ads[0].headlines[1]: is 41 characters; the limit is 40"]),
    (_ad(0, headlines=["Classes Every Day!"]),
     ["ad_groups[0].ads[0].headlines[0]: headlines may not contain '!' (Google editorial rule)"]),
    (_ad(0, headlines=["Classes Every Day", "classes every day"]),
     ["ad_groups[0].ads[0].headlines[1]: duplicates ad_groups[0].ads[0].headlines[0]"]),
    (_ad(0, descriptions=["x" * 91]), ["ad_groups[0].ads[0].descriptions[0]: is 91 characters; the limit is 90"]),
    (_ad(0, business_name="Pulse Fitness Springfield Gyms"),
     ["ad_groups[0].ads[0].business_name: is 30 characters; the limit is 25"]),
    (_ad(0, business_name=""), ["ad_groups[0].ads[0].business_name: is empty"]),
    (_ad(0, final_url="https://www.example.net/join"),
     ["ad_groups[0].ads[0].final_url: must be an https URL on an allowed domain (example.com)"]),
    (_ad(0, final_url=""), ["ad_groups[0].ads[0].final_url: add an https URL on one of your domains"]),
    (_ad(0, logo_images=[]), ["ad_groups[0].ads[0].logo_images: 0 given; use 1 to 5"]),
    (_ad(0, marketing_images=[], square_marketing_images=[]),
     ["ad_groups[0].ads[0].marketing_images: add a landscape or a square image (Google needs one)"]),
    (_ad(0, square_marketing_images=[{"asset": "customers/9999999999/assets/1"}]),
     ["ad_groups[0].ads[0].square_marketing_images[0]: must be an asset of this account"]),
    (_ad(0, square_marketing_images=[{"file": "dg-square.png"}]),
     ["ad_groups[0].ads[0].square_marketing_images[0]: use an absolute path (or ~/...)"]),
])
def test_image_ad_errors(search_cfg, demand_gen_plan, edit, expected):
    data = demand_gen_plan()
    edit(data)
    assert errors(check(search_cfg, data)) == expected


def test_a_headline_over_30_is_a_warning_until_google_says_otherwise(search_cfg, demand_gen_plan):
    data = demand_gen_plan()
    image_ad(data)["headlines"][4] = HEADLINE_35
    findings = check(search_cfg, data)
    assert errors(findings) == []
    assert warnings(findings) == [
        "ad_groups[0].ads[0].headlines[4]: is over 30 characters: Google Ads Help allows 40, the API documentation "
        "says 30; check_plan will tell"]
    image_ad(data)["headlines"] = [HEADLINE_35]
    assert "ad_groups[0].ads[0].headlines: add one of 30 characters or fewer, or Ad Strength shows Incomplete" in (
        warnings(check(search_cfg, data)))


def test_a_video_headline_over_30_is_fine(search_cfg, demand_gen_plan):
    data = demand_gen_plan()
    video_ad(data)["headlines"][4] = HEADLINE_35
    findings = check(search_cfg, data)
    assert errors(findings) == [] and warnings(findings) == []


def test_fewer_texts_and_missing_shapes_are_warnings(search_cfg, demand_gen_plan):
    data = demand_gen_plan()
    image_ad(data)["descriptions"] = image_ad(data)["descriptions"][:2]
    image_ad(data)["portrait_marketing_images"] = []
    findings = check(search_cfg, data)
    assert errors(findings) == []
    assert warnings(findings) == ["ad_groups[0].ads[0].descriptions: only 2 of 5; fill all 5 for the best Ad Strength",
                                  "ad_groups[0].ads[0]: no portrait images; cover all three shapes"]


def test_each_image_list_takes_its_own_shape_and_googles_minimum_size(search_cfg, demand_gen_plan, make_png):
    data = demand_gen_plan()
    ad = image_ad(data)
    ad["marketing_images"] = [{"file": ad["square_marketing_images"][0]["file"]}]
    ad["square_marketing_images"] = [{"file": str(make_png(250, 250, "tiny-square.png"))}]
    ad["portrait_marketing_images"] = [{"file": str(make_png(400, 500, "small-portrait.png"))}]
    ad["logo_images"] = [{"file": str(make_png(100, 100, "tiny-logo.png"))}]
    assert errors(check(search_cfg, data)) == [
        "ad_groups[0].ads[0].marketing_images[0]: is a square image; this list takes landscape images",
        "ad_groups[0].ads[0].square_marketing_images[0]: is 250x250; Google's minimum here is 300x300",
        "ad_groups[0].ads[0].portrait_marketing_images[0]: is 400x500; Google's minimum here is 480x600",
        "ad_groups[0].ads[0].logo_images[0]: is 100x100; Google's minimum here is 128x128",
    ]


def test_a_small_logo_above_googles_minimum_has_no_warning(search_cfg, demand_gen_plan, make_png):
    data = demand_gen_plan()
    logo = [{"file": str(make_png(200, 200, "small-logo.png"))}]
    image_ad(data)["logo_images"] = logo
    video_ad(data)["logo_images"] = logo
    findings = check(search_cfg, data)
    assert errors(findings) == [] and warnings(findings) == []


def test_at_most_20_images_of_all_three_shapes(search_cfg, demand_gen_plan, make_png):
    data = demand_gen_plan()
    ad = image_ad(data)
    ad["marketing_images"] = [{"file": str(make_png(1200, 628, f"l{i}.png"))} for i in range(10)]
    ad["square_marketing_images"] = [{"file": str(make_png(1200, 1200, f"s{i}.png"))} for i in range(6)]
    ad["portrait_marketing_images"] = [{"file": str(make_png(960, 1200, f"p{i}.png"))} for i in range(5)]
    assert errors(check(search_cfg, data)) == [
        "ad_groups[0].ads[0]: 21 landscape, square and portrait images; the maximum is 20"]


def test_existing_assets_are_trusted_for_their_shape(search_cfg, demand_gen_plan):
    data = demand_gen_plan()
    image_ad(data)["marketing_images"] = [{"asset": "customers/1234567890/assets/7"}]
    findings = check(search_cfg, data)
    assert errors(findings) == [] and warnings(findings) == []


# ------------------------------------------------------------------- the video ad


@pytest.mark.parametrize("edit, expected", [
    (_ad(1, videos=[]), ["ad_groups[0].ads[1].videos: 0 given; use 1 to 5"]),
    (_ad(1, videos=[{"youtube_id": f"abcdefghij{i}"} for i in range(6)]),
     ["ad_groups[0].ads[1].videos: 6 given; use 1 to 5"]),
    (_ad(1, videos=[{"youtube_id": "https://youtu.be/abcdefghijk"}]),
     ["ad_groups[0].ads[1].videos[0]: is not a YouTube video ID (11 characters)"]),
    (_ad(1, videos=[{"youtube_id": "abcdefghijk"}, {"youtube_id": "abcdefghijk"}]),
     ["ad_groups[0].ads[1].videos[1]: duplicate of ad_groups[0].ads[1].videos[0]"]),
    (_ad(1, long_headlines=["x" * 91]), ["ad_groups[0].ads[1].long_headlines[0]: is 91 characters; the limit is 90"]),
    (_ad(1, long_headlines=["Join today!"]),
     ["ad_groups[0].ads[1].long_headlines[0]: headlines may not contain '!' (Google editorial rule)"]),
    (_ad(1, call_to_action="Learn more"),
     ["ad_groups[0].ads[1].call_to_action: must be one of LEARN_MORE, GET_QUOTE, APPLY_NOW, SIGN_UP, CONTACT_US, "
      "SUBSCRIBE, DOWNLOAD, BOOK_NOW, SHOP_NOW, BUY_NOW, DONATE_NOW, ORDER_NOW, PLAY_NOW, SEE_MORE, START_NOW, "
      "VISIT_SITE, WATCH_NOW, or null to let Google choose"]),
    (_ad(1, logo_images=[]), ["ad_groups[0].ads[1].logo_images: 0 given; use 1 to 5"]),
    (_ad(1, business_name="Pulse!"),
     ["ad_groups[0].ads[1].business_name: headlines may not contain '!' (Google editorial rule)"]),
])
def test_video_ad_errors(search_cfg, demand_gen_plan, edit, expected):
    data = demand_gen_plan()
    edit(data)
    assert errors(check(search_cfg, data)) == expected


def test_a_call_to_action_and_an_existing_video(search_cfg, demand_gen_plan):
    data = demand_gen_plan()
    video_ad(data).update(call_to_action="SIGN_UP", videos=[{"asset": "customers/1234567890/assets/8"}])
    findings = check(search_cfg, data)
    assert errors(findings) == [] and warnings(findings) == []


def test_blocked_words_are_checked_in_every_text(search_cfg, demand_gen_plan):
    cfg = dataclasses.replace(search_cfg, blocked_words=("Rival",))
    data = demand_gen_plan()
    image_ad(data)["descriptions"][0] = "Better than Rival, with classes every day."
    video_ad(data)["long_headlines"][0] = "Leave Rival for Pulse"
    video_ad(data)["business_name"] = "Rival"
    assert errors(check(cfg, data)) == [
        "ad_groups[0].ads[0].descriptions[0]: contains the blocked word 'Rival'",
        "ad_groups[0].ads[1].long_headlines[0]: contains the blocked word 'Rival'",
        "ad_groups[0].ads[1].business_name: contains the blocked word 'Rival'",
    ]


# ------------------------------------------------------------------- what gets created


def test_a_file_used_in_several_ads_is_uploaded_once(demand_gen_plan):
    plan = plans.plan_from_dict(demand_gen_plan())
    files = dg.new_image_files(plan)
    assert [Path(f).name for f in files] == ["dg-landscape.png", "dg-square.png", "dg-portrait.png", "dg-logo.png"]
    assert dg.new_youtube_ids(plan) == ["abcdefghijk"] and dg.calls_to_action(plan) == []
    assert [p.name for p in plans._image_files(plan)] == [Path(f).name for f in files]


def test_derived_names_cover_the_budget_every_new_asset_and_every_ad(demand_gen_plan):
    plan = plans.plan_from_dict(demand_gen_plan())
    assert [(f, what) for f, what, _ in plans.derived_names(plan)] == [("name", "budget")] + [
        ("ad_groups", "asset")] * 5 + [("ad_groups[0].ads[0]", "ad"), ("ad_groups[0].ads[1]", "ad")]
    assert plans.derived_names(plan)[0][2] == "Pulse Springfield - Demand Gen budget"
    assert plans.derived_names(plan)[5][2] == "Pulse Springfield - Demand Gen | YouTube abcdefghijk"
    assert [name for _, what, name in plans.derived_names(plan) if what == "ad"] == [
        "Pulse Springfield - Demand Gen - Springfield members - image 1",
        "Pulse Springfield - Demand Gen - Springfield members - video 1"]


def test_ads_are_numbered_per_kind_within_each_ad_group(demand_gen_plan):
    data = demand_gen_plan()
    group = data["ad_groups"][0]
    group["ads"] = [group["ads"][1], group["ads"][0], group["ads"][1], group["ads"][0], group["ads"][0]]
    data["ad_groups"].append(dict(group, name="Yoga fans", ads=[group["ads"][1]]))
    assert dg.ad_names(plans.plan_from_dict(data)) == [
        [f"Pulse Springfield - Demand Gen - Springfield members - {kind}"
         for kind in ("video 1", "image 1", "video 2", "image 2", "image 3")],
        ["Pulse Springfield - Demand Gen - Yoga fans - image 1"],
    ]


def test_a_long_name_makes_asset_and_ad_names_too_long(search_cfg, demand_gen_plan):
    assert errors(check(search_cfg, demand_gen_plan(name="P" * 240))) == [
        f"ad_groups: makes the asset name {n} characters; the limit is 255" for n in (270, 267, 269, 265, 262)] + [
        "ad_groups[0].ads[0]: makes the ad name 272 characters; the limit is 255",
        "ad_groups[0].ads[1]: makes the ad name 272 characters; the limit is 255"]


def test_a_long_ad_group_name_makes_its_ad_names_too_long(search_cfg, demand_gen_plan):
    data = demand_gen_plan()
    data["ad_groups"][0]["name"] = "G" * 230
    assert errors(check(search_cfg, data)) == [
        f"ad_groups[0].ads[{a}]: makes the ad name 273 characters; the limit is 255" for a in (0, 1)]


def test_the_fingerprint_covers_the_image_bytes(demand_gen_plan, make_png):
    data = demand_gen_plan()
    before = plans.fingerprint(plans.plan_from_dict(data))
    make_png(1200, 1200, "dg-logo.png", color=(1, 2, 3))  # same path, new bytes
    assert plans.fingerprint(plans.plan_from_dict(data)) != before


def test_new_images_over_20_mb_are_refused(search_cfg, demand_gen_plan, monkeypatch):
    monkeypatch.setattr(plans, "MAX_PLAN_IMAGE_BYTES", 1000)
    assert any(e.startswith("ad_groups: new images total") for e in errors(check(search_cfg, demand_gen_plan())))


def test_approval_arguments_are_a_new_campaigns(search_cfg, demand_gen_plan):
    plan = plans.plan_from_dict(demand_gen_plan(daily_budget=600.0, confirm_budget_over_cap=600.0))
    assert plans.approval_args(plan, search_cfg) == {"campaign_name": "Pulse Springfield - Demand Gen",
                                                     "daily_budget": 600.0, "over_cap": True}


def test_window_counts(demand_gen_plan):
    data = demand_gen_plan()
    image_ad(data)["marketing_images"].append({"asset": "customers/1234567890/assets/7"})
    assert dg.window_counts(plans.plan_from_dict(data)) == {
        "image_ads": 1, "video_ads": 1, "new_images": 4, "existing_images": 1, "videos": 1}


# ------------------------------------------------------------------- summary


def test_the_summary_shows_every_setting_and_ad(search_cfg, demand_gen_plan):
    text = summary(search_cfg, demand_gen_plan(conversion_goals=[{"category": "PURCHASE", "origin": "WEBSITE"},
                                                                 {"category": "SUBMIT_LEAD_FORM", "origin": "WEBSITE"}]))
    for line in [
        "# Pulse Springfield - Demand Gen",
        "**Will be created PAUSED.** Nothing changes until the person approves.",
        "| Type | Demand Gen campaign (website) |",
        "| Bidding | Maximize conversions |",
        "| Daily budget | 30.00 EUR (check_plan compares it with Google's Demand Gen minimum) |",
        "| Conversion goals bid on | PURCHASE (WEBSITE), SUBMIT_LEAD_FORM (WEBSITE) |",
        "| Other conversion goals of the account | not bid on |",
        "| Targeting | location and language on each ad group (upgraded targeting) |",
        "| Location option | people in or regularly in your locations |",
        "| Third-party Display | never |",
        "| Optimized targeting | not turned on; demographic expansion excluded (quality_report reads both back) |",
        f"| Generated creatives | {dg.GENERATED_LABEL} |",
        "## Ad group 1: Springfield members",
        "- Channels: YouTube in-stream, YouTube in-feed, YouTube Shorts, Discover, Gmail",
        "- Locations: geoTargetConstants/1000001",
        "- Audiences: none",
        "### Ad 1: image ad",
        "- Ad name: Pulse Springfield - Demand Gen - Springfield members - image 1",
        "- Final URL: https://www.example.com/join",
        "- Business name: Pulse Fitness · 13/25",
        "#### Headlines (5)",
        "1. Classes Every Day · 17/40 · proven",
        "2. Join Pulse Fitness · 18/40 · new",
        "#### Landscape images (1.91:1) (1)",
        "#### Logos (1:1) (1)",
        "### Ad 2: video ad",
        "- Ad name: Pulse Springfield - Demand Gen - Springfield members - video 1",
        "- Call to action: chosen by Google",
        "#### Videos (1)",
        "- YouTube abcdefghijk",
        "#### Long headlines (5)",
        "## Approval arguments",
        "- campaign_name: Pulse Springfield - Demand Gen",
        "- over_cap: false",
    ]:
        assert line in text.split("\n"), line
    assert text.count("#### Logos (1:1) (1)") == 2 and "#### Square images (1:1) (1)" in text
    assert text == summary(search_cfg, demand_gen_plan(conversion_goals=[
        {"category": "PURCHASE", "origin": "WEBSITE"}, {"category": "SUBMIT_LEAD_FORM", "origin": "WEBSITE"}]))


def test_the_summary_names_resources_and_quotes_unknown_choices(search_cfg, demand_gen_plan):
    data = demand_gen_plan(conversion_goals=[{"category": "PURCHASE\n| Daily budget | 1", "origin": "WEBSITE"}],
                           brief={"sources": {}, "names": {"customers/1234567890/userLists/5": "Past members"}})
    data["ad_groups"][0].update(channels=["discover", "display\nBudget"], audiences=["customers/1234567890/userLists/5"])
    video_ad(data)["call_to_action"] = "SIGN_UP"
    lines = summary(search_cfg, data).split("\n")
    assert "| Conversion goals bid on | 'PURCHASE\\n\\| Daily budget \\| 1' (WEBSITE) |" in lines
    assert "- Channels: Discover, 'display\\nBudget'" in lines
    assert "- Audiences: Past members (customers/1234567890/userLists/5)" in lines
    assert "- Call to action: SIGN_UP" in lines


@pytest.mark.parametrize("over, label", [
    ({}, "Maximize conversions"),
    ({"bidding": "TARGET_CPA", "target_cpa": 2.0}, "Target cost per conversion 2.00 EUR"),
    ({"bidding": "MAXIMIZE_CLICKS"}, "Maximize clicks"),
    ({"bidding": "MANUAL_CPV"}, "'MANUAL_CPV'"),
])
def test_bidding_label(demand_gen_plan, over, label):
    assert dg.bidding_label(plans.plan_from_dict(demand_gen_plan(**over))) == label


def test_fifty_audiences_are_fine(search_cfg, demand_gen_plan):
    data = demand_gen_plan()
    data["ad_groups"][0]["audiences"] = [f"customers/1234567890/userLists/{n}" for n in range(50)]
    assert errors(check(search_cfg, data)) == []


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("name", ["Pulse\u3164Fitness", "Pulse\u2800Fitness", "Pulse\u00adFitness", "\u3164"])
def test_a_business_name_has_no_invisible_or_blank_looking_characters(search_cfg, demand_gen_plan, index, name):
    data = demand_gen_plan()
    data["ad_groups"][0]["ads"][index]["business_name"] = name
    assert f"ad_groups[0].ads[{index}].business_name: contains invisible characters" in errors(check(search_cfg, data))
