from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from validate_md_assets import delimiter_errors, validate_markdown  # noqa: E402


class ValidatorTests(unittest.TestCase):
    def test_agent_transcription_contract_passes_without_manifest(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pdf_md_validator_") as temp:
            root = Path(temp)
            image = root / "fig_2_3.png"
            image.write_bytes(b"not-rendered-in-unit-test")
            markdown = root / "book.md"
            markdown.write_text(
                "# Book\n\n## 目录\n\n- 第一章 运动\n\n<!-- PDF page 1 -->\n\n"
                "正文。\n\n$$\nF=ma\n$$\n\n"
                "![fig_2_3](fig_2_3.png)\n\n<!-- PDF page 2 -->\n\n结束。\n",
                encoding="utf-8",
            )
            report = validate_markdown(markdown, expected_pages=[1, 2])
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["summary"]["image_references"], 1)
            self.assertFalse(any("printed" in warning for warning in report["warnings"]))

    def test_missing_assets_and_unbalanced_math_fail(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pdf_md_validator_fail_") as temp:
            markdown = Path(temp) / "book.md"
            markdown.write_text(
                "# Book\n\n<!-- PDF page 1 -->\n\n![missing](missing.png)\n\n$F=ma\n",
                encoding="utf-8",
            )
            report = validate_markdown(markdown, expected_pages=[1])
            self.assertEqual(report["status"], "FAIL")
            self.assertEqual(report["missing_images"], ["missing.png"])
            self.assertTrue(delimiter_errors(markdown.read_text(encoding="utf-8")))

    def test_unknown_printed_page_is_only_required_when_requested(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pdf_md_validator_printed_") as temp:
            markdown = Path(temp) / "book.md"
            markdown.write_text("# Book\n\n<!-- PDF page 1 | printed page unknown -->\n\nText.\n", encoding="utf-8")
            optional = validate_markdown(markdown, expected_pages=[1])
            required = validate_markdown(markdown, expected_pages=[1], require_printed_pages=True)
            self.assertEqual(optional["status"], "PASS")
            self.assertEqual(required["status"], "WARN")

    def test_duplicate_markers_and_orphan_assets_need_review(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pdf_md_validator_markers_") as temp:
            root = Path(temp)
            (root / "kept.png").write_bytes(b"png")
            (root / "orphan.png").write_bytes(b"png")
            markdown = root / "book.md"
            markdown.write_text(
                "# Book\n\n<!-- PDF page 1 -->\n\n![kept](kept.png)\n\n"
                "<!-- PDF page 1 -->\n\nText.\n",
                encoding="utf-8",
            )
            report = validate_markdown(markdown, expected_pages=[1])
            self.assertEqual(report["status"], "FAIL")
            self.assertIn("orphan.png", report["orphan_assets"])
            duplicate_check = next(item for item in report["checks"] if item["id"] == "pages.duplicates")
            self.assertEqual(duplicate_check["status"], "FAIL")

    def test_optional_manifest_is_checked_but_not_required(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pdf_md_validator_manifest_") as temp:
            root = Path(temp)
            (root / "fig.png").write_bytes(b"png")
            markdown = root / "book.md"
            markdown.write_text("# Book\n\n<!-- PDF page 1 -->\n\n![fig](fig.png)\n", encoding="utf-8")
            manifest = root / "state.json"
            manifest.write_text(
                json.dumps({"output_markdown": str(markdown.resolve()), "images": 1, "pdf_pages": [1]}),
                encoding="utf-8",
            )
            report = validate_markdown(markdown, expected_pages=[1], manifest_path=manifest)
            self.assertEqual(report["status"], "PASS")


if __name__ == "__main__":
    unittest.main(verbosity=2)
