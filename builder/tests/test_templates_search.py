"""describe_campaign on a Search campaign, with gads.search answered from fake rows."""

import pytest

from gads_builder import plans, templates

CID = "1234567890"


class Account:
    """Answers gads.search like a small account with one Search campaign (ID 42). Records every query."""

    def __init__(self, ads_client):
        self.client = ads_client
        self.E = ads_client.enums
        self.queries = []
        self.channel = "SEARCH"
        self.bidding = "TARGET_CPA"
        self.settings = {"target_cpa.target_cpa_micros": 12_000_000}
        self.portfolio = ""
        self.display = False
        self.partners = True
        self.shared_budget = False
        self.proximity = 0  # radius targets
        self.shared_lists = []  # names of shared negative keyword lists on the campaign
        self.account_assets = []  # field type names of account-level assets
        self.group_assets = []  # field type names of ad-group-level assets
        self.pinned = False  # the enabled ad's first headline is pinned
        self.extra_groups = 0
        self.final_url = "https://www.example.com/gym"
        self.geo_option = "PRESENCE"  # the campaign's location option
        self.ai_max = False
        self.campaign_tracking = {}  # Campaign field -> value (custom parameters as (key, value) pairs)
        self.group_tracking = {}  # ad group ID -> {AdGroup field: value}
        self.group_status = {}  # ad group ID -> status name; ENABLED when not listed
        self.criterion_status = {}  # a campaign criterion's location, language or keyword -> status name
        self.paused_keywords = set()  # ad group keyword texts whose criterion is PAUSED
        self.paused_assets = set()  # asset field type names whose campaign asset link is PAUSED
        self.other_criteria = []  # campaign criterion type names other than location, language and keyword
        self.other_group_criteria = []  # (ad group ID, criterion type name) other than keywords

    def row(self):
        return self.client.get_type("GoogleAdsRow")

    def campaign_row(self):
        r = self.row()
        r.campaign.id = 42
        r.campaign.name = "Springfield Gyms"
        r.campaign.status = self.E.CampaignStatusEnum.ENABLED
        r.campaign.advertising_channel_type = getattr(self.E.AdvertisingChannelTypeEnum, self.channel)
        r.campaign_budget.amount_micros = 80_000_000
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
        c.network_settings.target_search_network = self.partners
        c.network_settings.target_content_network = self.display
        c.geo_target_type_setting.positive_geo_target_type = getattr(self.E.PositiveGeoTargetTypeEnum, self.geo_option)
        c.ai_max_setting.enable_ai_max = self.ai_max
        self._tracking(c, self.campaign_tracking)
        r.campaign_budget.explicitly_shared = self.shared_budget
        return r

    def _tracking(self, obj, fields):
        for name, value in fields.items():
            if name == "url_custom_parameters":
                for key, val in value:
                    param = self.client.get_type("CustomParameter")
                    param.key, param.value = key, val
                    obj.url_custom_parameters.append(param)
            else:
                setattr(obj, name, value)

    def criteria(self):
        rows = []
        for kind, negative, value in [("LOCATION", False, "geoTargetConstants/1000001"),
                                      ("LOCATION", True, "geoTargetConstants/1000002"),
                                      ("LANGUAGE", False, "languageConstants/1000"),
                                      ("KEYWORD", True, "gym jobs")] + [("PROXIMITY", False, "")] * self.proximity:
            r = self.row()
            cc = r.campaign_criterion
            cc.type_ = getattr(self.E.CriterionTypeEnum, kind)
            cc.negative = negative
            cc.status = getattr(self.E.CampaignCriterionStatusEnum, self.criterion_status.get(value, "ENABLED"))
            if kind == "LOCATION":
                cc.location.geo_target_constant = value
            elif kind == "LANGUAGE":
                cc.language.language_constant = value
            else:
                cc.keyword.text = value
                cc.keyword.match_type = self.E.KeywordMatchTypeEnum.PHRASE
            rows.append(r)
        for kind in self.other_criteria:
            kind, modifier = kind if isinstance(kind, tuple) else (kind, None)
            r = self.row()
            r.campaign_criterion.type_ = getattr(self.E.CriterionTypeEnum, kind)
            r.campaign_criterion.status = self.E.CampaignCriterionStatusEnum.ENABLED
            if modifier is not None:
                r.campaign_criterion.bid_modifier = modifier
            rows.append(r)
        return rows

    def ad_groups(self):
        rows = []
        extra = [(100 + i, f"Extra group {i}", 0) for i in range(self.extra_groups)]
        for gid, name, bid in [(7, "Gym membership", 1_200_000), (8, "Yoga classes", 900_000), *extra]:
            r = self.row()
            r.ad_group.id, r.ad_group.name, r.ad_group.cpc_bid_micros = gid, name, bid
            r.ad_group.status = getattr(self.E.AdGroupStatusEnum, self.group_status.get(gid, "ENABLED"))
            self._tracking(r.ad_group, self.group_tracking.get(gid, {}))
            rows.append(r)
        return rows

    def keywords(self):
        rows = []
        for gid, text, match, negative in [(7, "gym membership", "PHRASE", False), (7, "monthly gym pass", "EXACT", False),
                                           (7, "free gym", "EXACT", True), (8, "yoga classes", "BROAD", False)]:
            r = self.row()
            r.ad_group.id = gid
            crit = r.ad_group_criterion
            crit.type_ = self.E.CriterionTypeEnum.KEYWORD
            crit.status = getattr(self.E.AdGroupCriterionStatusEnum,
                                  "PAUSED" if text in self.paused_keywords else "ENABLED")
            crit.negative = negative
            crit.keyword.text = text
            crit.keyword.match_type = getattr(self.E.KeywordMatchTypeEnum, match)
            rows.append(r)
        for gid, kind in self.other_group_criteria:
            r = self.row()
            r.ad_group.id = gid
            r.ad_group_criterion.type_ = getattr(self.E.CriterionTypeEnum, kind)
            r.ad_group_criterion.status = self.E.AdGroupCriterionStatusEnum.ENABLED
            rows.append(r)
        return rows

    def ads(self):
        rows = []
        for ad_id, gid, group, status, headlines in [
            (101, 7, "Gym membership", "PAUSED", ["Old headline one", "Old headline two", "Old headline three"]),
            (102, 7, "Gym membership", "ENABLED", ["Gym Membership Deals", "Flexible Gym Membership", "Join Today"]),
        ]:
            r = self.row()
            r.ad_group.id, r.ad_group.name = gid, group
            r.ad_group_ad.status = getattr(self.E.AdGroupAdStatusEnum, status)
            ad = r.ad_group_ad.ad
            ad.resource_name = f"customers/{CID}/ads/{ad_id}"
            ad.type_ = self.E.AdTypeEnum.RESPONSIVE_SEARCH_AD
            for text in headlines:
                ad.responsive_search_ad.headlines.append(self._text(text))
            if self.pinned and status == "ENABLED":
                ad.responsive_search_ad.headlines[0].pinned_field = self.E.ServedAssetFieldTypeEnum.HEADLINE_1
            for text in ["Classes every day.", "Cancel any time."]:
                ad.responsive_search_ad.descriptions.append(self._text(text))
            ad.responsive_search_ad.path1 = "gym"
            if self.final_url:
                ad.final_urls.append(self.final_url)
            rows.append(r)
        return rows

    def _text(self, text):
        asset = self.client.get_type("AdTextAsset")
        asset.text = text
        return asset

    def assets(self):
        rows = []
        r = self.row()
        r.campaign_asset.field_type = self.E.AssetFieldTypeEnum.SITELINK
        r.asset.sitelink_asset.link_text = "Class Schedule"
        r.asset.sitelink_asset.description1 = "See every class this week"
        r.asset.sitelink_asset.description2 = "Book a spot in seconds"
        r.asset.final_urls.append("https://www.example.com/classes")
        rows.append(r)
        r = self.row()
        r.campaign_asset.field_type = self.E.AssetFieldTypeEnum.CALLOUT
        r.asset.callout_asset.callout_text = "No Joining Fee"
        rows.append(r)
        r = self.row()
        r.campaign_asset.field_type = self.E.AssetFieldTypeEnum.STRUCTURED_SNIPPET
        r.asset.structured_snippet_asset.header = "Types"
        r.asset.structured_snippet_asset.values.extend(["Yoga", "Spin", "Boxing"])
        rows.append(r)
        for r in rows:
            kind = r.campaign_asset.field_type.name
            r.campaign_asset.status = getattr(self.E.AssetLinkStatusEnum,
                                              "PAUSED" if kind in self.paused_assets else "ENABLED")
        return rows

    def shared_sets(self):
        rows = []
        for i, name in enumerate(self.shared_lists):
            r = self.row()
            r.campaign_shared_set.shared_set = f"customers/{CID}/sharedSets/{i + 1}"
            r.shared_set.name = name
            rows.append(r)
        return rows

    def asset_links(self, resource, kinds):
        rows = []
        for kind in kinds:
            r = self.row()
            getattr(r, resource).field_type = getattr(self.E.AssetFieldTypeEnum, kind)
            rows.append(r)
        return rows

    def names(self, resource, query):
        known = {"geoTargetConstants/1000001": ("name", "Springfield"), "languageConstants/1000": ("name", "English")}
        rows = []
        for name, (field, value) in known.items():
            if f"'{name}'" in query:
                r = self.row()
                obj = getattr(r, resource)
                obj.resource_name = name
                setattr(obj, field, value)
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
        answers = {"campaign_criterion": self.criteria, "ad_group": self.ad_groups,
                   "ad_group_criterion": self.keywords, "ad_group_ad": self.ads, "campaign_asset": self.assets,
                   "campaign_shared_set": self.shared_sets,
                   "customer_asset": lambda: self.asset_links("customer_asset", self.account_assets),
                   "ad_group_asset": lambda: self.asset_links("ad_group_asset", self.group_assets)}
        if resource in answers:
            return answers[resource]()
        return self.names(resource, query)


@pytest.fixture
def account(ads_client, monkeypatch):
    fake = Account(ads_client)
    monkeypatch.setattr(templates.gads, "search", fake)
    return fake


def test_a_search_campaign_becomes_a_search_template(ads_client, account):
    out = templates.describe_campaign(ads_client, CID, "Springfield Gyms")
    assert out["campaign"] == {"id": 42, "name": "Springfield Gyms", "status": "ENABLED", "channel": "SEARCH"}
    tp = out["template_plan"]
    assert tp == {
        "kind": "search_campaign",
        "name": "Springfield Gyms (copy)",
        "template": "Springfield Gyms",
        "daily_budget": 80.0,
        "currency": "EUR",
        "bidding": "TARGET_CPA",
        "target_cpa": 12.0,
        "max_cpc": None,
        "impression_share": None,
        "search_partners": True,
        "locations": ["geoTargetConstants/1000001"],
        "languages": ["languageConstants/1000"],
        "negative_keywords": [{"text": "gym jobs", "match": "PHRASE"}],
        "ad_groups": [
            {"name": "Gym membership",
             "keywords": [{"text": "gym membership", "match": "PHRASE"}, {"text": "monthly gym pass", "match": "EXACT"}],
             "negative_keywords": [{"text": "free gym", "match": "EXACT"}],
             "cpc_bid": None,
             "ad": {"headlines": ["Gym Membership Deals", "Flexible Gym Membership", "Join Today"],
                    "descriptions": ["Classes every day.", "Cancel any time."],
                    "final_url": "https://www.example.com/gym", "path1": "gym", "path2": ""}},
            {"name": "Yoga classes", "keywords": [{"text": "yoga classes", "match": "BROAD"}], "negative_keywords": [],
             "cpc_bid": None,
             "ad": {"headlines": [], "descriptions": [], "final_url": "", "path1": "", "path2": ""}},
        ],
        "sitelinks": [{"text": "Class Schedule", "url": "https://www.example.com/classes",
                       "desc1": "See every class this week", "desc2": "Book a spot in seconds"}],
        "callouts": ["No Joining Fee"],
        "snippets": [{"header": "Types", "values": ["Yoga", "Spin", "Boxing"]}],
        "brief": {"names": {"geoTargetConstants/1000001": "Springfield,Test Region", "languageConstants/1000": "English"}},
    }
    assert out["notes"] == ["1 excluded location(s) are not copied", "ad group 'Yoga classes' has no enabled responsive search ad; write its ad"]
    assert [a["ad"] for a in out["ads"]] == [f"customers/{CID}/ads/101", f"customers/{CID}/ads/102"]
    assert out["ads"][1]["images"] == [] and out["ads"][1]["videos"] == [] and out["ads"][1]["status"] == "ENABLED"
    plan = plans.plan_from_dict(tp)  # a valid shape, ready to edit
    assert plan.ad_groups[0].ad.final_url == "https://www.example.com/gym"


def test_every_query_after_the_first_is_by_campaign_id(ads_client, account):
    templates.describe_campaign(ads_client, CID, "Springfield Gyms")
    first, *rest = account.queries
    assert "campaign.name = 'Springfield Gyms'" in first and "campaign.advertising_channel_type" in first
    account_wide = [q for q in rest if " FROM customer_asset " in q]
    assert account_wide == ["SELECT customer_asset.field_type FROM customer_asset WHERE customer_asset.status != "
                            "'REMOVED' AND customer_asset.source = 'ADVERTISER'"]
    by_campaign = [q for q in rest if " FROM geo_target_constant " not in q and " FROM language_constant " not in q
                   and q not in account_wide]
    assert len(by_campaign) == 8
    assert all("WHERE campaign.id = 42" in q and "campaign.name" not in q.split(" WHERE ", 1)[1] for q in by_campaign)
    assert any("ad_group_ad.ad.type = 'RESPONSIVE_SEARCH_AD'" in q for q in by_campaign)
    assert any("campaign_asset.status != 'REMOVED'" in q and "campaign_asset.source = 'ADVERTISER'" in q
               for q in by_campaign)
    assert any(" FROM campaign_shared_set " in q and "shared_set.type = 'NEGATIVE_KEYWORDS'" in q for q in by_campaign)
    assert any(" FROM ad_group_asset " in q and "ad_group_asset.source = 'ADVERTISER'" in q for q in by_campaign)
    [criteria] = [q for q in by_campaign if " FROM campaign_criterion " in q]
    assert "campaign_criterion.type IN" not in criteria  # every type, so the ones not copied can be noted
    assert "campaign_criterion.status" in criteria.split(" FROM ", 1)[0]


ATTRIBUTED = ("campaign", "ad_group", "shared_set")  # resources a query can filter on besides its own


def test_queries_select_the_attributed_fields_they_filter_on(ads_client, account):
    """Google refused a campaign_asset query that filtered on campaign.id without selecting it: every
    query on a resource other than the campaign selects the campaign and ad group fields it filters on."""
    import re

    templates.describe_campaign(ads_client, CID, "Springfield Gyms")
    checked = 0
    for query in account.queries[1:]:
        select, rest = query.split(" FROM ", 1)
        resource, _, where = rest.partition(" WHERE ")
        if resource.split()[0] not in ("campaign_criterion", "ad_group", "ad_group_criterion", "campaign_asset",
                                       "ad_group_asset"):
            continue
        checked += 1
        for field in re.findall(r"\b((?:campaign|ad_group)\.[a-z_.]+)", where):
            assert field in select, (field, query)
    assert checked == 5


@pytest.mark.parametrize("bidding, settings, expected", [
    ("MAXIMIZE_CONVERSIONS", {}, {"bidding": "MAXIMIZE_CONVERSIONS", "target_cpa": None}),
    ("MAXIMIZE_CONVERSIONS", {"maximize_conversions.target_cpa_micros": 9_500_000},
     {"bidding": "MAXIMIZE_CONVERSIONS", "target_cpa": 9.5}),
    ("TARGET_SPEND", {"target_spend.cpc_bid_ceiling_micros": 1_500_000}, {"bidding": "MAXIMIZE_CLICKS", "max_cpc": 1.5}),
    ("TARGET_SPEND", {}, {"bidding": "MAXIMIZE_CLICKS", "max_cpc": None}),
    ("TARGET_IMPRESSION_SHARE", {"target_impression_share.location": 3,  # TOP_OF_PAGE
                                 "target_impression_share.location_fraction_micros": 650_000,
                                 "target_impression_share.cpc_bid_ceiling_micros": 2_000_000},
     {"bidding": "TARGET_IMPRESSION_SHARE", "max_cpc": 2.0,
      "impression_share": {"location": "TOP_OF_PAGE", "percent": 65.0}}),
    ("MANUAL_CPC", {}, {"bidding": "MANUAL_CPC", "max_cpc": 1.2}),
])
def test_bidding_is_copied(ads_client, account, bidding, settings, expected):
    account.bidding, account.settings = bidding, settings
    out = templates.describe_campaign(ads_client, CID, "Springfield Gyms")
    tp = out["template_plan"]
    assert {key: tp[key] for key in expected} == expected
    broad = ["1 broad match keyword(s) need MAXIMIZE_CONVERSIONS or TARGET_CPA bidding; change their match type "
             "or the bidding"] if expected["bidding"] not in ("MAXIMIZE_CONVERSIONS", "TARGET_CPA") else []
    assert out["notes"] == ["1 excluded location(s) are not copied", "ad group 'Yoga classes' has no enabled responsive search ad; write its ad", *broad]
    if bidding == "MANUAL_CPC":
        assert [g["cpc_bid"] for g in tp["ad_groups"]] == [1.2, 0.9]


def test_unsupported_bidding_portfolios_and_display_are_noted(ads_client, account):
    account.bidding, account.settings = "TARGET_ROAS", {}
    account.portfolio = f"customers/{CID}/biddingStrategies/5"
    account.display = True
    out = templates.describe_campaign(ads_client, CID, "Springfield Gyms")
    assert out["template_plan"]["bidding"] == "MAXIMIZE_CONVERSIONS" and out["template_plan"]["target_cpa"] is None
    assert out["notes"] == [
        "bidding TARGET_ROAS is not supported here; the template uses MAXIMIZE_CONVERSIONS",
        "the campaign uses a shared (portfolio) bid strategy; its targets are not copied",
        "the campaign also shows on the Display Network; the template never does",
        "1 excluded location(s) are not copied",
        "ad group 'Yoga classes' has no enabled responsive search ad; write its ad",
    ]


@pytest.mark.parametrize("channel", ["DISPLAY", "SHOPPING", "VIDEO"])
def test_other_campaign_types_are_refused_clearly(ads_client, account, channel):
    account.channel = channel
    with pytest.raises(ValueError, match=f"is a {channel} campaign; only App"):
        templates.describe_campaign(ads_client, CID, "Springfield Gyms")
    assert len(account.queries) == 1


def test_ad_assets_reads_a_responsive_search_ad(ads_client, account):
    row = account.ads()[1]
    assert templates.ad_assets(row) == {
        "headlines": ["Gym Membership Deals", "Flexible Gym Membership", "Join Today"],
        "descriptions": ["Classes every day.", "Cancel any time."], "images": [], "videos": []}


# ------------------------------------------------------------------- what the copy leaves out


@pytest.mark.parametrize("setup, note", [
    (lambda a: setattr(a, "shared_budget", True), "the campaign uses a shared budget; the copy gets a budget of its own"),
    (lambda a: setattr(a, "proximity", 2),
     "2 radius (proximity) target(s) are not copied; add the area as a location with find_locations"),
    (lambda a: setattr(a, "shared_lists", ["Job seekers", "Free stuff"]),
     "2 shared negative keyword list(s) are not copied: 'Job seekers', 'Free stuff'"),
    (lambda a: setattr(a, "account_assets", ["SITELINK", "CALLOUT", "SITELINK"]),
     "3 account-level asset(s) are not copied (callout, sitelink); they stay on the account and can show with "
     "the new campaign"),
    (lambda a: setattr(a, "group_assets", ["CALLOUT"]), "1 ad-group-level asset(s) are not copied (callout)"),
    (lambda a: setattr(a, "pinned", True),
     "ad group 'Gym membership': the ad's pinned headlines or descriptions are copied without their pins"),
    (lambda a: setattr(a, "extra_groups", 19),
     "the campaign has 21 ad groups and a plan takes at most 20; keep the best ones"),
    (lambda a: setattr(a, "final_url", ""), "ad group 'Gym membership': its ad has no final URL; add one"),
])
def test_what_is_not_copied_is_noted(ads_client, account, setup, note):
    setup(account)
    notes = templates.describe_campaign(ads_client, CID, "Springfield Gyms")["notes"]
    assert note in notes, notes


def test_an_ad_group_with_only_a_paused_ad_is_noted(ads_client, account, monkeypatch):
    ads = account.ads

    def paused_only():
        rows = ads()
        rows[1].ad_group_ad.status = account.E.AdGroupAdStatusEnum.PAUSED
        return rows

    monkeypatch.setattr(account, "ads", paused_only)
    out = templates.describe_campaign(ads_client, CID, "Springfield Gyms")
    assert "ad group 'Gym membership' has no enabled responsive search ad; its ad is copied from a paused one" \
        in out["notes"]
    assert out["template_plan"]["ad_groups"][0]["ad"]["headlines"][0] == "Old headline one"


def test_the_ads_say_which_are_pinned(ads_client, account):
    account.pinned = True
    ads = templates.describe_campaign(ads_client, CID, "Springfield Gyms")["ads"]
    assert [a["pinned"] for a in ads] == [False, True]


# ------------------------------------------------------------------- nothing paused is switched on


def _tp(ads_client, account):
    out = templates.describe_campaign(ads_client, CID, "Springfield Gyms")
    return out["template_plan"], out["notes"]


def test_paused_ad_groups_and_their_keywords_are_not_copied(ads_client, account):
    account.group_status = {8: "PAUSED"}
    tp, notes = _tp(ads_client, account)
    assert [g["name"] for g in tp["ad_groups"]] == ["Gym membership"]
    assert "paused ad group 'Yoga classes' not copied" in notes
    assert not any("Yoga classes" in n and "paused ad group" not in n for n in notes), notes
    assert not any("yoga classes" in n for n in notes), notes  # its keywords go with it, without a note each


def test_paused_keywords_are_not_copied(ads_client, account):
    account.paused_keywords = {"monthly gym pass", "free gym"}
    tp, notes = _tp(ads_client, account)
    group = tp["ad_groups"][0]
    assert group["keywords"] == [{"text": "gym membership", "match": "PHRASE"}] and group["negative_keywords"] == []
    assert "paused keyword 'monthly gym pass' in ad group 'Gym membership' not copied" in notes
    assert "paused negative keyword 'free gym' in ad group 'Gym membership' not copied" in notes


@pytest.mark.parametrize("kind, field, note", [
    ("SITELINK", "sitelinks", "paused sitelink 'Class Schedule' not copied"),
    ("CALLOUT", "callouts", "paused callout 'No Joining Fee' not copied"),
    ("STRUCTURED_SNIPPET", "snippets", "paused structured snippet 'Types' not copied"),
])
def test_paused_asset_links_are_not_copied(ads_client, account, kind, field, note):
    account.paused_assets = {kind}
    tp, notes = _tp(ads_client, account)
    assert tp[field] == [] and note in notes
    assert all(tp[other] for other in ("sitelinks", "callouts", "snippets") if other != field)


@pytest.mark.parametrize("item, field, note", [
    ("gym jobs", "negative_keywords", "paused campaign negative keyword 'gym jobs' not copied"),
    ("geoTargetConstants/1000001", "locations", "paused location geoTargetConstants/1000001 not copied"),
    ("languageConstants/1000", "languages", "paused language languageConstants/1000 not copied"),
])
def test_paused_campaign_criteria_are_not_copied(ads_client, account, item, field, note):
    account.criterion_status = {item: "PAUSED"}
    tp, notes = _tp(ads_client, account)
    assert tp[field] == [] and note in notes


def test_other_targeting_is_noted_not_copied(ads_client, account):
    account.other_criteria = ["AD_SCHEDULE", ("DEVICE", 0.5), "AD_SCHEDULE", "USER_LIST", "AGE_RANGE", "GENDER",
                              "INCOME_RANGE"]
    account.other_group_criteria = [(7, "AGE_RANGE"), (7, "AGE_RANGE"), (7, "USER_LIST"), (8, "AUDIENCE")]
    tp, notes = _tp(ads_client, account)
    assert ("campaign-level targeting is not copied: 2 ad schedule, 1 age range, 1 device, 1 gender, 1 income range, "
            "1 user list; set it in Google Ads after creating the campaign") in notes
    assert ("ad-group-level targeting is not copied: 2 age range, 1 audience, 1 user list; set it in Google Ads "
            "after creating the campaign") in notes
    assert tp["locations"] == ["geoTargetConstants/1000001"] and tp["languages"] == ["languageConstants/1000"]
    assert [len(g["keywords"]) for g in tp["ad_groups"]] == [2, 1]


def test_device_rows_are_noted_only_when_their_bid_is_adjusted(ads_client, account):
    # Google lists a criterion for every device on every campaign; only a changed bid is targeting worth a note.
    account.other_criteria = ["DEVICE", ("DEVICE", 1.0), "DEVICE"]
    tp, notes = _tp(ads_client, account)
    assert not any("device" in n for n in notes)
    account.other_criteria = ["DEVICE", ("DEVICE", 0.0), ("DEVICE", 1.0)]
    tp, notes = _tp(ads_client, account)
    assert ("campaign-level targeting is not copied: 1 device; set it in Google Ads after creating the campaign"
            in notes)


def test_other_targeting_in_a_paused_ad_group_is_not_noted(ads_client, account):
    account.group_status = {8: "PAUSED"}
    account.other_group_criteria = [(8, "AUDIENCE")]
    _, notes = _tp(ads_client, account)
    assert not any("ad-group-level targeting" in n for n in notes), notes


def test_tracking_settings_are_noted_not_copied(ads_client, account):
    account.campaign_tracking = {"tracking_url_template": "{lpurl}?src=ads", "final_url_suffix": "utm_source=ads",
                                 "url_custom_parameters": [("season", "spring")]}
    account.group_tracking = {7: {"final_url_suffix": "utm_term=gym"}, 8: {"url_custom_parameters": [("x", "1")],
                                                                            "tracking_url_template": "{lpurl}?g=8"}}
    tp, notes = _tp(ads_client, account)
    assert ("the campaign's tracking template, final URL suffix and custom parameters are not copied; set them in "
            "Google Ads after creating the campaign") in notes
    assert "ad group 'Gym membership': its final URL suffix is not copied" in notes
    assert "ad group 'Yoga classes': its tracking template and custom parameters are not copied" in notes
    assert not any(key in tp for key in ("tracking_url_template", "final_url_suffix", "url_custom_parameters"))


def test_no_tracking_note_without_tracking_settings(ads_client, account):
    _, notes = _tp(ads_client, account)
    assert not any("tracking" in n or "suffix" in n or "custom parameters" in n for n in notes), notes


@pytest.mark.parametrize("option", ["PRESENCE_OR_INTEREST", "SEARCH_INTEREST"])
def test_another_location_option_is_noted(ads_client, account, option):
    account.geo_option = option
    _, notes = _tp(ads_client, account)
    assert (f"the campaign's location option is {option}; the copy uses people in or regularly in your locations "
            "(PRESENCE)") in notes


def test_ai_max_on_is_noted(ads_client, account):
    account.ai_max = True
    _, notes = _tp(ads_client, account)
    assert "AI Max is on in the campaign; the copy has AI Max off" in notes


def test_presence_and_ai_max_off_need_no_note(ads_client, account):
    _, notes = _tp(ads_client, account)
    assert not any("location option" in n or "AI Max" in n for n in notes), notes


def test_the_settings_query_reads_the_location_option_ai_max_and_tracking(ads_client, account):
    templates.describe_campaign(ads_client, CID, "Springfield Gyms")
    [settings] = [q for q in account.queries if "campaign.bidding_strategy_type" in q]
    for field in ("campaign.geo_target_type_setting.positive_geo_target_type", "campaign.ai_max_setting.enable_ai_max",
                  "campaign.tracking_url_template", "campaign.final_url_suffix", "campaign.url_custom_parameters"):
        assert field in settings.split(" FROM ")[0], field
    [groups] = [q for q in account.queries if " FROM ad_group WHERE " in q]
    for field in ("ad_group.status", "ad_group.tracking_url_template", "ad_group.final_url_suffix",
                  "ad_group.url_custom_parameters"):
        assert field in groups.split(" FROM ")[0], field


def test_the_playbook_says_only_enabled_parts_are_copied():
    from pathlib import Path

    text = " ".join(Path(templates.__file__).with_name("playbook.md").read_text(encoding="utf-8").split())
    assert "never switches on something that was paused" in text
    assert "ad schedules, devices, audiences and demographics, tracking templates" in text
