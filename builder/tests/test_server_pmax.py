"""pmax_campaign plans through draft_plan, check_plan and create_campaign_from_plan."""

import json

import pytest

from gads_builder import checks, gads, server
from test_server import (CID, GOOGLE, PORTFOLIO, TEMP_IDS, checked, drafted, folder_of, names_search,  # noqa: F401
                         portfolio, real, renamed, wired, window)  # wired, window: fixtures

NAME, BUDGET = "Pulse Springfield - Performance Max", 50.0
ACCOUNT_GOALS = (("DOWNLOAD", "APP"), ("PURCHASE", "WEBSITE"), ("SUBMIT_LEAD_FORM", "WEBSITE"))
AUDIENCE = "customers/1234567890/audiences/5"
PERSONA = "customers/1234567890/audiences/6"  # made for one asset group (ASSET_GROUP scope)


@pytest.fixture
def pm_wired(wired, monkeypatch, search_cfg, ads_client):  # noqa: F811
    """The EUR account of the Search tests, with three conversion goals and two audiences."""
    E = ads_client.enums
    monkeypatch.setattr(server, "load_config", lambda: search_cfg)
    monkeypatch.setattr(gads, "account_currency", lambda client, cid: "EUR")
    monkeypatch.setattr(gads, "customer_goals", lambda client, cid: ACCOUNT_GOALS)
    known = dict(GOOGLE, **{
        AUDIENCE: ("audience", {"name": "Past members", "scope": E.AudienceScopeEnum.CUSTOMER,
                                "status": E.AudienceStatusEnum.ENABLED}),
        PERSONA: ("audience", {"name": "AssetGroupPersona_1", "scope": E.AudienceScopeEnum.ASSET_GROUP,
                               "status": E.AudienceStatusEnum.ENABLED}),
    })
    monkeypatch.setattr(gads, "search", names_search(ads_client, known=known))
    return wired


def create(out, **over):
    args = {"customer_id": CID, "fingerprint": out["fingerprint"], "campaign_name": NAME, "daily_budget": BUDGET,
            "over_cap": False, **over}
    return server.create_campaign_from_plan(out["plan_path"], **args)


def test_check_then_create_sends_one_paused_pmax_campaign(pm_wired, pmax_plan, ads_client):
    E = ads_client.enums
    out = drafted(pmax_plan())
    assert "| Type | Performance Max campaign (website) |" in out["summary"]
    check = server.check_plan(out["plan_path"])
    assert check["passed"] is True and check["operations"] == 72
    assert check["approval"] == {"customer_id": CID, "fingerprint": out["fingerprint"], "campaign_name": NAME,
                                 "daily_budget": BUDGET, "over_cap": False}
    assert "campaign_status" not in check and "note" not in check
    assert create(out)["done"] is True
    [sent] = real(pm_wired)
    campaign = next(op.campaign_operation.create for op in sent if "campaign_operation" in op)
    assert campaign.status == E.CampaignStatusEnum.PAUSED
    assert campaign.advertising_channel_type == E.AdvertisingChannelTypeEnum.PERFORMANCE_MAX
    goals = [op.campaign_conversion_goal_operation.update for op in sent if "campaign_conversion_goal_operation" in op]
    assert [(g.resource_name.rsplit("~", 2)[1:], g.biddable) for g in goals] == [
        (["DOWNLOAD", "APP"], False), (["PURCHASE", "WEBSITE"], True), (["SUBMIT_LEAD_FORM", "WEBSITE"], False)]
    record = json.loads((folder_of(out) / checks.CHECK_FILE).read_text(encoding="utf-8"))
    assert record["digest"] == gads.request_digest(CID, sent)
    again = create(out)
    assert again["done"] is False and "already applied" in again["errors"][0]
    assert len(real(pm_wired)) == 1


def test_the_window_shows_the_pmax_campaign(pm_wired, window, pmax_plan):  # noqa: F811
    out = checked(pmax_plan())
    assert create(out)["done"] is True
    [(_, lines)] = window.shown
    assert lines == [
        "Account: 1234567890",
        "Tool: create_campaign_from_plan (pmax_campaign)",
        f"Plan fingerprint: {out['fingerprint']}",
        'Campaign: "Pulse Springfield - Performance Max" (created PAUSED)',
        "Daily budget: 50.00 EUR",
        "Bidding: Maximize conversions",
        "Conversion goals bid on:",
        "- PURCHASE (WEBSITE)",
        "Other account conversion goals, not bid on: 2",
        "1 location, 1 language; people in or regularly in the locations",
        'Brand: business name "Pulse Fitness", 1 logo, 0 landscape logos',
        "Asset groups: 1, headlines: 15, long headlines: 5, descriptions: 5",
        "New images: 4, existing images: 0, videos: 1",
        "Search themes: 2, audience signals: 0",
        'Asset group 1 final URL: "https://www.example.com/join"',
        server.PMAX_FIXED_LINE,
    ]


def test_the_window_lists_every_goal_and_each_asset_groups_final_url(pm_wired, window, pmax_plan):  # noqa: F811
    data = pmax_plan(conversion_goals=[{"category": "PURCHASE", "origin": "WEBSITE"},
                                       {"category": "SUBMIT_LEAD_FORM", "origin": "WEBSITE"}])
    data["asset_groups"].append(dict(data["asset_groups"][0], name="Yoga fans",
                                     final_url="https://www.example.com/yoga", audience_signal=AUDIENCE))
    out = checked(data)
    assert create(out)["done"] is True
    lines = window.shown[0][1]
    assert lines[6:9] == ["Conversion goals bid on:", "- PURCHASE (WEBSITE)", "- SUBMIT_LEAD_FORM (WEBSITE)"]
    assert "Other account conversion goals, not bid on: 1" in lines
    assert "Search themes: 4, audience signals: 1" in lines
    assert lines[-3:] == ['Asset group 1 final URL: "https://www.example.com/join"',
                          'Asset group 2 final URL: "https://www.example.com/yoga"', server.PMAX_FIXED_LINE]


def test_the_live_checks(pm_wired, pmax_plan, monkeypatch):
    lead = drafted(pmax_plan(conversion_goals=[{"category": "SIGNUP", "origin": "WEBSITE"}]))
    assert server.check_plan(lead["plan_path"]) == {
        "passed": False, "errors": ["conversion_goals[0]: SIGNUP (WEBSITE) is not a conversion goal of this account"]}
    monkeypatch.setattr(gads, "account_currency", lambda client, cid: "USD")
    out = drafted(pmax_plan())
    assert server.check_plan(out["plan_path"])["errors"][0] == "currency: the account uses USD, the plan says EUR"
    assert pm_wired == []


def test_an_audience_signal_must_be_an_existing_account_wide_audience(pm_wired, pmax_plan):
    data = pmax_plan(brief={"sources": {"Classes Every Day": "proven"}, "names": {AUDIENCE: "Past members"}})
    data["asset_groups"][0]["audience_signal"] = AUDIENCE
    out = checked(data)
    assert "- Audience signal: Past members (customers/1234567890/audiences/5)" in out["summary"]
    data["brief"]["names"] = {}
    data["asset_groups"][0]["audience_signal"] = PERSONA
    persona = drafted(data)
    assert server.check_plan(persona["plan_path"])["errors"] == [
        f"asset_groups[0].audience_signal: {PERSONA} is not an enabled account-wide audience (an audience made for "
        "one asset group can't be reused)"]
    data["asset_groups"][0]["audience_signal"] = "customers/1234567890/audiences/7"
    missing = drafted(data)
    assert server.check_plan(missing["plan_path"])["errors"] == [
        "customers/1234567890/audiences/7: not found in Google Ads"]


def test_a_failed_live_read_blocks_the_check(pm_wired, pmax_plan, monkeypatch):
    def unreachable(client, cid):
        raise gads.GoogleAdsError(["Couldn't reach Google Ads (is the VPN on?): down"])

    monkeypatch.setattr(gads, "customer_goals", unreachable)
    out = drafted(pmax_plan())
    assert server.check_plan(out["plan_path"]) == {"passed": False,
                                                    "errors": ["Couldn't reach Google Ads (is the VPN on?): down"]}
    assert pm_wired == []


def test_local_errors_block_the_check(pm_wired, pmax_plan):
    data = pmax_plan()
    data["asset_groups"][0]["videos"] = []
    out = server.draft_plan(data)
    assert out["saved"] is True and any("YouTube videos" in e for e in out["errors"])
    assert server.check_plan(out["plan_path"])["passed"] is False
    assert pm_wired == []


def test_goals_changed_while_the_window_was_open_are_refused(pm_wired, window, pmax_plan, monkeypatch):  # noqa: F811
    out = checked(pmax_plan())

    def answer(title, lines, timeout_s=60):
        monkeypatch.setattr(gads, "customer_goals", lambda client, cid: (*ACCOUNT_GOALS, ("CONTACT", "WEBSITE")))
        return True

    monkeypatch.setattr(server.confirm, "ask_person", answer)
    assert create(out) == {"done": False, "errors": [server.GOALS_CHANGED]}
    assert real(pm_wired) == [] and not (folder_of(out) / checks.CLAIM_FILE).exists()


def test_a_failed_goal_read_after_approve_is_refused(pm_wired, pmax_plan, monkeypatch):
    out = checked(pmax_plan())

    def answer(title, lines, timeout_s=60):
        def down(client, cid):
            raise gads.GoogleAdsError(["Couldn't reach Google Ads (is the VPN on?): down"])

        monkeypatch.setattr(gads, "customer_goals", down)
        return True

    monkeypatch.setattr(server.confirm, "ask_person", answer)
    assert create(out) == {"done": False,
                           "errors": [server.GOALS_UNREAD, "Couldn't reach Google Ads (is the VPN on?): down"]}
    assert real(pm_wired) == []


def test_goals_changed_after_the_check_change_the_request(pm_wired, pmax_plan, monkeypatch):
    out = checked(pmax_plan())
    monkeypatch.setattr(gads, "customer_goals", lambda client, cid: (*ACCOUNT_GOALS, ("CONTACT", "WEBSITE")))
    assert create(out) == {"done": False, "errors": [checks.DIFFERS]}
    assert real(pm_wired) == []


def test_other_change_tools_refuse_a_pmax_plan(pm_wired, pmax_plan):
    out = checked(pmax_plan())
    assert server.upload_images(out["plan_path"], CID, out["fingerprint"], 4)["errors"] == [
        "this tool only accepts image_upload plans"]
    assert create(out)["done"] is True


@pytest.mark.parametrize("over", [{"campaign_name": "Other"}, {"daily_budget": 51.0}, {"over_cap": True},
                                  {"fingerprint": "0" * 12}])
def test_approval_arguments_must_match(pm_wired, pmax_plan, over):
    out = checked(pmax_plan())
    assert create(out, **over)["done"] is False
    assert real(pm_wired) == [] and create(out)["done"] is True


def test_existing_assets_in_place_of_new_ones(pm_wired, pmax_plan):
    data = pmax_plan(logos=[{"asset": "customers/1234567890/assets/7"}])
    data["asset_groups"][0].update(marketing_images=[{"asset": "customers/1234567890/assets/7"}],
                                   videos=[{"asset": "customers/1234567890/assets/8"}])
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
GROUP = _op("asset group[0] Springfield members", "asset_group_operation")
LOCATION = _op("location geoTargetConstants/1000001", "campaign_criterion_operation")
PURCHASE = _op("conversion goal PURCHASE~WEBSITE", "campaign_conversion_goal_operation")
BUSINESS = _op("business name", "campaign_asset_operation")
LOGO = _op("logo[0]", "campaign_asset_operation")
HEADLINE = _op("asset group[0] headline[0]", "asset_group_asset_operation")
LONG_HEADLINE = _op("asset group[0] long headline[0]", "asset_group_asset_operation")
LANDSCAPE = _op("asset group[0] marketing image[0]", "asset_group_asset_operation")
THEME = _op("asset group[0] search theme[0] gym membership springfield", "asset_group_signal_operation")
NAME_ASSET = _op("text[0] Pulse Fitness", "asset_operation")
VIDEO_ASSET = _op("video[0] abcdefghijk", "asset_operation")


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


def _automations(change):
    def edit(o, l, c, cid):
        change(type(CAMPAIGN(o, l)).pb(CAMPAIGN(o, l)).asset_automation_settings, c.enums)
    return edit


def _add_automation(kind, status):
    def change(settings, E):
        s = settings.add()
        s.asset_automation_type = getattr(E.AssetAutomationTypeEnum, kind)
        s.asset_automation_status = getattr(E.AssetAutomationStatusEnum, status)
    return _automations(change)


def _campaign_location(create, o, l, c, cid):
    create.campaign = CAMPAIGN(o, l).resource_name
    create.location.geo_target_constant = "geoTargetConstants/1000002"


def _another_image(create, o, l, c, cid):
    create.resource_name = f"customers/{cid}/assets/-99"
    create.image_asset.data = b"x"


def _sitelink_link(create, o, l, c, cid):
    create.campaign = CAMPAIGN(o, l).resource_name
    create.asset = f"customers/{cid}/assets/7"
    create.field_type = c.enums.AssetFieldTypeEnum.SITELINK


def _another_theme(create, o, l, c, cid):
    create.asset_group = GROUP(o, l).resource_name
    create.search_theme.text = "cheap gym"


def _counts(n, what, m):
    return f"internal safety check: {n} {what} operations in the request, {m} in the plan"


E_ = lambda name, member: lambda E: getattr(getattr(E, name), member)  # noqa: E731
GUARD_CASES = [
    # the generic checks
    (_set(CAMPAIGN, "status", E_("CampaignStatusEnum", "ENABLED")), "internal safety check: a campaign would not be paused"),
    (_set(BUDGET_OP, "amount_micros", 50_000_001),
     "internal safety check: the budget differs from the plan or is above 3x the cap"),
    (portfolio, PORTFOLIO),
    (renamed("campaign", "campaign_operation", "customers/{cid}/campaigns/999"), TEMP_IDS),
    (renamed("asset group[0] Springfield members", "asset_group_operation", "customers/{cid}/assetGroups/999"), TEMP_IDS),
    (renamed("text[0] Pulse Fitness", "asset_operation", "customers/{cid}/assets/999"), TEMP_IDS),
    (_set(BUDGET_OP, "explicitly_shared", True), server.SHARED_BUDGET),
    (_set(LOCATION, "negative", True), server.NEGATIVE_TARGETING),
    (_append("ad_group_operation", lambda create, o, l, c, cid: setattr(create, "name", "x")),
     "internal safety check: operation 72 (ad_group_operation create) is not allowed in a pmax_campaign plan"),
    # the campaign's fixed settings
    (_set(CAMPAIGN, "advertising_channel_type", E_("AdvertisingChannelTypeEnum", "DISPLAY")), server.PMAX_CAMPAIGN),
    (_set(CAMPAIGN, "advertising_channel_sub_type", E_("AdvertisingChannelSubTypeEnum", "TRAVEL_ACTIVITIES")),
     server.PMAX_CAMPAIGN),
    (_raw(CAMPAIGN, lambda pb: pb.ClearField("brand_guidelines_enabled")), server.PMAX_CAMPAIGN),
    (_set(CAMPAIGN, "brand_guidelines_enabled", False), server.PMAX_CAMPAIGN),
    (_automations(lambda s, E: s.pop()), server.PMAX_CAMPAIGN),
    (_automations(lambda s, E: setattr(s[0], "asset_automation_status", E.AssetAutomationStatusEnum.OPTED_IN)),
     server.PMAX_CAMPAIGN),
    (_automations(lambda s, E: s[1].ClearField("asset_automation_status")), server.PMAX_CAMPAIGN),
    (_add_automation("TEXT_ASSET_AUTOMATION", "OPTED_OUT"), server.PMAX_CAMPAIGN),  # listed twice
    (_add_automation("GENERATE_IMAGE_EXTRACTION", "OPTED_IN"), server.PMAX_CAMPAIGN),
    (_raw(CAMPAIGN, lambda pb: pb.ClearField("contains_eu_political_advertising")), server.EU_POLITICAL),
    (_set(CAMPAIGN, "geo_target_type_setting.positive_geo_target_type",
          E_("PositiveGeoTargetTypeEnum", "PRESENCE_OR_INTEREST")), server.PMAX_CAMPAIGN),
    # bidding and goals
    (_raw(CAMPAIGN, lambda pb: pb.maximize_conversion_value.SetInParent()), server.BIDDING),
    (_set(CAMPAIGN, "maximize_conversions.target_cpa_micros", 9_000_000), server.BIDDING),
    (_set(PURCHASE, "biddable", False), server.DEMAND_GEN_GOALS),
    (_drop("conversion goal DOWNLOAD~APP"), server.DEMAND_GEN_GOALS),
    # exactly the planned parts
    (_drop("location geoTargetConstants/1000001"), _counts(0, "location", 1)),
    (_append("campaign_criterion_operation", _campaign_location), _counts(2, "location", 1)),
    (_drop("asset group[0] headline[0]"), _counts(14, "headline", 15)),
    (_append("asset_operation", _another_image), _counts(5, "image asset", 4)),
    (_append("campaign_asset_operation", _sitelink_link), _counts(1, "campaign sitelink link", 0)),
    (_append("asset_group_signal_operation", _another_theme), _counts(3, "search theme", 2)),
    (_set(LOGO, "field_type", E_("AssetFieldTypeEnum", "LANDSCAPE_LOGO")), _counts(0, "logo link", 1)),
    (_copy("campaign"), server.REFERENCES),
    (_copy("budget"), server.REFERENCES),
    # references
    (_set(CAMPAIGN, "campaign_budget", "customers/{cid}/campaignBudgets/999"), server.REFERENCES),
    (_set(GROUP, "campaign", "customers/{cid}/campaigns/999"), server.REFERENCES),
    (_set(BUSINESS, "campaign", "customers/{cid}/campaigns/999"), server.REFERENCES),
    (_set(LOCATION, "campaign", "customers/{cid}/campaigns/999"), server.REFERENCES),
    (_set(HEADLINE, "asset_group", "customers/{cid}/assetGroups/999"), server.REFERENCES),
    (_set(THEME, "asset_group", "customers/{cid}/assetGroups/999"), server.REFERENCES),
    (_set(LOGO, "asset", "customers/9999999999/assets/5"), server.REFERENCES),
    (_set(LANDSCAPE, "asset", "customers/{cid}/assets/7"), server.PMAX_UNUSED),  # the new landscape image is unused
    # the asset group
    (_set(GROUP, "status", E_("AssetGroupStatusEnum", "PAUSED")), server.STATUSES),
    (lambda o, l, c, cid: GROUP(o, l).final_urls.__setitem__(0, "https://www.example.net/join"), server.PMAX_ASSET_GROUPS),
    (lambda o, l, c, cid: GROUP(o, l).final_urls.append("https://www.example.com/other"), server.PMAX_ASSET_GROUPS),
    (_set(GROUP, "path1", "deals"), server.PMAX_ASSET_GROUPS),
    # values
    (_set(CAMPAIGN, "name", "Pulse Springfield - Other"), server.PMAX_NAMES),
    (_set(BUDGET_OP, "name", "Some other budget"), server.PMAX_NAMES),
    (_set(GROUP, "name", "Other members"), server.PMAX_NAMES),
    (_set(LOCATION, "location.geo_target_constant", "geoTargetConstants/1000002"), server.PMAX_TARGETING),
    (_set(NAME_ASSET, "text_asset.text", "Rival Fitness"), server.PMAX_ASSETS),
    (_set(VIDEO_ASSET, "youtube_video_asset.youtube_video_id", "zyxwvutsrqp"), server.PMAX_ASSETS),
    (lambda o, l, c, cid: setattr(HEADLINE(o, l), "asset", LONG_HEADLINE(o, l).asset) or setattr(
        LONG_HEADLINE(o, l), "asset", _op("text[1] Classes Every Day", "asset_operation")(o, l).resource_name),
     server.PMAX_LINKS),  # a headline and a long headline swap texts
    (_set(THEME, "search_theme.text", "cheap gym"), server.PMAX_SIGNALS),
    # the field allowlist, checked last
    (_set(GROUP, "tracking_url_template", "https://tracker.example.net/{{lpurl}}"),
     "internal safety check: unexpected field tracking_url_template on asset group"),
    (lambda o, l, c, cid: GROUP(o, l).final_mobile_urls.append("https://www.example.com/m"),
     "internal safety check: unexpected field final_mobile_urls on asset group"),
    (lambda o, l, c, cid: NAME_ASSET(o, l).final_urls.append("https://www.example.net/"),
     "internal safety check: unexpected field final_urls on asset"),
    (_set(CAMPAIGN, "brand_guidelines.main_color", "#ff0000"),
     "internal safety check: unexpected field brand_guidelines on campaign"),
    (lambda o, l, c, cid: CAMPAIGN(o, l).text_guidelines.term_exclusions.append("cheap"),
     "internal safety check: unexpected field text_guidelines on campaign"),
    (_set(HEADLINE, "status", E_("AssetLinkStatusEnum", "PAUSED")),
     "internal safety check: unexpected field status on asset group link"),
]


@pytest.mark.parametrize("edit, expected", GUARD_CASES)
def test_the_guard_refuses_tampered_operations(pm_wired, pmax_plan, monkeypatch, edit, expected):
    out = checked(pmax_plan())
    monkeypatch.setattr(server, "compile_pmax_campaign", _tamper(edit)(server.compile_pmax_campaign))
    res = create(out)
    assert res == {"done": False, "errors": [expected]}, res
    assert real(pm_wired) == []
    before = len(pm_wired)
    check = server.check_plan(out["plan_path"])  # check_plan refuses too, without asking Google
    assert check == {"passed": False, "errors": [expected]} and len(pm_wired) == before


def test_the_guard_needs_the_account_goals(search_cfg, pmax_plan, ads_client):
    from gads_builder import plans
    from gads_builder.compile_pmax import compile_pmax_campaign

    plan = plans.plan_from_dict(pmax_plan())
    ops, _ = compile_pmax_campaign(plan, ads_client, CID, ACCOUNT_GOALS)
    assert server._guard(plan, ops, ads_client, search_cfg, {"account_goals": ACCOUNT_GOALS}) == []
    assert server._guard(plan, ops, ads_client, search_cfg) == [server.DEMAND_GEN_GOALS]


def test_a_headline_equal_to_the_business_name_is_one_text_asset_linked_twice(search_cfg, pmax_plan, ads_client):
    """A common real case (and the live check-only plan's): one text asset linked as the campaign's BUSINESS_NAME
    and as an asset group HEADLINE. The compiler creates it once; the rules and the guard accept it."""
    from gads_builder import plans
    from gads_builder.compile_pmax import compile_pmax_campaign

    data = pmax_plan()
    data["asset_groups"][0]["headlines"][0] = data["business_name"]
    plan = plans.plan_from_dict(data)
    assert [x for x in plans.validate(plan, search_cfg) if x.level == "error"] == []
    ops, _ = compile_pmax_campaign(plan, ads_client, CID, ACCOUNT_GOALS)
    made = [op.asset_operation.create for op in ops if "asset_operation" in op]
    [name] = [a.resource_name for a in made if a.text_asset.text == data["business_name"]]
    linked = [(link.field_type.name, which) for which in ("campaign_asset_operation", "asset_group_asset_operation")
              for op in ops if which in op for link in [getattr(op, which).create] if link.asset == name]
    assert linked == [("BUSINESS_NAME", "campaign_asset_operation"), ("HEADLINE", "asset_group_asset_operation")]
    assert server._guard(plan, ops, ads_client, search_cfg, {"account_goals": ACCOUNT_GOALS}) == []


# ------------------------------------------------------------- values compared with the plan

CTA_ASSET = _op("call to action[0] SIGN_UP", "asset_operation")
AUDIENCE_SIGNAL = _op(f"asset group[0] audience {AUDIENCE}", "asset_group_signal_operation")


def _with_audience_and_cta(data):
    data["asset_groups"][0].update(audience_signal=AUDIENCE, call_to_action="SIGN_UP")


def _with_a_landscape_logo(data):
    data["landscape_logos"] = [{"asset": "customers/1234567890/assets/9"}]


def _two_groups(data):
    data["asset_groups"].append(dict(data["asset_groups"][0], name="Yoga fans", final_url="https://www.example.com/yoga"))


def _logos_swapped(o, l, c, cid):
    logo, wide = LOGO(o, l), _op("landscape logo[0]", "campaign_asset_operation")(o, l)
    logo.asset, wide.asset = wide.asset, logo.asset


def _theme_moved(o, l, c, cid):
    THEME(o, l).asset_group = _op("asset group[1] Yoga fans", "asset_group_operation")(o, l).resource_name


def _headline_moved(o, l, c, cid):
    HEADLINE(o, l).asset_group = _op("asset group[1] Yoga fans", "asset_group_operation")(o, l).resource_name


VALUE_CASES = [
    (_with_audience_and_cta, _set(AUDIENCE_SIGNAL, "audience.audience", "customers/{cid}/audiences/7"),
     server.PMAX_SIGNALS),
    (_with_audience_and_cta, _set(CTA_ASSET, "call_to_action_asset.call_to_action", E_("CallToActionTypeEnum", "BUY_NOW")),
     server.PMAX_ASSETS),
    (_with_audience_and_cta, _raw(CTA_ASSET, lambda pb: setattr(pb.call_to_action_asset, "call_to_action", 999)),
     server.PMAX_ASSETS),  # a value no enum name has: refused, never a crash
    (_with_audience_and_cta, _drop("asset group[0] call to action"), _counts(0, "call to action", 1)),
    (_with_a_landscape_logo, _logos_swapped, server.PMAX_LINKS),
    (_two_groups, _theme_moved, server.PMAX_SIGNALS),
    (_two_groups, _headline_moved, server.PMAX_LINKS),
]


@pytest.mark.parametrize("change, edit, expected", VALUE_CASES)
def test_the_guard_compares_values_with_the_plan(pm_wired, pmax_plan, monkeypatch, change, edit, expected):
    data = pmax_plan()
    change(data)
    out = checked(data)
    monkeypatch.setattr(server, "compile_pmax_campaign", _tamper(edit)(server.compile_pmax_campaign))
    assert create(out) == {"done": False, "errors": [expected]}
    assert server.check_plan(out["plan_path"]) == {"passed": False, "errors": [expected]}
    assert real(pm_wired) == []
