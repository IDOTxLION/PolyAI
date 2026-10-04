"""Extract the top bars over the columns of a project's slabs (S-600), from its plan and from
its shop drawings.

On the plan, the bars to lay over a column are written beside it, along the way they run:

    14(6)        14 bars of the typical diameter, 6 of them in the band at the column
    8(4)-20M     the same with its diameter, when it is not the typical one

The typical diameter is written once on the sheet ("BARRE D'ARMATURE TYPIQUE : 15M"). A
callout is named after the grid crossing nearest to it and the grid line it runs along:
"C-12 axe C".

The fabricator draws the same plan, one drawing per level, and labels the same bars:

    8 15J9-06        quantity, then the mark, which starts with the diameter
    6 15J12-00       a second mark for the same bars, written right under the first
    6 m.b.           how many of them go in the band

The shop drawing is laid over the plan through the grid lines both carry, and each group of
labels takes the name of the callout it is nearest to; equal counts are trusted before
nearness, so a callout is not paired with its neighbour's bars.

Only these callouts are read: the bottom mat (given once per sheet as a minimum) and the
additional bars written "4-20M" are not. A shop drawing without a text layer is not read.

This module is used by extract_project.py. Run on its own it only prints counts:
python slabs.py plan.pdf [folder or PDFs of slab shop drawings]
"""

import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from statistics import median

import pymupdf

import grid
import plan_columns
from atelier_details import MIN_EMBEDDED_WORDS, SIZES, draw_check, lift_key
from reading import series_pages
from schema import Armature, Element

SLAB_SHEET = r"S-?\s?6\d\d"
CALLOUT = re.compile(r"(\d+)\s*\(\s*(\d+)\s*\)\s*(?:-\s*(\d\d)\s*M)?")  # "14(6)", "8 (4)-20M"
TYPICAL = re.compile(rf"TYPIQUE.*?({SIZES})")  # "- BARRE D'ARMATURE TYPIQUE (S.I.C.) : 15M"
LABEL = re.compile(r"(\d+)\s+((10|15|20|25|30|35|45|55)(?:M\s*|[A-Z]{1,3})\d[\w\-]*)")  # "8 15J9-06", "8 15M 12-06"
BAND = re.compile(r"(\d+)\s*m\.?\s*b\.?", re.IGNORECASE)  # "6 m.b."

REACH = 2.0  # grid bays: how far from its callout a group of labels may be written
TOUCHING = 0.6  # line heights: two labels this close across are written one under the other


def oriented_lines(page):
    """Return (box, text, vertical) for every line of embedded text, in the page's own
    coordinates; vertical tells that the text runs up or down the page."""
    lines = []
    for block in page.get_text("dict")["blocks"]:
        for line in block["lines"] if block["type"] == 0 else []:
            text = " ".join("".join(span["text"] for span in line["spans"]).split())
            if text:
                lines.append((pymupdf.Rect(line["bbox"]), text, abs(line["dir"][1]) > abs(line["dir"][0])))
    return lines


def centre(box):
    return (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2


def axis(x, y, vertical_text, vertical, horizontal):
    """The label of the grid line a text written at (x, y) runs along."""
    lines, position = (vertical, x) if vertical_text else (horizontal, y)
    return lines[min(lines, key=lambda p: abs(p - position))]


def bay(vertical, horizontal):
    """The usual distance between two grid lines of a sheet."""
    steps = [b - a for lines in (vertical, horizontal) for a, b in zip(sorted(lines), sorted(lines)[1:])]
    return median(steps) if steps else 100


def bars(quantity, band, diameter, mark=None):
    """The armature of a callout: its bars, then those of them that go in the band."""
    return [Armature(repere=" ".join(filter(None, ["HAUT", mark])), diametre=diameter, quantite=quantity,
                     espacement_mm=None, longueur_mm=None),
            Armature(repere="BANDE", diametre=diameter, quantite=band, espacement_mm=None, longueur_mm=None)]


def extract_plan(pdf_path):
    """Return (records, counts per page, views) for the slab sheets of a plan.

    views[sheet] holds what laying a shop drawing over the sheet needs: its title, its grid
    and, per callout, (record, x, y, the kind of grid line it runs along).
    """
    doc = pymupdf.open(pdf_path)
    records, report, views = [], {}, {}
    for page, feuillet, title in series_pages(doc, SLAB_SHEET):
        lines = oriented_lines(page)
        vertical, horizontal = plan_columns.find_grid(page)
        typical = next((match.group(1) for _, text, _ in lines if (match := TYPICAL.search(text))), None)
        callouts = [(box, match, upright) for box, text, upright in lines if (match := CALLOUT.fullmatch(text))]
        if not callouts or not vertical or not horizontal:
            continue
        taken, found = Counter(), []
        for box, match, upright in sorted(callouts, key=lambda item: (item[0].y0, item[0].x0)):
            x, y = centre(box)
            along = axis(x, y, upright, vertical, horizontal)
            name = f"{grid.crossing(x, y, vertical, horizontal)} axe {along}"
            taken[name] += 1
            if taken[name] > 1:  # bars on both sides of a column, written at the same crossing
                name = f"{name} ({taken[name]})"
            diameter = f"{match.group(3)}M" if match.group(3) else typical
            shown = pymupdf.Point(x, y) * page.rotation_matrix
            records.append(Element(
                id=f"{feuillet}_{name}_plan", source="plan", fichier=Path(pdf_path).name, feuillet=feuillet,
                page=page.number + 1, x=round(shown.x, 1), y=round(shown.y, 1),
                type_element="dalle", element=name, armature=bars(int(match.group(1)), int(match.group(2)), diameter),
            ))
            found.append((records[-1], x, y, along[0].isalpha()))
        views[feuillet] = {"title": title, "vertical": vertical, "horizontal": horizontal, "callouts": found}
        report[page.number + 1] = {"callouts": len(found), "typical diameter": typical}
    return records, report, views


def sheets_of_level(views):
    """Return {level key: sheets} from the titles of the slab sheets.

    "ARMATURE DU NIVEAU 9 @ 21" is the sheet of every level from 9 to 21.
    """
    levels = {}
    for sheet, view in views.items():
        title = plan_columns.SHEET.sub("", view["title"], count=1)
        span = re.search(r"NIVEAU\s+(\d+)\s*@\s*(\d+)", title)
        keys = [f"N{n}" for n in range(int(span.group(1)), int(span.group(2)) + 1)] if span else [plan_columns.level_key(title)]
        for key in keys:
            levels.setdefault(key, []).append(sheet)
    return levels


def words(text):
    """The words of a title or of a file name that tell one sheet from another: no accents, no plural."""
    plain = "".join(c for c in unicodedata.normalize("NFD", text.upper()) if unicodedata.category(c) != "Mn")
    return {word.rstrip("S") for word in re.findall(r"[A-Z]{4,}", plain)}


def sheets_of_drawing(stem, views):
    """The plan sheets a slab drawing may detail, from its file name, likeliest first.

    The level in the name gives them ("DALLE NIV 4"); a name without a level is tried on the
    sheets whose title shares a word with it ("TRÉFOND"). A level can be drawn on several
    sheets: the page itself then tells which one it fits (see extract_atelier_page).
    """
    name = re.sub(r"[-_]+", " ", stem.upper())
    key = lift_key(re.sub(r"\bNIV(\d)", r"NIV \1", name)) or plan_columns.level_key(name)
    sheets = sheets_of_level(views).get(key, []) if key else [sheet for sheet, view in views.items()
                                                               if words(name) & words(view["title"])]
    # the title that says least beyond the name comes first: "REZ-DE-CHAUSSÉE" before "REZ-DE-CHAUSSÉE TRÉFOND"
    return sorted(sheets, key=lambda sheet: (-len(words(name) & words(views[sheet]["title"])), len(words(views[sheet]["title"])), sheet))


def touching(a, b, upright):
    """True if two lines of text running the same way are written one right under the other."""
    height = min(a.width, a.height, b.width, b.height)
    across = min(abs(a.x0 - b.x1), abs(b.x0 - a.x1)) if upright else min(abs(a.y0 - b.y1), abs(b.y0 - a.y1))
    overlap = min(a.y1, b.y1) - max(a.y0, b.y0) if upright else min(a.x1, b.x1) - max(a.x0, b.x0)
    return across <= TOUCHING * height and overlap > 0


def label_groups(lines):
    """Return the groups of top bars of a page: (box, vertical, label, labels of its pile, band count).

    A band count belongs to the label nearest to it; only the labels that have one answer a
    callout of the plan. A label holds (quantity, diameter, mark). The labels written right
    over or under it form its pile: they are either a second mark for the same bars or the
    bars of the neighbouring callout, which the drawing alone does not tell (see lay_over).
    """
    labels = [(box, upright, (int(match.group(1)), f"{match.group(3)}M", match.group(2).replace(" ", "")))
              for box, text, upright in lines if (match := LABEL.fullmatch(text))]
    groups = []
    for box, text, upright in lines:
        band = BAND.fullmatch(text)
        if not band:
            continue
        x, y = centre(box)
        near = [(abs(centre(label[0])[0] - x) + abs(centre(label[0])[1] - y), i) for i, label in enumerate(labels) if label[1] == upright]
        if not near or min(near)[0] >= 5 * min(box.width, box.height):
            continue
        pile, grew = [min(near)[1]], True
        while grew:
            grew = False
            for i, label in enumerate(labels):
                if i not in pile and label[1] == upright and any(touching(label[0], labels[j][0], upright) for j in pile):
                    pile.append(i)
                    grew = True
        area = pymupdf.Rect(box)
        for i in pile:
            area |= labels[i][0]
        groups.append((area, upright, labels[pile[0]][2], [labels[i][2] for i in pile[1:]], int(band.group(1))))
    return groups


def pair_up(callouts, groups, reach):
    """Pair each callout with a group of labels. Returns {group index: callout index}.

    callouts hold (x, y, lettered, quantity, band) and groups (x, y, lettered, the quantities
    the group may stand for, band), all in the plan's coordinates. Pairs that agree on both
    counts are made first, nearest first: they are sure. What is left is paired by nearness
    alone, and is where the discrepancies are.
    """
    def distance(callout, group):
        return ((callout[0] - group[0]) ** 2 + (callout[1] - group[1]) ** 2) ** 0.5

    near = sorted((distance(callout, group), i, j) for i, callout in enumerate(callouts) for j, group in enumerate(groups)
                  if callout[2] == group[2] and distance(callout, group) <= reach)
    paired, taken = {}, set()
    for agreeing in (True, False):
        for _, i, j in near:
            if i in taken or j in paired or (agreeing and not agree(callouts[i], groups[j])):
                continue
            paired[j] = i
            taken.add(i)
    return paired


def agree(callout, group):
    return callout[3] in group[3] and callout[4] == group[4]


def lay_over(groups, vertical, horizontal, view, taken):
    """Lay the groups of a shop page over a plan sheet. Returns (placed, pairs, score) or None
    when the page does not carry two grid lines of the sheet each way.

    placed holds (x, y, lettered, quantities, band, line it runs along) per group, in the
    sheet's coordinates: the quantities are that of the label alone and that of its pile.
    pairs gives {group index: callout index}; the score counts the pairs that agree on both
    counts.
    """
    to_x, to_y = grid.mapping(vertical, view["vertical"]), grid.mapping(horizontal, view["horizontal"])
    if to_x is None or to_y is None:
        return None
    placed = []
    for area, upright, label, pile, band in groups:
        x, y = to_x(centre(area)[0]), to_y(centre(area)[1])
        along = axis(x, y, upright, view["vertical"], view["horizontal"])
        placed.append((x, y, along[0].isalpha(), (label[0], label[0] + sum(other[0] for other in pile)), band, along))
    callouts = [(x, y, lettered, record.armature[0].quantite, record.armature[1].quantite)
                for record, x, y, lettered in view["callouts"]]
    free = [i for i in range(len(callouts)) if i not in taken]
    paired = pair_up([callouts[i] for i in free], [group[:5] for group in placed],
                     REACH * bay(view["vertical"], view["horizontal"]))
    pairs = {j: free[i] for j, i in paired.items()}
    return placed, pairs, sum(agree(callouts[i], placed[j]) for j, i in pairs.items())


def extract_atelier_page(pdf_path, page, sheets, views, taken):
    """Return (records, counts, drawn) for one page of a slab shop drawing.

    The page is laid over each of the plan sheets it may detail and filed under the one it
    fits best. taken[sheet] holds the callouts earlier pages have already answered.

    A label is read alone when its own quantity is the callout's, and with its pile
    otherwise: a pile that is two callouts' bars is thus not counted twice, and one that
    agrees with the plan neither way is flagged with all its labels.
    """
    counts = Counter()
    if len(page.get_text("words")) < MIN_EMBEDDED_WORDS:
        # A slab sheet is the densest of all: OCR misreads one number in ten there, and takes
        # half a minute a page on over a hundred pages. It is not attempted.
        counts["pages without a text layer"] += 1
        return [], counts, []
    lines = oriented_lines(page)
    to_shown = page.rotation_matrix
    groups = label_groups(lines)
    counts["groups of top bars"] = len(groups)
    if not groups:
        return [], counts, []
    vertical, horizontal = grid.find_grid([(box, text) for box, text, _ in lines])
    fits = {sheet: fit for sheet in sheets if (fit := lay_over(groups, vertical, horizontal, views[sheet], taken[sheet]))}
    if not fits:
        counts["pages without the plan's grid"] += 1
        return [], counts, []
    feuillet = max(fits, key=lambda sheet: fits[sheet][2])  # the first of the likeliest when equal
    placed, pairs, _ = fits[feuillet]
    view = views[feuillet]

    records, drawn = [], []
    for j, (area, upright, label, pile, band) in enumerate(groups):
        labels = [label] + pile
        if j in pairs:
            taken[feuillet].add(pairs[j])
            callout = view["callouts"][pairs[j]][0]
            name = callout.element
            if callout.armature[0].quantite == label[0]:
                labels = [label]
        else:  # bars the plan does not ask for there
            x, y, _, _, _, along = placed[j]
            name = f"{grid.crossing(x, y, view['vertical'], view['horizontal'])} axe {along} (atelier {page.number + 1}.{j + 1})"
            counts["groups without a callout"] += 1
        armature = [bars(quantity, band, diameter, mark)[0] for quantity, diameter, mark in labels]
        armature.append(bars(0, band, label[1])[1])
        shown = pymupdf.Point(*centre(area)) * to_shown
        records.append(Element(
            id=f"{feuillet}_{name}_atelier", source="atelier", fichier=Path(pdf_path).name, feuillet=feuillet,
            page=page.number + 1, x=round(shown.x, 1), y=round(shown.y, 1),
            type_element="dalle", element=name, armature=armature,
        ))
        drawn.append((f"{name} {feuillet}", area * to_shown))
    counts["records"] = len(records)
    return records, counts, drawn


def extract_atelier(atelier_pdfs, views, check_dir=None):
    """Return the slab records of a project's shop drawings, and counts per file.

    A drawing whose file name tells no level of the plan is left out (its sheets are empty
    in the report).
    """
    records, report = [], {}
    taken = {sheet: set() for sheet in views}
    for pdf_path in sorted(atelier_pdfs):
        sheets = sheets_of_drawing(Path(pdf_path).stem, views)
        if not sheets:
            report[Path(pdf_path).name] = {"sheets": []}
            continue
        doc = pymupdf.open(pdf_path)
        totals, filed = Counter(), set()
        for page in doc:
            found, counts, drawn = extract_atelier_page(pdf_path, page, sheets, views, taken)
            totals += counts
            records += found
            filed |= {record.feuillet for record in found}
            if check_dir:
                draw_check(page, drawn)
        report[Path(pdf_path).name] = {"sheets": sorted(filed) or sheets, **totals}
        if check_dir and totals["records"]:
            doc.save(Path(check_dir) / f"{Path(pdf_path).stem}_dalles_check.pdf", garbage=3, deflate=True)
    return records, report


def main():
    from atelier_columns import atelier_pdfs

    plan_records, report, views = extract_plan(Path(sys.argv[1]))
    for page_number, counts in report.items():
        print(f"plan page {page_number}: " + ", ".join(f"{key} {value}" for key, value in counts.items()))
    records, report = extract_atelier(atelier_pdfs(sys.argv[2:]), views)
    for name, counts in report.items():
        print(f"{name}: " + ", ".join(f"{key} {value}" for key, value in counts.items()))
    plan, atelier = {(r.feuillet, r.element) for r in plan_records}, {(r.feuillet, r.element) for r in records}
    print(f"{len(plan_records)} plan callouts, {len(records)} shop groups, {len(plan & atelier)} paired")


if __name__ == "__main__":
    main()
