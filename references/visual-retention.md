# Necessary visual retention

Decide from the rendered page whether a region carries information that linear
text cannot preserve. Retain visual evidence for physical diagrams, apparatus,
free-body diagrams, axes and curves, circuit/optical diagrams, maps, meaningful
illustrations, complex tables, and exercise figures.

Make the decision while viewing the source page:

```text
visual region found → inspect it → can faithful text/table replace it?
                         ├─ yes: do not retain a crop
                         └─ no: retain the smallest useful crop
```

If a crop is referenced by final Markdown, that is already the Agent's
necessity decision. Do not append a generic request to decide its importance at
a later review stage. Leave a review note only when the region's meaning or
legibility remains genuinely unresolved after looking at the page.

Do not retain decorative lines, logos, page textures, watermarks, ordinary
separators, or an entire page merely because it contains a background image.
Vector artwork counts as visual content too, but only when it expresses
meaningful relationships.

When the environment supports region export, crop the smallest useful region,
choose a stable descriptive filename, and reference that exact filename in the
Markdown. Keep the crop in the same output directory or in one documented
asset subfolder. Do not create one screenshot per page as a substitute for
judgment.

Pages with diagrams, tables, coordinate plots, experimental apparatus,
force/free-body diagrams, circuits, optical paths, complex boxed or multi-column
layouts, or visibly unreliable text extraction are `Visual-required` pages.
They cannot be marked complete from the text layer alone. Ordinary clean prose
pages may remain text-assisted while preserving their PDF page markers.

If cropping is unavailable, finish the transcription first and leave a clear
review note identifying the page and visual region. If the current environment
can inspect and crop the page, resolve the decision immediately; cropping is an
enhancement, not a reason to block the complete Markdown.

The optional `scripts/pdf_helpers.py` can render a page or crop an explicitly
selected PDF-point bounding box; it does not decide which region is important.

## Phase 2 disposition

After Phase 1 reaches the final requested page, automatically inspect every
entry in `visual_review_required`. For each region, record one of three
outcomes: necessary information retained as the smallest useful crop;
faithfully replaceable by Markdown/table text with no crop; or concretely
unresolved with a page-specific `pending_review` reason. Do not leave a generic
"review later" note for a region that has already been retained, and do not
claim `visually_verified` from deterministic asset validation alone.

The final active visual queue must be empty. A document with unresolved but
explicitly described regions uses `completed_with_review_items`; a clean
`completed` document has no pending items either.
