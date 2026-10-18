"""Validated API request and response models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backintime.core.rsync_output import ChangeKind, Progress, TransferResult


class HealthResponse(BaseModel):
    """Liveness response."""

    status: str
    version: str


class SystemInfoResponse(BaseModel):
    """Non-sensitive runtime capability information."""

    platform: str
    python_version: str
    rsync_available: bool


class ProfileResponse(BaseModel):
    """Read-only view of a configured backup profile."""

    model_config = ConfigDict(frozen=True)

    profile_id: str
    name: str
    sources: list[str]
    target: str
    excludes: list[str]


class ProfileListResponse(BaseModel):
    """Configured profiles."""

    profiles: list[ProfileResponse]


class ProfileCreateRequest(BaseModel):
    """Client-supplied values for creating a profile."""

    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    name: str = Field(min_length=1)
    sources: list[str] = Field(min_length=1)
    target: str = Field(min_length=1)
    excludes: list[str] = Field(default_factory=list)


class ProfilePatchRequest(BaseModel):
    """Partial editable fields for an existing profile."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1)
    sources: list[str] | None = Field(default=None, min_length=1)
    target: str | None = Field(default=None, min_length=1)
    excludes: list[str] | None = None

    @model_validator(mode="after")
    def validate_patch(self) -> ProfilePatchRequest:
        if not self.model_fields_set:
            raise ValueError("At least one profile field must be provided.")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Profile fields must not be null.")
        return self


class ErrorDetail(BaseModel):
    """Stable API error fields."""

    code: str
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    """Consistent API error envelope."""

    error: ErrorDetail


class TransferProgressResponse(BaseModel):
    """Live or final transfer progress; ``percent`` is null when it cannot be known."""

    model_config = ConfigDict(frozen=True)

    indeterminate: bool
    percent: int | None = Field(default=None, ge=0, le=100)
    bytes_transferred: int = Field(ge=0)
    files_remaining: int | None = Field(default=None, ge=0)
    files_total: int | None = Field(default=None, ge=0)

    @classmethod
    def from_progress(cls, progress: Progress | None) -> TransferProgressResponse:
        if progress is None:
            return cls(indeterminate=True, bytes_transferred=0)
        return cls(
            indeterminate=progress.indeterminate,
            percent=progress.percent,
            bytes_transferred=progress.bytes_transferred,
            files_remaining=progress.files_remaining,
            files_total=progress.files_total,
        )


class TransferChangesResponse(BaseModel):
    """Counts of paths rsync reported, by kind of change."""

    model_config = ConfigDict(frozen=True)

    total: int = Field(ge=0)
    by_kind: dict[str, int]


class TransferStatusResponse(BaseModel):
    """Outcome of an rsync transfer for job and API status reporting.

    Diagnostic text can include file paths, so only its size is exposed here;
    the complete output belongs in the application log.
    """

    model_config = ConfigDict(frozen=True)

    status: str
    succeeded: bool
    exit_code: int
    message: str
    progress: TransferProgressResponse
    changes: TransferChangesResponse
    diagnostic_count: int = Field(ge=0)

    @classmethod
    def from_result(cls, result: TransferResult) -> TransferStatusResponse:
        by_kind = {kind.value: 0 for kind in ChangeKind}
        for change in result.changes:
            by_kind[change.kind.value] += 1
        return cls(
            status=result.status.value,
            succeeded=result.succeeded,
            exit_code=result.exit_code,
            message=result.message,
            progress=TransferProgressResponse.from_progress(result.last_progress),
            changes=TransferChangesResponse(total=len(result.changes), by_kind=by_kind),
            diagnostic_count=len(result.diagnostics),
        )
