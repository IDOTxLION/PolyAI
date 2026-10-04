"""Tests of the extraction helpers, on synthetic lines and shapes: no project document is needed.

python -m unittest test_extraction
"""

import unittest

import numpy as np
import pymupdf

import atelier_columns
import atelier_details
import beams
import extract_project
import foundations
import grid
import ocr_review
import plan_columns
import reading
import slabs
import start
import walls


def line(x, y, text, width=60, height=8):
    """A line of text with its top-left corner at (x, y)."""
    return pymupdf.Rect(x, y, x + width, y + height), text


class TestLevels(unittest.TestCase):
    def test_level_key_is_shared_by_plan_titles_and_shop_labels(self):
        self.assertEqual(plan_columns.level_key("PLAN DES COLONNES - REZ-DE-CHAUSSÉE"), "RDC")
        garbled = r"S-502 - PLAN DES COLONNES - REZ-DE-CHAUSS\311E\000"  # a page label left with its octal escapes
        self.assertEqual(plan_columns.level_key(plan_columns.clean_label(garbled)), "RDC")
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
        """A synthetic detail: its name at the left, its bars to the right, its spacings far right."""
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


class TestReading(unittest.TestCase):
    def test_plan_bars_reads_counts_and_spacings(self):
        (bars,) = reading.plan_bars("4-25M +GOUJ.")
        self.assertEqual((bars.quantite, bars.diametre, bars.espacement_mm), (4, "25M", None))
        (mat,) = reading.plan_bars('RANG 1: 20M@12" c/c', "BAS")
        self.assertEqual((mat.repere, mat.diametre, mat.quantite, mat.espacement_mm), ("BAS", "20M", None, 305))
        self.assertEqual(reading.plan_bars("15M @300 c/c")[0].espacement_mm, 300)
        self.assertEqual(reading.plan_bars("NIVEAU 2"), [])

    def test_shop_bar_keeps_the_keyword_in_front_of_the_mark(self):
        key, bar = reading.shop_bar("LONG: 9 20M 20M9-06")
        self.assertEqual((key, bar.repere, bar.quantite, bar.diametre, bar.espacement_mm), ("LONG", "LONG 20M9-06", 9, "20M", None))
        key, bar = reading.shop_bar('12 10M 10MET12X20 @7 1/2"c.c.')
        self.assertEqual((key, bar.repere, bar.quantite, bar.espacement_mm), (None, "10MET12X20", 12, 190))
        self.assertIsNone(reading.shop_bar("COLONNE A-6, 16X24:"))

    def test_neighbouring_views_are_split_at_the_widest_empty_stretch(self):
        titles = [line(100, 200, "P1 - 400x600", width=60), line(500, 200, "P2 - 400x600", width=60)]
        bars = [line(x, 100, "3-25M", width=20) for x in (60, 150, 240, 330)] + [line(470, 100, "2-20M", width=20), line(560, 100, "2-20M", width=20)]
        shared = reading.share_by_title(bars, titles)
        self.assertEqual({index: len(found) for index, found in shared.items()}, {0: 4, 1: 2})  # the first view is the wider one
        self.assertEqual(reading.share_by_title([line(100, 300, "3-25M")], titles), {})  # under the titles: in no view

    def test_a_line_goes_to_the_nearest_title_under_it(self):
        titles = [line(100, 400, "RADIER #1", width=150), line(900, 650, "RADIER #2", width=150)]
        shared = reading.share_by_title_under([line(820, 300, '20M@12"'), line(150, 100, '20M@6"')], titles)
        self.assertEqual({index: [text for _, text in found] for index, found in shared.items()}, {1: ['20M@12"'], 0: ['20M@6"']})

    def test_stacks_gather_lines_written_one_under_the_other(self):
        note = [line(50, 100 + 9 * i, text) for i, text in enumerate(["EMPATTEMENT TYPE-A:", "LONG: 9 20M 20M9-06", "TRAN: 9 20M 20M9-06"])]
        blocks = reading.stacks(note + [line(400, 100, "LONG: 7 20M 20M8-00")])
        self.assertEqual(sorted(len(block) for block in blocks), [1, 3])

    def test_runs_merge_the_strokes_of_one_beam(self):
        strokes = [(100, 300, 50), (290, 480, 52), (600, 700, 50), (100, 700, 400)]
        self.assertEqual(reading.runs(strokes, 0, 100), [(100, 480), (600, 700)])


class TestGrid(unittest.TestCase):
    def bubbles(self):
        top = [line(100 + 80 * i, 20, label, width=8) for i, label in enumerate("ABCDE")]
        left = [line(20, 100 + 80 * i, str(i + 1), width=8) for i in range(4)]
        return top + left

    def test_a_row_of_bubbles_is_told_by_its_order(self):
        quantities = [line(100 + 60 * i, 500, text, width=8) for i, text in enumerate(["8", "4", "12", "4", "6", "2"])]  # further out, but in no order
        vertical, horizontal = grid.find_grid(self.bubbles() + quantities)
        self.assertEqual(list(vertical.values()), list("ABCDE"))
        self.assertEqual(list(horizontal.values()), ["1", "2", "3", "4"])
        self.assertEqual(grid.crossing(270, 190, vertical, horizontal), "C-2")

    def test_a_shop_drawing_is_laid_over_the_plan_through_shared_lines(self):
        plan, _ = grid.find_grid(self.bubbles())
        half = {position / 2 + 30: label for position, label in plan.items() if label != "C"}  # another scale, a line missing
        to_plan = grid.mapping(half, plan)
        self.assertAlmostEqual(to_plan(min(half)), min(plan))
        self.assertAlmostEqual(to_plan((184 / 2 + 30 + 264 / 2 + 30) / 2), (184 + 264) / 2)  # halfway between B and C... D
        self.assertIsNone(grid.mapping({10.0: "A"}, plan))


class TestFoundations(unittest.TestCase):
    def test_the_schedule_gives_each_type_its_bars(self):
        header = [line(x, 100, text, width=40) for x, text in ((0, "TYPE"), (60, "LONGUEUR"), (120, "LARGEUR"), (180, "ÉPAISSEUR"), (240, "ARM. LONG."), (310, "ARM. TRANS."))]
        rows = [line(0, 112, "TYPE A", width=30), line(245, 112, "12-20M", width=30), line(315, 112, "10-20M", width=30),
                line(0, 130, "TYPE B", width=30), line(245, 130, "8-25M", width=30), line(240, 140, "+ ÉP. 15M@300", width=50), line(315, 130, "8-25M", width=30),
                line(420, 112, "PROJET", width=40)]  # beside the table: not a cell
        schedule = foundations.find_schedule(header + rows)
        self.assertEqual([(a.repere, a.quantite, a.diametre) for a in schedule["A"]], [("LONG", 12, "20M"), ("TRAN", 10, "20M")])
        self.assertEqual([(a.repere, a.quantite, a.espacement_mm) for a in schedule["B"]], [("LONG", 8, None), ("LONG", None, 300), ("TRAN", 8, None)])

    def test_a_letter_takes_the_column_on_its_usual_side(self):
        columns = [pymupdf.Rect(x - 6, y - 9, x + 6, y + 9) for x, y in ((100, 100), (300, 100), (500, 100), (530, 160))]
        letters = [pymupdf.Point(140, 140), pymupdf.Point(340, 140), pymupdf.Point(540, 140)]
        side = foundations.letter_side(letters, columns)
        self.assertEqual(side, (False, False))  # each column stands up and left of its letter
        self.assertEqual(foundations.column_of(letters[2], columns, side), columns[2])  # not the nearer one under it

    def test_a_raft_view_is_titled_larger_than_the_notes(self):
        lines = [(pymupdf.Rect(0, 0, 150, 17), "PLAN FONDATION - RADIER #2", 17), (pymupdf.Rect(0, 50, 60, 58), "VOIR RADIER #2", 8)]
        lines += [(pymupdf.Rect(0, 100 + 10 * i, 60, 108 + 10 * i), 'RANG 1: 20M@12" c/c', 8) for i in range(5)]
        self.assertEqual([number for _, number in foundations.raft_views(lines)], ["2"])


class TestBeams(unittest.TestCase):
    def test_a_title_gives_the_span_its_name(self):
        lines = [line(0, 0, 'P101 - 16" x 24"'), line(0, 20, "P-12: 400x600"), line(0, 40, "S-300 - ÉLÉVATIONS"), line(0, 60, "3-25M")]
        self.assertEqual([name for _, name in beams.find_titles(lines)], ["P101", "P-12"])

    def test_spans_under_one_stroke_are_one_beam(self):
        doc = pymupdf.open()
        page = doc.new_page(width=1000, height=600)
        page.draw_line((100, 100), (520, 100))  # one continuous beam over two titles
        page.draw_line((700, 100), (900, 100))
        titles = [line(150, 200, "P101 - 400x600"), line(400, 200, "P102 - 400x600"), line(770, 200, "P103 - 400x600")]
        named = beams.find_titles(titles)
        self.assertEqual(beams.continuous_beams(page, named), [[0, 1], [2]])


class TestSlabs(unittest.TestCase):
    def test_a_label_takes_the_band_count_written_under_it(self):
        lines = [(pymupdf.Rect(100, 100, 150, 107), "8 15J9-06", False), (pymupdf.Rect(100, 108, 150, 115), "6 15J12-00", False),
                 (pymupdf.Rect(100, 116, 130, 123), "6 m.b.", False), (pymupdf.Rect(400, 100, 450, 107), "5 15M 12-06", False)]
        (group,) = slabs.label_groups(lines)
        _, upright, label, pile, band = group
        self.assertEqual((upright, label, pile, band), (False, (6, "15M", "15J12-00"), [(8, "15M", "15J9-06")], 6))

    def test_equal_counts_are_paired_before_nearer_ones(self):
        callouts = [(100, 100, True, 14, 6), (160, 100, True, 10, 4)]
        groups = [(120, 100, True, (10, 10), 4), (150, 100, True, (8, 14), 6)]
        self.assertEqual(slabs.pair_up(callouts, groups, reach=200), {1: 0, 0: 1})
        self.assertEqual(slabs.pair_up(callouts, [(120, 100, True, (9, 9), 4)], reach=200), {0: 0})  # no equal counts: the nearest

    def test_a_drawing_is_tried_on_the_sheets_of_its_level(self):
        views = {"S-600A": {"title": "S-600A - ARMATURE DU REZ-DE-CHAUSSÉE TRÉFOND"}, "S-600B": {"title": "S-600B - ARMATURE DU REZ-DE-CHAUSSÉE"},
                 "S-601": {"title": "S-601 - ARMATURE DU NIVEAU 2"}, "S-610": {"title": "S-610 - ARMATURE DU NIVEAU 9 @ 21"}}
        self.assertEqual(slabs.sheets_of_drawing("XX_DALLE NIV 2", views), ["S-601"])
        self.assertEqual(slabs.sheets_of_drawing("XX_DALLE-NIV-12", views), ["S-610"])
        self.assertEqual(slabs.sheets_of_drawing("XX_DALLE NIV RDC", views), ["S-600B", "S-600A"])
        self.assertEqual(slabs.sheets_of_drawing("XX_DALLE TRÉFOND", views), ["S-600A"])
        self.assertEqual(slabs.sheets_of_drawing("XX_NOTES", views), [])


class TestWalls(unittest.TestCase):
    def test_a_wall_is_named_by_its_title_or_by_the_letter_beside_it(self):
        lines = [(pymupdf.Rect(300, 500, 380, 514), "ÉLÉVATION", 13.5), (pymupdf.Rect(180, 490, 200, 512), "B", 22.5),
                 (pymupdf.Rect(900, 500, 1100, 514), "ÉLÉVATION - MUR C - S.S2 @ NIV. 10", 13.5), (pymupdf.Rect(0, 0, 60, 8), "VOIR ÉLÉVATION", 8.5)]
        self.assertEqual([name for _, name in walls.find_titles(lines)], ["B", "C"])

    def test_a_tag_stands_on_the_storey_labelled_under_it(self):
        levels = [(100, "N2"), (300, "RDC")]
        self.assertEqual(walls.storey_under(pymupdf.Rect(0, 150, 10, 160), levels), "RDC")
        self.assertEqual(walls.storey_under(pymupdf.Rect(0, 350, 10, 360), levels), "FDN")


class TestProject(unittest.TestCase):
    def test_a_drawing_is_filed_by_its_folder_then_by_its_name(self):
        from pathlib import Path

        self.assertEqual(extract_project.kind_of(Path("p/DA/Refends/XX_CONCENTRATION COLONNES - RDC.pdf")), "mur")
        self.assertEqual(extract_project.kind_of(Path("p/DA/Semelles et radiers/XX_RADIERS 1@2.pdf")), "fondation")
        self.assertEqual(extract_project.kind_of(Path("p/DA/XX_POUTRES RDC.pdf")), "poutre")
        self.assertEqual(extract_project.kind_of(Path("p/DA/Dalles/XX_NIV 22.pdf")), "dalle")
        self.assertIsNone(extract_project.kind_of(Path("p/DA/XX_NOTES.pdf")))


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
