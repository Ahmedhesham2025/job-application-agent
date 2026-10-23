from __future__ import annotations

from .models import JobPosting


def deterministic_prefilter(
    jobs: list[JobPosting], policy: dict
) -> tuple[list[JobPosting], list[dict[str, object]]]:
    criteria = policy["criteria"]
    titles = [str(value).casefold() for value in criteria["target_titles"]]
    excluded_title_terms = [
        str(value).casefold() for value in criteria.get("excluded_title_terms", [])
    ]
    locations = [str(value).casefold() for value in criteria["allowed_locations"]]
    required_skills = [str(value).casefold() for value in criteria["required_skills"]]
    excluded_terms = [str(value).casefold() for value in criteria["excluded_terms"]]
    home_locations = [str(value).casefold() for value in criteria.get("home_locations", [])]
    relocation_terms = [
        str(value).casefold() for value in criteria.get("relocation_support_terms", [])
    ]
    remote_terms = [
        str(value).casefold() for value in criteria.get("remote_eligibility_terms", [])
    ]

    matched: list[JobPosting] = []
    rejected: list[dict[str, object]] = []
    for job in jobs:
        title = job.title.casefold()
        metadata_country = str(job.metadata.get("country") or "").casefold()
        location = f"{job.location} {metadata_country}".casefold()
        searchable = f"{job.title}\n{job.location}\n{job.description}".casefold()
        is_remote = (
            (job.workplace_type or "").casefold() == "remote"
            or "remote" in location
        )
        is_home_location = any(home in location for home in home_locations)
        reasons: list[str] = []
        if not any(target in title for target in titles):
            reasons.append("title_not_allowed")
        if any(term in title for term in excluded_title_terms):
            reasons.append("title_seniority_not_allowed")
        if not any(allowed in location for allowed in locations):
            reasons.append("location_not_allowed")
        if any(skill not in searchable for skill in required_skills):
            reasons.append("required_skill_missing")
        if any(term in searchable for term in excluded_terms):
            reasons.append("excluded_term_present")
        # A country-restricted remote role is safe to consider when the employer
        # explicitly offers visa or relocation support for that country. Otherwise,
        # require an explicit worldwide/EMEA remote eligibility statement.
        if (
            is_remote
            and remote_terms
            and not any(term in searchable for term in remote_terms)
            and not any(term in searchable for term in relocation_terms)
        ):
            reasons.append("remote_eligibility_not_explicit")
        if (
            criteria.get("require_relocation_support_for_non_remote_abroad", False)
            and not is_remote
            and not is_home_location
            and not any(term in searchable for term in relocation_terms)
        ):
            reasons.append("relocation_support_not_explicit")

        if reasons:
            rejected.append(
                {
                    "source_job_id": job.source_job_id,
                    "company": job.company,
                    "title": job.title,
                    "reasons": reasons,
                }
            )
        else:
            matched.append(job)
    return matched, rejected
