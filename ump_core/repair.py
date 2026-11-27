"""Bounded, provenance-bound repair decisions and orchestration."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .jobs import DomainError, JobStore

MAX_REPAIR_ATTEMPTS = 3
_CATEGORIES = ("technical", "motion", "visual", "editorial")
_ISSUE_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_REQUEST_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")


def _encode(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
        raise DomainError("INVALID_REVIEW", "repair input must be bounded JSON") from exc


def _checks(review: dict) -> list[tuple[str, int, dict]]:
    result = []
    for category in _CATEGORIES:
        section = review.get(category)
        if not isinstance(section, dict) or not isinstance(section.get("checks"), list):
            continue
        result.extend((category, index, item) for index, item in enumerate(section["checks"])
                      if isinstance(item, dict))
    return result


def _blocking(check: dict) -> bool:
    return check.get("advisory") is not True or check.get("required") is True \
        or check.get("conflicts_with_intent") is True


def _blocking_checks(review: dict) -> list[tuple[str, int, dict]]:
    return [(category, index, check) for category, index, check in _checks(review)
            if _blocking(check) and check.get("status") in {"fail", "pending_review"}]


def blocking_issue_count(review: dict) -> int:
    issues = _blocking_checks(review)
    if not issues and review.get("overall_status") == "fail" and not _checks(review):
        return 1
    return len(issues)


def open_issues(review: dict) -> list[dict[str, Any]]:
    issues = []
    for category, index, check in _blocking_checks(review):
        issues.append({
            "category": category,
            "check_index": index,
            "criterion": check.get("criterion"),
            "status": check.get("status"),
            "required": check.get("required") is True,
            "conflicts_with_intent": check.get("conflicts_with_intent") is True,
            "evidence_refs": check.get("evidence_refs", check.get("evidence_ref")),
        })
    for index, observation in enumerate(review.get("observations", [])):
        if isinstance(observation, dict) and observation.get("status") in {"fail", "pending_review"}:
            issues.append({
                "observation_index": index,
                "shot": observation.get("shot"),
                "time_ms": observation.get("time_ms"),
                "severity": observation.get("severity"),
                "status": observation.get("status"),
                "actual": observation.get("actual"),
                "repair": observation.get("repair"),
                "impact": observation.get("impact"),
            })
    return issues


def issue_id_for_review(review: dict) -> str:
    blocking = _blocking_checks(review)
    if blocking:
        category, index, check = blocking[0]
        identity = {
            "category": category,
            "check_index": index,
            "criterion": check.get("criterion"),
            "evidence_refs": check.get("evidence_refs", check.get("evidence_ref")),
        }
    else:
        observations = review.get("observations", [])
        observation = next((item for item in observations if isinstance(item, dict)
                            and item.get("status") in {"fail", "pending_review"}), None)
        identity = observation or {"binding": review.get("binding"),
                                   "overall_status": review.get("overall_status")}
    return "qa-" + hashlib.sha256(_encode(identity)).hexdigest()[:32]


def repair_decision(review: dict, attempts: int, budget_ok: bool, locked: bool) -> str:
    """Choose a bounded next step; advisory-only failures never trigger edits."""
    if type(attempts) is not int or attempts < 0:
        raise ValueError("attempts must be a non-negative integer")
    if type(budget_ok) is not bool or type(locked) is not bool:
        raise ValueError("budget_ok and locked must be booleans")
    if locked or attempts >= MAX_REPAIR_ATTEMPTS or not budget_ok:
        return "stop"
    if not isinstance(review, dict):
        return "pending_review"
    if review.get("overall_status") == "pending_review":
        return "pending_review"
    checks = _checks(review)
    blocking = [(category, index, check) for category, index, check in checks if _blocking(check)]
    if any(check.get("status") == "pending_review" for _category, _index, check in blocking):
        return "pending_review"
    if any(check.get("status") == "fail" for _category, _index, check in blocking):
        return "repair"
    if not checks:
        if review.get("overall_status") == "fail":
            return "repair"
        if review.get("overall_status") == "pass":
            return "stop"
    if review.get("overall_status") == "fail":
        return "repair"
    if review.get("overall_status") == "pass":
        return "stop"
    return "pending_review"


def _reason(decision: str, attempts: int, budget_ok: bool, budget: dict | None) -> str:
    if decision == "pending_review":
        return "human_review_pending"
    if attempts >= MAX_REPAIR_ATTEMPTS:
        return "attempt_limit"
    if not budget_ok:
        reasons = budget.get("reasons", []) if isinstance(budget, dict) else []
        return "resource_budget" if not reasons else "resource_budget: " + ",".join(map(str, reasons))
    if decision == "repair":
        return "mandatory_failure"
    return "no_mandatory_failure"


def _response(store: JobStore, job_id: str, initial_rev: int, issue_id: str | None,
              decision: str, reason: str | None, *, rev: int | None = None,
              budget: dict | None = None, idempotent: bool = False) -> dict:
    result = {
        "decision": decision,
        "job_id": job_id,
        "initial_rev": initial_rev,
        "issue_id": issue_id,
        "rev": store.get(job_id)["rev"] if rev is None else rev,
        "reason": reason,
        "idempotent": idempotent,
    }
    chain = store.get_repair_chain(job_id, initial_rev, issue_id) if issue_id else None
    if chain is not None:
        result.update({
            "attempts": chain["attempts"],
            "remaining": chain["remaining"],
            "repair": chain,
        })
    if budget is not None:
        result["budget"] = budget
    return result


def repair_job(store: JobStore, job_id: str, rev: int, review: dict, *,
               budget_ok: bool, budget: dict | None = None,
               initial_rev: int | None = None, issue_id: str | None = None,
               request_id: str | None = None, patch: dict | None = None,
               value: dict | None = None) -> dict:
    """Record the actual preview review and, only on a mandatory failure, revise source."""
    if patch is not None and value is not None:
        raise DomainError("INVALID_PAYLOAD", "provide patch or complete composition, not both")
    binding = review.get("binding") if isinstance(review, dict) else None
    if (not isinstance(binding, dict) or binding.get("job_id") != job_id
            or binding.get("rev") != rev):
        raise DomainError("REVIEW_BINDING_MISMATCH", "review is for a different job or revision")
    canonical = store.record_quality_review(review)
    if type(budget_ok) is not bool:
        raise DomainError("INVALID_PAYLOAD", "budget gate must be a boolean result from T3")

    has_open_issue = blocking_issue_count(canonical) > 0 or canonical["overall_status"] == "pending_review"
    parent = store.get_repair_attempt_for_revision(job_id, rev)
    requested_initial_rev = initial_rev
    if parent is not None:
        if requested_initial_rev is not None and requested_initial_rev != parent["initial_rev"]:
            raise DomainError("REPAIR_CHAIN_MISMATCH", "a repair revision cannot reset its initial_rev")
        if issue_id is not None and issue_id != parent["issue_id"]:
            raise DomainError("REPAIR_CHAIN_MISMATCH", "a repair revision must continue its registered issue chain")
        initial_rev = parent["initial_rev"]
        issue_id = parent["issue_id"]
    else:
        initial_rev = rev if requested_initial_rev is None else requested_initial_rev
        if type(initial_rev) is not int or initial_rev != rev:
            raise DomainError("REPAIR_CHAIN_MISMATCH", "a new chain must start at the reviewed revision")
        if issue_id is None and has_open_issue:
            issue_id = issue_id_for_review(canonical)
        elif issue_id is not None and not _ISSUE_ID.fullmatch(issue_id):
            raise DomainError("INVALID_REPAIR_ISSUE", "issue_id must be a bounded identifier")
        existing_chain = store.get_repair_chain(job_id, initial_rev, issue_id) if issue_id else None
        if not has_open_issue and existing_chain is None:
            issue_id = None

    if type(initial_rev) is not int or initial_rev < 1:
        raise DomainError("INVALID_REVISION", "initial_rev must be a positive revision")
    if issue_id is not None and not isinstance(issue_id, str):
        raise DomainError("INVALID_REPAIR_ISSUE", "issue_id must be text")

    change = {"patch": patch, "value": value}
    request_payload = {"job_id": job_id, "initial_rev": initial_rev, "issue_id": issue_id,
                       "binding": canonical["binding"], "review": canonical, "change": change}
    request_hash = hashlib.sha256(_encode(request_payload)).hexdigest()
    if request_id is None:
        request_id = "req-" + request_hash[:32]
    elif not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
        raise DomainError("INVALID_REPAIR_REQUEST", "request_id must be a bounded identifier")

    if issue_id is not None:
        prior = store.get_repair_attempt(job_id, initial_rev, issue_id, request_id)
        if prior is not None:
            if prior["request_sha256"] != request_hash:
                raise DomainError("IDEMPOTENCY_CONFLICT", "request_id was already used for different repair input")
            return _response(store, job_id, initial_rev, issue_id, "repair", None,
                             rev=prior["rev"], idempotent=True)

    if store.get(job_id)["rev"] != rev:
        raise DomainError("STALE_REVISION", f"expected {store.get(job_id)['rev']}")
    if issue_id is None:
        decision = repair_decision(canonical, 0, budget_ok, False)
        return _response(store, job_id, initial_rev, None, decision,
                         _reason(decision, 0, budget_ok, budget), budget=budget)

    store.ensure_repair_chain(
        job_id, initial_rev, issue_id, canonical,
        issue_count=blocking_issue_count(canonical), open_issues=open_issues(canonical),
    )
    attempts = store.repair_attempt_count(job_id, initial_rev, issue_id)
    decision = repair_decision(canonical, attempts, budget_ok, False)
    reason = _reason(decision, attempts, budget_ok, budget)
    if decision != "repair":
        state = "pending_review" if decision == "pending_review" else "complete" if reason == "no_mandatory_failure" else "stopped"
        store.set_repair_state(job_id, initial_rev, issue_id, state, reason)
        return _response(store, job_id, initial_rev, issue_id, decision, reason, budget=budget)
    if patch is None and value is None:
        store.set_repair_state(job_id, initial_rev, issue_id, "stopped", "repair_source_required")
        raise DomainError("REPAIR_SOURCE_REQUIRED", "mandatory failure requires a planner/editor source change")

    context = {
        "job_id": job_id,
        "initial_rev": initial_rev,
        "issue_id": issue_id,
        "request_id": request_id,
        "request_sha256": request_hash,
    }
    try:
        revised = store.repair_revision(
            job_id, rev, patch=patch, value=value, repair_context=context,
        )
    except DomainError as exc:
        if exc.code != "LOCK_CONFLICT":
            raise
        store.set_repair_state(job_id, initial_rev, issue_id, "stopped", "lock_conflict")
        return _response(store, job_id, initial_rev, issue_id, "stop", "lock_conflict", budget=budget)
    return _response(store, job_id, initial_rev, issue_id, "repair", None,
                     rev=revised["rev"], budget=budget)
