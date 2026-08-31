# Pipeline contract

The stable contract is:

```text
inspect_pdf -> route -> convert -> post-process -> quality gate -> validate -> manifest
```

`inspect_pdf.py` emits page-level signals without loading heavy models. The
`convert_pdf.py` CLI accepts one-based PDF ranges such as `1-20` or `1-3,7`.
The output directory contains `<input-stem>.md`, referenced visual assets,
`conversion_manifest.json`, and `validation_report.json`.

## Engine routing

`auto` means:

1. PyMuPDF for born-digital, low-complexity pages.
2. An installed Marker or Docling adapter for complex full-document input.
3. MinerU only for scan/OCR-heavy or otherwise failed quality-gate cases.
4. Poppler text extraction as a dependency-light compatibility path.

The current optional adapters are whole-document command adapters. A partial
range is never handed to one of them unless the adapter can prove that its
output preserves the requested page coverage; otherwise the page-local core
path is retained and the manifest records the reason. This is safer than
silently converting an entire textbook for a sample request.

## Page identity

Every page marker uses the form:

```html
<!-- PDF page 17 | printed page 9 -->
```

When the printed number cannot be proven, it is `unknown`. Use
`--printed-page-offset N` or a JSON object passed to `--printed-page-map` for
an externally verified mapping.

## Manifest essentials

The manifest records the requested and selected engines, pages per engine,
printed-page mapping, image assets and bounding boxes, table/formula counts,
warnings, quality-gate status, and the final validator status. It is part of
the analysis input: an agent should inspect it before trusting a conversion.

## Implementation boundary

The P0 path is complete and runnable with PyMuPDF or Poppler. P1 support
includes object/bounding-box crops, explicit printed-page mapping, and
whole-document Marker/Docling/MinerU dispatch with output validation. P2
features remain deliberately conservative: true per-page external-engine
merging, high-accuracy OCR language selection, and general table/formula
semantic verification need an engine-specific adapter and should be reported
as unresolved warnings rather than fabricated in the core path.
