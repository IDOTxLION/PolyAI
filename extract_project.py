"""Extract every element of one project, from its plan and from its shop drawings, into two
JSON files of Appendix A records: foundations, beams, shear walls, columns and slabs.

An element has the same `feuillet` and `element` in both files, so the two sides pair up on
those two fields: `feuillet` is the plan sheet that specifies the element, `element` is its
name (a column or a footing after its grid lines, a beam after its spans...).

Usage: python extract_project.py [project folder]
       python extract_project.py plan.pdf [folders or PDFs of shop drawings]
A project folder holds the plan PDF at its top and the shop drawings in folders under it,
one folder per kind of element. Writes, in out/:
    <project>_plan.json and <project>_atelier.json    the records
    *_check.pdf                                       the sources with what was read drawn on top
"""

import sys
import unicodedata
from pathlib import Path

import pymupdf

import beams
import extract_columns
import foundations
import plan_columns
import slabs
import walls
from atelier_details import MIN_EMBEDDED_WORDS, OCR_CACHE
from plan_columns import OUT_DIR

SECONDS_PER_PAGE = 35  # OCR of one sheet on a GPU

# The words of a folder or file name that tell which element its shop drawings detail.
KINDS = {
    "colonne": ("colon",),
    "fondation": ("fondation", "semelle", "radier", "empattement"),
    "poutre": ("poutre",),
    "mur": ("mur", "refend", "cisail", "contrevent"),
    "dalle": ("dalle",),
}


def plain(text):
    """Lower case and without accents, to look for a word in a name."""
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


def kind_of(pdf):
    """The kind of element a shop drawing details, from its folder's name, else from its own.

    The folder is believed first: "CONCENTRATION COLONNES" filed under the shear walls is a
    wall drawing. Among the words of a file name, the first one decides. None when no name
    tells.
    """
    for name in (plain(pdf.parent.name), plain(pdf.stem)):
        found = [(name.index(word), kind) for kind, words in KINDS.items() for word in words if word in name]
        if found:
            return min(found)[1]
    return None


def project_files(arguments):
    """Return (plan PDF, {kind of element: PDFs of its shop drawings}) from the command-line arguments.

    A project folder, or a plan PDF given alone, stands for the whole project: the plan is the
    PDF at the top of the folder and the shop drawings are the PDFs of the folders under it.
    Drawings given after the plan whose name tells no kind are taken as column drawings.
    """
    first = Path(arguments[0]) if arguments else plan_columns.DEFAULT_PDF.parent
    if not first.exists():
        sys.exit(f"{first}: no such file or folder")
    if len(arguments) > 1:
        drawings = [pdf for source in map(Path, arguments[1:]) for pdf in (sorted(source.rglob("*.pdf")) if source.is_dir() else [source])]
        plan, fallback = first, "colonne"
    else:
        project = first if first.is_dir() else first.parent
        plans = [first] if first.is_file() else sorted(project.glob("*.pdf"))
        if len(plans) != 1:
            sys.exit(f"{project}: expected one plan PDF at the top of the project folder, found {len(plans)}")
        plan, drawings, fallback = plans[0], sorted(pdf for pdf in project.rglob("*.pdf") if pdf.parent != project), None
    by_kind = {kind: [] for kind in KINDS}
    for pdf in drawings:
        kind = kind_of(pdf) or fallback
        if kind:
            by_kind[kind].append(pdf)
        else:
            print(f"{pdf.name}: left out, neither its folder nor its name tells which element it details")
    return plan, by_kind


def extract_foundations(plan_pdf, pdfs):
    plan_records, _, _ = foundations.extract_plan(plan_pdf)
    atelier_records, report = foundations.extract_atelier(pdfs, plan_records, check_dir=OUT_DIR)
    unread = sum(counts.get("pages without a text layer", 0) for counts in report.values())
    if unread:
        print(f"{unread} pages of foundation shop drawings have no text layer and are not read")
    return plan_records, atelier_records


def extract_beams(plan_pdf, pdfs):
    plan_records, _ = beams.extract_plan(plan_pdf)
    atelier_records, _ = beams.extract_atelier(pdfs, plan_records, check_dir=OUT_DIR)
    return plan_records, atelier_records


def extract_the_columns(plan_pdf, pdfs):
    plan_records, atelier_records, _ = extract_columns.extract(plan_pdf, pdfs)
    return plan_records, atelier_records


def extract_walls(plan_pdf, pdfs):
    plan_records, _ = walls.extract_plan(plan_pdf)
    if pdfs:
        print(f"{len(pdfs)} shear-wall shop drawings are not read: the walls of the plan are reported missing")
    return plan_records, []


def extract_slabs(plan_pdf, pdfs):
    plan_records, _, views = slabs.extract_plan(plan_pdf)
    atelier_records, report = slabs.extract_atelier(pdfs, views, check_dir=OUT_DIR)
    for name, counts in report.items():
        if not counts["sheets"]:
            print(f"{name}: left out, its name does not tell which level of the plan it details")
    unread = sum(counts.get("pages without a text layer", 0) for counts in report.values())
    if unread:
        print(f"{unread} pages of slab shop drawings have no text layer and are not read")
    return plan_records, atelier_records


def announce_ocr(by_kind):
    """Say how many pages will be read by OCR, and how long that takes, before it starts."""
    pages = [(pdf, page.number + 1) for kind in ("colonne", "poutre") for pdf in by_kind[kind] for page in pymupdf.open(pdf)
             if len(page.get_text("words")) < MIN_EMBEDDED_WORDS]
    new = [1 for pdf, number in pages if not (OCR_CACHE / f"{pdf.stem}_p{number}.json").exists()]
    if new:
        print(f"{len(new)} pages have no text layer and are read by OCR: about {SECONDS_PER_PAGE} s a page with a GPU "
              f"(CUDA build of PyTorch), so about {len(new) * SECONDS_PER_PAGE // 60 + 1} min; many minutes a page without one. "
              f"Each page is read once and kept in {OCR_CACHE}.", flush=True)


READERS = {  # in the order of the plan's series: S-100, S-300, S-400, S-500, S-600
    "fondation": extract_foundations,
    "poutre": extract_beams,
    "mur": extract_walls,
    "colonne": extract_the_columns,
    "dalle": extract_slabs,
}


def run(arguments):
    """Extract one project, given as on the command line. Returns the paths of the two JSON files.

    A kind of element that cannot be read does not stop the others: it is said, and left out.
    """
    plan_pdf, by_kind = project_files(arguments)
    OUT_DIR.mkdir(exist_ok=True)
    announce_ocr(by_kind)
    plan_records, atelier_records = [], []
    for kind, reader in READERS.items():
        try:
            plan, atelier = reader(plan_pdf, by_kind[kind])
        except Exception as error:  # one unreadable kind must not cost the report of the others
            print(f"{kind}: not read ({type(error).__name__}: {error})")
            continue
        both = {(r.feuillet, r.element) for r in plan} & {(r.feuillet, r.element) for r in atelier}
        print(f"\n{kind}: {len(plan)} on the plan, {len(atelier)} on {len(by_kind[kind])} shop drawings, {len(both)} on both")
        extract_columns.print_sheets(plan, atelier)
        plan_records += plan
        atelier_records += atelier

    project = plan_pdf.stem.split("_")[-1]
    plan_path = OUT_DIR / f"{project}_plan.json"
    atelier_path = OUT_DIR / f"{project}_atelier.json"
    extract_columns.write_json(plan_records, plan_path)
    extract_columns.write_json(atelier_records, atelier_path)
    print(f"\n{len(plan_records)} plan records -> {plan_path}")
    print(f"{len(atelier_records)} shop-drawing records -> {atelier_path}")
    print(f"check by eye -> {OUT_DIR}/*_check.pdf")
    return plan_path, atelier_path


def main():
    run(sys.argv[1:])


if __name__ == "__main__":
    main()
