from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field


class SessionDoctorModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentName(StrEnum):
    CODEX = "codex"
    CLAUDE = "claude"
    PI = "pi"
    UNKNOWN = "unknown"


class NormalizedRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    DEVELOPER = "developer"
    TOOL = "tool"
    UNKNOWN = "unknown"


class SourceKind(StrEnum):
    ROOT_SESSION = "root_session"
    SUBSESSION = "subsession"
    SUBAGENT_METADATA = "subagent_metadata"
    TOOL_RESULT = "tool_result"
    MEMORY = "memory"
    AUXILIARY = "auxiliary"
    UNKNOWN = "unknown"


Confidence = Annotated[float, Field(ge=0.0, le=1.0)]
Metadata = dict[str, Any]
OptionalDatetime = datetime | None

