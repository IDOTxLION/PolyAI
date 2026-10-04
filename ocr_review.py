"""Check the OCR by eye on pages that have no embedded text, and score it from the corrections.

Usage: python ocr_review.py path/to/file.pdf [page numbers, default 1]
       python ocr_review.py score

The first form reads each page with OCR and writes three files in out/review/:
  <pdf name>_p<page>_review.pdf        the drawing with a numbered box on every reading and what
                                       was read printed above it (blue = holds a digit, grey =
                                       does not), under a lettered grid
  <pdf name>_p<page>_readings.json     the readings and their numbers, kept so that a second run
                                       redraws the same numbers instead of reading the page again
  <pdf name>_p<page>_corrections.txt   to fill in by hand: the grid cells checked, and one line
                                       per mistake found in them

The second form scores every filled-in corrections file: per page, the numbers written on the
drawing in the checked cells, and the share of them read, misread or left undetected. A sheet
is too dense to check whole, hence the grid: only the checked cells count, so a reading nobody
looked at never passes as right.

Like ocr_check.py this only measures: the corrections never feed back into what OCR outputs.
"""

import json
import re
import string
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

import pymupdf

from start import OUT_DIR, ocr_page_words
from ocr_check import norm

REVIEW_DIR = OUT_DIR / "review"
CELL = 216  # points (3 in): the size aimed at for a grid cell, a few minutes of checking
NUMERIC, PLAIN, GRID = (0, 0.35, 0.9), (0.55, 0.55, 0.55), (0.8, 0, 0.6)

CORRECTION = re.compile(r"#?(\d+)\s*=\s*(.*)")  # "317 = 4 25M": box 317 should read "4 25M"
MISSED = re.compile(r"missed\s+([A-Z]\d+)\s*:\s*(.+)", re.IGNORECASE)  # "missed B4: 12 20M"

TEMPLATE = """\
# {fichier}, page {page}: OCR check by eye. Open {review} beside this file.
# Every box has a number and, after it, what OCR read. Blue boxes hold a digit, grey ones do not.
# Each grid cell shows its name and the box numbers it holds, for example "B4  #212-231".
#
# 1. After "reviewed:", list the cells you checked from end to end (for example B4 B5 C4), or: all
reviewed:
#
# 2. Below, one line per mistake found in those cells. Boxes that are right need no line.
#      example: 317 = 4 25M 25M12-06   box #317 is misread: the whole text in that box is 4 25M 25M12-06
#      example: 318 =                  box #318 sits on something that is not text
#      example: missed B4: 12 20M      the drawing says 12 20M in cell B4 and no box is on it
#    Only numbers are scored: a grey box needs a line only when the drawing has a number under it.
"""


def has_digit(text):
    return re.search(r"\d", text) is not None


def cell_names(info):
    """The names of the grid cells of a page, row by row: column letter, then row number."""
    return [f"{letter}{row}" for row in range(1, info["rows"] + 1) for letter in string.ascii_uppercase[:info["columns"]]]


def cell_of(x, y, info):
    """Name of the grid cell holding a point of the displayed page."""
    column = min(max(int(x / info["width"] * info["columns"]), 0), info["columns"] - 1)
    row = min(max(int(y / info["height"] * info["rows"]), 0), info["rows"] - 1)
    return f"{string.ascii_uppercase[column]}{row + 1}"


def read_page(page, fichier):
    """OCR one page and number its readings cell by cell, so that the numbers of a cell follow on."""
    width, height = page.rect.width, page.rect.height
    info = {"fichier": fichier, "page": page.number + 1, "width": width, "height": height,
            "columns": min(26, max(1, round(width / CELL))), "rows": max(1, round(height / CELL))}
    order = {name: index for index, name in enumerate(cell_names(info))}
    readings = ocr_page_words(page)
    for reading in readings:
        reading["cell"] = cell_of(reading["x"], reading["y"], info)
    readings.sort(key=lambda r: (order[r["cell"]], r["y"], r["x"]))
    info["readings"] = [{"id": number, **reading} for number, reading in enumerate(readings, 1)]
    return info


def draw_review(page, info):
    """Box every reading with '#number text' above it, and draw the grid with its cell labels."""
    to_unrotated = page.derotation_matrix  # drawing happens in unrotated coordinates
    shape = page.new_shape()  # one shape for the page: committing each box separately is very slow
    numbers = {}  # cell -> [first number, last number]
    for reading in info["readings"]:
        numbers.setdefault(reading["cell"], [reading["id"], reading["id"]])[1] = reading["id"]
        box = pymupdf.Rect(reading["x"] - reading["w"] / 2, reading["y"] - reading["h"] / 2,
                           reading["x"] + reading["w"] / 2, reading["y"] + reading["h"] / 2)
        color = NUMERIC if has_digit(reading["text"]) else PLAIN
        shape.draw_rect(box * to_unrotated)
        shape.finish(color=color, width=0.4)
        letter_height = reading["w"] if reading.get("vertical") else reading["h"]
        shape.insert_text(pymupdf.Point(box.x0, box.y0 - 1) * to_unrotated, f"#{reading['id']} {reading['text']}",
                          fontsize=max(4, min(letter_height * 0.7, 9)), color=color, rotate=page.rotation)

    width, height = info["width"], info["height"]
    step_x, step_y = width / info["columns"], height / info["rows"]
    for column in range(1, info["columns"]):
        shape.draw_line(pymupdf.Point(column * step_x, 0) * to_unrotated, pymupdf.Point(column * step_x, height) * to_unrotated)
    for row in range(1, info["rows"]):
        shape.draw_line(pymupdf.Point(0, row * step_y) * to_unrotated, pymupdf.Point(width, row * step_y) * to_unrotated)
    shape.finish(color=GRID, width=0.3)
    for name in cell_names(info):
        corner = pymupdf.Point(string.ascii_uppercase.index(name[0]) * step_x, (int(name[1:]) - 1) * step_y)
        first, last = numbers.get(name, (None, None))
        label = f"{name}  no box" if first is None else f"{name}  #{first}" if first == last else f"{name}  #{first}-{last}"
        shape.insert_text((corner + (3, 11)) * to_unrotated, label, fontsize=10, color=GRID, rotate=page.rotation)
    shape.commit()


def make_sheets(pdf_path, page_numbers):
    """Write the review PDF, the readings and an empty corrections file for each page."""
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(pdf_path)
    for number in page_numbers:
        page = doc[number - 1]
        stem = f"{pdf_path.stem}_p{number}"
        readings_path = REVIEW_DIR / f"{stem}_readings.json"
        if readings_path.exists():  # corrections refer to these numbers: never read the page again
            info = json.loads(readings_path.read_text(encoding="utf8"))
        else:
            info = read_page(page, pdf_path.name)
            readings_path.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf8")

        draw_review(page, info)
        review_path = REVIEW_DIR / f"{stem}_review.pdf"
        single = pymupdf.open()
        single.insert_pdf(doc, from_page=number - 1, to_page=number - 1)
        single.save(review_path, garbage=3, deflate=True)

        corrections_path = REVIEW_DIR / f"{stem}_corrections.txt"
        if not corrections_path.exists():  # never overwrite what was filled in by hand
            corrections_path.write_text(TEMPLATE.format(fichier=pdf_path.name, page=number, review=review_path.name),
                                        encoding="utf8")
        numeric = sum(1 for reading in info["readings"] if has_digit(reading["text"]))
        print(f"page {number}: {len(info['readings'])} readings ({numeric} with a digit), "
              f"{info['columns']}x{info['rows']} cells -> {review_path}")
    print(f"fill in {REVIEW_DIR}/*_corrections.txt, then: python ocr_review.py score")


def numbers_in(text):
    """The words of a text that hold a digit."""
    return [token for token in text.split() if has_digit(norm(token))]


def count_numbers(truth, read):
    """Count the numbers of a box's true text that OCR read whole, and those it did not.

    Both texts are reduced as in ocr_check.py (no spaces, no stray punctuation) and lined up
    on the parts they share: a number is read when it lies inside one shared part.
    """
    tokens = [norm(token) for token in truth.split()]
    shared = SequenceMatcher(None, "".join(tokens), norm(read), autojunk=False).get_matching_blocks()
    counts, start = Counter(), 0
    for token in tokens:
        end = start + len(token)
        if has_digit(token):
            whole = any(part.a <= start and end <= part.a + part.size for part in shared)
            counts["read" if whole else "misread"] += 1
        start = end
    return counts


def parse_corrections(path, info):
    """Read a filled-in corrections file.

    Returns (cells checked, {box number: true text}, [(cell, text with no box on it)]).
    """
    cells = set(cell_names(info))
    reviewed, corrections, missed = set(), {}, []
    for line_number, line in enumerate(path.read_text(encoding="utf8").splitlines(), 1):
        line = line.strip()
        correction, miss = CORRECTION.fullmatch(line), MISSED.fullmatch(line)
        if line.lower().startswith("reviewed:"):
            names = line[len("reviewed:"):].replace(",", " ").upper().split()
            reviewed |= cells if "ALL" in names else set(names)
        elif correction:
            corrections[int(correction.group(1))] = correction.group(2)
        elif miss:
            missed.append((miss.group(1).upper(), miss.group(2)))
        elif line and not line.startswith("#"):
            sys.exit(f"{path}: line {line_number} is not understood")

    unknown = (reviewed | {cell for cell, _ in missed}) - cells
    if unknown:
        sys.exit(f"{path}: this page has no grid cell {', '.join(sorted(unknown))}")
    unknown = sorted(set(corrections) - {reading["id"] for reading in info["readings"]})
    if unknown:
        sys.exit(f"{path}: this page has no box {', '.join(f'#{number}' for number in unknown)}")
    return reviewed, corrections, missed


def score_page(path):
    """Return (cells checked, counts) for one corrections file; counts is None if none is checked.

    A number counts as read, misread (a box is on it but says something else) or undetected
    (no box). "not text" counts the numbers OCR read where the drawing has no text.
    """
    info = json.loads(path.with_name(path.name.replace("_corrections.txt", "_readings.json")).read_text(encoding="utf8"))
    reviewed, corrections, missed = parse_corrections(path, info)
    if not reviewed:
        return reviewed, None

    counts, outside = Counter(), set()
    for reading in info["readings"]:
        if reading["cell"] not in reviewed:
            if reading["id"] in corrections:
                outside.add(reading["cell"])
            continue
        truth = corrections.get(reading["id"], reading["text"])
        counts += count_numbers(truth, reading["text"])
        if not truth.strip():
            counts["not text"] += len(numbers_in(reading["text"]))
    for cell, text in missed:
        if cell in reviewed:
            counts["undetected"] += len(numbers_in(text))
        else:
            outside.add(cell)
    if outside:  # counting only the mistakes of a cell would make the page look worse than it is
        print(f"  {path.name}: lines about {', '.join(sorted(outside))} left out, "
              f"as those cells are not listed after 'reviewed:'")
    return reviewed, counts


def score():
    """Print how the numbers of the checked cells were read: per page, per project and overall."""
    pages = []
    for path in sorted(REVIEW_DIR.glob("*_corrections.txt")):
        name = path.name.removesuffix("_corrections.txt")
        reviewed, counts = score_page(path)
        if counts is None:
            print(f"  {name}: no cell listed after 'reviewed:' yet, left out")
        else:
            pages.append((f"{name} ({len(reviewed)} cells)", name.split("_")[0], counts))
    if not pages:
        sys.exit(f"nothing to score: no filled-in corrections file in {REVIEW_DIR}")

    def row(label, counts):
        total = counts["read"] + counts["misread"] + counts["undetected"]
        shares = "".join(f"{counts[key] / total:>12.1%}" if total else f"{'-':>12}" for key in ("read", "misread", "undetected"))
        print(f"{label:<52}{total:>8}{shares}{counts['not text']:>10}")

    print(f"{'':<52}{'numbers':>8}{'read':>12}{'misread':>12}{'undetected':>12}{'not text':>10}")
    for label, _, counts in pages:
        row(label, counts)
    projects = sorted({project for _, project, _ in pages})
    for project in projects if len(pages) > 1 else []:
        row(f"{project}, all pages", sum((counts for _, p, counts in pages if p == project), Counter()))
    if len(projects) > 1:
        row("all projects", sum((counts for _, _, counts in pages), Counter()))


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    if sys.argv[1] == "score":
        score()
    else:
        make_sheets(Path(sys.argv[1]), [int(number) for number in sys.argv[2:]] or [1])


if __name__ == "__main__":
    main()
