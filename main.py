"""Point d'entree : des PDF d'un projet au rapport PDF.

Usage : python main.py [plan.pdf] [dossier ou PDF des dessins d'atelier]
        python main.py plan.json atelier.json

La premiere forme extrait les elements du plan et des dessins d'atelier (JSON de
l'annexe A, dans out/), les apparie, les compare et ecrit out/<projet>_rapport.pdf.
La seconde repart de JSON deja extraits, par exemple ceux de handoff/.
"""

import json
import logging
import sys
from pathlib import Path

from l2c.appariement import Apparieur
from l2c.comparaison import Comparateur
from l2c.modeles import Element
from l2c.rapport import Rapport

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

SORTIE = Path("out")


def chemins_json(arguments: list[str]) -> tuple[Path, Path]:
    """Retourne les JSON plan et atelier : ceux donnes, sinon ceux extraits des PDF."""
    if len(arguments) == 2 and all(argument.endswith(".json") for argument in arguments):
        return Path(arguments[0]), Path(arguments[1])
    import extract_columns  # charge PyMuPDF : inutile quand on repart des JSON

    return extract_columns.run(arguments)


def charger(chemin: Path) -> list[Element]:
    log.info("lecture de %s", chemin)
    data = json.loads(chemin.read_text(encoding="utf8"))
    elements = [Element.depuis_dict(carte) for carte in data]
    log.info("%s elements lus dans %s", len(elements), chemin.name)
    return elements


def main() -> None:
    try:
        chemin_plan, chemin_atelier = chemins_json(sys.argv[1:])
        plan = charger(chemin_plan)
        atelier = charger(chemin_atelier)
        paires = Apparieur(plan + atelier).apparier()
        log.info("%s paires construites", len(paires))
        verdicts = Comparateur().comparer_paires(paires)
        comptes: dict[str, int] = {}
        for verdict in verdicts:
            comptes[verdict.statut] = comptes.get(verdict.statut, 0) + 1
        log.info("verdicts %s", comptes)
        rapport = SORTIE / f"{chemin_plan.stem.split('_')[0]}_rapport.pdf"
        Rapport(verdicts).ecrire_pdf(rapport)
        log.info("rapport ecrit dans %s", rapport)
    except FileNotFoundError:
        log.exception("fichier introuvable")
        sys.exit(1)
    except json.JSONDecodeError:
        log.exception("json illisible")
        sys.exit(1)
    except Exception:
        log.exception("echec du pipeline")
        sys.exit(1)


if __name__ == "__main__":
    main()
