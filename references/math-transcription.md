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
3. Write LaTeX only for the structure and glyphs confirmed by the page.
4. Run the deterministic validator and repair any suspicious math finding
   before continuing.

When any glyph, exponent, sign, denominator, or subscript remains uncertain:

1. transcribe only what is visually confirmed;
2. keep the uncertain source expression or a bounded crop;
3. add a page-specific `Transcription note` naming the uncertainty;
4. mark the page as needing review instead of claiming visual completion.

Never place a normal Chinese sentence in `$$...$$`, `$...$`, `\(...\)`, or
`\[...\]`. Short labels such as `\text{合力}` may be retained when the page
clearly shows that label, but a sentence-like CJK run is an extraction error:
return to the source page and split the prose from the formula. An explicit
unresolved note is safer than incorrect LaTeX.

Do not use context to manufacture a likely equation. A correct-looking LaTeX
formula that was guessed is worse than an explicitly unresolved expression.
