"""Extract the footings of a project (S-100), from its plan and from its shop drawings.

On the plan, a footing is drawn under its column with the letter of its type beside it, and a
schedule on the same sheet gives the reinforcement of every type:

    TYPE     LONGUEUR   LARGEUR   ÉPAISSEUR   ARM. LONG.   ARM. TRANS.
    TYPE A   10' - 0"   10' - 0"  2' - 0"     12-20M       12-20M

The footing is named after the grid lines its column stands on ("A-6"), like the column.

The fabricator writes a note beside each footing:

    EMPATTEMENT TYPE-A:
    LONG: 12 20M 20M9-06         bars along the footing: quantity, diameter, mark
    TRAN: 12 20M 20M9-06         bars across it
    COLONNE A-6, 16X24:          the column it carries: the footing's name
    GOUJ: 4 25M 25ML3X04         dowels of the column, listed but not compared

Bars keep their role in front of their mark ("LONG", "TRAN"), on the plan and on the shop
drawing, so they are compared role by role.

A raft is drawn in plan on a sheet of its own, its title under it ("PLAN FONDATION - RADIER
#2"), with its mats written zone by zone: 'RANG 1: 20M@12" c/c' on the plan (layers 1 and 2
are the bottom mat, 3 and 4 the top one), 'LONG: 14 20M 20MA12-06 @12"BAS' on the shop
drawing. A raft is one element, "RADIER 2": the spacings of each mat are compared as a set.

This module is used by extract_project.py. Run on its own it only prints counts:
python foundations.py plan.pdf [folder or PDFs of foundation shop drawings]
"""

import re
import sys
from collections import Counter
from pathlib import Path

import pymupdf

import plan_columns
from atelier_details import MIN_EMBEDDED_WORDS, NAME, page_lines
from reading import SPACED, plan_bars, share_by_title_under, shop_bar, stacks, text_lines
from schema import Element

TYPE_ROW = re.compile(r"TYPE\s+([A-Z]\d?)")  # a row of the schedule: "TYPE A"
HEADERS = {"LONG": re.compile(r"ARM\.?\s*LONG"), "TRAN": re.compile(r"ARM\.?\s*TRANS")}
COLUMN_HEAD = re.compile(r"COL(?:ONNES?)?\.?\s+(.+)")  # "COLONNE A-6, 16X24:", "COLONNES A-6, A-7, 16X24:"
FOOTING_ROLES = ("LONG", "TRAN")
RAFT = re.compile(r"RADIER\s*-?\s*#\s*(\d+)")  # "PLAN FONDATION - RADIER #2", "RADIER - #2"
LAYER = re.compile(r"RANG\s*(\d)")  # "RANG 1: 20M@12\" c/c"
MAT = re.compile(r"\b(HAUT|BAS)\b")  # the mat a shop line belongs to
TITLE_HEIGHT = 1.3  # a view's title is written this much taller than the text of the page

COLUMN_REACH = 150  # points: a type letter this far from every column stands by a footing without one
MIN_COLUMN = 8  # points: smaller black shapes are arrowheads and dots, not columns
MARGIN = 60  # points: footings of the outer grid lines reach this far beyond them


def find_schedule(lines):
    """Return {type letter: armature} from the footing schedule, read on the page as displayed.

    A cell belongs to the column of the header it is written under and to the row of the
    "TYPE X" label at its height; a cell can hold two lines ("+ ÉP. 15M@300").
    """
    heads = {role: next((box for box, text in lines if pattern.match(text)), None) for role, pattern in HEADERS.items()}
    if None in heads.values():
        return {}
    centres = {role: (box.x0 + box.x1) / 2 for role, box in heads.items()}
    reach = abs(centres["TRAN"] - centres["LONG"]) / 2
    top = min(box.y0 for box in heads.values())
    rows = sorted(((box, TYPE_ROW.fullmatch(text).group(1)) for box, text in lines
                   if TYPE_ROW.fullmatch(text) and box.x1 < min(centres.values()) and 0 < box.y0 - top < 40 * box.height),
                  key=lambda row: row[0].y0)
    if not rows:
        return {}
    pitch = min([b[0].y0 - a[0].y0 for a, b in zip(rows, rows[1:])], default=2 * rows[0][0].height)
    schedule = {}
    for index, (label, letter) in enumerate(rows):
        bottom = rows[index + 1][0].y0 if index + 1 < len(rows) else label.y0 + pitch
        armature = []
        for role, centre in centres.items():
            cells = [(box, text) for box, text in lines
                     if label.y0 - 2 <= (box.y0 + box.y1) / 2 < bottom - 2 and abs((box.x0 + box.x1) / 2 - centre) < reach]
            for _, text in sorted(cells, key=lambda cell: cell[0].y0):
                armature += plan_bars(text, role)
        schedule[letter] = armature
    return schedule


def offset(point, column):
    return (column.tl + column.br) / 2 - point


def letter_side(points, columns):
    """On which side of its column a sheet writes a footing's type letter: the signs of the
    step from a letter to its column, for most letters. A footing's letter is written at the
    same corner all over a sheet, which tells its column from a neighbour's that is nearer."""
    sides = Counter()
    for point in points:
        nearest = min(columns, key=lambda column: abs(offset(point, column)), default=None)
        if nearest is not None and abs(offset(point, nearest)) <= COLUMN_REACH:
            sides[(offset(point, nearest).x > 0, offset(point, nearest).y > 0)] += 1
    return sides.most_common(1)[0][0] if sides else None


def column_of(point, columns, side):
    """The column of the footing whose type letter is written at `point`, or None."""
    near = [column for column in columns if abs(offset(point, column)) <= COLUMN_REACH]
    same_side = [column for column in near if (offset(point, column).x > 0, offset(point, column).y > 0) == side]
    return min(same_side or near, key=lambda column: abs(offset(point, column)), default=None)


def extract_plan_page(page, fichier, feuillet):
    """Return (records, counts, links) for the footings of one plan page.

    links holds (record, box of the type letter, box of the column or None) for the check PDF.
    """
    lines = text_lines(page)
    schedule = find_schedule([(box * page.rotation_matrix, text) for box, text, _ in lines])
    vertical, horizontal = plan_columns.find_grid(page)
    counts = Counter(types=len(schedule))
    if not schedule or not vertical or not horizontal:
        return [], counts, []

    def inside(box):
        return (min(vertical) - MARGIN < box.x0 < max(vertical) + MARGIN
                and min(horizontal) - MARGIN < box.y0 < max(horizontal) + MARGIN)

    letters = [(box, text, size) for box, text, size in lines if text in schedule and inside(box)]
    sizes = Counter(round(size, 1) for _, _, size in letters)
    letters = [(box, text) for box, text, size in letters if round(size, 1) == sizes.most_common(1)[0][0]]  # not the grid's
    columns, _ = plan_columns.find_columns_and_lines(page)
    columns = [column for column in columns if min(column.width, column.height) >= MIN_COLUMN]
    side = letter_side([(box.tl + box.br) / 2 for box, _ in letters], columns)

    records, links, taken = [], [], set()
    for box, letter in sorted(letters, key=lambda item: (item[0].y0, item[0].x0)):
        centre = (box.tl + box.br) / 2
        column = column_of(centre, columns, side)
        name, derived = plan_columns.grid_name(column if column is not None else box, vertical, horizontal)
        counts["named after the column"] += column is not None and name is not None and not derived
        counts["named between grid lines"] += name is not None and derived
        if name is None or name in taken:
            counts["unnamed" if name is None else "second footing of a column"] += 1
            name = f"?{len(records) + 1}"  # kept, so the engineer still sees it, but it cannot be matched by name
        taken.add(name)
        shown = centre * page.rotation_matrix
        records.append(Element(
            id=f"{feuillet}_{name}_plan", source="plan", fichier=fichier, feuillet=feuillet,
            page=page.number + 1, x=round(shown.x, 1), y=round(shown.y, 1),
            type_element="fondation", element=name, armature=[bar.model_copy() for bar in schedule[letter]],
        ))
        links.append((records[-1], box, column))
    counts["footings"] = len(records)
    return records, counts, links


def raft_views(lines):
    """Return (box, raft number) of the titles of the raft views among (box, text, height) lines.

    The title of a view is written larger than the notes that also name the raft.
    """
    named = [(box, RAFT.search(text).group(1), height) for box, text, height in lines if RAFT.search(text)]
    tallest = max((height for _, _, height in named), default=0)
    usual = sorted(height for _, _, height in lines)[len(lines) // 2] if lines else 0
    return [(box, number) for box, number, height in named if height >= 0.8 * tallest and height >= TITLE_HEIGHT * usual]


def mats(bars):
    """One armature entry per mat, diameter and spacing: a raft repeats them zone after zone."""
    unique = {(bar.repere, bar.diametre, bar.espacement_mm): bar for bar in bars}
    return [unique[key] for key in sorted(unique, key=str)]


def raft(feuillet, number, source, fichier, page, centre, armature):
    name = f"RADIER {number}"
    return Element(
        id=f"{feuillet}_{name}_{source}", source=source, fichier=fichier, feuillet=feuillet,
        page=page.number + 1, x=round(centre.x, 1), y=round(centre.y, 1),
        type_element="fondation", element=name, armature=armature,
    )


def extract_plan_rafts(page, fichier, feuillet):
    """Return the raft records of one plan page: one per view, with the mats written in it."""
    lines = [(box * page.rotation_matrix, text, size) for box, text, size in text_lines(page)]
    views = raft_views(lines)
    callouts = [(box, text) for box, text, _ in lines if SPACED.search(text) and not RAFT.search(text)]
    shared = share_by_title_under(callouts, views)
    records = []
    for index, (box, number) in enumerate(views):
        bars = []
        for _, text in shared.get(index, []):
            layer = LAYER.search(text)
            role = None if not layer else "BAS" if layer.group(1) in "12" else "HAUT"
            bars += [bar for bar in plan_bars(text, role) if bar.espacement_mm is not None]
        if bars:
            records.append(raft(feuillet, number, "plan", fichier, page, (box.tl + box.br) / 2, mats(bars)))
    return records


def extract_atelier_rafts(pdf_path, page, sheet_of):
    """Return (records, drawn) for the rafts of one page of a foundation shop drawing.

    sheet_of gives the plan sheet of each raft number. Only the mats are read (the LONG and
    TRAN lines that give a spacing and say HAUT or BAS): dowels and chairs are not on the plan.
    """
    lines = page_lines(pdf_path, page)
    views = [view for view in raft_views([(box, text, box.height) for box, text in lines]) if view[1] in sheet_of]
    found = [(box, text) for box, text in lines
             if MAT.search(text) and (bar := shop_bar(text)) and bar[0] in FOOTING_ROLES and bar[1].espacement_mm]
    shared = share_by_title_under(found, views)
    records, drawn = [], []
    for index, (box, number) in enumerate(views):
        bars = []
        area = pymupdf.Rect(box)
        for line_box, text in shared.get(index, []):
            _, bar = shop_bar(text)
            bar.repere = MAT.search(text).group(1)  # the mat is the role; a raft has too many marks to list
            bar.quantite = None
            bars.append(bar)
            area |= line_box
        if bars:
            records.append(raft(sheet_of[number], number, "atelier", Path(pdf_path).name, page, (box.tl + box.br) / 2, mats(bars)))
            drawn.append((f"RADIER {number} {sheet_of[number]}", area))
    return records, drawn


def extract_plan(pdf_path):
    """Return (records, counts per page, links per page index) for the footings of a plan.

    The footings are on the sheet that carries their schedule, whatever its number.
    """
    doc = pymupdf.open(pdf_path)
    titles = plan_columns.page_titles(doc)
    records, report, links_by_page = [], {}, {}
    for page in doc:
        sheet = plan_columns.SHEET.search(titles.get(page.number, ""))
        if not sheet:
            continue
        feuillet, text = sheet.group().replace(" ", ""), page.get_text()
        if "ARM. LONG" in text:
            page_records, counts, links = extract_plan_page(page, Path(pdf_path).name, feuillet)
            if page_records:
                records += page_records
                report[page.number + 1] = dict(counts)
                links_by_page[page.number] = links
        if RAFT.search(text) and not plan_columns.COLUMN_SHEET.fullmatch(sheet.group()):
            rafts = [record for record in extract_plan_rafts(page, Path(pdf_path).name, feuillet)
                     if record.element not in {earlier.element for earlier in records}]
            records += rafts
            if rafts:
                report.setdefault(page.number + 1, {})["rafts"] = len(rafts)
    return records, report, links_by_page


def extract_atelier_page(pdf_path, page, feuillet):
    """Return (records, counts, drawn) for one page of a foundation shop drawing.

    A footing is a block of text that names its column and gives bars along and across. A
    block written for several columns gives a record to each. drawn holds (label, box).
    """
    lines = page_lines(pdf_path, page)
    counts = Counter()
    records, drawn = [], []
    for block in stacks(lines):
        names = [f"{letter}-{number}" for _, text in block if (head := COLUMN_HEAD.match(text))
                 for letter, number in NAME.findall(head.group(1))]
        bars = [bar for _, text in block if (bar := shop_bar(text))]
        if not any(key in FOOTING_ROLES for key, _ in bars):
            continue
        if not names:
            counts["footings without a column"] += 1  # a strip footing, defined by a section and not by the schedule
            continue
        area = pymupdf.Rect()
        for box, _ in block:
            area |= box
        centre = (area.tl + area.br) / 2
        for name in dict.fromkeys(names):
            records.append(Element(
                id=f"{feuillet}_{name}_atelier", source="atelier", fichier=Path(pdf_path).name, feuillet=feuillet,
                page=page.number + 1, x=round(centre.x, 1), y=round(centre.y, 1),
                type_element="fondation", element=name, armature=[bar.model_copy() for _, bar in bars],
            ))
        drawn.append((f"{', '.join(dict.fromkeys(names))} {feuillet}", area))
    counts["records"] = len(records)
    return records, counts, drawn


def extract_atelier(atelier_pdfs, plan_records, check_dir=None):
    """Return the foundation records of a project's shop drawings, and counts per file.

    Footings are filed under the plan sheet of the footings, rafts under the sheet that draws
    them. A footing or a raft detailed twice keeps its first drawing.
    """
    from atelier_details import draw_check

    footings = [record for record in plan_records if not record.element.startswith("RADIER")]
    feuillet = footings[0].feuillet if footings else "S-100"
    raft_sheets = {record.element.split()[1]: record.feuillet for record in plan_records if record.element.startswith("RADIER")}
    by_id, report = {}, {}
    for pdf_path in sorted(atelier_pdfs):
        doc = pymupdf.open(pdf_path)
        totals = Counter()
        for page in doc:
            if len(page.get_text("words")) < MIN_EMBEDDED_WORDS:
                # the notes read here are written by the fabricators whose drawings carry their
                # text: a page that would need OCR (minutes without a GPU) gives nothing
                totals["pages without a text layer"] += 1
                continue
            records, counts, drawn = extract_atelier_page(pdf_path, page, feuillet)
            rafts, drawn_rafts = extract_atelier_rafts(pdf_path, page, raft_sheets)
            records, drawn = records + rafts, drawn + drawn_rafts
            counts["records"] = len(records)
            counts["rafts"] = len(rafts)
            totals += counts
            for record in records:
                by_id.setdefault(record.id, record)
            if check_dir:
                draw_check(page, drawn)
        report[Path(pdf_path).name] = dict(totals)
        if check_dir and totals["records"]:
            doc.save(Path(check_dir) / f"{Path(pdf_path).stem}_fondations_check.pdf", garbage=3, deflate=True)
    return list(by_id.values()), report


def main():
    from atelier_columns import atelier_pdfs

    plan_records, report, _ = extract_plan(Path(sys.argv[1]))
    for page_number, counts in report.items():
        print(f"plan page {page_number}: " + ", ".join(f"{key} {value}" for key, value in counts.items()))
    records, report = extract_atelier(atelier_pdfs(sys.argv[2:]), plan_records)
    for name, counts in report.items():
        print(f"{name}: " + ", ".join(f"{key} {value}" for key, value in counts.items()))
    plan, atelier = {r.element for r in plan_records}, {r.element for r in records}
    print(f"{len(plan_records)} plan foundations, {len(records)} on the shop drawings, {len(plan & atelier)} names on both sides")


if __name__ == "__main__":
    main()
