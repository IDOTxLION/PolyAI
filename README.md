# From Plans to Shop Drawings

Checks reinforcement shop drawings against the structural plans. From a project's PDFs it
extracts reinforcement into JSON (Appendix A of the brief), matches plan elements to their
shop-drawing equivalents, compares them, and writes PDF reports by plan sheet.

Everything runs locally. No document is sent to a cloud service or an external API.

## Install

Developed with Python 3.14, also tested with Python 3.12.

```
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

That is enough for a project whose shop drawings carry a text layer (about ten seconds a
project). Drawings without one are read by OCR, which needs a GPU to be usable: about 35 s a
sheet with one, many minutes a sheet without. For the GPU, install a CUDA build of PyTorch
in the same environment (NVIDIA card; pick the index of your CUDA version on pytorch.org):

```
pip install torch --index-url https://download.pytorch.org/whl/cu128
```

The OCR models (about 140 MB) are downloaded the first time OCR runs, which needs internet
once; after that everything works offline.

## Run

```
python main.py data/CLP
```

The argument is a project folder: the plan PDF at its top, the shop drawings in folders under
it, one folder per kind of element. The kind is told by the folder's name, else by the file's
(`Colonnes`, `Fondations`, `Semelles et radiers`, `Poutres`, `Dalles`, `Murs`, `Refends`). A plan
PDF followed by shop-drawing PDFs or folders works too (`python main.py plan.pdf drawings/`).
It writes, in `out/`:

| File | Content |
|---|---|
| `<project>_plan.json` | one record per element found on the plan: foundations, beams, shear walls, columns, slabs |
| `<project>_atelier.json` | one record per element found on the shop drawings |
| `<project>_rapport.pdf` | the report: counts per plan sheet, and the detail of every element to flag |
| `*_check.pdf` | the source PDFs with what was extracted drawn on top, to check by eye |

A project whose drawings carry a text layer takes about ten seconds. Before reading drawings
without one, the program says how many sheets go to OCR and how long that takes (see Install
for the GPU). Each sheet is read once and kept in `out/ocr_cache/`, so the next run takes
seconds: on a new project, run it once before a demonstration.

To rebuild the report from JSON already extracted: `python main.py plan.json atelier.json`.

`demo.ipynb` walks through the data and the whole pipeline on one project, step by step.

## Results on the provided projects

What is read on each side, per kind of element:

| Element | Plan sheets | On the plan | On the shop drawings |
|---|---|---|---|
| Footings | S-100 | all four projects | drawings with a text layer that name each footing's column (CLP) |
| Rafts | S-050, S-060 | sheets that draw each raft in its own view | the same views, with a text layer (CLP) |
| Beams | S-300 | all four projects | drawings that keep the plan's span titles, text layer or OCR (CLP, LIGREP) |
| Shear walls | S-400 | all four projects | not read |
| Columns | S-500 | all four projects | all four projects, text layer or OCR |
| Slabs | S-600 | top bars over the columns, all four projects | drawings with a text layer (CLP) |

CLP is the project whose shop drawings carry a text layer, and the only one with an answer
key: the organisers' list of six known mismatches. Five of the six are flagged (raft, footing,
two columns, slab); the sixth is on a shear wall.

| CLP | Plan records | Shop records | Compliant | Non compliant | Missing | Added |
|---|---|---|---|---|---|---|
| Foundations (76 footings, 6 rafts) | 82 | 87 | 61 | 18 | 3 | 8 |
| Beams | 14 | 7 | 4 | 3 | 7 | 0 |
| Shear walls | 119 | 0 | 0 | 0 | 119 | 0 |
| Columns | 394 | 392 | 375 | 8 | 11 | 9 |
| Slabs | 1001 | 903 | 733 | 144 | 124 | 26 |

What these numbers are worth:

- Footings: 71 of the 73 footings found on both sides have the same type letter on both, so
  they are the same footings. 12 of the 16 flagged footings are those of one type, for which
  the shop drawing has two bars fewer each way than the schedule: a difference between the
  documents, for the engineer to judge. Two of the six rafts are flagged.
- Beams: the seven missing ones are not on the shop drawing that was provided.
- Shear walls: read from the plan only, so all are reported missing, in one sentence per sheet.
- Slabs: 84% of the callouts found on both sides agree. Most of the others are not real
  discrepancies: the bars of one callout are sometimes labelled in two places, and only labels
  written one under the other are added up. 52 missing callouts are on the roof sheet, for
  which no shop drawing was provided.

Columns on the four projects:

| Project | Shop drawings | Plan records | Shop records | Compliant | Non compliant | Missing | Added |
|---|---|---|---|---|---|---|---|
| CLP | storey tables, text layer | 394 | 392 | 375 | 8 | 11 | 9 |
| EspCa3B | one detail per column, OCR | 644 | 638 | 579 | 37 | 28 | 22 |
| WP2 | one detail per group of columns, OCR | 903 | 611 | 483 | 69 | 351 | 59 |
| LIGREP | one detail per column, text layer | 657 | 353 | 209 | 67 | 381 | 77 |

Among the columns found on both sides, 98% are compliant on CLP, 94% on EspCa3B, 88% on WP2
and 76% on LIGREP. Many missing columns are on plan sheets for which no shop drawing was
provided (156 on WP2, 287 on LIGREP); the report says so in one sentence per sheet.

On EspCa3B, WP2 and LIGREP the other elements are read from the plan (22, 131 and 93
foundations, 12, 21 and 14 beams, 224, 196 and 173 wall records, 864, 2176 and 1547 slab
callouts) but not from the shop drawings, which have no text layer and another layout: they
are reported missing, one sentence per sheet. The exception is LIGREP's beams, read by OCR:
7 of 14 are found, 1 compliant. Outside CLP, the flagged elements were not checked against
the drawings one by one.

A project takes 7 to 12 seconds once its drawings have been read by OCR.

## How it works

| Step | Module | What it does |
|---|---|---|
| Columns, plan | `plan_columns.py` | Finds column tags on the S-500 sheets, follows each leader line to its column, and names the column after the grid lines it stands on. |
| Columns, storey tables | `atelier_columns.py` | Reads a fabricator's table of columns by storey: one record per column and storey. |
| Columns, details | `atelier_details.py` | Reads drawings that detail one column per block, from the text layer or from OCR. |
| Foundations | `foundations.py` | Footings: the schedule of types and the type letter beside each footing on the plan, the fabricator's note per footing. Rafts: the mats written in each raft's view. |
| Beams | `beams.py` | Span titles and the bars written along each elevation, on both sides; spans drawn as one continuous beam are one element. |
| Shear walls | `walls.py` | The concentration tags and the wall bars of each elevation, storey by storey (plan only). |
| Slabs | `slabs.py` | The top bars over the columns: callouts of the plan, groups of labels of the shop drawing, paired through the grid. |
| Shared reading | `reading.py`, `grid.py` | The forms bars are written in, which view a line belongs to, the grid of a drawing and how one drawing maps onto another. |
| Orchestration | `extract_project.py`, `extract_columns.py` | Sorts the shop drawings by kind of element, runs the readers, writes the JSON. A kind that fails to read is said and left out; the others still go through. |
| Record format | `schema.py`, `l2c/modeles.py` | The Appendix A record, validated on writing and read back for comparison. |
| Matching | `l2c/appariement.py` | Pairs plan and shop-drawing records on sheet and element name; what is left is missing or added. |
| Comparison | `l2c/comparaison.py` | Aligns the bars of a pair and lists the discrepancies. |
| Report | `l2c/rapport.py` | The PDF, by plan sheet. |
| Entry point | `main.py` | From a project's PDFs to the report in one command. |

**How an element gets its name.** Matching needs the same sheet and the same name on both
sides, and the plan names almost nothing itself:

| Element | Sheet (`feuillet`) | Name (`element`) |
|---|---|---|
| Column, footing | the plan sheet of the storey, the footing sheet | the grid lines its column stands on: `C-12` |
| Raft | the sheet that draws it | `RADIER 2` |
| Beam | the sheet that draws it | its spans: `P101+P102+P103` |
| Wall | the sheet of its elevation | wall, storey, place along the wall: `MUR B RDC C2`; `MUR B RDC` for the bars of the wall itself |
| Slab top bars | the sheet of the level | the nearest grid crossing and the grid line the bars run along: `C-12 axe C` |

**Columns.** A column sheet is found by its title (page label, or bookmark when the PDF has no
labels). A tag is a "COL." line with its "ARM." (vertical bars) and "LIG." (ties) lines; it
is tied to its column by a leader line. The grid is read from the bubbles in the margins, and
a column between two grid lines gets a decimal name ("B.4-12"). Each shop drawing is tried as
a storey table first, then as a sheet of details. In a sheet of details the lines are shared
between the detail titles; where the title sits in its detail is worked out from the page. A
detail drawn for several columns ("C.5-12, D.5-12", "3x8 25M") gives a record to each. When
the fabricator numbers the columns itself, the name is the grid position written in the detail
("COL : BB - 15"). The storey comes from the table's row labels, or from the file name of a
sheet of details ("...NIV-2@3" stands on level 2).

**Footings.** The sheet that carries the schedule ("TYPE A | ... | ARM. LONG. | ARM. TRANS.")
is the footing sheet. Each footing has its type letter beside it; the letter is written at the
same corner of every footing of a sheet, which tells a footing's column from a nearer
neighbour's. The footing takes its column's name and its type's bars. On the shop drawing a
footing is a note that names its column ("COLONNE A-6, 16X24:") and gives the bars along and
across ("LONG:", "TRAN:").

**Rafts.** A raft is drawn in plan in a view of its own, titled under it, with the spacing of
its mats written zone by zone. A raft is one element; the spacings of each mat, bottom (layers
1 and 2) and top (3 and 4), are compared as a set.

**Beams.** A title under each span gives its name and section. The bars written above a title
belong to that span; two neighbours are told apart by the widest empty stretch between their
titles. Spans drawn under one unbroken outline are one beam, because the bars over a support
belong to both spans.

**Slabs.** The plan writes the top bars over a column as "14(6)": 14 bars, 6 of them in the
band at the column, with the diameter when it is not the sheet's typical one. The fabricator
labels the same bars ("8 15J9-06", "6 m.b."). The shop drawing is laid over the plan through
the grid lines both carry, and each group of labels takes the name of the callout it is
nearest to, pairs with equal counts first.

**Comparison rules.** The plan summarises the reinforcement (a bar count and diameter, a tie
diameter and spacing) while the shop drawing lists every line. Bars are therefore aligned by
kind (counted or spaced) and diameter, not by position, and quantities of one diameter are
added up. When the plan gives its bars a role (along and across a footing, bottom and top mat
of a raft, the bars and the band of a slab callout), each role is compared on its own, and
shop-drawing bars of a role the plan does not give (dowels, ties of the dowels) are listed
without being judged. As the brief asks, a field is compared only when the plan gives it. An
element is flagged when a quantity, diameter or spacing differs, when a plan bar has no
counterpart, or when the shop drawing has a bar the plan does not. In a column, a tie zone
tighter than the plan's spacing complies; in every other element any other spacing is flagged.
A missed non-conformity costs more than a false alarm, so anything doubtful is flagged for the
engineer rather than passed.

**Report.** For each plan sheet, with the kind of element it holds: the number of conformities
and of non-conformities (non compliant, missing from the shop drawings, added in the shop
drawings), then one row per bar of each flagged element with the plan value and the
shop-drawing value side by side. Values that differ are in red. Each row gives the file, page
and X, Y of the element on the plan and on the shop drawing.

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

The tests run on synthetic data, which is all this repository provides: the project documents
are confidential and are not published here. The pipeline itself was developed and run on the
real plans and shop drawings, locally, and the figures under Results come from them. One
report test needs `pdftotext` (poppler).

## Assumptions

- A column is named after the two grid lines it stands on, as fabricators do in a title or
  in a position line; a column between two grid lines gets a decimal name, reconciled with
  the shop drawings when one name is close enough.
- A shop-drawing record belongs to the plan sheet of the storey the column stands on.
- When an element appears on several shop drawings, the first drawing that details it is kept.
- Ties counted one by one on the shop drawing, where the plan only gives a spacing, are
  listed but not judged. So are dowels, which the plan asks for without a count.
- "3x8 25M" in a detail drawn for three columns means 8 bars in each.
- A spacing of 24 or less with no unit is in inches, a larger one in millimetres.
- A footing belongs to the column on the side where most type letters of the sheet stand
  from their column.
- Layers 1 and 2 of a raft are its bottom mat, layers 3 and 4 its top mat.
- The second number of a slab callout, "14(6)", is the count the fabricator writes "6 m.b.".
- A slab label written right over or under another is read alone when its own quantity is the
  callout's, and added to its neighbour otherwise.

## Known limitations

- Shear walls are read from the plan only: all of them are reported missing.
- Outside columns, the shop-drawing readers were worked out on one fabricator, the one whose
  drawings have a text layer. Footings, rafts and slabs of the other three are not read, and
  a slab shop drawing without a text layer is not attempted (see OCR below).
- Slabs: only the top bars over the columns are compared, not the bottom mat nor the
  additional bars. About one paired callout in six is flagged, mostly because the labels of
  one callout are not written together. A callout paired with a neighbour's labels shows as a
  discrepancy.
- Beams: quantities are added up per diameter over the whole beam, so a bar supplied in two
  lapped pieces counts twice. Three of the seven CLP beams found on both sides are flagged.
- Rafts: the spacings of a mat are compared as a set, not zone by zone. A zone built at a
  spacing that another zone of the same mat has is not seen.
- Strip footings, defined by a section and not by the schedule, are left out.
- A sheet of column details is filed under a plan sheet from the storey in its file name; a
  drawing whose name tells no storey is left out. So is a slab drawing whose name tells no
  level.
- An element is matched by its name. When the plan's name and the fabricator's differ (a
  column between grid lines, a grid line the plan reader does not know, a name OCR misread),
  it shows twice: missing from the shop drawings, and added in them.
- A quantity is flagged when a line was not read or was read into the neighbouring detail,
  as well as when the count really differs.
- OCR misses about 4% of numbers altogether and reads about 2% as another number, mostly on
  small or vertical text. Its confidence score does not single out the wrong ones.
- The record format is described twice, in `schema.py` and in `l2c/modeles.py`.

## Confidentiality

The project documents are confidential. `data/` (the PDFs) and `out/` (everything the tools
write, including the OCR readings) are ignored by git.
