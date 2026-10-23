from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict, Field

from ..models import ApplicationPlan, SubmissionReceipt


class InspectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fields: list[dict] = Field(default_factory=list)
    review_triggers: list[str] = Field(default_factory=list)


class ApplicationAdapter(ABC):
    @abstractmethod
    async def inspect(self, application_url: str) -> InspectionResult:
        """Inspect a form without changing external state."""

    @abstractmethod
    async def submit(
        self, plan: ApplicationPlan, idempotency_key: str
    ) -> SubmissionReceipt:
        """Perform the adapter action after policy approval."""

