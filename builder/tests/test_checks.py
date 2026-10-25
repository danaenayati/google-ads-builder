import json
import re
import time

import pytest

from gads_builder import checks


def test_requires_a_record(tmp_path):
    with pytest.raises(checks.CheckRequired, match="check_plan"):
        checks.require_check(tmp_path, "abc", 24)


def test_passed_record_with_same_fingerprint(tmp_path):
    checks.record_check(tmp_path, "abc", passed=True)
    checks.require_check(tmp_path, "abc", 24)


def test_changed_plan_is_refused(tmp_path):
    checks.record_check(tmp_path, "abc", passed=True)
    with pytest.raises(checks.CheckRequired, match="changed"):
        checks.require_check(tmp_path, "def", 24)


def test_failed_check_is_refused(tmp_path):
    checks.record_check(tmp_path, "abc", passed=False)
    with pytest.raises(checks.CheckRequired, match="did not pass"):
        checks.require_check(tmp_path, "abc", 24)


def test_old_check_is_refused(tmp_path, monkeypatch):
    checks.record_check(tmp_path, "abc", passed=True)
    real = time.time()
    monkeypatch.setattr(checks.time, "time", lambda: real + 25 * 3600)
    with pytest.raises(checks.CheckRequired, match="too old") as refused:
        checks.require_check(tmp_path, "abc", 24)
    assert str(refused.value) == checks.TOO_OLD  # the same message as an expired pass in memory


def test_record_result(tmp_path):
    checks.record_result(tmp_path, "create", {"created": ["x"]})
    assert json.loads((tmp_path / "result-create.json").read_text(encoding="utf-8"))["created"] == ["x"]


def test_refused_after_a_result_with_the_same_fingerprint(tmp_path):
    checks.record_check(tmp_path, "abc", passed=True)
    checks.require_check(tmp_path, "abc", 24)
    checks.record_result(tmp_path, "create", {"fingerprint": "abc", "created": ["x"]})
    with pytest.raises(checks.CheckRequired, match="already applied. Draft a new plan"):
        checks.require_check(tmp_path, "abc", 24)


def test_result_for_another_fingerprint_does_not_block(tmp_path):
    checks.record_check(tmp_path, "abc", passed=True)
    checks.record_result(tmp_path, "create", {"fingerprint": "zzz"})
    checks.require_check(tmp_path, "abc", 24)


def test_future_timestamp_is_refused(tmp_path, monkeypatch):
    checks.record_check(tmp_path, "abc", passed=True)
    real = time.time()
    monkeypatch.setattr(checks.time, "time", lambda: real - 3600)
    with pytest.raises(checks.CheckRequired, match="future"):
        checks.require_check(tmp_path, "abc", 24)
    monkeypatch.setattr(checks.time, "time", lambda: real - 200)  # within the 300 s skew
    checks.require_check(tmp_path, "abc", 24)


@pytest.mark.parametrize("text", ["{not json", "[]", '{"fingerprint": "abc", "passed": true, "checked_at": "soon"}', ""])
def test_corrupt_check_file_is_refused(tmp_path, text):
    (tmp_path / checks.CHECK_FILE).write_text(text, encoding="utf-8")
    with pytest.raises(checks.CheckRequired, match="unreadable"):
        checks.require_check(tmp_path, "abc", 24)


def test_passed_must_be_exactly_true(tmp_path):
    record = {"fingerprint": "abc", "checked_at": time.time(), "passed": "yes"}
    (tmp_path / checks.CHECK_FILE).write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(checks.CheckRequired, match="did not pass"):
        checks.require_check(tmp_path, "abc", 24)


def test_customer_mismatch_is_refused(tmp_path):
    checks.record_check(tmp_path, "abc", passed=True, customer_id="111")
    checks.require_check(tmp_path, "abc", 24, customer_id="111")
    checks.require_check(tmp_path, "abc", 24)
    with pytest.raises(checks.CheckRequired, match="different account"):
        checks.require_check(tmp_path, "abc", 24, customer_id="222")


def test_checks_log_grows_and_check_file_is_last_record(tmp_path):
    checks.record_check(tmp_path, "abc", passed=False, customer_id="111")
    checks.record_check(tmp_path, "abc", passed=True, customer_id="111")
    lines = (tmp_path / checks.CHECK_LOG).read_text(encoding="utf-8").splitlines()
    assert [json.loads(l)["passed"] for l in lines] == [False, True]
    assert json.loads((tmp_path / checks.CHECK_FILE).read_text(encoding="utf-8")) == json.loads(lines[-1])


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "true", "1e400", "null"])
def test_non_finite_or_non_numeric_checked_at_is_refused(tmp_path, value):
    text = '{"fingerprint": "abc", "passed": true, "checked_at": %s}' % value
    (tmp_path / checks.CHECK_FILE).write_text(text, encoding="utf-8")
    with pytest.raises(checks.CheckRequired, match="unreadable"):
        checks.require_check(tmp_path, "abc", 24)


def test_record_without_an_account_is_refused_when_one_is_asked_for(tmp_path):
    checks.record_check(tmp_path, "abc", passed=True)
    checks.require_check(tmp_path, "abc", 24)
    with pytest.raises(checks.CheckRequired, match="account"):
        checks.require_check(tmp_path, "abc", 24, customer_id="111")


def test_recorded_digest_must_match_when_given(tmp_path):
    checks.record_check(tmp_path, "abc", passed=True, customer_id="111", digest="d1")
    assert json.loads((tmp_path / checks.CHECK_FILE).read_text(encoding="utf-8"))["digest"] == "d1"
    checks.require_check(tmp_path, "abc", 24, customer_id="111", digest="d1")
    with pytest.raises(checks.CheckRequired, match="differs from what Google checked"):
        checks.require_check(tmp_path, "abc", 24, customer_id="111", digest="d2")
    checks.record_check(tmp_path, "abc", passed=True, customer_id="111")  # no digest on record
    with pytest.raises(checks.CheckRequired, match="differs from what Google checked"):
        checks.require_check(tmp_path, "abc", 24, customer_id="111", digest="d1")


# ------------------------------------------------------------ passed checks in memory


def test_a_pass_in_memory_is_single_use(tmp_path):
    checks.remember_pass("d1", "fp", "111", tmp_path)
    checks.take_pass("d1", "fp", "111", tmp_path, 24)
    with pytest.raises(checks.CheckRequired, match="Run check_plan again"):
        checks.take_pass("d1", "fp", "111", tmp_path, 24)


def test_no_pass_in_memory_means_checking_again(tmp_path):
    checks.record_check(tmp_path, "fp", passed=True, customer_id="111", digest="d1")  # disk alone is not enough
    with pytest.raises(checks.CheckRequired, match="Run check_plan again"):
        checks.take_pass("d1", "fp", "111", tmp_path, 24)


def test_a_pass_for_another_request_of_the_same_plan_differs(tmp_path):
    checks.remember_pass("d1", "fp", "111", tmp_path)
    with pytest.raises(checks.CheckRequired, match="differs from what Google checked"):
        checks.take_pass("d2", "fp", "111", tmp_path, 24)
    checks.take_pass("d1", "fp", "111", tmp_path, 24)  # a refusal does not use the pass up


def test_a_pass_must_match_plan_account_and_folder(tmp_path):
    other = tmp_path / "other"
    checks.remember_pass("d1", "fp", "111", tmp_path)
    with pytest.raises(checks.CheckRequired, match="Run check_plan again"):
        checks.take_pass("d1", "fp2", "111", tmp_path, 24)
    with pytest.raises(checks.CheckRequired, match="different account"):
        checks.take_pass("d1", "fp", "222", tmp_path, 24)
    with pytest.raises(checks.CheckRequired, match="Run check_plan again"):
        checks.take_pass("d1", "fp", "111", other, 24)
    checks.take_pass("d1", "fp", "111", tmp_path, 24)


def test_forgetting_a_fingerprint_removes_its_passes(tmp_path):
    checks.remember_pass("d1", "fp", "111", tmp_path)
    checks.remember_pass("d2", "other", "111", tmp_path / "b")
    checks.forget_fingerprint("fp")
    with pytest.raises(checks.CheckRequired, match="Run check_plan again"):
        checks.take_pass("d1", "fp", "111", tmp_path, 24)
    checks.take_pass("d2", "other", "111", tmp_path / "b", 24)


def test_an_old_pass_in_memory_is_refused(tmp_path, monkeypatch):
    checks.remember_pass("d1", "fp", "111", tmp_path)
    later = checks._clock() + 25 * 3600
    monkeypatch.setattr(checks, "_clock", lambda: later)
    with pytest.raises(checks.CheckRequired, match="too old"):
        checks.take_pass("d1", "fp", "111", tmp_path, 24)


# ------------------------------------------------------------ apply claims


def test_a_claim_is_exclusive_and_records_the_attempt(tmp_path):
    checks.claim(tmp_path, fingerprint="fp", digest="d1", tool="upload_images")
    data = json.loads((tmp_path / checks.CLAIM_FILE).read_text(encoding="utf-8"))
    assert data["fingerprint"] == "fp" and data["digest"] == "d1" and data["tool"] == "upload_images"
    assert isinstance(data["at"], float)
    with pytest.raises(checks.CheckRequired, match="may have reached Google. Check the account first"):
        checks.claim(tmp_path, fingerprint="fp", digest="d1", tool="upload_images")
    with pytest.raises(checks.CheckRequired, match="may have reached Google"):
        checks.require_not_applied(tmp_path, "fp")
    checks.record_result(tmp_path, "upload", {"fingerprint": "fp"})
    with pytest.raises(checks.CheckRequired, match="already applied. Draft a new plan"):
        checks.claim(tmp_path, fingerprint="fp", digest="d1", tool="upload_images")
    with pytest.raises(checks.CheckRequired, match="already applied"):
        checks.require_not_applied(tmp_path, "fp")


def test_a_claim_blocks_require_check(tmp_path):
    checks.record_check(tmp_path, "fp", passed=True)
    checks.claim(tmp_path, fingerprint="fp", digest="d1", tool="create_campaign_from_plan")
    with pytest.raises(checks.CheckRequired, match="may have reached Google"):
        checks.require_check(tmp_path, "fp", 24)


def test_an_unreadable_claim_still_blocks(tmp_path):
    (tmp_path / checks.CLAIM_FILE).write_text("{half", encoding="utf-8")
    with pytest.raises(checks.CheckRequired, match="may have reached Google"):
        checks.require_not_applied(tmp_path, "fp")


def test_a_released_claim_frees_the_plan(tmp_path):
    checks.claim(tmp_path, fingerprint="fp", digest="d1", tool="upload_images")
    checks.release_claim(tmp_path)
    assert not (tmp_path / checks.CLAIM_FILE).exists()
    checks.require_not_applied(tmp_path, "fp")
    checks.claim(tmp_path, fingerprint="fp", digest="d1", tool="upload_images")


# ------------------------------------------------------------ audit records


def test_a_failed_check_keeps_its_errors(tmp_path):
    checks.record_check(tmp_path, "abc", passed=False, customer_id="111", errors=["a: bad", "b: worse"])
    checks.record_check(tmp_path, "abc", passed=True, customer_id="111", digest="d1")
    first, second = [json.loads(l) for l in (tmp_path / checks.CHECK_LOG).read_text(encoding="utf-8").splitlines()]
    assert first["errors"] == ["a: bad", "b: worse"] and first["passed"] is False
    assert "errors" not in second


def test_attempt_records_are_unique_and_complete(tmp_path):
    a = checks.record_attempt(tmp_path, tool="upload_images", fingerprint="fp", digest="d1",
                              outcome="refused", errors=["x: no"])
    b = checks.record_attempt(tmp_path, tool="upload_images", fingerprint="fp", digest="d1",
                              outcome="unknown", errors=["y: ?"])
    assert a != b and re.fullmatch(r"attempt-\d{8}T\d{6}\.\d{6}Z(-\d+)?\.json", a.name), a.name
    data = json.loads(a.read_text(encoding="utf-8"))
    assert {k: data[k] for k in ("tool", "fingerprint", "digest", "outcome", "errors")} == {
        "tool": "upload_images", "fingerprint": "fp", "digest": "d1", "outcome": "refused", "errors": ["x: no"]}
    assert json.loads(b.read_text(encoding="utf-8"))["outcome"] == "unknown"
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([a.name, b.name])  # no temp files left
    checks.require_not_applied(tmp_path, "fp")  # an attempt is not a result


def test_attempt_records_never_overwrite(tmp_path, monkeypatch):
    class Frozen(checks.datetime):
        @classmethod
        def now(cls, tz=None):
            return checks.datetime(2026, 1, 2, 3, 4, 5, 6, tzinfo=tz)

    monkeypatch.setattr(checks, "datetime", Frozen)
    paths = [checks.record_attempt(tmp_path, tool="t", fingerprint="fp", digest="d", outcome="refused", errors=[str(i)])
             for i in range(3)]
    assert len(set(paths)) == 3
    assert [json.loads(p.read_text(encoding="utf-8"))["errors"] for p in paths] == [["0"], ["1"], ["2"]]


def test_peeking_at_a_pass_uses_nothing_up(tmp_path):
    checks.remember_pass("d1", "fp", "111", tmp_path)
    checks.peek_pass("d1", "fp", "111", tmp_path, 24)
    checks.peek_pass("d1", "fp", "111", tmp_path, 24)
    with pytest.raises(checks.CheckRequired, match="differs from what Google checked"):
        checks.peek_pass("d2", "fp", "111", tmp_path, 24)
    with pytest.raises(checks.CheckRequired, match="different account"):
        checks.peek_pass("d1", "fp", "222", tmp_path, 24)
    checks.take_pass("d1", "fp", "111", tmp_path, 24)  # still there to take
    with pytest.raises(checks.CheckRequired, match="Run check_plan again"):
        checks.peek_pass("d1", "fp", "111", tmp_path, 24)


def test_peeking_at_an_old_pass_is_refused(tmp_path, monkeypatch):
    checks.remember_pass("d1", "fp", "111", tmp_path)
    later = checks._clock() + 25 * 3600
    monkeypatch.setattr(checks, "_clock", lambda: later)
    with pytest.raises(checks.CheckRequired, match="too old"):
        checks.peek_pass("d1", "fp", "111", tmp_path, 24)
    with pytest.raises(checks.CheckRequired, match="too old"):
        checks.take_pass("d1", "fp", "111", tmp_path, 24)


# ------------------------------------------------------------------- declines


def _log(folder, *lines):
    with open(folder / checks.APPROVAL_LOG, "a", encoding="utf-8") as log:
        log.writelines(line + "\n" for line in lines)


def test_declines_are_counted_from_the_approval_log(tmp_path):
    assert checks.declines(tmp_path) == 0  # no log yet
    checks.record_approval(tmp_path, tool="t", fingerprint="f", digest="d", approved=False, lines=["x"])
    checks.record_approval(tmp_path, tool="t", fingerprint="f", digest="d", approved=True, lines=["x"])
    _log(tmp_path, "{not json", "[]", json.dumps({"outcome": "Declined"}), "")
    assert checks.declines(tmp_path) == 1
    checks.require_not_declined(tmp_path)
    checks.record_approval(tmp_path, tool="t", fingerprint="other", digest="e", approved=False, lines=["x"])
    _log(tmp_path, json.dumps({"outcome": "declined"}))
    assert checks.declines(tmp_path) == 3  # every No for this plan folder, whatever the fingerprint
    with pytest.raises(checks.CheckRequired, match="declined 3 times; draft a new plan"):
        checks.require_not_declined(tmp_path)


def test_the_third_decline_forgets_the_passes_and_a_new_check_does_not_reset_it(tmp_path):
    checks.remember_pass("d1", "fp", "1", tmp_path)
    for n in (1, 2):
        checks.record_approval(tmp_path, tool="t", fingerprint="fp", digest="d1", approved=False, lines=[])
        assert checks.note_decline(tmp_path, "fp") is False
        checks.forget_fingerprint("fp")  # what check_plan does first
        checks.remember_pass("d1", "fp", "1", tmp_path)
    checks.record_approval(tmp_path, tool="t", fingerprint="fp", digest="d1", approved=False, lines=[])
    assert checks.note_decline(tmp_path, "fp") is True
    with pytest.raises(checks.CheckRequired):
        checks.peek_pass("d1", "fp", "1", tmp_path, 24)


def test_without_a_readable_log_the_declines_are_counted_in_memory(tmp_path):
    (tmp_path / checks.APPROVAL_LOG).mkdir()
    assert [checks.note_decline(tmp_path, "fp") for _ in range(3)] == [False, False, True]
    assert checks.declines(tmp_path) == 3
    checks.clear_passes()  # a restart: memory is gone, and so is this count
    assert checks.declines(tmp_path) == 0
