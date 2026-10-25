"""Check-before-change gates. Every one of them fails closed.

- Memory: a passed check_plan lives only in this server process, keyed by the digest of the exact
  request Google validated. A change needs that entry and uses it up. A restart means checking again.
- Disk, per plan folder: check.json / checks.jsonl / approvals.jsonl / result-*.json / attempt-*.json
  are the audit trail (check.json is also an extra gate, never sufficient on its own). applying.json
  is the apply claim: created before the real request, it makes a plan single-use across restarts
  and processes."""

from __future__ import annotations

import contextlib
import json
import math
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from .plans import write_atomic

CHECK_FILE = "check.json"
CHECK_LOG = "checks.jsonl"
CLAIM_FILE = "applying.json"
APPROVAL_LOG = "approvals.jsonl"
MAX_DECLINES = 3  # this many No answers in the approval window refuse the plan for good
FUTURE_SKEW_SECONDS = 300

ALREADY_APPLIED = "This plan was already applied. Draft a new plan for another change."
MAYBE_APPLIED = (
    "A previous attempt may have reached Google. Check the account first; then draft a new plan if still needed."
)
DIFFERS = "The request differs from what Google checked. Run check_plan again."
DECLINED_FOR_GOOD = (f"This plan was declined {MAX_DECLINES} times; draft a new plan. Ask the person what they want "
                     "first.")
TOO_OLD = "The last check is too old. Run check_plan again."
NOT_CHECKED_HERE = (
    "This server has no passed check for exactly this plan (passes are kept in memory, end when the "
    "server restarts and are used up by a change). Run check_plan again."
)


class CheckRequired(Exception):
    pass


# ------------------------------------------------------------------ disk


def record_check(
    plan_dir: Path,
    fingerprint: str,
    *,
    passed: bool,
    customer_id: str | None = None,
    digest: str | None = None,
    errors: list[str] | None = None,
) -> None:
    record = {"fingerprint": fingerprint, "checked_at": time.time(), "passed": passed}
    if customer_id is not None:
        record["customer_id"] = customer_id
    if digest is not None:
        record["digest"] = digest
    if errors is not None:
        record["errors"] = list(errors)
    folder = Path(plan_dir)
    line = json.dumps(record)
    write_atomic(folder / CHECK_FILE, line + "\n")
    with open(folder / CHECK_LOG, "a", encoding="utf-8", newline="\n") as log:
        log.write(line + "\n")


def _result_fingerprints(folder: Path) -> set:
    found = set()
    for path in folder.glob("result-*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise CheckRequired(f"{path.name} is unreadable; look in the account before applying again.") from None
        if isinstance(data, dict) and isinstance(data.get("fingerprint"), str):
            found.add(data["fingerprint"])
    return found


def _claimed_fingerprint(path: Path) -> str | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = data.get("fingerprint") if isinstance(data, dict) else None
    return value if isinstance(value, str) else None


def require_not_applied(plan_dir: Path, fingerprint: str) -> None:
    """Refuses a plan that was applied, or whose earlier attempt may have reached Google."""
    folder = Path(plan_dir)
    claim_path = folder / CLAIM_FILE
    if os.path.lexists(claim_path):
        done = _result_fingerprints(folder)
        claimed = _claimed_fingerprint(claim_path)
        if fingerprint in done or (claimed is not None and claimed in done):
            raise CheckRequired(ALREADY_APPLIED)
        raise CheckRequired(MAYBE_APPLIED)
    if fingerprint in _result_fingerprints(folder):
        raise CheckRequired(ALREADY_APPLIED)


def require_check(
    plan_dir: Path,
    fingerprint: str,
    max_age_hours: float,
    customer_id: str | None = None,
    digest: str | None = None,
) -> None:
    """The disk-side gate: not applied or claimed, not declined for good, and check.json is a recent
    pass for this plan, account and (when given) request digest."""
    folder = Path(plan_dir)
    require_not_applied(folder, fingerprint)
    require_not_declined(folder)
    path = folder / CHECK_FILE
    if not path.is_file():
        raise CheckRequired("Run check_plan first: Google has not validated this plan.")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(record, dict):
            raise ValueError("not an object")
        checked_at = record.get("checked_at")
        if isinstance(checked_at, bool) or not isinstance(checked_at, (int, float)) or not math.isfinite(checked_at):
            raise ValueError("checked_at must be a finite number")
    except (OSError, ValueError, TypeError, OverflowError):
        raise CheckRequired("The check record is unreadable. Run check_plan again.") from None
    if record.get("fingerprint") != fingerprint:
        raise CheckRequired("The plan changed after its last check. Run check_plan again.")
    if record.get("passed") is not True:
        raise CheckRequired("The last check did not pass. Fix the plan and run check_plan again.")
    if customer_id is not None and record.get("customer_id") != customer_id:
        raise CheckRequired("The last check was for a different account (or names none). Run check_plan again.")
    if digest is not None and record.get("digest") != digest:
        raise CheckRequired(DIFFERS)
    now = time.time()
    if checked_at > now + FUTURE_SKEW_SECONDS:
        raise CheckRequired("The last check is dated in the future. Run check_plan again.")
    if now - checked_at > max_age_hours * 3600:
        raise CheckRequired(TOO_OLD)


def record_approval(
    plan_dir: Path, *, tool: str, fingerprint: str, digest: str, approved: bool, lines: list[str]
) -> None:
    """Appends the approval window's answer, and exactly the lines it showed, to approvals.jsonl."""
    record = {"at": datetime.now(timezone.utc).isoformat(), "tool": tool, "fingerprint": fingerprint,
              "digest": digest, "outcome": "approved" if approved else "declined", "lines": list(lines)}
    with open(Path(plan_dir) / APPROVAL_LOG, "a", encoding="utf-8", newline="\n") as log:
        log.write(json.dumps(record, ensure_ascii=False) + "\n")


def _logged_declines(folder: Path) -> int:
    """The No answers in a plan folder's approvals.jsonl; 0 if it is missing or unreadable. A line that
    is not a JSON record is skipped."""
    count = 0
    try:
        with open(folder / APPROVAL_LOG, encoding="utf-8") as log:
            for line in log:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                count += isinstance(record, dict) and record.get("outcome") == "declined"
    except (OSError, UnicodeDecodeError):
        return 0
    return count


def declines(plan_dir: Path) -> int:
    """How often the person said No to this plan folder's changes: as approvals.jsonl records them, or
    as this process counted them if more (the log is best effort). A new check_plan resets neither."""
    folder = Path(plan_dir)
    logged = _logged_declines(folder)
    with _PASSED_LOCK:
        return max(logged, _DECLINES.get(str(folder), 0))


def require_not_declined(plan_dir: Path) -> None:
    """After MAX_DECLINES No answers the plan is refused for good: the person wants something else."""
    if declines(plan_dir) >= MAX_DECLINES:
        raise CheckRequired(DECLINED_FOR_GOOD)


def record_result(plan_dir: Path, name: str, data: dict) -> None:
    out = Path(plan_dir) / f"result-{name}.json"
    write_atomic(out, json.dumps({"at": time.time(), **data}, indent=2) + "\n")


def record_attempt(
    plan_dir: Path, *, tool: str, fingerprint: str, digest: str, outcome: str, errors: list[str]
) -> Path:
    """A real request that did not succeed: Google refused it ("refused") or its outcome is unknown.
    Each attempt gets its own file; the claim keeps attempts in one folder from running at once."""
    folder = Path(plan_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    path, n = folder / f"attempt-{stamp}.json", 2
    while os.path.lexists(path):
        path, n = folder / f"attempt-{stamp}-{n}.json", n + 1
    record = {"at": time.time(), "tool": tool, "fingerprint": fingerprint, "digest": digest,
              "outcome": outcome, "errors": list(errors)}
    write_atomic(path, json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    return path


# ---------------------------------------------------------------- claims


def claim(plan_dir: Path, *, fingerprint: str, digest: str, tool: str) -> None:
    """Atomically marks the plan as being applied, before the real request is sent. Exactly one
    caller, in any process, can create it; it stays unless Google clearly refused the request."""
    path = Path(plan_dir) / CLAIM_FILE
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        require_not_applied(plan_dir, fingerprint)
        raise CheckRequired(MAYBE_APPLIED) from None
    record = {"fingerprint": fingerprint, "digest": digest, "at": time.time(), "tool": tool}
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(record) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(path)  # nothing has been sent yet
        raise


def release_claim(plan_dir: Path) -> None:
    """Only after Google refused the whole request, so nothing was applied."""
    os.unlink(Path(plan_dir) / CLAIM_FILE)


# ---------------------------------------------------------------- memory


class Pass(NamedTuple):
    fingerprint: str
    customer_id: str
    checked_at: float  # _clock() (monotonic seconds) when Google's check-only call succeeded
    folder: str


_PASSED: dict[str, Pass] = {}  # request digest -> the passed check of exactly that request
_DECLINES: dict[str, int] = {}  # plan folder -> No answers in the approval window counted in this process
_PASSED_LOCK = threading.Lock()
_clock = time.monotonic


def remember_pass(digest: str, fingerprint: str, customer_id: str, plan_dir: Path) -> None:
    """Only check_plan calls this, after Google's validate-only call succeeded."""
    with _PASSED_LOCK:
        _PASSED[digest] = Pass(fingerprint, customer_id, _clock(), str(Path(plan_dir)))


def _forget(fingerprint: str) -> None:
    """The caller holds _PASSED_LOCK."""
    for digest in [d for d, p in _PASSED.items() if p.fingerprint == fingerprint]:
        del _PASSED[digest]


def forget_fingerprint(fingerprint: str) -> None:
    with _PASSED_LOCK:
        _forget(fingerprint)


def note_decline(plan_dir: Path, fingerprint: str) -> bool:
    """Counts a No in the approval window (after record_approval logged it). The MAX_DECLINES-th No for
    this plan folder, ever, forgets its passes, and require_not_declined refuses the plan from then on,
    in check_plan too. True if it did."""
    folder = str(Path(plan_dir))
    with _PASSED_LOCK:
        _DECLINES[folder] = _DECLINES.get(folder, 0) + 1
    if declines(plan_dir) < MAX_DECLINES:
        return False
    forget_fingerprint(fingerprint)
    return True


def clear_passes() -> None:
    with _PASSED_LOCK:
        _PASSED.clear()
        _DECLINES.clear()


def _matching_pass(digest: str, fingerprint: str, customer_id: str, folder: str) -> Pass:
    """The passed check of exactly this request for this plan, account and folder. The caller holds
    _PASSED_LOCK."""
    entry = _PASSED.get(digest)
    if entry is None:
        if any(p.fingerprint == fingerprint and p.folder == folder for p in _PASSED.values()):
            raise CheckRequired(DIFFERS)
        raise CheckRequired(NOT_CHECKED_HERE)
    if entry.fingerprint != fingerprint or entry.folder != folder:
        raise CheckRequired(NOT_CHECKED_HERE)
    if entry.customer_id != customer_id:
        raise CheckRequired("The last check was for a different account. Run check_plan again.")
    return entry


def peek_pass(digest: str, fingerprint: str, customer_id: str, plan_dir: Path, max_age_hours: float) -> None:
    """The same test as take_pass, but nothing is used up: run before asking the person, so nobody is
    asked to approve a request that would be refused."""
    with _PASSED_LOCK:
        entry = _matching_pass(digest, fingerprint, customer_id, str(Path(plan_dir)))
        if not _clock() - entry.checked_at <= max_age_hours * 3600:
            raise CheckRequired(TOO_OLD)


def take_pass(digest: str, fingerprint: str, customer_id: str, plan_dir: Path, max_age_hours: float) -> None:
    """Requires a passed check of exactly this request for this plan, account and folder, and uses
    it up. A refusal leaves the entry alone."""
    with _PASSED_LOCK:
        entry = _matching_pass(digest, fingerprint, customer_id, str(Path(plan_dir)))
        if not _clock() - entry.checked_at <= max_age_hours * 3600:
            del _PASSED[digest]
            raise CheckRequired(TOO_OLD)
        del _PASSED[digest]
