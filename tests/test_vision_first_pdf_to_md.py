from __future__ import annotations

import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pymupdf  # noqa: E402
import vision_first_pdf_to_md as runner  # noqa: E402
from validate_md_assets import validate_conversion_state  # noqa: E402


class VisionFirstRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="vision_first_runner_")
        self.root = Path(self.temp.name)
        self.source = self.root / "book.pdf"
        document = pymupdf.open()
        page = document.new_page(width=300, height=400)
        page.insert_text((72, 100), "vx=v0", fontsize=18)
        document.save(str(self.source))
        document.close()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _run(self, target: Path, decisions: dict | None = None) -> tuple[dict, Path]:
        decision_path = self.root / "visual_decisions.json"
        if decisions is not None:
            decision_path.write_text(json.dumps(decisions), encoding="utf-8")
        formula = (pymupdf.Rect(60, 70, 180, 110), "vx=v0")
        with patch.object(runner, "formula_bboxes", return_value=[formula]), patch.object(
            runner, "image_bboxes", return_value=[]
        ):
            state = runner.convert_book(
                self.source,
                target,
                force_fresh=True,
                skill_revision="test-revision",
                pages=[1],
                visual_decisions_path=decision_path if decisions is not None else None,
            )
        return state, target / "book" / "book.md"

    def _confirmed_decision(self, disposition: str = "latex_confirmed") -> dict:
        decision = {
            "pdf_page": 1,
            "candidate": "vx=v0",
            "disposition": disposition,
            "verification": "visual",
            "source_asset": "assets/pdf_page_001_formula_01.png",
            "source_pdf_page": 1,
        }
        if disposition == "latex_confirmed":
            decision["latex"] = "v_x=v_0"
        else:
            decision["unresolved_reason"] = "the bounded crop does not make the subscript structure safe"
        return {"formula_decisions": [decision]}

    def test_no_visual_decision_never_confirms_formula(self) -> None:
        state, markdown = self._run(self.root / "no_decision")

        self.assertEqual(state["status"], "visual_review")
        self.assertEqual(state["visual_review_required"], [1])
        self.assertEqual(state.get("formula_latex_confirmed", 0), 0)
        self.assertNotIn("$$", markdown.read_text(encoding="utf-8"))

    def test_visual_latex_confirmation_uses_latex_and_provenance(self) -> None:
        state, markdown = self._run(self.root / "confirmed", self._confirmed_decision())
        content = markdown.read_text(encoding="utf-8")

        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["formula_latex_confirmed"], 1)
        self.assertIn("v_x=v_0", content)
        self.assertIn("assets/pdf_page_001_formula_01.png", content)
        decision = state["visual_review_decisions"]["1"]["formulas"][0]
        self.assertEqual(decision["verification"], "visual")
        self.assertEqual(decision["source_asset"], "assets/pdf_page_001_formula_01.png")

    def test_crop_only_never_guesses_latex_and_keeps_pending_reason(self) -> None:
        state, markdown = self._run(self.root / "crop_only", self._confirmed_decision("crop_only"))
        content = markdown.read_text(encoding="utf-8")

        self.assertEqual(state["status"], "completed_with_review_items")
        self.assertEqual(state["formula_latex_confirmed"], 0)
        self.assertEqual(state["formula_crop_only_pending"], 1)
        self.assertNotIn("$$", content)
        self.assertEqual(state["pending_review"][0]["status"], "visually_reviewed_unresolved")
        self.assertIn("subscript structure", state["pending_review"][0]["reason"])

    def test_validator_pass_does_not_auto_promote_formula(self) -> None:
        state, _markdown = self._run(self.root / "validator_no_promotion")

        report = validate_conversion_state(state)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(state.get("formula_latex_confirmed", 0), 0)
        self.assertFalse(state.get("visual_review_decisions"))

    def test_explicit_page_visual_confirmation_closes_no_asset_queue(self) -> None:
        decision_path = self.root / "page_visual_confirmation.json"
        decision_path.write_text(
            json.dumps({"visual_confirmed_pages": [1]}),
            encoding="utf-8",
        )
        target = self.root / "page_visual_confirmation"
        with patch.object(runner, "formula_bboxes", return_value=[]), patch.object(
            runner, "image_bboxes", return_value=[]
        ):
            state = runner.convert_book(
                self.source,
                target,
                force_fresh=True,
                skill_revision="test-revision",
                pages=[1],
                visual_decisions_path=decision_path,
            )

        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["visual_review_required"], [])
        self.assertEqual(state["visual_review_decisions"]["1"]["visuals"][0]["disposition"], "markdown_sufficient")
        self.assertEqual(state["visual_review_decisions"]["1"]["visuals"][0]["verification"], "visual")

    def test_legacy_conservative_latex_auto_path_is_absent(self) -> None:
        self.assertFalse(hasattr(runner, "conservative_latex"))
        self.assertNotIn("def conservative_latex", inspect.getsource(runner))


if __name__ == "__main__":
    unittest.main(verbosity=2)
