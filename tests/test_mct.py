# SPDX-License-Identifier: GPL-2.0-only
"""Selector fixtures only; actual upstream/patched C paths run in the CI build."""
import unittest
from unittest.mock import patch
from scripts.check_mct import FUNCTIONS, main, select_functions

def fixture():
    return "\n".join("static int __init " + name + "(void)\n{\n\treturn 0;\n}\n" for name in FUNCTIONS)

class MctTests(unittest.TestCase):
    def test_selects_only_named_production_functions(self):
        source = fixture()
        selected = select_functions(source + "\nstatic int unrelated;\n")
        self.assertEqual(selected, source)
        self.assertNotIn("unrelated", selected)

    def test_missing_duplicate_unterminated_unbalanced_or_oversized_are_rejected(self):
        source = fixture()
        for bad in ("", source + source, source.replace("\n}\n", "\n", 1),
                    source.replace("return 0;", "{ return 0;", 1), source + "x" * (1024 * 1024)):
            with self.subTest(bytes=len(bad)), self.assertRaises(ValueError):
                select_functions(bad)

    def test_actual_production_regression_cli_refuses_non_CI_execution(self):
        with patch.dict("scripts.check_mct.os.environ", {}, clear=True), self.assertRaisesRegex(SystemExit, "CI-only"):
            main()
