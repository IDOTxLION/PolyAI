"""Helpers shared by the readers of foundations, beams, shear walls and slabs.

The engineer and the fabricators each write reinforcement in a few fixed ways, whatever the
element:

    4-25M                      plan: a count of bars of one diameter
    10M@6" c/c, 15M @300 c/c   plan: bars of one diameter at a spacing
    LONG: 9 20M 20M9-06        shop drawing: [keyword:] quantity, diameter, mark
    14 15M 12-06 @12"c.c.      shop drawing: the same with a spacing

and they lay a sheet out as views (a beam elevation, the plan of a raft), each with its title
under it. This module reads those forms and tells which view a line belongs to.
"""

import re

import pymupdf

from atelier_details import SIZES, millimetres
from plan_columns import SHEET, page_titles
from schema import Armature

INCH = r"\"|”|″|''|’’"
COUNTED = re.compile(rf"(?<![\d.@])(\d+)\s*-\s*({SIZES})\b")  # "4-25M", "9 - 25M"
SPACED = re.compile(rf"({SIZES})\s*@\s*(\d+(?:\.\d+)?)\s*({INCH}|mm)?")  # '10M@6"', "15M @300", "15M@300mm"
AT = re.compile(rf"@\s*(\d+(?:\.\d+)?)(?:\s+(\d+)/(\d+))?\s*({INCH}|mm)?")  # '@6"', '@7 1/2"', "@300"
SHOP_BAR = re.compile(  # "LONG: 9 20M 20M9-06", "ÉTRI:2x8 10M 10MT16X24 @6\"", "14 15M9-06"
    rf"^(?:(?P<key>[A-ZÀ-Ý][A-ZÀ-Ý\-]*)\s*[:.]\s*)?(?:(?P<sets>\d+)\s*[xX]\s*)?(?P<quantity>\d+)\s+(?P<diameter>{SIZES})\s*(?P<mark>[^\s@]*)\s*(?P<rest>.*)$")

ROW_GAP = 40  # points: titles within this height of each other stand in the same row of views


def series_pages(doc, pattern):
    """Return [(page, sheet number, title)] for the plan sheets whose title matches the pattern."""
    pages = []
    for index, title in sorted(page_titles(doc).items()):
        sheet = SHEET.search(title)
        if sheet and re.search(pattern, title, re.IGNORECASE):
            # "S-600A": a letter after the number tells sheets of one level apart
            number = re.match(r"S-?\s?\d{3}[A-Z]?(?![A-Za-zÀ-ÿ])", title[sheet.start():])
            pages.append((doc[index], (number or sheet).group().replace(" ", ""), title))
    return pages


def text_lines(page):
    """Return (box in the page's own coordinates, text, font size) for every line of embedded text.

    The grid and the drawings of a page are read in the page's own coordinates, before its
    rotation: so are these boxes. Multiply by page.rotation_matrix for the page as displayed.
    """
    lines = []
    for block in page.get_text("dict")["blocks"]:
        for line in block["lines"] if block["type"] == 0 else []:
            text = " ".join("".join(span["text"] for span in line["spans"]).split())
            if text:
                lines.append((pymupdf.Rect(line["bbox"]), text, max(span["size"] for span in line["spans"])))
    return lines


def spacing_mm(value, unit):
    """A spacing in millimetres, from its number and its unit (an inch sign, "mm" or nothing)."""
    return round(float(value)) if unit == "mm" else millimetres(float(value), unit)


def plan_bars(text, role=None):
    """Turn a callout of the plan into armature entries: every "4-25M" and every '10M@6"' in it."""
    spaced = [Armature(repere=role, diametre=diameter, quantite=None, espacement_mm=spacing_mm(value, unit), longueur_mm=None)
              for diameter, value, unit in SPACED.findall(text)]
    counted = [Armature(repere=role, diametre=diameter, quantite=int(quantity), espacement_mm=None, longueur_mm=None)
               for quantity, diameter in COUNTED.findall(text)]
    return counted + spaced


def shop_bar(text):
    """Turn one bar line of a shop drawing into (keyword or None, armature entry), or None.

    The keyword ("LONG", "GOUJ"...) is kept in front of the mark, so that the bars of a
    footing or of a raft can be compared role by role: "LONG 20M9-06".
    """
    match = SHOP_BAR.match(text.strip())
    if not match:
        return None
    key, mark, rest = match.group("key"), match.group("mark").rstrip(".,"), match.group("rest")
    at = AT.search(rest)
    spacing = None
    if at:
        value = float(at.group(1)) + (int(at.group(2)) / int(at.group(3)) if at.group(2) else 0)
        spacing = spacing_mm(value, at.group(4))
    return key, Armature(repere=" ".join(filter(None, [key, mark])) or None, diametre=match.group("diameter"),
                         quantite=int(match.group("quantity")), espacement_mm=spacing, longueur_mm=None)


def title_rows(titles):
    """Group titles (box, name) into rows of views, top to bottom; each row left to right."""
    rows = []
    for title in sorted(titles, key=lambda title: title[0].y0):
        if rows and title[0].y0 - rows[-1][-1][0].y0 <= ROW_GAP:
            rows[-1].append(title)
        else:
            rows.append([title])
    return [sorted(row, key=lambda title: title[0].x0) for row in rows]


def share_by_title(lines, titles):
    """Give every line to the view it is drawn in. Returns {index of the title: lines}.

    Views stand in rows, each centred over its title: a line belongs to the nearest row of
    titles under it. Views are not equally wide, so two neighbours are told apart by the
    widest empty stretch between their titles: the lines left of it are the left view's.
    Pass only the lines that matter (bars), so that dimensions do not fill that stretch. A
    line under the last row of titles belongs to no view.
    """
    order = {id(title): index for index, title in enumerate(titles)}
    shared = {}
    for row, found in rows_of_lines(lines, titles):
        centres = [(title[0].x0 + title[0].x1) / 2 for title in row]
        xs = sorted((box.x0 + box.x1) / 2 for box, _ in found)
        limits = []
        for left, right in zip(centres, centres[1:]):
            between = [left] + [x for x in xs if left < x < right] + [right]
            gap, start = max((b - a, a) for a, b in zip(between, between[1:]))
            limits.append(start + gap / 2)
        for box, text in found:
            view = sum((box.x0 + box.x1) / 2 > limit for limit in limits)
            shared.setdefault(order[id(row[view])], []).append((box, text))
    return shared


def share_by_title_under(lines, titles):
    """Give every line to the nearest title under it. Returns {index of the title: lines}.

    For sheets whose views do not stand in rows: a line is in the view whose title is the
    nearest one below it, counting how far it is sideways from the title's ends.
    """
    shared = {}
    for box, text in lines:
        x, y = (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2
        under = [((max(title.x0 - x, x - title.x1, 0) ** 2 + (title.y0 - y) ** 2) ** 0.5, index)
                 for index, (title, _) in enumerate(titles) if title.y0 > y]
        if under:
            shared.setdefault(min(under)[1], []).append((box, text))
    return shared


def rows_of_lines(lines, titles):
    """Return [(row of titles, the lines between that row and the one above it)], top to bottom."""
    rows = title_rows(titles)
    found = [[] for _ in rows]
    for box, text in lines:
        y = (box.y0 + box.y1) / 2
        row = next((index for index, row in enumerate(rows) if min(title[0].y0 for title in row) > y), None)
        if row is not None:
            found[row].append((box, text))
    return list(zip(rows, found))


def long_strokes(page, shortest=60, longest=0.8):
    """Return (x0, x1, y) of the long horizontal strokes of a page as displayed.

    The outline of a beam or of a wall is drawn with them. Strokes longer than `longest`
    times the page width are the sheet's frame and are left out.
    """
    m = page.rotation_matrix

    def shown(point):
        return point[0] * m.a + point[1] * m.c + m.e, point[0] * m.b + point[1] * m.d + m.f

    strokes = []
    for drawing in page.get_cdrawings():
        for item in drawing["items"]:
            if item[0] != "l":
                continue
            (ax, ay), (bx, by) = shown(item[1]), shown(item[2])
            if abs(ay - by) < 0.5 and shortest <= abs(ax - bx) <= longest * page.rect.width:
                strokes.append((min(ax, bx), max(ax, bx), ay))
    return strokes


def runs(strokes, top, bottom, slack=6):
    """Merge the strokes between two heights into the stretches they cover sideways: [(x0, x1)]."""
    merged = []
    for x0, x1, _ in sorted(stroke for stroke in strokes if top <= stroke[2] < bottom):
        if merged and x0 <= merged[-1][1] + slack:
            merged[-1][1] = max(merged[-1][1], x1)
        else:
            merged.append([x0, x1])
    return [tuple(run) for run in merged]


def stacks(lines, gap=1.3, indent=8):
    """Group lines written one under the other, left edges aligned, into blocks of text.

    Returns lists of (box, text), each top to bottom. A fabricator's note about one element
    (its title, then one line per bar) is such a block even when the PDF stores every line
    on its own.
    """
    blocks = []
    for box, text in sorted(lines, key=lambda line: (line[0].y0, line[0].x0)):
        above = [block for block in blocks
                 if abs(block[-1][0].x0 - box.x0) <= indent and -0.5 * box.height <= box.y0 - block[-1][0].y1 <= gap * box.height]
        if above:
            min(above, key=lambda block: abs(block[-1][0].x0 - box.x0)).append((box, text))
        else:
            blocks.append([(box, text)])
    return blocks
