"""Comparaison d'armature.

On part d'une Paire deja formee (meme etiquette). On ouvre ensuite
les listes `armature` et on compare barre par barre :
quantite, diametre, espacement_mm, longueur_mm.

Le plan resume l'armature (ex. 4-25M et 10M@6") alors que l'atelier
liste chaque ligne de barres : les deux listes n'ont ni la meme longueur
ni le meme ordre. On aligne donc les barres par nature (comptees ou
espacees) et par diametre, et un champ que le plan ne donne pas n'est
pas compare.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from typing import Any

try:
    from l2c.appariement import Paire
    from l2c.modeles import Barre, Element, Etiquette
except ModuleNotFoundError:
    from appariement import Paire
    from modeles import Barre, Element, Etiquette

CHAMPS = ("quantite", "diametre", "espacement_mm", "longueur_mm")


def _diametre(barre: Barre) -> str:
    return str(barre.diametre).strip().upper()


def _taille(barre: Barre) -> float:
    """Diametre en chiffres, pour trouver le plus proche : '25M' -> 25."""
    chiffres = "".join(car for car in str(barre.diametre) if car.isdigit())
    return float(chiffres) if chiffres else 0.0


@dataclass
class Ecart:
    """Un attribut different entre plan et atelier pour une barre."""

    champ: str
    valeur_plan: Any
    valeur_atelier: Any


@dataclass
class Verdict:
    """Jugement sur un element : conforme, NC, manquant ou ajoute."""

    paire: Paire
    statut: str  # "conforme" | "non_conforme" | "manquant" | "ajoute"
    ecarts: list[Ecart] = field(default_factory=list)


class Comparateur:
    """Compare l'acier des paires produites par Apparieur."""

    def comparer_paires(self, paires: list[Paire]) -> list[Verdict]:
        """Parcourt toutes les paires et rend un verdict chacune."""
        return [self.comparer_paire(paire) for paire in paires]

    def comparer_paire(self, paire: Paire) -> Verdict:
        """Si plan ou atelier manque, pas de lecture d'armature."""
        if paire.plan is None or paire.statut == "ajoute":
            return Verdict(paire=paire, statut="ajoute")
        if paire.atelier is None or paire.statut == "manquant":
            return Verdict(paire=paire, statut="manquant")

        armature_plan = list(paire.plan.armature)
        armature_atelier = list(paire.atelier.armature)
        ecarts: list[Ecart] = []
        for _, _, ecarts_barre in self.juger_barres(armature_plan, armature_atelier):
            ecarts.extend(ecarts_barre)

        statut = "non_conforme" if ecarts else "conforme"
        return Verdict(paire=paire, statut=statut, ecarts=ecarts)

    def juger_barres(
        self,
        armature_plan: list[Barre],
        armature_atelier: list[Barre],
    ) -> list[tuple[Barre | None, Barre | None, list[Ecart]]]:
        """Les barres alignees, chacune avec ses ecarts (liste vide = rien a signaler)."""
        jugees: list[tuple[Barre | None, Barre | None, list[Ecart]]] = []
        for barre_plan, barre_atelier in self.apparier_barres(
            armature_plan, armature_atelier
        ):
            if barre_plan is None and self.est_detail(barre_atelier, armature_plan):
                jugees.append((barre_plan, barre_atelier, []))
            else:
                ecarts = self.comparer_barre(barre_plan, barre_atelier)
                jugees.append((barre_plan, barre_atelier, ecarts))
        return jugees

    def apparier_barres(
        self,
        armature_plan: list[Barre],
        armature_atelier: list[Barre],
    ) -> list[tuple[Barre | None, Barre | None]]:
        """Aligne les barres par nature et par diametre. Pas de jointure sur `repere`.

        Barres comptees (sans espacement) : un groupe par diametre, quantites
        additionnees. Barres espacees : un groupe par diametre et espacement.

        Le diametre reste un champ a comparer : un groupe du plan sans jumeau
        est mis en face du groupe atelier le plus proche, donc l'ecart
        35M vs 25M ne disparait pas.
        """
        comptees_plan, espacees_plan = self.regrouper(armature_plan)
        comptees_atelier, espacees_atelier = self.regrouper(armature_atelier)
        details = [b for b in comptees_atelier if self.est_detail(b, armature_plan)]
        comptees_atelier = [
            b for b in comptees_atelier if not self.est_detail(b, armature_plan)
        ]
        return (
            self.aligner(comptees_plan, comptees_atelier)
            + self.aligner(espacees_plan, espacees_atelier)
            + [(None, barre) for barre in details]
        )

    def regrouper(self, armature: list[Barre]) -> tuple[list[Barre], list[Barre]]:
        """Retourne (barres comptees, barres espacees), un groupe par diametre."""
        comptees: dict[str, list[Barre]] = {}
        espacees: dict[tuple[str, float], list[Barre]] = {}
        for barre in armature:
            if barre.espacement_mm is None:
                comptees.setdefault(_diametre(barre), []).append(barre)
            else:
                cle = (_diametre(barre), round(float(barre.espacement_mm), 1))
                espacees.setdefault(cle, []).append(barre)
        return (
            [self.fusionner(lot) for lot in comptees.values()],
            [self.fusionner(lot) for lot in espacees.values()],
        )

    def fusionner(self, lot: list[Barre]) -> Barre:
        """Une seule barre pour un lot : quantites additionnees, reperes joints."""
        if len(lot) == 1:
            return lot[0]
        quantites = [barre.quantite for barre in lot]
        longueurs = {barre.longueur_mm for barre in lot}
        reperes = list(dict.fromkeys(barre.repere for barre in lot if barre.repere))
        return Barre(
            repere=" + ".join(reperes),
            diametre=lot[0].diametre,
            quantite=None if None in quantites else sum(quantites),
            espacement_mm=lot[0].espacement_mm,
            longueur_mm=longueurs.pop() if len(longueurs) == 1 else None,
        )

    def aligner(
        self,
        plan: list[Barre],
        atelier: list[Barre],
    ) -> list[tuple[Barre | None, Barre | None]]:
        """Met chaque groupe du plan en face de son jumeau atelier.

        1. meme diametre et meme espacement ;
        2. meme diametre, autre espacement (ex. une zone d'etriers differente) ;
        3. sinon le groupe restant le plus proche en diametre.
        Ce qui reste seul d'un cote sort avec None en face.
        """
        paires: list[tuple[Barre | None, Barre | None]] = []
        seuls_plan = list(plan)
        seuls_atelier = list(atelier)

        for barre in plan:
            jumelle = next(
                (a for a in seuls_atelier if self.meme_groupe(barre, a)), None
            )
            if jumelle is not None:
                paires.append((barre, jumelle))
                seuls_plan = [b for b in seuls_plan if b is not barre]
                seuls_atelier = [a for a in seuls_atelier if a is not jumelle]

        for barre in list(seuls_atelier):
            memes = [b for b in plan if _diametre(b) == _diametre(barre)]
            if memes:
                proche = min(memes, key=lambda b: self.distance(b, barre))
                paires.append((proche, barre))
                seuls_plan = [b for b in seuls_plan if b is not proche]
                seuls_atelier = [a for a in seuls_atelier if a is not barre]

        for barre in seuls_plan:
            if seuls_atelier:
                proche = min(seuls_atelier, key=lambda a: self.distance(barre, a))
                paires.append((barre, proche))
                seuls_atelier = [a for a in seuls_atelier if a is not proche]
            else:
                paires.append((barre, None))
        paires.extend((None, barre) for barre in seuls_atelier)
        return paires

    def meme_groupe(self, a: Barre, b: Barre) -> bool:
        """Meme diametre et meme espacement (ou aucun des deux cotes)."""
        return _diametre(a) == _diametre(b) and self.valeurs_equivalentes(
            "espacement_mm", a.espacement_mm, b.espacement_mm
        )

    def distance(self, a: Barre, b: Barre) -> tuple[float, float]:
        """Ecart de diametre, puis d'espacement, pour choisir le plus proche."""
        return (
            abs(_taille(a) - _taille(b)),
            abs((a.espacement_mm or 0) - (b.espacement_mm or 0)),
        )

    def est_detail(self, barre_atelier: Barre | None, armature_plan: list[Barre]) -> bool:
        """Barres comptees a l'atelier que le plan ne donne que par espacement.

        Ex. les etriers dans l'epaisseur de la dalle : l'atelier les compte
        (3 x 10M), le plan dit seulement 10M@6". Listees, pas jugees.
        """
        if barre_atelier is None or barre_atelier.espacement_mm is not None:
            return False
        espacees = {_diametre(b) for b in armature_plan if b.espacement_mm is not None}
        comptees = {_diametre(b) for b in armature_plan if b.espacement_mm is None}
        diametre = _diametre(barre_atelier)
        return diametre in espacees and diametre not in comptees

    def comparer_barre(
        self,
        barre_plan: Barre | None,
        barre_atelier: Barre | None,
    ) -> list[Ecart]:
        """Compare les 4 champs que le plan donne.

        Champ absent du plan = rien a comparer (le plan ne donne ni la
        longueur des barres ni le nombre d'etriers). Champ donne par le
        plan et absent de l'atelier = ecart.
        """
        if barre_plan is None or barre_atelier is None:
            return [
                Ecart(
                    champ="barre",
                    valeur_plan=barre_plan,
                    valeur_atelier=barre_atelier,
                )
            ]
        ecarts: list[Ecart] = []
        for champ in CHAMPS:
            a = getattr(barre_plan, champ)
            b = getattr(barre_atelier, champ)
            if a is None:
                continue
            if not self.valeurs_equivalentes(champ, a, b):
                ecarts.append(Ecart(champ=champ, valeur_plan=a, valeur_atelier=b))
        return ecarts

    def valeurs_equivalentes(self, champ: str, a: Any, b: Any) -> bool:
        """Egalite utile : 25M == 25m, 8 == 8.0, None == None."""
        if a is None and b is None:
            return True
        if a is None or b is None:
            return False
        if champ == "diametre":
            return str(a).strip().upper() == str(b).strip().upper()
        if champ in {"quantite", "espacement_mm", "longueur_mm"}:
            try:
                # Ça vise un calcul qui n'arrive pas exactement au même bit,
                # par exemple 12 * 25.4
                # donne 304.79999999999995, et 304.79999999999995 == 304.8 est faux.
                return abs(float(a) - float(b)) <= 0.05
            except (TypeError, ValueError):
                return str(a).strip() == str(b).strip()
        return a == b


def _barre(
    *,
    diametre: str = "25M",
    quantite: int | None = 8,
    espacement_mm: float | None = None,
    longueur_mm: float | None = 3600,
) -> Barre:
    return Barre(
        repere="X",
        diametre=diametre,
        quantite=quantite,
        espacement_mm=espacement_mm,
        longueur_mm=longueur_mm,
    )


def _element(source: str, armature: list[Barre]) -> Element:
    return Element(
        id=f"S-500_C-12_{source}",
        source=source,
        fichier="",
        feuillet="S-500",
        page=1,
        x=0,
        y=0,
        type_element="colonne",
        element="C-12",
        armature=armature,
    )


def _paire(
    plan: Element | None,
    atelier: Element | None,
    statut: str = "paire",
) -> Paire:
    return Paire(
        etiquette=Etiquette.depuis("S-500", "C-12"),
        plan=plan,
        atelier=atelier,
        statut=statut,
    )


class TestComparateur(unittest.TestCase):
    def setUp(self) -> None:
        self.cmp = Comparateur()

    def test_conforme_si_memes_attributs(self) -> None:
        verdict = self.cmp.comparer_paire(
            _paire(
                _element("plan", [_barre()]),
                _element("atelier", [_barre(diametre="25m")]),
            )
        )
        self.assertEqual(verdict.statut, "conforme")
        self.assertEqual(verdict.ecarts, [])

    def test_non_conforme_si_quantite_differente(self) -> None:
        verdict = self.cmp.comparer_paire(
            _paire(
                _element("plan", [_barre(quantite=9)]),
                _element("atelier", [_barre(quantite=11)]),
            )
        )
        self.assertEqual(verdict.statut, "non_conforme")
        self.assertEqual(verdict.ecarts[0].champ, "quantite")
        self.assertEqual(verdict.ecarts[0].valeur_plan, 9)
        self.assertEqual(verdict.ecarts[0].valeur_atelier, 11)

    def test_non_conforme_si_diametre_different(self) -> None:
        verdict = self.cmp.comparer_paire(
            _paire(
                _element("plan", [_barre(diametre="35M", quantite=4)]),
                _element("atelier", [_barre(diametre="25M", quantite=4)]),
            )
        )
        self.assertEqual(verdict.statut, "non_conforme")
        self.assertEqual([e.champ for e in verdict.ecarts], ["diametre"])

    def test_non_conforme_si_espacement_different(self) -> None:
        verdict = self.cmp.comparer_paire(
            _paire(
                _element("plan", [_barre(quantite=None, espacement_mm=304.8, longueur_mm=None)]),
                _element("atelier", [_barre(quantite=None, espacement_mm=152.4, longueur_mm=None)]),
            )
        )
        self.assertEqual(verdict.statut, "non_conforme")
        self.assertEqual(verdict.ecarts[0].champ, "espacement_mm")

    def test_manquant_si_pas_d_atelier(self) -> None:
        verdict = self.cmp.comparer_paire(_paire(_element("plan", [_barre()]), None, "manquant"))
        self.assertEqual(verdict.statut, "manquant")
        self.assertEqual(verdict.ecarts, [])

    def test_ajoute_si_pas_de_plan(self) -> None:
        verdict = self.cmp.comparer_paire(_paire(None, _element("atelier", [_barre()]), "ajoute"))
        self.assertEqual(verdict.statut, "ajoute")
        self.assertEqual(verdict.ecarts, [])

    def test_champ_absent_du_plan_pas_compare(self) -> None:
        verdict = self.cmp.comparer_paire(
            _paire(
                _element("plan", [_barre(quantite=4, longueur_mm=None)]),
                _element("atelier", [_barre(quantite=4, longueur_mm=3810)]),
            )
        )
        self.assertEqual(verdict.statut, "conforme")

    def test_non_conforme_si_champ_du_plan_absent_de_l_atelier(self) -> None:
        verdict = self.cmp.comparer_paire(
            _paire(
                _element("plan", [_barre(quantite=4)]),
                _element("atelier", [_barre(quantite=None)]),
            )
        )
        self.assertEqual([e.champ for e in verdict.ecarts], ["quantite"])

    def test_barres_alignees_par_nature_pas_par_position(self) -> None:
        barres_plan = [
            _barre(diametre="25M", quantite=4, longueur_mm=None),
            _barre(diametre="10M", quantite=None, espacement_mm=152, longueur_mm=None),
        ]
        barres_atelier = [
            _barre(diametre="10M", quantite=14, espacement_mm=152, longueur_mm=None),
            _barre(diametre="10M", quantite=3, longueur_mm=None),  # etriers dans la dalle
            _barre(diametre="25M", quantite=4, longueur_mm=3810),
        ]
        verdict = self.cmp.comparer_paire(
            _paire(_element("plan", barres_plan), _element("atelier", barres_atelier))
        )
        self.assertEqual(verdict.statut, "conforme")
        self.assertEqual(len(self.cmp.apparier_barres(barres_plan, barres_atelier)), 3)

    def test_quantites_additionnees_par_diametre(self) -> None:
        plan = _element("plan", [_barre(quantite=8, longueur_mm=None)])
        deux_lots = [_barre(quantite=4, longueur_mm=3810), _barre(quantite=4, longueur_mm=4270)]
        verdict = self.cmp.comparer_paire(_paire(plan, _element("atelier", deux_lots)))
        self.assertEqual(verdict.statut, "conforme")
        court = [_barre(quantite=4, longueur_mm=3810), _barre(quantite=2, longueur_mm=4270)]
        verdict = self.cmp.comparer_paire(_paire(plan, _element("atelier", court)))
        self.assertEqual([(e.champ, e.valeur_plan, e.valeur_atelier) for e in verdict.ecarts], [("quantite", 8, 6)])

    def test_zone_d_etriers_a_un_autre_espacement_signalee(self) -> None:
        plan = _element("plan", [_barre(diametre="10M", quantite=None, espacement_mm=152, longueur_mm=None)])
        atelier = _element(
            "atelier",
            [
                _barre(diametre="10M", quantite=14, espacement_mm=152, longueur_mm=None),
                _barre(diametre="10M", quantite=4, espacement_mm=305, longueur_mm=None),
            ],
        )
        verdict = self.cmp.comparer_paire(_paire(plan, atelier))
        self.assertEqual([(e.champ, e.valeur_plan, e.valeur_atelier) for e in verdict.ecarts], [("espacement_mm", 152, 305)])

    def test_barre_du_plan_absente_de_l_atelier(self) -> None:
        plan = _element(
            "plan",
            [
                _barre(diametre="25M", quantite=4, longueur_mm=None),
                _barre(diametre="10M", quantite=None, espacement_mm=152, longueur_mm=None),
            ],
        )
        atelier = _element("atelier", [_barre(diametre="10M", quantite=14, espacement_mm=152, longueur_mm=None)])
        verdict = self.cmp.comparer_paire(_paire(plan, atelier))
        self.assertEqual([e.champ for e in verdict.ecarts], ["barre"])
        self.assertIsNone(verdict.ecarts[0].valeur_atelier)

    def test_barre_atelier_hors_plan_signalee(self) -> None:
        plan = _element("plan", [_barre(diametre="25M", quantite=4, longueur_mm=None)])
        atelier = _element(
            "atelier",
            [_barre(diametre="25M", quantite=4), _barre(diametre="20M", quantite=2)],
        )
        verdict = self.cmp.comparer_paire(_paire(plan, atelier))
        self.assertEqual([e.champ for e in verdict.ecarts], ["barre"])
        self.assertIsNone(verdict.ecarts[0].valeur_plan)

    def test_comparer_paires_garde_l_ordre(self) -> None:
        paires = [
            _paire(_element("plan", [_barre()]), _element("atelier", [_barre()])),
            _paire(_element("plan", [_barre(quantite=9)]), _element("atelier", [_barre(quantite=11)])),
        ]
        verdicts = self.cmp.comparer_paires(paires)
        self.assertEqual([v.statut for v in verdicts], ["conforme", "non_conforme"])


if __name__ == "__main__":
    unittest.main()
