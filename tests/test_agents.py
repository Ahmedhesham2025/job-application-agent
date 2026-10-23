from __future__ import annotations

import unittest

from job_agent.agents import _anthropic_schema
from job_agent.models import JobDecision


class AnthropicSchemaTests(unittest.TestCase):
    def test_numeric_bounds_are_removed_but_local_model_keeps_them(self):
        schema = _anthropic_schema(JobDecision.model_json_schema())
        score = schema["properties"]["match_score"]

        self.assertNotIn("minimum", score)
        self.assertNotIn("maximum", score)
        with self.assertRaises(ValueError):
            JobDecision(
                source_job_id="job-1",
                qualifies=True,
                match_score=101,
                concise_reason="Invalid score",
            )


if __name__ == "__main__":
    unittest.main()
