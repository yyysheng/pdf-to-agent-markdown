---
name: pdf-to-md-with-necessary-image-cropping-retained
description: Convert PDF textbooks, papers, and technical documents into analysis-ready Markdown with stable PDF/printed-page mapping, conservative LaTeX, necessary cropped visual assets, and machine-readable validation.
metadata:
  short-description: Inspect, convert, crop, validate, and manifest PDF-to-Markdown output
---

# PDF-to-MD with Necessary Image Cropping Retained

Use this skill when a PDF must become searchable, citeable Markdown for later
agent analysis. The original PDF remains authoritative for exact visual layout.

## Required workflow

1. Resolve the input PDF, the requested page range, and an output directory. A
   sample request such as “first 20 pages” must never start a full-book run.
2. Inspect before converting:

   ```text
   python scripts/inspect_pdf.py input.pdf --pages 1-20 --json output/inspection.json
   ```

3. Convert through the reusable CLI. `auto` uses the lightest available path
   that can satisfy the inspection signals, then records the decision:

   ```text
   python scripts/convert_pdf.py input.pdf -o output/ --pages 1-20 --engine auto --table-mode both --formula-mode latex
   ```

4. Validate the Markdown and its manifest:

   ```text
   python scripts/validate_md_assets.py output/input.md --manifest output/conversion_manifest.json --json output/validation_report.json
   ```

## Routing rules

- Prefer PyMuPDF/PyMuPDF4LLM for born-digital, low-complexity pages. Poppler is
  a compatibility fallback when the core Python package is missing.
- Run the fast path first, then use the inspection signals and quality gate to
  decide whether an installed Marker or Docling adapter is warranted. Use
  MinerU only as a heavy OCR/layout fallback or when the user explicitly
  selects it; it is not a universal default.
- Preserve requested partial ranges. An optional whole-document engine must
  not silently convert pages outside the request.
- Page markers distinguish `pdf_page` from `printed_page`; unknown printed
  numbers stay unknown. Use `--printed-page-offset` or `--printed-page-map`
  when the mapping is known rather than guessing.
- Use conservative LaTeX: `$...$` for inline math and `$$...$$` for display
  math. If recognition is uncertain, retain source text, create a formula
  crop when possible, and mark the uncertainty.
- Extract necessary figures, diagrams, plots, tables, and inseparable visual
  elements by bounding box. Do not use one full-page screenshot per page as a
  substitute for image extraction; full-page scan captures are exceptions and
  must be recorded in the manifest.
- Keep image files beside the Markdown file. Generated image stems, alt text,
  and relative paths must be deterministic and mutually traceable.
- Tables should have a visual crop and a structured Markdown representation
  when the text layer makes that representation reliable; otherwise report the
  limitation explicitly.
- Treat `PASS`, `WARN`, and `FAIL` as quality states. Never silently accept
  missing page markers, missing assets, unbalanced math, or suspected garbage.

## Supporting references

Read only the reference relevant to the current decision:

- [pipeline.md](references/pipeline.md) for routing, page-level escalation,
  manifest fields, and the stable CLI contract.
- [pymupdf.md](references/pymupdf.md) for the fast path and crop heuristics.
- [marker.md](references/marker.md), [docling.md](references/docling.md), or
  [mineru.md](references/mineru.md) before installing or invoking an optional
  engine.
- [quality-gates.md](references/quality-gates.md) for interpretation of
  validator checks and unresolved warnings.
