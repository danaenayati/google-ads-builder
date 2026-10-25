from types import SimpleNamespace
from unittest import mock

import google.api_core.exceptions as api_exceptions
import google.auth.exceptions as auth_exceptions
import grpc
import pytest
from google.ads.googleads.errors import GoogleAdsException
from google.ads.googleads.v26.errors.types.errors import ErrorLocation
from google.ads.googleads.v26.services.services.google_ads_service import GoogleAdsServiceClient

from gads_builder import gads

FieldPath = ErrorLocation.FieldPathElement


def fake_exception(message, path):
    elements = [FieldPath(field_name=n, index=i) if i is not None else FieldPath(field_name=n) for n, i in path]
    error = SimpleNamespace(message=message, location=SimpleNamespace(field_path_elements=elements))
    return SimpleNamespace(failure=SimpleNamespace(errors=[error]))


def test_explain_maps_operation_index_to_label():
    exc = fake_exception(
        "Too long.",
        [("mutate_operations", 2), ("campaign_operation", None), ("create", None), ("name", None)],
    )
    assert gads.explain(exc, ["image a.png", "budget", "campaign"]) == [
        "campaign: Too long. (campaign_operation.create.name)"
    ]


def test_explain_without_location():
    exc = SimpleNamespace(failure=SimpleNamespace(errors=[SimpleNamespace(message="Denied.", location=None)]))
    assert gads.explain(exc, None) == ["Denied."]


def test_result_names(ads_client):
    response = ads_client.get_type("MutateGoogleAdsResponse")
    item = ads_client.get_type("MutateOperationResponse")
    item.campaign_result.resource_name = "customers/1234567890/campaigns/5"
    response.mutate_operation_responses.append(item)
    assert gads.result_names(response) == ["customers/1234567890/campaigns/5"]


def real_failure(ads_client, *errors):
    failure = ads_client.get_type("GoogleAdsFailure")
    for message, path in errors:
        err = ads_client.get_type("GoogleAdsError")
        err.message = message
        err.location.field_path_elements.extend(
            [FieldPath(field_name=n, index=i) if i is not None else FieldPath(field_name=n) for n, i in path]
        )
        failure.errors.append(err)
    return failure


def test_explain_first_operation_and_indexed_path(ads_client):
    failure = real_failure(
        ads_client,
        ("Bad headline.", [("mutate_operations", 0), ("ad_group_ad_operation", None),
                           ("create", None), ("ad", None), ("app_ad", None), ("headlines", 3)]),
    )
    exc = GoogleAdsException(None, None, failure, "req-1")
    assert gads.explain(exc, ["image a.png", "ad"]) == [
        "image a.png: Bad headline. (ad_group_ad_operation.create.ad.app_ad.headlines[3]) (request req-1)"
    ]


def test_explain_unset_index_is_not_operation_zero(ads_client):
    failure = real_failure(ads_client, ("Oops.", [("mutate_operations", None), ("x", None)]))
    exc = GoogleAdsException(None, None, failure, "r")
    assert gads.explain(exc, ["first"]) == ["Oops. (x) (request r)"]


def test_explain_empty_message_uses_error_code_name(ads_client):
    from google.ads.googleads.v26.errors.types.string_length_error import StringLengthErrorEnum

    failure = real_failure(ads_client, ("", [("mutate_operations", 1)]))
    failure.errors[0].error_code.string_length_error = StringLengthErrorEnum.StringLengthError.TOO_LONG
    exc = GoogleAdsException(None, None, failure, "r")
    assert gads.explain(exc, ["a", "b"]) == ["b: TOO_LONG (request r)"]


def test_explain_adds_a_hint_for_a_video_that_is_too_short(ads_client):
    from google.ads.googleads.v26.errors.types.asset_link_error import AssetLinkErrorEnum

    google_says = "The YouTube video referenced in the provided asset is too short for the field submitted."
    failure = real_failure(ads_client, (google_says, [("mutate_operations", 1), ("asset_group_asset_operation", None),
                                                     ("create", None), ("asset", None)]))
    failure.errors[0].error_code.asset_link_error = AssetLinkErrorEnum.AssetLinkError.YOUTUBE_VIDEO_TOO_SHORT
    exc = GoogleAdsException(None, None, failure, "r")
    assert gads.explain(exc, ["budget", "asset group[0] video[0]"]) == [
        f"asset group[0] video[0]: {google_says} Choose a longer video: Performance Max takes YouTube videos of"
        " 10 seconds or more. (asset_group_asset_operation.create.asset) (request r)"
    ]


def test_explain_no_hint_for_other_errors(ads_client):
    from google.ads.googleads.v26.errors.types.string_length_error import StringLengthErrorEnum

    failure = real_failure(ads_client, ("Too long.", [("mutate_operations", 0)]))
    failure.errors[0].error_code.string_length_error = StringLengthErrorEnum.StringLengthError.TOO_LONG
    exc = GoogleAdsException(None, None, failure, "r")
    assert gads.explain(exc, ["a"]) == ["a: Too long. (request r)"]


def test_explain_no_errors(ads_client):
    exc = GoogleAdsException(None, None, ads_client.get_type("GoogleAdsFailure"), "req-9")
    assert gads.explain(exc, None) == ["Google returned an error without details (request req-9)"]


@pytest.fixture
def service(ads_client, monkeypatch):
    svc = mock.create_autospec(GoogleAdsServiceClient, instance=True)
    monkeypatch.setattr(ads_client, "get_service", lambda name, *a, **k: svc)
    return svc


def an_op(ads_client):
    op = ads_client.get_type("MutateOperation")
    op.campaign_budget_operation.create.name = "b"
    return op


@pytest.mark.parametrize("flag", [True, False])
def test_mutate_builds_request_with_flags(ads_client, service, flag):
    gads.mutate(ads_client, "1234567890", [an_op(ads_client)], ["x"], validate_only=flag)
    kwargs = service.mutate.call_args.kwargs
    request = kwargs["request"]
    assert request.validate_only is flag
    assert request.partial_failure is False
    assert request.customer_id == "1234567890"
    assert len(request.mutate_operations) == 1
    assert kwargs["timeout"] == gads.TIMEOUT
    assert set(kwargs) == {"request", "timeout"}


@pytest.mark.parametrize("bad", [None, 1, "yes", 0])
def test_mutate_requires_a_real_bool(ads_client, service, bad):
    with pytest.raises(TypeError):
        gads.mutate(ads_client, "1", [], [], validate_only=bad)
    service.mutate.assert_not_called()


def test_mutate_validate_only_is_keyword_only(ads_client, service):
    with pytest.raises(TypeError):
        gads.mutate(ads_client, "1", [], [], True)


def test_mutate_google_error_becomes_readable(ads_client, service):
    failure = real_failure(ads_client, ("Too long.", [("mutate_operations", 0), ("name", None)]))
    service.mutate.side_effect = GoogleAdsException(None, None, failure, "req-1")
    with pytest.raises(gads.GoogleAdsError) as info:
        gads.mutate(ads_client, "1", [an_op(ads_client)], ["budget"], validate_only=True)
    assert info.value.messages == ["budget: Too long. (name) (request req-1)"]


TRANSPORT = [
    grpc.RpcError("connection reset"),
    auth_exceptions.TransportError("no route"),
    auth_exceptions.RefreshError("token expired"),
    api_exceptions.ServiceUnavailable("unavailable"),
]


@pytest.mark.parametrize("exc", TRANSPORT, ids=lambda e: type(e).__name__)
def test_check_transport_failure_is_translated(ads_client, service, exc):
    service.mutate.side_effect = exc
    with pytest.raises(gads.GoogleAdsError, match=r"Couldn't reach Google Ads \(is the VPN on\?\)") as info:
        gads.mutate(ads_client, "1", [], [], validate_only=True)
    assert info.value.unknown_outcome is False


@pytest.mark.parametrize("exc", TRANSPORT, ids=lambda e: type(e).__name__)
def test_real_change_transport_failure_says_result_unknown(ads_client, service, exc):
    service.mutate.side_effect = exc
    with pytest.raises(gads.GoogleAdsError, match="REAL change, so the result is unknown. Look in the account before retrying") as info:
        gads.mutate(ads_client, "1", [], [], validate_only=False)
    assert info.value.unknown_outcome is True


def rpc_status(code):
    return SimpleNamespace(code=lambda: code)


def test_google_rejection_of_a_real_change_is_a_known_outcome(ads_client, service):
    failure = real_failure(ads_client, ("Too long.", [("mutate_operations", 0), ("name", None)]))
    service.mutate.side_effect = GoogleAdsException(rpc_status(grpc.StatusCode.INVALID_ARGUMENT), None, failure, "r")
    with pytest.raises(gads.GoogleAdsError) as info:
        gads.mutate(ads_client, "1", [an_op(ads_client)], ["budget"], validate_only=False)
    assert info.value.unknown_outcome is False
    assert info.value.messages == ["budget: Too long. (name) (request r)"]


def test_internal_error_during_a_real_change_is_an_unknown_outcome(ads_client, service):
    from google.ads.googleads.v26.errors.types.internal_error import InternalErrorEnum

    failure = real_failure(ads_client, ("Internal error.", []))
    failure.errors[0].error_code.internal_error = InternalErrorEnum.InternalError.TRANSIENT_ERROR
    service.mutate.side_effect = GoogleAdsException(rpc_status(grpc.StatusCode.INVALID_ARGUMENT), None, failure, "r")
    with pytest.raises(gads.GoogleAdsError, match="result is unknown") as info:
        gads.mutate(ads_client, "1", [an_op(ads_client)], ["budget"], validate_only=False)
    assert info.value.unknown_outcome is True
    assert "Internal error. (request r)" in info.value.messages
    with pytest.raises(gads.GoogleAdsError) as checked:  # in check-only mode nothing can have changed
        gads.mutate(ads_client, "1", [an_op(ads_client)], ["budget"], validate_only=True)
    assert checked.value.unknown_outcome is False


@pytest.mark.parametrize("status", [grpc.StatusCode.DEADLINE_EXCEEDED, grpc.StatusCode.UNAVAILABLE,
                                    grpc.StatusCode.UNKNOWN, grpc.StatusCode.ABORTED, None])
def test_ambiguous_status_during_a_real_change_is_an_unknown_outcome(ads_client, service, status):
    failure = real_failure(ads_client, ("Something.", []))
    error = rpc_status(status) if status else None
    service.mutate.side_effect = GoogleAdsException(error, None, failure, "r")
    with pytest.raises(gads.GoogleAdsError) as info:
        gads.mutate(ads_client, "1", [an_op(ads_client)], ["budget"], validate_only=False)
    assert info.value.unknown_outcome is True


def test_google_ads_error_defaults_to_a_known_outcome():
    assert gads.GoogleAdsError(["x"]).unknown_outcome is False
    assert gads.GoogleAdsError(["x"], unknown_outcome=True).unknown_outcome is True


def test_request_digest_covers_every_byte(ads_client):
    cid = "1234567890"

    def budget(name="b", micros=1_000_000):
        op = ads_client.get_type("MutateOperation")
        op.campaign_budget_operation.create.name = name
        op.campaign_budget_operation.create.amount_micros = micros
        return op

    def image(data):
        op = ads_client.get_type("MutateOperation")
        op.asset_operation.create.image_asset.data = data
        return op

    base = gads.request_digest(cid, [budget(), image(b"\x00\x01\x02")])
    assert len(base) == 64 and int(base, 16) >= 0
    assert gads.request_digest(cid, [budget(), image(b"\x00\x01\x02")]) == base  # deterministic
    variants = [
        gads.request_digest("1234567891", [budget(), image(b"\x00\x01\x02")]),  # another account
        gads.request_digest(cid, [budget("c"), image(b"\x00\x01\x02")]),  # one character
        gads.request_digest(cid, [budget(micros=1_000_001), image(b"\x00\x01\x02")]),  # one micro
        gads.request_digest(cid, [budget(), image(b"\x00\x01\x03")]),  # one image byte
        gads.request_digest(cid, [image(b"\x00\x01\x02"), budget()]),  # order
        gads.request_digest(cid, [budget()]),  # one op fewer
    ]
    assert len(set(variants)) == len(variants) and base not in variants
    empty = ads_client.get_type("MutateOperation")  # serializes to b"": only the length prefix tells
    assert gads.request_digest(cid, []) != gads.request_digest(cid, [empty])
    assert gads.request_digest(cid, [empty]) != gads.request_digest(cid, [empty, empty])


@pytest.mark.parametrize("exc", TRANSPORT, ids=lambda e: type(e).__name__)
def test_search_transport_failure_is_translated(ads_client, service, exc):
    service.search.side_effect = exc
    with pytest.raises(gads.GoogleAdsError, match="Couldn't reach Google Ads"):
        gads.search(ads_client, "1", "SELECT customer.id FROM customer")
    assert service.search.call_args.kwargs["timeout"] == gads.TIMEOUT


def test_account_currency_translates_and_handles_no_rows(ads_client, service):
    service.search.side_effect = auth_exceptions.RefreshError("expired")
    with pytest.raises(gads.GoogleAdsError, match="Couldn't reach"):
        gads.account_currency(ads_client, "1")
    service.search.side_effect = None
    service.search.return_value = []
    with pytest.raises(gads.GoogleAdsError, match="no customer row"):
        gads.account_currency(ads_client, "1")


def test_client_from_translates_and_checks_proto_plus(monkeypatch):
    gads.client_from.cache_clear()
    monkeypatch.setattr(gads.GoogleAdsClient, "load_from_storage",
                        staticmethod(lambda path: (_ for _ in ()).throw(auth_exceptions.TransportError("down"))))
    with pytest.raises(gads.GoogleAdsError, match="Couldn't reach"):
        gads.client_from("a.yaml")
    monkeypatch.setattr(gads.GoogleAdsClient, "load_from_storage",
                        staticmethod(lambda path: SimpleNamespace(use_proto_plus=False)))
    with pytest.raises(gads.GoogleAdsError, match="use_proto_plus"):
        gads.client_from("b.yaml")
    gads.client_from.cache_clear()


def test_offline_tests_cannot_open_sockets():
    import socket

    with pytest.raises(RuntimeError, match="network access in an offline test"):
        socket.create_connection(("127.0.0.1", 9))
    with pytest.raises(RuntimeError, match="network access in an offline test"):
        socket.socket().connect(("127.0.0.1", 9))
    a, b = socket.socketpair()  # asyncio needs it; on Windows Python builds it from a loopback connection
    with a, b:
        a.sendall(b"x")
        assert b.recv(1) == b"x"
    with pytest.raises(RuntimeError, match="network access in an offline test"):
        socket.socket().connect(("127.0.0.1", 9))  # and the guard is back right after


def test_offline_tests_cannot_open_grpc_channels(ads_client):
    with pytest.raises(RuntimeError, match="network access in an offline test"):
        ads_client.get_service("GoogleAdsService")
    with pytest.raises(RuntimeError, match="network access in an offline test"):
        grpc.insecure_channel("127.0.0.1:9")
    with pytest.raises(RuntimeError, match="network access in an offline test"):
        grpc.secure_channel("127.0.0.1:9", grpc.local_channel_credentials())


# ------------------------------------------------------------ location suggestions


@pytest.fixture
def geo_service(ads_client, monkeypatch):
    from google.ads.googleads.v26.services.services.geo_target_constant_service import GeoTargetConstantServiceClient

    svc = mock.create_autospec(GeoTargetConstantServiceClient, instance=True)
    asked = []
    monkeypatch.setattr(ads_client, "get_service", lambda name, *a, **k: asked.append(name) or svc)
    svc.asked = asked
    return svc


def test_suggest_locations_builds_the_request_with_the_timeout(ads_client, geo_service):
    response = ads_client.get_type("SuggestGeoTargetConstantsResponse")
    suggestion = ads_client.get_type("GeoTargetConstantSuggestion")
    suggestion.geo_target_constant.resource_name = "geoTargetConstants/1"
    response.geo_target_constant_suggestions.append(suggestion)
    geo_service.suggest_geo_target_constants.return_value = response
    found = gads.suggest_locations(ads_client, ["Springfield"], "XX")
    assert [s.geo_target_constant.resource_name for s in found] == ["geoTargetConstants/1"]
    assert geo_service.asked == ["GeoTargetConstantService"]
    kwargs = geo_service.suggest_geo_target_constants.call_args.kwargs
    assert set(kwargs) == {"request", "timeout"} and kwargs["timeout"] == gads.TIMEOUT
    request = kwargs["request"]
    assert list(request.location_names.names) == ["Springfield"]
    assert request.country_code == "XX" and request.locale == "en"


@pytest.mark.parametrize("exc", TRANSPORT, ids=lambda e: type(e).__name__)
def test_suggest_locations_transport_failure_is_translated(ads_client, geo_service, exc):
    geo_service.suggest_geo_target_constants.side_effect = exc
    with pytest.raises(gads.GoogleAdsError, match="Couldn't reach Google Ads"):
        gads.suggest_locations(ads_client, ["x"], "XX")


def test_suggest_locations_google_error_becomes_readable(ads_client, geo_service):
    failure = real_failure(ads_client, ("Bad country.", []))
    geo_service.suggest_geo_target_constants.side_effect = GoogleAdsException(None, None, failure, "r")
    with pytest.raises(gads.GoogleAdsError) as info:
        gads.suggest_locations(ads_client, ["x"], "XX")
    assert info.value.messages == ["Bad country. (request r)"]


# ------------------------------------------------------------ mutate timeout


def image_op(ads_client, size):
    op = ads_client.get_type("MutateOperation")
    op.asset_operation.create.image_asset.data = b"\x00" * size
    return op


MB = 1024 * 1024


@pytest.mark.parametrize("sizes, expected", [
    ([], 120), ([0], 120), ([1], 150), ([MB], 150), ([MB + 1], 180), ([MB, 1], 180),
    ([5 * MB, 5 * MB, 5 * MB], 570), ([15 * MB + 1], 600), ([20 * MB], 600),
])
def test_mutate_timeout_grows_with_image_data(ads_client, sizes, expected):
    ops = [an_op(ads_client), *(image_op(ads_client, n) for n in sizes)]
    before = [gads.request_digest("1", [op]) for op in ops]
    assert gads.mutate_timeout(ops) == expected
    assert [gads.request_digest("1", [op]) for op in ops] == before  # reading sizes changes nothing


def test_mutate_uses_the_scaled_timeout(ads_client, service):
    gads.mutate(ads_client, "1", [image_op(ads_client, MB + 1)], ["image"], validate_only=True)
    assert service.mutate.call_args.kwargs["timeout"] == 180
    gads.search(ads_client, "1", "SELECT customer.id FROM customer")
    assert service.search.call_args.kwargs["timeout"] == gads.TIMEOUT


# ------------------------------------------------------------------- Demand Gen reads


def _goal_row(ads_client, category, origin):
    r = ads_client.get_type("GoogleAdsRow")
    r.customer_conversion_goal.category = getattr(ads_client.enums.ConversionActionCategoryEnum, category)
    r.customer_conversion_goal.origin = getattr(ads_client.enums.ConversionOriginEnum, origin)
    return r


def test_customer_goals_are_sorted_unique_names(ads_client, monkeypatch):
    rows = [_goal_row(ads_client, *g) for g in [("PURCHASE", "WEBSITE"), ("DOWNLOAD", "APP"), ("PURCHASE", "WEBSITE")]]
    queries = []
    monkeypatch.setattr(gads, "search", lambda client, cid, query: queries.append(query) or rows)
    assert gads.customer_goals(ads_client, "1234567890") == (("DOWNLOAD", "APP"), ("PURCHASE", "WEBSITE"))
    assert queries == ["SELECT customer_conversion_goal.category, customer_conversion_goal.origin "
                       "FROM customer_conversion_goal"]


def _currency_row(ads_client, minimum=None):
    r = ads_client.get_type("GoogleAdsRow")
    r.currency_constant.code = "EUR"
    if minimum is not None:
        r.currency_constant.demand_gen_info.minimum_budget_per_day_micros = minimum
    return r


def test_the_demand_gen_minimum_budget(ads_client, monkeypatch):
    queries = []
    monkeypatch.setattr(gads, "search", lambda client, cid, query: queries.append(query) or [_currency_row(ads_client, 5_000_000)])
    assert gads.demand_gen_minimum_micros(ads_client, "1234567890", "EUR") == 5_000_000
    assert queries == ["SELECT currency_constant.code, currency_constant.demand_gen_info.minimum_budget_per_day_micros "
                       "FROM currency_constant WHERE currency_constant.code = 'EUR'"]


@pytest.mark.parametrize("currency, rows, message", [
    ("EUR", [], "Google gave no Demand Gen minimum daily budget for EUR"),
    ("EUR", ["no minimum"], "Google gave no Demand Gen minimum daily budget for EUR"),
    ("EUR", ["5", "5"], "Google gave no Demand Gen minimum daily budget for EUR"),
    ("eur", [], "not a currency code: 'eur'"),
    ("EUR' OR '1", [], "not a currency code: \"EUR' OR '1\""),
])
def test_no_demand_gen_minimum_fails_closed(ads_client, monkeypatch, currency, rows, message):
    answer = [_currency_row(ads_client, None if r == "no minimum" else 5_000_000) for r in rows]
    queries = []
    monkeypatch.setattr(gads, "search", lambda client, cid, query: queries.append(query) or answer)
    with pytest.raises(gads.GoogleAdsError) as e:
        gads.demand_gen_minimum_micros(ads_client, "1234567890", currency)
    assert e.value.messages == [message]
    assert all("OR '1" not in q for q in queries)


# ------------------------------------------------------------------- the daily quota


class QuotaRpcError(grpc.RpcError):
    """What the google-ads library raises for a 429: the plain gRPC error with RESOURCE_EXHAUSTED (its interceptor
    never wraps that status in a GoogleAdsException)."""

    def __init__(self, details: str):
        super().__init__(details)
        self._details = details

    def code(self):
        return grpc.StatusCode.RESOURCE_EXHAUSTED

    def details(self):
        return self._details


RETRY_TEXT = "Too many requests. Retry in 62900 seconds."
QUOTA_ERRORS = [QuotaRpcError(RETRY_TEXT), api_exceptions.ResourceExhausted(RETRY_TEXT)]
SAYS_RETRY = "Google says to retry in 62,900 seconds (about 17 h 28 min)."


def _answer(exc) -> str:
    """Google's answer as the message quotes it: the gRPC error's details, or google.api_core's text (with its HTTP
    status)."""
    return RETRY_TEXT if isinstance(exc, QuotaRpcError) else f"429 {RETRY_TEXT}"


@pytest.mark.parametrize("exc", QUOTA_ERRORS, ids=lambda e: type(e).__name__)
def test_search_says_the_daily_quota_is_used_up(ads_client, service, exc):
    service.search.side_effect = exc
    with pytest.raises(gads.GoogleAdsError) as info:
        gads.search(ads_client, "1", "SELECT customer.id FROM customer")
    assert info.value.messages == [f"{gads.QUOTA} {SAYS_RETRY} Nothing was changed, and the connection to Google Ads "
                                   f"works. Google's answer: {_answer(exc)}"]
    assert info.value.quota_exhausted is True and info.value.unknown_outcome is False
    assert "VPN" not in str(info.value) and "Couldn't reach" not in str(info.value)


def test_without_a_retry_delay_the_quota_message_says_when_it_resets(ads_client, service):
    service.search.side_effect = QuotaRpcError("Quota exceeded for quota metric 'Requests'")
    with pytest.raises(gads.GoogleAdsError) as info:
        gads.search(ads_client, "1", "SELECT customer.id FROM customer")
    assert info.value.messages == [f"{gads.QUOTA} Try again after it resets (midnight Pacific time). Nothing was "
                                   "changed, and the connection to Google Ads works. Google's answer: Quota exceeded "
                                   "for quota metric 'Requests'"]


@pytest.mark.parametrize("exc", QUOTA_ERRORS, ids=lambda e: type(e).__name__)
def test_a_check_says_the_daily_quota_is_used_up(ads_client, service, exc):
    service.mutate.side_effect = exc
    with pytest.raises(gads.GoogleAdsError) as info:
        gads.mutate(ads_client, "1", [], [], validate_only=True)
    assert info.value.messages[0].startswith(f"{gads.QUOTA} {SAYS_RETRY} Nothing was changed")
    assert info.value.unknown_outcome is False and info.value.quota_exhausted is True and "VPN" not in str(info.value)


@pytest.mark.parametrize("exc", QUOTA_ERRORS, ids=lambda e: type(e).__name__)
def test_the_quota_during_a_real_change_still_leaves_the_result_unknown(ads_client, service, exc):
    service.mutate.side_effect = exc
    with pytest.raises(gads.GoogleAdsError) as info:
        gads.mutate(ads_client, "1", [], [], validate_only=False)
    [message] = info.value.messages
    assert message.startswith(f"{gads.QUOTA} {SAYS_RETRY} It came during a REAL change")
    assert message.endswith(f"so the result is unknown. Look in the account before retrying. Google's answer: "
                            f"{_answer(exc)}")
    assert info.value.unknown_outcome is True and info.value.quota_exhausted is True and "VPN" not in message


def test_the_retry_delay_is_found_past_the_first_200_characters(ads_client, service):
    """The message shows at most 200 characters of Google's answer, but the delay is read from all of it."""
    long_text = "Quota exceeded for quota metric 'Requests' " * 6 + "Retry in 3600 seconds."
    assert long_text.index("Retry in") > 200
    service.search.side_effect = QuotaRpcError(long_text)
    with pytest.raises(gads.GoogleAdsError) as info:
        gads.search(ads_client, "1", "SELECT customer.id FROM customer")
    [message] = info.value.messages
    assert "Google says to retry in 3,600 seconds (about 1 h 0 min)." in message
    assert message.endswith(f"Google's answer: {long_text[:200]}") and "Retry in 3600" not in message


TOO_LARGE_TEXT = "Received message larger than max (5242880 vs. 4194304)"


@pytest.mark.parametrize("exc", [QuotaRpcError(TOO_LARGE_TEXT), api_exceptions.ResourceExhausted(TOO_LARGE_TEXT),
                                 QuotaRpcError("Sent message larger than max (31457280 vs. 4194304)")],
                         ids=["grpc received", "api_core", "grpc sent"])
def test_a_message_too_large_is_not_taken_for_the_daily_quota(ads_client, service, exc):
    """gRPC answers RESOURCE_EXHAUSTED for a message over its size limit too: the message says the request (or
    Google's answer) is too large, never that the quota is used up, and never the VPN."""
    service.search.side_effect = exc
    with pytest.raises(gads.GoogleAdsError) as info:
        gads.search(ads_client, "1", "SELECT customer.id FROM customer")
    [message] = info.value.messages
    assert message.startswith(f"{gads.TOO_LARGE} Nothing was changed.") and "larger than max (" in message
    assert gads.QUOTA not in message and "quota is used up" not in message and "VPN" not in message
    assert info.value.quota_exhausted is False and info.value.unknown_outcome is False
    service.mutate.side_effect = exc
    with pytest.raises(gads.GoogleAdsError) as info:
        gads.mutate(ads_client, "1", [], [], validate_only=True)
    assert info.value.messages[0].startswith(f"{gads.TOO_LARGE} Nothing was changed.")
    assert info.value.unknown_outcome is False
    with pytest.raises(gads.GoogleAdsError) as info:
        gads.mutate(ads_client, "1", [], [], validate_only=False)
    [message] = info.value.messages
    assert message.startswith(f"{gads.TOO_LARGE} It came during a REAL change") and "the result is unknown" in message
    assert info.value.unknown_outcome is True and info.value.quota_exhausted is False and "VPN" not in message


def test_other_grpc_failures_still_point_at_the_connection(ads_client, service):
    class Unavailable(QuotaRpcError):
        def code(self):
            return grpc.StatusCode.UNAVAILABLE

    service.search.side_effect = Unavailable("Stream removed")
    with pytest.raises(gads.GoogleAdsError, match=r"Couldn't reach Google Ads \(is the VPN on\?\)") as info:
        gads.search(ads_client, "1", "SELECT customer.id FROM customer")
    assert info.value.quota_exhausted is False
