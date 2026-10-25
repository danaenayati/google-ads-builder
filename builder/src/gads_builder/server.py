"""google-ads-builder MCP server.

Read-only tools run freely. The three change tools are on Claude Code's "ask" list, refuse unless
check_plan passed, in this server process, on exactly the request bytes they are about to send, and
then open the builder's own approval window on the person's computer (macOS or Windows), whatever
Claude Code's mode. Each plan can be applied once."""

from __future__ import annotations

import hashlib
import math
import re
import threading
from collections import Counter
from pathlib import Path
from typing import Any, NamedTuple

from fastmcp import FastMCP

from . import (allowlists, checks, confirm, gads, images, insights, insights_search, insights_video, landing, plans,
               plans_demand_gen, plans_pmax, review, templates, templates_pmax)
from .compile_app import compile_app_campaign, to_micros
from .compile_assets import compile_asset_update, compile_image_upload
from .compile_demand_gen import compile_demand_gen_campaign
from .compile_pmax import compile_pmax_campaign
from .compile_search import compile_search_campaign
from .config import load_config
from .plans_demand_gen import DemandGenCampaignPlan
from .plans_pmax import PmaxCampaignPlan
from .plans_search import SearchCampaignPlan, bidding_label

PLAYBOOK = Path(__file__).with_name("playbook.md").read_text(encoding="utf-8")  # the single source of truth


def _split_playbook(text: str) -> tuple[str, dict[str, str]]:
    """(the text before the first `## ` heading, {heading: its section, from the heading line on})."""
    intro, *parts = re.split(r"(?m)^(?=## )", text)
    return intro, {part.split("\n", 1)[0][3:].strip(): part for part in parts}


PLAYBOOK_INTRO, PLAYBOOK_SECTIONS = _split_playbook(PLAYBOOK)
# Claude Code cuts a server's instructions at about 4,000 characters, and the playbook is far longer. So
# the instructions are short: the playbook's opening, a pointer to the `playbook` tool and its Safety
# section, all taken from playbook.md.
INSTRUCTIONS = "\n\n".join([
    "# google-ads-builder",
    PLAYBOOK_INTRO.split("\n", 1)[1].strip(),
    "**Before any campaign work, call the `playbook` tool and follow it.** It holds the whole workflow for App, "
    "Search, Demand Gen and Performance Max campaigns and the text, image, bidding and money rules; these "
    f"instructions are only its safety rules. Its sections: {', '.join(PLAYBOOK_SECTIONS)}.",
    PLAYBOOK_SECTIONS["Safety"].strip(),
])
# Strict: a tool argument of the wrong JSON type (the string "true" or the number 0 for a bool, "300" for a
# number) is refused before the tool runs, never coerced into what the person did not see.
mcp = FastMCP("google-ads-builder", instructions=INSTRUCTIONS, strict_input_validation=True)

_APPLY_LOCK = threading.Lock()  # one real change at a time in this process
_WINDOW_LOCK = threading.Lock()  # one approval window at a time: a second change tool is refused, never queued
LIVE_NOTE = "The campaign is ENABLED (running): this change takes effect immediately."
NOT_APPROVED = "Not approved in the approval window (Cancel, closed or timed out). Nothing was changed."
DECLINED_TOO_OFTEN = checks.DECLINED_FOR_GOOD
WINDOW_TITLE = "Google Ads: approve this change?"
WINDOW_OPEN = "Another approval window is open; answer it first. Nothing was changed."


class PlanPathError(ValueError):
    pass


def _ctx():
    cfg = load_config()
    return cfg, gads.client_from(str(cfg.credentials))


def _same_folder(a: Path, b: Path) -> bool:
    """The same folder on disk, not only the same name. (On Windows, path names compare without regard
    to letter case, as the file system does; this also checks it is the very same folder.)"""
    try:
        return a.samefile(b)
    except OSError:
        return False


def _load(plan_path: str, cfg):
    """Only a plan.json that draft_plan saved in its own folder directly inside cfg.plans_dir,
    after resolving symlinks and '..'. Returns (plan, its resolved folder)."""
    root = Path(cfg.plans_dir).expanduser().resolve()
    path = Path(plan_path).expanduser()
    if path.is_dir():
        path = path / "plan.json"
    path = path.resolve()
    if not path.is_relative_to(root) or path == root:
        raise PlanPathError(f"plans must be inside {root}")
    if path.name != "plan.json" or path.parent.parent != root or not _same_folder(path.parent.parent, root):
        raise PlanPathError(f"use a plan folder that draft_plan made directly inside {root}")
    return plans.load_plan(path), path.parent


PLAN_CHANGED = ("The plan or the builder config changed after the plan was drafted (or it wasn't made by "
                "draft_plan). Run draft_plan again and show the person the new summary.")


def _summary_is_current(folder: Path, plan, cfg) -> bool:
    """True only if summary.md is exactly what draft_plan writes for this plan with the current config,
    so what the person read is what gets checked and sent."""
    try:
        text = (folder / "summary.md").read_bytes().decode("utf-8")  # bytes: no newline translation
    except (OSError, UnicodeDecodeError):
        return False
    return text == plans.render_summary(plan, plans.validate(plan, cfg), cfg)


def _compile(plan, client, customer_id, facts=None):
    """facts: what the preflight read live (a Demand Gen or Performance Max plan's account goals)."""
    if isinstance(plan, plans.AppCampaignPlan):
        return compile_app_campaign(plan, client, customer_id)
    if isinstance(plan, SearchCampaignPlan):
        return compile_search_campaign(plan, client, customer_id)
    if isinstance(plan, DemandGenCampaignPlan):
        return compile_demand_gen_campaign(plan, client, customer_id, (facts or {}).get("account_goals", ()))
    if isinstance(plan, PmaxCampaignPlan):
        return compile_pmax_campaign(plan, client, customer_id, (facts or {}).get("account_goals", ()))
    if isinstance(plan, plans.ImageUploadPlan):
        return compile_image_upload(plan, client, customer_id)
    return compile_asset_update(plan, client, customer_id)


_AD_ID = re.compile(r"customers/[0-9]{10}/ads/([0-9]{1,19})")  # not \d: int() also reads other digits


def _ad_facts(client, customer_id: str, ad: str) -> dict:
    """The live campaign and type of the ad an asset update changes."""
    m = _AD_ID.fullmatch(ad)
    if not m:
        raise LookupError(f"{ad!r} is not an ad of the form customers/<id>/ads/<id>")
    ad_id = int(m.group(1))  # digits only: nothing else reaches the query
    rows = gads.search(
        client,
        customer_id,
        "SELECT campaign.name, campaign.status, ad_group_ad.ad.type "
        f"FROM ad_group_ad WHERE ad_group_ad.ad.id = {ad_id}",
    )
    if len(rows) != 1:
        raise LookupError(f"expected one ad {ad} in the account, found {len(rows)}")
    row = rows[0]
    return {
        "campaign_name": row.campaign.name,
        "campaign_status": row.campaign.status.name,
        "ad_type": row.ad_group_ad.ad.type_.name,
    }


def _targets(plan) -> list[str]:
    """The conversion actions, locations, languages and audiences a plan names."""
    if isinstance(plan, DemandGenCampaignPlan):
        return [x for group in plan.ad_groups for x in (*group.locations, *group.languages, *group.audiences)]
    if isinstance(plan, PmaxCampaignPlan):
        return [*plan.locations, *plan.languages, *(g.audience_signal for g in plan.asset_groups if g.audience_signal)]
    return [*getattr(plan, "conversion_actions", []), *plan.locations, *plan.languages]


def _name_problems(plan, cfg, client) -> list[str]:
    """The plan's conversion actions, locations, languages and audiences exist, and every label in
    brief.names is Google's own name for that resource, so the summary can't mislabel what the person
    approves."""
    labels = plan.brief.get("names", {})
    labels = labels if isinstance(labels, dict) else {}  # validate warns, and the summary ignores it
    used = list(dict.fromkeys(_targets(plan)))
    google = templates.google_names(client, cfg.customer_id, [*used, *labels])
    problems = [f"{r}: not found in Google Ads" for r in used if r not in google]
    for resource, label in labels.items():
        if resource not in google:
            problems.append(f"brief.names: {resource} was not found in Google Ads")
        elif label not in google[resource]:
            problems.append(f"brief.names: {label!r} is not Google's name for {resource} ({google[resource][0]!r})")
    return problems


def _demand_gen_problems(plan, cfg, client) -> tuple[list[str], dict]:
    """A Demand Gen plan's live checks: every goal is one of the account's conversion goals, and the
    budget is at least Google's Demand Gen minimum. Returns (problems, facts for compile and the window)."""
    goals = gads.customer_goals(client, cfg.customer_id)
    problems = [f"conversion_goals[{i}]: {plans_demand_gen.goal_label(g)} is not a conversion goal of this account"
                for i, g in enumerate(plan.conversion_goals) if (g.category, g.origin) not in goals]
    minimum = gads.demand_gen_minimum_micros(client, cfg.customer_id, plan.currency)
    if to_micros(plan.daily_budget) < minimum:
        problems.append(f"daily_budget: Google's smallest Demand Gen daily budget is {minimum / 1_000_000:,.2f} "
                        f"{plan.currency}")
    return problems, {"account_goals": goals, "minimum_budget_micros": minimum}


def _pmax_problems(plan, cfg, client) -> tuple[list[str], dict]:
    """A Performance Max plan's live checks: every goal is one of the account's conversion goals, and every
    audience signal is an ENABLED account-wide audience (one made for an asset group can't be used by another;
    _name_problems reports one that doesn't exist). Returns (problems, facts for compile and the window)."""
    goals = gads.customer_goals(client, cfg.customer_id)
    problems = [f"conversion_goals[{i}]: {plans_demand_gen.goal_label(g)} is not a conversion goal of this account"
                for i, g in enumerate(plan.conversion_goals) if (g.category, g.origin) not in goals]
    scopes = templates_pmax.audience_scopes(client, cfg.customer_id,
                                            [g.audience_signal for g in plan.asset_groups if g.audience_signal])
    for i, group in enumerate(plan.asset_groups):
        found = scopes.get(group.audience_signal)
        if found is not None and found != ("CUSTOMER", "ENABLED"):
            problems.append(f"asset_groups[{i}].audience_signal: {group.audience_signal} is not an enabled "
                            "account-wide audience (an audience made for one asset group can't be reused)")
    return problems, {"account_goals": goals}


def _preflight(plan, cfg, client) -> tuple[list[str], dict]:
    """Local rules plus live facts. Returns (blocking problems, live facts: an asset update's ad, a Demand
    Gen plan's account goals and minimum budget, or a Performance Max plan's account goals)."""
    problems = [f"{f.field}: {f.message}" for f in plans.validate(plan, cfg) if f.level == "error"]
    if problems:
        return problems, {}
    facts: dict = {}
    try:
        if isinstance(plan, plans.ImageUploadPlan):
            # one cheap live read, so a VPN or transport failure stops here, before any claim or real change
            gads.account_currency(client, cfg.customer_id)
        if isinstance(plan, (plans.AppCampaignPlan, SearchCampaignPlan, DemandGenCampaignPlan, PmaxCampaignPlan)):
            currency = gads.account_currency(client, cfg.customer_id)
            if currency != plan.currency:
                problems.append(f"currency: the account uses {currency}, the plan says {plan.currency}")
            problems += _name_problems(plan, cfg, client)
        if isinstance(plan, DemandGenCampaignPlan):
            goal_problems, facts = _demand_gen_problems(plan, cfg, client)
            problems += goal_problems
        if isinstance(plan, PmaxCampaignPlan):
            goal_problems, facts = _pmax_problems(plan, cfg, client)
            problems += goal_problems
        if isinstance(plan, plans.AssetUpdatePlan):
            ad_problems, facts = _ad_problems(plan, cfg, client)
            problems += ad_problems
    except gads.GoogleAdsError as e:
        problems += e.messages
    except LookupError as e:
        problems.append(f"ad: {e}")
    return problems, facts


def _ad_problems(plan, cfg, client) -> tuple[list[str], dict]:
    """The live part of an asset update's preflight: the ad's type, its current assets (the plan's
    base) and, for a responsive search ad, no pins. Returns (problems, live facts)."""
    facts = _ad_facts(client, cfg.customer_id, plan.ad)
    if facts["ad_type"] != plan.ad_type:
        return [f"ad_type: the ad is {facts['ad_type']} in the account, the plan says {plan.ad_type}"], facts
    assets, pinned = templates.current_ad(client, cfg.customer_id, plan.ad)  # one read: no gap between the two
    if assets != plan.base:
        return ["ad: it changed since this plan was drafted; draft again from describe_campaign"], facts
    if plan.ad_type == "RESPONSIVE_SEARCH_AD" and pinned:
        return ["ad: some headlines or descriptions are pinned, and this update would unpin them; "
                "change pinned ads in Google Ads"], facts
    return [], facts


CHANGED_WHILE_OPEN = "The ad or its campaign changed while the approval window was open, so nothing was changed."
GOALS_CHANGED = ("The account's conversion goals changed while the approval window was open, so nothing was "
                 "changed. Run check_plan again.")
GOALS_UNREAD = "The account's conversion goals could not be read again after Approve, so nothing was changed."


def _still_as_shown(plan, cfg, client, facts: dict) -> list[str]:
    """After Approve, before the change: an asset update's ad and campaign, or the account goals a Demand Gen
    or Performance Max campaign turns off, are still exactly what the window showed (the window can stay open
    for a minute). Any doubt is a refusal."""
    if isinstance(plan, (DemandGenCampaignPlan, PmaxCampaignPlan)):
        try:
            same = gads.customer_goals(client, cfg.customer_id) == facts.get("account_goals")
        except gads.GoogleAdsError as e:
            return [GOALS_UNREAD, *e.messages]
        return [] if same else [GOALS_CHANGED]
    if not isinstance(plan, plans.AssetUpdatePlan):
        return []
    try:
        problems, now = _ad_problems(plan, cfg, client)
        problems = problems or _mismatch(facts, now, "the account")
    except gads.GoogleAdsError as e:
        problems = e.messages
    except LookupError as e:
        problems = [f"ad: {e}"]
    return [CHANGED_WHILE_OPEN, *problems] if problems else []


# plan kind -> {MutateOperation field: the only action allowed on it}
_ALLOWED_OPS = {
    "app_campaign": {
        "asset_operation": "create",
        "campaign_budget_operation": "create",
        "campaign_operation": "create",
        "campaign_criterion_operation": "create",
        "ad_group_operation": "create",
        "ad_group_ad_operation": "create",
    },
    "search_campaign": {
        "asset_operation": "create",
        "campaign_budget_operation": "create",
        "campaign_operation": "create",
        "campaign_criterion_operation": "create",
        "ad_group_operation": "create",
        "ad_group_criterion_operation": "create",
        "ad_group_ad_operation": "create",
        "campaign_asset_operation": "create",
    },
    "demand_gen_campaign": {
        "asset_operation": "create",
        "campaign_budget_operation": "create",
        "campaign_operation": "create",
        "campaign_conversion_goal_operation": "update",
        "ad_group_operation": "create",
        "ad_group_criterion_operation": "create",
        "ad_group_ad_operation": "create",
    },
    "pmax_campaign": {
        "asset_operation": "create",
        "campaign_budget_operation": "create",
        "campaign_operation": "create",
        "campaign_asset_operation": "create",
        "campaign_criterion_operation": "create",
        "campaign_conversion_goal_operation": "update",
        "asset_group_operation": "create",
        "asset_group_asset_operation": "create",
        "asset_group_signal_operation": "create",
    },
    "image_upload": {"asset_operation": "create"},
    "asset_update": {"asset_operation": "create", "ad_operation": "update"},
}


# MutateOperation field -> the collection a new resource's temporary ID is in
_TEMP_COLLECTION = {"campaign_budget_operation": "campaignBudgets", "campaign_operation": "campaigns",
                    "ad_group_operation": "adGroups", "asset_operation": "assets", "asset_group_operation": "assetGroups"}
TEMP_IDS = "internal safety check: every new budget, campaign, ad group and asset needs a temporary ID of this account"
PORTFOLIO = "internal safety check: the campaign would use a portfolio bidding strategy"
SHARED_BUDGET = "internal safety check: the new budget must be explicitly not shared"
NEGATIVE_TARGETING = "internal safety check: only keywords may be negative; the campaign would exclude a location"
PARTNERS = "internal safety check: the campaign's search partners setting differs from the plan"
EU_POLITICAL = "internal safety check: the campaign would not declare that it has no EU political ads"
BUDGET_DELIVERY = "internal safety check: the budget's delivery method would not be standard"
STATUSES = ("internal safety check: an ad group, asset group, ad or keyword status is not the builder's (enabled, "
            "inside the paused campaign)")
AD_GROUP_TYPE = "internal safety check: a Search ad group would not be a standard Search ad group"


def _temp_id_ok(which: str, name: str, customer_id: str) -> bool:
    """A new budget, campaign, ad group, asset group or asset is named customers/<this account>/<its collection>/-N,
    so no existing resource can stand in for it; any other create names nothing."""
    collection = _TEMP_COLLECTION.get(which)
    if collection is None:
        return name == ""
    return re.fullmatch(rf"customers/{re.escape(customer_id)}/{collection}/-[1-9][0-9]*", name) is not None


def _guard(plan, ops, client, cfg, facts=None) -> list[str]:
    """A last look at the exact operations: only expected kinds of change, new parts with temporary
    IDs, campaigns PAUSED with their own bidding, the budget as planned, within 3x the cap and
    explicitly not shared, the values every campaign compiler hard-codes (the EU political ads declaration,
    standard budget delivery, ad groups, asset groups, ads and keywords enabled, a Search ad group's type), no
    excluded locations (only keywords may be negative), updates only on the planned ad (or, for Demand Gen and
    Performance Max, the new campaign's conversion goals). Then each plan kind's own checks, which compare the values
    sent with the plan (names, targeting, texts, final URLs, every image slot, the new images' bytes and names, an
    update's lists and mask), and last, every field set must be one its compiler sends, with no unknown field
    numbers (allowlists.py)."""
    problems = _guard_operations(plan, ops, client, cfg) or _guard_kind(plan, ops, client, cfg, facts or {})
    return problems or allowlists.problems(plan, ops)


def _guard_operations(plan, ops, client, cfg) -> list[str]:
    """The checks every plan kind shares, one operation at a time."""
    allowed = _ALLOWED_OPS[plan.kind]
    paused = client.enums.CampaignStatusEnum.PAUSED
    for i, op in enumerate(ops):
        pb = type(op).pb(op)
        which = pb.WhichOneof("operation")
        action = allowlists.action(getattr(pb, which) if which else None)  # None for a kind without the oneof
        if which not in allowed or allowed[which] != action:
            return [f"internal safety check: operation {i} ({which} {action}) is not allowed in a {plan.kind} plan"]
        if action == "create" and not _temp_id_ok(which, getattr(op, which).create.resource_name, cfg.customer_id):
            return [TEMP_IDS]
        if which == "campaign_operation" and op.campaign_operation.create.status != paused:
            return ["internal safety check: a campaign would not be paused"]
        if which == "campaign_operation" and op.campaign_operation.create.bidding_strategy:
            return [PORTFOLIO]
        if which == "campaign_operation" and op.campaign_operation.create.ai_max_setting.enable_ai_max:
            return ["internal safety check: the campaign would turn on AI Max"]
        if which == "campaign_operation" and op.campaign_operation.create.keyword_match_type:
            return ["internal safety check: the campaign would set one keyword match type for every keyword"]
        if which == "campaign_budget_operation":
            amount = op.campaign_budget_operation.create.amount_micros
            if amount != to_micros(plan.daily_budget) or amount > to_micros(3 * cfg.daily_budget_cap):
                return ["internal safety check: the budget differs from the plan or is above 3x the cap"]
            budget = getattr(pb, which).create  # raw protobuf: proto-plus reads an unset optional bool as False
            if not budget.HasField("explicitly_shared") or budget.explicitly_shared is not False:
                return [SHARED_BUDGET]
        if which == "campaign_criterion_operation":
            criterion = getattr(pb, which).create
            if criterion.negative and criterion.WhichOneof("criterion") != "keyword":
                return [NEGATIVE_TARGETING]
        if which == "ad_operation" and op.ad_operation.update.resource_name != plan.ad:
            return ["internal safety check: the update targets a different ad"]
        if action == "create":
            problems = _fixed_values(plan, which, getattr(op, which).create, client.enums)
            if problems:
                return problems
    return []


def _fixed_values(plan, which: str, create, E) -> list[str]:
    """The values the campaign compilers hard-code, the same in every kind that creates the part: the EU political ads
    declaration, the budget's standard delivery, ad groups, asset groups, ads and positive keywords enabled (inside
    the paused campaign), and a Search ad group's type. These enums have no presence, so unset reads as UNSPECIFIED and is
    refused too. Where a compiler leaves one of these fields unset (an App or Demand Gen ad group's type, any other
    criterion's status), the per-kind allowlist refuses it (allowlists.py)."""
    no_eu_ads = E.EuPoliticalAdvertisingStatusEnum.DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
    if which == "campaign_operation" and create.contains_eu_political_advertising != no_eu_ads:
        return [EU_POLITICAL]
    if which == "campaign_budget_operation" and create.delivery_method != E.BudgetDeliveryMethodEnum.STANDARD:
        return [BUDGET_DELIVERY]
    if which == "ad_group_operation":
        if create.status != E.AdGroupStatusEnum.ENABLED:
            return [STATUSES]
        if plan.kind == "search_campaign" and create.type_ != E.AdGroupTypeEnum.SEARCH_STANDARD:
            return [AD_GROUP_TYPE]
    if which == "ad_group_ad_operation" and create.status != E.AdGroupAdStatusEnum.ENABLED:
        return [STATUSES]
    if which == "asset_group_operation" and create.status != E.AssetGroupStatusEnum.ENABLED:
        return [STATUSES]
    if which == "ad_group_criterion_operation":
        criterion = _pb(create)
        if (criterion.WhichOneof("criterion") == "keyword" and not criterion.negative
                and criterion.status != E.AdGroupCriterionStatusEnum.ENABLED):
            return [STATUSES]
    return []


def _guard_kind(plan, ops, client, cfg, facts: dict) -> list[str]:
    """Each plan kind's own checks of the whole request."""
    if plan.kind == "app_campaign":
        return (_guard_app_references(ops) or _guard_bidding(plan, ops, client.enums)
                or _guard_app_values(plan, ops, client.enums))
    if plan.kind == "search_campaign":
        return (_guard_search_references(plan, ops, client) or _guard_search_counts(plan, ops)
                or _guard_bidding(plan, ops, client.enums) or _guard_search_values(plan, ops, cfg, client.enums))
    if plan.kind == "demand_gen_campaign":
        return _guard_demand_gen(plan, ops, client, cfg, facts)
    if plan.kind == "pmax_campaign":
        return _guard_pmax(plan, ops, client, cfg, facts)
    if plan.kind == "asset_update":
        return _guard_ad_update(plan, ops)
    if plan.kind == "image_upload":
        return _guard_upload(plan, ops)
    return []


NEW_IMAGES = ("internal safety check: the new images are not exactly the plan's files, in order, with their bytes "
              "and names")


def _pb(message):
    """The raw protobuf of a proto-plus message."""
    return type(message).pb(message)


def _new_image_names(assets, prefix: str, files: list[str]) -> list[str] | None:
    """The resource names of the request's new image assets (raw protobuf), in order, if they are exactly `files` in
    order: each with the file's bytes, read again here, and the name the compiler gives that file. None if not."""
    images = [a for a in assets if a.WhichOneof("asset_data") == "image_asset"]
    if len(images) != len(files):
        return None
    for asset, file in zip(images, files):
        if not isinstance(file, str):
            return None
        path = Path(file).expanduser()
        try:
            data = path.read_bytes()
        except OSError:
            return None
        if (asset.image_asset.data != data
                or asset.name != plans.image_asset_name(prefix, path, hashlib.sha256(data).hexdigest())):
            return None
    return [a.resource_name for a in images]


def _guard_upload(plan, ops) -> list[str]:
    """An upload creates exactly the plan's files, in order, and nothing else."""
    assets = [_pb(a) for a in _creates(ops, "asset_operation")]
    if len(assets) != len(plan.files) or _new_image_names(assets, plans.asset_prefix(plan), plan.files) is None:
        return [NEW_IMAGES]
    return []


BIDDING = "internal safety check: the bidding differs from the plan"


def _planned_bidding(plan, E) -> tuple[str, dict]:
    """The campaign_bidding_strategy member the plan's bidding compiles to, and that strategy's fields:
    {name: value}, None for a field that must be unset."""
    target = to_micros(plan.target_cpa) if plan.target_cpa else None
    if isinstance(plan, plans.AppCampaignPlan):
        if plans.APP_GOALS.get(plan.goal, ("",))[0] == "TARGET_CPA":
            return "target_cpa", {"target_cpa_micros": target}
        return "maximize_conversions", {"target_cpa_micros": 0}  # a plain int64: 0 is unset
    max_cpc = getattr(plan, "max_cpc", None)  # a Performance Max plan has none
    ceiling = to_micros(max_cpc) if max_cpc else None
    if isinstance(plan, DemandGenCampaignPlan):  # Google accepts neither a target nor a ceiling on these two
        if plan.bidding == "MAXIMIZE_CONVERSIONS":
            return "maximize_conversions", {"target_cpa_micros": 0}
        if plan.bidding == "MAXIMIZE_CLICKS":
            return "target_spend", {"cpc_bid_ceiling_micros": None}
    if plan.bidding == "MAXIMIZE_CONVERSIONS":
        return "maximize_conversions", {"target_cpa_micros": target or 0}
    if plan.bidding == "MAXIMIZE_CONVERSION_VALUE":
        return "maximize_conversion_value", {"target_roas": plan.target_roas or 0.0}  # a plain double: 0 is unset
    if plan.bidding == "TARGET_CPA":
        return "target_cpa", {"target_cpa_micros": target}
    if plan.bidding == "MAXIMIZE_CLICKS":
        return "target_spend", {"cpc_bid_ceiling_micros": ceiling}
    share = getattr(plan, "impression_share", None)
    if plan.bidding == "TARGET_IMPRESSION_SHARE" and share is not None:
        return "target_impression_share", {
            "location": getattr(E.TargetImpressionShareLocationEnum, share.location, None),
            "location_fraction_micros": int(round(share.percent * 10_000)), "cpc_bid_ceiling_micros": ceiling}
    if plan.bidding == "MANUAL_CPC":
        return "manual_cpc", {}
    return "", {}


def _bidding_field_matches(strategy, name: str, want) -> bool:
    """One field of the raw bidding strategy message against the plan's value. A field with presence (optional)
    must be unset where `want` is None and equal to it otherwise. A plain value with no presence (target_roas,
    maximize_conversions.target_cpa_micros) that the plan wants 0 must be absent from the message: -0.0 equals 0.0
    but protobuf lists and sends it."""
    if strategy.DESCRIPTOR.fields_by_name[name].has_presence:
        return (getattr(strategy, name) if strategy.HasField(name) else None) == want
    if want == 0:
        return all(field.name != name for field, _ in strategy.ListFields())
    return getattr(strategy, name) == want


def _guard_bidding(plan, ops, E) -> list[str]:
    """The new campaign's bidding is exactly the plan's: the strategy, its target or ceiling (read with presence:
    unset is not 0; a plain value the plan wants 0 is not sent at all), an App campaign's goal, and each Search ad
    group's manual bid (only with MANUAL_CPC). Runs after the reference checks, so the request has one campaign and
    the plan's ad groups in order."""
    campaign = _creates(ops, "campaign_operation")[0]
    cpb = type(campaign).pb(campaign)
    member, fields = _planned_bidding(plan, E)
    if not member or cpb.WhichOneof("campaign_bidding_strategy") != member:
        return [BIDDING]
    strategy = getattr(cpb, member)
    if not all(_bidding_field_matches(strategy, name, want) for name, want in fields.items()):
        return [BIDDING]
    if isinstance(plan, plans.AppCampaignPlan):
        goal = getattr(E.AppCampaignBiddingStrategyGoalTypeEnum, plan.goal, None)
        if goal is None or cpb.app_campaign_setting.bidding_strategy_goal_type != goal:
            return [BIDDING]
    if isinstance(plan, SearchCampaignPlan):
        for group, planned in zip(_creates(ops, "ad_group_operation"), plan.ad_groups):
            gpb = type(group).pb(group)
            bid = planned.cpc_bid if planned.cpc_bid is not None else plan.max_cpc
            if plan.bidding != "MANUAL_CPC":
                if gpb.HasField("cpc_bid_micros"):
                    return [BIDDING]
            elif bid is None or not gpb.HasField("cpc_bid_micros") or gpb.cpc_bid_micros != to_micros(bid):
                return [BIDDING]
    return []


# ad type -> the only fields an asset update may change on it
_ASSET_UPDATE_PATHS = {
    "APP_AD": {"app_ad.headlines", "app_ad.descriptions", "app_ad.images", "app_ad.youtube_videos"},
    "APP_ENGAGEMENT_AD": {"app_engagement_ad.headlines", "app_engagement_ad.descriptions",
                          "app_engagement_ad.images", "app_engagement_ad.videos"},
    "RESPONSIVE_SEARCH_AD": {"responsive_search_ad.headlines", "responsive_search_ad.descriptions"},
}


AD_UPDATE_ONCE = "internal safety check: the request does not update the planned ad exactly once"
AD_UPDATE_MASK = "internal safety check: the ad update's mask is not exactly the asset lists the update sends"
AD_UPDATE_VALUES = ("internal safety check: the ad update's headlines, descriptions, images or videos are not "
                    "exactly the plan's, in order")
AD_UPDATE_LISTS = "internal safety check: the ad update would keep an item the plan removes, or leave out one it adds"
NEW_VIDEOS ="internal safety check: the new YouTube videos are not exactly the plan's, in order, with their names"
_UPDATE_VIDEOS = {"APP_AD": "youtube_videos", "APP_ENGAGEMENT_AD": "videos"}


def _new_video_names(assets, prefix: str, youtube_ids: list[str]) -> list[str] | None:
    """The resource names of the request's new YouTube video assets (raw protobuf), in order, if they are exactly
    `youtube_ids` in order, each with the name the compiler gives it. None if not."""
    videos = [a for a in assets if a.WhichOneof("asset_data") == "youtube_video_asset"]
    if ([(a.youtube_video_asset.youtube_video_id, a.name) for a in videos]
            != [(y, plans.video_asset_name(prefix, y)) for y in youtube_ids]):
        return None
    return [a.resource_name for a in videos]


def _guard_ad_update(plan, ops) -> list[str]:
    """Only the planned ad type's own asset lists change, and a responsive search ad gets no new assets. The one
    update sends exactly the lists the plan makes, all named in its mask: the ad's items the plan keeps, then the
    added ones, in order; the new image and video assets are exactly the plan's files and YouTube IDs. Then, without
    the compiler's helper, every removed item is gone and every added one is there."""
    kinds = [type(op).pb(op).WhichOneof("operation") for op in ops]
    if plan.ad_type == "RESPONSIVE_SEARCH_AD" and "asset_operation" in kinds:
        return ["internal safety check: a responsive search ad update creates no assets"]
    allowed = _ASSET_UPDATE_PATHS.get(plan.ad_type, set())
    for op, which in zip(ops, kinds):
        if which == "ad_operation":
            extra = set(op.ad_operation.update_mask.paths) - allowed
            if extra:
                return [f"internal safety check: the ad update would change {sorted(extra)}"]
    updates = [op.ad_operation for op, which in zip(ops, kinds) if which == "ad_operation"]
    if len(updates) != 1:
        return [AD_UPDATE_ONCE]
    if sorted(updates[0].update_mask.paths) != sorted(allowed):
        return [AD_UPDATE_MASK]
    prefix, assets = plans.asset_prefix(plan), [_pb(a) for a in _creates(ops, "asset_operation")]
    new_images = _new_image_names(assets, prefix, [r.file for r in plan.add_images if not r.asset])
    if new_images is None:
        return [NEW_IMAGES]
    new_videos = _new_video_names(assets, prefix, [r.youtube_id for r in plan.add_videos if not r.asset])
    if new_videos is None:
        return [NEW_VIDEOS]
    field = allowlists.AD_UPDATES.get(plan.ad_type, ("",))[0]
    if not field:
        return [AD_UPDATE_VALUES]
    merged = plans.merged_assets(plan)
    info = getattr(_pb(updates[0].update), field)
    sent = {"headlines": [t.text for t in info.headlines], "descriptions": [t.text for t in info.descriptions]}
    added = {"headlines": list(plan.add_headlines), "descriptions": list(plan.add_descriptions)}
    planned = {"headlines": merged["headlines"], "descriptions": merged["descriptions"]}
    if plan.ad_type in _UPDATE_VIDEOS:
        images, videos = iter(new_images), iter(new_videos)
        sent["images"] = [i.asset for i in info.images]
        sent["videos"] = [v.asset for v in getattr(info, _UPDATE_VIDEOS[plan.ad_type])]
        added["images"] = [r.asset or next(images) for r in plan.add_images]
        added["videos"] = [r.asset or next(videos) for r in plan.add_videos]
        planned["images"] = merged["images"] + added["images"]
        planned["videos"] = merged["videos"] + added["videos"]
    if sent != planned:
        return [AD_UPDATE_VALUES]
    return _update_lists_problems(plan, sent, added)


def _update_lists_problems(plan, sent: dict, added: dict) -> list[str]:
    """Read from the plan alone, not with plans.merged_assets, which the compiler uses too (a bug there would pass
    the comparison with it): in each list sent, an item the plan removes appears only as often as the plan adds it
    back to that list (usually never), and every item the plan adds is there."""
    for kind, items in sent.items():
        if (any(items.count(x) != added[kind].count(x) for x in plan.remove)
                or Counter(added[kind]) - Counter(items)):
            return [AD_UPDATE_LISTS]
    return []


def _creates(ops, field: str) -> list:
    """The `create` of every operation of one kind (a MutateOperation field), in order."""
    return [getattr(op, field).create for op in ops if type(op).pb(op).WhichOneof("operation") == field]


REFERENCES = "internal safety check: the new campaign's parts do not reference each other correctly"
LOCATION_OPTION = ("internal safety check: the campaign would not target only people in or regularly in its "
                   "locations")


def _guard_app_references(ops) -> list[str]:
    """The new budget, campaign, criteria, ad group and ad must point at each other, not at anything else."""
    bad = [REFERENCES]
    budgets, campaigns = _creates(ops, "campaign_budget_operation"), _creates(ops, "campaign_operation")
    groups, ads = _creates(ops, "ad_group_operation"), _creates(ops, "ad_group_ad_operation")
    if len(budgets) != 1 or len(campaigns) != 1 or len(groups) != 1 or len(ads) != 1:
        return bad
    budget, campaign, group, ad = budgets[0], campaigns[0], groups[0], ads[0]
    if not budget.resource_name or campaign.campaign_budget != budget.resource_name:
        return bad
    if not campaign.resource_name or group.campaign != campaign.resource_name:
        return bad
    if any(c.campaign != campaign.resource_name for c in _creates(ops, "campaign_criterion_operation")):
        return bad
    if not group.resource_name or ad.ad_group != group.resource_name:
        return bad
    return []


APP_CAMPAIGN_VALUES = ("internal safety check: the campaign, budget or ad group name, the app, store, campaign type, "
                       "conversion actions, locations or languages differ from the plan")
APP_AD_VALUES = ("internal safety check: the ad's type, headlines, descriptions, images, videos or deep links differ "
                 "from the plan")


def _guard_app_values(plan, ops, E) -> list[str]:
    """The App campaign's values are the plan's: names, app and store, campaign type and subtype, conversion actions,
    locations and languages (in order, nothing else), the new image and video assets, and the ad's type, texts,
    images, videos and deep links, in order. Runs after the references, so there is one of each part."""
    budget, campaign = _creates(ops, "campaign_budget_operation")[0], _creates(ops, "campaign_operation")[0]
    group, ad = _creates(ops, "ad_group_operation")[0], _pb(_creates(ops, "ad_group_ad_operation")[0]).ad
    setting = campaign.app_campaign_setting
    criteria = [_pb(c) for c in _creates(ops, "campaign_criterion_operation")]
    if (campaign.name != plan.name or budget.name != plans.budget_name(plan.name)
            or group.name != plans.ad_group_name(plan.name)
            or campaign.advertising_channel_type != E.AdvertisingChannelTypeEnum.MULTI_CHANNEL
            or _enum_name(E.AdvertisingChannelSubTypeEnum, campaign.advertising_channel_sub_type) != plan.subtype
            or setting.app_id != plan.app_id or _enum_name(E.AppCampaignAppStoreEnum, setting.app_store) != plan.app_store
            or list(campaign.selective_optimization.conversion_actions) != plan.conversion_actions
            or [c.location.geo_target_constant for c in criteria if c.WhichOneof("criterion") == "location"]
            != plan.locations
            or [c.language.language_constant for c in criteria if c.WhichOneof("criterion") == "language"]
            != plan.languages
            or len(criteria) != len(plan.locations) + len(plan.languages)):
        return [APP_CAMPAIGN_VALUES]
    prefix, assets = plans.asset_prefix(plan), [_pb(a) for a in _creates(ops, "asset_operation")]
    new_images = _new_image_names(assets, prefix, [r.file for r in plan.images if not r.asset])
    if new_images is None:
        return [NEW_IMAGES]
    new_videos = _new_video_names(assets, prefix, [r.youtube_id for r in plan.videos if not r.asset])
    if new_videos is None:
        return [NEW_VIDEOS]
    kind, videos = ("app_ad", "youtube_videos") if plan.subtype == "APP_CAMPAIGN" else ("app_engagement_ad", "videos")
    if ad.WhichOneof("ad_data") != kind:
        return [APP_AD_VALUES]
    info, images_iter, videos_iter = getattr(ad, kind), iter(new_images), iter(new_videos)
    sent = ([t.text for t in info.headlines], [t.text for t in info.descriptions], [i.asset for i in info.images],
            [v.asset for v in getattr(info, videos)],
            [(_enum_name(E.AppUrlOperatingSystemTypeEnum, u.os_type), u.url) for u in ad.final_app_urls])
    planned = (plan.headlines, plan.descriptions, [r.asset or next(images_iter) for r in plan.images],
               [r.asset or next(videos_iter) for r in plan.videos],
               [(link.get("os_type"), link.get("url")) for link in plan.final_app_urls])
    if sent != planned:
        return [APP_AD_VALUES]
    return []


def _guard_search_references(plan: SearchCampaignPlan, ops, client) -> list[str]:
    """One new budget and one new Search campaign that never shows on the Display Network, shows on
    search partners exactly as planned, and targets only people in, or regularly in, its locations.
    Every criterion, ad group, keyword, ad and asset link points only at the parts created in this
    request, and every new asset is linked to the new campaign."""
    bad = [REFERENCES]
    budgets, campaigns = _creates(ops, "campaign_budget_operation"), _creates(ops, "campaign_operation")
    if len(budgets) != 1 or len(campaigns) != 1:
        return bad
    budget, campaign = budgets[0], campaigns[0]
    if not budget.resource_name or not campaign.resource_name or campaign.campaign_budget != budget.resource_name:
        return bad
    if campaign.advertising_channel_type != client.enums.AdvertisingChannelTypeEnum.SEARCH or not _search_networks_only(
            type(campaign).pb(campaign)):
        return ["internal safety check: the campaign would not be a Search campaign kept off the Display Network"]
    networks = type(campaign).pb(campaign).network_settings  # raw protobuf: an unset bool is not False here
    if not networks.HasField("target_search_network") or networks.target_search_network is not (
            plan.search_partners is True):
        return [PARTNERS]
    if campaign.geo_target_type_setting.positive_geo_target_type != client.enums.PositiveGeoTargetTypeEnum.PRESENCE:
        return [LOCATION_OPTION]
    if any(c.campaign != campaign.resource_name for c in _creates(ops, "campaign_criterion_operation")):
        return bad
    groups = _creates(ops, "ad_group_operation")
    group_names = {g.resource_name for g in groups}
    if not groups or "" in group_names or len(group_names) != len(groups):
        return bad
    if any(g.campaign != campaign.resource_name for g in groups):
        return bad
    if any(x.ad_group not in group_names
           for x in [*_creates(ops, "ad_group_criterion_operation"), *_creates(ops, "ad_group_ad_operation")]):
        return bad
    assets = _creates(ops, "asset_operation")
    asset_names = {a.resource_name for a in assets}
    if "" in asset_names or len(asset_names) != len(assets):
        return bad
    links = _creates(ops, "campaign_asset_operation")
    if any(link.campaign != campaign.resource_name or link.asset not in asset_names for link in links):
        return bad
    if asset_names - {link.asset for link in links}:
        return ["internal safety check: a new asset is not linked to the new campaign"]
    return []


def _search_networks_only(cpb) -> bool:
    """Read on the raw protobuf, because proto-plus reads an unset optional bool as False: Google
    Search explicitly on, the Display Network and the Google partner network explicitly off, YouTube
    and Google TV not on. (Search partners, target_search_network, is the plan's choice.)"""
    if not cpb.HasField("network_settings"):
        return False
    ns = cpb.network_settings
    explicit = all(ns.HasField(f) for f in ("target_google_search", "target_content_network",
                                            "target_partner_search_network"))
    return (explicit and ns.target_google_search is True and ns.target_content_network is False
            and ns.target_partner_search_network is False and not ns.target_youtube
            and not ns.target_google_tv_network)


_ASSET_PART = {"sitelink_asset": "sitelink", "callout_asset": "callout", "structured_snippet_asset": "snippet"}
_OTHER_PART = {"campaign_budget_operation": "budget", "campaign_operation": "campaign", "ad_group_operation": "ad group",
               "ad_group_ad_operation": "ad", "campaign_asset_operation": "asset link"}


def _search_part(which: str, create) -> str:
    """What one create in a Search request adds, in the words _guard_search_counts counts."""
    pb = type(create).pb(create)
    if which == "asset_operation":
        return _ASSET_PART.get(pb.WhichOneof("asset_data"), f"{pb.WhichOneof('asset_data')} asset")
    if which in ("campaign_criterion_operation", "ad_group_criterion_operation"):
        level = "campaign" if which == "campaign_criterion_operation" else "ad group"
        kind = pb.WhichOneof("criterion")
        if kind == "keyword":
            kind = "negative keyword" if create.negative else "keyword"
        return f"{level} {kind}"
    return _OTHER_PART[which]


def _guard_search_counts(plan: SearchCampaignPlan, ops) -> list[str]:
    """Exactly as many operations of each kind as the plan has parts: nothing extra, nothing left out."""
    groups = plan.ad_groups
    want = {
        "budget": 1, "campaign": 1,
        "campaign location": len(plan.locations), "campaign language": len(plan.languages),
        "campaign negative keyword": len(plan.negative_keywords),
        "ad group": len(groups), "ad group keyword": sum(len(g.keywords) for g in groups),
        "ad group negative keyword": sum(len(g.negative_keywords) for g in groups), "ad": len(groups),
        "sitelink": len(plan.sitelinks), "callout": len(plan.callouts), "snippet": len(plan.snippets),
        "asset link": len(plan.sitelinks) + len(plan.callouts) + len(plan.snippets),
    }
    seen = Counter()
    for op in ops:
        which = type(op).pb(op).WhichOneof("operation")
        seen[_search_part(which, getattr(op, which).create)] += 1
    for part in [*want, *sorted(set(seen) - set(want))]:
        if seen[part] != want.get(part, 0):
            return [f"internal safety check: {seen[part]} {part} operations in the request, "
                    f"{want.get(part, 0)} in the plan"]
    return []


SEARCH_CAMPAIGN = ("internal safety check: the campaign, budget or ad group names, or the campaign's locations, "
                   "languages or negative keywords, differ from the plan")
SEARCH_KEYWORDS = "internal safety check: an ad group's keywords or negative keywords differ from the plan"
SEARCH_ADS = ("internal safety check: an ad's headlines, descriptions or paths differ from the plan, or it is in "
              "another ad group")
SEARCH_URLS = ("internal safety check: an ad or sitelink would not send people to exactly its planned final URL on "
               "the allowed domains")
SEARCH_ASSETS = ("internal safety check: a sitelink, callout or structured snippet differs from the plan, or is "
                 "linked to the campaign as another kind of asset")
_LINK_TYPE = {"sitelink_asset": "SITELINK", "callout_asset": "CALLOUT", "structured_snippet_asset": "STRUCTURED_SNIPPET"}


def _keywords(criteria, E) -> list[tuple[str, str]]:
    return [(c.keyword.text, _enum_name(E.KeywordMatchTypeEnum, c.keyword.match_type)) for c in criteria]


def _guard_search_values(plan: SearchCampaignPlan, ops, cfg, E) -> list[str]:
    """The Search campaign's values are the plan's, in order: names; the campaign's locations, languages and negative
    keywords; each ad group's keywords and negative keywords; each ad in its own ad group with its texts and paths;
    every final URL (each ad's and each sitelink's) exactly the planned one and on the allowed domains; the
    sitelinks, callouts and snippets; and each asset linked as its own kind. Runs after the references and counts,
    so the parts line up."""
    budget, campaign = _creates(ops, "campaign_budget_operation")[0], _creates(ops, "campaign_operation")[0]
    groups = _creates(ops, "ad_group_operation")
    criteria = [_pb(c) for c in _creates(ops, "campaign_criterion_operation")]
    by_kind = {kind: [c for c in criteria if c.WhichOneof("criterion") == kind] for kind in ("location", "language")}
    if (campaign.name != plan.name or budget.name != plans.budget_name(plan.name)
            or [g.name for g in groups] != [g.name for g in plan.ad_groups]
            or [c.location.geo_target_constant for c in by_kind["location"]] != plan.locations
            or [c.language.language_constant for c in by_kind["language"]] != plan.languages
            or _keywords([c for c in criteria if c.negative], E) != [(k.text, k.match) for k in plan.negative_keywords]):
        return [SEARCH_CAMPAIGN]
    keywords = [_pb(c) for c in _creates(ops, "ad_group_criterion_operation")]
    ads = _creates(ops, "ad_group_ad_operation")
    for group, planned, ad in zip(groups, plan.ad_groups, ads):
        mine = [c for c in keywords if c.ad_group == group.resource_name]
        if (_keywords([c for c in mine if not c.negative], E) != [(k.text, k.match) for k in planned.keywords]
                or _keywords([c for c in mine if c.negative], E)
                != [(k.text, k.match) for k in planned.negative_keywords]):
            return [SEARCH_KEYWORDS]
        rsa = _pb(ad).ad.responsive_search_ad
        if (ad.ad_group != group.resource_name or [t.text for t in rsa.headlines] != planned.ad.headlines
                or [t.text for t in rsa.descriptions] != planned.ad.descriptions
                or (rsa.path1, rsa.path2) != (planned.ad.path1, planned.ad.path2)):
            return [SEARCH_ADS]
        if list(ad.ad.final_urls) != [planned.ad.final_url] or not cfg.allows_url(planned.ad.final_url):
            return [SEARCH_URLS]
    assets = [_pb(a) for a in _creates(ops, "asset_operation")]
    sitelinks = [a for a in assets if a.WhichOneof("asset_data") == "sitelink_asset"]
    for asset, link in zip(sitelinks, plan.sitelinks):
        if list(asset.final_urls) != [link.url] or not cfg.allows_url(link.url):
            return [SEARCH_URLS]
    sent = ([(a.sitelink_asset.link_text, a.sitelink_asset.description1, a.sitelink_asset.description2)
             for a in sitelinks],
            [a.callout_asset.callout_text for a in assets if a.WhichOneof("asset_data") == "callout_asset"],
            [(a.structured_snippet_asset.header, list(a.structured_snippet_asset.values)) for a in assets
             if a.WhichOneof("asset_data") == "structured_snippet_asset"])
    planned_assets = ([(link.text, link.desc1, link.desc2) if link.desc1 else (link.text, "", "")
                       for link in plan.sitelinks],
                      list(plan.callouts), [(snippet.header, list(snippet.values)) for snippet in plan.snippets])
    kinds = {a.resource_name: _LINK_TYPE.get(a.WhichOneof("asset_data")) for a in assets}
    if sent != planned_assets or any(_enum_name(E.AssetFieldTypeEnum, link.field_type) != kinds.get(link.asset)
                                     for link in _creates(ops, "campaign_asset_operation")):
        return [SEARCH_ASSETS]
    return []


# ------------------------------------------------------------------ Demand Gen guard

DEMAND_GEN_CAMPAIGN = ("internal safety check: the campaign would not be a Demand Gen campaign with upgraded "
                       "targeting and the location option 'people in or regularly in'")
DEMAND_GEN_GOALS = ("internal safety check: the conversion goal updates are not exactly the plan's goals on and "
                    "every other account goal off, on the new campaign")
DEMAND_GEN_CHANNELS = ("internal safety check: an ad group would not use exactly its planned channels, without "
                       "third-party Display or Maps")
DEMAND_GEN_EXPANSION = "internal safety check: an ad group would turn on optimized targeting or demographic expansion"
DEMAND_GEN_ADS = "internal safety check: an ad is not one planned kind of Demand Gen ad with one allowed final URL"
DEMAND_GEN_GENERATED = "internal safety check: an ad would not opt out of every generated creative"
UNUSED_ASSET = "internal safety check: a new asset is not used by any new ad"
DEMAND_GEN_NAMES = ("internal safety check: a campaign, budget, ad group or ad name, or an ad's ad group, differs "
                    "from the plan")
DEMAND_GEN_TARGETING = "internal safety check: an ad group's locations, languages or audiences differ from the plan"
DEMAND_GEN_MEDIA = ("internal safety check: the new YouTube videos or calls to action, or a video ad's videos or call "
                    "to action, differ from the plan")
DEMAND_GEN_TEXT = ("internal safety check: an ad's headlines, long headlines, descriptions, business name or final URL "
                   "differ from the plan")
DEMAND_GEN_IMAGES = "internal safety check: an ad's images or logos differ from the plan"
_GOAL = re.compile(r"customers/([0-9]{10})/campaignConversionGoals/(-[1-9][0-9]*)~([A-Z_]+)~([A-Z_]+)")
# ad oneof -> (the plan's ad type, the only fields it may set)
_DEMAND_GEN_ADS = {
    "demand_gen_multi_asset_ad": ("image", {"headlines", "descriptions", "business_name", "marketing_images",
                                            "square_marketing_images", "portrait_marketing_images", "logo_images"}),
    "demand_gen_video_responsive_ad": ("video", {"headlines", "long_headlines", "descriptions", "business_name",
                                                 "videos", "logo_images", "call_to_actions"}),
}


def _guard_demand_gen(plan, ops, client, cfg, facts: dict) -> list[str]:
    """One new budget and one new Demand Gen campaign with every fixed setting; the conversion
    goal updates; exactly the planned parts; each ad group with its own channels and no expansion; each ad
    a planned kind with one allowed final URL, every generated creative opted out, and assets that are new
    in this request or already in the account."""
    E = client.enums
    budgets, campaigns = _creates(ops, "campaign_budget_operation"), _creates(ops, "campaign_operation")
    if len(budgets) != 1 or len(campaigns) != 1:
        return [REFERENCES]
    budget, campaign = budgets[0], campaigns[0]
    if not budget.resource_name or not campaign.resource_name or campaign.campaign_budget != budget.resource_name:
        return [REFERENCES]
    if not _demand_gen_campaign_ok(type(campaign).pb(campaign), E):
        return [DEMAND_GEN_CAMPAIGN]
    problems = (_guard_bidding(plan, ops, E) or _guard_demand_gen_goals(plan, ops, campaign.resource_name, cfg, facts)
                or _guard_demand_gen_counts(plan, ops, facts))
    if problems:
        return problems
    groups = _creates(ops, "ad_group_operation")
    names = [g.resource_name for g in groups]
    if "" in names or len(set(names)) != len(names) or any(g.campaign != campaign.resource_name for g in groups):
        return [REFERENCES]
    if any(x.ad_group not in names
           for x in [*_creates(ops, "ad_group_criterion_operation"), *_creates(ops, "ad_group_ad_operation")]):
        return [REFERENCES]
    for group, planned in zip(groups, plan.ad_groups):
        problems = _demand_gen_group_problems(type(group).pb(group), planned)
        if problems:
            return problems
    new_assets = [a.resource_name for a in _creates(ops, "asset_operation")]
    if "" in new_assets or len(set(new_assets)) != len(new_assets):
        return [REFERENCES]
    used: set[str] = set()
    existing = re.compile(rf"customers/{re.escape(cfg.customer_id)}/assets/[0-9]+")
    for ad in _creates(ops, "ad_group_ad_operation"):
        problems, refs = _demand_gen_ad_problems(type(ad).pb(ad), cfg, E)
        if problems:
            return problems
        if any(r not in new_assets and not existing.fullmatch(r) for r in refs):
            return [REFERENCES]
        used |= set(refs)
    if set(new_assets) - used:
        return [UNUSED_ASSET]
    return _demand_gen_values(plan, ops, budget, campaign, groups, E)


_CRITERION_VALUE = {"location": "geo_target_constant", "language": "language_constant", "user_list": "user_list"}
_AD_KIND = {"image": "demand_gen_multi_asset_ad", "video": "demand_gen_video_responsive_ad"}


def _enum_name(enum, value: int) -> str:
    """An enum value's name, or the bare number for a value the enum doesn't have (it never matches a plan)."""
    try:
        return enum(value).name
    except ValueError:
        return str(value)


def _criterion_value(criterion) -> tuple[str, str]:
    """An ad group criterion as (kind, resource), read on the raw protobuf."""
    pb = type(criterion).pb(criterion)
    kind = pb.WhichOneof("criterion")
    field = _CRITERION_VALUE.get(kind)
    return (kind, getattr(getattr(pb, kind), field)) if field else (str(kind), "")


def _demand_gen_values(plan, ops, budget, campaign, groups, E) -> list[str]:
    """The values are the plan's, not just the counts: the campaign, budget, ad group and ad names; each ad group's
    locations, languages and user lists; the new images (the plan's files: bytes and names), YouTube videos and calls
    to action; and each ad in its own ad group, of its planned kind, with its texts, its final URL, every image and
    logo, and a video ad's videos and call to action. Runs after the counts and references, so the parts line up."""
    if (campaign.name != plan.name or budget.name != plans.budget_name(plan.name)
            or [g.name for g in groups] != [g.name for g in plan.ad_groups]):
        return [DEMAND_GEN_NAMES]
    criteria = _creates(ops, "ad_group_criterion_operation")
    for group, planned in zip(groups, plan.ad_groups):
        want = sorted([*(("location", x) for x in planned.locations), *(("language", x) for x in planned.languages),
                       *(("user_list", x) for x in planned.audiences)])
        if sorted(_criterion_value(c) for c in criteria if c.ad_group == group.resource_name) != want:
            return [DEMAND_GEN_TARGETING]
    assets = [type(a).pb(a) for a in _creates(ops, "asset_operation")]
    files = plans_demand_gen.new_image_files(plan)
    new_images = _new_image_names(assets, plan.name, files)
    if new_images is None:
        return [NEW_IMAGES]
    image_names = {plans_demand_gen._file_key(f): name for f, name in zip(files, new_images)}
    videos = {a.resource_name: a.youtube_video_asset.youtube_video_id for a in assets
              if a.WhichOneof("asset_data") == "youtube_video_asset"}
    ctas = {a.resource_name: _enum_name(E.CallToActionTypeEnum, a.call_to_action_asset.call_to_action) for a in assets
            if a.WhichOneof("asset_data") == "call_to_action_asset"}
    if (_new_video_names(assets, plan.name, plans_demand_gen.new_youtube_ids(plan)) is None  # IDs and names
            or list(ctas.values()) != plans_demand_gen.calls_to_action(plan)):
        return [DEMAND_GEN_MEDIA]
    planned_ads = [(group.resource_name, ad, name)
                   for group, planned, names in zip(groups, plan.ad_groups, plans_demand_gen.ad_names(plan))
                   for ad, name in zip(planned.ads, names)]
    for created, (group_name, ad, name) in zip(_creates(ops, "ad_group_ad_operation"), planned_ads):
        if created.ad_group != group_name or created.ad.name != name:
            return [DEMAND_GEN_NAMES]
        if type(created.ad).pb(created.ad).WhichOneof("ad_data") != _AD_KIND[ad.type]:
            return [DEMAND_GEN_ADS]
        info = _pb(getattr(created.ad, _AD_KIND[ad.type]))
        if _demand_gen_texts(info, ad.type) != _planned_texts(ad) or list(created.ad.final_urls) != [ad.final_url]:
            return [DEMAND_GEN_TEXT]
        slots = [name for name, _, _ in plans_demand_gen.IMAGE_LISTS] if ad.type == "image" else ["logo_images"]
        if any([i.asset for i in getattr(info, slot)]
               != [ref.asset or (image_names.get(plans_demand_gen._file_key(ref.file)) if ref.file else None)
                   for ref in getattr(ad, slot)]
               for slot in slots):
            return [DEMAND_GEN_IMAGES]
        if ad.type == "video":
            info = created.ad.demand_gen_video_responsive_ad
            if ([videos.get(v.asset, v.asset) for v in info.videos] != [v.asset or v.youtube_id for v in ad.videos]
                    or [ctas.get(c.asset, c.asset) for c in info.call_to_actions]
                    != ([ad.call_to_action] if ad.call_to_action else [])):
                return [DEMAND_GEN_MEDIA]
    return []


def _demand_gen_texts(info, kind: str) -> tuple:
    """An ad's texts as sent (raw protobuf): headlines, long headlines (video ads), descriptions, business name (a
    plain string on an image ad, an AdTextAsset on a video ad)."""
    long = [t.text for t in info.long_headlines] if kind == "video" else []
    business = info.business_name.text if kind == "video" else info.business_name
    return [t.text for t in info.headlines], long, [t.text for t in info.descriptions], business


def _planned_texts(ad) -> tuple:
    return ad.headlines, getattr(ad, "long_headlines", []), ad.descriptions, ad.business_name


def _demand_gen_campaign_ok(cpb, E) -> bool:
    """Read on the raw protobuf: optional fields must be present, not just read as their default. (The
    budget's explicitly_shared and delivery method, and the EU political ads declaration, are checked for every
    plan kind in _guard.)"""
    settings = cpb.demand_gen_campaign_settings
    return (cpb.advertising_channel_type == E.AdvertisingChannelTypeEnum.DEMAND_GEN
            and cpb.advertising_channel_sub_type == E.AdvertisingChannelSubTypeEnum.UNSPECIFIED
            and cpb.HasField("demand_gen_campaign_settings") and settings.HasField("upgraded_targeting")
            and settings.upgraded_targeting is True
            and cpb.geo_target_type_setting.positive_geo_target_type == E.PositiveGeoTargetTypeEnum.PRESENCE
            and not cpb.HasField("network_settings"))


def _guard_demand_gen_goals(plan, ops, campaign_name: str, cfg, facts: dict) -> list[str]:
    """One update per account goal, each only its biddable flag, on the new campaign: True exactly for the
    plan's goals."""
    wanted = {(g.category, g.origin) for g in plan.conversion_goals}
    seen: set[tuple[str, str]] = set()
    for op in ops:
        if type(op).pb(op).WhichOneof("operation") != "campaign_conversion_goal_operation":
            continue
        operation = op.campaign_conversion_goal_operation
        update = operation.update
        m = _GOAL.fullmatch(update.resource_name)
        if not m or m.group(1) != cfg.customer_id or campaign_name != f"customers/{m.group(1)}/campaigns/{m.group(2)}":
            return [DEMAND_GEN_GOALS]
        pair = (m.group(3), m.group(4))
        fields = {f.name for f, _ in type(update).pb(update).ListFields()}
        if list(operation.update_mask.paths) != ["biddable"] or not fields <= {"resource_name", "biddable"}:
            return [DEMAND_GEN_GOALS]
        if pair in seen or update.biddable != (pair in wanted):
            return [DEMAND_GEN_GOALS]
        seen.add(pair)
    if seen != set(facts.get("account_goals", ())) or not wanted <= seen:
        return [DEMAND_GEN_GOALS]
    return []


_DEMAND_GEN_ASSETS = {"image_asset": "image asset", "youtube_video_asset": "video asset",
                      "call_to_action_asset": "call to action asset"}
# an AudienceInfo criterion ("audience") is counted on its own, never as one of the plan's user lists
_DEMAND_GEN_CRITERIA = {"location": "location", "language": "language", "user_list": "user list"}
_DEMAND_GEN_OTHER = {"campaign_budget_operation": "budget", "campaign_operation": "campaign",
                     "campaign_conversion_goal_operation": "conversion goal", "ad_group_operation": "ad group"}


def _demand_gen_part(which: str, op) -> str:
    """What one operation in a Demand Gen request adds, in the words _guard_demand_gen_counts counts."""
    if which in _DEMAND_GEN_OTHER:
        return _DEMAND_GEN_OTHER[which]
    pb = type(op).pb(op)
    create = getattr(pb, which).create
    if which == "asset_operation":
        kind = create.WhichOneof("asset_data")
        return _DEMAND_GEN_ASSETS.get(kind, f"{kind} asset")
    if which == "ad_group_criterion_operation":
        kind = create.WhichOneof("criterion")
        negative = "negative " if create.HasField("negative") and create.negative else ""
        return f"ad group {negative}{_DEMAND_GEN_CRITERIA.get(kind, str(kind).replace('_', ' '))}"
    kind = create.ad.WhichOneof("ad_data")
    return {"demand_gen_multi_asset_ad": "image ad", "demand_gen_video_responsive_ad": "video ad"}.get(kind, f"{kind} ad")


def _guard_demand_gen_counts(plan, ops, facts: dict) -> list[str]:
    """Exactly as many operations of each kind as the plan has parts: nothing extra, nothing left out."""
    groups = plan.ad_groups
    ads = [ad for g in groups for ad in g.ads]
    want = {
        "image asset": len(plans_demand_gen.new_image_files(plan)),
        "video asset": len(plans_demand_gen.new_youtube_ids(plan)),
        "call to action asset": len(plans_demand_gen.calls_to_action(plan)),
        "budget": 1, "campaign": 1, "conversion goal": len(facts.get("account_goals", ())),
        "ad group": len(groups), "ad group location": sum(len(g.locations) for g in groups),
        "ad group language": sum(len(g.languages) for g in groups),
        "ad group user list": sum(len(g.audiences) for g in groups),
        "image ad": sum(ad.type == "image" for ad in ads), "video ad": sum(ad.type == "video" for ad in ads),
    }
    seen = Counter(_demand_gen_part(type(op).pb(op).WhichOneof("operation"), op) for op in ops)
    for part in [*want, *sorted(set(seen) - set(want))]:
        if seen[part] != want.get(part, 0):
            return [f"internal safety check: {seen[part]} {part} operations in the request, "
                    f"{want.get(part, 0)} in the plan"]
    return []


def _demand_gen_group_problems(gpb, planned) -> list[str]:
    """Exactly the planned channels, chosen one by one (never a channel strategy), and no expansion."""
    settings = gpb.demand_gen_ad_group_settings
    if not (gpb.HasField("demand_gen_ad_group_settings") and settings.HasField("channel_controls")
            and settings.channel_controls.WhichOneof("channel_configuration") == "selected_channels"):
        return [DEMAND_GEN_CHANNELS]
    chosen = settings.channel_controls.selected_channels
    on = {f.name for f in chosen.DESCRIPTOR.fields if getattr(chosen, f.name)}
    if on != set(planned.channels) or on - set(plans_demand_gen.CHANNELS):
        return [DEMAND_GEN_CHANNELS]
    if gpb.optimized_targeting_enabled or gpb.exclude_demographic_expansion is not True:
        return [DEMAND_GEN_EXPANSION]
    return []


def _demand_gen_ad_problems(adpb, cfg, E) -> tuple[list[str], list[str]]:
    """One ad: a planned kind with only that kind's fields, one allowed final URL and nothing else to
    send people elsewhere, and every generated creative of its kind opted out. Returns (problems, the
    assets it uses)."""
    ad = adpb.ad
    kind = ad.WhichOneof("ad_data")
    if kind not in _DEMAND_GEN_ADS:
        return [DEMAND_GEN_ADS], []
    planned, allowed = _DEMAND_GEN_ADS[kind]
    info = getattr(ad, kind)
    if {f.name for f, _ in info.ListFields()} - allowed:
        return [DEMAND_GEN_ADS], []
    if (len(ad.final_urls) != 1 or not cfg.allows_url(ad.final_urls[0]) or ad.final_mobile_urls or ad.final_app_urls
            or ad.HasField("tracking_url_template") or ad.HasField("final_url_suffix")):
        return [DEMAND_GEN_ADS], []
    settings = adpb.ad_group_ad_asset_automation_settings
    want = {getattr(E.AssetAutomationTypeEnum, t) for t in plans_demand_gen.GENERATED_CREATIVES[planned]}
    if (len(settings) != len(want) or {s.asset_automation_type for s in settings} != want
            or not all(s.HasField("asset_automation_type") and s.HasField("asset_automation_status")
                       and s.asset_automation_status == E.AssetAutomationStatusEnum.OPTED_OUT for s in settings)):
        return [DEMAND_GEN_GENERATED], []
    refs = [item.asset for field, value in info.ListFields() if field.message_type and field.is_repeated
            for item in value if "asset" in item.DESCRIPTOR.fields_by_name]
    return [], refs


# ------------------------------------------------------------------ Performance Max guard

PMAX_CAMPAIGN = ("internal safety check: the campaign would not be a Performance Max campaign with brand guidelines "
                 "on, every asset automation the builder sends opted out and the location option 'people in or "
                 "regularly in'")
PMAX_NAMES = "internal safety check: a campaign, budget or asset group name differs from the plan"
PMAX_ASSET_GROUPS = "internal safety check: an asset group would not have exactly its planned final URL and display path"
PMAX_TARGETING = "internal safety check: the campaign's locations or languages differ from the plan"
PMAX_ASSETS = ("internal safety check: the new texts, YouTube videos (IDs and names) or calls to action differ from "
               "the plan")
PMAX_LINKS = "internal safety check: the campaign's brand assets or an asset group's assets differ from the plan"
PMAX_SIGNALS = "internal safety check: an asset group's audience signal or search themes differ from the plan"
PMAX_UNUSED = "internal safety check: a new asset is not linked to the new campaign or one of its asset groups"
_PMAX_ASSETS = {"text_asset": "text asset", "image_asset": "image asset", "youtube_video_asset": "video asset",
                "call_to_action_asset": "call to action asset"}
_PMAX_BRAND = {"BUSINESS_NAME": "business name link", "LOGO": "logo link", "LANDSCAPE_LOGO": "landscape logo link"}
_PMAX_GROUP_LINKS = {"HEADLINE": "headline", "LONG_HEADLINE": "long headline", "DESCRIPTION": "description",
                     "MARKETING_IMAGE": "landscape image", "SQUARE_MARKETING_IMAGE": "square image",
                     "PORTRAIT_MARKETING_IMAGE": "portrait image", "YOUTUBE_VIDEO": "video",
                     "CALL_TO_ACTION_SELECTION": "call to action"}
_PMAX_SIGNAL = {"audience": "audience signal", "search_theme": "search theme"}
_PMAX_OTHER = {"campaign_budget_operation": "budget", "campaign_operation": "campaign",
               "campaign_conversion_goal_operation": "conversion goal", "asset_group_operation": "asset group"}


def _guard_pmax(plan, ops, client, cfg, facts: dict) -> list[str]:
    """One new budget and one new Performance Max campaign with every fixed setting and the plan's bidding; the
    conversion goal updates; exactly the planned parts; every part pointing only at parts of this request (or,
    for an asset, an asset already in the account), every new asset linked; each asset group with exactly its
    planned final URL (on the allowed domains) and display path (its ENABLED status is pinned in _fixed_values); and
    the values are the plan's (_pmax_values)."""
    E = client.enums
    budgets, campaigns = _creates(ops, "campaign_budget_operation"), _creates(ops, "campaign_operation")
    if len(budgets) != 1 or len(campaigns) != 1:
        return [REFERENCES]
    budget, campaign = budgets[0], campaigns[0]
    if not budget.resource_name or not campaign.resource_name or campaign.campaign_budget != budget.resource_name:
        return [REFERENCES]
    if not _pmax_campaign_ok(type(campaign).pb(campaign), E):
        return [PMAX_CAMPAIGN]
    problems = (_guard_bidding(plan, ops, E) or _guard_demand_gen_goals(plan, ops, campaign.resource_name, cfg, facts)
                or _guard_pmax_counts(plan, ops, facts, E))
    if problems:
        return problems
    groups = _creates(ops, "asset_group_operation")
    names = [g.resource_name for g in groups]
    if "" in names or len(set(names)) != len(names):
        return [REFERENCES]
    if any(x.campaign != campaign.resource_name for x in [*groups, *_creates(ops, "campaign_asset_operation"),
                                                          *_creates(ops, "campaign_criterion_operation")]):
        return [REFERENCES]
    if any(x.asset_group not in names for x in [*_creates(ops, "asset_group_asset_operation"),
                                                *_creates(ops, "asset_group_signal_operation")]):
        return [REFERENCES]
    new_assets = [a.resource_name for a in _creates(ops, "asset_operation")]
    if "" in new_assets or len(set(new_assets)) != len(new_assets):
        return [REFERENCES]
    existing = re.compile(rf"customers/{re.escape(cfg.customer_id)}/assets/[0-9]+")
    links = [*_creates(ops, "campaign_asset_operation"), *_creates(ops, "asset_group_asset_operation")]
    if any(link.asset not in new_assets and not existing.fullmatch(link.asset) for link in links):
        return [REFERENCES]
    if set(new_assets) - {link.asset for link in links}:
        return [PMAX_UNUSED]
    for group, planned in zip(groups, plan.asset_groups):
        gpb = type(group).pb(group)
        if (list(group.final_urls) != [planned.final_url] or not cfg.allows_url(planned.final_url)
                or gpb.path1 != planned.path1 or gpb.path2 != planned.path2):
            return [PMAX_ASSET_GROUPS]
    return _pmax_values(plan, ops, budget, campaign, groups, E)


def _pmax_campaign_ok(cpb, E) -> bool:
    """Read on the raw protobuf: optional fields must be present, not just read as their default. Every asset
    automation in plans_pmax.AUTOMATIONS is opted out, once each, with both fields present, and nothing else is
    listed. (The budget's explicitly_shared and delivery method, and the EU political ads declaration, are checked
    for every plan kind in _guard.)"""
    settings = cpb.asset_automation_settings
    kinds = sorted(_enum_name(E.AssetAutomationTypeEnum, s.asset_automation_type) for s in settings)
    return (cpb.advertising_channel_type == E.AdvertisingChannelTypeEnum.PERFORMANCE_MAX
            and cpb.advertising_channel_sub_type == E.AdvertisingChannelSubTypeEnum.UNSPECIFIED
            and cpb.HasField("brand_guidelines_enabled") and cpb.brand_guidelines_enabled is True
            and kinds == sorted(plans_pmax.AUTOMATIONS)
            and all(s.HasField("asset_automation_type") and s.HasField("asset_automation_status")
                    and s.asset_automation_status == E.AssetAutomationStatusEnum.OPTED_OUT for s in settings)
            and cpb.geo_target_type_setting.positive_geo_target_type == E.PositiveGeoTargetTypeEnum.PRESENCE)


def _pmax_part(which: str, op, E) -> str:
    """What one operation in a Performance Max request adds, in the words _guard_pmax_counts counts."""
    if which in _PMAX_OTHER:
        return _PMAX_OTHER[which]
    create = getattr(type(op).pb(op), which).create
    if which == "asset_operation":
        kind = create.WhichOneof("asset_data")
        return _PMAX_ASSETS.get(kind, f"{kind} asset")
    if which == "campaign_criterion_operation":
        kind = str(create.WhichOneof("criterion")).replace("_", " ")
        return f"negative {kind}" if create.HasField("negative") and create.negative else kind
    if which == "asset_group_signal_operation":
        kind = create.WhichOneof("signal")
        return _PMAX_SIGNAL.get(kind, f"{kind} signal")
    field = _enum_name(E.AssetFieldTypeEnum, create.field_type)
    if which == "campaign_asset_operation":
        return _PMAX_BRAND.get(field, f"campaign {field.lower()} link")
    return _PMAX_GROUP_LINKS.get(field, f"asset group {field.lower()} link")


def _guard_pmax_counts(plan, ops, facts: dict, E) -> list[str]:
    """Exactly as many operations of each kind as the plan has parts: nothing extra, nothing left out."""
    groups = plan.asset_groups
    want = {
        "text asset": len(plans_pmax.text_assets(plan)), "image asset": len(plans_pmax.new_image_files(plan)),
        "video asset": len(plans_pmax.new_youtube_ids(plan)),
        "call to action asset": len(plans_pmax.calls_to_action(plan)),
        "budget": 1, "campaign": 1, "business name link": 1, "logo link": len(plan.logos),
        "landscape logo link": len(plan.landscape_logos), "location": len(plan.locations),
        "language": len(plan.languages), "conversion goal": len(facts.get("account_goals", ())),
        "asset group": len(groups), "headline": sum(len(g.headlines) for g in groups),
        "long headline": sum(len(g.long_headlines) for g in groups),
        "description": sum(len(g.descriptions) for g in groups),
        "landscape image": sum(len(g.marketing_images) for g in groups),
        "square image": sum(len(g.square_marketing_images) for g in groups),
        "portrait image": sum(len(g.portrait_marketing_images) for g in groups),
        "video": sum(len(g.videos) for g in groups), "call to action": sum(bool(g.call_to_action) for g in groups),
        "audience signal": sum(bool(g.audience_signal) for g in groups),
        "search theme": sum(len(g.search_themes) for g in groups),
    }
    seen = Counter(_pmax_part(type(op).pb(op).WhichOneof("operation"), op, E) for op in ops)
    for part in [*want, *sorted(set(seen) - set(want))]:
        if seen[part] != want.get(part, 0):
            return [f"internal safety check: {seen[part]} {part} operations in the request, "
                    f"{want.get(part, 0)} in the plan"]
    return []


def _pmax_values(plan, ops, budget, campaign, groups, E) -> list[str]:
    """The values are the plan's, not just the counts: the campaign, budget and asset group names; the locations
    and languages; the new images (the plan's files: bytes and names), texts, YouTube videos (IDs and names) and
    calls to action, in order; the brand links and each asset group's links, slot by slot in order (an image is
    the planned existing asset or the new asset made for that file); and each asset group's audience signal and
    search themes. Runs after the counts and references, so the parts line up."""
    if (campaign.name != plan.name or budget.name != plans.budget_name(plan.name)
            or [g.name for g in groups] != [g.name for g in plan.asset_groups]):
        return [PMAX_NAMES]
    criteria = sorted(_criterion_value(c) for c in _creates(ops, "campaign_criterion_operation"))
    if criteria != sorted([*(("location", x) for x in plan.locations), *(("language", x) for x in plan.languages)]):
        return [PMAX_TARGETING]
    assets = [_pb(a) for a in _creates(ops, "asset_operation")]
    files = plans_pmax.new_image_files(plan)
    new_images = _new_image_names(assets, plan.name, files)
    if new_images is None:
        return [NEW_IMAGES]
    youtube_ids = plans_pmax.new_youtube_ids(plan)
    new_videos = _new_video_names(assets, plan.name, youtube_ids)
    texts = [a for a in assets if a.WhichOneof("asset_data") == "text_asset"]
    ctas = {a.resource_name: _enum_name(E.CallToActionTypeEnum, a.call_to_action_asset.call_to_action) for a in assets
            if a.WhichOneof("asset_data") == "call_to_action_asset"}
    if (new_videos is None or [a.text_asset.text for a in texts] != plans_pmax.text_assets(plan)
            or list(ctas.values()) != plans_pmax.calls_to_action(plan)):
        return [PMAX_ASSETS]
    shown = {a.resource_name: ("text", a.text_asset.text) for a in texts}
    shown |= {name: ("cta", cta) for name, cta in ctas.items()}
    shown |= {name: ("youtube", y) for name, y in zip(new_videos, youtube_ids)}
    shown |= {name: ("file", plans_pmax._file_key(f)) for name, f in zip(new_images, files)}

    def value(asset: str) -> tuple[str, str]:
        return shown.get(asset, ("asset", asset))

    def image(ref) -> tuple[str, str]:
        return ("asset", ref.asset) if ref.asset else ("file", plans_pmax._file_key(ref.file))

    brand = [(_enum_name(E.AssetFieldTypeEnum, link.field_type), value(link.asset))
             for link in _creates(ops, "campaign_asset_operation")]
    if brand != [("BUSINESS_NAME", ("text", plan.business_name)), *(("LOGO", image(r)) for r in plan.logos),
                 *(("LANDSCAPE_LOGO", image(r)) for r in plan.landscape_logos)]:
        return [PMAX_LINKS]
    links = _creates(ops, "asset_group_asset_operation")
    signals = _creates(ops, "asset_group_signal_operation")
    for group, planned in zip(groups, plan.asset_groups):
        got = [(_enum_name(E.AssetFieldTypeEnum, link.field_type), value(link.asset)) for link in links
               if link.asset_group == group.resource_name]
        want = [*(("HEADLINE", ("text", x)) for x in planned.headlines),
                *(("LONG_HEADLINE", ("text", x)) for x in planned.long_headlines),
                *(("DESCRIPTION", ("text", x)) for x in planned.descriptions),
                *(("MARKETING_IMAGE", image(r)) for r in planned.marketing_images),
                *(("SQUARE_MARKETING_IMAGE", image(r)) for r in planned.square_marketing_images),
                *(("PORTRAIT_MARKETING_IMAGE", image(r)) for r in planned.portrait_marketing_images),
                *(("YOUTUBE_VIDEO", ("asset", v.asset) if v.asset else ("youtube", v.youtube_id)) for v in planned.videos),
                *([("CALL_TO_ACTION_SELECTION", ("cta", planned.call_to_action))] if planned.call_to_action else [])]
        if got != want:
            return [PMAX_LINKS]
        mine = [_pb(s) for s in signals if s.asset_group == group.resource_name]
        audiences = [s.audience.audience for s in mine if s.WhichOneof("signal") == "audience"]
        themes = [s.search_theme.text for s in mine if s.WhichOneof("signal") == "search_theme"]
        if audiences != ([planned.audience_signal] if planned.audience_signal else []) or themes != planned.search_themes:
            return [PMAX_SIGNALS]
    return []


def _new_campaign_lines(plan, cfg) -> list[str]:
    over = " - ABOVE THE CAP" if plan.daily_budget > cfg.daily_budget_cap else ""
    return [f"Campaign: {confirm.quoted(plan.name)} (created PAUSED)",
            f"Daily budget: {plan.daily_budget:,.2f} {plan.currency}{over}"]


def _item_lines(items: list[str]) -> list[str]:
    """An asset update's "kind: value" items, with the value quoted. A long one keeps its end."""
    out = []
    for item in items:
        kind, _, value = item.partition(": ")
        out.append(confirm.quoted_tail(f"- {kind}: ", value))
    return out or ["- none"]


def _window_lines(tool: str, plan, cfg, facts: dict, fingerprint: str) -> list[str]:
    """What the approval window shows. Built here from the loaded plan, the config and live facts,
    never from the change tool's arguments. Free text is quoted. A list too long for the window is
    refused, never cut (_window_problem)."""
    lines = [f"Account: {cfg.customer_id}", f"Tool: {tool} ({plan.kind})", f"Plan fingerprint: {fingerprint[:12]}"]
    if isinstance(plan, plans.AppCampaignPlan):
        lines += _new_campaign_lines(plan, cfg)
        lines.append(f"Headlines: {len(plan.headlines)}, descriptions: {len(plan.descriptions)}, "
                     f"images: {len(plan.images)}, videos: {len(plan.videos)}")
    elif isinstance(plan, SearchCampaignPlan):
        groups = plan.ad_groups
        negatives = len(plan.negative_keywords) + sum(len(g.negative_keywords) for g in groups)
        lines += _new_campaign_lines(plan, cfg)
        lines += [
            f"Bidding: {bidding_label(plan)}",
            # never the Display Network: the guard refuses it
            "Networks: " + ("Google Search + search partners" if plan.search_partners is True else "Google Search only"),
            f"Locations: {len(plan.locations)} · languages: {len(plan.languages)}",
            f"Ad groups: {len(groups)}, keywords: {sum(len(g.keywords) for g in groups)}, "
            f"headlines: {sum(len(g.ad.headlines) for g in groups)}, "
            f"descriptions: {sum(len(g.ad.descriptions) for g in groups)}, negative keywords: {negatives}",
            f"Sitelinks: {len(plan.sitelinks)}, callouts: {len(plan.callouts)}, snippets: {len(plan.snippets)}",
        ]
    elif isinstance(plan, DemandGenCampaignPlan):
        lines += _new_campaign_lines(plan, cfg)
        lines += _demand_gen_lines(plan, facts)
    elif isinstance(plan, PmaxCampaignPlan):
        lines += _new_campaign_lines(plan, cfg)
        lines += _pmax_lines(plan, facts)
    elif isinstance(plan, plans.ImageUploadPlan):
        lines.append(f"Images to upload to the asset library: {len(plan.files)}")
        lines += [confirm.quoted_tail("- ", plans.shown_file(f)) for f in plan.files]
    elif isinstance(plan, plans.AssetUpdatePlan):
        lines.append(f"Campaign: {confirm.quoted(facts['campaign_name'])} ({facts['campaign_status']})")
        if facts["campaign_status"] == "ENABLED":
            lines.append("LIVE: takes effect immediately")
        lines.append(f"Ad: {plan.ad} ({plan.ad_type})")
        args = plans.approval_args(plan, cfg)
        lines.append(f"Adds {len(args['added'])} · removes {len(args['removed'])}")
        lines += ["Removed:", *_item_lines(args["removed"])]  # removals first: they matter most
        lines += ["Added:", *_item_lines(args["added"])]
    else:
        raise TypeError(f"no approval window for {plan.kind!r} plans")
    return lines


def _how_many(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


OPTIMIZED_TARGETING_LINE = ("Optimized targeting: not sent (Google's default may apply; quality_report checks it "
                            "after creation)")


def _demand_gen_lines(plan, facts: dict) -> list[str]:
    """A Demand Gen campaign in the approval window: bidding, the goals it bids on, how many other account
    goals it turns off, counts, each ad group's channels and targeting, and that optimized targeting is not
    sent (the API has no "off" for it)."""
    counts = plans_demand_gen.window_counts(plan)
    planned = {(g.category, g.origin) for g in plan.conversion_goals}
    others = len(set(facts.get("account_goals", ())) - planned)
    lines = [f"Bidding: {plans_demand_gen.bidding_label(plan)}", "Conversion goals bid on:"]
    lines += [f"- {plans_demand_gen.goal_label(g)}" for g in plan.conversion_goals]
    lines += [
        f"Other account conversion goals, not bid on: {others}",
        f"Ad groups: {len(plan.ad_groups)}, image ads: {counts['image_ads']}, video ads: {counts['video_ads']}",
        f"New images: {counts['new_images']}, existing images: {counts['existing_images']}, videos: {counts['videos']}",
    ]
    lines += [f"Ad group {i}: channels {plans_demand_gen.channel_labels(g.channels)}; "
              f"{_how_many(len(g.locations), 'location')}, {_how_many(len(g.languages), 'language')}, "
              f"{_how_many(len(g.audiences), 'audience')}"
              for i, g in enumerate(plan.ad_groups, 1)]
    return lines + [OPTIMIZED_TARGETING_LINE, "Never on third-party Display; generated creatives off"]


PMAX_FIXED_LINE = ("Brand guidelines on; final URL expansion, generated text, enhanced videos, image enhancement and "
                   "video crawl off")


def _pmax_lines(plan, facts: dict) -> list[str]:
    """A Performance Max campaign in the approval window: bidding, the goals it bids on, how many other account
    goals it turns off, its targeting and brand, counts, each asset group's final URL and the fixed settings."""
    counts = plans_pmax.window_counts(plan)
    planned = {(g.category, g.origin) for g in plan.conversion_goals}
    others = len(set(facts.get("account_goals", ())) - planned)
    lines = [f"Bidding: {plans_pmax.bidding_label(plan)}", "Conversion goals bid on:"]
    lines += [f"- {plans_demand_gen.goal_label(g)}" for g in plan.conversion_goals]
    lines += [
        f"Other account conversion goals, not bid on: {others}",
        f"{_how_many(len(plan.locations), 'location')}, {_how_many(len(plan.languages), 'language')}; people in or "
        "regularly in the locations",
        f"Brand: business name {confirm.quoted(plan.business_name)}, {_how_many(len(plan.logos), 'logo')}, "
        f"{_how_many(len(plan.landscape_logos), 'landscape logo')}",
        f"Asset groups: {counts['asset_groups']}, headlines: {counts['headlines']}, long headlines: "
        f"{counts['long_headlines']}, descriptions: {counts['descriptions']}",
        f"New images: {counts['new_images']}, existing images: {counts['existing_images']}, videos: {counts['videos']}",
        f"Search themes: {counts['search_themes']}, audience signals: {counts['audience_signals']}",
    ]
    lines += [confirm.quoted_tail(f"Asset group {i} final URL: ", g.final_url)
              for i, g in enumerate(plan.asset_groups, 1)]
    return lines + [PMAX_FIXED_LINE]


# plan kind -> the change tool that applies it
_CHANGE_TOOL = {"app_campaign": "create_campaign_from_plan", "search_campaign": "create_campaign_from_plan",
                "demand_gen_campaign": "create_campaign_from_plan", "pmax_campaign": "create_campaign_from_plan",
                "image_upload": "upload_images",
                "asset_update": "update_campaign_assets"}


def _window_problem(lines: list[str]) -> list[str]:
    """Everything must fit in one window: a longer list is refused, never cut."""
    return [confirm.TOO_MANY_LINES] if len(lines) > confirm.MAX_LINES else []


def _person_approves(lines: list[str]) -> tuple[bool, str | None]:
    """The builder's own Yes/No window on the person's computer: (approved, None) for the person's answer
    (an error once the window is up counts as No), or (False, why) if no window could be shown at all,
    which is not the person's No."""
    try:
        return confirm.ask_person(WINDOW_TITLE, lines) is True, None
    except confirm.WindowUnavailable as e:
        return False, str(e) or confirm.couldnt_start("no reason given", "computer")
    except Exception:
        return False, None


def _live_extra(facts: dict) -> dict:
    """An asset update's live campaign status (and the LIVE note); nothing for other plans."""
    if "campaign_status" not in facts:
        return {}
    extra = {"campaign_status": facts["campaign_status"]}
    if facts["campaign_status"] == "ENABLED":
        extra["note"] = LIVE_NOTE
    return extra


# ----------------------------------------------------------------- read-only


def playbook(section: str = "") -> str:
    """Read-only. The builder's playbook: the whole workflow for App, Search, Demand Gen and Performance Max
    campaigns and the safety, text, image, bidding and money rules. Call it before any campaign work and follow it. With `section`
    (a heading such as "Search campaigns" or "Text rules"), only that section; an unknown name returns the
    list of sections."""
    if not section.strip():
        return PLAYBOOK
    want = " ".join(section.strip().lstrip("#").split()).casefold()
    for heading, text in PLAYBOOK_SECTIONS.items():
        if want in (heading.casefold(), heading.split(" (", 1)[0].casefold()):
            return text.strip()
    return "\n".join(["Unknown section. The playbook's sections are:", *(f"- {h}" for h in PLAYBOOK_SECTIONS),
                      "Call playbook with one of them, or with no section for the whole playbook."])


def describe_campaign(campaign: str) -> dict:
    """Read-only. Settings, targeting and current ads (or asset groups) of an existing App, Search, Demand Gen
    or Performance Max campaign (exact name or ID), plus `template_plan`: a ready-to-edit app_campaign,
    search_campaign, demand_gen_campaign or pmax_campaign plan copied from it. For Search, Demand Gen and
    Performance Max campaigns, `notes` lists what could not be copied."""
    cfg, client = _ctx()
    return templates.describe_campaign(client, cfg.customer_id, campaign)


def list_campaigns(name_contains: str = "", channel: str = "MULTI_CHANNEL") -> list[dict]:
    """Read-only. Lists non-removed campaigns of one channel (App campaigns are MULTI_CHANNEL, Search
    campaigns SEARCH, Demand Gen campaigns DEMAND_GEN, Performance Max campaigns PERFORMANCE_MAX) whose name
    contains the text, with goal (an App campaign's goal; any other campaign's bidding strategy), budget and target
    cost per conversion.
    Use it to find similar campaigns to pass to `creative_insights`, and to see typical target CPAs."""
    cfg, client = _ctx()
    return templates.list_campaigns(client, cfg.customer_id, name_contains, channel)


def find_locations(names: list[str]) -> list[dict]:
    """Read-only. Google location IDs (geoTargetConstants/...) for city or region names."""
    cfg, client = _ctx()
    return templates.find_locations(client, names, cfg.country_code)


def creative_insights(campaigns: list[str], days: int = 90, min_impressions: int = 1000, download_images: bool = True,
                      mode: str = "app") -> dict:
    """Changes nothing in Google Ads. Asset-level history for the named campaigns: winners, losers,
    CTR, conversions per 1,000 impressions, Google's Best/Good/Low labels (min_impressions is at least
    1,000). mode "app" (the default) reads headlines, descriptions, images and videos; mode "demand_gen"
    reads the image and video ads' asset kinds: headlines, long headlines, descriptions, business names, calls
    to action, landscape, square, portrait and 9:16 images, logos and videos (not carousel cards); mode "pmax"
    reads Performance Max asset groups' assets (no labels exist there: ranked by results) with each asset
    group's Ad Strength and suggestions, the top asset combinations and the search categories (at most 10
    campaigns). Saves a report and the top/bottom images and video thumbnails in a new local folder; LOOK at them
    with the Read tool before writing the creative brief."""
    cfg, client = _ctx()
    return insights.build_insights(client, cfg, campaigns, days, min_impressions, download_images, mode)


def search_insights(campaigns: list[str], days: int = 90, min_impressions: int = 100) -> dict:
    """Changes nothing in Google Ads. History of the named Search campaigns: each keyword's Quality
    Score and its three parts (expected CTR, ad relevance, landing page experience); Google's labels
    (and results, when Google gives them) for responsive search ad headlines and descriptions; and the
    search terms report: converting terms that are not keywords yet (`keyword_ideas`) and terms that
    cost money without a conversion (`negative_keyword_ideas`). If Google refuses the headline and
    description results, they come with labels only and `asset_metrics_error` holds Google's answer
    (`asset_metrics` is then false). min_impressions is at least 100. Saves the report in a new local
    folder."""
    cfg, client = _ctx()
    return insights_search.build_search_insights(client, cfg, campaigns, days, min_impressions)


def video_insights(campaigns: list[str] | None = None, days: int = 90, min_impressions: int = 1000,
                   rank_by: str = "conv_per_1k", download_thumbnails: bool = True) -> dict:
    """Changes nothing in Google Ads. Ranks the YouTube videos of VIDEO, App and Demand Gen campaigns (all of
    them, or only the named campaigns) over the last `days` days, separately for each campaign type
    (`video_campaigns`, `app_campaigns`, `demand_gen_campaigns`), because their numbers aren't comparable.
    Each video has views (TrueView), view rate, CTR, conversions per 1,000 impressions, cost and its
    `currency`, campaigns, its YouTube ID and URL and its thumbnail URL. rank_by: conv_per_1k (the default),
    view_rate, ctr or views; only videos with at least min_impressions (at least 1,000) are ranked, and at most
    about half of them are winners. `scope` says whether all campaigns or only `campaigns` were read. Saves the
    report and the top and bottom thumbnails in a new local folder; LOOK at them with the Read tool before
    choosing videos."""
    cfg, client = _ctx()
    return insights_video.build_video_insights(client, cfg, campaigns, days, min_impressions, rank_by,
                                               download_thumbnails)


def check_landing_pages(urls: list[str], keywords: list[str]) -> list[dict]:
    """Changes nothing in Google Ads. Fetches each final URL's HTML with a phone browser's user agent (no
    JavaScript runs, so text a page adds with JavaScript shows as missing): https on the allowed domains
    in builder.toml only, on public internet addresses only (a VPN's fake-IP range 198.18.0.0/15 is also
    allowed, with a `note`), and a redirect is followed only while it stays there. At most 25 URLs and 50
    keywords per call; 20 seconds per page and 120 seconds per call. Reports the status, final URL, how
    long the HTML took to load, whether it declares a mobile viewport, its title, and which keywords
    appear in its visible text. Show the person every problem before drafting the plan."""
    return landing.check_landing_pages(urls, keywords, load_config())


def generate_image(prompt: str, shape: str, name: str) -> dict:
    """Local only (no Google Ads change). Makes ONE image through Codex (on macOS the ChatGPT app's) and fits it
    to landscape 1200x628, square 1200x1200 or portrait 1200x1500. Uses the ChatGPT Plus image allowance and
    needs a connection that reaches ChatGPT. LOOK at the result before using it. If Codex isn't installed, it
    says so: ask the person for image files instead."""
    return images.generate(prompt, shape, name, load_config())


def draft_plan(plan: dict) -> dict:
    """Saves a plan (kind: app_campaign | search_campaign | demand_gen_campaign | pmax_campaign |
    image_upload | asset_update) in a new folder and runs every local rule. Changes nothing in Google Ads. Show the
    returned summary to the person."""
    cfg = load_config()
    try:
        parsed = plans.plan_from_dict(plan)
    except plans.PlanError as e:
        return {"saved": False, "errors": [str(e)]}
    findings = plans.validate(parsed, cfg)
    path = plans.save_plan(parsed, cfg, findings)
    return {
        "saved": True,
        "plan_path": str(path),
        "fingerprint": plans.fingerprint(parsed)[:12],
        "errors": [f"{f.field}: {f.message}" for f in findings if f.level == "error"],
        "warnings": [f"{f.field}: {f.message}" for f in findings if f.level == "warning"],
        "summary": (path.parent / "summary.md").read_text(encoding="utf-8"),
    }


def check_plan(plan_path: str) -> dict:
    """Google validates the whole plan in check-only mode: NOTHING is created or changed.
    A pass is kept in this server's memory for exactly the request Google checked; the matching
    change tool needs it and uses it up. `approval` lists the arguments to pass to the change tool."""
    cfg, client = _ctx()
    try:
        plan, folder = _load(plan_path, cfg)
    except PlanPathError as e:
        return {"passed": False, "errors": [str(e)]}
    fingerprint = plans.fingerprint(plan)
    checks.forget_fingerprint(fingerprint)  # any earlier pass for this plan ends here, whatever happens next
    if not _summary_is_current(folder, plan, cfg):
        return {"passed": False, "errors": [PLAN_CHANGED]}
    try:
        checks.require_not_applied(folder, fingerprint)
        checks.require_not_declined(folder)
    except checks.CheckRequired as e:
        return {"passed": False, "errors": [str(e)]}

    def failed(errors: list[str], digest: str | None = None) -> dict:
        checks.record_check(folder, fingerprint, passed=False, customer_id=cfg.customer_id, digest=digest,
                            errors=errors)
        return {"passed": False, "errors": errors}

    problems, facts = _preflight(plan, cfg, client)
    if problems:
        return failed(problems)
    ops, labels = _compile(plan, client, cfg.customer_id, facts)
    problems = _guard(plan, ops, client, cfg, facts)
    if problems:
        return failed(problems)
    problems = _window_problem(_window_lines(_CHANGE_TOOL[plan.kind], plan, cfg, facts, fingerprint))
    if problems:
        return failed(problems)
    digest = gads.request_digest(cfg.customer_id, ops)
    try:
        gads.mutate(client, cfg.customer_id, ops, labels, validate_only=True)
    except gads.GoogleAdsError as e:
        return failed(e.messages, digest)
    if plans.fingerprint(plan) != fingerprint:
        return failed(["a file changed during the check; run check_plan again"], digest)
    checks.record_check(folder, fingerprint, passed=True, customer_id=cfg.customer_id, digest=digest)
    checks.remember_pass(digest, fingerprint, cfg.customer_id, folder)
    approval = {"customer_id": cfg.customer_id, "fingerprint": fingerprint[:12]}
    if isinstance(plan, plans.AssetUpdatePlan):  # the ad's campaign, read live
        approval["campaign_name"] = facts["campaign_name"]
        approval["campaign_status"] = facts["campaign_status"]
    approval.update(plans.approval_args(plan, cfg))
    return {"passed": True, "operations": len(ops), "fingerprint": fingerprint[:12], "approval": approval,
            "message": "Google accepted the whole plan in check-only mode. Nothing was created.",
            **_live_extra(facts)}


def quality_report(campaign: str) -> dict:
    """Read-only. Ad Strength, approval status and per-asset Best/Good/Low labels for a campaign. For a
    Demand Gen campaign, `demand_gen` also reads back its settings (upgraded targeting, location option,
    conversion goals, each ad group's channels, optimized targeting and demographic expansion, each ad's type
    and generated creatives); `to_review` lists every one that differs from what the builder sends (an app goal
    bid on, no goal bid on, a generated creative that is on or not opted out), with what to do in Google Ads.
    For a Performance Max campaign, `pmax` reads back brand guidelines, the asset automations (final URL
    expansion and the others the builder opts out of, and image extraction), the location option, the
    conversion goals, the brand assets, each asset group's Ad Strength, Google's suggestions and final URLs, and
    every asset Google added itself, with its own `to_review`. Show those to the person."""
    cfg, client = _ctx()
    return review.quality_report(client, cfg.customer_id, campaign)


# ------------------------------------------------------------------- changes


def _matches(want, given) -> bool:
    if isinstance(want, bool):  # before int: True == 1 in Python
        return type(given) is bool and given is want
    if isinstance(want, list):
        return type(given) is list and len(given) == len(want) and all(map(_matches, want, given))
    if isinstance(want, float):
        return (isinstance(given, (int, float)) and not isinstance(given, bool)
                and math.isfinite(given) and abs(given - want) < 0.005)
    if isinstance(want, int):
        return type(given) is int and given == want
    return type(given) is type(want) and given == want


def _mismatch(shown: dict, actual: dict, source: str) -> list[str]:
    for name, given in shown.items():
        if not _matches(actual[name], given):
            return [f"{name} does not match {source} ({actual[name]!r})"]
    return []


def _record_approval(folder: Path, tool: str, fingerprint: str, digest: str, approved: bool, lines: list[str]) -> None:
    """Best effort, like _record_attempt: the window's answer is what counts, not its record."""
    try:
        checks.record_approval(folder, tool=tool, fingerprint=fingerprint, digest=digest, approved=approved,
                               lines=confirm.shown_lines(lines))
    except Exception:
        pass


def _record_attempt(folder: Path, tool: str, fingerprint: str, digest: str, outcome: str, errors: list[str]) -> None:
    """Best effort: failing to write the audit record never changes what happens to the claim."""
    try:
        checks.record_attempt(folder, tool=tool, fingerprint=fingerprint, digest=digest, outcome=outcome, errors=errors)
    except Exception:
        pass


class _Request(NamedTuple):
    """A change request ready for the approval window: compiled once, and these ops are what is sent."""
    cfg: Any
    client: Any
    plan: Any
    folder: Path
    fingerprint: str
    facts: dict
    ops: list
    labels: list[str]
    lines: list[str]
    digest: str


def _apply(plan_path: str, *, kind: str | tuple[str, ...], tool: str, result_name: str, customer_id: str,
           shown_fingerprint: str, shown: dict, shown_live: dict | None = None) -> dict:
    """Every argument the person sees in Claude Code's approval prompt must match the configured
    account, the plan or (for the campaign of an asset update) the live account. Then the request is
    compiled once; the person approves it in the builder's own window; and only those exact
    operations are sent, once, if this server's check_plan passed on the same bytes."""
    if not confirm.window_supported():  # Linux and the like: no window can open, so refuse before any Google read
        return {"done": False, "errors": [confirm.UNSUPPORTED]}
    request = _prepare(plan_path, kind=kind, tool=tool, customer_id=customer_id, shown_fingerprint=shown_fingerprint,
                       shown=shown, shown_live=shown_live)
    if not isinstance(request, _Request):
        return {"done": False, "errors": request}
    errors = _ask(request, tool)
    if errors:
        return {"done": False, "errors": errors}
    return _send(request, tool, result_name)


def _prepare(plan_path: str, *, kind: str | tuple[str, ...], tool: str, customer_id: str, shown_fingerprint: str,
             shown: dict, shown_live: dict | None) -> _Request | list[str]:
    """Everything before the window: the shown arguments match, the plan and its summary are current and
    checked, the live facts hold, and the compiled request passes the guard and fits the window.
    Returns the request, or why it is refused."""
    cfg, client = _ctx()
    if not isinstance(customer_id, str) or customer_id.replace("-", "") != cfg.customer_id.replace("-", ""):
        return [f"customer_id does not match the configured account ({cfg.customer_id})"]
    try:
        plan, folder = _load(plan_path, cfg)
    except PlanPathError as e:
        return [str(e)]
    kinds = (kind,) if isinstance(kind, str) else kind
    if plan.kind not in kinds:
        return [f"this tool only accepts {' or '.join(kinds)} plans"]
    fingerprint = plans.fingerprint(plan)
    if not isinstance(shown_fingerprint, str) or len(shown_fingerprint) < 12 or not fingerprint.startswith(shown_fingerprint):
        return ["fingerprint does not match this plan; show the person the current summary and use its fingerprint"]
    problems = _mismatch(shown, plans.approval_args(plan, cfg), "the plan")
    if problems:
        return problems
    if not _summary_is_current(folder, plan, cfg):
        return [PLAN_CHANGED]
    try:
        checks.require_check(folder, fingerprint, cfg.check_max_age_hours, customer_id=cfg.customer_id)
    except checks.CheckRequired as e:
        return [str(e)]
    problems, facts = _preflight(plan, cfg, client)
    if not problems and shown_live:
        problems = _mismatch(shown_live, facts, "the account")
    if problems:
        return problems
    ops, labels = _compile(plan, client, cfg.customer_id, facts)  # compiled once; these objects are what is sent
    problems = _guard(plan, ops, client, cfg, facts)
    if problems:
        return problems
    lines = _window_lines(tool, plan, cfg, facts, fingerprint)
    problems = _window_problem(lines)
    if problems:
        return problems
    return _Request(cfg, client, plan, folder, fingerprint, facts, ops, labels, lines,
                    gads.request_digest(cfg.customer_id, ops))


def _ask(r: _Request, tool: str) -> list[str]:
    """The approval window, only for a request that would go ahead right now, and never over another
    open window. [] if the person approved; otherwise why not. Only the person's No is recorded and counted
    toward MAX_DECLINES: a window that couldn't start (or another one open) is neither."""
    with _APPLY_LOCK:
        try:
            checks.require_check(r.folder, r.fingerprint, r.cfg.check_max_age_hours,
                                 customer_id=r.cfg.customer_id, digest=r.digest)
            checks.peek_pass(r.digest, r.fingerprint, r.cfg.customer_id, r.folder, r.cfg.check_max_age_hours)
        except checks.CheckRequired as e:
            return [str(e)]
    if not _WINDOW_LOCK.acquire(blocking=False):  # never a second window over the first one
        return [WINDOW_OPEN]
    try:
        approved, unavailable = _person_approves(r.lines)  # outside _APPLY_LOCK: it can take up to a minute
    finally:
        _WINDOW_LOCK.release()
    if unavailable is not None:
        return [unavailable]
    _record_approval(r.folder, tool, r.fingerprint, r.digest, approved, r.lines)
    if approved:
        return []
    if checks.note_decline(r.folder, r.fingerprint):
        return [NOT_APPROVED, DECLINED_TOO_OFTEN]
    return [NOT_APPROVED]


def _send(r: _Request, tool: str, result_name: str) -> dict:
    """After Approve: the ad and campaign are still as shown, the pass is used up and the plan claimed,
    then the exact request is sent once, and the outcome recorded."""
    cfg, folder, fingerprint, digest = r.cfg, r.folder, r.fingerprint, r.digest
    extra = _live_extra(r.facts)
    with _APPLY_LOCK:
        problems = _still_as_shown(r.plan, cfg, r.client, r.facts)
        if problems:
            return {"done": False, "errors": problems}
        try:
            checks.require_check(folder, fingerprint, cfg.check_max_age_hours,
                                 customer_id=cfg.customer_id, digest=digest)
            checks.take_pass(digest, fingerprint, cfg.customer_id, folder, cfg.check_max_age_hours)
            checks.claim(folder, fingerprint=fingerprint, digest=digest, tool=tool)
        except checks.CheckRequired as e:
            return {"done": False, "errors": [str(e)]}
        try:
            response = gads.mutate(r.client, cfg.customer_id, r.ops, r.labels, validate_only=False)
        except gads.GoogleAdsError as e:
            if e.unknown_outcome:
                _record_attempt(folder, tool, fingerprint, digest, "unknown", e.messages)
                return {"done": False, "outcome": "unknown", "errors": e.messages, **extra}
            _record_attempt(folder, tool, fingerprint, digest, "refused", e.messages)
            try:
                checks.release_claim(folder)  # Google refused the whole request: nothing was applied
            except OSError:
                pass  # the claim stays and blocks this plan, which is safe
            return {"done": False, "errors": e.messages, **extra}
        except Exception as e:
            errors = ["Something failed during a REAL change, so the result is unknown. Look in the account "
                      f"before retrying. {type(e).__name__}: {e}"]
            _record_attempt(folder, tool, fingerprint, digest, "unknown", errors)
            return {"done": False, "outcome": "unknown", "errors": errors, **extra}
        out = {"done": True, "created": [], **extra}
        try:
            out["created"] = gads.result_names(response)
            checks.record_result(folder, result_name, {"fingerprint": fingerprint, "digest": digest,
                                                       "created": out["created"]})
        except Exception as e:  # the change was made; the claim keeps the plan from running again
            out["warnings"] = [f"The change was made, but its record could not be saved: {type(e).__name__}: {e}"]
    return out


def create_campaign_from_plan(plan_path: str, customer_id: str, fingerprint: str, campaign_name: str,
                              daily_budget: float, over_cap: bool) -> dict:
    """CHANGES THE ACCOUNT (the person approves first, in the builder's own approval window). Creates
    everything in an app_campaign, search_campaign, demand_gen_campaign or pmax_campaign plan in one
    all-or-nothing request. The campaign is ALWAYS created PAUSED. Pass customer_id, fingerprint, campaign_name, daily_budget and over_cap
    (true when the budget is above the private cap) exactly as check_plan's `approval` shows them, so
    the person sees them in Claude Code's prompt too; all must match the account and the plan. Refuses
    unless check_plan passed in this server session on exactly the request it sends. Single-use: never
    retry blindly; if `outcome` is "unknown", look in the account first."""
    return _apply(plan_path, kind=("app_campaign", "search_campaign", "demand_gen_campaign", "pmax_campaign"),
                  tool="create_campaign_from_plan",
                  result_name="create", customer_id=customer_id, shown_fingerprint=fingerprint,
                  shown={"campaign_name": campaign_name, "daily_budget": daily_budget, "over_cap": over_cap})


def upload_images(plan_path: str, customer_id: str, fingerprint: str, image_count: int) -> dict:
    """CHANGES THE ACCOUNT (the person approves first, in the builder's own approval window). Uploads
    an image_upload plan's images to the asset library (not attached to any ad). Pass customer_id,
    fingerprint and image_count (how many images) exactly as check_plan's `approval` shows them.
    Refuses unless check_plan passed in this server session on exactly the request it sends.
    Single-use: never retry blindly; if `outcome` is "unknown", look in the account first."""
    return _apply(plan_path, kind="image_upload", tool="upload_images", result_name="upload",
                  customer_id=customer_id, shown_fingerprint=fingerprint, shown={"image_count": image_count})


def update_campaign_assets(plan_path: str, customer_id: str, fingerprint: str, campaign_name: str,
                           campaign_status: str, added: list[str], removed: list[str]) -> dict:
    """CHANGES THE ACCOUNT (the person approves first, in the builder's own approval window). Adds or
    removes headlines, descriptions, images and videos on one existing App ad, or headlines and
    descriptions on one responsive search ad, per an asset_update plan. If the ad's campaign is
    ENABLED (running), the change takes effect IMMEDIATELY. Pass customer_id, fingerprint,
    campaign_name and campaign_status (the ad's campaign, read live from the account), added and
    removed (one line per item, such as "headline: <text>" or "image: <full path>", in order) exactly
    as check_plan's `approval` shows them. Refuses unless check_plan passed in this server session on
    exactly the request it sends, if the ad changed since drafting, or if a responsive search ad has
    pinned text. Single-use: never retry blindly; if `outcome` is "unknown", look in the account first."""
    return _apply(plan_path, kind="asset_update", tool="update_campaign_assets", result_name="update",
                  customer_id=customer_id, shown_fingerprint=fingerprint,
                  shown={"added": added, "removed": removed},
                  shown_live={"campaign_name": campaign_name, "campaign_status": campaign_status})


TOOLS = [
    playbook, describe_campaign, list_campaigns, find_locations, creative_insights, search_insights, video_insights,
    check_landing_pages, generate_image, draft_plan, check_plan, quality_report, create_campaign_from_plan,
    upload_images, update_campaign_assets,
]
TOOL_NAMES = [t.__name__ for t in TOOLS]
_CHANGE_TOOLS = {"create_campaign_from_plan", "upload_images", "update_campaign_assets"}
_NOT_READ_ONLY = {"draft_plan", "check_plan", "generate_image", "creative_insights", "search_insights",
                  "video_insights"}  # local files
for _tool in TOOLS:
    if _tool.__name__ in _CHANGE_TOOLS:
        _hints = {"readOnlyHint": False, "destructiveHint": True}
    elif _tool.__name__ in _NOT_READ_ONLY:
        _hints = {"readOnlyHint": False, "destructiveHint": False}
    else:
        _hints = {"readOnlyHint": True}
    mcp.tool(annotations=_hints)(_tool)


def main() -> None:
    mcp.run()
