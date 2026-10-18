"""API routes backed by core services."""

import logging
import platform
import shutil
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Response

from backintime import __version__
from backintime.api.dependencies import ConfigStoreDependency, JobServiceDependency
from backintime.api.schemas import (
    HealthResponse,
    JobListResponse,
    JobLogEntryResponse,
    JobResponse,
    ProfileCreateRequest,
    ProfileListResponse,
    ProfilePatchRequest,
    ProfileResponse,
    SystemInfoResponse,
)
from backintime.core.config import Configuration
from backintime.core.exceptions import (
    ConfigurationConflictError,
    ConfigurationError,
    JobConflictError,
    JobStoreError,
    ValidationError,
)
from backintime.core.jobs import BackupJob
from backintime.core.profile import Profile

router = APIRouter(prefix="/api/v1")
_logger = logging.getLogger("backintime.api")


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Report that the API process is responsive without inspecting user config."""
    return HealthResponse(status="ok", version=__version__)


@router.get("/system/info", response_model=SystemInfoResponse)
def system_info() -> SystemInfoResponse:
    """Report runtime capabilities without exposing filesystem locations."""
    return SystemInfoResponse(
        platform=platform.system(),
        python_version=platform.python_version(),
        rsync_available=shutil.which("rsync") is not None,
    )


@router.get("/profiles", response_model=ProfileListResponse)
def list_profiles(
    config_store: ConfigStoreDependency,
    response: Response,
) -> ProfileListResponse:
    """List validated configured profiles using the existing core ConfigStore."""
    try:
        configuration, revision = config_store.load_with_revision()
    except ConfigurationError as exc:
        raise _configuration_error(exc) from exc

    response.headers["ETag"] = _etag(revision)
    return ProfileListResponse(
        profiles=[_profile_response(profile) for profile in configuration.profiles]
    )


@router.get("/profiles/{profile_id}", response_model=ProfileResponse)
def get_profile(
    profile_id: str,
    config_store: ConfigStoreDependency,
    response: Response,
) -> ProfileResponse:
    """Return one configured profile by its stable identifier."""
    try:
        configuration, revision = config_store.load_with_revision()
    except ConfigurationError as exc:
        raise _configuration_error(exc) from exc

    for profile in configuration.profiles:
        if profile.profile_id == profile_id:
            response.headers["ETag"] = _etag(revision)
            return _profile_response(profile)
    raise HTTPException(
        status_code=404,
        detail={"code": "PROFILE_NOT_FOUND", "message": "The selected profile does not exist."},
    )


@router.post("/profiles", response_model=ProfileResponse, status_code=201)
def create_profile(
    payload: ProfileCreateRequest,
    config_store: ConfigStoreDependency,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> ProfileResponse:
    """Create a profile using an optimistic configuration revision."""
    expected_revision = _expected_revision(if_match)
    profile = Profile(
        profile_id=payload.profile_id,
        name=payload.name,
        sources=tuple(Path(source) for source in payload.sources),
        target=Path(payload.target),
        excludes=tuple(payload.excludes),
    )

    def add_profile(configuration: Configuration) -> Configuration:
        if any(item.profile_id == profile.profile_id for item in configuration.profiles):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "PROFILE_EXISTS",
                    "message": "A profile with this ID already exists.",
                },
            )
        return replace(configuration, profiles=configuration.profiles + (profile,))

    _, revision = _update_configuration(config_store, expected_revision, add_profile)
    response.headers["ETag"] = _etag(revision)
    return _profile_response(profile)


@router.patch("/profiles/{profile_id}", response_model=ProfileResponse)
def update_profile(
    profile_id: str,
    payload: ProfilePatchRequest,
    config_store: ConfigStoreDependency,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> ProfileResponse:
    """Update a profile without allowing stale clients to overwrite newer edits."""
    expected_revision = _expected_revision(if_match)

    def patch_profile(configuration: Configuration) -> Configuration:
        for index, existing in enumerate(configuration.profiles):
            if existing.profile_id == profile_id:
                updated_profile = replace(
                    existing,
                    name=payload.name if payload.name is not None else existing.name,
                    sources=(
                        tuple(Path(source) for source in payload.sources)
                        if payload.sources is not None
                        else existing.sources
                    ),
                    target=(
                        Path(payload.target) if payload.target is not None else existing.target
                    ),
                    excludes=(
                        tuple(payload.excludes)
                        if payload.excludes is not None
                        else existing.excludes
                    ),
                )
                updated_profiles = list(configuration.profiles)
                updated_profiles[index] = updated_profile
                return replace(configuration, profiles=tuple(updated_profiles))
        raise HTTPException(
            status_code=404,
            detail={"code": "PROFILE_NOT_FOUND", "message": "The selected profile does not exist."},
        )

    updated, revision = _update_configuration(config_store, expected_revision, patch_profile)
    profile = next(item for item in updated.profiles if item.profile_id == profile_id)
    response.headers["ETag"] = _etag(revision)
    return _profile_response(profile)


@router.delete("/profiles/{profile_id}", status_code=204)
def delete_profile(
    profile_id: str,
    config_store: ConfigStoreDependency,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> Response:
    """Remove profile configuration only; this endpoint never deletes target data."""
    expected_revision = _expected_revision(if_match)

    def remove_profile(configuration: Configuration) -> Configuration:
        remaining = tuple(
            profile for profile in configuration.profiles if profile.profile_id != profile_id
        )
        if len(remaining) == len(configuration.profiles):
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "PROFILE_NOT_FOUND",
                    "message": "The selected profile does not exist.",
                },
            )
        return replace(configuration, profiles=remaining)

    _, revision = _update_configuration(config_store, expected_revision, remove_profile)
    return Response(status_code=204, headers={"ETag": _etag(revision)})


@router.post(
    "/profiles/{profile_id}/backups",
    response_model=JobResponse,
    status_code=202,
)
def start_backup(
    profile_id: str,
    config_store: ConfigStoreDependency,
    jobs: JobServiceDependency,
) -> JobResponse:
    """Queue a backup for one profile and return the new job's status."""
    try:
        configuration = config_store.load()
    except ConfigurationError as exc:
        raise _configuration_error(exc) from exc
    profile = next((item for item in configuration.profiles if item.profile_id == profile_id), None)
    if profile is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "PROFILE_NOT_FOUND", "message": "The selected profile does not exist."},
        )
    try:
        job = jobs.submit(profile)
    except JobConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "JOB_CONFLICT",
                "message": "This profile already has a queued or running backup.",
            },
        ) from exc
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "PROFILE_INVALID", "message": str(exc)},
        ) from exc
    except JobStoreError as exc:
        raise _job_store_error(exc) from exc
    return _job_response(job)


@router.get("/jobs", response_model=JobListResponse)
def list_jobs(jobs: JobServiceDependency) -> JobListResponse:
    """List known backup jobs, newest first."""
    return JobListResponse(jobs=[_job_response(job) for job in jobs.list()])


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str, jobs: JobServiceDependency) -> JobResponse:
    """Return the current status of one backup job."""
    job = jobs.get(job_id)
    if job is None:
        raise _job_not_found()
    return _job_response(job)


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse)
def cancel_job(job_id: str, jobs: JobServiceDependency) -> JobResponse:
    """Request cancellation; a running backup stops at its next safe point."""
    try:
        job = jobs.cancel(job_id)
    except JobStoreError as exc:
        raise _job_store_error(exc) from exc
    if job is None:
        raise _job_not_found()
    return _job_response(job)


def _job_response(job: BackupJob) -> JobResponse:
    return JobResponse(
        job_id=job.job_id,
        profile_id=job.profile_id,
        state=job.state,
        created_at=job.created_at,
        updated_at=job.updated_at,
        files_copied=job.files_copied,
        bytes_copied=job.bytes_copied,
        snapshot_id=job.snapshot_id,
        error_message=job.error_message,
        logs=[
            JobLogEntryResponse(timestamp=entry.timestamp, message=entry.message)
            for entry in job.logs
        ],
    )


def _job_not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "JOB_NOT_FOUND", "message": "The selected job does not exist."},
    )


def _job_store_error(exc: JobStoreError) -> HTTPException:
    _logger.error("Background job service error: %s", exc)
    return HTTPException(
        status_code=503,
        detail={"code": "JOB_SERVICE_UNAVAILABLE", "message": str(exc)},
    )


def _profile_response(profile: Profile) -> ProfileResponse:
    return ProfileResponse(
        profile_id=profile.profile_id,
        name=profile.name,
        sources=[str(path) for path in profile.sources],
        target=str(profile.target),
        excludes=list(profile.excludes),
    )


def _expected_revision(if_match: str | None) -> str:
    if if_match is None:
        raise HTTPException(
            status_code=428,
            detail={
                "code": "PRECONDITION_REQUIRED",
                "message": "Fetch the latest configuration and send its ETag in If-Match.",
            },
        )
    if len(if_match) < 3 or not if_match.startswith('"') or not if_match.endswith('"'):
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_ETAG", "message": "If-Match must contain a strong ETag."},
        )
    return if_match[1:-1]


def _etag(revision: str) -> str:
    return f'"{revision}"'


def _update_configuration(
    config_store: ConfigStoreDependency,
    expected_revision: str,
    transform: Callable[[Configuration], Configuration],
) -> tuple[Configuration, str]:
    try:
        return config_store.update(expected_revision, transform)
    except ConfigurationConflictError as exc:
        raise HTTPException(
            status_code=412,
            detail={
                "code": "CONFIGURATION_CHANGED",
                "message": "Configuration changed. Reload it before retrying.",
            },
        ) from exc
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "PROFILE_INVALID", "message": str(exc)},
        ) from exc
    except ConfigurationError as exc:
        raise _configuration_error(exc) from exc


def _configuration_error(exc: ConfigurationError) -> HTTPException:
    _logger.error("Configuration operation failed: %s", exc)
    return HTTPException(
        status_code=503,
        detail={
            "code": "CONFIGURATION_INVALID",
            "message": "The application configuration could not be loaded or validated.",
        },
    )
