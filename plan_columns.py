"""Extract the columns of a structural plan into Appendix A records.

On the plan, a column is a black filled shape on a floor plan. A tag box is tied to it by a
leader line and reads, for example:

    COL. 16"x24"
    ARM.: 4-25M +GOUJ.
    LIG.: 10M@6" c/c
    BÉTON: 35MPa / C-1

The tag carries no column name: the column is named after the grid lines it stands on
("A-6" = grid line A and grid line 6), which is also how the shop drawings name it.

This module is used by extract_columns.py, which writes the JSON. Run on its own it only
prints counts: python plan_columns.py [path/to/plan.pdf]
"""

import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

import numpy as np
import pymupdf

from schema import Armature, Element

DEFAULT_PDF = Path("data/CLP/L2C_PLAN_STR_CLP.pdf")
OUT_DIR = Path("out")  # git-ignored: the outputs contain confidential plan content

INCH = r"[\"”″]"
BARS = re.compile(r"(\d+)\s*-\s*(\d{2}M)")  # "4-25M": quantity and diameter
TIES = re.compile(rf"(\d{{2}}M)\s*@\s*(\d+(?:\.\d+)?)\s*({INCH})?")  # '10M@6"': diameter and spacing
SHEET = re.compile(r"S-?\s?\d{3}")
COLUMN_SHEET = re.compile(r"S-?\s?5\d\d")  # the S-500 series holds the columns
GRID_LABEL = re.compile(r"[A-Z](\.\d)?|\d{1,2}(\.\d)?")

ON_GRID = 20  # points: a column this close to a grid line stands on it
MAX_LEADER_HOPS = 4  # a leader line is drawn as a few connected segments
SNAP = 1.5  # points: two line ends this close are the same point
TAG_REACH = 22  # points: a leader ends on the tag's box, which is drawn this close around its text


def level_key(text):
    """Reduce a level name to a short key shared by plans and shop drawings.

    "REZ-DE-CHAUSSÉE" and "RDC" -> "RDC", "NIVEAU 2" and "NIV. 2" -> "N2", "SOUS-SOL" -> "SS",
    "SOUS-SOL S2" and "SS2" -> "SS2", "TOIT ..." -> "TOIT ...". Returns None when the text
    names no storey.
    """
    plain = "".join(c for c in unicodedata.normalize("NFD", text.upper()) if unicodedata.category(c) != "Mn")
    if re.search(r"\bRDC\b|REZ.DE.CHAUSSEE", plain):
        return "RDC"
    match = re.search(r"\bSOUS.?SOL\s*S?(\d*)|\bSS\s*(\d*)\b", plain)
    if match:
        return "SS" + (match.group(1) or match.group(2) or "")
    match = re.search(r"\bNIV(?:EAU)?\.?\s*(\d+)", plain)
    if match:
        return "N" + match.group(1)
    match = re.search(r"\bTOIT\b.*", plain)
    if match:
        return " ".join(match.group().split())
    return None


def page_titles(doc):
    """Return {page index: sheet title}, e.g. "S-501 - PLAN DES COLONNES - RDC".

    The title is the page's label. A plan exported without page labels carries it in its
    bookmarks instead. A bookmark is believed only if its sheet number is written on the page
    it points to; when it points to the wrong page, the title goes to the one page left
    without a title that carries the number.
    """
    titles = {page.number: page.get_label() for page in doc if SHEET.search(page.get_label() or "")}
    if len(titles) == len(doc):
        return titles
    written = {page.number: {word[4].replace(" ", "") for word in page.get_text("words") if SHEET.fullmatch(word[4])}
               for page in doc if page.number not in titles}
    misplaced = []
    for _, title, page_number in doc.get_toc():
        sheet = SHEET.search(title)
        if not sheet:
            continue
        number = sheet.group().replace(" ", "")
        if number in written.get(page_number - 1, ()):
            titles.setdefault(page_number - 1, title)
        else:
            misplaced.append((number, title))
    for number, title in misplaced:
        carriers = [index for index, numbers in written.items() if number in numbers and index not in titles]
        if len(carriers) == 1:
            titles[carriers[0]] = title
    return titles


def sheet_keys(doc):
    """Return {sheet number: level key} for the column sheets (S-5xx) of a plan.

    The level comes from the sheet's title, e.g. "S-501 - PLAN DES COLONNES - SOUS-SOL": that
    sheet shows the columns standing on that level.
    """
    keys = {}
    for _, title in sorted(page_titles(doc).items()):
        sheet = SHEET.search(title)
        key = level_key(SHEET.sub("", title))
        if COLUMN_SHEET.fullmatch(sheet.group()) and key:
            keys.setdefault(sheet.group().replace(" ", ""), key)
    return keys


def sheet_levels(doc):
    """Return {level key: sheet number}: the first column sheet of each level."""
    levels = {}
    for sheet, key in sheet_keys(doc).items():
        levels.setdefault(key, sheet)
    return levels


def color_is(color, wanted):
    return color is not None and all(abs(a - b) < 0.02 for a, b in zip(color, wanted))


def find_tags(page):
    """Return (box, text) of every column tag: a "COL." line with its "ARM.:" line under it.

    A tag is usually one text block. Some plans store every line as a block of its own: the
    lines stacked under each "COL." line are then gathered into its tag.
    """
    tags, lines = [], []
    for block in page.get_text("dict")["blocks"]:
        if block["type"] != 0:
            continue
        text = "\n".join("".join(span["text"] for span in line["spans"]) for line in block["lines"])
        if "ARM.:" in text and "COL." in text:
            tags.append((pymupdf.Rect(block["bbox"]), text))
        lines += [(pymupdf.Rect(line["bbox"]), "".join(span["text"] for span in line["spans"]).strip())
                  for line in block["lines"]]
    return tags or stacked_tags(page, [line for line in lines if line[1]])


def stacked_tags(page, lines):
    """Gather the tags of a page that stores them one line per block."""
    to_displayed = page.rotation_matrix  # stacking is judged on the page as displayed
    shown = [(box * to_displayed, box, text) for box, text in lines]
    tags = []
    for head, head_box, head_text in shown:
        if not head_text.startswith("COL."):
            continue
        box, texts, last = pymupdf.Rect(head_box), [head_text], head
        while len(texts) < 5:
            under = [(s, b, t) for s, b, t in shown
                     if last.y0 + 0.5 * last.height < s.y0 < last.y1 + last.height  # the next line down
                     and min(s.x1, head.x1) - max(s.x0, head.x0) > 0.5 * min(s.width, head.width)
                     and not t.startswith("COL.")]
            if not under:
                break
            last, line_box, text = min(under, key=lambda item: item[0].y0)
            box |= line_box
            texts.append(text)
        if any(text.startswith("ARM") for text in texts):
            tags.append((box, "\n".join(texts)))
    return tags


def parse_tag(text):
    """Turn the text of a tag into armature entries: vertical bars, then ties."""
    armature = []
    for line in text.splitlines():
        if line.startswith("ARM"):
            for quantity, diameter in BARS.findall(line):
                armature.append(Armature(repere=None, diametre=diameter, quantite=int(quantity),
                                         espacement_mm=None, longueur_mm=None))
        elif line.startswith("LIG"):
            for diameter, spacing, inch in TIES.findall(line):
                spacing_mm = float(spacing) * 25.4 if inch else float(spacing)
                armature.append(Armature(repere=None, diametre=diameter, quantite=None,
                                         espacement_mm=round(spacing_mm), longueur_mm=None))
    return armature


def find_columns_and_lines(page):
    """Return the black filled shapes (columns) and the black line segments."""
    columns, black = [], []
    for drawing in page.get_drawings():
        rect = drawing["rect"]
        if (drawing["type"] in ("f", "fs") and color_is(drawing.get("fill"), (0, 0, 0))
                and 3 < rect.width < 60 and 3 < rect.height < 60):
            columns.append(rect)
        if color_is(drawing.get("color"), (0, 0, 0)):
            black += [(item[1], item[2]) for item in drawing["items"] if item[0] == "l"]
    return columns, black


def follow_leaders(columns, tags, black):
    """Match each column to the tag its leader line reaches. Returns {column index: tag index}.

    Starting from the line ends that touch a column, walk along connected segments for a few
    hops; the tag the walk comes closest to is that column's tag.
    """
    def key(point):
        return (round(point.x / SNAP), round(point.y / SNAP))

    neighbours = defaultdict(list)  # snapped point -> the other ends of the segments starting there
    for a, b in black:
        if abs(a - b) < 400:  # a leader is short; long black lines are borders and title blocks
            neighbours[key(a)].append(b)
            neighbours[key(b)].append(a)

    def near_keys(point):
        kx, ky = key(point)
        return [(kx + dx, ky + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]

    def nearest_tag(point):
        """(distance, index) of the tag whose text is closest to the point."""
        best = (float("inf"), None)
        for t, (box, _) in enumerate(tags):
            dx = max(box.x0 - point.x, point.x - box.x1, 0)
            dy = max(box.y0 - point.y, point.y - box.y1, 0)
            best = min(best, ((dx * dx + dy * dy) ** 0.5, t))
        return best

    # Both ends of every segment as one array: testing them against each column one PyMuPDF
    # point at a time took most of the plan extraction time.
    ends = np.array([(a.x, a.y, b.x, b.y) for a, b in black]).reshape(-1, 4)

    def inside(zone, x, y):
        return (x >= zone.x0) & (x < zone.x1) & (y >= zone.y0) & (y < zone.y1)

    matches = {}
    for index, column in enumerate(columns):
        zone = column + (-2, -2, 2, 2)
        a_in, b_in = inside(zone, ends[:, 0], ends[:, 1]), inside(zone, ends[:, 2], ends[:, 3])
        # the far end of each segment that leaves the column
        frontier = [black[i][0] if b_in[i] else black[i][1] for i in np.flatnonzero(a_in != b_in)]
        seen = set()
        best = (float("inf"), None)
        for _ in range(MAX_LEADER_HOPS):
            best = min([best] + [nearest_tag(point) for point in frontier])
            following = []
            for point in frontier:
                for k in near_keys(point):
                    if k not in seen:
                        seen.add(k)
                        following += neighbours.get(k, [])
            frontier = following
        if best[0] <= TAG_REACH:
            matches[index] = best[1]
    return matches


def match_nearest(columns, tags, matches):
    """Pair the columns and tags the leaders left unmatched, closest pairs first."""
    free_columns = [i for i in range(len(columns)) if i not in matches]
    free_tags = [t for t in range(len(tags)) if t not in matches.values()]

    def gap(column, box):
        dx = max(box.x0 - column.x1, column.x0 - box.x1, 0)
        dy = max(box.y0 - column.y1, column.y0 - box.y1, 0)
        return (dx * dx + dy * dy) ** 0.5

    pairs = sorted((gap(columns[i], tags[t][0]), i, t) for i in free_columns for t in free_tags)
    for distance, i, t in pairs:
        if distance < 150 and i not in matches and t not in matches.values():
            matches[i] = t
    return matches


def find_grid(page):
    """Return ({x: label} for vertical grid lines, {y: label} for horizontal ones).

    Each grid line ends in a bubble holding its label, and the bubbles of parallel lines are
    lined up in the margins of the drawing. So the labels of the outermost rows of short
    labels name the vertical lines, at their x, and those of the outermost columns name the
    horizontal lines, at their y. Short labels inside the drawing are something else.
    """
    labels = [((w[0] + w[2]) / 2, (w[1] + w[3]) / 2, w[4]) for w in page.get_text("words") if GRID_LABEL.fullmatch(w[4])]

    axes = []
    for across, along in ((1, 0), (0, 1)):  # rows of bubbles share a y; columns of bubbles share an x
        ordered = sorted(labels, key=lambda label: label[across])
        groups = []
        for label in ordered:
            if groups and label[across] - groups[-1][-1][across] <= 15:
                groups[-1].append(label)
            else:
                groups.append([label])
        # A row of bubbles normally holds many labels; a building with a short axis has few.
        groups = [g for g in groups if len(g) >= 6] or [g for g in groups if len(g) >= 3]
        axes.append(sorted((label[along], label[2]) for group in groups[:1] + groups[-1:] for label in group))

    # One axis is lettered and the other numbered; stray text of the other kind is not a label.
    # When both look alike, the axis with more labels is believed and the other is its opposite.
    lettered = [sum(text[0].isalpha() for _, text in found) > len(found) / 2 for found in axes]
    if lettered[0] == lettered[1]:
        fuller = 0 if len(axes[0]) >= len(axes[1]) else 1
        lettered[1 - fuller] = not lettered[fuller]
    grid = []
    for found, letters in zip(axes, lettered):
        lines = {}
        for position, text in found:  # the bubbles at both ends of a line give it twice
            if text[0].isalpha() == letters and (not lines or position - max(lines) > 8):
                lines[position] = text
        grid.append(lines)
    return grid[0], grid[1]


def label_order(label):
    """Sort key putting grid labels in their natural order: A, B, B.5, C or 1, 2, 2.5, 10."""
    head, _, decimal = label.partition(".")
    return (int(head) if head.isdigit() else ord(head), int(decimal or 0))


def line_name(position, lines):
    """Name the place of a column along one axis: the grid line it stands on, or a decimal
    between two lines ("B.4" = four tenths of the way from B to C), as the fabricator does.

    Returns (name, derived): derived is True when the name was worked out between two lines.
    """
    if not lines:
        return None, False
    nearest = min(lines, key=lambda p: abs(p - position))
    if abs(nearest - position) <= ON_GRID:
        return lines[nearest], False
    before = max((p for p in lines if p < position), default=None)
    after = min((p for p in lines if p > position), default=None)
    if before is None or after is None:
        return None, False
    first, second = sorted((before, after), key=lambda p: label_order(lines[p]))
    tenths = round(10 * (position - first) / (second - first))
    if "." in lines[first] or not 1 <= tenths <= 9:
        return None, False
    return f"{lines[first]}.{tenths}", True


def grid_name(column, vertical, horizontal):
    """Name a column after the two grid lines it stands on, letter first: 'A-6'.

    Returns (name or None, derived).
    """
    center = (column.tl + column.br) / 2
    names = [line_name(center.x, vertical), line_name(center.y, horizontal)]
    if any(name is None for name, _ in names):
        return None, False
    names.sort(key=lambda item: not item[0][0].isalpha())
    return "-".join(name for name, _ in names), any(derived for _, derived in names)


def extract_page(page, fichier, feuillet):
    """Return (records, counts, links) for one page of the plan, the sheet `feuillet`.

    links holds (record, tag box, column box or None, name worked out between grid lines)
    per record, for drawing the check PDF and for reconciling names with the shop drawings.
    """
    tags = find_tags(page)
    columns, black = find_columns_and_lines(page)
    by_leader = follow_leaders(columns, tags, black)
    matches = match_nearest(columns, tags, dict(by_leader))
    vertical, horizontal = find_grid(page)

    records, links, unnamed, derived_names = [], [], 0, 0
    column_of_tag = {t: i for i, t in matches.items()}
    for t, (box, text) in enumerate(tags):
        name, derived = grid_name(columns[column_of_tag[t]], vertical, horizontal) if t in column_of_tag else (None, False)
        derived_names += derived
        if name is None:
            unnamed += 1
            name = f"?{unnamed}"  # kept, so the engineer still sees it, but it cannot be matched by name
        center = ((box.tl + box.br) / 2) * page.rotation_matrix
        records.append(Element(
            id=f"{feuillet}_{name}_plan", source="plan", fichier=fichier, feuillet=feuillet,
            page=page.number + 1, x=round(center.x, 1), y=round(center.y, 1),
            type_element="colonne", element=name, armature=parse_tag(text),
        ))
        links.append((records[-1], box, columns[column_of_tag[t]] if t in column_of_tag else None, derived))
    counts = {
        "tags": len(tags), "columns": len(columns), "by leader": len(by_leader), "by distance": len(matches) - len(by_leader),
        "grid lines": f"{len(vertical)}x{len(horizontal)}", "named between lines": derived_names, "unnamed": unnamed,
        "duplicate names": len(records) - len({r.element for r in records}),
        "no bars": sum(1 for r in records if not any(a.quantite for a in r.armature)),
        "no ties": sum(1 for r in records if not any(a.espacement_mm for a in r.armature)),
    }
    return records, counts, links


def extract(pdf_path):
    """Return (records, counts per page, links per page index) for the column sheets (S-5xx) of a plan."""
    doc = pymupdf.open(pdf_path)
    records, report, links_by_page = [], {}, {}
    titles = page_titles(doc)
    for page in doc:
        sheet = COLUMN_SHEET.search(titles.get(page.number, ""))
        if not sheet:
            continue
        page_records, counts, links = extract_page(page, Path(pdf_path).name, sheet.group().replace(" ", ""))
        if counts["tags"]:
            records += page_records
            report[page.number + 1] = counts
            links_by_page[page.number] = links
    return records, report, links_by_page


def write_check(pdf_path, links_by_page, out_path):
    """Save the column sheets with each column's name beside it and a line to its tag.

    Blue = named from the grid, orange = name worked out between two grid lines, red = no name.
    Nothing is written when the plan has no column sheet.
    """
    if not links_by_page:
        return
    doc = pymupdf.open(pdf_path)
    for page_index, links in links_by_page.items():
        page = doc[page_index]
        to_unrotated = page.derotation_matrix
        shape = page.new_shape()
        for record, tag_box, column, derived in links:
            name = record.element
            color = (0.9, 0, 0) if name.startswith("?") else (0.95, 0.5, 0) if derived else (0, 0.35, 0.9)
            anchor = column if column is not None else tag_box
            if column is not None:
                shape.draw_line(((tag_box.tl + tag_box.br) / 2) * to_unrotated, ((column.tl + column.br) / 2) * to_unrotated)
                shape.finish(color=color, width=0.6)
                shape.draw_rect((column + (-2, -2, 2, 2)) * to_unrotated)
                shape.finish(color=color, width=1)
            shape.insert_text(pymupdf.Point(anchor.x1 + 3, anchor.y0 - 2) * to_unrotated, name,
                              fontsize=9, color=color, rotate=page.rotation)
        shape.commit()
    doc.select(sorted(links_by_page))
    doc.save(out_path, garbage=3, deflate=True)


def main():
    pdf_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PDF
    records, report, _ = extract(pdf_path)
    for page_number, counts in report.items():
        print(f"page {page_number:>3}: " + ", ".join(f"{key} {value}" for key, value in counts.items()))
    print(f"{len(records)} column records")


if __name__ == "__main__":
    main()
