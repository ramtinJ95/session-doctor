from __future__ import annotations

from decimal import Decimal

from pydantic import Field

from .common import Metadata, OptionalDatetime, SessionDoctorModel
from .semantics import UsageSemantics


class ModelUsage(SessionDoctorModel):
    model_usage_id: str
    session_id: str
    source_event_id: str | None = None
    timestamp: OptionalDatetime = None
    provider: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    total_tokens: int | None = None
    cost: Decimal | None = None
    aggregation_semantics: UsageSemantics = UsageSemantics.AGGREGATION_UNAVAILABLE
    metadata: Metadata = Field(default_factory=dict)
