"""Extract the columns of the shop drawings (dessins d'atelier) into Appendix A records.

The fabricator draws the columns as one big table per page:

    - a column mark ("A-6": the two grid lines the column stands on) at the bottom of each
      table column, the same name the plan extractor derives from the grid;
    - storey names down the left margin ("NIVEAU 2", "REZ-DE-CHAUSSÉE", "SOUS-SOL"...), each
      at the height of its floor line;
    - in each cell, the reinforcement of that column between two storeys:

          VERT:  4 25M 25M12-06          vertical bars: quantity, diameter, mark
          ÉTRI: 14 10M 10MT16X24 @6"     ties: quantity, diameter, mark, spacing
          ÉTRI:  3 10M 10MT16X24 dalle   ties inside the slab depth: no spacing

A cell is filed under the plan sheet of the storey the column stands on, so a shop-drawing
record and its plan record share the same `feuillet` and `element`.

This module is used by extract_columns.py, which writes the JSON. Run on its own it only
prints counts: python atelier_columns.py [plan.pdf] [folder or PDFs of column shop drawings]
"""

import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pymupdf

from plan_columns import DEFAULT_PDF as DEFAULT_PLAN
from plan_columns import INCH, level_key, sheet_levels
from schema import Armature, Element

DEFAULT_ATELIER = Path("data/CLP/DA/Colonnes")

MARK = re.compile(r"[A-Z](\.\d)?-\d{1,2}(\.\d)?")  # "A-6", "B.5-7", "C-12.5"
BAR_LINE = re.compile(r"^(VERT|[ÉE]TRI|[ÉE]PIN)\.?:\s*(\d+)\s+(\d{2}M)\s+(\S+)\s*(.*)$")
SPACING = re.compile(rf"@\s*(\d+(?:\.\d+)?)(?:\s+(\d+)/(\d+))?\s*(?:{INCH}|'')")
MARK_LENGTH = re.compile(r"(\d{1,3})-(\d{1,2})\.?$")  # a straight bar's mark ends with its length: 12-06 = 12'-6"
ELEVATION = re.compile(r"^[ÉE]L\.?:")

ROW_GAP = 8  # points: marks within this height of each other are on the same row


def text_lines(page):
    """Return (box in displayed coordinates, text) for every line of embedded text."""
    lines = []
    for block in page.get_text("dict")["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            text = " ".join("".join(span["text"] for span in line["spans"]).split())
            if text:
                lines.append((pymupdf.Rect(line["bbox"]) * page.rotation_matrix, text))
    return lines


def find_rulings(page):
    """Return the x of the long vertical lines of the page: the table's column rulings."""
    # A page holds hundreds of thousands of segments. Building a PyMuPDF point for each end
    # took most of the extraction time, so the drawings are read as plain tuples
    # (get_cdrawings) and the rotation matrix is read once and applied by hand.
    m = page.rotation_matrix

    def displayed(x, y):
        return x * m.a + y * m.c + m.e, x * m.b + y * m.d + m.f

    length = Counter()
    for drawing in page.get_cdrawings():
        for item in drawing["items"]:
            if item[0] == "l":
                (ax, ay), (bx, by) = displayed(*item[1]), displayed(*item[2])
                if abs(ax - bx) < 0.5:
                    length[round(ax)] += abs(ay - by)
            elif item[0] == "re":
                (ax, ay), (bx, by) = displayed(*item[1][:2]), displayed(*item[1][2:])
                if abs(ax - bx) < 3:
                    length[round(min(ax, bx))] += abs(ay - by)
    return sorted(x for x, total in length.items() if total > 0.25 * page.rect.height)


def find_columns(page, lines):
    """Return the table columns that carry a column mark, left to right, as (left, right, name).

    A mark ("A-6") names the table column it is written in. Marks are written under the
    table and sometimes repeated above it, at a different place within the column, so each
    one is filed by the pair of rulings around it rather than by its own position.
    """
    marks = [(box, text) for box, text in lines if MARK.fullmatch(text)]
    rows = Counter(round(box.y0 / ROW_GAP) for box, _ in marks)
    marks = [(box, text) for box, text in marks  # a real mark row holds several marks
             if sum(rows[round(box.y0 / ROW_GAP) + d] for d in (-1, 0, 1)) >= 3]
    rulings = find_rulings(page)

    columns = {}
    for box, name in sorted(marks, key=lambda m: m[0].y0):  # lower rows last, so they win
        left = max((x for x in rulings if x <= box.x0 + 1), default=None)
        right = min((x for x in rulings if x > box.x0 + 1), default=None)
        if left is not None and right is not None:
            columns[left] = (left, right, name)
    return sorted(columns.values())


def find_levels(lines, table_left):
    """Return the storey labels of the left margin, top to bottom, as (y of the floor line, level key).

    Other labels in the margin (footings, intermediate slabs) are left out: a cell runs from
    one storey to the next.
    """
    levels = []
    for box, text in lines:
        if box.x0 < table_left - 20 and not BAR_LINE.match(text) and not ELEVATION.match(text) and level_key(text):
            levels.append((box.y0 - 3, level_key(text)))  # the name is written just under its floor line
    return sorted(levels)


def parse_bar_line(text):
    """Turn one VERT / ÉTRI / ÉPIN line into an Armature, or None if it is not one."""
    match = BAR_LINE.match(text)
    if not match:
        return None
    keyword, quantity, diameter, mark, rest = match.groups()

    spacing_mm = None
    spacing = SPACING.search(rest)
    if spacing:
        inches = float(spacing.group(1))
        if spacing.group(2):
            inches += int(spacing.group(2)) / int(spacing.group(3))
        spacing_mm = round(inches * 25.4)

    length_mm = None
    length = MARK_LENGTH.search(mark)
    if keyword == "VERT" and length and int(length.group(2)) < 12:
        length_mm = round((int(length.group(1)) * 12 + int(length.group(2))) * 25.4)

    return Armature(repere=mark.rstrip("."), diametre=diameter, quantite=int(quantity),
                    espacement_mm=spacing_mm, longueur_mm=length_mm)


def extract_page(page, fichier, plan_sheets):
    """Return (records, counts, cells) for one page; cells holds (label, box) for the check PDF."""
    lines = text_lines(page)
    columns = find_columns(page, lines)
    counts = Counter(marks=len(columns))
    if not columns:
        return [], counts, []
    levels = find_levels(lines, columns[0][0])
    counts["storey labels"] = len(levels)

    cells = defaultdict(list)  # (column index, index of the storey below) -> [(box, armature)]
    for box, text in lines:
        armature = parse_bar_line(text)
        if armature is None:
            continue
        column = next((i for i, (left, right, _) in enumerate(columns) if left - 2 <= box.x0 < right - 2), None)
        if column is None:
            counts["bar lines in a column without a mark"] += 1
            continue
        below = next((i for i, (y, _) in enumerate(levels) if y > box.y0), None)
        if below is None:  # under the lowest storey: the column's base on its footing, and dowels
            counts["bar lines under the lowest storey"] += 1
            continue
        if below == 0:
            counts["bar lines above the top storey"] += 1
            continue
        cells[(column, below)].append((box, armature))

    records, drawn = [], []
    for (column, below), found in sorted(cells.items()):
        key = levels[below][1]
        feuillet = plan_sheets.get(key, f"?{key}")
        if feuillet.startswith("?"):
            counts["cells with no plan sheet"] += 1
        element = columns[column][2]
        area = pymupdf.Rect()
        for box, _ in found:
            area |= box
        center = (area.tl + area.br) / 2
        found.sort(key=lambda item: (item[0].y0, item[0].x0))
        records.append(Element(
            id=f"{feuillet}_{element}_atelier", source="atelier", fichier=fichier, feuillet=feuillet,
            page=page.number + 1, x=round(center.x, 1), y=round(center.y, 1),
            type_element="colonne", element=element, armature=[armature for _, armature in found],
        ))
        drawn.append((f"{element} {feuillet}", area))
    counts["records"] = len(records)
    return records, counts, drawn


def draw_check(page, drawn):
    """Box each extracted cell and write its column and plan sheet above it."""
    to_unrotated = page.derotation_matrix
    shape = page.new_shape()
    for label, area in drawn:
        color = (0.9, 0, 0) if "?" in label else (0, 0.35, 0.9)
        shape.draw_rect((area + (-2, -2, 2, 2)) * to_unrotated)
        shape.finish(color=color, width=0.8)
        shape.insert_text(pymupdf.Point(area.x0, area.y0 - 3) * to_unrotated, label,
                          fontsize=6, color=color, rotate=page.rotation)
    shape.commit()


def extract(atelier_pdfs, plan_pdf, check_dir=None):
    """Return the column records of a project's shop drawings, and counts per file.

    The same cell is usually repeated on later drawings, whose tables carry the earlier
    storeys along. One record is kept per sheet and column: the one from the first drawing
    that details it, taken as the drawing issued to fabricate that storey. On the CLP project
    this agreed with the plan more often than keeping the last drawing, and found the same
    known mismatches. The report counts the repeats that differ, so they are not hidden.
    """
    plan_sheets = sheet_levels(pymupdf.open(plan_pdf))
    by_id, report = {}, {}
    repeated = Counter()
    for pdf_path in sorted(atelier_pdfs):
        doc = pymupdf.open(pdf_path)
        totals = Counter()
        for page in doc:
            records, counts, drawn = extract_page(page, Path(pdf_path).name, plan_sheets)
            totals += counts
            for record in records:
                earlier = by_id.setdefault(record.id, record)
                if earlier is not record:
                    repeated["same" if earlier.armature == record.armature else "different"] += 1
            if check_dir:
                draw_check(page, drawn)
        report[Path(pdf_path).name] = dict(totals)
        if check_dir and totals["records"]:
            doc.save(Path(check_dir) / f"{Path(pdf_path).stem}_colonnes_check.pdf", garbage=3, deflate=True)
    report["repeated cells"] = dict(repeated)
    return list(by_id.values()), report


def atelier_pdfs(sources):
    """Expand folders into the PDFs they hold."""
    return [pdf for source in sources for pdf in (sorted(Path(source).glob("*.pdf")) if Path(source).is_dir() else [Path(source)])]


def main():
    plan_pdf = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PLAN
    records, report = extract(atelier_pdfs(sys.argv[2:] or [DEFAULT_ATELIER]), plan_pdf)
    for name, counts in report.items():
        print(f"{name}: " + ", ".join(f"{key} {value}" for key, value in counts.items()))
    print("records per plan sheet:", dict(sorted(Counter(r.feuillet for r in records).items())))


if __name__ == "__main__":
    main()
