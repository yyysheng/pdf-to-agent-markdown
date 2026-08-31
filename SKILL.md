---
name: pdf-to-md-with-necessary-image-cropping-retained
description: Convert PDF textbooks and documents into analysis-ready Markdown with printed page numbers, LaTeX formulas, and necessary cropped images stored beside the Markdown file.
metadata:
  short-description: PDF to Markdown with LaTeX and matched image crops
---

# PDF-to-MD with Necessary Image Cropping Retained

Convert a PDF into a clean, analysis-oriented Markdown deliverable while keeping the information an agent needs to reason about the source: readable text order, printed page references, usable LaTeX, and real image assets.

## Scope and defaults

- Resolve the exact input PDF, requested page range, and output directory before converting. If the user asks for a sample (for example, the first 20 pages), process only that range and do not start a full-book run.
- This skill's default conversion path is local and controllable: inspect the PDF text layer, render pages when needed, and use local extraction/OCR tools. Do not invoke MinerU as the conversion engine unless the user explicitly requests MinerU.
- Keep the Markdown file and all referenced cropped images in the same output subfolder unless the user specifies another layout.

## Conversion workflow

1. Inspect the PDF structure and representative pages. Distinguish PDF page indices from printed page numbers, and identify pages with multi-column text, tables, formulas, diagrams, or full-page artwork.
2. Extract text with a layout-aware method first; use raw extraction as a cross-check when columns or text boxes are misordered. Use OCR only when the PDF has no usable text layer. Reorder columns by visual reading order rather than trusting a single extraction mode.
3. Remove only repeated running headers, footers, page numbers, separator rules, and extraction-only glyph fragments. Preserve legitimate punctuation, footnotes, section labels, and text that continues across a page boundary. Add `<!-- PDF page N -->` markers so the source location remains recoverable.
4. Convert headings and subheadings into a consistent Markdown hierarchy. Preserve the table of contents as structured Markdown and retain its printed page numbers (for example, `- 1. 质点 参考系 …… 11`). Do not omit page numbers merely to reduce noise; they are useful for citation and source navigation.
5. Preserve mathematical content as LaTeX: use inline `$...$` for short expressions and display `$$...$$` for standalone formulas. Normalize common expressions such as `×`, fractions, powers, units, and coordinate variables. If a formula is uncertain, keep the source crop and flag the uncertainty instead of silently inventing a value.
6. Identify necessary visual assets: figures, diagrams, plots, tables, problem-box illustrations, and meaningful cover/artwork. Render the relevant page at sufficient resolution and crop the asset. Use deterministic names such as `fig_1_2_1_time_axis.png`; the Markdown image path, alt text, and filename must agree exactly. Avoid including unrelated prose in a crop, but retain surrounding context when the source uses an inseparable full-page composition.
7. For tables, preserve a readable crop for visual fidelity. If the user needs table querying or text-only analysis, also transcribe the table into Markdown or CSV; an image alone is not searchable by a text-only pass.

## Validation

Run the bundled validator when the Markdown is generated:

```text
python scripts/validate_md_assets.py path/to/output.md
```

The final check should confirm that every relative Markdown image reference exists beside the Markdown file, there are no missing assets, LaTeX delimiters are balanced, page markers match the requested range, and repeated headers/obvious extraction garbage are gone. Visually inspect representative crops (cover, diagram, table, formula, and problem-box image) before reporting completion.

Report the output Markdown path, image directory, processed page range, counts of image references and formula blocks, and any remaining limitations. For complex layouts, state that the original PDF remains the authority for exact visual placement and use it for spot checks.
