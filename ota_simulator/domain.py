"""Immutable domain models for deterministic OTA update simulations."""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from functools import total_ordering

VERSION_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


@total_ordering
@dataclass(frozen=True, slots=True)
class SemanticVersion:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, value: str) -> SemanticVersion:
        match = VERSION_PATTERN.fullmatch(value)
        if match is None:
            raise ValueError("version must use major.minor.patch with non-negative integers")
        components = tuple(int(part) for part in match.groups())
        if any(component > 9_999 for component in components):
            raise ValueError("version components must be between 0 and 9999")
        return cls(*components)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemanticVersion):
            return NotImplemented
        return (self.major, self.minor, self.patch) < (
            other.major,
            other.minor,
            other.patch,
        )


class ControllerHealth(StrEnum):
    HEALTHY = "healthy"
    ATTENTION = "attention"


class UpdateStage(StrEnum):
    IDLE = "idle"
    PRECHECK = "precheck"
    DOWNLOADING = "downloading"
    VALIDATING = "validating"
    INSTALLING = "installing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class FaultScenario(StrEnum):
    NONE = "none"
    LOW_VOLTAGE = "low_voltage"
    CONNECTIVITY_LOSS = "connectivity_loss"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    INSTALL_FAILURE = "install_failure"


class EventSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class DiagnosticTroubleCode:
    code: str
    title: str
    detail: str


@dataclass(frozen=True, slots=True)
class Controller:
    id: str
    name: str
    current_version: SemanticVersion
    previous_version: SemanticVersion | None
    health: ControllerHealth
    stage: UpdateStage
    voltage: float
    connectivity: str
    active_dtcs: tuple[DiagnosticTroubleCode, ...]
    last_seen: str = "just now"


@dataclass(frozen=True, slots=True)
class UpdatePackage:
    target_version: SemanticVersion
    payload: bytes
    expected_sha256: str

    @classmethod
    def build(cls, target_version: str, payload: bytes) -> UpdatePackage:
        return cls(
            target_version=SemanticVersion.parse(target_version),
            payload=payload,
            expected_sha256=hashlib.sha256(payload).hexdigest(),
        )

    def with_payload(self, payload: bytes) -> UpdatePackage:
        return replace(self, payload=payload)

    def verify_checksum(self) -> bool:
        actual = hashlib.sha256(self.payload).hexdigest()
        return hmac.compare_digest(actual, self.expected_sha256)


@dataclass(frozen=True, slots=True)
class DiagnosticEvent:
    sequence: int | None
    timestamp: datetime
    controller_id: str | None
    severity: EventSeverity
    code: str
    message: str
    stage: UpdateStage


@dataclass(frozen=True, slots=True)
class UpdateOutcome:
    controller: Controller
    events: tuple[DiagnosticEvent, ...]
