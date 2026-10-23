from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .database import ApplicationStore
from .models import ApplicationPlan, PolicyVerdict


def normalized_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    return re.sub(r"\s+", " ", text).strip().casefold()


def hostname_allowed(url: str, allowed_domains: list[str]) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    for raw_domain in allowed_domains:
        domain = raw_domain.lower().strip().rstrip(".")
        if host == domain or host.endswith("." + domain):
            return True
    return False


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def idempotency_key(plan: ApplicationPlan) -> str:
    raw = "|".join(
        [plan.candidate_id, plan.company.casefold(), plan.source_job_id]
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def evaluate_application(
    plan: ApplicationPlan,
    policy: dict,
    profile: dict,
    store: ApplicationStore,
) -> PolicyVerdict:
    reasons: list[str] = []
    submission = policy["submission"]
    criteria = policy["criteria"]

    if not submission.get("enabled", False):
        reasons.append("submission_disabled")

    if submission.get("mode") not in {"dry_run", "assist", "live"}:
        reasons.append("invalid_submission_mode")
    elif (
        submission.get("mode") == "live"
        and not submission.get("live_submission_enabled", False)
    ):
        reasons.append("live_submission_disabled")

    if not hostname_allowed(
        str(plan.application_url), submission.get("allowed_domains", [])
    ):
        reasons.append("domain_not_allowlisted")

    if plan.match_score < int(criteria["minimum_match_score"]):
        reasons.append("match_score_too_low")

    approved_hashes = {
        value.casefold() for value in submission.get("approved_resume_sha256", [])
    }
    if plan.resume_sha256.casefold() not in approved_hashes:
        reasons.append("resume_hash_not_approved")

    resume_path = Path(plan.resume_path)
    if not resume_path.is_file():
        reasons.append("resume_file_missing")
    elif file_sha256(resume_path) != plan.resume_sha256.casefold():
        reasons.append("resume_file_hash_mismatch")

    key = idempotency_key(plan)
    if store.already_reserved(key):
        reasons.append("duplicate_application")

    try:
        daily_timezone = ZoneInfo(
            submission.get("daily_limit_timezone", "UTC")
        )
        local_today = datetime.now(daily_timezone).date()
        start = datetime.combine(local_today, time.min, daily_timezone)
        end = start + timedelta(days=1)
        if store.count_between(start, end) >= int(submission["maximum_per_day"]):
            reasons.append("daily_limit_reached")
    except ZoneInfoNotFoundError:
        reasons.append("invalid_daily_limit_timezone")

    facts = profile.get("facts", {})
    allowed_categories = set(submission.get("allowed_answer_categories", []))
    blocked_categories = set(submission.get("blocked_answer_categories", []))

    for answer in plan.answers:
        category = answer.category.value
        if category in blocked_categories or category not in allowed_categories:
            reasons.append(f"blocked_category:{answer.field_name}:{category}")
            continue

        if not answer.fact_id:
            reasons.append(f"missing_fact_id:{answer.field_name}")
            continue

        fact = facts.get(answer.fact_id)
        if fact is None:
            reasons.append(f"unknown_fact:{answer.field_name}:{answer.fact_id}")
            continue

        if not fact.get("verified", False):
            reasons.append(f"unverified_fact:{answer.field_name}:{answer.fact_id}")
            continue

        if normalized_scalar(answer.value) != normalized_scalar(fact.get("value")):
            reasons.append(f"fact_mismatch:{answer.field_name}:{answer.fact_id}")

    return PolicyVerdict(allowed=not reasons, reasons=sorted(set(reasons)))
