"""Pydantic models for the simple vault API."""

from pydantic import BaseModel, ConfigDict, Field


class Secret(BaseModel):
    """A secret stored in the vault.

    Attributes:
        context: Logical grouping (e.g., "homelab", "finance", "commerce").
                 Derived from CSV filename (e.g., homelab.secrets.csv).
        key: Secret identifier within the context.
        value: The actual secret content.
        label: Human-readable description of the secret.
        update_count: Number of times this secret has been updated.
        updated_at: ISO 8601 timestamp of last update.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    context: str
    key: str
    value: str
    label: str = ""
    update_count: int = Field(default=0, ge=0)
    updated_at: str


class SecretList(BaseModel):
    """Response envelope for list operations.

    Attributes:
        secrets: List of secrets matching the query.
        count: Total number of secrets returned.
    """

    model_config = ConfigDict(extra="forbid")

    secrets: list[Secret]
    count: int
