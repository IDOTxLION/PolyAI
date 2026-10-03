"""Objets metier du JSON d'armature.

Etiquette = la cle d'appariement (feuillet + element).
Element = une carte plan ou atelier.
Barre = une ligne de sa liste `armature`.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from typing import Any


def resoudre_source(source: str, ident: str) -> str:
    """Retourne 'plan' ou 'atelier'. Si source est vide, regarde la fin de l'id."""
    brut = str(source).strip().lower()
    if brut in {"plan", "atelier"}:
        return brut
    nom = str(ident).lower()
    if nom.endswith("_plan"):
        return "plan"
    if nom.endswith("_atelier"):
        return "atelier"
    return brut


def _entier(valeur: Any) -> int | None:
    if valeur is None:
        return None
    return int(valeur)


def _flottant(valeur: Any) -> float | None:
    if valeur is None:
        return None
    return float(valeur)


@dataclass(frozen=True)
class Etiquette:
    """Cle metier deja normalisee : 'C-12' et 'c12' sont la meme etiquette."""

    feuillet: str
    element: str

    @staticmethod
    def normaliser(valeur: str) -> str:
        texte = str(valeur).strip().upper().replace(",", ".")
        return "".join(car for car in texte if car.isalnum() or car == ".")

    @classmethod
    def depuis(cls, feuillet: str, element: str) -> Etiquette:
        return cls(cls.normaliser(feuillet), cls.normaliser(element))


@dataclass
class Barre:
    """Une barre d'armature, tel que dans l'annexe A."""

    repere: str
    diametre: str
    quantite: int | None
    espacement_mm: float | None
    longueur_mm: float | None

    @classmethod
    def depuis_dict(cls, data: dict[str, Any]) -> Barre:
        return cls(
            repere=str(data.get("repere") or ""),
            diametre=str(data.get("diametre") or ""),
            quantite=_entier(data.get("quantite")),
            espacement_mm=_flottant(data.get("espacement_mm")),
            longueur_mm=_flottant(data.get("longueur_mm")),
        )


@dataclass
class Element:
    """Une information identifiee sur un plan ou un dessin d'atelier."""

    id: str
    source: str
    fichier: str
    feuillet: str
    page: int
    x: float
    y: float
    type_element: str
    element: str
    armature: list[Barre] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.source = resoudre_source(self.source, self.id)

    def etiquette(self) -> Etiquette:
        return Etiquette.depuis(self.feuillet, self.element)

    @classmethod
    def depuis_dict(cls, data: dict[str, Any]) -> Element:
        return cls(
            id=str(data.get("id") or ""),
            source=str(data.get("source") or ""),
            fichier=str(data.get("fichier") or ""),
            feuillet=str(data.get("feuillet") or ""),
            page=int(data.get("page") or 0),
            x=float(data.get("x") or 0),
            y=float(data.get("y") or 0),
            type_element=str(data.get("type_element") or ""),
            element=str(data.get("element") or ""),
            armature=[Barre.depuis_dict(barre) for barre in data.get("armature") or []],
        )


class TestModeles(unittest.TestCase):
    def test_etiquette_ignore_casse_et_tirets(self) -> None:
        self.assertEqual(
            Etiquette.depuis("S-500", "C-12"),
            Etiquette.depuis("s500", "c12"),
        )

    def test_element_depuis_dict_lit_une_barre(self) -> None:
        element = Element.depuis_dict(
            {
                "id": "S-500_C-12_plan",
                "source": "plan",
                "fichier": "Projet1_S-500.pdf",
                "feuillet": "S-500",
                "page": 1,
                "x": 412.5,
                "y": 318.0,
                "type_element": "colonne",
                "element": "C-12",
                "armature": [
                    {
                        "repere": "C12-1",
                        "diametre": "25M",
                        "quantite": 8,
                        "espacement_mm": None,
                        "longueur_mm": 3600,
                    }
                ],
            }
        )
        self.assertEqual(element.etiquette(), Etiquette.depuis("S-500", "C-12"))
        self.assertEqual(element.armature[0].quantite, 8)
        self.assertIsNone(element.armature[0].espacement_mm)

    def test_source_vide_se_deduit_de_l_id(self) -> None:
        element = Element.depuis_dict(
            {
                "id": "S-100_L-13_atelier",
                "source": " ",
                "feuillet": "S-100",
                "element": "L-13",
            }
        )
        self.assertEqual(element.source, "atelier")


if __name__ == "__main__":
    unittest.main()
