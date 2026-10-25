import dataclasses

import pytest

from gads_builder import plans
from gads_builder.plans_search import SearchCampaignPlan, bidding_label


def errors(findings):
    return [f"{f.field}: {f.message}" for f in findings if f.level == "error"]


def warnings(findings):
    return [f"{f.field}: {f.message}" for f in findings if f.level == "warning"]


def check(cfg, data):
    return plans.validate(plans.plan_from_dict(data), cfg)


def group(p, i=0):
    return p["ad_groups"][i]


def ad(p, i=0):
    return p["ad_groups"][i]["ad"]


def second_group(p, name="Yoga classes", keyword="yoga classes"):
    extra = dict(group(p), name=name, keywords=[{"text": keyword, "match": "PHRASE"}], negative_keywords=[])
    p["ad_groups"].append(extra)
    return extra


def test_the_fixture_plan_is_clean(search_cfg, search_plan):
    findings = check(search_cfg, search_plan())
    assert errors(findings) == [] and warnings(findings) == []


def test_a_domain_that_looks_like_a_public_suffix_is_a_warning(search_cfg, search_plan):
    cfg = dataclasses.replace(search_cfg, domains=("example.com", "co.xx"))
    findings = check(cfg, search_plan())
    assert errors(findings) == []
    assert warnings(findings) == ["domains: builder.toml allows 'co.xx', which looks like a public suffix: "
                                  "every site under it would be allowed"]


def test_plan_from_dict_builds_a_search_plan(search_plan):
    plan = plans.plan_from_dict(search_plan())
    assert isinstance(plan, SearchCampaignPlan) and plan.kind == "search_campaign"
    assert plan.ad_groups[0].keywords[0].text == "gym membership" and plan.ad_groups[0].ad.path2 == "join"
    assert plan.search_partners is False and plan.sitelinks[0].desc2 == "Book a spot in seconds"
    assert plans.plan_from_dict(plans.plan_to_dict(plan)) == plan


def test_save_and_load_roundtrip(search_cfg, search_plan):
    plan = plans.plan_from_dict(search_plan())
    path = plans.save_plan(plan, search_cfg, plans.validate(plan, search_cfg))
    assert path.parent.name.endswith("-springfield-gyms-search")
    again = plans.load_plan(path)
    assert again == plan
    saved = (path.parent / "summary.md").read_bytes().decode("utf-8")
    assert plans.render_summary(again, plans.validate(again, search_cfg), search_cfg) == saved


def test_texts_are_nfc_normalised(search_plan):
    data = search_plan()
    group(data)["keywords"][0]["text"] = "café gym"
    ad(data)["headlines"][0] = "Café Gym"
    plan = plans.plan_from_dict(data)
    assert plan.ad_groups[0].keywords[0].text == "café gym" and plan.ad_groups[0].ad.headlines[0] == "Café Gym"


@pytest.mark.parametrize("edit, message", [
    (lambda p: p.update(display_network=True), "the plan has unknown field(s): display_network"),
    (lambda p: ad(p).update(images=[]), "ad_groups[0].ad has unknown field(s): images"),
    (lambda p: p.update(search_partners="yes"), "search_partners must be true or false"),
    (lambda p: p.update(daily_budget="80"), "daily_budget must be a number"),
    (lambda p: p.update(daily_budget=True), "daily_budget must be a number"),
    (lambda p: p.update(target_cpa="5"), "target_cpa must be a number or null"),
    (lambda p: group(p).update(keywords="gym"), "ad_groups[0].keywords must be a list"),
    (lambda p: group(p).update(keywords=["gym"]), "ad_groups[0].keywords[0] must be an object with text, match"),
    (lambda p: p.update(impression_share=50), "impression_share must be an object with location, percent"),
    (lambda p: group(p).pop("ad"), "ad_groups[0]: missing field 'ad'"),
    (lambda p: p.pop("bidding"), "the plan: missing field 'bidding'"),
    (lambda p: (p.pop("bidding"), p.pop("locations")), "the plan: missing fields 'bidding', 'locations'"),
    (lambda p: p["sitelinks"][1].pop("url"), "sitelinks[1]: missing field 'url'"),
    (lambda p: p.update(brief=None), "brief must be an object"),
    (lambda p: p.update(callouts=[1]), "callouts[0] must be text"),
])
def test_bad_shapes_are_plan_errors(search_plan, edit, message):
    data = search_plan()
    edit(data)
    with pytest.raises(plans.PlanError) as e:
        plans.plan_from_dict(data)
    assert str(e.value) == message


ELEVEN_WORDS = "one two three four five six seven eight nine ten eleven"


@pytest.mark.parametrize("edit, expected", [
    (lambda p: p.update(name=" "), "name: is empty"),
    (lambda p: p.update(name="Gyms\nDaily budget: 1.00"), "name: contains line breaks"),
    (lambda p: p.update(template="Old\u202ecampaign"), "template: contains line breaks"),
    (lambda p: p.update(brief={"sources": {"Classes Every Day": "proven\n**x**"}}), "brief.sources['Classes Every Day']"),
    (lambda p: p.update(currency="USD"), "currency: the account uses EUR"),
    (lambda p: p.update(daily_budget=600.0), "daily_budget: 600.00 EUR is above the cap of 500.00"),
    (lambda p: p.update(daily_budget=1600.0, confirm_budget_over_cap=1600.0), "more than 3x the cap"),
    (lambda p: p.update(bidding="ENHANCED_CPC"), "bidding: must be one of MAXIMIZE_CONVERSIONS, TARGET_CPA"),
    (lambda p: p.update(bidding="TARGET_CPA"), "target_cpa: is required for TARGET_CPA bidding"),
    (lambda p: p.update(target_cpa=-1.0), "target_cpa: must be a positive number"),
    (lambda p: p.update(target_cpa=float("nan")), "target_cpa: must be a positive number"),
    (lambda p: p.update(bidding="MAXIMIZE_CLICKS", target_cpa=5.0), "target_cpa: is only used with MAXIMIZE_CONVERSIONS"),
    (lambda p: p.update(max_cpc=2.0), "max_cpc: is only used with MAXIMIZE_CLICKS"),
    (lambda p: p.update(bidding="MAXIMIZE_CLICKS", max_cpc=0), "max_cpc: must be a positive number"),
    (lambda p: p.update(bidding="MANUAL_CPC"), "max_cpc: is required for MANUAL_CPC bidding"),
    (lambda p: p.update(bidding="TARGET_IMPRESSION_SHARE", max_cpc=2.0), "impression_share: is required"),
    (lambda p: p.update(bidding="TARGET_IMPRESSION_SHARE", impression_share={"location": "TOP_OF_PAGE", "percent": 50}),
     "max_cpc: is required for TARGET_IMPRESSION_SHARE bidding"),
    (lambda p: p.update(bidding="TARGET_IMPRESSION_SHARE", max_cpc=2.0,
                        impression_share={"location": "SIDEBAR", "percent": 50}), "impression_share.location: must be one of"),
    (lambda p: p.update(bidding="TARGET_IMPRESSION_SHARE", max_cpc=2.0,
                        impression_share={"location": "TOP_OF_PAGE", "percent": 0}),
     "impression_share.percent: must be from 1 to 100"),
    (lambda p: p.update(impression_share={"location": "TOP_OF_PAGE", "percent": 50}),
     "impression_share: is only used with TARGET_IMPRESSION_SHARE bidding"),
    (lambda p: group(p).update(cpc_bid=1.0), "ad_groups[0].cpc_bid: is only used with MANUAL_CPC bidding"),
    (lambda p: (p.update(bidding="MANUAL_CPC", max_cpc=1.0), group(p).update(cpc_bid=-2)),
     "ad_groups[0].cpc_bid: must be a positive number"),
    (lambda p: p.update(locations=[]), "locations: add at least one location"),
    (lambda p: p.update(locations=["Springfield"]), "locations[0]: must look like geoTargetConstants/<id>"),
    (lambda p: p.update(languages=["English"]), "languages[0]: must look like languageConstants/<id>"),
    (lambda p: p.update(ad_groups=[]), "ad_groups: 0 given; use 1 to 20"),
    (lambda p: p.update(ad_groups=[dict(group(p), name=f"Group {i}") for i in range(21)]), "ad_groups: 21 given"),
    (lambda p: group(p).update(name=""), "ad_groups[0].name: is empty"),
    (lambda p: group(p).update(name="Gym\nmembership"), "ad_groups[0].name: contains line breaks"),
    (lambda p: group(p).update(name="x" * 256), "ad_groups[0].name: is 256 characters; the limit is 255"),
    (lambda p: second_group(p, name="GYM membership"), "ad_groups[1].name: duplicates ad_groups[0].name"),
    (lambda p: group(p).update(keywords=[]), "ad_groups[0].keywords: add at least one keyword"),
    (lambda p: group(p).update(keywords=[{"text": f"gym {i}", "match": "EXACT"} for i in range(101)]),
     "ad_groups[0].keywords: 101 given; the maximum is 100"),
    (lambda p: group(p)["keywords"][1].update(text="x" * 81),
     "ad_groups[0].keywords[1].text: is 81 characters; the limit is 80"),
    (lambda p: group(p)["keywords"][1].update(text=ELEVEN_WORDS),
     "ad_groups[0].keywords[1].text: has 11 words; the limit is 10"),
    (lambda p: group(p)["keywords"][1].update(text="gym, pool"), "ad_groups[0].keywords[1].text: may not contain ,"),
    (lambda p: group(p)["keywords"][1].update(text="gym [pool]"), "ad_groups[0].keywords[1].text: may not contain [ ]"),
    (lambda p: group(p)["keywords"][1].update(text="gym\tpool"), "ad_groups[0].keywords[1].text: contains line breaks"),
    (lambda p: group(p)["keywords"][1].update(text="gym  pool"), "ad_groups[0].keywords[1].text: has leading/trailing"),
    (lambda p: group(p)["keywords"][1].update(match="BROAD_MATCH"),
     "ad_groups[0].keywords[1].match: must be EXACT, PHRASE or BROAD"),
    (lambda p: (p.update(bidding="MAXIMIZE_CLICKS"), group(p)["keywords"][1].update(match="BROAD")),
     "ad_groups[0].keywords[1].match: broad match needs MAXIMIZE_CONVERSIONS or TARGET_CPA bidding"),
    (lambda p: group(p)["keywords"].append({"text": "GYM MEMBERSHIP", "match": "PHRASE"}),
     "ad_groups[0].keywords[5]: duplicates ad_groups[0].keywords[0]"),
    (lambda p: p["negative_keywords"].append({"text": "Join a Gym", "match": "EXACT"}),
     "negative_keywords[1]: 'Join a Gym' (EXACT) blocks the keyword 'join a gym' (ad_groups[0].keywords[3])"),
    (lambda p: group(p)["negative_keywords"].append({"text": "monthly gym pass", "match": "BROAD"}),
     "ad_groups[0].negative_keywords[1]: 'monthly gym pass' (BROAD) blocks the keyword 'monthly gym pass' "
     "(ad_groups[0].keywords[2])"),
    (lambda p: p["negative_keywords"].append({"text": "gym jobs", "match": "PHRASE"}),
     "negative_keywords[1]: duplicates negative_keywords[0]"),
    (lambda p: ad(p).update(headlines=ad(p)["headlines"][:2]),
     "ad_groups[0].ad.headlines: 2 given; a responsive search ad needs 3 to 15"),
    (lambda p: ad(p)["headlines"].append("Sixteenth Headline"),
     "ad_groups[0].ad.headlines: 16 given; a responsive search ad needs 3 to 15"),
    (lambda p: ad(p)["headlines"].__setitem__(3, "x" * 31), "ad_groups[0].ad.headlines[3]: is 31 characters"),
    (lambda p: ad(p)["headlines"].__setitem__(3, "Join Today!"), "ad_groups[0].ad.headlines[3]: headlines may not contain '!'"),
    (lambda p: ad(p)["headlines"].__setitem__(3, "Classes every day"),
     "ad_groups[0].ad.headlines[4]: duplicates ad_groups[0].ad.headlines[3]"),
    (lambda p: ad(p).update(descriptions=ad(p)["descriptions"][:1]),
     "ad_groups[0].ad.descriptions: 1 given; a responsive search ad needs 2 to 4"),
    (lambda p: ad(p)["descriptions"].append("A fifth description."),
     "ad_groups[0].ad.descriptions: 5 given; a responsive search ad needs 2 to 4"),
    (lambda p: ad(p)["descriptions"].__setitem__(0, "y" * 91), "ad_groups[0].ad.descriptions[0]: is 91 characters"),
    (lambda p: ad(p)["descriptions"].__setitem__(0, "Call +1 555 010 0199 to join."),
     "ad_groups[0].ad.descriptions[0]: phone numbers are not allowed"),
    (lambda p: ad(p).update(path1="", path2="join"), "ad_groups[0].ad.path2: needs path1"),
    (lambda p: ad(p).update(path1="x" * 16), "ad_groups[0].ad.path1: is 16 characters; the limit is 15"),
    (lambda p: ad(p).update(final_url="http://www.example.com/gym"),
     "ad_groups[0].ad.final_url: must be an https URL on an allowed domain (example.com)"),
    (lambda p: ad(p).update(final_url="https://www.example.net/gym"),
     "ad_groups[0].ad.final_url: must be an https URL on an allowed domain (example.com)"),
    (lambda p: ad(p).update(final_url=""), "ad_groups[0].ad.final_url: add an https URL on one of your domains"),
    (lambda p: ad(p).update(final_url="https://www.example.com/gym\n"), "ad_groups[0].ad.final_url: contains line breaks"),
    (lambda p: p["sitelinks"][0].update(text="x" * 26), "sitelinks[0].text: is 26 characters; the limit is 25"),
    (lambda p: p["sitelinks"][0].update(desc1="y" * 36), "sitelinks[0].desc1: is 36 characters; the limit is 35"),
    (lambda p: p["sitelinks"][1].update(desc2=""), "sitelinks[1]: give both desc1 and desc2, or neither"),
    (lambda p: p["sitelinks"][2].update(url="https://www.example.org/trial"), "sitelinks[2].url: must be an https URL"),
    (lambda p: p["sitelinks"][1].update(text="class schedule"), "sitelinks[1]: duplicates sitelinks[0]"),
    (lambda p: p.update(sitelinks=[dict(p["sitelinks"][0], text=f"Link {i}") for i in range(21)]),
     "sitelinks: 21 given; the maximum is 20"),
    (lambda p: p["callouts"].__setitem__(0, "x" * 26), "callouts[0]: is 26 characters; the limit is 25"),
    (lambda p: p["callouts"].__setitem__(1, "no joining fee"), "callouts[1]: duplicates callouts[0]"),
    (lambda p: p.update(callouts=[f"Callout {i}" for i in range(21)]), "callouts: 21 given; the maximum is 20"),
    (lambda p: p["snippets"][0].update(header=""), "snippets[0].header: is empty"),
    (lambda p: p["snippets"][0].update(header="   "), "snippets[0].header: is empty"),
    (lambda p: p["snippets"][0].update(header="x" * 26), "snippets[0].header: is 26 characters; the limit is 25"),
    (lambda p: p["snippets"][0].update(header="Gyms\u2028Types"), "snippets[0].header: contains line breaks"),
    (lambda p: p["snippets"][0].update(header="\u202eGyms"), "snippets[0].header: contains line breaks"),
    (lambda p: p["snippets"][0].update(values=["Yoga", "Spin"]), "snippets[0].values: 2 given; use 3 to 10"),
    (lambda p: p["snippets"][0]["values"].__setitem__(0, "x" * 26), "snippets[0].values[0]: is 26 characters"),
    (lambda p: p.update(snippets=p["snippets"] * 3), "snippets: 3 given; the maximum is 2"),
])
def test_rule_violations_are_errors(search_cfg, search_plan, edit, expected):
    data = search_plan()
    edit(data)
    found = errors(check(search_cfg, data))
    assert any(e.startswith(expected) or expected in e for e in found), found


@pytest.mark.parametrize("over", [
    {"bidding": "TARGET_CPA", "target_cpa": 12.0},
    {"bidding": "MAXIMIZE_CONVERSIONS", "target_cpa": 12.0},
    {"bidding": "MAXIMIZE_CLICKS"},
    {"bidding": "MAXIMIZE_CLICKS", "max_cpc": 1.5},
    {"bidding": "TARGET_IMPRESSION_SHARE", "max_cpc": 2.0, "impression_share": {"location": "TOP_OF_PAGE", "percent": 65}},
    {"bidding": "MANUAL_CPC", "max_cpc": 1.2},
    {"search_partners": True},
])
def test_every_bidding_choice_can_be_valid(search_cfg, search_plan, over):
    assert errors(check(search_cfg, search_plan(**over))) == []  # the fixture has no broad match


def test_manual_cpc_ad_groups_may_set_their_own_bid(search_cfg, search_plan):
    data = search_plan(bidding="MANUAL_CPC", max_cpc=1.2)
    group(data)["cpc_bid"] = 1.8
    assert errors(check(search_cfg, data)) == []


def test_broad_match_is_fine_with_smart_bidding(search_cfg, search_plan):
    for bidding, extra in (("MAXIMIZE_CONVERSIONS", {}), ("TARGET_CPA", {"target_cpa": 12.0})):
        data = search_plan(bidding=bidding, **extra)
        group(data)["keywords"][2]["match"] = "BROAD"
        assert errors(check(search_cfg, data)) == []


def test_a_negative_in_another_ad_group_may_route_traffic(search_cfg, search_plan):
    data = search_plan()
    second_group(data)["negative_keywords"] = [{"text": "gym membership", "match": "EXACT"}]
    assert errors(check(search_cfg, data)) == []


def test_keywords_may_name_a_competitor_but_ad_text_may_not(search_cfg, search_plan):
    cfg = dataclasses.replace(search_cfg, blocked_words=("Rival",))
    data = search_plan()
    group(data)["keywords"].append({"text": "rival gym membership", "match": "PHRASE"})
    assert errors(check(cfg, data)) == []
    for edit, field in [
        (lambda p: ad(p)["headlines"].__setitem__(5, "Better Than Rival"), "ad_groups[0].ad.headlines[5]"),
        (lambda p: ad(p)["descriptions"].__setitem__(1, "Cheaper than Rival gyms."), "ad_groups[0].ad.descriptions[1]"),
        (lambda p: ad(p).update(path2="rival"), "ad_groups[0].ad.path2"),
        (lambda p: p["sitelinks"][0].update(text="Rival Prices"), "sitelinks[0].text"),
        (lambda p: p["sitelinks"][0].update(desc2="Beat Rival today"), "sitelinks[0].desc2"),
        (lambda p: p["callouts"].__setitem__(0, "Not Rival"), "callouts[0]"),
        (lambda p: p["snippets"][0]["values"].__setitem__(0, "Rival Classes"), "snippets[0].values[0]"),
    ]:
        data = search_plan()
        edit(data)
        assert f"{field}: contains the blocked word 'Rival'" in errors(check(cfg, data)), field


@pytest.mark.parametrize("edit, expected", [
    (lambda p: ad(p)["headlines"].pop(), "ad_groups[0].ad.headlines: only 14 of 15; fill all 15 for the best Ad Strength"),
    (lambda p: ad(p)["descriptions"].pop(), "ad_groups[0].ad.descriptions: only 3 of 4"),
    (lambda p: group(p)["keywords"].pop(), "ad_groups[0].keywords: only 4; aim for 5 to 20 tightly related keywords"),
    (lambda p: group(p)["keywords"].extend({"text": f"gym near me {i}", "match": "EXACT"} for i in range(16)),
     "ad_groups[0].keywords: 21 keywords; more than 20 usually means several intents: split the ad group"),
    (lambda p: ad(p)["headlines"].__setitem__(1, "Flexible Plans"),
     "ad_groups[0].ad.headlines: the main keyword 'gym membership' is in 1 of the headlines; put it in 2 to 3"),
    (lambda p: ad(p).update(headlines=["Gym Membership Deals", "Gym Membership Today", *ad(p)["headlines"][:13]]),
     "ad_groups[0].ad.headlines: the main keyword 'gym membership' is in 4 of the headlines"),
    (lambda p: p["sitelinks"].pop(), "sitelinks: only 3; add at least 4 for expected CTR"),
    (lambda p: p["callouts"].pop(), "callouts: only 3; add at least 4 for expected CTR"),
    (lambda p: second_group(p, name="Pulse brand", keyword="pulse gym"),
     "ad_groups: 1 brand and 1 non-brand ad groups; keep brand keywords in a campaign of their own"),
    (lambda p: p.update(brief={}), "brief: no sources; mark each headline and description as proven or new"),
    (lambda p: p.update(bidding="TARGET_CPA", target_cpa=90.0), "target_cpa: is higher than the daily budget"),
    (lambda p: p.update(languages=[]), "languages: no language targeting"),
])
def test_quality_warnings(search_cfg, search_plan, edit, expected):
    data = search_plan()
    edit(data)
    findings = check(search_cfg, data)
    assert errors(findings) == [] and any(w.startswith(expected) for w in warnings(findings)), warnings(findings)


def test_an_all_brand_campaign_is_not_mixed(search_cfg, search_plan):
    data = search_plan()
    group(data)["keywords"][0]["text"] = "pulse gym membership"
    assert not any("brand" in w for w in warnings(check(search_cfg, data)))
    no_brand_words = dataclasses.replace(search_cfg, brand_words=())
    second_group(data, name="Pulse brand", keyword="pulse gym")
    assert not any("brand" in w for w in warnings(check(no_brand_words, data)))


def test_derived_budget_name_must_fit(search_cfg, search_plan):
    assert errors(check(search_cfg, search_plan(name="x" * 248))) == []
    found = errors(check(search_cfg, search_plan(name="x" * 249)))
    assert found == ["name: makes the budget name 256 characters; the limit is 255"]


def test_approval_args_match_the_app_campaign_ones(search_cfg, search_plan):
    plan = plans.plan_from_dict(search_plan())
    assert plans.approval_args(plan, search_cfg) == {"campaign_name": "Springfield Gyms - Search", "daily_budget": 80.0,
                                                     "over_cap": False}
    over = plans.plan_from_dict(search_plan(daily_budget=600, confirm_budget_over_cap=600))
    assert plans.approval_args(over, search_cfg)["over_cap"] is True


def test_the_summary_shows_everything_the_person_approves(search_cfg, search_plan):
    data = search_plan(brief={"sources": {"Gym Membership in Springfield": "proven", "Flexible Gym Membership": "new"},
                              "names": {"geoTargetConstants/1000001": "Springfield", "languageConstants/1000": "English"}})
    plan = plans.plan_from_dict(data)
    text = plans.render_summary(plan, plans.validate(plan, search_cfg), search_cfg)
    fp = plans.fingerprint(plan)[:12]
    for line in [
        "# Springfield Gyms - Search",
        f"**Plan fingerprint: {fp}**",
        "**Will be created PAUSED.** Nothing changes until the person approves.",
        "| Account | 1234567890 |",
        "| Type | Search campaign |",
        "| Bidding | Maximize conversions |",
        "| Networks | Google Search only; never the Display Network |",
        "| Daily budget | 80.00 EUR |",
        "| Locations | Springfield (geoTargetConstants/1000001) |",
        "| Languages | English (languageConstants/1000) |",
        "| Location option | people in or regularly in your locations |",
        "| AI Max | off |",
        "## Ad group 1: Gym membership",
        "- Final URL: https://www.example.com/gym",
        "- Display path: /gym/join",
        "### Keywords (5)",
        "- gym membership · PHRASE",
        "### Negative keywords (1)",
        "- free gym · EXACT",
        "### Headlines (15)",
        "1. Gym Membership in Springfield · 29/30 · proven",
        "2. Flexible Gym Membership · 23/30 · new",
        "### Descriptions (4)",
        "## Campaign negative keywords (1)",
        "- gym jobs · PHRASE",
        "## Sitelinks (4)",
        "1. Class Schedule · 14/25 → https://www.example.com/classes",
        "   - See every class this week · 25/35",
        "## Callouts (4)",
        "1. No Joining Fee · 14/25",
        "## Structured snippets (1)",
        "- Types: Yoga, Spin, Boxing, Strength",
        "## Approval arguments",
        "- campaign_name: Springfield Gyms - Search",
        "- daily_budget: 80.0",
        "- over_cap: false",
    ]:
        assert line in text.split("\n"), line


@pytest.mark.parametrize("over, label", [
    ({}, "Maximize conversions"),
    ({"target_cpa": 12.0}, "Maximize conversions, target cost per conversion 12.00 EUR"),
    ({"bidding": "TARGET_CPA", "target_cpa": 12.0}, "Target cost per conversion 12.00 EUR"),
    ({"bidding": "MAXIMIZE_CLICKS"}, "Maximize clicks"),
    ({"bidding": "MAXIMIZE_CLICKS", "max_cpc": 1.5}, "Maximize clicks, max CPC 1.50 EUR"),
    ({"bidding": "TARGET_IMPRESSION_SHARE", "max_cpc": 2.0,
      "impression_share": {"location": "ABSOLUTE_TOP_OF_PAGE", "percent": 65}},
     "Target impression share 65% absolute top of the page, max CPC 2.00 EUR"),
    ({"bidding": "MANUAL_CPC", "max_cpc": 1.2}, "Manual CPC, default bid 1.20 EUR"),
])
def test_bidding_labels(search_plan, over, label):
    assert bidding_label(plans.plan_from_dict(search_plan(**over))) == label


def test_summary_shows_search_partners_and_manual_bids(search_cfg, search_plan):
    data = search_plan(bidding="MANUAL_CPC", max_cpc=1.2, search_partners=True)
    second_group(data)["cpc_bid"] = 1.75
    plan = plans.plan_from_dict(data)
    lines = plans.render_summary(plan, plans.validate(plan, search_cfg), search_cfg).split("\n")
    assert "| Networks | Google Search and search partners; never the Display Network |" in lines
    assert lines.count("- Max CPC: 1.20 EUR") == 1 and lines.count("- Max CPC: 1.75 EUR") == 1
    assert "## Ad group 2: Yoga classes" in lines


def test_an_empty_plan_part_shows_none(search_cfg, search_plan):
    plan = plans.plan_from_dict(search_plan(sitelinks=[], callouts=[], snippets=[], negative_keywords=[]))
    text = plans.render_summary(plan, plans.validate(plan, search_cfg), search_cfg)
    assert "## Sitelinks (0)\n- none" in text and "## Structured snippets (0)\n- none" in text
    assert "## Campaign negative keywords (0)\n- none" in text


def test_the_plan_union_names_every_plan_kind():
    import typing

    names = {getattr(a, "__name__", None) or a.__forward_arg__ for a in typing.get_args(plans.Plan)}
    assert names == {"AppCampaignPlan", "ImageUploadPlan", "AssetUpdatePlan", "SearchCampaignPlan",
                     "DemandGenCampaignPlan", "PmaxCampaignPlan"}


# ------------------------------------------------------------------- money and currency


@pytest.mark.parametrize("edit, message", [
    (lambda p: p.update(daily_budget=10**400), "daily_budget is too large"),
    (lambda p: p.update(target_cpa=-10**400), "target_cpa is too large"),
    (lambda p: p.update(confirm_budget_over_cap=10**400), "confirm_budget_over_cap is too large"),
    (lambda p: p.update(bidding="TARGET_IMPRESSION_SHARE", max_cpc=2.0,
                        impression_share={"location": "TOP_OF_PAGE", "percent": 10**400}),
     "impression_share.percent is too large"),
    (lambda p: group(p).update(cpc_bid=10**400), "ad_groups[0].cpc_bid is too large"),
])
def test_an_amount_too_large_for_a_float_is_a_plan_error(search_plan, edit, message):
    data = search_plan()
    edit(data)
    with pytest.raises(plans.PlanError) as e:
        plans.plan_from_dict(data)
    assert str(e.value) == message


def test_amounts_are_floats_after_parsing(search_plan):
    plan = plans.plan_from_dict(search_plan(daily_budget=80, bidding="MANUAL_CPC", max_cpc=1))
    assert type(plan.daily_budget) is float and type(plan.max_cpc) is float


MANUAL = {"bidding": "MANUAL_CPC", "max_cpc": 1.2}


@pytest.mark.parametrize("over, expected", [
    ({"daily_budget": 1e13}, "daily_budget: 10,000,000,000,000.00 EUR is more than 3x the cap"),
    ({"daily_budget": 1e-9}, "daily_budget: must be at least 0.01"),
    ({"daily_budget": 12.345}, "daily_budget: must be in whole cents (at most 2 decimals)"),
    ({"target_cpa": 1e13}, "target_cpa: 10,000,000,000,000.00 is more than 3x the daily budget cap of 500.00"),
    ({"target_cpa": 1500.01}, "target_cpa: 1,500.01 is more than 3x the daily budget cap of 500.00"),
    ({"target_cpa": 1e-9}, "target_cpa: must be at least 0.01"),
    ({"bidding": "TARGET_CPA", "target_cpa": 12.345}, "target_cpa: must be in whole cents (at most 2 decimals)"),
    ({"bidding": "MAXIMIZE_CLICKS", "max_cpc": 1e13}, "max_cpc: is higher than the daily budget"),
    ({"bidding": "MAXIMIZE_CLICKS", "max_cpc": 80.01}, "max_cpc: is higher than the daily budget"),
    ({"bidding": "MANUAL_CPC", "max_cpc": 1e-9}, "max_cpc: must be at least 0.01"),
    ({"bidding": "TARGET_IMPRESSION_SHARE", "max_cpc": 12.345,
      "impression_share": {"location": "TOP_OF_PAGE", "percent": 50}}, "max_cpc: must be in whole cents"),
])
def test_money_must_be_whole_cents_and_within_limits(search_cfg, search_plan, over, expected):
    plan = plans.plan_from_dict(search_plan(**over))
    findings = plans.validate(plan, search_cfg)
    assert any(e.startswith(expected) for e in errors(findings)), errors(findings)
    assert "## Problems to fix" in plans.render_summary(plan, findings, search_cfg)  # no crash on the way


@pytest.mark.parametrize("bid, expected", [
    (1e13, "ad_groups[0].cpc_bid: is higher than the daily budget"),
    (80.01, "ad_groups[0].cpc_bid: is higher than the daily budget"),
    (1e-9, "ad_groups[0].cpc_bid: must be at least 0.01"),
    (12.345, "ad_groups[0].cpc_bid: must be in whole cents (at most 2 decimals)"),
])
def test_ad_group_bids_are_money_too(search_cfg, search_plan, bid, expected):
    data = search_plan(**MANUAL)
    group(data)["cpc_bid"] = bid
    assert expected in errors(check(search_cfg, data))


def test_the_limits_themselves_are_fine(search_cfg, search_plan):
    assert errors(check(search_cfg, search_plan(target_cpa=1500.0))) == []  # 3x the cap
    assert errors(check(search_cfg, search_plan(bidding="MAXIMIZE_CLICKS", max_cpc=80.0))) == []  # the budget
    data = search_plan(daily_budget=0.01, **dict(MANUAL, max_cpc=0.01))
    group(data)["cpc_bid"] = 0.01
    assert errors(check(search_cfg, data)) == []
    assert errors(check(search_cfg, search_plan(daily_budget=0.29, target_cpa=0.29))) == []  # 0.29 * 100 != 29.0


@pytest.mark.parametrize("currency", ["eur", "Eur", "EU", "EURO", "EU1"])
def test_the_currency_is_three_capital_letters(search_cfg, search_plan, currency):
    found = errors(check(search_cfg, search_plan(currency=currency)))
    assert found == ["currency: must be a 3-letter currency code in capitals, like EUR"], found


# ------------------------------------------------------------------- negatives that block keywords


def blocks(cfg, data):
    return [e for e in errors(check(cfg, data)) if " blocks the keyword " in e]


@pytest.mark.parametrize("negative, keyword", [
    ({"text": "GYM Membership", "match": "EXACT"}, "gym membership"),  # case
    ({"text": "\uff47ym membership", "match": "EXACT"}, "gym membership"),  # NFKC: a fullwidth g
    ({"text": "gym membership", "match": "PHRASE"}, "cheap gym membership"),  # contiguous, in order
    ({"text": "membership springfield", "match": "PHRASE"}, "gym membership springfield"),
    ({"text": "membership gym", "match": "BROAD"}, "cheap gym membership"),  # every word, any order
    ({"text": "pass", "match": "BROAD"}, "monthly gym pass"),
])
def test_a_campaign_negative_that_blocks_a_keyword_is_an_error(search_cfg, search_plan, negative, keyword):
    data = search_plan(negative_keywords=[negative])
    found = blocks(search_cfg, data)
    assert any(f"blocks the keyword {keyword!r}" in e and e.startswith("negative_keywords[0]: ") for e in found), found


@pytest.mark.parametrize("negative", [
    {"text": "gym membership", "match": "EXACT"},  # matches only the exact keyword "gym membership" ...
    {"text": "gym deals", "match": "PHRASE"},  # not contiguous in "gym membership deals"
    {"text": "membership gym", "match": "PHRASE"},  # the wrong order
    {"text": "gym pool", "match": "BROAD"},  # "pool" is in no keyword
    {"text": "gyms", "match": "BROAD"},  # a different word, not a part of one
    {"text": "member", "match": "PHRASE"},
])
def test_near_misses_do_not_block(search_cfg, search_plan, negative):
    data = search_plan(negative_keywords=[negative])
    group(data)["keywords"] = [{"text": "gym membership deals", "match": "PHRASE"},
                               {"text": "springfield gym membership offers", "match": "PHRASE"},
                               {"text": "monthly gym pass", "match": "PHRASE"},
                               {"text": "join a gym", "match": "PHRASE"},
                               {"text": "cheap gym membership", "match": "EXACT"}]
    group(data)["negative_keywords"] = []
    assert blocks(search_cfg, data) == []


def test_a_campaign_negative_is_checked_against_every_ad_group(search_cfg, search_plan):
    data = search_plan(negative_keywords=[{"text": "yoga", "match": "BROAD"}])
    second_group(data)
    assert blocks(search_cfg, data) == [
        "negative_keywords[0]: 'yoga' (BROAD) blocks the keyword 'yoga classes' (ad_groups[1].keywords[0])"]


def test_an_ad_group_negative_is_checked_against_its_own_ad_group_only(search_cfg, search_plan):
    data = search_plan()
    second_group(data)["negative_keywords"] = [{"text": "gym", "match": "BROAD"}]  # ad group 0 may keep "gym"
    assert blocks(search_cfg, data) == []
    group(data)["negative_keywords"] = [{"text": "gym membership", "match": "PHRASE"}]
    assert blocks(search_cfg, data) == [
        "ad_groups[0].negative_keywords[0]: 'gym membership' (PHRASE) blocks the keyword 'gym membership' "
        "(ad_groups[0].keywords[0])",
        "ad_groups[0].negative_keywords[0]: 'gym membership' (PHRASE) blocks the keyword "
        "'gym membership springfield' (ad_groups[0].keywords[1])",
        "ad_groups[0].negative_keywords[0]: 'gym membership' (PHRASE) blocks the keyword 'cheap gym membership' "
        "(ad_groups[0].keywords[4])",
    ]


def test_a_negative_with_an_unknown_match_type_blocks_nothing(search_cfg, search_plan):
    data = search_plan(negative_keywords=[{"text": "gym membership", "match": "SOMETIMES"}])
    assert blocks(search_cfg, data) == []


@pytest.mark.parametrize("ch", ["\u3164", "\u115f", "\u1160", "\uffa0", "\u2800", "\u200b"])
def test_campaign_and_ad_group_names_with_blank_or_invisible_characters_are_errors(search_cfg, search_plan, ch):
    data = search_plan(name=f"Springfield Gyms{ch}{ch}")
    group(data)["name"] = f"Gym{ch}membership"
    found = errors(check(search_cfg, data))
    assert "name: contains invisible characters" in found, found
    assert "ad_groups[0].name: contains invisible characters" in found, found


# ------------------------------------------------------------------- keyword characters


@pytest.mark.parametrize("text, expected", [
    ("gym\u200bmembership", "contains invisible characters"),  # zero-width space
    ("gym membership\u2060", "contains invisible characters"),  # word joiner (Cf)
    ("\ufeffgym membership", "contains invisible characters"),
    ("gym\u00admembership", "contains invisible characters"),  # soft hyphen (Cf)
    ('"gym membership"', 'may not contain "; set the match type instead of quotes or brackets'),
    ("+gym membership", "may not start with + or -"),
    ("-gym membership", "may not start with + or -"),
])
def test_keyword_characters(search_cfg, search_plan, text, expected):
    for put in (lambda p: group(p)["keywords"][1].update(text=text),
                lambda p: p["negative_keywords"][0].update(text=text)):
        data = search_plan()
        put(data)
        assert any(e.endswith(expected) for e in errors(check(search_cfg, data))), (text, errors(check(search_cfg, data)))


@pytest.mark.parametrize("text", ["gym\u200cmembership", "gym\u200dmembership", "gym-membership", "gym membership+",
                                  "24/7 gym", "gym & spa"])
def test_keyword_characters_that_are_fine(search_cfg, search_plan, text):
    data = search_plan()
    group(data)["keywords"][1]["text"] = text
    assert errors(check(search_cfg, data)) == []


# ------------------------------------------------------------------- caps


@pytest.mark.parametrize("edit, expected", [
    (lambda p: p.update(negative_keywords=[{"text": f"rival {i}", "match": "EXACT"} for i in range(501)]),
     "negative_keywords: 501 given; the maximum is 500"),
    (lambda p: group(p).update(negative_keywords=[{"text": f"rival {i}", "match": "EXACT"} for i in range(201)]),
     "ad_groups[0].negative_keywords: 201 given; the maximum is 200"),
    (lambda p: p.update(locations=[f"geoTargetConstants/{1000000 + i}" for i in range(201)]),
     "locations: 201 given; the maximum is 200"),
    (lambda p: p.update(languages=[f"languageConstants/{1000 + i}" for i in range(31)]),
     "languages: 31 given; the maximum is 30"),
])
def test_caps(search_cfg, search_plan, edit, expected):
    data = search_plan()
    edit(data)
    assert expected in errors(check(search_cfg, data))


def test_the_caps_themselves_are_fine(search_cfg, search_plan):
    data = search_plan(negative_keywords=[{"text": f"rival {i}", "match": "EXACT"} for i in range(500)],
                       locations=[f"geoTargetConstants/{1000000 + i}" for i in range(200)],
                       languages=[f"languageConstants/{1000 + i}" for i in range(30)])
    group(data)["negative_keywords"] = [{"text": f"rival {i}", "match": "EXACT"} for i in range(200)]
    assert errors(check(search_cfg, data)) == []


# ------------------------------------------------------------------- quality warnings


def test_no_structured_snippets_is_a_warning(search_cfg, search_plan):
    findings = check(search_cfg, search_plan(snippets=[]))
    assert errors(findings) == []
    assert warnings(findings) == ["snippets: none; add 1 or 2 structured snippets for expected CTR"]


TRANSLATED = ("snippets[0].header: not one of Google's English headers; Google's check will confirm it is a "
              "valid translated header")


@pytest.mark.parametrize("header", ["خدمات", "Gyms", "x" * 25])
def test_a_header_outside_the_english_list_is_a_warning(search_cfg, search_plan, header):
    """Google also accepts its translated headers (such as the Arabic for Services); check_plan asks Google."""
    data = search_plan()
    data["snippets"][0]["header"] = header
    findings = check(search_cfg, data)
    assert errors(findings) == [] and warnings(findings) == [TRANSLATED]


def test_a_translated_header_is_shown_quoted_on_one_line(search_cfg, search_plan):
    data = search_plan()
    data["snippets"][0]["header"] = "خدمات"
    plan = plans.plan_from_dict(data)
    lines = plans.render_summary(plan, plans.validate(plan, search_cfg), search_cfg).split("\n")
    assert "- 'خدمات': Yoga, Spin, Boxing, Strength" in lines


def test_duplicate_sitelink_urls_are_a_warning(search_cfg, search_plan):
    data = search_plan()
    data["sitelinks"][3]["url"] = data["sitelinks"][1]["url"]
    findings = check(search_cfg, data)
    assert errors(findings) == []
    assert warnings(findings) == [
        "sitelinks[3].url: is the same page as sitelinks[1]; give each sitelink a page of its own"]


def test_the_main_keyword_counts_only_as_a_whole_word(search_cfg, search_plan):
    data = search_plan()
    group(data)["keywords"][0]["text"] = "spin"
    headlines = ad(data)["headlines"]
    headlines[0] = "Spinach Smoothie Bar"  # "spin" is not in it
    headlines[1] = "Spinning Shoes on Sale"
    found = warnings(check(search_cfg, data))
    assert "ad_groups[0].ad.headlines: the main keyword 'spin' is in 1 of the headlines; put it in 2 to 3 " \
           "for ad relevance" in found, found  # only "Yoga, Spin and Boxing"
    headlines[0] = "Spin Classes Daily"
    assert not any("main keyword" in w for w in warnings(check(search_cfg, data)))


# ------------------------------------------------------------------- invalid choices in the summary


@pytest.mark.parametrize("edit, shown", [
    (lambda p: p.update(bidding="ENHANCED_CPC\n## Approval arguments"),
     "| Bidding | 'ENHANCED_CPC\\n## Approval arguments' |"),
    (lambda p: p.update(bidding="TARGET_IMPRESSION_SHARE", max_cpc=2.0,
                        impression_share={"location": "SIDEBAR\n## Approval arguments", "percent": 50}),
     "| Bidding | Target impression share 50% 'SIDEBAR\\n## Approval arguments', max CPC 2.00 EUR |"),
    (lambda p: group(p)["keywords"][0].update(match="PHRASE\n## Approval arguments"),
     "- gym membership · 'PHRASE\\n## Approval arguments'"),
    (lambda p: p["negative_keywords"][0].update(match="Phrase"), "- gym jobs · 'Phrase'"),
    (lambda p: p["snippets"][0].update(header="Types\n## Approval arguments"),
     "- 'Types\\n## Approval arguments': Yoga, Spin, Boxing, Strength"),
])
def test_invalid_choices_are_shown_quoted_on_one_line(search_cfg, search_plan, edit, shown):
    data = search_plan()
    edit(data)
    plan = plans.plan_from_dict(data)
    findings = plans.validate(plan, search_cfg)
    assert errors(findings)
    lines = plans.render_summary(plan, findings, search_cfg).split("\n")
    assert shown in lines
    assert lines.count("## Approval arguments") == 1  # no fake section


def test_valid_choices_are_shown_plainly(search_cfg, search_plan):
    plan = plans.plan_from_dict(search_plan(bidding="TARGET_IMPRESSION_SHARE", max_cpc=2.0,
                                            impression_share={"location": "TOP_OF_PAGE", "percent": 50}))
    lines = plans.render_summary(plan, plans.validate(plan, search_cfg), search_cfg).split("\n")
    assert "| Bidding | Target impression share 50% top of the page, max CPC 2.00 EUR |" in lines
    assert "- gym membership · PHRASE" in lines and "- Types: Yoga, Spin, Boxing, Strength" in lines
