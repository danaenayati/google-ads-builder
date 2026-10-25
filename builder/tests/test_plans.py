import dataclasses
import json
import sys
from pathlib import Path

import pytest

from gads_builder import plans
from gads_builder.config import AppRef


def errors(findings):
    return [f"{f.field}: {f.message}" for f in findings if f.level == "error"]


def test_valid_plan_has_no_errors(cfg, app_plan):
    plan = plans.plan_from_dict(app_plan())
    assert errors(plans.validate(plan, cfg)) == []


def test_unknown_kind_and_field_are_rejected(app_plan):
    with pytest.raises(plans.PlanError, match="unknown plan kind"):
        plans.plan_from_dict({"kind": "magic"})
    with pytest.raises(plans.PlanError):
        plans.plan_from_dict(app_plan(colour="red"))


@pytest.mark.parametrize(
    "override, expected",
    [
        ({"app_id": "com.other"}, "allow-list"),
        ({"currency": "EUR"}, "the account uses USD"),
        ({"daily_budget": 600.0}, "above the cap"),
        ({"target_cpa": None}, "target_cpa"),
        ({"subtype": "APP_CAMPAIGN_FOR_ENGAGEMENT", "goal": "OPTIMIZE_INSTALLS_TARGET_INSTALL_COST"}, "not installs"),
        ({"headlines": ["a", "b", "c", "d", "e", "f"]}, "maximum is 5"),
        ({"headlines": ["x" * 31]}, "limit is 30"),
        ({"headlines": ["Fast workout", "fast workout"]}, "duplicates"),
        ({"locations": ["Somewhere"]}, "geoTargetConstants"),
        ({"conversion_actions": ["customers/9999999999/conversionActions/1"]}, "this account"),
        ({"videos": [{"youtube_id": "short"}]}, "YouTube video ID"),
        ({"images": [{"file": "a.png", "asset": "customers/1234567890/assets/1"}]}, "exactly one"),
    ],
)
def test_rule_violations(cfg, app_plan, override, expected):
    found = errors(plans.validate(plans.plan_from_dict(app_plan(**override)), cfg))
    assert any(expected in e for e in found), found


def test_over_cap_allowed_with_exact_confirmation(cfg, app_plan):
    plan = plans.plan_from_dict(app_plan(daily_budget=600.0, confirm_budget_over_cap=600.0))
    assert errors(plans.validate(plan, cfg)) == []


def test_image_ratio_coverage_warning(cfg, app_plan, make_png):
    plan = plans.plan_from_dict(app_plan(images=[{"file": str(make_png(1200, 628))}]))
    warnings = [f.message for f in plans.validate(plan, cfg) if f.level == "warning"]
    assert any("portrait, square" in w for w in warnings)


def test_fingerprint_tracks_content_and_image_bytes(app_plan, make_png):
    img = make_png(1200, 1200, "sq.png")
    plan = plans.plan_from_dict(app_plan(images=[{"file": str(img)}]))
    first = plans.fingerprint(plan)
    assert plans.fingerprint(plans.plan_from_dict(app_plan(images=[{"file": str(img)}]))) == first
    edited = plans.plan_from_dict(app_plan(images=[{"file": str(img)}], headlines=["New headline"]))
    assert plans.fingerprint(edited) != first
    make_png(1200, 1200, "sq.png", color=(0, 0, 0))  # same file name, different pixels
    assert plans.fingerprint(plan) != first


def test_save_and_load_roundtrip(cfg, app_plan):
    plan = plans.plan_from_dict(app_plan())
    path = plans.save_plan(plan, cfg, plans.validate(plan, cfg))
    assert path.name == "plan.json" and (path.parent / "summary.md").is_file()
    assert plans.load_plan(path) == plan
    summary = (path.parent / "summary.md").read_text(encoding="utf-8")
    assert "PAUSED" in summary and "Fast workouts" in summary and "300.00 USD" in summary


def _update(**over):
    data = {
        "kind": "asset_update",
        "ad": "customers/1234567890/ads/55",
        "ad_type": "APP_AD",
        "base": {
            "headlines": ["Old one", "Old two"],
            "descriptions": ["Old description"],
            "images": ["customers/1234567890/assets/1"],
            "videos": [],
        },
    }
    data.update(over)
    return data


def test_asset_update_merge_and_rules(cfg):
    plan = plans.plan_from_dict(_update(add_headlines=["New one"], remove=["Old two"]))
    assert errors(plans.validate(plan, cfg)) == []
    merged = plans.merged_assets(plan)
    assert merged["headlines"] == ["Old one", "New one"]
    assert merged["images"] == ["customers/1234567890/assets/1"]


@pytest.mark.parametrize(
    "override, expected",
    [
        ({}, "nothing to change"),
        ({"remove": ["Not there"]}, "is not on the ad"),
        ({"add_headlines": ["a", "b", "c", "d"]}, "maximum is 5"),
        ({"add_headlines": ["Old one"]}, "duplicates"),
        ({"ad": "customers/9999999999/ads/55", "add_headlines": ["x y"]}, "this account"),
    ],
)
def test_asset_update_violations(cfg, override, expected):
    found = errors(plans.validate(plans.plan_from_dict(_update(**override)), cfg))
    assert any(expected in e for e in found), found


def test_image_upload_plan(cfg, make_png):
    plan = plans.plan_from_dict({"kind": "image_upload", "files": [str(make_png(1200, 628))]})
    assert errors(plans.validate(plan, cfg)) == []
    assert errors(plans.validate(plans.plan_from_dict({"kind": "image_upload", "files": []}), cfg))


def test_slugify():
    assert plans.slugify("UAC - New City Launch!", "x") == "uac-new-city-launch"
    assert plans.slugify("حملة", "app_campaign") == "app_campaign"


def _warnings(findings):
    return [f"{f.field}: {f.message}" for f in findings if f.level == "warning"]


DEEP_LINK = [{"os_type": "ANDROID", "url": "example://open/.*"}]


def test_engagement_and_maximize_plans_are_valid(cfg, app_plan):
    eng = plans.plan_from_dict(app_plan(subtype="APP_CAMPAIGN_FOR_ENGAGEMENT", final_app_urls=DEEP_LINK))
    assert errors(plans.validate(eng, cfg)) == []
    mx = plans.plan_from_dict(
        app_plan(goal="OPTIMIZE_IN_APP_CONVERSIONS_WITHOUT_TARGET_CPA", target_cpa=None)
    )
    assert errors(plans.validate(mx, cfg)) == []


def test_in_app_goal_needs_conversion_actions(cfg, app_plan):
    found = errors(plans.validate(plans.plan_from_dict(app_plan(conversion_actions=[])), cfg))
    assert any("conversion_actions" in e for e in found)


def test_install_goal_with_conversion_actions_has_no_misleading_warning(cfg, app_plan):
    plan = plans.plan_from_dict(app_plan(goal="OPTIMIZE_INSTALLS_TARGET_INSTALL_COST"))
    assert not any("ignore conversion_actions" in w for w in _warnings(plans.validate(plan, cfg)))


def test_target_cpa_checks(cfg, app_plan):
    nan = plans.plan_from_dict(app_plan(target_cpa=float("nan")))
    assert any("target_cpa" in e for e in errors(plans.validate(nan, cfg)))
    high = plans.plan_from_dict(app_plan(target_cpa=400.0, daily_budget=300.0))
    assert any("target_cpa" in w for w in _warnings(plans.validate(high, cfg)))


def test_cross_account_media(cfg, app_plan):
    plan = plans.plan_from_dict(
        app_plan(
            images=[{"asset": "customers/9999999999/assets/1"}],
            videos=[{"asset": "customers/9999999999/assets/2"}],
        )
    )
    found = errors(plans.validate(plan, cfg))
    assert any(e.startswith("images[0]") and "this account" in e for e in found)
    assert any(e.startswith("videos[0]") and "this account" in e for e in found)


def test_duplicate_media(cfg, app_plan, make_png):
    img = make_png(1200, 1200, "sq.png")
    a = "customers/1234567890/assets/1"
    plan = plans.plan_from_dict(
        app_plan(
            images=[{"file": str(img)}, {"file": str(img)}, {"asset": a}, {"asset": a}],
            videos=[{"youtube_id": "abcdefghijk"}, {"youtube_id": "abcdefghijk"}],
        )
    )
    found = errors(plans.validate(plan, cfg))
    assert sum("duplicate" in e and e.startswith("images[") for e in found) == 2, found
    assert any("duplicate" in e and e.startswith("videos[1]") for e in found), found
    vids = plans.plan_from_dict(
        app_plan(videos=[{"asset": "customers/1234567890/assets/5"}, {"asset": "customers/1234567890/assets/5"}])
    )
    assert any("duplicate" in e for e in errors(plans.validate(vids, cfg)))


def test_relative_image_path_is_an_error(cfg, app_plan):
    plan = plans.plan_from_dict(app_plan(images=[{"file": "pics/a.png"}]))
    assert any("use an absolute path (or ~/...)" in e for e in errors(plans.validate(plan, cfg)))
    up = plans.plan_from_dict({"kind": "image_upload", "files": ["a.png"]})
    assert any("use an absolute path" in e for e in errors(plans.validate(up, cfg)))
    upd = plans.plan_from_dict(_update(add_images=[{"file": "a.png"}]))
    assert any("use an absolute path" in e for e in errors(plans.validate(upd, cfg)))


def test_fingerprint_changes_when_bytes_move_between_files(tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    a.write_bytes(b"AAAB")
    b.write_bytes(b"CC")
    plan = plans.plan_from_dict({"kind": "image_upload", "files": [str(a), str(b)]})
    first = plans.fingerprint(plan)
    a.write_bytes(b"AAA")
    b.write_bytes(b"BCC")
    assert plans.fingerprint(plan) != first


def test_fingerprint_for_image_upload(tmp_path):
    a = tmp_path / "a.png"
    plan = plans.plan_from_dict({"kind": "image_upload", "files": [str(a)]})
    missing = plans.fingerprint(plan)
    a.write_bytes(b"x")
    present = plans.fingerprint(plan)
    a.write_bytes(b"y")
    assert len({missing, present, plans.fingerprint(plan)}) == 3


def test_asset_update_base_must_be_complete(cfg):
    base = {"headlines": ["Old one"], "descriptions": ["Old description"], "images": []}
    found = errors(plans.validate(plans.plan_from_dict(_update(base=base, add_headlines=["x y"])), cfg))
    assert any(e.startswith("base:") and "describe_campaign" in e for e in found)
    bad = dict(base, videos="nope")
    found = errors(plans.validate(plans.plan_from_dict(_update(base=bad, add_headlines=["x y"])), cfg))
    assert any(e.startswith("base:") for e in found)


def test_asset_update_ad_type_and_duplicate_image(cfg):
    found = errors(plans.validate(plans.plan_from_dict(_update(ad_type="SEARCH", add_headlines=["x y"])), cfg))
    assert any(e.startswith("ad_type") for e in found)
    dup = plans.plan_from_dict(_update(add_images=[{"asset": "customers/1234567890/assets/1"}]))
    assert any("already on the ad" in e or "duplicate" in e for e in errors(plans.validate(dup, cfg)))


def test_blocked_words_in_headlines_and_updates(cfg, app_plan):
    cfg = dataclasses.replace(cfg, blocked_words=("rival",))
    plan = plans.plan_from_dict(app_plan(headlines=["Beat Rival prices", "Train in minutes"]))
    assert any("blocked word 'rival'" in e for e in errors(plans.validate(plan, cfg)))
    plan = plans.plan_from_dict(app_plan(descriptions=["Cheaper than rival gyms."]))
    assert any("blocked word" in e for e in errors(plans.validate(plan, cfg)))
    upd = plans.plan_from_dict(_update(add_headlines=["Rival beater"]))
    assert any("blocked word" in e for e in errors(plans.validate(upd, cfg)))


def test_two_saves_get_two_folders(cfg, app_plan):
    plan = plans.plan_from_dict(app_plan())
    p1 = plans.save_plan(plan, cfg, [])
    p2 = plans.save_plan(plan, cfg, [])
    assert p1.parent != p2.parent and p1.is_file() and p2.is_file()
    assert p1.parent.name.endswith("-test-city-launch")
    up = plans.save_plan(plans.plan_from_dict({"kind": "image_upload", "files": []}), cfg, [])
    assert up.parent.name.endswith("-image-upload")
    upd = plans.save_plan(plans.plan_from_dict(_update()), cfg, [])
    assert upd.parent.name.endswith("-update-ad-55")


def test_summary_contents(cfg, app_plan):
    plan = plans.plan_from_dict(
        app_plan(
            daily_budget=600.0,
            confirm_budget_over_cap=600.0,
            brief={"names": {"geoTargetConstants/1000001": "Test | City"}},
        )
    )
    findings = plans.validate(plan, cfg)
    text = plans.render_summary(plan, findings, cfg)
    assert "Test \\| City (geoTargetConstants/1000001)" in text
    assert "customers/1234567890/conversionActions/111" in text
    assert "| Goal | In-app conversions, target cost per conversion |" in text
    assert f"**Plan fingerprint: {plans.fingerprint(plan)[:12]}**" in text
    assert "**⚠ 600.00 USD is ABOVE THE CAP" in text
    assert "**Will be created PAUSED.**" in text


def test_summary_goal_labels_cover_all_goals(cfg, app_plan):
    for goal in plans.APP_GOALS:
        text = plans.render_summary(plans.plan_from_dict(app_plan(goal=goal)), [], cfg)
        assert "| Goal | " in text and f"| Goal | {goal}" not in text


def test_summaries_for_other_kinds_and_problems(cfg, make_png):
    up = plans.plan_from_dict({"kind": "image_upload", "files": [str(make_png(1200, 628))]})
    text = plans.render_summary(up, [], cfg)
    assert "# Image upload" in text and "Plan fingerprint:" in text
    upd = plans.plan_from_dict(_update(add_headlines=["New one"]))
    text = plans.render_summary(upd, [], cfg)
    assert "New one" in text and "Plan fingerprint:" in text
    bad = plans.plan_from_dict(_update())
    text = plans.render_summary(bad, plans.validate(bad, cfg), cfg)
    assert "## Problems to fix" in text and "nothing to change" in text


def test_wrong_types_raise_plan_error(app_plan):
    for override, name in [
        ({"daily_budget": "300"}, "daily_budget"),
        ({"daily_budget": True}, "daily_budget"),
        ({"name": 5}, "name"),
        ({"headlines": "abc"}, "headlines"),
        ({"headlines": [1]}, "headlines"),
        ({"target_cpa": "x"}, "target_cpa"),
        ({"brief": None}, "brief"),
        ({"template": 3}, "template"),
        ({"images": [{"file": 3}]}, "images"),
        ({"images": ["a.png"]}, "images"),
    ]:
        with pytest.raises(plans.PlanError, match=name):
            plans.plan_from_dict(app_plan(**override))
    with pytest.raises(plans.PlanError, match="base"):
        plans.plan_from_dict(_update(base=None))
    with pytest.raises(plans.PlanError, match="files"):
        plans.plan_from_dict({"kind": "image_upload", "files": "a.png"})


def test_non_dict_sources_and_names_are_ignored_with_a_warning(cfg, app_plan):
    for brief in ({"sources": ["x"]}, {"names": "x"}):
        plan = plans.plan_from_dict(app_plan(brief=brief))
        findings = plans.validate(plan, cfg)
        assert any(
            f.level == "warning" and f.field == "brief" and "sources/names must be objects; ignored" in f.message
            for f in findings
        )
        assert "Headlines" in plans.render_summary(plan, findings, cfg)
        assert plans.save_plan(plan, cfg, findings).is_file()


def test_failed_render_leaves_no_folder(cfg, app_plan, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("render failed")

    monkeypatch.setattr(plans, "render_summary", boom)
    plan = plans.plan_from_dict(app_plan())
    with pytest.raises(RuntimeError):
        plans.save_plan(plan, cfg, [])
    assert not cfg.plans_dir.exists() or list(cfg.plans_dir.iterdir()) == []


def test_ad_texts_are_nfc_normalised(app_plan):
    decomposed = "Cafe\u0301"
    plan = plans.plan_from_dict(app_plan(headlines=[decomposed], descriptions=[decomposed]))
    assert plan.headlines == ["Caf\u00e9"] and plan.descriptions == ["Caf\u00e9"]
    upd = plans.plan_from_dict(
        _update(add_headlines=[decomposed], add_descriptions=[decomposed], remove=[decomposed])
    )
    assert upd.add_headlines == upd.add_descriptions == upd.remove == ["Caf\u00e9"]


def test_non_string_kind_is_a_plan_error():
    with pytest.raises(plans.PlanError):
        plans.plan_from_dict({"kind": ["app_campaign"]})


def test_remove_in_both_headlines_and_descriptions_is_ambiguous(cfg):
    base = {
        "headlines": ["Same text", "Old two"],
        "descriptions": ["Same text"],
        "images": [],
        "videos": [],
    }
    plan = plans.plan_from_dict(_update(base=base, remove=["Same text"]))
    found = errors(plans.validate(plan, cfg))
    assert any(
        e.startswith("remove[0]")
        and "appears in both headlines and descriptions; remove it by editing one list in a new plan" in e
        for e in found
    ), found


def _sized(tmp_path, name, mib):
    path = tmp_path / name
    with open(path, "wb") as fh:
        fh.truncate(int(mib * 1024 * 1024))
    return str(path)


def test_new_images_are_capped_at_20_mb_per_plan(cfg, app_plan, tmp_path):
    files = [_sized(tmp_path, f"big-{i}.png", 7) for i in range(3)]  # 21 MB in all
    expected = "new images total 21.0 MB; keep each plan under 20 MB"
    app = plans.plan_from_dict(app_plan(images=[{"file": f} for f in files]))
    assert f"images: {expected}" in errors(plans.validate(app, cfg))
    upload = plans.plan_from_dict({"kind": "image_upload", "files": files})
    assert f"files: {expected}" in errors(plans.validate(upload, cfg))
    update = plans.plan_from_dict(_update(add_images=[{"file": f} for f in files]))
    assert f"add_images: {expected}" in errors(plans.validate(update, cfg))
    broken = plans.plan_from_dict(_update(base={}, add_images=[{"file": f} for f in files]))
    assert f"add_images: {expected}" in errors(plans.validate(broken, cfg))  # even when the base is invalid


def test_exactly_20_mb_is_allowed_and_existing_assets_do_not_count(cfg, app_plan, tmp_path):
    upload = plans.plan_from_dict({"kind": "image_upload", "files": [_sized(tmp_path, "a.png", 20)]})
    assert not any("new images total" in e for e in errors(plans.validate(upload, cfg)))
    over = plans.plan_from_dict({"kind": "image_upload", "files": [_sized(tmp_path, "b.png", 20.01)]})
    assert any("new images total 20.1 MB" in e for e in errors(plans.validate(over, cfg)))
    assets = [{"asset": f"customers/1234567890/assets/{i}"} for i in range(1, 20)]
    app = plans.plan_from_dict(app_plan(images=assets))
    assert not any("new images total" in e for e in errors(plans.validate(app, cfg)))


def test_approval_args(cfg, app_plan, make_png):
    assert plans.approval_args(plans.plan_from_dict(app_plan(daily_budget=300)), cfg) == {
        "campaign_name": "Test City Launch", "daily_budget": 300.0, "over_cap": False}
    over = plans.approval_args(plans.plan_from_dict(app_plan(daily_budget=500.01)), cfg)
    assert over["over_cap"] is True
    assert plans.approval_args(plans.plan_from_dict(app_plan(daily_budget=500)), cfg)["over_cap"] is False
    up = plans.plan_from_dict({"kind": "image_upload", "files": [str(make_png(1200, 628)), str(make_png(1200, 1200))]})
    assert plans.approval_args(up, cfg) == {"image_count": 2}
    wide = make_png(1200, 628, "wide.png")  # shown by its full path
    upd = plans.plan_from_dict(_update(
        add_headlines=["New one"], add_descriptions=["New description"],
        add_images=[{"asset": "customers/1234567890/assets/7"}, {"file": str(wide)}],
        add_videos=[{"youtube_id": "abcdefghijk"}, {"asset": "customers/1234567890/assets/9"}],
        remove=["Old description", "customers/1234567890/assets/1", "Old two"],
    ))
    assert plans.approval_args(upd, cfg) == {
        "added": ["headline: New one", "description: New description", "image: customers/1234567890/assets/7",
                  f"image: {wide}", "video: abcdefghijk", "video: customers/1234567890/assets/9"],
        "removed": ["description: Old description", "image: customers/1234567890/assets/1", "headline: Old two"],
    }


def test_summary_shows_the_approval_arguments(cfg, app_plan, make_png):
    app = plans.plan_from_dict(app_plan())
    text = plans.render_summary(app, [], cfg)
    fp = plans.fingerprint(app)[:12]
    assert "## Approval arguments" in text
    assert f"- customer_id: 1234567890\n- fingerprint: {fp}" in text
    assert "- campaign_name: Test City Launch" in text and "- daily_budget: 300.0" in text
    assert "- over_cap: false" in text
    assert "| Account | 1234567890 |" in text
    up = plans.plan_from_dict({"kind": "image_upload", "files": [str(make_png(1200, 628))]})
    text = plans.render_summary(up, [], cfg)
    assert "- image_count: 1" in text and "Account: 1234567890" in text and "- customer_id: 1234567890" in text
    upd = plans.plan_from_dict(_update(add_headlines=["New one"], remove=["Old two"]))
    text = plans.render_summary(upd, [], cfg)
    assert "- added:\n  - headline: New one\n- removed:\n  - headline: Old two" in text
    assert "campaign_name: shown by check_plan" in text and "campaign_status: shown by check_plan" in text
    assert "takes effect immediately" in text and "Account: 1234567890" in text
    nothing_removed = plans.render_summary(plans.plan_from_dict(_update(add_headlines=["New one"])), [], cfg)
    assert "- removed: none" in nothing_removed


def test_summary_rendering_is_deterministic(cfg, app_plan, make_png):
    plan = plans.plan_from_dict(app_plan(images=[{"file": str(make_png(1200, 628))}]))
    path = plans.save_plan(plan, cfg, plans.validate(plan, cfg))
    saved = (path.parent / "summary.md").read_bytes().decode("utf-8")
    again = plans.load_plan(path)  # what check_plan and the change tools render to compare
    assert plans.render_summary(again, plans.validate(again, cfg), cfg) == saved


def test_deep_links_must_use_an_allowed_prefix(cfg, app_plan):
    allowed = dataclasses.replace(cfg, apps=(AppRef("GOOGLE_APP_STORE", "com.example.app", ("example://open/",)),))
    ok = plans.plan_from_dict(app_plan(final_app_urls=[{"os_type": "ANDROID", "url": "example://open/home"}]))
    assert errors(plans.validate(ok, allowed)) == []
    assert not any("allowlist" in w for w in _warnings(plans.validate(ok, allowed)))
    bad = plans.plan_from_dict(app_plan(final_app_urls=[{"os_type": "ANDROID", "url": "example://open/home"},
                                                        {"os_type": "ANDROID", "url": "other://open/home"}]))
    assert errors(plans.validate(bad, allowed)) == [
        "final_app_urls[1]: must start with an allowed deep-link prefix for this app (example://open/)"]


def test_deep_links_without_an_allowlist_are_a_warning(cfg, app_plan):
    plan = plans.plan_from_dict(app_plan(final_app_urls=[{"os_type": "ANDROID", "url": "example://open/home"}]))
    findings = plans.validate(plan, cfg)
    assert errors(findings) == []
    assert "final_app_urls: no deep-link allowlist configured for this app" in _warnings(findings)
    assert not any("allowlist" in w for w in _warnings(plans.validate(plans.plan_from_dict(app_plan()), cfg)))


@pytest.mark.parametrize("brief", [{}, {"sources": {}}, {"names": {}}])
def test_missing_sources_are_a_warning(cfg, app_plan, brief):
    found = _warnings(plans.validate(plans.plan_from_dict(app_plan(brief=brief)), cfg))
    assert "brief: no sources; mark each headline and description as proven or new" in found


def test_sources_given_means_no_warning(cfg, app_plan):
    plan = plans.plan_from_dict(app_plan(brief={"sources": {"Fast workouts": "proven"}}))
    assert not any(w.startswith("brief") for w in _warnings(plans.validate(plan, cfg)))


def test_no_languages_means_all_languages_warning_not_error(cfg, app_plan):
    # Verified live: a real engagement campaign targets no language (= all languages).
    findings = plans.validate(plans.plan_from_dict(app_plan(languages=[])), cfg)
    assert errors(findings) == []
    assert any(f.field == "languages" and "all languages" in f.message for f in findings if f.level == "warning")


def test_no_locations_is_still_an_error(cfg, app_plan):
    # Kept strict on purpose: a campaign must never run worldwide by accident.
    assert any("locations" in e for e in errors(plans.validate(plans.plan_from_dict(app_plan(locations=[])), cfg)))


def test_engagement_needs_a_deep_link(cfg, app_plan):
    # Verified live: Google rejects engagement ads without final_app_urls ("Too few").
    found = errors(plans.validate(plans.plan_from_dict(app_plan(subtype="APP_CAMPAIGN_FOR_ENGAGEMENT")), cfg))
    assert any(e.startswith("final_app_urls") and "deep link" in e for e in found), found


@pytest.mark.parametrize(
    "urls, expected",
    [
        ([{"os_type": "IOS", "url": "example://x"}], "does not match"),
        ([{"os_type": "WINDOWS", "url": "example://x"}], "ANDROID or IOS"),
        ([{"os_type": "ANDROID", "url": "bad url"}], "without spaces"),
        ([{"os_type": "ANDROID", "url": " "}], "without spaces"),
    ],
)
def test_deep_link_rules(cfg, app_plan, urls, expected):
    found = errors(plans.validate(plans.plan_from_dict(app_plan(final_app_urls=urls)), cfg))
    assert any(expected in e for e in found), found


@pytest.mark.parametrize("bad", ["example://x", [{"url": "example://x"}], [{"os_type": "ANDROID", "url": 5}],
                                 [{"os_type": "ANDROID", "url": "x", "extra": 1}]])
def test_deep_link_shape_is_checked(app_plan, bad):
    with pytest.raises(plans.PlanError, match="final_app_urls"):
        plans.plan_from_dict(app_plan(final_app_urls=bad))


def test_summary_shows_deep_links(cfg, app_plan):
    plan = plans.plan_from_dict(app_plan(subtype="APP_CAMPAIGN_FOR_ENGAGEMENT", final_app_urls=DEEP_LINK))
    assert "example://open/.*" in plans.render_summary(plan, plans.validate(plan, cfg), cfg)


@pytest.mark.parametrize("override", [
    {"conversion_actions": ["customers/1234567890/conversionActions/111\n"]},
    {"locations": ["geoTargetConstants/1000001\n"]},
    {"languages": ["languageConstants/1000\n"]},
    {"images": [{"asset": "customers/1234567890/assets/1\n"}]},
    {"videos": [{"asset": "customers/1234567890/assets/2\n"}]},
    {"videos": [{"youtube_id": "abcdefghijk\n"}]},
])
def test_resource_names_with_a_trailing_newline_are_errors(cfg, app_plan, override):
    assert errors(plans.validate(plans.plan_from_dict(app_plan(**override)), cfg))


def test_ad_with_a_trailing_newline_is_an_error(cfg):
    found = errors(plans.validate(plans.plan_from_dict(_update(ad="customers/1234567890/ads/55\n",
                                                               add_headlines=["x y"])), cfg))
    assert any(e.startswith("ad:") for e in found), found


@pytest.mark.parametrize("name", ["Line\nbreak", "Carriage\rreturn", "Tab\there", "Null\x00byte", "Del\x7fete",
                                  "Right\u202eto left", "Line\u2028separator", "Para\u2029graph", "Next\x85line",
                                  "Isolate\u2066d", "x" * 256])
def test_bad_campaign_names_are_errors(cfg, app_plan, name):
    found = errors(plans.validate(plans.plan_from_dict(app_plan(name=name)), cfg))
    assert any(e.startswith("name:") for e in found), found


@pytest.mark.parametrize("ch", ["\u202e", "\u2028"])
def test_bidi_override_and_line_separator_in_a_name_or_headline_are_errors(cfg, app_plan, ch):
    plan = plans.plan_from_dict(app_plan(name=f"Test{ch}City", headlines=[f"Fast{ch}workouts", "Train in minutes"]))
    found = errors(plans.validate(plan, cfg))
    assert any(e.startswith("name:") and "line breaks" in e for e in found), found
    assert any(e.startswith("headlines[0]:") and "line breaks" in e for e in found), found


@pytest.mark.parametrize("name", ["Test City Launch\u3164\u3164\u3164", "Test\u3164City", "\u115fTest", "Test\u1160",
                                  "Test\uffa0City", "Test\u2800", "Test\u200bCity", "Test\u2060City"])
def test_a_name_padded_with_blank_or_invisible_characters_is_an_error(cfg, app_plan, name):
    found = errors(plans.validate(plans.plan_from_dict(app_plan(name=name)), cfg))
    assert "name: contains invisible characters" in found, found


def test_zwnj_is_fine_in_a_name_and_a_headline(cfg, app_plan):
    plan = plans.plan_from_dict(app_plan(name="Test می\u200cروم", headlines=["می\u200cروم", "Train in minutes"]))
    assert errors(plans.validate(plan, cfg)) == []


# ------------------------------------------------- no fake lines in the summary or the approval

FAKE = "\n**Daily budget: 1.00 USD**"  # a summary line the plan must not be able to add


@pytest.mark.parametrize("override, field", [
    ({"template": "Old campaign" + FAKE}, "template"),
    ({"brief": {"sources": {"Fast workouts": "proven" + FAKE}}}, "brief.sources['Fast workouts']"),
    ({"brief": {"names": {"geoTargetConstants/1000001": "Springfield" + FAKE}}},
     "brief.names['geoTargetConstants/1000001']"),
    ({"brief": {"names": {"geoTargetConstants/1000001": "Springfield\u202e"}}},
     "brief.names['geoTargetConstants/1000001']"),
    ({"final_app_urls": [{"os_type": "ANDROID", "url": "example://open/\u202eDSU 00.1"}]}, "final_app_urls[0]"),
    ({"final_app_urls": [{"os_type": "ANDROID", "url": "example://open/\x1b[2J"}]}, "final_app_urls[0]"),
])
def test_text_that_could_fake_a_summary_line_is_an_error(cfg, app_plan, override, field):
    plan = plans.plan_from_dict(app_plan(**override))
    found = errors(plans.validate(plan, cfg))
    assert f"{field}: contains line breaks, control characters or bidi controls" in found, found


@pytest.mark.parametrize("override, field", [
    ({"template": "x" * 256}, "template"),
    ({"brief": {"sources": {"Fast workouts": "x" * 256}}}, "brief.sources['Fast workouts']"),
    ({"brief": {"names": {"geoTargetConstants/1000001": "x" * 256}}}, "brief.names['geoTargetConstants/1000001']"),
])
def test_free_text_is_capped(cfg, app_plan, override, field):
    found = errors(plans.validate(plans.plan_from_dict(app_plan(**override)), cfg))
    assert f"{field}: is 256 characters; the limit is 255" in found, found


def test_free_text_at_the_cap_is_fine(cfg, app_plan):
    brief = {"sources": {"Fast workouts": "x" * 255}, "names": {"geoTargetConstants/1000001": "x" * 255}}
    plan = plans.plan_from_dict(app_plan(template="x" * 255, brief=brief))
    assert errors(plans.validate(plan, cfg)) == []


def test_an_image_path_that_could_fake_a_line_is_an_error(cfg, app_plan, make_png):
    name = "wide" + FAKE + ".png"
    # Windows can't name a file with a line break, and the rule refuses the text before reading any file.
    img = str(cfg.images_dir / name) if sys.platform == "win32" else str(make_png(1200, 628, name))
    unsafe = "contains line breaks, control characters or bidi controls"
    for plan, field in [
        (app_plan(images=[{"file": img}]), "images[0]"),
        ({"kind": "image_upload", "files": [img]}, "files[0]"),
        (_update(add_images=[{"file": img}]), "add_images[0]"),
    ]:
        findings = plans.validate(plans.plan_from_dict(plan), cfg)
        assert f"{field}: {unsafe}" in errors(findings), errors(findings)
        assert not any("\n" in f.field or "\n" in f.message for f in findings)  # no finding repeats the name


def test_asset_update_base_and_remove_text_must_be_one_line(cfg):
    bad = "Old one" + FAKE
    base = {"headlines": [bad, "Old two"], "descriptions": ["Old description"], "images": [], "videos": []}
    found = errors(plans.validate(plans.plan_from_dict(_update(base=base, remove=[bad])), cfg))
    assert "base.headlines[0]: contains line breaks, control characters or bidi controls" in found, found
    assert "remove[0]: contains line breaks, control characters or bidi controls" in found, found


# ------------------------------------------------------- names derived from the campaign name


def test_the_longest_campaign_name_that_fits_is_fine(cfg, app_plan):
    # The longest derived name here is the new video's asset name, "<name> | YouTube abcdefghijk".
    assert errors(plans.validate(plans.plan_from_dict(app_plan(name="x" * 233)), cfg)) == []
    # Without new media it is the ad group's, "<name> - ad group 1".
    assert errors(plans.validate(plans.plan_from_dict(app_plan(name="x" * 242, videos=[])), cfg)) == []


def test_derived_names_over_255_characters_are_errors(cfg, app_plan, make_png):
    found = errors(plans.validate(plans.plan_from_dict(app_plan(name="x" * 243, videos=[])), cfg))
    assert found == ["name: makes the ad group name 256 characters; the limit is 255"]
    found = errors(plans.validate(plans.plan_from_dict(app_plan(name="x" * 234)), cfg))
    assert found == ["videos[0]: makes the asset name 256 characters; the limit is 255"]
    img = str(make_png(1200, 628, "y" * 40 + ".png"))  # "<name> | <file> | <sha8>": 200 + 3 + 44 + 3 + 8
    found = errors(plans.validate(plans.plan_from_dict(app_plan(name="x" * 200, images=[{"file": img}])), cfg))
    assert found == ["images[0]: makes the asset name 258 characters; the limit is 255"]


def test_long_image_file_names_are_errors_for_every_plan_kind(cfg, make_png):
    img = str(make_png(1200, 628, "y" * 250 + ".png"))
    upload = plans.plan_from_dict({"kind": "image_upload", "files": [img]})
    assert "files[0]: makes the asset name 274 characters; the limit is 255" in errors(plans.validate(upload, cfg))
    update = plans.plan_from_dict(_update(add_images=[{"file": img}]))
    assert "add_images[0]: makes the asset name 280 characters; the limit is 255" in errors(plans.validate(update, cfg))


# ------------------------------------------------------------- images in the approval prompt


def test_approval_shows_new_images_by_full_path_so_same_named_files_differ(cfg, make_png, tmp_path):
    files = []
    for folder in ("one", "two"):
        (tmp_path / folder).mkdir()
        files.append(make_png(1200, 628, f"{folder}/same.png"))
    plan = plans.plan_from_dict(_update(add_images=[{"file": str(f)} for f in files]
                                        + [{"file": "~/same.png"}, {"asset": "customers/1234567890/assets/7"}]))
    added = plans.approval_args(plan, cfg)["added"]
    assert added == [f"image: {files[0]}", f"image: {files[1]}", f"image: {Path.home() / 'same.png'}",
                     "image: customers/1234567890/assets/7"]
    assert len(set(added)) == 4 and all(Path(line.split(": ", 1)[1]).is_absolute() for line in added[:3])


# ------------------------------------------------------------------- ASCII digits only


@pytest.mark.parametrize("override", [
    {"locations": ["geoTargetConstants/\u0661\u0662"]},
    {"locations": ["geoTargetConstants/\u06f1\u06f2"]},
    {"languages": ["languageConstants/\u0661\u0660\u0660\u0660"]},
    {"conversion_actions": ["customers/1234567890/conversionActions/\u0661\u0661\u0661"]},
    {"images": [{"asset": "customers/1234567890/assets/\u0661"}]},
    {"videos": [{"asset": "customers/1234567890/assets/\u0662"}]},
])
def test_resource_names_need_ascii_digits(cfg, app_plan, override):
    assert errors(plans.validate(plans.plan_from_dict(app_plan(**override)), cfg))


def test_ad_names_need_ascii_digits(cfg):
    plan = plans.plan_from_dict(_update(ad="customers/1234567890/ads/\u0665\u0665", add_headlines=["x y"]))
    assert any(e.startswith("ad:") for e in errors(plans.validate(plan, cfg)))


# ------------------------------------------------------------------- money and currency


@pytest.mark.parametrize("field", ["daily_budget", "target_cpa", "confirm_budget_over_cap"])
def test_an_amount_too_large_for_a_float_is_a_plan_error(app_plan, field):
    with pytest.raises(plans.PlanError, match=f"^{field} is too large$"):
        plans.plan_from_dict(app_plan(**{field: 10**400}))


@pytest.mark.parametrize("over, expected", [
    ({"target_cpa": 1e13}, "target_cpa: is too large"),
    ({"target_cpa": float("inf")}, "target_cpa: must be a finite number"),
    ({"daily_budget": 1e13}, "daily_budget: 10,000,000,000,000.00 USD is more than 3x the cap"),
    ({"daily_budget": float("nan")}, "daily_budget: must be a positive number"),
    ({"daily_budget": 600.0, "confirm_budget_over_cap": float("inf")}, "daily_budget: 600.00 USD is above the cap"),
])
def test_huge_or_non_finite_app_amounts_are_clean_errors(cfg, app_plan, over, expected):
    plan = plans.plan_from_dict(app_plan(**over))
    findings = plans.validate(plan, cfg)
    assert any(e.startswith(expected) for e in errors(findings)), errors(findings)
    assert "## Problems to fix" in plans.render_summary(plan, findings, cfg)


def test_app_amounts_need_not_be_whole_cents(cfg, app_plan):
    """App plans are copied from micros, so 12.345 is a real target."""
    assert errors(plans.validate(plans.plan_from_dict(app_plan(target_cpa=12.345, daily_budget=300.125)), cfg)) == []


@pytest.mark.parametrize("currency", ["usd", "Usd", "US", "USDX", " USD", "US1"])
def test_the_currency_is_three_capital_letters(cfg, app_plan, currency):
    found = errors(plans.validate(plans.plan_from_dict(app_plan(currency=currency)), cfg))
    assert found == ["currency: must be a 3-letter currency code in capitals, like USD"], found


# A valid App plan's summary, byte for byte, before the money and currency rules changed.
APP_SUMMARIES = [
    ({}, "131f45bb73795f129a9214cdfc85f3129c32a8403acd818dd13285887af32d2f"),
    ({"daily_budget": 300, "target_cpa": 12}, "5df1c44ee98e9356ad86f3fce65e1ea35509875491e45e0635b983bc7c26b83d"),
    ({"daily_budget": 600.0, "confirm_budget_over_cap": 600.0},
     "e1723c83402c2ef119e882b06a45bbf532c228d2456c3bf1a0a2830abfe00e4f"),
]


@pytest.mark.parametrize("over, digest", APP_SUMMARIES)
def test_valid_app_summaries_are_unchanged(cfg, app_plan, over, digest):
    import hashlib

    plan = plans.plan_from_dict(app_plan(**over))
    text = plans.render_summary(plan, plans.validate(plan, cfg), cfg)
    assert hashlib.sha256(text.encode()).hexdigest() == digest
