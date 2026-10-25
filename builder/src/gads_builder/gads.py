"""Thin layer over the Google Ads client. Everything else calls Google through
these functions (as `gads.<name>`), which keeps tests able to swap them out."""

from __future__ import annotations

import hashlib
import math
import re
from functools import lru_cache

import google.api_core.exceptions as api_exceptions
import google.auth.exceptions as auth_exceptions
import grpc
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException


TIMEOUT = 120  # seconds, for every call to Google (mutate adds time for image data, see mutate_timeout)
MAX_MUTATE_TIMEOUT = 600
_MB = 1024 * 1024

TRANSPORT_ERRORS = (
    grpc.RpcError,
    auth_exceptions.TransportError,
    auth_exceptions.RefreshError,
    api_exceptions.GoogleAPICallError,
)


class GoogleAdsError(Exception):
    """unknown_outcome=True: a REAL change may or may not have been applied (look in the account).
    False: nothing was applied (a check-only call, or Google clearly refused the whole request).
    quota_exhausted=True: Google answered RESOURCE_EXHAUSTED for the daily API quota, not a network failure (not
    set for a message over gRPC's size limit, which has the same status)."""

    def __init__(self, messages: list[str], unknown_outcome: bool = False, quota_exhausted: bool = False):
        super().__init__("; ".join(messages))
        self.messages = messages
        self.unknown_outcome = unknown_outcome
        self.quota_exhausted = quota_exhausted


def request_digest(customer_id: str, operations: list) -> str:
    """sha256 of the exact request content: the customer ID, then every operation's deterministic
    wire bytes, each length-prefixed so no two different requests can hash the same input."""
    h = hashlib.sha256()

    def put(data: bytes) -> None:
        h.update(len(data).to_bytes(8, "big"))
        h.update(data)

    put(str(customer_id).encode("utf-8"))
    for op in operations:
        put(type(op).pb(op).SerializeToString(deterministic=True))
    return h.hexdigest()


def _full_detail(exc: BaseException) -> str:
    """The error's whole text on one line: a gRPC error's details, otherwise the exception's text."""
    text = ""
    details = getattr(exc, "details", None)
    if callable(details):
        try:
            text = str(details() or "")
        except Exception:
            text = ""
    text = text or str(exc) or type(exc).__name__
    return " ".join(text.split())


def _detail(exc: BaseException) -> str:
    """The first 200 characters of the error's text, for a message."""
    return _full_detail(exc)[:200]


QUOTA = "Google Ads refused the request: the account's daily Google Ads API quota is used up (RESOURCE_EXHAUSTED)."
TOO_LARGE = ("Google Ads couldn't carry the request: it or Google's answer is larger than the message size limit "
             "(RESOURCE_EXHAUSTED). This is not the daily API quota.")
_RETRY_IN = re.compile(r"[Rr]etry in\s*~?\s*([0-9]{1,9})\s*s")
# gRPC's own RESOURCE_EXHAUSTED for a message over its size limit ("Received message larger than max (N vs. M)",
# "Sent message larger than max ...")
_TOO_LARGE = re.compile(r"larger than (the )?max|message (is )?too large|too large to", re.IGNORECASE)


def _quota_exhausted(exc: BaseException) -> bool:
    """Google's 429 / RESOURCE_EXHAUSTED. The google-ads library never wraps this status in a GoogleAdsException:
    it raises the plain gRPC error (or google.api_core's ResourceExhausted), which would otherwise read as a
    network failure."""
    if isinstance(exc, api_exceptions.ResourceExhausted):
        return True
    code = getattr(exc, "code", None)
    if not callable(code):
        return False
    try:
        return code() == grpc.StatusCode.RESOURCE_EXHAUSTED
    except Exception:
        return False


def _retry_hint(exc: BaseException) -> str:
    """Google's retry delay from the error's whole text ("Retry in 62900 seconds"), when it gives one."""
    m = _RETRY_IN.search(_full_detail(exc))
    if not m:
        return "Try again after it resets (midnight Pacific time)."
    seconds = int(m.group(1))
    return f"Google says to retry in {seconds:,} seconds (about {seconds // 3600} h {seconds % 3600 // 60} min)."


def _quota_error(exc: BaseException, *, real_change: bool) -> GoogleAdsError:
    """RESOURCE_EXHAUSTED: the daily quota, or (when the text says so) a message over gRPC's size limit, which is
    not the quota. Either way Google's answer is quoted, so one can't pass for the other."""
    answer = f"Google's answer: {_detail(exc)}"
    if _TOO_LARGE.search(_full_detail(exc)):
        if real_change:
            return GoogleAdsError([f"{TOO_LARGE} It came during a REAL change, so the result is unknown. Look in the "
                                   f"account before retrying. {answer}"], unknown_outcome=True)
        return GoogleAdsError([f"{TOO_LARGE} Nothing was changed. Send less at once: fewer or smaller images, or for "
                               f"a report fewer campaigns or a shorter date range. {answer}"])
    if real_change:
        return GoogleAdsError([
            f"{QUOTA} {_retry_hint(exc)} It came during a REAL change: Google applies nothing it refuses for quota, "
            f"but this answer doesn't prove it, so the result is unknown. Look in the account before retrying. {answer}"
        ], unknown_outcome=True, quota_exhausted=True)
    return GoogleAdsError([f"{QUOTA} {_retry_hint(exc)} Nothing was changed, and the connection to Google Ads works. "
                           f"{answer}"], quota_exhausted=True)


def _unreachable(exc: BaseException, *, real_change: bool = False) -> GoogleAdsError:
    """A failure before Google answered with a GoogleAdsException: the daily quota (a plain RESOURCE_EXHAUSTED),
    or a network, sign-in or transport failure."""
    if _quota_exhausted(exc):
        return _quota_error(exc, real_change=real_change)
    if real_change:
        return GoogleAdsError([
            "Couldn't reach Google Ads during a REAL change, so the result is unknown. "
            f"Look in the account before retrying. {_detail(exc)}"
        ], unknown_outcome=True)
    return GoogleAdsError([f"Couldn't reach Google Ads (is the VPN on?): {_detail(exc)}"])


def not_a_refusal(e: GoogleAdsError) -> bool:
    """True when a search failed on the daily quota or the network: search reports a network failure as a
    GoogleAdsError too ("Couldn't reach Google Ads ..."). Neither is Google refusing the query, so a caller that
    works around a refusal raises these again."""
    return e.quota_exhausted or isinstance(e.__context__, TRANSPORT_ERRORS) or any(
        m.startswith("Couldn't reach Google Ads") for m in e.messages)


# gRPC statuses that do not prove the request was refused as a whole.
_AMBIGUOUS_STATUS = {"UNKNOWN", "INTERNAL", "UNAVAILABLE", "DEADLINE_EXCEEDED", "ABORTED", "CANCELLED", "DATA_LOSS"}


def _status_name(exc) -> str | None:
    code = getattr(getattr(exc, "error", None), "code", None)
    if not callable(code):
        return None
    try:
        return getattr(code(), "name", None)
    except Exception:
        return None


def _surely_refused(exc) -> bool:
    """True only when Google clearly refused the whole (atomic) request, so nothing was applied:
    a readable, unambiguous gRPC status and no internal error in the failure details."""
    status = _status_name(exc)
    if status is None or status in _AMBIGUOUS_STATUS:
        return False
    try:
        return not any(_code_kind(err) == "internal_error" for err in exc.failure.errors)
    except Exception:
        return False


@lru_cache(maxsize=2)
def client_from(credentials: str) -> GoogleAdsClient:
    try:
        client = GoogleAdsClient.load_from_storage(credentials)
    except TRANSPORT_ERRORS as e:
        raise _unreachable(e) from None
    if not getattr(client, "use_proto_plus", False):
        raise GoogleAdsError(["google-ads.yaml must set use_proto_plus: True"])
    return client


def _code_kind(err) -> str:
    """The error_code oneof that is set, such as "internal_error" or "string_length_error"."""
    try:
        code = err.error_code
        return type(code).pb(code).WhichOneof("error_code") or ""
    except Exception:
        return ""


def _code_name(err) -> str:
    try:
        which = _code_kind(err)
        if which:
            return getattr(err.error_code, which).name
    except Exception:
        pass
    return ""


def _has(element, field: str) -> bool:
    try:
        return field in element
    except TypeError:
        return getattr(element, field, None) is not None


# What to do next, for errors whose message from Google doesn't say it.
_HINTS = {
    "YOUTUBE_VIDEO_TOO_SHORT": "Choose a longer video: Performance Max takes YouTube videos of 10 seconds or more.",
}


def explain(exc, labels: list[str] | None) -> list[str]:
    """Turn a GoogleAdsException into readable lines, naming the plan part that failed."""
    request_id = getattr(exc, "request_id", None)
    suffix = f" (request {request_id})" if request_id else ""
    out = []
    for err in exc.failure.errors:
        location = getattr(err, "location", None)
        path = list(location.field_path_elements) if location is not None else []
        where = ""
        if labels and path and path[0].field_name == "mutate_operations":
            if _has(path[0], "index"):
                idx = path[0].index
                if 0 <= idx < len(labels):
                    where = f"{labels[idx]}: "
            path = path[1:]
        detail = ".".join(
            f"{p.field_name}[{p.index}]" if _has(p, "index") else p.field_name for p in path
        )
        code = _code_name(err)
        message = err.message or code or "unspecified error"
        if code in _HINTS:
            message = f"{message} {_HINTS[code]}"
        out.append(f"{where}{message}" + (f" ({detail})" if detail else ""))
    if not out:
        return [f"Google returned an error without details{suffix}"]
    out[-1] += suffix
    return out


def search(client, customer_id: str, query: str) -> list:
    service = client.get_service("GoogleAdsService")
    try:
        return list(service.search(customer_id=customer_id, query=query, timeout=TIMEOUT))
    except GoogleAdsException as e:
        raise GoogleAdsError(explain(e, None)) from None
    except TRANSPORT_ERRORS as e:
        raise _unreachable(e) from None


def suggest_locations(client, names: list[str], country_code: str, locale: str = "en") -> list:
    """Google's location suggestions (GeoTargetConstantService) for place names."""
    service = client.get_service("GeoTargetConstantService")
    request = client.get_type("SuggestGeoTargetConstantsRequest")
    request.locale = locale
    request.country_code = country_code
    request.location_names.names.extend(names)
    try:
        response = service.suggest_geo_target_constants(request=request, timeout=TIMEOUT)
    except GoogleAdsException as e:
        raise GoogleAdsError(explain(e, None)) from None
    except TRANSPORT_ERRORS as e:
        raise _unreachable(e) from None
    return list(response.geo_target_constant_suggestions)


def account_currency(client, customer_id: str) -> str:
    rows = search(client, customer_id, "SELECT customer.currency_code FROM customer LIMIT 1")
    if not rows:
        raise GoogleAdsError(["the account returned no customer row"])
    return rows[0].customer.currency_code


def customer_goals(client, customer_id: str) -> tuple[tuple[str, str], ...]:
    """The account's conversion goals as sorted (category, origin) names. A new Demand Gen campaign bids on
    every biddable one unless the campaign overrides it."""
    rows = search(client, customer_id, "SELECT customer_conversion_goal.category, customer_conversion_goal.origin "
                                       "FROM customer_conversion_goal")
    return tuple(sorted({(r.customer_conversion_goal.category.name, r.customer_conversion_goal.origin.name)
                         for r in rows}))


def demand_gen_minimum_micros(client, customer_id: str, currency: str) -> int:
    """Google's smallest daily budget for a Demand Gen campaign in `currency`, in micros. Fails closed."""
    if not re.fullmatch(r"[A-Z]{3}", currency or ""):
        raise GoogleAdsError([f"not a currency code: {currency!r}"])
    rows = search(client, customer_id, "SELECT currency_constant.code, "
                                       "currency_constant.demand_gen_info.minimum_budget_per_day_micros "
                                       f"FROM currency_constant WHERE currency_constant.code = '{currency}'")
    info = [type(r.currency_constant).pb(r.currency_constant).demand_gen_info for r in rows]
    if len(info) != 1 or not info[0].HasField("minimum_budget_per_day_micros"):
        raise GoogleAdsError([f"Google gave no Demand Gen minimum daily budget for {currency}"])
    return info[0].minimum_budget_per_day_micros


def mutate_timeout(operations: list) -> int:
    """TIMEOUT plus 30 s per started MB of new image data, at most MAX_MUTATE_TIMEOUT. Reads only."""
    size = 0
    for op in operations:
        pb = type(op).pb(op)
        if pb.WhichOneof("operation") == "asset_operation" and pb.asset_operation.WhichOneof("operation") == "create":
            size += len(pb.asset_operation.create.image_asset.data)
    return min(TIMEOUT + 30 * math.ceil(size / _MB), MAX_MUTATE_TIMEOUT)


def mutate(client, customer_id: str, operations: list, labels: list[str], *, validate_only: bool):
    """One all-or-nothing request. validate_only=True asks Google to check without changing anything."""
    if validate_only is not True and validate_only is not False:
        raise TypeError("validate_only must be exactly True or False")
    timeout = mutate_timeout(operations)
    service = client.get_service("GoogleAdsService")
    request = client.get_type("MutateGoogleAdsRequest")
    request.customer_id = customer_id
    request.mutate_operations.extend(operations)
    request.partial_failure = False
    request.validate_only = validate_only
    try:
        return service.mutate(request=request, timeout=timeout)
    except GoogleAdsException as e:
        messages = explain(e, labels)
        if not validate_only and not _surely_refused(e):
            raise GoogleAdsError([
                "Google returned an error during a REAL change that does not prove nothing was applied, "
                "so the result is unknown. Look in the account before retrying.",
                *messages,
            ], unknown_outcome=True) from None
        raise GoogleAdsError(messages) from None
    except TRANSPORT_ERRORS as e:
        raise _unreachable(e, real_change=not validate_only) from None


def result_names(response) -> list[str]:
    names = []
    for item in response.mutate_operation_responses:
        pb = type(item).pb(item)
        which = pb.WhichOneof("response")
        if which:
            names.append(getattr(pb, which).resource_name)
    return names
