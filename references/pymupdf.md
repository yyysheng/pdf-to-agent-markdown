# PyMuPDF fast path

When `PyMuPDF` is installed, the converter uses page text dictionaries and
block geometry rather than a single raw text stream. It orders ordinary pages
by vertical position, uses a left-column/right-column pass for suspected
two-column pages, and removes only repeated edge lines found on many pages.

Image blocks and image-object rectangles are rendered at a higher-resolution
clip to deterministic `fig_p###_##.png` assets. Table regions and uncertain
formula blocks receive `table_...` and `formula_uncertain_...` crops when their
bounding boxes can be supported by page text. A full-page `scan_...` image is
an explicit exception for a page with no usable text layer, not the default.

PyMuPDF does not make arbitrary formula recognition reliable by itself. The
converter only normalizes conservative scientific-notation patterns. Other
formula-like text remains source text and receives a review note; it is never
silently guessed into LaTeX.

If PyMuPDF is absent, Poppler's `pdftotext` keeps the CLI usable but cannot
provide reliable object bounds or image crops. The manifest and validation
report must expose that limitation.
