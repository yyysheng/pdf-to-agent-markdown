# PDF to Agent Markdown

Vision-first transcription of complete PDFs into faithful, Agent-readable
Markdown with conservative formulas, PDF page traceability, progressive
long-document handling, and necessary visual retention.

The original PDF is the source of truth, and rendered page visuals are the
primary evidence for formulas, tables, reading order, and visual content.
The skill works through complete PDFs progressively while preserving semantic
structure, confirmed LaTeX formulas, reliable tables, and necessary visuals
such as diagrams and apparatus.

scripts/validate_md_assets.py provides deterministic QA for Markdown hygiene,
page markers, image references, formula delimiters, and structural extraction
warnings. It does not understand formula semantics, decide whether an
equation is physically correct, or repair a transcription.
