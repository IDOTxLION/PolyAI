"""Measure the OCR on a PDF that has embedded text, using that text as the answer key.

Usage: python ocr_check.py path/to/file.pdf [page number, default 1]

Every embedded word gets one verdict, which says at which stage it was lost:
  read        OCR read it (green)
  duplicate   OCR read it, then duplicate removal deleted that reading (blue)
  misread     OCR put a box on it but read something else, printed beside it (orange)
  undetected  OCR put no box on it (red)
Prints the counts and writes out/<pdf name>_p<page>_ocrcheck.pdf with the words boxed in
those colours.

This is a measuring tool only: the answer key must never feed back into what OCR outputs,
because most shop drawings have no embedded text to compare with.
"""

import re
import sys
from collections import Counter
from pathlib import Path

import pymupdf

from start import OCR_DPI, OUT_DIR, drop_duplicates, page_readings, render

NEAR = 3  # points: an OCR box this close to a word counts as touching it
COLORS = {"read": (0, 0.6, 0), "duplicate": (0, 0.3, 0.9), "misread": (0.95, 0.5, 0), "undetected": (0.9, 0, 0)}


def norm(text):
    """Reduce text to what matters for comparing: upper case, no spaces or stray punctuation."""
    return re.sub(r"[^0-9A-Z@/.\-]", "", text.upper())


def to_lines(readings):
    """Turn OCR readings (in pixels) into (box in points, normalised text, raw text)."""
    scale = 72 / OCR_DPI
    return [(pymupdf.Rect(r[2], r[3], r[4], r[5]) * pymupdf.Matrix(scale, scale), norm(r[6]), r[6]) for r in readings]


def verdict(token, box, kept, raw):
    """Classify one embedded word. Returns (verdict, what OCR read there or None)."""
    near = box + (-NEAR, -NEAR, NEAR, NEAR)
    if any(token in text for line_box, text, _ in kept if line_box.intersects(near)):
        return "read", None
    if any(token in text for line_box, text, _ in raw if line_box.intersects(near)):
        return "duplicate", None
    center = (box.tl + box.br) / 2
    on_it = [(abs(line_box), shown) for line_box, _, shown in kept if line_box.contains(center)]
    if on_it:
        return "misread", min(on_it)[1]  # the tightest box on the word
    return "undetected", None


def check_page(page):
    """Return (box in displayed coordinates, vertical, verdict, OCR text) per embedded word."""
    raw_readings = page_readings(render(page))
    kept, raw = to_lines(drop_duplicates(raw_readings)), to_lines(raw_readings)

    results = []
    for x0, y0, x1, y1, text, *_ in page.get_text("words"):
        token = norm(text)
        if not token:
            continue
        box = pymupdf.Rect(x0, y0, x1, y1) * page.rotation_matrix
        vertical = len(token) > 1 and box.height > 1.3 * box.width
        results.append((box, vertical, *verdict(token, box, kept, raw)))
    return results


def main():
    pdf_path = Path(sys.argv[1])
    page_number = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    doc = pymupdf.open(pdf_path)
    page = doc[page_number - 1]
    results = check_page(page)
    if not results:
        sys.exit("This page has no embedded text, so there is no answer key to check against.")

    for name, wanted in (("horizontal", False), ("vertical", True)):
        counts = Counter(found for _, vertical, found, _ in results if vertical == wanted)
        total = sum(counts.values())
        if total:
            parts = "  ".join(f"{key} {counts[key]} ({counts[key] / total:.0%})" for key in COLORS)
            print(f"{name:<10} {total:>5} words:  {parts}")

    shape = page.new_shape()  # one shape for the page: committing each box separately is very slow
    for box, _, found, ocr_text in results:
        shape.draw_rect(box * page.derotation_matrix)
        shape.finish(color=COLORS[found], width=0.5 if found == "read" else 1.2)
        if ocr_text:
            shape.insert_text(pymupdf.Point(box.x1 + 1, box.y1) * page.derotation_matrix, ocr_text,
                              fontsize=5, color=COLORS[found], rotate=page.rotation)
    shape.commit()

    OUT_DIR.mkdir(exist_ok=True)
    out_path = OUT_DIR / f"{pdf_path.stem}_p{page_number}_ocrcheck.pdf"
    single = pymupdf.open()
    single.insert_pdf(doc, from_page=page_number - 1, to_page=page_number - 1)
    single.save(out_path, garbage=3, deflate=True)
    print(f"green = read, blue = deleted as duplicate, orange = misread, red = undetected -> {out_path}")


if __name__ == "__main__":
    main()
