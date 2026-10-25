"""ImageUploadPlan / AssetUpdatePlan -> MutateOperations."""

from __future__ import annotations

import itertools

from google.protobuf import field_mask_pb2

from .compile_app import asset_ops, fill_app_ad_info, text_assets
from .plans import AssetUpdatePlan, ImageRef, ImageUploadPlan, asset_prefix, merged_assets


def compile_image_upload(plan: ImageUploadPlan, client, customer_id: str) -> tuple[list, list[str]]:
    ids = itertools.count(-1, -1)
    refs = [ImageRef(file=f) for f in plan.files]
    ops, labels, _, _ = asset_ops(client, customer_id, ids, asset_prefix(plan), refs, [])
    return ops, labels


def compile_asset_update(plan: AssetUpdatePlan, client, customer_id: str) -> tuple[list, list[str]]:
    ids = itertools.count(-1, -1)
    ops, labels, new_images, new_videos = asset_ops(  # names differ per ad, even for the same file
        client, customer_id, ids, asset_prefix(plan), plan.add_images, plan.add_videos
    )
    merged = merged_assets(plan)
    op = client.get_type("MutateOperation")
    ad = op.ad_operation.update
    ad.resource_name = plan.ad
    if plan.ad_type == "RESPONSIVE_SEARCH_AD":
        ad.responsive_search_ad.headlines.extend(text_assets(client, merged["headlines"]))
        ad.responsive_search_ad.descriptions.extend(text_assets(client, merged["descriptions"]))
        paths = ["responsive_search_ad.headlines", "responsive_search_ad.descriptions"]
    else:
        field = "app_ad" if plan.ad_type == "APP_AD" else "app_engagement_ad"
        video_field = "youtube_videos" if plan.ad_type == "APP_AD" else "videos"
        fill_app_ad_info(
            client,
            getattr(ad, field),
            plan.ad_type,
            merged["headlines"],
            merged["descriptions"],
            merged["images"] + new_images,
            merged["videos"] + new_videos,
        )
        paths = [f"{field}.headlines", f"{field}.descriptions", f"{field}.images", f"{field}.{video_field}"]
    mask = field_mask_pb2.FieldMask(paths=paths)
    client.copy_from(op.ad_operation.update_mask, mask)
    ops.append(op)
    labels.append("ad update")
    return ops, labels
