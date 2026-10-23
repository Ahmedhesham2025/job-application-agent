from __future__ import annotations

from pathlib import Path
from typing import Any

from .adapters.base import ApplicationAdapter
from .agents import draft_application, qualify_job
from .database import ApplicationStore
from .models import ApplicationPlan, JobPosting
from .policy import evaluate_application, idempotency_key


def verified_candidate_view(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": profile["candidate_id"],
        "facts": {
            key: value
            for key, value in profile.get("facts", {}).items()
            if value.get("verified", False)
        },
        "skills": profile.get("skills", {}),
        "experience": profile.get("experience", []),
        "verified_achievements": profile.get("verified_achievements", []),
    }


async def process_job(
    job: JobPosting,
    policy: dict[str, Any],
    profile: dict[str, Any],
    store: ApplicationStore,
    adapter: ApplicationAdapter,
) -> dict[str, Any]:
    decision = await qualify_job(
        job,
        policy["criteria"],
        verified_candidate_view(profile),
    )
    store.audit("JOB_SCORED", decision.model_dump(mode="json"))

    if not decision.qualifies:
        return {
            "status": "NOT_QUALIFIED",
            "decision": decision.model_dump(mode="json"),
        }

    inspection = await adapter.inspect(str(job.application_url))
    if inspection.review_triggers:
        return {
            "status": "REVIEW_REQUIRED",
            "review_triggers": inspection.review_triggers,
            "decision": decision.model_dump(mode="json"),
        }

    draft = await draft_application(job, inspection.fields, profile)
    source_document = profile["source_document"]
    plan = ApplicationPlan(
        candidate_id=profile["candidate_id"],
        source_job_id=job.source_job_id,
        company=job.company,
        title=job.title,
        application_url=job.application_url,
        match_score=decision.match_score,
        resume_path=source_document["path"],
        resume_sha256=source_document["sha256"],
        cover_letter=draft.cover_letter,
        answers=draft.answers,
    )
    verdict = evaluate_application(plan, policy, profile, store)
    store.audit(
        "POLICY_EVALUATED",
        {
            "source_job_id": job.source_job_id,
            "verdict": verdict.model_dump(mode="json"),
        },
    )
    if not verdict.allowed:
        return {
            "status": "POLICY_BLOCKED",
            "decision": decision.model_dump(mode="json"),
            "plan": plan.model_dump(mode="json"),
            "verdict": verdict.model_dump(mode="json"),
        }

    key = idempotency_key(plan)
    if not store.reserve(key, plan.candidate_id, plan.source_job_id, plan.company):
        return {
            "status": "POLICY_BLOCKED",
            "decision": decision.model_dump(mode="json"),
            "plan": plan.model_dump(mode="json"),
            "verdict": {
                "allowed": False,
                "reasons": ["duplicate_application_race"],
            },
        }
    receipt = await adapter.submit(plan, key)
    store.record_receipt(key, receipt.model_dump(mode="json"))
    return {
        "status": receipt.status,
        "decision": decision.model_dump(mode="json"),
        "plan": plan.model_dump(mode="json"),
        "verdict": verdict.model_dump(mode="json"),
        "receipt": receipt.model_dump(mode="json"),
    }
