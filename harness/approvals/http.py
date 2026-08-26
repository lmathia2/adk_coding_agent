"""Framework-neutral HTTP adapter for approval APIs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .contracts import ApprovalDecision, ApprovalSubmission
from .store import ApprovalStore


class ApprovalHTTPRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: Literal["GET", "POST"]
    path: str
    json_body: dict[str, Any] = Field(default_factory=dict)
    query: dict[str, str] = Field(default_factory=dict)


class ApprovalHTTPResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status_code: int = Field(ge=100, le=599)
    body: dict[str, Any] | list[dict[str, Any]]


class ApprovalHTTPTransport:
    """Adapt typed requests to HTTP without choosing a server framework."""

    def __init__(self, store: ApprovalStore) -> None:
        self.store = store

    @staticmethod
    def _response(status_code: int, value: Any) -> ApprovalHTTPResponse:
        if isinstance(value, BaseModel):
            body = value.model_dump(mode="json")
        elif isinstance(value, list):
            body = [item.model_dump(mode="json") for item in value]
        else:
            body = value
        return ApprovalHTTPResponse(status_code=status_code, body=body)

    def handle(self, request: ApprovalHTTPRequest) -> ApprovalHTTPResponse:
        try:
            if request.method == "POST" and request.path == "/approval-requests":
                submission = ApprovalSubmission.model_validate(request.json_body)
                existing = self.store.for_fingerprint(
                    submission.task_id,
                    submission.fingerprint,
                )
                persisted = self.store.submit(submission)
                return self._response(200 if existing else 201, persisted)

            if request.method == "GET" and request.path == "/approval-requests":
                limit_text = request.query.get("limit", "100")
                limit = max(min(int(limit_text), 1_000), 1)
                requests = self.store.list(
                    task_id=request.query.get("task_id"),
                    status=request.query.get("status"),
                    limit=limit,
                )
                return self._response(200, requests)

            prefix = "/approval-requests/"
            if request.method == "GET" and request.path.startswith(prefix):
                request_id = request.path.removeprefix(prefix)
                persisted = self.store.get(request_id)
                if persisted is None:
                    return self._response(404, {"error": "approval request not found"})
                return self._response(200, persisted)

            if request.method == "POST" and request.path == "/approval-decisions":
                decision = ApprovalDecision.model_validate(request.json_body)
                return self._response(200, self.store.submit_decision(decision))
        except ValidationError as error:
            return self._response(422, {"error": str(error)})
        except KeyError:
            return self._response(404, {"error": "approval request not found"})
        except (TypeError, ValueError) as error:
            return self._response(409, {"error": str(error)})

        return self._response(404, {"error": "route not found"})


__all__ = [
    "ApprovalHTTPRequest",
    "ApprovalHTTPResponse",
    "ApprovalHTTPTransport",
]
