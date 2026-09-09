"""Error response contracts.

Declaring these on every route means the 4xx cases show up in /docs with a real
schema and example, rather than leaving a caller to discover the shape of a
failure by causing one in production.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """A single, human-readable failure. Used for every 4xx/5xx we raise."""

    detail: str = Field(description="What went wrong, in plain language.")

    model_config = {
        "json_schema_extra": {"example": {"detail": "event 3f8a…e21 not found"}}
    }


class ValidationErrorItem(BaseModel):
    type: str = Field(description="Machine-readable error kind, e.g. 'enum'.")
    loc: list[Any] = Field(
        description="Path to the offending field, e.g. ['body', 'action_type']."
    )
    msg: str = Field(description="What is wrong with this field.")
    input: Any = Field(default=None, description="The value that was rejected.")


class ValidationErrorResponse(BaseModel):
    """FastAPI's field-level validation failure, documented explicitly."""

    detail: list[ValidationErrorItem]

    model_config = {
        "json_schema_extra": {
            "example": {
                "detail": [
                    {
                        "type": "enum",
                        "loc": ["body", "action_type"],
                        "msg": "Input should be 'tool_call', 'delegation', 'data_access' or 'api_call'",
                        "input": "telepathy",
                    }
                ]
            }
        }
    }


# Reusable `responses=` fragments, so every route documents its failures.
NOT_FOUND = {404: {"model": ErrorResponse, "description": "No such resource"}}
CONFLICT = {409: {"model": ErrorResponse, "description": "Already ingested"}}
BAD_REQUEST = {400: {"model": ErrorResponse, "description": "Referentially invalid"}}
UNPROCESSABLE = {
    422: {
        "model": ValidationErrorResponse,
        "description": "Field-level validation failed",
    }
}
UNAVAILABLE = {
    503: {"model": ErrorResponse, "description": "A backing store is unreachable"}
}
