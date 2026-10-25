"""search_campaign plans through draft_plan, check_plan and create_campaign_from_plan."""

import dataclasses
import json

import pytest

from gads_builder import checks, gads, server
from test_server import (CID, NEGATIVE_TARGETING, PORTFOLIO, SHARED_BUDGET, TEMP_IDS, checked, drafted,  # noqa: F401
                         folder_of, portfolio, real, renamed, wired, window)  # wired, window: fixtures

NAME, BUDGET = "Springfield Gyms - Search", 80.0


@pytest.fixture
def search_wired(wired, monkeypatch, search_cfg):  # noqa: F811
    monkeypatch.setattr(server, "load_config", lambda: search_cfg)
    monkeypatch.setattr(gads, "account_currency", lambda client, cid: "EUR")
    return wired


def create(out, **over):
    args = {"customer_id": CID, "fingerprint": out["fingerprint"], "campaign_name": NAME, "daily_budget": BUDGET,
            "over_cap": False, **over}
    return server.create_campaign_from_plan(out["plan_path"], **args)


def test_check_then_create_sends_one_paused_search_campaign(search_wired, search_plan, ads_client):
    E = ads_client.enums
    out = drafted(search_plan())
    assert "| Type | Search campaign |" in out["summary"]
    check = server.check_plan(out["plan_path"])
    assert check["passed"] is True and check["operations"] == 31
    assert check["approval"] == {"customer_id": CID, "fingerprint": out["fingerprint"], "campaign_name": NAME,
                                 "daily_budget": BUDGET, "over_cap": False}
    assert create(out)["done"] is True
    [sent] = real(search_wired)
    campaign = next(op.campaign_operation.create for op in sent if "campaign_operation" in op)
    assert campaign.status == E.CampaignStatusEnum.PAUSED
    assert campaign.advertising_channel_type == E.AdvertisingChannelTypeEnum.SEARCH
    folder = folder_of(out)
    record = json.loads((folder / checks.CHECK_FILE).read_text(encoding="utf-8"))
    assert record["digest"] == gads.request_digest(CID, sent)
    assert json.loads((folder / "result-create.json").read_text(encoding="utf-8"))["digest"] == record["digest"]
    again = create(out)
    assert again["done"] is False and "already applied" in again["errors"][0]
    assert len(real(search_wired)) == 1


def test_the_window_shows_the_search_campaign(search_wired, window, search_plan):  # noqa: F811
    out = checked(search_plan())
    assert create(out)["done"] is True
    [(_, lines)] = window.shown
    assert lines == [
        "Account: 1234567890",
        "Tool: create_campaign_from_plan (search_campaign)",
        f"Plan fingerprint: {out['fingerprint']}",
        'Campaign: "Springfield Gyms - Search" (created PAUSED)',
        "Daily budget: 80.00 EUR",
        "Bidding: Maximize conversions",
        "Networks: Google Search only",
        "Locations: 1 · languages: 1",
        "Ad groups: 1, keywords: 5, headlines: 15, descriptions: 4, negative keywords: 2",
        "Sitelinks: 4, callouts: 4, snippets: 1",
    ]


def test_the_window_shows_search_partners_and_every_location_and_language(search_wired, window,  # noqa: F811
                                                                        search_plan, monkeypatch, ads_client):
    from test_server import GOOGLE, names_search

    town = {"name": "Shelbyville", "canonical_name": "Shelbyville,Test Region"}
    known = {**GOOGLE, "geoTargetConstants/1000002": ("geo_target_constant", town)}
    monkeypatch.setattr(gads, "search", names_search(ads_client, known=known))
    data = search_plan(search_partners=True, locations=["geoTargetConstants/1000001", "geoTargetConstants/1000002"],
                       languages=["languageConstants/1000", "languageConstants/1019"])
    out = checked(data)
    assert create(out)["done"] is True
    [(_, lines)] = window.shown
    assert "Networks: Google Search + search partners" in lines and "Locations: 2 · languages: 2" in lines
    assert not any("Display" in line for line in lines)


def test_a_no_in_the_window_sends_nothing(search_wired, window, search_plan):  # noqa: F811
    out = checked(search_plan())
    window.answer = False
    assert create(out) == {"done": False, "errors": [server.NOT_APPROVED]}
    assert real(search_wired) == [] and not (folder_of(out) / checks.CLAIM_FILE).exists()
    window.answer = True
    assert create(out)["done"] is True


def test_over_the_cap_needs_the_exact_amount_and_shows_it(search_wired, window, search_plan):  # noqa: F811
    out = checked(search_plan(daily_budget=600.0, confirm_budget_over_cap=600.0))
    approval = server.check_plan(out["plan_path"])["approval"]
    assert approval["over_cap"] is True and approval["daily_budget"] == 600.0
    assert create(out, daily_budget=600.0)["errors"] == ["over_cap does not match the plan (True)"]
    assert create(out, daily_budget=600.0, over_cap=True)["done"] is True
    assert "Daily budget: 600.00 EUR - ABOVE THE CAP" in window.shown[0][1]


@pytest.mark.parametrize("over", [{"campaign_name": "Other"}, {"daily_budget": 81.0}, {"over_cap": True},
                                  {"customer_id": "1234567891"}, {"fingerprint": "0" * 12}])
def test_approval_arguments_must_match(search_wired, search_plan, over):
    out = checked(search_plan())
    assert create(out, **over)["done"] is False
    assert real(search_wired) == []
    assert create(out)["done"] is True


def test_the_account_currency_and_google_names_are_checked(search_wired, search_plan, monkeypatch):
    out = drafted(search_plan())
    monkeypatch.setattr(gads, "account_currency", lambda client, cid: "USD")
    assert server.check_plan(out["plan_path"]) == {"passed": False,
                                                   "errors": ["currency: the account uses USD, the plan says EUR"]}
    monkeypatch.setattr(gads, "account_currency", lambda client, cid: "EUR")
    named = drafted(search_plan(brief={"sources": {"Classes Every Day": "proven"},
                                       "names": {"geoTargetConstants/1000001": "Capital City"}}))
    assert server.check_plan(named["plan_path"])["errors"] == [
        "brief.names: 'Capital City' is not Google's name for geoTargetConstants/1000001 ('Springfield,Test Region')"]
    missing = drafted(search_plan(locations=["geoTargetConstants/2000002"]))
    assert server.check_plan(missing["plan_path"])["errors"] == ["geoTargetConstants/2000002: not found in Google Ads"]
    assert search_wired == []


def test_local_errors_block_the_check(search_wired, search_plan):
    data = search_plan()
    data["ad_groups"][0]["ad"]["final_url"] = "https://www.example.net/gym"
    out = server.draft_plan(data)
    assert out["saved"] is True and any("allowed domain" in e for e in out["errors"])
    assert server.check_plan(out["plan_path"])["passed"] is False
    assert search_wired == []


def test_a_domain_removed_from_the_config_makes_the_plan_stale(search_wired, search_plan, search_cfg, monkeypatch):
    out = checked(search_plan())
    monkeypatch.setattr(server, "load_config", lambda: dataclasses.replace(search_cfg, domains=("example.org",)))
    assert server.check_plan(out["plan_path"]) == {"passed": False, "errors": [server.PLAN_CHANGED]}
    assert create(out) == {"done": False, "errors": [server.PLAN_CHANGED]}
    assert real(search_wired) == []


def test_other_change_tools_refuse_a_search_plan(search_wired, search_plan):
    out = checked(search_plan())
    assert server.upload_images(out["plan_path"], CID, out["fingerprint"], 0)["errors"] == [
        "this tool only accepts image_upload plans"]
    assert server.update_campaign_assets(out["plan_path"], CID, out["fingerprint"], NAME, "PAUSED", [], [])["errors"] == [
        "this tool only accepts asset_update plans"]
    assert create(out)["done"] is True  # the refusals used nothing up


# ------------------------------------------------------------- the last look at the operations


def _tamper(edit):
    def patch(original):
        def compiler(plan, client, cid):
            ops, labels = original(plan, client, cid)
            edit(ops, labels, client, cid)
            return ops, labels

        return compiler

    return patch


def _create(ops, labels, label, field):
    return getattr(ops[labels.index(label)], field).create


def _extra(field, action, resource):
    """Appends one operation of another kind: a remove, or an update of something that exists."""

    def edit(ops, labels, client, cid):
        op = client.get_type("MutateOperation")
        operation = getattr(op, field)
        if action == "remove":
            operation.remove = resource.format(cid=cid)
        else:
            operation.update.resource_name = resource.format(cid=cid)
        ops.append(op)
        labels.append("sneaky")

    return edit


GUARD_CASES = [
    (lambda o, l, c, cid: setattr(_create(o, l, "campaign", "campaign_operation"), "status",
                                  c.enums.CampaignStatusEnum.ENABLED),
     "internal safety check: a campaign would not be paused"),
    (lambda o, l, c, cid: setattr(_create(o, l, "campaign", "campaign_operation").network_settings,
                                  "target_content_network", True),
     "internal safety check: the campaign would not be a Search campaign kept off the Display Network"),
    (lambda o, l, c, cid: setattr(_create(o, l, "campaign", "campaign_operation"), "advertising_channel_type",
                                  c.enums.AdvertisingChannelTypeEnum.DISPLAY),
     "internal safety check: the campaign would not be a Search campaign kept off the Display Network"),
    (lambda o, l, c, cid: setattr(_create(o, l, "budget", "campaign_budget_operation"), "amount_micros", 80_000_001),
     "internal safety check: the budget differs from the plan or is above 3x the cap"),
    (_extra("ad_group_criterion_operation", "remove", "customers/{cid}/adGroupCriteria/1~2"),
     "internal safety check: operation 31 (ad_group_criterion_operation remove) is not allowed in a search_campaign plan"),
    (_extra("campaign_operation", "update", "customers/{cid}/campaigns/1"),
     "internal safety check: operation 31 (campaign_operation update) is not allowed in a search_campaign plan"),
    (_extra("ad_operation", "update", "customers/{cid}/ads/1"),
     "internal safety check: operation 31 (ad_operation update) is not allowed in a search_campaign plan"),
]

OFF_DISPLAY = "internal safety check: the campaign would not be a Search campaign kept off the Display Network"
PARTNERS = "internal safety check: the campaign's search partners setting differs from the plan"


def _networks(edit):
    """Edits the new campaign's network settings on the raw protobuf message."""
    def change(o, l, c, cid):
        campaign = _create(o, l, "campaign", "campaign_operation")
        edit(type(campaign).pb(campaign))
    return change


NETWORK_CASES = [
    _networks(lambda cpb: cpb.ClearField("network_settings")),  # proto-plus would still read False
    _networks(lambda cpb: cpb.network_settings.ClearField("target_content_network")),
    _networks(lambda cpb: cpb.network_settings.ClearField("target_partner_search_network")),
    _networks(lambda cpb: cpb.network_settings.ClearField("target_google_search")),
    _networks(lambda cpb: setattr(cpb.network_settings, "target_partner_search_network", True)),
    _networks(lambda cpb: setattr(cpb.network_settings, "target_google_search", False)),
    _networks(lambda cpb: setattr(cpb.network_settings, "target_youtube", True)),
    _networks(lambda cpb: setattr(cpb.network_settings, "target_google_tv_network", True)),
]

REFERENCES = "internal safety check: the new campaign's parts do not reference each other correctly"
ELSEWHERE = [
    lambda o, l, c, cid: setattr(_create(o, l, "location geoTargetConstants/1000001", "campaign_criterion_operation"),
                                 "campaign", f"customers/{cid}/campaigns/999"),
    lambda o, l, c, cid: setattr(_create(o, l, "campaign", "campaign_operation"), "campaign_budget",
                                 f"customers/{cid}/campaignBudgets/999"),
    lambda o, l, c, cid: setattr(_create(o, l, "ad group[0] Gym membership", "ad_group_operation"), "campaign",
                                 f"customers/{cid}/campaigns/999"),
    lambda o, l, c, cid: setattr(_create(o, l, "ad group[0] keyword[0] gym membership", "ad_group_criterion_operation"),
                                 "ad_group", f"customers/{cid}/adGroups/999"),
    lambda o, l, c, cid: setattr(_create(o, l, "ad group[0] ad", "ad_group_ad_operation"), "ad_group",
                                 f"customers/{cid}/adGroups/999"),
    lambda o, l, c, cid: setattr(_create(o, l, "sitelink[0] link", "campaign_asset_operation"), "campaign",
                                 f"customers/{cid}/campaigns/999"),
    lambda o, l, c, cid: setattr(_create(o, l, "callout[0] link", "campaign_asset_operation"), "asset",
                                 f"customers/{cid}/assets/7"),
    lambda o, l, c, cid: (o.append(o[l.index("budget")]), l.append("budget again")),
]



def _criterion_named(ops, labels, *_):
    create = ops[labels.index("location geoTargetConstants/1000001")].campaign_criterion_operation.create
    create.resource_name = f"customers/{CID}/campaignCriteria/999~1000001"


def _drop(*names):
    def edit(o, l, c, cid):
        for name in names:
            i = l.index(name)
            del o[i], l[i]
    return edit


def _append(field, fill):
    def edit(o, l, c, cid):
        op = c.get_type("MutateOperation")
        fill(getattr(op, field).create, o, l, c, cid)
        o.append(op)
        l.append("sneaky")
    return edit


def _second_location(create, o, l, c, cid):
    create.campaign = _create(o, l, "campaign", "campaign_operation").resource_name
    create.location.geo_target_constant = "geoTargetConstants/1000002"


def _second_keyword(create, o, l, c, cid, status="ENABLED"):
    create.ad_group = _create(o, l, "ad group[0] Gym membership", "ad_group_operation").resource_name
    if status:  # enabled, as the compiler sends a keyword: only the count can catch it
        create.status = getattr(c.enums.AdGroupCriterionStatusEnum, status)
    create.keyword.text = "gym near me"
    create.keyword.match_type = c.enums.KeywordMatchTypeEnum.BROAD


def _negative_made_positive(status="ENABLED"):
    def edit(o, l, c, cid):
        criterion = _create(o, l, "ad group[0] negative keyword[0] free gym", "ad_group_criterion_operation")
        criterion.negative = False
        if status:
            criterion.status = getattr(c.enums.AdGroupCriterionStatusEnum, status)
    return edit


def _relink(o, l, c, cid):  # two links to the first callout, none to the second
    first = _create(o, l, "callout[0] No Joining Fee", "asset_operation").resource_name
    _create(o, l, "callout[1] link", "campaign_asset_operation").asset = first


def _counts(n, what, m):
    return f"internal safety check: {n} {what} operations in the request, {m} in the plan"


HARDENING = [
    (lambda o, l, c, cid: setattr(_create(o, l, "campaign", "campaign_operation").ai_max_setting, "enable_ai_max", True),
     "internal safety check: the campaign would turn on AI Max"),
    (lambda o, l, c, cid: setattr(_create(o, l, "campaign", "campaign_operation"), "keyword_match_type",
                                  c.enums.CampaignKeywordMatchTypeEnum.BROAD),
     "internal safety check: the campaign would set one keyword match type for every keyword"),
    (_relink, "internal safety check: a new asset is not linked to the new campaign"),
    (_drop("ad group[0] keyword[4] cheap gym membership"), _counts(4, "ad group keyword", 5)),
    (_negative_made_positive(), _counts(6, "ad group keyword", 5)),
    (_negative_made_positive(status=""), server.STATUSES),  # a positive keyword without a status
    (lambda o, l, c, cid: setattr(_create(o, l, "campaign negative keyword[0] gym jobs",
                                          "campaign_criterion_operation"), "negative", False),
     _counts(0, "campaign negative keyword", 1)),
    (_append("campaign_criterion_operation", _second_location), _counts(2, "campaign location", 1)),
    (_append("ad_group_criterion_operation", _second_keyword), _counts(6, "ad group keyword", 5)),
    (_append("ad_group_criterion_operation", lambda *a: _second_keyword(*a, status="")), server.STATUSES),
    (_append("ad_group_criterion_operation", lambda *a: _second_keyword(*a, status="PAUSED")), server.STATUSES),
    (_drop("sitelink[3] Find a Gym", "sitelink[3] link"), _counts(3, "sitelink", 4)),
    (lambda o, l, c, cid: (o.append(o[l.index("callout[0] link")]), l.append("callout[0] link again")),
     _counts(10, "asset link", 9)),
    (_drop("ad group[0] ad"), _counts(0, "ad", 1)),
    (_drop("language languageConstants/1000"), _counts(0, "campaign language", 1)),
    (lambda o, l, c, cid: setattr(_create(o, l, "budget", "campaign_budget_operation"), "explicitly_shared", True),
     SHARED_BUDGET),
    (lambda o, l, c, cid: (lambda b: type(b).pb(b).ClearField("explicitly_shared"))(
        _create(o, l, "budget", "campaign_budget_operation")), SHARED_BUDGET),
    (lambda o, l, c, cid: setattr(_create(o, l, "location geoTargetConstants/1000001", "campaign_criterion_operation"),
                                  "negative", True), NEGATIVE_TARGETING),
    (_networks(lambda cpb: setattr(cpb.network_settings, "target_search_network", True)), PARTNERS),
    (_networks(lambda cpb: cpb.network_settings.ClearField("target_search_network")), PARTNERS),
    (lambda o, l, c, cid: setattr(_create(o, l, "campaign", "campaign_operation"), "final_url_suffix", "utm_source=x"),
     "internal safety check: unexpected field final_url_suffix on campaign"),
]

def _second_campaign(o, l, c, cid):
    """A copy of the new campaign, paused and with a temporary ID of its own: every per-operation check passes."""
    first = _create(o, l, "campaign", "campaign_operation")
    op = c.get_type("MutateOperation")
    type(first).pb(op.campaign_operation.create).CopyFrom(type(first).pb(first))
    op.campaign_operation.create.resource_name = f"customers/{cid}/campaigns/-99"
    o.append(op)
    l.append("second campaign")


ELSEWHERE.append(_second_campaign)

REAL_IDS = [
    (renamed("ad group[0] Gym membership", "ad_group_operation", "customers/{cid}/adGroups/999"), TEMP_IDS),
    (renamed("budget", "campaign_budget_operation", "customers/{cid}/campaignBudgets/999"), TEMP_IDS),
    (renamed("campaign", "campaign_operation", "customers/{cid}/campaigns/999"), TEMP_IDS),
    (renamed("sitelink[0] Class Schedule", "asset_operation", "customers/{cid}/assets/999"), TEMP_IDS),
    (renamed("campaign", "campaign_operation", "customers/9999999999/campaigns/-11"), TEMP_IDS),
    (renamed("campaign", "campaign_operation", "customers/{cid}/adGroups/-110"), TEMP_IDS),
    (renamed("callout[0] No Joining Fee", "asset_operation", ""), TEMP_IDS),
    (_criterion_named, TEMP_IDS),
    (portfolio, PORTFOLIO),
]


@pytest.mark.parametrize("edit, expected", GUARD_CASES + [(e, OFF_DISPLAY) for e in NETWORK_CASES]
                         + [(e, REFERENCES) for e in ELSEWHERE] + REAL_IDS + HARDENING)
def test_the_guard_refuses_tampered_operations(search_wired, search_plan, monkeypatch, edit, expected):
    out = checked(search_plan())
    monkeypatch.setattr(server, "compile_search_campaign", _tamper(edit)(server.compile_search_campaign))
    res = create(out)
    assert res == {"done": False, "errors": [expected]}, res
    assert real(search_wired) == []
    before = len(search_wired)
    check = server.check_plan(out["plan_path"])  # check_plan refuses too, without asking Google
    assert check == {"passed": False, "errors": [expected]} and len(search_wired) == before


@pytest.mark.parametrize("partners", [True, False])
def test_search_partners_are_sent_explicitly_as_planned(search_wired, search_plan, partners):
    out = checked(search_plan(search_partners=partners))
    assert create(out)["done"] is True
    [sent] = real(search_wired)
    campaign = next(op.campaign_operation.create for op in sent if "campaign_operation" in op)
    ns = type(campaign).pb(campaign).network_settings
    assert ns.HasField("target_search_network") and ns.target_search_network is partners


def test_the_guard_refuses_partners_the_plan_did_not_ask_for(search_wired, search_plan, monkeypatch):
    out = checked(search_plan(search_partners=True))
    off = _tamper(_networks(lambda cpb: setattr(cpb.network_settings, "target_search_network", False)))
    monkeypatch.setattr(server, "compile_search_campaign", off(server.compile_search_campaign))
    assert create(out) == {"done": False, "errors": [PARTNERS]}
    assert real(search_wired) == []


def test_the_window_shows_the_bidding(search_wired, window, search_plan):  # noqa: F811
    out = checked(search_plan(bidding="MAXIMIZE_CLICKS", max_cpc=1.5))
    assert create(out)["done"] is True
    assert "Bidding: Maximize clicks, max CPC 1.50 EUR" in window.shown[0][1]


LOCATION_OPTION = ("internal safety check: the campaign would not target only people in or regularly in its "
                   "locations")


@pytest.mark.parametrize("edit", [
    lambda o, l, c, cid: setattr(_create(o, l, "campaign", "campaign_operation").geo_target_type_setting,
                                 "positive_geo_target_type", c.enums.PositiveGeoTargetTypeEnum.PRESENCE_OR_INTEREST),
    _networks(lambda cpb: cpb.ClearField("geo_target_type_setting")),
])
def test_the_guard_requires_the_presence_location_option(search_wired, search_plan, monkeypatch, edit):
    out = checked(search_plan())
    monkeypatch.setattr(server, "compile_search_campaign", _tamper(edit)(server.compile_search_campaign))
    assert create(out) == {"done": False, "errors": [LOCATION_OPTION]}
    assert server.check_plan(out["plan_path"]) == {"passed": False, "errors": [LOCATION_OPTION]}
    assert real(search_wired) == []


def test_the_sent_campaign_has_ai_max_off_and_the_presence_option(search_wired, search_plan, ads_client):
    out = checked(search_plan())
    assert create(out)["done"] is True
    [sent] = real(search_wired)
    campaign = next(op.campaign_operation.create for op in sent if "campaign_operation" in op)
    cpb = type(campaign).pb(campaign)
    assert cpb.ai_max_setting.HasField("enable_ai_max") and cpb.ai_max_setting.enable_ai_max is False
    assert campaign.geo_target_type_setting.positive_geo_target_type == (
        ads_client.enums.PositiveGeoTargetTypeEnum.PRESENCE)


def test_ai_max_explicitly_off_is_fine(search_wired, search_plan, monkeypatch):
    off = _tamper(lambda o, l, c, cid: setattr(_create(o, l, "campaign", "campaign_operation").ai_max_setting,
                                               "enable_ai_max", False))
    monkeypatch.setattr(server, "compile_search_campaign", off(server.compile_search_campaign))
    out = checked(search_plan())
    assert create(out)["done"] is True
    [sent] = real(search_wired)
    campaign = next(op.campaign_operation.create for op in sent if "campaign_operation" in op)
    assert type(campaign).pb(campaign).ai_max_setting.HasField("enable_ai_max")


def test_the_guard_refuses_a_budget_above_3x_the_cap_even_when_it_matches_the_plan(search_cfg, search_plan, ads_client):
    """The plan's own budget (80.00), with a cap that makes it more than 3x: only the cap branch can refuse."""
    from gads_builder import plans
    from gads_builder.compile_search import compile_search_campaign

    plan = plans.plan_from_dict(search_plan())
    ops, _ = compile_search_campaign(plan, ads_client, CID)
    assert server._guard(plan, ops, ads_client, search_cfg) == []
    small = dataclasses.replace(search_cfg, daily_budget_cap=26.66)  # 3x = 79.98
    assert server._guard(plan, ops, ads_client, small) == [
        "internal safety check: the budget differs from the plan or is above 3x the cap"]
