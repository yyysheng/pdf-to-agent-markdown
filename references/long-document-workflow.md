# Progressive transcription for long PDFs

The user may provide an entire book, manual, or paper collection. Do not ask
them to split it and do not make a fixed page batch size part of the workflow.
Choose internal reading windows dynamically from context capacity, chapter
boundaries, visual density, and whether the current section is simple or
complex.

## State and persistence

Create the Markdown output early and keep appending/updating that same file.
For long work, maintain a small sibling `conversion_state.json` checkpoint.
Use schema version 3 and record the exact Skill revision that produced the
state:

```json
{
  "schema_version": 3,
  "skill": {
    "name": "pdf-to-agent-markdown",
    "revision": "<SHA>"
  },
  "source": "book.pdf",
  "last_completed_pdf_page": 128,
  "completed_pdf_pages": 128,
  "current_section": "第三章 第二节",
  "output": "book.md",
  "pending_review": [47, 83],
  "visual_review_required": [83],
  "status": "in_progress"
}
```

Update it after each coherent section or safe interruption point. It is a
checkpoint, not a second copy of the document. On resume, verify the last page
marker and current section before continuing; never duplicate already written
content. Keep the page state conceptually separate: `transcribed` is a draft
text state, `visually_verified` means the required source-page inspection and
visual-retention decision are complete, and `needs_review` is reserved for an
actually unresolved or interrupted page.

## Phase transitions

During Phase 1, keep `status: in_progress` and add formula/visual pages to
`visual_review_required` as they are transcribed. When the final requested PDF
page has been written, automatically change the state to
`status: visual_review` and drain that queue. This is an active phase, not a
request for another user message. Inspect the source page for every queue item,
record `visually_verified_pdf_pages` only after the inspection, and either
retain a necessary visual, replace it with faithful Markdown, or add a
page-specific unresolved item to `pending_review`.

Phase 3 starts only when `visual_review_required` is empty. Run deterministic
QA, repair hard failures, and verify page-marker and asset continuity. A clean
final document uses `status: completed` with both queues empty. If real
ambiguities remain after the visual pass, use `status: completed_with_review_items`
with concrete `pending_review` entries; do not leave the active visual queue
populated. Such a state is complete as a transcription artifact but explicitly
not fully resolved.

## Reading strategy

1. Establish total pages and inspect the cover/contents pages directly.
2. Build the chapter/section outline in the Markdown while retaining the
   source table of contents.
3. Read a semantically coherent region, using more pages for clean prose and
   fewer pages when formulas, tables, or dense diagrams need close inspection.
   Any page containing formulas, images, tables, plots, apparatus, force
   diagrams, circuits, optical paths, complex boxes/columns, or extraction
   anomalies is `Visual-required` and must be opened before it is completed.
4. Transcribe immediately into the same Markdown, including source-page
   markers and bounded visual evidence.
5. Resolve formula structure, text-layer corruption, and crop necessity during
   that page inspection. Do not add a generic "confirm later" note for an asset
   already retained in Markdown.
6. At chapter transitions, check continuity, headings, unresolved notes, and
   the next source location before advancing.
7. At EOF, verify that the last requested PDF page was reached, run the
   deterministic validator, repair hard failures, and visually recheck only the
   genuinely unresolved items. A page that could have been inspected in the
   current environment must not be left in `pending_review`.

Never write internal `Batch`, `Chunk`, or context-window labels into the final
Markdown. They are process state, not source content.
