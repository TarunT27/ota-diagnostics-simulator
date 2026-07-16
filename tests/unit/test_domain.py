from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from ota_simulator.domain import (
    Controller,
    ControllerHealth,
    FaultScenario,
    SemanticVersion,
    UpdatePackage,
    UpdateStage,
)


@pytest.mark.parametrize("version", ["0.0.0", "1.2.3", "100.42.7"])
def test_semantic_version_accepts_strict_release_versions(version: str) -> None:
    assert str(SemanticVersion.parse(version)) == version


@pytest.mark.parametrize(
    "version",
    ["", "1", "1.0", "1.0.0.0", "v1.0.0", "1.-1.0", "1.0.x", " 1.0.0"],
)
def test_semantic_version_rejects_malformed_values(version: str) -> None:
    with pytest.raises(ValueError, match=r"major\.minor\.patch"):
        SemanticVersion.parse(version)


def test_semantic_versions_are_ordered_numerically() -> None:
    assert SemanticVersion.parse("1.10.0") > SemanticVersion.parse("1.9.9")


def test_semantic_version_rejects_unbounded_components() -> None:
    with pytest.raises(ValueError, match="components"):
        SemanticVersion.parse("10000.0.0")


def test_update_package_validates_real_sha256_digest() -> None:
    package = UpdatePackage.build("2.1.0", b"signed-demo-firmware")

    assert package.verify_checksum()
    assert not package.with_payload(b"tampered").verify_checksum()


def test_controller_snapshots_are_immutable() -> None:
    controller = Controller(
        id="gateway",
        name="Gateway",
        current_version=SemanticVersion.parse("1.3.2"),
        previous_version=None,
        health=ControllerHealth.HEALTHY,
        stage=UpdateStage.IDLE,
        voltage=12.4,
        connectivity="strong",
        active_dtcs=(),
    )

    with pytest.raises(FrozenInstanceError):
        controller.voltage = 9.0  # type: ignore[misc]


def test_fault_scenarios_are_stable_api_values() -> None:
    assert {fault.value for fault in FaultScenario} == {
        "none",
        "low_voltage",
        "connectivity_loss",
        "checksum_mismatch",
        "install_failure",
    }
