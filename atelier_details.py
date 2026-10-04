"""Extract the columns of shop drawings that detail one column per block, into Appendix A records.

Most fabricators do not draw the storey table that atelier_columns.py reads. They draw one
detail per column, side by side: the column's name ("C-12", "C/12") as a title, or the names
of all the columns the detail stands for, and around it the bars of that column:

    8 25M 25M3450            vertical bars: quantity, diameter, mark
    3x8 25M 25M3450          the same when the detail stands for 3 columns: 8 bars in each
    14 10M 10MT1 @300        ties: quantity, diameter, mark, spacing
    14 @ 300                 or the tie spacing on a line of its own

Such drawings often have no text layer, so the lines come from OCR when they must. The storey
is not written in a fixed place on the sheet: it is taken from the file name ("...NIV-2@3" is
the lift standing on level 2), and gives the plan sheet the records are filed under.

This module is used by extract_columns.py. Run on its own it only prints counts:
python atelier_details.py plan.pdf [folder or PDFs of column shop drawings]
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

import pymupdf

from plan_columns import OUT_DIR, level_key, sheet_levels
from schema import Armature, Element

OCR_CACHE = OUT_DIR / "ocr_cache"  # OCR takes half a minute a page: each page is read once
MIN_EMBEDDED_WORDS = 20  # fewer than this and the page is treated as having no text layer

NAME = re.compile(r"([A-Z]{1,2}(?:\.\d)?)\s*[-/]\s*(\d{1,2}(?:\.\d)?)")  # "C-12", "C/12", "B.5-7"
SIZES = r"(?:10|15|20|25|30|35|45|55)M"  # the metric bar sizes
BAR = re.compile(rf"(?:(\d+)\s*[xX]\s*)?(\d+?)\s*({SIZES})\b\s*([^\s@]+)?")  # [columns x] quantity diameter [mark]
LOOSE_BAR = re.compile(rf"^({SIZES})\s+([^\s@]+)")  # a tie given without its quantity: "10M 10MT1"
SPACING = re.compile(r"(\d+)?\s*@\s*(\d+(?:\.\d+)?)\s*(\"|”|″|'')?")  # "14 @ 300", "@6''"
AT = re.compile(r"@\s*(\d+(?:\.\d+)?)\s*(\"|”|″|'')?")  # the spacing that ends a bar line: "... @300"


def page_lines(pdf_path, page):
    """Return (box in displayed coordinates, text) for every line of text of a page.

    The embedded text is used when the page has some; otherwise the page is read by OCR,
    once: the reading is kept in out/ocr_cache/ and reused while the PDF does not change.
    """
    if len(page.get_text("words")) >= MIN_EMBEDDED_WORDS:
        lines = []
        for block in page.get_text("dict")["blocks"]:
            for line in block["lines"] if block["type"] == 0 else []:
                text = " ".join("".join(span["text"] for span in line["spans"]).split())
                if text:
                    lines.append((pymupdf.Rect(line["bbox"]) * page.rotation_matrix, text))
        return lines

    stamp = [Path(pdf_path).stat().st_size, int(Path(pdf_path).stat().st_mtime)]
    cache = OCR_CACHE / f"{Path(pdf_path).stem}_p{page.number + 1}.json"
    if cache.exists():
        saved = json.loads(cache.read_text(encoding="utf8"))
        if saved["stamp"] == stamp:
            return [(pymupdf.Rect(box), text) for box, text in saved["lines"]]
    from start import ocr_page_words  # loads the OCR engine: only when a page needs it

    print(f"OCR of {Path(pdf_path).name}, page {page.number + 1} (kept in {OCR_CACHE} for the next run)", flush=True)
    lines = [(pymupdf.Rect(w["x"] - w["w"] / 2, w["y"] - w["h"] / 2, w["x"] + w["w"] / 2, w["y"] + w["h"] / 2), w["text"])
             for w in ocr_page_words(page) if not w.get("doubtful")]
    OCR_CACHE.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"stamp": stamp, "lines": [[list(box), text] for box, text in lines]}, ensure_ascii=False),
                     encoding="utf8")
    return lines


def names_in(text):
    """The column names of a title line, as "C-12" whatever the separator: one name, or several
    with commas between them when the detail stands for several columns. Empty for other lines."""
    parts = [part.strip() for part in text.strip().rstrip(",").split(",")]
    matches = [NAME.fullmatch(part) for part in parts]
    return [f"{match.group(1)}-{match.group(2)}" for match in matches] if all(matches) else []


def find_names(lines):
    """Return (box, names) of every detail's title: the names of the columns it stands for.

    A title can run over several lines, one under the other. Titles stand in rows; on a sheet
    that has rows of them, a lone name between the rows is something else (a note, a line of
    a schedule) and is left out.
    """
    titles = []
    for box, text in sorted(lines, key=lambda item: item[0].y0):
        names = names_in(text)
        if not names:
            continue
        above = next((title for title in titles
                      if box.y0 > title[0].y1 - 0.5 * box.height and box.y0 - title[0].y1 < box.height
                      and min(box.x1, title[0].x1) > max(box.x0, title[0].x0)), None)
        if above:  # the next line of the same title
            above[0] |= box
            above[1] += names
        else:
            titles.append([pymupdf.Rect(box), names])
    titles.sort(key=lambda title: title[0].y0)
    rows = []
    for title in titles:
        if rows and title[0].y0 - rows[-1][-1][0].y0 <= 3 * title[0].height:
            rows[-1].append(title)
        else:
            rows.append([title])
    if any(len(row) >= 3 for row in rows):
        rows = [row for row in rows if len(row) >= 2]
    return [(box, names) for row in rows for box, names in row]


def is_content(text):
    """True if a line carries reinforcement: a bar or a spacing."""
    return bool(BAR.search(text) or LOOSE_BAR.match(text.strip()) or SPACING.search(text))


def share_lines(lines, names):
    """Give every reinforcement line to the column name it belongs to. Returns {name index: lines}.

    A sheet holds rows of details, each as wide as the step between two names. Fabricators
    differ on where the name sits in its detail: above or under it, at its left, middle or
    right. Both are worked out from the page: the side that leaves fewer lines without a name,
    then the sideways position that leaves the most details complete, with both a bar and a
    tie spacing.
    """
    content = [(box, text) for box, text in lines if is_content(text) and not names_in(text)]
    centres = [((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2) for box, _ in names]
    if not content or not names:
        return {}
    row_height = 3 * max(box.height for box, _ in names)  # names of one row are not perfectly aligned

    def row(line_box, under_title):
        """The names of the nearest row above the line (or under it), left to right."""
        y = (line_box.y0 + line_box.y1) / 2
        side = [i for i, (_, ny) in enumerate(centres) if (ny < y) == under_title]
        if not side:
            return []
        edge = max(centres[i][1] for i in side) if under_title else min(centres[i][1] for i in side)
        return sorted((i for i in side if abs(centres[i][1] - edge) <= row_height), key=lambda i: centres[i][0])

    under_title = (sum(not row(box, True) for box, _ in content) <= sum(not row(box, False) for box, _ in content))
    rows = [row(box, under_title) for box, _ in content]
    steps = sorted(centres[b][0] - centres[a][0] for names_of_row in rows for a, b in zip(names_of_row, names_of_row[1:]))
    step = steps[len(steps) // 2] if steps else None

    def share(lead):
        """Share the lines when every detail starts `lead` points left of its name."""
        shared = {}
        for (box, text), names_of_row in zip(content, rows):
            x = (box.x0 + box.x1) / 2
            started = [i for i in names_of_row if centres[i][0] - lead <= x]
            if started and (step is None or x - (centres[started[-1]][0] - lead) < 1.25 * step):
                shared.setdefault(started[-1], []).append((box, text))
        return shared

    def complete(shared):
        return sum(any(BAR.search(text) for _, text in found) and any(SPACING.search(text) for _, text in found)
                   for found in shared.values())

    if step is None:  # one detail per row: everything on its side of the name is its own
        return share(float("inf"))
    leads = sorted((step * eighth / 8 for eighth in range(8)), key=lambda lead: abs(lead - step / 2))  # centred first
    return max((share(lead) for lead in leads), key=complete)


def millimetres(value, inch):
    """A spacing in millimetres: fabricators write inches (with or without the sign) or millimetres."""
    return round(value * 25.4) if inch or value <= 24 else round(value)


def parse_detail(found):
    """Turn the lines of one column detail into armature entries.

    A spacing written on a line of its own ("14@300", or "3@150 + 8@300" for two zones)
    belongs to the nearest tie, which is the thinnest bar of the detail. The first zone
    completes that entry and each further zone repeats it with its own spacing. Dowels
    ("GOUJ ...") keep that word in their mark: the plan asks for them without a count.
    """
    bars, zones = [], []
    for box, text in sorted(found, key=lambda item: (item[0].y0, item[0].x0)):
        bar = BAR.search(text)
        loose = LOOSE_BAR.match(text.strip())
        spacing = SPACING.search(text)
        if bar or loose:
            if bar:
                _, quantity, diameter, mark = bar.groups()  # "3x8": 3 columns of 8 bars, the record is one column
                length = int(mark) if mark and mark.isdigit() and len(mark) >= 3 else None
                entry = Armature(repere=None if length else mark, diametre=diameter,
                                 quantite=int(quantity), espacement_mm=None, longueur_mm=length)
            else:
                entry = Armature(repere=loose.group(2), diametre=loose.group(1), quantite=None,
                                 espacement_mm=None, longueur_mm=None)
            own = AT.search(text, bar.end(3) if bar else loose.end(1))
            if own:
                entry.espacement_mm = millimetres(float(own.group(1)), own.group(2))
            if "GOUJ" in text.upper():
                entry.repere = " ".join(filter(None, ["GOUJ", entry.repere]))
            bars.append((box, entry))
        elif spacing:
            zones += [(box, zone) for zone in SPACING.finditer(text)]

    thinnest = min((int(entry.diametre[:-1]) for _, entry in bars), default=99)
    ties = [(box, entry) for box, entry in bars if int(entry.diametre[:-1]) == thinnest and thinnest <= 15]
    for box, zone in zones:
        if not ties:
            break
        value = millimetres(float(zone.group(2)), zone.group(3))
        count = int(zone.group(1)) if zone.group(1) else None
        tie = min(ties, key=lambda item: abs(item[0].y0 - box.y0) + abs(item[0].x0 - box.x0))[1]
        if tie.espacement_mm is None:
            tie.espacement_mm = value
            tie.quantite = tie.quantite if tie.quantite is not None else count
        else:
            bars.append((box, tie.model_copy(update={"espacement_mm": value, "quantite": count})))
    return [entry for _, entry in bars]


def lift_key(stem):
    """Level key of the storey a drawing's columns stand on, from its file name.

    "WP2_COLONNE-NIV-2@3" -> "N2", "..._COLONNES NIV 10" -> "N10", "...-NIV-SS1@RDC" -> "SS1",
    "...-NIV-FDN@SS2" -> "FDN". None when the name tells no level.
    """
    plain = re.sub(r"[-_]+", " ", stem.upper())
    match = re.search(r"\bNIV\w*\.?\s+(\S+)", plain)
    first = match.group(1).split("@")[0] if match else ""
    if first.isdigit():
        return "N" + first
    if first.startswith("FDN") or first.startswith("FOND"):
        return "FDN"
    return level_key(first) or level_key(plain.split("@")[0])


def extract_page(pdf_path, page, feuillet):
    """Return (records, counts, drawn) for one page of details, filed under the plan sheet `feuillet`.

    drawn holds (name, box of its lines) per record, for the check PDF.
    """
    lines = page_lines(pdf_path, page)
    names = find_names(lines)
    shared = share_lines(lines, names)
    counts = Counter(names=len(names), details=len(shared))
    records, drawn = [], []
    for index, found in sorted(shared.items()):
        armature = parse_detail(found)
        if not armature:
            continue
        area = pymupdf.Rect()
        for box, _ in found:
            area |= box
        centre = (area.tl + area.br) / 2
        for name in names[index][1]:  # a detail drawn once for several columns gives a record to each
            records.append(Element(
                id=f"{feuillet}_{name}_atelier", source="atelier", fichier=Path(pdf_path).name, feuillet=feuillet,
                page=page.number + 1, x=round(centre.x, 1), y=round(centre.y, 1),
                type_element="colonne", element=name, armature=armature,
            ))
        drawn.append((f"{', '.join(names[index][1])} {feuillet}", area | names[index][0]))
    counts["records"] = len(records)
    return records, counts, drawn


def draw_check(page, drawn):
    """Box each detail's name with its reinforcement lines, and write the column and plan sheet above."""
    to_unrotated = page.derotation_matrix
    shape = page.new_shape()
    for label, area in drawn:
        color = (0.9, 0, 0) if "?" in label else (0, 0.35, 0.9)
        shape.draw_rect((area + (-2, -2, 2, 2)) * to_unrotated)
        shape.finish(color=color, width=0.8)
        shape.insert_text(pymupdf.Point(area.x0, area.y0 - 3) * to_unrotated, label,
                          fontsize=7, color=color, rotate=page.rotation)
    shape.commit()


def extract(atelier_pdfs, plan_pdf, check_dir=None):
    """Return the column records of drawings that detail one column per block, and counts per file.

    A column detailed twice in the same lift keeps its first detail. A drawing whose file name
    tells no storey is left out (its sheet is None in the report): its columns could not be
    filed under a plan sheet.
    """
    plan_sheets = sheet_levels(pymupdf.open(plan_pdf))
    lowest = min(plan_sheets.values(), default=None)
    by_id, report = {}, {}
    for pdf_path in sorted(atelier_pdfs):
        key = lift_key(Path(pdf_path).stem)
        if key is None:
            report[Path(pdf_path).name] = {"sheet": None}
            continue
        feuillet = (lowest if key == "FDN" else plan_sheets.get(key)) or f"?{key}"
        doc = pymupdf.open(pdf_path)
        totals = Counter()
        for page in doc:
            records, counts, drawn = extract_page(pdf_path, page, feuillet)
            totals += counts
            for record in records:
                by_id.setdefault(record.id, record)
            if check_dir:
                draw_check(page, drawn)
        report[Path(pdf_path).name] = {"sheet": feuillet, **totals}
        if check_dir and totals["records"]:
            doc.save(Path(check_dir) / f"{Path(pdf_path).stem}_colonnes_check.pdf", garbage=3, deflate=True)
    return list(by_id.values()), report


def main():
    from atelier_columns import atelier_pdfs

    records, report = extract(atelier_pdfs(sys.argv[2:]), Path(sys.argv[1]))
    for name, counts in report.items():
        print(f"{name}: " + ", ".join(f"{key} {value}" for key, value in counts.items()))
    print("records per plan sheet:", dict(sorted(Counter(r.feuillet for r in records).items())))


if __name__ == "__main__":
    main()
