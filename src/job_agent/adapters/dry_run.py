from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .base import ApplicationAdapter, InspectionResult
from ..models import ApplicationPlan, SubmissionReceipt


LEVER_FIELD_MAP = {
    "name": ("full_name", "Full legal name", "identity"),
    "email": ("email", "Email address", "contact"),
    "phone": ("phone", "Phone number", "contact"),
    "location": ("city", "Current location", "contact"),
    "org": ("current_employer", "Current company", "experience"),
    "urls[Your LinkedIn Profile]": (
        "linkedin_url",
        "LinkedIn profile",
        "contact",
    ),
}


class _LeverFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_application_form = False
        self.inputs: list[dict[str, str | bool]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "form" and attributes.get("id") == "application-form":
            self.in_application_form = True
            return
        if tag != "input" or not self.in_application_form:
            return
        name = attributes.get("name")
        if name:
            self.inputs.append(
                {
                    "name": name,
                    "type": attributes.get("type", "text"),
                    "required": "required" in attributes,
                }
            )

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self.in_application_form:
            self.in_application_form = False


def inspect_lever_html(html: str) -> InspectionResult:
    parser = _LeverFormParser()
    parser.feed(html)
    lowered = html.casefold()
    triggers: list[str] = []
    if "h-captcha-response" in lowered or "hcaptcha" in lowered:
        triggers.append("captcha_present")

    fields: list[dict] = []
    ignored = {
        "resume",
        "selectedLocation",
        "accountId",
        "linkedInData",
        "origin",
        "referer",
        "timezone",
        "socialReferralKey",
        "socialSource",
        "resumeStorageId",
        "h-captcha-response",
        "source",
    }
    for input_field in parser.inputs:
        name = str(input_field["name"])
        if name in ignored or input_field["type"] in {"hidden", "file", "submit"}:
            continue
        mapped = LEVER_FIELD_MAP.get(name)
        if mapped is None:
            fields.append(
                {
                    "field_name": name,
                    "question": name,
                    "category_hint": "unknown",
                    "required": bool(input_field["required"]),
                }
            )
            triggers.append("unknown_question")
            continue
        field_name, question, category = mapped
        fields.append(
            {
                "field_name": field_name,
                "question": question,
                "category_hint": category,
                "required": bool(input_field["required"]),
            }
        )
    return InspectionResult(
        fields=fields,
        review_triggers=sorted(set(triggers)),
    )


class DryRunAdapter(ApplicationAdapter):
    """Writes local evidence and never visits or submits to an external site."""

    def __init__(
        self,
        artifact_directory: str | Path,
        inspect_remote_forms: bool = False,
    ):
        self.artifact_directory = Path(artifact_directory)
        self.inspect_remote_forms = inspect_remote_forms

    async def inspect(self, application_url: str) -> InspectionResult:
        if self.inspect_remote_forms:
            parsed = urlparse(application_url)
            if parsed.hostname == "jobs.lever.co":
                async with httpx.AsyncClient(
                    timeout=30, follow_redirects=False
                ) as client:
                    response = await client.get(application_url)
                if response.is_redirect:
                    return InspectionResult(review_triggers=["external_redirect"])
                response.raise_for_status()
                return inspect_lever_html(response.text)

        # Representative fields used only by isolated local unit tests.
        return InspectionResult(
            fields=[
                {
                    "field_name": "full_name",
                    "question": "Full legal name",
                    "category_hint": "identity",
                },
                {
                    "field_name": "email",
                    "question": "Email address",
                    "category_hint": "contact",
                },
                {
                    "field_name": "phone",
                    "question": "Phone number",
                    "category_hint": "contact",
                },
                {
                    "field_name": "linkedin_url",
                    "question": "LinkedIn profile",
                    "category_hint": "contact",
                },
            ],
            review_triggers=[],
        )

    async def submit(
        self, plan: ApplicationPlan, idempotency_key: str
    ) -> SubmissionReceipt:
        self.artifact_directory.mkdir(parents=True, exist_ok=True)
        plan_path = self.artifact_directory / f"{idempotency_key}.plan.json"
        plan_path.write_text(
            json.dumps(plan.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return SubmissionReceipt(
            status="DRY_RUN_READY",
            source_job_id=plan.source_job_id,
            company=plan.company,
            application_url=str(plan.application_url),
            idempotency_key=idempotency_key,
            confirmation="No external request was sent.",
            artifacts=[str(plan_path.resolve())],
        )
