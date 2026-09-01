---
name: pdf-to-agent-markdown
description: Vision-first transcription of complete PDFs into faithful, Agent-readable Markdown with conservative formulas, PDF page traceability, progressive long-document handling, and necessary visual retention.
metadata:
  short-description: Vision-first PDFs to faithful Agent-readable Markdown
---

# PDF to Agent Markdown

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

## Three-phase completion model

`needs_review` is a checkpoint, not the normal final state for a complete PDF.
Use three explicit phases:

1. **Phase 1 — progressive transcription:** read the complete source in coherent
   windows, write the Markdown continuously, and queue formula/visual pages in
   `visual_review_required`.
2. **Phase 2 — visual verification:** after the final transcription page is
   reached, automatically drain `visual_review_required`. Do not wait for a new
   user prompt. Reopen each queued source page and make the formula and visual
   retention decision while the page evidence is available.
3. **Phase 3 — final QA:** run deterministic validation, check that source-page
   markers and asset links are complete, and emit a final state. A valid
   `completed` state has empty review queues; genuine unresolved items are
   concrete entries in `pending_review` and use
   `completed_with_review_items`.

`visually_verified` is a provenance claim, not a synonym for validator PASS.
Only record a page there after the Agent has actually inspected the relevant
source visual and made the immediate crop/table/formula decision.

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
   math block unless it is a short, necessary mathematical label. Queue the
   page in `visual_review_required` until this disposition is complete.
7. Decide visually which regions contain information that text cannot preserve,
   and make the retention decision immediately. Retain necessary diagrams,
   apparatus, free-body/circuit/optical diagrams, axes, curves, maps,
   meaningful illustrations, complex tables, and exercise figures. Crop only
   the smallest useful region with a stable filename and reference that exact
   file. A crop included in final Markdown means the Agent has already judged it
   necessary; do not attach a generic "confirm later" note. Do not retain
   decorative backgrounds, logos, watermarks, separators, or one full-page
   screenshot per page. If cropping is unavailable, record only that specific
   unresolved visual item. During Phase 2, every queued visual must be marked
   retained, Markdown-sufficient, or concretely unresolved; do not leave a
   generic future-review note.
8. Rebuild visually clear tables as Markdown tables. If cell boundaries,
   merged cells, values, or units are not reliable, retain the table visual and
   add only a qualified structural description; never invent cells.
9. Keep source content separate from Agent judgments. Use
   `> [Transcription note: ...]` only for a concrete uncertainty, provenance
   fact, or unresolved visual item; do not disguise Agent explanations as book
   text and do not use notes as a queue for work that can be solved now.
10. When Phase 1 reaches the final requested PDF page, transition to
    `status: visual_review` and automatically drain every page in
    `visual_review_required`. For each formula crop, inspect the source page:
    reconstruct the mathematical structure from the visual first, then use the
    text candidate only as a secondary cross-check. A confirmed formula uses
    `latex_confirmed` with visual provenance; an ambiguous structure stays
    `crop_only` or confirmed-text-only and gets a concrete page-specific entry
    in `pending_review`. Never guess or reuse the text candidate as the LaTeX
    template.
    Apply the same retained/Markdown-sufficient/unresolved decision to every
    visual region. Only after the queue is empty, run
    `scripts/validate_md_assets.py` and repair every `FAIL`. In particular,
    Chinese prose inside math and suspicious Unicode garbage are hard failures,
    not deferred warnings. Treat a `math.extraction_artifact` `WARN` or `FAIL`
    as a request to reopen the original PDF page, not as a formula to repair
    from context. Then verify chapter continuity, formula delimiters, page
    markers, image links, note volume, and that the final requested page was
    reached before emitting the final state.

`visual_review_required` means the source has not yet been inspected. A final
`pending_review` item means the source was inspected but the issue remains
unresolved. Every final pending item must identify `pdf_page`, `type` or
`kind`, `status: visually_reviewed_unresolved`, and a concrete reason; never
use phrases such as “需回看 PDF”, “待视觉确认”, or “confirm later” in a final
pending reason.

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

## Visual formula reconstruction contract

A visually confirmed formula MUST be reconstructed from the PDF visual or its
formula crop. The text-layer candidate may locate the expression and provide a
secondary ordinary-character cross-check, but it MUST NOT serve as the
structural template for final LaTeX. In Phase 2, temporarily ignore the
candidate's flattened two-dimensional structure and reread the visual for
glyphs, subscripts, superscripts, fractions, radicals, grouping, Greek letters,
vectors, operators, and unit exponents before writing LaTeX.

Do not turn strings such as `vx`, `v0`, `omega2`, `Deltat`, or `v2/r` into a
formula by string cleanup or physics familiarity. Inspect the visual to decide
whether the source shows a subscript, superscript, fraction, or an ordinary
product/variable. The final structure must be determined by visual evidence;
text-layer content is only a locator and cross-check.

Record every confirmed formula decision with `disposition: latex_confirmed`,
`verification: visual`, a non-empty `latex`, and either `source_asset` or
`source_pdf_page`. A `conservative_latex` label without this provenance is not
a valid final decision. If the visual was inspected but the structure remains
unsafe, use `disposition: crop_only`, `verification: visual`, a source asset or
page, and an `unresolved_reason`; add the corresponding concrete
`pending_review` item. Do not mark a page `visually_verified` when its formula
decisions are missing, ambiguous, or lack this provenance.

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
requires no outstanding `visual_review_required` pages and an empty
`pending_review` list. `status: visual_review` is the legal active state while
the queue is being drained. If the queue is empty but genuinely unresolved
items remain, use `status: completed_with_review_items`; each pending entry
must identify the PDF page, the specific region or content, and the reason it
could not be resolved. `status: needs_review` may be retained for an interrupted
checkpoint, but is not a successful final state for a complete PDF.

## Long-document checkpoint

For work likely to be interrupted, maintain a small sibling
`conversion_state.json` with `schema_version: 3`,
`skill: {name: "pdf-to-agent-markdown", revision: "<SHA>"}`, the source,
`last_completed_pdf_page`, current section, Markdown path, `pending_review`,
and `visual_review_required` pages, plus the workflow status. Keep
`visual_review` distinct from the final `completed_with_review_items` state.
It is only a checkpoint. On resume, verify the last marker before continuing so
content is not duplicated. See
[references/long-document-workflow.md](references/long-document-workflow.md).

## Supporting guidance

When the bundled `scripts/vision_first_pdf_to_md.py` runner is available, use
it as the lightweight workflow/state/crop orchestration layer. It does not
replace Agent visual interpretation: only explicit Agent visual decisions may
produce `latex_confirmed`.

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
