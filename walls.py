"""Extract the shear walls of a plan (S-400).

The plan draws every wall in elevation, storey by storey, with its title under it
("ÉLÉVATION" beside the wall's letter, or "ÉLÉVATION - MUR B - ..."). At each end of a wall
and at each storey, a tag gives the concentration of reinforcement there, written like a
column's:

    CA. 16x24
    ARM.: 8-25M
    LIG.: 10M@6" c/c

and the bars of the wall itself are written in each storey: "H.:15M@12\\"" and "V.:15M@12\\"".

A concentration is named after its wall, the storey it stands on and its place along the
wall, left to right: "MUR B RDC C2". The bars of the wall are one element per storey:
"MUR B RDC".

Only the plan is read. The fabricators draw the walls in ways too different to be read by
one rule (as columns of a storey table, as zones, on the foundation drawings), so every wall
of the plan is reported missing from the shop drawings, in one sentence per sheet.

This module is used by extract_project.py. Run on its own it only prints counts:
python walls.py plan.pdf
"""

import re
import sys
from collections import Counter
from pathlib import Path

import pymupdf

from plan_columns import level_key
from reading import plan_bars, series_pages, share_by_title_under, stacks, text_lines
from schema import Element

WALL_SHEET = r"S-?\s?4\d\d"
TITLE = re.compile(r"[ÉE]L[ÉE]VATION\b(?:.*?\bMUR\s+(\w+))?")  # "ÉLÉVATION", "ÉLÉVATION - MUR B - S.S2 @ NIV. 10"
TAG = re.compile(r"ARM\.?\s*:")  # the line of a concentration's tag that gives its vertical bars
WEB = re.compile(r"([HV])\.?\s*:\s*(.+)")  # "H.:15M@12\"", "V.:15M @300 c/c"
LETTER_REACH = 160  # points: the wall's letter is written this close to its title, on its left


def find_titles(lines):
    """Return (box, wall name) of the elevation titles among (box, text, font size) lines.

    A title is written larger than the notes that mention an elevation. The wall's name is
    in the title, or is the large letter written beside it; else the title's rank is used.
    """
    found = [(box, TITLE.match(text), size) for box, text, size in lines if TITLE.match(text)]
    largest = max((size for _, _, size in found), default=0)
    titles = []
    for box, match, size in sorted(found, key=lambda item: (round(item[0].y0 / 40), item[0].x0)):
        if size < 0.9 * largest:
            continue
        beside = [(box.x0 - other.x1, text) for other, text, other_size in lines
                  if re.fullmatch(r"[A-Z]\d?|\d{1,2}", text) and other_size > size
                  and 0 < box.x0 - other.x1 < LETTER_REACH and abs(other.y0 - box.y0) < 3 * box.height]
        titles.append((box, match.group(1) or (min(beside)[1] if beside else str(len(titles) + 1))))
    return titles


def storey_under(box, levels):
    """The level key of the floor a line stands on: the nearest storey label under it, or
    "FDN" under the lowest one, where the wall stands on its foundation."""
    under = [(y - box.y0, key) for y, key in levels if y > box.y0]
    return min(under)[1] if under else "FDN"


def extract_plan(pdf_path):
    """Return (records, counts per page) for the shear-wall sheets of a plan."""
    doc = pymupdf.open(pdf_path)
    records, report = [], {}
    for page, feuillet, _ in series_pages(doc, WALL_SHEET):
        lines = [(box * page.rotation_matrix, text, size) for box, text, size in text_lines(page)]
        titles = find_titles(lines)
        levels = [(box.y0, level_key(text)) for box, text, _ in lines if level_key(text) and len(text) < 30]
        plain = [(box, text) for box, text, _ in lines]
        tags = [block for block in stacks(plain) if any(TAG.match(text) for _, text in block)]
        heads = [(block[0][0], index) for index, block in enumerate(tags)]
        webs = [(box, text) for box, text in plain if WEB.fullmatch(text) and plan_bars(text)]
        of_tag, of_web = share_by_title_under(heads, titles), share_by_title_under(webs, titles)
        counts = Counter(walls=len(titles))

        def add(name, box, armature):
            if not armature:  # "ARM. & LIG.: VOIR ÉLÉV. A": given on another elevation
                counts["given elsewhere"] += 1
                return
            centre = (box.tl + box.br) / 2
            records.append(Element(
                id=f"{feuillet}_{name}_plan", source="plan", fichier=Path(pdf_path).name, feuillet=feuillet,
                page=page.number + 1, x=round(centre.x, 1), y=round(centre.y, 1),
                type_element="mur", element=name, armature=armature,
            ))

        for index, (_, wall) in enumerate(titles):
            by_storey = {}
            for box, tag in of_tag.get(index, []):
                by_storey.setdefault(storey_under(box, levels), []).append((box, tag))
            for storey, found in by_storey.items():
                for rank, (box, tag) in enumerate(sorted(found, key=lambda item: item[0].x0), 1):
                    armature = [bar for _, text in tags[tag] for bar in plan_bars(text)]
                    add(f"MUR {wall} {storey} C{rank}", box, armature)
                    counts["concentrations"] += 1
            by_storey = {}
            for box, text in of_web.get(index, []):
                way = "HOR" if WEB.fullmatch(text).group(1) == "H" else "VERT"
                by_storey.setdefault(storey_under(box, levels), []).append((box, plan_bars(text, way)))
            for storey, found in by_storey.items():
                unique = {(bar.repere, bar.diametre, bar.espacement_mm): bar for _, bars in found for bar in bars}
                add(f"MUR {wall} {storey}", found[0][0], [unique[key] for key in sorted(unique, key=str)])
                counts["storeys of wall"] += 1
        report[page.number + 1] = dict(counts)
    # the same wall drawn on two sheets, or two tags given one name, keep their first record
    first = {}
    for record in records:
        first.setdefault(record.id, record)
    return list(first.values()), report


def main():
    records, report = extract_plan(Path(sys.argv[1]))
    for page_number, counts in report.items():
        print(f"plan page {page_number}: " + ", ".join(f"{key} {value}" for key, value in counts.items()))
    print(f"{len(records)} wall records")


if __name__ == "__main__":
    main()
