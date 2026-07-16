"""Deterministic seed data used by the dashboard and tests."""

from __future__ import annotations

from ota_simulator.domain import (
    Controller,
    ControllerHealth,
    DiagnosticTroubleCode,
    SemanticVersion,
    UpdateStage,
)


def seed_controllers() -> tuple[Controller, ...]:
    antenna_dtc = DiagnosticTroubleCode(
        code="B1A0F",
        title="Telematics antenna open circuit",
        detail="Antenna continuity check is outside the expected range.",
    )
    communication_dtc = DiagnosticTroubleCode(
        code="U0121",
        title="Lost communication with ABS module",
        detail="ADAS did not receive a valid ABS heartbeat during the last cycle.",
    )
    return (
        _controller("gateway", "Gateway", "1.3.2", 12.4),
        _controller("telematics", "Telematics", "2.0.4", 12.3, (antenna_dtc,)),
        _controller("infotainment", "Infotainment", "3.1.0", 12.3),
        _controller(
            "adas",
            "ADAS",
            "1.8.7",
            11.8,
            (communication_dtc,),
            health=ControllerHealth.ATTENTION,
        ),
        _controller("battery", "Battery Management", "1.2.1", 12.5),
        _controller("body", "Body Control", "2.2.3", 12.4),
    )


def _controller(
    controller_id: str,
    name: str,
    version: str,
    voltage: float,
    active_dtcs: tuple[DiagnosticTroubleCode, ...] = (),
    *,
    health: ControllerHealth = ControllerHealth.HEALTHY,
) -> Controller:
    return Controller(
        id=controller_id,
        name=name,
        current_version=SemanticVersion.parse(version),
        previous_version=None,
        health=health,
        stage=UpdateStage.IDLE,
        voltage=voltage,
        connectivity="strong",
        active_dtcs=active_dtcs,
    )
