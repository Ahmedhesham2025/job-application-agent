from __future__ import annotations

import unittest

import httpx

from job_agent.sources import AshbySource, GreenhouseSource, LeverSource


class SourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_greenhouse_mapping(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.params.get("content"), "true")
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "id": 123,
                            "title": "Senior Data Analyst",
                            "location": {"name": "Cairo"},
                            "content": "<p>Build <strong>trusted</strong> KPIs.</p>",
                            "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/123",
                            "updated_at": "2026-10-23T00:00:00Z",
                        }
                    ]
                },
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await GreenhouseSource("acme", client=client).fetch()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source_job_id, "123")
        self.assertEqual(jobs[0].description, "Build trusted KPIs.")

    async def test_lever_mapping(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.params.get("mode"), "json")
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "job-1",
                        "text": "Analytics Engineer",
                        "categories": {
                            "location": "Remote",
                            "team": "Data",
                        },
                        "descriptionPlain": "Build dbt models.",
                        "lists": [
                            {
                                "text": "Requirements",
                                "content": "<ul><li>Advanced SQL</li><li>BigQuery</li></ul>",
                            }
                        ],
                        "additionalPlain": "Experience with governed metrics.",
                        "applyUrl": "https://jobs.lever.co/acme/job-1/apply",
                        "workplaceType": "remote",
                    }
                ],
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await LeverSource("acme", client=client).fetch()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].title, "Analytics Engineer")
        self.assertEqual(jobs[0].metadata["team"], "Data")
        self.assertEqual(
            jobs[0].description,
            "Build dbt models.\n\nRequirements Advanced SQL BigQuery\n\n"
            "Experience with governed metrics.",
        )

    async def test_ashby_mapping(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.params.get("includeCompensation"), "true")
            return httpx.Response(
                200,
                json={
                    "apiVersion": "1",
                    "jobs": [
                        {
                            "id": "ashby-job-1",
                            "title": "Senior Analytics Engineer",
                            "location": "Hamburg",
                            "workplaceType": "Hybrid",
                            "isListed": True,
                            "descriptionPlain": "SQL, dbt, and relocation support.",
                            "applyUrl": "https://jobs.ashbyhq.com/acme/job-1/apply",
                            "department": "Data",
                            "team": "Analytics",
                            "publishedAt": "2026-10-23T00:00:00Z",
                            "compensation": {
                                "scrapeableCompensationSalarySummary": "EUR 80k"
                            },
                        }
                    ],
                },
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await AshbySource("acme", client=client).fetch()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].workplace_type, "hybrid")
        self.assertEqual(jobs[0].salary_text, "EUR 80k")


if __name__ == "__main__":
    unittest.main()
