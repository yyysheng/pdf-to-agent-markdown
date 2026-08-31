# Progressive transcription for long PDFs

The user may provide an entire book, manual, or paper collection. Do not ask
them to split it and do not make a fixed page batch size part of the workflow.
Choose internal reading windows dynamically from context capacity, chapter
boundaries, visual density, and whether the current section is simple or
complex.

## State and persistence

Create the Markdown output early and keep appending/updating that same file.
For long work, maintain a small sibling `conversion_state.json` checkpoint:

```json
{
  "source": "book.pdf",
  "completed_pdf_pages": 128,
  "current_section": "第三章 第二节",
  "output": "book.md",
  "pending_review": [47, 83],
  "status": "in_progress"
}
```

Update it after each coherent section or safe interruption point. It is a
checkpoint, not a second copy of the document. On resume, verify the last page
marker and current section before continuing; never duplicate already written
content. Mark `completed` only after the final page and final review.

## Reading strategy

1. Establish total pages and inspect the cover/contents pages directly.
2. Build the chapter/section outline in the Markdown while retaining the
   source table of contents.
3. Read a semantically coherent region, using more pages for clean prose and
   fewer pages when formulas, tables, or dense diagrams need close inspection.
4. Transcribe immediately into the same Markdown, including source-page
   markers and bounded visual evidence.
5. At chapter transitions, check continuity, headings, unresolved notes, and
   the next source location before advancing.
6. At EOF, verify that the last requested PDF page was reached, run the
   optional deterministic validator, and visually recheck all pending items.

Never write internal `Batch`, `Chunk`, or context-window labels into the final
Markdown. They are process state, not source content.
