"""Conservative extraction for the non-column plan series.

The column reader is intentionally specialised because column plans have a reliable
leader-line and grid convention.  The other series vary much more between
fabricators, so this reader extracts only labelled elements and nearby reinforcement
text.  Uncertain records are retained (with a ``?`` name) for engineer review rather
than silently omitted.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pymupdf

from atelier_details import lift_key, millimetres
from plan_columns import level_key, page_titles, sheet_levels
from schema import Armature, Element

SERIES = {
    "S-100": ("fondation", ("fond", "foundation", "foot", "semelle", "radier")),
    "S-300": ("poutre", ("poutre", "beam", "girder")),
    "S-400": ("mur", ("mur", "wall", "shear")),
    "S-600": ("dalle", ("dalle", "slab", "floor")),
}

LABEL = re.compile(r"\b([A-Z]{1,3})\s*[-/]\s*(\d{1,4}(?:\.\d+)?)\b")
BAR = re.compile(r"(?:(\d+)\s*[-xX]\s*)?(\d{1,3}M)\b", re.I)
SPACING = re.compile(r"@\s*(\d+(?:\.\d+)?)\s*(?:[\"”″']|mm\b)?", re.I)


def _lines(page):
    lines = []
    for block in page.get_text("dict")["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            text = " ".join("".join(span["text"] for span in line["spans"]).split())
            if text:
                lines.append((pymupdf.Rect(line["bbox"]) * page.rotation_matrix, text))
    return lines


def _series_for_sheet(sheet: str) -> str | None:
    match = re.search(r"S[- ]?(\d{3})", sheet.upper())
    return f"S-{match.group(1)}" if match and f"S-{match.group(1)}" in SERIES else None


def _armature(lines):
    result = []
    for box, text in lines:
        for match in BAR.finditer(text):
            # A reinforcement line usually has a keyword, but many elevation sheets
            # use only a bar mark and diameter.  Exclude isolated sheet labels.
            if not any(char.isdigit() for char in text[:match.start()]) and not SPACING.search(text):
                continue
            quantity = int(match.group(1)) if match.group(1) else None
            spacing = SPACING.search(text, match.end())
            result.append(
                Armature(
                    repere=None,
                    diametre=match.group(2).upper(),
                    quantite=quantity,
                    espacement_mm=millimetres(float(spacing.group(1)), spacing.group(0).find('"') >= 0)
                    if spacing else None,
                    longueur_mm=None,
                )
            )
    return result


def _element_candidates(lines, label_words):
    candidates = []
    for index, (box, text) in enumerate(lines):
        upper = text.upper()
        if not any(word.upper() in upper for word in label_words):
            continue
        labels = LABEL.findall(text)
        for prefix, number in labels:
            candidates.append((index, box, f"{prefix}-{number}"))
    if candidates:
        return candidates
    # Elevation sheets often identify the element only by its mark (for example
    # B-12 or D-4) and put the series name in the sheet title.
    return [
        (index, box, f"{prefix}-{number}")
        for index, (box, text) in enumerate(lines)
        for prefix, number in LABEL.findall(text)
    ]


def extract_plan(plan_pdf: Path, series: str):
    """Extract labelled elements and reinforcement text from one plan series."""
    kind, words = SERIES[series]
    records, report = [], Counter()
    doc = pymupdf.open(plan_pdf)
    titles = page_titles(doc)
    for page in doc:
        sheet = _series_for_sheet(titles.get(page.number, ""))
        if sheet != series:
            continue
        lines = _lines(page)
        candidates = _element_candidates(lines, words)
        for ordinal, (index, label_box, name) in enumerate(candidates, 1):
            nearby = [
                item for item in lines
                if abs(item[0].y0 - label_box.y0) < max(180, label_box.height * 12)
                and (item[0].x0 - label_box.x1 < 260)
                and (item[0].x1 - label_box.x0 > -260)
            ]
            armature = _armature(nearby)
            centre = (label_box.tl + label_box.br) / 2
            records.append(Element(
                id=f"{series}_{name}_plan_{page.number + 1}_{ordinal}",
                source="plan", fichier=Path(plan_pdf).name, feuillet=series,
                page=page.number + 1, x=round(centre.x, 1), y=round(centre.y, 1),
                type_element=kind, element=name, armature=armature,
            ))
        report[page.number + 1] = len(candidates)
    return records, report


def _drawing_matches(path: Path, series: str) -> bool:
    words = SERIES[series][1]
    haystack = path.as_posix().lower()
    return any(word in haystack for word in words)


def extract_atelier(pdfs, plan_pdf: Path, series: str):
    """Extract labelled detail blocks from likely shop drawings for a series."""
    kind = SERIES[series][0]
    levels = sheet_levels(pymupdf.open(plan_pdf))
    lowest = min(levels.values(), default=series)
    records = []
    for pdf_path in pdfs:
        if not _drawing_matches(pdf_path, series):
            continue
        key = lift_key(pdf_path.stem)
        sheet = levels.get(key, lowest)
        doc = pymupdf.open(pdf_path)
        for page in doc:
            lines = _lines(page)
            for ordinal, (index, label_box, name) in enumerate(
                _element_candidates(lines, SERIES[series][1]), 1
            ):
                nearby = [
                    item for item in lines
                    if abs(item[0].y0 - label_box.y0) < max(220, label_box.height * 14)
                    and abs(item[0].x0 - label_box.x0) < 300
                ]
                armature = _armature(nearby)
                if not armature:
                    continue
                centre = (label_box.tl + label_box.br) / 2
                records.append(Element(
                    id=f"{sheet}_{name}_atelier_{page.number + 1}_{ordinal}",
                    source="atelier", fichier=pdf_path.name, feuillet=sheet,
                    page=page.number + 1, x=round(centre.x, 1), y=round(centre.y, 1),
                    type_element=kind, element=name, armature=armature,
                ))
    return records


def project_files(arguments):
    first = Path(arguments[0]) if arguments else Path("data")
    project = first if first.is_dir() else first.parent
    plan = first if first.is_file() else next(
        (path for path in sorted(project.glob("*.pdf")) if "plan" in path.name.lower()),
        None,
    )
    if plan is None:
        plans = sorted(project.glob("*.pdf"))
        if len(plans) != 1:
            raise FileNotFoundError(f"{project}: cannot identify the plan PDF")
        plan = plans[0]
    drawings = sorted(path for path in project.rglob("*.pdf") if path != plan)
    return plan, drawings


def run_all(arguments, output_dir: Path):
    """Write one plan JSON, shop JSON and report input per non-column series."""
    plan_pdf, drawings = project_files(arguments)
    output_dir.mkdir(parents=True, exist_ok=True)
    project = plan_pdf.stem.split("_")[-1]
    results = {}
    for series in SERIES:
        plan, _ = extract_plan(plan_pdf, series)
        atelier = extract_atelier(drawings, plan_pdf, series)
        plan_path = output_dir / f"{project}_{series.replace('-', '')}_plan.json"
        atelier_path = output_dir / f"{project}_{series.replace('-', '')}_atelier.json"
        for path, records in ((plan_path, plan), (atelier_path, atelier)):
            path.write_text(
                __import__("json").dumps(
                    [record.model_dump() for record in records],
                    ensure_ascii=False,
                    indent=1,
                ),
                encoding="utf8",
            )
        results[series] = (plan_path, atelier_path, len(plan), len(atelier))
    return results
