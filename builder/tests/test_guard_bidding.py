"""The guard re-checks the bidding against the plan for every campaign kind: the strategy, its targets and
ceilings (a Performance Max target ROAS too), and a Search campaign's manual ad group bids."""

import pytest

from gads_builder import server
from test_allowlists import _creates, build, guard  # noqa: F401  (build: a fixture)

BIDDING = "internal safety check: the bidding differs from the plan"


def _strategy(member, **values):
    """Switches the campaign to another bidding strategy (a oneof), with these values."""
    def edit(r, E):
        cpb = _creates(r, "campaign_operation")
        getattr(cpb, member).SetInParent()
        for name, value in values.items():
            setattr(getattr(cpb, member), name, value)
    return edit


def _value(member, name, value):
    def edit(r, E):
        setattr(getattr(_creates(r, "campaign_operation"), member), name, value(E) if callable(value) else value)
    return edit


def _cleared(member, name):
    return lambda r, E: getattr(_creates(r, "campaign_operation"), member).ClearField(name)


def _group_bid(value, pick=0):
    def edit(r, E):
        group = _creates(r, "ad_group_operation", pick)
        if value is None:
            group.ClearField("cpc_bid_micros")
        else:
            group.cpc_bid_micros = value
    return edit


CASES = [
    # Demand Gen
    ("demand gen", _value("maximize_conversions", "target_cpa_micros", 1_000_000)),
    ("demand gen", _strategy("target_spend")),
    ("demand gen", _strategy("target_cpa", target_cpa_micros=1_500_000)),
    ("demand gen target CPA", _value("target_cpa", "target_cpa_micros", 1_400_000)),
    ("demand gen target CPA", _cleared("target_cpa", "target_cpa_micros")),
    ("demand gen target CPA", _strategy("maximize_conversions", target_cpa_micros=1_500_000)),
    ("demand gen clicks", _value("target_spend", "cpc_bid_ceiling_micros", 900_000)),
    ("demand gen clicks", _strategy("maximize_conversions")),
    # Performance Max
    ("pmax", _value("maximize_conversions", "target_cpa_micros", 1_000_000)),
    ("pmax", _strategy("maximize_conversion_value")),
    ("pmax", _strategy("target_cpa", target_cpa_micros=5_000_000)),
    ("pmax with a target", _value("maximize_conversions", "target_cpa_micros", 4_000_000)),
    ("pmax with a target", _cleared("maximize_conversions", "target_cpa_micros")),
    ("pmax value", _value("maximize_conversion_value", "target_roas", 2.0)),
    ("pmax value", _value("maximize_conversion_value", "target_roas", -0.0)),  # == 0.0, but sent
    ("pmax value", _strategy("maximize_conversions")),
    ("pmax value with a target", _value("maximize_conversion_value", "target_roas", 3.6)),
    ("pmax value with a target", _cleared("maximize_conversion_value", "target_roas")),
    ("pmax value with a target", _strategy("target_roas", target_roas=3.5)),
    # Search
    ("search", _value("maximize_conversions", "target_cpa_micros", 5_000_000)),
    ("search", _strategy("manual_cpc")),
    ("search", _group_bid(1_000_000)),
    ("search with a target", _value("maximize_conversions", "target_cpa_micros", 4_000_000)),
    ("search target CPA", _value("target_cpa", "target_cpa_micros", 5_000_001)),
    ("search target CPA", _strategy("maximize_conversions", target_cpa_micros=5_000_000)),
    ("search clicks", _value("target_spend", "cpc_bid_ceiling_micros", 2_000_000)),
    ("search clicks", _cleared("target_spend", "cpc_bid_ceiling_micros")),
    ("search clicks", _strategy("manual_cpc")),
    ("search impression share", _value("target_impression_share", "location",
                                       lambda E: E.TargetImpressionShareLocationEnum.ABSOLUTE_TOP_OF_PAGE)),
    ("search impression share", _value("target_impression_share", "location_fraction_micros", 900_000)),
    ("search impression share", _value("target_impression_share", "cpc_bid_ceiling_micros", 9_000_000)),
    ("search impression share", _cleared("target_impression_share", "cpc_bid_ceiling_micros")),
    ("search manual", _group_bid(2_000_000)),
    ("search manual", _group_bid(None)),
    ("search manual", _strategy("target_spend", cpc_bid_ceiling_micros=1_500_000)),
    # App
    ("app", _value("target_cpa", "target_cpa_micros", 12_000_000)),
    ("app", _cleared("target_cpa", "target_cpa_micros")),
    ("app", _strategy("maximize_conversions")),
    ("app", lambda r, E: setattr(_creates(r, "campaign_operation").app_campaign_setting, "bidding_strategy_goal_type",
                                 E.AppCampaignBiddingStrategyGoalTypeEnum.OPTIMIZE_INSTALLS_TARGET_INSTALL_COST)),
    ("app engagement", _strategy("target_cpa", target_cpa_micros=12_500_000)),
    ("app engagement", _value("maximize_conversions", "target_cpa_micros", 12_500_000)),
]


@pytest.mark.parametrize("name, edit", CASES)
def test_bidding_that_differs_from_the_plan_is_refused(ads_client, build, name, edit):
    r = build(name)
    assert guard(ads_client, r) == []
    edit(r, ads_client.enums)
    assert guard(ads_client, r) == [BIDDING]
    assert BIDDING == server.BIDDING


def test_each_ad_groups_own_manual_bid_is_compared(ads_client, build, search_plan, search_cfg):
    from gads_builder import plans
    from gads_builder.compile_search import compile_search_campaign

    data = search_plan(bidding="MANUAL_CPC", max_cpc=1.5)
    data["ad_groups"].append(dict(data["ad_groups"][0], name="Yoga classes", cpc_bid=2.0))
    plan = plans.plan_from_dict(data)
    ops, _ = compile_search_campaign(plan, ads_client, "1234567890")
    assert server._guard(plan, ops, ads_client, search_cfg) == []
    second = [op.ad_group_operation.create for op in ops if "ad_group_operation" in op][1]
    second.cpc_bid_micros = 1_500_000  # the campaign's max CPC, not the ad group's own bid
    assert server._guard(plan, ops, ads_client, search_cfg) == [BIDDING]


def test_a_negative_zero_target_roas_is_sent_so_it_is_not_unset(ads_client, build):
    """target_roas is a plain double: 0.0 is unset and never sent, but -0.0 has its sign bit set, so protobuf lists
    and sends it although it equals 0.0. Where the plan has no target, the field must be absent."""
    r = build("pmax value")
    strategy = _creates(r, "campaign_operation").maximize_conversion_value
    strategy.target_roas = -0.0
    assert strategy.target_roas == 0.0 and [f.name for f, _ in strategy.ListFields()] == ["target_roas"]
    assert strategy.SerializeToString() != b""
    assert guard(ads_client, r) == [BIDDING]


@pytest.mark.parametrize("member, name, value, want, ok", [
    ("maximize_conversion_value", "target_roas", None, 0.0, True),  # unset
    ("maximize_conversion_value", "target_roas", -0.0, 0.0, False),
    ("maximize_conversion_value", "target_roas", 3.5, 3.5, True),
    ("maximize_conversion_value", "target_roas", -0.0, 3.5, False),
    ("maximize_conversions", "target_cpa_micros", None, 0, True),
    ("maximize_conversions", "target_cpa_micros", 1, 0, False),
    ("maximize_conversions", "target_cpa_micros", 5_000_000, 5_000_000, True),
    ("target_cpa", "target_cpa_micros", None, None, True),  # optional: unset is None
    ("target_cpa", "target_cpa_micros", 0, None, False),  # optional: an explicit 0 is sent
    ("target_cpa", "target_cpa_micros", 0, 0, True),
])
def test_one_bidding_field_compared_with_the_plan(ads_client, member, name, value, want, ok):
    """The one helper every campaign kind's bidding check uses (App, Search, Demand Gen, Performance Max)."""
    campaign = type(ads_client.get_type("Campaign")).pb()()
    strategy = getattr(campaign, member)
    strategy.SetInParent()
    if value is not None:
        setattr(strategy, name, value)
    assert server._bidding_field_matches(strategy, name, want) is ok
