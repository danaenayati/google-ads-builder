"""describe_campaign on a Performance Max campaign, with gads.search answered from fake rows."""

import grpc
import pytest

from gads_builder import gads, plans, templates, templates_pmax
from gads_builder.plans_pmax import SEARCH_THEMES, PmaxCampaignPlan
from gads_builder.rules import ERROR

CID = "1234567890"
HEADLINES = ["Classes Every Day", "Join Pulse Fitness", "Train With Expert Coaches"]


class Account:
    """Answers gads.search like a small account with one Performance Max campaign (ID 42) and one asset group
    (ID 7). Records every query."""

    def __init__(self, ads_client):
        self.client, self.E = ads_client, ads_client.enums
        self.queries = []
        self.bidding, self.settings = "MAXIMIZE_CONVERSIONS", {"maximize_conversions.target_cpa_micros": 2_000_000}
        self.portfolio = ""
        self.brand_guidelines = True
        self.automation = {kind: "OPTED_OUT" for kind in templates_pmax.AUTOMATIONS}
        self.option = "PRESENCE"
        self.tracking = ""
        self.merchant = 0
        self.shared_budget = False
        self.goals = [("PURCHASE", "WEBSITE", True), ("DOWNLOAD", "APP", False)]
        self.custom_goal = ""
        self.budget = 50_000_000
        self.campaign_criteria_rows = [("LOCATION", "ENABLED", False, "geoTargetConstants/1000001", None),
                                       ("LANGUAGE", "ENABLED", False, "languageConstants/1000", None)]
        self.campaign_assets = [("BUSINESS_NAME", "ENABLED", "ADVERTISER", f"customers/{CID}/assets/1", "Pulse Fitness"),
                                ("LOGO", "ENABLED", "ADVERTISER", f"customers/{CID}/assets/2", "")]
        self.group_names = {7: "Springfield members"}  # asset group ID -> name, in Google's order
        self.group_extra = {}  # asset group ID -> assets it has besides group_assets
        self.group_status = "ENABLED"
        self.group_urls = ["https://www.example.com/join"]
        self.group_suffix = ""
        self.group_assets = (
            [("HEADLINE", "ENABLED", "ADVERTISER", f"customers/{CID}/assets/{10 + i}", text, None)
             for i, text in enumerate(HEADLINES)]
            + [("LONG_HEADLINE", "ENABLED", "ADVERTISER", f"customers/{CID}/assets/20",
                "Join a friendly Springfield gym with classes every day", None),
               ("DESCRIPTION", "ENABLED", "ADVERTISER", f"customers/{CID}/assets/21", "Classes every day.", None),
               ("DESCRIPTION", "ENABLED", "ADVERTISER", f"customers/{CID}/assets/22", "Cancel any time.", None),
               ("MARKETING_IMAGE", "ENABLED", "ADVERTISER", f"customers/{CID}/assets/30", "", None),
               ("SQUARE_MARKETING_IMAGE", "ENABLED", "ADVERTISER", f"customers/{CID}/assets/31", "", None),
               ("PORTRAIT_MARKETING_IMAGE", "ENABLED", "ADVERTISER", f"customers/{CID}/assets/32", "", None),
               ("YOUTUBE_VIDEO", "ENABLED", "ADVERTISER", f"customers/{CID}/assets/40", "", None),
               ("CALL_TO_ACTION_SELECTION", "ENABLED", "ADVERTISER", f"customers/{CID}/assets/50", "", "SIGN_UP")])
        self.brand_style = {}  # Campaign.brand_guidelines field -> value
        self.term_exclusions, self.restrictions = [], []  # Campaign.text_guidelines
        self.guidelines_error = None  # raised by the brand and text guidelines query
        self.signals = [("", "gym membership springfield"), (f"customers/{CID}/audiences/5", "")]
        self.theme_status = {}  # search theme -> AssetGroupSignalApprovalStatusEnum name (unset: UNSPECIFIED)
        self.audiences = {f"customers/{CID}/audiences/5": ("CUSTOMER", "ENABLED"),
                          f"customers/{CID}/audiences/6": ("ASSET_GROUP", "ENABLED")}

    def row(self):
        return self.client.get_type("GoogleAdsRow")

    def campaign_row(self):
        r = self.row()
        r.campaign.id, r.campaign.name = 42, "Pulse Springfield PMax"
        r.campaign.status = self.E.CampaignStatusEnum.PAUSED
        r.campaign.advertising_channel_type = self.E.AdvertisingChannelTypeEnum.PERFORMANCE_MAX
        r.campaign_budget.amount_micros = self.budget
        r.customer.currency_code = "EUR"
        return r

    def settings_row(self):
        r = self.row()
        c = r.campaign
        c.bidding_strategy_type = getattr(self.E.BiddingStrategyTypeEnum, self.bidding)
        c.bidding_strategy = self.portfolio
        for path, value in self.settings.items():
            part, name = path.split(".")
            setattr(getattr(c, part), name, value)
        c.brand_guidelines_enabled = self.brand_guidelines
        for kind, status in self.automation.items():
            s = type(c).AssetAutomationSetting()
            s.asset_automation_type = getattr(self.E.AssetAutomationTypeEnum, kind)
            s.asset_automation_status = getattr(self.E.AssetAutomationStatusEnum, status)
            c.asset_automation_settings.append(s)
        c.geo_target_type_setting.positive_geo_target_type = getattr(self.E.PositiveGeoTargetTypeEnum, self.option)
        c.tracking_url_template = self.tracking
        c.shopping_setting.merchant_id = self.merchant
        r.campaign_budget.explicitly_shared = self.shared_budget
        return r

    def guidelines_row(self):
        if self.guidelines_error is not None:
            raise self.guidelines_error
        r = self.row()
        c = r.campaign
        for name, value in self.brand_style.items():
            setattr(c.brand_guidelines, name, value)
        c.text_guidelines.term_exclusions.extend(self.term_exclusions)
        for text in self.restrictions:
            c.text_guidelines.messaging_restrictions.append(type(c).MessagingRestriction(
                restriction_text=text,
                restriction_type=self.E.MessagingRestrictionTypeEnum.RESTRICTION_BASED_EXCLUSION))
        return r

    def goal_rows(self):
        rows = []
        for category, origin, biddable in self.goals:
            r = self.row()
            g = r.campaign_conversion_goal
            g.category = getattr(self.E.ConversionActionCategoryEnum, category)
            g.origin = getattr(self.E.ConversionOriginEnum, origin)
            g.biddable = biddable
            rows.append(r)
        return rows

    def goal_config(self):
        r = self.row()
        r.conversion_goal_campaign_config.custom_conversion_goal = self.custom_goal
        return [r]

    def campaign_criteria(self):
        rows = []
        for kind, status, negative, value, modifier in self.campaign_criteria_rows:
            r = self.row()
            cc = r.campaign_criterion
            cc.type_ = getattr(self.E.CriterionTypeEnum, kind)
            cc.status = getattr(self.E.CampaignCriterionStatusEnum, status)
            cc.negative = negative
            if modifier is not None:
                cc.bid_modifier = modifier
            if kind == "LOCATION":
                cc.location.geo_target_constant = value
            elif kind == "LANGUAGE":
                cc.language.language_constant = value
            rows.append(r)
        return rows

    def campaign_asset_rows(self):
        rows = []
        for kind, status, source, asset, text in self.campaign_assets:
            r = self.row()
            r.campaign_asset.field_type = getattr(self.E.AssetFieldTypeEnum, kind)
            r.campaign_asset.status = getattr(self.E.AssetLinkStatusEnum, status)
            r.campaign_asset.source = getattr(self.E.AssetSourceEnum, source)
            r.asset.resource_name, r.asset.text_asset.text = asset, text
            rows.append(r)
        return rows

    def groups(self):
        rows = []
        for gid, name in self.group_names.items():
            r = self.row()
            g = r.asset_group
            g.id, g.name = gid, name
            g.status = getattr(self.E.AssetGroupStatusEnum, self.group_status)
            g.final_urls.extend(self.group_urls)
            g.path1, g.path2 = "gym", "join"
            g.final_url_suffix = self.group_suffix
            rows.append(r)
        return rows

    def group_asset_rows(self):
        rows = []
        for gid, kind, status, source, asset, text, cta in [
                (gid, *x) for gid in self.group_names for x in self.group_assets + self.group_extra.get(gid, [])]:
            r = self.row()
            r.asset_group.id = gid
            link = r.asset_group_asset
            link.field_type = getattr(self.E.AssetFieldTypeEnum, kind)
            link.status = getattr(self.E.AssetLinkStatusEnum, status)
            link.source = getattr(self.E.AssetSourceEnum, source)
            r.asset.resource_name, r.asset.text_asset.text = asset, text
            if cta:
                r.asset.call_to_action_asset.call_to_action = getattr(self.E.CallToActionTypeEnum, cta)
            rows.append(r)
        return rows

    def signal_rows(self):
        rows = []
        for audience, theme in self.signals:
            r = self.row()
            r.asset_group.id = 7
            if audience:  # a oneof: setting one member clears the other
                r.asset_group_signal.audience.audience = audience
            else:
                r.asset_group_signal.search_theme.text = theme
                if theme in self.theme_status:
                    r.asset_group_signal.approval_status = getattr(
                        self.E.AssetGroupSignalApprovalStatusEnum, self.theme_status[theme])
            rows.append(r)
        return rows

    def audience_rows(self, query):
        rows = []
        for name, (scope, status) in self.audiences.items():
            if f"'{name}'" in query:
                r = self.row()
                r.audience.resource_name = name
                r.audience.scope = getattr(self.E.AudienceScopeEnum, scope)
                r.audience.status = getattr(self.E.AudienceStatusEnum, status)
                r.audience.name = "Past members"
                rows.append(r)
        return rows

    def names(self, resource, query):
        known = {"geoTargetConstants/1000001": "Springfield", "languageConstants/1000": "English"}
        rows = []
        for name, value in known.items():
            if f"'{name}'" in query:
                r = self.row()
                obj = getattr(r, resource)
                obj.resource_name, obj.name = name, value
                if resource == "geo_target_constant":
                    obj.canonical_name = "Springfield,Test Region"
                rows.append(r)
        return rows

    def __call__(self, client, customer_id, query):
        assert customer_id == CID
        self.queries.append(query)
        resource = query.split(" FROM ", 1)[1].split(" ", 1)[0]
        if resource == "campaign":
            if "bidding_strategy_type" in query:
                return [self.settings_row()]
            return [self.guidelines_row()] if "text_guidelines" in query else [self.campaign_row()]
        if resource == "audience":
            return self.audience_rows(query)
        answers = {"campaign_conversion_goal": self.goal_rows, "conversion_goal_campaign_config": self.goal_config,
                   "campaign_criterion": self.campaign_criteria, "campaign_asset": self.campaign_asset_rows,
                   "asset_group": self.groups, "asset_group_asset": self.group_asset_rows,
                   "asset_group_signal": self.signal_rows}
        if resource in answers:
            return answers[resource]()
        return self.names(resource, query)


@pytest.fixture
def account(ads_client, monkeypatch):
    fake = Account(ads_client)
    monkeypatch.setattr(templates.gads, "search", fake)
    return fake


def describe(ads_client):
    return templates.describe_campaign(ads_client, CID, "Pulse Springfield PMax")


def test_a_pmax_campaign_becomes_a_pmax_template(ads_client, account, search_cfg):
    out = describe(ads_client)
    assert out["campaign"] == {"id": 42, "name": "Pulse Springfield PMax", "status": "PAUSED",
                               "channel": "PERFORMANCE_MAX"}
    assert out["template_plan"] == {
        "kind": "pmax_campaign",
        "name": "Pulse Springfield PMax (copy)",
        "template": "Pulse Springfield PMax",
        "daily_budget": 50.0,
        "currency": "EUR",
        "bidding": "MAXIMIZE_CONVERSIONS",
        "target_cpa": 2.0,
        "target_roas": None,
        "conversion_goals": [{"category": "PURCHASE", "origin": "WEBSITE"}],
        "locations": ["geoTargetConstants/1000001"],
        "languages": ["languageConstants/1000"],
        "business_name": "Pulse Fitness",
        "logos": [{"asset": f"customers/{CID}/assets/2"}],
        "landscape_logos": [],
        "asset_groups": [{
            "name": "Springfield members", "final_url": "https://www.example.com/join", "path1": "gym", "path2": "join",
            "headlines": HEADLINES, "long_headlines": ["Join a friendly Springfield gym with classes every day"],
            "descriptions": ["Classes every day.", "Cancel any time."],
            "marketing_images": [{"asset": f"customers/{CID}/assets/30"}],
            "square_marketing_images": [{"asset": f"customers/{CID}/assets/31"}],
            "portrait_marketing_images": [{"asset": f"customers/{CID}/assets/32"}],
            "videos": [{"asset": f"customers/{CID}/assets/40"}],
            "call_to_action": "SIGN_UP", "audience_signal": f"customers/{CID}/audiences/5",
            "search_themes": ["gym membership springfield"],
        }],
        "brief": {"names": {"geoTargetConstants/1000001": "Springfield,Test Region", "languageConstants/1000": "English",
                            f"customers/{CID}/audiences/5": "Past members"}},
    }
    assert out["notes"] == []
    assert out["asset_groups"] == [{"id": 7, "name": "Springfield members", "status": "ENABLED"}]
    plan = plans.plan_from_dict(out["template_plan"])  # a valid shape, ready to edit
    assert isinstance(plan, PmaxCampaignPlan) and plan.asset_groups[0].videos[0].asset == f"customers/{CID}/assets/40"
    assert [x for x in plans.validate(plan, search_cfg) if x.level == ERROR] == []  # and it passes draft_plan's rules


GUIDELINES = ("campaign.brand_guidelines.main_color", "campaign.brand_guidelines.accent_color",
              "campaign.brand_guidelines.predefined_font_family", "campaign.text_guidelines.term_exclusions",
              "campaign.text_guidelines.messaging_restrictions")


def test_the_brand_and_text_guidelines_have_a_query_of_their_own(ads_client, account):
    """Google hasn't confirmed these five fields live: if it refuses one, only this query fails."""
    describe(ads_client)
    [settings] = [q for q in account.queries if " FROM campaign " in q and "bidding_strategy_type" in q]
    assert "campaign.brand_guidelines_enabled, " in settings
    assert not any(field in settings for field in GUIDELINES), settings
    [guidelines] = [q for q in account.queries if " FROM campaign " in q and "text_guidelines" in q]
    assert guidelines == f"SELECT campaign.id, {', '.join(GUIDELINES)} FROM campaign WHERE campaign.id = 42"
    [signals] = [q for q in account.queries if " FROM asset_group_signal " in q]
    assert "asset_group_signal.approval_status, " in signals


def test_a_refused_guidelines_query_is_noted_and_the_rest_is_copied(ads_client, account, search_cfg):
    expected = describe(ads_client)["template_plan"]
    account.brand_style = {"main_color": "#1a73e8"}  # never read: the query is refused
    account.guidelines_error = gads.GoogleAdsError(["Unrecognized field in the query: "
                                                    "'campaign.brand_guidelines.main_color'."])
    out = describe(ads_client)
    assert out["notes"] == [
        "couldn't read the campaign's brand or text guidelines (Google's answer: Unrecognized field in the query: "
        "'campaign.brand_guidelines.main_color'.); check them in Google Ads: they are not copied"]
    assert out["template_plan"] == expected
    assert out["asset_groups"] == [{"id": 7, "name": "Springfield members", "status": "ENABLED"}]
    plan = plans.plan_from_dict(out["template_plan"])
    assert [x for x in plans.validate(plan, search_cfg) if x.level == ERROR] == []


def test_a_long_guidelines_refusal_is_cut(ads_client, account):
    account.guidelines_error = gads.GoogleAdsError(["x" * 400, "y" * 400])
    [note] = describe(ads_client)["notes"]
    assert f"(Google's answer: {('x' * 400 + '; ' + 'y' * 400)[:templates_pmax.MAX_ANSWER]})" in note


def _network_failure():
    try:
        raise grpc.RpcError("Stream removed")
    except grpc.RpcError as e:
        return gads._unreachable(e)  # as gads.search raises it: `from None`, the RpcError as its context


def _quota():
    from test_gads import QuotaRpcError

    return gads._unreachable(QuotaRpcError("Too many requests. Retry in 600 seconds."))


@pytest.mark.parametrize("failure", [_network_failure, lambda: gads._unreachable(TimeoutError("timed out")), _quota])
def test_the_daily_quota_or_a_network_failure_on_the_guidelines_is_raised(ads_client, account, failure):
    account.guidelines_error = failure()
    with pytest.raises(gads.GoogleAdsError) as info:
        describe(ads_client)
    assert info.value is account.guidelines_error


def test_every_query_after_the_first_is_by_campaign_id_and_selects_what_it_filters_on(ads_client, account):
    describe(ads_client)
    first, *rest = account.queries
    assert "campaign.name = 'Pulse Springfield PMax'" in first
    by_campaign = [q for q in rest if " FROM geo_target_constant " not in q and " FROM language_constant " not in q
                   and " FROM audience " not in q]
    assert len(by_campaign) == 9
    assert all("WHERE campaign.id = 42" in q and q.startswith("SELECT campaign.id, ") for q in by_campaign)
    assert all("asset_group.status, " in q for q in by_campaign if "asset_group.status != 'REMOVED'" in q)
    assert {q.split(" FROM ", 1)[1].split(" ", 1)[0] for q in by_campaign} == {
        "campaign", "campaign_conversion_goal", "conversion_goal_campaign_config", "campaign_criterion",
        "campaign_asset", "asset_group", "asset_group_asset", "asset_group_signal"}
    audience = [q for q in rest if " FROM audience " in q]
    assert audience == [
        f"SELECT audience.resource_name, audience.scope, audience.status FROM audience WHERE audience.resource_name IN "
        f"('customers/{CID}/audiences/5')",
        f"SELECT audience.resource_name, audience.name FROM audience WHERE audience.resource_name IN "
        f"('customers/{CID}/audiences/5')"]


@pytest.mark.parametrize("bidding, settings, expected", [
    ("MAXIMIZE_CONVERSIONS", {}, {"bidding": "MAXIMIZE_CONVERSIONS", "target_cpa": None, "target_roas": None}),
    ("MAXIMIZE_CONVERSION_VALUE", {"maximize_conversion_value.target_roas": 3.5},
     {"bidding": "MAXIMIZE_CONVERSION_VALUE", "target_cpa": None, "target_roas": 3.5}),
    ("MAXIMIZE_CONVERSION_VALUE", {}, {"bidding": "MAXIMIZE_CONVERSION_VALUE", "target_cpa": None, "target_roas": None}),
])
def test_bidding_is_copied(ads_client, account, bidding, settings, expected):
    account.bidding, account.settings = bidding, settings
    tp = describe(ads_client)["template_plan"]
    assert {key: tp[key] for key in expected} == expected


@pytest.mark.parametrize("setup, note", [
    (lambda a: (setattr(a, "bidding", "TARGET_ROAS"), setattr(a, "settings", {})),
     "bidding TARGET_ROAS is not supported here; the template uses MAXIMIZE_CONVERSIONS"),
    (lambda a: (setattr(a, "portfolio", f"customers/{CID}/biddingStrategies/5"), setattr(a, "settings", {})),
     "the campaign uses a shared (portfolio) bid strategy; its targets are not copied"),
    (lambda a: setattr(a, "brand_guidelines", False),
     "brand guidelines are off in the campaign; the copy turns them on and links the business name and logos to "
     "the campaign"),
    (lambda a: setattr(a, "automation", {"TEXT_ASSET_AUTOMATION": "OPTED_IN"}),
     "the campaign doesn't opt out of final URL expansion, generated headlines and descriptions, enhanced (vertical "
     "and shorter) videos, image cropping and enhancement and videos added from your landing page, social profiles "
     "and YouTube channel; the copy opts out of all of them"),
    (lambda a: a.automation.update(GENERATE_IMAGE_EXTRACTION="OPTED_IN"),
     "the campaign takes images from the landing page; the copy follows the account's setting, which only the Google "
     "Ads UI changes"),
    (lambda a: setattr(a, "option", "PRESENCE_OR_INTEREST"),
     "the campaign's location option is PRESENCE_OR_INTEREST; the copy uses people in or regularly in your "
     "locations (PRESENCE)"),
    (lambda a: setattr(a, "tracking", "https://www.example.com/t?u={lpurl}"),
     "the campaign's tracking template is not copied; set it in Google Ads after creating the campaign"),
    (lambda a: setattr(a, "merchant", 123),
     "the campaign uses a Merchant Center feed (products and listing groups); the copy has none"),
    (lambda a: setattr(a, "shared_budget", True), "the campaign uses a shared budget; the copy gets a budget of its own"),
    (lambda a: setattr(a, "budget", 0), "the campaign has a campaign total budget, not a daily one; set daily_budget"),
    (lambda a: setattr(a, "goals", [("PURCHASE", "WEBSITE", True), ("DOWNLOAD", "APP", True)]),
     "1 app conversion goal(s) are not copied: Performance Max here promotes websites"),
    (lambda a: setattr(a, "goals", [("DOWNLOAD", "APP", False)]),
     "the campaign bids on no website conversion goal; list conversion_goals"),
    (lambda a: (setattr(a, "goals", []), setattr(a, "custom_goal", f"customers/{CID}/customConversionGoals/5")),
     "the campaign bids on a custom conversion goal, which a plan can't hold; list conversion_goals"),
    (lambda a: a.campaign_criteria_rows.extend([("KEYWORD", "ENABLED", True, "", None),
                                                ("DEVICE", "ENABLED", False, "", 0.5)]),
     "campaign-level targeting is not copied: 1 device, 1 keyword; set it in Google Ads after creating the campaign"),
    (lambda a: a.campaign_assets.extend([("SITELINK", "ENABLED", "ADVERTISER", f"customers/{CID}/assets/3", ""),
                                         ("CALLOUT", "ENABLED", "ADVERTISER", f"customers/{CID}/assets/4", "")]),
     "2 campaign-level asset(s) are not copied (callout, sitelink); add them in Google Ads after creating the campaign"),
    (lambda a: a.campaign_assets.append(("LOGO", "PAUSED", "ADVERTISER", f"customers/{CID}/assets/5", "")),
     f"paused campaign logo customers/{CID}/assets/5 not copied"),
    (lambda a: setattr(a, "group_status", "PAUSED"), "paused asset group 'Springfield members' not copied"),
    (lambda a: setattr(a, "group_urls", ["https://www.example.com/join", "https://www.example.com/other"]),
     "asset group 'Springfield members': only its first final URL is copied"),
    (lambda a: setattr(a, "group_suffix", "src=pmax"),
     "asset group 'Springfield members': its final URL suffix is not copied"),
    (lambda a: a.group_assets.append(("YOUTUBE_VIDEO", "ENABLED", "AUTOMATICALLY_CREATED", f"customers/{CID}/assets/41",
                                      "", None)),
     "asset group 'Springfield members': 1 asset(s) Google created automatically are not copied"),
    (lambda a: a.group_assets.append(("HEADLINE", "PAUSED", "ADVERTISER", f"customers/{CID}/assets/15", "Old", None)),
     "asset group 'Springfield members': 1 headline paused asset(s) not copied"),
    (lambda a: a.group_assets.append(("TALL_PORTRAIT_MARKETING_IMAGE", "ENABLED", "ADVERTISER",
                                      f"customers/{CID}/assets/33", "", None)),
     "asset group 'Springfield members': 1 tall portrait marketing image asset(s) are not copied"),
    (lambda a: setattr(a, "signals", [("", "gym"), (f"customers/{CID}/audiences/6", "")]),
     f"asset group 'Springfield members': its audience signal customers/{CID}/audiences/6 was made for that asset "
     "group and can't be reused; choose an account-wide audience (customers/<id>/audiences/<id>) or none"),
    (lambda a: (setattr(a, "signals", [(f"customers/{CID}/audiences/7", "")]),
                a.audiences.update({f"customers/{CID}/audiences/7": ("ASSET_GROUP", "REMOVED")})),
     f"asset group 'Springfield members': its audience signal customers/{CID}/audiences/7 is removed and is not "
     "copied; choose an account-wide audience (customers/<id>/audiences/<id>) or none"),
    (lambda a: setattr(a, "signals", [(f"customers/{CID}/audiences/8", "")]),
     f"asset group 'Springfield members': its audience signal customers/{CID}/audiences/8 was not found in the "
     "account and is not copied; choose an account-wide audience (customers/<id>/audiences/<id>) or none"),
    (lambda a: (a.signals.append((f"customers/{CID}/audiences/9", "")),
                a.audiences.update({f"customers/{CID}/audiences/9": ("CUSTOMER", "REMOVED")})),
     f"asset group 'Springfield members': its audience signal customers/{CID}/audiences/9 is removed and is not "
     "copied"),
    (lambda a: (a.signals.extend([(f"customers/{CID}/audiences/10", ""), (f"customers/{CID}/audiences/11", "")]),
                a.audiences.update({f"customers/{CID}/audiences/10": ("CUSTOMER", "ENABLED"),
                                    f"customers/{CID}/audiences/11": ("CUSTOMER", "ENABLED")})),
     f"asset group 'Springfield members': a plan takes one audience signal; 2 more account-wide audience(s) are "
     f"not copied: customers/{CID}/audiences/10 and customers/{CID}/audiences/11"),
    (lambda a: (a.signals.append(("", "gym near me")), a.theme_status.update({"gym near me": "DISAPPROVED"})),
     "asset group 'Springfield members': 1 disapproved search theme(s) not copied: 'gym near me'"),
    (lambda a: setattr(a, "signals", [("", f"gym theme {n}") for n in range(SEARCH_THEMES + 3)]),
     f"asset group 'Springfield members' has {SEARCH_THEMES + 3} search themes and a plan takes at most "
     f"{SEARCH_THEMES}; only the first {SEARCH_THEMES} are copied"),
    (lambda a: a.brand_style.update(main_color="#1a73e8"),
     "the campaign's brand main color (#1a73e8) is not copied: set it in Google Ads after creating the campaign"),
    (lambda a: a.brand_style.update(accent_color="#fbbc04"),
     "the campaign's brand accent color (#fbbc04) is not copied: set it in Google Ads after creating the campaign"),
    (lambda a: a.brand_style.update(predefined_font_family="Roboto"),
     "the campaign's brand font (Roboto) is not copied: set it in Google Ads after creating the campaign"),
    (lambda a: a.term_exclusions.extend(["cheap", "free"]),
     "the campaign's 2 term exclusion(s) (text guidelines) are not copied: set them in Google Ads after creating "
     "the campaign (they apply to generated text, which the copy turns off)"),
    (lambda a: a.restrictions.append("Never mention prices"),
     "the campaign's 1 messaging restriction(s) (text guidelines) are not copied: set them in Google Ads after "
     "creating the campaign (they apply to generated text, which the copy turns off)"),
    (lambda a: setattr(a, "group_assets", [x for x in a.group_assets if x[0] != "YOUTUBE_VIDEO"]),
     "asset group 'Springfield members' has no video of its own; add one of your YouTube videos"),
    (lambda a: setattr(a, "campaign_assets", []), "the campaign has no business name or logo to copy; add them"),
])
def test_what_is_not_copied_is_noted(ads_client, account, setup, note):
    setup(account)
    notes = describe(ads_client)["notes"]
    assert note in notes, notes


def test_audience_notes_name_their_own_reason(ads_client, account):
    """A removed or missing audience is never said to be made for its asset group; the first enabled account-wide
    audience is copied."""
    account.signals = [(f"customers/{CID}/audiences/7", ""), (f"customers/{CID}/audiences/8", ""),
                       (f"customers/{CID}/audiences/5", ""), (f"customers/{CID}/audiences/10", "")]
    account.audiences.update({f"customers/{CID}/audiences/7": ("ASSET_GROUP", "REMOVED"),
                              f"customers/{CID}/audiences/10": ("CUSTOMER", "ENABLED")})
    out = describe(ads_client)
    assert out["template_plan"]["asset_groups"][0]["audience_signal"] == f"customers/{CID}/audiences/5"
    audience_notes = [n for n in out["notes"] if "audience" in n]
    assert audience_notes == [
        f"asset group 'Springfield members': a plan takes one audience signal; 1 more account-wide audience(s) are "
        f"not copied: customers/{CID}/audiences/10",
        f"asset group 'Springfield members': its audience signal customers/{CID}/audiences/7 is removed and is not "
        "copied",
        f"asset group 'Springfield members': its audience signal customers/{CID}/audiences/8 was not found in the "
        "account and is not copied",
    ]
    assert not any("made for that asset group" in n for n in out["notes"])


def test_search_themes_copy_at_most_the_builders_limit_and_skip_disapproved_ones(ads_client, account, search_cfg):
    account.signals = [("", f"gym theme {n}") for n in range(SEARCH_THEMES + 3)]
    account.theme_status = {"gym theme 0": "DISAPPROVED", "gym theme 1": "UNDER_REVIEW", "gym theme 2": "LIMITED"}
    out = describe(ads_client)
    themes = out["template_plan"]["asset_groups"][0]["search_themes"]
    assert themes == [f"gym theme {n}" for n in range(1, SEARCH_THEMES + 1)]
    assert "asset group 'Springfield members': 1 disapproved search theme(s) not copied: 'gym theme 0'" in out["notes"]
    plan = plans.plan_from_dict(out["template_plan"])
    assert [x for x in plans.validate(plan, search_cfg) if x.level == ERROR] == []


def test_without_guidelines_nothing_about_them_is_noted(ads_client, account):
    notes = describe(ads_client)["notes"]
    assert not any("brand" in n or "text guidelines" in n for n in notes), notes


def test_an_asset_group_scoped_audience_is_not_copied(ads_client, account):
    account.signals = [(f"customers/{CID}/audiences/6", "")]
    assert describe(ads_client)["template_plan"]["asset_groups"][0]["audience_signal"] is None


def test_brand_assets_of_an_asset_group_move_to_the_campaign(ads_client, account):
    account.brand_guidelines = False
    account.campaign_assets = []
    account.group_assets += [("BUSINESS_NAME", "ENABLED", "ADVERTISER", f"customers/{CID}/assets/1", "Pulse Fitness", None),
                             ("LOGO", "ENABLED", "ADVERTISER", f"customers/{CID}/assets/2", "", None),
                             ("LANDSCAPE_LOGO", "ENABLED", "ADVERTISER", f"customers/{CID}/assets/3", "", None)]
    out = describe(ads_client)
    tp = out["template_plan"]
    assert tp["business_name"] == "Pulse Fitness" and tp["logos"] == [{"asset": f"customers/{CID}/assets/2"}]
    assert tp["landscape_logos"] == [{"asset": f"customers/{CID}/assets/3"}]
    assert "the business name and logos of the asset groups are copied to the campaign (brand guidelines)" in out["notes"]
    assert "the campaign has no business name or logo to copy; add them" not in out["notes"]


def test_logos_from_the_asset_groups_stop_at_five_in_all(ads_client, account, search_cfg):
    """Without brand guidelines, each asset group has logos of its own; the campaign takes 5, square and landscape
    together, so the first 5 in Google's order are copied and the rest are noted."""
    def logo(kind, n):
        return kind, "ENABLED", "ADVERTISER", f"customers/{CID}/assets/{n}", "", None

    account.brand_guidelines = False
    account.campaign_assets = []
    account.group_names[8] = "Shelbyville members"
    account.group_extra = {
        7: [("BUSINESS_NAME", "ENABLED", "ADVERTISER", f"customers/{CID}/assets/1", "Pulse Fitness", None),
            logo("LOGO", 60), logo("LOGO", 61), logo("LANDSCAPE_LOGO", 62)],
        8: [logo("LOGO", 63), logo("LOGO", 64), logo("LANDSCAPE_LOGO", 65)],
    }
    out = describe(ads_client)
    tp = out["template_plan"]
    assert tp["logos"] == [{"asset": f"customers/{CID}/assets/{n}"} for n in (60, 61, 63, 64)]
    assert tp["landscape_logos"] == [{"asset": f"customers/{CID}/assets/62"}]
    assert ("the campaign and its asset groups have 6 logos (square and landscape together) and a plan takes at "
            f"most 5; only the first 5 are copied, not customers/{CID}/assets/65") in out["notes"], out["notes"]
    assert len(tp["asset_groups"]) == 2
    plan = plans.plan_from_dict(tp)
    assert [x for x in plans.validate(plan, search_cfg) if x.level == ERROR] == []


def test_a_logo_in_several_asset_groups_counts_once(ads_client, account):
    account.brand_guidelines = False
    account.campaign_assets = []
    account.group_names[8] = "Shelbyville members"
    account.group_assets += [("LOGO", "ENABLED", "ADVERTISER", f"customers/{CID}/assets/{n}", "", None)
                             for n in (60, 61, 62, 63, 64)]  # the same five logos in both asset groups
    out = describe(ads_client)
    assert out["template_plan"]["logos"] == [{"asset": f"customers/{CID}/assets/{n}"} for n in (60, 61, 62, 63, 64)]
    assert not any("logos (square and landscape together)" in n for n in out["notes"]), out["notes"]


def test_a_paused_asset_group_is_not_copied(ads_client, account):
    account.group_status = "PAUSED"
    out = describe(ads_client)
    assert out["template_plan"]["asset_groups"] == []
    assert out["asset_groups"] == []


def test_audience_scopes_reads_only_this_accounts_audiences(ads_client, account):
    found = templates_pmax.audience_scopes(ads_client, CID, [
        f"customers/{CID}/audiences/5", f"customers/{CID}/audiences/6", "customers/9999999999/audiences/5",
        f"customers/{CID}/audiences/\u0661", f"customers/{CID}/audiences/5' OR 1=1"])
    assert found == {f"customers/{CID}/audiences/5": ("CUSTOMER", "ENABLED"),
                     f"customers/{CID}/audiences/6": ("ASSET_GROUP", "ENABLED")}
    assert all("9999999999" not in q and "OR 1=1" not in q for q in account.queries)
    assert templates_pmax.audience_scopes(ads_client, CID, []) == {} and len(account.queries) == 1


def test_list_campaigns_gives_a_pmax_campaigns_bidding_as_its_goal(ads_client, monkeypatch):
    row = ads_client.get_type("GoogleAdsRow")
    row.campaign.id, row.campaign.name = 42, "Pulse Springfield PMax"
    row.campaign.bidding_strategy_type = ads_client.enums.BiddingStrategyTypeEnum.MAXIMIZE_CONVERSION_VALUE
    row.campaign_budget.amount_micros = 50_000_000
    queries = []
    monkeypatch.setattr(templates.gads, "search", lambda client, cid, query: queries.append(query) or [row])
    [found] = templates.list_campaigns(ads_client, CID, "pmax", "PERFORMANCE_MAX")
    assert found["goal"] == "MAXIMIZE_CONVERSION_VALUE" and found["budget_micros"] == 50_000_000
    assert "campaign.advertising_channel_type = PERFORMANCE_MAX" in queries[0]
