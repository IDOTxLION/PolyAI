"""Rapport PDF par feuillet de plan.

Structure :
- FeuilletRapport : les verdicts d'un feuillet, avec les totaux.
- Rapport : regroupe les verdicts, puis ecrit le PDF.

Une conformite est un verdict "conforme".
Tout le reste (non_conforme, manquant, ajoute) est une non-conformite
et apparait dans le detail, avec page, x, y pour retrouver l'element.
"""

from __future__ import annotations

import unittest
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

try:
    from l2c.appariement import Paire
    from l2c.comparaison import Comparateur, Ecart, Verdict
    from l2c.modeles import Barre, Element, Etiquette
except ModuleNotFoundError:
    from appariement import Paire
    from comparaison import Comparateur, Ecart, Verdict
    from modeles import Barre, Element, Etiquette

COLONNES = (
    "Etiquette",
    "Nom plan",
    "Nom atelier",
    "Repere",
    "Diametre",
    "Quantite",
    "Espacement mm",
    "Longueur mm",
    "Statut",
)
CHAMPS_BARRE = ("repere", "diametre", "quantite", "espacement_mm", "longueur_mm")


@dataclass
class FeuilletRapport:
    """Resume d'un feuillet de plan."""

    feuillet: str
    verdicts: list[Verdict]

    @property
    def nb_conformes(self) -> int:
        return sum(1 for verdict in self.verdicts if verdict.statut == "conforme")

    @property
    def nb_non_conformes(self) -> int:
        return len(self.verdicts) - self.nb_conformes

    def a_signaler(self) -> list[Verdict]:
        return [verdict for verdict in self.verdicts if verdict.statut != "conforme"]

    def compte(self, statut: str) -> int:
        return sum(1 for verdict in self.verdicts if verdict.statut == statut)

    def types(self) -> list[str]:
        """Les types d'element du feuillet (fondation, poutre, mur, colonne, dalle)."""
        cartes = [verdict.paire.plan or verdict.paire.atelier for verdict in self.verdicts]
        return sorted({carte.type_element for carte in cartes if carte is not None and carte.type_element})

    def sans_atelier(self) -> bool:
        """Vrai si aucun dessin d'atelier ne couvre ce feuillet : tout y est manquant."""
        return bool(self.verdicts) and all(verdict.statut == "manquant" for verdict in self.verdicts)


class Rapport:
    """Construit le rapport a partir des verdicts du Comparateur."""

    def __init__(self, verdicts: list[Verdict]) -> None:
        self.verdicts = verdicts

    def par_feuillet(self) -> list[FeuilletRapport]:
        """Groupe par feuillet du plan, sinon celui de l'atelier."""
        groupes: dict[str, list[Verdict]] = defaultdict(list)
        for verdict in self.verdicts:
            groupes[self._feuillet(verdict)].append(verdict)
        return [
            FeuilletRapport(feuillet=feuillet, verdicts=groupes[feuillet])
            for feuillet in sorted(groupes)
        ]

    def ecrire_pdf(self, chemin: Path) -> None:
        """Ecrit le PDF : totaux par feuillet, puis un tableau des barres."""
        chemin.parent.mkdir(parents=True, exist_ok=True)
        styles = getSampleStyleSheet()
        titre = ParagraphStyle(
            "TitreRapport",
            parent=styles["Title"],
            fontName="Helvetica",
            fontSize=16,
            spaceAfter=12,
        )
        feuillet_style = ParagraphStyle(
            "FeuilletRapport",
            parent=styles["Heading2"],
            fontName="Helvetica",
            fontSize=12,
            spaceBefore=14,
            spaceAfter=6,
        )
        corps = ParagraphStyle(
            "CorpsRapport",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=9,
            leading=12,
        )
        cellule = ParagraphStyle(
            "CelluleRapport",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=7,
            leading=9,
        )
        story = [Paragraph("Rapport de conformite des armatures", titre)]
        story.append(
            Paragraph(
                "Seuls les elements a signaler sont detailles. Chaque champ d'armature"
                " affiche la valeur du plan, puis celle de l'atelier ; les valeurs en"
                " ecart sont en rouge.",
                corps,
            )
        )
        feuillets = self.par_feuillet()
        if not feuillets:
            story.append(Paragraph("Aucun element a rapporter.", corps))
        for bloc in feuillets:
            types = f" ({', '.join(bloc.types())})" if bloc.types() else ""
            story.append(Paragraph(escape(f"Feuillet {bloc.feuillet}{types}"), feuillet_style))
            story.append(
                Paragraph(
                    f"Conformites : {bloc.nb_conformes}"
                    f" &nbsp;&nbsp; Non-conformites : {bloc.nb_non_conformes}"
                    f" (non conformes : {bloc.compte('non_conforme')},"
                    f" manquants a l'atelier : {bloc.compte('manquant')},"
                    f" ajoutes a l'atelier : {bloc.compte('ajoute')})",
                    corps,
                )
            )
            if bloc.sans_atelier():
                story.append(
                    Paragraph(
                        "Aucun dessin d'atelier lu pour ce feuillet : tous ses elements sont manquants.",
                        corps,
                    )
                )
            elif bloc.a_signaler():
                story.append(Spacer(1, 0.08 * inch))
                story.append(self._tableau(bloc, cellule))
        SimpleDocTemplate(
            str(chemin),
            pagesize=landscape(letter),
            title="Rapport de conformite des armatures",
            leftMargin=0.4 * inch,
            rightMargin=0.4 * inch,
        ).build(story)

    def _tableau(self, bloc: FeuilletRapport, style: ParagraphStyle) -> Table:
        lignes = [[Paragraph(escape(colonne), style) for colonne in COLONNES]]
        lignes_en_ecart: list[int] = []
        for verdict in bloc.a_signaler():
            for cellules, en_ecart in self._lignes(verdict):
                if en_ecart:
                    lignes_en_ecart.append(len(lignes))
                lignes.append([Paragraph(cellule, style) for cellule in cellules])
        tableau = Table(
            lignes,
            colWidths=[60, 118, 118, 62, 58, 52, 72, 62, 58],
            repeatRows=1,
        )
        commandes = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.9, 0.9, 0.9)),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.Color(0.7, 0.7, 0.7)),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
        for index in lignes_en_ecart:
            commandes.append(
                ("BACKGROUND", (0, index), (-1, index), colors.Color(1, 0.93, 0.93))
            )
        tableau.setStyle(TableStyle(commandes))
        return tableau

    def _lignes(self, verdict: Verdict) -> list[tuple[list[str], bool]]:
        """Une ligne par barre : ses cellules, et si la ligne est a signaler.

        Element apparie : seules les barres en ecart sont a signaler, leurs
        valeurs en ecart en rouge. Element manquant ou ajoute : toutes ses barres.
        """
        plan = verdict.paire.plan
        atelier = verdict.paire.atelier
        seul = plan is None or atelier is None
        barres = Comparateur().juger_barres(
            list(plan.armature) if plan is not None else [],
            list(atelier.armature) if atelier is not None else [],
            (plan or atelier).type_element if seul is not None and (plan or atelier) else "colonne",
        )
        if not barres:
            barres = [(None, None, [])]
        lignes: list[tuple[list[str], bool]] = []
        for barre_plan, barre_atelier, ecarts in barres:
            champs = set() if seul else {ecart.champ for ecart in ecarts}
            cellules = [
                escape(self._etiquette(verdict)),
                escape(self._origine(plan)),
                escape(self._origine(atelier)),
                *[
                    self._champ(
                        barre_plan,
                        barre_atelier,
                        champ,
                        champ in champs or "barre" in champs,
                    )
                    for champ in CHAMPS_BARRE
                ],
                escape(verdict.statut),
            ]
            lignes.append((cellules, seul or bool(champs)))
        return lignes

    def _etiquette(self, verdict: Verdict) -> str:
        carte = verdict.paire.plan or verdict.paire.atelier
        nom = carte.element if carte is not None else verdict.paire.etiquette.element
        return f"{self._feuillet(verdict)} {nom}"

    def _origine(self, carte: Element | None) -> str:
        """Ou retrouver l'element : fichier, page, x, y."""
        if carte is None:
            return "x"
        return f"{carte.fichier} | page {carte.page}, x {carte.x}, y {carte.y}"

    def _champ(
        self,
        barre_plan: Barre | None,
        barre_atelier: Barre | None,
        champ: str,
        en_ecart: bool = False,
    ) -> str:
        texte = escape(
            f"{self._valeur(barre_plan, champ)} | {self._valeur(barre_atelier, champ)}"
        )
        if en_ecart:
            return f'<font color="#c00000"><b>{texte}</b></font>'
        return texte

    def _valeur(self, barre: Barre | None, champ: str) -> str:
        if barre is None:
            return "x"
        valeur = getattr(barre, champ)
        if valeur is None or valeur == "":
            return "x"
        return str(valeur)

    def _feuillet(self, verdict: Verdict) -> str:
        carte = verdict.paire.plan or verdict.paire.atelier
        if carte is not None and carte.feuillet:
            return carte.feuillet
        return verdict.paire.etiquette.feuillet

def _element(
    source: str,
    feuillet: str,
    element: str,
    *,
    page: int = 5,
    x: float = 188.2,
    y: float = 502.0,
    armature: list[Barre] | None = None,
) -> Element:
    return Element(
        id=f"{feuillet}_{element}_{source}",
        source=source,
        fichier=f"{source}.pdf",
        feuillet=feuillet,
        page=page,
        x=x,
        y=y,
        type_element="fondation",
        element=element,
        armature=armature or [],
    )


def _verdict(
    statut: str,
    *,
    feuillet: str = "S-500",
    element: str = "L-13",
    ecarts: list[Ecart] | None = None,
    avec_plan: bool = True,
    avec_atelier: bool = True,
    armature_plan: list[Barre] | None = None,
    armature_atelier: list[Barre] | None = None,
) -> Verdict:
    plan = (
        _element("plan", feuillet, element, armature=armature_plan) if avec_plan else None
    )
    atelier = (
        _element("atelier", feuillet, element, page=1, x=176.0, y=490.0, armature=armature_atelier)
        if avec_atelier
        else None
    )
    statut_paire = "paire"
    if plan is None:
        statut_paire = "ajoute"
    elif atelier is None:
        statut_paire = "manquant"
    return Verdict(
        paire=Paire(
            etiquette=Etiquette.depuis(feuillet, element),
            plan=plan,
            atelier=atelier,
            statut=statut_paire,
        ),
        statut=statut,
        ecarts=ecarts or [],
    )


class TestRapport(unittest.TestCase):
    def test_regroupe_par_feuillet_et_compte_les_non_conformites(self) -> None:
        rapport = Rapport(
            [
                _verdict("conforme", feuillet="S-500", element="C-12"),
                _verdict(
                    "non_conforme",
                    feuillet="S-100",
                    element="L-13",
                    ecarts=[Ecart("quantite", 9, 11)],
                ),
                _verdict("manquant", feuillet="S-100", element="D-22", avec_atelier=False),
            ]
        )
        blocs = {bloc.feuillet: bloc for bloc in rapport.par_feuillet()}
        self.assertEqual(blocs["S-500"].nb_conformes, 1)
        self.assertEqual(blocs["S-500"].nb_non_conformes, 0)
        self.assertEqual(blocs["S-100"].nb_conformes, 0)
        self.assertEqual(blocs["S-100"].nb_non_conformes, 2)

    def test_ajoute_sans_plan_reste_sur_le_feuillet_atelier(self) -> None:
        rapport = Rapport(
            [_verdict("ajoute", feuillet="S-400", element="C-99", avec_plan=False)]
        )
        bloc = rapport.par_feuillet()[0]
        self.assertEqual(bloc.feuillet, "S-400")
        self.assertEqual(bloc.nb_non_conformes, 1)

    def test_seule_la_valeur_en_ecart_est_marquee(self) -> None:
        verdict = _verdict(
            "non_conforme",
            armature_plan=[Barre("", "25M", 9, None, None), Barre("", "10M", None, 152, None)],
            armature_atelier=[Barre("A", "25M", 11, None, 2400), Barre("B", "10M", 14, 152, None)],
        )
        (barres, en_ecart), (etriers, etriers_en_ecart) = Rapport([verdict])._lignes(verdict)
        marquees = [cellule for cellule in barres if "<b>" in cellule]
        self.assertTrue(en_ecart)
        self.assertEqual(len(marquees), 1)
        self.assertIn("9 | 11", marquees[0])
        self.assertFalse(etriers_en_ecart)
        self.assertFalse(any("<b>" in cellule for cellule in etriers))

    def test_les_elements_conformes_ne_sont_pas_detailles(self) -> None:
        rapport = Rapport(
            [
                _verdict("conforme", element="C-12"),
                _verdict("manquant", element="D-22", avec_atelier=False),
            ]
        )
        tableau = rapport._tableau(rapport.par_feuillet()[0], getSampleStyleSheet()["Normal"])
        self.assertEqual(tableau._nrows, 2)  # l'entete et l'element manquant

    def test_feuillet_sans_dessin_d_atelier(self) -> None:
        rapport = Rapport(
            [
                _verdict("manquant", element="C-12", avec_atelier=False),
                _verdict("manquant", element="D-22", avec_atelier=False),
                _verdict("conforme", feuillet="S-501", element="C-12"),
            ]
        )
        blocs = {bloc.feuillet: bloc for bloc in rapport.par_feuillet()}
        self.assertTrue(blocs["S-500"].sans_atelier())
        self.assertFalse(blocs["S-501"].sans_atelier())

    def test_pdf_contient_le_feuillet_et_l_ecart(self) -> None:
        from tempfile import TemporaryDirectory

        barre_plan = Barre("L13-1", "25M", 9, None, 2400)
        barre_atelier = Barre("L13-1", "25M", 11, None, 2400)
        rapport = Rapport(
            [
                _verdict(
                    "non_conforme",
                    feuillet="S-100",
                    element="L-13",
                    ecarts=[Ecart("quantite", 9, 11)],
                    armature_plan=[barre_plan],
                    armature_atelier=[barre_atelier],
                )
            ]
        )
        with TemporaryDirectory() as dossier:
            chemin = Path(dossier) / "rapport.pdf"
            rapport.ecrire_pdf(chemin)
            self.assertTrue(chemin.is_file())
            self.assertGreater(chemin.stat().st_size, 500)
            import subprocess

            texte = subprocess.check_output(["pdftotext", str(chemin), "-"], text=True)
        self.assertIn("S-100", texte)
        self.assertIn("L-13", texte)
        self.assertIn("Quantite", texte)
        self.assertIn("Nom plan", texte)
        self.assertIn("Nom atelier", texte)
        self.assertIn("9", texte)
        self.assertIn("11", texte)
        self.assertIn("page 5", texte)
        self.assertIn("plan.pdf", texte)
        self.assertIn("atelier.pdf", texte)


if __name__ == "__main__":
    unittest.main()
