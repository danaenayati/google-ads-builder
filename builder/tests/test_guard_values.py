"""The guard compares the values in the request with the plan, not just its shape: a compiler bug that sends
other text, other images, other targeting or other URLs than the person approved is refused."""

import pytest

from gads_builder import server
from test_allowlists import CID, Request, _creates, build, guard  # noqa: F401  (build: a fixture)


def _all(r: Request, which: str) -> list:
    """The raw protobuf creates (or updates) of every operation of one kind, in order."""
    out = []
    for op in r.ops:
        pb = type(op).pb(op)
        if pb.WhichOneof("operation") == which:
            operation = getattr(pb, which)
            out.append(getattr(operation, operation.WhichOneof("operation")))
    return out


def _assets(r: Request, kind: str) -> list:
    return [a for a in _all(r, "asset_operation") if a.WhichOneof("asset_data") == kind]


def _drop_op(r: Request, which: str, pick: int):
    """Removes the `pick`-th operation of one kind."""
    found = [i for i, op in enumerate(r.ops) if type(op).pb(op).WhichOneof("operation") == which]
    del r.ops[found[pick]]


def _copy_op(r: Request, which: str, pick: int, resource_name: str):
    """Appends a copy of the `pick`-th operation of one kind, under another temporary ID."""
    found = [op for op in r.ops if type(op).pb(op).WhichOneof("operation") == which]
    op = type(found[pick])()
    type(op).pb(op).CopyFrom(type(found[pick]).pb(found[pick]))
    getattr(type(op).pb(op), which).create.resource_name = resource_name
    r.ops.append(op)


def _swap_ops(r: Request, which: str):
    found = [i for i, op in enumerate(r.ops) if type(op).pb(op).WhichOneof("operation") == which]
    i, j = found[0], found[1]
    r.ops[i], r.ops[j] = r.ops[j], r.ops[i]


# ------------------------------------------------------------------ image uploads

UPLOAD_CASES = [
    lambda r, E: setattr(_assets(r, "image_asset")[1].image_asset, "data", b"other bytes"),
    lambda r, E: setattr(_assets(r, "image_asset")[0], "name", "upload | other.png | 00000000"),
    lambda r, E: _swap_ops(r, "asset_operation"),
    lambda r, E: _drop_op(r, "asset_operation", 1),
    lambda r, E: _copy_op(r, "asset_operation", 0, f"customers/{CID}/assets/-9"),
]


@pytest.mark.parametrize("edit", UPLOAD_CASES)
def test_an_upload_sends_exactly_the_plans_files(ads_client, build, edit):
    r = build("upload")
    edit(r, ads_client.enums)
    assert guard(ads_client, r) == [server.NEW_IMAGES]


def test_an_upload_whose_file_changed_after_compiling_is_refused(ads_client, build):
    r = build("upload")
    with open(r.plan.files[0], "ab") as f:
        f.write(b"more")
    assert guard(ads_client, r) == [server.NEW_IMAGES]


# ------------------------------------------------------------------ asset updates (a running ad changes at once)

_INFO = {"update APP_AD": "app_ad", "update APP_ENGAGEMENT_AD": "app_engagement_ad",
         "update RESPONSIVE_SEARCH_AD": "responsive_search_ad"}
_VIDEOS = {"update APP_AD": "youtube_videos", "update APP_ENGAGEMENT_AD": "videos"}
APP_UPDATES = ["update APP_AD", "update APP_ENGAGEMENT_AD"]
ALL_UPDATES = [*APP_UPDATES, "update RESPONSIVE_SEARCH_AD"]


def _info(r: Request, name: str):
    return getattr(_creates(r, "ad_operation"), _INFO[name])


def _mask(r: Request):
    return next(getattr(type(op).pb(op), "ad_operation").update_mask for op in r.ops
                if type(op).pb(op).WhichOneof("operation") == "ad_operation")


UPDATE_VALUE_CASES = [
    (ALL_UPDATES, lambda r, n: setattr(_info(r, n).headlines[0], "text", "Other headline")),
    (ALL_UPDATES, lambda r, n: setattr(_info(r, n).descriptions[0], "text", "Other description")),
    (ALL_UPDATES, lambda r, n: _info(r, n).headlines.pop()),  # cut to one headline
    (ALL_UPDATES, lambda r, n: _info(r, n).headlines.add(text="One more")),
    (ALL_UPDATES, lambda r, n: (lambda h: (setattr(h[0], "text", "New one"), setattr(h[1], "text", "Old one")))(
        _info(r, n).headlines)),  # reordered
    (APP_UPDATES, lambda r, n: _info(r, n).ClearField("images")),
    (APP_UPDATES, lambda r, n: setattr(_info(r, n).images[0], "asset", f"customers/{CID}/assets/2")),
    (APP_UPDATES, lambda r, n: _info(r, n).images.pop(0)),  # the existing image dropped, the new one kept
    (APP_UPDATES, lambda r, n: getattr(_info(r, n), _VIDEOS[n]).pop()),
]


@pytest.mark.parametrize("name, edit", [(name, edit) for names, edit in UPDATE_VALUE_CASES for name in names])
def test_an_ad_update_sends_exactly_the_plans_lists(ads_client, build, name, edit):
    r = build(name)
    edit(r, name)
    assert guard(ads_client, r) == [server.AD_UPDATE_VALUES]


@pytest.mark.parametrize("name", ALL_UPDATES)
@pytest.mark.parametrize("edit, expected", [
    (lambda m: m.paths.remove(m.paths[-1]), "AD_UPDATE_MASK"),  # a list the update sends would not change
    (lambda m: m.paths.append(m.paths[0]), "AD_UPDATE_MASK"),
    (lambda m: m.paths.append("final_urls"), "internal safety check: the ad update would change ['final_urls']"),
])
def test_an_ad_updates_mask_is_exactly_its_lists(ads_client, build, name, edit, expected):
    r = build(name)
    edit(_mask(r))
    assert guard(ads_client, r) == [getattr(server, expected, expected)]


@pytest.mark.parametrize("name", APP_UPDATES)
@pytest.mark.parametrize("edit, expected", [
    (lambda r: setattr(_assets(r, "image_asset")[0].image_asset, "data", b"other bytes"), "NEW_IMAGES"),
    (lambda r: setattr(_assets(r, "image_asset")[0], "name", "update ad 55 | other.png | 00000000"), "NEW_IMAGES"),
    (lambda r: _copy_op(r, "asset_operation", 0, f"customers/{CID}/assets/-9"), "NEW_IMAGES"),
    (lambda r: setattr(_assets(r, "youtube_video_asset")[0].youtube_video_asset, "youtube_video_id", "zyxwvutsrqp"),
     "NEW_VIDEOS"),
    (lambda r: setattr(_assets(r, "youtube_video_asset")[0], "name", "Some video"), "NEW_VIDEOS"),
    (lambda r: _copy_op(r, "asset_operation", 1, f"customers/{CID}/assets/-9"), "NEW_VIDEOS"),
])
def test_an_ad_updates_new_assets_are_exactly_the_plans(ads_client, build, name, edit, expected):
    r = build(name)
    edit(r)
    assert guard(ads_client, r) == [getattr(server, expected)]


@pytest.mark.parametrize("name", ALL_UPDATES)
def test_the_ad_is_updated_exactly_once(ads_client, build, name):
    r = build(name)
    _copy_update = type(r.ops[-1])()
    type(_copy_update).pb(_copy_update).CopyFrom(type(r.ops[-1]).pb(r.ops[-1]))
    r.ops.append(_copy_update)
    assert guard(ads_client, r) == [server.AD_UPDATE_ONCE]


_LISTS = ("headlines", "descriptions", "images", "videos")


def _base_unchanged(plan):  # a merged_assets bug: nothing removed, nothing added
    return {k: list(plan.base.get(k, [])) for k in _LISTS}


def _additions_forgotten(plan):  # a merged_assets bug: the removals are right, the added texts are lost
    return {k: [x for x in plan.base.get(k, []) if x not in plan.remove] for k in _LISTS}


@pytest.mark.parametrize("name", [*ALL_UPDATES, "update APP_AD mixed"])
@pytest.mark.parametrize("helper", [_base_unchanged, _additions_forgotten])
def test_an_ad_update_is_checked_without_the_compilers_helper(ads_client, build, monkeypatch, name, helper):
    """The guard's main comparison uses plans.merged_assets, as the compiler does, so a bug there would pass it:
    every removed item must be gone and every added one sent, checked from the plan alone."""
    from gads_builder import compile_assets, plans

    monkeypatch.setattr(plans, "merged_assets", helper)  # as the guard sees it
    monkeypatch.setattr(compile_assets, "merged_assets", helper)  # as the compiler sees it
    r = build(name)
    assert guard(ads_client, r) == [getattr(server, "AD_UPDATE_LISTS", "AD_UPDATE_LISTS")]


@pytest.mark.parametrize("over", [
    {"add_headlines": ["Old two"], "remove": ["Old two"]},  # removed, then added again at the end
    {"add_headlines": ["New one"], "add_descriptions": ["Old two"], "remove": ["Old two"]},  # moved to descriptions
])
def test_an_item_the_plan_removes_and_adds_again_is_sent_once(ads_client, cfg, over):
    from gads_builder import plans
    from gads_builder.compile_assets import compile_asset_update
    from test_allowlists import BASE

    data = {"kind": "asset_update", "ad": f"customers/{CID}/ads/55", "ad_type": "RESPONSIVE_SEARCH_AD",
            "base": dict(BASE, images=[]), **over}
    plan = plans.plan_from_dict(data)
    r = Request(plan, compile_asset_update(plan, ads_client, CID)[0], cfg, {})
    assert guard(ads_client, r) == []


# ------------------------------------------------------------------ App campaigns

APPS = ["app", "app engagement"]


def _campaign(r: Request):
    return _creates(r, "campaign_operation")


def _app_info(r: Request):
    ad = _creates(r, "ad_group_ad_operation").ad
    return getattr(ad, ad.WhichOneof("ad_data"))


def _app_videos(r: Request):
    info = _app_info(r)
    return info.youtube_videos if hasattr(info, "youtube_videos") else info.videos


def _criteria_of(r: Request, kind: str) -> list:
    return [c for c in _all(r, "campaign_criterion_operation") if c.WhichOneof("criterion") == kind]


def _other_ad_type(r: Request, E):
    """The same texts and media, sent as the other kind of App ad."""
    ad = _creates(r, "ad_group_ad_operation").ad
    info = _app_info(r)
    texts, descriptions, images = list(info.headlines), list(info.descriptions), list(info.images)
    videos = list(_app_videos(r))
    other = ad.app_engagement_ad if ad.WhichOneof("ad_data") == "app_ad" else ad.app_ad
    other.headlines.extend(texts)
    other.descriptions.extend(descriptions)
    other.images.extend(images)
    (other.youtube_videos if hasattr(other, "youtube_videos") else other.videos).extend(videos)


def _deep_link(r: Request, E):
    link = _creates(r, "ad_group_ad_operation").ad.final_app_urls.add()
    link.os_type = E.AppUrlOperatingSystemTypeEnum.IOS
    link.url = "example://elsewhere"


APP_CAMPAIGN_CASES = [
    (APPS, lambda r, E: setattr(_campaign(r).app_campaign_setting, "app_id", "com.example.other")),
    (APPS, lambda r, E: setattr(_campaign(r).app_campaign_setting, "app_store",
                                E.AppCampaignAppStoreEnum.APPLE_APP_STORE)),
    (["app"], lambda r, E: setattr(_campaign(r), "advertising_channel_sub_type",
                                   E.AdvertisingChannelSubTypeEnum.APP_CAMPAIGN_FOR_ENGAGEMENT)),
    (["app engagement"], lambda r, E: setattr(_campaign(r), "advertising_channel_sub_type",
                                              E.AdvertisingChannelSubTypeEnum.APP_CAMPAIGN)),
    (APPS, lambda r, E: setattr(_campaign(r), "advertising_channel_type", E.AdvertisingChannelTypeEnum.DISPLAY)),
    (APPS, lambda r, E: _campaign(r).selective_optimization.conversion_actions.__setitem__(
        0, f"customers/{CID}/conversionActions/222")),
    (APPS, lambda r, E: _campaign(r).selective_optimization.conversion_actions.append(
        f"customers/{CID}/conversionActions/222")),
    (APPS, lambda r, E: _campaign(r).selective_optimization.ClearField("conversion_actions")),
    (APPS, lambda r, E: setattr(_criteria_of(r, "location")[0].location, "geo_target_constant",
                                "geoTargetConstants/1000002")),
    (APPS, lambda r, E: setattr(_criteria_of(r, "language")[1].language, "language_constant",
                                "languageConstants/1001")),
    (APPS, lambda r, E: _drop_op(r, "campaign_criterion_operation", 0)),  # no location left
    (APPS, lambda r, E: _drop_op(r, "campaign_criterion_operation", 2)),  # one language fewer
    (APPS, lambda r, E: setattr(_campaign(r), "name", "Other Launch")),
    (APPS, lambda r, E: setattr(_creates(r, "campaign_budget_operation"), "name", "Other budget")),
    (APPS, lambda r, E: setattr(_creates(r, "ad_group_operation"), "name", "Other ad group")),
]


def _extra_location(r: Request, E):
    found = [op for op in r.ops if type(op).pb(op).WhichOneof("operation") == "campaign_criterion_operation"]
    op = type(found[0])()
    type(op).pb(op).CopyFrom(type(found[0]).pb(found[0]))
    type(op).pb(op).campaign_criterion_operation.create.location.geo_target_constant = "geoTargetConstants/1000002"
    r.ops.append(op)


APP_CAMPAIGN_CASES.append((APPS, _extra_location))

APP_AD_CASES = [
    (APPS, _other_ad_type),
    (APPS, lambda r, E: setattr(_app_info(r).headlines[0], "text", "Other headline")),
    (APPS, lambda r, E: setattr(_app_info(r).descriptions[4], "text", "Other description.")),
    (APPS, lambda r, E: _app_info(r).headlines.pop()),
    (APPS, lambda r, E: _app_info(r).ClearField("descriptions")),
    (APPS, lambda r, E: setattr(_app_info(r).images[0], "asset", f"customers/{CID}/assets/7")),
    (APPS, lambda r, E: _app_info(r).images.add(asset=f"customers/{CID}/assets/7")),
    (APPS, lambda r, E: setattr(_app_videos(r)[0], "asset", f"customers/{CID}/assets/7")),
    (APPS, lambda r, E: _app_videos(r).pop()),
    (APPS, _deep_link),
    (["app engagement"], lambda r, E: setattr(_creates(r, "ad_group_ad_operation").ad.final_app_urls[0], "url",
                                              "example://elsewhere")),
    (["app engagement"], lambda r, E: setattr(_creates(r, "ad_group_ad_operation").ad.final_app_urls[0], "os_type",
                                              E.AppUrlOperatingSystemTypeEnum.IOS)),
    (["app engagement"], lambda r, E: _creates(r, "ad_group_ad_operation").ad.ClearField("final_app_urls")),
]
APP_MEDIA_CASES = [
    (lambda r, E: setattr(_assets(r, "image_asset")[0].image_asset, "data", b"other bytes"), "NEW_IMAGES"),
    (lambda r, E: setattr(_assets(r, "image_asset")[0], "name", "Test City Launch | other.png | 00000000"),
     "NEW_IMAGES"),
    (lambda r, E: setattr(_assets(r, "youtube_video_asset")[0].youtube_video_asset, "youtube_video_id", "zyxwvutsrqp"),
     "NEW_VIDEOS"),
    (lambda r, E: setattr(_assets(r, "youtube_video_asset")[0], "name", "Some video"), "NEW_VIDEOS"),
    (lambda r, E: _copy_op(r, "asset_operation", 0, f"customers/{CID}/assets/-99"), "NEW_IMAGES"),
    (lambda r, E: _copy_op(r, "asset_operation", 1, f"customers/{CID}/assets/-99"), "NEW_VIDEOS"),
]


@pytest.mark.parametrize("name, edit, expected", [
    *[(n, edit, "APP_CAMPAIGN_VALUES") for names, edit in APP_CAMPAIGN_CASES for n in names],
    *[(n, edit, "APP_AD_VALUES") for names, edit in APP_AD_CASES for n in names],
    *[(n, edit, expected) for edit, expected in APP_MEDIA_CASES for n in APPS],
])
def test_an_app_campaign_sends_exactly_the_plans_values(ads_client, build, name, edit, expected):
    r = build(name)
    edit(r, ads_client.enums)
    assert guard(ads_client, r) == [getattr(server, expected)]


# ------------------------------------------------------------------ Search campaigns


def _group_criteria(r: Request, negative: bool, group: int = 0) -> list:
    name = _all(r, "ad_group_operation")[group].resource_name
    return [c for c in _all(r, "ad_group_criterion_operation") if c.ad_group == name and c.negative is negative]


def _campaign_criteria(r: Request, kind: str) -> list:
    return [c for c in _all(r, "campaign_criterion_operation") if c.WhichOneof("criterion") == kind]


def _rsa(r: Request, pick: int = 0):
    return _all(r, "ad_group_ad_operation")[pick].ad.responsive_search_ad


def _match(name):
    return lambda E: getattr(E.KeywordMatchTypeEnum, name)


def _set_on(get, field, value):
    return lambda r, E: setattr(get(r), field, value(E) if callable(value) else value)


def _swap(get_a, get_b, field):
    def edit(r, E):
        a, b = get_a(r), get_b(r)
        va, vb = getattr(a, field), getattr(b, field)
        setattr(a, field, vb)
        setattr(b, field, va)
    return edit


def _swap_urls(get_a, get_b):
    """Swaps two messages' final URLs (a repeated field: swapped by contents)."""
    def edit(r, E):
        a, b = get_a(r).final_urls, get_b(r).final_urls
        first, second = list(a), list(b)
        a[:], b[:] = second, first
    return edit


def _swap_texts(get_list):
    def edit(r, E):
        items = get_list(r)
        first, second = items[0].text, items[1].text
        items[0].text, items[1].text = second, first
    return edit


def _keyword_moved(r: Request, E):
    """The first ad group's first keyword, sent in the second ad group: the counts stay the same."""
    _group_criteria(r, False, 0)[0].ad_group = _all(r, "ad_group_operation")[1].resource_name


SITELINK = lambda r, i=0: _assets(r, "sitelink_asset")[i]  # noqa: E731
CALLOUT = lambda r, i=0: _assets(r, "callout_asset")[i]  # noqa: E731
SNIPPET = lambda r, i=0: _assets(r, "structured_snippet_asset")[i]  # noqa: E731

SEARCH_CASES = [
    # names, campaign locations, languages and negative keywords
    ("search", _set_on(_campaign, "name", "Other Gyms - Search"), "SEARCH_CAMPAIGN"),
    ("search", _set_on(lambda r: _creates(r, "campaign_budget_operation"), "name", "Other budget"), "SEARCH_CAMPAIGN"),
    ("search", _set_on(lambda r: _all(r, "ad_group_operation")[0], "name", "Other group"), "SEARCH_CAMPAIGN"),
    ("search", _set_on(lambda r: _campaign_criteria(r, "location")[0].location, "geo_target_constant",
                       "geoTargetConstants/1000002"), "SEARCH_CAMPAIGN"),
    ("search", _set_on(lambda r: _campaign_criteria(r, "language")[0].language, "language_constant",
                       "languageConstants/1019"), "SEARCH_CAMPAIGN"),
    ("search two groups", _swap(lambda r: _campaign_criteria(r, "language")[0].language,
                                lambda r: _campaign_criteria(r, "language")[1].language, "language_constant"),
     "SEARCH_CAMPAIGN"),
    ("search", _set_on(lambda r: _campaign_criteria(r, "keyword")[0].keyword, "text", "gym"), "SEARCH_CAMPAIGN"),
    ("search", _set_on(lambda r: _campaign_criteria(r, "keyword")[0].keyword, "match_type", _match("BROAD")),
     "SEARCH_CAMPAIGN"),
    # each ad group's keywords and negative keywords, in order
    ("search", _set_on(lambda r: _group_criteria(r, False)[0].keyword, "text", "gym"), "SEARCH_KEYWORDS"),
    ("search", _set_on(lambda r: _group_criteria(r, False)[1].keyword, "match_type", _match("BROAD")),
     "SEARCH_KEYWORDS"),
    ("search", _swap(lambda r: _group_criteria(r, False)[0].keyword, lambda r: _group_criteria(r, False)[2].keyword,
                     "text"), "SEARCH_KEYWORDS"),
    ("search", _set_on(lambda r: _group_criteria(r, True)[0].keyword, "text", "gym"), "SEARCH_KEYWORDS"),
    ("search", _set_on(lambda r: _group_criteria(r, True)[0].keyword, "match_type", _match("PHRASE")),
     "SEARCH_KEYWORDS"),
    ("search two groups", _keyword_moved, "SEARCH_KEYWORDS"),
    # each ad: its own ad group, texts and paths
    ("search", _set_on(lambda r: _rsa(r).headlines[0], "text", "Other headline"), "SEARCH_ADS"),
    ("search", _set_on(lambda r: _rsa(r).descriptions[3], "text", "Other description."), "SEARCH_ADS"),
    ("search", lambda r, E: _rsa(r).headlines.pop(), "SEARCH_ADS"),
    ("search", _swap_texts(lambda r: _rsa(r).descriptions), "SEARCH_ADS"),
    ("search", _set_on(lambda r: _rsa(r), "path1", "deals"), "SEARCH_ADS"),
    ("search", lambda r, E: _rsa(r).ClearField("path2"), "SEARCH_ADS"),
    ("search two groups", _set_on(lambda r: _rsa(r, 1), "path1", "yoga"), "SEARCH_ADS"),
    ("search two groups", _swap(lambda r: _all(r, "ad_group_ad_operation")[0],
                                lambda r: _all(r, "ad_group_ad_operation")[1], "ad_group"), "SEARCH_ADS"),
    # final URLs: exactly the plan's, on the allowed domains
    ("search", lambda r, E: _all(r, "ad_group_ad_operation")[0].ad.final_urls.__setitem__(
        0, "https://www.example.com/other"), "SEARCH_URLS"),
    ("search", lambda r, E: _all(r, "ad_group_ad_operation")[0].ad.final_urls.__setitem__(
        0, "https://www.example.net/gym"), "SEARCH_URLS"),
    ("search", lambda r, E: _all(r, "ad_group_ad_operation")[0].ad.final_urls.append(
        "https://www.example.com/gym"), "SEARCH_URLS"),
    ("search two groups", _swap_urls(lambda r: _all(r, "ad_group_ad_operation")[0].ad,
                                     lambda r: _all(r, "ad_group_ad_operation")[1].ad), "SEARCH_URLS"),
    ("search", lambda r, E: SITELINK(r).final_urls.__setitem__(0, "https://www.example.com/other"), "SEARCH_URLS"),
    ("search", lambda r, E: SITELINK(r, 1).final_urls.__setitem__(0, "https://www.example.net/prices"), "SEARCH_URLS"),
    ("search", lambda r, E: SITELINK(r).ClearField("final_urls"), "SEARCH_URLS"),
    ("search", _swap_urls(lambda r: SITELINK(r, 0), lambda r: SITELINK(r, 1)), "SEARCH_URLS"),
    # sitelinks, callouts and snippets, and the kind each is linked as
    ("search", _set_on(lambda r: SITELINK(r).sitelink_asset, "link_text", "Other link"), "SEARCH_ASSETS"),
    ("search", _set_on(lambda r: SITELINK(r).sitelink_asset, "description2", "Other words"), "SEARCH_ASSETS"),
    ("search two groups", _set_on(lambda r: SITELINK(r, 4).sitelink_asset, "description1", "Added words"),
     "SEARCH_ASSETS"),
    ("search", _set_on(lambda r: CALLOUT(r, 2).callout_asset, "callout_text", "Free Parking"), "SEARCH_ASSETS"),
    ("search", _swap(lambda r: CALLOUT(r, 0).callout_asset, lambda r: CALLOUT(r, 1).callout_asset, "callout_text"),
     "SEARCH_ASSETS"),
    ("search", _set_on(lambda r: SNIPPET(r).structured_snippet_asset, "header", "Brands"), "SEARCH_ASSETS"),
    ("search", lambda r, E: SNIPPET(r).structured_snippet_asset.values.__setitem__(0, "Pilates"), "SEARCH_ASSETS"),
    ("search", lambda r, E: SNIPPET(r).structured_snippet_asset.values.pop(), "SEARCH_ASSETS"),
    ("search two groups", _swap(lambda r: SNIPPET(r, 0).structured_snippet_asset,
                                lambda r: SNIPPET(r, 1).structured_snippet_asset, "header"), "SEARCH_ASSETS"),
    ("search", _set_on(lambda r: _all(r, "campaign_asset_operation")[5], "field_type",
                       lambda E: E.AssetFieldTypeEnum.SITELINK), "SEARCH_ASSETS"),  # a callout linked as a sitelink
    ("search", _set_on(lambda r: _all(r, "campaign_asset_operation")[8], "field_type",
                       lambda E: E.AssetFieldTypeEnum.CALLOUT), "SEARCH_ASSETS"),  # the snippet linked as a callout
]


@pytest.mark.parametrize("name, edit, expected", SEARCH_CASES)
def test_a_search_campaign_sends_exactly_the_plans_values(ads_client, build, name, edit, expected):
    r = build(name)
    edit(r, ads_client.enums)
    assert guard(ads_client, r) == [getattr(server, expected)]


# ------------------------------------------------------------------ Demand Gen campaigns


def _dg_ad(r: Request, pick: int):
    return _all(r, "ad_group_ad_operation")[pick].ad


def _image_ad(r: Request, pick: int = 0):
    return _dg_ad(r, pick).demand_gen_multi_asset_ad


def _video_ad(r: Request, pick: int = 1):
    return _dg_ad(r, pick).demand_gen_video_responsive_ad


def _existing_logo(r: Request, E):
    """The video ad's logo becomes an existing asset; the image ad still uses the new logo, so nothing is unused."""
    _video_ad(r).logo_images[0].asset = f"customers/{CID}/assets/7"


def _new_square_as_logo(r: Request, E):
    _image_ad(r).logo_images[0].asset = _image_ad(r).square_marketing_images[0].asset


def _portrait_as_square_too(r: Request, E):
    _image_ad(r).square_marketing_images.add(asset=_image_ad(r).portrait_marketing_images[0].asset)


DEMAND_GEN_CASES = [
    # texts, on both kinds of ad
    (_set_on(lambda r: _image_ad(r).headlines[0], "text", "Other headline"), "DEMAND_GEN_TEXT"),
    (_set_on(lambda r: _image_ad(r).descriptions[4], "text", "Other description."), "DEMAND_GEN_TEXT"),
    (lambda r, E: _image_ad(r).headlines.pop(), "DEMAND_GEN_TEXT"),
    (_swap_texts(lambda r: _image_ad(r).descriptions), "DEMAND_GEN_TEXT"),
    (_set_on(_image_ad, "business_name", "Other Fitness"), "DEMAND_GEN_TEXT"),
    (_set_on(lambda r: _video_ad(r).headlines[2], "text", "Other headline"), "DEMAND_GEN_TEXT"),
    (_set_on(lambda r: _video_ad(r).long_headlines[0], "text", "Another long headline for the video ad"),
     "DEMAND_GEN_TEXT"),
    (_swap_texts(lambda r: _video_ad(r).long_headlines), "DEMAND_GEN_TEXT"),
    (lambda r, E: _video_ad(r).ClearField("long_headlines"), "DEMAND_GEN_TEXT"),
    (_set_on(lambda r: _video_ad(r).descriptions[1], "text", "Other description."), "DEMAND_GEN_TEXT"),
    (_set_on(lambda r: _video_ad(r).business_name, "text", "Other Fitness"), "DEMAND_GEN_TEXT"),
    # the final URL: the planned one, not just one on an allowed domain
    (lambda r, E: _dg_ad(r, 0).final_urls.__setitem__(0, "https://www.example.com/other"), "DEMAND_GEN_TEXT"),
    (_swap_urls(lambda r: _dg_ad(r, 0), lambda r: _dg_ad(r, 1)), "DEMAND_GEN_TEXT"),
    # every image slot, logos included
    (_swap(lambda r: _image_ad(r).marketing_images[0], lambda r: _image_ad(r).square_marketing_images[0], "asset"),
     "DEMAND_GEN_IMAGES"),
    (_existing_logo, "DEMAND_GEN_IMAGES"),
    (_new_square_as_logo, "DEMAND_GEN_IMAGES"),
    (_portrait_as_square_too, "DEMAND_GEN_IMAGES"),
    # the new image assets: the plan's files, bytes and names, in order
    (lambda r, E: setattr(_assets(r, "image_asset")[3].image_asset, "data", b"other bytes"), "NEW_IMAGES"),
    (_set_on(lambda r: _assets(r, "image_asset")[0], "name", "Pulse Springfield - Demand Gen | other.png | 00000000"),
     "NEW_IMAGES"),
    (lambda r, E: _swap_ops(r, "asset_operation"), "NEW_IMAGES"),
    # the new video asset's name
    (_set_on(lambda r: _assets(r, "youtube_video_asset")[0], "name", "Some video"), "DEMAND_GEN_MEDIA"),
]


@pytest.mark.parametrize("edit, expected", DEMAND_GEN_CASES)
def test_a_demand_gen_campaign_sends_exactly_the_plans_values(ads_client, build, edit, expected):
    r = build("demand gen")
    edit(r, ads_client.enums)
    assert guard(ads_client, r) == [getattr(server, expected)]


@pytest.mark.parametrize("edit, expected", [
    # the second ad group's image ad reuses the square file: its slot must still be that file's new asset
    (_set_on(lambda r: _image_ad(r, 2).square_marketing_images[0], "asset", f"customers/{CID}/assets/7"),
     "DEMAND_GEN_IMAGES"),
    (_swap(lambda r: _image_ad(r, 0).marketing_images[0], lambda r: _image_ad(r, 0).marketing_images[1], "asset"),
     "DEMAND_GEN_IMAGES"),
    (_set_on(lambda r: _video_ad(r, 3).business_name, "text", "Pulse Fitness"), "DEMAND_GEN_TEXT"),
    (_set_on(lambda r: _image_ad(r, 2).headlines[0], "text", "Classes Every Day"), "DEMAND_GEN_TEXT"),
])
def test_values_are_compared_ad_by_ad(ads_client, build, edit, expected):
    r = build("demand gen mixed")
    edit(r, ads_client.enums)
    assert guard(ads_client, r) == [getattr(server, expected)]


# ------------------------------------------------------------------ the fixed values every campaign compiler sends

CAMPAIGN_KINDS = ["app", "app engagement", "search", "demand gen"]


def _clear(get, field):
    return lambda r, E: get(r).ClearField(field)


def _enum(enum, member):
    return lambda E: getattr(getattr(E, enum), member)


def _positive_keyword(r: Request, pick: int = 0):
    return [c for c in _all(r, "ad_group_criterion_operation")
            if c.WhichOneof("criterion") == "keyword" and not c.negative][pick]


_BUDGET = lambda r: _creates(r, "campaign_budget_operation")  # noqa: E731
_GROUP = lambda r, pick=0: _all(r, "ad_group_operation")[pick]  # noqa: E731
_AD = lambda r, pick=0: _all(r, "ad_group_ad_operation")[pick]  # noqa: E731
EU = "contains_eu_political_advertising"

# (the kinds that create the part, the edit, the problem); a value the compiler sets is pinned, and so is its
# presence: cleared, it reads as UNSPECIFIED
FIXED_CASES = [
    (CAMPAIGN_KINDS, _set_on(_campaign, EU, _enum("EuPoliticalAdvertisingStatusEnum",
                                                  "CONTAINS_EU_POLITICAL_ADVERTISING")), "EU_POLITICAL"),
    (CAMPAIGN_KINDS, _clear(_campaign, EU), "EU_POLITICAL"),
    (CAMPAIGN_KINDS, _set_on(_BUDGET, "delivery_method", _enum("BudgetDeliveryMethodEnum", "ACCELERATED")),
     "BUDGET_DELIVERY"),
    (CAMPAIGN_KINDS, _clear(_BUDGET, "delivery_method"), "BUDGET_DELIVERY"),
    (CAMPAIGN_KINDS, _set_on(_GROUP, "status", _enum("AdGroupStatusEnum", "PAUSED")), "STATUSES"),
    (CAMPAIGN_KINDS, _set_on(_GROUP, "status", _enum("AdGroupStatusEnum", "REMOVED")), "STATUSES"),
    (CAMPAIGN_KINDS, _clear(_GROUP, "status"), "STATUSES"),
    (CAMPAIGN_KINDS, _set_on(_AD, "status", _enum("AdGroupAdStatusEnum", "PAUSED")), "STATUSES"),
    (CAMPAIGN_KINDS, _clear(_AD, "status"), "STATUSES"),
    (["demand gen"], _set_on(lambda r: _AD(r, 1), "status", _enum("AdGroupAdStatusEnum", "REMOVED")), "STATUSES"),
    (["search"], _set_on(_positive_keyword, "status", _enum("AdGroupCriterionStatusEnum", "PAUSED")), "STATUSES"),
    (["search"], _clear(_positive_keyword, "status"), "STATUSES"),
    (["search two groups"], _set_on(lambda r: _positive_keyword(r, -1), "status",
                                    _enum("AdGroupCriterionStatusEnum", "REMOVED")), "STATUSES"),
    (["search"], _set_on(_GROUP, "type_", _enum("AdGroupTypeEnum", "DISPLAY_STANDARD")), "AD_GROUP_TYPE"),
    (["search"], _set_on(_GROUP, "type_", _enum("AdGroupTypeEnum", "SEARCH_DYNAMIC_ADS")), "AD_GROUP_TYPE"),
    (["search"], _clear(_GROUP, "type_"), "AD_GROUP_TYPE"),
    (["search two groups"], _set_on(lambda r: _GROUP(r, 1), "type_", _enum("AdGroupTypeEnum", "DISPLAY_STANDARD")),
     "AD_GROUP_TYPE"),
]


@pytest.mark.parametrize("name, edit, expected", [(n, e, x) for names, e, x in FIXED_CASES for n in names])
def test_the_fixed_values_are_the_compilers(ads_client, build, name, edit, expected):
    r = build(name)
    edit(r, ads_client.enums)
    assert guard(ads_client, r) == [getattr(server, expected, expected)]


_CAMPAIGN_CRITERION = lambda r: _all(r, "campaign_criterion_operation")[0]  # noqa: E731


@pytest.mark.parametrize("name, edit, field, part", [
    # a field the compiler leaves unset is refused by the per-kind allowlist (a negative keyword's or a Demand Gen
    # criterion's status: test_allowlists.py)
    ("app", _set_on(_GROUP, "type_", _enum("AdGroupTypeEnum", "SEARCH_STANDARD")), "type_", "ad group"),
    ("demand gen", _set_on(_GROUP, "type_", _enum("AdGroupTypeEnum", "DISPLAY_STANDARD")), "type_", "ad group"),
    ("app", _set_on(_CAMPAIGN_CRITERION, "status", _enum("CampaignCriterionStatusEnum", "PAUSED")), "status",
     "campaign criterion"),
    ("search", _set_on(_CAMPAIGN_CRITERION, "status", _enum("CampaignCriterionStatusEnum", "PAUSED")), "status",
     "campaign criterion"),
])
def test_a_fixed_field_the_compiler_leaves_unset_is_refused(ads_client, build, name, edit, field, part):
    r = build(name)
    edit(r, ads_client.enums)
    assert guard(ads_client, r) == [f"internal safety check: unexpected field {field} on {part}"]


# ------------------------------------------------------------------ Performance Max


def _links(r: Request, kind: str, group: int = 0) -> list:
    """The asset group links of one field type (an AssetFieldTypeEnum name) in one asset group, in order."""
    groups = [g.resource_name for g in _all(r, "asset_group_operation")]
    links = _all(r, "asset_group_asset_operation")
    wanted = links[0].DESCRIPTOR.fields_by_name["field_type"].enum_type.values_by_name[kind].number
    return [link for link in links if link.asset_group == groups[group] and link.field_type == wanted]


def _brand(r: Request, pick: int):
    return _all(r, "campaign_asset_operation")[pick]


PMAX_CASES = [
    # the new texts, videos and calls to action, in order
    (_set_on(lambda r: _assets(r, "text_asset")[3].text_asset, "text", "Other headline"), "PMAX_ASSETS"),
    (lambda r, E: _swap_ops(r, "asset_operation"), "PMAX_ASSETS"),  # two texts in another order
    (_set_on(lambda r: _assets(r, "youtube_video_asset")[0], "name", "Some video"), "PMAX_ASSETS"),
    (_set_on(lambda r: _assets(r, "call_to_action_asset")[0].call_to_action_asset, "call_to_action", 7),
     "PMAX_ASSETS"),
    # the new image assets: the plan's files, bytes and names, in order
    (lambda r, E: setattr(_assets(r, "image_asset")[2].image_asset, "data", b"other bytes"), "NEW_IMAGES"),
    (_set_on(lambda r: _assets(r, "image_asset")[0], "name", "Pulse Springfield - Performance Max | other.png | 0"),
     "NEW_IMAGES"),
    # the asset group's final URL and display path: the planned ones
    (lambda r, E: _all(r, "asset_group_operation")[0].final_urls.__setitem__(0, "https://www.example.com/other"),
     "PMAX_ASSET_GROUPS"),
    (_set_on(lambda r: _all(r, "asset_group_operation")[0], "path2", "deals"), "PMAX_ASSET_GROUPS"),
    # every link, slot by slot
    (_swap(lambda r: _links(r, "MARKETING_IMAGE")[0], lambda r: _links(r, "SQUARE_MARKETING_IMAGE")[0], "asset"),
     "PMAX_LINKS"),
    (_swap(lambda r: _links(r, "HEADLINE")[0], lambda r: _links(r, "HEADLINE")[1], "asset"), "PMAX_LINKS"),
    (_swap(lambda r: _links(r, "HEADLINE")[0], lambda r: _links(r, "LONG_HEADLINE")[0], "asset"), "PMAX_LINKS"),
    (_swap(lambda r: _brand(r, 1), lambda r: _brand(r, 2), "asset"), "PMAX_LINKS"),  # logo and landscape logo
    (_swap(lambda r: _brand(r, 0), lambda r: _links(r, "HEADLINE")[0], "asset"), "PMAX_LINKS"),  # business name
    # the signals
    (_set_on(lambda r: _all(r, "asset_group_signal_operation")[0].audience, "audience",
             f"customers/{CID}/audiences/6"), "PMAX_SIGNALS"),
    (_set_on(lambda r: _all(r, "asset_group_signal_operation")[1].search_theme, "text", "cheap gym"), "PMAX_SIGNALS"),
]


@pytest.mark.parametrize("edit, expected", PMAX_CASES)
def test_a_pmax_campaign_sends_exactly_the_plans_values(ads_client, build, edit, expected):
    r = build("pmax")
    edit(r, ads_client.enums)
    assert guard(ads_client, r) == [getattr(server, expected)]


@pytest.mark.parametrize("edit, expected", [
    # the second asset group reuses the square file: its slot must still be that file's new asset
    (_set_on(lambda r: _links(r, "SQUARE_MARKETING_IMAGE", 1)[0], "asset", f"customers/{CID}/assets/7"), "PMAX_LINKS"),
    (_swap(lambda r: _links(r, "MARKETING_IMAGE")[0], lambda r: _links(r, "MARKETING_IMAGE")[1], "asset"),
     "PMAX_LINKS"),  # the existing and the new landscape image in the other order
    (_swap(lambda r: _links(r, "YOUTUBE_VIDEO", 1)[0], lambda r: _links(r, "YOUTUBE_VIDEO", 1)[2], "asset"),
     "PMAX_LINKS"),
    (_swap(lambda r: _links(r, "HEADLINE", 0)[1], lambda r: _links(r, "HEADLINE", 1)[2], "asset"), "PMAX_LINKS"),
])
def test_pmax_values_are_compared_asset_group_by_asset_group(ads_client, build, edit, expected):
    r = build("pmax mixed")
    edit(r, ads_client.enums)
    assert guard(ads_client, r) == [getattr(server, expected)]


_ASSET_GROUP = lambda r, pick=0: _all(r, "asset_group_operation")[pick]  # noqa: E731
# the shared fixed values on a Performance Max request, and its own: every asset group enabled
PMAX_FIXED_CASES = [
    ("pmax", _set_on(_campaign, EU, _enum("EuPoliticalAdvertisingStatusEnum", "CONTAINS_EU_POLITICAL_ADVERTISING")),
     "EU_POLITICAL"),
    ("pmax", _clear(_campaign, EU), "EU_POLITICAL"),
    ("pmax", _set_on(_BUDGET, "delivery_method", _enum("BudgetDeliveryMethodEnum", "ACCELERATED")), "BUDGET_DELIVERY"),
    ("pmax", _clear(_BUDGET, "delivery_method"), "BUDGET_DELIVERY"),
    ("pmax", _set_on(_ASSET_GROUP, "status", _enum("AssetGroupStatusEnum", "PAUSED")), "STATUSES"),
    ("pmax", _set_on(_ASSET_GROUP, "status", _enum("AssetGroupStatusEnum", "REMOVED")), "STATUSES"),
    ("pmax", _clear(_ASSET_GROUP, "status"), "STATUSES"),
    ("pmax mixed", _set_on(lambda r: _ASSET_GROUP(r, 1), "status", _enum("AssetGroupStatusEnum", "PAUSED")), "STATUSES"),
]


@pytest.mark.parametrize("name, edit, expected", PMAX_FIXED_CASES)
def test_a_pmax_campaigns_fixed_values_are_the_compilers(ads_client, build, name, edit, expected):
    r = build(name)
    edit(r, ads_client.enums)
    assert guard(ads_client, r) == [getattr(server, expected)]
