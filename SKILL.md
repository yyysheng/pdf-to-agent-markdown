---
name: pdf-to-md-with-necessary-image-cropping-retained
description: Transcribe complete PDF textbooks, papers, and technical documents into vision-first, Agent-readable Markdown with conservative formulas, necessary visual evidence, semantic structure, and PDF page traceability.
metadata:
  short-description: Read PDFs directly and progressively transcribe faithful Agent-readable Markdown
---

# PDF-to-MD with Necessary Image Cropping Retained

Use this skill when the user wants a PDF transcribed into Markdown for later
search, retrieval, or analysis. The original PDF is the source of truth.

## Core rule

When Codex can read the PDF pages or obtain their rendered visuals directly,
read the original PDF first. Use the PDF's text layer only as supporting
evidence for copying long prose. Do not send the document through a third-party
parser before the Agent has seen the relevant pages. Do not let a parser decide
reading order, headings, formulas, table cells, captions, or which visuals are
meaningful.

The output is semantic transcription, not a pixel/layout replica and not a
summary. Preserve definitions, explanations, examples, questions, solutions,
units, and qualifications while removing only layout noise.

## Workflow

1. Accept a complete PDF as the normal input. Do not ask the user to split it.
   If the user explicitly requests a range or sample, process only that range.
2. Establish the total page count and begin the target Markdown early. For a
   book, read the cover and table of contents directly, retain the contents,
   and build the chapter/section hierarchy.
3. For a long document, choose internal reading windows dynamically from
   context capacity, chapter boundaries, page complexity, and visual density.
   Read a coherent section, transcribe it immediately, then continue to the
   next section. Keep writing/updating the same Markdown file; never expose
   internal batches or fixed page-size rules in the output.
4. Reconstruct natural semantic structure: title, chapters, sections,
   subsections, examples, notes, sidebars, exercises, and solutions. Prefer
   section meaning over PDF coordinates or page order when a page contains
   multiple columns or boxes, but preserve every source-page transition.
5. Add `<!-- PDF page N -->` at every page transition. Add
   `| printed page M` only when the printed number is visually certain. Never
   infer it from an offset, and never treat an unknown printed page as a
   transcription failure.
6. Transcribe formulas from page visuals. Use LaTeX for confirmed superscripts,
   subscripts, fractions, radicals, Greek symbols, vectors, matrices, sums,
   integrals, scientific notation, and units. If a glyph or relation is
   uncertain, do not guess: retain the confirmed expression, add a clearly
   labeled `Transcription note`, and keep a bounded visual crop when useful.
7. Decide visually which regions contain information that text cannot preserve.
   Retain necessary diagrams, apparatus, free-body/circuit/optical diagrams,
   axes, curves, maps, meaningful illustrations, complex tables, and exercise
   figures. Crop only the smallest useful region with a stable filename and
   reference that exact file. Do not retain decorative backgrounds, logos,
   watermarks, separators, or one full-page screenshot per page. If cropping is
   unavailable, finish the text transcription and record the pending visual
   review instead of blocking the task.
8. Rebuild visually clear tables as Markdown tables. If cell boundaries,
   merged cells, values, or units are not reliable, retain the table visual and
   add only a qualified structural description; never invent cells.
9. Keep source content separate from Agent judgments. Use
   `> [Transcription note: ...]` for uncertainty, provenance, or review items;
   do not disguise Agent explanations as book text.
10. Before finishing, verify chapter continuity, formula delimiters, page
    markers, image links, unresolved notes, and that the final requested page
    was reached. Run `scripts/validate_md_assets.py` as an optional deterministic
    QA helper, then visually recheck every pending item against the PDF.

## Long-document checkpoint

For work likely to be interrupted, maintain a small sibling
`conversion_state.json` with the source, last completed PDF page, current
section, Markdown path, pending review pages, and `in_progress`/`completed`
status. It is only a checkpoint. On resume, verify the last marker before
continuing so content is not duplicated. See
[references/long-document-workflow.md](references/long-document-workflow.md).

## Supporting guidance

- [references/markdown-format.md](references/markdown-format.md) for the
  semantic Markdown and traceability contract.
- [references/math-transcription.md](references/math-transcription.md) for
  conservative visual formula handling.
- [references/visual-retention.md](references/visual-retention.md) for
  necessary crop decisions and the optional helper.
- [references/long-document-workflow.md](references/long-document-workflow.md)
  for progressive transcription and checkpointing.

The optional `scripts/pdf_helpers.py` provides only page count, page rendering,
and explicitly requested bbox cropping. It is not a semantic PDF parser.
