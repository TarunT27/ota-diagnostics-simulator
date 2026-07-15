from ota_simulator.updater import UpdateController


def main() -> None:
    controller = UpdateController("1.0.0")
    controller.apply_update("1.1.0", checksum_valid=True)
    print({"version": controller.current_version, "state": controller.state.value})


if __name__ == "__main__":
    main()
