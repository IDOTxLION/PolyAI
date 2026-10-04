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

# PLAN = Path("handoff/sample_plan_20.json")
# ATELIER = Path("handoff/sample_atelier_20.json")
PLAN = Path("handoff/CLP_colonnes_plan.json")
ATELIER = Path("handoff/CLP_colonnes_atelier.json")
RAPPORT = Path("out/rapport_sample.pdf")


def charger(chemin: Path) -> list[Element]:
    log.info("lecture de %s", chemin)
    data = json.loads(chemin.read_text())
    elements = [Element.depuis_dict(carte) for carte in data]
    log.info("%s elements lus dans %s", len(elements), chemin.name)
    return elements


def main() -> None:
    try:
        plan = charger(PLAN)
        atelier = charger(ATELIER)
        paires = Apparieur(plan + atelier).apparier()
        log.info("%s paires construites", len(paires))
        verdicts = Comparateur().comparer_paires(paires)
        comptes: dict[str, int] = {}
        for verdict in verdicts:
            comptes[verdict.statut] = comptes.get(verdict.statut, 0) + 1
        log.info("verdicts %s", comptes)
        Rapport(verdicts).ecrire_pdf(RAPPORT)
        log.info("rapport ecrit dans %s", RAPPORT)
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
