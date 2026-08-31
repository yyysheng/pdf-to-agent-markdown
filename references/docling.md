# Docling adapter

Docling is an optional structure-oriented path. Detect the `docling` CLI (or
package) before invoking it. The documented Markdown command shape is:

```text
docling input.pdf --to md --output output/
```

Full Docling is convenient but sizeable; `docling-slim` supports opt-in PDF
and CLI extras when a smaller installation is important. PDF model weights and
OCR/VLM extras should be installed only for documents that need them.

As with Marker, accept an external result only after checking page coverage,
image references, and Markdown validity. The current adapter does not force a
whole-document result into a partial sample.

Upstream references:

- <https://github.com/docling-project/docling>
- <https://github.com/docling-project/docling/blob/main/packages/docling-slim/README.md>

The upstream project states an MIT license for Docling. Model/checkpoint terms
remain a separate deployment concern.
