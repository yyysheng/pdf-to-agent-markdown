# Necessary visual retention

Decide from the rendered page whether a region carries information that linear
text cannot preserve. Retain visual evidence for physical diagrams, apparatus,
free-body diagrams, axes and curves, circuit/optical diagrams, maps, meaningful
illustrations, complex tables, and exercise figures.

Do not retain decorative lines, logos, page textures, watermarks, ordinary
separators, or an entire page merely because it contains a background image.
Vector artwork counts as visual content too, but only when it expresses
meaningful relationships.

When the environment supports region export, crop the smallest useful region,
choose a stable descriptive filename, and reference that exact filename in the
Markdown. Keep the crop in the same output directory or in one documented
asset subfolder. Do not create one screenshot per page as a substitute for
judgment.

If cropping is unavailable, finish the transcription first and leave a clear
review note identifying the page and visual region. Cropping is an enhancement,
not a reason to block the complete Markdown.

The optional `scripts/pdf_helpers.py` can render a page or crop an explicitly
selected PDF-point bounding box; it does not decide which region is important.
