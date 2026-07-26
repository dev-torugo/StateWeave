from __future__ import annotations

import unittest

from stateweave.contracts import check_module_schemas


class ModuleContractTests(unittest.TestCase):
    def test_all_optional_module_schemas_are_valid_draft_2020_12(self) -> None:
        self.assertEqual(check_module_schemas(), [])


if __name__ == "__main__":
    unittest.main()
