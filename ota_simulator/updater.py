"""Minimal update, validation, and rollback state machine."""

from enum import Enum


class UpdateState(str, Enum):
    READY = "ready"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


def _version_tuple(version: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError as error:
        raise ValueError(f"invalid version: {version}") from error


class UpdateController:
    def __init__(self, current_version: str) -> None:
        _version_tuple(current_version)
        self.current_version = current_version
        self.previous_version: str | None = None
        self.state = UpdateState.READY

    def apply_update(self, target_version: str, checksum_valid: bool) -> bool:
        if not checksum_valid:
            self.state = UpdateState.FAILED
            return False
        if _version_tuple(target_version) <= _version_tuple(self.current_version):
            return False

        self.previous_version = self.current_version
        self.current_version = target_version
        self.state = UpdateState.READY
        return True

    def rollback(self) -> bool:
        if self.previous_version is None:
            return False
        current_version = self.current_version
        self.current_version = self.previous_version
        self.previous_version = current_version
        self.state = UpdateState.ROLLED_BACK
        return True
