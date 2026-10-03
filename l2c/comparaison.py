"""Comparaison d'armature.

On part d'une Paire deja formee (meme etiquette). On ouvre ensuite
les listes `armature` et on compare barre par barre :
quantite, diametre, espacement_mm, longueur_mm.
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
        for barre_plan, barre_atelier in self.apparier_barres(
            armature_plan, armature_atelier
        ):
            ecarts.extend(self.comparer_barre(barre_plan, barre_atelier))

        statut = "non_conforme" if ecarts else "conforme"
        return Verdict(paire=paire, statut=statut, ecarts=ecarts)

    def apparier_barres(
        self,
        armature_plan: list[Barre],
        armature_atelier: list[Barre],
    ) -> list[tuple[Barre | None, Barre | None]]:
        """Aligne les barres par position. Pas de jointure sur `repere`.

        Le diametre est un champ a comparer, donc on ne s'en sert pas
        comme cle : sinon l'ecart 35M vs 25M disparaitrait.
        """
        n = max(len(armature_plan), len(armature_atelier))
        paires: list[tuple[Barre | None, Barre | None]] = []
        for i in range(n):
            plan = armature_plan[i] if i < len(armature_plan) else None
            atelier = armature_atelier[i] if i < len(armature_atelier) else None
            paires.append((plan, atelier))
        return paires

    def comparer_barre(
        self,
        barre_plan: Barre | None,
        barre_atelier: Barre | None,
    ) -> list[Ecart]:
        """Compare les 4 champs. `null` d'un seul cote = ecart."""
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

    def test_comparer_paires_garde_l_ordre(self) -> None:
        paires = [
            _paire(_element("plan", [_barre()]), _element("atelier", [_barre()])),
            _paire(_element("plan", [_barre(quantite=9)]), _element("atelier", [_barre(quantite=11)])),
        ]
        verdicts = self.cmp.comparer_paires(paires)
        self.assertEqual([v.statut for v in verdicts], ["conforme", "non_conforme"])


if __name__ == "__main__":
    unittest.main()
