from __future__ import annotations

import unittest

from job_agent.adapters.dry_run import inspect_lever_html


class LeverInspectionTests(unittest.TestCase):
    def test_captcha_and_unknown_question_stop_the_workflow(self):
        result = inspect_lever_html(
            """
            <form id="application-form">
              <input name="name" required>
              <input name="location" required>
              <input name="cards[q1]" required>
              <input type="hidden" name="h-captcha-response">
            </form>
            """
        )

        self.assertEqual(
            result.review_triggers,
            ["captcha_present", "unknown_question"],
        )
        self.assertEqual(result.fields[0]["field_name"], "full_name")
        self.assertEqual(result.fields[1]["field_name"], "city")
        self.assertEqual(result.fields[2]["category_hint"], "unknown")


if __name__ == "__main__":
    unittest.main()
