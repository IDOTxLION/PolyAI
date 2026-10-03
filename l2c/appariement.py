"""Appariement plan <-> atelier.

Une carte JSON = un element. On joint sur l'etiquette
(feuillet + element), jamais sur l'armature.
"""

from __future__ import annotations

import unittest
from collections import defaultdict
from dataclasses import dataclass

try:
    from l2c.modeles import Element, Etiquette
except ModuleNotFoundError:
    from modeles import Element, Etiquette


@dataclass
class Paire:
    """Resultat d'un appariement pour un objet du batiment."""

    etiquette: Etiquette
    plan: Element | None
    atelier: Element | None
    statut: str  # "paire" | "manquant" | "ajoute"


class Apparieur:
    """Construit les paires plan / atelier a partir de la base JSON."""

    def __init__(self, elements: list[Element]) -> None:
        self.elements = elements

    def separer_piles(self) -> tuple[list[Element], list[Element]]:
        """Coupe la base en pile plan et pile atelier via `source`."""
        plans = [element for element in self.elements if element.source == "plan"]
        ateliers = [element for element in self.elements if element.source == "atelier"]
        return plans, ateliers

    def etiquette(self, element: Element) -> Etiquette:
        """Cle metier : feuillet + element, deja normalises."""
        return element.etiquette()

    def indexer_atelier(
        self, pile_atelier: list[Element]
    ) -> dict[Etiquette, list[Element]]:
        """Dict etiquette -> cartes atelier. Plusieurs cartes possibles."""
        index: dict[Etiquette, list[Element]] = defaultdict(list)
        for carte in pile_atelier:
            index[self.etiquette(carte)].append(carte)
        return index

    def trouver_candidats(
        self,
        plan: Element,
        index_atelier: dict[Etiquette, list[Element]],
    ) -> list[Element]:
        """Cherche la jumelle : cle exacte, puis meme type_element."""
        candidats = list(index_atelier.get(self.etiquette(plan), []))
        type_plan = Etiquette.normaliser(plan.type_element)
        if not type_plan:
            return candidats
        memes_types = [
            carte
            for carte in candidats
            if Etiquette.normaliser(carte.type_element) == type_plan
        ]
        return memes_types or candidats

    def apparier(self) -> list[Paire]:
        """Enchaine separation -> index -> jointure."""
        plans, ateliers = self.separer_piles()
        index = self.indexer_atelier(ateliers)
        utilisees: set[int] = set()
        paires: list[Paire] = []

        for plan in plans:
            candidats = [
                carte
                for carte in self.trouver_candidats(plan, index)
                if id(carte) not in utilisees
            ]
            if candidats:
                atelier = candidats[0]
                utilisees.add(id(atelier))
                paires.append(
                    Paire(
                        etiquette=self.etiquette(plan),
                        plan=plan,
                        atelier=atelier,
                        statut="paire",
                    )
                )
            else:
                paires.append(
                    Paire(
                        etiquette=self.etiquette(plan),
                        plan=plan,
                        atelier=None,
                        statut="manquant",
                    )
                )

        for atelier in ateliers:
            if id(atelier) in utilisees:
                continue
            paires.append(
                Paire(
                    etiquette=self.etiquette(atelier),
                    plan=None,
                    atelier=atelier,
                    statut="ajoute",
                )
            )
        return paires


def _carte(source: str, feuillet: str, element: str, type_element: str = "colonne") -> Element:
    return Element(
        id=f"{feuillet}_{element}_{source}",
        source=source,
        fichier="",
        feuillet=feuillet,
        page=1,
        x=0,
        y=0,
        type_element=type_element,
        element=element,
    )


class TestApparieur(unittest.TestCase):
    def test_paire_si_meme_etiquette(self) -> None:
        paires = Apparieur(
            [
                _carte("plan", "S-500", "C-12"),
                _carte("atelier", "S-500", "c12"),
            ]
        ).apparier()
        self.assertEqual(len(paires), 1)
        self.assertEqual(paires[0].statut, "paire")
        self.assertIsNotNone(paires[0].plan)
        self.assertIsNotNone(paires[0].atelier)

    def test_manquant_si_plan_seul(self) -> None:
        paires = Apparieur([_carte("plan", "S-600", "D-22", "dalle")]).apparier()
        self.assertEqual(len(paires), 1)
        self.assertEqual(paires[0].statut, "manquant")
        self.assertIsNone(paires[0].atelier)

    def test_ajoute_si_atelier_seul(self) -> None:
        paires = Apparieur([_carte("atelier", "S-500", "C-99")]).apparier()
        self.assertEqual(len(paires), 1)
        self.assertEqual(paires[0].statut, "ajoute")
        self.assertIsNone(paires[0].plan)


if __name__ == "__main__":
    unittest.main()
