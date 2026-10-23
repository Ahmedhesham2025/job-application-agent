from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from job_agent.database import ApplicationStore
from job_agent.models import ApplicationPlan
from job_agent.policy import evaluate_application, hostname_allowed, idempotency_key


class HostnameTests(unittest.TestCase):
    def test_exact_and_subdomain_are_allowed(self):
        allowlist = ["jobs.example.com"]
        self.assertTrue(hostname_allowed("https://jobs.example.com/a", allowlist))
        self.assertTrue(hostname_allowed("https://eu.jobs.example.com/a", allowlist))

    def test_suffix_attack_is_rejected(self):
        self.assertFalse(
            hostname_allowed(
                "https://jobs.example.com.attacker.invalid/a",
                ["jobs.example.com"],
            )
        )

    def test_credentials_and_http_are_rejected(self):
        allowlist = ["jobs.example.com"]
        self.assertFalse(
            hostname_allowed("https://user@jobs.example.com/a", allowlist)
        )
        self.assertFalse(hostname_allowed("http://jobs.example.com/a", allowlist))


class PolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.NamedTemporaryFile(delete=False)
        temporary.write(b"approved resume")
        temporary.close()
        self.resume_path = Path(temporary.name)
        self.resume_hash = hashlib.sha256(b"approved resume").hexdigest()
        self.store = ApplicationStore(":memory:")
        self.profile = {
            "candidate_id": "candidate-1",
            "facts": {
                "full_name": {"value": "Sam Example", "verified": True},
                "work_authorization": {"value": None, "verified": False},
            },
        }
        self.policy = {
            "criteria": {"minimum_match_score": 82},
            "submission": {
                "enabled": True,
                "mode": "dry_run",
                "maximum_per_day": 5,
                "allowed_domains": ["jobs.example.com"],
                "approved_resume_sha256": [self.resume_hash],
                "allowed_answer_categories": ["identity", "contact"],
                "blocked_answer_categories": [
                    "work_authorization",
                    "unknown",
                ],
            },
        }

    def tearDown(self):
        self.resume_path.unlink(missing_ok=True)

    def make_plan(self, **changes):
        payload = {
            "candidate_id": "candidate-1",
            "source_job_id": "job-1",
            "company": "Example",
            "title": "Senior Data Analyst",
            "application_url": "https://jobs.example.com/job-1/apply",
            "match_score": 90,
            "resume_path": str(self.resume_path),
            "resume_sha256": self.resume_hash,
            "cover_letter": None,
            "answers": [
                {
                    "field_name": "full_name",
                    "question": "Full name",
                    "value": "Sam Example",
                    "fact_id": "full_name",
                    "category": "identity",
                }
            ],
        }
        payload.update(changes)
        return ApplicationPlan.model_validate(payload)

    def test_verified_exact_fact_passes(self):
        verdict = evaluate_application(
            self.make_plan(), self.policy, self.profile, self.store
        )
        self.assertTrue(verdict.allowed, verdict.reasons)

    def test_fact_mismatch_blocks(self):
        plan = self.make_plan()
        plan.answers[0].value = "Someone Else"
        verdict = evaluate_application(plan, self.policy, self.profile, self.store)
        self.assertIn("fact_mismatch:full_name:full_name", verdict.reasons)

    def test_sensitive_category_blocks(self):
        plan = self.make_plan(
            answers=[
                {
                    "field_name": "authorization",
                    "question": "Authorized to work?",
                    "value": True,
                    "fact_id": "work_authorization",
                    "category": "work_authorization",
                }
            ]
        )
        verdict = evaluate_application(plan, self.policy, self.profile, self.store)
        self.assertIn(
            "blocked_category:authorization:work_authorization", verdict.reasons
        )

    def test_verified_work_authorization_can_be_allowlisted(self):
        self.profile["facts"]["authorized_to_work_outside_egypt"] = {
            "value": False,
            "verified": True,
        }
        self.policy["submission"]["allowed_answer_categories"].append(
            "work_authorization"
        )
        self.policy["submission"]["blocked_answer_categories"].remove(
            "work_authorization"
        )
        plan = self.make_plan(
            answers=[
                {
                    "field_name": "authorization",
                    "question": "Authorized to work outside Egypt?",
                    "value": False,
                    "fact_id": "authorized_to_work_outside_egypt",
                    "category": "work_authorization",
                }
            ]
        )

        verdict = evaluate_application(plan, self.policy, self.profile, self.store)

        self.assertTrue(verdict.allowed, verdict.reasons)

    def test_resume_hash_mismatch_blocks(self):
        plan = self.make_plan(resume_sha256="0" * 64)
        verdict = evaluate_application(plan, self.policy, self.profile, self.store)
        self.assertIn("resume_hash_not_approved", verdict.reasons)
        self.assertIn("resume_file_hash_mismatch", verdict.reasons)

    def test_duplicate_blocks(self):
        plan = self.make_plan()
        key = idempotency_key(plan)
        self.store.reserve(key, plan.candidate_id, plan.source_job_id, plan.company)
        verdict = evaluate_application(plan, self.policy, self.profile, self.store)
        self.assertIn("duplicate_application", verdict.reasons)

    def test_disabled_submission_blocks(self):
        self.policy["submission"]["enabled"] = False
        verdict = evaluate_application(
            self.make_plan(), self.policy, self.profile, self.store
        )
        self.assertIn("submission_disabled", verdict.reasons)

    def test_live_mode_requires_separate_enablement(self):
        self.policy["submission"]["mode"] = "live"
        self.policy["submission"]["live_submission_enabled"] = False
        verdict = evaluate_application(
            self.make_plan(), self.policy, self.profile, self.store
        )
        self.assertIn("live_submission_disabled", verdict.reasons)


if __name__ == "__main__":
    unittest.main()
