# From Plans to Shop Drawings

Checks reinforcement shop drawings against the structural plans. From a project's PDFs it
extracts each element's reinforcement into JSON (Appendix A of the brief), matches every plan
element to its shop-drawing equivalent, compares them, and writes a PDF report by plan sheet.

Everything runs locally. No document is sent to a cloud service or an external API.

## Install

Developed and tested with Python 3.14.

```
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

The OCR models ship inside the `rapidocr` package, so nothing is downloaded at run time. With a
CUDA build of PyTorch installed, OCR runs on the GPU, about 10 times faster.

## Run

```
python main.py data/CLP/L2C_PLAN_STR_CLP.pdf data/CLP/DA/Colonnes
```

The first argument is the plan PDF, the rest are shop-drawing PDFs or folders of them. With no
argument it runs on the CLP project as above. It writes, in `out/`:

| File | Content |
|---|---|
| `<project>_colonnes_plan.json` | one record per column found on the plan |
| `<project>_colonnes_atelier.json` | one record per column and storey found on the shop drawings |
| `<project>_rapport.pdf` | the report: counts per plan sheet, and the detail of every element to flag |
| `*_colonnes_check.pdf` | the source PDFs with what was extracted drawn on top, to check by eye |

To rebuild the report from JSON already extracted: `python main.py plan.json atelier.json`.

## How it works

| Step | Module | What it does |
|---|---|---|
| Plan extraction | `plan_columns.py` | Finds the column tags on the S-500 sheets, follows each leader line to its column, and names the column after the grid lines it stands on. |
| Shop-drawing extraction | `atelier_columns.py` | Reads the fabricator's column tables: one record per column and storey, filed under the plan sheet of that storey. |
| Orchestration | `extract_columns.py` | Runs both, reconciles the names of columns that stand between grid lines, writes the JSON. |
| Record format | `schema.py`, `l2c/modeles.py` | The Appendix A record, validated on writing and read back for comparison. |
| Matching | `l2c/appariement.py` | Pairs plan and shop-drawing records on sheet and element name; what is left is missing or added. |
| Comparison | `l2c/comparaison.py` | Aligns the bars of a pair and lists the discrepancies. |
| Report | `l2c/rapport.py` | The PDF, by plan sheet. |
| Entry point | `main.py` | From PDFs to report in one command. |

**Comparison rules.** The plan summarises the reinforcement (a bar count and diameter, a tie
diameter and spacing) while the shop drawing lists every line. Bars are therefore aligned by
kind (counted or spaced) and diameter, not by position, and quantities of one diameter are
added up. As the brief asks, a field is compared only when the plan gives it. An element is
flagged when a quantity, diameter or spacing differs, when a plan bar has no counterpart, or
when the shop drawing has a bar the plan does not. A missed non-conformity costs more than a
false alarm, so anything doubtful is flagged for the engineer rather than passed.

**Report.** For each plan sheet: the number of conformities and of non-conformities (non
compliant, missing from the shop drawings, added in the shop drawings), then one row per bar
of each flagged element with the plan value and the shop-drawing value side by side. Values
that differ are in red. Each row gives the file, page and X, Y of the element on the plan and
on the shop drawing.

## Results on the CLP project (columns)

394 plan records and 392 shop-drawing records, extracted and compared in about 4 seconds:
375 compliant, 8 non compliant, 11 missing, 9 added. Both column non-conformities documented
by the organisers for this project are among the 8.

## OCR

Most shop drawings of the other projects have no text layer. `start.py` reads such pages with
OCR: the page is rendered at 300 dpi, cut into overlapping tiles, read once as displayed and
once turned a quarter turn for vertical text, and the duplicate readings are removed.

| Tool | Purpose |
|---|---|
| `python start.py file.pdf` | Words and positions of a PDF as JSON, plus an overlay PDF of what OCR read. |
| `python ocr_check.py file.pdf [page]` | Scores the OCR on a page that has a text layer, using that layer as the answer key. |
| `python ocr_review.py file.pdf [pages]` | Review sheets for pages without a text layer: every reading numbered on the drawing, to check by hand. `python ocr_review.py score` scores the corrections. |

Measured on 41 pages that have a text layer (36,254 numbers): 93.6% of numbers are read
exactly. Column drawings are read best (99%), dense slab and foundation sheets less well
(about 90%). A lone "1" whose shape is not that of the digit, usually an arrow or the end of a
line, is marked doubtful instead of being trusted.

## Model

No model was trained or fine-tuned, so there are no weights or training script to deliver. The
OCR is PaddleOCR's pretrained PP-OCRv6 detection and recognition models, unmodified, run
through RapidOCR (Apache-2.0). Extraction, matching and comparison are rule-based on the text
and geometry that PyMuPDF reads from the PDFs.

## Tests

```
python -m unittest l2c.modeles l2c.appariement l2c.comparaison l2c.rapport
```

One report test needs `pdftotext` (poppler).

## Assumptions

- A column is named after the two grid lines it stands on, as the fabricator does; a column
  between two grid lines gets a decimal name, reconciled with the shop drawings when one name
  is close enough.
- A shop-drawing cell belongs to the plan sheet of the storey the column stands on.
- When a column appears on several shop drawings, the first drawing that details it is kept.
- Ties counted one by one on the shop drawing, where the plan only gives a spacing, are
  listed but not judged.

## Known limitations

- Only columns (S-500) are extracted. Foundations, beams, shear walls and slabs are not.
- Only CLP runs end to end. On the other projects the plan side works for WP2, finds the
  LIGREP columns without naming them, and finds nothing on EspCa3B; their shop drawings use
  other layouts or need OCR, and the OCR output is not yet turned into records.
- Most of the missing and added elements on CLP, and some of the non compliant ones, are
  columns between grid lines whose name does not match the fabricator's. They are pairing
  errors of the extraction, not real discrepancies.
- OCR misses about 4% of numbers altogether and reads about 2% as another number, mostly on
  small or vertical text. Its confidence score does not single out the wrong ones.
- The record format is described twice, in `schema.py` and in `l2c/modeles.py`.

## Confidentiality

The project documents are confidential. `data/` (the PDFs) and `out/` (everything the tools
write) are ignored by git.
