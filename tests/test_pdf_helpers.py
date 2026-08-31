from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from pdf_helpers import parse_bbox  # noqa: E402


class PdfHelperTests(unittest.TestCase):
    def test_parse_bbox_is_explicit_and_point_based(self) -> None:
        self.assertEqual(parse_bbox("10, 20, 110, 220"), (10.0, 20.0, 110.0, 220.0))
        with self.assertRaises(ValueError):
            parse_bbox("10,20,10,220")

    def test_helper_has_no_semantic_batch_api(self) -> None:
        import pdf_helpers

        public_names = set(getattr(pdf_helpers, "__all__", ()))
        self.assertFalse({"convert", "inspect", "route", "batch"} & public_names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
