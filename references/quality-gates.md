# Quality gates

The validator returns:

- `PASS`: no detected correctness or traceability issue.
- `WARN`: output is usable, but an agent should review a bounded limitation
  such as unknown printed pages, OCR need, uncertain formulas, or an
  unstructured table.
- `FAIL`: page coverage, asset existence/path safety, LaTeX delimiters, or
  manifest consistency is broken.

Useful checks include:

```text
python scripts/validate_md_assets.py output/book.md \
  --manifest output/conversion_manifest.json \
  --page-start 1 --page-end 20 \
  --json output/validation_report.json
```

Use `--strict` in CI when WARN should block publication. A warning is not a
reason to erase the source crop or guess a formula; it is a reason to inspect
the source PDF or select an explicit engine/OCR option.

An orphan asset usually means a failed post-process or a stale output
directory. Prefer a fresh sample directory or remove only known generated
assets before rerunning. The original PDF remains the authority for exact
layout and figure placement.
