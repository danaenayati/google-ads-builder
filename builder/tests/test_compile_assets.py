import hashlib

from gads_builder import plans
from gads_builder.compile_assets import compile_asset_update, compile_image_upload

CID = "1234567890"


def test_image_upload(ads_client, make_png):
    a, b = make_png(1200, 628, "a.png"), make_png(1200, 1200, "b.png")
    plan = plans.plan_from_dict({"kind": "image_upload", "files": [str(a), str(b)]})
    ops, labels = compile_image_upload(plan, ads_client, CID)
    assert labels == ["image[0] a.png", "image[1] b.png"]
    assert ops[1].asset_operation.create.image_asset.data == b.read_bytes()
    assert ops[0].asset_operation.create.name == f"upload | a.png | {hashlib.sha256(a.read_bytes()).hexdigest()[:8]}"


def test_same_file_name_in_two_folders_is_distinguishable(ads_client, tmp_path, make_png):
    one = make_png(1200, 628, "x.png", color=(1, 2, 3))
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    two = other_dir / "x.png"
    from PIL import Image
    Image.new("RGB", (1200, 628), (9, 9, 9)).save(two)
    plan = plans.plan_from_dict({"kind": "image_upload", "files": [str(one), str(two)]})
    ops, labels = compile_image_upload(plan, ads_client, CID)
    assert labels == ["image[0] x.png", "image[1] x.png"]
    assert ops[0].asset_operation.create.name != ops[1].asset_operation.create.name


def test_asset_names_are_deterministic(ads_client, make_png):
    a = make_png(1200, 628, "a.png")
    plan = plans.plan_from_dict({"kind": "image_upload", "files": [str(a)]})
    first, _ = compile_image_upload(plan, ads_client, CID)
    second, _ = compile_image_upload(plan, ads_client, CID)
    assert first[0].asset_operation.create.name == second[0].asset_operation.create.name


def _update(**over):
    data = {
        "kind": "asset_update",
        "ad": "customers/1234567890/ads/55",
        "ad_type": "APP_AD",
        "base": {
            "headlines": ["Old one", "Old two"],
            "descriptions": ["Old description"],
            "images": ["customers/1234567890/assets/1"],
            "videos": ["customers/1234567890/assets/2"],
        },
    }
    data.update(over)
    return plans.plan_from_dict(data)


def test_asset_update_replaces_lists_with_merged_result(ads_client, make_png):
    img = make_png(1200, 1500, "p.png")
    plan = _update(add_headlines=["New one"], remove=["Old two"], add_images=[{"file": str(img)}])
    ops, labels = compile_asset_update(plan, ads_client, CID)
    assert labels == ["image[0] p.png", "ad update"]
    new_image = ops[0].asset_operation.create.resource_name
    assert ops[0].asset_operation.create.name == f"update ad 55 | p.png | {hashlib.sha256(img.read_bytes()).hexdigest()[:8]}"
    ad = ops[1].ad_operation.update
    assert ad.resource_name == "customers/1234567890/ads/55"
    assert [h.text for h in ad.app_ad.headlines] == ["Old one", "New one"]
    assert [i.asset for i in ad.app_ad.images] == ["customers/1234567890/assets/1", new_image]
    assert [v.asset for v in ad.app_ad.youtube_videos] == ["customers/1234567890/assets/2"]
    assert sorted(ops[1].ad_operation.update_mask.paths) == [
        "app_ad.descriptions",
        "app_ad.headlines",
        "app_ad.images",
        "app_ad.youtube_videos",
    ]


def test_engagement_ad_update_paths(ads_client):
    plan = _update(ad_type="APP_ENGAGEMENT_AD", add_descriptions=["Another description"])
    ops, _ = compile_asset_update(plan, ads_client, CID)
    assert "app_engagement_ad.videos" in ops[-1].ad_operation.update_mask.paths
    assert [d.text for d in ops[-1].ad_operation.update.app_engagement_ad.descriptions][-1] == "Another description"


def test_same_file_on_two_ads_gets_two_names(ads_client, make_png):
    img = make_png(1200, 628, "same.png")
    one, _ = compile_asset_update(_update(add_images=[{"file": str(img)}], add_videos=[{"youtube_id": "abcdefghijk"}]), ads_client, CID)
    two, _ = compile_asset_update(
        _update(ad="customers/1234567890/ads/56", add_images=[{"file": str(img)}], add_videos=[{"youtube_id": "abcdefghijk"}]),
        ads_client, CID,
    )
    assert one[0].asset_operation.create.name.startswith("update ad 55 | same.png | ")
    assert two[0].asset_operation.create.name.startswith("update ad 56 | same.png | ")
    assert one[1].asset_operation.create.name == "update ad 55 | YouTube abcdefghijk"
    assert two[1].asset_operation.create.name == "update ad 56 | YouTube abcdefghijk"


def test_app_and_search_compilers_share_one_text_asset_helper(ads_client):
    from pathlib import Path

    from gads_builder import compile_app, compile_assets, compile_search

    assert compile_search.text_assets is compile_app.text_assets
    assert [a.text for a in compile_app.text_assets(ads_client, ["One", "Two"])] == ["One", "Two"]
    assert "compile_search" not in Path(compile_assets.__file__).read_text(encoding="utf-8")
