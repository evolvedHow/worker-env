"""Pydantic models describing context records and API payloads."""

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SLUG_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
MAX_LABEL_LENGTH = 256
MAX_VALUE_LENGTH = 8192


def _check_label(value: str) -> str:
    """Validate a label by rejecting blank input.

    Args:
        value: Raw label text supplied by the client.

    Returns:
        The unchanged, validated label.

    Raises:
        ValueError: If the label is empty or whitespace-only.
    """
    if not value.strip():
        raise ValueError("label must not be empty")
    return value


def _check_value(value: str) -> str:
    """Validate a context value by rejecting blank input.

    Args:
        value: Raw value text supplied by the client.

    Returns:
        The unchanged, validated value.

    Raises:
        ValueError: If the value is empty or whitespace-only.
    """
    if not value.strip():
        raise ValueError("value must not be empty")
    return value


class ContextCreate(BaseModel):
    """Creation payload for a new context.

    These four fields are the only client-provided inputs; ``usage_count``
    and ``create_date`` are computed by the server at creation time.
    """

    model_config = ConfigDict(extra="forbid")

    namespace: Annotated[str, Field(min_length=1, max_length=128, pattern=SLUG_PATTERN)]
    name: Annotated[str, Field(min_length=1, max_length=128, pattern=SLUG_PATTERN)]
    label: Annotated[str, Field(min_length=1, max_length=MAX_LABEL_LENGTH)]
    value: Annotated[str, Field(min_length=1, max_length=MAX_VALUE_LENGTH)]

    @field_validator("label")
    @classmethod
    def check_label(cls, value: str) -> str:
        """Reject blank labels.

        Args:
            value: Candidate label.

        Returns:
            The validated label.
        """
        return _check_label(value)

    @field_validator("value")
    @classmethod
    def check_value(cls, value: str) -> str:
        """Reject blank values.

        Args:
            value: Candidate value.

        Returns:
            The validated value.
        """
        return _check_value(value)


class Context(BaseModel):
    """A stored context record as returned by the API."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    namespace: str
    name: str
    label: str
    value: str
    usage_count: Annotated[int, Field(ge=0)]
    create_date: datetime


class ContextUpdate(BaseModel):
    """Partial update payload restricted to the mutable fields.

    ``namespace`` and ``name`` are immutable and rejected when present;
    ``usage_count`` and ``create_date`` are server-managed and likewise
    rejected. At least one mutable field must be supplied.
    """

    model_config = ConfigDict(extra="forbid")

    label: Annotated[str | None, Field(default=None, min_length=1, max_length=MAX_LABEL_LENGTH)] = (
        None
    )
    value: Annotated[str | None, Field(default=None, min_length=1, max_length=MAX_VALUE_LENGTH)] = (
        None
    )

    @field_validator("label")
    @classmethod
    def check_label(cls, value: str | None) -> str | None:
        """Reject blank labels while allowing an omitted field.

        Args:
            value: Candidate label or ``None`` when omitted.

        Returns:
            The validated label, or ``None`` when omitted.
        """
        if value is None:
            return None
        return _check_label(value)

    @field_validator("value")
    @classmethod
    def check_value(cls, value: str | None) -> str | None:
        """Reject blank values while allowing an omitted field.

        Args:
            value: Candidate value or ``None`` when omitted.

        Returns:
            The validated value, or ``None`` when omitted.
        """
        if value is None:
            return None
        return _check_value(value)

    @model_validator(mode="after")
    def require_mutable_field(self) -> Self:
        """Require at least one mutable field in the payload.

        Returns:
            The validated update payload.

        Raises:
            ValueError: If both ``label`` and ``value`` are absent.
        """
        if self.label is None and self.value is None:
            raise ValueError("at least one of 'label' or 'value' must be provided")
        return self


class ContextList(BaseModel):
    """Envelope returned by list operations."""

    model_config = ConfigDict(extra="forbid")

    contexts: list[Context]
    count: Annotated[int, Field(ge=0)]


class ErrorDetail(BaseModel):
    """Single-field error payload referenced by OpenAPI responses."""

    model_config = ConfigDict(extra="forbid")

    detail: str


class HealthStatus(BaseModel):
    """Liveness payload returned by ``GET /health``."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"
    version: str
