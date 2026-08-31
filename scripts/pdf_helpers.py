#!/usr/bin/env python3
"""Small deterministic PDF helpers for a vision-first transcription workflow.

This module deliberately does not extract headings, formulas, tables, reading
order, or page semantics.  It only supplies facts or assets requested by the
Agent: total page count, a rendered page, or a crop of an explicitly chosen
bounding box.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


__all__ = ["page_count", "parse_bbox", "render_page"]


def _fitz_module():
    try:
        import pymupdf as fitz  # type: ignore

        return fitz
    except ImportError:
        try:
            import fitz  # type: ignore

            return fitz
        except ImportError:
            return None


def _command(name: str) -> str | None:
    return shutil.which(name)


def page_count(pdf: Path) -> int:
    """Return the number of pages without interpreting document semantics."""

    pdf = pdf.resolve()
    if not pdf.is_file():
        raise FileNotFoundError(pdf)
    fitz = _fitz_module()
    if fitz is not None:
        document = fitz.open(str(pdf))
        try:
            return int(document.page_count)
        finally:
            document.close()
    pdfinfo = _command("pdfinfo")
    if pdfinfo:
        result = subprocess.run(
            [pdfinfo, str(pdf)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        match = re.search(r"^Pages:\s*(\d+)", result.stdout, re.MULTILINE)
        if match:
            return int(match.group(1))
    raise RuntimeError("cannot determine PDF page count; install PyMuPDF or Poppler pdfinfo")


def _check_page(pdf: Path, page: int) -> None:
    if page < 1:
        raise ValueError("page numbers are one-based and must be positive")
    total = page_count(pdf)
    if page > total:
        raise ValueError(f"PDF page {page} is outside the document (1-{total})")


def parse_bbox(value: str) -> tuple[float, float, float, float]:
    """Parse ``x0,y0,x1,y1`` in PDF points."""

    try:
        values = tuple(float(item.strip()) for item in value.split(","))
    except ValueError as exc:
        raise ValueError("bbox must be x0,y0,x1,y1 in PDF points") from exc
    if len(values) != 4 or values[2] <= values[0] or values[3] <= values[1]:
        raise ValueError("bbox must have four values with x1>x0 and y1>y0")
    return values  # type: ignore[return-value]


def render_page(
    pdf: Path,
    page: int,
    destination: Path,
    *,
    dpi: int = 160,
    bbox: tuple[float, float, float, float] | None = None,
) -> Path:
    """Render one page or one explicitly selected region to a PNG."""

    pdf = pdf.resolve()
    destination = destination.resolve()
    if dpi < 36 or dpi > 600:
        raise ValueError("dpi must be between 36 and 600")
    _check_page(pdf, page)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fitz = _fitz_module()
    if fitz is not None:
        document = fitz.open(str(pdf))
        try:
            pdf_page = document.load_page(page - 1)
            clip = fitz.Rect(bbox) if bbox is not None else None
            if clip is not None:
                clip = clip.intersect(pdf_page.rect)
                if clip.is_empty or clip.width <= 0 or clip.height <= 0:
                    raise ValueError("bbox does not intersect the selected PDF page")
            pixmap = pdf_page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), clip=clip, alpha=False)
            pixmap.save(str(destination))
        finally:
            document.close()
        return destination

    if bbox is not None:
        raise RuntimeError("explicit PDF-region cropping requires PyMuPDF in this environment")
    pdftoppm = _command("pdftoppm")
    if not pdftoppm:
        raise RuntimeError("cannot render PDF; install PyMuPDF or Poppler pdftoppm")
    with tempfile.TemporaryDirectory(prefix="pdf_md_render_") as temp_dir:
        prefix = Path(temp_dir) / "page"
        result = subprocess.run(
            [pdftoppm, "-f", str(page), "-l", str(page), "-singlefile", "-png", "-r", str(dpi), str(pdf), str(prefix)],
            capture_output=True,
            check=False,
        )
        generated = prefix.with_suffix(".png")
        if result.returncode != 0 or not generated.is_file():
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(detail or "pdftoppm failed to render the selected page")
        shutil.copy2(generated, destination)
    return destination


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    count_parser = subparsers.add_parser("page-count", help="print total page count")
    count_parser.add_argument("pdf", type=Path)

    for name, help_text in (("render-page", "render one complete page"), ("crop-page", "crop one explicitly selected region")):
        command_parser = subparsers.add_parser(name, help=help_text)
        command_parser.add_argument("pdf", type=Path)
        command_parser.add_argument("page", type=int)
        command_parser.add_argument("output", type=Path)
        command_parser.add_argument("--dpi", type=int, default=160)
        if name == "crop-page":
            command_parser.add_argument("--bbox", required=True, help="x0,y0,x1,y1 in PDF points")

    args = parser.parse_args()
    try:
        if args.command == "page-count":
            print(page_count(args.pdf))
        else:
            bbox = parse_bbox(args.bbox) if args.command == "crop-page" else None
            print(render_page(args.pdf, args.page, args.output, dpi=args.dpi, bbox=bbox))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_main())
