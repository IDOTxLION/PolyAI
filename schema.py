"""The record format of Appendix A of the challenge brief.

Both sides of the project use it: extraction writes these records, one per element found on
a plan or a shop drawing, and matching/reporting reads them.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

Source = Literal["plan", "atelier"]  # structural plan, or shop drawing ("dessin d'atelier")
TypeElement = Literal["fondation", "poutre", "mur", "colonne", "dalle"]  # S-100/300/400/500/600


class Armature(BaseModel):
    """One group of identical bars."""

    model_config = ConfigDict(extra="forbid")

    repere: str | None  # bar mark, e.g. "C12-1"
    diametre: str | None  # e.g. "25M"
    quantite: int | None
    espacement_mm: int | None
    longueur_mm: int | None


class Element(BaseModel):
    """One element (a column, a beam...) as found on one sheet, with its reinforcement."""

    model_config = ConfigDict(extra="forbid")

    id: str  # e.g. "S-500_C-12_plan"
    source: Source
    fichier: str
    feuillet: str  # sheet, e.g. "S-500"
    page: int  # 1-based
    x: float  # centre of the annotation, in PDF points from the top-left corner of the page
    y: float
    type_element: TypeElement
    element: str  # e.g. "C-12"
    armature: list[Armature]
