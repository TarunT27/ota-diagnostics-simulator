"""FastAPI transport layer for the OTA diagnostics simulator."""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

from ota_simulator.domain import Controller, DiagnosticEvent, FaultScenario, SemanticVersion
from ota_simulator.repository import SQLiteRepository
from ota_simulator.service import ControllerNotFoundError, SimulatorService

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CONTROLLER_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
MAX_REQUEST_BODY_BYTES = 16_384
LOGGER = logging.getLogger(__name__)


class DiagnosticTroubleCodeResponse(BaseModel):
    code: str
    title: str
    detail: str


class ControllerResponse(BaseModel):
    id: str
    name: str
    current_version: str
    previous_version: str | None
    health: str
    stage: str
    voltage: float
    connectivity: str
    last_seen: str
    active_dtcs: list[DiagnosticTroubleCodeResponse]


class DiagnosticEventResponse(BaseModel):
    sequence: int | None
    timestamp: str
    controller_id: str | None
    severity: str
    code: str
    message: str
    stage: str


class FleetSummaryResponse(BaseModel):
    total_controllers: int
    healthy_controllers: int
    attention_controllers: int
    active_dtcs: int


class StatusResponse(BaseModel):
    status: str


class MessageResponse(BaseModel):
    message: str


class SuccessEnvelope[DataT](BaseModel):
    success: Literal[True]
    data: DataT


class ErrorDetailResponse(BaseModel):
    code: str
    message: str
    details: object | None = None


class ErrorEnvelope(BaseModel):
    success: Literal[False]
    error: ErrorDetailResponse


class UpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_version: Annotated[str, StringConstraints(max_length=32)]
    fault: FaultScenario = FaultScenario.NONE

    @field_validator("target_version")
    @classmethod
    def validate_target_version(cls, value: str) -> str:
        SemanticVersion.parse(value)
        return value


class RequestRateLimiter:
    def __init__(
        self,
        max_requests: int = 120,
        window_seconds: int = 60,
        max_identifiers: int = 2_048,
    ) -> None:
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._max_identifiers = max_identifiers
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, identifier: str) -> tuple[bool, int]:
        now = time.monotonic()
        with self._lock:
            if identifier not in self._requests and len(self._requests) >= self._max_identifiers:
                self._requests.pop(next(iter(self._requests)))
            requests = self._requests[identifier]
            while requests and now - requests[0] >= self._window_seconds:
                requests.popleft()
            if len(requests) >= self._max_requests:
                return False, 0
            requests.append(now)
            return True, self._max_requests - len(requests)


def create_app(*, service: SimulatorService | None = None) -> FastAPI:
    simulator = service or _default_service()
    read_limiter = RequestRateLimiter()
    write_limiter = RequestRateLimiter(max_requests=30)
    app = FastAPI(
        title="OTA Fleet Diagnostics Lab API",
        version="1.0.0",
        description="A deterministic local simulator. It does not flash real vehicle hardware.",
        docs_url=None,
        redoc_url=None,
    )
    app.state.simulator = simulator

    @app.middleware("http")
    async def secure_and_limit(request: Request, call_next: Any) -> Any:
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if origin is not None and not _origin_allowed(origin, request):
                return _security_headers(
                    JSONResponse(
                        status_code=403,
                        content=_error(
                            "origin_not_allowed", "Cross-origin state changes are not allowed."
                        ),
                    )
                )
            content_length = request.headers.get("content-length")
            if content_length is None or (
                not content_length.isdecimal() or int(content_length) > MAX_REQUEST_BODY_BYTES
            ):
                return _security_headers(
                    JSONResponse(
                        status_code=413,
                        content=_error("request_too_large", "Request body is too large."),
                    )
                )
        if request.url.path.startswith("/api/"):
            identifier = request.client.host if request.client else "local"
            is_write = request.method in {"POST", "PUT", "PATCH", "DELETE"}
            limiter = write_limiter if is_write else read_limiter
            limit = 30 if is_write else 120
            allowed, remaining = limiter.check(identifier)
            if not allowed:
                response = JSONResponse(
                    status_code=429,
                    content=_error(
                        "rate_limit_exceeded", "Rate limit exceeded. Try again shortly."
                    ),
                )
                response.headers["Retry-After"] = "60"
                return _security_headers(response)
        else:
            remaining = 120
            limit = 120
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return _security_headers(response)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request, error: RequestValidationError
    ) -> JSONResponse:
        details = [
            {
                "field": ".".join(str(part) for part in item["loc"] if part != "body"),
                "message": item["msg"],
            }
            for item in error.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_error("validation_error", "Request validation failed.", details),
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_request: Request, error: Exception) -> JSONResponse:
        LOGGER.exception("Unhandled simulator request failure", exc_info=error)
        return _security_headers(
            JSONResponse(
                status_code=500,
                content=_error("internal_error", "An unexpected error occurred."),
            )
        )

    @app.get("/health", response_model=SuccessEnvelope[StatusResponse])
    def health() -> dict[str, object]:
        return _success({"status": "ok"})

    @app.get("/api/v1/summary", response_model=SuccessEnvelope[FleetSummaryResponse])
    def summary() -> dict[str, object]:
        return _success(simulator.summary())

    @app.get(
        "/api/v1/controllers",
        response_model=SuccessEnvelope[list[ControllerResponse]],
    )
    def list_controllers() -> dict[str, object]:
        return _success(
            [_controller_data(controller) for controller in simulator.list_controllers()]
        )

    @app.get(
        "/api/v1/controllers/{controller_id}",
        response_model=SuccessEnvelope[ControllerResponse],
        responses={404: {"model": ErrorEnvelope}, 422: {"model": ErrorEnvelope}},
    )
    def get_controller(controller_id: str) -> JSONResponse | dict[str, object]:
        invalid = _validate_controller_id(controller_id)
        if invalid is not None:
            return invalid
        try:
            return _success(_controller_data(simulator.get_controller(controller_id)))
        except ControllerNotFoundError:
            return JSONResponse(
                status_code=404,
                content=_error("controller_not_found", "Controller was not found."),
            )

    @app.get(
        "/api/v1/events",
        response_model=SuccessEnvelope[list[DiagnosticEventResponse]],
        responses={404: {"model": ErrorEnvelope}, 422: {"model": ErrorEnvelope}},
    )
    def list_events(
        controller_id: Annotated[str | None, Query(max_length=40)] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ) -> JSONResponse | dict[str, object]:
        if controller_id is not None:
            invalid = _validate_controller_id(controller_id)
            if invalid is not None:
                return invalid
        try:
            events = simulator.list_events(controller_id=controller_id, limit=limit)
        except ControllerNotFoundError:
            return JSONResponse(
                status_code=404,
                content=_error("controller_not_found", "Controller was not found."),
            )
        return _success([_event_data(event) for event in events])

    @app.post(
        "/api/v1/controllers/{controller_id}/updates",
        response_model=SuccessEnvelope[ControllerResponse],
        responses={
            404: {"model": ErrorEnvelope},
            409: {"model": ErrorEnvelope},
            422: {"model": ErrorEnvelope},
        },
    )
    def run_update(controller_id: str, request: UpdateRequest) -> JSONResponse | dict[str, object]:
        invalid = _validate_controller_id(controller_id)
        if invalid is not None:
            return invalid
        try:
            controller = simulator.run_update(controller_id, request.target_version, request.fault)
        except ControllerNotFoundError:
            return JSONResponse(
                status_code=404,
                content=_error("controller_not_found", "Controller was not found."),
            )
        except ValueError as error:
            return JSONResponse(
                status_code=409,
                content=_error("state_conflict", str(error)),
            )
        return _success(_controller_data(controller))

    @app.post(
        "/api/v1/controllers/{controller_id}/rollback",
        response_model=SuccessEnvelope[ControllerResponse],
        responses={
            404: {"model": ErrorEnvelope},
            409: {"model": ErrorEnvelope},
            422: {"model": ErrorEnvelope},
        },
    )
    def rollback(controller_id: str) -> JSONResponse | dict[str, object]:
        invalid = _validate_controller_id(controller_id)
        if invalid is not None:
            return invalid
        try:
            controller = simulator.rollback(controller_id)
        except ControllerNotFoundError:
            return JSONResponse(
                status_code=404,
                content=_error("controller_not_found", "Controller was not found."),
            )
        except ValueError:
            return JSONResponse(
                status_code=409,
                content=_error("state_conflict", "Controller has no previous version to restore."),
            )
        return _success(_controller_data(controller))

    @app.post("/api/v1/reset", response_model=SuccessEnvelope[MessageResponse])
    def reset() -> dict[str, object]:
        simulator.reset()
        return _success({"message": "Lab reset complete."})

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def dashboard() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app


def _default_service() -> SimulatorService:
    database_path = Path(os.getenv("OTA_DATABASE_PATH", "data/ota-lab.db"))
    return SimulatorService(SQLiteRepository(database_path))


def _success(data: object) -> dict[str, object]:
    return {"success": True, "data": data}


def _error(code: str, message: str, details: object | None = None) -> dict[str, object]:
    error: dict[str, object] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"success": False, "error": error}


def _validate_controller_id(controller_id: str) -> JSONResponse | None:
    if CONTROLLER_ID_PATTERN.fullmatch(controller_id) is not None:
        return None
    return JSONResponse(
        status_code=422,
        content=_error("validation_error", "Controller ID has an invalid format."),
    )


def _origin_allowed(origin: str, request: Request) -> bool:
    parsed = urlsplit(origin)
    return (
        parsed.scheme == request.url.scheme
        and parsed.netloc.lower() == request.headers.get("host", "").lower()
    )


def _controller_data(controller: Controller) -> dict[str, object]:
    return {
        "id": controller.id,
        "name": controller.name,
        "current_version": str(controller.current_version),
        "previous_version": (
            None if controller.previous_version is None else str(controller.previous_version)
        ),
        "health": controller.health.value,
        "stage": controller.stage.value,
        "voltage": controller.voltage,
        "connectivity": controller.connectivity,
        "last_seen": controller.last_seen,
        "active_dtcs": [
            {"code": dtc.code, "title": dtc.title, "detail": dtc.detail}
            for dtc in controller.active_dtcs
        ],
    }


def _event_data(event: DiagnosticEvent) -> dict[str, object]:
    return {
        "sequence": event.sequence,
        "timestamp": event.timestamp.isoformat(),
        "controller_id": event.controller_id,
        "severity": event.severity.value,
        "code": event.code,
        "message": event.message,
        "stage": event.stage.value,
    }


def _security_headers[ResponseT: Response](response: ResponseT) -> ResponseT:
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
        "base-uri 'self'; frame-ancestors 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response
