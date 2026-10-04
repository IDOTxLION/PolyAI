"""Tests of the extraction helpers, on invented lines and shapes: no project document is needed.

python -m unittest test_extraction
"""

import unittest

import numpy as np
import pymupdf

import atelier_columns
import atelier_details
import ocr_review
import plan_columns
import start


def line(x, y, text, width=60, height=8):
    """A line of text with its top-left corner at (x, y)."""
    return pymupdf.Rect(x, y, x + width, y + height), text


class TestLevels(unittest.TestCase):
    def test_level_key_is_shared_by_plan_titles_and_shop_labels(self):
        self.assertEqual(plan_columns.level_key("PLAN DES COLONNES - REZ-DE-CHAUSSÉE"), "RDC")
        self.assertEqual(plan_columns.level_key("PLAN DES COLONNES - NIVEAU 12"), "N12")
        self.assertEqual(plan_columns.level_key("NIV. 3"), "N3")
        self.assertEqual(plan_columns.level_key("PLAN DES COLONNES - SOUS-SOL"), "SS")
        self.assertEqual(plan_columns.level_key("PLAN DES COLONNES - SOUS-SOL S2"), "SS2")
        self.assertIsNone(plan_columns.level_key("NOTES GÉNÉRALES"))

    def test_lift_key_reads_the_storey_from_a_file_name(self):
        self.assertEqual(atelier_details.lift_key("XX_COLONNE-NIV-2@3"), "N2")
        self.assertEqual(atelier_details.lift_key("XX_COLONNES NIV 10"), "N10")
        self.assertEqual(atelier_details.lift_key("XX_COLONNE-NIV-SS1@RDC"), "SS1")
        self.assertEqual(atelier_details.lift_key("XX_COLONNE-NIV-RDC@2"), "RDC")
        self.assertEqual(atelier_details.lift_key("XX_COLONNE-NIV-FDN@SS2"), "FDN")
        self.assertIsNone(atelier_details.lift_key("XX_COLONNES Partie 1"))


class TestPlan(unittest.TestCase):
    def test_parse_tag_reads_bars_then_ties(self):
        bars, ties = plan_columns.parse_tag('COL. 16"x24"\nARM.: 4-25M +GOUJ.\nLIG.: 10M@6" c/c\nBÉTON: 35MPa')
        self.assertEqual((bars.quantite, bars.diametre, bars.espacement_mm), (4, "25M", None))
        self.assertEqual((ties.quantite, ties.diametre, ties.espacement_mm), (None, "10M", 152))

    def test_parse_tag_takes_a_spacing_without_inch_sign_as_millimetres(self):
        _, ties = plan_columns.parse_tag("COL. 500x600\nARM.: 8-25M\nLIG.: 10M@300 c/c")
        self.assertEqual(ties.espacement_mm, 300)

    def test_a_column_is_named_after_its_grid_lines(self):
        vertical, horizontal = {100.0: "1", 300.0: "2", 500.0: "3"}, {50.0: "A", 250.0: "B"}
        self.assertEqual(plan_columns.grid_name(pymupdf.Rect(295, 45, 305, 55), vertical, horizontal), ("A-2", False))
        # two fifths of the way from line 1 to line 2: a name worked out between the lines
        self.assertEqual(plan_columns.grid_name(pymupdf.Rect(175, 245, 185, 255), vertical, horizontal), ("B-1.4", True))


class TestStoreyTables(unittest.TestCase):
    def test_parse_bar_line_of_a_storey_table(self):
        bars = atelier_columns.parse_bar_line("VERT: 4 25M 25M12-06")
        self.assertEqual((bars.quantite, bars.diametre, bars.repere, bars.longueur_mm), (4, "25M", "25M12-06", 3810))
        ties = atelier_columns.parse_bar_line('ÉTRI: 14 10M 10MT16X24 @6"')
        self.assertEqual((ties.quantite, ties.diametre, ties.espacement_mm), (14, "10M", 152))
        self.assertIsNone(atelier_columns.parse_bar_line("NIVEAU 2"))


class TestDetails(unittest.TestCase):
    def detail(self, x, y, name, vertical, zones):
        """An invented detail: its name at the left, its bars to the right, its spacings far right."""
        return [line(x, y, name, width=28), line(x + 20, y + 300, f"VERT: {vertical}", width=110),
                line(x + 25, y + 240, "ET.:14 10M 10MT1", width=90), line(x + 135, y + 240, zones, width=12, height=60)]

    def test_each_detail_gets_its_own_lines_even_when_they_lie_nearer_the_next_name(self):
        lines = (self.detail(80, 100, "A/1", "8 25M 3450", "14@300") + self.detail(290, 100, "A/2", "4 20M 3450", "3@150 + 8@300")
                 + self.detail(500, 100, "B/1", "6 25M 3450", "14@400") + self.detail(80, 490, "B/2", "4 25M 3450", "14@300")
                 + self.detail(290, 490, "C/1", "4 25M 3450", "14@300") + [line(700, 300, "Z-9", width=28)])  # a lone name: not a title
        names = atelier_details.find_names(lines)
        self.assertEqual([found for _, found in names], [["A-1"], ["A-2"], ["B-1"], ["B-2"], ["C-1"]])
        shared = atelier_details.share_lines(lines, names)
        read = {names[i][1][0]: atelier_details.parse_detail(found) for i, found in shared.items()}
        self.assertEqual({name: sorted((a.diametre, a.quantite, a.espacement_mm) for a in bars) for name, bars in read.items()}, {
            "A-1": [("10M", 14, 300), ("25M", 8, None)],
            "A-2": [("10M", 8, 300), ("10M", 14, 150), ("20M", 4, None)],
            "B-1": [("10M", 14, 400), ("25M", 6, None)],
            "B-2": [("10M", 14, 300), ("25M", 4, None)],
            "C-1": [("10M", 14, 300), ("25M", 4, None)],
        })

    def test_a_title_can_name_several_columns_over_several_lines(self):
        lines = [line(100, 50, "C.5-12, D.5-12", width=80), line(100, 60, "E-12", width=30), line(400, 50, "A-1", width=30),
                 line(110, 300, "8 25M 25M3450"), line(410, 300, "4 20M 20M3450")]
        names = atelier_details.find_names(lines)
        self.assertEqual([found for _, found in names], [["C.5-12", "D.5-12", "E-12"], ["A-1"]])
        self.assertEqual(atelier_details.names_in("VOIR C-12"), [])

    def test_parse_detail_reads_a_tie_with_its_spacing_on_the_same_line(self):
        vertical, tie = atelier_details.parse_detail([line(0, 0, "8 25M 25M3450"), line(0, 20, "2x7 10M 10MT1 @300")])
        self.assertEqual((vertical.quantite, vertical.diametre, vertical.repere), (8, "25M", "25M3450"))
        self.assertEqual((tie.quantite, tie.diametre, tie.espacement_mm), (7, "10M", 300))  # 7 in each of 2 columns

    def test_dowels_keep_their_word_in_the_mark(self):
        vertical, dowels = atelier_details.parse_detail([line(0, 0, "VERT: 4 25M 25M3450"), line(0, 20, "GOUJ 4 25M 1200")])
        self.assertEqual((vertical.repere, dowels.repere, dowels.longueur_mm), ("25M3450", "GOUJ", 1200))

    def test_small_spacings_are_inches(self):
        self.assertEqual(atelier_details.millimetres(6, None), 152)
        self.assertEqual(atelier_details.millimetres(300, None), 300)


class TestOcr(unittest.TestCase):
    def reading(self, score, x0, y0, x1, y1, text, vertical=False, cut=False):
        return (score, cut, x0, y0, x1, y1, text, vertical)

    def test_a_longer_reading_of_the_same_text_wins_over_a_shorter_surer_one(self):
        short = self.reading(1.00, 0, 0, 100, 40, "QTE :", vertical=True)
        whole = self.reading(0.95, 0, 0, 140, 40, "QTE : 1")
        self.assertEqual([r[6] for r in start.drop_duplicates([short, whole])], ["QTE : 1"])

    def test_a_reading_of_other_text_does_not_replace_the_kept_one(self):
        kept = self.reading(1.00, 0, 0, 100, 40, "25M")
        other = self.reading(0.90, 0, 0, 140, 40, "Z5M 1")
        self.assertEqual([r[6] for r in start.drop_duplicates([kept, other])], ["25M"])

    def glyph(self, *strokes):
        """A grey-level crop with dark rectangles (top, bottom, left, right) on white."""
        gray = np.full((40, 30), 255.0)
        for top, bottom, left, right in strokes:
            gray[top:bottom, left:right] = 0
        return gray

    def test_a_one_has_a_hook_on_its_top_left_only(self):
        stem = (4, 36, 16, 20)
        self.assertTrue(start.looks_like_one(self.glyph(stem, (6, 10, 8, 16))))  # stem and hook
        self.assertFalse(start.looks_like_one(self.glyph(stem)))  # a bare line
        self.assertFalse(start.looks_like_one(self.glyph(stem, (6, 14, 8, 28))))  # an arrowhead: ink on both sides
        self.assertTrue(start.looks_like_one(self.glyph(stem, (6, 10, 8, 16), (0, 2, 0, 30))))  # a rule above the digit

    def test_review_scoring_counts_the_numbers_read_whole(self):
        counts = ocr_review.count_numbers("ETRI: 11 10M 10MT16X24 @6", "ETRI: 14 10M 10MT16X24 @6")
        self.assertEqual((counts["read"], counts["misread"]), (3, 1))


if __name__ == "__main__":
    unittest.main()
