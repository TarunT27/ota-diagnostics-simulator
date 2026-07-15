import unittest

from ota_simulator.updater import UpdateController, UpdateState


class UpdateControllerTests(unittest.TestCase):
    def test_applies_newer_version(self):
        controller = UpdateController(current_version="1.0.0")

        result = controller.apply_update("1.1.0", checksum_valid=True)

        self.assertTrue(result)
        self.assertEqual("1.1.0", controller.current_version)
        self.assertEqual(UpdateState.READY, controller.state)

    def test_rejects_invalid_checksum(self):
        controller = UpdateController(current_version="1.0.0")

        result = controller.apply_update("1.1.0", checksum_valid=False)

        self.assertFalse(result)
        self.assertEqual("1.0.0", controller.current_version)
        self.assertEqual(UpdateState.FAILED, controller.state)

    def test_rolls_back_to_previous_version(self):
        controller = UpdateController(current_version="1.0.0")
        controller.apply_update("1.1.0", checksum_valid=True)

        self.assertTrue(controller.rollback())
        self.assertEqual("1.0.0", controller.current_version)
        self.assertEqual(UpdateState.ROLLED_BACK, controller.state)

    def test_rejects_same_or_older_version(self):
        controller = UpdateController(current_version="1.1.0")

        self.assertFalse(controller.apply_update("1.1.0", checksum_valid=True))
        self.assertFalse(controller.apply_update("1.0.0", checksum_valid=True))


if __name__ == "__main__":
    unittest.main()
