"""API routes backed by core services."""

import logging
import platform
import shutil
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Query, Response

from backintime import __version__
from backintime.api.dependencies import ConfigStoreDependency
from backintime.api.schemas import (
    HealthResponse,
    ProfileCreateRequest,
    ProfileListResponse,
    ProfilePatchRequest,
    ProfileResponse,
    SnapshotDirectoryResponse,
    SnapshotEntryResponse,
    SystemInfoResponse,
)
from backintime.core.config import Configuration
from backintime.core.exceptions import (
    ConfigurationConflictError,
    ConfigurationError,
    RepositoryError,
    SnapshotNotFoundError,
    ValidationError,
)
from backintime.core.profile import Profile
from backintime.core.repository import Repository
from backintime.core.snapshot_id import SnapshotID

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


@router.get(
    "/profiles/{profile_id}/snapshots/{snapshot_id}/files",
    response_model=SnapshotDirectoryResponse,
)
def browse_snapshot_files(
    profile_id: str,
    snapshot_id: str,
    config_store: ConfigStoreDependency,
    path: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=100),
    after: str | None = Query(default=None),
) -> SnapshotDirectoryResponse:
    """Browse one safe, bounded page in a healthy profile snapshot."""
    try:
        configuration = config_store.load()
    except ConfigurationError as exc:
        _logger.error("Configuration read failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={
                "code": "CONFIGURATION_INVALID",
                "message": "The application configuration could not be loaded or validated.",
            },
        ) from exc

    profile = next(
        (item for item in configuration.profiles if item.profile_id == profile_id),
        None,
    )
    if profile is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "PROFILE_NOT_FOUND", "message": "The selected profile does not exist."},
        )

    try:
        validated_snapshot_id = SnapshotID.parse(snapshot_id)
    except ValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_SNAPSHOT_ID", "message": "The snapshot ID is invalid."},
        ) from exc

    try:
        listing = Repository(profile.target).browse_snapshot(
            validated_snapshot_id,
            path,
            limit=limit,
            after=after,
        )
    except SnapshotNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "SNAPSHOT_NOT_FOUND",
                "message": "The selected snapshot was not found.",
            },
        ) from exc
    except ValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "SNAPSHOT_PATH_NOT_ALLOWED",
                "message": "The requested path is invalid or outside the selected snapshot.",
            },
        ) from exc
    except RepositoryError as exc:
        _logger.error("Snapshot browsing failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={
                "code": "SNAPSHOT_REPOSITORY_UNAVAILABLE",
                "message": "The selected snapshot repository could not be read.",
            },
        ) from exc

    return SnapshotDirectoryResponse(
        snapshot_id=str(validated_snapshot_id),
        path=listing.path,
        entries=[
            SnapshotEntryResponse(
                name=entry.name,
                kind=entry.kind,
                size_bytes=entry.size_bytes,
                modified_time_ns=entry.modified_time_ns,
            )
            for entry in listing.entries
        ],
        next_cursor=listing.next_cursor,
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
