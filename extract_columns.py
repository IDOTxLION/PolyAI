"""Extract the columns of one project, from its plan and from its shop drawings, into two JSON
files of Appendix A records.

A column has the same `feuillet` and `element` in both files, so the two sides pair up on
those two fields: `feuillet` is the plan sheet of the storey the column stands on, `element`
is the column's name ("A-6").

Usage: python extract_columns.py [project folder]
       python extract_columns.py plan.pdf [folder or PDFs of column shop drawings]
A project folder holds the plan PDF at its top and the shop drawings in folders under it.
Writes, in out/:
    <project>_colonnes_plan.json and <project>_colonnes_atelier.json   the records
    <plan name>_colonnes_check.pdf, <shop drawing name>_colonnes_check.pdf   to check by eye
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

import pymupdf

import atelier_columns
import atelier_details
import plan_columns
from plan_columns import OUT_DIR

NAME = re.compile(r"([A-Z])(?:\.(\d))?-(\d{1,2})(?:\.(\d))?")  # "B.4-12.5" -> B, 4, 12, 5
CLOSE = 2  # tenths: how far a name worked out between grid lines may be from the fabricator's


def reconcile_names(plan_links, atelier_records):
    """Give plan columns that stand between grid lines the name the fabricator used.

    The plan does not name its columns. One that stands between two grid lines gets a name
    worked out from its position ("B.4-12"), where the fabricator may have written "B.5-12".
    Such a name is replaced by a shop-drawing name of the same sheet that differs only by a
    couple of tenths, when exactly one fits. Returns the number of names replaced.
    """
    by_sheet = {}
    for record in atelier_records:
        by_sheet.setdefault(record.feuillet, set()).add(record.element)
    plan_names = {(record.feuillet, record.element) for record, *_ in plan_links}

    renamed = 0
    for record, _, _, derived in plan_links:
        mine = NAME.fullmatch(record.element)
        if not derived or mine is None or record.element in by_sheet.get(record.feuillet, ()):
            continue
        close = []
        for name in by_sheet.get(record.feuillet, ()):
            theirs = NAME.fullmatch(name)
            if (theirs and (record.feuillet, name) not in plan_names
                    and theirs.group(1) == mine.group(1) and theirs.group(3) == mine.group(3)
                    and abs(int(theirs.group(2) or 0) - int(mine.group(2) or 0)) <= CLOSE
                    and abs(int(theirs.group(4) or 0) - int(mine.group(4) or 0)) <= CLOSE):
                close.append(name)
        if len(close) == 1:
            plan_names.add((record.feuillet, close[0]))
            record.element = close[0]
            record.id = f"{record.feuillet}_{close[0]}_plan"
            renamed += 1
    return renamed


def refile(atelier_records, plan_records, plan_pdf):
    """File shop-drawing records under the right sheet when a storey is drawn on several.

    A plan can split a storey over two sheets (zones A and B). A shop-drawing record is filed
    under the first of them; it moves to the sheet of the same storey whose plan has that
    column. Returns the number of records moved.
    """
    keys = plan_columns.sheet_keys(pymupdf.open(plan_pdf))
    on_plan = {(record.feuillet, record.element) for record in plan_records}
    taken = {(record.feuillet, record.element) for record in atelier_records}
    moved = 0
    for record in atelier_records:
        if (record.feuillet, record.element) in on_plan:
            continue
        siblings = [sheet for sheet, key in keys.items() if key == keys.get(record.feuillet) and sheet != record.feuillet]
        homes = [sheet for sheet in siblings if (sheet, record.element) in on_plan and (sheet, record.element) not in taken]
        if len(homes) == 1:
            taken.add((homes[0], record.element))
            record.feuillet = homes[0]
            record.id = f"{homes[0]}_{record.element}_atelier"
            moved += 1
    return moved


def write_json(records, path):
    path.write_text(json.dumps([record.model_dump() for record in records], ensure_ascii=False, indent=1), encoding="utf8")


def project_files(arguments):
    """Return (plan PDF, PDFs of the column shop drawings) from the command-line arguments.

    A project folder, or a plan PDF given alone, stands for the whole project: the plan is the
    PDF at the top of the folder, and the column shop drawings are the PDFs of the folders
    named after columns under it, else every PDF named after columns.
    """
    first = Path(arguments[0]) if arguments else plan_columns.DEFAULT_PDF
    if not first.exists():
        sys.exit(f"{first}: no such file or folder")
    if len(arguments) > 1:
        return first, atelier_columns.atelier_pdfs(arguments[1:])
    project = first if first.is_dir() else first.parent
    plans = [first] if first.is_file() else sorted(project.glob("*.pdf"))
    if len(plans) != 1:
        sys.exit(f"{project}: expected one plan PDF at the top of the project folder, found {len(plans)}")
    drawings = sorted(pdf for pdf in project.rglob("*.pdf") if pdf.parent != project)
    columns = [pdf for pdf in drawings if "colon" in pdf.parent.name.lower()]
    return plans[0], columns or [pdf for pdf in drawings if "colon" in pdf.name.lower()]


def extract(plan_pdf, pdfs):
    """Return (plan records, shop-drawing records, names replaced) for the columns of one project.

    Writes the check PDFs in out/.
    """
    plan_records, plan_report, links_by_page = plan_columns.extract(plan_pdf)
    if not plan_records:
        print(f"no column tag found in {plan_pdf}: its S-500 sheets were not recognised")
    # Storey tables first; the drawings that hold none are read as one detail per column.
    atelier_records, atelier_report = atelier_columns.extract(pdfs, plan_pdf, check_dir=OUT_DIR)
    tabled = {name for name, counts in atelier_report.items() if counts.get("records")}
    details, details_report = atelier_details.extract([pdf for pdf in pdfs if pdf.name not in tabled], plan_pdf,
                                                      check_dir=OUT_DIR)
    atelier_records += details
    refile(atelier_records, plan_records, plan_pdf)
    for name, counts in details_report.items():
        if counts["sheet"] is None:
            print(f"{name}: left out, its name does not tell which storey it details")
    renamed = reconcile_names([link for links in links_by_page.values() for link in links], atelier_records)
    plan_columns.write_check(plan_pdf, links_by_page, OUT_DIR / f"{plan_pdf.stem}_colonnes_check.pdf")
    return plan_records, atelier_records, renamed


def print_sheets(plan_records, atelier_records):
    """Print, per plan sheet, how many elements each side has and how many names they share."""
    plan_keys = Counter((r.feuillet, r.element) for r in plan_records)
    atelier_keys = Counter((r.feuillet, r.element) for r in atelier_records)
    print(f"{'sheet':<8}{'plan':>6}{'atelier':>9}{'paired':>8}{'plan only':>11}{'atelier only':>14}")
    for sheet in sorted({sheet for sheet, _ in plan_keys | atelier_keys}):
        plan = {name for s, name in plan_keys if s == sheet}
        atelier = {name for s, name in atelier_keys if s == sheet}
        print(f"{sheet:<8}{len(plan):>6}{len(atelier):>9}{len(plan & atelier):>8}{len(plan - atelier):>11}{len(atelier - plan):>14}")


def run(arguments):
    """Extract the columns of one project, given as on the command line. Returns the paths of the two JSON files."""
    plan_pdf, pdfs = project_files(arguments)
    OUT_DIR.mkdir(exist_ok=True)
    if not pdfs:
        print(f"no column shop drawing found beside {plan_pdf}: give them after the plan on the command line")
    plan_records, atelier_records, renamed = extract(plan_pdf, pdfs)

    project = plan_pdf.stem.split("_")[-1]
    plan_path = OUT_DIR / f"{project}_colonnes_plan.json"
    atelier_path = OUT_DIR / f"{project}_colonnes_atelier.json"
    write_json(plan_records, plan_path)
    write_json(atelier_records, atelier_path)

    print_sheets(plan_records, atelier_records)
    unnamed = sum(1 for r in plan_records if r.element.startswith("?"))
    print(f"{len(plan_records)} plan records ({unnamed} without a name, {renamed} renamed after the shop drawings) -> {plan_path}")
    print(f"{len(atelier_records)} shop-drawing records -> {atelier_path}")
    print(f"check by eye -> {OUT_DIR}/*_colonnes_check.pdf")
    return plan_path, atelier_path


def main():
    run(sys.argv[1:])


if __name__ == "__main__":
    main()
