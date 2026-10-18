"""FastAPI dependencies and application state accessors."""

from threading import Lock
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from backintime.core.config import ConfigStore
from backintime.core.exceptions import JobStoreError
from backintime.core.jobs import JobService

_job_service_lock = Lock()


def get_config_store(request: Request) -> ConfigStore:
    """Return the configured store for this app instance."""
    config_store = request.app.state.config_store
    if not isinstance(config_store, ConfigStore):
        raise RuntimeError("Application configuration store was not initialized.")
    return config_store


ConfigStoreDependency = Annotated[ConfigStore, Depends(get_config_store)]


def get_job_service(request: Request) -> JobService:
    """Return the app's job service, starting it on first use.

    Starting lazily keeps importing or creating the app free of side effects;
    the service takes its single-process lock only when jobs are first used.
    """
    state = request.app.state
    with _job_service_lock:
        if state.job_service is None:
            try:
                state.job_service = JobService(state.job_state_path)
            except JobStoreError as exc:
                raise HTTPException(
                    status_code=503,
                    detail={"code": "JOB_SERVICE_UNAVAILABLE", "message": str(exc)},
                ) from exc
        job_service = state.job_service
    if not isinstance(job_service, JobService):
        raise RuntimeError("Application job service was not initialized.")
    return job_service


JobServiceDependency = Annotated[JobService, Depends(get_job_service)]
