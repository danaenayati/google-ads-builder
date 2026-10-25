"""demand_gen_campaign plans through draft_plan, check_plan and create_campaign_from_plan."""

import json

import pytest

from gads_builder import checks, gads, server
from test_server import (CID, GOOGLE, PORTFOLIO, TEMP_IDS, checked, drafted, folder_of, names_search,  # noqa: F401
                         portfolio, real, renamed, wired, window)  # wired, window: fixtures

NAME, BUDGET = "Pulse Springfield - Demand Gen", 30.0
ACCOUNT_GOALS = (("DOWNLOAD", "APP"), ("PURCHASE", "WEBSITE"), ("SUBMIT_LEAD_FORM", "WEBSITE"))
LIST = "customers/1234567890/userLists/5"


@pytest.fixture
def dg_wired(wired, monkeypatch, search_cfg, ads_client):  # noqa: F811
    """The EUR account of the Search tests, with three conversion goals and a 5.00 Demand Gen minimum."""
    monkeypatch.setattr(server, "load_config", lambda: search_cfg)
    monkeypatch.setattr(gads, "account_currency", lambda client, cid: "EUR")
    monkeypatch.setattr(gads, "customer_goals", lambda client, cid: ACCOUNT_GOALS)
    monkeypatch.setattr(gads, "demand_gen_minimum_micros", lambda client, cid, currency: 5_000_000)
    known = dict(GOOGLE, **{LIST: ("user_list", {"name": "Past members"})})
    monkeypatch.setattr(gads, "search", names_search(ads_client, known=known))
    return wired


def create(out, **over):
    args = {"customer_id": CID, "fingerprint": out["fingerprint"], "campaign_name": NAME, "daily_budget": BUDGET,
            "over_cap": False, **over}
    return server.create_campaign_from_plan(out["plan_path"], **args)


def test_check_then_create_sends_one_paused_demand_gen_campaign(dg_wired, demand_gen_plan, ads_client):
    E = ads_client.enums
    out = drafted(demand_gen_plan())
    assert "| Type | Demand Gen campaign (website) |" in out["summary"]
    check = server.check_plan(out["plan_path"])
    assert check["passed"] is True and check["operations"] == 15
    assert check["approval"] == {"customer_id": CID, "fingerprint": out["fingerprint"], "campaign_name": NAME,
                                 "daily_budget": BUDGET, "over_cap": False}
    assert "campaign_status" not in check and "note" not in check
    assert create(out)["done"] is True
    [sent] = real(dg_wired)
    campaign = next(op.campaign_operation.create for op in sent if "campaign_operation" in op)
    assert campaign.status == E.CampaignStatusEnum.PAUSED
    assert campaign.advertising_channel_type == E.AdvertisingChannelTypeEnum.DEMAND_GEN
    goals = [op.campaign_conversion_goal_operation.update for op in sent if "campaign_conversion_goal_operation" in op]
    assert [(g.resource_name.rsplit("~", 2)[1:], g.biddable) for g in goals] == [
        (["DOWNLOAD", "APP"], False), (["PURCHASE", "WEBSITE"], True), (["SUBMIT_LEAD_FORM", "WEBSITE"], False)]
    record = json.loads((folder_of(out) / checks.CHECK_FILE).read_text(encoding="utf-8"))
    assert record["digest"] == gads.request_digest(CID, sent)
    again = create(out)
    assert again["done"] is False and "already applied" in again["errors"][0]
    assert len(real(dg_wired)) == 1


def test_the_window_shows_the_demand_gen_campaign(dg_wired, window, demand_gen_plan):  # noqa: F811
    out = checked(demand_gen_plan())
    assert create(out)["done"] is True
    [(_, lines)] = window.shown
    assert lines == [
        "Account: 1234567890",
        "Tool: create_campaign_from_plan (demand_gen_campaign)",
        f"Plan fingerprint: {out['fingerprint']}",
        'Campaign: "Pulse Springfield - Demand Gen" (created PAUSED)',
        "Daily budget: 30.00 EUR",
        "Bidding: Maximize conversions",
        "Conversion goals bid on:",
        "- PURCHASE (WEBSITE)",
        "Other account conversion goals, not bid on: 2",
        "Ad groups: 1, image ads: 1, video ads: 1",
        "New images: 4, existing images: 0, videos: 1",
        "Ad group 1: channels YouTube in-stream, YouTube in-feed, YouTube Shorts, Discover, Gmail; 1 location, "
        "1 language, 0 audiences",
        "Optimized targeting: not sent (Google's default may apply; quality_report checks it after creation)",
        "Never on third-party Display; generated creatives off",
    ]


def test_the_window_lists_every_goal_and_each_ad_groups_channels(dg_wired, window, demand_gen_plan,  # noqa: F811
                                                                 monkeypatch, ads_client):
    north = ("geo_target_constant", {"name": "North Springfield", "canonical_name": "North Springfield,Test Region"})
    known = dict(GOOGLE, **{LIST: ("user_list", {"name": "Past members"}), "geoTargetConstants/1000002": north})
    monkeypatch.setattr(gads, "search", names_search(ads_client, known=known))
    data = demand_gen_plan(conversion_goals=[{"category": "PURCHASE", "origin": "WEBSITE"},
                                             {"category": "SUBMIT_LEAD_FORM", "origin": "WEBSITE"}])
    data["ad_groups"].append(dict(data["ad_groups"][0], name="Yoga fans", channels=["youtube_shorts", "gmail"],
                                  locations=["geoTargetConstants/1000001", "geoTargetConstants/1000002"],
                                  languages=[], audiences=[LIST]))
    out = checked(data)
    assert create(out)["done"] is True
    lines = window.shown[0][1]
    assert lines[6:9] == ["Conversion goals bid on:", "- PURCHASE (WEBSITE)", "- SUBMIT_LEAD_FORM (WEBSITE)"]
    assert "Other account conversion goals, not bid on: 1" in lines
    assert lines[-4:] == [
        "Ad group 1: channels YouTube in-stream, YouTube in-feed, YouTube Shorts, Discover, Gmail; 1 location, "
        "1 language, 0 audiences",
        "Ad group 2: channels YouTube Shorts, Gmail; 2 locations, 0 languages, 1 audience",
        "Optimized targeting: not sent (Google's default may apply; quality_report checks it after creation)",
        "Never on third-party Display; generated creatives off"]


def test_the_live_checks(dg_wired, demand_gen_plan, monkeypatch):
    lead = drafted(demand_gen_plan(conversion_goals=[{"category": "SIGNUP", "origin": "WEBSITE"}]))
    assert server.check_plan(lead["plan_path"]) == {
        "passed": False, "errors": ["conversion_goals[0]: SIGNUP (WEBSITE) is not a conversion goal of this account"]}
    monkeypatch.setattr(gads, "demand_gen_minimum_micros", lambda client, cid, currency: 50_000_000)
    small = drafted(demand_gen_plan())
    assert server.check_plan(small["plan_path"]) == {
        "passed": False, "errors": ["daily_budget: Google's smallest Demand Gen daily budget is 50.00 EUR"]}
    monkeypatch.setattr(gads, "account_currency", lambda client, cid: "USD")
    assert server.check_plan(small["plan_path"])["errors"][0] == "currency: the account uses USD, the plan says EUR"
    assert dg_wired == []


def test_audiences_and_their_names_are_checked_against_google(dg_wired, demand_gen_plan):
    data = demand_gen_plan(brief={"sources": {"Classes Every Day": "proven"}, "names": {LIST: "Past members"}})
    data["ad_groups"][0]["audiences"] = [LIST]
    out = checked(data)
    assert "- Audiences: Past members (customers/1234567890/userLists/5)" in out["summary"]
    data["ad_groups"][0]["audiences"] = ["customers/1234567890/userLists/6"]
    data["brief"]["names"] = {}
    missing = drafted(data)
    assert server.check_plan(missing["plan_path"])["errors"] == [
        "customers/1234567890/userLists/6: not found in Google Ads"]


def test_a_failed_live_read_blocks_the_check(dg_wired, demand_gen_plan, monkeypatch):
    def unreachable(client, cid):
        raise gads.GoogleAdsError(["Couldn't reach Google Ads (is the VPN on?): down"])

    monkeypatch.setattr(gads, "customer_goals", unreachable)
    out = drafted(demand_gen_plan())
    assert server.check_plan(out["plan_path"]) == {"passed": False,
                                                    "errors": ["Couldn't reach Google Ads (is the VPN on?): down"]}
    assert dg_wired == []


def test_local_errors_block_the_check(dg_wired, demand_gen_plan):
    data = demand_gen_plan()
    data["ad_groups"][0]["channels"].append("display")
    out = server.draft_plan(data)
    assert out["saved"] is True and any("third-party Display" in e for e in out["errors"])
    assert server.check_plan(out["plan_path"])["passed"] is False
    assert dg_wired == []


def test_goals_changed_while_the_window_was_open_are_refused(dg_wired, window, demand_gen_plan, monkeypatch):  # noqa: F811
    out = checked(demand_gen_plan())

    def answer(title, lines, timeout_s=60):
        monkeypatch.setattr(gads, "customer_goals", lambda client, cid: (*ACCOUNT_GOALS, ("CONTACT", "WEBSITE")))
        return True

    monkeypatch.setattr(server.confirm, "ask_person", answer)
    assert create(out) == {"done": False, "errors": [server.GOALS_CHANGED]}
    assert real(dg_wired) == [] and not (folder_of(out) / checks.CLAIM_FILE).exists()


def test_a_failed_goal_read_after_approve_is_refused(dg_wired, demand_gen_plan, monkeypatch):
    out = checked(demand_gen_plan())

    def answer(title, lines, timeout_s=60):
        def down(client, cid):
            raise gads.GoogleAdsError(["Couldn't reach Google Ads (is the VPN on?): down"])

        monkeypatch.setattr(gads, "customer_goals", down)
        return True

    monkeypatch.setattr(server.confirm, "ask_person", answer)
    assert create(out) == {"done": False,
                           "errors": [server.GOALS_UNREAD, "Couldn't reach Google Ads (is the VPN on?): down"]}
    assert real(dg_wired) == []


def test_goals_changed_after_the_check_change_the_request(dg_wired, demand_gen_plan, monkeypatch):
    out = checked(demand_gen_plan())
    monkeypatch.setattr(gads, "customer_goals", lambda client, cid: (*ACCOUNT_GOALS, ("CONTACT", "WEBSITE")))
    assert create(out) == {"done": False, "errors": [checks.DIFFERS]}
    assert real(dg_wired) == []


def test_other_change_tools_refuse_a_demand_gen_plan(dg_wired, demand_gen_plan):
    out = checked(demand_gen_plan())
    assert server.upload_images(out["plan_path"], CID, out["fingerprint"], 4)["errors"] == [
        "this tool only accepts image_upload plans"]
    assert create(out)["done"] is True


@pytest.mark.parametrize("over", [{"campaign_name": "Other"}, {"daily_budget": 31.0}, {"over_cap": True},
                                  {"fingerprint": "0" * 12}])
def test_approval_arguments_must_match(dg_wired, demand_gen_plan, over):
    out = checked(demand_gen_plan())
    assert create(out, **over)["done"] is False
    assert real(dg_wired) == [] and create(out)["done"] is True


def test_an_existing_asset_in_place_of_a_new_one_is_still_this_accounts(dg_wired, demand_gen_plan):
    data = demand_gen_plan()
    data["ad_groups"][0]["ads"][0]["marketing_images"] = [{"asset": "customers/1234567890/assets/7"}]
    out = checked(data)
    assert create(out)["done"] is True


# ------------------------------------------------------------- the last look at the operations


def _tamper(edit):
    def patch(original):
        def compiler(plan, client, cid, goals):
            ops, labels = original(plan, client, cid, goals)
            edit(ops, labels, client, cid)
            return ops, labels

        return compiler

    return patch


def _op(label, field):
    """The created (or updated) message of the operation labelled `label`."""
    def get(o, l):
        operation = getattr(o[l.index(label)], field)
        return operation.update if field == "campaign_conversion_goal_operation" else operation.create
    return get


CAMPAIGN = _op("campaign", "campaign_operation")
BUDGET_OP = _op("budget", "campaign_budget_operation")
GROUP = _op("ad group[0] Springfield members", "ad_group_operation")
IMAGE_AD = _op("ad group[0] ad[0] image", "ad_group_ad_operation")
VIDEO_AD = _op("ad group[0] ad[1] video", "ad_group_ad_operation")
LOCATION = _op("ad group[0] location geoTargetConstants/1000001", "ad_group_criterion_operation")
PURCHASE = _op("conversion goal PURCHASE~WEBSITE", "campaign_conversion_goal_operation")
DOWNLOAD = _op("conversion goal DOWNLOAD~APP", "campaign_conversion_goal_operation")


def _set(get, path, value):
    """Sets a field (dotted path) on the message `get` finds; a callable value gets the client's enums."""
    def edit(o, l, c, cid):
        target = get(o, l)
        *parts, name = path.split(".")
        for part in parts:
            target = getattr(target, part)
        setattr(target, name, value(c.enums) if callable(value) else value.format(cid=cid) if isinstance(value, str) else value)
    return edit


def _raw(get, change):
    """Changes the raw protobuf message (to clear a field, for example)."""
    def edit(o, l, c, cid):
        message = get(o, l)
        change(type(message).pb(message))
    return edit


def _drop(*labels):
    def edit(o, l, c, cid):
        for label in labels:
            i = l.index(label)
            del o[i], l[i]
    return edit


def _copy(label):
    def edit(o, l, c, cid):
        op = c.get_type("MutateOperation")
        type(op).pb(op).CopyFrom(type(o[l.index(label)]).pb(o[l.index(label)]))
        o.append(op)
        l.append(f"{label} again")
    return edit


def _append(field, fill):
    def edit(o, l, c, cid):
        op = c.get_type("MutateOperation")
        fill(getattr(op, field).create, o, l, c, cid)
        o.append(op)
        l.append("sneaky")
    return edit


def _second_location(create, o, l, c, cid):
    create.ad_group = GROUP(o, l).resource_name
    create.location.geo_target_constant = "geoTargetConstants/1000002"


def _another_image(create, o, l, c, cid):
    create.resource_name = f"customers/{cid}/assets/-99"
    create.image_asset.data = b"x"


def _campaign_location(create, o, l, c, cid):
    create.campaign = CAMPAIGN(o, l).resource_name
    create.location.geo_target_constant = "geoTargetConstants/1000002"


def _automation(get, change):
    def edit(o, l, c, cid):
        change(get(o, l).ad_group_ad_asset_automation_settings, c)
    return edit


def _extra_text_automation(settings, c):
    s = c.get_type("AdGroupAdAssetAutomationSetting")
    s.asset_automation_type = c.enums.AssetAutomationTypeEnum.TEXT_ASSET_AUTOMATION
    s.asset_automation_status = c.enums.AssetAutomationStatusEnum.OPTED_OUT
    settings.append(s)


def _swap_landscape(o, l, c, cid):  # the new landscape image is replaced by an existing asset: it is left unused
    IMAGE_AD(o, l).ad.demand_gen_multi_asset_ad.marketing_images[0].asset = f"customers/{cid}/assets/7"


def _foreign_logo(o, l, c, cid):
    VIDEO_AD(o, l).ad.demand_gen_video_responsive_ad.logo_images[0].asset = "customers/9999999999/assets/5"


def _counts(n, what, m):
    return f"internal safety check: {n} {what} operations in the request, {m} in the plan"


E_ = lambda name, member: lambda E: getattr(getattr(E, name), member)  # noqa: E731
GUARD_CASES = [
    # the generic checks
    (_set(CAMPAIGN, "status", E_("CampaignStatusEnum", "ENABLED")), "internal safety check: a campaign would not be paused"),
    (_set(BUDGET_OP, "amount_micros", 30_000_001),
     "internal safety check: the budget differs from the plan or is above 3x the cap"),
    (portfolio, PORTFOLIO),
    (renamed("campaign", "campaign_operation", "customers/{cid}/campaigns/999"), TEMP_IDS),
    (renamed("image[0] dg-landscape.png", "asset_operation", "customers/{cid}/assets/999"), TEMP_IDS),
    (_append("campaign_criterion_operation", _campaign_location),
     "internal safety check: operation 15 (campaign_criterion_operation create) is not allowed in a "
     "demand_gen_campaign plan"),
    # the campaign's fixed settings
    (_set(CAMPAIGN, "advertising_channel_type", E_("AdvertisingChannelTypeEnum", "DISPLAY")), server.DEMAND_GEN_CAMPAIGN),
    (_set(CAMPAIGN, "advertising_channel_sub_type", E_("AdvertisingChannelSubTypeEnum", "APP_CAMPAIGN")),
     server.DEMAND_GEN_CAMPAIGN),
    (_raw(CAMPAIGN, lambda pb: pb.ClearField("demand_gen_campaign_settings")), server.DEMAND_GEN_CAMPAIGN),
    (_raw(CAMPAIGN, lambda pb: pb.demand_gen_campaign_settings.ClearField("upgraded_targeting")),
     server.DEMAND_GEN_CAMPAIGN),
    (_set(CAMPAIGN, "demand_gen_campaign_settings.upgraded_targeting", False), server.DEMAND_GEN_CAMPAIGN),
    (_raw(CAMPAIGN, lambda pb: pb.ClearField("contains_eu_political_advertising")), server.EU_POLITICAL),  # shared
    (_set(CAMPAIGN, "geo_target_type_setting.positive_geo_target_type",
          E_("PositiveGeoTargetTypeEnum", "PRESENCE_OR_INTEREST")), server.DEMAND_GEN_CAMPAIGN),
    (_raw(CAMPAIGN, lambda pb: pb.ClearField("geo_target_type_setting")), server.DEMAND_GEN_CAMPAIGN),
    (_set(CAMPAIGN, "network_settings.target_content_network", False), server.DEMAND_GEN_CAMPAIGN),
    (_set(BUDGET_OP, "explicitly_shared", True), server.SHARED_BUDGET),
    (_raw(BUDGET_OP, lambda pb: pb.ClearField("explicitly_shared")), server.SHARED_BUDGET),
    # conversion goals
    (_set(PURCHASE, "biddable", False), server.DEMAND_GEN_GOALS),
    (_set(DOWNLOAD, "biddable", True), server.DEMAND_GEN_GOALS),
    (_set(DOWNLOAD, "resource_name", "customers/{cid}/campaignConversionGoals/-99~DOWNLOAD~APP"),
     server.DEMAND_GEN_GOALS),
    (_set(DOWNLOAD, "resource_name", "customers/{cid}/campaignConversionGoals/123~DOWNLOAD~APP"),
     server.DEMAND_GEN_GOALS),
    (_set(DOWNLOAD, "resource_name", "customers/9999999999/campaignConversionGoals/-7~DOWNLOAD~APP"),
     server.DEMAND_GEN_GOALS),
    (_set(DOWNLOAD, "campaign", "customers/{cid}/campaigns/5"), server.DEMAND_GEN_GOALS),
    (lambda o, l, c, cid: o[l.index("conversion goal DOWNLOAD~APP")].campaign_conversion_goal_operation.update_mask
     .paths.append("category"), server.DEMAND_GEN_GOALS),
    (_drop("conversion goal DOWNLOAD~APP"), server.DEMAND_GEN_GOALS),
    (_copy("conversion goal DOWNLOAD~APP"), server.DEMAND_GEN_GOALS),
    # exactly the planned parts
    (_drop("ad group[0] location geoTargetConstants/1000001"), _counts(0, "ad group location", 1)),
    (_append("ad_group_criterion_operation", _second_location), _counts(2, "ad group location", 1)),
    (_set(LOCATION, "negative", True), _counts(0, "ad group location", 1)),
    (_drop("ad group[0] ad[1] video"), _counts(0, "video ad", 1)),
    (_append("asset_operation", _another_image), _counts(5, "image asset", 4)),
    (_copy("campaign"), server.REFERENCES),
    (_copy("budget"), server.REFERENCES),
    # references
    (_set(GROUP, "campaign", "customers/{cid}/campaigns/999"), server.REFERENCES),
    (_set(LOCATION, "ad_group", "customers/{cid}/adGroups/999"), server.REFERENCES),
    (_set(IMAGE_AD, "ad_group", "customers/{cid}/adGroups/999"), server.REFERENCES),
    (_set(CAMPAIGN, "campaign_budget", "customers/{cid}/campaignBudgets/999"), server.REFERENCES),
    (_foreign_logo, server.REFERENCES),
    (_swap_landscape, server.UNUSED_ASSET),
    # channels and expansion
    (_set(GROUP, "demand_gen_ad_group_settings.channel_controls.selected_channels.display", True),
     server.DEMAND_GEN_CHANNELS),
    (_set(GROUP, "demand_gen_ad_group_settings.channel_controls.selected_channels.maps", True),
     server.DEMAND_GEN_CHANNELS),
    (_set(GROUP, "demand_gen_ad_group_settings.channel_controls.selected_channels.gmail", False),
     server.DEMAND_GEN_CHANNELS),
    (_set(GROUP, "demand_gen_ad_group_settings.channel_controls.channel_strategy",
          E_("DemandGenChannelStrategyEnum", "ALL_OWNED_AND_OPERATED_CHANNELS")), server.DEMAND_GEN_CHANNELS),
    (_raw(GROUP, lambda pb: pb.ClearField("demand_gen_ad_group_settings")), server.DEMAND_GEN_CHANNELS),
    (_set(GROUP, "optimized_targeting_enabled", True), server.DEMAND_GEN_EXPANSION),
    (_set(GROUP, "exclude_demographic_expansion", False), server.DEMAND_GEN_EXPANSION),
    # the ads
    (_set(IMAGE_AD, "ad.demand_gen_multi_asset_ad.call_to_action_text", "Learn more"), server.DEMAND_GEN_ADS),
    (lambda o, l, c, cid: IMAGE_AD(o, l).ad.demand_gen_multi_asset_ad.tall_portrait_marketing_images.append(
        IMAGE_AD(o, l).ad.demand_gen_multi_asset_ad.square_marketing_images[0]), server.DEMAND_GEN_ADS),
    (_set(VIDEO_AD, "ad.demand_gen_video_responsive_ad.breadcrumb1", "deals"), server.DEMAND_GEN_ADS),
    (lambda o, l, c, cid: IMAGE_AD(o, l).ad.final_urls.__setitem__(0, "https://www.example.net/join"),
     server.DEMAND_GEN_ADS),
    (lambda o, l, c, cid: IMAGE_AD(o, l).ad.final_urls.append("https://www.example.com/other"), server.DEMAND_GEN_ADS),
    (_set(VIDEO_AD, "ad.tracking_url_template", "https://tracker.example.net/{{lpurl}}"), server.DEMAND_GEN_ADS),
    (_set(VIDEO_AD, "ad.final_url_suffix", "utm_source=x"), server.DEMAND_GEN_ADS),
    (lambda o, l, c, cid: VIDEO_AD(o, l).ad.final_mobile_urls.append("https://www.example.com/m"),
     server.DEMAND_GEN_ADS),
    (_automation(IMAGE_AD, lambda s, c: s.pop()), server.DEMAND_GEN_GENERATED),
    (_automation(VIDEO_AD, lambda s, c: setattr(s[0], "asset_automation_status",
                                                c.enums.AssetAutomationStatusEnum.OPTED_IN)), server.DEMAND_GEN_GENERATED),
    (_automation(IMAGE_AD, _extra_text_automation), server.DEMAND_GEN_GENERATED),
    (_raw(VIDEO_AD, lambda pb: pb.ad_group_ad_asset_automation_settings[1].ClearField("asset_automation_status")),
     server.DEMAND_GEN_GENERATED),
    # bidding: exactly the plan's
    (_set(CAMPAIGN, "maximize_conversions.target_cpa_micros", 1_000_000), server.BIDDING),
    # a field no other check reads: only the allowlist sees it
    (_set(CAMPAIGN, "tracking_url_template", "https://tracker.example.net/{{lpurl}}"),
     "internal safety check: unexpected field tracking_url_template on campaign"),
    (_set(GROUP, "target_cpa_micros", 1_000_000), "internal safety check: unexpected field target_cpa_micros on ad group"),
]


@pytest.mark.parametrize("edit, expected", GUARD_CASES)
def test_the_guard_refuses_tampered_operations(dg_wired, demand_gen_plan, monkeypatch, edit, expected):
    out = checked(demand_gen_plan())
    monkeypatch.setattr(server, "compile_demand_gen_campaign", _tamper(edit)(server.compile_demand_gen_campaign))
    res = create(out)
    assert res == {"done": False, "errors": [expected]}, res
    assert real(dg_wired) == []
    before = len(dg_wired)
    check = server.check_plan(out["plan_path"])  # check_plan refuses too, without asking Google
    assert check == {"passed": False, "errors": [expected]} and len(dg_wired) == before


def test_the_guard_needs_the_account_goals(search_cfg, demand_gen_plan, ads_client):
    from gads_builder import plans
    from gads_builder.compile_demand_gen import compile_demand_gen_campaign

    plan = plans.plan_from_dict(demand_gen_plan())
    ops, _ = compile_demand_gen_campaign(plan, ads_client, CID, ACCOUNT_GOALS)
    assert server._guard(plan, ops, ads_client, search_cfg, {"account_goals": ACCOUNT_GOALS}) == []
    assert server._guard(plan, ops, ads_client, search_cfg) == [server.DEMAND_GEN_GOALS]


# ------------------------------------------------------------- values compared with the plan

VIDEO_ASSET = _op("video[0] abcdefghijk", "asset_operation")
CTA_ASSET = _op("call to action[0] SIGN_UP", "asset_operation")
LANGUAGE = _op("ad group[0] language languageConstants/1000", "ad_group_criterion_operation")
AUDIENCE = _op(f"ad group[0] audience {LIST}", "ad_group_criterion_operation")
SECOND_GROUP = _op("ad group[1] Yoga fans", "ad_group_operation")


def _with_audience_and_cta(data):
    data["ad_groups"][0]["audiences"] = [LIST]
    data["ad_groups"][0]["ads"][1]["call_to_action"] = "SIGN_UP"
    data["brief"]["names"] = {LIST: "Past members"}


def _two_groups(data):
    data["ad_groups"].append(dict(data["ad_groups"][0], name="Yoga fans", channels=["discover"]))


def _two_videos(data):
    data["ad_groups"][0]["ads"][1]["videos"] = [{"youtube_id": "abcdefghijk"}, {"asset": "customers/1234567890/assets/7"}]


def _cta_on_one_of_two_video_ads(data):
    _with_audience_and_cta(data)
    data["ad_groups"][0]["ads"].append(dict(data["ad_groups"][0]["ads"][1], call_to_action=None))


def _cta_on_the_other_one_too(o, l, c, cid):
    other = _op("ad group[0] ad[2] video", "ad_group_ad_operation")(o, l).ad.demand_gen_video_responsive_ad
    cta = c.get_type("AdCallToActionAsset")
    cta.asset = CTA_ASSET(o, l).resource_name
    other.call_to_actions.append(cta)


def _an_audience_segment(o, l, c, cid):  # the user list criterion becomes an AudienceInfo criterion
    AUDIENCE(o, l).audience.audience = f"customers/{cid}/audiences/5"


def _audience_segment_added(create, o, l, c, cid):
    create.ad_group = GROUP(o, l).resource_name
    create.audience.audience = f"customers/{cid}/audiences/5"


def _ad_moved(o, l, c, cid):
    VIDEO_AD(o, l).ad_group = SECOND_GROUP(o, l).resource_name


def _videos_reordered(o, l, c, cid):
    videos = VIDEO_AD(o, l).ad.demand_gen_video_responsive_ad.videos
    first, second = videos[0].asset, videos[1].asset
    videos[0].asset, videos[1].asset = second, first


NAMES = server.DEMAND_GEN_NAMES
VALUE_CASES = [
    (None, _set(LOCATION, "location.geo_target_constant", "geoTargetConstants/1000002"), server.DEMAND_GEN_TARGETING),
    (None, _set(LANGUAGE, "language.language_constant", "languageConstants/1019"), server.DEMAND_GEN_TARGETING),
    (None, _set(VIDEO_ASSET, "youtube_video_asset.youtube_video_id", "zyxwvutsrqp"), server.DEMAND_GEN_MEDIA),
    (None, _append("ad_group_criterion_operation", _audience_segment_added), _counts(1, "ad group audience", 0)),
    (None, _set(CAMPAIGN, "name", "Pulse Springfield - Other"), NAMES),
    (None, _set(BUDGET_OP, "name", "Some other budget"), NAMES),
    (None, _set(GROUP, "name", "Other members"), NAMES),
    (None, _set(IMAGE_AD, "ad.name", "Pulse Springfield - Demand Gen - Springfield members - image 2"), NAMES),
    (_with_audience_and_cta, _set(AUDIENCE, "user_list.user_list", "customers/{cid}/userLists/6"),
     server.DEMAND_GEN_TARGETING),
    (_with_audience_and_cta, _an_audience_segment, _counts(0, "ad group user list", 1)),
    (_with_audience_and_cta, _set(CTA_ASSET, "call_to_action_asset.call_to_action", E_("CallToActionTypeEnum", "BUY_NOW")),
     server.DEMAND_GEN_MEDIA),
    (_with_audience_and_cta, _raw(CTA_ASSET, lambda pb: setattr(pb.call_to_action_asset, "call_to_action", 999)),
     server.DEMAND_GEN_MEDIA),  # a value no enum name has: refused, never a crash
    (_with_audience_and_cta, lambda o, l, c, cid: VIDEO_AD(o, l).ad.demand_gen_video_responsive_ad.call_to_actions.pop(),
     server.UNUSED_ASSET),
    (_cta_on_one_of_two_video_ads, _cta_on_the_other_one_too, server.DEMAND_GEN_MEDIA),
    (_two_groups, _ad_moved, NAMES),
    (_two_videos, _videos_reordered, server.DEMAND_GEN_MEDIA),
]


@pytest.mark.parametrize("change, edit, expected", VALUE_CASES)
def test_the_guard_compares_values_with_the_plan(dg_wired, demand_gen_plan, monkeypatch, change, edit, expected):
    data = demand_gen_plan()
    if change:
        change(data)
    out = checked(data)
    monkeypatch.setattr(server, "compile_demand_gen_campaign", _tamper(edit)(server.compile_demand_gen_campaign))
    assert create(out) == {"done": False, "errors": [expected]}
    assert server.check_plan(out["plan_path"]) == {"passed": False, "errors": [expected]}
    assert real(dg_wired) == []


@pytest.mark.parametrize("over, error", [
    ({"target_cpa": 2.5}, "target_cpa: Demand Gen doesn't accept a target cost per conversion with "
                          "MAXIMIZE_CONVERSIONS; use bidding TARGET_CPA"),
    ({"bidding": "MAXIMIZE_CLICKS", "max_cpc": 0.8},
     "max_cpc: Demand Gen doesn't accept a maximum CPC with MAXIMIZE_CLICKS; leave max_cpc out"),
])
def test_bidding_google_refuses_is_a_draft_error(dg_wired, demand_gen_plan, over, error):
    out = server.draft_plan(demand_gen_plan(**over))
    assert out["errors"] == [error]
    assert server.check_plan(out["plan_path"])["passed"] is False
    assert dg_wired == []
