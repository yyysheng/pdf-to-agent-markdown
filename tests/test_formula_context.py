from __future__ import annotations

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


class FormulaContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="formula_context_runner_")
        self.root = Path(self.temp.name)
        self.source = self.root / "book.pdf"
        document = pymupdf.open()
        page = document.new_page(width=360, height=480)
        page.insert_text((72, 120), "F=ma", fontsize=18)
        page.insert_text((72, 190), "vx=v0", fontsize=18)
        document.save(str(self.source))
        document.close()
        self.candidates = [
            (pymupdf.Rect(60, 95, 150, 130), "F=ma"),
            (pymupdf.Rect(60, 165, 170, 200), "vx=v0"),
        ]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _run(self, target: Path, decisions: dict | None = None) -> tuple[dict, Path]:
        decision_path = self.root / "visual_decisions.json"
        if decisions is not None:
            decision_path.write_text(json.dumps(decisions), encoding="utf-8")
        with patch.object(runner, "formula_bboxes", return_value=self.candidates), patch.object(
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

    def test_phase_one_retains_tight_and_context_assets_with_group_provenance(self) -> None:
        state, markdown = self._run(self.root / "phase_one")
        records = state["formula_candidate_records"]["1"]

        self.assertEqual([record["candidate_index"] for record in records], [1, 2])
        for record in records:
            self.assertTrue(record["source_asset"])
            self.assertTrue(record["context_asset"])
            self.assertIn(record["context_type"], {"line", "region"})
            self.assertTrue(record["source_candidates"])
            self.assertTrue((markdown.parent / record["source_asset"]).is_file())
            self.assertTrue((markdown.parent / record["context_asset"]).is_file())
            self.assertIn(record["context_asset"], markdown.read_text(encoding="utf-8"))

        groups = state["formula_review_groups"]["1"]
        self.assertEqual(len(groups), 2)
        self.assertEqual([group["source_candidates"] for group in groups], [[1], [2]])
        self.assertTrue(all(group["source_candidates"] for group in groups))
        self.assertTrue(all("candidate" not in group for group in groups))

    def test_grouping_tracks_visual_candidates_without_joining_candidate_text(self) -> None:
        document = pymupdf.open()
        page = document.new_page(width=300, height=300)
        candidates = [
            (pymupdf.Rect(40, 100, 90, 116), "a="),
            (pymupdf.Rect(94, 101, 145, 117), "b"),
        ]
        groups = runner.formula_candidate_groups(page, candidates)
        document.close()

        self.assertEqual(groups[0]["candidate_indices"], [1, 2])
        self.assertNotIn("candidate", groups[0])
        self.assertNotIn("a=b", str(groups[0]))

    def test_candidate_index_selection_is_stable_and_does_not_need_text_copy(self) -> None:
        selection_path = self.root / "selection.json"
        selection_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "items": [{"book": "book", "pdf_page": 1, "candidate_index": 2}],
                }
            ),
            encoding="utf-8",
        )
        selection = runner.load_formula_selection(selection_path, book_identifier="book")

        self.assertEqual(runner.selected_formula_candidate_indices(self.candidates, selection, 1), [1])
        self.assertEqual(runner.filter_formula_candidates(self.candidates, selection, 1), [self.candidates[1]])

    def test_not_formula_closes_candidate_without_pending_or_latex(self) -> None:
        decisions = {
            "formula_decisions": [
                {
                    "pdf_page": 1,
                    "candidate": "F=ma",
                    "disposition": "not_formula",
                    "verification": "visual",
                    "source_pdf_page": 1,
                    "reason": "numeric equality is part of a prose label, not an independent mathematical expression",
                },
                {
                    "pdf_page": 1,
                    "candidate": "vx=v0",
                    "disposition": "crop_only",
                    "verification": "visual",
                    "source_pdf_page": 1,
                    "unresolved_reason": "the crop does not establish the subscript structure safely",
                },
            ]
        }
        state, markdown = self._run(self.root / "not_formula", decisions)

        self.assertEqual(state["status"], "completed_with_review_items")
        self.assertEqual(state["formula_not_formula"], 1)
        self.assertEqual(state["formula_crop_only_pending"], 1)
        self.assertEqual(len(state["pending_review"]), 1)
        self.assertEqual(state["visual_review_decisions"]["1"]["formulas"][0]["disposition"], "not_formula")
        self.assertEqual(state["execution_trace"][0]["decision"], "not_formula")
        self.assertTrue(state["execution_trace"][0]["context_source"])
        self.assertEqual(state["execution_trace"][0]["source_candidates"], [1])
        self.assertNotIn("$$", markdown.read_text(encoding="utf-8"))
        self.assertEqual(validate_conversion_state(state)["status"], "WARN")

    def test_new_state_validator_rejects_missing_context_provenance(self) -> None:
        state, _markdown = self._run(
            self.root / "missing_context",
            {
                "formula_decisions": [
                    {
                        "pdf_page": 1,
                        "candidate": "F=ma",
                        "disposition": "not_formula",
                        "verification": "visual",
                        "source_pdf_page": 1,
                        "reason": "the candidate is a prose fragment rather than an independent relation",
                    },
                    {
                        "pdf_page": 1,
                        "candidate": "vx=v0",
                        "disposition": "not_formula",
                        "verification": "visual",
                        "source_pdf_page": 1,
                        "reason": "the candidate is a prose fragment rather than an independent relation",
                    },
                ]
            },
        )
        state["visual_review_decisions"]["1"]["formulas"][0].pop("context_asset")

        self.assertEqual(validate_conversion_state(state)["status"], "FAIL")


if __name__ == "__main__":
    unittest.main(verbosity=2)
