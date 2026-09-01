# Conservative mathematical transcription

Read every formula from the rendered PDF page visually. The rule is:

```text
PDF visual = primary evidence
text layer = secondary copying aid
```

Text extraction may help locate or copy a long expression, but it cannot by
itself establish superscripts, subscripts, fractions, radicals, Greek letters,
vectors, matrices, limits, sums, integrals, scientific notation, or unit
relationships. A broken string such as `a = Δt Δv` must not be guessed into a
plausible equation.

Prefer display math for standalone equations:

```latex
$$
v^2-v_0^2=2ax
$$
```

Use inline `$...$` only for short expressions inside a sentence. Preserve
spacing and units in a readable way, but do not change a symbol's meaning.

Required workflow for a suspected formula:

1. Locate the expression in the text layer if that is useful.
2. Open the original PDF page and inspect the expression visually.
3. Temporarily ignore the text candidate's flattened two-dimensional layout.
   Reconstruct glyphs, subscripts, superscripts, fractions, radicals, grouping,
   Greek letters, vectors, operators, and unit exponents from the PDF visual or
   formula crop.
4. Use the text candidate only as a secondary ordinary-character cross-check;
   it must not be the structural template for the final LaTeX.
5. Write LaTeX only for the structure and glyphs confirmed by the visual.
6. Run the deterministic validator and repair any suspicious math finding
   before continuing.

Strings such as `vx`, `v0`, `omega2`, `Deltat`, and `v2/r` cannot be repaired
by string cleanup or familiar physics. Inspect the visual to determine whether
they contain subscripts, superscripts, fractions, or ordinary products. Visual
evidence determines the final mathematical structure.

## Formula provenance

A confirmed formula must be recorded in the conversion state as an explicit
visual decision, for example:

```json
{
  "source_text": "ω＝Δt",
  "source_asset": "assets/pdf_page_029_formula_01.png",
  "disposition": "latex_confirmed",
  "verification": "visual",
  "latex": "\\omega=\\frac{\\Delta\\theta}{\\Delta t}"
}
```

If no crop was needed, use `source_pdf_page` instead of `source_asset`. A
`latex_confirmed` decision without `verification: visual` and a traceable
visual source is invalid. The old `conservative_latex` label is not a final
provenance state.

The validator also runs a structural `math.extraction_artifact` screen. Scores
from 3 to 5 are warnings and scores of 6 or more are failures. Signals include
compressed digit/letter tokens such as `v102`, isolated number fragments,
fraction-like line flattening, unit exponents such as `m/s2`, full-width math
punctuation, low LaTeX-structure density, and dense alternating
letter/number tokens. These signals locate likely text-layer damage; they do
not establish the correct equation.

When any glyph, exponent, sign, denominator, or subscript remains uncertain:

1. transcribe only what is visually confirmed;
2. keep the uncertain source expression or a bounded crop;
3. add a page-specific `Transcription note` naming the uncertainty;
4. mark the page as needing review instead of claiming visual completion.

For a `math.extraction_artifact` warning or failure, reopen the source PDF page
identified in the report and then transcribe the visually confirmed expression.
Do not repair the fragment by applying a familiar physics formula or by
guessing the missing fraction, exponent, or subscript. A formula page is not
`visually_verified` until the artifact finding disappears or the page is
explicitly left in `pending_review` with a concrete reason.

Never place a normal Chinese sentence in `$$...$$`, `$...$`, `\(...\)`, or
`\[...\]`. Short labels such as `\text{合力}` may be retained when the page
clearly shows that label, but a sentence-like CJK run is an extraction error:
return to the source page and split the prose from the formula. An explicit
unresolved note is safer than incorrect LaTeX.

Do not use context to manufacture a likely equation. A correct-looking LaTeX
formula that was guessed is worse than an explicitly unresolved expression.

## Phase 2 disposition

Every formula crop created during progressive transcription is a Phase 2
checkpoint item. Reopen the source PDF page and inspect the crop in context:

- If every relevant glyph, grouping, exponent, sign, and unit relationship is
  clear after visual reconstruction, replace the text-layer fragment with
  `latex_confirmed` LaTeX and record `verification: visual` plus
  `source_asset` or `source_pdf_page`. Retain the crop when it remains useful
  evidence.
- If the expression is legible but its structure is not safe to encode after
  visual inspection, keep a bounded crop or confirmed-text-only
  representation and add one concrete `pending_review` entry naming the PDF
  page, ambiguous region, and `status: visually_reviewed_unresolved`.
- If the crop is decorative, redundant, or not a formula after visual review,
  remove it and clear the queue item.

After the final transcription page, automatically drain the formula queue; do
not emit a normal final state with `visual_review_required` still populated.
The formula counts in the final report must distinguish confirmed LaTeX from
crop-only or unresolved evidence. A validator PASS does not make an uninspected
formula `visually_verified`.
