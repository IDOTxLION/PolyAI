"""Inspect one PDF: words with their positions, plus OCR of image regions (titles).

Words come from the embedded text when the page has any; pages drawn as vector
outlines (most shop drawings) have none and are read with RapidOCR instead.

Usage: python start.py [path/to/file.pdf]
Writes out/<pdf name>.json and prints a one-line summary per page. If any page was read by
OCR, also writes out/<pdf name>_overlay.pdf: the drawing with a box around every piece of
text and what OCR read printed above it, to check the reading by eye.
"""

import json
import logging
import sys
import warnings
from pathlib import Path

import numpy as np
import pymupdf

DEFAULT_PDF = Path("data/CLP/L2C_PLAN_STR_CLP.pdf")
OUT_DIR = Path("out")  # git-ignored: the outputs contain confidential plan content
OCR_DPI = 300  # 97% of numeric tokens read on the CLP benchmark page, against 95% at 200
TILE = 2000  # pixels; a 36x24 in sheet is far larger than the OCR model's input
TILE_OVERLAP = 500  # text cut by a tile edge is whole in the neighbouring tile if shorter than this
OCR_MODEL_SIZE = "medium"  # "small" is about 2x faster but read 80% of a CLP slab page against 90%
MIN_EMBEDDED_WORDS = 20  # fewer than this and the page is treated as having no text layer

_engine = None


def ocr_engine():
    """Create the RapidOCR engine once; it runs on the GPU through PyTorch when there is one."""
    global _engine
    if _engine is None:
        from rapidocr import EngineType, ModelType, RapidOCR

        logging.getLogger("RapidOCR").disabled = True  # it logs every model load and blank tile
        warnings.filterwarnings("ignore", message="Using padding='same'")
        size = ModelType(OCR_MODEL_SIZE)
        params = {"Det.model_type": size, "Rec.model_type": size}
        try:
            import torch

            if torch.cuda.is_available():
                params |= {
                    "Det.engine_type": EngineType.TORCH,
                    "Cls.engine_type": EngineType.TORCH,
                    "Rec.engine_type": EngineType.TORCH,
                    "EngineConfig.torch.use_cuda": True,
                }
        except ImportError:
            pass
        _engine = RapidOCR(params=params)
    return _engine


def read_image(img):
    """OCR one image array. Returns (x0, y0, x1, y1, text, score) per text line, in pixels."""
    result = ocr_engine()(np.ascontiguousarray(img))
    if result is None or result.boxes is None:
        return []
    lines = []
    for quad, text, score in zip(np.asarray(result.boxes), result.txts, result.scores):
        xs, ys = quad[:, 0], quad[:, 1]
        lines.append((float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max()), text, float(score)))
    return lines


def render(page, clip=None):
    """Render the page as displayed (or one region of it) to an RGB array."""
    pix = page.get_pixmap(dpi=OCR_DPI, clip=clip, colorspace=pymupdf.csRGB, alpha=False)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)


def covered(box, others):
    """Share of box's area covered by the most overlapping box in others (an N x 4 array)."""
    if len(others) == 0:
        return 0.0
    w = np.minimum(box[2], others[:, 2]) - np.maximum(box[0], others[:, 0])
    h = np.minimum(box[3], others[:, 3]) - np.maximum(box[1], others[:, 1])
    area = max((box[2] - box[0]) * (box[3] - box[1]), 1e-6)
    return float((np.clip(w, 0, None) * np.clip(h, 0, None)).max() / area)


def touches_inner_edge(x0, y0, x1, y1, left, top, right, bottom, width, height):
    """True if a box (in image pixels) reaches an edge of its crop that is not the image edge."""
    edge = 4  # pixels
    return ((left > 0 and x0 <= left + edge) or (top > 0 and y0 <= top + edge)
            or (right < width and x1 >= right - edge) or (bottom < height and y1 >= bottom - edge))


def read_crop(img, left, top, right, bottom):
    """OCR one rectangle of the image. Returns (score, cut, x0, y0, x1, y1, text) in image pixels.

    cut is True when the reading touches an inner edge of the rectangle, so it may be truncated.
    """
    height, width = img.shape[:2]
    readings = []
    for x0, y0, x1, y1, text, score in read_image(img[top:bottom, left:right]):
        x0, y0, x1, y1 = left + x0, top + y0, left + x1, top + y1
        cut = touches_inner_edge(x0, y0, x1, y1, left, top, right, bottom, width, height)
        readings.append((score, cut, x0, y0, x1, y1, text.strip()))
    return readings


def same_line(a, b):
    """True if two boxes overlap sideways and sit on the same text row."""
    overlap_y = min(a[3], b[3]) - max(a[1], b[1])
    return a[0] < b[2] and b[0] < a[2] and overlap_y > 0.5 * min(a[3] - a[1], b[3] - b[1])


def group_same_line(boxes):
    """Group boxes of one text row that overlap, directly or through a chain. Returns index lists."""
    groups = []
    for i, box in enumerate(boxes):
        joined = [g for g in groups if any(same_line(box, boxes[j]) for j in g)]
        for g in joined:
            groups.remove(g)
        groups.append([i] + [j for g in joined for j in g])
    return groups


def join_texts(left, right):
    """Join two readings of one line that share its middle, e.g. 'NIV3 PA' + 'V3 PART 3'.

    The characters at the cut are often misread, so up to two may be dropped on each side.
    Returns None when no shared part of at least three characters is found.
    """
    best = None
    for drop_left in range(3):
        for drop_right in range(3):
            a = left[:len(left) - drop_left]
            b = right[drop_right:]
            for size in range(min(len(a), len(b)), 2, -1):
                if a[-size:] == b[:size]:
                    if best is None or size > best[0]:
                        best = (size, a + b[size:])
                    break
    return best[1] if best else None


def join_pieces(pieces):
    """Join the truncated readings of one long line, left to right, into a single reading.

    Returns one whole reading, or the pieces unchanged (still marked cut) if they do not fit.
    """
    pieces = sorted(pieces, key=lambda r: r[2])
    score, _, x0, y0, x1, y1, text = pieces[0]
    for piece in pieces[1:]:
        if piece[4] <= x1 + 4:  # nothing new: the same stretch seen from another tile
            continue
        joined = join_texts(text, piece[6])
        if joined is None:
            return pieces
        text = joined
        score, x1 = min(score, piece[0]), piece[4]
        y0, y1 = min(y0, piece[3]), max(y1, piece[5])
    return [(score, False, x0, y0, x1, y1, text)]


def read_tiled(img):
    """OCR a large image tile by tile. Returns (score, cut, x0, y0, x1, y1, text) in image pixels.

    Text longer than the tile overlap (sheet titles, long notes) is truncated in every tile
    that sees it. Those pieces are joined on the part they share; only pieces that cannot be
    joined are returned with cut=True.
    """
    height, width = img.shape[:2]
    step = TILE - TILE_OVERLAP

    readings = []
    for top in range(0, max(height - TILE_OVERLAP, 1), step):
        for left in range(0, max(width - TILE_OVERLAP, 1), step):
            readings += read_crop(img, left, top, min(left + TILE, width), min(top + TILE, height))

    whole = [r for r in readings if not r[1]]
    whole_boxes = np.array([r[2:6] for r in whole]).reshape(-1, 4)
    # Truncated pieces of vertical text (tall boxes) are left to the pass where they lie flat.
    pieces = [r for r in readings if r[1] and (r[5] - r[3]) <= 1.5 * (r[4] - r[2])
              and covered(np.array(r[2:6]), whole_boxes) < 0.5]
    for group in group_same_line([r[2:6] for r in pieces]):
        whole += join_pieces([pieces[i] for i in group])
    return whole


def sideways(reading):
    """True if the text was not lying flat in the pass that read it.

    Each pass also reads the text meant for the other one, a quarter turn off. Such a reading
    is as sure of its characters, but its box is often cut short. Short readings are never
    called sideways: their box does not tell which way they run.
    """
    x0, y0, x1, y1, text, vertical = reading[2:8]
    width, height = x1 - x0, y1 - y0
    if len(text) < 3:
        return False
    return width > 1.2 * height if vertical else height > 1.2 * width


def priority(reading):
    """Sort key deciding which of several overlapping readings is kept: lowest first.

    Whole readings before truncated ones, then text lying flat before text read sideways,
    then the best score. Preferring the longest box instead gave fewer duplicates but lost
    vertical text on the CLP benchmark pages.
    """
    score, cut = reading[:2]
    return (cut, sideways(reading), -score)


def page_readings(img):
    """Read a rendered page twice: as displayed, and turned a quarter turn for vertical text.

    Returns every reading before duplicates are removed, as
    (score, cut, x0, y0, x1, y1, text, vertical) in pixels of the displayed page.
    """
    height = img.shape[0]
    readings = [(*r, False) for r in read_tiled(img)]
    # Turn the page clockwise so text running bottom-to-top becomes horizontal; the engine
    # itself handles upside-down text, which covers top-to-bottom text in this pass.
    for score, cut, x0, y0, x1, y1, text in read_tiled(np.rot90(img, k=-1)):
        readings.append((score, cut, y0, height - x1, y1, height - x0, text, True))
    return readings


def letters(text):
    """The letters and digits of a text, to compare two readings of the same place."""
    return "".join(char for char in text.upper() if char.isalnum())


def says_more(longer, shorter):
    """True if a reading repeats another one and adds to it: the same characters and more,
    in a box that holds the other's."""
    a, b = letters(longer[6]), letters(shorter[6])
    if longer[1] or not b or len(a) <= len(b) or b not in a:
        return False
    return covered(np.array(shorter[2:6]), np.array([longer[2:6]])) >= 0.8


def drop_duplicates(readings):
    """Keep one reading per piece of text.

    The same text is usually read several times (tiles overlap, and each pass also attempts
    the text meant for the other one). Readings are taken in priority order, and one is
    dropped when an already kept reading covers half of its box or more.

    A dropped reading that says the same as the kept one and more takes its place: a bold
    "QTE : 1" is also read as "QTE :" alone, with a better score, which lost the number.
    """
    kept, kept_boxes = [], np.empty((0, 4))
    for reading in sorted(readings, key=priority):
        box = np.array(reading[2:6])
        if not reading[6]:
            continue
        if covered(box, kept_boxes) < 0.5:
            kept.append(reading)
            kept_boxes = np.vstack([kept_boxes, box])
            continue
        w = np.minimum(box[2], kept_boxes[:, 2]) - np.maximum(box[0], kept_boxes[:, 0])
        h = np.minimum(box[3], kept_boxes[:, 3]) - np.maximum(box[1], kept_boxes[:, 1])
        repeated = int(np.argmax(np.clip(w, 0, None) * np.clip(h, 0, None)))
        if says_more(reading, kept[repeated]):
            kept[repeated], kept_boxes[repeated] = reading, box
            # Readings kept on their own that the longer one now includes.
            inside = [i for i in range(len(kept)) if i != repeated and covered(kept_boxes[i], box[None, :]) >= 0.5
                      and letters(kept[i][6]) in letters(reading[6])]
            for i in sorted(inside, reverse=True):
                del kept[i]
                kept_boxes = np.delete(kept_boxes, i, axis=0)
    return kept


def ink_pieces(ink):
    """Split an ink mask into its connected pieces. Returns one mask per piece."""
    labels = np.zeros(ink.shape, int)
    count = 0
    for y, x in zip(*np.nonzero(ink)):
        if labels[y, x]:
            continue
        count += 1
        labels[y, x] = count
        stack = [(y, x)]
        while stack:
            cy, cx = stack.pop()
            for ny in range(max(cy - 1, 0), min(cy + 2, ink.shape[0])):
                for nx in range(max(cx - 1, 0), min(cx + 2, ink.shape[1])):
                    if ink[ny, nx] and not labels[ny, nx]:
                        labels[ny, nx] = count
                        stack.append((ny, nx))
    return [labels == n for n in range(1, count + 1)]


def looks_like_one(gray):
    """True if a grey-level crop holds a digit 1: a stem with a hook on its top left and
    nothing on its top right.

    An arrow or the end of a line is often read as "1". An arrowhead has ink on both sides
    of its stem and a bare line has none, while a real 1 always has the hook. The baseline is
    not asked for: bold titles draw the 1 without one. Each connected piece of ink is tried
    on its own, so that a frame or a rule beside the digit does not hide it.
    """
    low, high = gray.min(), gray.max()
    if high - low < 40:  # no ink at all
        return False
    ink = gray < (low + high) / 2
    for piece in ink_pieces(ink):
        rows, cols = np.flatnonzero(piece.any(axis=1)), np.flatnonzero(piece.any(axis=0))
        if len(rows) < 0.4 * ink.shape[0]:  # too small to be the character that was read
            continue
        glyph = piece[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]
        height = glyph.shape[0]
        stem = np.flatnonzero(glyph.sum(axis=0) >= 0.7 * height)
        if len(stem) == 0:
            continue
        top = glyph[: max(1, round(0.4 * height))]
        left, right = int(top[:, :stem[0]].sum()), int(top[:, stem[-1] + 1:].sum())
        if left >= max(2, len(stem)) and right <= 0.25 * left:
            return True
    return False


def is_digit_one(img, reading):
    """Check the shape under a reading of a lone "1", upright or upside down in its own pass."""
    x0, y0, x1, y1 = (int(value) for value in reading[2:6])
    gray = img[max(y0, 0):y1 + 1, max(x0, 0):x1 + 1].mean(axis=2)
    if gray.size == 0:
        return False
    if reading[7]:  # read in the turned pass: lay it the way that pass saw it
        gray = np.rot90(gray, k=-1)
    return looks_like_one(gray) or looks_like_one(np.rot90(gray, k=2))


def ocr_page_words(page):
    """Read a whole page with OCR.

    Returns one entry per text line found (it can hold several words), with its centre and
    size in displayed page points and the OCR confidence (0-100). Entries read in the turned
    pass carry "vertical", and ones that may be truncated carry "cut". A lone "1" whose ink is
    not shaped like the digit carries "doubtful": it is kept for the engineer to check rather
    than removed.
    """
    img = render(page)
    kept = drop_duplicates(page_readings(img))

    scale = 72 / OCR_DPI  # pixels of the rendered page -> PDF points
    words = []
    for score, cut, x0, y0, x1, y1, text, vertical in sorted(kept, key=lambda r: (r[3], r[2])):
        word = {
            "text": text,
            "x": round((x0 + x1) / 2 * scale, 1),
            "y": round((y0 + y1) / 2 * scale, 1),
            "w": round((x1 - x0) * scale, 1),
            "h": round((y1 - y0) * scale, 1),
            "conf": round(score * 100),
        }
        if vertical:
            word["vertical"] = True
        if cut:
            word["cut"] = True
        if text == "1" and not is_digit_one(img, (score, cut, x0, y0, x1, y1, text, vertical)):
            word["doubtful"] = True
        words.append(word)
    return words


def ocr_region(page, rect):
    """Read the text of one region of the page (rect in displayed coordinates)."""
    lines = sorted(read_image(render(page, clip=rect)), key=lambda line: (line[1], line[0]))
    return " ".join(line[4].strip() for line in lines)


def inspect_page(page):
    # PyMuPDF returns text and image boxes in unrotated coordinates; rotation_matrix
    # converts them to the page as displayed, with the origin at the top-left corner.
    to_displayed = page.rotation_matrix

    words = []
    for x0, y0, x1, y1, text, *_ in page.get_text("words"):
        box = pymupdf.Rect(x0, y0, x1, y1) * to_displayed
        center = (box.tl + box.br) / 2
        words.append({"text": text, "x": round(center.x, 1), "y": round(center.y, 1),
                      "w": round(box.width, 1), "h": round(box.height, 1)})

    source = "text"
    if len(words) < MIN_EMBEDDED_WORDS:
        words = ocr_page_words(page)
        source = "ocr"

    images = []
    for img in page.get_images():
        for rect in page.get_image_rects(img[0]):
            box = (rect * to_displayed) & page.rect
            if box.is_empty:
                continue
            try:
                text = ocr_region(page, box)
            except Exception as exc:
                text = None
                print(f"  OCR failed on page {page.number + 1}: {exc}")
            center = (box.tl + box.br) / 2
            images.append({
                "x": round(center.x, 1),
                "y": round(center.y, 1),
                "width": round(box.width, 1),
                "height": round(box.height, 1),
                "text": text,
            })

    return {
        "page": page.number + 1,
        "label": page.get_label(),
        "width": page.rect.width,
        "height": page.rect.height,
        "rotation": page.rotation,
        "source": source,
        "words": words,
        "images": images,
    }


def reading_color(word):
    """Green for confident OCR readings, orange for doubtful, red for likely wrong.

    A lone character needs a high score to be trusted: arrows and line ends are often read
    as "1" or "I". A lone "1" that is not shaped like the digit is red whatever its score.
    Readings that may be truncated are magenta whatever their score.
    """
    if word.get("cut"):
        return (0.8, 0, 0.8)
    conf = word["conf"]
    if word.get("doubtful") or conf < 70 or (len(word["text"]) == 1 and conf < 90):
        return (0.85, 0, 0)
    if conf < 90:
        return (0.9, 0.5, 0)
    return (0, 0.55, 0)


def write_overlay(pdf_path, pages, out_path):
    """Copy the PDF and, on OCR pages, box each piece of text and print what was read above it."""
    doc = pymupdf.open(pdf_path)
    for page, info in zip(doc, pages):
        if info["source"] != "ocr":
            continue
        # Drawing happens in unrotated coordinates, the reverse of what inspect_page did.
        to_unrotated = page.derotation_matrix
        shape = page.new_shape()  # one shape per page: committing each box separately is very slow
        for word in info["words"]:
            box = pymupdf.Rect(word["x"] - word["w"] / 2, word["y"] - word["h"] / 2,
                               word["x"] + word["w"] / 2, word["y"] + word["h"] / 2)
            color = reading_color(word)
            shape.draw_rect(box * to_unrotated)
            shape.finish(color=color, width=0.4)
            shape.insert_text(pymupdf.Point(box.x0, box.y0 - 1) * to_unrotated, word["text"],
                              fontsize=max(4, min(word["h"] * 0.7, 9)), color=color,
                              rotate=page.rotation)
        shape.commit()
    doc.save(out_path, garbage=3, deflate=True)
    doc.close()


def main():
    pdf_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PDF

    doc = pymupdf.open(pdf_path)
    result = {
        "fichier": pdf_path.name,
        "toc": doc.get_toc(),
        "pages": [inspect_page(page) for page in doc],
    }
    doc.close()

    for page in result["pages"]:
        titles = " | ".join(img["text"] for img in page["images"] if img["text"])
        print(f"page {page['page']:>3}  label={page['label']!r:<10} {len(page['words']):>5} words ({page['source']})  "
              f"{len(page['images'])} images  {titles}")

    OUT_DIR.mkdir(exist_ok=True)
    out_path = OUT_DIR / f"{pdf_path.stem}.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf8")
    print(f"{len(result['toc'])} bookmarks, {len(result['pages'])} pages -> {out_path}")

    if any(page["source"] == "ocr" for page in result["pages"]):
        overlay_path = OUT_DIR / f"{pdf_path.stem}_overlay.pdf"
        write_overlay(pdf_path, result["pages"], overlay_path)
        print(f"OCR check (green = confident, orange = doubtful, red = likely wrong, magenta = may be truncated) -> {overlay_path}")


if __name__ == "__main__":
    main()
