import tempfile
import unittest
from pathlib import Path

import app_core
from services.four_rabbits import status


class QuantbrainSimulationBridgeTest(unittest.TestCase):
    def test_learning_database_is_forwarded_to_simulator(self):
        command = app_core.build_commands("simulate", {
            "sample": 5,
            "cash": 100000,
            "trade": 20000,
            "smartTProfile": "quantbrain",
            "learning_database": "account-learning.sqlite3",
        })[0]
        index = command.index("--learning-database")
        self.assertEqual(command[index + 1], "account-learning.sqlite3")

    def test_four_rabbit_state_is_account_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            class Core:
                @staticmethod
                def user_data_path(email, filename):
                    return Path(directory) / f"{email}-{filename}"

            first = status(Core, "first@example.com")
            second = status(Core, "second@example.com")
            self.assertFalse(first["enabled"])
            self.assertFalse(second["enabled"])
            self.assertEqual(set(first["agents"]), {"training", "challenger", "official", "risk"})
            self.assertEqual(first["phase"], "idle")
            self.assertEqual(first["progress"], 0)
            self.assertEqual(first["lastResult"], {})
            self.assertEqual(first["events"], [])


if __name__ == "__main__":
    unittest.main()
