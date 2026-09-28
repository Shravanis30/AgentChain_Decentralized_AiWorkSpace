from typing import Any
from pydantic import BaseModel, Field


class AgentResult(BaseModel):
    execution_id: str
    status: str = Field(default="SUCCEEDED")
    output: dict[str, Any]
    execution_time_ms: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }
