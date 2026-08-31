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

Treat the evidence sources explicitly:

```text
PDF visual = primary evidence
text layer = secondary copying aid
```

The text layer may reduce copying cost for ordinary prose, but it is never
evidence that the Agent has visually checked a page. When text extraction and
the rendered page disagree, follow the page visual.

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
6. Treat every formula, mathematical expression, superscript, subscript,
   fraction, radical, Greek letter, vector, matrix, or unit relationship as a
   `Visual-required page` item. Do not turn a text-layer fragment into LaTeX
   without looking at the original page. If the visual confirms the structure,
   write conservative LaTeX; if it does not, preserve only what is confirmed,
   keep a bounded formula crop when useful, and add a page-specific
   `Transcription note` rather than guessing. Chinese prose must not enter a
   math block unless it is a short, necessary mathematical label.
7. Decide visually which regions contain information that text cannot preserve,
   and make the retention decision immediately. Retain necessary diagrams,
   apparatus, free-body/circuit/optical diagrams, axes, curves, maps,
   meaningful illustrations, complex tables, and exercise figures. Crop only
   the smallest useful region with a stable filename and reference that exact
   file. A crop included in final Markdown means the Agent has already judged it
   necessary; do not attach a generic "confirm later" note. Do not retain
   decorative backgrounds, logos, watermarks, separators, or one full-page
   screenshot per page. If cropping is unavailable, record only that specific
   unresolved visual item.
8. Rebuild visually clear tables as Markdown tables. If cell boundaries,
   merged cells, values, or units are not reliable, retain the table visual and
   add only a qualified structural description; never invent cells.
9. Keep source content separate from Agent judgments. Use
   `> [Transcription note: ...]` only for a concrete uncertainty, provenance
   fact, or unresolved visual item; do not disguise Agent explanations as book
   text and do not use notes as a queue for work that can be solved now.
10. Before finishing, run `scripts/validate_md_assets.py` and repair every
    `FAIL`. In particular, Chinese prose inside math and suspicious Unicode
    garbage are hard failures, not deferred warnings. Treat a
    `math.extraction_artifact` `WARN` or `FAIL` as a request to reopen the
    original PDF page, not as a formula to repair from context. Resolve every
    `Visual-required page` that the current environment can inspect, then
    verify chapter continuity, formula delimiters, page markers, image links,
    note volume, and that the final requested page was reached.

## Formula extraction-artifact gate

Syntactically balanced LaTeX is not evidence that the formula was visually
confirmed. The validator's `math.extraction_artifact` check is deliberately
structural: it accumulates independent signs such as digit/letter flattening,
isolated number fragments, fraction-like broken layout, flattened unit
exponents, full-width math punctuation, low LaTeX-structure density, and dense
alternating letter/number tokens. It does not evaluate physics, solve
equations, or rewrite Markdown.

Scores `3–5` are `WARN`; scores `6` or higher are `FAIL`. Every finding carries
the nearest PDF page marker, line number, score, signals, and snippet. For
either status, reopen the source page and visually reconstruct the expression
before marking that page `visually_verified`. Never use physical intuition to
turn a flagged fragment into a plausible formula. If the source remains
unreadable, keep only the confirmed portion, add a page-specific
`Transcription note`, and retain the page in `pending_review`.

## Visual-required pages and completion

The following pages require actual page-visual inspection before they can be
marked complete: pages containing formulas, images, tables, coordinate plots,
experimental apparatus, force diagrams, circuits, optical paths, complex
multi-column or boxed layouts, obvious text-layer anomalies, or text whose
reading order is not coherent. Ordinary clean prose may be text-assisted, but
it still receives a source-page marker.

A page may be treated as `transcribed` after its text is drafted. It is
`visually_verified` only after the required visual checks and immediate crop
necessity decision are complete. A page containing a formula cannot be marked
`visually_verified` merely because its math delimiters are balanced or its
LaTeX is syntactically valid. Use `needs_review` only when the source page
is genuinely unreadable, damaged, missing, inaccessible, or remains ambiguous
after repeated visual inspection. A `math.extraction_artifact` `WARN` or
`FAIL` must be cleared by source-page visual review before completion. For a requested range, `status: completed`
requires no outstanding `visual_review_required` pages; a populated
`pending_review` list must name the concrete unresolved reason.

## Long-document checkpoint

For work likely to be interrupted, maintain a small sibling
`conversion_state.json` with the source, `last_completed_pdf_page`, current
section, Markdown path, `pending_review`, and `visual_review_required` pages,
plus `in_progress`/`completed` status. It is only a checkpoint. On resume,
verify the last marker before continuing so content is not duplicated. See
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
