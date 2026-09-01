from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from validate_md_assets import (  # noqa: E402
    delimiter_errors,
    suspicious_formula_artifacts,
    suspicious_math_braces,
    suspicious_math_mojibake,
    validate_conversion_state,
    validate_pending_review_semantics,
    validate_markdown,
)


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures"


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

    def test_chinese_prose_inside_math_is_a_hard_failure(self) -> None:
        markdown = FIXTURE_ROOT / "chinese_prose_inside_math.md"
        report = validate_markdown(markdown, expected_pages=[1])
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["summary"]["suspicious_math_blocks"], 1)
        check = next(item for item in report["checks"] if item["id"] == "math.chinese_prose")
        self.assertEqual(check["status"], "FAIL")

    def test_visually_confirmed_math_passes(self) -> None:
        markdown = FIXTURE_ROOT / "visually_confirmed_math.md"
        report = validate_markdown(markdown, expected_pages=[1])
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["summary"]["suspicious_math_blocks"], 0)

    def test_short_chinese_math_label_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pdf_md_validator_math_label_") as temp:
            markdown = Path(temp) / "book.md"
            markdown.write_text(
                "# Book\n\n<!-- PDF page 1 -->\n\n$$F_{\\text{合力}}=ma$$\n",
                encoding="utf-8",
            )
            report = validate_markdown(markdown, expected_pages=[1])
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["summary"]["suspicious_math_blocks"], 0)

    def test_legal_chinese_formula_labels_pass_brace_and_mojibake_checks(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pdf_md_validator_legal_labels_") as temp:
            markdown = Path(temp) / "book.md"
            markdown.write_text(
                "# Book\n\n<!-- PDF page 1 -->\n\n"
                "$$\n"
                "F_{\\text{压}}=\\mu F_{\\text{压}}\n"
                "P_{\\text{热}}=I^2R\n"
                "v_{\\text{甲}y}=v_{\\text{甲}}\\sin\\theta\n"
                "v_{AC,\\mathrm{平均}}=v_B\n"
                "\\{x\\}\n"
                "$$\n",
                encoding="utf-8",
            )
            report = validate_markdown(markdown, expected_pages=[1])
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["summary"]["suspicious_math_braces"], 0)
            self.assertEqual(report["summary"]["suspicious_math_mojibake"], 0)
            self.assertEqual(suspicious_math_braces(markdown.read_text(encoding="utf-8")), [])
            self.assertEqual(suspicious_math_mojibake(markdown.read_text(encoding="utf-8")), [])

    def test_unbalanced_latex_braces_fail_with_stack_diagnostics(self) -> None:
        for formula in (r"F_f=\mu F_{\text{鍘媫", r"F_{n", r"F=ma}"):
            with self.subTest(formula=formula):
                with tempfile.TemporaryDirectory(prefix="pdf_md_validator_braces_") as temp:
                    markdown = Path(temp) / "book.md"
                    markdown.write_text(
                        f"# Book\n\n<!-- PDF page 7 -->\n\n$$\n{formula}\n$$\n",
                        encoding="utf-8",
                    )
                    report = validate_markdown(markdown, expected_pages=[7])
                    self.assertEqual(report["status"], "FAIL")
                    self.assertGreaterEqual(report["summary"]["suspicious_math_braces"], 1)
                    check = next(item for item in report["checks"] if item["id"] == "math.braces")
                    self.assertEqual(check["status"], "FAIL")
                    self.assertTrue(check["findings"][0]["diagnostics"])

    def test_utf8_as_gbk_mojibake_inside_label_fails_even_when_balanced(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pdf_md_validator_mojibake_") as temp:
            markdown = Path(temp) / "book.md"
            original = "# Book\n\n<!-- PDF page 8 -->\n\n$$v_{AC,\\mathrm{骞冲潎}}=v_B$$\n"
            markdown.write_text(original, encoding="utf-8")
            report = validate_markdown(markdown, expected_pages=[8])
            self.assertEqual(report["status"], "FAIL")
            self.assertEqual(report["summary"]["suspicious_math_braces"], 0)
            self.assertEqual(report["summary"]["suspicious_math_mojibake"], 1)
            check = next(item for item in report["checks"] if item["id"] == "math.mojibake")
            self.assertEqual(check["status"], "FAIL")
            self.assertEqual(check["findings"][0]["pdf_page"], 8)
            self.assertIn("validator does not repair", check["findings"][0]["message"])

            self.assertEqual(markdown.read_text(encoding="utf-8"), original)
            self.assertEqual(suspicious_math_mojibake(original)[0]["recovered"], "平均")

    def test_known_utf8_as_gbk_mojibake_variants_are_detected(self) -> None:
        known_variants = ("骞冲潎", "鍘媫", "鐢瞹", "鐑瓆", "閲嶅姟")
        for page, mojibake in enumerate(known_variants, start=10):
            with self.subTest(mojibake=mojibake):
                with tempfile.TemporaryDirectory(prefix="pdf_md_validator_mojibake_variant_") as temp:
                    markdown = Path(temp) / "book.md"
                    markdown.write_text(
                        f"# Book\n\n<!-- PDF page {page} -->\n\n$$F_{{\\mathrm{{{mojibake}}}}}=x$$\n",
                        encoding="utf-8",
                    )
                    report = validate_markdown(markdown, expected_pages=[page])
                    self.assertEqual(report["summary"]["suspicious_math_braces"], 0)
                    self.assertEqual(report["summary"]["suspicious_math_mojibake"], 1)

    def test_formula_artifact_scoring_and_page_locator(self) -> None:
        expected = {
            "artifact_fraction_flattened_a.md": ("FAIL", 107),
            "artifact_fraction_flattened_b.md": ("FAIL", 107),
            "artifact_label_residue.md": ("WARN", 82),
            "artifact_compact_tokens.md": ("FAIL", 111),
        }
        for filename, (status, page) in expected.items():
            markdown = FIXTURE_ROOT / filename
            report = validate_markdown(markdown, expected_pages=[page])
            self.assertEqual(report["status"], status)
            self.assertEqual(report["summary"]["suspicious_formula_artifact"], 1)
            finding = suspicious_formula_artifacts(markdown.read_text(encoding="utf-8"))[0]
            self.assertEqual(finding["pdf_page"], page)
            self.assertGreaterEqual(finding["artifact_score"], 3)
            self.assertIn("reopen the source page", finding["message"])

    def test_normal_formula_fixtures_do_not_trigger_artifact_screen(self) -> None:
        for markdown in sorted(FIXTURE_ROOT.glob("normal_formula_*.md")):
            report = validate_markdown(markdown, expected_pages=[1])
            self.assertEqual(report["status"], "PASS", markdown.name)
            self.assertEqual(report["summary"]["suspicious_formula_artifact"], 0, markdown.name)

    def test_unit_rich_latex_formula_is_not_a_flattening_finding(self) -> None:
        markdown = r"""# Unit-rich formula

<!-- PDF page 45 -->

$$
v=v_0+at=10,mathrm{m/s}+0.6,mathrm{m/s^2}	imes10,mathrm{s}=16,mathrm{m/s}
$$
"""
        self.assertEqual(suspicious_formula_artifacts(markdown), [])

    def test_suspicious_unicode_garbage_is_a_hard_failure(self) -> None:
        markdown = FIXTURE_ROOT / "suspicious_unicode_garbage.md"
        report = validate_markdown(markdown, expected_pages=[1])
        self.assertEqual(report["status"], "FAIL")
        self.assertGreaterEqual(report["summary"]["suspicious_garbage"], 1)
        check = next(item for item in report["checks"] if item["id"] == "text.garbage")
        self.assertEqual(check["status"], "FAIL")

    def test_replacement_and_private_use_characters_fail(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pdf_md_validator_unicode_") as temp:
            markdown = Path(temp) / "book.md"
            markdown.write_text("# Book\n\n<!-- PDF page 1 -->\n\n\ufffd \ue000\n", encoding="utf-8")
            report = validate_markdown(markdown, expected_pages=[1])
            self.assertEqual(report["status"], "FAIL")
            self.assertEqual(report["summary"]["suspicious_garbage"], 2)

    def test_normal_chinese_text_does_not_trigger_garbage_check(self) -> None:
        markdown = FIXTURE_ROOT / "normal_chinese.md"
        report = validate_markdown(markdown, expected_pages=[1])
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["summary"]["suspicious_garbage"], 0)

    def test_excessive_transcription_notes_warn(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pdf_md_validator_notes_") as temp:
            markdown = Path(temp) / "book.md"
            pages = []
            for page in range(1, 11):
                pages.append(f"<!-- PDF page {page} -->\n\n> [Transcription note: unresolved item]\n")
            markdown.write_text("# Book\n\n" + "\n".join(pages), encoding="utf-8")
            report = validate_markdown(markdown, expected_pages=list(range(1, 11)))
            self.assertEqual(report["status"], "WARN")
            self.assertEqual(report["summary"]["transcription_notes"], 10)
            check = next(item for item in report["checks"] if item["id"] == "notes.excessive")
            self.assertEqual(check["status"], "WARN")

    def test_conversion_state_workflow_semantics(self) -> None:
        expected = {
            "workflow_state_completed_with_queue.json": "FAIL",
            "workflow_state_visual_review.json": "PASS",
            "workflow_state_completed.json": "PASS",
            "workflow_state_completed_pending.json": "WARN",
        }
        for filename, status in expected.items():
            state = json.loads((FIXTURE_ROOT / filename).read_text(encoding="utf-8"))
            report = validate_conversion_state(state)
            self.assertEqual(report["status"], status, filename)
        unresolved = validate_conversion_state(
            json.loads((FIXTURE_ROOT / "workflow_state_completed_pending.json").read_text(encoding="utf-8"))
        )
        self.assertEqual(unresolved["workflow_status"], "completed_with_review_items")
        self.assertEqual(unresolved["pending_review"], 1)

    def test_formula_decisions_require_visual_provenance(self) -> None:
        missing = json.loads(
            (FIXTURE_ROOT / "workflow_state_formula_without_provenance.json").read_text(encoding="utf-8")
        )
        missing_report = validate_conversion_state(missing)
        self.assertEqual(missing_report["status"], "FAIL")
        self.assertEqual(missing_report["formula_provenance"]["status"], "FAIL")

        confirmed = json.loads(
            (FIXTURE_ROOT / "workflow_state_formula_visual_provenance.json").read_text(encoding="utf-8")
        )
        confirmed_report = validate_conversion_state(confirmed)
        self.assertEqual(confirmed_report["status"], "PASS")
        self.assertEqual(confirmed_report["formula_provenance"]["status"], "PASS")

        crop_only = json.loads(
            (FIXTURE_ROOT / "workflow_state_formula_crop_only.json").read_text(encoding="utf-8")
        )
        crop_report = validate_conversion_state(crop_only)
        self.assertEqual(crop_report["status"], "WARN")
        self.assertEqual(crop_report["formula_provenance"]["status"], "PASS")
        self.assertEqual(crop_report["pending_review_semantics"]["status"], "PASS")

    def test_pending_review_rejects_unperformed_review_language(self) -> None:
        report = validate_pending_review_semantics(
            [
                {
                    "pdf_page": 83,
                    "type": "formula",
                    "status": "visually_reviewed_unresolved",
                    "reason": "需回看原 PDF 页面。",
                }
            ]
        )
        self.assertEqual(report["status"], "FAIL")


if __name__ == "__main__":
    unittest.main(verbosity=2)
