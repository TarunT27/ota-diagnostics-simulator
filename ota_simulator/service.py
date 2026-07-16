"""Application service coordinating immutable transitions and persistence."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime

from ota_simulator.domain import (
    Controller,
    DiagnosticEvent,
    EventSeverity,
    FaultScenario,
    UpdateOutcome,
    UpdatePackage,
    UpdateStage,
)
from ota_simulator.repository import SQLiteRepository
from ota_simulator.updater import execute_rollback, execute_update


class ControllerNotFoundError(LookupError):
    pass


class SimulatorService:
    def __init__(
        self,
        repository: SQLiteRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()

    def list_controllers(self) -> tuple[Controller, ...]:
        return self._repository.list_controllers()

    def get_controller(self, controller_id: str) -> Controller:
        controller = self._repository.get_controller(controller_id)
        if controller is None:
            raise ControllerNotFoundError(controller_id)
        return controller

    def summary(self) -> dict[str, int]:
        controllers = self.list_controllers()
        healthy = sum(controller.health.value == "healthy" for controller in controllers)
        active_dtcs = sum(len(controller.active_dtcs) for controller in controllers)
        return {
            "total_controllers": len(controllers),
            "healthy_controllers": healthy,
            "attention_controllers": len(controllers) - healthy,
            "active_dtcs": active_dtcs,
        }

    def run_update(
        self,
        controller_id: str,
        target_version: str,
        fault: FaultScenario,
    ) -> Controller:
        with self._lock:

            def transition(controller: Controller) -> UpdateOutcome:
                payload = f"{controller_id}:{target_version}:demo-firmware".encode()
                package = UpdatePackage.build(target_version, payload)
                return execute_update(controller, package, fault, clock=self._clock)

            outcome = self._repository.transition_controller(controller_id, transition)
            if outcome is None:
                raise ControllerNotFoundError(controller_id)
            return outcome.controller

    def rollback(self, controller_id: str) -> Controller:
        with self._lock:
            outcome = self._repository.transition_controller(
                controller_id,
                lambda controller: execute_rollback(controller, clock=self._clock),
            )
            if outcome is None:
                raise ControllerNotFoundError(controller_id)
            return outcome.controller

    def list_events(
        self,
        *,
        controller_id: str | None = None,
        limit: int = 100,
    ) -> tuple[DiagnosticEvent, ...]:
        if controller_id is not None:
            self.get_controller(controller_id)
        return self._repository.list_events(controller_id=controller_id, limit=limit)

    def reset(self) -> None:
        with self._lock:
            event = DiagnosticEvent(
                sequence=None,
                timestamp=self._clock(),
                controller_id=None,
                severity=EventSeverity.INFO,
                code="LAB_RESET",
                message="Deterministic demo fleet restored to its baseline snapshot.",
                stage=UpdateStage.IDLE,
            )
            self._repository.reset(event)
