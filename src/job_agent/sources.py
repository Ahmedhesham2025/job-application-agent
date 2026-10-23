from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Protocol

import httpx

from .models import JobPosting


TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(value: str | None) -> str:
    parser = _TextExtractor()
    parser.feed(value or "")
    return " ".join(" ".join(parser.parts).split())


def lever_description(raw: dict) -> str:
    sections = [
        raw.get("descriptionPlain") or html_to_text(raw.get("description"))
    ]
    for item in raw.get("lists") or []:
        heading = str(item.get("text") or "").strip()
        content = html_to_text(item.get("content"))
        sections.append(" ".join(part for part in (heading, content) if part))
    additional = raw.get("additionalPlain") or html_to_text(raw.get("additional"))
    if additional:
        sections.append(additional)
    return "\n\n".join(section.strip() for section in sections if section and section.strip())


class JobSource(Protocol):
    async def fetch(self) -> list[JobPosting]: ...


class GreenhouseSource:
    def __init__(self, board_token: str, client: httpx.AsyncClient | None = None):
        if not TOKEN_PATTERN.fullmatch(board_token):
            raise ValueError("Invalid Greenhouse board token")
        self.board_token = board_token
        self.client = client

    async def fetch(self) -> list[JobPosting]:
        url = (
            "https://boards-api.greenhouse.io/v1/boards/"
            f"{self.board_token}/jobs"
        )
        owned_client = self.client is None
        client = self.client or httpx.AsyncClient(timeout=30, follow_redirects=False)
        try:
            response = await client.get(url, params={"content": "true"})
            response.raise_for_status()
            payload = response.json()
        finally:
            if owned_client:
                await client.aclose()

        company = self.board_token.replace("-", " ").title()
        jobs: list[JobPosting] = []
        for raw in payload.get("jobs", []):
            jobs.append(
                JobPosting(
                    source="greenhouse",
                    source_job_id=str(raw["id"]),
                    company=company,
                    title=raw["title"],
                    location=(raw.get("location") or {}).get("name", ""),
                    description=html_to_text(raw.get("content")),
                    application_url=raw["absolute_url"],
                    metadata={"updated_at": raw.get("updated_at")},
                )
            )
        return jobs


class LeverSource:
    def __init__(self, site: str, client: httpx.AsyncClient | None = None):
        if not TOKEN_PATTERN.fullmatch(site):
            raise ValueError("Invalid Lever site name")
        self.site = site
        self.client = client

    async def fetch(self) -> list[JobPosting]:
        url = f"https://api.lever.co/v0/postings/{self.site}"
        owned_client = self.client is None
        client = self.client or httpx.AsyncClient(timeout=30, follow_redirects=False)
        try:
            response = await client.get(url, params={"mode": "json"})
            response.raise_for_status()
            payload = response.json()
        finally:
            if owned_client:
                await client.aclose()

        company = self.site.replace("-", " ").title()
        jobs: list[JobPosting] = []
        for raw in payload:
            categories = raw.get("categories") or {}
            jobs.append(
                JobPosting(
                    source="lever",
                    source_job_id=str(raw["id"]),
                    company=company,
                    title=raw["text"],
                    location=categories.get("location", ""),
                    workplace_type=raw.get("workplaceType"),
                    description=lever_description(raw),
                    application_url=raw["applyUrl"],
                    salary_text=raw.get("salaryDescriptionPlain"),
                    metadata={
                        "team": categories.get("team"),
                        "country": raw.get("country"),
                    },
                )
            )
        return jobs


class AshbySource:
    def __init__(self, board: str, client: httpx.AsyncClient | None = None):
        if not TOKEN_PATTERN.fullmatch(board):
            raise ValueError("Invalid Ashby board name")
        self.board = board
        self.client = client

    async def fetch(self) -> list[JobPosting]:
        url = f"https://api.ashbyhq.com/posting-api/job-board/{self.board}"
        owned_client = self.client is None
        client = self.client or httpx.AsyncClient(timeout=30, follow_redirects=False)
        try:
            response = await client.get(url, params={"includeCompensation": "true"})
            response.raise_for_status()
            payload = response.json()
        finally:
            if owned_client:
                await client.aclose()

        company = self.board.replace("-", " ").title()
        jobs: list[JobPosting] = []
        for raw in payload.get("jobs", []):
            if not raw.get("isListed", True):
                continue
            compensation = raw.get("compensation") or {}
            postal_address = ((raw.get("address") or {}).get("postalAddress") or {})
            jobs.append(
                JobPosting(
                    source="ashby",
                    source_job_id=str(raw["id"]),
                    company=company,
                    title=raw["title"],
                    location=raw.get("location", ""),
                    workplace_type=(raw.get("workplaceType") or "").casefold() or None,
                    description=raw.get("descriptionPlain")
                    or html_to_text(raw.get("descriptionHtml")),
                    application_url=raw["applyUrl"],
                    salary_text=compensation.get("scrapeableCompensationSalarySummary"),
                    metadata={
                        "department": raw.get("department"),
                        "team": raw.get("team"),
                        "published_at": raw.get("publishedAt"),
                        "country": postal_address.get("addressCountry"),
                    },
                )
            )
        return jobs
