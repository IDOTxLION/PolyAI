# From Plans to Shop Drawings

Checks reinforcement shop drawings against the structural plans. From a project's PDFs it
extracts reinforcement into JSON (Appendix A of the brief), matches plan elements to their
shop-drawing equivalents, compares them, and writes PDF reports by plan sheet.

Everything runs locally. No document is sent to a cloud service or an external API.

## Install

Developed and tested with Python 3.14.

```
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Run

```
python main.py data/CLP
```

The argument is a project folder: the plan PDF at its top, the shop drawings in folders under
it. The column drawings are found by their folder or file name. A plan PDF followed by
shop-drawing PDFs or folders works too (`python main.py plan.pdf drawings/`). It writes, in
`out/`:

| File | Content |
|---|---|
| `<project>_colonnes_plan.json` | one record per column found on the plan |
| `<project>_colonnes_atelier.json` | one record per column and storey found on the shop drawings |
| `<project>_rapport.pdf` | the report: counts per plan sheet, and the detail of every element to flag |
| `*_colonnes_check.pdf` | the source PDFs with what was extracted drawn on top, to check by eye |

The submission CLI intentionally generates only the validated S-500 column outputs. An
experimental `extract_series.py` prototype exists for S-100, S-300, S-400 and S-600, but it
is not invoked by the CLI and is not included in the accuracy claims below.

A project whose drawings carry a text layer takes a few seconds. Drawings without one are read
by OCR, about half a minute per sheet on a GPU and far longer without; each sheet is read once
and kept in `out/ocr_cache/`, so the next run is fast. The OCR models (about 140 MB) are
downloaded the first time OCR runs, then it works offline. For the GPU, install a CUDA build
of PyTorch.

To rebuild the report from JSON already extracted: `python main.py plan.json atelier.json`.

`demo.ipynb` walks through the data and the whole pipeline on one project, step by step.

## Results on the provided projects (columns)

| Project | Shop drawings | Plan records | Shop records | Compliant | Non compliant | Missing | Added |
|---|---|---|---|---|---|---|---|
| CLP | storey tables, text layer | 394 | 392 | 375 | 8 | 11 | 9 |
| EspCa3B | one detail per column, OCR | 644 | 638 | 579 | 37 | 28 | 22 |
| WP2 | one detail per group of columns, OCR | 903 | 611 | 483 | 69 | 351 | 59 |
| LIGREP | one detail per column, text layer | 657 | 353 | 209 | 67 | 381 | 77 |

On CLP, both column non-conformities documented by the organisers are among the 8. Among the
columns found on both sides, 98% are compliant on CLP, 94% on EspCa3B, 88% on WP2 and 76% on
LIGREP. Many missing columns are on plan sheets for which no shop drawing was provided (156
on WP2, 287 on LIGREP); the report says so in one sentence per sheet. The flagged elements of
EspCa3B, WP2 and LIGREP were not checked against the drawings one by one.

Each project takes 4 to 7 seconds once its drawings have been read by OCR.

## How it works

| Step | Module | What it does |
|---|---|---|
| Plan extraction | `plan_columns.py` | Finds column tags on the S-500 sheets, follows each leader line to its column, and names the column after the grid lines it stands on. |
| Shop drawings, storey tables | `atelier_columns.py` | Reads a fabricator's table of columns by storey: one record per column and storey. |
| Shop drawings, details | `atelier_details.py` | Reads drawings that detail one column per block, from the text layer or from OCR. |
| Orchestration | `extract_columns.py` | Runs the plan and both shop-drawing readers, reconciles the names of columns that stand between grid lines, writes the JSON. |
| Record format | `schema.py`, `l2c/modeles.py` | The Appendix A record, validated on writing and read back for comparison. |
| Matching | `l2c/appariement.py` | Pairs plan and shop-drawing records on sheet and element name; what is left is missing or added. |
| Comparison | `l2c/comparaison.py` | Aligns the bars of a pair and lists the discrepancies. |
| Report | `l2c/rapport.py` | The PDF, by plan sheet. |
| Entry point | `main.py` | From a project's PDFs to the report in one command. |

**Plan.** A column sheet is found by its title (page label, or bookmark when the PDF has no
labels). A tag is a "COL." line with its "ARM." (vertical bars) and "LIG." (ties) lines; it
is tied to its column by a leader line. The grid is read from the bubbles in the margins, and
a column between two grid lines gets a decimal name ("B.4-12").

**Shop drawings.** Each drawing is tried as a storey table first, then as a sheet of details.
In a sheet of details the lines are shared between the detail titles; where the title sits
in its detail (above or under, left or middle) is worked out from the page. A detail drawn
for several columns ("C.5-12, D.5-12", "3x8 25M") gives a record to each. When the fabricator
numbers the columns itself, the name is the grid position written in the detail
("COL : BB - 15"). The storey comes from the table's row labels, or from the file name of a
sheet of details ("...NIV-2@3" stands on level 2), and gives the plan sheet the record is
filed under.

**Comparison rules.** The plan summarises the reinforcement (a bar count and diameter, a tie
diameter and spacing) while the shop drawing lists every line. Bars are therefore aligned by
kind (counted or spaced) and diameter, not by position, and quantities of one diameter are
added up. As the brief asks, a field is compared only when the plan gives it. An element is
flagged when a quantity, diameter or spacing differs, when a plan bar has no counterpart, or
when the shop drawing has a bar the plan does not. A tie zone tighter than the plan's spacing
complies; a wider one, or no zone at the plan's spacing, does not. A missed non-conformity
costs more than a false alarm, so anything doubtful is flagged for the engineer rather than
passed.

**Report.** For each plan sheet: the number of conformities and of non-conformities (non
compliant, missing from the shop drawings, added in the shop drawings), then one row per bar
of each flagged element with the plan value and the shop-drawing value side by side. Values
that differ are in red. Each row gives the file, page and X, Y of the element on the plan and
on the shop drawing.

## OCR

`start.py` reads a page that has no text layer: the page is rendered at 300 dpi, cut into
overlapping tiles, read once as displayed and once turned a quarter turn for vertical text,
and the duplicate readings are removed.

| Tool | Purpose |
|---|---|
| `python start.py file.pdf` | Words and positions of a PDF as JSON, plus an overlay PDF of what OCR read. |
| `python ocr_check.py file.pdf [page]` | Scores the OCR on a page that has a text layer, using that layer as the answer key. |
| `python ocr_review.py file.pdf [pages]` | Review sheets for pages without a text layer: every reading numbered on the drawing, to check by hand. `python ocr_review.py score` scores the corrections. |

Measured on 41 pages that have a text layer (36,254 numbers): 93.6% of numbers are read
exactly. Column drawings are read best (99%), dense slab and foundation sheets less well
(about 90%). A lone "1" whose shape is not that of the digit, usually an arrow or the end of a
line, is marked doubtful and not used.

## Model

No model was trained or fine-tuned, so there are no weights or training script to deliver. The
OCR is PaddleOCR's pretrained PP-OCRv6 detection and recognition models, unmodified, run
through RapidOCR (Apache-2.0). Extraction, matching and comparison are rule-based on the text
and geometry that PyMuPDF or the OCR reads from the PDFs.

## Tests

```
python -m unittest test_extraction l2c.modeles l2c.appariement l2c.comparaison l2c.rapport
```

They run on invented data and need none of the project documents. One report test needs
`pdftotext` (poppler).

## Assumptions

- A column is named after the two grid lines it stands on, as fabricators do in a title or
  in a position line; a column between two grid lines gets a decimal name, reconciled with
  the shop drawings when one name is close enough.
- A shop-drawing record belongs to the plan sheet of the storey the column stands on.
- When a column appears on several shop drawings, the first drawing that details it is kept.
- Ties counted one by one on the shop drawing, where the plan only gives a spacing, are
  listed but not judged. So are dowels, which the plan asks for without a count.
- "3x8 25M" in a detail drawn for three columns means 8 bars in each.
- A spacing of 24 or less with no unit is in inches, a larger one in millimetres.

## Known limitations

- The validated extraction and reporting pipeline covers columns (S-500). An experimental
  prototype for foundations, beams, shear walls and slabs is not part of the default run.
- A sheet of details is filed under a plan sheet from the storey in its file name; a drawing
  whose name tells no storey is left out.
- A column is matched by its name. When the plan's name and the fabricator's differ (a column
  between grid lines, a grid line the plan reader does not know, a name OCR misread), the
  column shows twice: missing from the shop drawings, and added in them.
- The layouts were worked out on four fabricators. Another fabricator's drawings may need
  their own rules before they are read.
- A quantity is flagged when a line was not read or was read into the neighbouring detail,
  as well as when the count really differs.
- OCR misses about 4% of numbers altogether and reads about 2% as another number, mostly on
  small or vertical text. Its confidence score does not single out the wrong ones.
- The record format is described twice, in `schema.py` and in `l2c/modeles.py`.

## Confidentiality

The project documents are confidential. `data/` (the PDFs) and `out/` (everything the tools
write, including the OCR readings) are ignored by git.
