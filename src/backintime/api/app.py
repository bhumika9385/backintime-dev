"""FastAPI application factory for loopback-hosted local management."""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backintime.api.routes import router
from backintime.api.schemas import ErrorDetail, ErrorResponse
from backintime.core.config import ConfigStore
from backintime.core.jobs import JobService

_request_id: ContextVar[str] = ContextVar("backintime_request_id", default="-")
_logger = logging.getLogger("backintime.api")
_ALLOWED_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "[::1]", "::1"})
_DEVELOPMENT_ORIGINS = frozenset(
    {
        ("http", "127.0.0.1", 5173),
        ("http", "localhost", 5173),
        ("http", "::1", 5173),
    }
)
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def create_app(
    *,
    config_store: ConfigStore | None = None,
    job_service: JobService | None = None,
    job_state_path: Path | None = None,
) -> FastAPI:
    """Create an API application with injectable configuration and job services."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        service = app.state.job_service
        if isinstance(service, JobService):
            service.shutdown()

    app = FastAPI(
        title="BackInTime API",
        version="0.1.0",
        description="Loopback-local API for the independent BackInTime application.",
        lifespan=lifespan,
    )
    app.state.config_store = config_store or ConfigStore(ConfigStore.default_path())
    app.state.job_service = job_service
    app.state.job_state_path = job_state_path or JobService.default_path()
    app.include_router(router)

    @app.middleware("http")
    async def attach_request_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = uuid4().hex
        token = _request_id.set(request_id)
        try:
            host = request.headers.get("host")
            if not _is_allowed_host(host):
                return _error_response(
                    400,
                    "HOST_NOT_ALLOWED",
                    "The request Host must be a loopback address.",
                )

            origin = request.headers.get("origin")
            if origin is not None:
                allowed_origins = _DEVELOPMENT_ORIGINS | {_request_origin(request)}
                if _origin_key(origin) not in allowed_origins:
                    return _error_response(
                        403,
                        "ORIGIN_NOT_ALLOWED",
                        "The browser request origin is not allowed.",
                    )
            elif request.method.upper() not in _SAFE_METHODS:
                return _error_response(
                    403,
                    "ORIGIN_REQUIRED",
                    "State-changing requests must include an allowed Origin header.",
                )

            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            _request_id.reset(token)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        del request
        if isinstance(exc.detail, Mapping):
            code = str(exc.detail.get("code", "HTTP_ERROR"))
            message = str(exc.detail.get("message", "Request failed."))
        else:
            code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
            message = str(exc.detail)
        if code == "CONFIGURATION_INVALID":
            _logger.error("Configuration failure (request_id=%s): %s", _request_id.get(), message)
            message = "The application configuration could not be loaded or validated."
        return _error_response(exc.status_code, code, message)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        del request
        fields = ", ".join(".".join(str(part) for part in error["loc"]) for error in exc.errors())
        message = f"Request validation failed{f' for {fields}' if fields else ''}."
        return _error_response(422, "REQUEST_INVALID", message)

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        _logger.exception(
            "Unhandled API error for %s %s (request_id=%s)",
            request.method,
            request.url.path,
            _request_id.get(),
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        return _error_response(500, "INTERNAL_ERROR", "An unexpected server error occurred.")

    return app


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorDetail(
            code=code,
            message=message,
            request_id=_request_id.get(),
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(),
        headers={"X-Request-ID": _request_id.get()},
    )


def _is_allowed_host(host: str | None) -> bool:
    """Accept only explicit loopback hostnames, rejecting rebinding host values."""
    if host is None:
        return False
    try:
        parsed = urlsplit(f"//{host}")
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.hostname is not None
        and parsed.hostname.lower() in _ALLOWED_HOSTNAMES
        and parsed.username is None
        and parsed.password is None
        and parsed.path == ""
        and parsed.query == ""
        and parsed.fragment == ""
        and (port is None or 1 <= port <= 65535)
    )


def _origin_key(origin: str) -> tuple[str, str, int] | None:
    """Normalize a strict HTTP(S) origin into a comparable scheme/host/port tuple."""
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except ValueError:
        return None

    if (
        parsed.scheme.lower() not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != ""
        or parsed.query
        or parsed.fragment
    ):
        return None

    scheme = parsed.scheme.lower()
    effective_port = port or (443 if scheme == "https" else 80)
    return scheme, parsed.hostname.lower(), effective_port


def _request_origin(request: Request) -> tuple[str, str, int]:
    """Return the normalized origin of the current loopback API request."""
    origin = _origin_key(f"{request.url.scheme}://{request.headers['host']}")
    if origin is None:
        raise RuntimeError("Validated request host did not produce a valid origin.")
    return origin


app = create_app()
