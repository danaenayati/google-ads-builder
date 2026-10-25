"""describe_campaign on a Demand Gen campaign, with gads.search answered from fake rows."""

import pytest

from gads_builder import plans, templates
from gads_builder.plans_demand_gen import DemandGenCampaignPlan

CID = "1234567890"


class Account:
    """Answers gads.search like a small account with one Demand Gen campaign (ID 42). Records every query."""

    def __init__(self, ads_client):
        self.client, self.E = ads_client, ads_client.enums
        self.queries = []
        self.bidding, self.settings = "TARGET_CPA", {"target_cpa.target_cpa_micros": 2_000_000}
        self.portfolio = ""
        self.upgraded = True
        self.option = "PRESENCE"
        self.tracking = ""  # the campaign's tracking template
        self.shared_budget = False
        self.campaign_criteria_rows = []  # (type, status, negative, value, bid modifier or None)
        self.group_status = "ENABLED"
        self.group_suffix = ""  # the ad group's final URL suffix
        self.goals = [("PURCHASE", "WEBSITE", True), ("SUBMIT_LEAD_FORM", "WEBSITE", False), ("DOWNLOAD", "APP", False)]
        self.channel_config = "SELECTED_CHANNELS"
        self.channel_strategy = "UNSPECIFIED"
        self.selected = {"youtube_in_stream": True, "youtube_shorts": True, "discover": True}
        self.optimized = False
        self.criteria = [("LOCATION", False, "geoTargetConstants/1000001", "ENABLED"),
                         ("LANGUAGE", False, "languageConstants/1000", "ENABLED"),
                         ("USER_LIST", False, f"customers/{CID}/userLists/5", "ENABLED")]
        self.ad_status = "ENABLED"
        self.cta_text = ""
        self.video_cta = False
        self.tall = 0
        self.extra_ad = None  # an AdTypeEnum name for a third ad
        self.budget_micros = 30_000_000  # 0: a campaign total budget
        self.classic = 0  # the image ad's classic display images
        self.companion = False  # the video ad's companion banner
        self.breadcrumbs = ("", "")
        self.ad_tracking = {}  # the video ad's own tracking fields: {field: value}
        self.mobile_urls = []  # the image ad's mobile final URLs

    def row(self):
        return self.client.get_type("GoogleAdsRow")

    def campaign_row(self):
        r = self.row()
        r.campaign.id, r.campaign.name = 42, "Pulse Springfield DG"
        r.campaign.status = self.E.CampaignStatusEnum.ENABLED
        r.campaign.advertising_channel_type = self.E.AdvertisingChannelTypeEnum.DEMAND_GEN
        r.campaign_budget.amount_micros = self.budget_micros
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
        c.demand_gen_campaign_settings.upgraded_targeting = self.upgraded
        c.geo_target_type_setting.positive_geo_target_type = getattr(self.E.PositiveGeoTargetTypeEnum, self.option)
        c.tracking_url_template = self.tracking
        r.campaign_budget.explicitly_shared = self.shared_budget
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

    def groups(self):
        r = self.row()
        g = r.ad_group
        g.id, g.name = 7, "Springfield members"
        g.status = getattr(self.E.AdGroupStatusEnum, self.group_status)
        g.final_url_suffix = self.group_suffix
        g.optimized_targeting_enabled = self.optimized
        controls = g.demand_gen_ad_group_settings.channel_controls
        controls.channel_config = getattr(self.E.DemandGenChannelConfigEnum, self.channel_config)
        if self.channel_config == "SELECTED_CHANNELS":
            for name, on in self.selected.items():
                setattr(controls.selected_channels, name, on)
        else:
            controls.channel_strategy = getattr(self.E.DemandGenChannelStrategyEnum, self.channel_strategy)
        return [r]

    def group_criteria(self):
        rows = []
        for kind, negative, value, status in self.criteria:
            r = self.row()
            r.ad_group.id = 7
            crit = r.ad_group_criterion
            crit.type_ = getattr(self.E.CriterionTypeEnum, kind)
            crit.status = getattr(self.E.AdGroupCriterionStatusEnum, status)
            crit.negative = negative
            if kind == "LOCATION":
                crit.location.geo_target_constant = value
            elif kind == "LANGUAGE":
                crit.language.language_constant = value
            elif kind == "USER_LIST":
                crit.user_list.user_list = value
            rows.append(r)
        return rows

    def _text(self, text):
        asset = self.client.get_type("AdTextAsset")
        asset.text = text
        return asset

    def _assets(self, kind, names):
        out = []
        for name in names:
            a = self.client.get_type(kind)
            a.asset = name
            out.append(a)
        return out

    def ads(self):
        image = self.row()
        image.ad_group.id, image.ad_group.name = 7, "Springfield members"
        image.ad_group_ad.status = getattr(self.E.AdGroupAdStatusEnum, self.ad_status)
        ad = image.ad_group_ad.ad
        ad.resource_name, ad.type_ = f"customers/{CID}/ads/101", self.E.AdTypeEnum.DEMAND_GEN_MULTI_ASSET_AD
        ad.final_urls.append("https://www.example.com/join")
        info = ad.demand_gen_multi_asset_ad
        info.headlines.extend([self._text("Classes Every Day"), self._text("Join Pulse Fitness")])
        info.descriptions.append(self._text("Classes every day, coaches who know your name."))
        info.business_name = "Pulse Fitness"
        info.call_to_action_text = self.cta_text
        info.marketing_images.extend(self._assets("AdImageAsset", [f"customers/{CID}/assets/1"]))
        info.square_marketing_images.extend(self._assets("AdImageAsset", [f"customers/{CID}/assets/2"]))
        info.portrait_marketing_images.extend(self._assets("AdImageAsset", [f"customers/{CID}/assets/3"]))
        info.tall_portrait_marketing_images.extend(
            self._assets("AdImageAsset", [f"customers/{CID}/assets/{10 + i}" for i in range(self.tall)]))
        info.logo_images.extend(self._assets("AdImageAsset", [f"customers/{CID}/assets/4"]))
        info.classic_display_images.extend(
            self._assets("AdImageAsset", [f"customers/{CID}/assets/{20 + i}" for i in range(self.classic)]))
        ad.final_mobile_urls.extend(self.mobile_urls)
        video = self.row()
        video.ad_group.id, video.ad_group.name = 7, "Springfield members"
        video.ad_group_ad.status = getattr(self.E.AdGroupAdStatusEnum, self.ad_status)
        ad = video.ad_group_ad.ad
        ad.resource_name, ad.type_ = f"customers/{CID}/ads/102", self.E.AdTypeEnum.DEMAND_GEN_VIDEO_RESPONSIVE_AD
        ad.final_urls.append("https://www.example.com/classes")
        info = ad.demand_gen_video_responsive_ad
        info.videos.extend(self._assets("AdVideoAsset", [f"customers/{CID}/assets/8"]))
        info.headlines.append(self._text("Train With Expert Coaches"))
        info.long_headlines.append(self._text("Yoga, spin, boxing and strength classes for every level"))
        info.descriptions.append(self._text("Pay monthly in the app and cancel any time."))
        info.business_name.text = "Pulse Fitness"
        info.logo_images.extend(self._assets("AdImageAsset", [f"customers/{CID}/assets/4"]))
        if self.video_cta:
            info.call_to_actions.extend(self._assets("AdCallToActionAsset", [f"customers/{CID}/assets/9"]))
        if self.companion:
            info.companion_banners.extend(self._assets("AdImageAsset", [f"customers/{CID}/assets/30"]))
        info.breadcrumb1, info.breadcrumb2 = self.breadcrumbs
        for field, value in self.ad_tracking.items():
            if field == "url_custom_parameters":
                parameter = self.client.get_type("CustomParameter")
                parameter.key, parameter.value = value
                ad.url_custom_parameters.append(parameter)
            else:
                setattr(ad, field, value)
        rows = [image, video]
        if self.extra_ad:
            extra = self.row()
            extra.ad_group.id, extra.ad_group.name = 7, "Springfield members"
            extra.ad_group_ad.status = self.E.AdGroupAdStatusEnum.ENABLED
            extra.ad_group_ad.ad.resource_name = f"customers/{CID}/ads/103"
            extra.ad_group_ad.ad.type_ = getattr(self.E.AdTypeEnum, self.extra_ad)
            extra.ad_group_ad.ad.final_urls.append("https://www.example.com/shop")
            rows.append(extra)
        return rows

    def names(self, resource, query):
        known = {"geoTargetConstants/1000001": "Springfield", "languageConstants/1000": "English",
                 f"customers/{CID}/userLists/5": "Past members"}
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
            return [self.settings_row()] if "bidding_strategy_type" in query else [self.campaign_row()]
        answers = {"campaign_conversion_goal": self.goal_rows, "campaign_criterion": self.campaign_criteria,
                   "ad_group": self.groups, "ad_group_criterion": self.group_criteria, "ad_group_ad": self.ads}
        if resource in answers:
            return answers[resource]()
        return self.names(resource, query)


@pytest.fixture
def account(ads_client, monkeypatch):
    fake = Account(ads_client)
    monkeypatch.setattr(templates.gads, "search", fake)
    return fake


def describe(ads_client):
    return templates.describe_campaign(ads_client, CID, "Pulse Springfield DG")


def test_a_demand_gen_campaign_becomes_a_demand_gen_template(ads_client, account):
    out = describe(ads_client)
    assert out["campaign"] == {"id": 42, "name": "Pulse Springfield DG", "status": "ENABLED", "channel": "DEMAND_GEN"}
    assert out["template_plan"] == {
        "kind": "demand_gen_campaign",
        "name": "Pulse Springfield DG (copy)",
        "template": "Pulse Springfield DG",
        "daily_budget": 30.0,
        "currency": "EUR",
        "bidding": "TARGET_CPA",
        "target_cpa": 2.0,
        "max_cpc": None,
        "conversion_goals": [{"category": "PURCHASE", "origin": "WEBSITE"}],
        "ad_groups": [{
            "name": "Springfield members",
            "locations": ["geoTargetConstants/1000001"],
            "languages": ["languageConstants/1000"],
            "channels": ["youtube_in_stream", "youtube_shorts", "discover"],
            "audiences": [f"customers/{CID}/userLists/5"],
            "ads": [
                {"type": "image", "headlines": ["Classes Every Day", "Join Pulse Fitness"],
                 "descriptions": ["Classes every day, coaches who know your name."], "business_name": "Pulse Fitness",
                 "final_url": "https://www.example.com/join",
                 "marketing_images": [{"asset": f"customers/{CID}/assets/1"}],
                 "square_marketing_images": [{"asset": f"customers/{CID}/assets/2"}],
                 "portrait_marketing_images": [{"asset": f"customers/{CID}/assets/3"}],
                 "logo_images": [{"asset": f"customers/{CID}/assets/4"}]},
                {"type": "video", "videos": [{"asset": f"customers/{CID}/assets/8"}],
                 "headlines": ["Train With Expert Coaches"],
                 "long_headlines": ["Yoga, spin, boxing and strength classes for every level"],
                 "descriptions": ["Pay monthly in the app and cancel any time."], "business_name": "Pulse Fitness",
                 "final_url": "https://www.example.com/classes", "logo_images": [{"asset": f"customers/{CID}/assets/4"}],
                 "call_to_action": None},
            ],
        }],
        "brief": {"names": {"geoTargetConstants/1000001": "Springfield,Test Region", "languageConstants/1000": "English",
                            f"customers/{CID}/userLists/5": "Past members"}},
    }
    assert out["notes"] == []
    assert out["ads"] == [
        {"ad": f"customers/{CID}/ads/101", "ad_type": "DEMAND_GEN_MULTI_ASSET_AD", "ad_group": "Springfield members",
         "status": "ENABLED"},
        {"ad": f"customers/{CID}/ads/102", "ad_type": "DEMAND_GEN_VIDEO_RESPONSIVE_AD",
         "ad_group": "Springfield members", "status": "ENABLED"}]
    plan = plans.plan_from_dict(out["template_plan"])  # a valid shape, ready to edit
    assert isinstance(plan, DemandGenCampaignPlan)
    assert plan.ad_groups[0].ads[1].videos[0].asset == f"customers/{CID}/assets/8"


def test_every_query_after_the_first_is_by_campaign_id_and_selects_what_it_filters_on(ads_client, account):
    describe(ads_client)
    first, *rest = account.queries
    assert "campaign.name = 'Pulse Springfield DG'" in first
    by_campaign = [q for q in rest if " FROM geo_target_constant " not in q and " FROM language_constant " not in q
                   and " FROM user_list " not in q]
    assert len(by_campaign) == 6
    assert all("WHERE campaign.id = 42" in q and q.startswith("SELECT campaign.id, ") for q in by_campaign)
    assert all("ad_group.status, " in q for q in by_campaign if "ad_group.status != 'REMOVED'" in q)
    assert {q.split(" FROM ", 1)[1].split(" ", 1)[0] for q in by_campaign} == {
        "campaign", "campaign_conversion_goal", "campaign_criterion", "ad_group", "ad_group_criterion", "ad_group_ad"}


@pytest.mark.parametrize("bidding, settings, expected", [
    ("MAXIMIZE_CONVERSIONS", {}, {"bidding": "MAXIMIZE_CONVERSIONS", "target_cpa": None, "max_cpc": None}),
    ("MAXIMIZE_CONVERSIONS", {"maximize_conversions.target_cpa_micros": 2_500_000},
     {"bidding": "TARGET_CPA", "target_cpa": 2.5, "max_cpc": None}),
    ("TARGET_CPA", {"target_cpa.target_cpa_micros": 2_000_000},
     {"bidding": "TARGET_CPA", "target_cpa": 2.0, "max_cpc": None}),
    ("TARGET_SPEND", {}, {"bidding": "MAXIMIZE_CLICKS", "target_cpa": None, "max_cpc": None}),
    ("TARGET_SPEND", {"target_spend.cpc_bid_ceiling_micros": 800_000},
     {"bidding": "MAXIMIZE_CLICKS", "target_cpa": None, "max_cpc": None}),
])
def test_bidding_is_copied(ads_client, account, bidding, settings, expected):
    account.bidding, account.settings = bidding, settings
    tp = describe(ads_client)["template_plan"]
    assert {key: tp[key] for key in expected} == expected


@pytest.mark.parametrize("bidding, settings, note", [
    ("MAXIMIZE_CONVERSIONS", {"maximize_conversions.target_cpa_micros": 2_500_000},
     "the campaign maximizes conversions with a target cost per conversion of 2.5; Demand Gen takes that as "
     "bidding TARGET_CPA"),
    ("TARGET_SPEND", {"target_spend.cpc_bid_ceiling_micros": 800_000},
     "the campaign's maximum CPC of 0.8 is not copied; Demand Gen doesn't accept one with MAXIMIZE_CLICKS"),
])
def test_bidding_google_refuses_on_a_new_campaign_is_mapped_and_noted(ads_client, account, bidding, settings, note):
    account.bidding, account.settings = bidding, settings
    assert note in describe(ads_client)["notes"]


@pytest.mark.parametrize("setup, note", [
    (lambda a: (setattr(a, "bidding", "TARGET_ROAS"), setattr(a, "settings", {})),
     "bidding TARGET_ROAS is not supported here; the template uses MAXIMIZE_CONVERSIONS"),
    (lambda a: (setattr(a, "portfolio", f"customers/{CID}/biddingStrategies/5"), setattr(a, "settings", {})),
     "the campaign uses a shared (portfolio) bid strategy; its targets are not copied"),
    (lambda a: setattr(a, "shared_budget", True), "the campaign uses a shared budget; the copy gets a budget of its own"),
    (lambda a: setattr(a, "goals", [("PURCHASE", "WEBSITE", True), ("DOWNLOAD", "APP", True)]),
     "1 app conversion goal(s) are not copied: Demand Gen here promotes websites"),
    (lambda a: setattr(a, "goals", [("DOWNLOAD", "APP", False)]),
     "the campaign bids on no website conversion goal; list conversion_goals"),
    (lambda a: a.selected.update(display=True, maps=True),
     "ad group 'Springfield members' also runs on display and maps; the copy doesn't"),
    (lambda a: (setattr(a, "channel_config", "CHANNEL_STRATEGY"), setattr(a, "channel_strategy", "ALL_CHANNELS")),
     "ad group 'Springfield members' uses all channels, including third-party Display; the copy uses YouTube, "
     "Discover and Gmail only"),
    (lambda a: (setattr(a, "channel_config", "CHANNEL_STRATEGY"),
                setattr(a, "channel_strategy", "ALL_OWNED_AND_OPERATED_CHANNELS")),
     "ad group 'Springfield members' lets Google choose among its own channels; the copy names YouTube, Discover "
     "and Gmail"),
    (lambda a: setattr(a, "optimized", True),
     "ad group 'Springfield members' has optimized targeting on; the copy doesn't turn it on, but Google may; check "
     "quality_report after creation"),
    (lambda a: a.campaign_criteria_rows.extend([("LOCATION", "ENABLED", False, "geoTargetConstants/1000002", None),
                                                ("LANGUAGE", "ENABLED", False, "languageConstants/1019", None)]),
     "campaign-level targeting is not copied: 1 language, 1 location; with upgraded targeting the copy uses each ad "
     "group's own locations and languages, so add them there if they matter"),
    (lambda a: setattr(a, "budget_micros", 0),
     "the campaign has a campaign total budget, not a daily one; set daily_budget"),
    (lambda a: setattr(a, "classic", 2), f"ad customers/{CID}/ads/101: 2 classic display image(s) are not copied"),
    (lambda a: setattr(a, "mobile_urls", ["https://m.example.com/join"]),
     f"ad customers/{CID}/ads/101: its mobile final URLs are not copied"),
    (lambda a: setattr(a, "companion", True), f"ad customers/{CID}/ads/102: its companion banner is not copied"),
    (lambda a: setattr(a, "breadcrumbs", ("classes", "")),
     f"ad customers/{CID}/ads/102: its breadcrumbs are not copied"),
    (lambda a: setattr(a, "ad_tracking", {"tracking_url_template": "https://www.example.com/t?u={lpurl}",
                                          "url_custom_parameters": ("season", "spring")}),
     f"ad customers/{CID}/ads/102: its tracking template and custom parameters are not copied; set them in Google "
     "Ads after creating the campaign"),
    (lambda a: setattr(a, "ad_tracking", {"final_url_suffix": "src=dg"}),
     f"ad customers/{CID}/ads/102: its final URL suffix is not copied; set it in Google Ads after creating the "
     "campaign"),
    (lambda a: a.criteria.append(("LOCATION", True, "geoTargetConstants/1000002", "ENABLED")),
     "1 excluded location(s), language(s) or list(s) are not copied"),
    (lambda a: a.criteria.extend([("AGE_RANGE", False, "", "ENABLED"), ("AUDIENCE", False, "", "ENABLED")]),
     "ad-group-level targeting is not copied: 1 age range, 1 audience; set it in Google Ads after creating the "
     "campaign"),
    (lambda a: a.criteria.append(("USER_LIST", False, f"customers/{CID}/userLists/6", "PAUSED")),
     f"paused user list customers/{CID}/userLists/6 in ad group 'Springfield members' not copied"),
    (lambda a: setattr(a, "option", "PRESENCE_OR_INTEREST"),
     "the campaign's location option is PRESENCE_OR_INTEREST; the copy uses people in or regularly in your "
     "locations (PRESENCE)"),
    (lambda a: setattr(a, "tracking", "https://www.example.com/t?u={lpurl}"),
     "the campaign's tracking template is not copied; set it in Google Ads after creating the campaign"),
    (lambda a: setattr(a, "group_suffix", "src=dg"), "ad group 'Springfield members': its final URL suffix is not copied"),
    (lambda a: a.campaign_criteria_rows.extend([("AD_SCHEDULE", "ENABLED", False, "", None),
                                                ("DEVICE", "ENABLED", False, "", 0.5)]),
     "campaign-level targeting is not copied: 1 ad schedule, 1 device; set it in Google Ads after creating the "
     "campaign"),
    (lambda a: setattr(a, "ad_status", "PAUSED"),
     "ad group 'Springfield members' has no enabled ad; its ads are copied from paused ones"),
    (lambda a: setattr(a, "cta_text", "Sign up"),
     f"ad customers/{CID}/ads/101: its call to action 'Sign up' is not copied; Google chooses one"),
    (lambda a: setattr(a, "tall", 2), f"ad customers/{CID}/ads/101: 2 9:16 image(s) are not copied"),
    (lambda a: setattr(a, "video_cta", True),
     f"ad customers/{CID}/ads/102: its call to action is not copied; set call_to_action"),
    (lambda a: setattr(a, "extra_ad", "DEMAND_GEN_CAROUSEL_AD"),
     f"ad customers/{CID}/ads/103 is a DEMAND_GEN_CAROUSEL_AD and is not copied"),
])
def test_what_is_not_copied_is_noted(ads_client, account, setup, note):
    setup(account)
    notes = describe(ads_client)["notes"]
    assert note in notes, notes


def test_the_ad_query_reads_what_is_not_copied(ads_client, account):
    describe(ads_client)
    query = next(q for q in account.queries if " FROM ad_group_ad " in q)
    for field in ("demand_gen_multi_asset_ad.classic_display_images", "demand_gen_video_responsive_ad.companion_banners",
                  "demand_gen_video_responsive_ad.breadcrumb1", "demand_gen_video_responsive_ad.breadcrumb2",
                  "ad.tracking_url_template", "ad.final_url_suffix", "ad.url_custom_parameters", "ad.final_mobile_urls"):
        assert f"ad_group_ad.ad.{field}" in query or f"ad_group_ad.{field}" in query, field


def test_with_upgraded_targeting_the_ad_groups_keep_their_own_targeting(ads_client, account):
    account.campaign_criteria_rows = [("LOCATION", "ENABLED", False, "geoTargetConstants/1000002", None)]
    group = describe(ads_client)["template_plan"]["ad_groups"][0]
    assert group["locations"] == ["geoTargetConstants/1000001"]


def test_a_total_budget_is_copied_as_zero_to_be_set(ads_client, account):
    account.budget_micros = 0
    assert describe(ads_client)["template_plan"]["daily_budget"] == 0.0


def test_a_paused_ad_group_is_not_copied(ads_client, account):
    account.group_status = "PAUSED"
    out = describe(ads_client)
    assert out["template_plan"]["ad_groups"] == []
    assert "paused ad group 'Springfield members' not copied" in out["notes"]
    assert [a["ad"] for a in out["ads"]] == [f"customers/{CID}/ads/101", f"customers/{CID}/ads/102"]


def test_devices_without_a_bid_change_are_not_targeting(ads_client, account):
    account.campaign_criteria_rows = [("DEVICE", "ENABLED", False, "", None), ("DEVICE", "ENABLED", False, "", 1.0)]
    assert describe(ads_client)["notes"] == []


def test_campaign_level_targeting_goes_on_each_ad_group(ads_client, account):
    account.upgraded = False
    account.campaign_criteria_rows = [("LOCATION", "ENABLED", False, "geoTargetConstants/1000002", None),
                                      ("LANGUAGE", "ENABLED", False, "languageConstants/1000", None),
                                      ("LOCATION", "PAUSED", False, "geoTargetConstants/1000003", None),
                                      ("LOCATION", "ENABLED", True, "geoTargetConstants/1000004", None)]
    out = describe(ads_client)
    group = out["template_plan"]["ad_groups"][0]
    assert group["locations"] == ["geoTargetConstants/1000002"] and group["languages"] == ["languageConstants/1000"]
    assert group["audiences"] == [f"customers/{CID}/userLists/5"]
    assert ("the campaign targets locations and languages at campaign level; the copy puts them on each ad group "
            "(upgraded targeting)") in out["notes"]
    assert "paused location geoTargetConstants/1000003 not copied" in out["notes"]
    assert "1 excluded campaign location(s) or language(s) are not copied" in out["notes"]


def test_an_ad_group_without_a_copyable_ad_is_noted(ads_client, account, monkeypatch):
    monkeypatch.setattr(account, "ads", lambda: [])
    out = describe(ads_client)
    assert out["template_plan"]["ad_groups"][0]["ads"] == []
    assert "ad group 'Springfield members' has no image or video ad; write one" in out["notes"]
