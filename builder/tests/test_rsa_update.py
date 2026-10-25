"""asset_update plans for responsive search ads: rules, compile, live checks and the change tool."""

import pytest

from gads_builder import gads, plans, server, templates
from gads_builder.compile_assets import compile_asset_update
from test_server import CID, checked, drafted, real, wired, window  # noqa: F401  (wired, window: fixtures)

AD = "customers/1234567890/ads/77"
HEADLINES = ["Gym Membership Deals", "Flexible Gym Membership", "Join Today", "Classes Every Day"]
DESCRIPTIONS = ["Classes every day.", "Cancel any time."]
BASE = {"headlines": HEADLINES, "descriptions": DESCRIPTIONS, "images": [], "videos": []}


def rsa_plan(**over) -> dict:
    data = {"kind": "asset_update", "ad": AD, "ad_type": "RESPONSIVE_SEARCH_AD",
            "base": {k: list(v) for k, v in BASE.items()},
            "add_headlines": ["Open Early and Late"], "remove": ["Join Today"]}
    data.update(over)
    return data


def errors(data, cfg):
    return [f"{f.field}: {f.message}" for f in plans.validate(plans.plan_from_dict(data), cfg) if f.level == "error"]


# ------------------------------------------------------------------- rules


def test_a_valid_rsa_update(cfg):
    assert errors(rsa_plan(), cfg) == []
    plan = plans.plan_from_dict(rsa_plan())
    assert plans.merged_assets(plan)["headlines"] == [
        "Gym Membership Deals", "Flexible Gym Membership", "Classes Every Day", "Open Early and Late"]


@pytest.mark.parametrize("over, expected", [
    ({"add_headlines": [], "remove": ["Join Today", "Classes Every Day"]},
     "headlines: the ad would have 2; a responsive search ad needs 3 to 15"),
    ({"add_headlines": [f"New headline {i}" for i in range(12)], "remove": []},
     "headlines: the ad would have 16; a responsive search ad needs 3 to 15"),
    ({"add_headlines": [], "remove": ["Cancel any time."]},
     "descriptions: the ad would have 1; a responsive search ad needs 2 to 4"),
    ({"add_descriptions": ["One more.", "Two more.", "Three more."], "add_headlines": [], "remove": []},
     "descriptions: the ad would have 5; a responsive search ad needs 2 to 4"),
    ({"add_images": [{"asset": "customers/1234567890/assets/5"}]}, "add_images: a responsive search ad has no images or videos"),
    ({"add_videos": [{"youtube_id": "abcdefghijk"}]}, "add_videos: a responsive search ad has no images or videos"),
    ({"add_headlines": ["x" * 31]}, "add_headlines[0]: is 31 characters; the limit is 30"),
    ({"add_descriptions": ["y" * 91]}, "add_descriptions[0]: is 91 characters; the limit is 90"),
    ({"add_headlines": ["join today"], "remove": []}, "headlines[4]: duplicates headlines[2]"),
    ({"ad_type": "TEXT_AD"}, "ad_type: must be APP_AD, APP_ENGAGEMENT_AD or RESPONSIVE_SEARCH_AD"),
])
def test_rsa_limits(cfg, over, expected):
    assert expected in errors(rsa_plan(**over), cfg)


@pytest.mark.parametrize("media", ["images", "videos"])
def test_an_rsa_base_with_images_or_videos_is_an_error(cfg, media):
    base = dict(BASE, **{media: ["customers/1234567890/assets/1"]})
    found = errors(rsa_plan(base=base), cfg)
    assert "base: a responsive search ad has no images or videos" in found
    assert not any(e.startswith(("add_images", "add_videos")) for e in found), found


def test_a_90_character_rsa_description_is_fine(cfg):
    assert errors(rsa_plan(add_descriptions=["y" * 90]), cfg) == []


def test_app_limits_still_apply_to_app_ads(cfg):
    data = rsa_plan(ad_type="APP_AD", add_headlines=["One", "Two"], remove=[])
    assert "headlines: 6 given; the maximum is 5" in errors(data, cfg)


# ------------------------------------------------------------------- compile


def test_compile_replaces_the_two_text_lists(ads_client):
    ops, labels = compile_asset_update(plans.plan_from_dict(rsa_plan()), ads_client, CID)
    assert labels == ["ad update"]
    update = ops[0].ad_operation.update
    assert update.resource_name == AD
    assert [h.text for h in update.responsive_search_ad.headlines] == [
        "Gym Membership Deals", "Flexible Gym Membership", "Classes Every Day", "Open Early and Late"]
    assert [d.text for d in update.responsive_search_ad.descriptions] == DESCRIPTIONS
    assert list(ops[0].ad_operation.update_mask.paths) == [
        "responsive_search_ad.headlines", "responsive_search_ad.descriptions"]
    assert "app_ad" not in update


# ------------------------------------------------------------------- reading the live ad


def rsa_row(ads_client, *, headlines=HEADLINES, pinned=None, campaign="Springfield Gyms", status="ENABLED",
            pinned_description=None):
    row = ads_client.get_type("GoogleAdsRow")
    row.campaign.name = campaign
    row.campaign.status = getattr(ads_client.enums.CampaignStatusEnum, status)
    ad = row.ad_group_ad.ad
    ad.resource_name = AD
    ad.type_ = ads_client.enums.AdTypeEnum.RESPONSIVE_SEARCH_AD
    for text in headlines:
        asset = ads_client.get_type("AdTextAsset")
        asset.text = text
        if text == pinned:
            asset.pinned_field = ads_client.enums.ServedAssetFieldTypeEnum.HEADLINE_1
        ad.responsive_search_ad.headlines.append(asset)
    for text in DESCRIPTIONS:
        asset = ads_client.get_type("AdTextAsset")
        asset.text = text
        if text == pinned_description:
            asset.pinned_field = ads_client.enums.ServedAssetFieldTypeEnum.DESCRIPTION_2
        ad.responsive_search_ad.descriptions.append(asset)
    return row


def live_rsa(monkeypatch, ads_client, **kw):
    """gads.search for ad 77 only. Returns the queries it answered."""
    row, queries = rsa_row(ads_client, **kw), []

    def search(client, cid, query):
        queries.append(query)
        if cid == CID and query.endswith("FROM ad_group_ad WHERE ad_group_ad.ad.id = 77"):
            return [row]
        raise RuntimeError(f"unexpected query {query}")

    monkeypatch.setattr(gads, "search", search)
    return queries


def test_current_ad_reads_an_rsa(ads_client, monkeypatch):
    queries = live_rsa(monkeypatch, ads_client)
    assert templates.current_ad(ads_client, CID, AD)[0] == BASE
    assert "ad_group_ad.ad.responsive_search_ad.headlines" in queries[0]


def test_pins_are_detected(ads_client, monkeypatch):
    live_rsa(monkeypatch, ads_client)
    assert templates.current_ad(ads_client, CID, AD)[1] is False
    live_rsa(monkeypatch, ads_client, pinned="Join Today")
    assert templates.current_ad(ads_client, CID, AD)[1] is True


def test_the_assets_and_the_pins_come_from_one_read(ads_client, monkeypatch):
    queries = live_rsa(monkeypatch, ads_client, pinned="Join Today")
    assert templates.current_ad(ads_client, CID, AD) == (BASE, True)
    assert queries == [f"SELECT {templates.AD_FIELDS} FROM ad_group_ad WHERE ad_group_ad.ad.id = 77"]


@pytest.mark.parametrize("ad", [
    "customers/1234567890/ads/77 OR ad_group_ad.ad.id > 0",
    "customers/1234567890/ads/77\n",
    "customers/1234567890/ads/\u0667\u0667",  # Arabic-Indic digits: int() would read them
    "customers/1234567890/ads/",
    "customers/1234567890/assets/77",
    "77",
])
def test_an_ad_id_that_is_not_ascii_digits_never_reaches_a_query(ads_client, monkeypatch, ad):
    def search(client, cid, query):
        raise AssertionError(f"queried: {query}")

    monkeypatch.setattr(gads, "search", search)
    with pytest.raises(LookupError, match="is not an ad of the form customers/<id>/ads/<id>"):
        templates.current_ad(ads_client, CID, ad)


def test_check_plan_reads_the_ad_once_for_its_assets_and_pins(wired, monkeypatch, ads_client):  # noqa: F811
    queries = live_rsa(monkeypatch, ads_client)
    out = drafted(rsa_plan())
    assert server.check_plan(out["plan_path"])["passed"] is True
    assert [q.split(" FROM ")[0] for q in queries] == [
        "SELECT campaign.name, campaign.status, ad_group_ad.ad.type", f"SELECT {templates.AD_FIELDS}"]


# ------------------------------------------------------------------- the change tool

SHOWN = {"campaign_name": "Springfield Gyms", "campaign_status": "ENABLED",
         "added": ["headline: Open Early and Late"], "removed": ["headline: Join Today"]}


def update(out, **over):
    return server.update_campaign_assets(out["plan_path"], **{"customer_id": CID, "fingerprint": out["fingerprint"],
                                                               **SHOWN, **over})


def test_an_rsa_update_end_to_end(wired, window, monkeypatch, ads_client):  # noqa: F811
    live_rsa(monkeypatch, ads_client)
    out = drafted(rsa_plan())
    check = server.check_plan(out["plan_path"])
    assert check["passed"] is True and check["approval"] == {"customer_id": CID, "fingerprint": out["fingerprint"], **SHOWN}
    assert "immediately" in check["note"]
    assert update(out)["done"] is True
    [sent] = real(wired)
    assert list(sent[-1].ad_operation.update_mask.paths) == ["responsive_search_ad.headlines",
                                                             "responsive_search_ad.descriptions"]
    [(_, lines)] = window.shown
    assert lines[3:] == ['Campaign: "Springfield Gyms" (ENABLED)', "LIVE: takes effect immediately",
                         f"Ad: {AD} (RESPONSIVE_SEARCH_AD)", "Adds 1 · removes 1",
                         "Removed:", '- headline: "Join Today"', "Added:", '- headline: "Open Early and Late"']


def test_a_pinned_rsa_is_refused(wired, monkeypatch, ads_client):  # noqa: F811
    live_rsa(monkeypatch, ads_client, pinned="Gym Membership Deals")
    out = drafted(rsa_plan())
    check = server.check_plan(out["plan_path"])
    assert check == {"passed": False, "errors": [
        "ad: some headlines or descriptions are pinned, and this update would unpin them; change pinned ads in Google Ads"]}
    assert wired == []


def test_a_pinned_description_is_refused_too(wired, monkeypatch, ads_client):  # noqa: F811
    live_rsa(monkeypatch, ads_client, pinned_description="Cancel any time.")
    assert templates.current_ad(ads_client, CID, AD)[1] is True
    out = drafted(rsa_plan())
    check = server.check_plan(out["plan_path"])
    assert check["passed"] is False and "pinned" in check["errors"][0]
    assert wired == []


def test_a_pin_added_after_the_check_is_refused(wired, monkeypatch, ads_client):  # noqa: F811
    live_rsa(monkeypatch, ads_client)
    out = checked(rsa_plan())
    live_rsa(monkeypatch, ads_client, pinned="Classes Every Day")
    res = update(out)
    assert res["done"] is False and "pinned" in res["errors"][0] and real(wired) == []


def test_a_pin_added_while_the_window_is_open_is_refused(wired, monkeypatch, ads_client):  # noqa: F811
    live_rsa(monkeypatch, ads_client)
    out = checked(rsa_plan())

    def window(title, lines, timeout_s=60):
        live_rsa(monkeypatch, ads_client, pinned="Classes Every Day")
        return True

    monkeypatch.setattr(server.confirm, "ask_person", window)
    res = update(out)
    assert res["done"] is False and res["errors"][0] == server.CHANGED_WHILE_OPEN and "pinned" in res["errors"][1]
    assert real(wired) == []


def test_the_mask_guard_allows_only_the_two_rsa_lists(wired, monkeypatch, ads_client):  # noqa: F811
    live_rsa(monkeypatch, ads_client)
    out = checked(rsa_plan())
    original = server.compile_asset_update

    def widened(plan, client, cid):
        ops, labels = original(plan, client, cid)
        ops[-1].ad_operation.update_mask.paths.append("responsive_search_ad.path1")
        return ops, labels

    monkeypatch.setattr(server, "compile_asset_update", widened)
    res = update(out)
    assert res == {"done": False, "errors": [
        "internal safety check: the ad update would change ['responsive_search_ad.path1']"]}
    assert real(wired) == []


def _widened(original, edit):
    def compiler(plan, client, cid):
        ops, labels = original(plan, client, cid)
        edit(ops, labels, client, cid)
        return ops, labels
    return compiler


def _app_paths(ops, labels, client, cid):
    ops[-1].ad_operation.update_mask.paths.append("app_ad.headlines")


def _extra_asset(ops, labels, client, cid):
    op = client.get_type("MutateOperation")
    op.asset_operation.create.resource_name = f"customers/{cid}/assets/-9"
    op.asset_operation.create.callout_asset.callout_text = "Free Parking"
    ops.insert(0, op)
    labels.insert(0, "sneaky asset")


@pytest.mark.parametrize("edit, expected", [
    (_app_paths, "internal safety check: the ad update would change ['app_ad.headlines']"),
    (_extra_asset, "internal safety check: a responsive search ad update creates no assets"),
])
def test_an_rsa_update_changes_only_its_two_text_lists(wired, monkeypatch, ads_client, edit, expected):  # noqa: F811
    live_rsa(monkeypatch, ads_client)
    out = checked(rsa_plan())
    monkeypatch.setattr(server, "compile_asset_update", _widened(server.compile_asset_update, edit))
    assert update(out) == {"done": False, "errors": [expected]}
    assert real(wired) == []
    before = len(wired)
    assert server.check_plan(out["plan_path"]) == {"passed": False, "errors": [expected]} and len(wired) == before
