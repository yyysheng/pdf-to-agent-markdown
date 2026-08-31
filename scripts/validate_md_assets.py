#!/usr/bin/env python3
"""Validate local image links, LaTeX delimiters, and PDF page markers in Markdown."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import unquote


IMAGE_RE = re.compile(r"!\[[^\]]*\]\((?:<([^>]+)>|([^\s)]+))\)")
PAGE_RE = re.compile(r"<!--\s*PDF page\s+(\d+)\s*-->")


def local_image_refs(markdown: str) -> list[str]:
    refs: list[str] = []
    for match in IMAGE_RE.finditer(markdown):
        ref = match.group(1) or match.group(2) or ""
        if re.match(r"^(?:[a-z][a-z0-9+.-]*:|//)", ref, re.I):
            continue
        refs.append(unquote(ref.split("#", 1)[0].split("?", 1)[0]))
    return refs


def delimiter_errors(markdown: str) -> list[str]:
    errors: list[str] = []
    display_count = markdown.count("$$")
    if display_count % 2:
        errors.append(f"unbalanced display-math delimiters: {display_count} occurrences of $$")
    without_display = markdown.replace("$$", "")
    single_count = len(re.findall(r"(?<!\\)\$", without_display))
    if single_count % 2:
        errors.append(f"unbalanced inline-math delimiters: {single_count} unescaped $ markers")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("markdown", type=Path, help="Markdown file to validate")
    parser.add_argument("--page-start", type=int, help="expected first PDF page marker")
    parser.add_argument("--page-end", type=int, help="expected last PDF page marker")
    parser.add_argument(
        "--forbid",
        action="append",
        default=[],
        help="literal residual string that should not occur; may be repeated",
    )
    args = parser.parse_args()

    md_path = args.markdown.resolve()
    if not md_path.is_file():
        print(f"ERROR: Markdown file not found: {md_path}", file=sys.stderr)
        return 2

    markdown = md_path.read_text(encoding="utf-8")
    refs = local_image_refs(markdown)
    missing = [ref for ref in refs if not (md_path.parent / ref).is_file()]
    pages = [int(value) for value in PAGE_RE.findall(markdown)]
    errors = delimiter_errors(markdown)

    if args.page_start is not None and (not pages or pages[0] != args.page_start):
        errors.append(f"first page marker is {pages[0] if pages else 'missing'}, expected {args.page_start}")
    if args.page_end is not None and (not pages or pages[-1] != args.page_end):
        errors.append(f"last page marker is {pages[-1] if pages else 'missing'}, expected {args.page_end}")
    for forbidden in args.forbid:
        if forbidden in markdown:
            errors.append(f"forbidden residual text found: {forbidden!r}")

    print(f"markdown: {md_path}")
    print(f"image references: {len(refs)} (unique: {len(set(refs))})")
    print(f"page markers: {len(pages)}")
    print(f"inline math markers: {len(re.findall(r'(?<!\\)\$(?!\$)', markdown))}")
    print(f"display math blocks: {markdown.count('$$') // 2}")
    if missing:
        print("missing images:")
        for ref in missing:
            print(f"  - {ref}")
    if errors:
        print("validation: FAILED")
        for error in errors:
            print(f"  - {error}")
        return 1

    print("validation: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
