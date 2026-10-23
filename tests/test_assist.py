"""Assist mode tests on local copies of Greenhouse- and Ashby-style forms.

A fake AI drafter is used, so no API key or network is needed. The test plays
the human: it waits for the agent's banner, then presses Submit itself.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from job_agent.assist import assist_application, consistent_with_fact, review_answers
from job_agent.config import load_policy, load_yaml
from job_agent.database import ApplicationStore
from job_agent.models import FormAnswer, FormDraft, JobPosting

FIXTURES = Path(__file__).parent / "fixtures"


def load_profile():
    """Fictional sample candidate, so the tests never depend on real personal data."""
    profile = load_yaml(FIXTURES / "sample_profile.yaml")
    profile["source_document"]["path"] = str(FIXTURES / profile["source_document"]["path"])
    return profile


def sample_policy():
    policy = load_policy()
    policy["submission"]["approved_resume_sha256"] = [load_profile()["source_document"]["sha256"]]
    policy["submission"]["maximum_per_day"] = 100
    return policy

try:
    import playwright  # noqa: F401

    HAVE_PLAYWRIGHT = True
except ImportError:  # pragma: no cover
    HAVE_PLAYWRIGHT = False


def answer(key, value, fact_id=None, category="identity", basis="fact"):
    return FormAnswer(key=key, value=value, fact_id=fact_id, category=category, basis=basis)


class FactConsistencyTests(unittest.TestCase):
    def test_yes_no_mapping(self):
        self.assertTrue(consistent_with_fact("Yes", True))
        self.assertTrue(consistent_with_fact("No", False))
        self.assertFalse(consistent_with_fact("Yes", False))
        self.assertFalse(consistent_with_fact("No, I do not", True))
        self.assertTrue(consistent_with_fact("checked", True))
        self.assertFalse(consistent_with_fact("unchecked", True))
        self.assertFalse(consistent_with_fact("Not sure", True))

    def test_equivalent_wording(self):
        self.assertTrue(consistent_with_fact("1 Month", "30 days"))
        self.assertTrue(consistent_with_fact("2 weeks", "14 days"))
        self.assertFalse(consistent_with_fact("3 months", "30 days"))
        self.assertTrue(consistent_with_fact("Bachelor´s Degree", "Bachelor of Engineering, Electronics"))
        self.assertFalse(consistent_with_fact("Master's Degree", "Bachelor of Engineering"))

    def test_text_and_numbers(self):
        self.assertTrue(consistent_with_fact("50000", "50000"))
        self.assertTrue(consistent_with_fact("EUR 50,000", "50000"))
        self.assertFalse(consistent_with_fact("60000", "50000"))
        self.assertTrue(consistent_with_fact("Cairo, Egypt", "Cairo, Egypt"))


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.profile = load_profile()

    def test_wrong_answers_go_to_human(self):
        fields = [
            {"key": "a", "kind": "number", "label": "Salary", "required": True},
            {"key": "b", "kind": "radio", "label": "Disability?", "required": False, "options": ["Yes", "No", "Prefer not to disclose"]},
            {"key": "c", "kind": "select", "label": "Convicted?", "required": True, "options": ["Yes", "No"]},
            {"key": "d", "kind": "text", "label": "Name", "required": True},
            {"key": "e", "kind": "select", "label": "Sponsorship?", "required": True, "options": ["Yes", "No"]},
        ]
        draft = FormDraft(
            cover_letter="Hi",
            answers=[
                answer("a", "60000", "salary_expectation_eur_yearly", "salary"),  # does not match fact
                answer("b", "No", "disability_status", "disability"),  # personal: must decline
                answer("c", "No", None, "criminal_history", "open_answer"),  # never answered
                answer("d", "Sam Example", "full_name"),
                answer("e", "Maybe", "requires_sponsorship", "sponsorship"),  # not an option
            ],
        )
        review = review_answers(fields, draft, self.profile)
        self.assertEqual([a.key for a in review.fill], ["d"])
        self.assertEqual(sorted(t["key"] for t in review.todo), ["a", "b", "c", "e"])

    def test_required_field_without_answer_is_flagged(self):
        fields = [{"key": "x", "kind": "text", "label": "Portfolio*", "required": True}]
        review = review_answers(fields, FormDraft(cover_letter="Hi", answers=[]), self.profile)
        self.assertEqual(review.todo[0]["key"], "x")


GREENHOUSE_ANSWERS = {
    "First Name": ("Sam", "first_name", "identity", "fact"),
    "Last Name": ("Example", "last_name", "identity", "fact"),
    "Email": ("sam@example.com", "email", "contact", "fact"),
    "Phone": ("+20 100 000 0000", "phone", "contact", "fact"),
    "Resume": ("RESUME", "full_name", "identity", "fact"),
    "Cover Letter": ("COVER_LETTER", "full_name", "identity", "fact"),
    "LinkedIn": ("https://linkedin.com/in/sam-example", "linkedin_url", "contact", "fact"),
    "Pronouns": ("Prefer not to say", "pronouns", "demographic", "fact"),
    "How did you": ("Company careers page", "referral_source", "preference", "fact"),
    "sponsorship": ("Yes", "requires_sponsorship", "sponsorship", "fact"),
    "EU citizen": ("No", "eu_citizen", "work_authorization", "fact"),
    "convicted": ("No", None, "criminal_history", "open_answer"),
    "Why do you want": ("I build trusted KPI datasets.", None, "open_answer", "open_answer"),
}

ASHBY_ANSWERS = {
    "Resume": ("RESUME", None, "identity", "fact"),
    "Name": ("Sam Example", "full_name", "identity", "fact"),
    "Email": ("sam@example.com", "email", "contact", "fact"),
    "Phone": ("+20 100 000 0000", "phone", "contact", "fact"),
    "Milan office": ("Yes", "willing_hybrid_or_onsite", "preference", "fact"),
    "sponsorship": ("checked", "requires_sponsorship", "sponsorship", "fact"),
    "currently using": (["I'm not a user"], None, "open_answer", "open_answer"),
    "Salary": ("45000", "salary_expectation_eur_yearly", "salary", "fact"),
    "protected category": ("Prefer not to disclose", "disability_status", "disability", "fact"),
    "consent": ("checked", "data_consent", "consent", "fact"),
}


def fake_drafter(table):
    async def drafter(job, fields, profile):
        answers = []
        for f in fields:
            for needle, (value, fact_id, category, basis) in table.items():
                if needle.lower() in f["label"].lower():
                    answers.append(answer(f["key"], value, fact_id, category, basis))
                    break
        return FormDraft(cover_letter="Dear team,\nI would like to apply.", answers=answers)

    return drafter


@unittest.skipUnless(HAVE_PLAYWRIGHT, "playwright not installed")
class AssistEndToEndTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.policy = sample_policy()
        self.profile = load_profile()
        self.store = ApplicationStore(":memory:")

    async def _run(self, fixture, url, table, human_submits=True, human_fills=None, learned_path=None):
        html = (FIXTURES / fixture).read_text(encoding="utf-8")
        pages = []

        async def prepare(context):
            async def serve(route):
                await route.fulfill(status=200, content_type="text/html", body=html)

            await context.route(url, serve)
            await context.route("https://www.recaptcha.net/**", lambda r: r.fulfill(status=200, body=""))
            context.on("page", lambda p: pages.append(p))
            pages.extend(context.pages)

        async def human():
            # Wait until the agent shows its banner, then press Submit.
            for _ in range(200):
                await asyncio.sleep(0.1)
                for p in pages:
                    try:
                        if await p.get_by_text("Job agent filled").count():
                            self.filled_page_text = await p.evaluate("() => JSON.stringify([...document.querySelectorAll('input,textarea,select')].map(e => [e.id || e.name, e.type, e.type==='checkbox'||e.type==='radio' ? e.checked : (e.type==='file' ? e.files.length : e.value)]))")
                            self.buttons = await p.evaluate("() => [...document.querySelectorAll('button[data-on]')].map(b => b.textContent)")
                            if human_fills:
                                await human_fills(p)
                                await asyncio.sleep(2.5)  # let the agent take a snapshot
                            if human_submits:
                                await p.locator("form button[type=submit], #go").last.click()
                            else:
                                await p.close()
                            return
                    except Exception:
                        pass

        job = JobPosting(
            source="test", source_job_id=fixture, company="Example", title="Analytics Engineer",
            description="SQL dbt BigQuery", application_url=url,
        )
        helper = asyncio.create_task(human())
        result = await assist_application(
            job, 85, self.policy, self.profile, self.store, self.tmp / "artifacts",
            self.tmp / "profile", fake_drafter(table), wait_minutes=1, headless=True,
            prepare_context=prepare, learned_path=learned_path,
        )
        await helper
        return result

    async def test_greenhouse_like_form(self):
        url = "https://job-boards.greenhouse.io/example/jobs/1"
        result = await self._run("greenhouse_like.html", url, GREENHOUSE_ANSWERS)
        self.assertEqual(result.status, "SUBMITTED", result.detail)
        values = {k: v for k, _, v in json.loads(self.filled_page_text)}
        self.assertEqual(values["first_name"], "Sam")
        self.assertEqual(values["resume"], 1)
        self.assertEqual(values["cover_letter"], 1)
        self.assertEqual(values["q3"], "Prefer not to say")
        self.assertEqual(values["q5"], "Yes")
        self.assertEqual(values["q6"], "No")
        self.assertEqual(values["q7"], "Select...")  # crime question left to the human
        needs = [t["label"] for t in result.detail["needs_you"]]
        self.assertTrue(any("convicted" in n for n in needs), needs)
        self.assertEqual(self.store.submitted_job_ids(), {"greenhouse_like.html"})

    async def test_ashby_like_form(self):
        url = "https://jobs.ashbyhq.com/example/abc/application"
        result = await self._run("ashby_like.html", url, ASHBY_ANSWERS)
        self.assertEqual(result.status, "SUBMITTED", result.detail)
        state = {(k, t): v for k, t, v in json.loads(self.filled_page_text)}
        self.assertEqual(state[("_systemfield_resume", "file")], 1)
        self.assertEqual(state[("sal", "number")], "45000")
        self.assertTrue(state[("spons", "checkbox")])
        self.assertTrue(state[("consent", "checkbox")])
        self.assertEqual(self.buttons, ["Yes"])
        self.assertEqual(result.detail["needs_you"], [])

    async def test_learns_answer_you_typed_and_reuses_it(self):
        from job_agent.learning import load_learned

        url = "https://jobs.ashbyhq.com/example/abc/application"
        learned_path = self.tmp / "learned_answers.yaml"
        table = {k: v for k, v in ASHBY_ANSWERS.items() if k != "Milan office"}

        async def press_yes(page):
            await page.get_by_role("button", name="Yes").click()

        first = await self._run("ashby_like.html", url, table, human_fills=press_yes, learned_path=learned_path)
        self.assertEqual(first.status, "SUBMITTED")
        learned = load_learned(learned_path)
        self.assertEqual(len(learned), 1, learned)
        self.assertIn("Milan office", learned[0]["question"])
        self.assertEqual(learned[0]["answer"], "Yes")

        # Second form: the drafter reuses the learned answer and nothing is left to the human.
        self.store = ApplicationStore(":memory:")
        self.profile["learned_answers"] = learned
        table2 = dict(table)
        table2["Milan office"] = ("Yes", learned[0]["id"], "preference", "learned")
        second = await self._run("ashby_like.html", url, table2, learned_path=learned_path)
        self.assertEqual(second.status, "SUBMITTED")
        self.assertEqual(second.detail["needs_you"], [])
        self.assertEqual(self.buttons, ["Yes"])

    async def test_closing_without_submit_frees_the_job(self):
        url = "https://jobs.ashbyhq.com/example/abc/application"
        result = await self._run("ashby_like.html", url, ASHBY_ANSWERS, human_submits=False)
        self.assertEqual(result.status, "ABANDONED")
        self.assertEqual(self.store.submitted_job_ids(), set())
        self.assertFalse(self.store.count_between.__self__.connection.execute("SELECT COUNT(*) FROM submissions").fetchone()[0])

    async def test_wrong_domain_is_blocked_before_opening(self):
        job = JobPosting(source="t", source_job_id="x", company="Evil", title="Data Analyst",
                         description="", application_url="https://jobs.evil.example/apply")
        result = await assist_application(job, 90, self.policy, self.profile, self.store,
                                          self.tmp, self.tmp / "p", fake_drafter({}), headless=True)
        self.assertEqual(result.status, "POLICY_BLOCKED")


if __name__ == "__main__":
    unittest.main()
