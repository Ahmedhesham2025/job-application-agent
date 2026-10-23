from __future__ import annotations

import unittest

from job_agent.matching import deterministic_prefilter
from job_agent.models import JobPosting


class MatchingTests(unittest.TestCase):
    def test_prefilter_keeps_only_explicit_title_location_and_skill_matches(self):
        policy = {
            "criteria": {
                "target_titles": ["Senior Data Analyst"],
                "excluded_title_terms": ["Staff", "Lead"],
                "allowed_locations": ["Cairo"],
                "required_skills": ["SQL"],
                "excluded_terms": ["unpaid"],
                "home_locations": ["Cairo", "Egypt"],
                "require_relocation_support_for_non_remote_abroad": True,
                "relocation_support_terms": ["relocation support", "visa sponsorship"],
                "remote_eligibility_terms": ["remote - emea", "anywhere in the world"],
            }
        }
        jobs = [
            JobPosting(
                source="lever",
                source_job_id="match",
                company="Klivvr",
                title="Senior Data Analyst",
                location="One Kattameya, Cairo",
                description="Advanced SQL and governed analytics.",
                application_url="https://jobs.lever.co/klivvr/match/apply",
            ),
            JobPosting(
                source="lever",
                source_job_id="reject",
                company="Klivvr",
                title="Senior Data Scientist",
                location="One Kattameya, Cairo",
                description="Advanced SQL.",
                application_url="https://jobs.lever.co/klivvr/reject/apply",
            ),
        ]

        matched, rejected = deterministic_prefilter(jobs, policy)

        self.assertEqual([job.source_job_id for job in matched], ["match"])
        self.assertEqual(rejected[0]["reasons"], ["title_not_allowed"])

    def test_abroad_role_requires_explicit_relocation_support(self):
        policy = {
            "criteria": {
                "target_titles": ["Senior Data Analyst"],
                "excluded_title_terms": ["Staff", "Lead"],
                "allowed_locations": ["Amsterdam"],
                "required_skills": ["SQL"],
                "excluded_terms": [],
                "home_locations": ["Cairo", "Egypt"],
                "require_relocation_support_for_non_remote_abroad": True,
                "relocation_support_terms": ["relocation support", "visa sponsorship"],
                "remote_eligibility_terms": ["remote - emea"],
            }
        }
        job = JobPosting(
            source="ashby",
            source_job_id="nl-job",
            company="Acme",
            title="Senior Data Analyst",
            location="Amsterdam",
            workplace_type="hybrid",
            description="Advanced SQL; applicants must already live locally.",
            application_url="https://jobs.ashbyhq.com/acme/nl-job/apply",
        )

        matched, rejected = deterministic_prefilter([job], policy)

        self.assertEqual(matched, [])
        self.assertIn("relocation_support_not_explicit", rejected[0]["reasons"])

    def test_country_remote_role_accepts_explicit_visa_sponsorship(self):
        policy = {
            "criteria": {
                "target_titles": ["Analytics Engineer"],
                "excluded_title_terms": ["Staff", "Lead"],
                "allowed_locations": ["Spain"],
                "required_skills": ["SQL"],
                "excluded_terms": [],
                "home_locations": ["Cairo", "Egypt"],
                "require_relocation_support_for_non_remote_abroad": True,
                "relocation_support_terms": ["relocation support", "visa sponsorship"],
                "remote_eligibility_terms": ["remote - emea", "anywhere in the world"],
            }
        }
        job = JobPosting(
            source="greenhouse",
            source_job_id="visa-remote",
            company="Example",
            title="Analytics Engineer II",
            location="Remote Spain",
            description="Advanced SQL. We offer visa sponsorship for this role.",
            application_url="https://job-boards.greenhouse.io/example/jobs/visa-remote",
        )

        matched, rejected = deterministic_prefilter([job], policy)

        self.assertEqual(matched, [job])
        self.assertEqual(rejected, [])


if __name__ == "__main__":
    unittest.main()
