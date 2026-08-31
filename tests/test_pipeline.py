from __future__ import annotations

import sys
import tempfile
import unittest
import zlib
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import convert_pdf as converter_module  # noqa: E402
import inspect_pdf as inspector_module  # noqa: E402
from convert_pdf import Options, _select_engine, convert, fitz  # noqa: E402
from inspect_pdf import inspect_pdf  # noqa: E402
from pipeline_utils import parse_pages  # noqa: E402
from validate_md_assets import delimiter_errors, validate_markdown  # noqa: E402


def _pdf_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_fixture_pdf(path: Path) -> None:
    """Create a tiny three-page PDF without reportlab or other test packages."""

    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        4: (
            b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 "
            b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode /Length 11 >>\n"
            b"stream\n"
            + zlib.compress(bytes((220, 30, 30)))
            + b"\nendstream"
        ),
    }
    page_ids = [5, 7, 9]
    content_ids = [6, 8, 10]
    page_texts = [
        "Physics page 1\nF = 3 x 10 4\n1",
        "Chinese textbook page 2\nA useful paragraph\n2",
        "",
    ]
    contents = [
        f"BT /F1 14 Tf 72 760 Td ({_pdf_string(text.replace(chr(10), ') Tj 0 -24 Td ('))}) Tj ET".encode("ascii")
        if text
        else b"q 100 0 0 100 200 500 cm /Im1 Do Q"
        for text in page_texts
    ]
    for content_id, content in zip(content_ids, contents):
        objects[content_id] = (
            f"<< /Length {len(content)} >>\nstream\n".encode("ascii") + content + b"\nendstream"
        )
    objects[2] = (
        b"<< /Type /Pages /Kids [5 0 R 7 0 R 9 0 R] /Count 3 >>"
    )
    for page_id, content_id, has_image in zip(page_ids, content_ids, (False, False, True)):
        image_resources = b" /XObject << /Im1 4 0 R >>" if has_image else b""
        objects[page_id] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 3 0 R >>" + image_resources + b" >> "
            + f"/Contents {content_id} 0 R >>".encode("ascii")
        )

    highest = max(objects)
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0] * (highest + 1)
    for object_id in range(1, highest + 1):
        offsets[object_id] = len(output)
        output.extend(f"{object_id} 0 obj\n".encode("ascii"))
        output.extend(objects[object_id])
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {highest + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {highest + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
    )
    path.write_bytes(output)


class PipelineTests(unittest.TestCase):
    def test_page_range_parser(self) -> None:
        self.assertEqual(parse_pages("1-3,2,5", 5), [1, 2, 3, 5])
        with self.assertRaises(ValueError):
            parse_pages("3-1", 5)

    def test_inspector_reports_scan_image_and_formula_signals(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pdf_md_inspect_") as temp:
            pdf = Path(temp) / "signals.pdf"
            make_fixture_pdf(pdf)
            report = inspect_pdf(pdf)
            self.assertEqual(report["pages"], 3)
            self.assertIn(3, report["scan_pages"])
            self.assertTrue(report["page_records"][0]["formula_suspected"])
            if fitz is not None:
                self.assertGreaterEqual(report["page_records"][2]["image_count"], 1)

    def test_auto_route_has_dependency_fallback(self) -> None:
        engine, warnings = _select_engine(
            "auto",
            {"recommended_engine": "pymupdf", "pages": 1},
            [1],
        )
        self.assertIn(engine, {"pymupdf", "poppler"})
        if engine == "poppler":
            self.assertTrue(warnings)

    def test_inspect_convert_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pdf_md_test_") as temp:
            root = Path(temp)
            pdf = root / "中文教材_fixture.pdf"
            output = root / "output"
            make_fixture_pdf(pdf)
            inspection = inspect_pdf(pdf)
            self.assertEqual(inspection["pages"], 3)
            self.assertTrue(inspection["text_layer"])
            self.assertIn(3, inspection["scan_pages"])
            result = convert(
                Options(
                    input_pdf=pdf,
                    output_dir=output,
                    pages=[1, 2, 3],
                    requested_engine="auto",
                    printed_page_offset=-1,
                )
            )
            markdown = result["markdown"].read_text(encoding="utf-8")
            self.assertIn("<!-- PDF page 1 | printed page 0 -->", markdown)
            self.assertIn("<!-- PDF page 3 | printed page 2 -->", markdown)
            self.assertEqual(result["manifest_data"]["page_range"], "1-3")
            validation = validate_markdown(
                result["markdown"], expected_pages=[1, 2, 3], manifest_path=result["manifest"]
            )
            self.assertNotEqual(validation["status"], "FAIL")
            if fitz is not None:
                self.assertGreaterEqual(result["manifest_data"]["images"], 1)
                self.assertTrue(any(output.glob("scan_p003_*.png")) or any(output.glob("fig_p003_*.png")))

    def test_poppler_compatibility_path_without_pymupdf(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pdf_md_poppler_") as temp:
            root = Path(temp)
            pdf = root / "fallback.pdf"
            output = root / "output"
            make_fixture_pdf(pdf)
            old_converter_fitz = converter_module.fitz
            old_inspector_fitz = inspector_module.fitz
            converter_module.fitz = None
            inspector_module.fitz = None
            try:
                result = convert(
                    Options(input_pdf=pdf, output_dir=output, pages=[1, 2, 3], requested_engine="auto")
                )
            finally:
                converter_module.fitz = old_converter_fitz
                inspector_module.fitz = old_inspector_fitz
            self.assertEqual(result["manifest_data"]["selected_engine"], "poppler")
            self.assertEqual(result["manifest_data"]["images"], 0)
            self.assertNotEqual(result["validation"]["status"], "FAIL")

    def test_validator_detects_missing_orphan_and_delimiter(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pdf_md_validate_") as temp:
            root = Path(temp)
            markdown = root / "book.md"
            markdown.write_text(
                "# Book\n\n<!-- PDF page 1 -->\n\n![fig_p001_01](missing.png)\n\n"
                "<!-- PDF page 3 -->\n\nText $x=1\n",
                encoding="utf-8",
            )
            (root / "orphan.png").write_bytes(b"not-a-real-image")
            self.assertTrue(delimiter_errors(markdown.read_text(encoding="utf-8")))
            report = validate_markdown(markdown, expected_pages=[1, 2, 3])
            self.assertEqual(report["status"], "FAIL")
            self.assertEqual(report["missing_images"], ["missing.png"])
            self.assertIn("orphan.png", report["orphan_assets"])

    def test_validator_passes_stable_page_and_image_contract(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pdf_md_validator_pass_") as temp:
            root = Path(temp)
            (root / "fig_p001_01.png").write_bytes(b"png")
            markdown = root / "book.md"
            markdown.write_text(
                "# Book\n\n<!-- PDF page 1 | printed page 10 -->\n\n"
                "Text $x=1$.\n\n![fig_p001_01](fig_p001_01.png)\n\n"
                "<!-- PDF page 2 | printed page 11 -->\n\nMore text.\n",
                encoding="utf-8",
            )
            manifest = root / "conversion_manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "output_markdown": str(markdown.resolve()),
                        "images": 1,
                        "pdf_pages": [1, 2],
                    }
                ),
                encoding="utf-8",
            )
            report = validate_markdown(
                markdown,
                expected_pages=[1, 2],
                expected_printed_pages=(10, 11),
                manifest_path=manifest,
                require_alt_stem=True,
            )
            self.assertEqual(report["status"], "PASS")

    def test_validator_warns_without_manifest(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pdf_md_validator_warn_") as temp:
            markdown = Path(temp) / "book.md"
            markdown.write_text("# Book\n\n<!-- PDF page 1 -->\n\nText.\n", encoding="utf-8")
            report = validate_markdown(markdown, expected_pages=[1])
            self.assertEqual(report["status"], "WARN")
            self.assertTrue(any("manifest" in warning for warning in report["warnings"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
