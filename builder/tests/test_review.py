import pytest

from gads_builder import review


def test_quality_report_maps_rows(ads_client, monkeypatch):
    queries = []
    ad = ads_client.get_type("GoogleAdsRow")
    ad.campaign.name = "Camp"
    ad.campaign.status = ads_client.enums.CampaignStatusEnum.PAUSED
    ad.ad_group_ad.ad.resource_name = "customers/1234567890/ads/5"
    ad.ad_group_ad.ad.type_ = ads_client.enums.AdTypeEnum.APP_AD
    ad.ad_group_ad.ad_strength = ads_client.enums.AdStrengthEnum.POOR
    ad.ad_group_ad.action_items.append("Add more headlines")
    ad.ad_group_ad.primary_status = ads_client.enums.AdGroupAdPrimaryStatusEnum.NOT_ELIGIBLE
    ad.ad_group_ad.primary_status_reasons.append(ads_client.enums.AdGroupAdPrimaryStatusReasonEnum.CAMPAIGN_PAUSED)
    ad.ad_group_ad.policy_summary.approval_status = ads_client.enums.PolicyApprovalStatusEnum.APPROVED_LIMITED
    entry = ads_client.get_type("PolicyTopicEntry")
    entry.topic = "ALCOHOL"
    ad.ad_group_ad.policy_summary.policy_topic_entries.append(entry)

    asset = ads_client.get_type("GoogleAdsRow")
    asset.ad_group_ad_asset_view.field_type = ads_client.enums.AssetFieldTypeEnum.HEADLINE
    asset.ad_group_ad_asset_view.performance_label = ads_client.enums.AssetPerformanceLabelEnum.GOOD
    asset.asset.resource_name = "customers/1234567890/assets/9"
    asset.asset.text_asset.text = "Hi"

    def fake(client, customer_id, query):
        queries.append(query)
        return [ad] if "FROM ad_group_ad " in query else [asset]

    monkeypatch.setattr(review.gads, "search", fake)
    out = review.quality_report(ads_client, "1234567890", "Camp")
    assert out["ads"] == [{
        "campaign": "Camp", "campaign_status": "PAUSED", "ad": "customers/1234567890/ads/5",
        "ad_type": "APP_AD", "ad_strength": "POOR", "approval": "APPROVED_LIMITED",
        "ad_strength_suggestions": ["Add more headlines"], "primary_status": "NOT_ELIGIBLE",
        "primary_status_reasons": ["CAMPAIGN_PAUSED"], "policy_topics": ["ALCOHOL"],
    }]
    assert out["assets"] == [{"field": "HEADLINE", "label": "GOOD", "asset": "customers/1234567890/assets/9", "text": "Hi"}]
    assert all("campaign.status != 'REMOVED'" in q for q in queries)
    assert "campaign.name = 'Camp'" in queries[0]


IMAGE_OPT_OUTS = ("GENERATE_DESIGN_VERSIONS_FOR_IMAGES", "GENERATE_VIDEOS_FROM_OTHER_ASSETS",
                  "GENERATE_ANIMATED_IMAGES_FROM_OTHER_ASSETS")
VIDEO_OPT_OUTS = ("GENERATE_VERTICAL_YOUTUBE_VIDEOS", "GENERATE_SHORTER_YOUTUBE_VIDEOS", "GENERATE_LANDING_PAGE_TEXT",
                  "GENERATE_LANDING_PAGE_PREVIEW")


class DemandGenAccount:
    """A Demand Gen campaign (ID 42) as Google reads it back, with one ad group and one ad."""

    def __init__(self, ads_client):
        self.client, self.E = ads_client, ads_client.enums
        self.queries = []
        self.optimized, self.excluded = False, True
        self.selected = {"youtube_in_stream": True, "discover": True}
        self.strategy = None
        self.option = "PRESENCE"
        self.goals = [("PURCHASE", "WEBSITE", True), ("DOWNLOAD", "APP", False)]
        self.ad_type = "DEMAND_GEN_MULTI_ASSET_AD"
        self.generated = dict.fromkeys(IMAGE_OPT_OUTS, "OPTED_OUT")

    def row(self):
        return self.client.get_type("GoogleAdsRow")

    def __call__(self, client, customer_id, query):
        self.queries.append(query)
        E, r = self.E, self.row()
        resource = query.split(" FROM ", 1)[1].split(" ", 1)[0]
        if resource == "campaign" and "advertising_channel_type" in query:
            r.campaign.id, r.campaign.name = 42, "Pulse DG"
            r.campaign.advertising_channel_type = E.AdvertisingChannelTypeEnum.DEMAND_GEN
        elif resource == "campaign":
            r.campaign.demand_gen_campaign_settings.upgraded_targeting = True
            r.campaign.geo_target_type_setting.positive_geo_target_type = getattr(E.PositiveGeoTargetTypeEnum, self.option)
        elif resource == "campaign_conversion_goal":
            rows = []
            for category, origin, biddable in self.goals:
                g = self.row()
                goal = g.campaign_conversion_goal
                goal.category = getattr(E.ConversionActionCategoryEnum, category)
                goal.origin = getattr(E.ConversionOriginEnum, origin)
                goal.biddable = biddable
                rows.append(g)
            return rows
        elif resource == "ad_group":
            g = r.ad_group
            g.name, g.optimized_targeting_enabled, g.exclude_demographic_expansion = "Members", self.optimized, self.excluded
            controls = g.demand_gen_ad_group_settings.channel_controls
            if self.strategy:
                controls.channel_config = E.DemandGenChannelConfigEnum.CHANNEL_STRATEGY
                controls.channel_strategy = getattr(E.DemandGenChannelStrategyEnum, self.strategy)
            else:
                controls.channel_config = E.DemandGenChannelConfigEnum.SELECTED_CHANNELS
                for name, on in self.selected.items():
                    setattr(controls.selected_channels, name, on)
        elif resource == "ad_group_ad" and "asset_automation" in query:
            r.ad_group_ad.ad.resource_name = "customers/1234567890/ads/5"
            r.ad_group_ad.ad.type_ = getattr(E.AdTypeEnum, self.ad_type)
            for kind, status in self.generated.items():
                s = self.client.get_type("AdGroupAdAssetAutomationSetting")
                s.asset_automation_type = getattr(E.AssetAutomationTypeEnum, kind)
                s.asset_automation_status = getattr(E.AssetAutomationStatusEnum, status)
                r.ad_group_ad.ad_group_ad_asset_automation_settings.append(s)
        else:
            return []  # the ads and assets of the report itself
        return [r]


def test_a_demand_gen_campaign_is_read_back(ads_client, monkeypatch):
    fake = DemandGenAccount(ads_client)
    monkeypatch.setattr(review.gads, "search", fake)
    out = review.quality_report(ads_client, "1234567890", "Pulse DG")
    assert out["demand_gen"] == [{
        "campaign": "Pulse DG", "upgraded_targeting": True, "location_option": "PRESENCE",
        "conversion_goals": {"bid_on": ["PURCHASE (WEBSITE)"], "not_bid_on": ["DOWNLOAD (APP)"]},
        "ad_groups": [{"name": "Members", "optimized_targeting": False, "demographic_expansion_excluded": True,
                       "channels": ["youtube_in_stream", "discover"]}],
        "ads": [{"ad": "customers/1234567890/ads/5", "ad_type": "DEMAND_GEN_MULTI_ASSET_AD",
                 "generated_creatives": dict.fromkeys(IMAGE_OPT_OUTS, "OPTED_OUT")}],
        "to_review": [],
    }]
    assert all("campaign.id = 42" in q for q in fake.queries[3:])
    assert "ad_group_ad.ad.type" in next(q for q in fake.queries if "asset_automation" in q)


def test_the_builders_own_constants_are_the_expected_opt_outs():
    assert review.EXPECTED_OPT_OUTS == {"DEMAND_GEN_MULTI_ASSET_AD": IMAGE_OPT_OUTS,
                                        "DEMAND_GEN_VIDEO_RESPONSIVE_AD": VIDEO_OPT_OUTS}


AD = "ad customers/1234567890/ads/5"


def _video_ad(account, generated):
    account.ad_type = "DEMAND_GEN_VIDEO_RESPONSIVE_AD"
    account.generated = generated


@pytest.mark.parametrize("setup, note", [
    (lambda a: setattr(a, "optimized", True),
     "ad group 'Members' has optimized targeting on (Google's default; the API can't send 'off'): switch it off in "
     "Google Ads before you switch the campaign on"),
    (lambda a: setattr(a, "excluded", False),
     "ad group 'Members' allows demographic expansion: exclude demographic expansion in Google Ads"),
    (lambda a: a.selected.update(display=True), "ad group 'Members' runs on display"),
    (lambda a: setattr(a, "strategy", "ALL_CHANNELS"),
     "ad group 'Members' runs on all channels, including third-party Display"),
    (lambda a: setattr(a, "option", "PRESENCE_OR_INTEREST"),
     "the location option is PRESENCE_OR_INTEREST, not people in or regularly in the locations (PRESENCE)"),
    (lambda a: a.generated.update(GENERATE_VIDEOS_FROM_OTHER_ASSETS="OPTED_IN"),
     f"{AD} lets Google generate: GENERATE_VIDEOS_FROM_OTHER_ASSETS"),
    # an expected opt-out that is missing, or neither opted out nor in
    (lambda a: a.generated.pop("GENERATE_ANIMATED_IMAGES_FROM_OTHER_ASSETS"),
     f"{AD} is not opted out of GENERATE_ANIMATED_IMAGES_FROM_OTHER_ASSETS: Google's default applies; opt out in "
     "Google Ads"),
    (lambda a: a.generated.update(GENERATE_DESIGN_VERSIONS_FOR_IMAGES="UNSPECIFIED"),
     f"{AD} is not opted out of GENERATE_DESIGN_VERSIONS_FOR_IMAGES: Google's default applies; opt out in Google Ads"),
    # another kind Google reports as on
    (lambda a: a.generated.update(GENERATE_VERTICAL_YOUTUBE_VIDEOS="OPTED_IN"),
     f"{AD} lets Google generate: GENERATE_VERTICAL_YOUTUBE_VIDEOS"),
    # a video ad is held to the video opt-outs, not the image ones
    (lambda a: _video_ad(a, dict.fromkeys(IMAGE_OPT_OUTS, "OPTED_OUT")),
     f"{AD} is not opted out of {', '.join(sorted(VIDEO_OPT_OUTS))}: Google's default applies; opt out in Google Ads"),
    (lambda a: a.goals.append(("DOWNLOAD", "APP", True)),
     "the campaign bids on the app conversion goal DOWNLOAD (APP); the builder never does: switch it off for this "
     "campaign in Google Ads"),
    (lambda a: setattr(a, "goals", [("PURCHASE", "WEBSITE", False), ("DOWNLOAD", "APP", False)]),
     "the campaign bids on no conversion goal: switch the plan's goals on for this campaign in Google Ads"),
])
def test_what_differs_from_the_builders_settings_is_listed(ads_client, monkeypatch, setup, note):
    fake = DemandGenAccount(ads_client)
    setup(fake)
    monkeypatch.setattr(review.gads, "search", fake)
    assert review.quality_report(ads_client, "1234567890", "Pulse DG")["demand_gen"][0]["to_review"] == [note]


def test_other_campaigns_have_no_demand_gen_part(ads_client, monkeypatch):
    monkeypatch.setattr(review.gads, "search", lambda client, cid, query: [])
    assert "demand_gen" not in review.quality_report(ads_client, "1234567890", "Camp")


def test_a_video_ad_with_every_video_opt_out_is_fine(ads_client, monkeypatch):
    fake = DemandGenAccount(ads_client)
    _video_ad(fake, dict.fromkeys(VIDEO_OPT_OUTS, "OPTED_OUT"))
    monkeypatch.setattr(review.gads, "search", fake)
    report = review.quality_report(ads_client, "1234567890", "Pulse DG")["demand_gen"][0]
    assert report["to_review"] == [] and report["ads"][0]["ad_type"] == "DEMAND_GEN_VIDEO_RESPONSIVE_AD"


class PmaxAccount:
    """A Performance Max campaign (ID 42) as Google reads it back, with one asset group."""

    def __init__(self, ads_client):
        self.client, self.E = ads_client, ads_client.enums
        self.queries = []
        self.brand_guidelines = True
        self.automation = {"FINAL_URL_EXPANSION_TEXT_ASSET_AUTOMATION": "OPTED_OUT", "TEXT_ASSET_AUTOMATION": "OPTED_OUT",
                           "GENERATE_ENHANCED_YOUTUBE_VIDEOS": "OPTED_OUT", "GENERATE_IMAGE_ENHANCEMENT": "OPTED_OUT",
                           "AUTOMATED_VIDEO_CRAWL": "OPTED_OUT", "GENERATE_IMAGE_EXTRACTION": "OPTED_OUT"}
        self.option = "PRESENCE"
        self.goals = [("PURCHASE", "WEBSITE", True), ("DOWNLOAD", "APP", False)]
        self.custom_goal = ""
        self.campaign_assets = [("BUSINESS_NAME", "ADVERTISER"), ("LOGO", "ADVERTISER")]
        self.strength = "GOOD"
        self.urls = ["https://www.example.com/join"]
        self.generated = []  # field types of assets Google added to the asset group

    def row(self):
        return self.client.get_type("GoogleAdsRow")

    def campaign_row(self, query):
        E, r = self.E, self.row()
        if "advertising_channel_type" in query:
            r.campaign.id, r.campaign.name = 42, "Pulse PMax"
            r.campaign.advertising_channel_type = E.AdvertisingChannelTypeEnum.PERFORMANCE_MAX
            return [r]
        r.campaign.brand_guidelines_enabled = self.brand_guidelines
        for kind, status in self.automation.items():
            s = type(r.campaign).AssetAutomationSetting()
            s.asset_automation_type = getattr(E.AssetAutomationTypeEnum, kind)
            s.asset_automation_status = getattr(E.AssetAutomationStatusEnum, status)
            r.campaign.asset_automation_settings.append(s)
        r.campaign.geo_target_type_setting.positive_geo_target_type = getattr(E.PositiveGeoTargetTypeEnum, self.option)
        return [r]

    def __call__(self, client, customer_id, query):
        self.queries.append(query)
        E = self.E
        resource = query.split(" FROM ", 1)[1].split(" ", 1)[0]
        if resource == "campaign":
            return self.campaign_row(query)
        rows = []
        if resource == "campaign_conversion_goal":
            for category, origin, biddable in self.goals:
                r = self.row()
                r.campaign_conversion_goal.category = getattr(E.ConversionActionCategoryEnum, category)
                r.campaign_conversion_goal.origin = getattr(E.ConversionOriginEnum, origin)
                r.campaign_conversion_goal.biddable = biddable
                rows.append(r)
        elif resource == "conversion_goal_campaign_config":
            r = self.row()
            r.conversion_goal_campaign_config.custom_conversion_goal = self.custom_goal
            rows.append(r)
        elif resource == "campaign_asset":
            for kind, source in self.campaign_assets:
                r = self.row()
                r.campaign_asset.field_type = getattr(E.AssetFieldTypeEnum, kind)
                r.campaign_asset.status = E.AssetLinkStatusEnum.ENABLED
                r.campaign_asset.source = getattr(E.AssetSourceEnum, source)
                rows.append(r)
        elif resource == "asset_group_asset":
            for kind in self.generated:
                r = self.row()
                r.asset_group.name = "Members"
                r.asset_group_asset.field_type = getattr(E.AssetFieldTypeEnum, kind)
                r.asset_group_asset.source = E.AssetSourceEnum.AUTOMATICALLY_CREATED
                rows.append(r)
        elif resource == "asset_group":
            r = self.row()
            g = r.asset_group
            g.name, g.status = "Members", E.AssetGroupStatusEnum.ENABLED
            g.ad_strength = getattr(E.AdStrengthEnum, self.strength)
            g.primary_status = E.AssetGroupPrimaryStatusEnum.NOT_ELIGIBLE
            g.final_urls.extend(self.urls)
            item = self.client.get_type("AdStrengthActionItem")
            item.add_asset_details.asset_field_type = E.AssetFieldTypeEnum.YOUTUBE_VIDEO
            item.add_asset_details.asset_count = 1
            item.add_asset_details.video_aspect_ratio_requirement = E.AssetCoverageVideoAspectRatioRequirementEnum.VERTICAL
            g.asset_coverage.ad_strength_action_items.append(item)
            rows.append(r)
        return rows  # the ads and assets of the report itself: none for Performance Max


def test_a_pmax_campaign_is_read_back(ads_client, monkeypatch):
    fake = PmaxAccount(ads_client)
    monkeypatch.setattr(review.gads, "search", fake)
    out = review.quality_report(ads_client, "1234567890", "Pulse PMax")
    assert "demand_gen" not in out
    assert out["pmax"] == [{
        "campaign": "Pulse PMax", "brand_guidelines": True, "asset_automation": fake.automation,
        "location_option": "PRESENCE",
        "conversion_goals": {"bid_on": ["PURCHASE (WEBSITE)"], "not_bid_on": ["DOWNLOAD (APP)"]},
        "brand": {"business_names": 1, "logos": 1, "landscape_logos": 0},
        "asset_groups": [{"name": "Members", "status": "ENABLED", "ad_strength": "GOOD", "primary_status": "NOT_ELIGIBLE",
                          "final_urls": ["https://www.example.com/join"], "add_assets": ["1 YOUTUBE_VIDEO (VERTICAL)"],
                          "generated_assets": []}],
        "to_review": [],
    }]
    by_campaign = fake.queries[3:]
    assert all("campaign.id = 42" in q and q.startswith("SELECT campaign.id, ") for q in by_campaign)
    assert all("asset_group.status, " in q for q in by_campaign if "asset_group.status != 'REMOVED'" in q)
    assert all("asset_group_asset.source" in q.split(" FROM ")[0] for q in by_campaign if "asset_group_asset.source =" in q)


@pytest.mark.parametrize("setup, note", [
    (lambda a: setattr(a, "brand_guidelines", False),
     "brand guidelines are off: the builder creates the campaign with them on; check the business name and logos in "
     "Google Ads"),
    (lambda a: a.automation.pop("FINAL_URL_EXPANSION_TEXT_ASSET_AUTOMATION"),
     "the campaign is not opted out of final URL expansion (FINAL_URL_EXPANSION_TEXT_ASSET_AUTOMATION): Google's "
     "default applies; opt out in the campaign's asset optimization settings in Google Ads"),
    (lambda a: a.automation.update(TEXT_ASSET_AUTOMATION="OPTED_IN", GENERATE_IMAGE_ENHANCEMENT="OPTED_IN"),
     "the campaign is not opted out of generated headlines and descriptions, image cropping and enhancement "
     "(TEXT_ASSET_AUTOMATION, GENERATE_IMAGE_ENHANCEMENT): Google's default applies; opt out in the campaign's asset "
     "optimization settings in Google Ads"),
    (lambda a: a.automation.pop("GENERATE_IMAGE_EXTRACTION"),
     "Google may take images from the landing page (image extraction follows the account's setting, which only the "
     "Google Ads UI changes): switch it off there if you don't want it"),
    (lambda a: setattr(a, "option", "PRESENCE_OR_INTEREST"),
     "the location option is PRESENCE_OR_INTEREST, not people in or regularly in the locations (PRESENCE)"),
    (lambda a: setattr(a, "goals", [("PURCHASE", "WEBSITE", True), ("DOWNLOAD", "APP", True)]),
     "the campaign bids on the app conversion goal DOWNLOAD (APP); the builder never does: switch it off for this "
     "campaign in Google Ads"),
    (lambda a: setattr(a, "goals", [("PURCHASE", "WEBSITE", False)]),
     "the campaign bids on no conversion goal: switch the plan's goals on for this campaign in Google Ads"),
    (lambda a: setattr(a, "custom_goal", "customers/1234567890/customConversionGoals/5"),
     "the campaign bids on a custom conversion goal; the builder never sets one: check its goals in Google Ads"),
    (lambda a: setattr(a, "campaign_assets", [("BUSINESS_NAME", "ADVERTISER")]),
     "the campaign has no enabled business name or logo of its own: add them in Google Ads"),
    (lambda a: a.campaign_assets.extend([("SITELINK", "AUTOMATICALLY_CREATED"), ("SITELINK", "AUTOMATICALLY_CREATED")]),
     "Google added campaign assets of its own (2 sitelink): remove them in Google Ads if you don't want them"),
    (lambda a: setattr(a, "urls", ["https://www.example.com/join", "https://www.example.com/other"]),
     "asset group 'Members' has 2 final URL(s) and 0 mobile URL(s); the builder sends exactly one final URL"),
    (lambda a: setattr(a, "strength", "POOR"),
     "asset group 'Members' has POOR Ad Strength: Google suggests adding 1 YOUTUBE_VIDEO (VERTICAL) (a new "
     "asset_update can't change asset groups yet: add them in Google Ads)"),
    (lambda a: setattr(a, "generated", ["YOUTUBE_VIDEO", "YOUTUBE_VIDEO"]),
     "asset group 'Members' has assets Google added itself (2 youtube video): remove them in Google Ads if you don't "
     "want them"),
])
def test_what_differs_from_the_pmax_settings_is_listed(ads_client, monkeypatch, setup, note):
    fake = PmaxAccount(ads_client)
    setup(fake)
    monkeypatch.setattr(review.gads, "search", fake)
    assert review.quality_report(ads_client, "1234567890", "Pulse PMax")["pmax"][0]["to_review"] == [note]
