"""Extract the concrete beams of a project (S-300), from its plan and from its shop drawings.

The plan draws every beam in elevation, with its reinforcement written along it and, under
each span, a title giving the span's name and section:

    3-25M                 a count of bars, top or bottom, where they run
    10M @ 12'' c/c        stirrups, zone by zone
    P101 - 16" x 24"      the title of a span

Spans that follow one another are drawn as one continuous beam. The bars over a support
belong to the spans on both sides of it, so the element is the whole beam, named after its
spans: "P101+P102+P103".

The fabricator draws the same elevations under the same titles and writes one line per bar:

    3 25M 22-06                 quantity, diameter, mark
    12 10M 10MET12X20 @12"      stirrups: quantity, diameter, mark, spacing

Its spans are gathered into the beams of the plan, and the record is filed under the plan
sheet that draws the beam.

This module is used by extract_project.py. Run on its own it only prints counts:
python beams.py plan.pdf [folder or PDFs of beam shop drawings]
"""

import re
import sys
from collections import Counter
from pathlib import Path

import pymupdf

from atelier_details import draw_check, page_lines
from reading import long_strokes, plan_bars, runs, series_pages, share_by_title, shop_bar, text_lines, title_rows
from schema import Element

BEAM_SHEET = r"S-?\s?3\d\d"
TITLE = re.compile(r"([A-Z]{1,2}-?\d{2,4}[A-Z]?)\s*[-:]\s*\d")  # "P101 - 16\" x 24\"", "P-101: 400x600"
JOIN = "+"  # between the names of the spans of one beam


def find_titles(lines):
    """Return (box, name) of every span title among (box, text) lines."""
    return [(box, match.group(1)) for box, text in lines if (match := TITLE.match(text))]


def continuous_beams(page, titles):
    """Group the spans drawn as one continuous beam. Returns lists of title indices, each left to right.

    The faces of a beam are long strokes above its titles: two spans are parts of one beam
    when such strokes run without a break from over one title to over the other.
    """
    strokes = long_strokes(page)
    order = {id(title): index for index, title in enumerate(titles)}
    beams, top = [], 0
    for row in title_rows(titles):
        bottom = min(box.y0 for box, _ in row)
        covered = runs(strokes, top, bottom)
        by_run = {}
        for title in row:
            centre = (title[0].x0 + title[0].x1) / 2
            run = next((run for run in covered if run[0] <= centre <= run[1]), None)
            by_run.setdefault(run or id(title), []).append(order[id(title)])  # no stroke over it: a beam of its own
        beams += list(by_run.values())
        top = max(box.y1 for box, _ in row)
    return beams


def element(feuillet, name, source, pdf_path, page, area, armature):
    centre = (area.tl + area.br) / 2
    return Element(
        id=f"{feuillet}_{name}_{source}", source=source, fichier=Path(pdf_path).name, feuillet=feuillet,
        page=page.number + 1, x=round(centre.x, 1), y=round(centre.y, 1),
        type_element="poutre", element=name, armature=armature,
    )


def extract_plan(pdf_path):
    """Return (records, counts per page) for the beam sheets of a plan: one record per beam."""
    doc = pymupdf.open(pdf_path)
    records, report = [], {}
    for page, feuillet, _ in series_pages(doc, BEAM_SHEET):
        lines = [(box * page.rotation_matrix, text) for box, text, _ in text_lines(page)]
        titles = find_titles(lines)
        shared = share_by_title([line for line in lines if plan_bars(line[1]) and not TITLE.match(line[1])], titles)
        beams = continuous_beams(page, titles)
        for spans in beams:
            armature = [bar for index in spans for _, text in shared.get(index, []) for bar in plan_bars(text)]
            name = JOIN.join(titles[index][1] for index in spans)
            records.append(element(feuillet, name, "plan", pdf_path, page, titles[spans[0]][0], armature))
        report[page.number + 1] = {"spans": len(titles), "beams": len(beams)}
    return records, report


def extract_atelier_page(pdf_path, page, beam_of):
    """Return ({beam name: [box of its first title, box of its lines, bars]}, counts) for one page.

    beam_of gives the beam of the plan each span belongs to; a span the plan does not have
    is a beam of its own.
    """
    lines = page_lines(pdf_path, page)
    titles = find_titles(lines)
    shared = share_by_title([line for line in lines if shop_bar(line[1])], titles)
    found = {}
    for index, (box, name) in enumerate(titles):
        beam = found.setdefault(beam_of.get(name, name), [box, pymupdf.Rect(box), []])
        for line_box, text in shared.get(index, []):
            beam[1] |= line_box
            beam[2].append(shop_bar(text)[1])
    return {name: beam for name, beam in found.items() if beam[2]}, Counter(spans=len(titles))


def extract_atelier(atelier_pdfs, plan_records, check_dir=None):
    """Return the beam records of a project's shop drawings, and counts per file.

    A beam drawn twice keeps its first drawing.
    """
    sheet_of = {record.element: record.feuillet for record in plan_records}
    beam_of = {span: record.element for record in plan_records for span in record.element.split(JOIN)}
    default_sheet = min(sheet_of.values(), default="S-300")
    by_id, report = {}, {}
    for pdf_path in sorted(atelier_pdfs):
        doc = pymupdf.open(pdf_path)
        totals = Counter()
        for page in doc:
            found, counts = extract_atelier_page(pdf_path, page, beam_of)
            totals += counts
            drawn = []
            for name, (box, area, bars) in found.items():
                feuillet = sheet_of.get(name, default_sheet)
                record = element(feuillet, name, "atelier", pdf_path, page, box, bars)
                by_id.setdefault(record.id, record)
                drawn.append((f"{name} {feuillet}", area))
            totals["records"] += len(found)
            if check_dir:
                draw_check(page, drawn)
        report[Path(pdf_path).name] = dict(totals)
        if check_dir and totals["records"]:
            doc.save(Path(check_dir) / f"{Path(pdf_path).stem}_poutres_check.pdf", garbage=3, deflate=True)
    return list(by_id.values()), report


def main():
    from atelier_columns import atelier_pdfs

    plan_records, report = extract_plan(Path(sys.argv[1]))
    for page_number, counts in report.items():
        print(f"plan page {page_number}: " + ", ".join(f"{key} {value}" for key, value in counts.items()))
    records, report = extract_atelier(atelier_pdfs(sys.argv[2:]), plan_records)
    for name, counts in report.items():
        print(f"{name}: " + ", ".join(f"{key} {value}" for key, value in counts.items()))
    plan, atelier = {r.element for r in plan_records}, {r.element for r in records}
    print(f"{len(plan_records)} plan beams, {len(records)} shop beams, {len(plan & atelier)} names on both sides")


if __name__ == "__main__":
    main()
