# Agent-readable Markdown contract

Use Markdown as a semantic transcription, not a two-dimensional PDF replica
and not a summary. Preserve definitions, explanations, examples, questions,
solutions, captions, units, and meaningful qualifiers. Remove only layout
noise such as repeated running headers, decorative separators, and accidental
line breaks.

## Structure

- Keep the book title as `#` and reconstruct chapters, sections, and
  subsections with a natural heading hierarchy.
- Read the table of contents early and retain it as a `## 目录` or `## Table
  of Contents` section. Normalize its indentation into nested Markdown lists.
- Convert sidebars, notes, examples, and exercises into labeled subsections or
  blockquotes when that makes their role clear. Do not merge them silently into
  surrounding prose.
- Keep original content separate from Agent review notes. Use a visible note,
  for example `> [Transcription note: ...]`, for a concrete uncertainty or
  unresolved visual decision. In a final state, the note must describe an issue
  already inspected and must not be a generic request to revisit the source.

## Traceability

Put a marker at every source-page transition:

```html
<!-- PDF page 37 -->
```

If a printed page number is visually certain, use:

```html
<!-- PDF page 45 | printed page 37 -->
```

Never infer a printed number solely from PDF index arithmetic. PDF page is
always the reliable locator; printed page is optional metadata.

## Images and tables

Use stable descriptive alt text and a relative filename when a visual asset is
saved. Keep the asset beside the Markdown file or in a clearly named child
folder. A visual retained for provenance should be referenced exactly once.
Tables should become normal Markdown tables only when the page makes cell
boundaries and values clear; otherwise keep a crop and add a short, explicitly
qualified structural note.
