"""Extract the columns of one project, from its plan and from its shop drawings, into two JSON
files of Appendix A records.

A column has the same `feuillet` and `element` in both files, so the two sides pair up on
those two fields: `feuillet` is the plan sheet of the storey the column stands on, `element`
is the column's name ("A-6").

Usage: python extract_columns.py [plan.pdf] [folder or PDFs of column shop drawings]
Writes, in out/:
    <project>_colonnes_plan.json and <project>_colonnes_atelier.json   the records
    <plan name>_colonnes_check.pdf, <shop drawing name>_colonnes_check.pdf   to check by eye
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

import atelier_columns
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


def write_json(records, path):
    path.write_text(json.dumps([record.model_dump() for record in records], ensure_ascii=False, indent=1), encoding="utf8")


def run(arguments):
    """Extract one project, given as on the command line. Returns the paths of the two JSON files."""
    plan_pdf = Path(arguments[0]) if arguments else plan_columns.DEFAULT_PDF
    pdfs = atelier_columns.atelier_pdfs(arguments[1:] or [atelier_columns.DEFAULT_ATELIER])
    OUT_DIR.mkdir(exist_ok=True)

    plan_records, plan_report, links_by_page = plan_columns.extract(plan_pdf)
    atelier_records, atelier_report = atelier_columns.extract(pdfs, plan_pdf, check_dir=OUT_DIR)
    renamed = reconcile_names([link for links in links_by_page.values() for link in links], atelier_records)
    plan_columns.write_check(plan_pdf, links_by_page, OUT_DIR / f"{plan_pdf.stem}_colonnes_check.pdf")

    project = plan_pdf.stem.split("_")[-1]
    plan_path = OUT_DIR / f"{project}_colonnes_plan.json"
    atelier_path = OUT_DIR / f"{project}_colonnes_atelier.json"
    write_json(plan_records, plan_path)
    write_json(atelier_records, atelier_path)

    plan_keys = Counter((r.feuillet, r.element) for r in plan_records)
    atelier_keys = Counter((r.feuillet, r.element) for r in atelier_records)
    print(f"{'sheet':<8}{'plan':>6}{'atelier':>9}{'paired':>8}{'plan only':>11}{'atelier only':>14}")
    for sheet in sorted({sheet for sheet, _ in plan_keys | atelier_keys}):
        plan = {name for s, name in plan_keys if s == sheet}
        atelier = {name for s, name in atelier_keys if s == sheet}
        print(f"{sheet:<8}{len(plan):>6}{len(atelier):>9}{len(plan & atelier):>8}{len(plan - atelier):>11}{len(atelier - plan):>14}")
    unnamed = sum(1 for r in plan_records if r.element.startswith("?"))
    print(f"{len(plan_records)} plan records ({unnamed} without a name, {renamed} renamed after the shop drawings) -> {plan_path}")
    print(f"{len(atelier_records)} shop-drawing records -> {atelier_path}")
    print(f"check by eye -> {OUT_DIR}/*_colonnes_check.pdf")
    return plan_path, atelier_path


def main():
    run(sys.argv[1:])


if __name__ == "__main__":
    main()
